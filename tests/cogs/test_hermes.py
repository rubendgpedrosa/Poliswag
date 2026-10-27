import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock

import discord
import pytest

from cogs.hermes import ApprovalView, Hermes, approval_details
from modules.config import Config


def context(*, owner=True, dm=False):
    ctx = MagicMock()
    ctx.author = MagicMock(spec=discord.User if dm else discord.Member)
    ctx.author.id = 123 if owner else 999
    ctx.author.bot = False
    ctx.guild = None if dm else MagicMock(id=789)
    ctx.author.guild = ctx.guild
    ctx.message.author = ctx.author
    ctx.message.webhook_id = None
    ctx.message.content = "Hello"
    ctx.message.id = 555
    ctx.channel.id = 456
    ctx.message.add_reaction = AsyncMock()
    ctx.reply = AsyncMock()
    ctx.channel.send = AsyncMock()
    return ctx


@pytest.fixture
def cog(monkeypatch):
    monkeypatch.setattr(Config, "MY_ID", 123)
    bot = MagicMock()
    result = Hermes(bot)
    result.client.reply = AsyncMock(return_value=("run-1", "Olá!"))
    return result


async def test_owner_dm_has_only_owner_text_no_history_or_attachments(cog):
    ctx = context(dm=True)
    ctx.message.attachments = [MagicMock()]
    cog.client.forget = MagicMock()
    assert await cog.handle(ctx, "  Olá  ")
    assert cog.client.reply.call_args.args == ("Olá",)
    assert cog.client.reply.call_args.kwargs["message_id"] == 555
    assert cog.client.reply.call_args.kwargs["channel_id"] == 456
    assert callable(cog.client.reply.call_args.kwargs["approval_callback"])
    cog.client.forget.assert_called_once_with("run-1")
    assert ctx.message.add_reaction.await_args_list[0].args == ("👀",)
    ctx.channel.history.assert_not_called()
    assert ctx.reply.call_args.args == ("Olá!",)
    assert not ctx.reply.call_args.kwargs["allowed_mentions"].everyone
    assert not ctx.reply.call_args.kwargs["mention_author"]


@pytest.mark.parametrize("kind", ["nonowner", "bot", "webhook", "fake", "wrong_guild"])
async def test_unauthorized_entry_never_calls_hermes(cog, kind):
    ctx = context(owner=kind != "nonowner", dm=kind != "wrong_guild")
    if kind == "bot":
        ctx.author.bot = True
    elif kind == "webhook":
        ctx.message.webhook_id = 333
    elif kind == "fake":
        ctx.author = MagicMock(id=123, bot=False)
    elif kind == "wrong_guild":
        ctx.author.guild = MagicMock(id=555)
    assert not await cog.handle(ctx, "secret")
    cog.client.reply.assert_not_awaited()
    ctx.reply.assert_not_awaited()


async def test_acknowledges_before_model_and_segments_full_long_reply(cog):
    ctx = context(dm=True)
    text = "á" * 2500

    async def reply(*args, **kwargs):
        ctx.message.add_reaction.assert_awaited_once_with("👀")
        return ("run-2", text)

    cog.client.reply.side_effect = reply
    await cog.handle(ctx, "hello")
    chunks = [
        call.args[0]
        for call in ctx.reply.await_args_list + ctx.channel.send.await_args_list
    ]
    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert all("file" not in call.kwargs for call in ctx.reply.await_args_list)


async def test_error_never_exposes_exception_or_falls_back(cog):
    cog.client.reply.side_effect = RuntimeError("SECRET token")
    ctx = context(dm=True)
    await cog.handle(ctx, "hello")
    assert "SECRET" not in ctx.reply.call_args.args[0]
    cog.poliswag.get_cog.assert_not_called()
    cog.poliswag.utility.log_to_file.assert_not_called()


