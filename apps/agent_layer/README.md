# Agent Execution Layer ⚙️

**Ultraleichtgewichtige Ausführungsschicht für LLM-Agenten — Sandbox für Tool
Calling, Memory, Billing und xAI in einem CPU-Container.**

Die Plattform ist API-first: Apps entstehen, indem man Tools registriert und
`POST /api/run` aufruft. Das LLM (z. B. **gemini4-31B hinter vLLM**) läuft als
separater Dienst; die Plattform selbst braucht nur Python-Stdlib + SQLite und
läuft auf einer kleinen CPU-Instanz.

**Ist-Footprint (2026-07-18):** ~1.945 LOC Kern, 0 externe Runtime-Abhängigkeiten,
~159 ms Kaltstart, ~23 MiB RSS im Leerlauf. Diese Leichtgewichtigkeit ist ein
verbindliches, gemessenes Ziel — siehe [`docs/PLATFORM_CHARTER.md`](../../docs/PLATFORM_CHARTER.md)
(Zielsetzung + Budgets) und [`docs/SPRINT_PLAN_4_5.md`](../../docs/SPRINT_PLAN_4_5.md)
(Roadmap: Async/Streaming, Tenant-Sharding).

## Architektur

```
Client ──POST /api/run──▶ AgentRuntime (ReAct-Loop)
                             │
        ┌────────────────────┼───────────────────────┐
        ▼                    ▼                       ▼
   BrainFumpKernel      ToolRegistry            VLLMClient
   (Memory Layer:       │  Schema-Validierung   (OpenAI-kompatibel,
    fRAG-Retrieval,     ▼                        gemini4-31b @ vLLM)
    Gatekeeper,      ProcessSandbox
    Event Log)       (fork + rlimits, Egress-Sperre)
        │                    │                       │
        └──────────▶  TraceStore (xAI)  ◀────────────┘
                     BillingLedger (Budget-Gate)
```

Jeder Run durchläuft vier harte Querschnitte:

1. **Memory** — fRAG-Retrieval aus dem BrainFump-Kernel formt den
   Systemkontext; Ergebnisse fließen als `successful_attempt` /
   `failed_attempt`-Events zurück (der Agent lernt über Runs hinweg).
2. **Gatekeeper** — jeder Tool-Aufruf passiert das Pre-Action Gate des
   Kernels; Geblocktes wird nie ausgeführt, sondern dem Modell begründet
   zurückgemeldet.
3. **Sandbox** — untrusted Tool-Code läuft in einem geforkten Kindprozess mit
   `RLIMIT_CPU/AS/FSIZE/NOFILE`, Wall-Clock-Timeout, frischem Tempdir,
   geleerter Umgebung (keine Secrets-Vererbung) und Egress-Sperre auf
   Socket-Ebene. Ergebnisse sind JSON-only und größenbegrenzt.
4. **Billing + xAI** — jeder LLM-/Tool-Schritt wird in Mikro-USD verbucht
   (Budget-Überschreitung stoppt den Run deterministisch) und vollständig
   getract; `GET /api/explain` liefert die Kausalkette der Antwort.

## Paper-Basis

| Baustein | Grundlage |
|---|---|
| ReAct-Loop (Reason → Act → Observe) | Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models* (ICLR 2023) |
| Tool Calling mit Schema-Deklaration | Schick et al., *Toolformer* (NeurIPS 2023); OpenAI-Function-Calling-Format (vLLM-kompatibel) |
| Sandboxing untrusted Tool-Ausführung | Ruan et al., *Identifying the Risks of LM Agents with an LM-Emulated Sandbox* (ToolEmu, ICLR 2024); Lu et al., *ToolSandbox* (2024) |
| Memory als Kontroll- und Lernsystem | Packer et al., *MemGPT* (2023); Xu et al., *A-MEM: Agentic Memory* (2025) — umgesetzt im BrainFump-Kernel (fRAG, Gatekeeper, Evolution) |
| Lernen aus Fehlversuchen | Shinn et al., *Reflexion* (NeurIPS 2023) — `failed_attempt`-Events blocken Wiederholungen über den Gatekeeper |
| Ressourcen-/Mandanten-Verwaltung als "Agent-OS" | Mei et al., *AIOS: LLM Agent Operating System* (2024) — hier radikal verschlankt auf SQLite + rlimits |
| Beobachtbarkeit / xAI | Dong et al., *AgentOps: Enabling Observability of LLM Agents* (2024) — Trace jeder Kausalkette statt Post-hoc-Rationalisierung |

