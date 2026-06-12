# Coding-Agent-Guard

App Nr. 2 der BrainFump-Suite: PROJECTMEM in echt — ein headless
Wächter-Service, den Coding Agents vor jedem Edit befragen.

## Was es kann

- **Ereignisse aufnehmen**: Architekturentscheidungen, gescheiterte Fixes
  (mit Alternative), fragile Dateien, Korrekturen — pro Repository
  isoliert (case-bounded memory).
- **Pre-Edit Gate**: blockt die Wiederholung gescheiterter Fixes,
  erzwingt Review bei fragilen Dateien, prüft kompilierte Regeln
  (z. B. „keine React-Abhängigkeit") vor Task-Abschluss.
- **Kontext-Summary**: deterministisches Markdown-Projektgedächtnis zur
  Injektion in den Agenten-Kontext (`GET /api/summary?repo=`).

## Start

```bash
docker compose up coding-agent-guard   # → http://localhost:8020
# oder lokal:
python3 apps/coding_agent_guard/server.py --port 8020 --data ./data
```

## API

| Route | Zweck |
|---|---|
| `POST /api/report` | `{repo, report_type, content, payload}` — report_type: `decision`, `correction`, `failed_attempt`, `successful_attempt`, `risk_marker`, `fragile_file`, … |
| `POST /api/gate` | `{repo, action_type, files?, error_signature?, context?}` → Gate-Entscheidung |
| `GET /api/summary?repo=` | Markdown-Projektgedächtnis |
| `GET /api/stats?repo=` | Events, Cards, Gate-Checks, Interventionen |

## Claude-Code-Integration

`hooks/pre_edit_gate.py` ist ein fertiger PreToolUse-Hook: Er fragt vor
jedem `Edit`/`Write` das Gate und blockiert bei `block`/`require_review`
(Exit-Code 2, Begründung landet als Feedback beim Agenten):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": "python3 /pfad/zu/pre_edit_gate.py"}]
      }
    ]
  }
}
```

Beispiel-Workflow:

```bash
# Agent meldet einen gescheiterten Fix
curl -X POST localhost:8020/api/report -d '{
  "repo": "mein-projekt", "report_type": "failed_attempt",
  "content": "Retry-Loop hat Gateway-Timeout nicht behoben.",
  "payload": {"error_signature": "TimeoutError: gateway",
              "alternative": "Circuit Breaker einbauen."}}'

# Später: gleicher Fix wird versucht → Gate schlägt Alternative vor
curl -X POST localhost:8020/api/gate -d '{
  "repo": "mein-projekt", "error_signature": "TimeoutError: gateway"}'
# → {"mode": "suggest_alternative", "suggested_alternative": "Circuit Breaker einbauen.", ...}
```
