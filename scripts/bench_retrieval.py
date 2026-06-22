#!/usr/bin/env python3
"""Sprint 1 — Retrieval-Latenz-Benchmark (lexical vs. embedding).

Befüllt einen Memory Card Store mit N synthetischen Karten und misst die
Latenz von ``Retriever.search`` für beide Similarity-Implementierungen.
Gibt p50/p95/max in Millisekunden aus — als Grundlage für das CI-Schwellen-
Kriterium der Roadmap (p95 < 50 ms je Akte).

Beispiel:
    python3 scripts/bench_retrieval.py --cards 10000 --queries 200
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brainfump.embeddings import HashingEmbedder  # noqa: E402
from brainfump.memory_cards import MemoryCard, MemoryCardStore  # noqa: E402
from brainfump.retrieval import EmbeddingSimilarity, LexicalSimilarity, Retriever  # noqa: E402

_TOPICS = [
    "zahlung stripe gateway timeout",
    "frontend vanilla js vue react komponente",
    "datenbank migration postgres index",
    "auth login token session ablauf",
    "deployment docker compose healthcheck",
]


def populate(store: MemoryCardStore, n: int) -> None:
    for i in range(n):
        topic = _TOPICS[i % len(_TOPICS)]
        store.add(
            MemoryCard(
                memory_type="semantic",
                statement=f"{topic} variante {i}",
                case_id="bench",
                scope=tuple(topic.split()[:2]),
            )
        )


def percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def measure(retriever: Retriever, queries: int) -> dict[str, float]:
    timings: list[float] = []
    for i in range(queries):
        query = _TOPICS[i % len(_TOPICS)]
        start = time.perf_counter()
        retriever.search(query, case_id="bench", k=5)
        timings.append((time.perf_counter() - start) * 1000)
    return {
        "p50_ms": round(percentile(timings, 50), 3),
        "p95_ms": round(percentile(timings, 95), 3),
        "max_ms": round(max(timings), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval-Latenz-Benchmark")
    parser.add_argument("--cards", type=int, default=10000)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--dim", type=int, default=256)
    args = parser.parse_args()

    store = MemoryCardStore()
    print(f"Befülle {args.cards} Karten …")
    populate(store, args.cards)

    variants = {
        "lexical": Retriever(store, similarity=LexicalSimilarity()),
        "embedding(hashing)": Retriever(
            store, similarity=EmbeddingSimilarity(HashingEmbedder(dim=args.dim))
        ),
    }
    print(f"\n{args.queries} Queries über {args.cards} Karten:\n")
    print(f"{'similarity':<22} {'p50_ms':>8} {'p95_ms':>8} {'max_ms':>8}")
    for name, retriever in variants.items():
        stats = measure(retriever, args.queries)
        print(f"{name:<22} {stats['p50_ms']:>8} {stats['p95_ms']:>8} {stats['max_ms']:>8}")


if __name__ == "__main__":
    main()
