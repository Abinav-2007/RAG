from langchain_community.document_loaders import (
    PyMuPDFLoader,
    UnstructuredPDFLoader,
    UnstructuredMarkdownLoader,
    UnstructuredWordDocumentLoader,
    TextLoader,
    WebBaseLoader,
    UnstructuredFileLoader,
)

import os
from urllib.parse import urlparse


LOADERS = {
    "pymupdf": PyMuPDFLoader,
    "unstructured_pdf": UnstructuredPDFLoader,
    "markdown": UnstructuredMarkdownLoader,
    "docx": UnstructuredWordDocumentLoader,
    "text": TextLoader,
    "web": WebBaseLoader,
    "file": UnstructuredFileLoader
}


def _is_url(source: str) -> bool:
    p = urlparse(source) 
    return p.scheme in ("http", "https")


def detect_loader_for_source(source: str):
    if _is_url(source):
        return LOADERS["web"]

    _, ext = os.path.splitext(source.lower())

    match ext:
        case ".pdf":
            return LOADERS["pymupdf"] or LOADERS["unstructured_pdf"]

        case ".md" | ".markdown":
            return LOADERS["markdown"]

        case ".docx" | ".doc":
            return LOADERS["docx"]

        case ".txt":
            return LOADERS["text"]

        case _:
            return LOADERS["file"]


def load_documents(source: str):
    loader_cls = detect_loader_for_source(source)

    print(f"Selected Loader : {loader_cls.__name__}")

    loader = loader_cls(source)
    docs = loader.load()

    return docs


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python document_loader.py <file_or_url>")
        exit(1)

    source = sys.argv[1]

    docs = load_documents(source)

    print(f"Documents Loaded : {len(docs)}")

    for i, doc in enumerate(docs):
        print(f"\n========== Document {i + 1} ==========")
        print("Metadata:", doc.metadata)
        print("Characters:", len(doc.page_content))
        print(doc.page_content[:300])