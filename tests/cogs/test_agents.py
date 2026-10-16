import time
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
    context.channel.id = 789
    context.channel.guild.id = 321
    context.message.content = content
    context.send = AsyncMock()
    context.reply = AsyncMock()
    context.message.add_reaction = AsyncMock()
    context.author.send = AsyncMock()
    context.author.create_dm = AsyncMock(return_value=MagicMock(send=AsyncMock()))
    return context


async def test_owner_tagged_explicit_code_request_is_routed(cog):
    cog.submit = AsyncMock()
    context = ctx()
    await cog.submit_natural_request(context, "code openai fix this")
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


async def test_failed_channel_acknowledgement_does_not_discard_owner_work(cog):
    import discord

    context = ctx(guild=MagicMock())
    context.message.add_reaction.side_effect = discord.Forbidden(
        MagicMock(status=403), "blocked"
    )
    cog.snapshot = AsyncMock(return_value={})
    cog.jobs.submit = MagicMock(
        return_value={"id": "abcdef123456", "provider": "claude"}
    )
    await cog.submit(context, "ask", "claude", "stats")
    cog.jobs.submit.assert_called_once()


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
        "code openai fix this",
    ],
)
async def test_owner_untagged_message_never_starts_work(question):
    import main

    context = ctx(content=question)
    context.command = None
    bot = MagicMock()
    bot.user.id = 456
    bot.get_context = AsyncMock(return_value=context)
    bot.invoke = AsyncMock()
    bot._mentions_poliswag = lambda m: main.Poliswag._mentions_poliswag(bot, m)
    message = MagicMock(author=MagicMock(bot=False), webhook_id=None, content=question)

    await main.Poliswag.process_commands(bot, message)

    bot.get_cog.assert_not_called()
    bot.invoke.assert_awaited_once_with(context)


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


async def test_submission_acknowledges_before_snapshot_and_work(cog):
    context = ctx(guild=MagicMock())

    async def snapshot():
        context.message.add_reaction.assert_awaited_once_with("👀")
        return {}

    cog.snapshot = AsyncMock(side_effect=snapshot)
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
    assert user.send.call_args.args[0] == "Corrigido e testado."
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


async def test_plain_owner_answer_resumes_waiting_task_with_acknowledgement(cog):
    job = save_reply(
        cog, state="waiting_input", reply="Qual cidade devo usar?", details=""
    )
    cog.submit = AsyncMock()
    context = ctx(content="Leiria")
    await cog.submit_natural_request(context, "Leiria")
    resumed = cog.jobs.get(job["id"], 123)
    assert resumed["state"] == "queued" and resumed["turns"][0]["answer"] == "Leiria"
    cog.submit.assert_not_awaited()
    context.message.add_reaction.assert_awaited_once_with("👀")
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
    assert user.send.call_args.args[0] == "Aqui estão os números."
    attachment = user.send.call_args.kwargs["file"]
    assert attachment.fp.read().decode() == details


async def test_channel_submission_preserves_destination_without_requiring_dms(cog):
    import discord

    context = ctx(guild=MagicMock())
    context.author.create_dm.side_effect = discord.Forbidden(
        MagicMock(status=403), "blocked"
    )
    cog.snapshot = AsyncMock(return_value={})
    cog.jobs.submit = MagicMock(
        return_value={"id": "abcdef123456", "provider": "claude"}
    )
    await cog.submit(context, "code", "claude", "Generate a leaderboard image")
    assert cog.jobs.submit.call_args.kwargs["destination"] == {
        "type": "channel",
        "channel_id": 789,
        "guild_id": 321,
    }
    context.author.create_dm.assert_not_awaited()


async def test_explicit_dm_overrides_originating_channel(cog):
    context = ctx(guild=MagicMock())
    cog.snapshot = AsyncMock(return_value={})
    cog.jobs.submit = MagicMock(
        return_value={"id": "abcdef123456", "provider": "claude"}
    )
    await cog.submit(context, "code", "claude", "Generate image. Send via DM")
    assert cog.jobs.submit.call_args.kwargs["destination"] == {"type": "dm"}
    context.author.create_dm.assert_awaited_once()


@pytest.mark.parametrize(
    "instruction",
    ["Send to <#999>", "Envia para #moderação", "Send to channel moderação"],
)
def test_explicit_channel_overrides_origin(cog, instruction):
    context = ctx(guild=MagicMock())
    channel = MagicMock(id=999, name="moderação")
    channel.name = "moderação"
    channel.guild.id = 321
    context.guild.channels = [channel]
    context.guild.threads = []
    assert cog.destination_for(context, instruction) == {
        "type": "channel",
        "channel_id": 999,
        "guild_id": 321,
    }