## Schnellstart

**Ohne GPU ausprobieren** — `AGENT_SIM=1` ersetzt das Modell durch den
deterministischen [`SimulatedLLM`](simllm.py) (Sandbox, Gatekeeper, Billing
und xAI laufen unverändert; die Demo-App [Agent Flightdeck](../agent_flightdeck/)
macht das klickbar):

```bash
AGENT_SIM=1 python3 apps/agent_layer/server.py --data ./data
```

**Mit echtem Modell:**

```bash
docker compose up -d agent-layer          # Plattform (CPU)
# vLLM separat, z. B.:
# vllm serve gemini4-31b --port 8000      # GPU-Instanz, oder CPU-Backend
export VLLM_BASE_URL=http://<vllm-host>:8000/v1
```

```bash
# 1. API-Key ausstellen (Admin)
curl -s -X POST localhost:8060/api/keys \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{"tenant": "acme", "budget_usd": 5.0}'
# → {"api_key": "agl_…", …}

# 2. Agent-Run
curl -s -X POST localhost:8060/api/run \
  -H "X-API-Key: agl_…" \
  -d '{"goal": "Rechne (17+25)*3 und merke dir das Ergebnis.", "case_id": "akte_1"}'
# → {"run_id": "run_…", "status": "ok", "answer": "…", "cost_usd": 0.0003, …}

# 3. xAI: Warum diese Antwort?
curl -s "localhost:8060/api/explain?run_id=run_…"
# → memories_used, tool_calls (inkl. Gatekeeper-/Sandbox-Urteil), Token-/Kosten-Split

# 4. Billing
curl -s localhost:8060/api/usage -H "X-API-Key: agl_…"
```

## Apps auf der Plattform bauen

Eine App = eine Tool-Menge + ein Runtime-Objekt. Kein Framework-Overhead:

```python
from brainfump import BrainFumpKernel
from apps.agent_layer import (
    AgentRuntime, BillingLedger, TraceStore, VLLMClient, builtin_registry,
)
from apps.agent_layer.sandbox import SandboxPolicy

kernel = BrainFumpKernel("./data/memory")
registry = builtin_registry(kernel)

@registry.tool(
    "read_invoice",
    "Parse an invoice text and extract totals.",
    {"type": "object",
     "properties": {"text": {"type": "string"}},
     "required": ["text"]},
    policy=SandboxPolicy(wall_timeout_s=5.0, memory_bytes=128 * 1024 * 1024),
)
def read_invoice(text: str) -> dict:
    ...  # läuft automatisch sandboxed

runtime = AgentRuntime(
    llm=VLLMClient(),                 # VLLM_BASE_URL / AGENT_MODEL aus der Umgebung
    registry=registry,
    traces=TraceStore("./data/traces.db"),
    kernel=kernel,
    ledger=BillingLedger("./data/billing.db"),
)
result = runtime.run("Prüfe die Rechnung …", tenant="acme", case_id="akte_1")
```

## API

| Route | Auth | Beschreibung |
|---|---|---|
| `POST /api/keys` | `X-Admin-Token` | API-Key für einen Tenant ausstellen (`budget_usd`, optional `ttl_seconds`) |
| `POST /api/keys/rotate` | `X-API-Key` | Eigenen Key rotieren; alter gilt im Kulanzfenster weiter (`grace_seconds`) |
| `POST /api/run` | `X-API-Key` | Agent-Run: `{goal, case_id?, async?}` → Antwort+Kosten (sync) oder `202`+`run_id` (async); Rate-Limit → `429`, Überlast → `503`, je mit `Retry-After` |
| `GET /api/runs?run_id=` | `X-API-Key` / Admin | Zustand/Ergebnis eines async Runs (`queued`/`running`/`done`/`error`) |
| `GET /api/runs/stream?run_id=` | `X-API-Key` / Admin | Live-Stream der Trace-Schritte als Server-Sent Events (`step`/`done`) |
| `GET /api/trace?run_id=` | `X-API-Key` / Admin | Schritt-Trace — nur eigener Tenant oder Admin |
| `GET /api/explain?run_id=` | `X-API-Key` / Admin | Begründung + Kosten-Breakdown — nur eigener Tenant oder Admin |
| `GET /api/usage` | `X-API-Key` | Verbrauch, Budget, Rest des eigenen Tenants |
| `GET /api/tools` | — | registrierte Tools inkl. Sandbox-Limits |
| `GET /api/health`, `GET /api/version` | — | Betrieb/Docker-Healthcheck |

