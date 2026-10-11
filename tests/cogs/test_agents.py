from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.agents import Agents
from modules.config import Config
from modules.permissions import is_owner


@pytest.fixture
def cog(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "MY_ID", 123)
    monkeypatch.setattr(Config, "AGENT_JOBS_DIR", str(tmp_path / "jobs"))
    bot = MagicMock()
    bot.user.id = 456
    return Agents(bot)


def ctx(author=123, content="claude explain", guild=None):
    context = MagicMock()
    context.author.id = author
    context.guild = guild
    context.message.content = content
    context.send = AsyncMock()
    context.author.send = AsyncMock()
    context.author.create_dm = AsyncMock(return_value=MagicMock(send=AsyncMock()))
    return context


@pytest.mark.parametrize("author", [999, 42])
async def test_nonowner_direct_request_never_enqueued(cog, author):
    cog.submit = AsyncMock()
    assert not await cog.handle_direct_request(ctx(author=author))
    cog.submit.assert_not_awaited()


async def test_owner_dm_routes_explicit_code_request(cog):
    cog.submit = AsyncMock()
    context = ctx(content="code openai fix this")
    assert await cog.handle_direct_request(context)
    cog.submit.assert_awaited_once_with(context, "code", "openai", "fix this")


async def test_owner_natural_request_is_code_capable(cog):
    cog.submit = AsyncMock()
    context = ctx()
    await cog.submit_natural_request(context, "fix the quest bug")
    cog.submit.assert_awaited_once_with(context, "code", "claude", "fix the quest bug")


async def test_owner_natural_request_can_choose_openai(cog):
    cog.submit = AsyncMock()
    context = ctx()
    await cog.submit_natural_request(context, "use OpenAI to fix this")
    cog.submit.assert_awaited_once_with(
        context, "code", "codex", "use OpenAI to fix this"
    )


async def test_nonowner_cannot_call_natural_delegation(cog):
    cog.submit = AsyncMock()
    await cog.submit_natural_request(ctx(author=999), "fix this")
    cog.submit.assert_not_awaited()


async def test_channel_requires_leading_bot_mention(cog):
    cog.submit = AsyncMock()
    assert not await cog.handle_direct_request(ctx(guild=MagicMock()))
    context = ctx(content="<@456> claude explain", guild=MagicMock())
    assert await cog.handle_direct_request(context)
    cog.submit.assert_awaited_once_with(context, "ask", "claude", "explain")


async def test_closed_dm_does_not_launch_work(cog):
    import discord

    context = ctx()
    context.send = AsyncMock()
    context.author.create_dm = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "blocked")
    )
    cog.jobs.submit = MagicMock()
    await cog.submit(context, "code", "claude", "fix")
    cog.jobs.submit.assert_not_called()


def test_every_agent_command_has_owner_gate(cog):
    assert cog.cog_check(ctx())
    assert not cog.cog_check(ctx(author=999))
    assert all(is_owner in command.checks for command in cog.get_commands())


async def test_result_is_private_and_mentions_disabled(cog):
    from modules.agent_jobs import write_json

    directory = cog.jobs.directory("abcdef123456")
    directory.mkdir(parents=True)
    write_json(directory / "request.json", {})
    (directory / "answer.txt").write_text("@everyone " + "x" * 4000)
    user = MagicMock(send=AsyncMock())
    await cog.send_result(
        user,
        {"id": directory.name, "provider": "claude", "state": "completed"},
        full=True,
    )
    sent = user.send.call_args.kwargs
    assert not sent["allowed_mentions"].everyone
    assert "file" in sent


async def test_delivery_marker_prevents_repeated_completion_dm(cog):
    from modules.agent_jobs import write_json

    directory = cog.jobs.directory("abcdef123456")
    directory.mkdir(parents=True)
    write_json(
        directory / "request.json",
        {"id": directory.name, "owner_id": "123", "created": 1, "provider": "claude"},
    )
    write_json(directory / "result.json", {"state": "completed"})
    cog.send_result = AsyncMock()
    await cog.deliver_results()
    await cog.deliver_results()
    cog.send_result.assert_awaited_once()


@pytest.mark.parametrize(
    "question",
    [
        "Porque é que o céu é azul?",
        "Help me plan a birthday party",
        "explain scanner accounts",
        "use Codex to explain gravity",
    ],
)
async def test_owner_plain_dm_routes_any_question(cog, question):
    cog.submit_natural_request = AsyncMock()
    context = ctx(content=question)
    assert await cog.handle_direct_request(context)
    cog.submit_natural_request.assert_awaited_once_with(context, question)


@pytest.mark.parametrize("content", [" ", "!unknown command"])
async def test_owner_empty_dm_or_unknown_command_is_not_an_agent_request(cog, content):
    cog.submit_natural_request = AsyncMock()
    assert not await cog.handle_direct_request(ctx(content=content))
    cog.submit_natural_request.assert_not_awaited()


async def test_owner_unmentioned_server_question_is_not_routed(cog):
    cog.submit_natural_request = AsyncMock()
    assert not await cog.handle_direct_request(
        ctx(content="Why is the sky blue?", guild=MagicMock())
    )
    cog.submit_natural_request.assert_not_awaited()