@pytest.mark.parametrize(
    "dm,owner,text,command,expected",
    [
        (True, True, "hello", False, True),
        (True, False, "hello", False, False),
        (False, True, "hello", False, False),
        (True, True, "!unknown", False, False),
        (True, True, "!help", True, False),
        (True, True, "  ", False, False),
    ],
)
async def test_main_plain_dm_and_command_gates(cog, dm, owner, text, command, expected):
    from main import Poliswag

    ctx = context(owner=owner, dm=dm)
    ctx.message.content = text
    ctx.command = MagicMock() if command else None
    bot = MagicMock()
    bot.get_context = AsyncMock(return_value=ctx)
    bot._mentions_poliswag.return_value = False
    bot.get_cog.return_value = cog
    bot.invoke = AsyncMock()
    await Poliswag.process_commands(bot, ctx.message)
    assert cog.client.reply.await_count == int(expected)
    if expected:
        bot.invoke.assert_not_awaited()
    elif command:
        bot.invoke.assert_awaited_once_with(ctx)


async def test_owner_guild_direct_entry_cannot_access_private_conversation(cog):
    ctx = context(dm=False)
    assert not await cog.handle(ctx, "private history")
    cog.client.reply.assert_not_awaited()
    ctx.message.add_reaction.assert_not_awaited()
    ctx.reply.assert_not_awaited()


@pytest.mark.parametrize(
    "kind", ["other_user", "guild", "other_channel", "fake", "bot"]
)
async def test_approval_interaction_cannot_be_forged(cog, kind):
    view = ApprovalView(456, "run", "request")
    interaction = MagicMock()
    interaction.guild = None
    interaction.channel_id = 456
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = 123
    interaction.user.bot = False
    interaction.response.send_message = AsyncMock()
    if kind == "other_user":
        interaction.user.id = 999
    elif kind == "guild":
        interaction.guild = MagicMock()
    elif kind == "other_channel":
        interaction.channel_id = 789
    elif kind == "fake":
        interaction.user = MagicMock(id=123, bot=False)
    else:
        interaction.user.bot = True
    await view.decide(interaction, "once")
    assert view.choice == "deny"
    assert not view.decided
    interaction.response.send_message.assert_awaited_once()
    view.stop()


async def test_owner_can_approve_exact_view_once(cog):
    view = ApprovalView(456, "run", "request")
    interaction = MagicMock()
    interaction.guild = None
    interaction.channel_id = 456
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = 123
    interaction.user.bot = False
    interaction.response.defer = AsyncMock()
    await view.decide(interaction, "once")
    await view.decide(interaction, "deny")
    assert view.choice == "once"
    assert view.run_id == "run" and view.request_id == "request"
    await view.finish()
    assert all(button.disabled for button in view.children)


async def test_timeout_denies_and_disables_buttons(cog):
    view = ApprovalView(456, "run", "request")
    view.message = AsyncMock()
    await view.on_timeout()
    assert view.choice == "deny"
    assert all(button.disabled for button in view.children)
    view.message.edit.assert_awaited_once()
    view.stop()


@pytest.mark.parametrize(
    "command",
    [
        "cat /root/.env",
        "TOKEN=abcdef run",
        "cat credentials.json",
        "ssh-rsa ABC",
        "A" * 60,
    ],
)
def test_approval_card_hides_possible_credentials(command):
    text = approval_details({"description": "Reiniciar serviço", "command": command})
    assert command not in text
    assert "ocultados" in text
    assert "Reiniciar serviço" in text


async def test_approval_callback_renders_private_card_and_closes_view(cog, monkeypatch):
    ctx = context(dm=True)
    view = MagicMock()
    view.wait = AsyncMock()
    view.finish = AsyncMock()
    view.choice = "once"
    factory = MagicMock(return_value=view)
    monkeypatch.setattr("cogs.hermes.ApprovalView", factory)
    result = await cog.request_approval(
        ctx,
        {
            "run_id": "r",
            "request_id": "q",
            "command": "systemctl restart example",
            "description": "Reiniciar serviço example",
        },
    )
    assert result == "once"
    factory.assert_called_once_with(456, "r", "q")
    assert ctx.reply.call_args.kwargs["view"] is view
    assert (
        "systemctl restart example" in ctx.reply.call_args.kwargs["embed"].description
    )
    assert not ctx.reply.call_args.kwargs["allowed_mentions"].everyone
    view.stop.assert_called_once()
    view.finish.assert_awaited_once()


