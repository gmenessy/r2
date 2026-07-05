# Sprint Planning Review — BrainFump NextGen

_Stand: 2026-06-19 · Basis: `main` nach v0.2-Suite (Kernel + 4 Apps, 97 Tests grün)_

---

## 1. Erkenntnisse aus der Tiefenanalyse (Phase 1)

### Architektur & Struktur
- **Sauber geschichteter Kernel**: `events → memory_cards → extractor →
  evolution/rules → gatekeeper → retrieval/consolidation/evaluation`,
  zusammengeführt über die Fassade `kernel.py`. Klare Verantwortlichkeiten,
  keine Zyklen.
- **Konsistente App-Struktur**: Jede der vier Apps folgt demselben Schnitt
  (`<logik>.py` + `server.py` + `test_*.py` + `Dockerfile` + `README.md`
  [+ `static/`]). Gute Wiedererkennbarkeit.
- **Pattern-Verstoß (behoben):** `EvolutionMemory._full_chain` griff direkt
  auf das private `MemoryCardStore._conn` zu — eine Schichtverletzung.
- **Größter Tech Debt (offen):** Fünf HTTP-Server (`brainfump/api.py` +
  4 App-Server) duplizieren ~70 % Boilerplate: Routing-Dispatch, `_send`,
  `_send_file`, JSON-Parsing, Fehler-zu-Statuscode-Mapping, `log_message`,
  `main()`/argparse. Ca. 480 Zeilen, davon ~330 redundant.

### Code-Qualität
- Durchgängige Typannotationen, `@dataclass(frozen=True)` für alle
  Wertobjekte, spec-verankerte Docstrings.
- **Tote/asymmetrische Stellen (behoben):** ein ungenutzter Import;
  `MemoryCard` ohne `from_dict` (während `Event` beides hatte).
- Validierungs- und Pflichtfeld-Checks in den Servern ad hoc und uneinheitlich
  (`_require` nur im Optimizer).

### Sicherheit & Performance
- `except Exception → 500` ist als bewusst defensiver Rand akzeptabel.
- **O(n²)** paarweise Vergleiche in `Consolidator._detect_contradictions`
  und `EvolutionMemory.detect_conflicts`; **O(n)**-Vollscan je Retrieval-Query
  (kein Index/Embedding). Bei kleinen Datenmengen unkritisch, skaliert aber
  nicht — als Epic vermerkt.
- POST-Bodies werden ungedrosselt über `Content-Length` gelesen (kein
  Größenlimit). Für interne Tools geringe Priorität.

### Testabdeckung
- **97 Tests grün**, jedes Kernel-Modul und jede App abgedeckt, inkl.
  HTTP-Roundtrips und Neustart-Persistenz. Starke Basis.
- Lücken: keine Coverage-Messung, keine CI-Pipeline, keine Last-/Property-Tests.

---

## 2. Changelog — in Phase 2 bereits erledigte Quick Wins

| # | Änderung | Datei(en) | Nutzen |
|---|----------|-----------|--------|
| 1 | Ungenutzten Import `field` entfernt | `brainfump/gatekeeper.py` | Toter Code weg, pyflakes sauber |
| 2 | Öffentliche Methode `MemoryCardStore.successor_of()` eingeführt und in `EvolutionMemory._full_chain` statt `store._conn` genutzt | `memory_cards.py`, `evolution.py` | Schichtverletzung behoben, Kapselung wiederhergestellt |
| 3 | `MemoryCard.from_dict()` als Inverse zu `to_dict()` ergänzt (Symmetrie mit `Event`) | `memory_cards.py` | Vollständige (De-)Serialisierungs-API |
| 4 | Tests für `successor_of` und Dict-Roundtrip | `tests/test_memory_cards.py` | Regression abgesichert (+2 Tests → 97) |

Verifikation: `pyflakes` clean, `pytest` 97/97 grün.

---

## 3. Backlog für den neuen Sprint

### A) Mittelfristige Aufgaben (Features & größeres Refactoring)

> **Sprint-Ergebnis: alle Tickets der Kategorie A abgeschlossen** (Details im
> Abschnitt 4). Suite von 95 → **119 Tests** (Stand M-1…M-5; inzwischen 138
> nach den Next-Horizon-Sprints), Branch-Coverage ~94 %.

| Ticket | Beschreibung | Aufwand | Status |
|--------|--------------|---------|--------|
| **M-1 · Shared Web-Toolkit** | Gemeinsames `brainfump/webkit.py`: deklaratives Routing, JSON-Helfer, Static-Serving, einheitliches Fehler-Mapping, `serve()`-Helper. Alle 5 Server darauf umstellen. | M | ✅ erledigt |
| M-2 · Einheitliche Request-Validierung | `require()`/Schema-Helfer im Web-Toolkit, in allen Servern statt ad-hoc-Checks. Baut auf M-1 auf. | S | ✅ erledigt |
| M-3 · `/api/health` + `/api/version` | Einheitliche Health-/Version-Endpunkte je Service; Docker-`HEALTHCHECK` darauf umstellen. | S | ✅ erledigt |
| M-4 · Coverage + CI | `pytest-cov` + GitHub-Actions-Workflow (Lint via pyflakes, Tests, Coverage-Gate). | M | ✅ erledigt |
| M-5 · fRAG-Embedding-Slot | Pluggable Similarity-Funktion hinter dem bestehenden `Weights`-Schema (optional Embeddings), API-stabil. | M | ✅ erledigt |

