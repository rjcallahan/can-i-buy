#!/usr/bin/env python3
"""
Ingest policy PDFs into the ChromaDB vector store.

Run once after initial deployment and again whenever policy documents change.
The store persists on the Railway volume — app code reads it at request time.

Usage:
    python scripts/ingest_documents.py
    python scripts/ingest_documents.py --docs-path /data/documents
"""

import argparse
import os
import sys

# Allow running from the scripts/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import policy_rag  # noqa: E402 — must follow sys.path patch and load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Ingest policy PDFs into ChromaDB"
    )
    parser.add_argument(
        "--docs-path",
        help=f"Path to documents folder (default: {policy_rag.docs_path()})",
    )
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is not set.")
        print("Add it to your .env file or Railway environment variables.")
        sys.exit(1)

    docs = args.docs_path or policy_rag.docs_path()
    print(f"Ingesting documents from: {docs}")
    print(f"Vector store path: {policy_rag._CHROMA_PATH}\n")

    count = policy_rag.ingest(docs_path=docs)

    if count > 0:
        print(f"\nDone. Vector store is ready with {count} source documents.")
    else:
        print("\nNo documents ingested. Check that PDF files exist in the documents folder.")
        sys.exit(1)


if __name__ == "__main__":
    main()
