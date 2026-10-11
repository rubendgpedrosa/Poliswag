"""Owner-only Claude/Codex delegation and Poliswag's runtime awareness."""

import asyncio
import io
import re
import time

import discord
from discord.ext import commands, tasks

from modules.agent_jobs import AgentJobs, TERMINAL, parse_direct_request
from modules.agent_reply import short_sentence
from modules.agent_database import AgentDatabase
from modules.agent_artifacts import MAX_ATTACHMENTS, MAX_TOTAL_BYTES, read_artifact
from modules.agent_destination import delivery_reference, normalized_name
from modules.config import Config
from modules.embeds import status_embed
from modules.permissions import is_owner, owner_only

_STATES = {
    "queued": "na fila",
    "running": "a executar",
    "completed": "concluída",
    "failed": "falhou",
    "cancelled": "cancelada",
    "interrupted": "interrompida",
    "waiting_input": "à espera de uma decisão",
}
_USAGE = (
    "`!ask claude <pergunta>` · `!ask openai <pergunta>`\n"
    "`!code claude <pedido>` · `!code openai <pedido>`\n"
    "`!jobs` · `!job <código>` · `!answer <código> <resposta>` · `!cancel <código>` · `!self`\n"
    "Em DM podes fazer qualquer pergunta; uso Claude por defeito. "
    "Pede Codex quando quiseres mudar de agente."
)


