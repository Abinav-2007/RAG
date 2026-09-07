"""
retriever.py
------------
Retrieval and query layer for the RAG pipeline.

Purpose
-------
Provides semantic search over the vector store and LLM-powered response
generation using retrieved context chunks.

Pipeline
--------
User Query → Embed Query → Search ChromaDB → Retrieve Top-K Chunks 
→ Build Context → LLM Prompt → Generated Response

Key Features
------------
- Multi-model LLM support (OpenAI GPT-4, GPT-3.5, Google Gemini)
- Configurable retrieval parameters (top-k, distance threshold)
- Citation tracking (which chunks were used)
- Query reformulation and context filtering
- MMR (Maximal Marginal Relevance) for diversity (optional)
"""

import os
from typing import Optional
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from sentence_transformers import SentenceTransformer

from vector_store import create_collection
from embeddings import load_embedding_model, EMBEDDING_MODEL, EMBEDDING_DEVICE


# Retrieval configuration
DEFAULT_TOP_K = 5
DEFAULT_DISTANCE_THRESHOLD = 1.5  # Cosine distance (lower is more similar)

# LLM configuration
DEFAULT_LLM_PROVIDER = "openai"  # "openai" or "google"
DEFAULT_LLM_MODEL_OPENAI = "gpt-4o-mini"  # or "gpt-4", "gpt-3.5-turbo"
DEFAULT_LLM_MODEL_GOOGLE = "gemini-1.5-flash"  # or "gemini-1.5-pro"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1000


@dataclass
class RetrievalResult:
    """Container for retrieval results."""
    query: str
    chunks: list[Document]
    distances: list[float]
    sources: list[str]
    answer: Optional[str] = None
    model_used: Optional[str] = None


