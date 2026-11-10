"""!sorteio: sorteios por reação num anúncio, revistos pelos mods.

Um mod regista a mensagem (`!sorteio <link>` ou resposta a ela); o bot lê do
próprio texto a hora (`<t:…>`) e o emoji personalizado, e reage com ele para
que entrar seja um clique. Quem esteve no Campfire entra com
`!sorteio campfire` + capturas. À hora marcada (ou com `!sorteio sortear`) o
bot tira um nome entre quem tem a reação nesse momento e os do Campfire (sem
bots, sem quem escreveu o anúncio, que entrega o prémio, e sem quem o
registou), e não anuncia nada: põe uma mensagem de revisão no canal de
moderação, sem prazo, com quatro botões:

- Publicar: responde ao anúncio com o vencedor. Só quem escreveu o anúncio o
  pode fazer (os mods, se foi o Poliswag a escrevê-lo). Há sempre uma só
  resposta pública: ao publicar um reroll, a anterior é apagada.
- Repetir: tira outro nome; quem saiu pode voltar a sair numa próxima vez.
- Repetir sem este: tira outro e exclui quem saiu (já tem o prémio).
- Cancelar: para tudo.

`!sorteio reroll` abre uma nova revisão depois de publicado, para substituir
um vencedor que já tinha o prémio ou não apareceu; o público só vê o anúncio
novo.
"""

import io
from pathlib import Path
from types import SimpleNamespace

import discord
from discord.ext import commands, tasks

from modules import campfire, giveaways
from modules.config import Config
from modules.embeds import status_embed
from modules.permissions import is_mod

