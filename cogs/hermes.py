"""The owner's single private Hermes conversation, available only in DMs."""

import asyncio
import re
import time
from collections import deque

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import react_to_request
from modules.hermes_client import HermesBusy, HermesClient
from modules.permissions import is_owner

_UNAVAILABLE = (
    "Não consegui confirmar o resultado do {name}. O trabalho pode continuar "
    "em execução; verifica o estado antes de repetires o pedido."
)


class Hermes(commands.Cog):
    NAME = "Hermes"

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.client = HermesClient(
            Config.HERMES_API_URL,
            Config.HERMES_API_KEY,
            Config.MY_ID,
            Config.HERMES_SESSION_ID,
            runs_dir=Config.HERMES_RUNS_DIR,
        )
        self.live = set()

    def status_for(self, run_id):
        """The live status card of this owner turn's run, if it is still open."""
        for status in self.live:
            if run_id is not None and status.progress.run_id == run_id:
                return status
        return None

    async def handle(self, ctx, text):
        # This entry point is also called outside command dispatch; repeat auth.
        author = ctx.author
        if (
            ctx.guild is not None
            or not is_owner(ctx)
            or Config.MY_ID <= 0
            or not isinstance(author, (discord.User, discord.Member))
            or author.bot
            or ctx.message.author.id != author.id
            or ctx.message.webhook_id is not None
        ):
            return False
        if not isinstance(text, str) or not text.strip():
            return False
        await react_to_request(ctx.message)
        if len(text) > 12000:
            await self.respond(ctx, "O pedido pode ter no máximo 12000 caracteres.")
            return True
        progress = RunProgress(ahead=self.client.waiting, name=self.NAME)
        status = StatusMessage(self, ctx, progress)
        status.start()
        run_id = None
        outcome = "❌"
        try:
            while True:
                status.turn = asyncio.ensure_future(
                    self.client.reply(
                        text.strip(),
                        message_id=ctx.message.id,
                        channel_id=ctx.channel.id,
                        approval_callback=lambda approval: self.request_approval(
                            ctx, approval, progress
                        ),
                        on_event=progress.apply,
                    )
                )
                try:
                    run_id, reply = await status.turn
                    break
                except asyncio.CancelledError:
                    if status.redirect is None:
                        raise
                    if await status.redirect:
                        # The queued text went to the active run as guidance.
                        await status.finish("redirected")
                        await react(ctx.message, "💬")
                        return True
                    # The active run refused it: back to the queue as a turn.
                    status.redirect = None
            outcome = "⚠️" if reply.startswith("⚠️") else "✅"
        except HermesBusy:
            reply = f"Já tens 5 pedidos à espera no {self.NAME}. Aguarda um pouco."
        except asyncio.CancelledError:
            await status.finish("unavailable")
            raise
        except Exception:
            # Do not publish or log prompts, credentials, or transport errors.
            reply = _UNAVAILABLE.format(name=self.NAME)
        await status.finish(
            {"✅": "completed", "⚠️": "partial"}.get(outcome, "unavailable")
        )
        delivered = await self.respond(ctx, reply)
        await react(ctx.message, outcome)
        if run_id is not None and delivered:
            self.client.forget(run_id)
        return True

    async def recover_pending(self):
        """Resume runs a previous bot process left without delivering a reply."""
        try:
            await self.poliswag.wait_until_ready()
        except Exception:
            pass
        try:
            await self.client.recover_pending(self._deliver_recovered)
        except Exception:
            # Recovery must never take down the bot loop.
            pass

    async def _deliver_recovered(self, channel_id, text):
        channel = self.poliswag.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.poliswag.fetch_channel(channel_id)
            except (discord.HTTPException, discord.NotFound):
                return False
        return await send_segments(
            channel.send, "Resultado recuperado após reinício do Poliswag.\n\n" + text
        )

    @staticmethod
    async def request_approval(ctx, approval, progress=None):
        if progress is not None:
            progress.awaiting_approval = True
        view = ApprovalView(ctx.channel.id, approval["run_id"], approval["request_id"])
        # With a live status card the decision is recorded there instead.
        view.remove = progress is not None
        embed = discord.Embed(
            title="Hermes precisa da tua aprovação",
            description=approval_details(approval),
            color=Config.EMBED_COLOR,
        )
        embed.set_footer(text="Uma autorização vale apenas para esta ação.")
        try:
            view.message = await ctx.reply(
                embed=embed,
                view=view,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await view.wait()
            return view.choice
        finally:
            if progress is not None:
                progress.awaiting_approval = False
                progress.record_approval(view.choice, approval)
            view.stop()
            await view.finish()

    @staticmethod
    async def respond(ctx, text):
        return await send_segments(ctx.channel.send, text, first_reply=ctx.reply)


async def react(message, emoji):
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe(text, limit):
    """One-line, markdown-escaped excerpt; possible credentials are never shown."""
    if not isinstance(text, str) or not text.strip():
        return ""
    if _SENSITIVE.search(text):
        return "(detalhes ocultados)"
    return discord.utils.escape_markdown(_clip(text, limit)).replace("@", "＠")


def _elapsed(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _tool_label(name):
    # MCP tools arrive as mcp__<server>__<tool>; the tool part is what matters.
    name = str(name or "ferramenta").rsplit("__", 1)[-1]
    return _clip(name, 40)


class RunProgress:
    """Folds run events into what the owner sees while Hermes works."""

    HISTORY = 5

    def __init__(self, ahead=0, name="Hermes"):
        self.name = name
        self.started = time.monotonic()
        self.run_started = None
        self.status = "queued" if ahead else "admitting"
        self.ahead = ahead
        self.run_id = None
        self.current = None
        self.steps = deque(maxlen=self.HISTORY)
        self.tools = 0
        self.errors = 0
        self.note = ""
        self.steered = 0
        # Steers already counted locally whose run.steered echo is still due.
        self.unechoed = 0
        self.guidance = ""
        self.approvals = 0
        self.awaiting_approval = False
        self.dirty = True

    def apply(self, event):
        kind = event.get("event")
        if kind == "run.admitted":
            self.run_id = event.get("run_id")
            self.status = "running"
            self.run_started = time.monotonic()
        elif kind == "run.starting":
            self.status = "admitting"
        elif kind == "status" and isinstance(event.get("status"), str):
            self.status = event["status"]
        elif kind == "tool.started":
            self.current = (
                _tool_label(event.get("tool")),
                _safe(event.get("preview"), 90),
                time.monotonic(),
            )
        elif kind in ("tool.completed", "tool.failed"):
            failed = kind == "tool.failed" or event.get("error") is True
            name = _tool_label(event.get("tool"))
            duration = event.get("duration")
            if not isinstance(duration, (int, float)) and self.current:
                duration = time.monotonic() - self.current[2]
            self.tools += 1
            self.errors += int(failed)
            self.steps.append(("✗" if failed else "✓", name, duration))
            self.current = None
        elif kind == "message.interim":
            note = _safe(event.get("text"), 300)
            if note:
                self.note = note
        elif kind == "subagent.start":
            self.current = (
                "subagente",
                _safe(event.get("preview"), 90),
                time.monotonic(),
            )
        elif kind == "subagent.complete":
            self.current = None
        elif kind == "run.steered":
            if self.unechoed:
                self.unechoed -= 1
            else:
                self.steered += 1
        else:
            return
        self.dirty = True

    def record_steer(self, text):
        self.steered += 1
        self.unechoed += 1
        self.guidance = _safe(text, 200)
        self.dirty = True

    def record_approval(self, choice, approval):
        approved = choice == "once"
        self.approvals += int(approved)
        label = "aprovado" if approved else "recusado"
        detail = _safe(approval.get("description") or approval.get("command"), 70)
        self.steps.append(
            (
                "🔓" if approved else "🚫",
                f"{label} — {detail}" if detail else label,
                None,
            )
        )
        self.dirty = True

    @property
    def active(self):
        return self.status in ("admitting", "queued", "running", "waiting_for_approval")

    def embed(self, outcome=None):
        now = time.monotonic()
        elapsed = _elapsed(now - (self.run_started or self.started))
        tools = f"{self.tools} ferramenta" + ("" if self.tools == 1 else "s")
        if outcome is not None:
            title, description = {
                "completed": ("✅ Concluído", f"{tools} · {elapsed}"),
                "partial": ("⚠️ Terminou sem sucesso", f"{tools} · {elapsed}"),
                "unavailable": (
                    "❌ Sem resultado confirmado",
                    f"{tools} · {elapsed} · pode continuar em execução",
                ),
                "redirected": (
                    "💬 Enviado à tarefa atual",
                    "Este pedido foi entregue como instrução à tarefa em curso.",
                ),
            }[outcome]
            if self.errors and outcome != "redirected":
                description += f" · {self.errors} com erro"
            if self.approvals and outcome != "redirected":
                description += f" · {self.approvals} aprovaç" + (
                    "ão" if self.approvals == 1 else "ões"
                )
            return discord.Embed(
                title=title, description=description, color=Config.EMBED_COLOR
            )
        if self.status == "queued":
            ahead = max(self.ahead, 1)
            return discord.Embed(
                title="⏳ Na fila",
                description=(
                    f"{ahead} pedido{'s' if ahead > 1 else ''} à frente. "
                    "Usa **Enviar à tarefa atual** para o entregar já como instrução."
                ),
                color=Config.EMBED_COLOR,
            )
        if self.awaiting_approval or self.status == "waiting_for_approval":
            title = "🔐 À espera da tua aprovação"
        elif self.status == "stopping":
            title = "⏹️ A parar…"
        else:
            title = f"⚙️ {self.name} a trabalhar"
        lines = []
        for icon, name, duration in self.steps:
            took = (
                f" · {_elapsed(duration)}" if isinstance(duration, (int, float)) else ""
            )
            # Tool names are code; approval entries are already-escaped text.
            label = f"`{name}`" if icon in ("✓", "✗") else name
            lines.append(f"{icon} {label}{took}")
        if self.current:
            name, preview, since = self.current
            detail = f" — {preview}" if preview else ""
            lines.append(f"▸ `{name}`{detail} · {_elapsed(now - since)}")
        elif self.status in ("running", "admitting") and not self.awaiting_approval:
            lines.append("▸ a pensar…")
        if self.guidance:
            lines.append(f"💬 {self.guidance}")
        if self.note:
            lines.append(f"\n> {self.note}")
        embed = discord.Embed(
            title=title, description="\n".join(lines)[:3900], color=Config.EMBED_COLOR
        )
        footer = f"{elapsed} · {tools}"
        if self.steered:
            footer += f" · {self.steered} instrução extra"
        embed.set_footer(text=footer)
        return embed


class StatusMessage:
    """One live, edited embed per owner turn, plus a typing indicator."""

    # Quick answers need no status card; edits stay well under rate limits.
    FIRST_DELAY = 3
    REFRESH = 4

    def __init__(self, cog, ctx, progress):
        self.cog = cog
        self.ctx = ctx
        self.progress = progress
        self.message = None
        self.view = None
        self.turn = None
        # While queued, a future resolving to whether the active run took it.
        self.redirect = None
        self._task = None

    def start(self):
        self.cog.live.add(self)
        self._task = asyncio.ensure_future(self._run())

    async def _run(self):
        try:
            async with self.ctx.channel.typing():
                await asyncio.sleep(self.FIRST_DELAY)
                while True:
                    if self.progress.dirty or self.progress.current:
                        await self._render()
                    await asyncio.sleep(self.REFRESH)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Feedback is best effort; the turn itself continues regardless.
            pass

    async def _render(self):
        self.progress.dirty = False
        embed = self.progress.embed()
        if self.view is None:
            self.view = ProgressView(self)
        self.view.sync()
        kwargs = {
            "embed": embed,
            "view": self.view,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if self.message is None:
            self.message = await self.ctx.reply(mention_author=False, **kwargs)
        else:
            await self.message.edit(**kwargs)

    async def finish(self, outcome):
        self.cog.live.discard(self)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self.view is not None:
            self.view.stop()
        if self.message is None:
            return
        try:
            if outcome == "redirected":
                # The active turn's card now shows this text as guidance.
                await self.message.delete()
                return
            await self.message.edit(
                embed=self.progress.embed(outcome),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def stop_run(self):
        run_id = self.progress.run_id
        if run_id is None or not await self.cog.client.stop(run_id):
            return False
        self.progress.status = "stopping"
        self.progress.dirty = True
        return True

    async def steer(self, text):
        """Guidance for this turn's run, or for the active run while queued."""
        if self.progress.status == "queued":
            run_id = self.cog.client.active_run
            if run_id is None or self.turn is None or self.redirect is not None:
                return False
            # Leave the queue first, synchronously: once this turn is admitted
            # it must never be abandoned, and the text must not run twice.
            self.redirect = asyncio.get_running_loop().create_future()
            self.turn.cancel()
            accepted = await self.cog.client.steer(run_id, text)
            if accepted and (active := self.cog.status_for(run_id)) is not None:
                active.progress.record_steer(text)
            self.redirect.set_result(accepted)
            return accepted
        run_id = self.progress.run_id
        if run_id is None or not await self.cog.client.steer(run_id, text):
            return False
        self.progress.record_steer(text)
        return True


def _owner_interaction(interaction, channel_id):
    return (
        interaction.guild is None
        and interaction.channel_id == channel_id
        and isinstance(interaction.user, (discord.User, discord.Member))
        and interaction.user.id == Config.MY_ID
        and not interaction.user.bot
    )


class ProgressView(discord.ui.View):
    def __init__(self, status):
        super().__init__(timeout=None)
        self.status = status

    def sync(self):
        queued = self.status.progress.status == "queued"
        self.stop_button.disabled = queued or self.status.progress.status == "stopping"
        self.steer_button.label = "Enviar à tarefa atual" if queued else "Redirecionar"

    async def interaction_check(self, interaction):
        if _owner_interaction(interaction, self.status.ctx.channel.id):
            return True
        await interaction.response.send_message(
            "Esta ação só está disponível na DM do dono.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Parar", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer()
        if not await self.status.stop_run():
            await interaction.followup.send(
                "Não consegui parar a tarefa; pode já ter terminado.", ephemeral=True
            )

    @discord.ui.button(
        label="Redirecionar", emoji="💬", style=discord.ButtonStyle.secondary
    )
    async def steer_button(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        if self.status.progress.status == "queued":
            # The queued request itself becomes guidance for the active run.
            await interaction.response.defer()
            if not await self.status.steer(self.status.ctx.message.content):
                await interaction.followup.send(
                    "A tarefa atual não aceitou a instrução; o pedido volta à fila.",
                    ephemeral=True,
                )
            return
        await interaction.response.send_modal(SteerModal(self.status))


class SteerModal(discord.ui.Modal):
    guidance = discord.ui.TextInput(
        label="O que deve mudar ou ter em conta?",
        style=discord.TextStyle.paragraph,
        max_length=4000,
    )

    def __init__(self, status):
        super().__init__(title=f"Instrução para o {status.cog.NAME}", timeout=600)
        self.status = status

    async def on_submit(self, interaction):
        if not _owner_interaction(interaction, self.status.ctx.channel.id):
            await interaction.response.send_message(
                "Esta ação só está disponível na DM do dono.", ephemeral=True
            )
            return
        await interaction.response.defer()
        if not await self.status.steer(self.guidance.value):
            await interaction.followup.send(
                "A tarefa não aceitou a instrução; pode já ter terminado.",
                ephemeral=True,
            )


def fenced_blocks(text):
    """Find fenced blocks, including an unfinished block at end of output."""
    opening = re.compile(r"(?m)^ {0,3}(`{3,}|~{3,})[^\n]*\n")
    position = 0
    while match := opening.search(text, position):
        marker = match.group(1)
        closing = re.compile(
            r"(?m)^ {0,3}"
            + re.escape(marker[0])
            + r"{"
            + str(len(marker))
            + r",}[ \t]*(?=\n|$)"
        ).search(text, match.end())
        end = closing.end() if closing else len(text)
        yield match.start(), end
        position = end


def split_reply(text, limit=2000):
    """Aggregate paragraphs and never split a fenced block that fits a message."""
    chunks = []
    while len(text) > limit:
        end = text.rfind("\n\n", 0, limit - 1)
        if end >= 0:
            end += 2
        else:
            end = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
            end = end + 1 if end >= 0 else limit
        for start, stop in fenced_blocks(text):
            if start < end < stop:
                end = start if start else stop
                if end > limit:
                    raise ValueError("Oversized code block must be segmented first")
                break
        if text[:end].strip():
            chunks.append(text[:end])
        text = text[end:]
    if text.strip():
        chunks.append(text)
    return chunks


def prepare_reply(text):
    """Keep all output in chat, closing and reopening oversized code fences."""
    for start, end in reversed(list(fenced_blocks(text))):
        if end - start <= 2000:
            continue
        block = text[start:end]
        opening = re.match(r" {0,3}(`{3,}|~{3,})[^\n]*\n", block)
        marker = opening.group(1)
        header = opening.group(0)
        closing = re.search(
            r"(?m)^ {0,3}"
            + re.escape(marker[0])
            + r"{"
            + str(len(marker))
            + r",}[ \t]*$",
            block[opening.end() :],
        )
        body_end = opening.end() + closing.start() if closing else len(block)
        body = block[opening.end() : body_end]
        # Malformed, overlong fence labels are plain text rather than attachments.
        capacity = 2000 - len(header) - len(marker) - 1
        if capacity < 1:
            text = text[:start] + block.replace(marker, "") + text[end:]
            continue
        parts = []
        while body:
            stop = min(len(body), capacity)
            if stop < len(body):
                newline = body.rfind("\n", 0, stop)
                if newline >= 0:
                    stop = newline + 1
            part, body = body[:stop], body[stop:]
            parts.append(header + part + ("" if part.endswith("\n") else "\n") + marker)
        text = text[:start] + "\n\n".join(parts) + text[end:]
    return split_reply(text)


async def send_segments(send, text, *, first_reply=None):
    try:
        for index, chunk in enumerate(prepare_reply(text)):
            kwargs = {"allowed_mentions": discord.AllowedMentions.none()}
            if index == 0 and first_reply is not None:
                await first_reply(chunk, mention_author=False, **kwargs)
            else:
                await send(chunk, silent=index > 0, **kwargs)
        return True
    except discord.HTTPException:
        return False


# Do not echo a credential-bearing shell command into a Discord approval card.
_SENSITIVE = re.compile(
    r"(?i)(token|secret|password|passwd|authorization|api[_-]?key|bearer|"
    r"private.key|credentials|\.env\b|BEGIN [A-Z ]*KEY|ssh-rsa|ssh-ed25519|"
    r"[A-Za-z0-9+/=_-]{48,})"
)


def approval_details(approval):
    details = []
    for label, key in (("Ação", "description"), ("Comando", "command")):
        value = approval.get(key)
        if isinstance(value, str) and value.strip():
            if _SENSITIVE.search(value) or len(value) > 1800:
                details.append(
                    f"**{label}:** detalhes ocultados por poderem conter credenciais."
                )
            else:
                safe = discord.utils.escape_markdown(value).replace("@", "＠")
                details.append(f"**{label}:** {safe}")
    return "\n\n".join(details)[:3800] or "Ação administrativa pendente no Hermes."


class ApprovalView(discord.ui.View):
    def __init__(self, channel_id, run_id, request_id):
        super().__init__(timeout=275)
        self.channel_id = channel_id
        self.run_id = run_id
        self.request_id = request_id
        self.choice = "deny"
        self.decided = False
        self.message = None
        self.remove = False

    async def interaction_check(self, interaction):
        allowed = _owner_interaction(interaction, self.channel_id)
        if not allowed:
            await interaction.response.send_message(
                "Esta decisão só está disponível na DM do dono.", ephemeral=True
            )
        return allowed

    async def decide(self, interaction, choice):
        # Recheck here as well, rather than relying on the UI dispatch alone.
        if not await self.interaction_check(interaction):
            return
        if self.decided:
            await interaction.response.defer()
            return
        self.decided = True
        self.choice = choice
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Aprovar uma vez", style=discord.ButtonStyle.success)
    async def approve(self, interaction, button):
        await self.decide(interaction, "once")

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger)
    async def deny(self, interaction, button):
        await self.decide(interaction, "deny")

    async def on_timeout(self):
        self.decided = True
        self.choice = "deny"
        await self.finish()

    async def finish(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None and self.remove:
            message, self.message = self.message, None
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        elif self.message is not None:
            embed = discord.Embed(
                title="Aprovação enviada" if self.choice == "once" else "Ação recusada",
                description=(
                    "Autorização única."
                    if self.choice == "once"
                    else "Sem autorização; inclui pedidos sem resposta a tempo."
                ),
                color=Config.EMBED_COLOR,
            )
            try:
                await self.message.edit(
                    embed=embed,
                    view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass


async def setup(poliswag):
    cog = Hermes(poliswag)
    await poliswag.add_cog(cog)
    poliswag.loop.create_task(cog.recover_pending())
