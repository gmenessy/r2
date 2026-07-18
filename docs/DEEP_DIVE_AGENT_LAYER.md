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

## 3. Bewusste, dokumentierte Grenzen (offen — Roadmap)

| ID | Bereich | Grenze | Empfehlung |
|---|---|---|---|
| **O1** | Sandbox | Ressourcen-Isolation (rlimits, Timeout, Env-Scrubbing, Tempdir), aber **keine Dateisystem-/Privilegien-Isolation**: der Kindprozess läuft mit gleicher UID und kann z. B. `/data/billing.db` lesen | P1: bei Root-Start `setuid(nobody)` im Kind; mittelfristig Landlock/seccomp oder gVisor-Klasse; Container mit `read_only` + `no-new-privileges` betreiben |
| **O2** | Sandbox | Egress-Sperre wirkt auf Bibliotheksebene (`socket`-Stub) — gegen *versehentlichen* Netzzugriff, durch bösartigen Code umgehbar (`ctypes`, Re-Import von `_socket`) | P1: harte Isolation über Container-Network-Policy; ist im README bereits als Betriebsanforderung dokumentiert |
| **O3** | Skalierung | Ein-Prozess-Architektur: SQLite-Ledger/-Traces sind instanzlokal — **horizontal nicht skalierbar**; bei Mehrinstanz-Betrieb wären Budget-Checks racy | P2: gemeinsamer Store (Postgres/Litestream) oder Sticky-Tenant-Sharding |
| **O4** | Billing | Teilgedecktes Budget kann um **maximal einen LLM-Call** überzogen werden (Buchung nach dem Call; F3 deckt nur den Null-Rest-Fall) | P2: Reservierung (`max_tokens`-basierte Vorab-Buchung, Differenz-Gutschrift) |
| **O5** | Billing/API | Keine Rate-Limits pro Zeitfenster, keine Key-Rotation/-Ablauf | P2 |
| **O6** | xAI | Traces/Events wachsen unbegrenzt (keine Retention) | P3: TTL-Pruning-Job, Konsolidierung existiert kernelseitig bereits |
| **O7** | Performance | Synchrones Request-Handling: lange LLM-Runs binden je einen Worker-Thread | P3: Job-Queue + `GET /api/runs/{id}`-Polling oder SSE-Streaming |
| **O8** | Tools | `validate_args` prüft Pflichtfelder + Basistypen, keine verschachtelten Schemata | P3, bewusster Lightweight-Trade-off |

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

| Dimension | Grad | Begründung (nach den Fixes dieses Reviews) |
|---|:---:|---|
| Funktionalität (Agent-Loop, Tools, Memory-Integration) | **3** | ReAct-Loop mit Gate, Schema-Validierung, Lern-Rückfluss; kein Streaming, keine parallelen Tool-Calls |
| Sicherheit & Sandboxing | **3** | Mandanten-Isolation der API (F1), Gate-Bypass geschlossen (F4), rlimits/Timeout/Env-Scrubbing; Isolationstiefe begrenzt (O1/O2) |
| Mandantenfähigkeit & Billing | **3** | Gehashte Keys, Ganzzahl-Ledger, Budget-Preflight (F3), Ceiling (F8); keine Reservierung/Quotas (O4/O5) |
| Observability & xAI | **3–4** | Lückenlose Traces inkl. Fehlerpfaden (F7), Explain-Narrativ, Kosten-Breakdown pro Run; keine Retention/Metrics-Endpoint (O6) |
| Zuverlässigkeit & Skalierung | **2** | Ein Prozess, SQLite, synchron (O3/O7) — für Pilot ausreichend, kein HA |
| Testbarkeit & CI | **4** | 295 Tests, Branch-Coverage-Gate ~95 %, SimulatedLLM macht E2E offline/deterministisch, Findings als Regressionstests |
| Doku & Developer Experience | **4** | Paper-referenzierte READMEs, API-Tabellen, Demo-App zeigt App-Bau in ~60 Zeilen, Flightdeck macht Verhalten sichtbar |
| Betrieb & Deployment | **3** | Docker + Compose, Healthchecks, Volumes, Env-Konfiguration; kein Metrics-/Alerting-Pfad |

**Gesamt: Reifegrad 3 — „Definiert".**
Empfohlene Einsatzstufe *heute*: interne Piloten, Demos, App-Prototyping
(API-first, das erklärte Plattformziel). **Gate für Produktion mit externen
Tenants:** O1 + O2 (Isolationstiefe), O3 (gemeinsamer Store), O4/O5
(Billing-Härtung) — damit wäre Grad 4 erreichbar.

---

## 6. Prioritäten

1. **P1 (vor externem Betrieb):** Privilege-Drop im Sandbox-Kind (O1),
   Netzwerk-Isolation als Deployment-Pflicht dokumentiert erzwingen (O2).
2. **P2 (Wachstum):** Budget-Reservierung (O4), Rate-Limits/Key-Rotation (O5),
   gemeinsamer Store für Mehrinstanz (O3).
3. **P3 (Komfort):** Trace-Retention (O6), asynchrone Runs/Streaming (O7),
   tiefere Schema-Validierung (O8).
