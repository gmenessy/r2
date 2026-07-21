"""WasmSandbox (Path A): normale Ausführung, alle drei Resource-Limits,
Fehlerpfade, Nebenläufigkeit, graceful Degradation ohne wasmtime."""

from __future__ import annotations

import threading

import pytest

from apps.agent_layer import wasm_sandbox as ws
from apps.agent_layer.wasm_sandbox import (
    WasmSandbox,
    WasmSandboxPolicy,
    WasmUnavailableError,
    wasm_available,
)

pytestmark = pytest.mark.skipif(not wasm_available(),
                                reason="wasmtime not installed — pip install .[wasm]")

ADD_WAT = """
(module
  (func (export "run") (param $a i64) (param $b i64) (result i64)
    local.get $a
    local.get $b
    i64.add))
"""
ADD_SCHEMA = {"type": "object",
              "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}

LOOP_WAT = """
(module
  (func (export "run") (param $a i64) (param $b i64) (result i64)
    (loop $c (br $c))
    unreachable))
"""

BIG_MEMORY_WAT = (
    '(module (memory (export "memory") 100) '
    '(func (export "run") (param $a i64) (param $b i64) (result i64) i64.const 0))'
)


@pytest.fixture(scope="module")
def sandbox() -> WasmSandbox:
    return WasmSandbox()


def test_normal_execution(sandbox: WasmSandbox) -> None:
    result = sandbox.run(ADD_WAT, {"a": 5, "b": 7}, ADD_SCHEMA)
    assert result.ok and result.value == {"result": 12}
    assert result.exit_reason == "ok"
    d = result.to_dict()
    assert d["engine"] == "wasm" and d["duration_ms"] >= 0


def test_module_is_compiled_once_and_cached(sandbox: WasmSandbox) -> None:
    sandbox.run(ADD_WAT, {"a": 1, "b": 1}, ADD_SCHEMA)
    cached_count = len(sandbox._modules)
    sandbox.run(ADD_WAT, {"a": 2, "b": 2}, ADD_SCHEMA)
    assert len(sandbox._modules) == cached_count  # kein neuer Cache-Eintrag


def test_fuel_exhaustion_traps_deterministically(sandbox: WasmSandbox) -> None:
    result = sandbox.run(LOOP_WAT, {"a": 0, "b": 0}, ADD_SCHEMA,
                         policy=WasmSandboxPolicy(fuel=1000, wall_timeout_s=5.0))
    assert not result.ok and result.exit_reason == "fuel_exhausted"


def test_wall_timeout_traps_even_with_fuel_left(sandbox: WasmSandbox) -> None:
    result = sandbox.run(LOOP_WAT, {"a": 0, "b": 0}, ADD_SCHEMA,
                         policy=WasmSandboxPolicy(fuel=10**15, wall_timeout_s=0.15))
    assert not result.ok and result.exit_reason == "timeout"
    assert result.duration_ms < 1000  # trapt zeitnah, hängt nicht bis Fuel alle ist


def test_memory_limit_rejected_at_instantiation(sandbox: WasmSandbox) -> None:
    result = sandbox.run(BIG_MEMORY_WAT, {"a": 0, "b": 0}, ADD_SCHEMA,
                         policy=WasmSandboxPolicy(max_memory_bytes=1024 * 1024))
    assert not result.ok and result.exit_reason == "memory_exceeded"


def test_missing_argument_reported_cleanly_not_as_missing_export(sandbox: WasmSandbox) -> None:
    result = sandbox.run(ADD_WAT, {"a": 1}, ADD_SCHEMA)
    assert not result.ok and result.exit_reason == "error"
    assert "missing argument" in result.error
    assert "exports no function" not in result.error


def test_wrong_export_name_reported_cleanly(sandbox: WasmSandbox) -> None:
    wat = '(module (func (export "nope")))'
    result = sandbox.run(wat, {"a": 1, "b": 2}, ADD_SCHEMA)
    assert not result.ok and "exports no function" in result.error


def test_invalid_source_reports_compile_error(sandbox: WasmSandbox) -> None:
    result = sandbox.run("not valid wat {{{", {"a": 1, "b": 2}, ADD_SCHEMA)
    assert not result.ok and result.exit_reason == "compile_error"


def test_unsupported_schema_type_reports_compile_error(sandbox: WasmSandbox) -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    result = sandbox.run(ADD_WAT, {"a": "x"}, schema)
    assert not result.ok and result.exit_reason == "compile_error"


def test_precompiled_wasm_bytes_also_work(sandbox: WasmSandbox) -> None:
    import wasmtime
    wasm_bytes = wasmtime.wat2wasm(ADD_WAT)
    result = sandbox.run(wasm_bytes, {"a": 3, "b": 4}, ADD_SCHEMA)
    assert result.ok and result.value == {"result": 7}


def test_concurrent_calls_are_thread_safe(sandbox: WasmSandbox) -> None:
    errors: list[tuple[int, dict]] = []

    def worker(i: int) -> None:
        for _ in range(20):
            r = sandbox.run(ADD_WAT, {"a": i, "b": i}, ADD_SCHEMA)
            if not r.ok or r.value["result"] != 2 * i:
                errors.append((i, r.to_dict()))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_wasm_sandbox_construction_fails_clearly_when_unavailable(monkeypatch) -> None:
    def _raise() -> None:
        raise WasmUnavailableError(
            "wasmtime is not installed — install the optional extra: pip install .[wasm]"
        )

    monkeypatch.setattr(ws, "_load_wasmtime", _raise)
    with pytest.raises(WasmUnavailableError, match="pip install .\\[wasm\\]"):
        WasmSandbox()
    assert wasm_available() is False
