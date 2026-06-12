"""End-to-End: Kernel-Pipeline und /api/gatekeeper/check."""

import json
import threading
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from brainfump.api import create_server, handle_gatekeeper_check
from brainfump.evolution import EvolutionPatch
from brainfump.gatekeeper import GateMode


def test_full_pipeline_correction_to_enforcement():
    """Spec-Beispiel: 'Keine React-Abhängigkeit' wird gespeichert UND erzwungen."""
    kernel = BrainFumpKernel()
    kernel.record(
        "correction",
        "Bitte keine React-Abhängigkeit im MVP.",
        case_id="akte_1",
        payload={
            "forbid_dependencies": ["react"],
            "project_type": "mvp_frontend",
            "scope": ["frontend", "mvp"],
        },
    )

    # 1. Event ist beweissicher geloggt.
    assert len(kernel.events.query(event_type="correction")) == 1
    # 2. Memory Card existiert.
    cards = kernel.cards.active(case_id="akte_1", memory_type="preference")
    assert len(cards) == 1
    # 3. Retrieval findet sie.
    results = kernel.search("React Abhängigkeit MVP", case_id="akte_1")
    assert results[0].card.card_id == cards[0].card_id
    # 4. Gatekeeper erzwingt sie (Preference Compliance, nicht nur Access).
    decision = kernel.check_action(
        {
            "action_type": "finish_task",
            "case_id": "akte_1",
            "context": {
                "project_type": "mvp_frontend",
                "package_json": {"dependencies": {"react": "^18.0.0"}},
            },
        }
    )
    assert decision.mode == GateMode.WARN
    # 5. Die Gate-Entscheidung ist selbst auditierbar geloggt.
    audit = kernel.events.query(case_id="akte_1", event_type="agent_action")
    assert audit[-1].payload["decision"]["mode"] == "warn"


def test_failed_attempt_then_blocked_retry():
    kernel = BrainFumpKernel()
    kernel.record(
        "failed_attempt",
        "Retry-Loop hat Timeout nicht behoben.",
        case_id="akte_1",
        payload={"error_signature": "TimeoutError: gateway"},
    )
    decision = kernel.check_action(
        {"action_type": "apply_fix", "case_id": "akte_1", "error_signature": "TimeoutError: gateway"}
    )
    assert decision.mode == GateMode.BLOCK


def test_evolution_patch_via_kernel():
    kernel = BrainFumpKernel()
    kernel.record("decision", "Vanilla JS only", case_id="akte_1", payload={"scope": ["frontend"]})
    card = kernel.cards.active(case_id="akte_1", include_global=False)[0]
    new = kernel.patch(
        EvolutionPatch(
            target_memory=card.card_id,
            patch_type="replace",
            new_value="Vanilla JS für MVP, Vue für Admin-Backend",
            valid_from="2026-06-12",
        )
    )
    assert kernel.evolution.resolve(card.card_id, on_date="2026-06-15").card_id == new.card_id


def test_persistence_on_disk(tmp_path):
    path = str(tmp_path / "memory")
    kernel = BrainFumpKernel(path)
    kernel.record("decision", "persistiert", case_id="akte_1")

    reloaded = BrainFumpKernel(path)
    assert len(reloaded.events.query(case_id="akte_1")) == 1
    assert len(reloaded.cards.active(case_id="akte_1", include_global=False)) == 1


def test_api_handler_requires_action():
    with pytest.raises(ValueError):
        handle_gatekeeper_check(BrainFumpKernel(), {})


def test_api_gatekeeper_check_over_http():
    kernel = BrainFumpKernel()
    kernel.record(
        "failed_attempt",
        "Fix scheiterte.",
        case_id="akte_1",
        payload={"error_signature": "sig_1"},
    )
    server = create_server(kernel, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(
            {"action": {"case_id": "akte_1", "error_signature": "sig_1"}}
        ).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/gatekeeper/check",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
        assert payload["mode"] == "block"
        assert payload["allowed"] is False
        assert payload["findings"][0]["gate"] == "no_repeat_failed_fix"
    finally:
        server.shutdown()
