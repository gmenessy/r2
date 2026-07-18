"""Sandbox-Grenzen: Timeout, Speicher, Output-Deckel, Egress, Fehlerpfade."""

from __future__ import annotations

import pytest

from apps.agent_layer.sandbox import ProcessSandbox, SandboxPolicy


@pytest.fixture(scope="module")
def sandbox() -> ProcessSandbox:
    return ProcessSandbox()


def _ok_tool(x: int, y: int) -> dict:
    return {"sum": x + y}


def _sleepy_tool() -> dict:
    import time

    time.sleep(30)
    return {"never": True}


def _hungry_tool() -> dict:
    blob = "x" * (512 * 1024 * 1024)
    return {"len": len(blob)}


def _chatty_tool() -> str:
    return "y" * (1024 * 1024)


def _crashing_tool() -> dict:
    raise RuntimeError("boom")


def _network_tool() -> dict:
    import socket

    socket.create_connection(("192.0.2.1", 80), timeout=1)
    return {"connected": True}


def _env_probe() -> dict:
    import os

    return {"has_secret": "AGENT_SECRET" in os.environ, "cwd": os.getcwd()}


def test_successful_tool_returns_json_value(sandbox: ProcessSandbox) -> None:
    result = sandbox.run(_ok_tool, {"x": 2, "y": 40})
    assert result.ok and result.value == {"sum": 42}
    assert result.exit_reason == "ok" and result.hardened


def test_wall_timeout_terminates_hanging_tool(sandbox: ProcessSandbox) -> None:
    result = sandbox.run(_sleepy_tool, {}, SandboxPolicy(wall_timeout_s=0.5))
    assert not result.ok and result.exit_reason == "timeout"


def test_memory_limit_stops_allocation(sandbox: ProcessSandbox) -> None:
    result = sandbox.run(_hungry_tool, {}, SandboxPolicy(memory_bytes=64 * 1024 * 1024))
    assert not result.ok
    assert result.exit_reason in ("killed", "error")


def test_output_limit_rejects_oversized_result(sandbox: ProcessSandbox) -> None:
    result = sandbox.run(_chatty_tool, {}, SandboxPolicy(max_output_bytes=1024))
    assert not result.ok and result.exit_reason == "output_limit"


def test_exception_is_reported_not_raised(sandbox: ProcessSandbox) -> None:
    result = sandbox.run(_crashing_tool, {})
    assert not result.ok and result.exit_reason == "error"
    assert "RuntimeError: boom" in (result.error or "")


def test_network_egress_is_blocked_by_default(sandbox: ProcessSandbox) -> None:
    result = sandbox.run(_network_tool, {}, SandboxPolicy(wall_timeout_s=5.0))
    assert not result.ok
    assert "network egress is disabled" in (result.error or "")


def test_environment_is_scrubbed_and_cwd_isolated(sandbox: ProcessSandbox, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SECRET", "hunter2")
    result = sandbox.run(_env_probe, {})
    assert result.ok
    assert result.value["has_secret"] is False
    assert "agent-tool-" in result.value["cwd"]