def test_unknown_requested_channel_does_not_silently_redirect(cog):
    context = ctx(guild=MagicMock())
    context.guild.channels = []
    context.guild.threads = []
    with pytest.raises(ValueError):
        cog.destination_for(context, "Send to #missing")


async def test_image_and_long_report_delivered_to_saved_channel_once(cog):
    from modules.agent_artifacts import stage_artifacts
    from modules.agent_jobs import read_json, write_json

    job = save_reply(cog, kind="stats", details="Report row\n" * 300)
    directory = cog.jobs.directory(job["id"])
    worktree = directory / "worktree"
    worktree.mkdir()
    image_bytes = b"\x89PNG\r\n\x1a\nimage content"
    (worktree / "leaderboard.png").write_bytes(image_bytes)
    artifacts = stage_artifacts(worktree, directory, ["leaderboard.png"], 0)
    request = read_json(directory / "request.json")
    write_json(
        directory / "request.json",
        {
            **request,
            "destination": {
                "type": "channel",
                "channel_id": 789,
                "guild_id": 321,
            },
        },
    )
    write_json(
        directory / "result.json",
        {**read_json(directory / "result.json"), "artifacts": artifacts},
    )
    channel = MagicMock(send=AsyncMock())
    channel.guild.id = 321
    cog.poliswag.get_channel.return_value = channel
    await cog.deliver_results()
    await cog.deliver_results()
    channel.send.assert_awaited_once()
    files = channel.send.call_args.kwargs["files"]
    assert [f.filename for f in files] == ["leaderboard.png", f"{job['id']}.txt"]
    assert files[0].fp.read() == image_bytes
    assert files[1].fp.read().decode() == "Report row\n" * 300
    cog.poliswag.get_user.assert_not_called()


async def test_failed_channel_delivery_retries_without_dm_fallback(cog):
    import discord
    from modules.agent_jobs import read_json, write_json

    job = save_reply(cog)
    directory = cog.jobs.directory(job["id"])
    write_json(
        directory / "request.json",
        {
            **read_json(directory / "request.json"),
            "destination": {
                "type": "channel",
                "channel_id": 789,
                "guild_id": 321,
            },
        },
    )
    channel = MagicMock(
        send=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "blocked"))
    )
    channel.guild.id = 321
    cog.poliswag.get_channel.return_value = channel
    await cog.deliver_results()
    assert not cog.jobs.get(job["id"], 123)["notified"]
    cog.poliswag.get_user.assert_not_called()
    channel.send.side_effect = None
    await cog.deliver_results()
    assert cog.jobs.get(job["id"], 123)["notified"]


async def test_missing_image_does_not_mark_job_delivered(cog):
    from modules.agent_jobs import read_json, write_json

    job = save_reply(cog)
    directory = cog.jobs.directory(job["id"])
    write_json(
        directory / "result.json",
        {
            **read_json(directory / "result.json"),
            "artifacts": [
                {"path": "artifacts/0/missing.png", "filename": "missing.png"},
            ],
        },
    )
    await cog.deliver_results()
    assert not cog.jobs.get(job["id"], 123)["notified"]
    cog.poliswag.get_user.return_value.send.assert_not_called()


def conversation_ctx(channel_id=None):
    context = ctx(guild=MagicMock() if channel_id is not None else None)
    if channel_id is not None:
        context.channel.id = channel_id
    return context


def waiting_job(cog, job_id, origin, *, destination=None):
    from modules.agent_jobs import write_json

    directory = cog.jobs.directory(job_id)
    directory.mkdir(parents=True)
    write_json(
        directory / "request.json",
        {
            "id": job_id,
            "owner_id": "123",
            "provider": "claude",
            "mode": "code",
            "prompt": "question",
            "created": 1,
            "origin": origin,
            "destination": destination or origin,
        },
    )
    write_json(
        directory / "result.json",
        {
            "state": "waiting_input",
            "reply": "Which city?",
            "revision": 0,
        },
    )
    return job_id


