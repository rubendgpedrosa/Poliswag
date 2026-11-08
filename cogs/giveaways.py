"""!sorteio: sorteios por reação num anúncio.

Um mod regista a mensagem (link ou resposta a ela); o bot lê do próprio texto
a hora (`<t:…>`) e o emoji personalizado, e reage com ele para que entrar seja
um clique. A cada minuto, um sorteio cuja hora chegou é tirado entre quem tem
a reação nesse momento (sem bots, sem quem escreveu o anúncio, que entrega o
prémio, e sem quem o registou). O vencedor é anunciado em resposta ao anúncio
e quem registou recebe um DM com o resultado.
"""

import discord
from discord.ext import commands, tasks

from modules import giveaways
from modules.embeds import status_embed
from modules.permissions import is_mod

_USAGE = (
    "**Registar:** responde ao anúncio com `!sorteio`, ou "
    "`!sorteio <link da mensagem>`. A hora (`<t:…>`) e o emoji personalizado "
    "vêm do próprio anúncio; podes indicar outros: "
    "`!sorteio <link> <t:1794348000:f> 2` (2 vencedores).\n"
    "**Ver:** `!sorteio estado` · **Já:** `!sorteio sortear` · "
    "**Novo vencedor:** `!sorteio repetir` · `!sorteio cancelar`\n"
    "Com um só sorteio aberto não precisas do link."
)
_MAX_WINNERS = 20


