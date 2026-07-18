# Sprint-Plan — Agent Execution Layer, Sprint 3

_Stand: 2026-07-18 · Basis: `claude/agent-execution-layer-bju91q` nach Deep Dive
(Plattform + Flightdeck-Demo, 295 Tests grün, Coverage ~95,5 %, Reifegrad 3)_

> Grundlage ist die Roadmap aus [`docs/DEEP_DIVE_AGENT_LAYER.md`](DEEP_DIVE_AGENT_LAYER.md)
> (offene Punkte O1–O8, Prioritäten P1–P3). Die acht verifizierten Findings
> F1–F8 sind bereits behoben; dieser Sprint schließt die **offenen Grenzen**,
> nicht neue Bugs.

---

> **✅ Durchgeführt (2026-07-18).** Alle fünf Tickets abgeschlossen, 295 →
> **316 Tests** grün, Coverage ~95,8 %, Reifegrad **3 → 4**. Abschlussbericht:
> [`DEEP_DIVE_AGENT_LAYER.md`](DEEP_DIVE_AGENT_LAYER.md) Abschnitt 7. Status je
> Ticket in der Backlog-Tabelle unten.

## 1. Sprint-Ziel (ein Satz)

**Die Plattform von Reifegrad 3 („Definiert") auf Reifegrad 4 („Gemanagt")
in den Dimensionen Sicherheit/Sandboxing und Billing heben — damit der
Betrieb mit *nicht* vollständig vertrauenswürdigen Tenants freigegeben werden
kann.**

Messbares Sprint-Ergebnis:
- Sandbox isoliert auch gegen Privilegien-/Dateisystem-Zugriff, nicht nur
  Ressourcen (O1).
- Kein Tenant kann sein Budget mehr als um Rundungsreste überziehen (O4).
- Missbrauch durch Frequenz oder kompromittierte Keys ist eingegrenzt (O5).
- Traces/Events wachsen nicht mehr unbegrenzt (O6).

---

## 2. Backlog — priorisiert und nach Abhängigkeit sortiert

| Ticket | O-Ref | Prio | Aufwand | Status | Beschreibung |
|--------|:-----:|:----:|:-------:|:------:|--------------|
| **S3-1 · Sandbox Privilege-Drop + Container-Hardening** | O1 | P1 | M | ✅ | Läuft der Server als root, droppt das Sandbox-Kind vor `fn()` auf `nobody` (`setgroups([])`/`setgid`/`setuid`); ist der Server bereits unprivilegiert, bleibt es dabei (No-op, `dropped_privileges` im Report). Compose: `read_only`, `no-new-privileges`, `cap_drop: [ALL]` + `cap_add: [SETUID,SETGID]`, `tmpfs`. |
| **S3-2 · Netz-Isolation als Deployment-Pflicht** | O2 | P1 | S | ✅ | `docker-compose.hardened.yml` (`internal`-Netz, vLLM innen, kein Egress); Betriebsanforderung + Verifikation in `docs/DEPLOY_HARDENING.md`. Socket-Sperre bleibt Defense-in-Depth. |
| **S3-3 · Budget-Reservierung (Pre-Auth + Settlement)** | O4 | P1 | M | ✅ | Vor dem LLM-Call wird der Höchstpreis (`estimate_prompt_tokens` × `max_tokens`) **reserviert** (`reserve()`), danach auf die Ist-Tokens **abgerechnet** (`settle()`), bei Fehler **freigegeben** (`release()`). „Ein Call über Budget"-Fenster geschlossen. |
| **S3-4 · Rate-Limits + Key-Lifecycle** | O5 | P2 | M | ✅ | Token-Bucket pro Tenant → HTTP 429 mit `Retry-After`. Keys mit `ttl_seconds` und `rotate_key()` (neuer Key, alter im Kulanzfenster gültig, keine Budget-Verdopplung); Ablauf im `resolve()`-Pfad geprüft. |
| **S3-5 · Trace/Event-Retention** | O6 | P3 | S | ✅ | `TraceStore.prune(older_than_days, keep_last_n_per_tenant)`; Start-Parameter `--retention-days` / `AGENT_RETENTION_DAYS`; Default aus (keine stille Löschung). |

**Kapazität:** fünf Tickets (2× S, 3× M) — realistisch für einen Sprint,
gemessen an den fünf A-Tickets der Vorsprints. S3-1..S3-3 (alle P1) sind das
Muss-Ergebnis; S3-4/S3-5 sind das Soll.

### Sequenzierung & Abhängigkeiten

