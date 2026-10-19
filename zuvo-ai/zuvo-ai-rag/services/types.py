from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RetrievalRecord:
    source: str
    content: str
    score: float | None
    metadata: dict
