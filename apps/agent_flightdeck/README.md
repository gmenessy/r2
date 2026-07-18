# Agent Flightdeck 🛫

**Demo-App + One More Thing des [Agent Execution Layer](../agent_layer/):
der Plattform beim Denken zusehen — offline, deterministisch, ein Klick.**

Zwei Dinge in einem Service:

1. **Eine echte Demo-App**: ein Spesen-Agent. Die komplette Fachlichkeit —
   vier Domänen-Tools mit Schema und Sandbox-Policy — sind ~60 Zeilen in
   [`flightdeck.py`](flightdeck.py). Das ist das Versprechen der Plattform:
   Tools registrieren, `run()` aufrufen, fertig.
2. **Ein Live-Cockpit**: Vier Ein-Klick-Szenarien führen je eine Säule der
   Plattform an ihre Grenze; die UI rendert Trace-Timeline, Gatekeeper-/
   Sandbox-Urteile, xAI-Narrativ und den Kosten-Ticker in Echtzeit.

| Szenario | Was es zeigt |
|---|---|
| ✅ **Happy Path** | ReAct-Loop: `parse_expense` → `policy_check`, beide sandboxed, Kosten pro Schritt |
| 🛑 **Gatekeeper** | Governance-Karte verbietet `pay_out` — der Aufruf wird **vor** der Ausführung geblockt: Memory als Kontrollsystem, nicht als Kontext |
| ⏱️ **Sandbox** | `slow_scan` hängt absichtlich; nach 1 s Wall-Timeout wird der Kindprozess terminiert, Run und Plattform bleiben intakt |
| 💸 **Budget** | Tenant `sparfuchs` hat 0 USD — schon der erste LLM-Schritt endet deterministisch mit `budget_exceeded` |

## Testbar ohne GPU: der SimulatedLLM

Der Kniff, der die Plattform erlebbar macht: standardmäßig läuft der
[`SimulatedLLM`](../agent_layer/simllm.py) — ein deterministischer,
regelbasierter Ersatz für das echte Modell. Sandbox, Gatekeeper, Billing und
xAI verhalten sich **exakt wie im Produktivbetrieb**; nur die
Modell-Entscheidung ist geskriptet, gesteuert über Regieanweisungen im Ziel:

```
Prüfe die Abrechnung. [tool:parse_expense {"text": "Taxi 62,50 EUR"}]
Rechne (17+25)*3.                  ← Heuristik: Ausdruck → calc-Tool
Sag hallo. [answer:Hallo Welt!]    ← fixierte Schlussantwort
```

Damit ist jeder Klick in der UI reproduzierbar — und dieselben
Regieanweisungen treiben die Integrationstests der Plattform in der CI.
Das echte Modell (gemini4-31B via vLLM) übernimmt mit einem Schalter:

```bash
AGENT_SIM=0 VLLM_BASE_URL=http://<vllm-host>:8000/v1 docker compose up -d agent-flightdeck
```

## Schnellstart

```bash
docker compose up -d agent-flightdeck   # kein vLLM nötig
open http://localhost:8070              # Szenario klicken, Trace lesen
```

Oder lokal: `python3 apps/agent_flightdeck/server.py --data ./data --port 8070`

## API

| Route | Beschreibung |
|---|---|
| `GET /` | Flightdeck-UI |
| `GET /api/state` | LLM-Modus, Sandbox-Status, Tenants (Budget/Verbrauch), letzte Runs |
| `GET /api/scenarios` | die vier Szenarien |
| `POST /api/scenarios/run` | `{"name": "happy_path"}` → Run-Ergebnis + `run_id` |
| `POST /api/run` | `{"goal": "…"}` — freier Run als Tenant `demo` |
| `GET /api/trace?run_id=` | vollständiger xAI-Trace |
| `GET /api/explain?run_id=` | Begründung + Kosten-Breakdown |
| `GET /api/tools` | Tools inkl. Sandbox-Limits |
