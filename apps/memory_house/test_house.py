"""Tests für das Memory House (App Nr. 4)."""

import json
import threading
import urllib.request

from brainfump import BrainFumpKernel
from apps.memory_house.house import MemoryHouse
from apps.memory_house.server import create_server


def make_house(seed=True):
    house = MemoryHouse(BrainFumpKernel())
    if seed:
        house.seed_demo()
    return house


def test_failed_heating_attempt_gets_alternative():
    house = make_house()
    result = house.attempt("bad", "heizung_einstellen", signature="heizung_24_um_6")
    assert result["mode"] == "suggest_alternative"
    assert not result["executed"]
    assert "5:30" in result["suggested_alternative"]
    # Im Wohnzimmer gibt es diese Lektion nicht — Räume sind isoliert.
    assert house.attempt("wohnzimmer", "heizung_einstellen", signature="heizung_24_um_6")["executed"]


def test_fragile_breaker_requires_review():
    house = make_house()
    result = house.attempt("keller", "sicherung_schalten", device="sicherung_hauptkasten")
    assert result["mode"] == "require_review"
    assert not result["executed"]


def test_no_vacuum_during_call():
    house = make_house()
    blocked = house.attempt("buero", "staubsaugen", context={"call_aktiv": True})
    assert blocked["mode"] == "block"
    assert not blocked["executed"]
    allowed = house.attempt("buero", "staubsaugen", context={"call_aktiv": False})
    assert allowed["executed"]


def test_door_never_unlocked_automatically():
    house = make_house()
    for room in ("wohnzimmer", "keller"):
        result = house.attempt(room, "tuer_entriegeln")
        assert result["mode"] == "block"
        assert result["findings"][0]["gate"] == "governance"


def test_executed_actions_land_in_room_history():
    house = make_house()
    house.attempt("kueche", "licht_an")
    status = house.room_status("kueche")
    assert any("licht_an" in e["content"] for e in status["recent_events"])


def test_season_change_is_evolution_patch():
    house = make_house(seed=False)
    mode = house.set_mode("wohnzimmer", "Winterbetrieb: Heizung 21°.")
    winter_card = house.kernel.cards.get(mode["card_id"])
    summer_start = "2099-07-01"  # sicher nach valid_from der Winterkarte
    house.change_season(
        "wohnzimmer", mode["card_id"], "Sommerbetrieb: Heizung aus, Markise um 12 Uhr.", summer_start
    )
    cards = house.kernel.cards.active(case_id="wohnzimmer", include_global=False)
    statements = [c.statement for c in cards]
    assert any("Sommerbetrieb" in s for s in statements)
    assert not any("Winterbetrieb" in s for s in statements)
    # Historie bleibt nachvollziehbar: vor dem Wechsel gilt der Winterbetrieb …
    resolved = house.kernel.evolution.resolve(mode["card_id"], on_date=winter_card.valid_from)
    assert "Winterbetrieb" in resolved.statement
    # … danach der Sommerbetrieb.
    resolved = house.kernel.evolution.resolve(mode["card_id"], on_date="2099-08-01")
    assert "Sommerbetrieb" in resolved.statement


def test_rules_survive_restart(tmp_path):
    path = str(tmp_path / "haus")
    house = MemoryHouse(BrainFumpKernel(path))
    house.seed_demo()
    # Neustart: neuer Kernel, neue MemoryHouse-Instanz, gleiche Daten.
    reloaded = MemoryHouse(BrainFumpKernel(path))
    result = reloaded.attempt("buero", "staubsaugen", context={"call_aktiv": True})
    assert result["mode"] == "block"


def test_seed_is_idempotent():
    house = make_house()
    events_before = len(house.kernel.events.query())
    house.seed_demo()
    assert len(house.kernel.events.query()) == events_before


def test_http_roundtrip():
    house = make_house()
    server = create_server(house, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/attempt",
            data=json.dumps(
                {"room": "keller", "action_type": "sicherung_schalten", "device": "sicherung_hauptkasten"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as r:
            result = json.loads(r.read())
        assert result["mode"] == "require_review"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/rooms") as r:
            assert "keller" in json.loads(r.read())["rooms"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert "Memory House" in r.read().decode()
    finally:
        server.shutdown()