class Agents(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.jobs = AgentJobs(Config.AGENT_JOBS_DIR)
        self.database = AgentDatabase(self.jobs, Config.MY_ID)

    async def cog_load(self):
        self.deliver_results.start()
        self.serve_database.start()

    async def cog_unload(self):
        self.deliver_results.cancel()
        self.serve_database.cancel()

    def cog_check(self, ctx):
        return is_owner(ctx)

    async def snapshot(self):
        context = {
            "identity": "Poliswag, Discord bot for PoGoLeiria",
            "environment": Config.ENV,
            "captured_at": time.time(),
            "commands": sorted(c.qualified_name for c in self.poliswag.walk_commands()),
            "coding_repo": "Poliswag",
            "worker": self.jobs.worker_status(),
            "production_database": "Live read-only queries available through poliswag_db MCP tools",
        }
        try:
            data = await asyncio.wait_for(
                self.poliswag.scanner_status.get_full_status(), timeout=10
            )
            # Counts and health only: no account usernames/device identifiers.
            context["scanner"] = {
                "last_pokemon_seconds_ago": data.get("last_pokemon_seconds_ago"),
                "devices_alive": sum(d.get("is_alive", False) for d in data["devices"]),
                "devices_total": len(data["devices"]),
                "workers_total": len(data["workers"]),
            }
        except Exception:
            context["scanner"] = {"available": False}
        return context

    async def submit(self, ctx, mode, provider, prompt):
        # Direct-message routing bypasses command invocation, so gate it here too.
        if not is_owner(ctx):
            return
        try:
            destination = self.destination_for(ctx, prompt)
            await self.acknowledge(ctx)
            if destination["type"] == "dm":
                await ctx.author.create_dm()
            job = self.jobs.submit(
                owner_id=ctx.author.id,
                provider=provider,
                mode=mode,
                prompt=prompt,
                context=await self.snapshot(),
                destination=destination,
            )
            self.poliswag.utility.log_to_file(
                f"[AGENT] owner {ctx.author.id} queued {job['id']} {job['provider']} {mode}"
            )
        except ValueError as error:
            await ctx.send(
                short_sentence(error), allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            await ctx.send(
                "Não consegui abrir a tua DM.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    def destination_for(self, ctx, prompt):
        reference = delivery_reference(prompt)
        if reference == "dm" or (reference is None and ctx.guild is None):
            return {"type": "dm"}
        channel = ctx.channel
        if reference is not None:
            channels = (
                [*ctx.guild.channels, *ctx.guild.threads]
                if ctx.guild is not None
                else list(self.poliswag.get_all_channels())
            )
            if reference.startswith("<#"):
                matches = [c for c in channels if c.id == int(reference[2:-1])]
            else:
                name = normalized_name(reference.lstrip("#"))
                matches = [c for c in channels if normalized_name(c.name) == name]
            if len(matches) != 1 or not hasattr(matches[0], "send"):
                raise ValueError(
                    "Não encontrei um único canal para enviar o resultado."
                )
            channel = matches[0]
        return {
            "type": "channel",
            "channel_id": channel.id,
            "guild_id": channel.guild.id,
        }

    @commands.command(name="code", brief="Pede alterações de código a Claude ou Codex")
    @owner_only()
    async def code(self, ctx, provider: str, *, prompt: str):
        """!code claude|codex|openai <pedido> — altera e testa numa cópia separada."""
        await self.submit(ctx, "code", provider, prompt)

    @commands.command(name="ask", brief="Faz uma pergunta a Claude ou Codex")
    @owner_only()
    async def ask(self, ctx, provider: str, *, prompt: str):
        """!ask claude|codex|openai <pergunta> — consulta sem ferramentas de escrita."""
        await self.submit(ctx, "ask", provider, prompt)

    @commands.command(name="self", brief="Mostra capacidades e estado do Poliswag")
    @owner_only()
    async def self_status(self, ctx):
        context = await self.snapshot()
        worker = context["worker"]
        providers = ", ".join(k for k, v in worker.get("providers", {}).items() if v)
        embed = status_embed(
            "🤖 Poliswag",
            "Sou o bot do PoGoLeiria. Faço a gestão de quests, eventos, alertas e saúde "
            "do scanner. Posso delegar consultas e alterações de código "
            "a Claude ou Codex a teu pedido.",
        )
        embed.add_field(
            name="Estado",
            value=f"Ambiente: {Config.ENV}\n"
            f"Executor: {'online' if worker['online'] else 'offline'}\n"
            f"Agentes instalados: {providers or 'indisponíveis'}\n"
            f"Comandos carregados: {len(context['commands'])}",
            inline=False,
        )
        scanner = context["scanner"]
        embed.add_field(
            name="Scanner agora",
            value=(
                f"Dispositivos ligados: {scanner['devices_alive']}/{scanner['devices_total']}\n"
                f"Workers: {scanner['workers_total']}\n"
                f"Último Pokémon: {scanner['last_pokemon_seconds_ago']} s atrás"
                if scanner.get("available") is not False
                else "Não consegui recolher o estado."
            ),
            inline=False,
        )
        embed.add_field(name="Pedidos", value=_USAGE, inline=False)
        await ctx.author.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @commands.command(name="jobs", brief="Mostra as últimas tarefas dos agentes")
    @owner_only()
    async def jobs_list(self, ctx):
        jobs = self.jobs.list(ctx.author.id)[:10]
        lines = [f"`{j['id']}` · {j['provider']} · {_STATES[j['state']]}" for j in jobs]
        await ctx.author.send(
            embed=status_embed(
                "🤖 Tarefas", "\n".join(lines) or "Ainda não há tarefas."
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="job", brief="Consulta uma tarefa e o seu resultado")
    @owner_only()
    async def job_status(self, ctx, job_id: str):
        job = self.jobs.get(job_id, ctx.author.id)
        await self.send_result(ctx.author, job, full=True)

    @commands.command(name="answer", brief="Responde à decisão que bloqueia uma tarefa")
    @owner_only()
    async def answer_job(self, ctx, job_id: str, *, response: str):
        self.jobs.resume(job_id, ctx.author.id, response)
        await self.acknowledge(ctx)

    @commands.command(name="cancel", brief="Cancela uma tarefa de Claude ou Codex")
    @owner_only()
    async def cancel_job(self, ctx, job_id: str):
        self.jobs.cancel(job_id, ctx.author.id)

    async def handle_direct_request(self, ctx):
        if not is_owner(ctx):
            return False
        content = ctx.message.content
        if ctx.guild is not None:
            user = self.poliswag.user
            if user is None:
                return False
            prefixes = (f"<@{user.id}>", f"<@!{user.id}>")
            prefix = next((p for p in prefixes if content.startswith(p)), None)
            if prefix is None:
                return False
            content = content[len(prefix) :]
        parsed = parse_direct_request(content)
        if parsed is None:
            # In the owner's DM, ordinary questions need no mention or prefix.
            # Unknown bot commands still follow normal command routing.
            if (
                ctx.guild is None
                and content.strip()
                and not content.lstrip().startswith("!")
            ):
                await self.submit_natural_request(ctx, content.strip())
                return True
            return False
        await self.submit(ctx, *parsed)
        return True

    async def submit_natural_request(self, ctx, text):
        """Only the real owner can delegate free-form mentions.

        A general-purpose agent can answer any question or make requested changes.
        No other person's messages, attachments, or channel history are included.
        """
        if not is_owner(ctx):
            return
        parsed = parse_direct_request(text)
        if parsed:
            await self.submit(ctx, *parsed)
            return
        waiting = [
            job
            for job in self.jobs.list(ctx.author.id)
            if job["state"] == "waiting_input"
        ]
        if len(waiting) == 1:
            try:
                self.jobs.resume(waiting[0]["id"], ctx.author.id, text)
                await self.acknowledge(ctx)
            except ValueError as error:
                await ctx.send(
                    short_sentence(error),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return
        if len(waiting) > 1:
            await ctx.send(
                "Qual tarefa devo continuar com `!answer <id> <resposta>`?",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        provider = "codex" if re.search(r"\b(openai|codex)\b", text, re.I) else "claude"
        await self.submit(ctx, "code", provider, text)

    @staticmethod
    async def acknowledge(ctx):
        try:
            await ctx.reply(
                "Já estou a tratar disso.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            # A failed channel acknowledgement must not discard an owner task.
            pass

    @tasks.loop(seconds=1)
    async def serve_database(self):
        await self.database.poll()

    @serve_database.before_loop
    async def before_database(self):
        await self.poliswag.wait_until_ready()

    async def send_result(self, user, job, *, full=False):
        answer = self.jobs.directory(job["id"]) / "answer.txt"
        kwargs = {}
        if job["state"] in {"completed", "waiting_input"}:
            details = answer.read_text() if answer.exists() else ""
            text = short_sentence(job.get("reply") or details or "Feito.")
            kind = job.get("kind", "code" if job.get("mode") == "code" else "chat")
            if job["state"] == "completed" and kind == "stats":
                text = f"{text}\n\n{details}" if details and details != text else text
            if (full or kind == "stats") and job["state"] == "completed" and details:
                if kind != "stats" or len(text) > 1900:
                    kwargs["file"] = discord.File(
                        io.BytesIO(details[:500000].encode()),
                        filename=f"{job['id']}.txt",
                    )
                    if kind == "stats":
                        text = short_sentence(
                            job.get("reply") or "O relatório está no ficheiro."
                        )
        elif job["state"] in TERMINAL:
            text = {
                "cancelled": "Tarefa cancelada.",
                "interrupted": "O executor reiniciou; o trabalho ficou guardado.",
            }.get(
                job["state"],
                "Não consegui concluir o pedido; o trabalho ficou guardado para revisão.",
            )
        else:
            text = "Ainda estou a tratar do pedido."
        files = []
        try:
            artifacts = job.get("artifacts", []) if job["state"] == "completed" else []
            if len(artifacts) > MAX_ATTACHMENTS:
                raise ValueError("Too many attachments")
            total = 0
            for artifact in artifacts:
                data = read_artifact(self.jobs.directory(job["id"]), artifact["path"])
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Attachments are too large")
                files.append(
                    discord.File(io.BytesIO(data), filename=artifact["filename"])
                )
            if files:
                if "file" in kwargs:
                    files.append(kwargs.pop("file"))
                kwargs["files"] = files
            await user.send(
                text[:1900],
                allowed_mentions=discord.AllowedMentions.none(),
                **kwargs,
            )
        finally:
            for file in files:
                file.close()
            if "file" in kwargs:
                kwargs["file"].close()

    async def result_destination(self, job):
        destination = job.get("destination") or {"type": "dm"}
        if destination["type"] == "channel":
            channel = self.poliswag.get_channel(destination["channel_id"])
            if channel is None:
                channel = await self.poliswag.fetch_channel(destination["channel_id"])
            if channel.guild.id != destination["guild_id"] or not hasattr(
                channel, "send"
            ):
                raise ValueError("Invalid delivery channel")
            return channel
        if destination["type"] != "dm":
            raise ValueError("Invalid delivery destination")
        user = self.poliswag.get_user(Config.MY_ID)
        if user is None:
            user = await self.poliswag.fetch_user(Config.MY_ID)
        return user

    @tasks.loop(seconds=10)
    async def deliver_results(self):
        for job in self.jobs.list(Config.MY_ID):
            if job["state"] not in TERMINAL | {"waiting_input"} or job["notified"]:
                continue
            try:
                destination = await self.result_destination(job)
                await self.send_result(destination, job)
                self.jobs.notification_marker(job["id"], job["revision"]).touch(
                    mode=0o600
                )
            except (discord.HTTPException, OSError, ValueError) as error:
                self.poliswag.utility.log_to_file(
                    f"[AGENT] delivery failed for {job['id']}: {error}"
                )
                continue

    @deliver_results.before_loop
    async def before_results(self):
        await self.poliswag.wait_until_ready()

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            return
        original = getattr(error, "original", error)
        if isinstance(original, ValueError):
            message = str(original)
        elif isinstance(error, commands.UserInputError):
            message = _USAGE
        elif isinstance(original, discord.HTTPException):
            message = "Não consegui enviar-te uma DM."
        else:
            self.poliswag.utility.log_to_file(
                f"[AGENT] command failed: {original}", "ERROR"
            )
            message = "Não consegui processar o pedido."
        await ctx.send(
            short_sentence(message), allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(poliswag):
    await poliswag.add_cog(Agents(poliswag))
