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
ausgeführt und am Ergebnis gemessen (🛡 abgewehrt / ☠ durchgekommen). Diese
Demo lief den **Trust-Layer** (`brainfump/trust.py`) erst als offene Lücke —
inzwischen ist er implementiert und hier aktiv (`angreifer`-Trust = 0.1):

| # | Angriff | ohne Trust | Trust + Matching |
|---|---------|-----------|------------------|
| 1 | Cross-Case-Leak | 🛡 | 🛡 Case-Isolation |
| 2 | Failure-Gate via Signatur-Mutation | ☠ | 🛡 Signatur-Normalisierung |
| 3 | Governance-Bypass via Umbenennung | ☠ | 🛡 Intent-Ontologie |
| 4 | systemweite Sperre via globale DNA | ☠ | 🛡 Autorisierung ≥0.9 |
| 5 | Wahres Wissen per Widerspruch löschen | ☠ | 🛡 trust-gewichtet |
| 6 | bösartige „Alternative" unterschieben | ☠ | 🛡 zurückgehalten <0.7 |
| 7 | Build-Sabotage via Korrektur | ☠ | 🛡 nicht erzwungen <0.7 |

**Bilanz: 1/7 → 7/7 abgewehrt.** Zwei Schichten greifen ineinander: der
**Trust-Layer** kippt die vier Poisoning-Angriffe aus blindem Schreiber-
Vertrauen (4–7); robusteres **Matching** fängt die zwei Umgehungen —
Signaturen werden normalisiert verglichen (2), und Governance-Karten drücken
*Absicht* aus (`forbidden_intents: [{"verb":"destroy","resource":"prod"}]`),
sodass sogar das neue Synonym `eliminate_prod_records` blockiert wird (3).

> **Ehrlicher Vorbehalt:** Das Intent-Matching ist eine geteilte **Ontologie**
> (Verb-/Ressourcen-Synonymklassen an *einer* Stelle statt Regex je Karte),
> kein neuronales Modell — ein Verb außerhalb der Klassen (`vaporize`) braucht
> einen Eintrag. Die `IntentMatcher.matches()`-Schnittstelle ist aber so
> geschnitten, dass ein echtes Embedding-Modell dieselbe Rolle übernimmt.

> Kernidee: Vertrauen **und** Bedeutung sind jetzt First-Class — Provenienz je
> Memory Card, Autorisierung auf globale DNA & Regeln, trust-gewichtete
> Widerspruchsauflösung, ein Gatekeeper der untrusted Alternativen zurückhält,
> normalisierte Signaturen und intent-basierte (ontologische) Governance.