async def test_short_paragraphs_stay_in_one_reply(cog):
    ctx = context(dm=True)
    text = "Resolvido.\n\nConfirmei o resultado."
    assert await cog.respond(ctx, text)
    ctx.reply.assert_awaited_once()
    assert ctx.reply.await_args.args == (text,)
    ctx.channel.send.assert_not_awaited()


async def test_only_continuations_are_silent_normal_messages(cog):
    ctx = context(dm=True)
    text = "x" * 4500
    assert await cog.respond(ctx, text)
    ctx.reply.assert_awaited_once()
    assert not ctx.reply.await_args.kwargs.get("silent", False)
    calls = ctx.channel.send.await_args_list
    assert len(calls) == 2
    for c in calls:
        assert c.kwargs.get("silent") is True
        assert "reference" not in c.kwargs
        assert "mention_author" not in c.kwargs
        assert not c.kwargs["allowed_mentions"].everyone
    assert "".join(c.args[0] for c in [ctx.reply.await_args, *calls]) == text


async def test_recovered_reply_uses_same_segmentation(cog):
    channel = MagicMock()
    channel.send = AsyncMock()
    cog.poliswag.get_channel.return_value = channel
    text = "x" * 2500
    assert await cog._deliver_recovered(456, text)
    calls = channel.send.await_args_list
    assert (
        "".join(c.args[0] for c in calls)
        == "Resultado recuperado após reinício do Poliswag.\n\n" + text
    )
    assert all(len(c.args[0]) <= 2000 for c in calls)
    assert all(not c.kwargs["allowed_mentions"].everyone for c in calls)


async def test_partial_delivery_failure_keeps_pending_run(cog):
    ctx = context(dm=True)
    cog.client.reply.return_value = ("run-1", "x" * 2500)
    cog.client.forget = MagicMock()
    error = discord.HTTPException(MagicMock(status=500, reason="error"), "error")
    ctx.channel.send.side_effect = error
    assert await cog.handle(ctx, "hello")
    assert ctx.reply.await_count == 1
    assert ctx.channel.send.await_count == 1
    cog.client.forget.assert_not_called()


async def test_short_code_block_stays_intact_across_segments(cog):
    ctx = context(dm=True)
    code = "```python\n" + "print('ok')\n" * 50 + "```"
    text = "Introdução. " * 140 + "\n" + code + "\n\nFim."
    assert await cog.respond(ctx, text)
    calls = [ctx.reply.await_args, *ctx.channel.send.await_args_list]
    assert "".join(c.args[0] for c in calls) == text
    assert any(code in c.args[0] for c in calls)
    assert all(len(c.args[0]) <= 2000 for c in calls)


async def test_large_technical_block_stays_in_chat_without_losing_prose(cog):
    import re

    ctx = context(dm=True)
    body = "Linha de log.\n" * 400
    code = "```log\n" + body + "```"
    assert await cog.respond(
        ctx, "Resolvido.\n\n" + code + "\n\nFalta validar no dispositivo."
    )
    calls = [ctx.reply.await_args, *ctx.channel.send.await_args_list]
    content = "".join(c.args[0] for c in calls)
    assert "Resolvido." in content and "Falta validar no dispositivo." in content
    assert all("file" not in c.kwargs and "files" not in c.kwargs for c in calls)
    bodies = [
        m.group(1)
        for c in calls
        for m in re.finditer(r"```log\n(.*?)```", c.args[0], re.S)
    ]
    assert "".join(bodies) == body
    assert all(len(c.args[0]) <= 2000 for c in calls)


