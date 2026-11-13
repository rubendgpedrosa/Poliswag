"""Tests for modules.hermiswag_client and its use by ConversationModel."""

import json
from unittest.mock import AsyncMock, MagicMock

from modules.conversation_model import AGENT_INSTRUCTIONS, ConversationModel
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
        await model.line("greeting", text="Há Kubfu?", session="s7", history=history)
        == "Há Kubfu!"
    )
    session, instructions, prompt = agent.ask.await_args.args
    assert session == "s7" and instructions == AGENT_INSTRUCTIONS
    assert "map_query" in instructions and "não uses ferramentas" not in instructions
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
    assert await model.line("greeting", text="Olá?", session="s7") == "ok"
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


def test_learning_batches_cut_by_size_and_keep_the_channel_name(tmp_path):
    from modules.hermiswag_learning import LearningState, batches

    out = batches("convívio", [("Rita", "a" * 50), ("Zé", "b" * 50)], limit=80)
    assert len(out) == 2 and all(p.startswith("Canal: #convívio\n") for p in out)
    assert batches("x", []) == []
    state = LearningState(tmp_path / "s.json")
    assert state.load() == {}
    state.save({"1": 5})
    assert state.load() == {"1": 5}


def test_learning_skips_its_own_replies_in_conversations_only():
    from types import SimpleNamespace as N

    from modules.hermiswag_learning import learnable

    me = N(id=1)
    own = N(webhook_id=None, author=N(id=1, bot=True))
    member = N(webhook_id=None, author=N(id=2, bot=False))
    other_bot = N(webhook_id=None, author=N(id=3, bot=True))
    hook = N(webhook_id=9, author=N(id=2, bot=False))
    assert [learnable(m, me, True) for m in (own, member, other_bot, hook)] == [
        False,
        True,
        False,
        False,
    ]
    assert [learnable(m, me, False) for m in (own, member, other_bot, hook)] == [
        True,
        True,
        False,
        False,
    ]


def test_open_questions_wait_one_minute_and_skip_answered_ones():
    from datetime import timedelta
    from types import SimpleNamespace as N

    from modules.hermiswag_proactive import candidates, looks_like_question, utcnow

    now = utcnow()

    def msg(mid, text, minutes, bot=False, reference=None, mentions=()):
        return N(
            id=mid,
            content=text,
            created_at=now - timedelta(minutes=minutes),
            author=N(id=mid + 1000, bot=bot),
            webhook_id=None,
            reference=None if reference is None else N(message_id=reference),
            mentions=list(mentions),
        )

    messages = [
        msg(1, "Alguém sabe como se evolui o Kubfu?", 45),
        msg(2, "Onde é que fica o Gym da Sé?", 50),
        msg(3, "isso é verdade", 40, reference=2),
        msg(4, "Como faço reset ao Go Plus?", 0.5),
        msg(
            10, "Como se liga o Go Plus ao telemóvel?", 2
        ),  # still under 3 min  # too recent for a 3 min floor
        msg(9, "Qual é o melhor counter para o Garchomp?", 15),
        msg(5, "Quando volta o Dialga?", 300),  # too old
        msg(6, "Há Raids hoje?", 40, bot=True),
        msg(7, "Como se chama o evento?", 40, mentions=[N(id=99)]),
        msg(8, "ok obrigado", 35),
    ]
    found = candidates(messages, now, handled={1}, own_id=99)
    assert [m.id for m, _ in found] == [9]
    found = candidates(messages, now, handled=set(), own_id=99)
    assert [m.id for m, _ in found] == [1, 9]
    assert [m.id for m in found[0][1]][:2] == [2, 3]
    assert looks_like_question("O Go Plus não liga, alguém sabe?")
    assert not looks_like_question("ok?")


def test_unprompted_answers_need_confidence_and_a_checked_source():
    import json as j

    from modules.hermiswag_proactive import confirmed_reply

    ok = {"reply": "Reinicia a app.", "confident": True, "verified_with": "web"}
    assert confirmed_reply(j.dumps(ok)) == "Reinicia a app."
    assert confirmed_reply(j.dumps({**ok, "confident": False})) is None
    assert confirmed_reply(j.dumps({**ok, "verified_with": ""})) is None
    assert confirmed_reply(j.dumps({**ok, "reply": ""})) is None
    tip = {**ok, "reply": "Costuma resolver reiniciar a app. Qual é o telemóvel?"}
    assert confirmed_reply(j.dumps(tip)).endswith("Qual é o telemóvel?")
    assert confirmed_reply("Reinicia a app.") is None


