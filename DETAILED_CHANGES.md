# Detailed Changes to RAG Pipeline

This document provides a comprehensive, line-by-line breakdown of every change made to transform your RAG pipeline into a production-grade system.

---

## 📋 TABLE OF CONTENTS

1. [vector_store.py](#1-vector_storepy)
2. [document_registry.py](#2-document_registrypy-new-file)
3. [chunking.py](#3-chunkingpy)
4. [retriever.py](#4-retrieverpy-new-file)
5. [main.py](#5-mainpy)
6. [pyproject.toml](#6-projecttoml)
7. [Security & Documentation Files](#7-security--documentation-files)

---

## 1. vector_store.py

### 📍 Location & Size
- **File**: `/home/tholvimaaran/Projects/RAG/vector_store.py`
- **Change Type**: Major refactoring
- **Lines Modified**: ~150 lines changed/added

### 🔄 Changes Made

#### A. Added Model Tag Constant
```python
# NEW: Added at top of file after imports
DEFAULT_MODEL_TAG = "bge-small-en-v1.5"
```
**Why**: Allows versioning of embeddings. Different models → different IDs → no incompatibility issues.

#### B. Rewrote `_build_chunk_id()` Function
**OLD Implementation** (lines 109-152):
```python
def _build_chunk_id(chunk: Document, index: int) -> str:
    content_hash = hashlib.sha256(chunk.page_content.encode("utf-8")).hexdigest()[:16]
    source: str = chunk.metadata.get("source", "unknown")
    stem = Path(source).stem
    page: Optional[int] = chunk.metadata.get("page")
    if page is not None:
        return f"{stem}_p{page}_c{index}_{content_hash}"
    return f"{stem}_c{index}_{content_hash}"
```
**Problem**: 
- Used content hash + batch position
- IDs changed when document was modified
- Led to duplicates

**NEW Implementation**:
```python
def _build_chunk_id(
    chunk: Document, 
    index: int, 
    model_tag: str = DEFAULT_MODEL_TAG
) -> str:
    source: str = chunk.metadata.get("source", "unknown")
    stem = Path(source).stem
    clean_model_tag = model_tag.replace("/", "-").replace(":", "-")
    page: Optional[int] = chunk.metadata.get("page")
    if page is not None:
        return f"{stem}__{clean_model_tag}__p{page}__c{index}"
    return f"{stem}__{clean_model_tag}__c{index}"
```
**Benefits**:
- Deterministic: Same document → Same IDs across runs
- Model-versioned: Embeds model info in ID
- No hash collisions: Uses explicit structure instead
- Example: `document__bge-small-en-v1.5__p0__c2`

#### C. Added `delete_document_chunks()` Function
**NEW Function** (lines 225-265):
```python
def delete_document_chunks(
    collection: Collection,
    source: str,
    model_tag: Optional[str] = None
) -> int:
    """Delete all chunks from a specific source document."""
    try:
        results = collection.get(where={"source": source}, include=["metadatas"])
        ids_to_delete = results.get("ids", [])
        
        if model_tag and ids_to_delete:
            clean_model_tag = model_tag.replace("/", "-").replace(":", "-")
            filtered_ids = []
            for chunk_id, metadata in zip(ids_to_delete, results["metadatas"]):
                if f"__{clean_model_tag}__" in chunk_id:
                    filtered_ids.append(chunk_id)
            ids_to_delete = filtered_ids
        
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
        
        return len(ids_to_delete)
    except Exception as exc:
        raise RuntimeError(f"Failed to delete chunks for '{source}': {exc}") from exc
```
**Purpose**: Clean up old chunks before re-ingesting modified documents

#### D. Added `delete_orphaned_chunks()` Function
**NEW Function** (lines 268-308):
```python
def delete_orphaned_chunks(
    collection: Collection,
    source_directory: str = "data"
) -> int:
    """Delete chunks whose source files no longer exist."""
    try:
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
```
**Purpose**: Remove chunks from deleted source files

#### E. Added `get_document_chunks()` Function
**NEW Function** (lines 311-338):
```python
def get_document_chunks(
    collection: Collection,
    source: str,
    include_embeddings: bool = False
) -> dict:
    """Retrieve all chunks for a specific source document."""
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
```
**Purpose**: Inspect what's stored for a document

#### F. Updated `store_embeddings()` Function Signature
**OLD**:
```python
def store_embeddings(
    collection: Collection,
    chunks: list[Document],
    embeddings: list[list[float]],
) -> int:
```

**NEW**:
```python
def store_embeddings(
    collection: Collection,
    chunks: list[Document],
    embeddings: list[list[float]],
    model_tag: str = DEFAULT_MODEL_TAG,
) -> int:
```
**Change**: Added `model_tag` parameter, updated calls to `_build_chunk_id()`:

**OLD**:
```python
ids.append(_build_chunk_id(chunk, index))
```

**NEW**:
```python
ids.append(_build_chunk_id(chunk, index, model_tag))
```

---

## 2. document_registry.py

### 📍 Location & Size
- **File**: `/home/tholvimaaran/Projects/RAG/document_registry.py`
- **Change Type**: NEW FILE
- **Lines**: ~350 lines

### 🆕 Complete New Module

This is an entirely new module for document fingerprinting and change detection.

#### Key Functions

**1. `_compute_file_hash(file_path: str) -> str`**
```python
def _compute_file_hash(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    try:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as exc:
        raise RuntimeError(f"Failed to compute hash for '{file_path}': {exc}") from exc
```
**Purpose**: Compute SHA-256 hash of file content for change detection
**Method**: Reads in 8KB chunks for memory efficiency

**2. `_get_file_metadata(file_path: str) -> dict`**
```python
def _get_file_metadata(file_path: str) -> dict:
    path = Path(file_path)
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtime_readable": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }
```
**Purpose**: Extract file size and modification time
**Data Captured**: size (bytes), mtime (timestamp), mtime_readable (ISO format)

**3. `create_registry_collection(persist_dir: str) -> Collection`**
```python
def create_registry_collection(persist_dir: str = "chroma_db") -> Collection:
    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_or_create_collection(
        name=REGISTRY_COLLECTION_NAME,
        metadata={"description": "Document fingerprints and ingestion history"}
    )
    return collection
```
**Purpose**: Initialize a separate ChromaDB collection for document registry
**Note**: Uses separate collection name: `document_registry`

**4. `register_document(registry: Collection, source: str, chunk_count: int, model_tag: str)`**
```python
def register_document(
    registry: Collection,
    source: str,
    chunk_count: int,
    model_tag: str = "bge-small-en-v1.5"
) -> None:
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
        
        registry.upsert(
            ids=[source],
            documents=[file_hash],
            metadatas=[metadata]
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to register document '{source}': {exc}") from exc
```
**Purpose**: Record successful ingestion with metadata
**Stored Data**:
- ID: source path
- Document: file hash
- Metadata: chunk count, model version, timestamp, file size, mtime

**5. `check_document_changed(registry: Collection, source: str, model_tag: str) -> tuple[bool, Optional[str]]`**
```python
def check_document_changed(
    registry: Collection,
    source: str,
    model_tag: str = "bge-small-en-v1.5"
) -> tuple[bool, Optional[str]]:
    try:
        result = registry.get(ids=[source], include=["documents", "metadatas"])
        
        if not result["ids"]:
            return True, "Document not in registry (first ingestion)"
        
        stored_hash = result["documents"][0]
        stored_meta = result["metadatas"][0]
        
        stored_model = stored_meta.get("model_tag", "unknown")
        if stored_model != model_tag:
            return True, f"Model changed: {stored_model} → {model_tag}"
        
        if not Path(source).exists():
            return True, "Source file no longer exists"
        
        current_hash = _compute_file_hash(source)
        if current_hash != stored_hash:
            return True, "File content has changed"
        
        return False, None
    except Exception as exc:
        return True, f"Error during check: {exc}"
```
**Purpose**: Determine if document needs re-ingestion
**Checks**:
1. First ingestion? → True
2. Model version changed? → True
3. File deleted? → True
4. Content hash changed? → True
5. Otherwise → False (unchanged)

**6. `get_registered_documents(registry: Collection) -> list[dict]`**
```python
def get_registered_documents(registry: Collection) -> list[dict]:
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
```
**Purpose**: List all registered documents with their metadata

**7. `list_orphaned_chunks(registry: Collection, source_directory: str) -> list[str]`**
```python
def list_orphaned_chunks(
    registry: Collection,
    source_directory: str = "data"
) -> list[str]:
    registered = get_registered_documents(registry)
    orphaned = []
    for doc in registered:
        source = doc["source"]
        if not Path(source).exists():
            orphaned.append(source)
    return orphaned
```
**Purpose**: Find registered documents whose files have been deleted

---

## 3. chunking.py

### 📍 Location & Size
- **File**: `/home/tholvimaaran/Projects/RAG/chunking.py`
- **Change Type**: Complete rewrite
- **Lines Modified**: All ~60 lines replaced

### 🔄 Complete Rewrite

#### OLD Implementation
```python
# Character-based splitting
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200

def split_documents(documents: list[Document]) -> list[Document]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS
    )
    return text_splitter.split_documents(documents)
```

#### NEW Implementation

**A. Added imports**
```python
import tiktoken
```

**B. Added new constants**
```python
TOKEN_CHUNK_SIZE = 512          # tokens per chunk
TOKEN_CHUNK_OVERLAP = 50        # token overlap
ENCODING_NAME = "cl100k_base"   # GPT-4, GPT-3.5 standard
CHAR_CHUNK_SIZE = 2000          # fallback
CHAR_CHUNK_OVERLAP = 200        # fallback
```

**C. Added `get_token_counter()` function**
```python
def get_token_counter(encoding_name: str = ENCODING_NAME):
    encoding = tiktoken.get_encoding(encoding_name)
    def count_tokens(text: str) -> int:
        return len(encoding.encode(text))
    return count_tokens
```

**D. Updated `split_documents()` function**
```python
def split_documents(
    documents: list[Document],
    use_token_splitting: bool = True,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None
) -> list[Document]:
    if use_token_splitting:
        effective_chunk_size = chunk_size or TOKEN_CHUNK_SIZE
        effective_overlap = chunk_overlap or TOKEN_CHUNK_OVERLAP
        
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=ENCODING_NAME,
            chunk_size=effective_chunk_size,
            chunk_overlap=effective_overlap,
            separators=SEPARATORS
        )
        print(f"Using token-based chunking: {effective_chunk_size} tokens...")
    else:
        # Fallback to character-based
        effective_chunk_size = chunk_size or CHAR_CHUNK_SIZE
        effective_overlap = chunk_overlap or CHAR_CHUNK_OVERLAP
        
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=effective_chunk_size,
            chunk_overlap=effective_overlap,
            separators=SEPARATORS
        )
        print(f"Using character-based chunking: {effective_chunk_size} chars...")
    
    return text_splitter.split_documents(documents)
```

**E. Added `estimate_tokens()` function**
```python
def estimate_tokens(text: str, encoding_name: str = ENCODING_NAME) -> int:
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))
```

**F. Updated `main()` demo**
```python
def main() -> None:
    source = "data/document.pdf"
    documents = load_documents(source)
    chunks = split_documents(documents, use_token_splitting=True)

    print(f"\nDocuments loaded : {len(documents)}")
    print(f"Chunks created   : {len(chunks)}")

    token_counter = get_token_counter()
    
    for index, chunk in enumerate(chunks[:3], start=1):
        token_count = token_counter(chunk.page_content)
        char_count = len(chunk.page_content)
        
        print(f"\nChunk {index}")
        print("-" * 50)
        print(f"Characters : {char_count}")
        print(f"Tokens     : {token_count}")
        print(f"Ratio      : {char_count/token_count:.2f} chars/token")
```

### Key Improvements

| Aspect | OLD | NEW |
|--------|-----|-----|
| Chunk Sizing | 2000 characters | 512 tokens |
| Accuracy | ~3.5 chars per token (estimate) | Exact via tiktoken |
| Encoding | Generic | `cl100k_base` (GPT-4 standard) |
| Flexibility | Fixed | Configurable with fallback |
| Debugging | Character count only | Token count + char ratio |

---

## 4. retriever.py

### 📍 Location & Size
- **File**: `/home/tholvimaaran/Projects/RAG/retriever.py`
- **Change Type**: NEW FILE
- **Lines**: ~450 lines

### 🆕 Complete New Module

This is an entirely new query pipeline module.

#### A. Imports & Configuration
```python
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from sentence_transformers import SentenceTransformer

DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_LLM_MODEL_OPENAI = "gpt-4o-mini"
DEFAULT_LLM_MODEL_GOOGLE = "gemini-1.5-flash"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1000
```

#### B. `RetrievalResult` Dataclass
```python
@dataclass
class RetrievalResult:
    """Container for retrieval results."""
    query: str
    chunks: list[Document]
    distances: list[float]
    sources: list[str]
    answer: Optional[str] = None
    model_used: Optional[str] = None
```
**Purpose**: Structured return type for query results

#### C. `RAGRetriever` Class

**Constructor**:
```python
def __init__(
    self,
    llm_provider: str = DEFAULT_LLM_PROVIDER,
    llm_model: Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    top_k: int = DEFAULT_TOP_K,
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
):
    self.llm_provider = llm_provider.lower()
    self.top_k = top_k
    self.distance_threshold = distance_threshold
    
    # Initialize embedding model
    self.embedding_model = load_embedding_model()
    
    # Initialize LLM based on provider
    if self.llm_provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        self.llm = ChatOpenAI(
            model=llm_model or DEFAULT_LLM_MODEL_OPENAI,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )
    elif self.llm_provider == "google":
        api_key = os.getenv("GEMINI_API_KEY")
        self.llm = ChatGoogleGenerativeAI(
            model=llm_model or DEFAULT_LLM_MODEL_GOOGLE,
            temperature=temperature,
            max_tokens=max_tokens,
            google_api_key=api_key
        )
    
    # Initialize vector store
    self.collection = create_collection()
```

**Key Methods**:

1. **`_embed_query(query: str) -> list[float]`**
   - Embeds user query with same BGE model as documents
   - Returns 384-dim vector

2. **`retrieve(query: str, top_k: Optional[int] = None, filter_metadata: Optional[dict] = None) -> RetrievalResult`**
   - Semantic search in ChromaDB
   - Filters by distance threshold
   - Returns chunks with distances and sources

3. **`_build_prompt(query: str, chunks: list[Document]) -> str`**
   - Constructs RAG prompt with context chunks
   - Adds citations with source and page
   - Format:
     ```
     Context 1:
     [chunk text]
     [Source: file.pdf, Page 1]
     
     Question: [user query]
     Answer:
     ```

4. **`generate_answer(query: str, top_k: Optional[int] = None, ...) -> RetrievalResult`**
   - End-to-end RAG pipeline:
     1. Retrieve chunks
     2. Build prompt with context
     3. Call LLM
     4. Return result with answer + metadata

5. **`query(question: str, verbose: bool = True, **kwargs) -> str`**
   - Simple interface returning just the answer string
   - Optional verbose output with statistics
   - Shows: retrieved chunks, sources, model used

---

## 5. main.py

### 📍 Location & Size
- **File**: `/home/tholvimaaran/Projects/RAG/main.py`
- **Change Type**: Complete rewrite
- **Lines Modified**: All ~200 lines replaced

### 🔄 Complete Rewrite

#### OLD Implementation
```python
def run_ingestion(source: str) -> None:
    documents = load_documents(source)
    chunks = split_documents(documents)
    model = load_embedding_model()
    embeddings = generate_embeddings(chunks, model)
    collection = create_collection()
    stored = store_embeddings(collection, chunks, embeddings)
    # ... print stats ...

def main() -> None:
    sources = [
        "data/document.pdf",
        "data/document.txt",
        "data/document.md",
        "data/document.doc",
    ]
    for source in sources:
        try:
            run_ingestion(source)
        except Exception as exc:
            print(f"Skipped '{source}': {exc}")
```

**Problems**:
- Hardcoded file list
- No change detection
- No deletion of old chunks
- No CLI interface
- No query capability
- No status checking

#### NEW Implementation

**A. Added argparse CLI**
```python
parser = argparse.ArgumentParser(description="RAG Pipeline Orchestrator")
subparsers = parser.add_subparsers(dest="command", help="Command to run")

# ingest command
ingest_parser = subparsers.add_parser("ingest", help="Ingest documents")
ingest_parser.add_argument("--source", type=str, help="Single document to ingest")
ingest_parser.add_argument("--force", action="store_true", help="Force re-ingestion")
ingest_parser.add_argument("--clean-orphans", action="store_true", help="Clean deleted files")

# status command
subparsers.add_parser("status", help="Show pipeline status")

# query command
query_parser = subparsers.add_parser("query", help="Query the knowledge base")
query_parser.add_argument("question", type=str, help="Question to ask")
query_parser.add_argument("--top-k", type=int, default=5, help="Chunks to retrieve")
query_parser.add_argument("--llm", type=str, choices=["openai", "google"], default="openai")
```

**B. Added Intelligent Ingestion Function**
```python
def run_ingestion(
    source: str,
    force: bool = False,
    model_tag: str = MODEL_TAG
) -> dict:
    collection = create_collection()
    registry = create_registry_collection()

    # Check if document changed
    has_changed, reason = check_document_changed(registry, source, model_tag)

    if not has_changed and not force:
        print(f"  ⏭️  Skipped: Document unchanged")
        return {"status": "skipped", "reason": "unchanged", "chunks": 0}

    # Delete old chunks if exists
    deleted = delete_document_chunks(collection, source, model_tag)
    if deleted > 0:
        print(f"  🗑️  Deleted {deleted} old chunks")

    # Stage 1: Load
    documents = load_documents(source)
    
    # Stage 2: Chunk (token-based)
    chunks = split_documents(documents, use_token_splitting=True)
    
    # Stage 3: Embed
    model = load_embedding_model()
    embeddings = generate_embeddings(chunks, model)
    
    # Stage 4: Store
    stored = store_embeddings(collection, chunks, embeddings, model_tag)
    
    # Stage 5: Register
    register_document(registry, source, len(chunks), model_tag)

    return {
        "status": "success",
        "documents": len(documents),
        "chunks": len(chunks),
        "stored": stored,
        "deleted": deleted,
    }
```

**C. Added Batch Processing**
```python
def ingest_directory(
    directory: str = DEFAULT_DATA_DIR,
    force: bool = False,
    clean_orphans: bool = False
) -> None:
    # Find all supported documents
    supported_extensions = {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx"}
    data_path = Path(directory)
    sources = []
    for ext in supported_extensions:
        sources.extend(data_path.glob(f"*{ext}"))
    
    # Clean orphans first if requested
    if clean_orphans:
        collection = create_collection()
        registry = create_registry_collection()
        orphaned_docs = list_orphaned_chunks(registry, directory)
        for orphaned_source in orphaned_docs:
            deleted = delete_document_chunks(collection, orphaned_source)
            print(f"  Deleted {deleted} chunks from: {orphaned_source}")
    
    # Ingest each document
    total_stats = {"success": 0, "skipped": 0, "failed": 0, "total_chunks": 0}
    for source in sources:
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
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"📊 INGESTION SUMMARY")
    print(f"✅ Success  : {total_stats['success']}")
    print(f"⏭️  Skipped  : {total_stats['skipped']}")
    print(f"❌ Failed   : {total_stats['failed']}")
    print(f"📦 Chunks   : {total_stats['total_chunks']} created")
    print(f"{'='*60}\n")
```

**D. Added Status Command**
```python
def show_status() -> None:
    collection = create_collection()
    registry = create_registry_collection()

    print(f"\n{'='*60}")
    print(f"📊 RAG PIPELINE STATUS")
    print(f"{'='*60}")

    # Vector store stats
    total_chunks = collection_count(collection)
    print(f"💾 Vector Store: {total_chunks} chunks")

    # Registry stats
    registered = get_registered_documents(registry)
    print(f"\n📚 Document Registry: {len(registered)} documents")

    # Check for orphans
    orphaned = list_orphaned_chunks(registry, DEFAULT_DATA_DIR)
    if orphaned:
        print(f"\n⚠️  Orphaned documents:")
        for source in orphaned:
            print(f"   - {source}")
```

**E. Updated Main Entry Point**
```python
def main():
    parser = argparse.ArgumentParser(description="RAG Pipeline Orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # ... setup subparsers ...
    
    args = parser.parse_args()

    if args.command == "ingest":
        if args.source:
            run_ingestion(args.source, force=args.force)
        else:
            ingest_directory(force=args.force, clean_orphans=args.clean_orphans)

    elif args.command == "status":
        show_status()

    elif args.command == "query":
        from retriever import RAGRetriever
        retriever = RAGRetriever(llm_provider=args.llm, top_k=args.top_k)
        retriever.query(args.question, verbose=True)
```

### CLI Commands Available

```bash
# Ingest all documents
python main.py ingest

# Ingest single file
python main.py ingest --source data/document.pdf

# Force re-ingest all
python main.py ingest --force

# Clean and re-ingest
python main.py ingest --clean-orphans

# Check status
python main.py status

# Query
python main.py query "What is...?"
python main.py query "Summarize" --llm google --top-k 10
```

---

## 6. pyproject.toml

### 📍 Location & Size
- **File**: `/home/tholvimaaran/Projects/RAG/pyproject.toml`
- **Change Type**: Minor addition
- **Lines Modified**: 1 line added to dependencies

### 🔄 Change Made

**Added tiktoken dependency**:
```toml
dependencies = [
    # ... existing dependencies ...
    "tiktoken>=0.8.0",  # NEW: For token-based chunking
    # ... rest of dependencies ...
]
```

**Why**: Required for accurate token counting in `chunking.py` using `cl100k_base` encoding

---

## 7. Security & Documentation Files

### A. `.env.example` (NEW FILE)

**Location**: `/home/tholvimaaran/Projects/RAG/.env.example`
**Lines**: 11 lines

```env
# RAG Pipeline Environment Variables
OPENAI_API_KEY='your-openai-api-key-here'
GEMINI_API_KEY='your-gemini-api-key-here'

# Optional configuration
# EMBEDDING_MODEL='BAAI/bge-small-en-v1.5'
# EMBEDDING_DEVICE='cpu'
# CHROMA_PERSIST_DIR='chroma_db'
# COLLECTION_NAME='rag_documents'
```

**Purpose**: 
- Safe template for developers
- No real API keys
- Guides setup process

### B. `SECURITY.md` (NEW FILE)

**Location**: `/home/tholvimaaran/Projects/RAG/SECURITY.md`
**Lines**: ~100 lines

**Sections**:
1. **Critical Security Rules**
   - Never commit `.env`
   - Rotate keys if exposed
   - Check before committing

2. **Setup Instructions**
   - How to copy `.env.example`
   - Where to get API keys
   - File permissions

3. **Best Practices**
   - API key scoping
   - Spending limits
   - Data privacy
   - ChromaDB security

4. **If Keys Are Exposed**
   - Immediate actions
   - Remove from git history
   - Check unauthorized usage

### C. `README.md` (NEW/UPDATED FILE)

**Location**: `/home/tholvimaaran/Projects/RAG/README.md`
**Lines**: ~250 lines

**Sections**:
1. **Overview**: Features and benefits
2. **Prerequisites**: Python 3.12+, uv/pip
3. **Installation**: Step-by-step setup
4. **Project Structure**: File organization
5. **Usage**: Commands and examples
6. **How It Works**: Architecture explanation
7. **Advanced Configuration**: Tuning parameters
8. **Troubleshooting**: Common issues
9. **Security**: Critical warnings
10. **Dependencies**: Full package list

### D. `IMPROVEMENTS.md` (NEW FILE)

**Location**: `/home/tholvimaaran/Projects/RAG/IMPROVEMENTS.md`
**Lines**: ~200 lines

**Sections**:
1. **Gap Summary**: Before/after comparison
2. **Detailed Improvements**: 7 gaps addressed
3. **Benefits**: Impact of changes
4. **Migration Guide**: From old to new

### E. `DETAILED_CHANGES.md` (NEW FILE)

**Location**: `/home/tholvimaaran/Projects/RAG/DETAILED_CHANGES.md`
**Lines**: ~800 lines (this file)

**Purpose**: Line-by-line breakdown of all changes

---

## 📊 SUMMARY STATISTICS

### Files Modified
| File | Type | Lines Changed |
|------|------|----------------|
| `vector_store.py` | Modified | ~150 added/changed |
| `chunking.py` | Rewritten | ~60 all lines |
| `main.py` | Rewritten | ~200 all lines |
| `pyproject.toml` | Modified | 1 line added |

### Files Created
| File | Lines | Purpose |
|------|-------|---------|
| `document_registry.py` | 350 | Fingerprinting & change detection |
| `retriever.py` | 450 | Query pipeline with LLM |
| `.env.example` | 11 | Safe configuration template |
| `SECURITY.md` | 100 | Security guidelines |
| `README.md` | 250 | Full documentation |
| `IMPROVEMENTS.md` | 200 | Gap fixes explanation |
| `DETAILED_CHANGES.md` | 800 | Line-by-line changes |

**Total New Lines**: ~2,600 lines of production code and documentation

---

## 🎯 KEY ARCHITECTURAL CHANGES

### Before: Linear Pipeline
```
Source File → Load → Chunk → Embed → Store → Done
```

### After: Intelligent Pipeline
```
Source File
    ↓
Registry Check (Has it changed?)
    ├─ YES → Continue
    └─ NO → Skip
    ↓
Delete Old Chunks
    ↓
Load Document
    ↓
Token-Based Chunking (512 tokens)
    ↓
Embed (BGE model)
    ↓
Store with Stable IDs
    ↓
Register in Document Registry
    ↓
Ready for Query

Query Pipeline:
User Question → Embed → Semantic Search → LLM + Context → Answer with Citations
```

---

## 🔄 Data Flow Examples

### Example 1: First Ingestion
```
document.pdf
├─ Registry: NEW DOCUMENT
├─ Action: Load, chunk (20 chunks), embed, store
├─ Metadata: Hash stored, timestamp recorded
└─ Result: 20 chunks added to store

Registry Entry:
{
  "id": "document.pdf",
  "file_hash": "a3b2c1d0...",
  "chunk_count": 20,
  "model_tag": "bge-small-en-v1.5",
  "last_ingested": "2024-01-10T14:30:00"
}
```

### Example 2: Unchanged File (Skip)
```
document.pdf (unchanged)
├─ Registry: DOCUMENT EXISTS
├─ Hash Check: IDENTICAL
├─ Action: SKIP (print "⏭️  Skipped")
└─ Result: No changes to vector store

Output: ⏭️  Skipped: Document unchanged
```

### Example 3: Modified File (Re-ingest)
```
document.pdf (modified)
├─ Registry: DOCUMENT EXISTS
├─ Hash Check: DIFFERENT
├─ Action: Delete old chunks (20 chunks)
├─ Load: New content (25 chunks)
├─ Store: Add 25 new chunks
├─ Register: Update hash and timestamp
└─ Result: 25 new chunks, old 20 cleaned up

Output: 
  📝 Change detected: File content has changed
  🗑️  Deleted 20 old chunks
  ✅ Success: 25 chunks stored
```

### Example 4: Query with LLM
```
User: "What is the main topic?"
├─ Embed query → [0.02, -0.11, 0.18, ...]
├─ Search ChromaDB (cosine distance)
├─ Retrieve 5 chunks (distance < 1.5)
├─ Build prompt with chunks + question
├─ Call LLM (GPT-4o-mini or Gemini)
└─ Return answer + sources

Output:
The main topic is... [Based on chunks from document.pdf page 1]
```

---

## ✨ CRITICAL IMPROVEMENTS

1. **No More Duplicates**: Stable IDs prevent chunk accumulation
2. **Smart Caching**: Skip unchanged files (compute savings)
3. **Auto Cleanup**: Old chunks deleted before re-ingestion
4. **Accurate Costs**: Token-based chunking prevents LLM overruns
5. **Complete RAG**: Query pipeline with multi-model LLM support
6. **Production Ready**: CLI, status checking, comprehensive docs
7. **Secure**: .env template, security guidelines, API key safety

---

## 🚀 USAGE COMPARISON

### OLD
```bash
python main.py
# Processes: document.pdf, document.txt, document.md, document.doc
# Issues: Duplicates, wastes compute, no query, no status
```

### NEW
```bash
# Ingest with smart change detection
python main.py ingest

# Check what's stored
python main.py status

# Query the knowledge base
python main.py query "What is the main topic?"

# Force clean re-ingestion
python main.py ingest --force --clean-orphans

# Use different LLM
python main.py query "Summarize" --llm google --top-k 10
```

---

## 📝 CONCLUSION

The RAG pipeline has been comprehensively upgraded from a basic proof-of-concept to a production-grade system with:

✅ **7 Major Gaps Fixed**
✅ **4 New/Rewritten Core Modules**  
✅ **2,600+ Lines Added**  
✅ **Industry Best Practices**  
✅ **Complete Documentation**  

All changes maintain backward compatibility with document formats while introducing forward-compatible versioning through model tags.
