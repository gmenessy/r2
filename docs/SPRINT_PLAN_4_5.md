# Sprint-Plan 4 & 5 — Agent Execution Layer

_Stand: 2026-07-18 · Basis: `claude/agent-execution-layer-bju91q` nach Sprint 3
(Reifegrad 4, 316 Tests, ~95,8 % Coverage)_

> Übergeordnete Zielsetzung: [`PLATFORM_CHARTER.md`](PLATFORM_CHARTER.md).
> **Jedes Ticket hier respektiert die Leichtgewicht-Budgets (§2 der Charter);
> S4-1 macht diese Budgets zum CI-Gate — es läuft zuerst.**
> Offene Deep-Dive-Punkte adressiert: O3, O7, O8.

---

## Sprint 4 — „Verschlanken & Durchreichen" (Skalierung *nach unten*, Streaming)

**Sprint-Ziel:** Die Leichtgewichtigkeit gegen Regression absichern und die
letzte Funktionslücke (synchrone Runs, O7) schließen — ohne eine einzige neue
Laufzeit-Abhängigkeit.

| Ticket | O/Charter | Prio | Aufwand | Beschreibung |
|--------|:---------:|:----:|:-------:|--------------|
| **S4-1 · Footprint-Gate** | Charter §2 | P1 | S | `scripts/measure_footprint.py` misst LOC, Kaltstart, RSS, Demo-LOC, Dependency-Zahl; CI-Job bricht bei Budgetüberschreitung ab. Verankert Leichtgewichtigkeit dauerhaft. |
| **S4-2 · Asynchrone Runs (Job-Queue + Polling)** | O7 | P1 | L | `POST /api/run` akzeptiert `{"async": true}` → sofort `run_id` + `status: queued`; ein Stdlib-`ThreadPoolExecutor` (feste, kleine Größe) arbeitet ab; `GET /api/runs/{id}` pollt Status/Ergebnis. Synchroner Pfad bleibt Default. Lange Runs binden keinen Request-Thread mehr. |
| **S4-3 · Token-Streaming (SSE)** | O7 | M | M | `GET /api/runs/{id}/stream` als Server-Sent-Events (reine Stdlib): Trace-Schritte werden live gepusht, sobald sie im Store landen. Nutzt die vorhandene Trace-Schritt-Sequenz — kein neuer Zustand. |
| **S4-4 · Verschachtelte Schema-Validierung** | O8 | P2 | S | `validate_args` versteht `object`/`array`-`items`/`properties` rekursiv (Stdlib, kein `jsonschema`). Fängt strukturell falsche Tool-Argumente vor der Sandbox. |
| **S4-5 · Backpressure & Queue-Limits** | O7/Charter | P2 | S | Queue-Tiefe je Tenant gedeckelt (`429`, wiederverwendet aus S3-4); voller Executor → `503` mit `Retry-After`. Schützt den kleinen Prozess vor Überlast statt unbegrenzt zu puffern. |

**Sequenzierung:** S4-1 zuerst (Gate steht, bevor L-Tickets Code hinzufügen) →
S4-2 (Fundament) → S4-3/S4-5 (bauen auf der Queue auf) → S4-4 (isoliert).

**Charter-Check:** Alle Tickets sind Stdlib (`concurrent.futures`,
`http.server`-SSE). Erwarteter LOC-Zuwachs ~350–450 → bleibt unter dem
2.600-Budget. Kein neuer Dienst, keine neue Abhängigkeit.

**Definition of Done:** wie Sprint 3 (Repro-vor-Test, offline-E2E über
SimulatedLLM, Live-Smoke für S4-2/S4-3, Coverage-Gate) **plus**: S4-1-Gate grün.

---

## Sprint 5 — „Horizontal, aber leicht" (Skalierung *nach außen* via Sharding)

**Sprint-Ziel:** Mehrinstanz-Betrieb ohne geteilten DB-State und ohne die
Charter zu brechen — über **Tenant-Sharding** statt einer verteilten Datenbank.
Damit erreicht die Skalierungs-Dimension Grad 3–4, ohne Stdlib-Only aufzugeben.

