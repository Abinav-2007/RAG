from sentence_transformers import SentenceTransformer
from langchain_core.documents import Document

from document_loader import load_documents
from chunking import split_documents


model = SentenceTransformer(
    "BAAI/bge-small-en-v1.5",
    device="cpu"
)


def load_embedding_model() -> SentenceTransformer:
    """
    Load and return the configured embedding model.

    Returns:
        Loaded SentenceTransformer embedding model.
    """

    return SentenceTransformer(EMBEDDING_MODEL)


def generate_embeddings(
    chunks: list[Document],
    model: SentenceTransformer
) -> list[list[float]]:
    """
    Generate embeddings for document chunks.

    Args:
        chunks: List of LangChain Document objects.
        model: Loaded SentenceTransformer model.

    Returns:
        List of embedding vectors.
    """

    texts = [chunk.page_content for chunk in chunks]

    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=True
    )

    return embeddings.tolist()


def main() -> None:
    """Run the document chunking and embedding pipeline."""

    source = "data/document.pdf"

    # Load documents
    documents = load_documents(source)

    # Split documents into chunks
    chunks = split_documents(documents)

    # Load embedding model
    model = load_embedding_model()

    # Generate embeddings
    embeddings = generate_embeddings(chunks, model)

    print(f"\nDocuments loaded : {len(documents)}")
    print(f"Chunks created   : {len(chunks)}")
    print(f"Embeddings       : {len(embeddings)}")
    print(f"Dimensions       : {len(embeddings[0])}")

    # Inspect the first embedding
    print("\nFirst embedding:")
    print(embeddings[0])


if __name__ == "__main__":
    main()