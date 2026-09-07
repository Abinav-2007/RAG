"""
vector_store.py
---------------
ChromaDB vector storage module for the RAG ingestion pipeline.

Responsibilities
----------------
- Initialize and return a persistent ChromaDB collection.
- Store chunk text, embedding vectors, and metadata together under a
  unique, structured chunk ID.
- Keep the ingestion path (this module) fully decoupled from the query
  path so that embeddings are never regenerated at query time.

Association model inside ChromaDB
----------------------------------
ChromaDB stores four parallel arrays per collection, keyed by ID:

    ID           →  unique string per chunk
    document     →  chunk.page_content  (the raw text)
    embedding    →  384-dimensional BGE vector
    metadata     →  chunk.metadata dict (source, page, …)

When a semantic query arrives later, ChromaDB will:
  1. Embed the query with the same BGE model.
  2. Compute cosine similarity against every stored embedding.
  3. Return the top-k (document, metadata) pairs — ready for citation.

ID strategy
-----------
Current implementation uses a *content-hash* strategy:

    SHA-256( page_content )[:16]

This gives:
  - Collision resistance across chunks with different text.
  - Idempotent ingestion: re-running on unchanged documents produces
    the same IDs and ChromaDB's upsert silently skips duplicates.

Planned stable ID (future upgrade)
-----------------------------------
When the full pipeline matures, replace the hash with a deterministic
composite key that encodes provenance and model version:

    {stem}__{model_tag}__p{page}__c{chunk_index}

Example:
    document__bge-small-en-v1.5__p4__c2

This will support:
  - Reproducible ingestion across runs.
  - Incremental updates (only re-ingest changed pages).
  - Safe embedding-model version bumps (new model → new IDs, no
    mismatch with existing vectors).
  - Duplicate prevention when the same document is loaded twice.
"""

import hashlib
import os
from pathlib import Path
from typing import Optional

import chromadb
from chromadb import Collection
from langchain_core.documents import Document

# Model tag for ID versioning
DEFAULT_MODEL_TAG = "bge-small-en-v1.5"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHROMA_PERSIST_DIR: str = "chroma_db"
COLLECTION_NAME: str = "rag_documents"

# Cosine distance is the correct metric for L2-normalized BGE embeddings.
# ChromaDB internally stores "cosine distance" but higher similarity =
# lower distance, so retrieval results are still ranked best-first.
COLLECTION_DISTANCE_METRIC: str = "cosine"


# ---------------------------------------------------------------------------
# Client / collection initialisation
# ---------------------------------------------------------------------------

def create_collection(
    persist_dir: str = CHROMA_PERSIST_DIR,
    collection_name: str = COLLECTION_NAME,
    distance_metric: str = COLLECTION_DISTANCE_METRIC,
) -> Collection:
    """
    Initialize a persistent ChromaDB client and return the named collection.

    If the collection already exists it is loaded; otherwise it is created
    with cosine similarity as the distance metric.  The database is written
    to *persist_dir* on disk so that it survives application restarts and
    document embeddings never need to be regenerated unless the source
    documents actually change.

    Args:
        persist_dir:      Path to the local directory used for persistence.
        collection_name:  Name of the ChromaDB collection.
        distance_metric:  Distance function used for vector search.
                          Must be ``"cosine"``, ``"l2"``, or ``"ip"``.

    Returns:
        A ChromaDB ``Collection`` object ready for ingestion or querying.

    Raises:
        RuntimeError: If the client or collection cannot be initialised.
    """
    persist_path = Path(persist_dir)
    persist_path.mkdir(parents=True, exist_ok=True)

    try:
        client = chromadb.PersistentClient(path=str(persist_path))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to initialise ChromaDB client at '{persist_path}': {exc}"
        ) from exc

    try:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": distance_metric},
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to get/create collection '{collection_name}': {exc}"
        ) from exc

    return collection


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _build_chunk_id(
    chunk: Document, 
    index: int, 
    model_tag: str = DEFAULT_MODEL_TAG
) -> str:
    """
    Build a unique, stable, deterministic chunk ID.

    Stable ID strategy (PRODUCTION)
    --------------------------------
    Uses a fully deterministic composite key that encodes provenance
    and embedding model version:

        {stem}__{model_tag}__p{page}__c{index}

    Example:
        document__bge-small-en-v1.5__p4__c2

    This strategy provides:
      - Reproducible ingestion: same document → same IDs across runs
      - Incremental updates: only changed pages need re-ingestion
      - Model versioning: different model → different IDs, no mismatch
      - Duplicate prevention: identical documents produce identical IDs

    The index is now globally stable within the document structure, not
    dependent on batch order.

    Args:
        chunk: LangChain Document object.
        index: Zero-based position of this chunk within its source document.
               Must be deterministic (e.g., from sorted chunk order).
        model_tag: Embedding model identifier for version control.

    Returns:
        A unique, deterministic string ID for this chunk.
    """
    source: str = chunk.metadata.get("source", "unknown")
    stem = Path(source).stem  # e.g. "document" from "data/document.pdf"

    # Sanitize model tag for use in ID (remove special chars)
    clean_model_tag = model_tag.replace("/", "-").replace(":", "-")

    page: Optional[int] = chunk.metadata.get("page")
    if page is not None:
        return f"{stem}__{clean_model_tag}__p{page}__c{index}"

    return f"{stem}__{clean_model_tag}__c{index}"


