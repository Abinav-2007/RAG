# RAG Pipeline

A production-grade Retrieval-Augmented Generation (RAG) system with intelligent document management, token-based chunking, and multi-model LLM support.

## 🚀 Features

- **Intelligent Document Management**: SHA-256 fingerprinting with automatic change detection
- **Token-Based Chunking**: Accurate token counts using tiktoken (512 tokens/chunk, 50 token overlap)
- **Stable ID Generation**: Deterministic chunk IDs with model versioning
- **Stale Chunk Cleanup**: Automatic deletion of outdated chunks when documents change
- **Multi-Model LLM Support**: OpenAI (GPT-4, GPT-3.5) and Google Gemini
- **Semantic Search**: BGE embeddings with ChromaDB vector store
- **Citation Tracking**: Source attribution in generated answers
- **CLI Interface**: Easy-to-use commands for ingestion, querying, and status

## 📋 Prerequisites

- Python 3.12+
- uv (recommended) or pip
- OpenAI API key and/or Google Gemini API key

## 🔧 Installation

1. Clone the repository
2. Install dependencies: `uv sync` or `pip install -e .`
3. Set up environment variables: `cp .env.example .env` (then add your API keys)

## 🎯 Usage

### Ingest Documents
```bash
python main.py ingest                    # Ingest all documents in data/
python main.py ingest --source file.pdf  # Ingest single file
python main.py ingest --force            # Force re-ingestion
python main.py ingest --clean-orphans    # Clean deleted files first
```

### Query Knowledge Base
```bash
python main.py query "What is the main topic?"
python main.py query "Summarize" --llm google --top-k 10
```

### Check Status
```bash
python main.py status
```

## 🔐 Security

**CRITICAL**: Never commit `.env` with real API keys! See `SECURITY.md` for details.

## 📁 Project Structure

- `main.py` — CLI orchestrator
- `document_registry.py` — Fingerprinting & change detection
- `chunking.py` — Token-based text splitting
- `vector_store.py` — ChromaDB with stable IDs
- `retriever.py` — Query pipeline with LLM integration

See full documentation for advanced configuration and troubleshooting.
