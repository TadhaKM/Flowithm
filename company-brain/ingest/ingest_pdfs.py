"""Ingest PDF documents into the brain."""
from pathlib import Path

from PyPDF2 import PdfReader

from brain.chunker import chunk_text
from brain.embedder import embed_chunks
from brain.store import upsert_chunks

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo-data"


def extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def main() -> None:
    chunks = []
    for pdf_path in DEMO_DIR.glob("*.pdf"):
        text = extract_pdf(pdf_path)
        for c in chunk_text(text):
            chunks.append({
                "source_type": "pdf",
                "source_name": pdf_path.stem,
                "content": c,
                "metadata": {"filename": pdf_path.name},
            })

    for txt_path in DEMO_DIR.glob("*.txt"):
        text = txt_path.read_text(encoding="utf-8")
        for c in chunk_text(text):
            chunks.append({
                "source_type": "pdf",
                "source_name": txt_path.stem,
                "content": c,
                "metadata": {"filename": txt_path.name},
            })

    embedded = embed_chunks(chunks)
    upsert_chunks(embedded)
    print(f"Ingested {len(embedded)} PDF chunks.")


if __name__ == "__main__":
    main()
