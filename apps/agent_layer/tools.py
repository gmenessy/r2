"""Tool-Registry: deklarative Tools mit Schema-Validierung und Sandbox-Policy.

Jedes Tool trägt sein JSON-Schema (OpenAI-Function-Format, damit vLLM es
direkt fürs Tool Calling nutzt), seine :class:`SandboxPolicy` und ein Flag,
ob es sandboxed (Kindprozess) oder trusted-inline läuft. Memory-Tools laufen
inline, weil sie auf die geteilte Kernel-SQLite-Verbindung zugreifen —
sie sind Teil der Plattform, nicht untrusted Tool-Code.

App-Entwicklung auf der Plattform heißt: Tools registrieren, fertig —
``@registry.tool(...)`` genügt, Schema und Limits sind deklarativ.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from apps.agent_layer.sandbox import SandboxPolicy
from apps.agent_layer.wasm_sandbox import WasmSandboxPolicy

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


class ToolError(Exception):
    """Registrier- oder Validierungsfehler der Tool-Schicht."""


@dataclass(frozen=True)
class ToolSpec:
    """Ein Tool läuft in genau einer von zwei Engines:

    - ``engine="python"`` (Default): ``handler`` ist eine Python-Funktion,
      läuft sandboxed im Kindprozess (:class:`SandboxPolicy`) oder — für
      Plattform-Tools wie Memory — trusted inline (``sandboxed=False``).
    - ``engine="wasm"``: ``wasm_source`` ist WAT-Text oder kompilierte
      ``.wasm``-Bytes; läuft in der :class:`WasmSandbox` (Path A) — für reine
      numerische Berechnungen ~80× schneller als der Kindprozess-Fork, mit
      strukturell keiner Ambient Authority (kein WASI verlinkt).
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any] | None = None
    policy: SandboxPolicy = field(default_factory=SandboxPolicy)
    sandboxed: bool = True
    side_effects: bool = False
    engine: str = "python"
    wasm_source: bytes | str | None = None
    wasm_policy: WasmSandboxPolicy = field(default_factory=WasmSandboxPolicy)

    def __post_init__(self) -> None:
        if self.engine == "wasm":
            if self.wasm_source is None:
                raise ToolError(f"wasm tool {self.name!r} requires wasm_source")
        elif self.engine == "python":
            if self.handler is None:
                raise ToolError(f"python tool {self.name!r} requires a handler")
        else:
            raise ToolError(f"tool {self.name!r}: unknown engine {self.engine!r}")

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def validate_args(parameters: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """Leichtgewichtige Prüfung gegen das Parameter-Schema.

    Deckt Pflichtfelder, unbekannte Felder und JSON-Typen ab — inklusive
    **verschachtelter** ``object``/``array``-Schemata (S4-4), rekursiv über
    ``properties`` bzw. ``items``. Reine Stdlib, kein ``jsonschema``.
    """
    return _validate(parameters, args, path="")


def _type_ok(schema: dict[str, Any], value: Any, path: str) -> str | None:
    expected = _JSON_TYPES.get(schema.get("type", ""))
    if expected is None:
        return None
    if expected is int and isinstance(value, bool):
        return f"argument {path}: expected integer, got boolean"
    # bool ist Subtyp von int — für "number" akzeptieren wir keine Bools.
    if schema.get("type") == "number" and isinstance(value, bool):
        return f"argument {path}: expected number, got boolean"
    if not isinstance(value, expected):
        return f"argument {path}: expected {schema['type']}, got {type(value).__name__}"
    return None


def _validate(schema: dict[str, Any], value: Any, path: str) -> list[str]:
    problems: list[str] = []
    type_problem = _type_ok(schema, value, path or "<root>")
    if type_problem:
        return [type_problem]  # bei falschem Typ nicht weiter absteigen

    if schema.get("type") == "object" and isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"missing required argument: {_join(path, name)}")
        allow_extra = schema.get("additionalProperties", False)
        for name, item in value.items():
            child = _join(path, name)
            if name not in properties:
                if not allow_extra:
                    problems.append(f"unknown argument: {child}")
                continue
            problems.extend(_validate(properties[name], item, child))
    elif schema.get("type") == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                problems.extend(_validate(item_schema, item, f"{path or '<root>'}[{index}]"))
    return problems


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


