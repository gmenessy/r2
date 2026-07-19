"""Tenant-Sharding (S5-1/S5-2): stabile, deterministische Zuordnung."""

from __future__ import annotations

import pytest

from apps.agent_layer.sharding import Shard, ShardManifest


def test_owner_is_deterministic_and_in_range() -> None:
    manifest = ShardManifest(total=4)
    for tenant in ("acme", "rival", "kim", "üöä", ""):
        idx = manifest.owner_index(tenant)
        assert 0 <= idx < 4
        assert manifest.owner_index(tenant) == idx  # stabil bei Wiederholung


def test_owner_is_process_stable_via_sha256() -> None:
    """Der Kern von Sharding: identische Zuordnung über Instanzen/Prozesse —
    darum sha256 statt des prozess-gesalzenen hash()."""
    manifest = ShardManifest(total=8)
    # Fest verankerte Erwartung: bricht, falls jemand auf hash() umstellt.
    assert manifest.owner_index("acme") == int(
        __import__("hashlib").sha256(b"acme").hexdigest(), 16) % 8


def test_owns_matches_owner() -> None:
    manifest = ShardManifest(total=3)
    idx = manifest.owner_index("acme")
    assert manifest.owns("acme", idx)
    assert not manifest.owns("acme", (idx + 1) % 3)


def test_tenants_distribute_across_shards() -> None:
    manifest = ShardManifest(total=4)
    seen = {manifest.owner_index(f"tenant-{i}") for i in range(200)}
    assert seen == {0, 1, 2, 3}  # alle Shards werden genutzt


def test_manifest_carries_urls() -> None:
    manifest = ShardManifest(total=2, urls={0: "http://a:8060", 1: "http://b:8060"})
    tenant = "acme"
    owner = manifest.owner(tenant)
    assert owner.base_url in ("http://a:8060", "http://b:8060")
    assert manifest.to_dict()["shards"][0] == {"index": 0, "base_url": "http://a:8060"}


def test_single_manifest_owns_everything() -> None:
    manifest = ShardManifest.single()
    assert all(manifest.owns(f"t{i}", 0) for i in range(50))


def test_invalid_total_rejected() -> None:
    with pytest.raises(ValueError):
        ShardManifest(total=0)


def test_shard_to_dict() -> None:
    assert Shard(2, "http://x").to_dict() == {"index": 2, "base_url": "http://x"}