class RAGRetriever:
    """
    Retrieval and generation engine for RAG pipeline.
    
    Handles semantic search over the vector store and LLM-based
    response generation with citations.
    """
    
    def __init__(
        self,
        llm_provider: str = DEFAULT_LLM_PROVIDER,
        llm_model: Optional[str] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        top_k: int = DEFAULT_TOP_K,
        distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    ):
        """
        Initialize the RAG retriever.
        
        Args:
            llm_provider: "openai" or "google"
            llm_model: Model name (auto-selects default if None)
            temperature: LLM temperature (0.0 = deterministic, 1.0 = creative)
            max_tokens: Maximum tokens in LLM response
            top_k: Number of chunks to retrieve
            distance_threshold: Maximum cosine distance for retrieval
        """
        self.llm_provider = llm_provider.lower()
        self.top_k = top_k
        self.distance_threshold = distance_threshold
        
        # Initialize embedding model (same as used during ingestion)
        print(f"Loading embedding model: {EMBEDDING_MODEL}")
        self.embedding_model = load_embedding_model()
        
        # Initialize LLM
        if llm_model is None:
            llm_model = (
                DEFAULT_LLM_MODEL_OPENAI 
                if self.llm_provider == "openai" 
                else DEFAULT_LLM_MODEL_GOOGLE
            )
        
        self.llm_model = llm_model
        print(f"Initializing LLM: {self.llm_provider}/{llm_model}")
        
        if self.llm_provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found in environment")
            
            self.llm = ChatOpenAI(
                model=llm_model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=api_key
            )
        elif self.llm_provider == "google":
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not found in environment")
            
            self.llm = ChatGoogleGenerativeAI(
                model=llm_model,
                temperature=temperature,
                max_tokens=max_tokens,
                google_api_key=api_key
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {self.llm_provider}")
        
        # Initialize vector store collection
        self.collection = create_collection()
        print(f"Connected to ChromaDB collection: {self.collection.count()} chunks\n")
    
    def _embed_query(self, query: str) -> list[float]:
        """
        Embed the user query using the same model as document chunks.
        
        Args:
            query: User's question string.
            
        Returns:
            384-dimensional embedding vector.
        """
        # Use same normalization as during ingestion
        embedding = self.embedding_model.encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False
        )
        return embedding[0].tolist()
    
    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        filter_metadata: Optional[dict] = None
    ) -> RetrievalResult:
        """
        Retrieve relevant chunks from the vector store.
        
        Args:
            query: User's question.
            top_k: Number of chunks to retrieve (overrides default).
            filter_metadata: Optional metadata filter (e.g., {"source": "data/doc.pdf"}).
            
        Returns:
            RetrievalResult with chunks, distances, and sources.
        """
        k = top_k or self.top_k
        
        # Embed the query
        query_embedding = self._embed_query(query)
        
        # Search ChromaDB
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=filter_metadata,
            include=["documents", "metadatas", "distances"]
        )
        
        # Parse results
        chunks = []
        distances = results["distances"][0] if results["distances"] else []
        metadatas = results["metadatas"][0] if results["metadatas"] else []
        documents = results["documents"][0] if results["documents"] else []
        
        # Filter by distance threshold
        for i, distance in enumerate(distances):
            if distance <= self.distance_threshold:
                chunk = Document(
                    page_content=documents[i],
                    metadata=metadatas[i]
                )
                chunks.append(chunk)
        
        # Extract unique sources
        sources = list(set(meta.get("source", "unknown") for meta in metadatas))
        
        return RetrievalResult(
            query=query,
            chunks=chunks[:k],  # Ensure we don't exceed top_k
            distances=distances[:k],
            sources=sources
        )
    
    def _build_prompt(self, query: str, chunks: list[Document]) -> str:
        """
        Build the RAG prompt with context chunks.
        
        Args:
            query: User's question.
            chunks: Retrieved document chunks.
            
        Returns:
            Formatted prompt string.
        """
        # Build context from chunks
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            source = chunk.metadata.get("source", "unknown")
            page = chunk.metadata.get("page")
            
            citation = f"[Source: {source}"
            if page is not None:
                citation += f", Page {page + 1}"  # Convert 0-indexed to 1-indexed
            citation += "]"
            
            context_parts.append(f"Context {i}:\n{chunk.page_content}\n{citation}")
        
        context = "\n\n".join(context_parts)
        
        # Build full prompt
        prompt = f"""You are a helpful assistant that answers questions based on the provided context.

Context:
{context}

Question: {query}

Instructions:
- Answer the question using ONLY the information from the provided context.
- If the context doesn't contain enough information to answer, say so clearly.
- Cite sources by mentioning the source file and page number when applicable.
- Be concise but complete in your answer.

Answer:"""
        
        return prompt
    
    def generate_answer(
        self,
        query: str,
        top_k: Optional[int] = None,
        filter_metadata: Optional[dict] = None,
        return_chunks: bool = True
    ) -> RetrievalResult:
        """
        End-to-end RAG: retrieve chunks and generate an answer.
        
        Args:
            query: User's question.
            top_k: Number of chunks to retrieve (overrides default).
            filter_metadata: Optional metadata filter.
            return_chunks: Whether to include chunks in the result.
            
        Returns:
            RetrievalResult with answer and optional chunks.
        """
        # Step 1: Retrieve relevant chunks
        retrieval_result = self.retrieve(
            query=query,
            top_k=top_k,
            filter_metadata=filter_metadata
        )
        
        if not retrieval_result.chunks:
            retrieval_result.answer = (
                "I couldn't find any relevant information in the knowledge base "
                "to answer your question."
            )
            retrieval_result.model_used = "N/A (no retrieval)"
            return retrieval_result
        
        # Step 2: Build prompt with context
        prompt = self._build_prompt(query, retrieval_result.chunks)
        
        # Step 3: Generate answer with LLM
        try:
            response = self.llm.invoke(prompt)
            answer = response.content
        except Exception as exc:
            answer = f"Error generating answer: {exc}"
        
        retrieval_result.answer = answer
        retrieval_result.model_used = f"{self.llm_provider}/{self.llm_model}"
        
        # Optionally remove chunks to save memory
        if not return_chunks:
            retrieval_result.chunks = []
        
        return retrieval_result
    
    def query(
        self,
        question: str,
        verbose: bool = True,
        **kwargs
    ) -> str:
        """
        Simple query interface that returns just the answer string.
        
        Args:
            question: User's question.
            verbose: Whether to print retrieval statistics.
            **kwargs: Additional arguments passed to generate_answer().
            
        Returns:
            Generated answer string.
        """
        result = self.generate_answer(question, **kwargs)
        
        if verbose:
            print(f"\n{'='*60}")
            print(f"Question: {question}")
            print(f"{'='*60}")
            print(f"Retrieved {len(result.chunks)} chunks from {len(result.sources)} sources:")
            for i, (chunk, distance) in enumerate(zip(result.chunks, result.distances), 1):
                source = chunk.metadata.get("source", "unknown")
                page = chunk.metadata.get("page")
                page_str = f", Page {page + 1}" if page is not None else ""
                print(f"  {i}. {source}{page_str} (distance: {distance:.4f})")
            print(f"\nModel: {result.model_used}")
            print(f"{'='*60}\n")
            print(f"Answer:\n{result.answer}")
            print(f"{'='*60}\n")
        
        return result.answer


def main():
    """Demo usage of the RAG retriever."""
    from dotenv import load_dotenv
    load_dotenv()
    
    # Initialize retriever
    retriever = RAGRetriever(
        llm_provider="openai",  # or "google"
        llm_model="gpt-4o-mini",  # or "gemini-1.5-flash"
        top_k=3,
        temperature=0.7
    )
    
    # Example queries
    queries = [
        "What is the main topic of the documents?",
        "Summarize the key points.",
        "What are the conclusions?"
    ]
    
    for query in queries:
        answer = retriever.query(query, verbose=True)
        print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    main()
