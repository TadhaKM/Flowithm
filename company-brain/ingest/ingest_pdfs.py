"""Ingest PDF (and plain-text) documents into chunk dicts.

Standalone for now: prints chunks to stdout as JSON, count to stderr —
matches the pattern of ingest_slack / ingest_notion / ingest_github.
The actual embed-and-store happens via brain.run_ingest, which doesn't
currently include this path because demo-data/ has no PDFs.
"""
import json
import sys
from pathlib import Path

from brain.chunker import chunk_text

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo-data"


def extract_pdf(path: Path) -> str:
    # Lazy import — keeps the module importable in environments without PyPDF2.
    from PyPDF2 import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def build_chunks(demo_dir: Path = DEMO_DIR) -> list[dict]:
    chunks: list[dict] = []
    for pdf_path in demo_dir.glob("*.pdf"):
        text = extract_pdf(pdf_path)
        for c in chunk_text(text):
            chunks.append({
                "source_type": "pdf",
                "source_name": pdf_path.stem,
                "content": c,
                "metadata": {"filename": pdf_path.name},
            })
    for txt_path in demo_dir.glob("*.txt"):
        text = txt_path.read_text(encoding="utf-8")
        for c in chunk_text(text):
            chunks.append({
                "source_type": "pdf",
                "source_name": txt_path.stem,
                "content": c,
                "metadata": {"filename": txt_path.name},
            })
    return chunks


def main() -> None:
    chunks = build_chunks()
    print(json.dumps(chunks, indent=2, ensure_ascii=False))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