_USAGE = (
    "**Registar:** responde ao anúncio com `!sorteio`, ou "
    "`!sorteio <link da mensagem>`. A hora (`<t:…>`) e o emoji personalizado "
    "vêm do próprio anúncio; podes indicar outros: "
    "`!sorteio <link> <t:1794348000:f> 2` (2 vencedores).\n"
    "**Campfire:** `!sorteio campfire` com as capturas anexadas (ou em resposta "
    "a elas, ou com o link da mensagem que as tem): os nomes entram no sorteio, "
    "sem contar duas vezes quem também reagiu. `!sorteio campfire limpar` tira-os.\n"
    "**Sortear:** à hora marcada, ou já com `!sorteio sortear`. O resultado vai "
    "para a moderação com os botões Publicar · Repetir · Repetir sem este · "
    "Cancelar; só quem escreveu o anúncio publica.\n"
    "**Ver:** `!sorteio lista` (todos os nomes) · `!sorteio estado`\n"
    "**Depois:** `!sorteio reroll` (novo vencedor) · "
    "`!sorteio cancelar`\n"
    "Com um só sorteio ativo não precisas do link."
)
_MAX_WINNERS = 20
_PREFIX = "sorteio"
_ACTIONS = ("publish", "reroll", "exclude", "stop")
_NO_PINGS = discord.AllowedMentions.none()


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


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
                await self.start_review(row)
            except Exception as error:
                self._log(
                    f"draw {row.get('message_id')} failed: "
                    f"{type(error).__name__}: {error}",
                    "ERROR",
                )

    @draw_due.before_loop
    async def before_draw_due(self):
        await self.poliswag.wait_until_ready()

    # --- participants ----------------------------------------------------

    def _log(self, text, level="INFO"):
        self.poliswag.utility.log_to_file(f"[SORTEIO] {text}", level)

    async def _channel(self, channel_id):
        channel = self.poliswag.get_channel(int(channel_id))
        if channel is None:
            channel = await self.poliswag.fetch_channel(int(channel_id))
        return channel

    async def _message(self, row):
        channel = await self._channel(row["channel_id"])
        return await channel.fetch_message(int(row["message_id"]))

    async def _mod_channel(self):
        channel = getattr(self.poliswag, "MOD_CHANNEL", None)
        if channel is None and Config.MOD_CHANNEL_ID:
            channel = await self._channel(Config.MOD_CHANNEL_ID)
        return channel

    async def _trainers(self):
        """{discord_id: [trainer_name]} de quem associou a conta."""
        try:
            rows = await self.poliswag.db.get_data_from_database(giveaways.TRAINERS_SQL)
        except Exception as error:
            self._log(f"trainer names unavailable: {type(error).__name__}", "ERROR")
            return {}
        trainers = {}
        for row in rows or []:
            trainers.setdefault(int(row["discord_id"]), []).append(row["trainer_name"])
        return trainers

    async def participants(self, row, message=None):
        """(mensagem, ids, nomes) de quem tem a reação agora: sem bots, sem o
        autor do anúncio nem quem registou o sorteio, e só membros do
        servidor quando a lista de membros está completa. `nomes` é
        {discord_id: [nomes no Discord]}, para comparar com o Campfire."""
        message = message or await self._message(row)
        reaction = next(
            (r for r in message.reactions if str(r.emoji) == row["emoji"]), None
        )
        if reaction is None:
            return message, [], {}
        guild = message.guild
        excluded = {int(row["created_by"]), message.author.id}
        ids, names = [], {}
        async for user in reaction.users(limit=None):
            if user.bot or user.id in excluded or user.id in names:
                continue
            if (
                guild is not None
                and guild.chunked
                and guild.get_member(user.id) is None
            ):
                continue
            ids.append(user.id)
            names[user.id] = _names_of(user)
        return message, ids, names

    async def entries(self, row, message=None):
        """(mensagem, entradas, ids do Discord, nomes do Campfire repetidos):
        todos os que podem ganhar, cada pessoa uma vez."""
        message, pool, ids, _, duplicates = await self.listing(row, message)
        return message, pool, ids, duplicates

    async def listing(self, row, message=None):
        """Como `entries`, mais {discord_id: [nomes]} para mostrar a lista."""
        message, ids, names = await self.participants(row, message)
        trainers = await self._trainers()
        aliases = {}
        for user_id in ids:
            for name in names.get(user_id, []) + trainers.get(user_id, []):
                aliases.setdefault(giveaways.name_key(name), user_id)
        blocked = set()
        for user_id in {int(row["created_by"]), message.author.id}:
            for name in trainers.get(user_id, []):
                blocked.add(giveaways.name_key(name))
        for name in _names_of(message.author):
            blocked.add(giveaways.name_key(name))
        pool, duplicates = giveaways.pool(
            ids,
            giveaways.entries(row.get("campfire_names")),
            aliases,
            blocked,
        )
        return message, pool, ids, names, duplicates

    # --- review ----------------------------------------------------------

    async def start_review(self, row, reroll=False, replacing=None):
        """Tira o(s) nome(s) e põe a revisão na moderação. De `open` (o
        sorteio), de publicado (`replacing`: os vencedores a substituir) ou de
        uma revisão (`reroll`, que mantém quem estava a ser substituído).
        Devolve quem saiu, [] sem ninguém, ou None se outro pedido já mudou a
        linha."""
        message, pool, ids, duplicates = await self.entries(row)
        winners = giveaways.winner_ids(row)
        if replacing is None:
            replacing = giveaways.entries(row.get("replacing_ids"))
        exclude = winners + giveaways.entries(row.get("excluded_ids"))
        count = len(replacing) or (1 if winners else int(row.get("winners") or 1))
        avoid = giveaways.entries(row.get("pending_ids")) if reroll else ()
        pending = giveaways.pick(pool, count, exclude=exclude, avoid=avoid)
        from_status, from_review = (
            ("drawn", 1 if giveaways.in_review(row) else 0)
            if row["status"] == "drawn"
            else ("open", 0)
        )
        changed = await self.poliswag.db.execute_query_to_database(
            giveaways.REVIEW_SQL,
            params=(
                giveaways.dump(pending),
                giveaways.dump(replacing),
                len(pool),
                int(row["message_id"]),
                from_status,
                from_review,
            ),
        )
        if not changed:
            return None
        old_review = row.get("review_message_id") if from_review else None
        row = {
            **row,
            "status": "drawn",
            "awaiting_review": 1,
            "pending_ids": giveaways.dump(pending),
            "replacing_ids": giveaways.dump(replacing),
            "participants": len(pool),
            "review_message_id": None,
        }
        self._log(
            f"{giveaways.message_link(row)}: {len(pool)} entries "
            f"({len(ids)} discord), drew {pending} for review"
        )
        channel = await self._mod_channel()
        if channel is None:
            self._log("no moderation channel for the review", "ERROR")
            return pending
        embed = self._review_embed(row, message, pool, ids, duplicates)
        review = await channel.send(
            embed=embed,
            view=self._review_view(row, can_publish=bool(pending)),
            allowed_mentions=_NO_PINGS,
        )
        await self.poliswag.db.execute_query_to_database(
            giveaways.REVIEW_MESSAGE_SQL,
            params=(review.id, int(row["message_id"])),
        )
        if old_review:
            await self._retire(channel, old_review, "Substituída por uma nova revisão.")
        return pending

    def _review_embed(self, row, message, pool, ids, duplicates, footer=None):
        winners = giveaways.winner_ids(row)
        pending = giveaways.entries(row.get("pending_ids"))
        excluded = giveaways.entries(row.get("excluded_ids"))
        from_campfire = len(pool) - len(ids)
        lines = [
            f"[Anúncio]({giveaways.message_link(row)}) · "
            f"{_plural(len(pool), 'participante', 'participantes')} "
            f"({len(ids)} pela reação {row['emoji']}"
            + (f", {from_campfire} do Campfire" if from_campfire else "")
            + ")"
            + (
                f" · {_plural(len(duplicates), 'nome', 'nomes')} do Campfire "
                "já contava(m) pela reação"
                if duplicates
                else ""
            ),
        ]
        if pending:
            label = "Saiu" if len(pending) == 1 else "Saíram"
            lines.append(f"\n**{label}:** " + ", ".join(map(_describe, pending)))
        else:
            lines.append("\n**Ninguém para sortear** (sem participantes novos).")
        replacing = giveaways.entries(row.get("replacing_ids"))
        if replacing:
            lines.append(
                "A substituir: "
                + ", ".join(map(giveaways.show, replacing))
                + " (sai dos vencedores e fica excluído ao publicar)"
            )
        staying = [w for w in winners if w not in replacing]
        if staying:
            lines.append("Já publicado: " + ", ".join(map(giveaways.show, staying)))
        if excluded:
            lines.append("Excluídos: " + ", ".join(map(giveaways.show, excluded)))
        publisher = (
            "os mods"
            if message.author.bot
            else f"<@{message.author.id}> (quem escreveu o anúncio)"
        )
        lines.append(
            f"\n**Publicar** responde ao anúncio com o vencedor; só {publisher} "
            "pode publicar"
            + (
                ", e a resposta anterior no anúncio é apagada, por isso o canal "
                "não mostra o reroll"
                if winners
                else ""
            )
            + ". **Repetir** tira outro nome; **Repetir sem este** também o "
            "exclui (já tem o prémio). Nada é publicado sem um clique."
        )
        title = "🔁 Novo vencedor: confirmar" if winners else "🎟️ Sorteio: confirmar"
        embed = status_embed(title, "\n".join(lines))
        if footer:
            embed.set_footer(text=footer)
        return embed

    @staticmethod
    def _review_view(row, can_publish=True):
        message_id = int(row["message_id"])
        view = discord.ui.View(timeout=None)
        for action, label, style in (
            ("publish", "Publicar", discord.ButtonStyle.success),
            ("reroll", "Repetir", discord.ButtonStyle.primary),
            ("exclude", "Repetir sem este", discord.ButtonStyle.secondary),
            ("stop", "Cancelar", discord.ButtonStyle.danger),
        ):
            view.add_item(
                discord.ui.Button(
                    label=label,
                    style=style,
                    custom_id=f"{_PREFIX}:{action}:{message_id}",
                    disabled=action == "publish" and not can_publish,
                )
            )
        return view

    async def _retire(self, channel, review_id, footer):
        """Tira os botões de uma revisão que deixou de valer."""
        try:
            old = await channel.fetch_message(int(review_id))
            embed = old.embeds[0] if old.embeds else None
            if embed is not None:
                embed.set_footer(text=footer)
            await old.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != _PREFIX or parts[1] not in _ACTIONS:
            return
        action, message_id = parts[1], int(parts[2])
        await interaction.response.defer()
        try:
            await self._handle(interaction, action, message_id)
        except Exception as error:
            self._log(
                f"button {action} on {message_id} failed: "
                f"{type(error).__name__}: {error}",
                "ERROR",
            )
            await self._whisper(interaction, "Algo correu mal; vê o log do Poliswag.")

    async def _whisper(self, interaction, text):
        await interaction.followup.send(text, ephemeral=True)

    def _is_mod(self, user):
        return is_mod(SimpleNamespace(author=user, bot=self.poliswag))

    async def _handle(self, interaction, action, message_id):
        db = self.poliswag.db
        rows = await db.get_data_from_database(
            giveaways.SELECT_SQL, params=(message_id,)
        )
        row = rows[0] if rows else None
        review_id = interaction.message.id
        if (
            row is None
            or not giveaways.in_review(row)
            or int(row.get("review_message_id") or 0) != review_id
        ):
            await interaction.message.edit(view=None)
            await self._whisper(interaction, "Esta revisão já não está ativa.")
            return
        message = await self._message(row)
        user = interaction.user
        author_id = message.author.id
        if action == "publish":
            allowed = self._is_mod(user) if message.author.bot else user.id == author_id
            if not allowed:
                await self._whisper(
                    interaction,
                    f"Só <@{author_id}>, que escreveu o anúncio, pode publicar.",
                )
                return
        elif not (self._is_mod(user) or user.id == author_id):
            await self._whisper(interaction, "Só os mods mexem neste sorteio.")
            return

        pending = giveaways.entries(row.get("pending_ids"))
        winners = giveaways.winner_ids(row)
        if action == "stop":
            await db.execute_query_to_database(
                giveaways.STOP_SQL, params=(message_id, review_id)
            )
            self._log(f"{user} stopped {giveaways.message_link(row)}")
            note = (
                "volta ao vencedor já publicado" if winners else "ninguém foi sorteado"
            )
            await self._close(
                interaction, f"🚫 Cancelado por {user.display_name}: {note}."
            )
            return

        if action == "publish":
            if not pending:
                await self._whisper(interaction, "Não há ninguém para publicar.")
                return
            replacing = giveaways.entries(row.get("replacing_ids"))
            current = [w for w in winners if w not in replacing] + pending
            excluded = giveaways.entries(row.get("excluded_ids"))
            excluded += [r for r in replacing if r not in excluded]
            done = await db.execute_query_to_database(
                giveaways.PUBLISH_SQL,
                params=(
                    giveaways.dump(current),
                    giveaways.dump(excluded),
                    message_id,
                    review_id,
                    row["pending_ids"],
                ),
            )
            if not done:
                await self._whisper(interaction, "Esta revisão acabou de mudar.")
                return
            self._log(
                f"{user} published {pending} on {giveaways.message_link(row)}"
                + (f" replacing {replacing}" if replacing else "")
            )
            await self._announce(
                message, row, current, pending, int(row.get("participants") or 0)
            )
            await self._close(interaction, f"✅ Publicado por {user.display_name}.")
            return

        # reroll / exclude
        message, pool, ids, duplicates = await self.entries(row, message)
        excluded = giveaways.entries(row.get("excluded_ids"))
        if action == "exclude":
            excluded = excluded + [p for p in pending if p not in excluded]
        count = len(pending) or (1 if winners else int(row.get("winners") or 1))
        new = giveaways.pick(pool, count, exclude=winners + excluded, avoid=pending)
        if action == "reroll" and pending and set(new) == set(pending):
            await self._whisper(interaction, "Não há mais ninguém para tirar.")
            return
        done = await db.execute_query_to_database(
            giveaways.REROLL_SQL,
            params=(
                giveaways.dump(new),
                giveaways.dump(excluded),
                len(pool),
                message_id,
                review_id,
                row["pending_ids"],
            ),
        )
        if not done:
            await self._whisper(interaction, "Esta revisão acabou de mudar.")
            return
        self._log(
            f"{user} {action} on {giveaways.message_link(row)}: {pending} -> {new}"
        )
        row = {
            **row,
            "pending_ids": giveaways.dump(new),
            "excluded_ids": giveaways.dump(excluded),
        }
        footer = (
            f"🚫 {user.display_name} excluiu quem tinha saído e repetiu."
            if action == "exclude"
            else f"🔁 Repetido por {user.display_name}."
        )
        await interaction.message.edit(
            embed=self._review_embed(row, message, pool, ids, duplicates, footer),
            view=self._review_view(row, can_publish=bool(new)),
        )

    async def _close(self, interaction, footer):
        embeds = interaction.message.embeds
        embed = embeds[0] if embeds else None
        if embed is not None:
            embed.set_footer(text=footer)
        await interaction.message.edit(embed=embed, view=None)

    async def _announce(self, message, row, current, new, total):
        """A única resposta pública: apaga a anterior (se houver) e responde ao
        anúncio com os vencedores atuais, pingando só os novos. Um reroll fica
        assim igual a um primeiro sorteio para quem está no canal."""
        old_reply = row.get("announcement_reply_id")
        if old_reply:
            try:
                old = await message.channel.fetch_message(int(old_reply))
                await old.delete()
            except discord.HTTPException as error:
                self._log(f"old winner reply {old_reply} not deleted: {error}", "ERROR")
        mentions = ", ".join(giveaways.show(w) for w in current)
        pings = " ".join(f"<@{w}>" for w in new if isinstance(w, int))
        giver = int(row["created_by"]) if message.author.bot else message.author.id
        embed = status_embed(
            "🎉 Sorteio terminado!",
            f"Parabéns {mentions}! "
            f"{'Ganhaste' if len(current) == 1 else 'Ganharam'} o sorteio "
            f"({_plural(total, 'participante', 'participantes')}).\n"
            f"Vais receber o prémio de <@{giver}>.",
        )
        reply = await message.reply(
            content=f"🎉 {pings}" if pings else None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                users=True, everyone=False, roles=False, replied_user=False
            ),
        )
        await self.poliswag.db.execute_query_to_database(
            giveaways.REPLY_SQL, params=(reply.id, int(row["message_id"]))
        )
        return reply

    # --- commands --------------------------------------------------------

    async def _target(self, ctx, text, wanted, latest=False, quiet=False):
        """A linha visada: link no texto, a mensagem a que se responde, ou o
        único sorteio ativo. `wanted(row)` diz que linhas servem; com
        `latest`, entre várias fica a mais recente."""
        links = giveaways.parse_links(text)
        reference = getattr(ctx.message, "reference", None)
        candidates = [m for _, m in links]
        if reference and reference.message_id:
            candidates.append(reference.message_id)
        db = self.poliswag.db
        for message_id in candidates:
            rows = await db.get_data_from_database(
                giveaways.SELECT_SQL, params=(int(message_id),)
            )
            if rows:
                if wanted(rows[0]):
                    return rows[0]
                if not quiet:
                    await ctx.send(
                        embed=status_embed("❌ Este sorteio não serve aqui", _USAGE)
                    )
                return None
        rows = [
            r
            for r in await db.get_data_from_database(giveaways.RECENT_SQL) or []
            if wanted(r)
        ]
        if len(rows) == 1 or (rows and latest):
            return rows[0]
        if not quiet:
            await ctx.send(
                embed=status_embed(
                    "❓ Qual sorteio?" if rows else "❌ Sorteio não encontrado",
                    "Indica o link da mensagem ou responde ao anúncio.\n\n" + _USAGE,
                )
            )
        return None

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
            channel = await self._channel(channel_id)
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
        publisher = "os mods" if message.author.bot else f"<@{message.author.id}>"
        await ctx.send(
            embed=status_embed(
                "🎟️ Sorteio registado",
                f"[Anúncio]({message.jump_url}) · entra quem reagir com {emoji}.\n"
                f"Sorteio: <t:{when}:F> (<t:{when}:R>) · "
                f"{_plural(winners, 'vencedor', 'vencedores')}.\n"
                + (
                    "Já reagi com o emoji, por isso entrar é um clique."
                    if reacted
                    else "Não consegui reagir com o emoji (o bot não o tem?)."
                )
                + "\nNão entram bots, nem tu, nem quem escreveu o anúncio.\n"
                "Quem esteve no Campfire: `!sorteio campfire` com as capturas.\n"
                "O resultado vem para a moderação; nada é anunciado sem "
                f"{publisher} carregar em Publicar.",
            ),
            allowed_mentions=_NO_PINGS,
        )

    async def _list(self, ctx):
        rows = await self.poliswag.db.get_data_from_database(giveaways.RECENT_SQL)
        if not rows:
            await ctx.send(embed=status_embed("🎟️ Sem sorteios ativos", _USAGE))
            return
        lines = [
            f"[Anúncio]({giveaways.message_link(r)}) · {r['emoji']} · {_state(r)}"
            for r in rows
        ]
        await ctx.send(
            embed=status_embed("🎟️ Sorteios", "\n".join(lines) + "\n\n" + _USAGE),
            allowed_mentions=_NO_PINGS,
        )

    @sorteio.command(name="estado", brief="Quantos participantes tem o sorteio")
    async def estado(self, ctx, *, texto: str = ""):
        row = await self._target(
            ctx, texto, lambda r: r["status"] in ("open", "drawn"), latest=True
        )
        if row is None:
            return
        _, pool, ids, duplicates = await self.entries(row)
        lines = [
            f"[Anúncio]({giveaways.message_link(row)}) · {row['emoji']}",
            f"**{len(pool)}** {'participante' if len(pool) == 1 else 'participantes'}"
            f" neste momento: {len(ids)} pela reação, {len(pool) - len(ids)} do "
            "Campfire"
            + (
                f" ({len(duplicates)} do Campfire também reagiram)"
                if duplicates
                else ""
            )
            + ".",
            _state(row),
        ]
        await ctx.send(
            embed=status_embed("🎟️ Estado do sorteio", "\n".join(lines)),
            allowed_mentions=_NO_PINGS,
        )

    @sorteio.command(name="lista", brief="Todos os nomes que entram no sorteio")
    async def lista(self, ctx, *, texto: str = ""):
        row = await self._target(
            ctx, texto, lambda r: r["status"] in ("open", "drawn"), latest=True
        )
        if row is None:
            return
        _, pool, ids, names, duplicates = await self.listing(row)
        excluded = set(
            giveaways.winner_ids(row) + giveaways.entries(row.get("excluded_ids"))
        )
        lines = []
        for number, entry in enumerate(pool, 1):
            label = (
                f"{(names.get(entry) or [str(entry)])[0]} (Discord)"
                if isinstance(entry, int)
                else f"{entry} (Campfire)"
            )
            if entry in excluded:
                label += " · já ganhou ou foi excluído"
            lines.append(f"{number}. {label}")
        head = (
            f"[Anúncio]({giveaways.message_link(row)}) · **{len(pool)}** "
            f"{'participante' if len(pool) == 1 else 'participantes'}: {len(ids)} "
            f"pela reação {row['emoji']}, {len(pool) - len(ids)} só do Campfire"
            + (
                f" ({len(duplicates)} do Campfire também reagiram e contam uma vez)"
                if duplicates
                else ""
            )
            + "."
        )
        listing = "\n".join(lines) or "Ninguém, por agora."
        if len(head) + len(listing) + 2 <= 4000:
            await ctx.send(
                embed=status_embed("📋 Participantes", head + "\n\n" + listing),
                allowed_mentions=_NO_PINGS,
            )
        else:
            await ctx.send(
                embed=status_embed("📋 Participantes", head + "\n\nLista em anexo."),
                file=discord.File(io.BytesIO(listing.encode()), "participantes.txt"),
                allowed_mentions=_NO_PINGS,
            )

    @sorteio.command(name="sortear", brief="Sorteia já, sem esperar pela hora")
    async def sortear(self, ctx, *, texto: str = ""):
        row = await self._target(ctx, texto, lambda r: r["status"] == "open")
        if row is None:
            return
        drawn = await self.start_review(row)
        if drawn is None:
            await ctx.send(embed=status_embed("ℹ️ Este sorteio já foi feito"))
        else:
            await ctx.send(
                embed=status_embed(
                    "✅ Sorteio feito", "O resultado está na moderação, por publicar."
                )
            )

    @sorteio.command(
        name="reroll",
        aliases=["repetir"],
        brief="Tira um novo vencedor e manda-o para revisão",
    )
    async def reroll(self, ctx, *, texto: str = ""):
        """Depois de publicado: substitui um vencedor (o único, ou o indicado
        com @menção ou nome do Campfire) por outro nome, em nova revisão."""
        row = await self._target(
            ctx, texto, lambda r: r["status"] == "drawn", latest=True
        )
        if row is None:
            return
        replacing = None
        if not giveaways.in_review(row):
            replacing = _who_to_replace(ctx, texto, giveaways.winner_ids(row))
            if replacing is None:
                won = ", ".join(map(giveaways.show, giveaways.winner_ids(row)))
                await ctx.send(
                    embed=status_embed(
                        "❓ Quem substituir?",
                        f"Este sorteio tem vários vencedores ({won}). Indica qual: "
                        "`!sorteio reroll <link> @pessoa` (ou o nome do Campfire).",
                    ),
                    allowed_mentions=_NO_PINGS,
                )
                return
        drawn = await self.start_review(
            row, reroll=giveaways.in_review(row), replacing=replacing
        )
        if drawn is None:
            await ctx.send(embed=status_embed("ℹ️ O sorteio mudou entretanto"))
        elif not drawn:
            await ctx.send(
                embed=status_embed(
                    "ℹ️ Sem novo vencedor",
                    "Já não há participantes que não tenham ganho ou sido "
                    "excluídos. A revisão está na moderação.",
                )
            )
        else:
            await ctx.send(
                embed=status_embed(
                    "✅ Novo vencedor tirado",
                    "Está na moderação, por publicar. Ao publicar, a resposta "
                    "antiga no anúncio é apagada e fica só a nova.",
                )
            )

    @sorteio.command(name="cancelar", brief="Cancela um sorteio aberto ou em revisão")
    async def cancelar(self, ctx, *, texto: str = ""):
        row = await self._target(
            ctx,
            texto,
            lambda r: r["status"] == "open" or giveaways.in_review(r),
        )
        if row is None:
            return
        if row["status"] == "open":
            await self.poliswag.db.execute_query_to_database(
                giveaways.CANCEL_SQL, params=(int(row["message_id"]),)
            )
        else:
            await self.poliswag.db.execute_query_to_database(
                giveaways.STOP_SQL,
                params=(int(row["message_id"]), row.get("review_message_id")),
            )
            channel = await self._mod_channel()
            if channel is not None and row.get("review_message_id"):
                await self._retire(
                    channel,
                    row["review_message_id"],
                    f"🚫 Cancelado por {ctx.author.display_name}.",
                )
        self._log(f"{ctx.author} cancelled {giveaways.message_link(row)}")
        await ctx.send(
            embed=status_embed(
                "🚫 Sorteio cancelado",
                (
                    "Volta ao vencedor já publicado."
                    if giveaways.winner_ids(row)
                    else "Ninguém vai ser sorteado."
                ),
            )
        )

    @sorteio.command(name="campfire", brief="Junta os nomes do Campfire ao sorteio")
    async def campfire_names(self, ctx, *, texto: str = ""):
        db = self.poliswag.db
        row = await self._target(
            ctx,
            texto,
            lambda r: r["status"] == "open" or giveaways.in_review(r),
            quiet=True,
        )
        if texto.strip().lower().startswith("limpar"):
            if row is None:
                await ctx.send(embed=status_embed("❌ Sorteio não encontrado", _USAGE))
                return
            await db.execute_query_to_database(
                giveaways.CAMPFIRE_SQL, params=(None, int(row["message_id"]))
            )
            await ctx.send(embed=status_embed("🧹 Nomes do Campfire retirados"))
            return
        images, problems = await self._images(ctx, texto, row)
        if not images:
            await ctx.send(
                embed=status_embed(
                    "❌ Sem capturas",
                    "\n".join(problems)
                    or "Anexa as capturas do Campfire ao comando, responde à "
                    "mensagem que as tem, ou junta o link dela.",
                )
            )
            return
        try:
            async with ctx.typing():
                read = await campfire.read_names(images)
        except campfire.CampfireError as error:
            self._log(f"campfire read failed: {error}", "ERROR")
            await ctx.send(
                embed=status_embed("❌ Não consegui ler as capturas", str(error))
            )
            return
        new = giveaways.merge_names(read)
        lines = [
            f"Li **{len(new)}** {'conta' if len(new) == 1 else 'contas'} em "
            f"{_plural(len(images), 'captura', 'capturas')}"
            + (f" ({len(read) - len(new)} repetidas)" if len(read) > len(new) else "")
            + "."
        ]
        lines += problems
        names = new
        if row is not None:
            before = giveaways.entries(row.get("campfire_names"))
            names = giveaways.merge_names(before, new)
            await db.execute_query_to_database(
                giveaways.CAMPFIRE_SQL,
                params=(giveaways.dump(names), int(row["message_id"])),
            )
            row = {**row, "campfire_names": giveaways.dump(names)}
            _, pool, ids, duplicates = await self.entries(row)
            lines.append(
                f"[Sorteio]({giveaways.message_link(row)}): "
                f"{len(names)} do Campfire"
                + (f" ({len(names) - len(before)} novos)" if before else "")
                + f", {len(ids)} pela reação"
                + (f", {len(duplicates)} nos dois" if duplicates else "")
                + f" → **{_plural(len(pool), 'participante', 'participantes')}**."
            )
            if giveaways.in_review(row):
                lines.append(
                    "Já está em revisão: usa Repetir (ou `!sorteio reroll`) para "
                    "sortear com estes nomes."
                )
            self._log(
                f"{ctx.author} added {len(new)} campfire names to "
                f"{giveaways.message_link(row)} ({len(names)} in all)"
            )
        else:
            lines.append(
                "Não encontrei um sorteio ativo para os juntar (indica o link do "
                "anúncio): esta lista não ficou guardada."
            )
        listing = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
        body = "\n".join(lines)
        if len(body) + len(listing) + 2 <= 4000:
            await ctx.send(
                embed=status_embed("🔥 Campfire", body + "\n\n" + listing),
                allowed_mentions=_NO_PINGS,
            )
        else:
            await ctx.send(
                embed=status_embed(
                    "🔥 Campfire", body + "\n\nLista completa em anexo."
                ),
                file=discord.File(io.BytesIO(listing.encode()), "campfire.txt"),
                allowed_mentions=_NO_PINGS,
            )

    async def _images(self, ctx, texto, row):
        """As capturas: anexos do comando, da mensagem a que responde e das
        mensagens nos links (menos a do próprio sorteio)."""
        sources = [ctx.message]
        reference = getattr(ctx.message, "reference", None)
        skip = {int(row["message_id"])} if row else set()
        wanted = []
        if reference and reference.message_id and reference.message_id not in skip:
            wanted.append((reference.channel_id, reference.message_id))
        wanted += [(c, m) for c, m in giveaways.parse_links(texto) if m not in skip]
        problems = []
        for channel_id, message_id in wanted:
            try:
                channel = await self._channel(channel_id)
                sources.append(await channel.fetch_message(message_id))
            except discord.HTTPException:
                problems.append(f"Não consegui abrir a mensagem {message_id}.")
        images = []
        for source in sources:
            for attachment in getattr(source, "attachments", []) or []:
                media_type = _media_type(attachment)
                if media_type is None:
                    continue
                if attachment.size > campfire.MAX_IMAGE_BYTES:
                    problems.append(f"{attachment.filename} passa dos 5 MB.")
                    continue
                if len(images) >= campfire.MAX_IMAGES:
                    problems.append(
                        f"Só leio {campfire.MAX_IMAGES} capturas de cada vez."
                    )
                    break
                images.append((await attachment.read(), media_type))
        return images, problems


