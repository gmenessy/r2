"""Trace-Streaming als Server-Sent Events — wiederverwendbarer Baustein.

Extrahiert aus dem Plattform-Server (Sprint 4, S4-3), damit jede App auf der
Plattform (z. B. ein zweites Cockpit) denselben Live-Stream bekommt, ohne die
Logik zu duplizieren. Reine Stdlib-Generatorfunktion — der HTTP-Transport
(``StreamingResponse`` aus ``brainfump.webkit``) bleibt Sache des Servers.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from apps.agent_layer.xai import TraceStore

TERMINAL_STATUSES = frozenset({"ok", "error", "budget_exceeded", "max_steps", "llm_error"})


def trace_sse_events(
    traces: TraceStore,
    run_id: str,
    poll_s: float = 0.05,
    timeout_s: float = 120.0,
    wait_for_begin: bool = False,
) -> Iterator[bytes]:
    """Trace-Schritte live als SSE, bis der Run terminal ist.

    ``wait_for_begin``: der Run ist async eingereiht, aber noch nicht
    gestartet (kein Trace) — geduldig auf ``begin()`` warten statt sofort mit
    ``unknown run`` abzubrechen (Autorisierung gegen den Runner-Zustand ist
    Sache des Aufrufers, siehe agent_layer/server.py)."""
    sent = 0
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        trace = traces.trace(run_id)
        if trace is None:
            if wait_for_begin:
                yield b": waiting for run start\n\n"  # SSE-Kommentar als Heartbeat
                time.sleep(poll_s)
                continue
            # Bewusst NICHT "error" genannt: der Browser-EventSource liefert
            # ein server-benanntes "error"-Event über denselben Listener-Kanal
            # wie echte Verbindungsfehler — nicht unterscheidbar für den Client.
            yield _event("stream_error", {"error": "unknown run"})
            return
        for step in trace["steps"][sent:]:
            yield _event("step", step)
        sent = len(trace["steps"])
        if trace["status"] in TERMINAL_STATUSES:
            yield _event("done", {"status": trace["status"], "answer": trace["answer"]})
            return
        time.sleep(poll_s)
    yield b"event: timeout\ndata: {}\n\n"


def _event(kind: str, payload: dict[str, Any]) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
