# Path A — WASM-Sandbox für Code-Execution

_Stand: 2026-07-19 · Umsetzung von „Path A" aus der Leichtgewicht-Analyse
(siehe Gesprächsverlauf): Python bleibt der Stack, `wasmtime` ergänzt die
Fork-Sandbox als zweite, viel schnellere Engine für numerische Tools._

## 1. Warum

Die Deep-Dive-Analyse der Plattform-Achsen „Speed / Memory / Scaling / Code
Execution / Deepness" identifizierte die Fork-Sandbox
(`apps/agent_layer/sandbox.py`) als größten Hebel für die Achse **Code
Execution**: Jeder Tool-Aufruf dupliziert einen kompletten Python-Prozess
(`fork()`), startet effektiv einen Interpreter neu und kostet dadurch
Millisekunden — und die Isolation beruht auf rlimits plus einem
Socket-Monkeypatch (Deep-Dive-Finding O2: von entschlossenem Code umgehbar).

WebAssembly via [wasmtime](https://wasmtime.dev/) adressiert beides:
Instanziierung eines vorkompilierten Moduls kostet Mikrosekunden statt
Millisekunden, und ohne verlinkte Imports hat ein WASM-Modul **strukturell**
keine Ambient Authority — kein Dateisystem, kein Netzwerk, keine Syscalls,
unabhängig davon, ob eine rlimit- oder Monkeypatch-Regel vergessen wurde.

## 2. Empirisch gemessene Zahlen

Vor der Implementierung mit einem echten End-to-End-PoC verifiziert (nicht
angenommen — radikaler Realismus):

| Messung | Wert |
|---|---|
| WASM instantiate+call, Modul vorkompiliert (500x) | **Median 73,7 µs**, p95 117,3 µs |
| Fork-Sandbox, bestehende Plattform (200x) | Median 5.939,5 µs (5,9 ms), p95 8.140,8 µs |
| **Faktor** | **~80× schneller** |
| WASM ohne Modul-Cache (wat2wasm + compile + call, 200x) | Median 824 µs (immer noch ~7× schneller als Fork) |

Gemessen mit `python3 -m timeit`-äquivalenten Mikrobenchmarks gegen ein
triviales `(i64,i64)->i64`-Additions-Modul, siehe Herleitung im
Gesprächsverlauf dieser Session — reproduzierbar über
`apps/agent_layer/test_wasm_sandbox.py`.

## 3. Sicherheitsmodell — drei unabhängige Grenzen

| Ressource | Mechanismus | Verhalten bei Überschreitung |
|---|---|---|
| **CPU** | Fuel-Metering (`Store.set_fuel`) — jede WASM-Instruktion verbraucht Fuel | Deterministischer Trap (`TrapCode.OUT_OF_FUEL`), unabhängig von Wanduhr-Timing |
| **Wanduhr** | Epoch-Interruption — ein Hintergrund-Thread inkrementiert die Engine-Epoche nach `wall_timeout_s` | Trap (`TrapCode.INTERRUPT`), selbst wenn noch Fuel übrig ist (schützt gegen fuel-günstige, aber pathologisch lange Schleifen) |
| **Speicher** | `Store.set_limits(memory_size=…)` | Scheitert bereits bei der Instanziierung, kein laufender Prozess zu terminieren |

**Ambient Authority:** Der `Linker` verlinkt keine Imports — kein WASI, kein
Dateisystem-Stub, kein Netzwerk-Stub. Ein Modul kann buchstäblich keinen
Syscall ausführen, weil keine Host-Funktion dafür verfügbar gemacht wird.
Das ist eine stärkere Garantie als die Fork-Sandbox, deren Isolation von
korrekt gepflegten rlimits und einem zur Laufzeit patchbaren `socket`-Modul
abhängt (Deep-Dive O2).

Alle drei Mechanismen wurden vor der Implementierung mit einem hängenden
WAT-Testmodul (`(loop $c (br $c))`) einzeln verifiziert: Fuel-Erschöpfung,
Wanduhr-Timeout und Speicher-Limit lösen jeweils den erwarteten, strukturiert
unterscheidbaren Trap-Code aus (nicht String-Matching auf Fehlermeldungen).

## 4. ABI — bewusst denkbar einfach

Ein WASM-Tool exportiert eine Funktion `run` mit einer **typisierten**
Signatur, 1:1 aus dem flachen JSON-Schema des Tools abgeleitet:

| JSON-Schema-Typ | WASM-Typ |
|---|---|
| `integer` | `i64` |
| `number` | `f64` |
| `boolean` | `i32` |

Reihenfolge = Deklarationsreihenfolge in `properties`. Der Host
(`apps/agent_layer/wasm_sandbox.py`) übernimmt das komplette JSON-Marshalling
zwischen Tool-Aufruf und WASM-Funktionssignatur; das Modul selbst bekommt nie
JSON zu Gesicht und muss keinen Parser mitbringen. Das deckt eine reale,
nützliche Klasse von Tools ab — Arithmetik, Schwellwert-Prüfungen,
Validierungslogik, Scoring-Funktionen — bewusst **nicht** String-/Objekt-
lastige Tools; dafür bleibt die Python-Fork-Sandbox die richtige Wahl.

**Warum kein WASI-Stdio-ABI?** WASI (`fd_read`/`fd_write` für stdin/stdout)
wäre der "universellere" Standard-Ansatz, hätte aber zwei Nachteile: (a) es
bräuchte einen JSON-Parser *innerhalb* des WASM-Moduls, was Test-Fixtures in
reinem WAT unhandhabbar macht, und (b) es eröffnet eine (wenn auch virtualisierte)
Stdio-Oberfläche — die typisierte Signatur hat gar keine I/O-Oberfläche zu
virtualisieren.

## 5. Registrierung

```python
from apps.agent_layer.tools import ToolRegistry
from apps.agent_layer.wasm_sandbox import WasmSandboxPolicy

registry = ToolRegistry()
registry.register_wasm(
    "severity_score",
    "Compute an incident severity score from latency and error rate.",
    {"type": "object", "properties": {
        "latency_ms": {"type": "integer"}, "error_rate_permille": {"type": "integer"},
    }},
    wasm_source=SEVERITY_WAT,             # WAT-Text ODER kompilierte .wasm-Bytes
    wasm_policy=WasmSandboxPolicy(fuel=50_000, wall_timeout_s=0.5),
)
```

`wasm_source` akzeptiert entweder rohen WAT-Text (kompiliert beim ersten
Aufruf, cachefähig, kein externes Toolchain nötig außer `wasmtime` selbst —
genutzt für alle Test-/Demo-Module dieser Plattform) oder bereits kompilierte
`.wasm`-Bytes (für mit Rust/C/Zig/TinyGo gebaute Module).

## 6. Optionales Extra — echt lazy, nicht nur "graceful"

```bash
pip install .[wasm]   # aktiviert wasmtime
```

Ohne das Extra läuft die Plattform unverändert weiter — `wasm_available()`
liefert `False`, ein registriertes WASM-Tool meldet beim Aufruf eine klare
`WasmUnavailableError` statt die Plattform zum Absturz zu bringen, der Run
selbst läuft weiter (das Modell erfährt den Fehler wie bei jedem anderen
Tool-Fehler auch).

Wichtig: der `import wasmtime`-Versuch ist **lazy** — er passiert erst beim
tatsächlichen ersten Bedarf (`wasm_available()` oder `WasmSandbox()`), nicht
beim bloßen Import von `apps.agent_layer.tools` (das praktisch jede App auf
der Plattform transitiv importiert). Eine frühere Version importierte
`wasmtime` unbedingt auf Modulebene in `wasm_sandbox.py` — das hätte
`wasmtime` in *jedem* Prozess geladen, der die Plattform importiert, auch
wenn nie ein WASM-Tool registriert wird. Das ist genau der Unterschied
zwischen „optional, aber trotzdem immer geladen" und „wirklich optional".

## 7. Footprint-Auswirkung (Charter §2)

`core_loc`-Budget am 2026-07-19 bewusst von 2.600 auf **2.900** angehoben
(Charter-Eskalationsklausel „wer das Budget ausschöpft, muss den Gegenwert
begründen") — `wasm_sandbox.py` selbst ist ~200 dichte, funktionale Zeilen
(sechs unterscheidbare Fehlerpfade, Fuel/Epoch/Memory-Handling, Thread-
sicheres Caching), plus Integration in `tools.py`/`runtime.py`.
Kaltstart und RSS bleiben **unverändert**, weil der Footprint-Messprozess
isoliert läuft (`-I -S`) und `wasmtime` — dank Punkt 6 — ohnehin nie ungefragt
importiert wird.

## 8. Ehrlicher Scope — was Path A NICHT umfasst

Die ursprüngliche Analyse nannte für Path A auch einen asyncio-basierten
HTTP-Layer als zweiten Hebel für die Scaling-Achse. Das wurde **bewusst
zurückgestellt**: `brainfump/webkit.py` ist von acht Apps im Monorepo geteilt
(alle BrainFump-Kernel-Apps plus alle drei Agent-Layer-Apps); ein Umbau des
Transport-Layers auf asyncio hätte einen Blast-Radius weit über die
Agent-Execution-Layer-Plattform hinaus und würde die restlichen, unrelated
365 Tests der anderen Apps mitgefährden. Das ist unverhältnismäßig zum
angefragten Scope. Eine kleinere, risikoärmere Verbesserung für dieselbe
Achse — reduzierte Thread-Stack-Größe für `ThreadingHTTPServer` — bleibt ein
offenes, klar abgegrenztes Folge-Ticket.
