"""fRAG Retrieval Layer (Abschnitt 4.6).

Fragment-aware Retrieval über Memory Cards mit dem Spec-Scoring:

    score = semantic_similarity + case_relevance + recency
          + confidence + trust + risk_weight + governance_priority

Die semantische Komponente ist austauschbar: Default ist eine lexikalische
Jaccard-Ähnlichkeit (ohne externe Abhängigkeit), per Dependency Injection
kann aber jede Embedding-basierte Ähnlichkeit eingehängt werden, ohne dass
sich das Ranking-Schema ändert (Prinzip 7: der Vektor-Index ist nur der
semantische Zugriffspfad, nicht das Memory selbst).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Protocol, Sequence

from brainfump.memory_cards import MemoryCard, MemoryCardStore

_TOKEN = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


class Similarity(Protocol):
    """Semantische Ähnlichkeit zwischen Query und Memory Card in ``[0, 1]``."""

    def __call__(self, query: str, card: MemoryCard) -> float: ...


class LexicalSimilarity:
    """Jaccard-Ähnlichkeit über Tokens von Statement + Scope (Default).

    Reproduziert das ursprüngliche Verhalten und bleibt damit der
    abhängigkeitsfreie Standard.
    """

    def __call__(self, query: str, card: MemoryCard) -> float:
        query_tokens = _tokens(query)
        card_tokens = _tokens(card.statement) | {s.lower() for s in card.scope}
        union = query_tokens | card_tokens
        if not union:
            return 0.0
        return len(query_tokens & card_tokens) / len(union)


class EmbeddingSimilarity:
    """Cosinus-Ähnlichkeit über einen injizierten Embedding-Provider.

    ``embed`` bildet einen Text auf einen Vektor ab (z. B. ein lokales
    Modell oder ein API-Aufruf). Card-Embeddings werden je
    ``(card_id, statement)`` zwischengespeichert, sodass eine geänderte
    Aussage neu eingebettet wird, eine unveränderte aber nicht. Negative
    Cosinus-Werte (entgegengesetzt) werden auf ``0`` geklemmt.
    """

    def __init__(self, embed: Callable[[str], Sequence[float]]) -> None:
        self._embed = embed
        self._cache: dict[tuple[str, str], Sequence[float]] = {}
        # Ein-Slot-Memo für die Query: innerhalb einer search()-Schleife über
        # N Karten wird die Query so genau einmal eingebettet statt N-mal
        # (spart bei echten Embedding-APIs N-fache Kosten und Latenz).
        self._query_memo: tuple[str | None, Sequence[float] | None] = (None, None)

    def _embed_query(self, query: str) -> Sequence[float]:
        cached_query, cached_vec = self._query_memo
        if cached_query != query or cached_vec is None:
            cached_vec = self._embed(query)
            self._query_memo = (query, cached_vec)
        return cached_vec

    def __call__(self, query: str, card: MemoryCard) -> float:
        key = (card.card_id, card.statement)
        card_vec = self._cache.get(key)
        if card_vec is None:
            text = " ".join([card.statement, *card.scope])
            card_vec = self._embed(text)
            self._cache[key] = card_vec
        return max(0.0, _cosine(self._embed_query(query), card_vec))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("embedding dimensionality mismatch")
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


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
    def __init__(
        self,
        store: MemoryCardStore,
        weights: Weights | None = None,
        similarity: Similarity | None = None,
        min_similarity: float = 0.0,
    ) -> None:
        self.store = store
        self.weights = weights or Weights()
        self.similarity = similarity or LexicalSimilarity()
        # Kandidaten mit Ähnlichkeit <= Schwelle werden verworfen. 0.0 erhält
        # das lexikalische Verhalten (jede Überlappung zählt); für Embeddings
        # kann ein positiver Schwellwert das Grundrauschen herausfiltern.
        self.min_similarity = min_similarity

    def search(
        self,
        query: str,
        case_id: str | None = None,
        k: int = 5,
        include_global: bool = True,
        on_date: str | None = None,
    ) -> list[ScoredCard]:
        on_date = on_date or date.today().isoformat()
        candidates = self.store.active(
            case_id=case_id, include_global=include_global, on_date=on_date
        )
        scored = [
            ScoredCard(card=c, score=self._score(query, c, case_id)) for c in candidates
        ]
        scored = [s for s in scored if s.score > 0]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:k]

    def _score(self, query: str, card: MemoryCard, case_id: str | None) -> float:
        w = self.weights
        semantic = self.similarity(query, card)
        if semantic <= self.min_similarity:
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
