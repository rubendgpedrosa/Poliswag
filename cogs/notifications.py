import json

import discord
from discord.ext import commands

from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.poracle_client import PoracleError


class Notifications(commands.Cog):
    """Admin-only commands for managing per-channel Poracle pokemon alerts."""

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.poracle_db = DatabaseConnector("poracle")

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    # ---- helpers --------------------------------------------------------

    def _pokemon_name(self, pokemon_id: int) -> str:
        name_map = self.poliswag.quest_search.pokemon_name_map or {}
        if not name_map:
            try:
                with open(Config.POKEMON_NAME_FILE, "r") as f:
                    name_map = json.load(f)
                self.poliswag.quest_search.pokemon_name_map = name_map
            except Exception:
                return f"#{pokemon_id}"
        return name_map.get(str(pokemon_id), f"#{pokemon_id}").title()

    def _resolve_pokemon(self, name: str) -> int | None:
        matches = self.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map(name)
        exact = [
            mid
            for mid in matches
            if self.poliswag.quest_search.pokemon_name_map.get(mid, "") == name.lower()
        ]
        if exact:
            return int(exact[0])
        if len(matches) == 1:
            return int(matches[0])
        return None

    def _resolve_targets(self, ref: str) -> list[dict]:
        """Map a user-supplied reference to one or more registered channels.

        Accepts:
          - A `<#id>` mention or raw numeric id → exact match on id
          - An exact channel name (e.g. ``alertas-level5``)
          - A category suffix (``raros``, ``100iv``, ``0iv``, ``uteis``) → both
            ``leiria-<suffix>`` and ``marinha-<suffix>`` when they exist
        """
        ref = ref.strip()
        if ref.startswith("<#") and ref.endswith(">"):
            ref = ref[2:-1]
        if ref.isdigit():
            rows = self.poracle_db.get_data_from_database(
                "SELECT id, name, enabled FROM humans "
                "WHERE type = 'discord:channel' AND id = %s",
                params=(ref,),
            )
            return rows or []

        exact = self.poracle_db.get_data_from_database(
            "SELECT id, name, enabled FROM humans "
            "WHERE type = 'discord:channel' AND name = %s",
            params=(ref,),
        )
        if exact:
            return exact
        return (
            self.poracle_db.get_data_from_database(
                "SELECT id, name, enabled FROM humans "
                "WHERE type = 'discord:channel' AND name LIKE %s "
                "ORDER BY name",
                params=(f"%-{ref}",),
            )
            or []
        )

    async def _send_no_match(self, ctx, ref: str):
        await ctx.send(
            f"Não encontrei nenhum canal registado para '{ref}'. "
            "Usa `!notify channels` para ver os disponíveis."
        )

    def _render_rule(self, rule: dict) -> str:
        pokemon_id = rule.get("pokemon_id", 0)
        name = "Qualquer" if pokemon_id == 0 else self._pokemon_name(pokemon_id)
        min_iv, max_iv = rule.get("min_iv", 0), rule.get("max_iv", 100)
        iv = f"IV={min_iv}" if min_iv == max_iv else f"IV {min_iv}-{max_iv}"
        parts = [f"`{rule.get('uid', '?')}`", name, f"— {iv}"]
        min_cp, max_cp = rule.get("min_cp", 0), rule.get("max_cp", 9000)
        if min_cp > 0 or max_cp < 9000:
            parts.append(f"CP {min_cp}-{max_cp}")
        return " ".join(parts)

    # ---- command group --------------------------------------------------

    @commands.group(name="notify", invoke_without_command=True)
    async def notify(self, ctx):
        await ctx.send(
            "Subcomandos: `channels`, `list`, `add`, `remove`, `register`, "
            "`enable`, `disable`, `test`, `reload`"
        )

    @notify.command(name="channels", brief="Lista os canais registados em Poracle")
    async def channels_cmd(self, ctx):
        try:
            rows = self.poracle_db.get_data_from_database(
                "SELECT id, name, enabled FROM humans "
                "WHERE type = 'discord:channel' ORDER BY name"
            )
        except Exception as e:
            await ctx.send(f"Erro ao obter canais: {e}")
            return

        if not rows:
            await ctx.send("Sem canais registados em Poracle.")
            return

        lines = [
            f"{'🟢' if row['enabled'] else '🔴'} <#{row['id']}> — `{row['id']}`"
            for row in rows
        ]
        embed = discord.Embed(
            title="Canais Poracle",
            description="\n".join(lines)[:4000],
            color=Config.EMBED_COLOR,
        )
        await ctx.send(embed=embed)

    @notify.command(name="list", brief="Lista pokémon seguidos num canal ou categoria")
    async def list_cmd(self, ctx, ref: str):
        targets = self._resolve_targets(ref)
        if not targets:
            await self._send_no_match(ctx, ref)
            return

        sections = []
        for target in targets:
            try:
                rules = await self.poliswag.poracle.list_pokemon_tracking(target["id"])
            except PoracleError as e:
                sections.append(f"**#{target['name']}** — erro: {e}")
                continue
            header = f"**#{target['name']}** (`{target['id']}`)"
            if not rules:
                sections.append(f"{header}\n_nenhum pokémon seguido_")
            else:
                body = "\n".join(self._render_rule(r) for r in rules)
                sections.append(f"{header}\n{body}")

        embed = discord.Embed(
            title=f"Pokémon seguidos — {ref}",
            description="\n\n".join(sections)[:4000],
            color=Config.EMBED_COLOR,
        )
        embed.set_footer(text="Usa !notify remove <uid> para remover uma regra")
        await ctx.send(embed=embed)

    @notify.command(name="add", brief="Adiciona um pokémon a um canal ou categoria")
    async def add_cmd(
        self,
        ctx,
        ref: str,
        name: str,
        min_iv: int = 0,
        min_cp: int = 0,
    ):
        targets = self._resolve_targets(ref)
        if not targets:
            await self._send_no_match(ctx, ref)
            return

        pokemon_id = self._resolve_pokemon(name)
        if pokemon_id is None:
            await ctx.send(
                f"Não encontrei um pokémon único para '{name}'. Tenta um nome exato."
            )
            return

        rule = {"pokemon_id": pokemon_id, "min_iv": min_iv, "min_cp": min_cp}
        added = []
        failures = []
        for target in targets:
            try:
                await self.poliswag.poracle.add_pokemon_tracking(target["id"], rule)
                added.append(target["name"])
            except PoracleError as e:
                failures.append(f"#{target['name']}: {e}")

        if added:
            try:
                await self.poliswag.poracle.reload()
            except PoracleError:
                pass

        pretty = self._pokemon_name(pokemon_id)
        suffix = f" (IV≥{min_iv}, CP≥{min_cp})" if (min_iv or min_cp) else ""
        lines = []
        if added:
            mentions = ", ".join(f"#{n}" for n in added)
            lines.append(f"✔ {pretty}{suffix} adicionado a {mentions}.")
        for f in failures:
            lines.append(f"✖ {f}")
        await ctx.send("\n".join(lines) if lines else "Sem alterações.")

    @notify.command(name="remove", brief="Remove uma regra pelo uid")
    async def remove_cmd(self, ctx, uid: str):
        if not uid.isdigit():
            await ctx.send("O uid deve ser numérico.")
            return
        rows = self.poracle_db.get_data_from_database(
            "SELECT id FROM monsters WHERE uid = %s", params=(uid,)
        )
        if not rows:
            await ctx.send(f"Não encontrei nenhuma regra com uid `{uid}`.")
            return
        human_id = rows[0]["id"]
        try:
            await self.poliswag.poracle.delete_pokemon_tracking_uid(human_id, uid)
            await self.poliswag.poracle.reload()
        except PoracleError as e:
            await ctx.send(f"Erro ao remover regra: {e}")
            return
        await ctx.send(f"✔ Regra `{uid}` removida de <#{human_id}>.")

    @notify.command(name="register", brief="Regista um canal em Poracle")
    async def register_cmd(self, ctx, channel: discord.TextChannel):
        existing = await self.poliswag.poracle.get_human(channel.id)
        if existing:
            await ctx.send(f"{channel.mention} já está registado em Poracle.")
            return
        try:
            await self.poliswag.poracle.create_channel(channel.id, channel.name)
            await self.poliswag.poracle.start(channel.id)
        except PoracleError as e:
            await ctx.send(f"Erro ao registar canal: {e}")
            return
        await ctx.send(f"✔ {channel.mention} registado e activo.")

    @notify.command(name="enable", brief="Activa notificações num canal ou categoria")
    async def enable_cmd(self, ctx, ref: str):
        await self._toggle(ctx, ref, enable=True)

    @notify.command(
        name="disable", brief="Desactiva notificações num canal ou categoria"
    )
    async def disable_cmd(self, ctx, ref: str):
        await self._toggle(ctx, ref, enable=False)

    async def _toggle(self, ctx, ref: str, *, enable: bool):
        targets = self._resolve_targets(ref)
        if not targets:
            await self._send_no_match(ctx, ref)
            return
        action = self.poliswag.poracle.start if enable else self.poliswag.poracle.stop
        changed, failures = [], []
        for target in targets:
            try:
                await action(target["id"])
                changed.append(target["name"])
            except PoracleError as e:
                failures.append(f"#{target['name']}: {e}")
        if changed:
            try:
                await self.poliswag.poracle.reload()
            except PoracleError:
                pass
        verb = "activadas" if enable else "desactivadas"
        lines = []
        if changed:
            mentions = ", ".join(f"#{n}" for n in changed)
            lines.append(f"✔ Notificações {verb} em {mentions}.")
        for f in failures:
            lines.append(f"✖ {f}")
        await ctx.send("\n".join(lines) if lines else "Sem alterações.")

    @notify.command(name="reload", brief="Recarrega as regras em Poracle")
    async def reload_cmd(self, ctx):
        try:
            await self.poliswag.poracle.reload()
        except PoracleError as e:
            await ctx.send(f"Erro ao recarregar: {e}")
            return
        await ctx.send("✔ Regras recarregadas.")

    @notify.command(name="test", brief="Envia uma notificação de teste para o canal")
    async def test_cmd(self, ctx, ref: str):
        targets = self._resolve_targets(ref)
        if not targets:
            await self._send_no_match(ctx, ref)
            return
        webhook = {
            "pokemon_id": 25,
            "latitude": 39.744,
            "longitude": -8.807,
            "individual_attack": 15,
            "individual_defense": 15,
            "individual_stamina": 15,
            "cp": 999,
            "pokemon_level": 30,
        }
        sent, failures = [], []
        for target in targets:
            payload = {
                "id": str(target["id"]),
                "name": target["name"],
                "type": "discord:channel",
                "language": "en",
                "template": "1",
            }
            try:
                await self.poliswag.poracle.test_pokemon(webhook, payload)
                sent.append(target["name"])
            except PoracleError as e:
                failures.append(f"#{target['name']}: {e}")
        lines = []
        if sent:
            mentions = ", ".join(f"#{n}" for n in sent)
            lines.append(f"✔ Teste enviado para {mentions}.")
        for f in failures:
            lines.append(f"✖ {f}")
        await ctx.send("\n".join(lines) if lines else "Sem alterações.")


async def setup(bot):
    await bot.add_cog(Notifications(bot))
