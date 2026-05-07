"""Token-aware text chunker."""
from brain.text_utils import ENCODING, count_tokens  # noqa: F401  (count_tokens re-exported for callers)


def chunk_text(text: str, max_tokens: int = 500, overlap: int = 50) -> list[str]:
    tokens = ENCODING.encode(text)
    if not tokens:
        return []

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunks.append(ENCODING.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start = end - overlap
    return chunks
