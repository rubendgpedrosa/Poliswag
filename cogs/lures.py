import asyncio
import time
import re

from discord.ext import commands

from modules import lure_locks
from modules.config import Config
from modules.embeds import build_embed
from modules.http_client import get_session

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
}


class Lures(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.lure_manager = poliswag.lure_manager

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
                async with get_session() as session:
                    async with session.post(
                        Config.POLISNATCH_LINK_URL.rstrip("/") + "/command",
                        json={"worker": worker, "action": verb},
                        headers=_headers(),
                    ) as reply:
                        if reply.status != 202:
                            raise RuntimeError(f"HTTP {reply.status}")
            except Exception as error:  # noqa: BLE001
                await ctx.send(embed=build_embed("COMANDO NÃO ENTREGUE", f"`{error}`"))
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
            async with get_session() as session:
                async with session.get(
                    Config.POLISNATCH_LINK_URL.rstrip("/") + "/state",
                    headers=_headers(),
                ) as reply:
                    if reply.status != 200:
                        raise RuntimeError(f"HTTP {reply.status}")
                    data = await reply.json()
        except Exception as error:  # noqa: BLE001
            await ctx.send(embed=build_embed("POLISNATCH INACESSÍVEL", f"`{error}`"))
            return

        workers = data.get("workers") or {}
        if not workers:
            await ctx.send(
                embed=build_embed(
                    "SEM ESTADO",
                    "Nenhum worker reporta estado. O interruptor *Report status to "
                    "the bridge* está desligado, ou não há telefone ligado.",
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

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
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
            job = await _post(
                "/lure",
                {
                    "lat": lat,
                    "lon": lon,
                    "lureType": lure_type,
                    "requestedBy": str(ctx.author.id),
                },
            )
        except Exception as error:  # noqa: BLE001 - report, don't crash
            await ctx.send(
                embed=build_embed("POLISNATCH INDISPONÍVEL", str(error)[:200])
            )
            return

        if job.get("duplicate"):
            # Someone else's identical request is still being worked on: follow
            # that one instead of queueing a second, and say so.
            await ctx.send(
                embed=build_embed(
                    "JÁ EM CURSO",
                    f"Esse stop já está a ser tratado (job `{job['jobId']}`). "
                    "Aviso quando terminar.",
                )
            )
            asyncio.create_task(_follow(ctx, job["jobId"], nearest, lat, lon, tipo))
            return

        await ctx.send(
            embed=build_embed(
                "LURE NA FILA",
                (
                    "Stop mais próximo do worker"
                    if nearest
                    else f"Stop em {lat:.5f}, {lon:.5f}"
                )
                + f" — {tipo or 'normal'}. Job `{job['jobId']}`; "
                "aviso quando estiver colocado.",
            )
        )

        result = await _await_job(job["jobId"], "/lure/")
        if result is None:
            await ctx.send(
                embed=build_embed(
                    "LURE SEM RESPOSTA", "Nenhum worker completou o trabalho a tempo."
                )
            )
            return
        _settle(key, job["jobId"], result, ctx.author.id)

        if result.get("state") == "done":
            await ctx.reply(embed=build_embed("LURE COLOCADO", f"{lat:.5f}, {lon:.5f}"))
            return
        reason = REASONS.get(
            str(result.get("failReason")), str(result.get("failReason"))
        )
        await ctx.reply(embed=build_embed("LURE NÃO COLOCADO", reason))

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


def _headers():
    try:
        with open(Config.POLISNATCH_LINK_TOKEN_FILE, encoding="utf-8") as handle:
            token = handle.read().strip()
    except FileNotFoundError:
        token = ""
    return {"Authorization": token} if token else {}


async def _post(path, body):
    session = get_session()
    async with session.post(
        Config.POLISNATCH_LINK_URL.rstrip("/") + path, json=body, headers=_headers()
    ) as reply:
        # 202 queued, 200 already in flight for this stop - both are answers.
        if reply.status not in (200, 202):
            raise RuntimeError(f"HTTP {reply.status}")
        return await reply.json()


async def _await_job(job_id, prefix, deadline_s=90.0):
    """Poll until the job leaves queued/claimed, or give up."""
    session = get_session()
    waited = 0.0
    while waited < deadline_s:
        async with session.get(
            Config.POLISNATCH_LINK_URL.rstrip("/") + prefix + job_id, headers=_headers()
        ) as reply:
            if reply.status != 200:
                return None
            state = await reply.json()
        if state.get("state") in ("done", "failed", "cancelled"):
            return state
        await asyncio.sleep(2.0)
        waited += 2.0
    return None


async def _follow(ctx, job_id, nearest, lat, lon, tipo, deadline_s=900.0):
    """Keeps watching a job the command gave up on, then edits a fresh reply."""
    result = await _await_job(job_id, "/lure/", deadline_s=deadline_s)
    if result is None:
        return
    where = "Stop mais próximo do worker" if nearest else f"{lat:.5f}, {lon:.5f}"
    if result.get("state") == "done":
        verified = result.get("verified")
        text = where
        if verified is True:
            text += " — confirmado no mapa"
        elif verified is False:
            text += " — colocado, mas não confirmado no mapa"
        await ctx.reply(embed=build_embed("LURE COLOCADO", text))
        return
    reason = REASONS.get(str(result.get("failReason")), str(result.get("failReason")))
    await ctx.reply(embed=build_embed("LURE NÃO COLOCADO", reason))


def _settle(key, job_id, result, discord_id):
    """Lock the stop only when the lure is verified; otherwise let the cooldown go."""
    try:
        if result.get("state") == "done" and result.get("verified") is True:
            lure_locks.lock(key, fort_id=result.get("fortId"), discord_id=discord_id)
        else:
            lure_locks.clear(key)
    except Exception as error:  # noqa: BLE001 - never break the reply
        print(f"lure lock: {error} ({job_id})")
