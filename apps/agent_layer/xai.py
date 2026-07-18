"""xAI-Schicht: vollständige, abfragbare Begründung jedes Agent-Runs.

Kein nachträgliches Rationalisieren, sondern ein Trace der tatsächlichen
Kausalkette (AgentOps-/LangSmith-Idee, aber SQLite-leichtgewichtig):
welche Memories den Kontext geformt haben, welche Tool-Aufrufe mit welchem
Sandbox-Urteil und Gatekeeper-Entscheid liefen, welche LLM-Schritte wie
viele Tokens gekostet haben. ``explain()`` verdichtet den Trace zu einer
menschenlesbaren Begründung.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from brainfump._locking import locked


class TraceStore:
    """Append-only Ablage für Runs und ihre Schritte."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id   TEXT PRIMARY KEY,
                tenant   TEXT NOT NULL,
                goal     TEXT NOT NULL,
                status   TEXT NOT NULL DEFAULT 'running',
                answer   TEXT NOT NULL DEFAULT '',
                started  REAL NOT NULL,
                finished REAL
            );
            CREATE TABLE IF NOT EXISTS steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                ts          REAL NOT NULL,
                kind        TEXT NOT NULL,
                duration_ms REAL NOT NULL DEFAULT 0,
                payload     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, seq);
            """
        )
        self._conn.commit()

    @locked
    def begin(self, run_id: str, tenant: str, goal: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, tenant, goal, started) VALUES (?,?,?,?)",
            (run_id, tenant, goal, time.time()),
        )
        self._conn.commit()

    @locked
    def step(self, run_id: str, kind: str, payload: dict[str, Any],
             duration_ms: float = 0.0) -> None:
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM steps WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        self._conn.execute(
            "INSERT INTO steps (run_id, seq, ts, kind, duration_ms, payload) VALUES (?,?,?,?,?,?)",
            (run_id, seq, time.time(), kind, duration_ms,
             json.dumps(payload, ensure_ascii=False, default=str)),
        )
        self._conn.commit()

    @locked
    def finish(self, run_id: str, status: str, answer: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, answer = ?, finished = ? WHERE run_id = ?",
            (status, answer, time.time(), run_id),
        )
        self._conn.commit()

    @locked
    def trace(self, run_id: str) -> dict[str, Any] | None:
        run = self._conn.execute(
            "SELECT run_id, tenant, goal, status, answer, started, finished FROM runs"
            " WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        steps = [
            {"seq": seq, "ts": ts, "kind": kind, "duration_ms": duration_ms,
             "payload": json.loads(payload)}
            for seq, ts, kind, duration_ms, payload in self._conn.execute(
                "SELECT seq, ts, kind, duration_ms, payload FROM steps"
                " WHERE run_id = ? ORDER BY seq",
                (run_id,),
            )
        ]
        return {
            "run_id": run[0], "tenant": run[1], "goal": run[2], "status": run[3],
            "answer": run[4], "started": run[5], "finished": run[6], "steps": steps,
        }

    def explain(self, run_id: str) -> dict[str, Any] | None:
        """Verdichtet den Trace zur Kausalkette der Antwort."""
        trace = self.trace(run_id)
        if trace is None:
            return None

        memories: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        llm_calls = 0
        prompt_tokens = completion_tokens = 0
        narrative: list[str] = [f"Ziel: {trace['goal']}"]

        for step in trace["steps"]:
            payload = step["payload"]
            if step["kind"] == "memory_hits":
                memories = payload.get("hits", [])
                if memories:
                    narrative.append(
                        f"{len(memories)} Memory-Treffer haben den Kontext geformt "
                        f"(top: {memories[0].get('statement', '')!r})."
                    )
            elif step["kind"] == "llm_call":
                llm_calls += 1
                prompt_tokens += payload.get("prompt_tokens", 0)
                completion_tokens += payload.get("completion_tokens", 0)
            elif step["kind"] == "tool_call":
                tool_calls.append(payload)
                verdict = payload.get("gate", {}).get("mode", "allow")
                outcome = payload.get("sandbox", {}).get("exit_reason", "?")
                narrative.append(
                    f"Tool {payload.get('tool')!r}: Gatekeeper={verdict}, Sandbox={outcome}."
                )
            elif step["kind"] == "budget_stop":
                narrative.append(f"Abbruch durch Billing: {payload.get('reason')}")

        narrative.append(
            f"{llm_calls} LLM-Schritte ({prompt_tokens} Prompt-/"
            f"{completion_tokens} Completion-Tokens), Status: {trace['status']}."
        )
        return {
            "run_id": run_id,
            "goal": trace["goal"],
            "status": trace["status"],
            "answer": trace["answer"],
            "memories_used": memories,
            "tool_calls": tool_calls,
            "llm": {"calls": llm_calls, "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens},
            "narrative": narrative,
        }
