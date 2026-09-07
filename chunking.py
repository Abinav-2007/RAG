"""
chunking.py
-----------
Token-based document chunking for RAG pipeline.

Purpose
-------
Splits loaded documents into smaller chunks using token counts instead of
character counts, ensuring accurate sizing for LLM context windows and
token-based billing.

Why token-based chunking?
--------------------------
- Character-based chunking is an approximation (1 char ≠ 1 token)
- Different tokenizers have different token:char ratios
- Token-accurate splitting prevents:
  * Exceeding LLM context limits
  * Unexpected billing costs
  * Inefficient chunk sizes

Token model
-----------
Uses tiktoken with the cl100k_base encoding (GPT-4, GPT-3.5-turbo standard).
This is compatible with most modern OpenAI models and provides accurate
token counts.
"""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tiktoken

from document_loader import load_documents


# Chunking configuration - token-based
TOKEN_CHUNK_SIZE = 512  # tokens per chunk (reasonable for most LLMs)
TOKEN_CHUNK_OVERLAP = 50  # token overlap between chunks
ENCODING_NAME = "cl100k_base"  # GPT-4, GPT-3.5-turbo, text-embedding-ada-002

# Fallback to character-based for non-tokenizable content
CHAR_CHUNK_SIZE = 2000
CHAR_CHUNK_OVERLAP = 200

SEPARATORS = [
    "\n\n",
    "\n",
    " ",
    ""
]


def get_token_counter(encoding_name: str = ENCODING_NAME):
    """
    Create a token counting function for the specified encoding.
    
    Args:
        encoding_name: tiktoken encoding name (e.g., "cl100k_base").
        
    Returns:
        Function that counts tokens in a string.
    """
    encoding = tiktoken.get_encoding(encoding_name)
    
    def count_tokens(text: str) -> int:
        return len(encoding.encode(text))
    
    return count_tokens


def split_documents(
    documents: list[Document],
    use_token_splitting: bool = True,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None
) -> list[Document]:
    """
    Split loaded documents into smaller chunks using token-based splitting.

    Args:
        documents: List of LangChain Document objects.
        use_token_splitting: If True, use token-based chunking (recommended).
                             If False, fall back to character-based.
        chunk_size: Override default chunk size (tokens if use_token_splitting=True,
                    characters otherwise).
        chunk_overlap: Override default chunk overlap.

    Returns:
        List of chunked Document objects with their metadata preserved.
    """
    if use_token_splitting:
        # Token-based splitting (recommended)
        effective_chunk_size = chunk_size or TOKEN_CHUNK_SIZE
        effective_overlap = chunk_overlap or TOKEN_CHUNK_OVERLAP
        
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=ENCODING_NAME,
            chunk_size=effective_chunk_size,
            chunk_overlap=effective_overlap,
            separators=SEPARATORS
        )
        
        print(f"Using token-based chunking: {effective_chunk_size} tokens, "
              f"{effective_overlap} overlap (encoding: {ENCODING_NAME})")
    else:
        # Character-based splitting (fallback)
        effective_chunk_size = chunk_size or CHAR_CHUNK_SIZE
        effective_overlap = chunk_overlap or CHAR_CHUNK_OVERLAP
        
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=effective_chunk_size,
            chunk_overlap=effective_overlap,
            separators=SEPARATORS
        )
        
        print(f"Using character-based chunking: {effective_chunk_size} chars, "
              f"{effective_overlap} overlap")

    return text_splitter.split_documents(documents)


def estimate_tokens(text: str, encoding_name: str = ENCODING_NAME) -> int:
    """
    Estimate token count for a given text.
    
    Useful for debugging and understanding how text maps to tokens.
    
    Args:
        text: Input text string.
        encoding_name: tiktoken encoding name.
        
    Returns:
        Number of tokens.
    """
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))


def main() -> None:
    """Load a document, split it into chunks, and display a summary."""

    source = "data/document.pdf"

    documents = load_documents(source)
    chunks = split_documents(documents, use_token_splitting=True)

    print(f"\nDocuments loaded : {len(documents)}")
    print(f"Chunks created   : {len(chunks)}")

    token_counter = get_token_counter()
    
    for index, chunk in enumerate(chunks[:3], start=1):  # Show first 3
        token_count = token_counter(chunk.page_content)
        char_count = len(chunk.page_content)
        
        print(f"\nChunk {index}")
        print("-" * 50)
        print(f"Characters : {char_count}")
        print(f"Tokens     : {token_count}")
        print(f"Ratio      : {char_count/token_count:.2f} chars/token")
        print(f"Metadata   : {chunk.metadata}")
        print(f"Content    : {chunk.page_content[:150]}...")


if __name__ == "__main__":
    main()
