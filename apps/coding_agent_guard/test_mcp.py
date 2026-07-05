"""E-5 — MCP-Server für den Coding-Agent-Guard."""

import io
import json

from brainfump import BrainFumpKernel
from apps.coding_agent_guard.guard import CodingAgentGuard
from apps.coding_agent_guard.mcp_server import PROTOCOL_VERSION, McpGuardServer


def make_server():
    return McpGuardServer(CodingAgentGuard(BrainFumpKernel()))


def test_initialize_handshake():
    resp = make_server().handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": PROTOCOL_VERSION}})
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "brainfump-coding-agent-guard"


def test_initialized_notification_gets_no_response():
    assert make_server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list():
    resp = make_server().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"report_event", "pre_edit_gate", "project_summary", "guard_stats"}
    # Jedes Tool hat ein JSON-Schema.
    for tool in resp["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_report_then_gate_blocks_repeat_via_tools_call():
    server = make_server()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "report_event",
        "arguments": {"repo": "r", "report_type": "failed_attempt", "content": "Retry scheiterte",
                      "payload": {"error_signature": "sig", "alternative": "Circuit Breaker"}},
    }})
    resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "pre_edit_gate", "arguments": {"repo": "r", "error_signature": "sig"},
    }})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["mode"] == "suggest_alternative"
    assert "Circuit Breaker" in payload["suggested_alternative"]


def test_gate_block_is_flagged_as_error():
    server = make_server()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "report_event",
        "arguments": {"repo": "r", "report_type": "failed_attempt", "content": "x",
                      "payload": {"error_signature": "sig"}},  # keine Alternative → block
    }})
    resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "pre_edit_gate", "arguments": {"repo": "r", "error_signature": "sig"},
    }})
    assert resp["result"]["isError"] is True


def test_project_summary_returns_markdown():
    server = make_server()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "report_event",
        "arguments": {"repo": "r", "report_type": "decision", "content": "Architektur: FastAPI."},
    }})
    resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "project_summary", "arguments": {"repo": "r"},
    }})
    text = resp["result"]["content"][0]["text"]
    assert "Projektgedächtnis: r" in text
    assert "FastAPI" in text


def test_missing_required_argument_is_tool_error():
    resp = make_server().handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "pre_edit_gate", "arguments": {},  # repo fehlt
    }})
    assert resp["result"]["isError"] is True
    assert "repo" in resp["result"]["content"][0]["text"]


def test_unknown_method_returns_jsonrpc_error():
    resp = make_server().handle({"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_tool_error():
    resp = make_server().handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "delete_everything", "arguments": {}}})
    assert resp["result"]["isError"] is True


def test_stdio_loop_roundtrip():
    server = make_server()
    stdin = io.StringIO("\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]) + "\n")
    stdout = io.StringIO()
    server.serve_stdio(stdin=stdin, stdout=stdout)
    lines = [json.loads(l) for l in stdout.getvalue().splitlines()]
    # initialize + tools/list beantwortet, Notification nicht.
    assert len(lines) == 2
    assert lines[0]["id"] == 1
    assert lines[1]["id"] == 2 and "tools" in lines[1]["result"]