async def test_large_plain_answer_is_not_replaced_with_attachment(cog):
    ctx = context(dm=True)
    text = "Uma explicação em português. " * 220
    assert await cog.respond(ctx, text)
    calls = [ctx.reply.await_args, *ctx.channel.send.await_args_list]
    assert "".join(c.args[0] for c in calls) == text
    assert all("file" not in c.kwargs for c in calls)


@pytest.mark.parametrize("marker", ["```", "~~~~"])
def test_code_with_blank_lines_is_not_split(marker):
    from cogs.hermes import split_reply

    block = marker + "text\n" + "abc\n\n" * 100 + marker
    text = "Resumo. " * 150 + "\n" + block + "\nFim."
    chunks = split_reply(text)
    assert "".join(chunks) == text
    assert any(block in c for c in chunks)
    assert all(len(c) <= 2000 for c in chunks)


async def test_failed_technical_message_keeps_run_pending(cog):
    ctx = context(dm=True)
    cog.client.reply.return_value = ("run-1", "```log\n" + "line\n" * 500 + "```")
    cog.client.forget = MagicMock()
    ctx.reply.side_effect = discord.HTTPException(
        MagicMock(status=500, reason="error"), "error"
    )
    assert await cog.handle(ctx, "hello")
    cog.client.forget.assert_not_called()


def test_unclosed_oversized_code_stays_in_chat_with_balanced_fences():
    import re
    from cogs.hermes import prepare_reply

    body = "line\n" * 600
    chunks = prepare_reply("Resumo.\n\n```log\n" + body)
    assert "Resumo." in "".join(chunks)
    assert all(len(c) <= 2000 for c in chunks)
    bodies = [
        m.group(1) for c in chunks for m in re.finditer(r"```log\n(.*?)```", c, re.S)
    ]
    assert "".join(bodies) == body


@pytest.mark.parametrize("marker", ["```", "~~~~"])
def test_long_code_lines_are_kept_in_chat_without_truncation(marker):
    import re
    from cogs.hermes import prepare_reply

    body = "x" * 6500
    chunks = prepare_reply(marker + "text\n" + body + "\n" + marker)
    assert all(len(c) <= 2000 for c in chunks)
    bodies = []
    for c in chunks:
        match = re.search(
            re.escape(marker) + r"text\n(.*?)" + re.escape(marker), c, re.S
        )
        assert match is not None
        bodies.append(match.group(1).rstrip("\n"))
    assert "".join(bodies) == body


async def test_outcome_reaction_follows_reply(cog):
    ctx = context(dm=True)
    await cog.handle(ctx, "hello")
    reactions = [call.args[0] for call in ctx.message.add_reaction.await_args_list]
    assert reactions == ["👀", "✅"]


async def test_failure_reacts_with_cross(cog):
    cog.client.reply.side_effect = RuntimeError("x")
    ctx = context(dm=True)
    await cog.handle(ctx, "hello")
    assert ctx.message.add_reaction.await_args_list[-1].args == ("❌",)


