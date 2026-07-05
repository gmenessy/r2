"""Memory Cockpit — die 'One More Thing'-App."""

import json
import threading
import urllib.request

from apps.memory_cockpit.cockpit import build_cockpit
from apps.memory_cockpit.server import create_server


def test_seed_produces_rich_graph():
    c = build_cockpit()
    g = c.graph_data(c.demo_case)
    assert len(g["nodes"]) >= 5
    rels = {e["rel"] for e in g["edges"]}
    # Alle Kernbeziehungen sind vertreten.
    assert {"supersedes", "exception_to", "contradicts", "depends_on"} <= rels


def test_contradiction_edge_has_winner_and_loser_marked():
    c = build_cockpit()
    g = c.graph_data(c.demo_case)
    contradicts = [e for e in g["edges"] if e["rel"] == "contradicts"]
    assert contradicts and contradicts[0]["meta"]["winner"] is not None
    # Der Verlierer (praktikant, trust 0.2) bleibt im Graph, aber als
    # 'contradicted' (im UI ausgegraut) — nicht mehr aktiv.
    by_label = {n["label"]: n for n in g["nodes"]}
    loser = next((n for la, n in by_label.items() if la and "nicht PostgreSQL" in la), None)
    assert loser is not None and loser["status"] == "contradicted"


def test_node_size_reflects_trust():
    c = build_cockpit()
    nodes = {n["label"]: n for n in c.graph_data(c.demo_case)["nodes"]}
    # ops-Karten (trust 1.0) sind höher vertraut als der Default (0.6).
    pg = next(n for la, n in nodes.items() if "PostgreSQL" in (la or ""))
    assert pg["trust"] == 1.0


def test_card_detail_exposes_relationships():
    c = build_cockpit()
    g = c.graph_data(c.demo_case)
    # Nimm einen Knoten mit Beziehung.
    with_edge = {e["src"] for e in g["edges"]} | {e["dst"] for e in g["edges"]}
    detail = c.card_detail(next(iter(with_edge)))
    assert "card" in detail and "relationships" in detail
    assert len(detail["relationships"]) >= 1


def test_live_gatekeeper_blocks_repeat_and_governance():
    c = build_cockpit()
    # Der geseedete gescheiterte Fix hat eine vertraute Alternative → suggest_alternative.
    d = c.simulate(c.demo_case, {"action_type": "apply_fix",
                                 "error_signature": "TimeoutError: gateway"})
    assert d["mode"] == "suggest_alternative"
    # Governance-Intent (destroy prod) blockt eine umbenannte destruktive Aktion.
    d2 = c.simulate(c.demo_case, {"action_type": "drop_prod_tables"})
    assert d2["mode"] == "block"
    # Fragile Datei erzwingt Review.
    d3 = c.simulate(c.demo_case, {"action_type": "modify_file", "files": ["payment.py"]})
    assert d3["mode"] == "require_review"


def test_http_roundtrip_and_ui_served():
    cockpit = build_cockpit()
    server = create_server(cockpit, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base}/") as r:
            assert "Memory Cockpit" in r.read().decode()
        with urllib.request.urlopen(f"{base}/api/graph?case={cockpit.demo_case}") as r:
            graph = json.loads(r.read())
        assert graph["nodes"] and graph["edges"]

        req = urllib.request.Request(
            f"{base}/api/simulate",
            data=json.dumps({"case": cockpit.demo_case, "action_type": "drop_prod_tables"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())["mode"] == "block"

        with urllib.request.urlopen(f"{base}/api/wiki?case={cockpit.demo_case}") as r:
            assert f"# Akte: {cockpit.demo_case}" in r.read().decode()
    finally:
        server.shutdown()


def test_seed_is_idempotent():
    cockpit = build_cockpit()
    n_before = len(cockpit.graph_data(cockpit.demo_case)["nodes"])
    cockpit.seed_demo()
    assert len(cockpit.graph_data(cockpit.demo_case)["nodes"]) == n_before
