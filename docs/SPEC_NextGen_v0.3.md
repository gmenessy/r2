# BrainFump NextGen — Spezifikation v0.3

**Agentic Memory Kernel für aktenbasierte GenAI-Systeme.**

v0.3 bringt die Spezifikation auf den tatsächlichen Code-Stand: der Kernel ist
über die v0.2-Pipeline hinaus um Trust/Provenance, einen Memory Graph, eine
Wiki-Projektion, Mandantenfähigkeit, austauschbares/semantisches Matching und
einen MCP-Server gewachsen. Die Leitthese bleibt:

> Memory ist nicht nur Kontext. Memory ist ein Kontroll- und Lernsystem.

Die Basis (v0.2, Sprints 1–5) ist in [`SPEC_NextGen_v0.2.md`](SPEC_NextGen_v0.2.md)
beschrieben. Dieses Dokument ergänzt, was seither dazugekommen ist.

## 1. Modulübersicht (Ist-Stand)

| Modul | Rolle |
|---|---|
| `events.py` | Append-only Event Log (SQLite-Trigger verbieten UPDATE/DELETE) |
| `memory_cards.py` | Typisierte, case-bounded, versionierte Memory Cards + invertierter Token-Index |
| `extractor.py` | Deterministische Extraktion Event→Card (stempelt Provenienz-Trust) |
| `evolution.py` | Patch-basierte Versionierung, Validity Resolver, Konflikt-Erkennung |
| `rules.py` | Korrekturen→Regeln (TRACE), Runtime Checks, **persistenter `RuleStore`** |
| `gatekeeper.py` | Pre-Action Gate: allow/warn/require_review/suggest_alternative/block |
| `retrieval.py` | fRAG-Ranking, austauschbare `Similarity` (lexikalisch/Embedding) |
| `consolidation.py` | Offline: Dedupe, trust-gewichtete Widersprüche, Archivierung |
| `trust.py` | **Trust & Provenance**: `TrustPolicy` |
| `graph.py` | **Memory Graph** (A-MEM/Zettelkasten): typisierte Kanten |
| `wiki.py` | **Wiki-Projektion**: menschenlesbare Markdown-Seiten pro Akte |
| `matching.py` | **Action-Intent-Matching**: Ontologie + Embedding-Adapter |
| `tenancy.py` | **Mandantenfähigkeit**: `TenantManager` |
| `embeddings.py` | `HashingEmbedder`, persistenter `SqliteVectorCache` |
| `webkit.py` | Stdlib-Web-Baukasten (Routing, Validierung, `/api/health`) |
| `kernel.py` | Fassade, verdrahtet die gesamte Pipeline |

## 2. Neu gegenüber v0.2

### 2.1 Trust & Provenance (`trust.py`)
Jede Quelle (`event.source`) hat ein Vertrauensmaß in `[0,1]`. Daraus abgeleitet:
wer **globale DNA** setzen darf, wessen **Korrekturen zu erzwungenen Regeln**
werden, und welche **Fix-Alternativen** der Gatekeeper überhaupt vorschlägt.
Der Quellen-Trust wird auf jede Memory Card gestempelt und steuert die
**trust-gewichtete Widerspruchsauflösung** (die vertrauenswürdigere Aussage
überlebt; nur bei Gleichstand fallen beide). Default-Policy ist permissiv →
unverändertes Verhalten ohne Konfiguration.