@pytest.mark.parametrize("source,target", [(789, None), (None, 789), (789, 999)])
async def test_waiting_task_in_other_conversation_does_not_capture_question(
    cog, source, target
):
    origin = cog.origin_for(conversation_ctx(source))
    job_id = waiting_job(cog, "abcdef123456", origin)
    cog.submit = AsyncMock()
    context = conversation_ctx(target)
    await cog.submit_natural_request(context, "a separate question")
    cog.submit.assert_awaited_once_with(
        context, "code", "claude", "a separate question"
    )
    assert cog.jobs.get(job_id, 123)["state"] == "waiting_input"


@pytest.mark.parametrize("channel_id", [None, 789, 999])
async def test_each_conversation_resumes_only_its_own_waiting_task(cog, channel_id):
    ids = {None: "abcdef123450", 789: "abcdef123451", 999: "abcdef123452"}
    for source, job_id in ids.items():
        waiting_job(cog, job_id, cog.origin_for(conversation_ctx(source)))
    cog.submit = AsyncMock()
    context = conversation_ctx(channel_id)
    await cog.submit_natural_request(context, "Leiria")
    for source, job_id in ids.items():
        job = cog.jobs.get(job_id, 123)
        assert job["state"] == ("queued" if source == channel_id else "waiting_input")
    cog.submit.assert_not_awaited()
    context.message.add_reaction.assert_awaited_once_with("👀")


async def test_delivery_override_does_not_change_followup_conversation(cog):
    origin = cog.origin_for(conversation_ctx(789))
    job_id = waiting_job(cog, "abcdef123456", origin, destination={"type": "dm"})
    cog.submit = AsyncMock()
    await cog.submit_natural_request(conversation_ctx(None), "a new DM question")
    assert cog.jobs.get(job_id, 123)["state"] == "waiting_input"
    cog.submit.assert_awaited_once()
    await cog.submit_natural_request(conversation_ctx(789), "Leiria")
    assert cog.jobs.get(job_id, 123)["state"] == "queued"
    assert cog.jobs.get(job_id, 123)["destination"] == {"type": "dm"}


async def test_independent_questions_are_delivered_to_two_channels_and_dm(cog):
    from modules.agent_jobs import write_json

    cog.jobs.root.mkdir(parents=True, exist_ok=True)
    write_json(
        cog.jobs.root / "worker.json",
        {
            "heartbeat": time.time(),
            "providers": {"claude": True},
        },
    )
    cog.snapshot = AsyncMock(return_value={})
    channels = {i: MagicMock(send=AsyncMock()) for i in (789, 999)}
    for channel in channels.values():
        channel.guild.id = 321
    user = MagicMock(send=AsyncMock())
    cog.poliswag.get_channel.side_effect = channels.get
    cog.poliswag.get_user.return_value = user
    for source, question in [(789, "q1"), (999, "q2"), (None, "q3")]:
        await cog.submit_natural_request(conversation_ctx(source), question)
    jobs = cog.jobs.list(123)
    assert len(jobs) == 3
    for job in jobs:
        assert job["origin"] == job["destination"]
        write_json(
            cog.jobs.directory(job["id"]) / "result.json",
            {
                "state": "completed",
                "reply": f"Answer for {job['prompt']}",
            },
        )
    await cog.deliver_results()
    await cog.deliver_results()
    for target, question in [
        (channels[789], "q1"),
        (channels[999], "q2"),
        (user, "q3"),
    ]:
        target.send.assert_awaited_once()
        assert target.send.call_args.args[0] == f"Answer for {question}"


async def test_natural_answer_preserves_sentences_and_paragraphs(cog):
    text = "Primeira frase. Segunda frase.\n\n" + "Explicação completa. " * 30
    job = save_reply(cog, kind="chat", reply=text)
    target = MagicMock(send=AsyncMock())
    await cog.send_result(target, job)
    assert target.send.call_args.args[0] == text.strip()
    assert "embed" not in target.send.call_args.kwargs


async def test_very_long_answer_includes_complete_text_attachment(cog):
    text = "Explicação completa. " * 400
    job = save_reply(cog, kind="chat", reply=text)
    target = MagicMock(send=AsyncMock())
    await cog.send_result(target, job)
    sent = target.send.call_args.kwargs
    assert len(target.send.call_args.args[0]) <= 2000
    assert "embed" not in sent
    assert sent["file"].fp.read().decode() == text.strip()


async def test_acknowledgement_only_reacts(cog):
    context = ctx()
    await cog.acknowledge(context)
    context.message.add_reaction.assert_awaited_once_with("👀")
    context.reply.assert_not_awaited()
    context.send.assert_not_awaited()
