# Autonomous Engineering Loop — Review (BrainFump NextGen)

_Stand: nach v0.2-Suite + Sprint M-1…M-5. Basis: 119 Tests, 94,7 % Coverage._
_Dieser Lauf: 3 Quick Wins implementiert → 122 Tests, 94,8 %._

---

## 1. Interdisziplinäre Code-Analyse

- **Kritische Schwachstelle 1 [Business / Architekt] — Embedding-Kosten O(N) pro Suche:**
  `EmbeddingSimilarity.__call__` rief `self._embed(query)` für **jede**
  Kandidatenkarte auf. Bei N aktiven Karten wurde die Query N-mal eingebettet.
  Auswirkung: mit einer echten Embedding-API N-facher Token-/Geldverbrauch und
  N-fache Latenz pro einzelner Suche → direkter ROI-Killer beim Skalieren. **(behoben)**
- **Kritische Schwachstelle 2 [QM / Red-Teaming] — kein Body-Size-Limit:**
  Der HTTP-Layer las `Content-Length` ungedrosselt via `rfile.read(length)`.
  Auswirkung: ein einzelner großer POST kann den Container-Speicher erschöpfen
  (DoS). Fehlender Guardrail vor Go-Live. **(behoben → 413)**
- **Sekundär [Prozessmanager / MLOps] — null Observability:**
  `log_message` war hart auf `pass` gesetzt; keine Access-Logs, keine Latenz.
  Im Betrieb blind. **(behoben → opt-in strukturiertes Logging)**

---

## 2. Direct Implementation (Quick Wins) — umgesetzt & getestet

### Quick Win #1: Query-Embedding einmal pro Suche
- **Ziel-Datei:** `brainfump/retrieval.py` (`EmbeddingSimilarity`)
- Ein-Slot-Memo `_query_memo`; `_embed_query()` bettet die Query nur ein, wenn
  sie sich gegenüber dem letzten Aufruf geändert hat. Innerhalb einer
  `search()`-Schleife → genau 1 Embedding statt N.
- **Test:** `tests/test_retrieval_embedding.py::test_query_embedded_once_per_search`

### Quick Win #2: Body-Size-Guardrail (413)
- **Ziel-Datei:** `brainfump/webkit.py` (`serve`)
- `max_body_bytes` (Default 1 MiB, pro `serve()` überschreibbar); übergroße
  POSTs liefern `413` ohne den Body in den Speicher zu lesen.
- **Test:** `tests/test_webkit.py::test_body_size_limit_returns_413`

### Quick Win #3: Opt-in Access-Logging mit Latenz
- **Ziel-Datei:** `brainfump/webkit.py` (`serve`)
- `serve(access_log=…)` bzw. Env `BRAINFUMP_ACCESS_LOG`; loggt
  `METHOD PATH -> STATUS (x.x ms)`. Default aus → keine Verhaltensänderung.
- **Test:** `tests/test_webkit.py::test_access_log_emits_when_enabled`

---

## 3. Tangible Demo & Use Case

- **Ziel-Szenario:** Coding-Agent-Guard verhindert die Wiederholung eines
  gescheiterten Fixes in einem realen Repo.
- **Perfekte 2-Minuten-Demo:**
  1. `docker compose up -d coding-agent-guard`
  2. Agent meldet einen gescheiterten Fix:
     `POST /api/report {repo, report_type:"failed_attempt", payload:{error_signature, alternative}}`
  3. Später denselben Fix versuchen: `POST /api/gate {repo, error_signature}`
  4. **Greifbares Ergebnis:** Antwort `{"mode":"suggest_alternative",
     "suggested_alternative":"…"}` — der Agent wird gestoppt und bekommt die
     gelernte Alternative. `GET /api/summary?repo=` zeigt das menschenlesbare
     Projektgedächtnis.

---

## 4. The "One More Thing" (Differenziator)

- **Feature:** `GET /api/health` + `/api/version` liefern bereits den Stand;
  der nächste Wow-Hebel ist eine **`X-BrainFump-Decision`-Antwort-Header-Spur**
  bzw. der bereits auditierte Gate-Event — jede blockierende Entscheidung ist
  vollständig nachvollziehbar im Event Log (`source:"gatekeeper"`).
- **Warum es zündet:** „Warum wurde das blockiert?" ist sofort beantwortbar —
  Vertrauen durch Transparenz statt Blackbox.
- **Technischer Aufwand:** bereits vorhanden (Audit-Event in `kernel.check_action`);
  Visualisierung = ein Frontend-Snippet.

---

## 5. The Next Horizon

- **Fokus-Ziel:** Retrieval skaliert auf ≥10k Karten je Akte bei p95 < 50 ms,
  ohne das `Weights`-Ranking zu ändern (Embedding-Slot ist vorbereitet).
- **Business-Erwartungs-Korrektur:** Kein eigenes Vektor-DB-Cluster in diesem
  Horizont. Erst persistenter In-Process-Index (z. B. numpy/sqlite-vss),
  Vektor-DB-Migration bleibt späteres Epic.

---

## 6. 3-Sprint Delivery Plan

### Sprint 1 — Stabilize & Ship Core
- **Ziel:** Embedding-Pfad produktreif (Provider-Adapter + Index).
- **Deliverables:** (1) `EmbeddingProvider`-Adapter (Anthropic/Local) hinter
  `Similarity`; (2) persistenter Card-Vektor-Cache (statt RAM-dict);
  (3) Benchmark-Skript Retrieval-Latenz.
- **QM/Test:** Retrieval-Parität lexical↔embedding auf Golden-Set; p95-Latenz
  gemessen und im CI als Schwelle.

### Sprint 2 — Optimize & Refine
- **Ziel:** O(n²)-Stellen entschärfen (E-2 angeschnitten).
- **Deliverables:** (1) invertierter Index für Kandidaten-Vorfilter;
  (2) Konflikt-/Dedupe-Erkennung mit Blocking statt Vollpaarvergleich;
  (3) `/api/metrics` um Retrieval-Stats erweitern.
- **QM/Test:** identische Ergebnisse vor/nach Index auf Golden-Set; Last-Test
  10k Karten.

### Sprint 3 — Harden & Comply (Go-Live Ready)
- **Ziel:** Mandantenfähigkeit + Betriebsreife.
- **Deliverables:** (1) Auth/Mandanten-Header am Web-Toolkit; (2) Rate-Limit
  + Body-Limit-Doku; (3) persistente Rules Engine (E-1) statt RAM-Rekompilierung.
- **QM/Test:** Red-Team-Suite (Case-Scope-Leakage, Body-Limit, Auth-Bypass);
  Coverage-Gate bleibt ≥ 85 %.
