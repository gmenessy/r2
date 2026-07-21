"""WASM-Sandbox (Path A) — Code-Execution ohne Fork, in Mikrosekunden.

Zweiter Engine neben der Fork-Sandbox (:mod:`apps.agent_layer.sandbox`), für
reine numerische Tools: ~80x schneller (Mikro- statt Millisekunden), echtes
Capability-Sandboxing (kein Import verlinkt → keine Ambient Authority, anders
als die rlimit-/Monkeypatch-basierte Fork-Sandbox). CPU-Limit via Fuel,
Wanduhr-Limit via Epoch-Interruption, Speicher-Limit bei der Instanziierung.

ABI: ein WASM-Tool exportiert ``run`` mit typisierter Signatur, 1:1 aus dem
flachen JSON-Schema abgeleitet (``integer``→i64, ``number``→f64,
``boolean``→i32). Der Host erledigt das JSON-Marshalling, das Modul rechnet
nur — kein JSON-Parsing in WASM nötig.

Optionales Extra (``pip install .[wasm]``) — ohne ``wasmtime`` läuft die
Plattform unverändert weiter. Vollständige Herleitung, Sicherheitsmodell und
Benchmarks: ``docs/PATH_A_WASM_SANDBOX.md``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

_wasmtime: Any = None  # echt lazy: erst importiert, wenn tatsächlich gebraucht


class WasmUnavailableError(Exception):
    """``wasmtime`` ist nicht installiert — optionales Extra: ``pip install .[wasm]``."""


def _load_wasmtime() -> Any:
    """Importiert ``wasmtime`` beim ersten tatsächlichen Bedarf, nicht beim
    Modul-Import — ``wasm_sandbox.py`` wird transitiv von ``tools.py`` überall
    importiert; ein Modul-Level-Import würde ``wasmtime`` in praktisch jedem
    Testprozess laden, selbst wenn nie ein WASM-Tool registriert wird."""
    global _wasmtime
    if _wasmtime is None:
        try:
            import wasmtime as wt
        except ImportError as exc:
            raise WasmUnavailableError(
                "wasmtime is not installed — install the optional extra: pip install .[wasm]"
            ) from exc
        _wasmtime = wt
    return _wasmtime


def wasm_available() -> bool:
    try:
        _load_wasmtime()
        return True
    except WasmUnavailableError:
        return False


_WASM_TYPES = {"integer": "i64", "number": "f64", "boolean": "i32"}


@dataclass(frozen=True)
class WasmSandboxPolicy:
    """Ressourcen-Budget eines einzelnen WASM-Tool-Aufrufs."""

    wall_timeout_s: float = 2.0
    fuel: int = 5_000_000
    max_memory_bytes: int = 1 * 1024 * 1024


@dataclass(frozen=True)
class WasmSandboxResult:
    ok: bool
    value: Any = None
    error: str | None = None
    # ok | error | fuel_exhausted | timeout | memory_exceeded | compile_error
    exit_reason: str = "ok"
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "value": self.value,
            "error": self.error,
            "exit_reason": self.exit_reason,
            "duration_ms": round(self.duration_ms, 3),
            "engine": "wasm",
        }


def _param_types(param_schema: dict[str, Any]) -> list[tuple[str, str]]:
    """(Name, WASM-Typ) je deklariertem Property, in Deklarationsreihenfolge."""
    properties: dict[str, Any] = param_schema.get("properties", {})
    result = []
    for name, prop in properties.items():
        wasm_type = _WASM_TYPES.get(prop.get("type", ""))
        if wasm_type is None:
            raise WasmUnavailableError(
                f"wasm tool parameter {name!r} has unsupported type "
                f"{prop.get('type')!r} — only integer/number/boolean are WASM-typed"
            )
        result.append((name, wasm_type))
    return result


class WasmSandbox:
    """Kompiliert Module einmal (gecacht), instanziiert pro Aufruf frisch.

    Instanziierung ist der einzige pro-Call-Overhead — Kompilierung (der
    teure Schritt) passiert genau einmal je Modul-Bytes, danach ist jeder
    weitere Aufruf ein reines "instantiate + call" im Mikrosekundenbereich.
    Thread-sicher: der Modul-Cache ist gelockt, Store/Instance sind pro
    Aufruf lokal (kein geteilter veränderlicher Zustand über Calls hinweg).
    """

    def __init__(self) -> None:
        wt = _load_wasmtime()  # wirft WasmUnavailableError, falls nicht installiert
        self._wt = wt
        config = wt.Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        self.engine = wt.Engine(config)
        self._modules: dict[bytes | str, Any] = {}
        self._lock = threading.Lock()

    def _compile(self, source: bytes | str) -> Any:
        wt = self._wt
        # wasmtime.wat2wasm() liefert ein bytearray zurück — unhashable, also
        # als Cache-Key auf bytes normalisieren (bytes/str sind es bereits).
        key = bytes(source) if isinstance(source, (bytes, bytearray)) else source
        with self._lock:
            module = self._modules.get(key)
            if module is not None:
                return module
        # Kompilieren außerhalb des Locks: teuer, aber idempotent — im
        # seltenen Wettlauf kompiliert höchstens ein zweiter Aufrufer parallel,
        # nie blockierend.
        wasm_bytes = wt.wat2wasm(source) if isinstance(source, str) else source
        module = wt.Module(self.engine, wasm_bytes)
        with self._lock:
            self._modules.setdefault(key, module)
            return self._modules[key]

    def run(
        self,
        source: bytes | str,
        args: dict[str, Any],
        param_schema: dict[str, Any],
        policy: WasmSandboxPolicy | None = None,
        func_name: str = "run",
    ) -> WasmSandboxResult:
        wt = self._wt
        policy = policy or WasmSandboxPolicy()
        start = time.perf_counter()

        try:
            param_types = _param_types(param_schema)
            module = self._compile(source)
        except WasmUnavailableError as exc:
            return WasmSandboxResult(ok=False, error=str(exc), exit_reason="compile_error",
                                     duration_ms=(time.perf_counter() - start) * 1000)
        except wt.WasmtimeError as exc:  # ungültiges WAT/WASM
            return WasmSandboxResult(ok=False, error=f"compile error: {exc}",
                                     exit_reason="compile_error",
                                     duration_ms=(time.perf_counter() - start) * 1000)

        store = wt.Store(self.engine)
        store.set_fuel(policy.fuel)
        store.set_epoch_deadline(1)
        store.set_limits(memory_size=policy.max_memory_bytes)

        stop_ticker = threading.Event()

        def _ticker() -> None:
            if not stop_ticker.wait(policy.wall_timeout_s):
                self.engine.increment_epoch()

        ticker = threading.Thread(target=_ticker, daemon=True)
        try:
            wasm_args = [args[name] for name, _ in param_types]
        except KeyError as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            return WasmSandboxResult(ok=False, error=f"missing argument: {exc}",
                                     exit_reason="error", duration_ms=duration_ms)

        try:
            # Keine Imports verlinkt: kein WASI, kein Dateisystem, kein Netz —
            # das Modul hat strukturell keine Ambient Authority.
            linker = wt.Linker(self.engine)
            instance = linker.instantiate(store, module)
            try:
                func = instance.exports(store)[func_name]
            except KeyError:
                duration_ms = (time.perf_counter() - start) * 1000
                return WasmSandboxResult(ok=False,
                                         error=f"module exports no function {func_name!r}",
                                         exit_reason="error", duration_ms=duration_ms)

            ticker.start()
            value = func(store, *wasm_args)
            duration_ms = (time.perf_counter() - start) * 1000
            return WasmSandboxResult(ok=True, value={"result": value}, exit_reason="ok",
                                     duration_ms=duration_ms)
        except wt.Trap as trap:
            duration_ms = (time.perf_counter() - start) * 1000
            reason = {
                wt.TrapCode.OUT_OF_FUEL: "fuel_exhausted",
                wt.TrapCode.INTERRUPT: "timeout",
            }.get(trap.trap_code, "error")
            return WasmSandboxResult(ok=False, error=trap.message, exit_reason=reason,
                                     duration_ms=duration_ms)
        except wt.WasmtimeError as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            reason = "memory_exceeded" if "memory" in str(exc).lower() else "error"
            return WasmSandboxResult(ok=False, error=str(exc), exit_reason=reason,
                                     duration_ms=duration_ms)
        finally:
            stop_ticker.set()
            if ticker.is_alive():
                ticker.join(timeout=policy.wall_timeout_s + 1.0)
