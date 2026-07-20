"""Agent Ops Console (App Nr. 7) — die Plattform bei der Arbeit live verfolgen. 📡

Zweite Demo-App neben dem [Flightdeck](../agent_flightdeck/): während das
Flightdeck die *Fundamente* zeigt (Sandbox, Gatekeeper, Budget, xAI im
synchronen Modus), macht die Ops Console die **Sprint-4/5-Fähigkeiten**
sichtbar, die bisher nur per curl/Tests verifiziert waren — asynchrone Runs,
Live-Streaming, Backpressure:

1. live_triage    Async-Run + SSE: Health-Check → automatische Skalierung,
                   der Trace strömt live rein, während der Agent arbeitet
2. change_freeze   Governance verbietet restart_service während des
                   Quartalsabschlusses → Block, Agent weicht auf page_oncall aus
3. noisy_neighbor  Mehrfach schnell ausgelöst zeigt das Rate-Limit live:
                   429 mit Retry-After, sobald der Token-Bucket leer ist

Standardmäßig läuft der :class:`SimulatedLLM` (offline, deterministisch);
mit ``AGENT_SIM=0`` + ``VLLM_BASE_URL`` übernimmt das echte Modell.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from brainfump import BrainFumpKernel
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.ratelimit import RateLimiter
from apps.agent_layer.runner import AsyncRunner
from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.sandbox import SandboxPolicy
from apps.agent_layer.simllm import SimulatedLLM
from apps.agent_layer.tools import ToolRegistry, builtin_registry
from apps.agent_layer.xai import TraceStore

TENANT = "ops"

_KNOWN_SERVICES: dict[str, dict[str, Any]] = {
    "checkout-api": {"status": "degraded", "latency_ms": 420},
    "billing-service": {"status": "down", "latency_ms": 0},
    "auth-service": {"status": "healthy", "latency_ms": 35},
}
_STATES = ("healthy", "degraded", "down")

SCENARIOS: dict[str, dict[str, Any]] = {
    "live_triage": {
        "title": "🔴 Live Triage — Incident live verfolgen",
        "pillar": "Async Runs + SSE-Streaming",
        "description": "checkout-api hakt. Der Run läuft asynchron im Hintergrund "
                       "(sofort eine run_id, kein blockierender Request) — der Trace "
                       "strömt live rein, während der Agent Health-Check und "
                       "Skalierung ausführt.",
        "async": True,
        "case_id": "incident_checkout",
        "goal": ('Prüfe checkout-api und skaliere hoch, falls nötig. '
                 '[tool:check_health {"service": "checkout-api"}] '
                 '[tool:scale_up {"service": "checkout-api", "replicas": 4}]'),
    },
    "change_freeze": {
        "title": "🛑 Change Freeze — Neustart untersagt",
        "pillar": "Memory Layer als Kontrollsystem",
        "description": "billing-service hängt. Eine globale Governance-Karte verbietet "
                       "restart_service während des Quartalsabschlusses — der Aufruf "
                       "wird geblockt, der Agent weicht auf page_oncall aus.",
        "async": False,
        "case_id": "incident_billing",
        "goal": ('Der billing-service hängt seit 10 Minuten. Starte ihn neu; geht das '
                 'nicht, page den Bereitschaftsdienst. '
                 '[tool:restart_service {"service": "billing-service"}] '
                 '[tool:page_oncall {"service": "billing-service", '
                 '"message": "Neustart durch Change Freeze geblockt - manueller Eingriff"}]'),
    },
    "noisy_neighbor": {
        "title": "🐝 Noisy Neighbor — Rate-Limit live",
        "pillar": "Backpressure (Token-Bucket)",
        "description": "Mehrfach schnell hintereinander ausgelöst: sobald der "
                       "Token-Bucket des Tenants leer ist, antwortet die Plattform "
                       "mit 429 + Retry-After statt unbegrenzt zu puffern.",
        "async": False,
        "case_id": "incident_probe",
        "goal": '[tool:check_health {"service": "auth-service"}]',
    },
}


# -- Fach-Tools des Ops-Agenten (laufen sandboxed) -----------------------------

def check_health(service: str) -> dict[str, Any]:
    known = _KNOWN_SERVICES.get(service)
    if known is not None:
        return {"service": service, **known}
    digest = int(hashlib.sha256(service.encode()).hexdigest(), 16)
    return {"service": service, "status": _STATES[digest % 3], "latency_ms": 20 + digest % 400}


def scale_up(service: str, replicas: int) -> dict[str, Any]:
    return {"service": service, "replicas": replicas, "scaled": True}


def restart_service(service: str) -> dict[str, Any]:
    time.sleep(0.05)
    return {"service": service, "restarted": True}


def page_oncall(service: str, message: str) -> dict[str, Any]:
    return {"paged": True, "service": service, "message": message, "channel": "pagerduty"}


def build_registry(kernel: BrainFumpKernel) -> ToolRegistry:
    registry = builtin_registry(kernel)
    registry.tool(
        "check_health", "Check the health status of a service.",
        {"type": "object", "properties": {"service": {"type": "string"}},
         "required": ["service"]},
        policy=SandboxPolicy(wall_timeout_s=3.0, cpu_seconds=2),
    )(check_health)
    registry.tool(
        "scale_up", "Scale a service to a target replica count.",
        {"type": "object",
         "properties": {"service": {"type": "string"}, "replicas": {"type": "integer"}},
         "required": ["service", "replicas"]},
        side_effects=True,
    )(scale_up)
    registry.tool(
        "restart_service", "Restart a hanging service.",
        {"type": "object", "properties": {"service": {"type": "string"}},
         "required": ["service"]},
        side_effects=True,
    )(restart_service)
    registry.tool(
        "page_oncall", "Page the on-call responder for a service.",
        {"type": "object",
         "properties": {"service": {"type": "string"}, "message": {"type": "string"}},
         "required": ["service", "message"]},
        side_effects=True,
    )(page_oncall)
    return registry


class OpsConsole:
    """Verdrahtet die Plattform mit dem Ops-Agenten, Async-Runner und Rate-Limiter."""

    def __init__(
        self,
        kernel: BrainFumpKernel,
        ledger: BillingLedger,
        traces: TraceStore,
        llm: Any | None = None,
        rate_limiter: RateLimiter | None = None,
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
        self.async_runner = AsyncRunner(self.runtime, max_workers=2)
        # Demo-tuned: der Bucket deckt ein normales Durchklicken von
        # live_triage + change_freeze (2 Tokens), aber ein rascher Doppelklick
        # auf "noisy_neighbor" erschöpft ihn zuverlässig — kein
        # Produktions-Default (siehe Charter/Deploy-Docs).
        self.rate_limiter = rate_limiter or RateLimiter(per_minute=12, burst=3)
        self._seed()

    def _seed(self) -> None:
        """Idempotent: Governance-DNA und Demo-Tenant nur einmal anlegen."""
        already = any(
            event.payload.get("forbidden_actions") == ["restart_service"]
            for event in self.kernel.events.query(event_type="policy_violation")
        )
        if not already:
            self.kernel.record(
                "policy_violation",
                "Change Freeze bis Quartalsabschluss — keine Neustarts an Kern-Services.",
                source="compliance",
                payload={"forbidden_actions": ["restart_service"]},
            )
        if self.ledger.usage(TENANT)["budget_usd"] is None:
            self.ledger.create_key(TENANT, budget_usd=5.0)

    # -- API der App ------------------------------------------------------------

    def scenarios(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "title": s["title"], "pillar": s["pillar"],
             "description": s["description"], "async": s["async"]}
            for name, s in SCENARIOS.items()
        ]

    def run_scenario(self, name: str) -> dict[str, Any]:
        scenario = SCENARIOS.get(name)
        if scenario is None:
            raise ValueError(f"unknown scenario: {name}")

        allowed, retry_after = self.rate_limiter.acquire(TENANT)
        if not allowed:
            return {"scenario": name, "title": scenario["title"], "pillar": scenario["pillar"],
                    "status": "rate_limited", "retry_after_s": round(retry_after, 2)}

        if scenario["async"]:
            run_id = self.async_runner.submit(scenario["goal"], tenant=TENANT,
                                              case_id=scenario["case_id"])
            return {"scenario": name, "title": scenario["title"], "pillar": scenario["pillar"],
                    "run_id": run_id, "status": "queued", "async": True}

        result = self.runtime.run(scenario["goal"], tenant=TENANT, case_id=scenario["case_id"])
        return {"scenario": name, "title": scenario["title"], "pillar": scenario["pillar"],
                "async": False, **result.to_dict()}

    def run_status(self, run_id: str) -> dict[str, Any] | None:
        return self.async_runner.status(run_id)

    def state(self) -> dict[str, Any]:
        return {
            "llm": {"model": getattr(self.runtime.llm, "model", "?"),
                    "base_url": getattr(self.runtime.llm, "base_url", "?"),
                    "simulated": isinstance(self.runtime.llm, SimulatedLLM)},
            "sandbox_hardened": self.runtime.sandbox.hardened,
            "rate_limit": {"per_minute": self.rate_limiter.per_minute,
                          "burst": self.rate_limiter.burst},
            "tenant": self.ledger.usage(TENANT),
            "recent_runs": self.traces.recent(limit=15),
        }
