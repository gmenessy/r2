"""Rate-Limiting pro Tenant (S3-4/O5) — Token-Bucket, thread-sicher.

In-Memory (instanzlokal, passend zur Ein-Prozess-Architektur): jeder Tenant
bekommt einen Eimer mit ``burst`` Tokens, der mit ``per_minute / 60`` Tokens
pro Sekunde nachfüllt. Ein Run kostet ein Token; ist der Eimer leer, meldet
:meth:`acquire` die Wartezeit bis zum nächsten freien Token (``Retry-After``).

Der Zeitgeber ist injizierbar (``now=``), damit Tests ohne echte Uhr auskommen.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token-Bucket-Limiter mit einem Eimer je Tenant."""

    def __init__(self, per_minute: int = 60, burst: int | None = None,
                 now: Callable[[], float] | None = None) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.per_minute = per_minute
        self.burst = float(burst if burst is not None else per_minute)
        self._refill_per_s = per_minute / 60.0
        self._now = now or __import__("time").monotonic
        self._lock = threading.RLock()
        self._buckets: dict[str, _Bucket] = {}

    def acquire(self, tenant: str, cost: float = 1.0) -> tuple[bool, float]:
        """Ein Token abbuchen. Rückgabe ``(erlaubt, retry_after_sekunden)``.

        Bei Erfolg ist ``retry_after`` 0.0; bei Ablehnung die Sekunden bis
        genug Tokens nachgefüllt sind."""
        with self._lock:
            now = self._now()
            bucket = self._buckets.get(tenant)
            if bucket is None:
                bucket = _Bucket(tokens=self.burst, updated=now)
                self._buckets[tenant] = bucket
            # Nachfüllen (auf burst gedeckelt).
            elapsed = now - bucket.updated
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * self._refill_per_s)
            bucket.updated = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0.0
            deficit = cost - bucket.tokens
            return False, deficit / self._refill_per_s