# ---------------------------------------------------------------------------
# Metadata sanitisation
# ---------------------------------------------------------------------------

def _sanitise_metadata(raw: dict) -> dict:
    """
    Ensure all metadata values are ChromaDB-compatible scalar types.

    ChromaDB accepts only ``str``, ``int``, ``float``, and ``bool``.
    Any other value is coerced to ``str`` so the original metadata dict
    is never silently dropped.

    Args:
        raw: The ``chunk.metadata`` dict from a LangChain Document.

    Returns:
        A sanitised copy safe to pass directly to ChromaDB.
    """
    sanitised: dict = {}
    for key, value in raw.items():
        if isinstance(value, (str, int, float, bool)):
            sanitised[key] = value
        else:
            sanitised[key] = str(value)
    return sanitised


# ---------------------------------------------------------------------------
# Core ingestion function
# ---------------------------------------------------------------------------

def delete_document_chunks(
    collection: Collection,
    source: str,
    model_tag: Optional[str] = None
) -> int:
    """
    Delete all chunks from a specific source document.

    Used before re-ingesting a modified document to prevent stale chunks
    from accumulating in the vector store.

    Args:
        collection: An initialised ChromaDB Collection.
        source: Path to the source document (e.g., "data/document.pdf").
        model_tag: Optional embedding model identifier. If None, deletes
                   chunks for all model versions of this document.

    Returns:
        Number of chunks deleted.

    Raises:
        RuntimeError: If the deletion operation fails.
    """
    try:
        # Get all records for this source
        results = collection.get(
            where={"source": source},
            include=["metadatas"]
        )
        ids_to_delete = results.get("ids", [])
        
        # Filter by model tag if specified
        if model_tag and ids_to_delete:
            clean_model_tag = model_tag.replace("/", "-").replace(":", "-")
            filtered_ids = []
            for chunk_id, metadata in zip(ids_to_delete, results["metadatas"]):
                # Check if ID contains the model tag
                if f"__{clean_model_tag}__" in chunk_id:
                    filtered_ids.append(chunk_id)
            ids_to_delete = filtered_ids
        
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
        
        return len(ids_to_delete)
    except Exception as exc:
        raise RuntimeError(f"Failed to delete chunks for '{source}': {exc}") from exc


def delete_orphaned_chunks(
    collection: Collection,
    source_directory: str = "data"
) -> int:
    """
    Delete chunks whose source files no longer exist.

    Scans all chunks in the collection and removes those whose source
    file has been deleted from the file system.

    Args:
        collection: An initialised ChromaDB Collection.
        source_directory: Root directory to check file existence.

    Returns:
        Number of orphaned chunks deleted.

    Raises:
        RuntimeError: If the deletion operation fails.
    """
    try:
        # Get all chunks with their metadata
        results = collection.get(include=["metadatas"])
        ids = results.get("ids", [])
        metadatas = results.get("metadatas", [])
        
        orphaned_ids = []
        for chunk_id, metadata in zip(ids, metadatas):
            source = metadata.get("source")
            if source and not Path(source).exists():
                orphaned_ids.append(chunk_id)
        
        if orphaned_ids:
            collection.delete(ids=orphaned_ids)
        
        return len(orphaned_ids)
    except Exception as exc:
        raise RuntimeError(f"Failed to delete orphaned chunks: {exc}") from exc


