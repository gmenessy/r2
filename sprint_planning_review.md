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

| Ticket | Beschreibung | Aufwand | Priorität |
|--------|--------------|---------|-----------|
| **M-1 · Shared Web-Toolkit** | Gemeinsames `brainfump/webkit.py`: deklaratives Routing, JSON-Helfer, Static-Serving, einheitliches Fehler-Mapping, `serve()`-Helper. Alle 5 Server darauf umstellen. | M | **HOCH (gewählt)** |
| M-2 · Einheitliche Request-Validierung | `require()`/Schema-Helfer im Web-Toolkit, in allen Servern statt ad-hoc-Checks. Baut auf M-1 auf. | S | Hoch |
| M-3 · `/api/health` + `/api/version` | Einheitliche Health-/Version-Endpunkte je Service; Docker-`HEALTHCHECK` darauf umstellen. | S | Mittel |
| M-4 · Coverage + CI | `pytest-cov` + GitHub-Actions-Workflow (Lint via pyflakes, Tests, Coverage-Gate). | M | Mittel |
| M-5 · fRAG-Embedding-Slot | Pluggable Similarity-Funktion hinter dem bestehenden `Weights`-Schema (optional Embeddings), API-stabil. | M | Mittel |

### B) Langfristige Epics (Architekturumbau, Tech Debt)

| Epic | Beschreibung |
|------|--------------|
| E-1 · Persistente Rules Engine | Regeln aktuell nur im RAM (Rekompilierung beim Start). Eigene `rules`-Tabelle mit Versionierung statt Re-Derivation. |
| E-2 · Skalierbares Retrieval/Graph | Invertierter Index bzw. Vektor-Backend ablösen von O(n)-Scan; dedizierte Graph-Schicht (A-MEM/Zettelkasten) für Konflikte/Abhängigkeiten — beseitigt O(n²). |
| E-3 · Wiki-Projektion | Menschenlesbare Memory-Seiten pro Akte (Spec-Abschnitt 7, v0.3). |
| E-4 · Postgres-Backend | SQLite-Store-Abstraktion für Mehrbenutzer/Mandanten, Migrationen, Nebenläufigkeit. |
| E-5 · Coding-Agent-Guard als MCP-Server | Echter MCP-Transport statt nur HTTP, damit Coding Agents nativ andocken. |

---

## 4. Sprint-Auswahl (Phase 4)

Gewählt: **M-1 · Shared Web-Toolkit** — größter Hebel gegen den dominanten
Tech Debt, entriegelt M-2 und M-3 und senkt das Risiko jeder künftigen App.

### Implementierungs-Status M-1 (erledigt)

- Neues Modul `brainfump/webkit.py` (195 Z.): deklaratives `WebApp`-Routing,
  `Request`/`Response`, `json_response`/`text_response`, zentrales Fehler-
  Mapping (`HttpError`→Status, `ValueError`/`KeyError`→400, sonst 500,
  unbekannte Route→404), Static-Serving mit Content-Type-Inferenz, `serve()`.
- Alle fünf Server (`brainfump/api.py` + 4 App-Server) auf das Toolkit
  umgestellt; `create_server(...)`-Signaturen und `main()`-Einstiegspunkte
  unverändert (keine Auswirkung auf Dockerfiles/Tests/Aufrufer).
- **M-2 teilweise miterledigt:** einheitlicher `require()`-Helfer ersetzt die
  ad-hoc-Pflichtfeldprüfungen; nutzt Präsenzprüfung statt Wahrheitswert und
  behebt damit den latenten Bug, dass ein Score von `0.0` als „fehlend" galt.
- 14 neue Tests (`tests/test_webkit.py`); Gesamtsuite **111 grün**,
  `pyflakes` sauber.

Folgeempfehlung für den nächsten Sprint: **M-3** (`/api/health` je Service auf
Basis des Toolkits) und **M-4** (Coverage + CI).
