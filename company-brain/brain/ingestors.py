"""Shared base class + chunk dataclass for every ingest source.

Concrete ingestors (SlackIngestor, NotionIngestor, GitHubIngestor) live in
the /ingest package and only need to implement build_chunks(). validate()
and process() are inherited.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from brain.text_utils import cap_tokens, count_tokens  # noqa: F401  (cap_tokens re-exported for ingestors)


@dataclass
class Chunk:
    source_type: str        # 'slack' | 'notion' | 'github' | 'pdf' | 'manual'
    source_name: str        # channel name, page title, filename etc
    content: str
    metadata: dict = field(default_factory=dict)
    token_count: int = 0

    def __post_init__(self):
        self.token_count = count_tokens(self.content)


class BaseIngestor(ABC):
    MAX_CHUNK_TOKENS = 600

    @abstractmethod
    def build_chunks(self, raw_data) -> list[Chunk]:
        raise NotImplementedError

    def validate(self, chunk: Chunk) -> bool:
        return (
            len(chunk.content.strip()) > 20
            and chunk.token_count > 0
            and chunk.source_type is not None
            and chunk.source_name is not None
        )

    def process(self, raw_data) -> list[Chunk]:
        chunks = self.build_chunks(raw_data)
        valid = [c for c in chunks if self.validate(c)]
        invalid_count = len(chunks) - len(valid)
        name = self.__class__.__name__
        if invalid_count > 0:
            print(f"[{name}] Dropped {invalid_count} invalid chunks")
        print(f"[{name}] {len(valid)} chunks")
        return valid
