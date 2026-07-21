"""Tool-Registry: Registrierung, Schema-Export, Validierung, Builtins."""

from __future__ import annotations

import pytest

from brainfump import BrainFumpKernel
from apps.agent_layer.tools import (
    ToolError,
    ToolRegistry,
    ToolSpec,
    builtin_registry,
    safe_calc,
    validate_args,
)
from apps.agent_layer.wasm_sandbox import WasmSandboxPolicy

SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "string"}, "n": {"type": "integer"}},
    "required": ["a"],
}


def test_register_and_openai_schema() -> None:
    registry = ToolRegistry()

    @registry.tool("echo", "Echo a value.", SCHEMA)
    def _echo(a: str, n: int = 1) -> dict:
        return {"echo": a * n}

    assert registry.names() == ["echo"]
    schema = registry.openai_tools()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert schema["function"]["parameters"] == SCHEMA


def test_duplicate_registration_rejected() -> None:
    registry = ToolRegistry()
    registry.tool("echo", "d", SCHEMA)(lambda a: a)
    with pytest.raises(ToolError):
        registry.tool("echo", "d", SCHEMA)(lambda a: a)


def test_validate_args_catches_hallucinated_input() -> None:
    assert validate_args(SCHEMA, {"a": "hi"}) == []
    assert validate_args(SCHEMA, {"a": "hi", "n": 2}) == []
    assert "missing required argument: a" in validate_args(SCHEMA, {})[0]
    assert any("unknown argument" in p for p in validate_args(SCHEMA, {"a": "x", "z": 1}))
    assert any("expected string" in p for p in validate_args(SCHEMA, {"a": 5}))
    assert any("expected integer" in p for p in validate_args(SCHEMA, {"a": "x", "n": True}))


NESTED = {
    "type": "object",
    "properties": {
        "user": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name"],
        },
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["user"],
}


def test_validate_args_nested_object_and_array_ok() -> None:
    assert validate_args(NESTED, {"user": {"name": "Kim", "age": 30},
                                  "tags": ["a", "b"]}) == []


def test_validate_args_nested_type_errors() -> None:
    problems = validate_args(NESTED, {"user": {"name": 5}, "tags": ["ok", 3]})
    assert any("user.name: expected string" in p for p in problems)
    assert any("tags[1]: expected string" in p for p in problems)


def test_validate_args_nested_missing_required() -> None:
    problems = validate_args(NESTED, {"user": {"age": 30}})
    assert any("missing required argument: user.name" in p for p in problems)


def test_validate_args_nested_unknown_field() -> None:
    problems = validate_args(NESTED, {"user": {"name": "Kim", "x": 1}})
    assert any("unknown argument: user.x" in p for p in problems)


def test_validate_args_additional_properties_allowed() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}},
              "additionalProperties": True}
    assert validate_args(schema, {"a": "x", "extra": 99}) == []


def test_validate_args_wrong_container_type() -> None:
    problems = validate_args(NESTED, {"user": "nicht-objekt"})
    assert any("user: expected object" in p for p in problems)


def test_safe_calc_arithmetic_and_rejections() -> None:
    assert safe_calc("(2+3)*4") == 20
    assert safe_calc("-7 // 2") == -4
    assert safe_calc("2**10") == 1024
    with pytest.raises(ValueError):
        safe_calc("__import__('os')")
    with pytest.raises(ValueError):
        safe_calc("2**9999")
    with pytest.raises(ValueError):
        safe_calc("1+" * 150 + "1")


def test_builtin_registry_without_kernel_has_no_memory_tools() -> None:
    registry = builtin_registry()
    assert registry.names() == ["calc", "utc_now"]
    assert registry.get("calc").handler(expression="6*7") == {"result": 42}
    assert "utc" in registry.get("utc_now").handler()


def test_builtin_memory_tools_roundtrip() -> None:
    kernel = BrainFumpKernel(None)
    registry = builtin_registry(kernel)
    assert "memory_search" in registry.names() and "memory_record" in registry.names()

    record = registry.get("memory_record")
    assert not record.sandboxed and record.side_effects
    out = record.handler(event_type="decision", content="Wir nutzen SQLite.", case_id="case_1")
    assert out["event_id"].startswith("evt_")

    hits = registry.get("memory_search").handler(query="SQLite", case_id="case_1")["hits"]
    assert hits and "SQLite" in hits[0]["statement"]


def test_describe_exposes_limits() -> None:
    described = builtin_registry().describe()
    calc = next(d for d in described if d["name"] == "calc")
    assert calc["sandboxed"] and calc["limits"]["allow_network"] is False
    assert calc["limits"]["wall_timeout_s"] == 3.0
    assert calc["engine"] == "python"


# -- WASM-Engine (Path A) — Registrierung/Beschreibung, keine wasmtime-Ausführung ---

ADD_WAT = """
(module
  (func (export "run") (param $a i64) (param $b i64) (result i64)
    local.get $a
    local.get $b
    i64.add))
"""
WASM_SCHEMA = {"type": "object",
               "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}


def test_python_tool_requires_handler() -> None:
    with pytest.raises(ToolError, match="requires a handler"):
        ToolSpec(name="x", description="d", parameters=SCHEMA, handler=None)


def test_wasm_tool_requires_wasm_source() -> None:
    with pytest.raises(ToolError, match="requires wasm_source"):
        ToolSpec(name="x", description="d", parameters=WASM_SCHEMA, engine="wasm")


def test_unknown_engine_rejected() -> None:
    with pytest.raises(ToolError, match="unknown engine"):
        ToolSpec(name="x", description="d", parameters=SCHEMA, handler=lambda: None,
                engine="quantum")


def test_register_wasm() -> None:
    registry = ToolRegistry()
    spec = registry.register_wasm("wasm_add", "Add two integers via WASM.", WASM_SCHEMA,
                                  ADD_WAT, wasm_policy=WasmSandboxPolicy(fuel=10_000))
    assert spec.engine == "wasm" and spec.wasm_source == ADD_WAT
    assert spec.wasm_policy.fuel == 10_000
    assert registry.get("wasm_add") is spec
    schema = registry.openai_tools()[0]
    assert schema["function"]["name"] == "wasm_add"  # OpenAI-Schema identisch zu Python-Tools


def test_describe_shows_wasm_limits_not_sandbox_policy() -> None:
    registry = ToolRegistry()
    registry.register_wasm("wasm_add", "d", WASM_SCHEMA, ADD_WAT,
                           wasm_policy=WasmSandboxPolicy(fuel=42, wall_timeout_s=1.5,
                                                         max_memory_bytes=2048))
    described = registry.describe()[0]
    assert described["engine"] == "wasm"
    assert described["limits"] == {"wall_timeout_s": 1.5, "fuel": 42, "memory_kib": 2}
    assert "cpu_seconds" not in described["limits"]  # keine Fork-Sandbox-Felder
