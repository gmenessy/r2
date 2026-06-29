# Deep-Dive Review — BrainFump NextGen

_Methodik: 4 unabhängige Review-Agenten (Kernel · Retrieval/Security · Apps · Doku-vs-Code)
über den gesamten Repo-Stand, plus Faktenbasis (Inventar, Import-Graph, Coverage je Modul).
Alle hier gelisteten Funde sind am Code verifiziert (file:line)._

## Gesamtbild

- **Architektur gesund:** Import-Graph ist ein zyklenfreier DAG (`kernel` als Fassade,
  `events`/`embeddings`/`webkit` als abhängigkeitsfreie Blätter), keine Rückimporte der
  Fassade in Kernmodule. 3.544 LoC Produktivcode, 2.023 LoC Tests, 138 Tests.
- **Keine TODO/FIXME/bare-except**, nur 1 bewusstes `except Exception` (defensiv im HTTP-Layer).
- **Kernrisiko: Nebenläufigkeit.** Die Apps laufen auf `ThreadingHTTPServer`, teilen aber
  eine unsynchronisierte SQLite-Connection und einen In-RAM-Index → unter Last nicht thread-safe.
- **Bereits behoben in diesem Review:** ein reproduzierbarer CI-Bruch (flaky Index-Paritätstest
  unter `pytest --cov`).

## Befunde (verifiziert, priorisiert)

> **Status Fix-Sprint 1:** K1, K2, K3, K5 sind **behoben** (siehe ✅), abgesichert
> durch Regressionstests inkl. Nebenläufigkeits-Stresstest. Suite: 142 grün
> (stabil unter `pytest --cov`). Offen bleiben K4, K6 und die MITTEL/NIEDRIG-Punkte.

### KRITISCH

| # | Ort | Problem | Auswirkung | Fix |
|---|-----|---------|-----------|-----|
| K1 ✅ behoben | `memory_cards.py` `_ensure_index`/`_invalidate_index`; `webkit.serve` | Geteilter `_token_index` (dict) wird lock-frei gelesen/gebaut, während Schreibpfade ihn auf `None` setzen — über viele HTTP-Threads | Race → `dict changed size during iteration`, halb gebaute Indizes, verlorene Treffer unter Last | `threading.Lock` um Index-Bau + Schreibpfade |
| K2 ✅ behoben | `memory_cards.py:131`, `embeddings.py:64` | Eine SQLite-Connection mit `check_same_thread=False`, parallel aus mehreren Threads | `sqlite3.ProgrammingError`/`database locked` bei gleichzeitigen Requests | Connection-Lock oder `threading.local`-Connection-per-Thread |
| K3 ✅ behoben | `optimizer.py:169` | `must_contain`/`must_not_contain`-Regel hat `condition={"case_id":…}` ohne `action_type` → feuert bei *jedem* Gate-Aufruf | Nach einer einzigen Stil-Korrektur liefert das Pre-Test-Gate (`check_variant`) dauerhaft `warn` statt `allow`/`block` (verifiziert) | `condition` um `{"action_type":"validate_output"}` ergänzen |
| K4 | `webkit.py:233` | `int(Content-Length)` ungeprüft: nicht-numerisch → 500; negativ umgeht Body-Limit, `rfile.read(-1)` liest bis EOF | Trivialer DoS / Body-Limit-Bypass | `Content-Length` defensiv parsen, `<0`→400 |
| K5 ✅ behoben | `retrieval.py:95` vs `:103` | `_card_key` hasht nur `statement`, eingebettet wird `statement+scope` | Reine Scope-Änderung liefert veralteten Embedding-Vektor (Korrektheit) | Scope in den Cache-Key aufnehmen |
| K6 | `evolution.py:161` | `resolve` einer globalen Karte ruft `active(case_id=None)` → liefert Karten *aller* Akten; fremde Scope-Exception kann globale DNA überschreiben | Case-Scope-Isolation (Prinzip 3.3) verletzt | Exception-Scan auf `exc.case_id == resolved.case_id` filtern |

### MITTEL

