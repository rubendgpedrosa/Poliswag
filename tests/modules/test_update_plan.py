"""Tests for modules.update_plan and cogs.update_plan."""

import pytest

from modules import update_plan
from modules.config import Config


@pytest.fixture(autouse=True)
def plan_file(tmp_path, mocker):
    mocker.patch.object(Config, "UPDATE_PLAN_FILE", str(tmp_path / "plan.json"))


def _state(status="scheduled"):
    return {
        "plan_id": "p1",
        "when": "04:00",
        "run_date": "2026-11-14",
        "items": [{"key": "loki", "label": "Loki", "from": "3.7.9", "to": "3.7.10"}],
        "status": status,
    }


def test_accept_keeps_cancel_and_cancel_closes_the_plan():
    state = update_plan.decide(_state(), "accept", "p1")
    assert state["status"] == "accepted"
    labels = [b["label"] for b in update_plan.components(state)[0]["components"]]
    assert labels == ["Atualizar já", "Cancelar"]
    state = update_plan.decide(state, "cancel", "p1")
    assert state["status"] == "cancelled"
    assert update_plan.components(state) == []


def test_stale_or_finished_plans_ignore_buttons():
    assert update_plan.decide(_state(), "accept", "other") is None
    assert update_plan.decide(_state("done"), "cancel", "p1") is None
    assert update_plan.decide(_state("cancelled"), "accept", "p1") is None


def test_embed_marks_now_and_night_items_then_results():
    state = _state()
    state["items"].append(
        {
            "key": "immich",
            "label": "Immich",
            "from": "3.4.1",
            "to": "3.5.0",
            "when": "night",
        }
    )
    state["items"][0]["when"] = "now"
    embed = update_plan.embed(state)
    assert (
        "⚡ **Loki**" in embed["description"]
        and "🌙 **Immich**" in embed["description"]
    )
    assert [i["key"] for i in update_plan.pending(state, "now")] == ["loki"]
    state["items"][0]["result"] = {"ok": True, "detail": "atualizado para 3.7.10"}
    embed = update_plan.embed(state)
    assert "✅ **Loki**" in embed["description"]
    assert "Corre esta noite" in embed["footer"]["text"]
    state["items"][1]["result"] = {"ok": False, "detail": "falhou"}
    assert update_plan.embed(state)["color"] == update_plan.FAILED_COLOUR


def test_state_round_trip():
    update_plan.save(_state())
    assert update_plan.load()["plan_id"] == "p1"


def test_update_now_and_buttons_order():
    labels = [b["label"] for b in update_plan.components(_state())[0]["components"]]
    assert labels == ["Atualizar já", "Aceitar", "Cancelar"]
    state = update_plan.decide(_state(), "now", "p1")
    assert state["status"] == "now"
    assert update_plan.components(state) == []
    assert "já" in update_plan.embed(state)["footer"]["text"]
