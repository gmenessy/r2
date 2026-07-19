# Sprint-Plan 4 & 5 — Agent Execution Layer

_Stand: 2026-07-18 · Basis: `claude/agent-execution-layer-bju91q` nach Sprint 3
(Reifegrad 4, 316 Tests, ~95,8 % Coverage)_

> Übergeordnete Zielsetzung: [`PLATFORM_CHARTER.md`](PLATFORM_CHARTER.md).
> **Jedes Ticket hier respektiert die Leichtgewicht-Budgets (§2 der Charter);
> S4-1 macht diese Budgets zum CI-Gate — es läuft zuerst.**
> Offene Deep-Dive-Punkte adressiert: O3, O7, O8.

---

## Sprint 4 — „Verschlanken & Durchreichen" (Skalierung *nach unten*, Streaming)

> **✅ Durchgeführt (2026-07-19).** Alle fünf Tickets abgeschlossen, 316 →
> **334 Tests** grün, Coverage ~95,9 %, Footprint-Gate aktiv (Kern 2.157 LOC,
> Kaltstart ~86 ms, RSS 24 MiB — alle Budgets gehalten). Async/SSE zusätzlich
> live gegen den laufenden Server verifiziert.

**Sprint-Ziel:** Die Leichtgewichtigkeit gegen Regression absichern und die
letzte Funktionslücke (synchrone Runs, O7) schließen — ohne eine einzige neue
Laufzeit-Abhängigkeit.

| Ticket | O/Charter | Prio | Status | Beschreibung |
|--------|:---------:|:----:|:------:|--------------|
| **S4-1 · Footprint-Gate** | Charter §2 | P1 | ✅ | `scripts/measure_footprint.py` misst Deps/LOC/Kaltstart/RSS/Demo-LOC gegen die Charter-Budgets; CI-Schritt bricht bei Überschreitung ab. RSS über `VmRSS` (nicht `ru_maxrss`), Messung im isolierten Interpreter (`-I -S`) → deterministisch. |
| **S4-2 · Asynchrone Runs (Job-Queue + Polling)** | O7 | P1 | ✅ | `POST /api/run {"async": true}` → `202` + `run_id`; `AsyncRunner` (Stdlib-`ThreadPoolExecutor`, feste Größe) arbeitet ab; `GET /api/runs?run_id=` pollt. Synchroner Pfad bleibt Default. |
| **S4-3 · Token-Streaming (SSE)** | O7 | M | ✅ | `GET /api/runs/stream?run_id=` als Server-Sent-Events (webkit `StreamingResponse`, reine Stdlib): Trace-Schritte live als `step`/`done`-Events. |
| **S4-4 · Verschachtelte Schema-Validierung** | O8 | P2 | ✅ | `validate_args` prüft `object`/`array` rekursiv über `properties`/`items` (Stdlib), mit `additionalProperties`-Respekt und Pfad-genauen Fehlern. |
| **S4-5 · Backpressure & Queue-Limits** | O7/Charter | P2 | ✅ | In-Flight je Tenant gedeckelt → `429`; global überlastet → `503`; je mit `Retry-After`. |

**Sequenzierung (umgesetzt):** S4-1 → S4-2/S4-5 (gemeinsam) → S4-3 → S4-4.

**Charter-Check (erfüllt):** Alles Stdlib (`concurrent.futures`, SSE über
`http.server`). LOC-Zuwachs 1.945 → 2.157 (+212) — deutlich unter dem
2.600-Budget. Kein neuer Dienst, keine neue Abhängigkeit.

---

## Sprint 5 — „Horizontal, aber leicht" (Skalierung *nach außen* via Sharding)

> **✅ Durchgeführt (2026-07-19).** Alle fünf Tickets abgeschlossen, 334 →
> **348 Tests** grün, Coverage ~95,7 %, Footprint-Gate gehalten (Kern 2.373
> LOC / 0 Deps / ~83 ms Kaltstart / 24 MiB RSS). Zwei-Shard-Routing und
> Metrics live gegen laufende Instanzen verifiziert.

**Sprint-Ziel:** Mehrinstanz-Betrieb ohne geteilten DB-State und ohne die
Charter zu brechen — über **Tenant-Sharding** statt einer verteilten Datenbank.

