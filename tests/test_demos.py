"""Smoke-Tests für die Demos — sie müssen fehlerfrei laufen, und die eine
harte Sicherheitsgrenze (Case-Isolation) muss nachweislich halten.
"""

import os
import subprocess
import sys

_DEMOS = os.path.join(os.path.dirname(__file__), "..", "demos")


def _run(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(_DEMOS, name)],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1"},
    )


def test_chronos_runs():
    r = _run("chronos.py")
    assert r.returncode == 0, r.stderr
    assert "CHRONOS" in r.stdout
    assert "Grenzen des Zeitmodells" in r.stdout


def test_tribunal_runs():
    r = _run("tribunal.py")
    assert r.returncode == 0, r.stderr
    assert "TRIBUNAL" in r.stdout
    # Der klare Negations-Widerspruch muss erkannt werden.
    assert "textueller Widerspruch erkannt" in r.stdout


def test_red_team_runs_and_case_isolation_holds():
    r = _run("red_team.py")
    assert r.returncode == 0, r.stderr
    assert "RED TEAM" in r.stdout
    # Regressionsschutz: Trust-Layer + robustes Matching müssen alle 7 halten.
    assert "ABGEWEHRT" in r.stdout
    assert "abgewehrt:     7/7" in r.stdout
    assert "durchgekommen: 0/7" in r.stdout
