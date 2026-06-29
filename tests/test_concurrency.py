"""K1/K2 — Thread-Safety der SQLite-Stores unter ThreadingHTTPServer-Last.

Diese Tests würden ohne die Serialisierung (brainfump/_locking.py) durch
Races am geteilten Token-Index und an der geteilten SQLite-Connection
fehlschlagen ("dictionary changed size during iteration" / "database is locked").
"""

import threading

from brainfump import BrainFumpKernel
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.retrieval import LexicalSimilarity, Retriever


def test_store_thread_safe_under_concurrent_add_and_search():
    store = MemoryCardStore()
    retriever = Retriever(store, similarity=LexicalSimilarity())
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(50):
                store.add(
                    MemoryCard(
                        memory_type="semantic",
                        statement=f"zahlung stripe gateway {tid} {i}",
                        case_id="a",
                        scope=("zahlung",),
                    )
                )
                # Suche baut den Index, Add invalidiert ihn — der Race-Hotspot.
                retriever.search("zahlung gateway", case_id="a", k=5)
        except Exception as exc:  # pragma: no cover - nur bei Regression
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Race-Conditions: {errors[:3]}"
    assert len(store) == 8 * 50


def test_kernel_thread_safe_under_concurrent_record_and_check():
    kernel = BrainFumpKernel()
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(40):
                kernel.record(
                    "failed_attempt",
                    f"Fix {tid}-{i} scheiterte",
                    case_id="repo",
                    payload={"error_signature": f"sig_{tid}_{i}"},
                )
                kernel.check_action(
                    {"action_type": "apply_fix", "case_id": "repo", "error_signature": f"sig_{tid}_{i}"}
                )
                kernel.search("Fix scheiterte", case_id="repo", k=3)
        except Exception as exc:  # pragma: no cover - nur bei Regression
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Race-Conditions: {errors[:3]}"