class ToolRegistry:
    """Namensraum aller für einen Agenten verfügbaren Tools."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise ToolError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        return spec

    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        policy: SandboxPolicy | None = None,
        sandboxed: bool = True,
        side_effects: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Dekorator-Zucker: ``@registry.tool("calc", …)`` über einer Funktion."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(ToolSpec(
                name=name, description=description, parameters=parameters,
                handler=fn, policy=policy or SandboxPolicy(),
                sandboxed=sandboxed, side_effects=side_effects,
            ))
            return fn

        return decorator

    def register_wasm(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        wasm_source: bytes | str,
        wasm_policy: WasmSandboxPolicy | None = None,
        side_effects: bool = False,
    ) -> ToolSpec:
        """Ein WASM-Tool registrieren (Path A) — kein Python-Handler nötig.

        ``wasm_source`` ist entweder WAT-Text (``str``, kompiliert bei erstem
        Aufruf) oder bereits kompilierte ``.wasm``-Bytes. ``parameters`` muss
        ein flaches Schema aus ``integer``/``number``/``boolean``-Properties
        sein — die exportierte Funktion ``run`` wird mit genau diesen Typen
        in Deklarationsreihenfolge aufgerufen."""
        return self.register(ToolSpec(
            name=name, description=description, parameters=parameters,
            engine="wasm", wasm_source=wasm_source,
            wasm_policy=wasm_policy or WasmSandboxPolicy(), side_effects=side_effects,
        ))

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [self._specs[name].openai_schema() for name in self.names()]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "sandboxed": spec.sandboxed,
                "side_effects": spec.side_effects,
                "engine": spec.engine,
                "limits": self._describe_limits(spec),
            }
            for name in self.names()
            for spec in [self._specs[name]]
        ]

    @staticmethod
    def _describe_limits(spec: ToolSpec) -> dict[str, Any]:
        if spec.engine == "wasm":
            return {
                "wall_timeout_s": spec.wasm_policy.wall_timeout_s,
                "fuel": spec.wasm_policy.fuel,
                "memory_kib": spec.wasm_policy.max_memory_bytes // 1024,
            }
        return {
            "wall_timeout_s": spec.policy.wall_timeout_s,
            "cpu_seconds": spec.policy.cpu_seconds,
            "memory_mib": spec.policy.memory_bytes // (1024 * 1024),
            "allow_network": spec.policy.allow_network,
        }


# -- Builtins -----------------------------------------------------------------

_CALC_OPS: dict[type, Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_calc(expression: str) -> float:
    """Arithmetik über einem AST-Whitelist-Evaluator — kein ``eval``."""

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
            if isinstance(node.op, ast.Pow):
                left, right = _eval(node.left), _eval(node.right)
                if abs(right) > 64:
                    raise ValueError("exponent too large")
                return _CALC_OPS[type(node.op)](left, right)
            return _CALC_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _CALC_OPS:
            return _CALC_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    if len(expression) > 200:
        raise ValueError("expression too long")
    return _eval(ast.parse(expression, mode="eval"))


def builtin_registry(kernel: Any | None = None) -> ToolRegistry:
    """Registry mit den Plattform-Builtins; Memory-Tools nur mit Kernel."""
    registry = ToolRegistry()

    registry.register(ToolSpec(
        name="calc",
        description="Evaluate an arithmetic expression (+ - * / // % **, parentheses).",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. (2+3)*4"}},
            "required": ["expression"],
        },
        handler=lambda expression: {"result": safe_calc(expression)},
        policy=SandboxPolicy(wall_timeout_s=3.0, cpu_seconds=2),
    ))

    registry.register(ToolSpec(
        name="utc_now",
        description="Current UTC timestamp (ISO 8601).",
        parameters={"type": "object", "properties": {}},
        handler=lambda: {"utc": datetime.now(timezone.utc).isoformat()},
        policy=SandboxPolicy(wall_timeout_s=3.0, cpu_seconds=1),
    ))

    if kernel is not None:
        registry.register(ToolSpec(
            name="memory_search",
            description="Search the long-term memory (fRAG) for prior knowledge about the case.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "case_id": {"type": "string"},
                    "k": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda query, case_id=None, k=5: {
                "hits": [
                    {"score": round(hit.score, 4), "statement": hit.card.statement,
                     "memory_type": hit.card.memory_type, "card_id": hit.card.card_id}
                    for hit in kernel.search(query, case_id=case_id, k=int(k))
                ]
            },
            sandboxed=False,  # geteilte SQLite-Verbindung der Plattform
        ))

        registry.register(ToolSpec(
            name="memory_record",
            description="Record a durable observation, decision or correction into long-term memory.",
            parameters={
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "one of: decision, correction, failed_attempt, "
                                       "successful_attempt, risk_marker",
                    },
                    "content": {"type": "string"},
                    "case_id": {"type": "string"},
                },
                "required": ["event_type", "content"],
            },
            handler=lambda event_type, content, case_id=None: {
                "event_id": kernel.record(event_type, content, case_id=case_id,
                                          source="agent_layer").event_id
            },
            sandboxed=False,
            side_effects=True,
        ))

    return registry