async def test_outside_learning_channels_the_agent_is_told_not_to_remember(tmp_path):
    from modules.conversation_model import NO_MEMORY_RULE

    agent = MagicMock()
    agent.ask = AsyncMock(return_value=json.dumps({"reply": "ok"}))
    model = ConversationModel(tmp_path, agent)
    model.jobs = MagicMock()
    await model.line("greeting", text="Olá?", session="s1", learn=False)
    assert NO_MEMORY_RULE in agent.ask.await_args.args[1]
    await model.line("greeting", text="Olá?", session="s1")
    assert NO_MEMORY_RULE not in agent.ask.await_args.args[1]


def test_staff_rules_only_in_the_moderation_channel():
    from modules.conversation_model import (
        NO_MEMORY_RULE,
        STAFF_RULES,
        agent_instructions,
    )

    assert STAFF_RULES not in agent_instructions(True)
    staff = agent_instructions(False, True)
    assert STAFF_RULES in staff and NO_MEMORY_RULE in staff and "!anunciar" in staff


def test_staff_gate_lets_tool_questions_through_but_not_credentials():
    from modules.public_conversation import private_topic, secret_topic

    question = "como faço um scan de quests e onde vejo os logs?"
    assert private_topic(question) and not secret_topic(question)
    assert secret_topic("qual é a password do scanner?")


async def test_staff_answers_may_mention_everyone_without_pinging():
    from unittest.mock import AsyncMock, MagicMock

    from modules.public_conversation import PublicConversation

    model = MagicMock()
    model.line = AsyncMock(
        return_value="O !anunciar publica e o @everyone vai tal e qual."
    )
    public = PublicConversation(MagicMock(), MagicMock(), model, None)
    staff = await public.reply("como funciona o anúncio?", staff=True)
    assert "@ everyone" in staff
    member = await public.reply("como funciona o anúncio?")
    assert "@everyone" not in member and "@ everyone" not in member


async def test_the_agent_knows_who_is_asking(tmp_path):
    agent = MagicMock()
    agent.ask = AsyncMock(return_value=json.dumps({"reply": "ok"}))
    model = ConversationModel(tmp_path, agent)
    model.jobs = MagicMock()
    await model.line("greeting", text="Há Kubfu?", session="s1", author="JMBoy")
    assert '"author": "JMBoy"' in agent.ask.await_args.args[2]


def test_owner_is_always_faynn_in_the_context(mocker):
    from types import SimpleNamespace as N

    from cogs.mentions import speaker_name
    from modules.config import Config

    mocker.patch.object(Config, "MY_ID", 42)
    assert speaker_name(N(id=42, display_name="Not Faynn")) == "faynn"
    assert speaker_name(N(id=7, display_name="JMBoy")) == "JMBoy"


def test_person_budget_caps_proactive_answers():
    from datetime import timedelta
    from types import SimpleNamespace as N

    from modules.hermiswag_proactive import (
        PERSON_LIMIT,
        candidates,
        record_answer,
        utcnow,
    )

    now = utcnow()
    message = N(
        id=1,
        content="Alguém sabe como se evolui o Kubfu agora?",
        created_at=now - timedelta(minutes=5),
        author=N(id=42, bot=False),
        webhook_id=None,
        reference=None,
        mentions=[],
    )
    people = {}
    assert [m.id for m, _ in candidates([message], now, set(), 99, people)] == [1]
    for _ in range(PERSON_LIMIT):
        record_answer(people, 42, now)
    assert candidates([message], now, set(), 99, people) == []


def test_proactive_state_reads_the_old_flat_file_and_keeps_people(tmp_path):
    from modules.hermiswag_proactive import ProactiveState

    path = tmp_path / "proactive.json"
    path.write_text('{"555": [1, 2]}')
    state = ProactiveState(path)
    messages, people = state.load()
    assert messages == {"555": [1, 2]}
    assert people == {}
    state.save(messages, {"42": ["2026-11-13T10:00:00+00:00"]})
    messages, people = state.load()
    assert messages == {"555": [1, 2]}
    assert people == {"42": ["2026-11-13T10:00:00+00:00"]}
