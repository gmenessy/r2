"""Agent-Loop: Tool-Calling, Gatekeeper-Block, Budget-Stop, Memory-Lernen."""

from __future__ import annotations

import pytest

from brainfump import BrainFumpKernel
from apps.agent_layer.billing import BillingLedger, BudgetExceededError
from apps.agent_layer.llm import ChatResult, LLMError, ToolCall
from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.tools import builtin_registry
from apps.agent_layer.xai import TraceStore


class FakeLLM:
    """Skriptbarer LLM-Ersatz (Duck-Type von VLLMClient.chat)."""

    def __init__(self, script: list[ChatResult]) -> None:
        self.script = list(script)
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages, tools=None, **_kwargs) -> ChatResult:
        self.seen_messages.append(messages)
        return self.script.pop(0)


def _answer(text: str, prompt=100, completion=20) -> ChatResult:
    return ChatResult(content=text, prompt_tokens=prompt, completion_tokens=completion)


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> ChatResult:
    return ChatResult(content="", prompt_tokens=100, completion_tokens=10,
                      tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)])


def _runtime(script: list[ChatResult], kernel=None, ledger=None, max_steps: int = 4):
    traces = TraceStore()
    runtime = AgentRuntime(
        llm=FakeLLM(script), registry=builtin_registry(kernel), traces=traces,
        kernel=kernel, ledger=ledger, max_steps=max_steps,
    )
    return runtime, traces


def test_direct_answer_run() -> None:
    runtime, traces = _runtime([_answer("Die Antwort ist 42.")])
    result = runtime.run("Was ist die Antwort?")
    assert result.status == "ok" and result.answer == "Die Antwort ist 42."
    assert result.llm_calls == 1 and result.tool_calls == 0
    trace = traces.trace(result.run_id)
    assert [s["kind"] for s in trace["steps"]] == ["llm_call"]
    assert trace["status"] == "ok"


def test_tool_calling_roundtrip_through_sandbox() -> None:
    runtime, traces = _runtime([
        _tool_call("calc", {"expression": "6*7"}),
        _answer("Das Ergebnis ist 42."),
    ])
    result = runtime.run("Rechne 6*7")
    assert result.status == "ok" and result.tool_calls == 1

    tool_step = next(s for s in traces.trace(result.run_id)["steps"] if s["kind"] == "tool_call")
    assert tool_step["payload"]["sandbox"]["exit_reason"] == "ok"
    assert tool_step["payload"]["outcome"]["value"] == {"result": 42}

    # Das Tool-Ergebnis muss dem Modell als tool-Message zurückgereicht werden.
    followup = runtime.llm.seen_messages[-1]
    assert followup[-1]["role"] == "tool" and "42" in followup[-1]["content"]


def test_unknown_tool_and_invalid_args_are_reported_to_model() -> None:
    runtime, _ = _runtime([
        _tool_call("teleport", {"to": "moon"}),
        _tool_call("calc", {"nonsense": True}, call_id="call_2"),
        _answer("fertig"),
    ])
    result = runtime.run("mach was")
    assert result.status == "ok"
    tool_messages = [m for m in runtime.llm.seen_messages[-1] if m["role"] == "tool"]
    assert "unknown tool" in tool_messages[0]["content"]
    assert "invalid arguments" in tool_messages[1]["content"]


def test_gatekeeper_blocks_forbidden_tool() -> None:
    kernel = BrainFumpKernel(None)
    kernel.record("policy_violation", "calc ist untersagt",
                  payload={"forbidden_actions": ["calc"]})
    runtime, traces = _runtime([
        _tool_call("calc", {"expression": "1+1"}),
        _answer("ok, ich lasse es"),
    ], kernel=kernel)
    result = runtime.run("Rechne 1+1", case_id="case_блок")
    assert result.status == "ok"

    tool_step = next(s for s in traces.trace(result.run_id)["steps"] if s["kind"] == "tool_call")
    assert tool_step["payload"]["gate"]["allowed"] is False
    assert tool_step["payload"]["outcome"]["error"] == "blocked by memory gatekeeper"
    assert "sandbox" not in tool_step["payload"]  # nie ausgeführt


