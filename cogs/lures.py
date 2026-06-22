from discord.ext import commands

from modules.embeds import build_embed


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
        accounts = self.lure_manager.list_available_with_lures()
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
        name="uselure",
        brief="Ajusta o número de lures de uma conta",
        help="Utilização: uselure USERNAME NUMERO. NUMERO positivo adiciona "
        "lures, negativo remove (mínimo 0).",
    )
    async def uselure(
        self, ctx, username: str | None = None, number: str | None = None
    ):
        if username is None or number is None:
            await ctx.send("Utilização: `!uselure USERNAME NUMERO`")
            return
        try:
            delta = int(number)
        except ValueError:
            await ctx.send(
                "NUMERO tem de ser um inteiro. Utilização: `!uselure USERNAME NUMERO`"
            )
            return

        if delta == 0:
            await ctx.send("NUMERO tem de ser diferente de zero.")
            return

        affected = self.lure_manager.adjust_lure_count(username, delta)
        if not affected:
            await ctx.send(f"A conta `{username}` não foi encontrada.")
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
