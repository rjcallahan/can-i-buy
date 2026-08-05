# Embedding Eval

Compares Ollama's `nomic-embed-text` against OpenAI's `text-embedding-3-small`
on retrieval quality, using the procurement rules as ground truth. Answers one
question: is it safe to switch `policy_rag.py` from Ollama to OpenAI
embeddings?

## Files

- `rules_chunks.json` — atomic rule chunks extracted from
  `tenants/palm-springs/config.json` by `extract_chunks.py`.
- `test_queries.json` — realistic department-user questions, each mapped to
  the chunk id it should retrieve. `expected_id` can be a single id or a
  list of ids — use a list when the source config states the same rule in
  more than one place (e.g. a threshold restated in both `bid_thresholds`
  and `procurement_methods`), so either chunk counts as a correct retrieval.
  Edit this file directly to add or correct queries.
- `eval.py` — embeds the chunks with both models, runs every query against
  both indexes, reports recall@k.
- `embeddings_ollama.npz`, `embeddings_openai.npz` — cached embeddings,
  generated on first run.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in this directory with:

```env
OPENAI_API_KEY=sk-...
```

Ollama needs to be running locally with the embedding model pulled:

```bash
ollama serve
ollama pull nomic-embed-text
```

## Running

Regenerate the chunks if `tenants/palm-springs/config.json` changed:

```bash
python extract_chunks.py
```

Run the comparison:

```bash
python eval.py
```

Subsequent runs reuse the cached embeddings. Force a full re-embed (e.g.
after editing `rules_chunks.json`) with:

```bash
python eval.py --rebuild
```

`eval.py` also detects when `rules_chunks.json` has changed since the cache
was built and rebuilds automatically — `--rebuild` is only needed to force it
regardless.

## Reading the output

For each query, `eval.py` prints PASS/FAIL per model — PASS means the
expected chunk id showed up in that model's top-k results (`--top-k`,
default 3). The summary at the end gives recall@k as a fraction for each
model side by side.
