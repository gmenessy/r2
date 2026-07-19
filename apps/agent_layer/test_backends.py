"""Contract-Test der Persistenz-Naht (S5-3): die SQLite-Stores erfüllen den
Vertrag strukturell — jedes künftige Backend muss dasselbe leisten."""

from __future__ import annotations

from apps.agent_layer.backends import LedgerBackend, TraceBackend
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.xai import TraceStore


def test_billing_ledger_satisfies_ledger_backend() -> None:
    assert isinstance(BillingLedger(), LedgerBackend)


def test_trace_store_satisfies_trace_backend() -> None:
    assert isinstance(TraceStore(), TraceBackend)


def test_backend_protocol_rejects_incomplete_impl() -> None:
    class Partial:
        def begin(self, run_id, tenant, goal): ...

    assert not isinstance(Partial(), TraceBackend)
