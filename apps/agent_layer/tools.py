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
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    policy: SandboxPolicy = field(default_factory=SandboxPolicy)
    sandboxed: bool = True
    side_effects: bool = False

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

    Deckt Pflichtfelder, unbekannte Felder und die JSON-Basistypen ab — genug,
    um halluzinierte Argumente vor der Sandbox abzufangen, ohne einen
    JSON-Schema-Validator als Abhängigkeit zu ziehen.
    """
    problems: list[str] = []
    properties: dict[str, Any] = parameters.get("properties", {})
    for name in parameters.get("required", []):
        if name not in args:
            problems.append(f"missing required argument: {name}")
    for name, value in args.items():
        if name not in properties:
            problems.append(f"unknown argument: {name}")
            continue
        expected = _JSON_TYPES.get(properties[name].get("type", ""))
        if expected is None:
            continue
        if expected is int and isinstance(value, bool):
            problems.append(f"argument {name}: expected integer, got boolean")
        elif not isinstance(value, expected):
            problems.append(
                f"argument {name}: expected {properties[name]['type']}, got {type(value).__name__}"
            )
    return problems


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
                "limits": {
                    "wall_timeout_s": spec.policy.wall_timeout_s,
                    "cpu_seconds": spec.policy.cpu_seconds,
                    "memory_mib": spec.policy.memory_bytes // (1024 * 1024),
                    "allow_network": spec.policy.allow_network,
                },
            }
            for name in self.names()
            for spec in [self._specs[name]]
        ]


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
