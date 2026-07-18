"""SimulatedLLM: Regieanweisungen, Heuristiken, Ergebnis-Zusammenfassung."""

from __future__ import annotations

import json

from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.simllm import SimulatedLLM
from apps.agent_layer.tools import builtin_registry
from apps.agent_layer.xai import TraceStore


def _messages(goal: str) -> list[dict]:
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": goal}]


def test_directive_emits_one_tool_call_per_turn() -> None:
    llm = SimulatedLLM()
    goal = '[tool:calc {"expression": "1+1"}] [tool:utc_now {}]'
    first = llm.chat(_messages(goal))
    assert first.wants_tools and first.tool_calls[0].name == "calc"
    assert first.tool_calls[0].arguments == {"expression": "1+1"}
    assert first.prompt_tokens > 0 and first.completion_tokens > 0

    # Nach einem Tool-Ergebnis folgt die zweite Regieanweisung.
    history = _messages(goal) + [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "sim_call_1", "type": "function",
             "function": {"name": "calc", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "sim_call_1",
         "content": json.dumps({"ok": True, "value": {"result": 2}})},
    ]
    second = llm.chat(history)
    assert second.wants_tools and second.tool_calls[0].name == "utc_now"


def test_final_answer_summarizes_outcomes_including_failures() -> None:
    llm = SimulatedLLM()
    goal = '[tool:calc {"expression": "1+1"}]'
    history = _messages(goal) + [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "sim_call_1", "type": "function",
             "function": {"name": "calc", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "sim_call_1",
         "content": json.dumps({"ok": False, "error": "blocked by memory gatekeeper"})},
    ]
    answer = llm.chat(history)
    assert not answer.wants_tools
    assert "FEHLGESCHLAGEN" in answer.content and "blocked" in answer.content
    assert "Nicht alle Schritte" in answer.content


def test_arithmetic_heuristic_without_directives() -> None:
    result = SimulatedLLM().chat(_messages("Rechne bitte (17+25)*3 aus."))
    assert result.wants_tools
    assert result.tool_calls[0].name == "calc"
    assert "(17+25)*3" in result.tool_calls[0].arguments["expression"]


def test_answer_directive_fixes_final_text() -> None:
    result = SimulatedLLM().chat(_messages("Sag hallo. [answer:Hallo Welt!]"))
    assert not result.wants_tools and result.content == "Hallo Welt!"


def test_plain_goal_gets_direct_answer() -> None:
    result = SimulatedLLM().chat(_messages("Erzähl mir von der Plattform."))
    assert not result.wants_tools and "keine Tools" in result.content


def test_broken_directive_json_degrades_to_empty_args() -> None:
    result = SimulatedLLM().chat(_messages("[tool:calc {kaputt}]"))
    assert result.wants_tools and result.tool_calls[0].arguments == {}


def test_end_to_end_with_runtime_and_sandbox() -> None:
    """Der Sim-LLM treibt den echten Loop: Tool-Call → Sandbox → Antwort."""
    runtime = AgentRuntime(llm=SimulatedLLM(), registry=builtin_registry(),
                           traces=TraceStore())
    result = runtime.run('Rechne. [tool:calc {"expression": "6*7"}]')
    assert result.status == "ok" and result.tool_calls == 1
    assert '"result": 42' in result.answer
    assert "Alle Schritte erfolgreich" in result.answer
