"""Owner-only Claude/Codex delegation and Poliswag's runtime awareness."""

import asyncio
import io
import time

import discord
from discord.ext import commands, tasks

from modules.agent_jobs import AgentJobs, TERMINAL, parse_direct_request
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
}
_USAGE = (
    "`!ask claude <pergunta>` · `!ask openai <pergunta>`\n"
    "`!code claude <pedido>` · `!code openai <pedido>`\n"
    "`!jobs` · `!job <código>` · `!cancel <código>` · `!self`\n"
    "Em DM também podes escrever `claude <pergunta>` ou `code claude <pedido>`."
)


class Agents(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.jobs = AgentJobs(Config.AGENT_JOBS_DIR)

    async def cog_load(self):
        self.deliver_results.start()

    async def cog_unload(self):
        self.deliver_results.cancel()

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
            # Resolve the DM before enqueueing: avoid starting work with no way
            # to tell the owner it started or return its result.
            dm = await ctx.author.create_dm()
            await dm.send(
                embed=status_embed("🤖 Pedido recebido", "A verificar o executor…"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            job = self.jobs.submit(
                owner_id=ctx.author.id,
                provider=provider,
                mode=mode,
                prompt=prompt,
                context=await self.snapshot(),
            )
            await dm.send(
                embed=status_embed(
                    "🤖 Tarefa na fila",
                    f"`{job['id']}` · {job['provider']} · "
                    f"{'código' if mode == 'code' else 'consulta'}\n"
                    "Aviso-te aqui quando terminar. "
                    f"Estado: `!job {job['id']}`.",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if ctx.guild is not None:
                await ctx.send("Enviei o estado da tarefa por DM.")
            self.poliswag.utility.log_to_file(
                f"[AGENT] owner {ctx.author.id} queued {job['id']} {job['provider']} {mode}"
            )
        except ValueError as error:
            await ctx.send(embed=status_embed("Pedido não iniciado", str(error)))
        except discord.HTTPException:
            await ctx.send(embed=status_embed("Não consegui enviar-te uma DM."))

    @commands.command(name="code", brief="Pede alterações de código a Claude ou Codex")
    @owner_only()
    async def code(self, ctx, provider: str, *, prompt: str):
        """!code claude|codex|openai <pedido> — altera e testa numa cópia separada."""
        await self.submit(ctx, "code", provider, prompt)

    @commands.command(name="ask", brief="Consulta Claude ou Codex sobre o bot")
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
        if ctx.guild is not None:
            await ctx.send("Enviei o meu estado por DM.")

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
        await self.send_result(ctx.author, job)

    @commands.command(name="cancel", brief="Cancela uma tarefa de Claude ou Codex")
    @owner_only()
    async def cancel_job(self, ctx, job_id: str):
        self.jobs.cancel(job_id, ctx.author.id)
        await ctx.send(embed=status_embed("Cancelamento pedido", f"`{job_id}`"))

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
            return False
        await self.submit(ctx, *parsed)
        return True

    async def send_result(self, user, job):
        description = f"`{job['id']}` · {job['provider']} · {_STATES[job['state']]}"
        if job.get("branch"):
            description += f"\nRamo: `{job['branch']}`"
        answer = self.jobs.directory(job["id"]) / "answer.txt"
        kwargs = {}
        if job["state"] in TERMINAL:
            text = answer.read_text() if answer.exists() else job.get("message", "")
            if job["state"] != "completed" and job.get("message"):
                text = f"{text}\n\n{job['message']}".strip()
            description += "\n\n" + text[:3000]
            if len(text) > 3000:
                kwargs["file"] = discord.File(
                    io.BytesIO(text[:500000].encode()), filename=f"{job['id']}.txt"
                )
        await user.send(
            embed=status_embed("🤖 Tarefa", description),
            allowed_mentions=discord.AllowedMentions.none(),
            **kwargs,
        )

    @tasks.loop(seconds=10)
    async def deliver_results(self):
        for job in self.jobs.list(Config.MY_ID):
            if job["state"] not in TERMINAL or job["notified"]:
                continue
            try:
                user = self.poliswag.get_user(Config.MY_ID)
                if user is None:
                    user = await self.poliswag.fetch_user(Config.MY_ID)
                await self.send_result(user, job)
                (self.jobs.directory(job["id"]) / "notified").touch(mode=0o600)
            except (discord.HTTPException, OSError) as error:
                self.poliswag.utility.log_to_file(
                    f"[AGENT] delivery failed for {job['id']}: {error}"
                )
                break

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
        await ctx.send(embed=status_embed("🤖 Agentes", message))


async def setup(poliswag):
    await poliswag.add_cog(Agents(poliswag))