async def test_owner_general_question_uses_claude(cog):
    cog.submit = AsyncMock()
    context = ctx()
    await cog.submit_natural_request(context, "Why is the sky blue?")
    cog.submit.assert_awaited_once_with(
        context, "code", "claude", "Why is the sky blue?"
    )


async def test_owner_general_question_can_explicitly_choose_codex(cog):
    cog.submit = AsyncMock()
    context = ctx()
    await cog.submit_natural_request(context, "use Codex to explain gravity")
    cog.submit.assert_awaited_once_with(
        context, "code", "codex", "use Codex to explain gravity"
    )


async def test_successful_submission_is_silent_until_result(cog):
    context = ctx(guild=MagicMock())
    cog.snapshot = AsyncMock(return_value={})
    cog.jobs.submit = MagicMock(
        return_value={"id": "abcdef123456", "provider": "claude"}
    )
    await cog.submit(context, "code", "claude", "fix the quest bug")
    cog.jobs.submit.assert_called_once()
    context.author.create_dm.return_value.send.assert_not_awaited()
    context.author.send.assert_not_awaited()
    context.send.assert_not_awaited()


def save_reply(
    cog,
    state="completed",
    kind="code",
    revision=0,
    details="Full technical report",
    reply="Corrigido e testado.",
):
    from modules.agent_jobs import write_json

    directory = cog.jobs.directory("abcdef123456")
    directory.mkdir(parents=True, exist_ok=True)
    write_json(
        directory / "request.json",
        {
            "id": directory.name,
            "owner_id": "123",
            "provider": "claude",
            "mode": "code",
            "prompt": "fix",
            "created": 1,
        },
    )
    write_json(
        directory / "result.json",
        {
            "state": state,
            "kind": kind,
            "reply": reply,
            "revision": revision,
        },
    )
    (directory / "answer.txt").write_text(details)
    return cog.jobs.get(directory.name, 123)


async def test_automatic_code_result_is_only_a_short_sentence(cog):
    job = save_reply(cog)
    user = MagicMock(send=AsyncMock())
    await cog.send_result(user, job)
    assert user.send.call_args.args == ("Corrigido e testado.",)
    assert "file" not in user.send.call_args.kwargs
    assert "embed" not in user.send.call_args.kwargs
    assert not user.send.call_args.kwargs["allowed_mentions"].everyone


async def test_stats_keep_the_actual_numbers_and_rows(cog):
    job = save_reply(
        cog,
        kind="stats",
        reply="Aqui estão os números.",
        details="Leiria: 42 Pokémon\nMarinha: 17 raids",
    )
    user = MagicMock(send=AsyncMock())
    await cog.send_result(user, job)
    assert (
        user.send.call_args.args[0]
        == "Aqui estão os números.\n\nLeiria: 42 Pokémon\nMarinha: 17 raids"
    )


async def test_worker_error_details_do_not_become_a_chat_reply(cog):
    job = save_reply(cog, state="failed", details="SECRET stack trace")
    job["message"] = "SECRET stderr"
    user = MagicMock(send=AsyncMock())
    await cog.send_result(user, job)
    assert "SECRET" not in user.send.call_args.args[0]
    assert "embed" not in user.send.call_args.kwargs


async def test_question_and_resumed_completion_are_each_delivered_once(cog):
    job = save_reply(
        cog, state="waiting_input", reply="Qual cidade devo usar?", details=""
    )
    cog.send_result = AsyncMock()
    await cog.deliver_results()
    await cog.deliver_results()
    assert cog.send_result.await_count == 1
    cog.jobs.resume(job["id"], 123, "Leiria")
    await cog.deliver_results()
    assert cog.send_result.await_count == 1
    save_reply(cog, revision=1)
    await cog.deliver_results()
    await cog.deliver_results()
    assert cog.send_result.await_count == 2


async def test_plain_owner_answer_resumes_waiting_task_silently(cog):
    job = save_reply(
        cog, state="waiting_input", reply="Qual cidade devo usar?", details=""
    )
    cog.submit = AsyncMock()
    context = ctx(content="Leiria")
    await cog.submit_natural_request(context, "Leiria")
    resumed = cog.jobs.get(job["id"], 123)
    assert resumed["state"] == "queued" and resumed["turns"][0]["answer"] == "Leiria"
    cog.submit.assert_not_awaited()
    context.send.assert_not_awaited()


async def test_running_jobs_are_never_delivered_as_progress(cog):
    save_reply(cog, state="running")
    cog.send_result = AsyncMock()
    await cog.deliver_results()
    cog.send_result.assert_not_awaited()


async def test_long_stats_are_complete_in_file_with_short_cover_sentence(cog):
    details = "Leiria: 42 Pokémon\n" * 200
    job = save_reply(cog, kind="stats", reply="Aqui estão os números.", details=details)
    user = MagicMock(send=AsyncMock())
    await cog.send_result(user, job)
    assert user.send.call_args.args == ("Aqui estão os números.",)
    attachment = user.send.call_args.kwargs["file"]
    assert attachment.fp.read().decode() == details
