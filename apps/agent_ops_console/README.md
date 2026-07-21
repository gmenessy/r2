# Agent Ops Console 📡

**Zweite Demo-App des [Agent Execution Layer](../agent_layer/) — die
Plattform bei der Arbeit live verfolgen, während sie läuft.**

Während das [Flightdeck](../agent_flightdeck/) die *Fundamente* zeigt
(Sandbox, Gatekeeper, Budget, xAI im synchronen Modus), macht die Ops
Console genau die Sprint-4/5- und Path-A-Fähigkeiten sichtbar, die bisher
nur per curl/Tests verifiziert waren: **asynchrone Runs, Live-Streaming,
Backpressure, WASM-Code-Execution.** Domäne: ein SRE-Agent für
Incident-Response.

| Szenario | Was es zeigt |
|---|---|
| 🔴 **Live Triage** | Async-Run (`202` + `run_id`, kein blockierender Request) — der Trace strömt per Server-Sent Events live rein, während der Agent `check_health` ausführt, den Schweregrad in der **WASM-Sandbox** berechnet (`severity_score`, ~80× schneller als Fork) und `scale_up` aufruft |
| 🛑 **Change Freeze** | Governance verbietet `restart_service` während des Quartalsabschlusses — geblockt **vor** der Ausführung; der Agent weicht auf `page_oncall` aus |
| 🐝 **Noisy Neighbor** | Mehrfach schnell ausgelöst: sobald der Token-Bucket leer ist, antwortet die Plattform mit `429` + `Retry-After` statt unbegrenzt zu puffern |

Die Fach-Tools (`check_health`, `scale_up`, `restart_service`, `page_oncall`,
`severity_score`) sind ~65 Zeilen in [`console.py`](console.py) — dasselbe
Versprechen wie beim Flightdeck: Tools registrieren, fertig. Der Live-Feed
zeigt für jeden Tool-Call die tatsächliche Engine (`fork` vs. `wasm`) samt
Laufzeit — die Sandbox-Wahl ist beobachtbar, nicht nur ein interner Kniff.

## Live-Feed statt Timeline

Das Cockpit rendert keine fertige Trace-Liste, sondern öffnet direkt nach dem
Absenden einen `EventSource`-Stream (`GET /api/runs/stream`) und hängt jeden
Trace-Schritt an, sobald er entsteht — für synchrone **und** asynchrone Runs
über denselben Code, denn `trace_sse_events` liefert bei einem bereits
abgeschlossenen Run einfach alle Schritte in schneller Folge gefolgt vom
`done`-Event.

## Testbar ohne GPU: der SimulatedLLM

Wie im Flightdeck läuft standardmäßig der
[`SimulatedLLM`](../agent_layer/simllm.py) — Sandbox, Gatekeeper, Billing,
Rate-Limiter und xAI verhalten sich exakt wie im Produktivbetrieb, nur die
Modell-Entscheidung ist über Regieanweisungen im Ziel geskriptet. Das echte
Modell übernimmt mit einem Schalter:

```bash
AGENT_SIM=0 VLLM_BASE_URL=http://<vllm-host>:8000/v1 docker compose up -d agent-ops-console
```

**Hinweis zum Rate-Limit:** Der Token-Bucket dieser Demo ist bewusst eng
(`per_minute=12, burst=3`), damit „Noisy Neighbor" mit wenigen Klicks
triggert — kein Produktions-Default (siehe
[`docs/DEPLOY_HARDENING.md`](../../docs/DEPLOY_HARDENING.md)).

## Schnellstart

```bash
docker compose up -d agent-ops-console   # kein vLLM nötig
open http://localhost:8080               # Szenario klicken, Live-Feed lesen
```

Oder lokal: `python3 apps/agent_ops_console/server.py --data ./data --port 8080`

## API

| Route | Beschreibung |
|---|---|
| `GET /` | Ops-Console-UI |
| `GET /api/state` | LLM-Modus, Rate-Limit-Konfig, Tenant (Budget/Verbrauch), letzte Runs |
| `GET /api/scenarios` | die drei Szenarien |
| `POST /api/scenarios/run` | `{"name": "live_triage"}` → `200` (sync-Ergebnis), `202` (queued) oder `429` (Rate-Limit, `Retry-After`) |
| `GET /api/runs?run_id=` | Zustand/Ergebnis eines async Runs |
| `GET /api/runs/stream?run_id=` | Live-Trace als Server-Sent Events (`step`/`done`) |
| `GET /api/trace?run_id=` | vollständiger xAI-Trace |
| `GET /api/explain?run_id=` | Begründung + Kosten-Breakdown |