| Ticket | O/Charter | Prio | Aufwand | Beschreibung |
|--------|:---------:|:----:|:-------:|--------------|
| **S5-1 · Tenant-Router** | O3 | P1 | M | Deterministisches Sharding (`hash(tenant) % N`) auf eine feste Instanzmenge. Jede Instanz besitzt exklusiv ihre Tenants → kein geteilter Budget-/Rate-State, keine Races. Router als schlanke Stdlib-Komponente + `X-Shard`-Header/Redirect. |
| **S5-2 · Shard-Manifest & Health-Aggregation** | O3 | P2 | S | `GET /api/shards` (Manifest: welche Instanz hält welche Tenants) und aggregierte Health; Router liest daraus. Deklarativ (JSON/Env), kein Service-Mesh. |
| **S5-3 · Persistenz-Adapter-Naht (optional Postgres)** | O3/Charter §4 | P2 | M | Store-Interface (`KeyValue`/`Ledger`) explizit als Protocol; SQLite bleibt Default. Ein Postgres-Adapter wird als **optionales Extra** (`pip install .[postgres]`) skizziert — nicht in den Kern gezogen, offline nicht getestet (Naht + Vertrag + Contract-Test-Skelett). |
| **S5-4 · Graceful Drain & Rebalance** | O3 | P2 | M | Eine Instanz kann Tenants „entladen" (neue Runs abweisen mit `503`+Ziel, laufende zu Ende führen), damit Sharding-Änderungen ohne Datenverlust möglich sind. Reservierungen/Traces bleiben instanzlokal konsistent. |
| **S5-5 · Metrics-Endpoint** | Betrieb | P3 | S | `GET /api/metrics` (Prometheus-Textformat, Stdlib): Runs, Latenz-Histogramm, Queue-Tiefe, Budget-Auslastung je Shard. Schließt die letzte Betriebs-Lücke aus dem Deep-Dive. |

**Sequenzierung:** S5-1 (Kern) → S5-2 (macht Sharding betreibbar) → S5-4
(sicheres Rebalancing) → S5-3 (Naht, parallel möglich) → S5-5 (isoliert).

**Charter-Check:** Sharding ist die **leichtgewichtige** Antwort auf O3 — es
fügt keine verteilte DB hinzu, sondern nutzt die vorhandene
Ein-Prozess-Stärke N-fach. Postgres bleibt strikt optional (§4). Ein neuer
optionaler Extra ist zulässig (Charter §3.1: optional, hinter Naht).

**Definition of Done:** wie Sprint 4 **plus**: Sharding-E2E mit ≥ 2 Instanzen
im Test (zwei `create_server` auf verschiedenen Ports, Router davor),
Footprint-Gate weiter grün (Kern wächst nur um den Router, nicht um Postgres).

---

## Erwarteter Endzustand nach Sprint 5

| Dimension | Nach Sprint 3 | Ziel nach Sprint 5 |
|---|:---:|:---:|
| Funktionalität (inkl. async/streaming) | 3 | **4** (S4-2/S4-3) |
| Sicherheit & Sandboxing | 4 | 4 |
| Mandantenfähigkeit & Billing | 4 | 4 |
| Observability & xAI (+ Metrics) | 4 | **4–5** (S5-5) |
| Zuverlässigkeit & Skalierung | 2 | **4** (Sharding, Drain — S5-1/S5-4) |
| Testbarkeit & CI (+ Footprint-Gate) | 4 | **4–5** (S4-1) |
| Leichtgewichtigkeit (neue Dimension) | — | **Gate grün, Budgets gehalten** |

**Ziel:** Reifegrad **4 durchgängig**, Skalierung gelöst — und das bei
gehaltenem Leichtgewicht-Budget (0 Pflicht-Abhängigkeiten, Kaltstart < 400 ms).
Das ist die in der Charter §6 definierte „fertig für externe Produktion"-Marke.

---

## Was auch nach Sprint 5 bewusst offen bleibt

- Verteilter, geteilter State (eine globale DB über alle Shards) — durch
  Sharding vermieden, nicht gelöst; nur nötig, wenn Tenants instanzübergreifend
  Budget teilen müssten (kein bekannter Bedarf).
- Eigenes Modell-/Embedding-Hosting — dauerhaftes Non-Goal (Charter §4).
- Ein Produkt-Frontend — das Flightdeck bleibt Demo.
