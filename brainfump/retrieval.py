"""fRAG Retrieval Layer (Abschnitt 4.6).

Fragment-aware Retrieval über Memory Cards mit dem Spec-Scoring:

    score = semantic_similarity + case_relevance + recency
          + confidence + trust + risk_weight + governance_priority

Bewusst ohne Vector-DB-Abhängigkeit: die semantische Komponente ist eine
lexikalische Jaccard-Ähnlichkeit und kann später durch Embeddings ersetzt
werden, ohne dass sich das Ranking-Schema ändert (Prinzip 7: Vector Index
ist nur der semantische Zugriff, nicht das Memory).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from brainfump.memory_cards import MemoryCard, MemoryCardStore

_TOKEN = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


@dataclass(frozen=True)
class Weights:
    semantic: float = 1.0
    case_relevance: float = 1.0
    recency: float = 0.5
    confidence: float = 0.5
    trust: float = 0.5
    risk: float = 0.75
    governance: float = 0.75


@dataclass(frozen=True)
class ScoredCard:
    card: MemoryCard
    score: float


class Retriever:
    def __init__(self, store: MemoryCardStore, weights: Weights | None = None) -> None:
        self.store = store
        self.weights = weights or Weights()

    def search(
        self,
        query: str,
        case_id: str | None = None,
        k: int = 5,
        include_global: bool = True,
        on_date: str | None = None,
    ) -> list[ScoredCard]:
        on_date = on_date or date.today().isoformat()
        query_tokens = _tokens(query)
        candidates = self.store.active(
            case_id=case_id, include_global=include_global, on_date=on_date
        )
        scored = [
            ScoredCard(card=c, score=self._score(c, query_tokens, case_id))
            for c in candidates
        ]
        scored = [s for s in scored if s.score > 0]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:k]

    def _score(self, card: MemoryCard, query_tokens: set[str], case_id: str | None) -> float:
        w = self.weights
        card_tokens = _tokens(card.statement) | {s.lower() for s in card.scope}
        union = query_tokens | card_tokens
        semantic = len(query_tokens & card_tokens) / len(union) if union else 0.0
        if semantic == 0.0:
            return 0.0

        case_relevance = 1.0 if (case_id is not None and card.case_id == case_id) else 0.0
        recency = self._recency(card.created_at)
        risk_weight = 1.0 if card.memory_type in ("risk", "failure") else 0.0
        governance = 1.0 if card.memory_type == "governance" else 0.0

        return (
            w.semantic * semantic
            + w.case_relevance * case_relevance
            + w.recency * recency
            + w.confidence * card.confidence
            + w.trust * card.trust
            + w.risk * risk_weight
            + w.governance * governance
        )

    @staticmethod
    def _recency(created_at: str, half_life_days: float = 30.0) -> float:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400
        return 0.5 ** (max(age_days, 0.0) / half_life_days)
