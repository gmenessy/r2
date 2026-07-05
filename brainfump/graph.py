"""E-2 — Memory Graph (A-MEM / Zettelkasten-Schicht).

Beziehungen zwischen Memory Cards werden bisher nur implizit gehalten
(``supersedes``-Feld, ``exception_to`` im payload, paarweise
Widerspruchserkennung). Diese Schicht macht sie **explizit, typisiert und
persistent abfragbar** — als gerichteter Graph mit typisierten Kanten:

- ``supersedes``     neue Version → ersetzte Version
- ``exception_to``   Scope-Ausnahme → Grundregel
- ``contradicts``    widersprechende Aussagen (Metadaten: winner/loser)
- ``depends_on``     nutzerdeklarierte Abhängigkeit
- ``relates_to``     freie Verknüpfung

Damit lassen sich „Warum wurde das stillgelegt?", Abhängigkeitsketten und
Konfliktnetze traversieren, statt bei jeder Frage O(n²) neu zu vergleichen.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

from brainfump._locking import locked
from brainfump.events import utc_now

EDGE_TYPES = frozenset(
    {"supersedes", "exception_to", "contradicts", "depends_on", "relates_to", "evidence"}
)


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    rel: str
    weight: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "rel": self.rel,
            "weight": self.weight,
            "meta": self.meta,
            "created_at": self.created_at,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS edges (
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    rel        TEXT NOT NULL,
    weight     REAL NOT NULL,
    meta       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (src, dst, rel)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges (dst);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges (rel);
"""


class MemoryGraph:
    """Persistenter, thread-safer Beziehungsgraph über Card-IDs."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    @locked
    def add_edge(
        self,
        src: str,
        dst: str,
        rel: str = "relates_to",
        weight: float = 1.0,
        meta: dict[str, Any] | None = None,
    ) -> Edge:
        if rel not in EDGE_TYPES:
            raise ValueError(f"unknown edge type: {rel!r}")
        edge = Edge(src=src, dst=dst, rel=rel, weight=weight, meta=meta or {}, created_at=utc_now())
        self._conn.execute(
            "INSERT OR REPLACE INTO edges VALUES (?, ?, ?, ?, ?, ?)",
            (src, dst, rel, weight, json.dumps(edge.meta), edge.created_at),
        )
        self._conn.commit()
        return edge

    @locked
    def edges_of(
        self, card_id: str, rel: str | None = None, direction: str = "both"
    ) -> list[Edge]:
        """Kanten an ``card_id``. direction: 'out' (src), 'in' (dst), 'both'."""
        clauses, params = [], []
        if direction == "out":
            clauses.append("src = ?")
            params.append(card_id)
        elif direction == "in":
            clauses.append("dst = ?")
            params.append(card_id)
        else:
            clauses.append("(src = ? OR dst = ?)")
            params.extend([card_id, card_id])
        if rel is not None:
            clauses.append("rel = ?")
            params.append(rel)
        sql = "SELECT src, dst, rel, weight, meta, created_at FROM edges WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, rel"
        return [self._row_to_edge(r) for r in self._conn.execute(sql, params)]

    @locked
    def neighbors(self, card_id: str, rel: str | None = None, direction: str = "both") -> list[str]:
        seen: list[str] = []
        for e in self.edges_of(card_id, rel=rel, direction=direction):
            other = e.dst if e.src == card_id else e.src
            if other not in seen and other != card_id:
                seen.append(other)
        return seen

    @locked
    def all_edges(self, rel: str | None = None) -> list[Edge]:
        sql = "SELECT src, dst, rel, weight, meta, created_at FROM edges"
        params: list[Any] = []
        if rel is not None:
            sql += " WHERE rel = ?"
            params.append(rel)
        sql += " ORDER BY created_at"
        return [self._row_to_edge(r) for r in self._conn.execute(sql, params)]

    @locked
    def remove_node(self, card_id: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM edges WHERE src = ? OR dst = ?", (card_id, card_id)
        )
        self._conn.commit()
        return cur.rowcount

    @locked
    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    def reachable(self, card_id: str, rel: str, direction: str = "out", max_depth: int = 16) -> list[str]:
        """Transitiv erreichbare Knoten entlang eines Kantentyps (z. B. die
        gesamte Abhängigkeits- oder Supersedes-Kette)."""
        out: list[str] = []
        frontier = [card_id]
        depth = 0
        visited = {card_id}
        while frontier and depth < max_depth:
            nxt: list[str] = []
            for node in frontier:
                for other in self.neighbors(node, rel=rel, direction=direction):
                    if other not in visited:
                        visited.add(other)
                        out.append(other)
                        nxt.append(other)
            frontier = nxt
            depth += 1
        return out

    @staticmethod
    def _row_to_edge(row: Iterable[Any]) -> Edge:
        src, dst, rel, weight, meta, created_at = row
        return Edge(src=src, dst=dst, rel=rel, weight=weight, meta=json.loads(meta), created_at=created_at)
