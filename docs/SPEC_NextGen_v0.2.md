# BrainFump NextGen — Spezifikation v0.2

**Agentic Memory Kernel für aktenbasierte GenAI-Systeme**

v0.2 ist die implementierte Fassung der Spezifikation v0.1. Jeder Abschnitt
verweist auf das Modul, das ihn umsetzt.

## 1. Leitthese

BrainFump NextGen ist kein klassisches RAG-System und kein bloßer Memory Store.
Es ist ein **Agentic Memory Kernel**, der Erinnerungen nicht nur speichert, sondern:

- versioniert (`evolution.py`)
- verdichtet (`consolidation.py`)
- bewertet (`evaluation.py`)
- in Regeln übersetzt (`rules.py`)
- Aktionen warnt oder blockiert (`gatekeeper.py`)
- und kontinuierlich evaluiert (`evaluation.py`)

> **Memory ist nicht nur Kontext. Memory ist ein Kontroll- und Lernsystem.**

Die Forschungsbasis: PROJECTMEM (Memory-as-Governance, Pre-Action Gate),
EvoMem/EvoArena (patch-basierte Evolution), TRACE (Preference Compliance statt
nur Preference Access), LightMem (Online/Offline-Trennung),
MemGym/EvoMemBench (eigenständige Memory-Evaluation).

## 2. Zielbild

Das System verhindert, dass Agenten:

| Fehlverhalten | Mechanismus | Modul |
|---|---|---|
| alte Fehler wiederholen | Gate `no_repeat_failed_fix` | `gatekeeper.py` |
| veraltete Präferenzen anwenden | Validity Resolver + Patches | `evolution.py` |
| fragile Dateien ungeschützt ändern | Gate `fragile_file` → `require_review` | `gatekeeper.py` |
| widersprüchliche Stände vermischen | Contradiction Detection | `consolidation.py` |
| falsche Erinnerungen ungeprüft nutzen | Confidence/Trust im Ranking + Evaluation | `retrieval.py`, `evaluation.py` |
| globale Memories in falsche Akten injizieren | Case-Bounded Store + Leakage-Metrik | `memory_cards.py`, `evaluation.py` |

## 3. Architekturprinzipien

### 3.1 Event First

Alles beginnt mit einem unveränderlichen Event Log (`events.py`). SQLite-Trigger
verbieten UPDATE/DELETE auf Datenbankebene — beweissicher per Konstruktion.

Event-Typen: `user_input`, `agent_action`, `tool_call`, `decision`,
`correction`, `failed_attempt`, `successful_attempt`, `document_change`,
`policy_violation`, `risk_marker`.

### 3.2 Memory Cards statt Rohchunks

Aus Events entstehen typisierte Memory Cards (`memory_cards.py`,
`extractor.py` — deterministisch, ohne LLM, daher auditierbar):

```json
{
  "memory_type": "preference",
  "statement": "Für MVP-Frontends wird Vanilla JS bevorzugt.",
  "scope": ["frontend", "mvp", "prototype"],
  "status": "active",
  "valid_from": "2026-06-12",
  "confidence": 0.91,
  "evidence": ["event_123", "event_456"]
}
```

Memory-Typen: `episodic`, `semantic`, `procedural`, `preference`,
`governance`, `evolution`, `failure`, `skill`, `risk`.

### 3.3 Case-Bounded Memory

Jede Akte besitzt einen eigenen Memory-Raum (`case_id`). Retrieval und
Gatekeeping konsultieren zuerst die Akte, dann globale DNA (`case_id=None`).
Fremde Akten sind unsichtbar; Leakage wird gemessen
(`case_scope_leakage_rate`).

### 3.4 Memory-as-Governance

Memory beeinflusst Aktionen: warnen, blockieren, Review erzwingen,
Alternative vorschlagen. Beispielregel (implementiert als eingebautes Gate):

```yaml
rule_id: no_repeat_failed_fix
condition: same_error_signature AND failed_attempt_exists
action: block_or_warn
severity: high
```

## 4. Kernmodule

| Spec-Abschnitt | Modul | Status |
|---|---|---|
| 4.1 Event Log | `brainfump/events.py` | ✅ |
| 4.2 Memory Card Store | `brainfump/memory_cards.py` | ✅ |
| 4.3 Evolution Memory | `brainfump/evolution.py` | ✅ |
| 4.4 Runtime Rule Compiler | `brainfump/rules.py` | ✅ |
| 4.5 Memory Gatekeeper | `brainfump/gatekeeper.py` | ✅ |
| 4.6 fRAG Retrieval Layer | `brainfump/retrieval.py` | ✅ (lexikalisch; Embedding-Slot vorgesehen) |
| 4.7 Dream / Consolidation | `brainfump/consolidation.py` | ✅ |
| 4.8 Evaluation Layer | `brainfump/evaluation.py` | ✅ |
| API `/api/gatekeeper/check` | `brainfump/api.py` | ✅ (Stdlib, framework-frei) |
| Fassade / Pipeline | `brainfump/kernel.py` | ✅ |

