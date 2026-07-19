"""Billing: API-Keys, Token-Ledger und Budget-Durchsetzung (SQLite).

Abgerechnet wird in Mikro-USD (Ganzzahlen — keine Float-Drift in der
Buchhaltung): LLM-Aufrufe nach Prompt-/Completion-Tokens, Tool-Aufrufe
pauschal pro Ausführung. Jede Buchung referenziert den Run und ist damit im
xAI-Trace nachvollziehbar ("was hat diese Antwort gekostet und warum").

API-Keys werden nur als SHA-256-Hash gespeichert; der Klartext existiert
genau einmal in der Antwort von :meth:`BillingLedger.create_key`.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from brainfump._locking import locked

MICRO_PER_USD = 1_000_000


class BudgetExceededError(Exception):
    """Das Tenant-Budget deckt die anstehende Buchung nicht mehr."""


@dataclass(frozen=True)
class PriceTable:
    """Preise in Mikro-USD; Defaults passend für ein selbst gehostetes 31B-Modell."""

    prompt_per_1m: int = 150_000       # 0.15 USD / 1M Prompt-Tokens
    completion_per_1m: int = 600_000   # 0.60 USD / 1M Completion-Tokens
    per_tool_call: int = 100           # 0.0001 USD pro Tool-Ausführung

    def llm_cost(self, prompt_tokens: int, completion_tokens: int) -> int:
        # Aufrunden (Ceiling): sonst wären Mini-Calls unterhalb der
        # Mikro-USD-Auflösung dauerhaft kostenlos (Finding F8).
        raw = prompt_tokens * self.prompt_per_1m + completion_tokens * self.completion_per_1m
        return -(-raw // 1_000_000)


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


class BillingLedger:
    """Ein SQLite-Ledger pro Plattform-Instanz; thread-sicher via ``@locked``."""

    def __init__(self, path: str = ":memory:", prices: PriceTable | None = None) -> None:
        self.prices = prices or PriceTable()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash    TEXT PRIMARY KEY,
                tenant      TEXT NOT NULL,
                budget_micro INTEGER NOT NULL,
                created_at  REAL NOT NULL,
                revoked     INTEGER NOT NULL DEFAULT 0,
                expires_at  REAL
            );
            CREATE TABLE IF NOT EXISTS usage (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                tenant      TEXT NOT NULL,
                run_id      TEXT NOT NULL,
                kind        TEXT NOT NULL,
                units_in    INTEGER NOT NULL DEFAULT 0,
                units_out   INTEGER NOT NULL DEFAULT 0,
                cost_micro  INTEGER NOT NULL,
                detail      TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_usage_tenant ON usage(tenant);
            CREATE INDEX IF NOT EXISTS idx_usage_run ON usage(run_id);
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY,
                tenant       TEXT NOT NULL,
                run_id       TEXT NOT NULL,
                amount_micro INTEGER NOT NULL,
                created_at   REAL NOT NULL,
                released     INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_res_tenant ON reservations(tenant, released);
            """
        )
        # Migration für Bestands-DBs, die vor S3-4 ohne expires_at angelegt wurden.
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(api_keys)")}
        if "expires_at" not in cols:  # pragma: no cover - nur bei Alt-DBs
            self._conn.execute("ALTER TABLE api_keys ADD COLUMN expires_at REAL")
        self._conn.commit()

    # -- Keys -----------------------------------------------------------------

    @locked
    def create_key(self, tenant: str, budget_usd: float = 10.0,
                   ttl_seconds: float | None = None) -> str:
        api_key = f"agl_{secrets.token_urlsafe(24)}"
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        self._conn.execute(
            "INSERT INTO api_keys (key_hash, tenant, budget_micro, created_at, expires_at)"
            " VALUES (?,?,?,?,?)",
            (_hash_key(api_key), tenant, int(budget_usd * MICRO_PER_USD), time.time(), expires_at),
        )
        self._conn.commit()
        return api_key

    @locked
    def resolve(self, api_key: str) -> str | None:
        """Tenant zu einem Key — nur wenn nicht widerrufen UND nicht abgelaufen (S3-4)."""
        row = self._conn.execute(
            "SELECT tenant, expires_at FROM api_keys WHERE key_hash = ? AND revoked = 0",
            (_hash_key(api_key),),
        ).fetchone()
        if row is None:
            return None
        tenant, expires_at = row
        if expires_at is not None and expires_at <= time.time():
            return None
        return tenant

    @locked
    def revoke_key(self, api_key: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE key_hash = ?", (_hash_key(api_key),)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @locked
    def rotate_key(self, api_key: str, grace_seconds: float = 300.0) -> str | None:
        """Key rotieren: neuen Key für denselben Tenant/Budget ausstellen, den
        alten nach einem Kulanzfenster ablaufen lassen (S3-4). ``None``, wenn
        der alte Key unbekannt/bereits ungültig ist."""
        row = self._conn.execute(
            "SELECT tenant, budget_micro, expires_at FROM api_keys"
            " WHERE key_hash = ? AND revoked = 0",
            (_hash_key(api_key),),
        ).fetchone()
        if row is None:
            return None
        tenant, budget_micro, expires_at = row
        now = time.time()
        if expires_at is not None and expires_at <= now:
            return None
        new_key = f"agl_{secrets.token_urlsafe(24)}"
        self._conn.execute(
            "INSERT INTO api_keys (key_hash, tenant, budget_micro, created_at, expires_at)"
            " VALUES (?,?,?,?,?)",
            (_hash_key(new_key), tenant, budget_micro, now, None),
        )
        # Alten Key im Kulanzfenster auslaufen lassen (nicht sofort kappen) und
        # sein Budget auf 0 setzen — sonst zählte der Tenant das Budget doppelt,
        # solange beide Keys gültig sind.
        grace_expiry = now + grace_seconds
        new_expiry = grace_expiry if expires_at is None else min(grace_expiry, expires_at)
        self._conn.execute(
            "UPDATE api_keys SET expires_at = ?, budget_micro = 0 WHERE key_hash = ?",
            (new_expiry, _hash_key(api_key)),
        )
        self._conn.commit()
        return new_key

    # -- Buchungen ------------------------------------------------------------

    @locked
    def has_budget(self, tenant: str) -> bool:
        """Preflight: hat der Tenant noch Rest-Budget? (ohne Budget: ja).

        Verhindert, dass ein erschöpfter Tenant noch echte LLM-Kosten auslöst,
        bevor die Buchung scheitert (Finding F3). Berücksichtigt offene
        Reservierungen (S3-3)."""
        budget = self._budget(tenant)
        return budget is None or self._committed(tenant) < budget

    # -- Reservierung (S3-3: Pre-Auth vor dem LLM-Call, Settlement danach) -----

    @locked
    def reserve(self, tenant: str, run_id: str, amount_micro: int) -> str:
        """Höchstpreis eines anstehenden Calls vorab binden.

        Schließt das „ein Call über Budget"-Fenster (O4): reicht das
        Rest-Budget (abzüglich Verbrauch UND bereits offener Reservierungen)
        nicht, wird der Call gar nicht erst ausgelöst. Gibt eine
        Reservierungs-ID zurück, die :meth:`settle` oder :meth:`release`
        wieder auflöst."""
        budget = self._budget(tenant)
        if budget is not None and self._committed(tenant) + amount_micro > budget:
            raise BudgetExceededError(
                f"tenant {tenant!r}: budget exhausted — reservation of "
                f"{amount_micro / MICRO_PER_USD:.4f} USD exceeds remaining "
                f"{(budget - self._committed(tenant)) / MICRO_PER_USD:.4f} USD"
            )
        reservation_id = f"res_{secrets.token_hex(8)}"
        self._conn.execute(
            "INSERT INTO reservations (reservation_id, tenant, run_id, amount_micro, created_at)"
            " VALUES (?,?,?,?,?)",
            (reservation_id, tenant, run_id, max(0, amount_micro), time.time()),
        )
        self._conn.commit()
        return reservation_id

    @locked
    def settle(self, reservation_id: str, tenant: str, run_id: str, prompt_tokens: int,
               completion_tokens: int) -> int:
        """Reservierung auflösen und die TATSÄCHLICHEN Kosten buchen.

        Die Differenz zur Reservierung wird implizit gutgeschrieben (die
        Reservierung verschwindet, nur der echte Verbrauch bleibt). Prüft das
        Budget nicht erneut — der Call ist bereits erfolgt und muss verbucht
        werden; die Reservierung hat den Rahmen zuvor garantiert."""
        cost = self.prices.llm_cost(prompt_tokens, completion_tokens)
        self._release(reservation_id)
        self._conn.execute(
            "INSERT INTO usage (ts, tenant, run_id, kind, units_in, units_out, cost_micro, detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), tenant, run_id, "llm", prompt_tokens, completion_tokens, cost,
             "chat_completion"),
        )
        self._conn.commit()
        return cost

    @locked
    def release(self, reservation_id: str) -> None:
        """Reservierung ohne Buchung freigeben (Abbruch/Fehler vor dem Call)."""
        self._release(reservation_id)
        self._conn.commit()

    def _release(self, reservation_id: str) -> None:
        self._conn.execute(
            "UPDATE reservations SET released = 1 WHERE reservation_id = ?", (reservation_id,)
        )

    def _active_reservations(self, tenant: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(amount_micro), 0) FROM reservations"
            " WHERE tenant = ? AND released = 0",
            (tenant,),
        ).fetchone()
        return int(row[0])

    def _committed(self, tenant: str) -> int:
        """Verbindlich gebunden: verbucht + offen reserviert."""
        return self._spent(tenant) + self._active_reservations(tenant)

    @locked
    def charge_llm(self, tenant: str, run_id: str, prompt_tokens: int,
                   completion_tokens: int) -> int:
        cost = self.prices.llm_cost(prompt_tokens, completion_tokens)
        self._charge(tenant, run_id, "llm", prompt_tokens, completion_tokens, cost,
                     detail="chat_completion")
        return cost

    @locked
    def charge_tool(self, tenant: str, run_id: str, tool_name: str) -> int:
        cost = self.prices.per_tool_call
        self._charge(tenant, run_id, "tool", 1, 0, cost, detail=tool_name)
        return cost

    def _charge(self, tenant: str, run_id: str, kind: str, units_in: int,
                units_out: int, cost: int, detail: str) -> None:
        budget = self._budget(tenant)
        if budget is not None and self._committed(tenant) + cost > budget:
            raise BudgetExceededError(
                f"tenant {tenant!r}: budget exhausted "
                f"({self._committed(tenant) / MICRO_PER_USD:.4f} of {budget / MICRO_PER_USD:.4f} USD)"
            )
        self._conn.execute(
            "INSERT INTO usage (ts, tenant, run_id, kind, units_in, units_out, cost_micro, detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), tenant, run_id, kind, units_in, units_out, cost, detail),
        )
        self._conn.commit()

    def _budget(self, tenant: str) -> int | None:
        row = self._conn.execute(
            "SELECT SUM(budget_micro) FROM api_keys WHERE tenant = ? AND revoked = 0"
            " AND (expires_at IS NULL OR expires_at > ?)",
            (tenant, time.time()),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def _spent(self, tenant: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_micro), 0) FROM usage WHERE tenant = ?", (tenant,)
        ).fetchone()
        return int(row[0])

    # -- Reporting ------------------------------------------------------------

    @locked
    def usage(self, tenant: str) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT kind, COUNT(*), SUM(units_in), SUM(units_out), SUM(cost_micro)"
            " FROM usage WHERE tenant = ? GROUP BY kind",
            (tenant,),
        ).fetchall()
        by_kind = {
            kind: {"calls": calls, "units_in": units_in or 0, "units_out": units_out or 0,
                   "cost_usd": (cost or 0) / MICRO_PER_USD}
            for kind, calls, units_in, units_out, cost in rows
        }
        budget = self._budget(tenant)
        spent = self._spent(tenant)
        return {
            "tenant": tenant,
            "by_kind": by_kind,
            "spent_usd": spent / MICRO_PER_USD,
            "budget_usd": None if budget is None else budget / MICRO_PER_USD,
            "remaining_usd": None if budget is None else (budget - spent) / MICRO_PER_USD,
        }

    @locked
    def totals(self) -> dict[str, float]:
        """Instanzweite Summen für den Metrics-Endpoint (S5-5)."""
        spent = self._conn.execute("SELECT COALESCE(SUM(cost_micro), 0) FROM usage").fetchone()[0]
        tenants = self._conn.execute(
            "SELECT COUNT(DISTINCT tenant) FROM api_keys WHERE revoked = 0").fetchone()[0]
        reserved = self._conn.execute(
            "SELECT COALESCE(SUM(amount_micro), 0) FROM reservations WHERE released = 0"
        ).fetchone()[0]
        return {"spent_usd": spent / MICRO_PER_USD, "active_tenants": int(tenants),
                "reserved_usd": reserved / MICRO_PER_USD}

    @locked
    def run_cost(self, run_id: str) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT kind, detail, units_in, units_out, cost_micro FROM usage"
            " WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        items = [
            {"kind": kind, "detail": detail, "units_in": units_in, "units_out": units_out,
             "cost_usd": cost / MICRO_PER_USD}
            for kind, detail, units_in, units_out, cost in rows
        ]
        return {"run_id": run_id, "items": items,
                "total_usd": sum(item["cost_usd"] for item in items)}
