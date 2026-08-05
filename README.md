# New City Deployment Checklist

CAPA Procurement Gateway — Clear2Buy

---

## 1. Repository

One repo, one codebase, serves every city. A new city is a new tenant folder, not a new repo.

- [ ] Create `tenants/<city-slug>/` (e.g. `tenants/cathedral-city/`)
- [ ] Copy an existing tenant's `config.json` into `tenants/<city-slug>/config.json` as a starting point

---

## 2. City Configuration

Edit **`tenants/<city-slug>/config.json`** — this is the only file that needs city-specific changes:

- [ ] `city.name` — full city name (e.g. `"City of Cathedral City"`)
- [ ] `city.state` — state abbreviation (e.g. `"CA"`)
- [ ] `city.city_state_zip` — (e.g. `"Cathedral City, CA 92234"`)
- [ ] `city.city_clerk_name` — name of the City Clerk
- [ ] `city.procurement_email` — procurement department email
- [ ] `city.procurement_phone` — procurement department phone
- [ ] `city.warehouse_address` — warehouse/delivery address
- [ ] `bid_thresholds` — update if city thresholds differ from Palm Springs
- [ ] `signing_authority.levels` — update dollar limits per role if different
- [ ] `pcard.single_transaction_limit` — update if different
- [ ] `hr_review_keywords` — add/remove keywords relevant to this city
- [ ] `maintenance_redirect_keywords` — update as needed
- [ ] `mail.allowed_domain` — the city email domain allowed to sign in
- [ ] `admin.emails` — who gets admin access for this city

---

## 3. Policy Documents & Vector Store

- [ ] Add source PDFs under `tenants/<city-slug>/documents/<category>/`
- [ ] Build the vector store locally (requires Ollama with `nomic-embed-text` — the embed model is hardcoded to `localhost:11434`, so this step cannot run on Railway):

  ```bash
  set TENANT=<city-slug>
  ollama serve
  python scripts/ingest_documents.py
  ```

  This writes `tenants/<city-slug>/chroma_db/`.
- [ ] Commit `tenants/<city-slug>/` (config, documents, chroma_db) and push:

  ```bash
  git add tenants/<city-slug>/
  git commit -m "Add <city-slug> tenant"
  git push
  ```

`/api/admin/ingest` exists in `app.py` but calls the same Ollama-only embed model, so triggering it on Railway will fail. Treat re-ingestion as a local, pre-push step only.

---

## 4. Resend (Email)

- [ ] Log into [resend.com](https://resend.com)
- [ ] Add the new city's sending domain (or reuse `capa.consulting` subdomain)
- [ ] Add the DNS records Resend requires (DKIM, SPF) — see DNS section below
- [ ] Verify the domain in Resend
- [ ] Create a new API key for this deployment
- [ ] Copy the API key — you'll need it for Railway

---

## 5. Railway

- [ ] Create a new Railway service (new project, or add to the existing one), connected to this repo/branch
- [ ] Add a **Volume** mounted at `/data` — this is where `procurement.db`, the runtime chroma copy, and the bootstrapped config live, and it's the only thing that survives redeploys
- [ ] Set all environment variables:

| Variable         | Value                                                                                                       |
| ---------------- | ----------------------------------------------------------------------------------------------------------- |
| `TENANT`         | `<city-slug>` — must match the `tenants/<city-slug>/` folder name                                           |
| `DATA_DIR`       | `/data`                                                                                                     |
| `OPENAI_API_KEY` | OpenAI API key                                                                                              |
| `RESEND_API_KEY` | Key from Resend step above                                                                                  |
| `SMTP_FROM`      | Sending email address (e.g. `ron@capa.consulting`)                                                          |
| `SMTP_FROM_NAME` | Display name (e.g. "CAPA Procurement Gateway")                                                              |
| `BASE_URL`       | `https://cathedralcity.capa.consulting` (set after DNS)                                                     |
| `SECRET_KEY`     | Random string — Flask session signing; don't leave on the dev default                                       |
| `ADMIN_USERNAME` | Admin's email — grants admin UI access                                                                      |
| `ADMIN_PASSWORD` | Gates `/admin/config` and `/admin/db/download`. Left unset, those routes are open to anyone — always set it |

- [ ] Deploy and confirm build succeeds
- [ ] Note the Railway-assigned URL (e.g. `web-production-xxxxx.up.railway.app`)

---

## 6. DNS (Cloudflare)

- [ ] Log into Cloudflare → `capa.consulting`
- [ ] Add a CNAME record for the new city subdomain:

| Type  | Name            | Target                                | Proxy   |
| ----- | --------------- | ------------------------------------- | ------- |
| CNAME | `cathedralcity` | `web-production-xxxxx.up.railway.app` | Proxied |

- [ ] Add Resend DNS records if using a new sending domain:
  - TXT `resend._domainkey` — DKIM key from Resend
  - Update SPF TXT on the sending domain to include `include:spf.resend.com`
  - Confirm SPF record stays under 10 DNS lookups

---

## 7. Railway Custom Domain

- [ ] Railway → service → **Settings** → **Networking** → **Custom Domain**
- [ ] Enter `cathedralcity.capa.consulting`
- [ ] Enter port `8080`
- [ ] Click **Add Domain**
- [ ] Wait 1-2 minutes for SSL cert to provision

---

## 8. Final Checks

- [ ] Visit `https://cathedralcity.capa.consulting` — app loads with the right city name
- [ ] Run a test procurement analysis end to end
- [ ] Send a test sign-in email — confirm it arrives and the link works
- [ ] Check Railway logs for any errors
- [ ] Confirm `procurement_config.json` was bootstrapped to the volume (check logs for `Bootstrapped config from repo to /data/procurement_config.json`)

---

## Notes

- Each city gets its own Railway service, volume, and subdomain — they are fully independent deployments, but all run the same codebase from this one repo.
- `tenants/<city-slug>/config.json` is the **seed file**. On first boot, if `/data/procurement_config.json` doesn't exist yet, the app copies the seed to the volume. After that, edit the volume copy directly (via `/admin/config`) or redeploy with an updated repo file — the seed is only read when the volume copy is missing.
- Do not share API keys between city deployments.

## Testing

- python -m pytest