def _names_of(user):
    """Os nomes por que alguém aparece no Discord (alcunha, nome global e de
    utilizador), sem repetições."""
    names = [
        getattr(user, "nick", None),
        getattr(user, "global_name", None),
        getattr(user, "display_name", None),
        getattr(user, "name", None),
    ]
    return list(dict.fromkeys(n for n in names if isinstance(n, str) and n))


def _who_to_replace(ctx, texto, winners):
    """Os vencedores publicados que um reroll substitui: o único que há, ou os
    indicados por @menção ou nome do Campfire. None quando não se sabe."""
    if not winners:
        return []
    mentioned = [u.id for u in getattr(ctx.message, "mentions", []) or []]
    words = giveaways.name_key(giveaways._LINK.sub("", texto or ""))
    chosen = [
        w
        for w in winners
        if (isinstance(w, int) and w in mentioned)
        or (isinstance(w, str) and giveaways.name_key(w) in words)
    ]
    if chosen:
        return chosen
    return list(winners) if len(winners) == 1 else None


def _media_type(attachment):
    media_type = (attachment.content_type or "").split(";")[0].strip().lower()
    if media_type in campfire.MEDIA_TYPES:
        return media_type
    guessed = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(Path(attachment.filename or "").suffix.lower())
    return guessed


def _describe(entry):
    if isinstance(entry, int):
        return f"<@{entry}> (`{entry}`)"
    return giveaways.show(entry) + " · não está no Discord, contacta-o no jogo"


def _state(row):
    if row["status"] == "open":
        when = giveaways.unix(row["draw_at"])
        return f"sorteio <t:{when}:R>"
    if giveaways.in_review(row):
        pending = giveaways.entries(row.get("pending_ids"))
        return "em revisão na moderação: " + (
            ", ".join(map(giveaways.show, pending)) or "ninguém"
        )
    won = giveaways.winner_ids(row)
    return "publicado: " + (", ".join(map(giveaways.show, won)) or "ninguém")


async def setup(poliswag):
    await poliswag.add_cog(Giveaways(poliswag))
