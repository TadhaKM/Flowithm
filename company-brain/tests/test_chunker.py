"""brain.chunker — token-aware text splitting."""
from brain.chunker import chunk_text


def test_empty_input_returns_no_chunks():
    assert chunk_text("") == []


def test_short_input_returns_one_chunk():
    chunks = chunk_text("hello world this is a short message")
    assert len(chunks) == 1
    # The chunk preserves the input text verbatim (modulo tiktoken roundtrip).
    assert "hello world" in chunks[0]


def test_long_input_splits_into_multiple_chunks():
    text = " ".join(["lorem ipsum dolor"] * 1000)
    chunks = chunk_text(text, max_tokens=200, overlap=20)
    assert len(chunks) > 1


def test_max_tokens_respected():
    """No single chunk should be wildly longer than max_tokens (in tokens)."""
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    text = " ".join(["word"] * 5000)
    chunks = chunk_text(text, max_tokens=300, overlap=30)
    for c in chunks:
        # Allow a small fudge for boundary tokenization differences.
        assert len(enc.encode(c)) <= 320


def test_overlap_creates_shared_content():
    """Adjacent chunks should share the overlap region."""
    text = " ".join([f"w{i}" for i in range(2000)])
    chunks = chunk_text(text, max_tokens=200, overlap=50)
    assert len(chunks) >= 2
    # The end of chunk 0 should appear at/near the start of chunk 1.
    end_of_first = chunks[0].split()[-10:]
    start_of_second = chunks[1].split()[:60]
    assert any(t in start_of_second for t in end_of_first)
