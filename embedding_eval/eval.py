#!/usr/bin/env python3
"""
Compare Ollama's nomic-embed-text against OpenAI's text-embedding-3-small
for retrieval quality on the procurement rule chunks.

Embeds rules_chunks.json with both models, caches the vectors to disk, then
runs every query in test_queries.json against both indexes and reports
recall@k side by side.

Usage:
    python eval.py
    python eval.py --rebuild
    python eval.py --top-k 5
"""

import argparse
import contextlib
import json
import os
import sys

import numpy as np
import requests
from dotenv import load_dotenv

with contextlib.suppress(AttributeError):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_MODEL = "nomic-embed-text"
OPENAI_MODEL = "text-embedding-3-small"


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def embed_ollama(texts: list[str]) -> np.ndarray:
    vectors = []
    for text in texts:
        resp = requests.post(
            OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": text}, timeout=60
        )
        resp.raise_for_status()
        vectors.append(resp.json()["embedding"])
    return np.array(vectors, dtype=np.float32)


def embed_openai(texts: list[str]) -> np.ndarray:
    from openai import OpenAI

    client = OpenAI()
    vectors = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = client.embeddings.create(model=OPENAI_MODEL, input=batch)
        vectors.extend(item.embedding for item in resp.data)
    return np.array(vectors, dtype=np.float32)


def load_or_build_cache(cache_path, chunk_ids, chunk_texts, embed_fn, rebuild):
    if not rebuild and os.path.exists(cache_path):
        cached = np.load(cache_path, allow_pickle=True)
        if [str(i) for i in cached["ids"]] == chunk_ids:
            return cached["vectors"]
        print(f"  Cache at {os.path.basename(cache_path)} is stale (chunks changed) — rebuilding.")

    vectors = embed_fn(chunk_texts)
    np.savez(cache_path, ids=np.array(chunk_ids, dtype=object), vectors=vectors)
    return vectors


def cosine_top_k(query_vec, matrix, ids, k):
    q = query_vec / np.linalg.norm(query_vec)
    m = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    scores = m @ q
    order = np.argsort(-scores)[:k]
    return [(ids[i], float(scores[i])) for i in order]


def expected_ids(q):
    """expected_id may be a single chunk id or a list of acceptable ids."""
    exp = q["expected_id"]
    return exp if isinstance(exp, list) else [exp]


def run_backend(name, embed_fn, chunk_ids, chunk_texts, queries, cache_path, rebuild, top_k):
    print(f"\n=== {name} ===")
    try:
        vectors = load_or_build_cache(cache_path, chunk_ids, chunk_texts, embed_fn, rebuild)
    except Exception as e:
        print(f"  Skipped: {e}")
        return None

    results = []
    for q in queries:
        try:
            query_vec = embed_fn([q["query"]])[0]
        except Exception as e:
            print(f"  Query embed failed: {e}")
            return None
        top = cosine_top_k(query_vec, vectors, chunk_ids, top_k)
        top_ids = [t[0] for t in top]
        accepted = expected_ids(q)
        results.append({
            "query": q["query"],
            "expected_id": q["expected_id"],
            "top": top,
            "pass": any(eid in top_ids for eid in accepted),
        })

    hits = sum(r["pass"] for r in results)
    print(f"  Embedded {len(chunk_ids)} chunks, ran {len(queries)} queries.")
    return {"results": results, "recall": hits / len(queries) if queries else 0.0}


def _truncate(text, n=150):
    return text if len(text) <= n else text[:n - 3] + "..."


def print_report(runs, queries, chunk_lookup, top_k):
    names = list(runs.keys())

    print(f"\n{'=' * 100}")
    print(f"Per-query comparison (top-{top_k})")
    print(f"{'=' * 100}")

    for i, q in enumerate(queries):
        print(f"\n[{i + 1}] Query: {q['query']}")
        accepted = expected_ids(q)
        for eid in accepted:
            print(f"    Expected ({eid}): {_truncate(chunk_lookup.get(eid, '(unknown chunk id)'))}")

        for name in names:
            run = runs[name]
            print(f"\n    {name}:")
            if run is None:
                print("      skipped")
                continue
            result = run["results"][i]
            print(f"      [{'PASS' if result['pass'] else 'FAIL'}]")
            for rank, (cid, score) in enumerate(result["top"], start=1):
                marker = "  <-- expected" if cid in accepted else ""
                print(f"      {rank}. ({score:.3f}) {cid}{marker}")
                print(f"         {_truncate(chunk_lookup.get(cid, ''))}")

    print(f"\n{'=' * 100}")
    print(f"Summary — recall@{top_k}")
    print(f"{'=' * 100}")
    for name in names:
        run = runs[name]
        if run is None:
            print(f"  {name}: skipped")
        else:
            hits = sum(r["pass"] for r in run["results"])
            print(f"  {name}: {hits}/{len(queries)} ({run['recall'] * 100:.0f}%)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", default=os.path.join(_DIR, "rules_chunks.json"))
    parser.add_argument("--queries", default=os.path.join(_DIR, "test_queries.json"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--rebuild", action="store_true", help="Force re-embedding, ignore cache")
    args = parser.parse_args()

    chunks = load_json(args.chunks)
    queries = load_json(args.queries)
    chunk_ids = [c["id"] for c in chunks]
    chunk_texts = [c["text"] for c in chunks]
    chunk_lookup = dict(zip(chunk_ids, chunk_texts))

    backends = [
        ("Ollama (nomic-embed-text)", embed_ollama, os.path.join(_DIR, "embeddings_ollama.npz")),
        ("OpenAI (text-embedding-3-small)", embed_openai, os.path.join(_DIR, "embeddings_openai.npz")),
    ]

    runs = {}
    for name, embed_fn, cache_path in backends:
        runs[name] = run_backend(
            name, embed_fn, chunk_ids, chunk_texts, queries, cache_path, args.rebuild, args.top_k
        )

    print_report(runs, queries, chunk_lookup, args.top_k)


if __name__ == "__main__":
    main()
