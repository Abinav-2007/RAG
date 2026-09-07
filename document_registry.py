"""
document_registry.py
--------------------
Document fingerprinting and change detection system for RAG pipeline.

Purpose
-------
Tracks which documents have been ingested and detects when they've been
modified, preventing unnecessary re-ingestion and enabling smart incremental
updates.

Registry storage
----------------
Uses a separate ChromaDB collection to store document fingerprints:
    - ID: source file path
    - Document: file hash (SHA-256)
    - Metadata: mtime, size, last_ingested, chunk_count

Benefits
--------
- Skip unchanged documents during re-runs (saves compute)
- Detect modifications and trigger re-ingestion
- Track ingestion history per document
- Support deletion detection (files removed from source)
"""

import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional

import chromadb
from chromadb import Collection


# Registry configuration
REGISTRY_COLLECTION_NAME = "document_registry"


def _compute_file_hash(file_path: str) -> str:
    """
    Compute SHA-256 hash of file content.
    
    Args:
        file_path: Path to the file.
        
    Returns:
        Hexadecimal SHA-256 hash string.
        
    Raises:
        FileNotFoundError: If file does not exist.
        RuntimeError: If file cannot be read.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    try:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            # Read in chunks for memory efficiency with large files
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as exc:
        raise RuntimeError(f"Failed to compute hash for '{file_path}': {exc}") from exc


def _get_file_metadata(file_path: str) -> dict:
    """
    Extract file metadata (size, modification time).
    
    Args:
        file_path: Path to the file.
        
    Returns:
        Dictionary with size and mtime.
    """
    path = Path(file_path)
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtime_readable": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def create_registry_collection(
    persist_dir: str = "chroma_db"
) -> Collection:
    """
    Initialize the document registry collection.
    
    Args:
        persist_dir: Path to ChromaDB persist directory.
        
    Returns:
        ChromaDB Collection for document registry.
    """
    client = chromadb.PersistentClient(path=persist_dir)
    
    collection = client.get_or_create_collection(
        name=REGISTRY_COLLECTION_NAME,
        metadata={"description": "Document fingerprints and ingestion history"}
    )
    
    return collection


def register_document(
    registry: Collection,
    source: str,
    chunk_count: int,
    model_tag: str = "bge-small-en-v1.5"
) -> None:
    """
    Register or update a document in the registry after successful ingestion.
    
    Args:
        registry: Registry collection.
        source: Path to the source document.
        chunk_count: Number of chunks created from this document.
        model_tag: Embedding model used for ingestion.
        
    Raises:
        RuntimeError: If registration fails.
    """
    try:
        file_hash = _compute_file_hash(source)
        file_meta = _get_file_metadata(source)
        
        metadata = {
            "source": source,
            "chunk_count": chunk_count,
            "model_tag": model_tag,
            "last_ingested": datetime.now().isoformat(),
            "size": file_meta["size"],
            "mtime": file_meta["mtime"],
            "mtime_readable": file_meta["mtime_readable"],
        }
        
        # Use source path as ID for easy lookup
        registry.upsert(
            ids=[source],
            documents=[file_hash],  # Store hash as the document field
            metadatas=[metadata]
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to register document '{source}': {exc}") from exc


def check_document_changed(
    registry: Collection,
    source: str,
    model_tag: str = "bge-small-en-v1.5"
) -> tuple[bool, Optional[str]]:
    """
    Check if a document has changed since last ingestion.
    
    Args:
        registry: Registry collection.
        source: Path to the source document.
        model_tag: Current embedding model being used.
        
    Returns:
        Tuple of (has_changed, reason):
            - has_changed: True if document needs re-ingestion
            - reason: Human-readable explanation (or None if unchanged)
    """
    try:
        # Check if document exists in registry
        result = registry.get(
            ids=[source],
            include=["documents", "metadatas"]
        )
        
        if not result["ids"]:
            return True, "Document not in registry (first ingestion)"
        
        # Get stored hash and metadata
        stored_hash = result["documents"][0]
        stored_meta = result["metadatas"][0]
        
        # Check model version
        stored_model = stored_meta.get("model_tag", "unknown")
        if stored_model != model_tag:
            return True, f"Model changed: {stored_model} → {model_tag}"
        
        # Compute current hash
        if not Path(source).exists():
            return True, "Source file no longer exists"
        
        current_hash = _compute_file_hash(source)
        
        if current_hash != stored_hash:
            return True, "File content has changed"
        
        # Check mtime as fast preliminary check (optional optimization)
        current_meta = _get_file_metadata(source)
        if current_meta["mtime"] != stored_meta.get("mtime"):
            # Hash already checked above, so if hash matches, just update mtime
            # This shouldn't happen often, but covers edge cases
            pass
        
        return False, None
        
    except Exception as exc:
        # If check fails, err on the side of re-ingesting
        return True, f"Error during check: {exc}"


def get_registered_documents(registry: Collection) -> list[dict]:
    """
    Get all registered documents with their metadata.
    
    Args:
        registry: Registry collection.
        
    Returns:
        List of dictionaries with document info.
    """
    try:
        result = registry.get(include=["documents", "metadatas"])
        
        documents = []
        for source, file_hash, metadata in zip(
            result["ids"],
            result["documents"],
            result["metadatas"]
        ):
            doc_info = {
                "source": source,
                "file_hash": file_hash,
                **metadata
            }
            documents.append(doc_info)
        
        return documents
    except Exception:
        return []


def unregister_document(registry: Collection, source: str) -> bool:
    """
    Remove a document from the registry.
    
    Useful when a source file has been deleted and you want to clean up
    both the registry and the vector store.
    
    Args:
        registry: Registry collection.
        source: Path to the source document.
        
    Returns:
        True if document was found and removed, False otherwise.
    """
    try:
        result = registry.get(ids=[source])
        if result["ids"]:
            registry.delete(ids=[source])
            return True
        return False
    except Exception:
        return False


def list_orphaned_chunks(
    registry: Collection,
    source_directory: str = "data"
) -> list[str]:
    """
    Find documents in the registry that no longer exist in the source directory.
    
    Args:
        registry: Registry collection.
        source_directory: Directory to scan for existing files.
        
    Returns:
        List of source paths that are registered but files are missing.
    """
    registered = get_registered_documents(registry)
    orphaned = []
    
    for doc in registered:
        source = doc["source"]
        if not Path(source).exists():
            orphaned.append(source)
    
    return orphaned


if __name__ == "__main__":
    # Demo usage
    registry = create_registry_collection()
    
    print("=== Document Registry ===\n")
    
    registered_docs = get_registered_documents(registry)
    if registered_docs:
        print(f"Found {len(registered_docs)} registered documents:\n")
        for doc in registered_docs:
            print(f"Source: {doc['source']}")
            print(f"  Hash: {doc['file_hash'][:16]}...")
            print(f"  Model: {doc.get('model_tag', 'unknown')}")
            print(f"  Chunks: {doc.get('chunk_count', 'unknown')}")
            print(f"  Last ingested: {doc.get('last_ingested', 'unknown')}")
            print()
    else:
        print("No documents registered yet.")
    
    # Check for orphaned chunks
    orphaned = list_orphaned_chunks(registry)
    if orphaned:
        print(f"\n⚠️  Found {len(orphaned)} orphaned documents:")
        for source in orphaned:
            print(f"  - {source}")
