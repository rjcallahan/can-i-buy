# New City Deployment Checklist

### CAPA Procurement Gateway — Clear2Buy

---

## 1. Repository

- [ ] Fork or copy the `Clear2Buy` repo into a new repo named for the city (e.g. `Cathedral-City`)
- [ ] Clone it locally
- [ ] Create a new branch `main`

---

## 2. City Configuration

Edit **`data/procurement_config.json`** — this is the only file that needs city-specific changes:

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

---

## 3. Resend (Email)

- [ ] Log into [resend.com](https://resend.com)
- [ ] Add the new city's sending domain (or reuse `capa.consulting` subdomain)
- [ ] Add the DNS records Resend requires (DKIM, SPF) — see DNS section below
- [ ] Verify the domain in Resend
- [ ] Create a new API key for this deployment
- [ ] Copy the API key — you'll need it for Railway

---

## 4. Railway

- [ ] Create a new Railway project
- [ ] Connect it to the new city's GitHub repo
- [ ] Add a **Volume** mounted at `/data`
- [ ] Set all environment variables:

| Variable            | Value                                                   |
| ------------------- | ------------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Anthropic API key                                       |
| `RESEND_API_KEY`    | Key from Resend step above                              |
| `SMTP_FROM`         | Sending email address (e.g. `ron@capa.consulting`)      |
| `SMTP_FROM_NAME`    | Display name (e.g. `"CAPA Procurement Gateway"`)        |
| `BASE_URL`          | `https://cathedralcity.capa.consulting` (set after DNS) |
| `DATA_DIR`          | `/data`                                                 |

- [ ] Deploy and confirm build succeeds
- [ ] Note the Railway-assigned URL (e.g. `web-production-xxxxx.up.railway.app`)

---

## 5. DNS (Cloudflare)

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

## 6. Railway Custom Domain

- [ ] Railway → service → **Settings** → **Networking** → **Custom Domain**
- [ ] Enter `cathedralcity.capa.consulting`
- [ ] Enter port `8080`
- [ ] Click **Add Domain**
- [ ] Wait 1-2 minutes for SSL cert to provision

---

## 7. Final Checks

- [ ] Visit `https://cathedralcity.capa.consulting` — app loads
- [ ] Run a test procurement analysis end to end
- [ ] Send a test email — confirm it arrives
- [ ] Check Railway logs for any errors
- [ ] Update `BASE_URL` in Railway variables to the custom domain if not already set
- [ ] Confirm `data/procurement_config.json` was bootstrapped to the Railway volume (check logs for `Bootstrapped config from repo`)

---

## Notes

- The `data/procurement_config.json` in the repo is the **seed file** — on first deploy Railway copies it to the `/data` volume. After that, edits should be made on the volume directly or by redeploying with an updated repo file.
- Each city gets its own Railway service, volume, and subdomain — they are fully independent deployments.
- The `documents/` folder contains reference policy documents used to build the AI prompt context. Update these for the new city if their policies differ significantly.
- Do not share API keys between city deployments.

## Testing

- python -m pytest
