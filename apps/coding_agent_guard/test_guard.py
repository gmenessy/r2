"""Tests für den Coding-Agent-Guard (App Nr. 2)."""

import json
import threading
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from apps.coding_agent_guard.guard import CodingAgentGuard
from apps.coding_agent_guard.server import create_server


def make_guard():
    return CodingAgentGuard(BrainFumpKernel())


def test_failed_fix_blocks_retry():
    guard = make_guard()
    guard.report(
        "repo_a",
        "failed_attempt",
        "Retry-Loop hat Timeout nicht behoben.",
        error_signature="TimeoutError: gateway",
        alternative="Circuit Breaker einbauen.",
    )
    decision = guard.gate("repo_a", error_signature="TimeoutError: gateway")
    assert decision["mode"] == "suggest_alternative"
    assert "Circuit Breaker" in decision["suggested_alternative"]


def test_fragile_file_requires_review():
    guard = make_guard()
    guard.report(
        "repo_a", "fragile_file", "Legacy-Abrechnung, keine Tests.", files=["legacy_billing.py"]
    )
    decision = guard.gate("repo_a", files=["legacy_billing.py", "other.py"])
    assert decision["mode"] == "require_review"
    assert guard.gate("repo_a", files=["other.py"])["mode"] == "allow"


def test_repos_are_isolated():
    guard = make_guard()
    guard.report("repo_a", "failed_attempt", "Fix scheiterte.", error_signature="sig_1")
    assert guard.gate("repo_b", error_signature="sig_1")["mode"] == "allow"


def test_correction_becomes_enforced_rule():
    guard = make_guard()
    guard.report(
        "repo_a",
        "correction",
        "Bitte keine React-Abhängigkeit im MVP.",
        forbid_dependencies=["react"],
        project_type="mvp_frontend",
    )
    decision = guard.gate(
        "repo_a",
        action_type="finish_task",
        context={
            "project_type": "mvp_frontend",
            "case_id": "repo_a",
            "package_json": {"dependencies": {"react": "^18"}},
        },
    )
    assert decision["mode"] == "warn"


def test_unknown_report_type_rejected():
    with pytest.raises(ValueError):
        make_guard().report("repo_a", "gossip", "...")


def test_summary_is_deterministic_markdown():
    guard = make_guard()
    guard.report("repo_a", "decision", "Architektur: FastAPI + Qdrant.")
    guard.report("repo_a", "fragile_file", "Keine Tests.", files=["legacy.py"])
    guard.report(
        "repo_a",
        "failed_attempt",
        "Cache-Invalidierung über TTL scheiterte.",
        error_signature="stale_cache",
        alternative="Event-basierte Invalidierung.",
    )
    summary = guard.summary("repo_a")
    assert summary == guard.summary("repo_a")  # deterministisch
    assert "FastAPI + Qdrant" in summary
    assert "legacy.py" in summary
    assert "Event-basierte Invalidierung" in summary
    assert "Projektgedächtnis: repo_a" in summary


def test_empty_summary():
    assert "Noch keine Erinnerungen" in make_guard().summary("leer")


def test_stats_count_interventions():
    guard = make_guard()
    guard.report("repo_a", "failed_attempt", "x", error_signature="sig")
    guard.gate("repo_a", error_signature="sig")   # block
    guard.gate("repo_a", error_signature="anders")  # allow
    stats = guard.stats("repo_a")
    assert stats["gate_checks"] == 2
    assert stats["interventions"] == 1


def test_http_roundtrip():
    guard = make_guard()
    server = create_server(guard, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as r:
            return json.loads(r.read())

    try:
        post(
            "/api/report",
            {
                "repo": "repo_a",
                "report_type": "fragile_file",
                "content": "fragil",
                "payload": {"files": ["core.py"]},
            },
        )
        decision = post("/api/gate", {"repo": "repo_a", "files": ["core.py"]})
        assert decision["mode"] == "require_review"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/summary?repo=repo_a") as r:
            assert "core.py" in r.read().decode()
    finally:
        server.shutdown()