## Betrieb & Härtung (Sprint 3)

Für den Betrieb mit nicht vollständig vertrauenswürdigen Tenants — Details und
Verifikation in [`docs/DEPLOY_HARDENING.md`](../../docs/DEPLOY_HARDENING.md):

- **Sandbox-Privilege-Drop:** Läuft der Container als root, droppt jeder
  Tool-Aufruf auf `nobody` — Fremdcode erreicht die Plattform-Dateien nicht
  (`dropped_privileges` im Sandbox-Report). Härtung in `docker-compose.yml`.
- **Netz-Isolation:** Produktion via `docker-compose.hardened.yml`
  (`internal`-Netz, vLLM innen, kein Egress).
- **Budget-Reservierung:** Der Höchstpreis eines Calls wird vorab gebunden;
  ein Call, der das Budget sprengen würde, läuft gar nicht erst — kein Overrun.
- **Rate-Limits:** `--rate-per-minute` / `AGENT_RATE_PER_MINUTE` (Token-Bucket
  je Tenant, `429` + `Retry-After`).
- **Key-Lifecycle:** Ablauf (`ttl_seconds`) und Rotation mit Kulanzfenster.
- **Retention:** `--retention-days` / `AGENT_RETENTION_DAYS` löscht alte Traces
  beim Start (Default aus — keine stille Löschung).

## Durchsatz & Streaming (Sprint 4)

- **Asynchrone Runs:** `{"async": true}` → sofort `202` + `run_id`; ein kleiner
  `ThreadPoolExecutor` (`--async-workers`) arbeitet ab, `GET /api/runs?run_id=`
  pollt. Lange Runs binden keinen Request-Thread mehr.
- **Backpressure:** pro Tenant zu viele Runs in Flight → `429`, global
  überlastet → `503` (je mit `Retry-After`) — der kleine Prozess puffert nicht
  unbegrenzt.
- **Token-/Trace-Streaming:** `GET /api/runs/stream?run_id=` als Server-Sent
  Events (reine Stdlib) — Trace-Schritte live, sobald sie entstehen.
- **Verschachtelte Schema-Validierung:** `validate_args` prüft `object`/`array`
  rekursiv (ohne `jsonschema`), fängt strukturell falsche Tool-Argumente vor
  der Sandbox.
- **Leichtgewicht-Gate:** `scripts/measure_footprint.py` bewacht die Budgets
  der [Charter](../../docs/PLATFORM_CHARTER.md) (0 Deps, Kaltstart < 400 ms,
  RSS < 60 MiB) in der CI.

## Performance-Entscheidungen

- **Keine externen Abhängigkeiten**: Stdlib + SQLite (WAL) — Containerstart
  in Millisekunden, Image auf `python:3.12-slim`-Basis.
- **Ein Prozess, Threads + kurzlebige Forks**: Der HTTP-Server ist ein
  `ThreadingHTTPServer`; nur untrusted Tool-Code forkt (Copy-on-Write, kein
  Interpreter-Neustart, kein Container-pro-Call).
- **Alles Teure ist ausgelagert**: Die LLM-Inferenz (gemini4-31B) läuft in
  vLLM auf eigener Hardware; die Plattform macht nur Orchestrierung, I/O und
  Buchhaltung — CPU genügt.
- **Ganzzahl-Billing** in Mikro-USD: keine Float-Drift, ein `SUM()` pro
  Budget-Check über indizierte Spalten.
