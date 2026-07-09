# policy_rag.py
"""
Vector store interface for policy document retrieval.

Documents are ingested from the `documents/` folder (or DATA_DIR/documents on Railway)
into a persistent ChromaDB collection. At analysis time, relevant policy passages are
retrieved and injected into the Claude prompt instead of hardcoded rules.

This makes the app city-agnostic: swap the documents and re-ingest, no code changes needed.
"""

import os

_BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR    = os.getenv("DATA_DIR", os.path.join(_BASE_DIR, "data"))
_CHROMA_PATH = os.path.join(_DATA_DIR, "chroma_db")

# Documents: Railway volume path takes priority, falls back to repo folder
_VOLUME_DOCS = "/data/documents"
_REPO_DOCS   = os.path.join(_BASE_DIR, "documents")
_DOCS_PATH   = (
    os.getenv("DOCS_PATH")
    or (_VOLUME_DOCS if os.path.exists(_VOLUME_DOCS) else _REPO_DOCS)
)

COLLECTION = "policy_documents"


def _embed_model():
    from llama_index.embeddings.ollama import OllamaEmbedding
    return OllamaEmbedding(
        model_name="nomic-embed-text",
        base_url="http://localhost:11434",
    )


def _collection(reset: bool = False):
    import chromadb
    client = chromadb.PersistentClient(path=_CHROMA_PATH)
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    return client.get_or_create_collection(COLLECTION)


def ingest(docs_path: str | None = None, reset: bool = True) -> int:
    """
    Ingest all PDFs from docs_path into ChromaDB.
    Called by scripts/ingest_documents.py and the admin API endpoint.
    Returns number of source documents ingested.
    """
    import fitz  # PyMuPDF
    from llama_index.core import VectorStoreIndex, StorageContext, Settings
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.schema import Document
    from llama_index.vector_stores.chroma import ChromaVectorStore

    if docs_path is None:
        docs_path = _DOCS_PATH

    collection = _collection(reset=reset)

    Settings.embed_model = _embed_model()
    Settings.llm = None

    docs = []
    for root, _, files in os.walk(docs_path):
        for fname in sorted(files):
            if not fname.lower().endswith(".pdf"):
                continue
            fpath = os.path.join(root, fname)
            category = os.path.basename(root)
            try:
                pdf  = fitz.open(fpath)
                text = "\n".join(page.get_text() for page in pdf)
                pdf.close()
                if text.strip():
                    docs.append(Document(
                        text=text,
                        metadata={"filename": fname, "category": category},
                    ))
                    print(f"  Loaded: {fname}", flush=True)
            except Exception as e:
                print(f"  Skipped {fname}: {e}", flush=True)

    if not docs:
        print("No PDF documents found.", flush=True)
        return 0

    splitter     = SentenceSplitter(chunk_size=512, chunk_overlap=64)
    nodes        = splitter.get_nodes_from_documents(docs)
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)
    VectorStoreIndex(nodes, storage_context=storage_ctx, show_progress=True)

    print(f"\nIngested {len(docs)} documents -> {len(nodes)} chunks.", flush=True)
    return len(docs)


def query(queries: list[str], top_k: int = 4) -> str:
    """
    Retrieve relevant policy passages for the given queries.
    Returns concatenated source-labelled text, or empty string if store not ready.
    """
    from llama_index.core import VectorStoreIndex, StorageContext, Settings
    from llama_index.vector_stores.chroma import ChromaVectorStore

    try:
        collection = _collection()
        if collection.count() == 0:
            return ""

        Settings.embed_model = _embed_model()
        Settings.llm = None

        vector_store = ChromaVectorStore(chroma_collection=collection)
        storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)
        index        = VectorStoreIndex.from_vector_store(
            vector_store, storage_context=storage_ctx
        )
        retriever = index.as_retriever(similarity_top_k=top_k)

        seen   = set()
        chunks = []
        for q in queries:
            for node in retriever.retrieve(q):
                key = node.text[:80]
                if key not in seen:
                    seen.add(key)
                    src = node.metadata.get("filename", "policy document")
                    chunks.append(f"[Source: {src}]\n{node.text.strip()}")

        return "\n\n---\n\n".join(chunks)

    except Exception as e:
        print(f"RAG query failed: {e}", flush=True)
        return ""


def is_ready() -> bool:
    """Return True if the vector store has been populated."""
    try:
        return _collection().count() > 0
    except Exception:
        return False


def docs_path() -> str:
    return _DOCS_PATH