### 2.2 Memory Graph (`graph.py`)
Beziehungen zwischen Karten sind jetzt explizit, typisiert und persistent:
`supersedes`, `exception_to`, `contradicts` (mit winner/loser), `depends_on`,
`relates_to`, `evidence`. Evolution und Consolidation schreiben Kanten
automatisch; `kernel.link/related/explain` machen sie abfragbar (`explain`
liefert ein menschenlesbares „Warum"). Die Consolidation bucketet zusätzlich
nach `(case, memory_type)` und reduziert damit die O(n²)-Vergleiche.

### 2.3 Persistente Rules Engine (`rules.py::RuleStore`)
Regeln werden nicht mehr bei jedem Start neu abgeleitet, sondern versioniert
gespeichert (active/revoked/superseded). Ein idempotenter Backfill migriert
Bestandsdaten. `kernel.revoke_rule()` nimmt eine Regel dauerhaft zurück, ohne
das append-only Event Log anzutasten.

### 2.4 Semantisches Action-Matching (`matching.py`)
Governance-Karten drücken **Absicht** aus statt exakter Namen:
`forbidden_intents: [{"verb":"destroy","resource":"prod"}]`. Der `IntentMatcher`
(Ontologie, offline) fängt Synonyme (`eliminate_prod_records`) über geteilte
Verb-/Ressourcenklassen; der `EmbeddingIntentMatcher` erfüllt dieselbe
`ActionMatcher`-Schnittstelle über ein echtes Embedding-Modell. Fehler-Signaturen
werden zudem normalisiert verglichen (gegen triviale Mutationen).

### 2.5 Wiki-Projektion (`wiki.py`)
Read-only Markdown-Seite pro Akte: aktives Wissen nach Typ, das Beziehungsnetz
aus dem Graph, der revisionssichere Verlauf. Abgeleitet, „nicht die alleinige
Wahrheit".

### 2.6 Mandantenfähigkeit (`tenancy.py`)
`TenantManager` gibt jedem Mandanten einen vollständig isolierten Kernel
(eigene DB-Dateien). `kernel_factory`-Naht für ein alternatives DB-Backend.

### 2.7 MCP-Server (`apps/coding_agent_guard/mcp_server.py`)
Protokoll-korrekter MCP-Stdio-Server (JSON-RPC 2.0) — Coding Agents docken
nativ an, statt über HTTP: Tools `report_event`, `pre_edit_gate`,
`project_summary`, `guard_stats`.

## 3. Anwendungen

| App | Port | Fokus |
|---|---|---|
| Agentische Akte | 8010 | Aktenführung mit Gedächtnis-Gate + `/api/wiki` |
| Coding-Agent-Guard | 8020 | PROJECTMEM-Wächter (HTTP **und** MCP) |
| Prompt-Optimizer | 8030 | Prompt-Testlabor mit Langzeitgedächtnis |
| Memory House | 8040 | „Das Haus, das sich erinnert" 🏠 |
| **Memory Cockpit** | 8050 | 🧭 Cognitive Core sichtbar: Graph + Provenienz + Live-Gatekeeper |

Dazu drei Grenz-Demos (`demos/`): Chronos (Zeitreise), Tribunal
(Widerspruchsauflösung), Red Team (Adversarial Poisoning, 7/7 abgewehrt).

## 4. Strategische Architekturentscheidung (bestätigt)
BrainFump ist bewusst **kein „Vector DB = Memory"**. Es ist ein Kontroll- und
Beziehungssystem: SQLite als Wahrheit/Events/Versionen, Graph für
Beziehungen/Konflikte/Abhängigkeiten, Rules Engine als ausführbare Erinnerung,
Trust für Provenienz, Wiki als menschenlesbare Projektion, Evaluation als
Qualitätskontrolle. Der semantische Zugriff (Embedding) ist ein **austauschbarer
Slot**, nicht das Fundament.

## 5. Qualität
- **224 Tests**, Branch-Coverage-Gate `fail_under=85` (aktuell ~94,6 %).
- CI: pyflakes + `pytest --cov` über Python 3.10/3.11/3.12.
- Alle Stores thread-safe (RLock), append-only Event Log per DB-Trigger.

## 6. Bewusst offen (braucht ein externes Artefakt)
- **ANN-/Vektorindex** für den Embedding-Retrieval-Pfad (echtes Modell +
  Index-Struktur). Der `Similarity`-Slot ist vorbereitet.
- **Echter Postgres-Adapter** (laufender DB-Server). Die `kernel_factory`-Naht
  ist vorbereitet.
- **Konkreter Embedding-Provider** hinter `EmbeddingSimilarity`/
  `EmbeddingIntentMatcher` (API/Modell).

Diese drei sind ehrlich als Naht dokumentiert und **nicht** durch Stubs
vorgetäuscht.