class Giveaways(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    def cog_check(self, ctx):
        return is_mod(ctx)

    async def cog_load(self):
        self.draw_due.start()

    async def cog_unload(self):
        self.draw_due.cancel()

    # --- scheduler -------------------------------------------------------

    @tasks.loop(seconds=60)
    async def draw_due(self):
        try:
            rows = await self.poliswag.db.get_data_from_database(giveaways.DUE_SQL)
        except Exception as error:
            self._log(f"due query failed: {type(error).__name__}", "ERROR")
            return
        for row in rows or []:
            try:
                await self.draw(row)
            except Exception as error:
                self._log(
                    f"draw {row.get('message_id')} failed: "
                    f"{type(error).__name__}: {error}",
                    "ERROR",
                )

    @draw_due.before_loop
    async def before_draw_due(self):
        await self.poliswag.wait_until_ready()

    # --- core ------------------------------------------------------------

    def _log(self, text, level="INFO"):
        self.poliswag.utility.log_to_file(f"[SORTEIO] {text}", level)

    async def _message(self, row):
        channel_id = int(row["channel_id"])
        channel = self.poliswag.get_channel(channel_id)
        if channel is None:
            channel = await self.poliswag.fetch_channel(channel_id)
        return await channel.fetch_message(int(row["message_id"]))

    async def participants(self, row, message=None):
        """(mensagem, ids) de quem tem a reação agora: sem bots, sem o autor
        do anúncio nem quem registou o sorteio, e só membros do servidor
        quando a lista de membros está completa."""
        message = message or await self._message(row)
        reaction = next(
            (r for r in message.reactions if str(r.emoji) == row["emoji"]), None
        )
        if reaction is None:
            return message, []
        guild = message.guild
        excluded = {int(row["created_by"]), message.author.id}
        ids = []
        async for user in reaction.users(limit=None):
            if user.bot or user.id in excluded or user.id in ids:
                continue
            if (
                guild is not None
                and guild.chunked
                and guild.get_member(user.id) is None
            ):
                continue
            ids.append(user.id)
        return message, ids

    async def draw(self, row, reroll=False):
        """Tira o(s) vencedor(es), fecha a linha e anuncia. Devolve os novos
        vencedores, [] sem participantes, ou None se outro sorteio já a fechou."""
        message, ids = await self.participants(row)
        previous = giveaways.winner_ids(row)
        count = 1 if reroll else int(row.get("winners") or 1)
        won = giveaways.pick(ids, count, exclude=previous)
        all_winners = previous + won
        expected = "drawn" if reroll else "open"
        closed = await self.poliswag.db.execute_query_to_database(
            giveaways.CLOSE_SQL,
            params=(
                ",".join(str(i) for i in all_winners) or None,
                len(ids),
                int(row["message_id"]),
                expected,
            ),
        )
        if not closed:
            return None
        self._log(
            f"{giveaways.message_link(row)}: {len(ids)} participants, "
            f"winners {won} (reroll={reroll})"
        )
        await self._announce(message, row, won, len(ids), reroll)
        await self._tell_creator(row, won, len(ids), reroll)
        return won

    async def _announce(self, message, row, won, total, reroll):
        if not won:
            text = None
            embed = status_embed(
                "🎟️ Sorteio sem participantes",
                f"Ninguém reagiu com {row['emoji']}, por isso não houve vencedor.",
            )
        else:
            mentions = ", ".join(f"<@{i}>" for i in won)
            text = f"🎉 {mentions}"
            title = "🔁 Novo vencedor do sorteio" if reroll else "🎉 Sorteio terminado!"
            embed = status_embed(
                title,
                f"Parabéns {mentions}! "
                f"{'Ganhaste' if len(won) == 1 else 'Ganharam'} o sorteio "
                f"({total} {'participante' if total == 1 else 'participantes'}).\n"
                f"Vais receber o prémio de <@{message.author.id}>.",
            )
        await message.reply(
            content=text,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                users=True, everyone=False, roles=False, replied_user=False
            ),
        )

    async def _tell_creator(self, row, won, total, reroll):
        creator_id = int(row["created_by"])
        try:
            user = self.poliswag.get_user(creator_id) or await self.poliswag.fetch_user(
                creator_id
            )
            lines = [
                f"[Anúncio]({giveaways.message_link(row)}) · {total} participantes."
            ]
            if won:
                lines.append("Vencedor: " + ", ".join(f"<@{i}> (`{i}`)" for i in won))
                lines.append(
                    "Se não responder, `!sorteio repetir` tira outro (sem repetir "
                    "quem já ganhou)."
                )
            else:
                lines.append("Sem participantes: ninguém ganhou.")
            await user.send(
                embed=status_embed(
                    "🔁 Sorteio repetido" if reroll else "🎟️ Sorteio feito",
                    "\n".join(lines),
                )
            )
        except discord.HTTPException as error:
            self._log(f"creator DM failed: {error}", "ERROR")

    # --- commands --------------------------------------------------------

    async def _target(self, ctx, text, statuses=("open",)):
        """A linha visada: link no texto, a mensagem a que se responde, ou o
        único sorteio aberto."""
        parsed = giveaways.parse_link(text)
        reference = getattr(ctx.message, "reference", None)
        message_id = (
            parsed[1]
            if parsed
            else getattr(reference, "message_id", None) if reference else None
        )
        db = self.poliswag.db
        if message_id:
            rows = await db.get_data_from_database(
                giveaways.SELECT_SQL, params=(int(message_id),)
            )
            row = rows[0] if rows else None
            if row is None or row["status"] not in statuses:
                await ctx.send(embed=status_embed("❌ Sorteio não encontrado", _USAGE))
                return None
            return row
        rows = await db.get_data_from_database(giveaways.OPEN_SQL)
        if "drawn" in statuses and not rows:
            rows = await db.get_data_from_database(
                "SELECT * FROM giveaway WHERE status = 'drawn' "
                "ORDER BY drawn_at DESC LIMIT 1"
            )
        if len(rows or []) != 1:
            await ctx.send(
                embed=status_embed(
                    "❓ Qual sorteio?",
                    "Indica o link da mensagem ou responde ao anúncio.\n\n" + _USAGE,
                )
            )
            return None
        return rows[0]

    @commands.group(
        name="sorteio",
        invoke_without_command=True,
        brief="Sorteio por reação num anúncio",
        help=_USAGE,
    )
    async def sorteio(self, ctx, *, texto: str = ""):
        parsed = giveaways.parse_link(texto)
        reference = getattr(ctx.message, "reference", None)
        if not parsed and not (reference and reference.message_id):
            await self._list(ctx)
            return
        if parsed:
            channel_id, message_id = parsed
        else:
            channel_id, message_id = reference.channel_id, reference.message_id
        try:
            channel = self.poliswag.get_channel(
                channel_id
            ) or await self.poliswag.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            await ctx.send(
                embed=status_embed(
                    "❌ Não encontrei a mensagem",
                    "Confirma o link e se o Poliswag consegue ver esse canal.",
                )
            )
            return
        rest = giveaways._LINK.sub("", texto)
        draw_at = giveaways.parse_draw_at(rest) or giveaways.parse_draw_at(
            message.content
        )
        emoji = giveaways.find_emoji(rest) or giveaways.find_emoji(message.content)
        numbers = [w for w in rest.split() if w.isdigit() and len(w) <= 2]
        winners = max(1, min(_MAX_WINNERS, int(numbers[0]) if numbers else 1))
        if draw_at is None or emoji is None:
            missing = " e ".join(
                x
                for x, ok in (("a hora (`<t:…>`)", draw_at), ("o emoji", emoji))
                if ok is None
            )
            await ctx.send(
                embed=status_embed(
                    "❌ Falta informação",
                    f"Não encontrei {missing} no anúncio. Indica-os no comando.\n\n"
                    + _USAGE,
                )
            )
            return
        await self.poliswag.db.execute_query_to_database(
            giveaways.INSERT_SQL,
            params=(
                message.id,
                message.channel.id,
                message.guild.id if message.guild else 0,
                emoji,
                draw_at,
                winners,
                ctx.author.id,
            ),
        )
        reacted = True
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            reacted = False
        self._log(
            f"{ctx.author} registered {message.jump_url} emoji {emoji} at {draw_at}"
        )
        when = giveaways.unix(draw_at)
        await ctx.send(
            embed=status_embed(
                "🎟️ Sorteio registado",
                f"[Anúncio]({message.jump_url}) · entra quem reagir com {emoji}.\n"
                f"Sorteio: <t:{when}:F> (<t:{when}:R>) · "
                f"{winners} {'vencedor' if winners == 1 else 'vencedores'}.\n"
                + (
                    "Já reagi com o emoji, por isso entrar é um clique."
                    if reacted
                    else "Não consegui reagir com o emoji (o bot não o tem?)."
                )
                + "\nNão entram bots, nem tu, nem quem escreveu o anúncio.",
            )
        )

    async def _list(self, ctx):
        rows = await self.poliswag.db.get_data_from_database(giveaways.OPEN_SQL)
        if not rows:
            await ctx.send(embed=status_embed("🎟️ Sem sorteios abertos", _USAGE))
            return
        lines = [
            f"[Anúncio]({giveaways.message_link(r)}) · {r['emoji']} · "
            f"<t:{giveaways.unix(r['draw_at'])}:R>"
            for r in rows
        ]
        await ctx.send(
            embed=status_embed(
                "🎟️ Sorteios abertos", "\n".join(lines) + "\n\n" + _USAGE
            )
        )

    @sorteio.command(name="estado", brief="Quantos participantes tem o sorteio")
    async def estado(self, ctx, *, texto: str = ""):
        row = await self._target(ctx, texto, statuses=("open", "drawn"))
        if row is None:
            return
        _, ids = await self.participants(row)
        when = giveaways.unix(row["draw_at"])
        lines = [
            f"[Anúncio]({giveaways.message_link(row)}) · {row['emoji']}",
            f"**{len(ids)}** {'participante' if len(ids) == 1 else 'participantes'}"
            " neste momento.",
        ]
        if row["status"] == "open":
            lines.append(f"Sorteio <t:{when}:F> (<t:{when}:R>).")
        else:
            won = giveaways.winner_ids(row)
            lines.append(
                "Já sorteado. Vencedor(es): "
                + (", ".join(f"<@{i}>" for i in won) or "ninguém")
            )
        await ctx.send(embed=status_embed("🎟️ Estado do sorteio", "\n".join(lines)))

    @sorteio.command(name="sortear", brief="Sorteia já, sem esperar pela hora")
    async def sortear(self, ctx, *, texto: str = ""):
        row = await self._target(ctx, texto)
        if row is None:
            return
        won = await self.draw(row)
        if won is None:
            await ctx.send(embed=status_embed("ℹ️ Este sorteio já foi feito"))
        else:
            await ctx.send(
                embed=status_embed("✅ Sorteio feito", "Vê o resultado no anúncio.")
            )

    @sorteio.command(name="repetir", brief="Tira um novo vencedor (sem repetir)")
    async def repetir(self, ctx, *, texto: str = ""):
        row = await self._target(ctx, texto, statuses=("drawn",))
        if row is None:
            return
        won = await self.draw(row, reroll=True)
        if not won:
            await ctx.send(
                embed=status_embed(
                    "ℹ️ Sem novo vencedor",
                    "Já não há participantes que ainda não tenham ganho.",
                )
            )
        else:
            await ctx.send(
                embed=status_embed("✅ Novo vencedor", "Vê o resultado no anúncio.")
            )

    @sorteio.command(name="cancelar", brief="Cancela um sorteio aberto")
    async def cancelar(self, ctx, *, texto: str = ""):
        row = await self._target(ctx, texto)
        if row is None:
            return
        await self.poliswag.db.execute_query_to_database(
            giveaways.CANCEL_SQL, params=(int(row["message_id"]),)
        )
        self._log(f"{ctx.author} cancelled {giveaways.message_link(row)}")
        await ctx.send(
            embed=status_embed("🚫 Sorteio cancelado", "Ninguém vai ser sorteado.")
        )


async def setup(poliswag):
    await poliswag.add_cog(Giveaways(poliswag))
