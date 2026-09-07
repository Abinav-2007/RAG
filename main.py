"""
main.py
-------
Orchestrator for the RAG ingestion pipeline with intelligent document management.

Pipeline stages
---------------
Documents  →  Fingerprint Check  →  Loader  →  Chunking  →  Embedding  →  ChromaDB

Features
--------
- Document fingerprinting (SHA-256 hash) to detect changes
- Skip unchanged documents (saves compute)
- Delete stale chunks before re-ingesting modified documents
- Clean up orphaned chunks from deleted files
- Model version tracking for safe embedding upgrades
- Comprehensive logging and statistics

Usage
-----
python main.py ingest [--source path] [--force] [--clean-orphans]
python main.py query "Your question here"
python main.py status
"""

import argparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from document_loader import load_documents
from chunking import split_documents
from embeddings import load_embedding_model, generate_embeddings, EMBEDDING_MODEL
from vector_store import (
    create_collection,
    store_embeddings,
    delete_document_chunks,
    delete_orphaned_chunks,
    collection_count,
    peek_collection,
    DEFAULT_MODEL_TAG,
)
from document_registry import (
    create_registry_collection,
    register_document,
    check_document_changed,
    get_registered_documents,
    list_orphaned_chunks,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = "data"
MODEL_TAG = DEFAULT_MODEL_TAG  # "bge-small-en-v1.5"


# ---------------------------------------------------------------------------
# Ingestion with intelligent document management
# ---------------------------------------------------------------------------

def run_ingestion(
    source: str,
    force: bool = False,
    model_tag: str = MODEL_TAG
) -> dict:
    """
    Run the full document ingestion pipeline for a single source.

    Includes intelligent change detection:
    - Checks document fingerprint (SHA-256 hash)
    - Skips if unchanged (unless force=True)
    - Deletes old chunks before re-ingesting if changed

    Args:
        source: Path to the document file (PDF, DOCX, MD, TXT) or URL.
        force: If True, re-ingest even if document hasn't changed.
        model_tag: Embedding model identifier for version tracking.

    Returns:
        Dictionary with ingestion statistics.
    """
    collection = create_collection()
    registry = create_registry_collection()

    # Check if document has changed
    has_changed, reason = check_document_changed(registry, source, model_tag)

    if not has_changed and not force:
        print(f"  ⏭️  Skipped: Document unchanged")
        return {
            "status": "skipped",
            "reason": "unchanged",
            "chunks": 0
        }

    if force:
        print(f"  🔄 Force re-ingestion requested")
    elif has_changed:
        print(f"  📝 Change detected: {reason}")

    # Delete old chunks if document exists in store
    deleted = delete_document_chunks(collection, source, model_tag)
    if deleted > 0:
        print(f"  🗑️  Deleted {deleted} old chunks")

    # Stage 1 — Load
    print(f"  📄 Loading document...")
    documents = load_documents(source)

    # Stage 2 — Chunk (token-based)
    print(f"  ✂️  Chunking...")
    chunks = split_documents(documents, use_token_splitting=True)

    # Stage 3 — Embed
    print(f"  🧮 Generating embeddings...")
    model = load_embedding_model()
    embeddings = generate_embeddings(chunks, model)

    # Stage 4 — Store
    print(f"  💾 Storing in ChromaDB...")
    stored = store_embeddings(collection, chunks, embeddings, model_tag)

    # Stage 5 — Register
    register_document(registry, source, len(chunks), model_tag)

    # Summary
    stats = {
        "status": "success",
        "documents": len(documents),
        "chunks": len(chunks),
        "embeddings": len(embeddings),
        "stored": stored,
        "deleted": deleted,
    }

    print(f"  ✅ Success: {stored} chunks stored")
    return stats


def ingest_directory(
    directory: str = DEFAULT_DATA_DIR,
    force: bool = False,
    clean_orphans: bool = False
) -> None:
    """
    Ingest all supported documents in a directory.

    Args:
        directory: Path to directory containing documents.
        force: If True, re-ingest all documents regardless of changes.
        clean_orphans: If True, delete chunks from deleted source files.
    """
    supported_extensions = {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx"}
    data_path = Path(directory)

    if not data_path.exists():
        print(f"❌ Error: Directory not found: {directory}")
        return

    # Find all supported documents
    sources = []
    for ext in supported_extensions:
        sources.extend(data_path.glob(f"*{ext}"))

    if not sources:
        print(f"⚠️  No supported documents found in {directory}")
        return

    print(f"\n{'='*60}")
    print(f"📚 RAG INGESTION PIPELINE")
    print(f"{'='*60}")
    print(f"Directory     : {directory}")
    print(f"Documents     : {len(sources)}")
    print(f"Model         : {EMBEDDING_MODEL}")
    print(f"Model Tag     : {MODEL_TAG}")
    print(f"Force         : {force}")
    print(f"Clean Orphans : {clean_orphans}")
    print(f"{'='*60}\n")

    # Clean orphans first if requested
    if clean_orphans:
        collection = create_collection()
        registry = create_registry_collection()

        orphaned_docs = list_orphaned_chunks(registry, directory)
        if orphaned_docs:
            print(f"🗑️  Cleaning {len(orphaned_docs)} orphaned documents...\n")
            for orphaned_source in orphaned_docs:
                deleted = delete_document_chunks(collection, orphaned_source)
                print(f"  Deleted {deleted} chunks from: {orphaned_source}")
            print()

    # Ingest each document
    total_stats = {
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "total_chunks": 0
    }

    for source in sources:
        print(f"📄 {source.name}")
        print("-" * 60)
        try:
            stats = run_ingestion(str(source), force=force)
            if stats["status"] == "success":
                total_stats["success"] += 1
                total_stats["total_chunks"] += stats["chunks"]
            elif stats["status"] == "skipped":
                total_stats["skipped"] += 1
        except Exception as exc:
            total_stats["failed"] += 1
            print(f"  ❌ Failed: {exc}")
        print()

    # Final summary
    collection = create_collection()
    print(f"{'='*60}")
    print(f"📊 INGESTION SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Success  : {total_stats['success']}")
    print(f"⏭️  Skipped  : {total_stats['skipped']}")
    print(f"❌ Failed   : {total_stats['failed']}")
    print(f"📦 Chunks   : {total_stats['total_chunks']} created")
    print(f"💾 Database : {collection_count(collection)} total chunks")
    print(f"{'='*60}\n")


def show_status() -> None:
    """Display current pipeline status and statistics."""
    collection = create_collection()
    registry = create_registry_collection()

    print(f"\n{'='*60}")
    print(f"📊 RAG PIPELINE STATUS")
    print(f"{'='*60}")

    # Vector store stats
    total_chunks = collection_count(collection)
    print(f"💾 Vector Store:")
    print(f"   Total chunks: {total_chunks}")

    # Registry stats
    registered = get_registered_documents(registry)
    print(f"\n📚 Document Registry:")
    print(f"   Registered documents: {len(registered)}")

    if registered:
        print(f"\n   Documents:")
        for doc in registered:
            source = Path(doc["source"]).name
            chunks = doc.get("chunk_count", "?")
            model = doc.get("model_tag", "unknown")
            last_ingested = doc.get("last_ingested", "unknown")
            exists = "✅" if Path(doc["source"]).exists() else "❌"
            print(f"   {exists} {source}: {chunks} chunks, {model}, {last_ingested}")

    # Check for orphans
    orphaned = list_orphaned_chunks(registry, DEFAULT_DATA_DIR)
    if orphaned:
        print(f"\n⚠️  Orphaned documents (source files deleted):")
        for source in orphaned:
            print(f"   - {source}")
        print(f"   Run 'python main.py ingest --clean-orphans' to remove")

    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="RAG Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py ingest                    # Ingest all documents in data/
  python main.py ingest --force            # Force re-ingest all
  python main.py ingest --clean-orphans    # Clean orphaned chunks first
  python main.py ingest --source file.pdf  # Ingest single file
  python main.py status                    # Show pipeline status
  python main.py query "What is...?"       # Query the knowledge base
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest documents")
    ingest_parser.add_argument(
        "--source",
        type=str,
        help="Single document to ingest (default: ingest all in data/)"
    )
    ingest_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-ingestion even if document hasn't changed"
    )
    ingest_parser.add_argument(
        "--clean-orphans",
        action="store_true",
        help="Delete chunks from deleted source files before ingesting"
    )

    # Status command
    subparsers.add_parser("status", help="Show pipeline status")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query the knowledge base")
    query_parser.add_argument("question", type=str, help="Question to ask")
    query_parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of chunks to retrieve (default: 5)"
    )
    query_parser.add_argument(
        "--llm",
        type=str,
        choices=["openai", "google"],
        default="openai",
        help="LLM provider (default: openai)"
    )

    args = parser.parse_args()

    if args.command == "ingest":
        if args.source:
            # Ingest single file
            print(f"\n{'='*60}")
            print(f"📄 Ingesting: {args.source}")
            print(f"{'='*60}\n")
            try:
                stats = run_ingestion(args.source, force=args.force)
                print(f"\n✅ Done: {stats}")
            except Exception as exc:
                print(f"\n❌ Error: {exc}")
        else:
            # Ingest directory
            ingest_directory(
                directory=DEFAULT_DATA_DIR,
                force=args.force,
                clean_orphans=args.clean_orphans
            )

    elif args.command == "status":
        show_status()

    elif args.command == "query":
        # Import retriever only when needed
        from retriever import RAGRetriever

        print(f"\n🔍 Querying knowledge base...")
        retriever = RAGRetriever(
            llm_provider=args.llm,
            top_k=args.top_k
        )
        retriever.query(args.question, verbose=True)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()