# BrainFump NextGen

**Agentic Memory Kernel für aktenbasierte GenAI-Systeme.**

> Memory ist nicht nur Kontext. Memory ist ein Kontroll- und Lernsystem.

BrainFump speichert Erinnerungen nicht nur — es versioniert, verdichtet,
bewertet sie, übersetzt sie in ausführbare Regeln und kann Agenten-Aktionen
warnen oder blockieren, bevor sie ausgeführt werden.

Vollständige Spezifikation: [docs/SPEC_NextGen_v0.2.md](docs/SPEC_NextGen_v0.2.md)

## Schnellstart

Keine Abhängigkeiten außer Python ≥ 3.10 (Stdlib + SQLite).

```python
from brainfump import BrainFumpKernel
from brainfump.gatekeeper import GateMode

kernel = BrainFumpKernel("./brainfump_data")  # oder None für in-memory

# 1. Nutzerkorrektur loggen — wird automatisch zu Memory Card UND Runtime-Regel
kernel.record(
    "correction",
    "Bitte keine React-Abhängigkeit im MVP.",
    case_id="akte_42",
    payload={"forbid_dependencies": ["react"], "project_type": "mvp_frontend"},
)

# 2. Gescheiterten Fix loggen
kernel.record(
    "failed_attempt",
    "Retry-Loop hat das Gateway-Timeout nicht behoben.",
    case_id="akte_42",
    payload={"error_signature": "TimeoutError: gateway"},
)

# 3. Memory Gatekeeper: Pre-Action Check
decision = kernel.check_action({
    "action_type": "apply_fix",
    "case_id": "akte_42",
    "error_signature": "TimeoutError: gateway",
})
assert decision.mode == GateMode.BLOCK  # alter Fehler wird nicht wiederholt

# 4. fRAG Retrieval (case-bounded)
for hit in kernel.search("Welches Frontend Framework?", case_id="akte_42"):
    print(hit.score, hit.card.statement)
```

## Gatekeeper-API

```bash
python3 -m brainfump.api --data ./brainfump_data --port 8080
```

```bash
curl -X POST http://127.0.0.1:8080/api/gatekeeper/check \
  -d '{"action": {"case_id": "akte_42", "error_signature": "TimeoutError: gateway"}}'
# → {"mode": "block", "allowed": false, "findings": [...], ...}
```

## Module

| Modul | Aufgabe |
|---|---|
| `brainfump/events.py` | Append-only Event Log (UPDATE/DELETE per Trigger verboten) |
| `brainfump/memory_cards.py` | Typisierte, case-bounded, versionierbare Memory Cards |
| `brainfump/extractor.py` | Deterministische Extraktion Event → Card |
| `brainfump/evolution.py` | Patch-basierte Versionierung, Validity Resolver, Konflikt-Erkennung |
| `brainfump/rules.py` | Korrekturen → ausführbare Regeln (TRACE) + Runtime Checks |
| `brainfump/gatekeeper.py` | Pre-Action Gate: allow / warn / require_review / suggest_alternative / block |
| `brainfump/retrieval.py` | fRAG-Ranking: semantic + case + recency + confidence + trust + risk + governance |
| `brainfump/consolidation.py` | Offline: Dedupe, Widersprüche, Archivierung |
| `brainfump/evaluation.py` | Memory-Metriken + Golden-Scenario-Harness |
| `brainfump/kernel.py` | Fassade, verdrahtet die Pipeline |
| `brainfump/api.py` | `/api/gatekeeper/check` und `/api/memory/search` (Stdlib-HTTP) |

## Anwendungen (apps/)

Auf dem Kernel aufbauende Subprojekte, ausgeliefert über Docker Compose:

| App | Port | Status | Beschreibung |
|---|---|---|---|
| [`apps/prompt_optimizer`](apps/prompt_optimizer/) | 8030 | ✅ | Prompt-Testlabor mit Langzeitgedächtnis: blockt gescheiterte Varianten, kompiliert Stil-Korrekturen zu Output-Checks |
| `apps/agentic_akte` | 8010 | geplant | Aktenbasierter Agent mit Gedächtnis-Gate |
| `apps/coding_agent_guard` | 8020 | geplant | MCP-/HTTP-Wächter für Coding Agents (PROJECTMEM) |
| `apps/memory_house` | 8040 | geplant | Das Haus, das sich erinnert 🏠 |

```bash
docker compose up -d prompt-optimizer
# → http://localhost:8030
```

## Tests

```bash
pip install pytest
python3 -m pytest tests/
```
