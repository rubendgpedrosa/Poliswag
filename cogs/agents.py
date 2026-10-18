"""Owner-only, read-only Claude/Codex delegation and Poliswag's runtime awareness.

Agents start only from a tagged owner message (cogs/mentions.py); there is no
untagged `!` command that queues or resumes agent work.
"""

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
from modules.embeds import react_to_request, status_embed
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
    "Marca-me (@Poliswag) com qualquer pergunta, em DM ou no servidor; uso Claude por defeito. "
    "Pede Codex quando quiseres mudar de agente. Só consulto: não altero código.\n"
    "`@Poliswag answer <código> <resposta>` responde a uma tarefa à espera.\n"
    "`!jobs` · `!job <código>` · `!cancel <código>` · `!self`"
)
_ANSWER = re.compile(r"!?answer\s+([0-9a-f]{12})\s+(.+)", re.I | re.S)


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

    async def submit(self, ctx, provider, prompt):
        # Mentions bypass command invocation, so gate it here too.
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
                prompt=prompt,
                context=await self.snapshot(),
                destination=destination,
                origin=self.origin_for(ctx),
            )
            self.poliswag.utility.log_to_file(
                f"[AGENT] owner {ctx.author.id} queued {job['id']} {job['provider']}"
            )
        except ValueError as error:
            await ctx.send(
                embed=status_embed("🐸 Poliswag", short_sentence(error)),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            await ctx.send(
                embed=status_embed("🐸 Poliswag", "Não consegui abrir a tua DM."),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @staticmethod
    def origin_for(ctx):
        if ctx.guild is None:
            return {"type": "dm"}
        return {
            "type": "channel",
            "channel_id": ctx.channel.id,
            "guild_id": ctx.channel.guild.id,
        }

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

    @commands.command(name="self", brief="Mostra capacidades e estado do Poliswag")
    @owner_only()
    async def self_status(self, ctx):
        context = await self.snapshot()
        worker = context["worker"]
        providers = ", ".join(k for k, v in worker.get("providers", {}).items() if v)
        embed = status_embed(
            "🤖 Poliswag",
            "Sou o bot do PoGoLeiria. Faço a gestão de quests, eventos, alertas e saúde "
            "do scanner. Posso delegar consultas só de leitura a Claude ou Codex "
            "quando me marcas.",
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

    @commands.command(name="cancel", brief="Cancela uma tarefa de Claude ou Codex")
    @owner_only()
    async def cancel_job(self, ctx, job_id: str):
        self.jobs.cancel(job_id, ctx.author.id)

    async def submit_natural_request(self, ctx, text):
        """Only the real owner can delegate free-form mentions.

        Reached only from Mentions, so untagged messages never start work.

        A read-only general-purpose agent answers any question; it never edits.
        No other person's messages, attachments, or channel history are included.
        """
        if not is_owner(ctx):
            return
        answer = _ANSWER.fullmatch(text.strip())
        if answer:
            await self.resume(ctx, *answer.groups())
            return
        parsed = parse_direct_request(text)
        if parsed:
            await self.submit(ctx, *parsed)
            return
        origin = self.origin_for(ctx)
        waiting = [
            job
            for job in self.jobs.list(ctx.author.id)
            if job["state"] == "waiting_input"
            and (job.get("origin") or job.get("destination") or {"type": "dm"})
            == origin
        ]
        if len(waiting) == 1:
            await self.resume(ctx, waiting[0]["id"], text)
            return
        if len(waiting) > 1:
            await ctx.send(
                embed=status_embed(
                    "🐸 Poliswag",
                    "Qual tarefa devo continuar? Marca-me com `answer <código> <resposta>`.",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        provider = "codex" if re.search(r"\b(openai|codex)\b", text, re.I) else "claude"
        await self.submit(ctx, provider, text)

    async def resume(self, ctx, job_id, response):
        try:
            self.jobs.resume(job_id, ctx.author.id, response)
            await self.acknowledge(ctx)
        except ValueError as error:
            await ctx.send(
                embed=status_embed("🐸 Poliswag", short_sentence(error)),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @staticmethod
    async def acknowledge(ctx):
        await react_to_request(ctx.message)

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
            text = (job.get("reply") or details or "Feito.").strip()
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
                        text = (
                            job.get("reply") or "O relatório está no ficheiro."
                        ).strip()
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
        if len(text) > 2000:
            if "file" in kwargs:
                kwargs["file"].close()
            kwargs["file"] = discord.File(
                io.BytesIO(text.encode()), filename=f"{job['id']}.txt"
            )
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
                text[:2000],
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
            embed=status_embed("🐸 Poliswag", short_sentence(message)),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(poliswag):
    await poliswag.add_cog(Agents(poliswag))
