"""Tests für den Prompt-Optimizer (App Nr. 3)."""

import json
import threading
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from apps.prompt_optimizer.optimizer import PromptOptimizer, prompt_signature
from apps.prompt_optimizer.server import create_server


def make_optimizer():
    return PromptOptimizer(BrainFumpKernel())


def test_signature_is_whitespace_and_case_tolerant():
    assert prompt_signature("Fasse  das\nzusammen") == prompt_signature("fasse das zusammen")
    assert prompt_signature("a") != prompt_signature("b")


def test_failed_variant_is_blocked_on_retry():
    opt = make_optimizer()
    opt.record_experiment("proj", "Fasse zusammen.", score=0.2, task="summarize")
    result = opt.check_variant("proj", "fasse  zusammen.")
    assert result["decision"]["mode"] == "block"
    assert result["known_results"][0]["outcome"] == "failure"


def test_failed_variant_suggests_best_known_alternative():
    opt = make_optimizer()
    opt.record_experiment("proj", "Guter Prompt mit Details.", score=0.9, task="summarize")
    opt.record_experiment("proj", "Schlechter Prompt.", score=0.1, task="summarize")
    result = opt.check_variant("proj", "Schlechter Prompt.")
    assert result["decision"]["mode"] == "suggest_alternative"
    assert "Guter Prompt" in result["decision"]["suggested_alternative"]


def test_successful_variant_is_not_blocked():
    opt = make_optimizer()
    opt.record_experiment("proj", "Guter Prompt.", score=0.95)
    result = opt.check_variant("proj", "Guter Prompt.")
    assert result["decision"]["mode"] == "allow"
    assert result["known_results"][0]["outcome"] == "success"


def test_projects_are_isolated():
    opt = make_optimizer()
    opt.record_experiment("proj_a", "Prompt X", score=0.1)
    result = opt.check_variant("proj_b", "Prompt X")
    assert result["decision"]["mode"] == "allow"
    assert result["known_results"] == []


def test_score_bounds():
    with pytest.raises(ValueError):
        make_optimizer().record_experiment("proj", "p", score=1.5)


def test_correction_compiles_to_output_rule():
    opt = make_optimizer()
    result = opt.add_correction(
        "proj",
        "Antworten auf Deutsch im Du-Stil, nie siezen.",
        must_not_contain=["Sehr geehrte"],
    )
    assert len(result["rule_ids"]) == 1
    decision = opt.validate_output("proj", "Sehr geehrte Damen und Herren, ...")
    assert decision["mode"] == "warn"
    assert decision["findings"][0]["gate"] == "runtime_rule"
    # Konformer Output passiert das Gate.
    assert opt.validate_output("proj", "Hi, hier ist deine Zusammenfassung.")["mode"] == "allow"


def test_must_contain_correction_does_not_poison_pretest_gate():
    """K3: Eine must_contain-Korrektur darf NUR validate_output betreffen,
    nicht das Pre-Test-Gate (check_variant) jeder neuen Variante."""
    opt = make_optimizer()
    opt.add_correction("p", "Antwort muss Quellen nennen", must_contain=["Quelle"])
    # Pre-Test-Gate für eine unbekannte Variante: kein gescheiterter Fix → allow.
    result = opt.check_variant("p", "Komplett neue Prompt-Variante")
    assert result["decision"]["mode"] == "allow"
    # Die Output-Validierung erzwingt die Regel weiterhin.
    assert opt.validate_output("p", "Antwort ohne Beleg")["mode"] == "warn"
    assert opt.validate_output("p", "Antwort mit Quelle X")["mode"] == "allow"


def test_correction_rules_are_project_scoped():
    opt = make_optimizer()
    opt.add_correction("proj_a", "kein Denglisch", must_not_contain=["committen"])
    assert opt.validate_output("proj_b", "bitte committen")["mode"] == "allow"
    assert opt.validate_output("proj_a", "bitte committen")["mode"] == "warn"


def test_metrics():
    opt = make_optimizer()
    opt.record_experiment("proj", "A", score=0.9, task="t")
    opt.record_experiment("proj", "B", score=0.2, task="t")
    opt.check_variant("proj", "B")  # blockierter Retry
    metrics = opt.metrics("proj")
    assert metrics["experiments"] == 2
    assert metrics["successes"] == 1
    assert metrics["failures"] == 1
    assert metrics["best_score"] == 0.9
    assert metrics["blocked_retries"] == 1


def test_http_roundtrip():
    opt = make_optimizer()
    server = create_server(opt, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(path, payload=None):
        if payload is None:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                return json.loads(r.read())
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as r:
            return json.loads(r.read())

    try:
        result = call(
            "/api/experiments",
            {"project": "proj", "prompt_text": "Prompt P", "score": 0.1, "task": "t"},
        )
        assert result["outcome"] == "failure"
        check = call("/api/check", {"project": "proj", "prompt_text": "Prompt P"})
        assert check["decision"]["mode"] == "block"
        metrics = call("/api/metrics?project=proj")
        assert metrics["experiments"] == 1
        # UI wird ausgeliefert
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert b"Prompt-Optimizer" in r.read()
    finally:
        server.shutdown()
