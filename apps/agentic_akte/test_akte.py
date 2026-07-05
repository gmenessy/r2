"""Tests für die Agentische Akte (App Nr. 1)."""

import json
import threading
import urllib.request

from brainfump import BrainFumpKernel
from apps.agentic_akte.akte import AgenticAkte
from apps.agentic_akte.server import create_server


def make_akte():
    return AgenticAkte(BrainFumpKernel())


def test_global_dna_blocks_delete_in_every_case():
    akte = make_akte()
    decision = akte.check_action("akte_1", "delete_document", documents=["x.pdf"])
    assert decision["mode"] == "block"
    assert decision["findings"][0]["gate"] == "governance"
    # gilt auch in einer anderen Akte
    assert akte.check_action("akte_2", "delete_document")["mode"] == "block"


def test_global_dna_seeded_only_once():
    kernel = BrainFumpKernel()
    AgenticAkte(kernel)
    AgenticAkte(kernel)
    governance = kernel.cards.active(case_id=None, memory_type="governance")
    assert len(governance) == 1


def test_fragile_document_requires_review():
    akte = make_akte()
    akte.add_document(
        "akte_1", "vertrag_original.pdf", "Unterschriebenes Original", fragile=True
    )
    decision = akte.check_action(
        "akte_1", "modify_document", documents=["vertrag_original.pdf"]
    )
    assert decision["mode"] == "require_review"
    # Anderes Dokument bleibt frei.
    assert akte.check_action("akte_1", "modify_document", documents=["notiz.md"])["mode"] == "allow"


def test_rejected_analysis_is_not_repeated():
    akte = make_akte()
    akte.record_failed_analysis(
        "akte_1",
        "Auslegung über §5 Abs. 2 trägt nicht.",
        signature="auslegung_§5_variante_a",
        alternative="Über §7 argumentieren.",
    )
    decision = akte.check_action(
        "akte_1", "apply_analysis", error_signature="auslegung_§5_variante_a"
    )
    assert decision["mode"] == "suggest_alternative"
    assert "§7" in decision["suggested_alternative"]


def test_deadlines_with_overdue_status():
    akte = make_akte()
    akte.add_deadline("akte_1", "Stellungnahme", "2099-01-01")
    akte.add_deadline("akte_1", "Vorfrist", "2020-01-01")
    deadlines = akte.deadlines("akte_1", today="2026-06-12")
    assert [d["status"] for d in deadlines] == ["overdue", "open"]
    # Überfällige Fristen erscheinen in jeder Gate-Antwort.
    decision = akte.check_action("akte_1", "send_draft")
    assert len(decision["overdue_deadlines"]) >= 1


def test_timeline_is_audit_trail():
    akte = make_akte()
    akte.add_document("akte_1", "a.pdf", "Eingang")
    akte.record_decision("akte_1", "Vanilla JS bleibt.")
    akte.check_action("akte_1", "send_draft")
    timeline = akte.timeline("akte_1")
    types = [e["event_type"] for e in timeline]
    assert "document_change" in types
    assert "decision" in types
    assert "agent_action" in types  # Gate-Entscheidung selbst ist auditiert


def test_cases_listing_and_isolation():
    akte = make_akte()
    akte.add_document("akte_1", "a.pdf", "x", fragile=True)
    akte.record_decision("akte_2", "y")
    assert akte.cases() == ["akte_1", "akte_2"]
    # Risiko aus akte_1 leakt nicht nach akte_2.
    assert akte.check_action("akte_2", "modify_document", documents=["a.pdf"])["mode"] == "allow"


def test_consolidate_detects_contradictions():
    akte = make_akte()
    akte.record_decision("akte_1", "React im Frontend verwenden", scope=["frontend"])
    akte.record_decision("akte_1", "Kein React im Frontend verwenden", scope=["frontend"])
    report = akte.consolidate("akte_1")
    assert len(report["contradictions"]) == 1


def test_http_roundtrip():
    akte = make_akte()
    server = create_server(akte, host="127.0.0.1", port=0)
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
            "/api/documents",
            {"case": "akte_1", "name": "v.pdf", "summary": "Original", "fragile": True},
        )
        decision = post(
            "/api/check",
            {"case": "akte_1", "action_type": "modify_document", "documents": ["v.pdf"]},
        )
        assert decision["mode"] == "require_review"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/memory?case=akte_1") as r:
            cards = json.loads(r.read())["cards"]
        assert any(c["memory_type"] == "risk" for c in cards)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert "Agentische Akte" in r.read().decode()
        # E-3: Wiki-Projektion der Akte als Markdown.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/wiki?case=akte_1") as r:
            wiki = r.read().decode()
        assert "# Akte: akte_1" in wiki
        assert "v.pdf" in wiki
    finally:
        server.shutdown()