```
S3-1 (Sandbox)   ─┐
S3-2 (Netz/Doku) ─┤  unabhängig, parallelisierbar
S3-5 (Retention) ─┘

S3-3 (Reservierung) ──► erweitert BillingLedger
                          │
S3-4 (Rate/Keys)     ────┘  baut auf demselben Ledger auf → nach S3-3
```

S3-3 zuerst im Billing-Pfad (verändert die Buchungsreihenfolge im Runtime),
danach S3-4 (nutzt denselben Ledger, keine Konflikte). Die Sandbox- und
Retention-Tickets sind isoliert und können jederzeit dazwischen laufen.

---

## 3. Definition of Done (pro Ticket)

1. `python3 -m pyflakes brainfump apps` sauber.
2. Regressionstests, die das jeweilige Verhalten **vor** dem Ticket scheitern
   ließen — der Deep-Dive-Standard: erst per Repro belegen, dann als Test
   verankern.
3. `python3 -m pytest --cov` grün, Branch-Coverage-Gate (`fail_under=85`)
   gehalten.
4. Für S3-1 und S3-4 zusätzlich ein Live-Smoke gegen den laufenden Server
   (Privilege-Drop-Pfad bzw. 429-Antwort), wie bei der F1-Verifikation.
5. README/Compose der betroffenen App aktualisiert; bei S3-1/S3-2 die
   Deploy-Checkliste.
6. Am Sprint-Ende: Reifegrad-Messung in `DEEP_DIVE_AGENT_LAYER.md` fortschreiben.

---

## 4. Ausdrücklich NICHT in diesem Sprint (radikaler Realismus)

| Punkt | Warum aufgeschoben |
|-------|--------------------|
| **O3 · Gemeinsamer Store / Horizontale Skalierung** | Ein echter Postgres-/Litestream-Adapter ist **offline nicht testbar** — konsequent wie bei E-4 im Kernel nicht gefaked. Die SQLite-Naht bleibt; das Ticket wartet auf eine testbare Umgebung. Bis dahin gilt: eine Instanz pro Deployment. |
| **O7 · Asynchrone Runs / Streaming** | Wertvoll (lange Runs blockieren je einen Worker-Thread), aber Größe L und kein Sicherheits-/Billing-Gate. Eigener Sprint 4, sobald die Härtung steht. |
| **O8 · Tiefe Schema-Validierung** | Bewusster Lightweight-Trade-off; Pflichtfelder + Basistypen genügen für den aktuellen Tool-Bestand. P3. |
| **Parallele Tool-Calls / ReAct-Erweiterungen** | Funktionalität, kein Reifegrad-Gate. Nach der Härtung. |

---

## 5. Risiken & Gegenmaßnahmen

- **S3-1 in der CI:** Der `setuid`-Pfad lässt sich als root nicht in der
  normalen Test-Umgebung durchlaufen (Tests laufen unprivilegiert). →
  Getestet wird die **Guard-Logik** (kein Drop, wenn nicht root; korrekte
  Reihenfolge setgroups→setgid→setuid), der reale Drop per manuellem
  Root-Container-Smoke, dokumentiert wie der O1/O2-Status.
- **S3-3 Reihenfolge-Umbau:** Die Reservierung ändert die Buchungssequenz im
  Runtime. → Bestehende Billing-Tests (F3/F8) müssen unverändert grün bleiben;
  neue Tests decken Reservierung → Settlement → Gutschrift und Verfall bei
  `budget_exceeded`/`llm_error` ab.
- **Scope-Creep Richtung O3:** Verlockung, „gleich richtig zu skalieren". →
  Explizites Non-Goal (Abschnitt 4); die Naht ist vorbereitet, mehr nicht.

---

## 6. Erwartetes Ergebnis

Nach S3-1..S3-3 (P1) ist das Produktions-Gate aus dem Deep Dive (O1/O2/O4)
geschlossen; mit S3-4 zusätzlich O5. Die Reifegrad-Matrix bewegt sich dann:

| Dimension | heute | Ziel nach Sprint 3 |
|---|:---:|:---:|
| Sicherheit & Sandboxing | 3 | **4** (Privilege-Drop + erzwungene Netz-Isolation) |
| Mandantenfähigkeit & Billing | 3 | **4** (Reservierung, Rate-Limits, Key-Lifecycle) |
| Observability & xAI | 3–4 | **4** (Retention schließt die letzte O6-Lücke) |
| Zuverlässigkeit & Skalierung | 2 | 2 (unverändert — O3/O7 bleiben Sprint 4) |

**Gesamt-Reifegrad danach: 4 („Gemanagt") für den Ein-Instanz-Betrieb mit
externen Tenants.** Der Weg zu Grad 5 (horizontale Skalierung, Streaming)
bleibt Sprint 4 vorbehalten.
