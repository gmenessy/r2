"""Asynchrone Runs mit Backpressure (Sprint 4, S4-2/S4-5).

Lange Runs sollen keinen HTTP-Worker-Thread binden: ``submit()`` reiht den Run
in einen kleinen, festen ``ThreadPoolExecutor`` und gibt sofort die ``run_id``
zurück; ``status()`` pollt Zustand und Ergebnis. Rein Stdlib
(``concurrent.futures``) — kein Broker, kein neuer Dienst (Charter §3).

Backpressure schützt den kleinen Prozess vor Überlast, statt unbegrenzt zu
puffern:
- pro Tenant zu viele Runs in Flight → :class:`TenantQueueFullError` (→ HTTP 429)
- global zu viele Runs in Flight → :class:`QueueFullError` (→ HTTP 503)
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from apps.agent_layer.runtime import AgentRuntime, RunResult


class QueueFullError(Exception):
    """Globale In-Flight-Grenze erreicht (→ 503)."""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__("run queue is full")


class TenantQueueFullError(Exception):
    """Der Tenant hat zu viele Runs gleichzeitig in Flight (→ 429)."""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__("too many concurrent runs for tenant")


class AsyncRunner:
    """Führt Runs im Hintergrund aus; Zustand/Ergebnis über ``status()`` abrufbar."""

    def __init__(self, runtime: AgentRuntime, max_workers: int = 4,
                 max_inflight_total: int = 64, max_inflight_per_tenant: int = 8,
                 retry_after_s: float = 5.0) -> None:
        self.runtime = runtime
        self.max_inflight_total = max_inflight_total
        self.max_inflight_per_tenant = max_inflight_per_tenant
        self.retry_after_s = retry_after_s
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="agent-run")
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}

    # -- Öffentlich -----------------------------------------------------------

    def submit(self, goal: str, tenant: str, case_id: str | None = None) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        with self._lock:
            total, per_tenant = self._inflight(tenant)
            if total >= self.max_inflight_total:
                raise QueueFullError(self.retry_after_s)
            if per_tenant >= self.max_inflight_per_tenant:
                raise TenantQueueFullError(self.retry_after_s)
            self._states[run_id] = {
                "run_id": run_id, "tenant": tenant, "goal": goal,
                "status": "queued", "submitted_at": time.time(), "result": None,
            }
        self._executor.submit(self._execute, run_id, goal, tenant, case_id)
        return run_id

    def status(self, run_id: str) -> dict[str, Any] | None:
        """Zustand eines Runs: queued | running | done | error (+ Ergebnis)."""
        with self._lock:
            state = self._states.get(run_id)
            return dict(state) if state is not None else None

    def stats(self) -> dict[str, int]:
        """In-Flight-Zähler für den Metrics-Endpoint (S5-5)."""
        with self._lock:
            queued = sum(1 for s in self._states.values() if s["status"] == "queued")
            running = sum(1 for s in self._states.values() if s["status"] == "running")
        return {"queued": queued, "running": running, "inflight": queued + running}

    def shutdown(self) -> None:  # pragma: no cover - Lebenszyklus
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- Intern ---------------------------------------------------------------

    def _inflight(self, tenant: str) -> tuple[int, int]:
        active = [s for s in self._states.values() if s["status"] in ("queued", "running")]
        return len(active), sum(1 for s in active if s["tenant"] == tenant)

    def _execute(self, run_id: str, goal: str, tenant: str, case_id: str | None) -> None:
        with self._lock:
            self._states[run_id]["status"] = "running"
        try:
            result: RunResult = self.runtime.run(goal, tenant=tenant, case_id=case_id,
                                                 run_id=run_id)
            with self._lock:
                self._states[run_id]["status"] = "done"
                self._states[run_id]["result"] = result.to_dict()
        except Exception as exc:  # noqa: BLE001 - Grenze des Hintergrund-Jobs
            with self._lock:
                self._states[run_id]["status"] = "error"
                self._states[run_id]["result"] = {"error": f"{type(exc).__name__}: {exc}"}
