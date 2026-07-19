"""AsyncRunner (S4-2/S4-5): Hintergrund-Runs, Polling, Backpressure."""

from __future__ import annotations

import threading
import time

import pytest

from brainfump import BrainFumpKernel
from apps.agent_layer.runner import AsyncRunner, QueueFullError, TenantQueueFullError
from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.simllm import SimulatedLLM
from apps.agent_layer.tools import builtin_registry
from apps.agent_layer.xai import TraceStore


def _runner(llm=None, **kwargs) -> AsyncRunner:
    kernel = BrainFumpKernel(None)
    runtime = AgentRuntime(llm=llm or SimulatedLLM(), registry=builtin_registry(kernel),
                           traces=TraceStore(), kernel=kernel)
    return AsyncRunner(runtime, **kwargs)


def _wait_for(runner: AsyncRunner, run_id: str, status: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = runner.status(run_id)
        if state and state["status"] == status:
            return state
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} reached {runner.status(run_id)}, erwartet {status}")


class BlockingLLM:
    """Hängt in chat(), bis freigegeben — hält Runs kontrolliert 'running'."""

    model = "blocking"
    base_url = "test://blocking"

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)

    def chat(self, messages, tools=None, **_kwargs):
        self.entered.release()
        self.release.wait(timeout=5)
        return SimulatedLLM().chat(messages, tools=tools)


def test_submit_returns_run_id_and_completes() -> None:
    runner = _runner()
    run_id = runner.submit("Rechne (2+3).", tenant="acme")
    assert run_id.startswith("run_")
    # Direkt nach submit: queued oder schon running.
    assert runner.status(run_id)["status"] in ("queued", "running", "done")
    state = _wait_for(runner, run_id, "done")
    assert state["result"]["status"] == "ok"
    assert state["tenant"] == "acme"


def test_status_of_unknown_run_is_none() -> None:
    assert _runner().status("run_missing") is None


def test_per_tenant_backpressure_raises_429_equivalent() -> None:
    llm = BlockingLLM()
    runner = _runner(llm=llm, max_workers=4, max_inflight_per_tenant=2)
    try:
        runner.submit("a", tenant="acme")
        runner.submit("b", tenant="acme")
        # Beide sind in Flight (queued/running) → dritter Run wird abgewiesen.
        with pytest.raises(TenantQueueFullError):
            runner.submit("c", tenant="acme")
        # Anderer Tenant hat eigenes Kontingent.
        assert runner.submit("d", tenant="rival")
    finally:
        llm.release.set()


def test_global_backpressure_raises_503_equivalent() -> None:
    llm = BlockingLLM()
    runner = _runner(llm=llm, max_workers=4, max_inflight_total=2,
                     max_inflight_per_tenant=10)
    try:
        runner.submit("a", tenant="t1")
        runner.submit("b", tenant="t2")
        with pytest.raises(QueueFullError):
            runner.submit("c", tenant="t3")
    finally:
        llm.release.set()


def test_inflight_frees_up_after_completion() -> None:
    runner = _runner(max_inflight_per_tenant=1)
    first = runner.submit("Rechne (1+1).", tenant="acme")
    _wait_for(runner, first, "done")
    # Nach Abschluss ist der Slot wieder frei.
    second = runner.submit("Rechne (2+2).", tenant="acme")
    assert _wait_for(runner, second, "done")["result"]["status"] == "ok"


def test_run_error_is_captured_as_state() -> None:
    class ExplodingLLM:
        def chat(self, messages, tools=None, **_):
            raise RuntimeError("kaputt")

    runner = _runner(llm=ExplodingLLM())
    run_id = runner.submit("frage", tenant="acme")
    # runtime.run fängt LLMError, aber RuntimeError ist kein LLMError → Job-Grenze.
    state = _wait_for(runner, run_id, "error")
    assert "kaputt" in state["result"]["error"]