### 4.5 Memory Gatekeeper — Modi

`allow < warn < require_review < suggest_alternative < block` —
der schwerste Treffer gewinnt. Implementierte Gates:

1. **no_repeat_failed_fix** — Failure-Karte mit gleicher `error_signature`
   blockiert; ist eine Alternative hinterlegt, wird sie vorgeschlagen.
2. **fragile_file** — Risk-Karte mit `fragile_files` erzwingt Review.
3. **governance** — Governance-Karte mit `forbidden_actions` blockiert.
4. **runtime_rule** — kompilierte TRACE-Regeln gegen den Aktionskontext
   (`warning` → warn, `high`/`critical` → block).

Jede Gate-Entscheidung wird selbst als Event auditierbar geloggt.

### 4.6 fRAG-Ranking

```
score = semantic_similarity + case_relevance + recency
      + confidence + trust + risk_weight + governance_priority
```

Die semantische Komponente ist bewusst austauschbar (aktuell Jaccard,
später Embeddings) — das Ranking-Schema bleibt stabil (Prinzip 7:
Vector Index ist nur der Zugriffspfad, nicht das Memory).

### 4.8 Evaluation — Metriken

`memory_precision_at_k`, `rule_compliance_rate`,
`repeated_failure_avoidance`, `case_scope_leakage_rate`,
`memory_freshness_score`, `governance_false_positive_rate` —
plus Golden-Scenario-Harness als Regressionsschutz.

## 5. Systempipeline

```
User / Agent Event
  → Event Log               (kernel.record)
  → Memory Extractor        (deterministisch)
  → Memory Card Typisierung
  → Evolution Patch Check   (kernel.patch)
  → Consolidation           (kernel.consolidate, offline)
  → fRAG Retrieval          (kernel.search)
  → Runtime Rule Compiler   (automatisch bei correction-Events)
  → Memory Gatekeeper       (kernel.check_action)
  → Agent Action
  → Audit + Evaluation      (kernel.evaluate)
```

LightMem-Schnitt: `record`/`search`/`check_action` sind schnelle
Online-Pfade ohne globale Konsolidierung; `consolidate` und `evaluate`
laufen asynchron/offline.

## 6. Sprint-Status

| Sprint | Deliverables | Status |
|---|---|---|
| 1 — Event Log + Memory Cards | `events.py`, `memory_cards.py`, `extractor.py`, Tests | ✅ |
| 2 — Memory Gatekeeper | `gatekeeper.py`, Risk-Gates, `/api/gatekeeper/check` | ✅ (UI-Warnkarte offen) |
| 3 — Evolution Memory | `evolution.py`, Patch Schema, Validity Resolver, Conflict Detection | ✅ |
| 4 — Runtime Rule Compiler | `rules.py` (Compiler + Checks), Rule Test Harness | ✅ |
| 5 — Evaluation Harness | `evaluation.py`, Golden Scenarios, Compliance Tests | ✅ |

## 7. Strategische Architekturentscheidung

Nicht "Vector DB = Memory", sondern:

- **SQLite/Postgres** = Wahrheit, Events, Versionen (implementiert: SQLite)
- **Vector Index** = semantischer Zugriff (Slot in `retrieval.py`)
- **Graph** = Beziehungen, Konflikte (v0.2: Konflikt-/Exception-Verweise in Payloads; dedizierter Graph in v0.3)
- **Wiki** = menschenlesbare Projektion (v0.3)
- **Rules Engine** = ausführbare Erinnerung (implementiert)
- **Evaluation** = Qualitätskontrolle (implementiert)

## 8. One-More-Thing

Der zentrale Differenzierer bleibt der **Memory Gatekeeper**:

> Der Agent darf nicht nur wissen, was früher passiert ist.
> Er muss daraus Konsequenzen ziehen, bevor er handelt.

## 9. Ausblick v0.3

- Embedding-basierte Similarity hinter dem bestehenden Weights-Schema
- Dedizierte Graph-Schicht (Zettelkasten/A-MEM-Verlinkung)
- Wiki-Projektion: menschenlesbare Memory-Seiten pro Akte
- UI-Warnkarte für Gate-Entscheidungen in der Akte
- SLM-gestützte Extraktion als optionale Ergänzung zur deterministischen
