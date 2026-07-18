"""RateLimiter (S3-4): Token-Bucket mit injizierter Uhr — deterministisch."""

from __future__ import annotations

import pytest

from apps.agent_layer.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_burst_then_throttle() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=3, now=clock)
    # Drei sofort erlaubte Runs (voller Eimer), dann Ablehnung.
    assert [limiter.acquire("acme")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.acquire("acme")
    assert allowed is False
    assert retry_after == pytest.approx(1.0)  # 1 Token/s → 1 s bis zum nächsten


def test_refill_over_time() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=1, now=clock)
    assert limiter.acquire("acme")[0] is True
    assert limiter.acquire("acme")[0] is False
    clock.advance(1.0)  # ein Token nachgefüllt
    assert limiter.acquire("acme")[0] is True


def test_tenants_are_isolated() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=1, now=clock)
    assert limiter.acquire("acme")[0] is True
    assert limiter.acquire("acme")[0] is False
    # Anderer Tenant hat seinen eigenen vollen Eimer.
    assert limiter.acquire("rival")[0] is True


def test_burst_is_capped_on_refill() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=2, now=clock)
    clock.advance(100)  # lange Pause darf den Eimer nicht über burst füllen
    assert limiter.acquire("acme")[0] is True
    assert limiter.acquire("acme")[0] is True
    assert limiter.acquire("acme")[0] is False


def test_invalid_rate_rejected() -> None:
    with pytest.raises(ValueError):
        RateLimiter(per_minute=0)
