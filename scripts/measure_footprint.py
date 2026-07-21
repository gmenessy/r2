#!/usr/bin/env python3
"""Footprint-Gate (Sprint 4, S4-1) — Leichtgewichtigkeit als messbares CI-Kriterium.

Misst die in der Platform Charter (docs/PLATFORM_CHARTER.md §2) definierten
Größen und bricht mit Exit-Code 1 ab, sobald ein Budget überschritten wird.
So bleibt Leichtgewichtigkeit kein Slogan, sondern ein Build-Fehler.

Gemessen wird:
- externe Runtime-Abhängigkeiten (aus pyproject.toml)
- Kern-LOC (apps/agent_layer/*.py ohne Tests)
- Demo-App-LOC (apps/agent_flightdeck/{flightdeck,server,__init__}.py)
- Kaltstart bis "server-ready" (Import + Aufbau aller Stores)
- RSS im Leerlauf

Beispiel:
    python3 scripts/measure_footprint.py            # misst + prüft Budgets
    python3 scripts/measure_footprint.py --json      # nur Messwerte als JSON
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import resource
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Budgets aus der Charter §2 (~30 % Kopffreiheit über der Baseline vom 2026-07-18).
# core_loc am 2026-07-19 bewusst auf 2900 angehoben (Charter-Eskalationsklausel
# "wer das Budget ausschöpft, muss den Gegenwert begründen"): Path A, die
# WASM-Sandbox (wasm_sandbox.py, ~200 LOC) — ~80x schneller als der Fork-Pfad
# für numerische Tools, echtes Capability-Sandboxing. Begründung + Benchmarks:
# docs/PATH_A_WASM_SANDBOX.md. Kein optionales Extra zählt gegen "runtime_dependencies".
BUDGETS: dict[str, float] = {
    "runtime_dependencies": 0,
    "core_loc": 2900,
    "demo_loc": 500,
    "cold_start_ms": 400,
    "idle_rss_mib": 60,
}


def _loc(paths: list[str]) -> int:
    total = 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            total += sum(1 for _ in fh)
    return total


def _core_files() -> list[str]:
    return sorted(
        p for p in glob.glob(os.path.join(ROOT, "apps/agent_layer/*.py"))
        if not os.path.basename(p).startswith("test_")
    )


def _demo_files() -> list[str]:
    base = os.path.join(ROOT, "apps/agent_flightdeck")
    return [os.path.join(base, name) for name in ("__init__.py", "flightdeck.py", "server.py")]


def _runtime_dependencies() -> int:
    """Zählt die Pflicht-Abhängigkeiten aus pyproject.toml ``dependencies``."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        text = fh.read()
    # Bewusst simpel (kein tomllib-Zwang auf 3.10): den dependencies-Block lesen.
    start = text.index("dependencies = [")
    end = text.index("]", start)
    body = text[start + len("dependencies = ["):end]
    return len([item for item in body.split(",") if item.strip().strip('"')])


def _cold_start_and_rss() -> tuple[float, int]:
    """Import + Aufbau aller Stores in einem frischen Subprozess messen."""
    import subprocess

    # RSS über /proc/self/status VmRSS (aktuelle Resident-Memory) statt
    # ru_maxrss: Letzteres erbt beim fork+exec kurzzeitig die COW-Seiten des
    # Elternprozesses in seinen Peak und misst unter einem großen Parent
    # (pytest/coverage) zu hoch. VmRSS spiegelt nur den eigenen Speicher.
    probe = (
        "import time,sys;"
        "sys.path.insert(0, %r);"
        "t=time.perf_counter();"
        "from brainfump import BrainFumpKernel;"
        "from apps.agent_layer import AgentRuntime,BillingLedger,SimulatedLLM,TraceStore,builtin_registry;"
        "k=BrainFumpKernel(None);"
        "AgentRuntime(llm=SimulatedLLM(),registry=builtin_registry(k),traces=TraceStore(),kernel=k,ledger=BillingLedger());"
        "ms=(time.perf_counter()-t)*1000;"
        "rss=next(int(l.split()[1])//1024 for l in open('/proc/self/status') if l.startswith('VmRSS:'));"
        "print(ms, rss)"
    ) % ROOT
    # Vollständig isolierter Interpreter (``-I`` ignoriert PYTHON*-Env-Vars und
    # User-Site, ``-S`` überspringt jegliche site-Verarbeitung inkl. des
    # Coverage-Auto-Start-``.pth``). Damit ist die Messung deterministisch —
    # unabhängig davon, ob measure() unter `pytest --cov` läuft — und spiegelt
    # den realen Produktions-Fußabdruck. Gefahrlos, weil der Kern keine
    # Fremd-Abhängigkeiten importiert (die Probe setzt ROOT selbst auf sys.path).
    out = subprocess.check_output([sys.executable, "-I", "-S", "-c", probe], cwd=ROOT, text=True)
    ms_s, rss_s = out.split()
    return float(ms_s), int(rss_s)


def measure() -> dict[str, float]:
    cold_ms, rss_mib = _cold_start_and_rss()
    return {
        "runtime_dependencies": _runtime_dependencies(),
        "core_loc": _loc(_core_files()),
        "demo_loc": _loc(_demo_files()),
        "cold_start_ms": round(cold_ms, 1),
        "idle_rss_mib": rss_mib,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Footprint-Gate der Plattform")
    parser.add_argument("--json", action="store_true", help="nur Messwerte als JSON ausgeben")
    args = parser.parse_args()

    metrics = measure()
    if args.json:
        print(json.dumps(metrics, indent=2))
        return 0

    print("Footprint (Charter §2):")
    violations: list[str] = []
    for key, value in metrics.items():
        budget = BUDGETS[key]
        ok = value <= budget
        marker = "OK " if ok else "!! "
        print(f"  {marker}{key:22} {value:>8}  (Budget ≤ {budget})")
        if not ok:
            violations.append(f"{key}={value} > {budget}")

    if violations:
        print("\nFOOTPRINT-GATE FEHLGESCHLAGEN:")
        for v in violations:
            print(f"  - {v}")
        print("\nCharter §2: Feature aufteilen oder weglassen — Budget ist ein Build-Fehler.")
        return 1
    print("\nAlle Budgets gehalten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
