# Demos — Agentic Memory an seinen Grenzen

Drei ausführbare Szenarien, die je eine Fähigkeit des BrainFump-Kernels an
ihre Grenze treiben. Jede Demo führt echte Kernel-Operationen aus und
markiert ehrlich, wo das System **hält** (✔) und wo eine **Grenze** liegt (✘).

```bash
python3 demos/chronos.py     # Evolution Memory / Zeitreise
python3 demos/tribunal.py    # Consolidation / Widerspruchsauflösung
python3 demos/red_team.py    # Sicherheit / Adversarial Poisoning  (gewagt)
```

Keine Abhängigkeiten (reine Stdlib). `NO_COLOR=1` schaltet ANSI-Farben ab.

## 1. Chronos — Zeitreise durchs Projektgedächtnis
Baut die Wissens-Historie einer Frontend-Regel über ein Jahr auf
(Vanilla → Vue-Ausnahme fürs Admin-Backend → Svelte) und reist zu
Stichtagen zurück. **Gefundene Grenzen:** `deprecate` vergisst eine Version
*rückwirkend* (auch historische Stichtage verlieren sie); eine deprecatete
Basis-Kette **verwaist** noch aktive Scope-Ausnahmen (aktiver Store-Stand ≠
auflösbarer Stand); der Gatekeeper hat keine Zeitachse (`check_action` kennt
kein `on_date`).

## 2. Tribunal — drei Agenten, ein Widerspruch
Rechercheur, Architekt und Security schreiben teils widersprüchliche Fakten.
**Was hält:** direkter Negations-Widerspruch (OAuth2 ⇔ kein OAuth2) und
strukturierter Konflikt über `payload` (postgres ⇄ mysql). **Grenzen:**
Sachwidersprüche ohne Negation und umschriebene Widersprüche (täglich vs.
quartalsweise) bleiben unsichtbar; und die Auflösung legt **beide** Karten
still — die wahre Aussage verschwindet mit der falschen, ohne Eskalation.

## 3. Red Team — Adversarial Memory Poisoning  🚩 (die gewagte)
Angreifer und Opfer teilen sich denselben Kernel. Sieben Angriffe, wirklich
ausgeführt und am Ergebnis gemessen (🛡 abgewehrt / ☠ durchgekommen):

| # | Angriff | Ergebnis |
|---|---------|----------|
| 1 | Cross-Case-Leak | 🛡 abgewehrt (Case-Isolation hält) |
| 2 | Failure-Gate via Signatur-Mutation | ☠ exakter Match, kein Fuzzy |
| 3 | Governance-Bypass via Umbenennung | ☠ action_type ist string-exakt |
| 4 | systemweite Sperre via globale DNA | ☠ keine Autorisierung |
| 5 | Wahres Wissen per Widerspruch löschen | ☠ Truth-Suppression |
| 6 | bösartige „Alternative" unterschieben | ☠ ungeprüft weitergereicht |
| 7 | Build-Sabotage via eingeschleuster Korrektur | ☠ Rule-Injection-DoS |

**Bilanz: 1/7 abgewehrt.** Die einzige harte Grenze, die hält, ist die
Case-Isolation. Alle anderen Angriffe teilen dieselbe Wurzel:

> Der Kernel schützt vor **Versehen**, nicht vor **Absicht** — er hat
> Guardrails, aber keinen **Trust-Layer** (Provenienz je Card, Autorisierung
> auf globale DNA, Vertrauensgewichtung im Gatekeeper).

Das ist die stärkste Roadmap-Erkenntnis dieser Demos: der nächste echte
Meilenstein ist kein Feature, sondern **Trust & Provenance**.
