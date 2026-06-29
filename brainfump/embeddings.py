"""Sprint 1 — Embedding-Provider und persistenter Vektor-Cache.

Macht den in M-5 vorbereiteten fRAG-Embedding-Slot produktiv nutzbar,
ohne externe Abhängigkeiten:

- ``HashingEmbedder``: deterministisches, abhängigkeitsfreies Embedding über
  Feature-Hashing (Bag-of-Words → fixe Dimension, L2-normalisiert). Dient als
  Offline-Default-Provider und als Testdouble für echte API-Embedder.
- ``SqliteVectorCache``: persistiert Card-Vektoren, sodass nach einem Neustart
  nicht alles neu eingebettet werden muss (Kosten-/Latenzersparnis).

Ein echter API-Embedder lässt sich identisch einhängen — er muss nur eine
``Callable[[str], Sequence[float]]`` sein.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from typing import Iterator, MutableMapping, Sequence

from brainfump._locking import locked

_TOKEN = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class HashingEmbedder:
    """Deterministisches Feature-Hashing-Embedding (stdlib-only).

    Bildet Tokens per stabilem Hash auf ``dim`` Dimensionen ab (signiertes
    Hashing gegen systematische Kollisions-Bias) und L2-normalisiert das
    Ergebnis, sodass das Skalarprodukt direkt die Cosinus-Ähnlichkeit ist.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokens(text):
            digest = hashlib.md5(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec


class SqliteVectorCache(MutableMapping):
    """Persistenter ``MutableMapping``-Cache für Embedding-Vektoren.

    Schlüssel sind Strings (z. B. ``card_id`` + Statement-Hash), Werte sind
    Float-Sequenzen. Drop-in für das interne dict von ``EmbeddingSimilarity``.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vec TEXT NOT NULL)"
        )
        self._conn.commit()

    @locked
    def __getitem__(self, key: str) -> Sequence[float]:
        row = self._conn.execute(
            "SELECT vec FROM vectors WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    @locked
    def __setitem__(self, key: str, value: Sequence[float]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO vectors (key, vec) VALUES (?, ?)",
            (key, json.dumps(list(value))),
        )
        self._conn.commit()

    @locked
    def __delitem__(self, key: str) -> None:
        cur = self._conn.execute("DELETE FROM vectors WHERE key = ?", (key,))
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(key)

    @locked
    def __iter__(self) -> Iterator[str]:
        return iter([row[0] for row in self._conn.execute("SELECT key FROM vectors")])

    @locked
    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
