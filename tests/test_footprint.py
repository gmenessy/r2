"""Footprint-Gate (S4-1): misst die Charter-Budgets und hält sie ein.

Verankert Leichtgewichtigkeit (docs/PLATFORM_CHARTER.md §2) als Test — jede
Regression im Ressourcen-Fußabdruck bricht die Suite, nicht erst die manuelle
Kontrolle."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.measure_footprint import BUDGETS, measure


def test_all_footprint_budgets_are_held() -> None:
    metrics = measure()
    for key, budget in BUDGETS.items():
        assert key in metrics, f"unmessbares Budget: {key}"
        assert metrics[key] <= budget, f"Budget gesprengt: {key}={metrics[key]} > {budget}"


def test_zero_runtime_dependencies_is_enforced() -> None:
    # Das Herzstück der Leichtgewichtigkeit: keine Pflicht-Abhängigkeit.
    assert BUDGETS["runtime_dependencies"] == 0
    assert measure()["runtime_dependencies"] == 0


def test_metrics_are_plausible() -> None:
    metrics = measure()
    assert metrics["core_loc"] > 500          # es gibt echten Code
    assert metrics["cold_start_ms"] > 0
    assert metrics["idle_rss_mib"] > 0
