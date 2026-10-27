import re
import time

import discord
from discord.ext import commands

from modules import lure_locks
from modules.embeds import build_embed
from modules.permissions import owner_only
from modules.polisnatch_api import JobApiError
from modules.polisnatch_jobs import JobPoller, PoliSnatchJobs
from modules.polisnatch_store import JobStore

LURE_TYPES = {
    "normal": 501,
    "glacial": 502,
    "mossy": 503,
    "magnetic": 504,
    "rainy": 505,
    "sparkly": 506,
}
REASONS = {
    "already_lured": "o stop já tinha lure",
    "too_far": "o worker não chegou ao stop",
    "no_item": "a conta não tem esse lure",
    "inaccessible": "o stop está inacessível",
    "unknown": "resposta inesperada do jogo",
    "unknown_job": "o servidor já não conhece este job",
    "cancelled": "o job foi cancelado",
}


class Lures(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.lure_manager = poliswag.lure_manager
        self.jobs = PoliSnatchJobs()
        self.job_store = JobStore()
        # Started in cog_load: its first tick is the startup sweep for jobs a
        # previous process left queued or claimed.
        self.poller = JobPoller(
            poliswag, self.jobs, self.job_store, announce=self._announce_job
        )

    @commands.command(name="pn")
    async def pn(self, ctx, action: str = "", worker: str = "*"):
        """What the phones say they are doing - or start/stop/restart their workers.

        !pn            the last word from each phone
        !pn restart    restart the job workers on every phone
        !pn stop       stop the job workers (the control poll keeps running)
        !pn start      start them again
        """
        if action:
            verb = action.lower()
            if verb not in ("start", "stop", "restart", "status"):
                await ctx.send(
                    embed=build_embed(
                        "AÇÃO DESCONHECIDA", "start, stop, restart ou status"
                    )
                )
                return
            try:
                await self.jobs.command(worker, verb)
            except JobApiError as error:
                await ctx.send(
                    embed=build_embed("COMANDO NÃO ENTREGUE", _error_text(error))
                )
                return
            where = "todos os telefones" if worker == "*" else f"`{worker}`"
            await ctx.send(
                embed=build_embed(
                    "COMANDO ENVIADO",
                    f"`{verb}` para {where}. É entregue no próximo poll (até 5s) "
                    "e aparece no `!pn` seguinte.",
                )
            )
            return

        try:
            workers = await self.jobs.workers()
        except JobApiError as error:
            await ctx.send(
                embed=build_embed("POLISNATCH INACESSÍVEL", _error_text(error))
            )
            return

        if not workers:
            await ctx.send(
                embed=build_embed(
                    "SEM ESTADO",
                    "Nenhum worker reporta estado. O telefone não está a reportar "
                    "estado, ou não está ligado.",
                )
            )
            return

        now = time.time()
        lines = []
        for name in sorted(workers):
            beat = workers[name]
            age = int(now - float(beat.get("receivedAt") or now))
            bits = []
            if beat.get("connected") is not None:
                bits.append("ligado" if beat["connected"] else "desligado")
            if beat.get("mode"):
                bits.append(str(beat["mode"]))
            if beat.get("ticks") is not None:
                bits.append(f"{beat['ticks']} ticks")
            if beat.get("rpc_total") is not None:
                bits.append(f"{beat['rpc_total']} rpc")
            attempts = beat.get("reconnect_attempts")
            if attempts:
                bits.append(f"{attempts} tentativa(s) de reconexão")
            if beat.get("paused"):
                bits.append("workers parados")
            if beat.get("last_error"):
                bits.append(f"erro: {beat['last_error']}")
            state = "vivo" if age < 45 else f"silencioso há {age}s"
            lines.append(
                f"**{name}** — {state} — " + (", ".join(bits) or "sem detalhe")
            )
        await ctx.send(embed=build_embed("POLISNATCH", "\n".join(lines[:12])))

    @commands.command(
        name="pstoken",
        brief="Emite um token PoliSnatch para um telefone (só na DM do dono)",
        help="Utilização: `!pstoken EMAIL [workers] [nome]`. O token aparece uma "
        "vez, só aqui na DM; o telefone entra com ele e o mesmo email.",
    )
    @owner_only()
    async def pstoken(
        self, ctx, email: str = "", workers: str = "1", *, name: str = ""
    ):
        if ctx.guild is not None:
            await ctx.send(
                embed=build_embed("SÓ POR DM", "Usa `!pstoken` na DM com o Poliswag.")
            )
            return
        if not email or "@" not in email:
            await ctx.send(
                embed=build_embed(
                    "UTILIZAÇÃO INVÁLIDA",
                    "Utilização: `!pstoken EMAIL [workers] [nome]`.",
                )
            )
            return
        try:
            slots = int(workers)
        except (TypeError, ValueError):
            slots = 0
        if not 1 <= slots <= 8:
            await ctx.send(
                embed=build_embed(
                    "UTILIZAÇÃO INVÁLIDA", "workers tem de ser um inteiro de 1 a 8."
                )
            )
            return
        try:
            result = await self.jobs.issue_token(email=email, name=name, workers=slots)
        except JobApiError as error:
            await ctx.send(embed=build_embed("TOKEN NÃO EMITIDO", _error_text(error)))
            return
        token = result.get("token")
        if not token:
            await ctx.send(
                embed=build_embed(
                    "TOKEN NÃO EMITIDO",
                    "O servidor aceitou o pedido mas não devolveu o token.",
                )
            )
            return
        scope = result.get("scope") or {}
        text = (
            f"Email: `{result.get('email') or email}`\n"
            f"Device: `{result.get('device_id') or '?'}`\n"
            f"Scope: slots={scope.get('max_workers', slots)} · "
            f"canais={','.join(scope.get('channels') or [])} · jobs=sim\n\n"
            f"```\n{token}\n```\n"
            "Aparece uma vez. Entra no PoliSnatch com este token **e o mesmo "
            "email**. Não o partilhes. Para o revogar: `revoke <device>` na CT 108."
        )
        await ctx.send(
            embed=build_embed("TOKEN POLISNATCH", text),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")
        self.poller.start()

    async def cog_unload(self):
        await self.poller.stop()
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    @commands.command(
        name="lures",
        brief="Lista as contas disponíveis com lures",
        help="Mostra até 5 contas livres e saudáveis com lures disponíveis, "
        "com username, password e número de lures restantes.",
    )
    async def lures(self, ctx):
        accounts = await self.lure_manager.list_available_with_lures()
        if not accounts:
            await ctx.send(
                embed=build_embed(
                    "LISTA DE CONTAS DISPONÍVEIS",
                    "Não há contas disponíveis com lures neste momento.",
                )
            )
            return

        lines = "\n".join(
            f"{a['username']} / {a['password']} — {a['nb_lures']} lures"
            for a in accounts
        )
        await ctx.send(embed=build_embed("LISTA DE CONTAS DISPONÍVEIS", lines))

    @commands.command(
        name="lure",
        brief="Coloca um lure num stop através do PoliSnatch",
        help="Utilização: `!lure [LAT,LON] [tipo]`. Sem coordenadas, o worker "
        "coloca o lure no stop mais próximo de si. Tipos: normal, glacial, mossy, "
        "magnetic, rainy, sparkly (por omissão normal).",
    )
    async def lure(self, ctx, coords: str | None = None, tipo: str | None = None):
        # No coordinates: the worker finds the nearest stop on its own map, which
        # it can also move to - so the lure is not a guess about where it stands.
        nearest = coords is None
        if nearest:
            lat, lon = None, None
        else:
            parsed = _parse_coords(coords)
            if parsed is None:
                await ctx.send(
                    embed=build_embed(
                        "UTILIZAÇÃO INVÁLIDA",
                        "Utilização: `!lure [LAT,LON] [tipo]` — sem coordenadas, "
                        "usa o stop mais próximo do worker.",
                    )
                )
                return
            lat, lon = parsed
        lure_type = LURE_TYPES.get((tipo or "normal").lower())
        if lure_type is None:
            await ctx.send(
                embed=build_embed(
                    "LURE DESCONHECIDO",
                    "Tipos: " + ", ".join(sorted(LURE_TYPES)) + ".",
                )
            )
            return

        # The map is the last word, but the worker is the one who pays to learn
        # it. A locked stop (a verified lure inside its lifetime) and a short
        # cooldown are checked here, before anything is queued.
        key = (
            lure_locks.stop_key(lat=lat, lon=lon) if nearest is False else "geo:nearest"
        )
        state = lure_locks.status(key)
        if state["locked_for_s"] > 0:
            minutes = (state["locked_for_s"] + 59) // 60
            await ctx.send(
                embed=build_embed(
                    "STOP JÁ TEM LURE",
                    f"Esse stop tem lure há {minutes} min. "
                    "Não vale a pena gastar outro.",
                )
            )
            return
        if state["cooldown_for_s"] > 0:
            await ctx.send(
                embed=build_embed(
                    "PEDIDO REPETIDO",
                    f"Já pediste este stop há {state['cooldown_for_s']}s.",
                )
            )
            return

        try:
            job = await self.jobs.submit_lure(
                lat=lat,
                lon=lon,
                nearest=nearest,
                lure_type=lure_type,
                requested_by=ctx.author.id,
            )
        except JobApiError as error:
            await ctx.send(
                embed=build_embed("POLISNATCH INDISPONÍVEL", _error_text(error))
            )
            return

        job_id = job.get("jobId")
        if not job_id:
            await ctx.send(
                embed=build_embed(
                    "RESPOSTA INESPERADA",
                    "O PoliSnatch aceitou o pedido mas não devolveu um job.",
                )
            )
            return

        if job.get("duplicate"):
            # A stop + lure already queued or claimed is one job, not an error:
            # follow the existing one instead of spending a second account.
            message = await ctx.send(
                embed=build_embed(
                    "JÁ EM CURSO",
                    f"Esse stop já está a ser tratado (job `{job_id}`). "
                    "O resultado é anunciado quando terminar.",
                )
            )
            await self._record_job(ctx, message, job_id, key, nearest, lat, lon, job)
            self.poller.wake()
            return

        where = (
            "Stop mais próximo do worker"
            if nearest
            else f"Stop em {lat:.5f}, {lon:.5f}"
        )
        message = await ctx.send(
            embed=build_embed(
                "LURE NA FILA",
                f"{where} — {tipo or 'normal'}. Job `{job_id}`; "
                "aviso quando estiver colocado.",
            )
        )
        if await self._record_job(ctx, message, job_id, key, nearest, lat, lon, job):
            self.poller.wake()

    async def _record_job(self, ctx, message, job_id, key, nearest, lat, lon, job):
        """Persist the job before waiting on it; False when the store failed."""
        try:
            self.job_store.record(
                job_id,
                "lure",
                discord_id=ctx.author.id,
                channel_id=ctx.channel.id,
                message_id=message.id,
                stop_key=key,
                lat=lat,
                lon=lon,
                nearest=nearest,
                state=job.get("state") or "queued",
            )
        except Exception as error:  # noqa: BLE001 - the job is already submitted
            self._log(f"PoliSnatch: cannot record job {job_id}: {error}", "ERROR")
            await ctx.send(
                embed=build_embed(
                    "AVISO INDISPONÍVEL",
                    "O lure foi pedido, mas não consegui guardar o job; "
                    "não te consigo avisar quando terminar.",
                )
            )
            return False
        return True

    async def _announce_job(self, row):
        """Called by the poller once per finished lure job."""
        if row.get("kind") != "lure":
            # Link jobs belong to the shiny linker, which writes shiny_link.
            return
        state = row.get("state")
        where = (
            "Stop mais próximo do worker"
            if row.get("nearest")
            else _where(row.get("lat"), row.get("lon"))
        )
        if state == "done":
            text = where
            verified = row.get("verified")
            if verified in (1, True):
                text += " — confirmado no mapa"
            elif verified in (0, False):
                text += " — colocado, mas não confirmado no mapa"
            embed = build_embed("LURE COLOCADO", text)
        else:
            reason = row.get("fail_reason") or state or "unknown"
            embed = build_embed(
                "LURE NÃO COLOCADO", REASONS.get(str(reason), str(reason))
            )
        await self._deliver(row, embed)
        _settle(
            row.get("stop_key"),
            row["job_id"],
            {
                "state": state,
                "verified": row.get("verified"),
                "fortId": row.get("fort_id"),
            },
            row.get("discord_id"),
        )

    async def _deliver(self, row, embed):
        """Edits the queued message, or sends a fresh one when it is gone."""
        channel_id = row.get("channel_id")
        if not channel_id:
            self._log(
                f"PoliSnatch: job {row.get('job_id')} has no channel to announce on",
                "ERROR",
            )
            return
        channel = self.poliswag.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.poliswag.fetch_channel(int(channel_id))
            except discord.NotFound:
                self._log(
                    f"PoliSnatch: channel {channel_id} is gone "
                    f"(job {row.get('job_id')})",
                    "ERROR",
                )
                return
        message = None
        if row.get("message_id"):
            try:
                message = await channel.fetch_message(int(row["message_id"]))
            except discord.NotFound:
                message = None
        if message is None:
            await channel.send(embed=embed)
            return
        await message.edit(embed=embed)

    def _log(self, message, level="INFO"):
        try:
            self.poliswag.utility.log_to_file(message, level)
        except Exception:  # noqa: BLE001 - never let logging break an announce
            pass

    @commands.command(
        name="uselure",
        brief="Ajusta o número de lures de uma conta",
        help="Utilização: uselure USERNAME NUMERO. NUMERO positivo adiciona "
        "lures, negativo remove (mínimo 0).",
    )
    async def uselure(
        self, ctx, username: str | None = None, number: str | None = None
    ):
        if username is None or number is None:
            await ctx.send(
                embed=build_embed(
                    "UTILIZAÇÃO INVÁLIDA", "Utilização: `!uselure USERNAME NUMERO`"
                )
            )
            return
        try:
            delta = int(number)
        except ValueError:
            await ctx.send(
                embed=build_embed(
                    "UTILIZAÇÃO INVÁLIDA",
                    "NUMERO tem de ser um inteiro. Utilização: `!uselure USERNAME NUMERO`",
                )
            )
            return

        if delta == 0:
            await ctx.send(
                embed=build_embed(
                    "UTILIZAÇÃO INVÁLIDA", "NUMERO tem de ser diferente de zero."
                )
            )
            return

        affected = await self.lure_manager.adjust_lure_count(username, delta)
        if not affected:
            await ctx.send(
                embed=build_embed(
                    "CONTA NÃO ENCONTRADA", f"A conta `{username}` não foi encontrada."
                )
            )
            return

        amount = abs(delta)
        action = "removida" if delta < 0 else "adicionada"
        plural = "" if amount == 1 else "s"
        self.poliswag.utility.log_to_file(
            f"[LURES] @{ctx.author} ({ctx.author.id}): "
            f"{amount} lure{plural} {action}{plural} -> {username}"
        )
        await ctx.send(
            embed=build_embed(
                "LURES ATUALIZADAS",
                f"{amount} lure{plural} {action}{plural} da conta **{username}**.",
            )
        )


async def setup(poliswag):
    await poliswag.add_cog(Lures(poliswag))


def _parse_coords(value):
    """'39.74,-8.80' (or '39.74 -8.80') -> (lat, lon), else None."""
    if not value:
        return None
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$", value)
    if match is None:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _where(lat, lon):
    if lat is None or lon is None:
        return "Stop sem coordenadas"
    return "%.5f, %.5f" % (float(lat), float(lon))


def _error_text(error):
    """PT copy for the job API's configuration errors and everything else."""
    if error.code == "no_key" or error.status == 401:
        return (
            "A chave de submissão do PoliSnatch está em falta ou não é válida. "
            "Gera-a com `polisnatch-dist jobs-key` e instala-a em "
            "`/etc/poliswag/secrets/polisnatch-jobs-key`."
        )
    if error.code == "jobs_disabled" or error.status == 503:
        return (
            "Os jobs estão desativados no servidor PoliSnatch: falta-lhe a chave "
            "de submissão ou um `link_lobby` na configuração."
        )
    if error.status is None:
        return f"PoliSnatch não respondeu (`{error}`)."
    return f"HTTP {error.status}: {error}"


def _settle(key, job_id, result, discord_id):
    """Lock the stop only when the lure is verified; otherwise let the cooldown go."""
    if not key:
        return
    try:
        if result.get("state") == "done" and result.get("verified") in (1, True):
            lure_locks.lock(key, fort_id=result.get("fortId"), discord_id=discord_id)
        else:
            lure_locks.clear(key)
    except Exception as error:  # noqa: BLE001 - never break the reply
        print(f"lure lock: {error} ({job_id})")
