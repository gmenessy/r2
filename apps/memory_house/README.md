# Memory House 🏠

App Nr. 4 der BrainFump-Suite — der absolut verrückte Vorschlag:
Das Haus, das sich erinnert. Jeder Raum ist eine Akte, Bewohner-Regeln
sind globale DNA, und vor jedem Automatismus fragt das Haus seinen
Memory Gatekeeper.

## Was das Haus gelernt hat (Demo-Seed)

- 🔥 „Heizung auf 24° um 6:00 → Bad trotzdem kalt" — wird nie wiederholt;
  das Haus schlägt stattdessen vor: Vorlauf 30 Minuten früher.
- ⚡ Die Sicherung im Keller ist fragil — Schalten nur nach Review.
- 🧹 Nie staubsaugen, wenn jemand im Homeoffice-Call ist — kein Merkzettel,
  sondern ein Runtime-Check gegen den Live-Kontext (`call_aktiv`).
- 🚪 Die Haustür wird nachts nie automatisch entriegelt — globale
  Governance, gilt in jedem Raum.
- ☀️ Saisonwechsel sind Evolution Patches: Sommerbetrieb ersetzt
  Winterbetrieb, die Historie bleibt auflösbar.

## Start

```bash
docker compose up memory-house   # → http://localhost:8040
# oder lokal:
python3 apps/memory_house/server.py --port 8040 --data ./data
```

In der UI: Raum wählen, Aktionen klicken, den Call-Schalter umlegen —
und zusehen, wie das Haus zustimmt, warnt, Review verlangt oder blockt.

## API

| Route | Zweck |
|---|---|
| `POST /api/attempt` | `{room, action_type, device?, signature?, context?}` → Gate + executed |
| `POST /api/failures` | Lektion: `{room, content, signature, alternative?}` |
| `POST /api/fragile` | `{room, device, reason}` |
| `POST /api/rules` | `{text, forbidden_action, when?}` — mit `when` kontextabhängig |
| `POST /api/modes` · `/api/season` | Betriebsmodus setzen / per Evolution Patch wechseln |
| `GET /api/rooms` · `/api/room?room=` | Räume / Raumstatus mit Memory & Historie |
