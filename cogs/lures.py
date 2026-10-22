import asyncio
import re

from discord.ext import commands

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
        if reply.status != 202:
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