| Ticket | O/Charter | Prio | Status | Beschreibung |
|--------|:---------:|:----:|:------:|--------------|
| **S5-1 · Tenant-Router** | O3 | P1 | ✅ | `ShardManifest.owner_index = sha256(tenant) % N` (prozess-stabil, NICHT `hash()`). Jede Instanz (`--shard-index`/`--shard-total`) besitzt exklusiv ihre Tenants; eine fremde Anfrage wird mit `421` + Ziel-Shard abgewiesen — kein geteilter State. |
| **S5-2 · Shard-Manifest** | O3 | P2 | ✅ | `GET /api/shards` (Manifest Index→URL + eigener Index + Drain-Zustand), deklarativ, kein Service-Mesh. |
| **S5-3 · Persistenz-Adapter-Naht** | O3/Charter §4 | P2 | ✅ | `backends.py`: `LedgerBackend`/`TraceBackend` als `runtime_checkable Protocol` — die SQLite-Stores erfüllen den Vertrag strukturell (Contract-Test). Postgres bleibt **optionale Naht**, nicht im Kern, offline nicht gefaked. |
| **S5-4 · Graceful Drain** | O3 | P2 | ✅ | `POST /api/admin/drain` (Admin) → neue Runs `503`, Lese-Endpunkte offen; async/laufende Runs beenden. Rebalancing ohne Datenverlust. |
| **S5-5 · Metrics-Endpoint** | Betrieb | P3 | ✅ | `GET /api/metrics` (Prometheus-Textformat, Stdlib): Runs je Status, In-Flight, Kosten/Reservierung, aktive Tenants, Shard-Index. |

**Sequenzierung (umgesetzt):** S5-1 → S5-2 → S5-4 → S5-5 → S5-3.

**Charter-Check:** Sharding ist die **leichtgewichtige** Antwort auf O3 — es
fügt keine verteilte DB hinzu, sondern nutzt die vorhandene
Ein-Prozess-Stärke N-fach. Postgres bleibt strikt optional (§4). Ein neuer
optionaler Extra ist zulässig (Charter §3.1: optional, hinter Naht).

**Definition of Done:** wie Sprint 4 **plus**: Sharding-E2E mit ≥ 2 Instanzen
im Test (zwei `create_server` auf verschiedenen Ports, Router davor),
Footprint-Gate weiter grün (Kern wächst nur um den Router, nicht um Postgres).

---

## Endzustand nach Sprint 5 (erreicht)

| Dimension | Nach Sprint 3 | Nach Sprint 5 |
|---|:---:|:---:|
| Funktionalität (inkl. async/streaming) | 3 | **4** (S4-2/S4-3) |
| Sicherheit & Sandboxing | 4 | 4 |
| Mandantenfähigkeit & Billing | 4 | 4 |
| Observability & xAI (+ Metrics) | 4 | **4–5** (S5-5) |
| Zuverlässigkeit & Skalierung | 2 | **4** (Sharding, Drain — S5-1/S5-4) |
| Testbarkeit & CI (+ Footprint-Gate) | 4 | **4–5** (S4-1) |
| Leichtgewichtigkeit (neue Dimension) | — | **Gate grün, Budgets gehalten** |

**Erreicht:** Reifegrad **4 durchgängig**, Skalierung über Sharding gelöst —
bei gehaltenem Leichtgewicht-Budget (0 Pflicht-Abhängigkeiten, Kaltstart
~83 ms, RSS 24 MiB, Kern 2.373/2.600 LOC). Das ist die in Charter §6
definierte „fertig für externe Produktion"-Marke.

---

## Was auch nach Sprint 5 bewusst offen bleibt

- Verteilter, geteilter State (eine globale DB über alle Shards) — durch
  Sharding vermieden, nicht gelöst; nur nötig, wenn Tenants instanzübergreifend
  Budget teilen müssten (kein bekannter Bedarf).
- Eigenes Modell-/Embedding-Hosting — dauerhaftes Non-Goal (Charter §4).
- Ein Produkt-Frontend — das Flightdeck bleibt Demo.
