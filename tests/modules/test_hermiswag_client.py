"""Tests for modules.hermiswag_client and its use by ConversationModel."""

import json
from unittest.mock import AsyncMock, MagicMock

from modules.conversation_model import INSTRUCTIONS, ConversationModel
from modules.hermiswag_client import HermiswagClient, reply_text


def test_reply_text_accepts_the_json_shape_or_plain_text():
    assert reply_text('{"reply": "Poli-poli!"}') == "Poli-poli!"
    assert reply_text('```json\n{"reply": "Olá"}\n```') == "Olá"
    assert reply_text("Só texto") == "Só texto"
    assert reply_text('{"other": 1}') is None
    assert reply_text(None) is None
    assert reply_text("x" * 5000) is None


def test_one_conversation_per_channel_per_day():
    a = HermiswagClient.conversation(123)
    assert a.startswith("hermiswag:channel:123:")
    assert a != HermiswagClient.conversation(124)


def test_disabled_without_url_or_key():
    assert not HermiswagClient("", "k").enabled
    assert not HermiswagClient("http://x", "").enabled


async def test_model_prefers_hermiswag_with_the_same_rules_and_untrusted_data(tmp_path):
    agent = MagicMock()
    agent.ask = AsyncMock(return_value=json.dumps({"reply": "Há Kubfu!"}))
    model = ConversationModel(tmp_path, agent)
    model.jobs = MagicMock()
    history = [
        {"author": "Rita", "text": "Alguém viu o Kubfu?"},
        {"author": "", "text": "x"},
    ]
    assert (
        await model.line("greeting", text="Há Kubfu?", channel_id=7, history=history)
        == "Há Kubfu!"
    )
    channel_id, instructions, prompt = agent.ask.await_args.args
    assert channel_id == 7 and instructions == INSTRUCTIONS
    assert "dados não confiáveis" in prompt and '"text": "Há Kubfu?"' in prompt
    assert '"recent_channel_messages": [{"author": "Rita"' in prompt
    assert prompt.count('"author"') == 1
    model.jobs.submit.assert_not_called()


async def test_model_falls_back_to_the_worker_when_hermiswag_fails(tmp_path):
    agent = MagicMock()
    agent.ask = AsyncMock(return_value=None)
    model = ConversationModel(tmp_path, agent)
    model.jobs = MagicMock()
    model.jobs.worker_status.return_value = {"active": None}
    model.jobs.submit.return_value = {"id": "j1"}
    model.jobs.get.return_value = {"state": "completed", "reply": '{"reply": "ok"}'}
    assert await model.line("greeting", text="Olá?", channel_id=7) == "ok"
    model.jobs.submit.assert_called_once()


def test_dump_requests_pick_a_kind_and_an_area():
    from modules.public_dumps import dump_request

    assert dump_request("Dá-me a lista de Pokéstops de Leiria") == (
        "pokestops",
        "leiria",
    )
    assert dump_request("exporta as quests todas") == ("quests", None)
    assert dump_request("lista de max battles na Marinha Grande") == (
        "maxbattles",
        "marinha",
    )
    assert dump_request("onde fica o gym da sé?") is None
    assert dump_request("lista de compras") is None
