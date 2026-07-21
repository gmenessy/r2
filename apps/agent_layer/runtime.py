"""AgentRuntime — der Ausführungs-Loop der Plattform.

ReAct-Schleife (Reason → Act → Observe) mit vier harten Querschnitten:

1. **Memory**: Vor dem ersten LLM-Schritt formt fRAG-Retrieval aus dem
   BrainFump-Kernel den Systemkontext; Ergebnisse fließen als Events zurück
   (der Agent lernt über Runs hinweg — MemGPT-/A-MEM-Linie).
2. **Gatekeeper**: Jeder Tool-Aufruf passiert den Memory Gatekeeper
   (Pre-Action Gate); geblocktes wird nie ausgeführt, sondern dem Modell als
   Verweigerung zurückgemeldet.
3. **Sandbox**: Ausführung untrusted Tools nur im rlimit-gedeckelten
   Kindprozess (:mod:`apps.agent_layer.sandbox`) — oder, für reine
   numerische ``engine="wasm"``-Tools, in der ~80× schnelleren
   :class:`~apps.agent_layer.wasm_sandbox.WasmSandbox` (Path A, optional).
4. **Billing + xAI**: Jeder LLM-/Tool-Schritt wird bepreist und getract;
   ein erschöpftes Budget stoppt den Run deterministisch.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from apps.agent_layer.billing import BillingLedger, BudgetExceededError
from apps.agent_layer.llm import (
    ChatResult,
    LLMError,
    ToolCall,
    VLLMClient,
    estimate_prompt_tokens,
)
from apps.agent_layer.sandbox import ProcessSandbox
from apps.agent_layer.tools import ToolRegistry, ToolSpec, validate_args
from apps.agent_layer.wasm_sandbox import WasmSandbox, WasmUnavailableError
from apps.agent_layer.xai import TraceStore

DEFAULT_SYSTEM_PROMPT = (
    "You are an execution agent on a lightweight agent platform. "
    "Solve the user's goal step by step. Use the available tools when they help; "
    "call them with valid JSON arguments only. When you have the final answer, "
    "reply with plain text and no further tool calls."
)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str  # ok | max_steps | budget_exceeded | llm_error
    answer: str
    llm_calls: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    memory_hits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "status": self.status, "answer": self.answer,
            "llm_calls": self.llm_calls, "tool_calls": self.tool_calls,
            "cost_usd": round(self.cost_usd, 6), "memory_hits": self.memory_hits,
        }


class AgentRuntime:
    """Verdrahtet LLM, Tools, Sandbox, Memory-Kernel, Billing und Traces."""

    def __init__(
        self,
        llm: VLLMClient,
        registry: ToolRegistry,
        traces: TraceStore,
        kernel: Any | None = None,
        ledger: BillingLedger | None = None,
        sandbox: ProcessSandbox | None = None,
        wasm_sandbox: WasmSandbox | None = None,
        max_steps: int = 6,
        memory_k: int = 3,
        max_tokens: int = 1024,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.traces = traces
        self.kernel = kernel
        self.ledger = ledger
        self.sandbox = sandbox or ProcessSandbox()
        # Lazy: WasmSandbox() erfordert wasmtime (optionales Extra) — nur
        # instanziieren, wenn tatsächlich ein wasm-Tool aufgerufen wird, damit
        # die Plattform ohne wasmtime unverändert lauffähig bleibt.
        self._wasm_sandbox = wasm_sandbox
        self.max_steps = max_steps
        self.memory_k = memory_k
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt

    def _get_wasm_sandbox(self) -> WasmSandbox:
        if self._wasm_sandbox is None:
            self._wasm_sandbox = WasmSandbox()
        return self._wasm_sandbox

    # -- Öffentliche API ------------------------------------------------------

    def run(self, goal: str, tenant: str = "default", case_id: str | None = None,
            run_id: str | None = None) -> RunResult:
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        self.traces.begin(run_id, tenant, goal)

        # Preflight (F3): Ein erschöpfter Tenant darf keinen einzigen echten
        # LLM-Call mehr auslösen — der würde Kosten verursachen, die die
        # anschließende Buchung nur noch ablehnen kann.
        if self.ledger is not None and not self.ledger.has_budget(tenant):
            reason = f"tenant {tenant!r}: budget exhausted (preflight)"
            self.traces.step(run_id, "budget_stop", {"reason": reason})
            self.traces.finish(run_id, "budget_exceeded", f"aborted: {reason}")
            return RunResult(run_id=run_id, status="budget_exceeded",
                             answer=f"aborted: {reason}")

        memory_hits = self._recall(run_id, goal, case_id)
        messages = self._initial_messages(goal, memory_hits)

        llm_calls = tool_calls = 0
        answer, status = "", "max_steps"
        try:
            for _ in range(self.max_steps):
                result = self._llm_step(run_id, tenant, messages)
                llm_calls += 1
                if not result.wants_tools:
                    answer, status = result.content.strip(), "ok"
                    break
                messages.append(self._assistant_message(result))
                for call in result.tool_calls:
                    outcome = self._execute_tool(run_id, tenant, case_id, call)
                    tool_calls += 1
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": json.dumps(outcome, ensure_ascii=False),
                    })
        except BudgetExceededError as exc:
            status, answer = "budget_exceeded", f"aborted: {exc}"
            self.traces.step(run_id, "budget_stop", {"reason": str(exc)})
        except LLMError as exc:
            # F7: Der Trace darf nie als 'running' hängen bleiben — der Fehler
            # ist Teil der Kausalkette und gehört ins Audit.
            status, answer = "llm_error", f"aborted: {exc}"
            self.traces.step(run_id, "llm_error", {"error": str(exc)})

        self._memorize(goal, case_id, run_id, status)
        self.traces.finish(run_id, status, answer)
        return RunResult(
            run_id=run_id, status=status, answer=answer,
            llm_calls=llm_calls, tool_calls=tool_calls,
            cost_usd=self._run_cost(run_id), memory_hits=memory_hits,
        )

    # -- Bausteine ------------------------------------------------------------

    def _recall(self, run_id: str, goal: str, case_id: str | None) -> list[dict[str, Any]]:
        if self.kernel is None:
            return []
        hits = [
            {"score": round(hit.score, 4), "statement": hit.card.statement,
             "memory_type": hit.card.memory_type, "card_id": hit.card.card_id}
            for hit in self.kernel.search(goal, case_id=case_id, k=self.memory_k)
        ]
        self.traces.step(run_id, "memory_hits", {"query": goal, "case_id": case_id, "hits": hits})
        return hits

    def _initial_messages(self, goal: str, memory_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system = self.system_prompt
        if memory_hits:
            known = "\n".join(f"- ({h['memory_type']}) {h['statement']}" for h in memory_hits)
            system += f"\n\nKnown facts from long-term memory (respect corrections):\n{known}"
        return [{"role": "system", "content": system}, {"role": "user", "content": goal}]

    def _llm_step(self, run_id: str, tenant: str, messages: list[dict[str, Any]]) -> ChatResult:
        tools = self.registry.openai_tools()
        # S3-3: Höchstpreis dieses Calls VOR der Ausführung binden. reserve()
        # wirft BudgetExceededError, wenn der Rahmen nicht reicht — dann läuft
        # gar kein echter Call. Nach der Antwort auf die Ist-Kosten abrechnen
        # (settle) bzw. bei Fehler freigeben (release).
        reservation: str | None = None
        if self.ledger is not None:
            ceiling = self.ledger.prices.llm_cost(estimate_prompt_tokens(messages), self.max_tokens)
            reservation = self.ledger.reserve(tenant, run_id, ceiling)
        try:
            result = self.llm.chat(messages, tools=tools or None, max_tokens=self.max_tokens)
        except BaseException:
            if reservation is not None:
                self.ledger.release(reservation)
            raise
        cost = 0
        if self.ledger is not None and reservation is not None:
            cost = self.ledger.settle(reservation, tenant, run_id, result.prompt_tokens,
                                      result.completion_tokens)
        self.traces.step(run_id, "llm_call", {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cost_micro_usd": cost,
            "tool_calls": [call.name for call in result.tool_calls],
            "content_preview": result.content[:200],
        }, duration_ms=result.latency_ms)
        return result

    @staticmethod
    def _assistant_message(result: ChatResult) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": [
                {"id": call.call_id, "type": "function",
                 "function": {"name": call.name,
                              "arguments": json.dumps(call.arguments, ensure_ascii=False)}}
                for call in result.tool_calls
            ],
        }

    def _execute_tool(self, run_id: str, tenant: str, case_id: str | None,
                      call: ToolCall) -> dict[str, Any]:
        spec = self.registry.get(call.name)
        if spec is None:
            outcome = {"ok": False, "error": f"unknown tool: {call.name}"}
            self.traces.step(run_id, "tool_call", {"tool": call.name, "args": call.arguments,
                                                   "outcome": outcome})
            return outcome

        problems = validate_args(spec.parameters, call.arguments)
        if problems:
            outcome = {"ok": False, "error": "invalid arguments", "problems": problems}
            self.traces.step(run_id, "tool_call", {"tool": call.name, "args": call.arguments,
                                                   "outcome": outcome})
            return outcome

        gate: dict[str, Any] = {"mode": "allow", "allowed": True}
        if self.kernel is not None:
            # Plattform-Kontext NACH den Tool-Argumenten setzen: LLM-gelieferte
            # Argumente wie {"action_type": "harmlos"} dürfen den Gatekeeper-
            # Kontext niemals überschreiben (Gate-Bypass, Finding F4).
            action = {k: v for k, v in call.arguments.items() if isinstance(v, str)}
            action["action_type"] = call.name
            action["case_id"] = case_id
            decision = self.kernel.check_action(action)
            gate = decision.to_dict()
            if not decision.allowed:
                outcome = {"ok": False, "error": "blocked by memory gatekeeper",
                           "findings": gate["findings"],
                           "alternative": gate.get("suggested_alternative")}
                self.traces.step(run_id, "tool_call", {"tool": call.name, "args": call.arguments,
                                                       "gate": gate, "outcome": outcome})
                return outcome

        if self.ledger is not None:
            self.ledger.charge_tool(tenant, run_id, call.name)

        sandbox_report, outcome = self._invoke(spec, call.arguments)
        self.traces.step(
            run_id, "tool_call",
            {"tool": call.name, "args": call.arguments, "gate": gate,
             "sandbox": sandbox_report, "outcome": outcome},
            duration_ms=sandbox_report.get("duration_ms", 0.0),
        )
        return outcome

    def _invoke(self, spec: ToolSpec, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Sandboxed im Kindprozess, in der WASM-Sandbox (Path A) oder —
        für Plattform-Tools (Memory) — trusted inline."""
        if spec.engine == "wasm":
            try:
                wasm_sandbox = self._get_wasm_sandbox()
            except WasmUnavailableError as exc:
                return ({"exit_reason": "compile_error", "engine": "wasm"},
                        {"ok": False, "error": str(exc)})
            result = wasm_sandbox.run(spec.wasm_source, arguments, spec.parameters,
                                      spec.wasm_policy)
            report = result.to_dict()
            outcome = ({"ok": True, "value": result.value} if result.ok
                       else {"ok": False, "error": result.error})
            return report, outcome
        if spec.sandboxed:
            result = self.sandbox.run(spec.handler, arguments, spec.policy)
            report = result.to_dict()
            outcome = ({"ok": True, "value": result.value} if result.ok
                       else {"ok": False, "error": result.error})
            return report, outcome
        try:
            value = json.loads(json.dumps(spec.handler(**arguments), ensure_ascii=False,
                                          default=str))
            return {"exit_reason": "ok", "inline": True}, {"ok": True, "value": value}
        except Exception as exc:  # noqa: BLE001 - Tool-Grenze, dem Modell melden
            return ({"exit_reason": "error", "inline": True},
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _memorize(self, goal: str, case_id: str | None, run_id: str, status: str) -> None:
        if self.kernel is None:
            return
        event_type = "successful_attempt" if status == "ok" else "failed_attempt"
        self.kernel.record(
            event_type, f"agent run {status}: {goal[:120]}",
            case_id=case_id, source="agent_layer", payload={"run_id": run_id},
        )

    def _run_cost(self, run_id: str) -> float:
        if self.ledger is None:
            return 0.0
        return self.ledger.run_cost(run_id)["total_usd"]
