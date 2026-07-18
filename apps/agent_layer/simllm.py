"""SimulatedLLM — deterministischer LLM-Ersatz für Demo und Integrationstests.

Die Plattform wird damit ohne GPU/vLLM end-to-end erlebbar: Sandbox,
Gatekeeper, Billing und xAI verhalten sich exakt wie im Produktivbetrieb,
nur die Modell-Entscheidung ist geskriptet. Duck-Type von
:meth:`apps.agent_layer.llm.VLLMClient.chat` — via ``AGENT_SIM=1`` auch im
Server aktivierbar.

Steuerung über Regieanweisungen im Goal-Text:

    [tool:parse_expense {"text": "Taxi 62,50 EUR"}]   → ein Tool-Call pro Turn
    [answer:Alles geprüft.]                            → fixierte Schlussantwort

Ohne Regieanweisungen greifen Heuristiken (arithmetischer Ausdruck → ``calc``),
danach fasst die Schlussantwort alle Tool-Ergebnisse zusammen — inklusive
Blocks und Sandbox-Fehlern, damit die Kausalkette auch in der Antwort sichtbar
ist. Token-Zahlen werden aus den Textlängen geschätzt, damit Billing-Pfade
realistisch laufen.
"""

from __future__ import annotations

import json
import re
from typing import Any

from apps.agent_layer.llm import ChatResult, ToolCall

_DIRECTIVE = re.compile(r"\[tool:(\w+)\s*(\{.*?\})?\]", re.DOTALL)
_ANSWER = re.compile(r"\[answer:(.+?)\]", re.DOTALL)
_ARITH_RUN = re.compile(r"[0-9+\-*/(),.\s]+")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class SimulatedLLM:
    """Regelbasierter, vollständig deterministischer Chat-'Backend'-Ersatz."""

    model = "sim-llm"
    base_url = "offline://simulated"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> ChatResult:
        goal = next((m["content"] for m in messages if m["role"] == "user"), "")
        prompt_tokens = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)

        directives = self._directives(goal)
        done = sum(1 for m in messages if m["role"] == "tool")
        if done < len(directives):
            name, arguments = directives[done]
            return ChatResult(
                content="",
                tool_calls=[ToolCall(call_id=f"sim_call_{done + 1}", name=name,
                                     arguments=arguments)],
                prompt_tokens=prompt_tokens,
                completion_tokens=12,
                latency_ms=1.0,
            )

        answer = self._final_answer(goal, messages)
        return ChatResult(
            content=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=_estimate_tokens(answer),
            latency_ms=1.0,
        )

    # -- Skript-Ableitung -----------------------------------------------------

    @staticmethod
    def _directives(goal: str) -> list[tuple[str, dict[str, Any]]]:
        directives: list[tuple[str, dict[str, Any]]] = []
        for name, raw_args in _DIRECTIVE.findall(goal):
            try:
                arguments = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                arguments = {}
            directives.append((name, arguments))
        if not directives:
            expression = SimulatedLLM._find_expression(goal)
            if expression:
                directives.append(("calc", {"expression": expression}))
        return directives

    @staticmethod
    def _find_expression(goal: str) -> str | None:
        """Längster arithmetischer Kandidat im Text (deutsches Komma → Punkt)."""
        best: str | None = None
        for raw in _ARITH_RUN.findall(goal):
            candidate = re.sub(r"(\d),(\d)", r"\1.\2", raw).strip(" \t\n,.")
            has_value_operator = re.search(r"\d\s*[+\-*/]", candidate) or re.search(
                r"[+\-*/]\s*[\d(]", candidate
            )
            if not re.search(r"\d", candidate) or not has_value_operator:
                continue
            if best is None or len(candidate) > len(best):
                best = candidate
        return best

    def _final_answer(self, goal: str, messages: list[dict[str, Any]]) -> str:
        fixed = _ANSWER.search(goal)
        if fixed:
            return fixed.group(1).strip()

        outcomes = self._tool_outcomes(messages)
        if not outcomes:
            return "Verstanden — für dieses Ziel brauche ich keine Tools."

        lines = []
        for name, outcome in outcomes:
            if outcome.get("ok"):
                lines.append(f"{name}: {json.dumps(outcome.get('value'), ensure_ascii=False)}")
            else:
                lines.append(f"{name}: FEHLGESCHLAGEN — {outcome.get('error')}")
        verdict = ("Alle Schritte erfolgreich."
                   if all(o.get("ok") for _, o in outcomes)
                   else "Nicht alle Schritte waren möglich (Details oben).")
        return "Ergebnis:\n" + "\n".join(f"- {line}" for line in lines) + f"\n{verdict}"

    @staticmethod
    def _tool_outcomes(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        """(Tool-Name, Ergebnis) in Aufrufreihenfolge; Name via tool_call_id."""
        names: dict[str, str] = {}
        for message in messages:
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
        outcomes: list[tuple[str, dict[str, Any]]] = []
        for message in messages:
            if message["role"] != "tool":
                continue
            try:
                outcome = json.loads(message["content"])
            except json.JSONDecodeError:
                outcome = {"ok": False, "error": "unreadable tool result"}
            outcomes.append((names.get(message.get("tool_call_id", ""), "tool"), outcome))
        return outcomes