def test_memory_recall_shapes_system_prompt_and_runs_are_memorized() -> None:
    kernel = BrainFumpKernel(None)
    kernel.record("decision", "Deployment läuft über Blue-Green.", case_id="ops")
    runtime, traces = _runtime([_answer("Blue-Green, wie beschlossen.")], kernel=kernel)
    result = runtime.run("Wie deployen wir? Blue-Green?", case_id="ops")

    assert result.memory_hits and "Blue-Green" in result.memory_hits[0]["statement"]
    system = runtime.llm.seen_messages[0][0]
    assert system["role"] == "system" and "Blue-Green" in system["content"]
    assert traces.trace(result.run_id)["steps"][0]["kind"] == "memory_hits"

    # Erfolgreiche Runs landen als successful_attempt im Event Log.
    events = [e for e in kernel.events.query(case_id="ops")
              if e.event_type == "successful_attempt"]
    assert events and events[-1].payload["run_id"] == result.run_id


def test_budget_exhaustion_stops_run() -> None:
    ledger = BillingLedger()
    ledger.create_key("tiny", budget_usd=0.00001)
    runtime, traces = _runtime([
        _answer("teuer", prompt=1_000_000, completion=1_000_000),
    ], ledger=ledger)
    result = runtime.run("teure Frage", tenant="tiny")
    assert result.status == "budget_exceeded"
    assert "budget exhausted" in result.answer
    assert any(s["kind"] == "budget_stop" for s in traces.trace(result.run_id)["steps"])


def test_max_steps_bounds_tool_loops() -> None:
    runtime, _ = _runtime([
        _tool_call("calc", {"expression": "1+1"}, call_id=f"call_{i}") for i in range(3)
    ], max_steps=3)
    result = runtime.run("endlos")
    assert result.status == "max_steps" and result.llm_calls == 3


def test_billing_records_llm_and_tool_charges() -> None:
    ledger = BillingLedger()
    ledger.create_key("acme", budget_usd=10.0)
    runtime, _ = _runtime([
        _tool_call("calc", {"expression": "2+2"}),
        _answer("4"),
    ], ledger=ledger)
    result = runtime.run("2+2?", tenant="acme")
    breakdown = ledger.run_cost(result.run_id)
    kinds = [item["kind"] for item in breakdown["items"]]
    assert kinds == ["llm", "tool", "llm"]
    assert result.cost_usd == pytest.approx(breakdown["total_usd"])


def test_preflight_blocks_exhausted_tenant_before_any_llm_call() -> None:
    """F3: Ein erschöpfter Tenant löst keinen einzigen echten LLM-Call mehr aus."""
    ledger = BillingLedger()
    ledger.create_key("broke", budget_usd=0.0)
    runtime, traces = _runtime([_answer("dürfte nie gesendet werden")], ledger=ledger)
    result = runtime.run("frage", tenant="broke")
    assert result.status == "budget_exceeded" and result.llm_calls == 0
    assert runtime.llm.script  # das Skript wurde nicht angerührt → kein LLM-Call
    assert traces.trace(result.run_id)["steps"][0]["kind"] == "budget_stop"


def test_tool_arguments_cannot_override_gatekeeper_context() -> None:
    """F4: {"action_type": "harmlos"} als Tool-Argument darf das Gate nicht umgehen."""
    kernel = BrainFumpKernel(None)
    kernel.record("policy_violation", "risky verboten",
                  payload={"forbidden_actions": ["risky"]})
    runtime, traces = _runtime([
        _tool_call("risky", {"action_type": "harmlos"}),
        _answer("ok"),
    ], kernel=kernel)
    runtime.registry.tool(
        "risky", "d",
        {"type": "object", "properties": {"action_type": {"type": "string"}}},
    )(lambda action_type="x": {"done": True})
    result = runtime.run("x", case_id="a")
    step = next(s for s in traces.trace(result.run_id)["steps"] if s["kind"] == "tool_call")
    assert step["payload"]["gate"]["allowed"] is False


def test_llm_error_finishes_trace_and_reports_status() -> None:
    """F7: Ein LLM-Backend-Fehler hinterlässt keinen 'running'-Zombie-Trace."""

    class ExplodingLLM:
        def chat(self, messages, tools=None, **_):
            raise LLMError("backend down")

    traces = TraceStore()
    runtime = AgentRuntime(llm=ExplodingLLM(), registry=builtin_registry(), traces=traces)
    result = runtime.run("frage")
    assert result.status == "llm_error" and "backend down" in result.answer
    trace = traces.trace(result.run_id)
    assert trace["status"] == "llm_error"
    assert "Abbruch durch LLM-Backend-Fehler" in " ".join(traces.explain(result.run_id)["narrative"])


def test_budget_error_outside_runtime_still_raises() -> None:
    ledger = BillingLedger()
    ledger.create_key("t", budget_usd=0.0)
    with pytest.raises(BudgetExceededError):
        ledger.charge_tool("t", "r", "calc")
