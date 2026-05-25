from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RetrievedContextItem:
    id: str
    title: str | None
    source_uri: str | None
    page_number: int | None
    snippet: str
    metadata: dict[str, object] = field(default_factory=dict)
    rank: int | None = None


@dataclass
class RetrievalResult:
    query: str
    items: list[RetrievedContextItem]
    provider: str


class FeedbackRetrievalProvider(Protocol):
    def retrieve_for_answer(self, db, ans) -> RetrievalResult:
        ...