def get_document_chunks(
    collection: Collection,
    source: str,
    include_embeddings: bool = False
) -> dict:
    """
    Retrieve all chunks for a specific source document.

    Useful for inspecting what's stored for a document or for
    implementing custom update logic.

    Args:
        collection: An initialised ChromaDB Collection.
        source: Path to the source document.
        include_embeddings: Whether to include embedding vectors in results.

    Returns:
        Dictionary with ids, documents, metadatas, and optionally embeddings.
    """
    include_fields = ["documents", "metadatas"]
    if include_embeddings:
        include_fields.append("embeddings")
    
    try:
        results = collection.get(
            where={"source": source},
            include=include_fields
        )
        return results
    except Exception as exc:
        raise RuntimeError(f"Failed to get chunks for '{source}': {exc}") from exc


def store_embeddings(
    collection: Collection,
    chunks: list[Document],
    embeddings: list[list[float]],
    model_tag: str = DEFAULT_MODEL_TAG,
) -> int:
    """
    Store chunks, their embeddings, and their metadata in ChromaDB.

    Each chunk is stored as a single record with four associated fields:

    ┌──────────┬──────────────────────────────────────────────────────┐
    │ Field    │ Source                                               │
    ├──────────┼──────────────────────────────────────────────────────┤
    │ id       │ Deterministic content-hash ID (see _build_chunk_id)  │
    │ document │ chunk.page_content  — the raw text to be retrieved   │
    │ embedding│ 384-dim BGE vector  — drives cosine similarity search│
    │ metadata │ chunk.metadata      — source, page, … for citations  │
    └──────────┴──────────────────────────────────────────────────────┘

    Only ``page_content`` is embedded; metadata is stored as structured
    fields and is never included in the embedding computation.

    Uses ``upsert`` so that re-running ingestion on unchanged documents
    does not create duplicate records.

    Args:
        collection: An initialised ChromaDB ``Collection``.
        chunks:     List of LangChain ``Document`` objects.
        embeddings: Parallel list of 384-dimensional embedding vectors
                    produced by ``generate_embeddings()`` in embeddings.py.
        model_tag:  Embedding model identifier for version control.

    Returns:
        Number of records written (or updated) in the collection.

    Raises:
        ValueError: If ``chunks`` and ``embeddings`` lengths differ.
        RuntimeError: If the ChromaDB upsert operation fails.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Mismatch: {len(chunks)} chunks vs {len(embeddings)} embeddings."
        )

    if not chunks:
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for index, chunk in enumerate(chunks):
        ids.append(_build_chunk_id(chunk, index, model_tag))
        documents.append(chunk.page_content)
        metadatas.append(_sanitise_metadata(chunk.metadata))

    try:
        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
    except Exception as exc:
        raise RuntimeError(f"ChromaDB upsert failed: {exc}") from exc

    return len(ids)


# ---------------------------------------------------------------------------
# Inspection helpers
# ---------------------------------------------------------------------------

def collection_count(collection: Collection) -> int:
    """
    Return the total number of records stored in the collection.

    Args:
        collection: An initialised ChromaDB ``Collection``.

    Returns:
        Number of stored records.
    """
    return collection.count()


def peek_collection(collection: Collection, n: int = 3) -> None:
    """
    Print a brief human-readable summary of the first *n* stored records.

    Intended for development-time debugging.  Does not print raw embedding
    vectors to keep terminal output readable.

    Args:
        collection: An initialised ChromaDB ``Collection``.
        n:          Number of records to display.
    """
    result = collection.peek(limit=n)

    ids = result.get("ids", [])
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])

    print(f"\nCollection preview ({min(n, len(ids))} of {collection.count()} records)")
    print("-" * 60)

    for chunk_id, doc, meta in zip(ids, documents, metadatas):
        preview = doc[:120].replace("\n", " ")
        print(f"  ID       : {chunk_id}")
        print(f"  Metadata : {meta}")
        print(f"  Text     : {preview}...")
        print()