async def test_live_status_card_tracks_events_and_collapses(cog, monkeypatch):
    from cogs import hermes

    monkeypatch.setattr(hermes.StatusMessage, "FIRST_DELAY", 0)
    monkeypatch.setattr(hermes.StatusMessage, "REFRESH", 0.01)
    ctx = context(dm=True)
    card = MagicMock()
    card.edit = AsyncMock()
    replies = []

    async def reply(*args, **kwargs):
        replies.append(kwargs)
        return card if "embed" in kwargs else MagicMock()

    ctx.reply = AsyncMock(side_effect=reply)

    async def run(*args, on_event, **kwargs):
        on_event({"event": "run.starting"})
        on_event({"event": "run.admitted", "run_id": "run-1"})
        on_event({"event": "tool.started", "tool": "mcp__sysadmin__sysadmin_execute"})
        await asyncio.sleep(0.05)
        on_event({"event": "tool.completed", "tool": "mcp__sysadmin__sysadmin_execute"})
        on_event({"event": "message.interim", "text": "A ver o token=abc"})
        await asyncio.sleep(0.05)
        return ("run-1", "Feito")

    cog.client.reply = AsyncMock(side_effect=run)
    await cog.handle(ctx, "hello")
    first = replies[0]["embed"]
    assert first.title == "⚙️ Hermes a trabalhar"
    assert "sysadmin_execute" in first.description
    rendered = [call.kwargs["embed"] for call in card.edit.await_args_list]
    assert any("✓ `sysadmin_execute`" in (e.description or "") for e in rendered)
    assert all("abc" not in (e.description or "") for e in rendered)
    final = card.edit.await_args_list[-1].kwargs
    assert final["embed"].title == "✅ Concluído"
    assert final["view"] is None
    assert replies[-1] == {"mention_author": False, "allowed_mentions": ANY}


async def test_quick_answer_posts_no_status_card(cog):
    ctx = context(dm=True)
    await cog.handle(ctx, "hello")
    assert all("embed" not in call.kwargs for call in ctx.reply.await_args_list)


async def test_queued_request_can_be_redirected_into_active_run(cog):
    from cogs.hermes import RunProgress, StatusMessage

    ctx = context(dm=True)
    release = asyncio.Event()

    async def wait_in_queue(*args, **kwargs):
        await release.wait()
        return ("run-2", "não devia correr")

    cog.client.reply = AsyncMock(side_effect=wait_in_queue)
    cog.client._pending = 1
    cog.client.active_run = "run-1"
    cog.client.steer = AsyncMock(return_value=True)
    created = []
    original = StatusMessage.__init__

    def track(self, *args):
        original(self, *args)
        created.append(self)

    StatusMessage.__init__ = track
    try:
        task = asyncio.ensure_future(cog.handle(ctx, "usa a porta 8080"))
        await asyncio.sleep(0.01)
        status = created[0]
        assert isinstance(status.progress, RunProgress)
        assert status.progress.status == "queued"
        assert await status.steer(ctx.message.content)
        assert await task
    finally:
        StatusMessage.__init__ = original
    cog.client.steer.assert_awaited_once_with("run-1", ctx.message.content)
    assert ctx.message.add_reaction.await_args_list[-1].args == ("💬",)
    assert all(
        call.args[:1] != ("não devia correr",) for call in ctx.reply.await_args_list
    )


