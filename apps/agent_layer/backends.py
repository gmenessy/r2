"""Persistenz-Naht (Sprint 5, S5-3) — Vertrag, kein Backend.

Die Plattform ist bewusst SQLite-only (Charter §4: kein eingebautes
schweres Backend). Für Betreiber, die einen gemeinsamen Store *über* das
Tenant-Sharding hinaus wollen, definiert dieses Modul den **Vertrag**, den
ein alternatives Backend erfüllen müsste — als ``Protocol``, das die
vorhandenen SQLite-Stores strukturell bereits erfüllen.

Ein echter Postgres-Adapter ist **nicht** Teil des Kerns und wird offline
nicht gefaked (radikaler Realismus). Er wäre ein optionales Extra
(``pip install .[postgres]``) hinter genau dieser Naht. Der Contract-Test
(``tests``/Skelett) prüft, dass ein Kandidat den Vertrag erfüllt.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LedgerBackend(Protocol):
    """Minimaler Billing-Vertrag (von :class:`BillingLedger` erfüllt)."""

    def create_key(self, tenant: str, budget_usd: float = ...,
                   ttl_seconds: float | None = ...) -> str: ...

    def resolve(self, api_key: str) -> str | None: ...

    def has_budget(self, tenant: str) -> bool: ...

    def reserve(self, tenant: str, run_id: str, amount_micro: int) -> str: ...

    def settle(self, reservation_id: str, tenant: str, run_id: str,
               prompt_tokens: int, completion_tokens: int) -> int: ...

    def usage(self, tenant: str) -> dict[str, Any]: ...


@runtime_checkable
class TraceBackend(Protocol):
    """Minimaler xAI-Vertrag (von :class:`TraceStore` erfüllt)."""

    def begin(self, run_id: str, tenant: str, goal: str) -> None: ...

    def step(self, run_id: str, kind: str, payload: dict[str, Any],
             duration_ms: float = ...) -> None: ...

    def finish(self, run_id: str, status: str, answer: str) -> None: ...

    def trace(self, run_id: str) -> dict[str, Any] | None: ...
