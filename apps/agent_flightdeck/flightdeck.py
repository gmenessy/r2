"""Agent Flightdeck (App Nr. 6) — die Plattform beim Denken zusehen. 🛫

Demo-App **und** Schaufenster des Agent Execution Layer in einem: ein
Spesen-Agent (Fach-Tools in ~60 Zeilen) plus vier Ein-Klick-Szenarien, die
je eine Säule der Plattform an ihre Grenze führen:

1. happy_path   ReAct-Loop: parse → policy-check → Antwort (Sandbox, Kosten)
2. gatekeeper   Memory als Kontrollsystem: Barauszahlung ist globale DNA-
                verboten → der Tool-Aufruf wird nie ausgeführt
3. sandbox      Ein hängendes OCR-Tool läuft in den Wall-Timeout — die
                Plattform bleibt stabil, der Trace zeigt das Urteil
4. budget       Tenant ohne Budget → deterministischer Billing-Stopp

Standardmäßig läuft der :class:`SimulatedLLM` (offline, deterministisch);
mit ``AGENT_SIM=0`` + ``VLLM_BASE_URL`` übernimmt das echte Modell.
"""

from __future__ import annotations

import re
import time
from typing import Any

from brainfump import BrainFumpKernel
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.sandbox import SandboxPolicy
from apps.agent_layer.simllm import SimulatedLLM
from apps.agent_layer.tools import ToolRegistry, builtin_registry
from apps.agent_layer.xai import TraceStore

_AMOUNT = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:EUR|€)", re.IGNORECASE)
_CATEGORIES = {"taxi": "taxi", "hotel": "hotel", "bahn": "bahn", "zug": "bahn",
               "flug": "flug", "essen": "bewirtung", "restaurant": "bewirtung"}

DEMO_TENANT = "demo"
BROKE_TENANT = "sparfuchs"

_EXPENSE_TEXT = "Taxi Flughafen 62,50 EUR, Beleg fehlt"

SCENARIOS: dict[str, dict[str, Any]] = {
    "happy_path": {
        "title": "✅ Happy Path — Spesen prüfen",
        "pillar": "ReAct + Sandbox + Billing",
        "description": "Zwei Fach-Tools laufen sandboxed durch: Beleg parsen, "
                       "Policy prüfen. Der Trace zeigt jeden Schritt samt Kosten.",
        "tenant": DEMO_TENANT,
        "case_id": "akte_spesen",
        "goal": (
            f'Prüfe diese Spesenabrechnung: "{_EXPENSE_TEXT}". '
            f'[tool:parse_expense {{"text": "{_EXPENSE_TEXT}"}}] '
            '[tool:policy_check {"amount": 62.5, "category": "taxi", "has_receipt": false}]'
        ),
    },
    "gatekeeper": {
        "title": "🛑 Gatekeeper — verbotene Auszahlung",
        "pillar": "Memory Layer als Kontrollsystem",
        "description": "Eine globale Governance-Karte verbietet pay_out. Der "
                       "Aufruf wird vor der Ausführung geblockt — Memory ist "
                       "kein Kontext, sondern ein Gate.",
        "tenant": DEMO_TENANT,
        "case_id": "akte_spesen",
        "goal": ('Zahle die 62,50 EUR bitte sofort bar aus. '
                 '[tool:pay_out {"amount": 62.5, "recipient": "M. Mustermann"}]'),
    },
    "sandbox": {
        "title": "⏱️ Sandbox — hängendes Tool",
        "pillar": "Prozess-Sandbox (rlimits + Timeout)",
        "description": "slow_scan hängt absichtlich. Nach 1 s Wall-Timeout wird "
                       "der Kindprozess terminiert; Plattform und Run bleiben "
                       "intakt, das Modell erfährt den Fehler.",
        "tenant": DEMO_TENANT,
        "case_id": "akte_spesen",
        "goal": ('Scanne den Papierbeleg ein. '
                 '[tool:slow_scan {"document": "beleg_042.pdf"}]'),
    },
    "budget": {
        "title": "💸 Budget — Tenant ohne Guthaben",
        "pillar": "Billing als hartes Gate",
        "description": "Der Tenant 'sparfuchs' hat 0 USD Budget. Schon der "
                       "erste LLM-Schritt wird verweigert, der Run endet "
                       "deterministisch mit budget_exceeded.",
        "tenant": BROKE_TENANT,
        "case_id": "akte_spesen",
        "goal": "Fasse alle offenen Spesenfälle zusammen.",
    },
}


# -- Fach-Tools des Spesen-Agenten (laufen sandboxed) --------------------------

def parse_expense(text: str) -> dict[str, Any]:
    match = _AMOUNT.search(text)
    amount = float(match.group(1).replace(",", ".")) if match else None
    lowered = text.lower()
    category = next((cat for key, cat in _CATEGORIES.items() if key in lowered), "sonstiges")
    return {
        "amount": amount,
        "category": category,
        "has_receipt": "beleg" in lowered and "fehlt" not in lowered,
    }


