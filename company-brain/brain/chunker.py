"""Token-aware text chunker."""
import tiktoken

ENCODING = tiktoken.get_encoding("cl100k_base")


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
