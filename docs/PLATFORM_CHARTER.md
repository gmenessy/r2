# Platform Charter — Agent Execution Layer

_Stand: 2026-07-18 · verbindliche Zielsetzung für Sprint 4+_

Diese Charter legt fest, **was die Plattform ist und was sie bewusst nicht
wird**. Sie ist der Maßstab, an dem jedes künftige Ticket gemessen wird —
insbesondere gegen Feature-Druck.

---

## 1. Mission

**Eine ultraleichtgewichtige Ausführungsschicht, auf der API-first-Agenten-Apps
in Stunden statt Wochen entstehen — die schwere Arbeit (LLM-Inferenz) ist
ausgelagert, die Plattform selbst bleibt klein genug, um sie vollständig zu
verstehen.**

Kern-Wertversprechen (aus README/Deep-Dive, jetzt verbindlich):
- **App-Bau in ~60 Zeilen**: Tools mit Schema + Policy registrieren, `run()`.
- **Vier Säulen ohne Framework-Ballast**: Sandbox, Memory-Gate, Billing, xAI.
- **CPU genügt**: Die Plattform orchestriert; gemini4-31B läuft extern auf vLLM.

---

## 2. Leichtgewichtigkeit als hartes Ziel (nicht Slogan)

Leichtgewichtigkeit ist das **oberste nicht-funktionale Ziel** und wird
gemessen. Die folgenden Budgets sind ab Sprint 4 ein CI-Gate (Ticket S4-1);
eine Überschreitung ist ein Build-Fehler, kein Diskussionspunkt.

| Metrik | Baseline (2026-07-18) | Budget (Gate) | Messung |
|---|---:|---:|---|
| Externe Runtime-Abhängigkeiten | **0** | **0** — hart | `pyproject.toml dependencies == []` |
| Kern-LOC (`apps/agent_layer/*.py` ohne Tests) | **1.945** | **≤ 2.900**¹ | `scripts/measure_footprint.py` |
| Kaltstart bis „server-ready" | **159 ms** | **≤ 400 ms** | dito |
| RSS im Leerlauf (Import + Stores) | **23 MiB** | **≤ 60 MiB** | dito |
| Demo-App-LOC (Fach-App auf der Plattform) | **347** | **≤ 500** | dito |
| Docker-Image (agent-layer) | `python:3.12-slim` | keine Nicht-Stdlib-Wheels | Dockerfile-Review |

¹ Am 2026-07-19 von 2.600 auf 2.900 angehoben — begründete Ausnahme (siehe
Regel unten) für Path A, die WASM-Sandbox (`wasm_sandbox.py`, ~200 LOC,
~80× schnellere Code-Execution für numerische Tools als der Fork-Pfad).
Details: [`docs/PATH_A_WASM_SANDBOX.md`](PATH_A_WASM_SANDBOX.md). Zählt
weiterhin **nicht** gegen „Externe Runtime-Abhängigkeiten" — `wasmtime` ist
ein optionales, echt lazy geladenes Extra (`pip install .[wasm]`), niemals
Pflicht.

**Regel:** Die Budgets haben ~30 % Kopffreiheit über der Baseline. Wer sie
ausschöpfen will, muss den Gegenwert begründen. Wer sie sprengt, teilt das
Feature auf oder lässt es weg.

---

## 3. Design-Prinzipien (Entscheidungsfilter)

1. **Stdlib-first.** Eine neue Abhängigkeit ist zulässig, wenn sie (a) optional
   ist (die Plattform läuft ohne sie), (b) hinter einer schmalen Naht steckt
   und (c) mehr Komplexität entfernt als hinzufügt. Andernfalls: nein.
2. **Ausgelagerte Schwere.** Alles Rechenintensive (Inferenz, Embeddings)
   gehört hinter eine Naht in einen externen Dienst — nie in die Plattform.
3. **Ein Prozess, bis es weh tut.** Threads + kurzlebige Forks vor verteilten
   Systemen. Verteilung nur, wenn ein reales Limit gemessen (nicht vermutet)
   ist — und dann leichtgewichtig (siehe §5, O3).
4. **Naht statt Fake.** Nicht offline testbare Backends (Postgres, echte
   Embedding-APIs) werden nicht gefaked, sondern hinter eine Naht gelegt und
   als offenes Ticket geführt (radikaler Realismus, wie im Kernel etabliert).
5. **Testbarkeit ist Teil des Features.** Kein Deliverable ohne offline-E2E über
   den `SimulatedLLM`; jedes Finding/Ticket als Regressionstest.

---

## 4. Explizite Non-Goals (was die Plattform NICHT wird)

- **Kein eigener Inferenz-Server, kein Modell-Hosting.** vLLM bleibt extern.
- **Keine schwergewichtige Framework-Schicht** (kein Web-Framework, kein ORM,
  keine Task-Queue-Engine als Abhängigkeit).
- **Kein eingebautes Vektor-DB-System.** Der Embedding-/ANN-Pfad bleibt ein
  injizierbarer Slot; ein echter Vektorindex ist Sache eines externen Dienstes.
- **Keine UI-Plattform.** Das Flightdeck ist eine Demo, kein Produkt-Frontend.
- **Kein Multi-Cloud-Orchestrator.** Deployment ist Docker/Compose; darüber
  hinaus ist es Sache des Betreibers.

---

## 5. Roadmap-Leitplanken für die offenen Punkte

Die Deep-Dive-Punkte O3/O7/O8 werden in Sprint 4/5 **nur so** angegangen, dass
§2 gewahrt bleibt:

- **O7 Streaming/Async** (Sprint 4): passt zur Charter — reine Stdlib
  (`http.server` + Generatoren), kein neuer Dienst.
- **O3 Skalierung** (Sprint 5): **nicht** über eine schwere verteilte DB,
  sondern **Tenant-Sharding** — jede Instanz besitzt exklusiv eine
  Tenant-Menge (kein geteilter Budget-State, keine Races). Ein optionaler
  Postgres-Adapter bleibt eine Naht, kein Muss.
- **O8 Schema-Tiefe** (Sprint 4, klein): verschachtelte Validierung in Stdlib,
  ohne JSON-Schema-Abhängigkeit.

---

## 6. Definition of „fertig für externe Produktion"

Erreicht, wenn: Reifegrad ≥ 4 in allen Dimensionen **außer** bewusst
aufgeschobenen; Leichtgewicht-Budgets (§2) grün; Deploy-Härtung
(`DEPLOY_HARDENING.md`) aktiv; ein dokumentierter Skalierungspfad (Sharding)
existiert. Ziel: **Ende Sprint 5.**
