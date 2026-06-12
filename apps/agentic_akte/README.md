# Agentische Akte

App Nr. 1 der BrainFump-Suite: Aktenführung mit Gedächtnis-Gate.
Jede Akte ist ein eigener Memory-Raum; das Event Log ist zugleich die
revisionssichere Aktenhistorie.

## Was es kann

- **Dokumente erfassen**, optional als geschützt markieren → Änderungen
  erzwingen Review (`require_review`-Warnkarte in der UI).
- **Entscheidungen & Fristen**: Fristen bekommen einen Status
  (offen/überfällig) und erscheinen in jeder Gate-Antwort.
- **Verworfene Analysen** werden nie wiederholt: gleiche Signatur →
  Block bzw. Vorschlag der hinterlegten Alternative.
- **Globale DNA**: organisationsweite Governance (z. B. „Dokumente nie
  automatisch löschen") gilt in jeder Akte.
- **Konsolidierung**: Dedupe + Widerspruchserkennung auf Knopfdruck.
- **UI-Warnkarte** (Sprint-2-Deliverable): jede Gatekeeper-Entscheidung
  wird farbcodiert angezeigt (allow/warn/require_review/block/alternative).

## Start

```bash
docker compose up agentic-akte   # → http://localhost:8010
# oder lokal:
python3 apps/agentic_akte/server.py --port 8010 --data ./data
```

## API

| Route | Zweck |
|---|---|
| `POST /api/documents` | `{case, name, summary, fragile?, reason?}` |
| `POST /api/decisions` | `{case, text, scope?}` |
| `POST /api/deadlines` | `{case, description, due_date}` |
| `POST /api/open-points` | `{case, text}` |
| `POST /api/failed-analyses` | `{case, text, signature, alternative?}` |
| `POST /api/check` | `{case, action_type, documents?, error_signature?}` → Gate + überfällige Fristen |
| `POST /api/consolidate` | Offline-Lauf: Dedupe, Widersprüche, Archivierung |
| `GET /api/cases` · `/api/timeline` · `/api/memory` · `/api/deadlines` | Abfragen (`?case=`) |
