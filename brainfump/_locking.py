"""Gemeinsamer Locking-Helfer für die SQLite-gestützten Stores.

Die App-Server laufen auf ``ThreadingHTTPServer`` und teilen sich je eine
Store-Instanz mit genau einer SQLite-Connection (``check_same_thread=False``)
plus In-RAM-State (z. B. der Token-Index). Ohne Serialisierung führt das zu
Races (``dictionary changed size during iteration``, ``database is locked``).
``@locked`` serialisiert den Zugriff über ein ``threading.RLock`` der Instanz
(re-entrant, damit sich Methoden gegenseitig aufrufen dürfen).
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def locked(method: F) -> F:
    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