async def test_refused_redirect_returns_request_to_queue(cog):
    from cogs.hermes import StatusMessage

    ctx = context(dm=True)
    calls = []
    release = asyncio.Event()

    async def queued(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            await release.wait()
        return ("run-2", "Feito")

    cog.client.reply = AsyncMock(side_effect=queued)
    cog.client._pending = 1
    cog.client.active_run = "run-1"
    cog.client.steer = AsyncMock(return_value=False)
    created = []
    original = StatusMessage.__init__

    def track(self, *args):
        original(self, *args)
        created.append(self)

    StatusMessage.__init__ = track
    try:
        task = asyncio.ensure_future(cog.handle(ctx, "pedido"))
        await asyncio.sleep(0.01)
        assert not await created[0].steer("pedido")
        assert await task
    finally:
        StatusMessage.__init__ = original
    assert len(calls) == 2
    assert ctx.reply.await_args_list[-1].args == ("Feito",)


def test_progress_card_hides_credentials_and_shows_queue():
    from cogs.hermes import RunProgress

    progress = RunProgress(ahead=2)
    assert progress.embed().title == "⏳ Na fila"
    progress.apply({"event": "run.admitted", "run_id": "r"})
    progress.apply({"event": "tool.started", "tool": "terminal", "preview": "cat .env"})
    assert "cat .env" not in progress.embed().description
    progress.apply({"event": "tool.failed", "tool": "terminal"})
    assert progress.errors == 1
    assert "com erro" in progress.embed("completed").description


@pytest.mark.parametrize(
    "text,ignored",
    [
        ("!ignore isto é só uma nota", True),
        ("  !IGNORE", True),
        ("!ignore <@1> marca", True),
        ("!ignored", False),
        ("não !ignore", False),
    ],
)
async def test_ignore_prefix_never_reaches_hermes_or_commands(cog, text, ignored):
    from main import Poliswag

    ctx = context(dm=True)
    ctx.message.content = text
    ctx.command = None
    bot = MagicMock()
    bot.get_context = AsyncMock(return_value=ctx)
    bot._mentions_poliswag.return_value = "<@1>" in text
    bot.get_cog.return_value = cog
    bot.invoke = AsyncMock()
    await Poliswag.process_commands(bot, ctx.message)
    if ignored:
        bot.get_context.assert_not_awaited()
        bot.get_cog.assert_not_called()
        bot.invoke.assert_not_awaited()
    else:
        bot.get_context.assert_awaited_once()


async def test_progress_buttons_only_for_owner_dm(cog):
    from cogs.hermes import ProgressView, RunProgress, StatusMessage

    ctx = context(dm=True)
    view = ProgressView(StatusMessage(cog, ctx, RunProgress()))
    interaction = MagicMock()
    interaction.guild = None
    interaction.channel_id = 456
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = 999
    interaction.user.bot = False
    interaction.response.send_message = AsyncMock()
    assert not await view.interaction_check(interaction)
    interaction.user.id = 123
    assert await view.interaction_check(interaction)


async def test_decided_approval_card_is_deleted_and_logged_on_status_card(cog):
    from cogs.hermes import RunProgress

    ctx = context(dm=True)
    message = AsyncMock()
    ctx.reply = AsyncMock(return_value=message)
    progress = RunProgress()
    approval = {
        "run_id": "r",
        "request_id": "q",
        "description": "Reiniciar serviço example",
    }
    task = asyncio.ensure_future(cog.request_approval(ctx, approval, progress))
    await asyncio.sleep(0.01)
    assert progress.awaiting_approval
    view = ctx.reply.call_args.kwargs["view"]
    view.choice, view.decided = "once", True
    view.stop()
    assert await task == "once"
    message.delete.assert_awaited_once()
    message.edit.assert_not_awaited()
    assert not progress.awaiting_approval
    progress.apply({"event": "run.admitted", "run_id": "r"})
    assert "🔓 aprovado — Reiniciar serviço example" in progress.embed().description
    assert "1 aprovação" in progress.embed("completed").description


def test_local_steer_is_not_counted_twice_when_echoed():
    from cogs.hermes import RunProgress

    progress = RunProgress()
    progress.record_steer("usa a porta 8080")
    progress.apply({"event": "run.steered"})
    assert progress.steered == 1
    assert "💬 usa a porta 8080" in progress.embed().description
    progress.apply({"event": "run.steered"})
    assert progress.steered == 2


async def test_redirect_deletes_queued_card_and_updates_active_card(cog):
    from cogs.hermes import RunProgress, StatusMessage

    active = StatusMessage(cog, context(dm=True), RunProgress())
    active.progress.run_id = "run-1"
    cog.live.add(active)
    queued = StatusMessage(cog, context(dm=True), RunProgress(ahead=1))
    queued.turn = asyncio.ensure_future(asyncio.sleep(10))
    queued.message = AsyncMock()
    cog.client.active_run = "run-1"
    cog.client.steer = AsyncMock(return_value=True)
    assert await queued.steer("usa a porta 8080")
    await queued.finish("redirected")
    queued.message.delete.assert_awaited_once()
    queued.message.edit.assert_not_awaited()
    assert active.progress.steered == 1
    assert "usa a porta 8080" in active.progress.embed().description