def policy_check(amount: float, category: str, has_receipt: bool = True) -> dict[str, Any]:
    if category == "taxi" and amount > 50 and not has_receipt:
        return {"verdict": "abgelehnt",
                "reason": "Taxi über 50 EUR erfordert einen Beleg."}
    if amount > 500:
        return {"verdict": "review",
                "reason": "Beträge über 500 EUR brauchen eine Freigabe."}
    return {"verdict": "genehmigt", "reason": "Im Rahmen der Reisekostenrichtlinie."}


def pay_out(amount: float, recipient: str) -> dict[str, Any]:
    return {"paid": amount, "recipient": recipient,
            "reference": f"TX-{int(amount * 100):08d}"}


def slow_scan(document: str) -> dict[str, Any]:
    time.sleep(30)  # hängt absichtlich — die Sandbox muss abbrechen
    return {"document": document, "text": "nie erreicht"}


def build_registry(kernel: BrainFumpKernel) -> ToolRegistry:
    registry = builtin_registry(kernel)
    registry.tool(
        "parse_expense", "Parse an expense line: amount, category, receipt flag.",
        {"type": "object", "properties": {"text": {"type": "string"}},
         "required": ["text"]},
        policy=SandboxPolicy(wall_timeout_s=3.0, cpu_seconds=2),
    )(parse_expense)
    registry.tool(
        "policy_check", "Check an expense against the travel cost policy.",
        {"type": "object",
         "properties": {"amount": {"type": "number"}, "category": {"type": "string"},
                        "has_receipt": {"type": "boolean"}},
         "required": ["amount", "category"]},
        policy=SandboxPolicy(wall_timeout_s=3.0, cpu_seconds=2),
    )(policy_check)
    registry.tool(
        "pay_out", "Pay out an approved expense to a recipient.",
        {"type": "object",
         "properties": {"amount": {"type": "number"}, "recipient": {"type": "string"}},
         "required": ["amount", "recipient"]},
        side_effects=True,
    )(pay_out)
    registry.tool(
        "slow_scan", "OCR-scan a paper receipt (intentionally hangs — sandbox demo).",
        {"type": "object", "properties": {"document": {"type": "string"}},
         "required": ["document"]},
        policy=SandboxPolicy(wall_timeout_s=1.0, cpu_seconds=2),
    )(slow_scan)
    return registry


class Flightdeck:
    """Verdrahtet die Plattform mit dem Spesen-Agenten und den Szenarien."""

    def __init__(
        self,
        kernel: BrainFumpKernel,
        ledger: BillingLedger,
        traces: TraceStore,
        llm: Any | None = None,
    ) -> None:
        self.kernel = kernel
        self.ledger = ledger
        self.traces = traces
        self.runtime = AgentRuntime(
            llm=llm or SimulatedLLM(),
            registry=build_registry(kernel),
            traces=traces,
            kernel=kernel,
            ledger=ledger,
        )
        self._seed()

    def _seed(self) -> None:
        """Idempotent: Governance-DNA und Demo-Tenants nur einmal anlegen."""
        already = any(
            event.payload.get("forbidden_actions") == ["pay_out"]
            for event in self.kernel.events.query(event_type="policy_violation")
        )
        if not already:
            self.kernel.record(
                "policy_violation",
                "Barauszahlungen sind untersagt — Vier-Augen-Prinzip der Buchhaltung.",
                source="compliance",
                payload={"forbidden_actions": ["pay_out"]},
            )
        if self.ledger.usage(DEMO_TENANT)["budget_usd"] is None:
            self.ledger.create_key(DEMO_TENANT, budget_usd=2.0)
        if self.ledger.usage(BROKE_TENANT)["budget_usd"] is None:
            self.ledger.create_key(BROKE_TENANT, budget_usd=0.0)

    # -- API der App ----------------------------------------------------------

    def scenarios(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "title": s["title"], "pillar": s["pillar"],
             "description": s["description"], "tenant": s["tenant"]}
            for name, s in SCENARIOS.items()
        ]

    def run_scenario(self, name: str) -> dict[str, Any]:
        scenario = SCENARIOS.get(name)
        if scenario is None:
            raise ValueError(f"unknown scenario: {name}")
        result = self.runtime.run(scenario["goal"], tenant=scenario["tenant"],
                                  case_id=scenario["case_id"])
        return {"scenario": name, "title": scenario["title"],
                "pillar": scenario["pillar"], **result.to_dict()}

    def run_goal(self, goal: str, case_id: str | None = None) -> dict[str, Any]:
        return self.runtime.run(goal, tenant=DEMO_TENANT,
                                case_id=case_id or "akte_spesen").to_dict()

    def state(self) -> dict[str, Any]:
        return {
            "llm": {"model": getattr(self.runtime.llm, "model", "?"),
                    "base_url": getattr(self.runtime.llm, "base_url", "?"),
                    "simulated": isinstance(self.runtime.llm, SimulatedLLM)},
            "sandbox_hardened": self.runtime.sandbox.hardened,
            "tenants": [self.ledger.usage(DEMO_TENANT), self.ledger.usage(BROKE_TENANT)],
            "recent_runs": self.traces.recent(limit=15),
        }
