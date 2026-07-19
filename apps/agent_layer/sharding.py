"""Tenant-Sharding (Sprint 5, S5-1/S5-2) — horizontale Skalierung, leicht.

Statt einer verteilten Datenbank besitzt jede Instanz exklusiv eine
Tenant-Menge: ``owner_index(tenant) = sha256(tenant) % total``. Kein geteilter
Budget-/Rate-State, keine Races (Charter §5). Der Hash ist bewusst ``sha256``
statt Pythons prozess-gesalzenem ``hash()`` — nur so ist die Zuordnung über
Instanzgrenzen hinweg **stabil und identisch**.

Ein Manifest (Index → Basis-URL) macht das Sharding betreibbar: eine Instanz,
die eine fremde Tenant-Anfrage erhält, weist sie mit der Ziel-URL zurück
(HTTP 421), statt sie falsch zu bedienen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Shard:
    index: int
    base_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "base_url": self.base_url}


class ShardManifest:
    """Feste Instanzmenge; bildet Tenants deterministisch auf Shards ab."""

    def __init__(self, total: int, urls: dict[int, str] | None = None) -> None:
        if total < 1:
            raise ValueError("total shards must be >= 1")
        self.total = total
        self._urls = dict(urls or {})

    def owner_index(self, tenant: str) -> int:
        digest = hashlib.sha256(tenant.encode()).hexdigest()
        return int(digest, 16) % self.total

    def owner(self, tenant: str) -> Shard:
        index = self.owner_index(tenant)
        return Shard(index, self._urls.get(index, ""))

    def owns(self, tenant: str, shard_index: int) -> bool:
        return self.owner_index(tenant) == shard_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "shards": [Shard(i, self._urls.get(i, "")).to_dict() for i in range(self.total)],
        }

    @classmethod
    def single(cls) -> "ShardManifest":
        """Trivialmanifest: eine Instanz besitzt alle Tenants (Default-Betrieb)."""
        return cls(total=1)
