from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from document_loader import load_documents


# Chunking configuration
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200

SEPARATORS = [
    "\n\n",
    "\n",
    " ",
    ""
]


def split_documents(documents: list[Document]) -> list[Document]:
    """
    Split loaded documents into smaller chunks.

    Args:
        documents: List of LangChain Document objects.

    Returns:
        List of chunked Document objects with their metadata preserved.
    """

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS
    )

    return text_splitter.split_documents(documents)


def main() -> None:
    """Load a document, split it into chunks, and display a summary."""

    source = "data/document.pdf"

    documents = load_documents(source)
    chunks = split_documents(documents)

    print(f"Documents loaded : {len(documents)}")
    print(f"Chunks created   : {len(chunks)}")

    for index, chunk in enumerate(chunks, start=1):
        print(f"\nChunk {index}")
        print("-" * 50)
        print(f"Characters : {len(chunk.page_content)}")
        print(f"Metadata   : {chunk.metadata}")
        print(f"Content    : {chunk.page_content[:200]}...")


if __name__ == "__main__":
    main()