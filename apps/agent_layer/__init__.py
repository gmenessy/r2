"""Agent Execution Layer — ultraleichtgewichtige Ausführungsschicht für
LLM-Agenten auf dem BrainFump-Kernel.

Säulen (siehe README für die Paper-Basis):
- ``llm``           OpenAI-kompatibler vLLM-Client (Modell z. B. gemini4-31b)
- ``sandbox``       Prozess-Sandbox für Tool Calling (rlimits, Timeout, Egress-Sperre)
- ``wasm_sandbox``  WASM-Sandbox (Path A, optional) — ~80x schneller für numerische Tools
- ``tools``         Tool-Registry mit JSON-Schema-Validierung + Builtins
- ``billing``       API-Keys, Token-Ledger, Budgets (SQLite)
- ``xai``           Trace-Store + Explain: jeder Run vollständig begründbar
- ``runtime``       Der eigentliche Agent-Loop (ReAct-Stil, gatekeeper-gesichert)
"""

from apps.agent_layer.billing import BillingLedger, BudgetExceededError, PriceTable
from apps.agent_layer.llm import ChatResult, LLMError, VLLMClient
from apps.agent_layer.ratelimit import RateLimiter
from apps.agent_layer.runner import AsyncRunner, QueueFullError, TenantQueueFullError
from apps.agent_layer.runtime import AgentRuntime, RunResult
from apps.agent_layer.sandbox import ProcessSandbox, SandboxPolicy, SandboxResult
from apps.agent_layer.sharding import Shard, ShardManifest
from apps.agent_layer.simllm import SimulatedLLM
from apps.agent_layer.tools import ToolRegistry, ToolSpec, builtin_registry
from apps.agent_layer.wasm_sandbox import (
    WasmSandbox,
    WasmSandboxPolicy,
    WasmSandboxResult,
    WasmUnavailableError,
    wasm_available,
)
from apps.agent_layer.xai import TraceStore

__all__ = [
    "AgentRuntime",
    "AsyncRunner",
    "BillingLedger",
    "BudgetExceededError",
    "ChatResult",
    "LLMError",
    "PriceTable",
    "ProcessSandbox",
    "QueueFullError",
    "RateLimiter",
    "RunResult",
    "SandboxPolicy",
    "SandboxResult",
    "Shard",
    "ShardManifest",
    "SimulatedLLM",
    "TenantQueueFullError",
    "ToolRegistry",
    "ToolSpec",
    "TraceStore",
    "VLLMClient",
    "WasmSandbox",
    "WasmSandboxPolicy",
    "WasmSandboxResult",
    "WasmUnavailableError",
    "builtin_registry",
    "wasm_available",
]
