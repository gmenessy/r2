# Deep Dive & Reifegrad-Messung — Agent Execution Layer

**Gegenstand:** `apps/agent_layer` (Plattform), `apps/agent_flightdeck` (Demo-App),
Änderung an `brainfump/webkit.py` (Header-Durchreichung).
**Methode:** Kritische Code-Review mit **empirischer Verifikation** — jedes
Finding wurde per Repro-Skript bestätigt, jeder Fix per Regressionstest und
erneutem Repro gegengeprüft. Ergänzt um Mikro-Benchmarks auf der Ziel-Klasse
(CPU-Container). Kein Finding beruht auf bloßer Vermutung.

---

## 1. Ergebnis in einem Satz

Die Plattform ist nach diesem Review **Reifegrad 3 („Definiert")** — solide
für interne Pilotprojekte und PoCs mit vertrauenswürdigen Tenants; vor einem
externen Produktivbetrieb müssen die offenen Punkte O1–O5 (Isolationstiefe,
Skalierung über einen Prozess hinaus, Quotas) adressiert werden.

---

## 2. Verifizierte Findings — alle acht behoben

| ID | Schwere | Bereich | Finding (Beleg per Repro) | Fix |
|---|---|---|---|---|
| **F1** | 🔴 hoch | Security | `GET /api/trace`/`/api/explain` waren **unauthentifiziert**: jeder mit einer `run_id` konnte Ziele, Tool-Argumente und Memory-Inhalte *fremder Tenants* lesen | Trace-Zugriff nur noch für den eigenen Tenant (`X-API-Key`) oder Admin; ohne Key 401, fremder Tenant 403; Test `test_trace_isolation_between_tenants` |
| **F4** | 🔴 hoch | Security | **Gate-Bypass**: ein Tool-Argument `{"action_type": "harmlos"}` überschrieb per Dict-Spread den Gatekeeper-Kontext — ein per Governance verbotenes Tool passierte das Gate (Repro: `gate.allowed=True` trotz `forbidden_actions`) | Plattform-Kontext (`action_type`, `case_id`) wird *nach* den LLM-Argumenten gesetzt und gewinnt immer; Test `test_tool_arguments_cannot_override_gatekeeper_context` |
| **F2** | 🟠 mittel | Reliability | `RLIMIT_AS` maß den **gesamten** Adressraum inkl. geerbtem Parent-Speicher: bei einem 430-MiB-Serverprozess starb ein legitimes 20-MiB-Tool trotz 256-MiB-Policy mit `memory limit exceeded` (Repro bestätigt) | Limit ist jetzt **relativ**: `geerbter VmSize + memory_bytes` — `memory_bytes` bedeutet „Spielraum des Tools"; Test mit 300-MiB-Ballast |
| **F3** | 🟠 mittel | Billing | Ein Tenant mit **erschöpftem Budget** löste trotzdem noch einen vollen, echten LLM-Call aus (Kosten beim Betreiber), bevor die Buchung scheiterte (Repro: 1 LLM-Call bei 0-Budget) | Preflight `has_budget()` vor dem ersten LLM-Schritt: 0 Calls, deterministischer `budget_exceeded` |
| **F7** | 🟠 mittel | xAI | Ein LLM-Backend-Fehler ließ den Trace als **`running`-Zombie** zurück und der dokumentierte Status `llm_error` existierte gar nicht (Repro bestätigt) | `LLMError` wird gefangen: Trace-Step `llm_error`, Status `llm_error`, Run-Ergebnis statt 502; Explain-Narrativ nennt den Abbruchgrund |
| **F5** | 🟡 niedrig | Security | Admin-Token-Vergleich mit `!=` — nicht timing-sicher | `hmac.compare_digest` |
| **F6** | 🟡 niedrig | Reliability | HTTP-**4xx** vom LLM-Backend wurde wie ein Netzfehler retryt — deterministische Fehler können durch Wiederholung nie gelingen | 4xx → sofortiger `LLMError`; nur 5xx/Netzfehler werden mit Backoff wiederholt |
| **F8** | 🟡 niedrig | Billing | `llm_cost` rundete **ab**: Calls unterhalb der Mikro-USD-Auflösung waren dauerhaft kostenlos | Ceiling-Rundung — jeder Call mit >0 Tokens kostet ≥1 Mikro-USD |

Repros: Sandbox-/Runtime-Szenarien wurden vor dem Fix ausgeführt (Finding
bestätigt) und nach dem Fix erneut (Verhalten korrekt). Alle Fixes sind
zusätzlich als dauerhafte Regressionstests verankert.

---

## 3. Bewusste, dokumentierte Grenzen

> **Sprint 3 (2026-07-18)** hat O1, O2, O4, O5 und O6 geschlossen — siehe
> [`SPRINT_PLAN_AGENT_LAYER.md`](SPRINT_PLAN_AGENT_LAYER.md) und den
> Abschlussbericht in Abschnitt 7. Offen bleiben bewusst O3, O7, O8.

| ID | Bereich | Grenze | Status |
|---|---|---|---|
| **O1** | Sandbox | Fehlende Privilegien-/Dateisystem-Isolation (Kind mit gleicher UID) | ✅ **S3-1:** Root-Start → `setgroups/setgid/setuid(nobody)` vor `fn()`; `read_only`+`cap_drop`+`no-new-privileges` in Compose |
| **O2** | Sandbox | Egress-Sperre nur auf Bibliotheksebene, durch Fremdcode umgehbar | ✅ **S3-2:** `internal`-Netz ohne Egress (`docker-compose.hardened.yml`), vLLM innen; als Deploy-Pflicht in `DEPLOY_HARDENING.md` |
| **O4** | Billing | Budget um max. einen LLM-Call überziehbar | ✅ **S3-3:** `reserve()` bindet den Höchstpreis vor dem Call, `settle()` bucht die Ist-Kosten — Fenster geschlossen |
| **O5** | Billing/API | Keine Rate-Limits, keine Key-Rotation/-Ablauf | ✅ **S3-4:** Token-Bucket je Tenant (`429`+`Retry-After`), `ttl_seconds` + `rotate_key` mit Kulanzfenster |
| **O6** | xAI | Traces/Events wachsen unbegrenzt | ✅ **S3-5:** `TraceStore.prune()` (Alter + N-jüngste/Tenant), `--retention-days` beim Start |
| **O3** | Skalierung | Ein-Prozess/SQLite — horizontal nicht skalierbar | ⏳ offen (Sprint 4): gemeinsamer Store; offline nicht testbar, Naht vorbereitet |
| **O7** | Performance | Synchrones Handling — lange Runs binden einen Thread | ⏳ offen (Sprint 4): Job-Queue + Polling/SSE |
| **O8** | Tools | `validate_args` ohne verschachtelte Schemata | ⏳ offen (P3): bewusster Lightweight-Trade-off |

---

## 4. Performance-Messung (CPU-Container-Klasse, Python 3.11)

Der Plattform-Overhead ist gegenüber der LLM-Inferenz (im Produktivbetrieb
Sekunden auf vLLM) vernachlässigbar — die Architekturentscheidung
„Orchestrierung auf CPU, Inferenz ausgelagert" trägt:

| Messgröße | Wert |
|---|---|
| Sandbox-Roundtrip (fork + rlimits + Pipe + JSON) | **~5 ms** median, p95 ~6 ms |
| Voller Agent-Run: 2 LLM-Steps (SimLLM), 1 sandboxed Tool, Gatekeeper, Billing, Trace | **~6,6 ms** → ~150 Runs/s pro Thread |
| Run ohne Tool (LLM + Memory + Billing + Trace) | **~0,8 ms** |
| Trusted-Inline-Tool (Vergleichswert) | ~0,004 ms |

Einordnung: Der Fork kostet ~5 ms pro Tool-Aufruf — der Preis der
Prozess-Isolation. Für Hochfrequenz-Tools ohne Fremdcode steht der
`sandboxed=False`-Pfad (Plattform-Tools) zur Verfügung.

---

## 5. Reifegrad-Messung

Modell: 5 Stufen — **1 Initial** (ad hoc), **2 Wiederholbar** (läuft, Lücken
bekannt), **3 Definiert** (dokumentiert, getestet, Grenzen explizit),
**4 Gemanagt** (messbar, mandantenfest, betriebsbewährt), **5 Optimiert**.

Die Matrix zeigt zwei Stände: **nach dem Deep Dive** (Findings F1–F8 behoben)
und **nach Sprint 3** (O1/O2/O4/O5/O6 geschlossen).

| Dimension | Deep Dive | Nach Sprint 3 | Begründung (Sprint-3-Stand) |
|---|:---:|:---:|---|
| Funktionalität (Agent-Loop, Tools, Memory) | 3 | **3** | ReAct-Loop mit Gate, Schema-Validierung, Lern-Rückfluss; kein Streaming, keine parallelen Tool-Calls (O7/O8) |
| Sicherheit & Sandboxing | 3 | **4** | + Privilege-Drop auf `nobody` (S3-1), erzwungene Netz-Isolation (S3-2) zusätzlich zu API-Isolation (F1) und Gate (F4) |
| Mandantenfähigkeit & Billing | 3 | **4** | + Budget-Reservierung (S3-3), Rate-Limits + Key-Ablauf/-Rotation (S3-4) auf gehashtem Ganzzahl-Ledger |
| Observability & xAI | 3–4 | **4** | + Retention (S3-5) schließt O6; lückenlose Traces inkl. Fehlerpfaden, Explain, Kosten-Breakdown |
| Zuverlässigkeit & Skalierung | 2 | **2** | unverändert — ein Prozess, SQLite, synchron (O3/O7 bewusst Sprint 4) |
| Testbarkeit & CI | 4 | **4** | 316 Tests, Branch-Coverage-Gate ~95,8 %, SimulatedLLM für offline-E2E, jedes Ticket als Regressionstest |
| Doku & Developer Experience | 4 | **4** | + `DEPLOY_HARDENING.md`, Härtungs-Override; Demo-App + Flightdeck unverändert stark |
| Betrieb & Deployment | 3 | **4** | + gehärteter Compose (read_only/caps/no-new-privileges), Deploy-Checkliste, Retention/Rate-Limit-Schalter |

**Gesamt nach Sprint 3: Reifegrad 4 — „Gemanagt" für den Ein-Instanz-Betrieb
mit externen Tenants.** Die einzige Dimension unter Grad 4 ist Skalierung
(Grad 2) — der Weg zu Grad 5 (horizontale Skalierung, Streaming) ist Sprint 4
(O3, O7).

---

## 6. Prioritäten

1. ~~**P1 (vor externem Betrieb):** Privilege-Drop (O1), Netz-Isolation (O2).~~
   ✅ Sprint 3 (S3-1, S3-2).
2. ~~**P2 (Wachstum):** Budget-Reservierung (O4), Rate-Limits/Key-Rotation (O5).~~
   ✅ Sprint 3 (S3-3, S3-4). Gemeinsamer Store (O3) bleibt offen (Sprint 4).
3. **P3 (Komfort):** ~~Trace-Retention (O6)~~ ✅ (S3-5); asynchrone
   Runs/Streaming (O7), tiefere Schema-Validierung (O8) → offen.

---

## 7. Sprint-3-Abschlussbericht (2026-07-18)

Umgesetzt nach dem Plan aus `SPRINT_PLAN_AGENT_LAYER.md`, Reihenfolge nach
Abhängigkeit (S3-3 zuerst, da es die Billing-Sequenz umbaut).

| Ticket | Deliverable | Verifikation |
|---|---|---|
| **S3-3** (O4) | `BillingLedger.reserve/settle/release`; Runtime bindet den Höchstpreis (`estimate_prompt_tokens` × `max_tokens`) vor jedem LLM-Call, rechnet danach auf Ist-Kosten ab, gibt bei Fehler frei | Repro „ein Call über Budget" vor/nach; Reservierungs-Lebenszyklus, Freigabe bei `llm_error`, keine Ist-Überbuchung als Regressionstests |
| **S3-1** (O1) | Sandbox droppt bei Root-Start auf `nobody` (setgroups→setgid→setuid, Workdir vorher beschreibbar); `dropped_privileges` im Report; Compose gehärtet | Guard-Logik unit-getestet (CI unprivilegiert); realer Drop als dokumentierter Root-Container-Smoke |
| **S3-2** (O2) | `docker-compose.hardened.yml` (`internal`-Netz, vLLM innen); `DEPLOY_HARDENING.md` mit Checkliste; Härtung im Standard-Compose | Beide Compose-Configs validiert (`config -q`) |
| **S3-4** (O5) | `RateLimiter` (Token-Bucket/Tenant), `429`+`Retry-After`; Key-`ttl_seconds` + `rotate_key` (Kulanzfenster, keine Budget-Verdopplung) | Limiter mit injizierter Uhr; Key-Ablauf/Rotation; Live-Smoke: `429`/`Retry-After: 30`, Rotation ohne Verdopplung |
| **S3-5** (O6) | `TraceStore.prune()` (Alter + N-jüngste/Tenant); `--retention-days` beim Start | Prune nach Alter/pro-Tenant/No-op als Tests |

**Ergebnis:** 295 → **316 Tests** grün, Branch-Coverage **~95,8 %**, pyflakes
sauber. Reifegrad **3 → 4** in Sicherheit, Billing, Observability und Betrieb.
Bewusst offen (radikaler Realismus): O3 (Postgres — offline nicht testbar),
O7 (Streaming), O8 (Schema-Tiefe) → Sprint 4.