| # | Ort | Problem | Fix |
|---|-----|---------|-----|
| M1 | `gatekeeper.py:104` | Gates rufen `active(case_id=…)` ohne `on_date` → abgelaufene (per `valid_to`) Verbote/Risiken feuern weiter | `on_date=date.today()` übergeben |
| M2 | `memory_cards.py:237` | `active_by_tokens` macht N+1 `get()` (ein SELECT pro Kandidat) → kann langsamer als Vollscan werden | `SELECT … WHERE card_id IN (…)` |
| M3 | `pre_edit_gate.py:59` | Hook blockt nur `block`/`require_review`; `suggest_alternative` (Hauptfall „gescheiterter Fix") → Exit 0, Edit läuft durch | `suggest_alternative` in die blockierende Menge |
| M4 | `evolution.py:77` | `apply_patch` prüft `target.status` nicht → zweiter Patch auf superseded Karte verzweigt die Versionskette | `if target.status!="active": raise` |
| M5 | `webkit.py:242` | Kein Read-Timeout → Slowloris hält Worker-Threads | `connection.settimeout(...)`, gechunkt lesen |
| M6 | `guard.py:49` | `report("fragile_file")` ohne `files`/`file` → nackter `KeyError` (nur durch webkit→400 abgefangen) | explizit `require`/`HttpError` |
| M7 | `consolidation.py:90` | `_contradicts` bestimmt Negation global pro Statement → Falsch-Positive setzen Karten irrtümlich auf `contradicted` | konservativer schwellen / Negation lokal |

### NIEDRIG (Auswahl)
- `retrieval.py:213` `recency` nutzt `datetime.now()` pro Scoring → Scores nicht reproduzierbar (Wurzel des gefixten CI-Flakes); `now` einmal pro `search()` bestimmen.
- `embeddings.py:48` MD5 im Heißpfad → schneller Nicht-Krypto-Hash genügt.
- `consolidation.py` Dedupe/Konflikt sind O(n²) und mutieren während des Scans (offline tolerierbar).
- `extractor.py:43` String-`scope` wird zu Zeichen-Tupel; `memory_cards.active_by_tokens` ignoriert den `scope`-Filter (Asymmetrie zu `active`).
- `webkit.serve` default-bindet `0.0.0.0`; 500-Antworten spiegeln rohe Exception-Strings.
- `house/server.py` reicht `severity` nicht durch; uneinheitliche Default-Namen (`default` vs `wohnzimmer`).

## Doku-/Sprint-Plan-Abgleich

| Behauptung | Realität | Status |
|---|---|---|
| Architektur/Features (Ports 8010-8040, `/api/health`, Embedding-Slot, invertierter Index, Versionen 0.2.0) | code-gedeckt | ✅ korrekt |
| „119 Tests" (README) / 122 / 132 | tatsächlich **138** | korrigiert |
| Coverage „94,7 %" | gemessen **93,89 %** | korrigiert |
| „138 Tests grün / CI läuft" | unter `pytest --cov` war **1 failed** | **behoben** (CI-Flake gefixt) |
| E-2 „offenes Epic" | Teil 1 (lexical-Prefilter) **im Code** | als „teilweise erledigt" markiert |
| Sprint 1 „EmbeddingProvider-Adapter (Anthropic/Local) ✅" | nur `HashingEmbedder` existiert, **kein** Provider-Adapter | Plan überzeichnet → Ticket zurück in Backlog |

## Empfohlene Remediation-Reihenfolge

1. **Nebenläufigkeit härten (K1+K2)** — Voraussetzung für jeden echten Mehrnutzer-Betrieb.
2. **K3 (Gate-Vergiftung) + K5 (Embedding-Scope)** — echte Korrektheits-Bugs, klein zu fixen.
3. **K4+M5 (HTTP-Härtung)** — Content-Length-Validierung, Read-Timeout.
4. **K6+M1+M4 (Memory-Semantik)** — Case-Scope, Gültigkeitsfenster, Versionskette.
5. **M2 (N+1)** — den Index-Perf-Gewinn absichern.
6. Danach die NIEDRIG-Punkte als Hygiene-Sweep.
