# Memory Cockpit 🧭 — die „One More Thing"-App

Ein agentisches Gedächtnis ist normalerweise **unsichtbar** — es wirkt im
Hintergrund, entscheidet mit, aber niemand *sieht* es. Das Cockpit dreht das
um: Es macht den kompletten Cognitive Core in einem Fenster begreifbar.

```bash
docker compose up memory-cockpit    # → http://localhost:8050
# oder lokal:
python3 apps/memory_cockpit/server.py --port 8050
```

## Was man sieht (und tut)

1. **Interaktiver Wissensgraph.** Jede Memory Card ist ein Knoten (Farbe = Typ,
   **Größe = Vertrauen/Provenienz**), jede Beziehung eine typisierte Kante:
   `ersetzt` (Evolution), `widerspricht` (mit bestätigtem Gewinner), `Ausnahme`,
   `hängt ab von`. Ausgegraute Knoten sind superseded/contradicted — Historie
   bleibt sichtbar.
2. **Klick auf einen Knoten** → Aussage, Typ, Vertrauen, Gültigkeit und das
   **Beziehungsnetz mit Provenienz** (das „Warum" aus `kernel.explain`).
3. **Live-Gatekeeper.** Man beschreibt eine geplante Aktion und sieht sofort
   das Urteil: `allow / warn / require_review / suggest_alternative / block` —
   inklusive Begründung. Der geseedete Demo-Fall zeigt: ein gescheiterter Fix
   schlägt die gelernte Alternative vor, `drop_prod_tables` wird über die
   Intent-Ontologie geblockt, `payment.py` erzwingt Review.

Ein Fenster für **jede** Kernel-Fähigkeit: Event Log, Evolution/Versionierung,
Memory Graph, Trust/Provenienz, Widerspruchsauflösung, Wiki und Pre-Action-Gate.

## Warum BrainFump als Framework dafür geeignet ist

Das Cockpit war **in einem Nachmittag** baubar, weil der Kernel genau die
Primitive schon liefert, die eine solche Anwendung braucht:

| Was das Cockpit braucht | Was der Kernel out-of-the-box gibt |
|---|---|
| Knoten mit Typ/Zustand/Vertrauen | typisierte, versionierte **Memory Cards** mit `trust`-Feld |
| Kanten (Beziehungen) | **`MemoryGraph`** (E-2): supersedes/contradicts/depends_on, persistent |
| „Warum ist das so?" | **`kernel.explain()`** traversiert die Kanten menschenlesbar |
| Live-Entscheidung | **`MemoryGatekeeper`** als fertige Pre-Action-Instanz |
| Herkunft/Glaubwürdigkeit | **`TrustPolicy`** stempelt Provenienz je Karte |
| Lesbare Zusammenfassung | **`WikiProjection`** (E-3) |
| Kein Framework-Zoo im Frontend | **Stdlib-`webkit`** + Vanilla JS, null externe Abhängigkeiten |
| Mandanten/Isolation | **`TenantManager`** (E-4), falls mehrere Nutzer |

Der entscheidende Punkt: BrainFump ist **kein reiner Vektor-Store**, sondern
ein *Kontroll- und Beziehungssystem*. Genau deshalb entsteht hier eine App,
die nicht nur „ähnliche Texte findet", sondern **Struktur, Herkunft, Konflikte
und Konsequenzen** zeigt — die Dinge, die eine Vektor-DB gerade nicht kann.
Das Cockpit ist der sichtbare Beweis der Leitthese: *Memory ist nicht nur
Kontext — Memory ist ein Kontroll- und Lernsystem.*

## API

| Route | Zweck |
|---|---|
| `GET /api/graph?case=` | Knoten + Kanten des Wissensgraphen |
| `GET /api/card?id=` | Karte + Beziehungen/Provenienz |
| `GET /api/wiki?case=` | Markdown-Projektion |
| `GET /api/timeline?case=` | Ereignis-Verlauf |
| `POST /api/simulate` | `{case, action_type, files?, error_signature?, context?}` → Gate-Urteil |