### B) Langfristige Epics (Architekturumbau, Tech Debt)

| Epic | Beschreibung |
|------|--------------|
| E-1 · Persistente Rules Engine | **✅ erledigt.** `RuleStore` (SQLite, versioniert: active/revoked/superseded) in `rules.py`; der Kernel lädt aktive Regeln aus dem Store statt sie bei jedem Start neu abzuleiten (idempotenter Backfill für Bestandsdaten). `kernel.revoke_rule()` nimmt eine erzwungene Regel zurück, ohne das append-only Event Log anzutasten. |
| E-2 · Skalierbares Retrieval/Graph | **Weitgehend erledigt.** Lexikalischer invertierter Index (`active_by_tokens`, p95 ~48 ms) **und** dedizierte Graph-Schicht `MemoryGraph` (A-MEM/Zettelkasten: typisierte Kanten supersedes/exception_to/contradicts/depends_on, persistent, `kernel.link/related/explain`). Consolidation schreibt Widerspruchs-Kanten + bucketet nach (case, type) → weniger O(n²). **Offen:** ANN/Vektor-Index für den Embedding-Pfad (braucht ein echtes Modell). |
| E-3 · Wiki-Projektion | **✅ erledigt.** `WikiProjection` (`wiki.py`) rendert menschenlesbare Markdown-Seiten pro Akte aus Store + Graph + Verlauf (read-only, „nicht die alleinige Wahrheit"). `kernel.wiki_page/wiki_index`; `/api/wiki` in der Agentischen Akte. |
| E-4 · Postgres-Backend | SQLite-Store-Abstraktion für Mehrbenutzer/Mandanten, Migrationen, Nebenläufigkeit. |
| E-5 · Coding-Agent-Guard als MCP-Server | Echter MCP-Transport statt nur HTTP, damit Coding Agents nativ andocken. |

---

## 4. Sprint-Durchführung (Phase 4) — Abschlussbericht

Der gesamte Backlog der Kategorie A wurde abgearbeitet. Reihenfolge bewusst
nach Abhängigkeit: M-1 schafft das Fundament, M-2/M-3 setzen darauf auf,
M-4 sichert die Qualität, M-5 erweitert den Kernel.

### M-1 · Shared Web-Toolkit ✅
- Neues Modul `brainfump/webkit.py`: deklaratives `WebApp`-Routing,
  `Request`/`Response`, `json_response`/`text_response`, zentrales Fehler-
  Mapping (`HttpError`→Status, `ValueError`/`KeyError`→400, sonst 500,
  unbekannte Route→404), Static-Serving mit Content-Type-Inferenz, `serve()`.
- Alle fünf Server (`brainfump/api.py` + 4 App-Server) umgestellt;
  `create_server(...)`-Signaturen und `main()`-Einstiegspunkte unverändert.

### M-2 · Einheitliche Request-Validierung ✅
- `require()`-Helfer im Toolkit ersetzt alle ad-hoc-Pflichtfeldprüfungen.
  Präsenzprüfung statt Wahrheitswert behebt den latenten Bug, dass ein Score
  von `0.0` als „fehlend" galt.

### M-3 · Health-/Version-Endpunkte ✅
- `WebApp.health(service, version)` registriert einheitlich `/api/health`
  und `/api/version` auf allen fünf Services.
- Docker-`HEALTHCHECK`s zeigen jetzt auf `/api/health` statt auf
  Geschäfts-Endpunkte; Version aus `brainfump.__version__`.

### M-4 · Coverage + CI ✅
- `pytest-cov` mit Branch-Coverage und Gate (`fail_under=85`, aktuell
  **94,7 %**); reine Transport-Schichten (`server.py`) ausgenommen.
- GitHub-Actions-Workflow `.github/workflows/ci.yml`: pyflakes + `pytest --cov`
  über Python 3.10/3.11/3.12.

### M-5 · fRAG-Embedding-Slot ✅
- `Retriever` akzeptiert eine austauschbare `Similarity` (Protocol).
  Default `LexicalSimilarity` (Jaccard, unverändertes Verhalten),
  `EmbeddingSimilarity(embed)` für Cosinus über einen injizierten Provider
  mit Card-Vektor-Cache. `min_similarity`-Schwelle filtert Grundrauschen.
- `BrainFumpKernel(similarity=…)` reicht die Wahl durch — das Ranking-Schema
  (`Weights`) bleibt stabil.

**Verifikation:** `pyflakes` sauber, **119 Tests grün**, Branch-Coverage 94,7 %.

### Empfehlung für den nächsten Sprint
Aus Kategorie B zuerst **E-2** (skalierbares Retrieval/Graph — baut direkt auf
dem nun vorhandenen Embedding-Slot auf) und **E-1** (persistente Rules Engine,
beseitigt die RAM-Rekompilierung beim Start).

---

## 5. Next Horizon — Sprint 1 (Stabilize & Ship Core) ✅ durchgeführt

Umsetzung der Roadmap aus `docs/ENGINEERING_LOOP_REVIEW.md`.

### Deliverables
- **Embedding-Provider** `brainfump/embeddings.py::HashingEmbedder` —
  deterministisches, abhängigkeitsfreies Feature-Hashing-Embedding
  (L2-normalisiert), Offline-Default und Adapter-Vorbild für echte APIs.
  _Hinweis (Deep-Dive-Korrektur): ein echter API-`EmbeddingProvider`-Adapter
  (Anthropic/Local) ist NOCH NICHT umgesetzt — bleibt offenes Ticket._
- **Bekannter Bug (Deep-Dive):** Der Vektor-Cache-Schlüssel hasht nur das
  Statement, eingebettet wird aber Statement+Scope → reine Scope-Änderungen
  werden nicht neu eingebettet. Fix offen (siehe `docs/DEEP_DIVE_REVIEW.md` K5).
- **Persistenter Vektor-Cache** `SqliteVectorCache` (MutableMapping) —
  in `EmbeddingSimilarity(embed, cache=…)` injizierbar; Card-Vektoren
  überleben Neustarts, statt jedes Mal neu eingebettet zu werden.
  Schlüssel enthält Statement-Hash → geänderte Aussagen werden re-embedded.
- **Latenz-Benchmark** `scripts/bench_retrieval.py` — misst p50/p95/max für
  lexical vs. embedding über N Karten.

### QM
- Parität lexical↔embedding auf Golden-Set (`test_retrieval_parity_*`),
  Cache-Persistenz-Test über „Neustart", Latenz-Smoke. **132 Tests grün.**

### Gemessene Baseline (`--cards 10000 --queries 200`)
| Similarity | p50 | p95 | max |
|---|---|---|---|
| lexical | ~118 ms | ~131 ms | ~152 ms |
| embedding (hashing, dim=256) | ~318 ms | ~362 ms | ~1057 ms |

**Befund (radikaler Realismus):** Das p95-Ziel < 50 ms wird bei 10k Karten
**noch nicht** erreicht — der O(n)-Vollscan über alle aktiven Karten je Query
ist der Flaschenhals. Das ist genau das Signal, das Sprint 2 begründet:
Kandidaten-Vorfilter über einen invertierten Index (Epic E-2), damit das
Scoring nur noch auf einer kleinen Treffermenge läuft. Der Benchmark dient ab
jetzt als Regressions-/Schwellenmessung.

---

## 6. Next Horizon — Sprint 2 (Optimize & Refine) ✅ Kern-Deliverable

### Deliverable: invertierter Token-Index als Kandidaten-Vorfilter (E-2, Teil 1)
- `MemoryCardStore` führt einen lazy invertierten Index (Token → aktive
  card_ids), der bei jedem Schreibzugriff invalidiert und beim nächsten Lesen
  neu gebaut wird. Neue Methode `active_by_tokens(...)`.
- `LexicalSimilarity.candidate_tokens(query)` meldet die Vorfilter-Tokens;
  `Retriever(use_index=True)` nutzt sie. Da Jaccard genau dann > 0 ist, wenn
  ein Token geteilt wird, ist der Vorfilter **ergebnis-identisch** zum vollen
  Scan (Test `test_index_scores_same_cards_as_full_scan`).
- Embeddings haben keinen `candidate_tokens` → automatischer Fallback auf den
  vollen Scan (kein Ergebnisverlust).

### QM
- Exaktheit (Index == Full-Scan auf Zufalls-Store), Index-Invalidierung bei
  add/Status-Wechsel, Case-/Datums-Filter, Embedding-Fallback. **138 Tests grün.**

### Benchmark vorher → nachher (10k Karten, 200 Queries)
| Similarity | p95 vorher | p95 nachher |
|---|---|---|
| lexical | ~131 ms | **~48 ms** (Ziel < 50 ms erreicht) |
| embedding (hashing) | ~362 ms | ~384 ms (unverändert — Token-Prefilter nicht anwendbar) |

**Fazit:** Das p95-Ziel < 50 ms ist für die lexikalische Default-Similarity
erreicht. Für Embeddings bleibt ein approximativer Nearest-Neighbor-Index
(ANN) das nächste Epic — bewusst nicht in diesem Horizont (radikaler Realismus).
