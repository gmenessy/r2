# BrainFump NextGen

**Agentic Memory Kernel für aktenbasierte GenAI-Systeme.**

> Memory ist nicht nur Kontext. Memory ist ein Kontroll- und Lernsystem.

BrainFump speichert Erinnerungen nicht nur — es versioniert, verdichtet,
bewertet sie, übersetzt sie in ausführbare Regeln und kann Agenten-Aktionen
warnen oder blockieren, bevor sie ausgeführt werden.

Vollständige Spezifikation: [docs/SPEC_NextGen_v0.3.md](docs/SPEC_NextGen_v0.3.md)
(aktueller Stand; die ursprüngliche [v0.2](docs/SPEC_NextGen_v0.2.md) bleibt als Historie)

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
| `brainfump/retrieval.py` | fRAG-Ranking: semantic + case + recency + confidence + trust + risk + governance; austauschbare `Similarity` (lexikalisch oder Embedding) |
| `brainfump/consolidation.py` | Offline: Dedupe, trust-gewichtete Widersprüche, Archivierung |
| `brainfump/trust.py` | Trust & Provenance: `TrustPolicy` — Vertrauen je Quelle, Autorisierung auf globale DNA/Regeln |
| `brainfump/graph.py` | Memory Graph (A-MEM/Zettelkasten): typisierte Kanten (supersedes/contradicts/depends_on …), `kernel.link/related/explain` |
| `brainfump/wiki.py` | Wiki-Projektion: menschenlesbare Markdown-Seiten pro Akte (`kernel.wiki_page`) |
| `brainfump/rules.py` | Korrekturen → Regeln (TRACE) + Runtime Checks + persistenter `RuleStore` (versioniert, revoke) |
| `brainfump/evaluation.py` | Memory-Metriken + Golden-Scenario-Harness |
| `brainfump/kernel.py` | Fassade, verdrahtet die Pipeline (`BrainFumpKernel(similarity=…, trust=…)`) |
| `brainfump/webkit.py` | Gemeinsamer Web-Baukasten: Routing, Validierung, `/api/health` |
| `brainfump/api.py` | `/api/gatekeeper/check` und `/api/memory/search` (Stdlib-HTTP) |

### Austauschbarer Embedding-Slot (fRAG)

Default ist abhängigkeitsfreies lexikalisches Matching. Ein Embedding-Provider
lässt sich injizieren, ohne dass sich das Ranking-Schema (`Weights`) ändert:

```python
from brainfump import BrainFumpKernel, EmbeddingSimilarity

kernel = BrainFumpKernel(similarity=EmbeddingSimilarity(embed=my_embedding_fn))
```

### Trust & Provenance

Ohne `TrustPolicy` ist der Kernel permissiv (jede Quelle voll vertrauenswürdig
— unverändertes Verhalten). Mit einer Policy wird Vertrauen ein First-Class-Feld:
Quellen unterhalb der Schwelle dürfen keine globale DNA setzen, ihre Korrekturen
werden nicht zu erzwungenen Regeln, ihre Widersprüche löschen kein höher
vertrautes Wissen, und der Gatekeeper hält ihre Fix-Alternativen zurück.

```python
from brainfump import BrainFumpKernel, TrustPolicy

policy = TrustPolicy(default=0.5).grant("ops", 1.0).grant("externer_bot", 0.1)
kernel = BrainFumpKernel(trust=policy)
```

Siehe [`demos/red_team.py`](demos/red_team.py) — dieselben sieben Poisoning-Angriffe,
mit Trust-Layer + robustem Matching von 1/7 auf **7/7** abgewehrt.

### Governance-Intent-Matching

Governance-Karten können statt exakter action-Namen eine **Absicht** verbieten.
Der Gatekeeper matcht über einen austauschbaren `ActionMatcher`:

- `IntentMatcher` (Default) — geteilte Verb-/Ressourcen-Ontologie, abhängigkeitsfrei;
  fängt `eliminate_prod_records` über die `destroy`-Synonymklasse.
- `EmbeddingIntentMatcher(embed)` — derselbe Vertrag über ein echtes
  Embedding-Modell (Cosinus gegen Referenz-Beispiele). Mit dem lexikalischen
  `HashingEmbedder` erkennt es keine Synonyme — dafür ist die Ontologie da.

```python
from brainfump import MemoryGatekeeper, EmbeddingIntentMatcher
gate = MemoryGatekeeper(store, action_matcher=EmbeddingIntentMatcher(embed=my_model))
# Governance-Karte: payload={"forbidden_intents": [{"verb": "destroy", "resource": "prod"}]}
```

## Demos

Drei ausführbare Grenz-Szenarien in [`demos/`](demos/): `chronos.py`
(Zeitreise/Evolution), `tribunal.py` (Widerspruchsauflösung), `red_team.py`
(Adversarial Poisoning). Siehe [demos/README.md](demos/README.md).

## Anwendungen (apps/)

Auf dem Kernel aufbauende Subprojekte, ausgeliefert über Docker Compose:

| App | Port | Beschreibung |
|---|---|---|
| [`apps/agentic_akte`](apps/agentic_akte/) | 8010 | Aktenführung mit Gedächtnis-Gate: fragile Dokumente, Fristen, verworfene Analysen, globale DNA, UI-Warnkarte |
| [`apps/coding_agent_guard`](apps/coding_agent_guard/) | 8020 | PROJECTMEM-Wächter für Coding Agents: Pre-Edit Gate, Kontext-Summary, fertiger Claude-Code-Hook |
| [`apps/prompt_optimizer`](apps/prompt_optimizer/) | 8030 | Prompt-Testlabor mit Langzeitgedächtnis: blockt gescheiterte Varianten, kompiliert Stil-Korrekturen zu Output-Checks |
| [`apps/memory_house`](apps/memory_house/) | 8040 | Das Haus, das sich erinnert 🏠 — Räume als Akten, Hausregeln als Runtime-Checks |
| [`apps/memory_cockpit`](apps/memory_cockpit/) | 8050 | 🧭 **One More Thing**: Cognitive Core sichtbar gemacht — interaktiver Wissensgraph, Provenienz, Live-Gatekeeper |
| [`apps/agent_layer`](apps/agent_layer/) | 8060 | ⚙️ **Agent Execution Layer**: ReAct-Runtime mit Prozess-Sandbox fürs Tool Calling, Billing (API-Keys, Token-Ledger, Budgets), xAI-Traces; LLM via vLLM (OpenAI-kompatibel, z. B. gemini4-31B) |
| [`apps/agent_flightdeck`](apps/agent_flightdeck/) | 8070 | 🛫 **Agent Flightdeck**: Demo-App (Spesen-Agent) + Live-Cockpit des Agent Layer — vier Ein-Klick-Szenarien (Sandbox-Timeout, Gatekeeper-Block, Budget-Stopp), Trace-Timeline, xAI-Narrativ; läuft offline via SimulatedLLM |

```bash
docker compose up -d          # alle sieben Apps
docker compose up -d memory-cockpit   # oder einzeln
```

Jeder Service bietet `GET /api/health` (für den Docker-`HEALTHCHECK`) und
`GET /api/version`.

## Tests & CI

```bash
pip install -e ".[dev]"
python3 -m pytest --cov      # 286 Tests, Branch-Coverage-Gate (fail_under=85; aktuell ~95,6 %)
python3 -m pyflakes brainfump apps
```

CI (`.github/workflows/ci.yml`) führt pyflakes + `pytest --cov` über
Python 3.10/3.11/3.12 aus.
