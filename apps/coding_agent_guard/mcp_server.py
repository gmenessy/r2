"""E-5 — Coding-Agent-Guard als MCP-Server.

Ein Model-Context-Protocol-Server (stdio-Transport: newline-delimited
JSON-RPC 2.0), der die Guard-Funktionen als MCP-Tools bereitstellt, damit
Coding Agents (Claude Code u. a.) das Projektgedächtnis nativ andocken statt
über HTTP-Calls. Kein SDK nötig — der Transport ist bewusst schlank und die
Dispatch-Logik (``handle``) ist vom stdio-Loop getrennt und damit testbar.

Bereitgestellte Tools:
    report_event    Entwicklungsereignis melden (decision/failed_attempt/…)
    pre_edit_gate   Pre-Action-Check vor einem Edit
    project_summary deterministisches Markdown-Projektgedächtnis
    guard_stats     Kennzahlen

Start (z. B. in .mcp.json / Claude-Code-Config):
    python3 -m apps.coding_agent_guard.mcp_server --data ./data
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from apps.coding_agent_guard.guard import CodingAgentGuard  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "brainfump-coding-agent-guard", "version": "0.2.0"}

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "report_event",
        "description": "Ein Entwicklungsereignis ins Projektgedächtnis melden "
        "(decision, correction, failed_attempt, successful_attempt, risk_marker, "
        "fragile_file, …). Wird auditierbar geloggt und verdichtet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository/Projekt-ID (Akte)."},
                "report_type": {"type": "string"},
                "content": {"type": "string"},
                "payload": {"type": "object", "description": "Zusatzdaten, z. B. error_signature, files, alternative."},
            },
            "required": ["repo", "report_type"],
        },
    },
    {
        "name": "pre_edit_gate",
        "description": "Vor einem Edit prüfen, ob eine Aktion riskant ist "
        "(bereits gescheiterter Fix, fragile Datei, Governance/Regel-Verstoß). "
        "Liefert mode: allow/warn/require_review/suggest_alternative/block.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "action_type": {"type": "string", "default": "edit"},
                "files": {"type": "array", "items": {"type": "string"}},
                "error_signature": {"type": "string"},
                "context": {"type": "object"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "project_summary",
        "description": "Deterministisches, menschenlesbares Markdown-Projektgedächtnis "
        "(Architekturentscheidungen, fragile Dateien, gescheiterte Fixes, Regeln).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "max_items": {"type": "integer", "default": 10},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "guard_stats",
        "description": "Kennzahlen des Projektgedächtnisses (Events, Cards, Gate-Checks, Interventionen).",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    },
]


class McpGuardServer:
    """MCP-JSON-RPC-Dispatch über einem CodingAgentGuard."""

    def __init__(self, guard: CodingAgentGuard) -> None:
        self.guard = guard

    # -- JSON-RPC ----------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Verarbeitet eine JSON-RPC-Nachricht. Gibt die Antwort zurück oder
        None für Notifications (die keine Antwort bekommen)."""
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                }
            elif method in ("notifications/initialized", "initialized"):
                return None  # Notification
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _TOOLS}
            elif method == "tools/call":
                result = self._call_tool(message.get("params") or {})
            else:
                if is_notification:
                    return None
                return self._error(msg_id, -32601, f"method not found: {method}")
        except _ToolError as exc:
            return self._tool_error_result(msg_id, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(msg_id, -32603, f"internal error: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "report_event":
            _require(args, "repo", "report_type")
            out = self.guard.report(args["repo"], args["report_type"],
                                    args.get("content", ""), **(args.get("payload") or {}))
            return _text(json.dumps(out))
        if name == "pre_edit_gate":
            _require(args, "repo")
            out = self.guard.gate(args["repo"], action_type=args.get("action_type", "edit"),
                                  files=args.get("files"), error_signature=args.get("error_signature"),
                                  context=args.get("context"))
            return _text(json.dumps(out), is_error=out.get("mode") == "block")
        if name == "project_summary":
            _require(args, "repo")
            return _text(self.guard.summary(args["repo"], max_items=args.get("max_items", 10)))
        if name == "guard_stats":
            _require(args, "repo")
            return _text(json.dumps(self.guard.stats(args["repo"])))
        raise _ToolError(f"unknown tool: {name}")

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error_result(msg_id: Any, message: str) -> dict[str, Any]:
        # Tool-Fehler werden per Konvention als Ergebnis mit isError=true geliefert.
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": {"content": [{"type": "text", "text": message}], "isError": True}}

    # -- stdio-Loop --------------------------------------------------------------

    def serve_stdio(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()


class _ToolError(Exception):
    pass


def _require(args: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not args.get(k)]
    if missing:
        raise _ToolError(f"missing arguments: {missing}")


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Coding-Agent-Guard MCP-Server (stdio)")
    parser.add_argument("--data", default=os.environ.get("BRAINFUMP_DATA", "./data"))
    args = parser.parse_args()

    guard = CodingAgentGuard(BrainFumpKernel(args.data))
    McpGuardServer(guard).serve_stdio()


if __name__ == "__main__":  # pragma: no cover
    main()
