import json

import discord
from discord.ext import commands

from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.poracle_client import PoracleError

_PAIRED_PREFIXES = ("leiria-", "marinha-")


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

    async def _reply(
        self, ctx, description: str, *, title: str | None = None, error: bool = False
    ):
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.red() if error else Config.EMBED_COLOR,
        )
        await ctx.send(embed=embed)

    async def _send_no_match(self, ctx, ref: str):
        await self._reply(
            ctx,
            f"Não encontrei nenhum canal registado para '{ref}'. "
            "Usa `!notify channels` para ver os disponíveis.",
            error=True,
        )

    def _render_rule_summary(self, rule: dict) -> str:
        """Name + IV/CP filters, without the UID (channel-agnostic)."""
        pokemon_id = rule.get("pokemon_id", 0)
        name = "Qualquer" if pokemon_id == 0 else self._pokemon_name(pokemon_id)
        min_iv, max_iv = rule.get("min_iv", 0), rule.get("max_iv", 100)
        iv = f"IV={min_iv}" if min_iv == max_iv else f"IV {min_iv}-{max_iv}"
        parts = [name, f"— {iv}"]
        min_cp, max_cp = rule.get("min_cp", 0), rule.get("max_cp", 9000)
        if min_cp > 0 or max_cp < 9000:
            parts.append(f"CP {min_cp}-{max_cp}")
        return " ".join(parts)

    def _render_rule(self, rule: dict) -> str:
        """Summary with a leading UID tag."""
        return f"`{rule.get('uid', '?')}` {self._render_rule_summary(rule)}"

    # ---- command group --------------------------------------------------

    @commands.group(
        name="notify",
        invoke_without_command=True,
        brief="Gere alertas Poracle por canal",
        help=(
            "Grupo de comandos para gerir alertas de pokémon do Poracle, "
            "canal a canal. Usa `!help notify <subcomando>` para detalhes."
        ),
    )
    async def notify(self, ctx):
        embed = discord.Embed(
            title="!notify — alertas Poracle",
            description=(
                "Gere os alertas de pokémon do Poracle por canal.\n\n"
                "**`<ref>` aceita:**\n"
                "• `#leiria-100iv` — mention\n"
                "• `868051002782277652` — id numérico\n"
                "• `alertas-level5` — nome exacto do canal\n"
                "• `raros` / `100iv` / `0iv` / `uteis` — categoria (aplica aos dois canais leiria + marinha)\n"
            ),
            color=Config.EMBED_COLOR,
        )
        embed.add_field(
            name="Ver",
            value=(
                "`!notify channels` — canais registados\n"
                "`!notify list [ref]` — regras seguidas (todos os canais se sem ref)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Editar",
            value=(
                "`!notify add <ref> <nome[,nome…]> [min_iv] [min_cp]`\n"
                "`!notify remove <ref> <nome[,nome…]|uid>`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Estado",
            value=(
                "`!notify enable <ref>` — activa notificações\n"
                "`!notify disable <ref>` — desactiva notificações\n"
                "`!notify register <#canal>` — regista canal novo\n"
                "`!notify test <ref|dm> <pokemon>` — envia notificação de teste"
            ),
            inline=False,
        )
        embed.add_field(
            name="Exemplos",
            value=(
                "`!notify add raros Tyranitar,Mewtwo`\n"
                "`!notify add 100iv Charizard 95 2000`\n"
                "`!notify remove raros 172`\n"
                "`!notify disable alertas-level5`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @notify.command(
        name="channels",
        brief="Lista os canais registados em Poracle",
        help=(
            "Mostra todos os canais Discord registados como alvos de alertas "
            "Poracle, com 🟢 activo / 🔴 desactivado."
        ),
    )
    async def channels_cmd(self, ctx):
        try:
            rows = self.poracle_db.get_data_from_database(
                "SELECT id, name, enabled FROM humans "
                "WHERE type = 'discord:channel' ORDER BY name"
            )
        except Exception as e:
            await self._reply(ctx, f"Erro ao obter canais: {e}", error=True)
            return

        if not rows:
            await self._reply(ctx, "Sem canais registados em Poracle.")
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

    @notify.command(
        name="list",
        brief="Lista pokémon seguidos (opcionalmente filtrando por canal/categoria)",
        help=(
            "Sem argumentos: lista todas as regras em todos os canais registados.\n"
            "Com `<ref>`: filtra por canal ou categoria (ex: `raros`, `100iv`).\n"
            "Exemplos:\n"
            "  `!notify list`\n"
            "  `!notify list 100iv`"
        ),
    )
    async def list_cmd(self, ctx, ref: str | None = None):
        if ref is None:
            targets = (
                self.poracle_db.get_data_from_database(
                    "SELECT id, name, enabled FROM humans "
                    "WHERE type = 'discord:channel' ORDER BY name"
                )
                or []
            )
            title_ref = "todos os canais"
        else:
            targets = self._resolve_targets(ref)
            if not targets:
                await self._send_no_match(ctx, ref)
                return
            title_ref = ref

        body = await self._list_per_channel(targets)

        if not body:
            await self._reply(
                ctx,
                f"Nenhum pokémon seguido em {title_ref}.",
                title=f"Pokémon seguidos — {title_ref}",
            )
            return

        embed = discord.Embed(
            title=f"Pokémon seguidos — {title_ref}",
            description=body[:4000],
            color=Config.EMBED_COLOR,
        )
        embed.set_footer(
            text="Usa !notify remove <ref> <nome|uid> para remover uma regra"
        )
        await ctx.send(embed=embed)

    def _group_targets_by_suffix(
        self, targets: list[dict]
    ) -> list[tuple[str, list[dict]]]:
        """Group leiria/marinha channel pairs by their shared suffix.

        Channels whose names start with a paired prefix (leiria- or marinha-)
        are merged under the bare suffix key. Unprefixed channels are returned
        individually. Result is sorted by label so the output is stable.
        """
        by_suffix: dict[str, list[dict]] = {}
        singletons: list[dict] = []
        for target in targets:
            name = target["name"]
            matched = False
            for prefix in _PAIRED_PREFIXES:
                if name.startswith(prefix):
                    suffix = name[len(prefix) :]
                    by_suffix.setdefault(suffix, []).append(target)
                    matched = True
                    break
            if not matched:
                singletons.append(target)
        result: list[tuple[str, list[dict]]] = sorted(by_suffix.items())
        for t in singletons:
            result.append((t["name"], [t]))
        return result

    async def _list_per_channel(self, targets: list[dict]) -> str:
        groups = self._group_targets_by_suffix(targets)
        sections = []
        for label, group_targets in groups:
            if len(group_targets) == 1:
                t = group_targets[0]
                header = f"**#{t['name']}** (`{t['id']}`)"
                try:
                    rules = await self.poliswag.poracle.list_pokemon_tracking(t["id"])
                except PoracleError as e:
                    sections.append(f"{header}\n✖ {e}")
                    continue
                if not rules:
                    sections.append(f"{header}\n_nenhum pokémon seguido_")
                    continue
                sorted_rules = sorted(
                    rules,
                    key=lambda r: (r.get("pokemon_id", 0), r.get("min_iv", 0)),
                )
                sections.append(
                    f"{header}\n"
                    + "\n".join(self._render_rule(r) for r in sorted_rules)
                )
            else:
                # Merged group: e.g. leiria-raros + marinha-raros → "raros"
                mentions = " · ".join(f"<#{t['id']}>" for t in group_targets)
                header = f"**{label}** ({mentions})"
                seen_keys: set[tuple] = set()
                merged_rules: list[dict] = []
                errors: list[str] = []
                for t in group_targets:
                    try:
                        rules = await self.poliswag.poracle.list_pokemon_tracking(
                            t["id"]
                        )
                    except PoracleError as e:
                        errors.append(f"#{t['name']}: {e}")
                        continue
                    for rule in rules or []:
                        key = (
                            rule.get("pokemon_id", 0),
                            rule.get("min_iv", 0),
                            rule.get("max_iv", 100),
                            rule.get("min_cp", 0),
                            rule.get("max_cp", 9000),
                        )
                        if key not in seen_keys:
                            seen_keys.add(key)
                            merged_rules.append(rule)
                if not merged_rules and not errors:
                    sections.append(f"{header}\n_nenhum pokémon seguido_")
                    continue
                sorted_rules = sorted(
                    merged_rules,
                    key=lambda r: (r.get("pokemon_id", 0), r.get("min_iv", 0)),
                )
                lines = [self._render_rule_summary(r) for r in sorted_rules]
                for err in errors:
                    lines.append(f"✖ {err}")
                sections.append(f"{header}\n" + "\n".join(lines))
        return "\n\n".join(sections)

    @notify.command(
        name="add",
        brief="Adiciona um ou mais pokémon (separados por vírgula)",
        help=(
            "Adiciona regras de alerta a um canal ou categoria.\n"
            "Aceita vários nomes separados por vírgula; os filtros IV/CP aplicam-se a todos.\n"
            "Exemplos:\n"
            "  `!notify add raros Tyranitar`\n"
            "  `!notify add 100iv Charizard, Mewtwo 95 2000`"
        ),
    )
    async def add_cmd(
        self,
        ctx,
        ref: str,
        names: str,
        min_iv: int = 0,
        min_cp: int = 0,
    ):
        targets = self._resolve_targets(ref)
        if not targets:
            await self._send_no_match(ctx, ref)
            return

        parsed = [n.strip() for n in names.split(",") if n.strip()]
        if not parsed:
            await self._reply(ctx, "Não recebi nenhum nome de pokémon.", error=True)
            return

        resolved = []
        unresolved = []
        for n in parsed:
            pid = self._resolve_pokemon(n)
            if pid is None:
                unresolved.append(n)
            else:
                resolved.append((n, pid))

        if not resolved:
            await self._reply(
                ctx,
                f"Nenhum dos nomes ({', '.join(unresolved)}) resolveu para um pokémon único.",
                error=True,
            )
            return

        suffix = f" (IV≥{min_iv}, CP≥{min_cp})" if (min_iv or min_cp) else ""
        success_lines = []
        skipped = []
        failures = []
        any_added = False
        for _input_name, pokemon_id in resolved:
            pretty = self._pokemon_name(pokemon_id)
            rule = {"pokemon_id": pokemon_id, "min_iv": min_iv, "min_cp": min_cp}
            added_channels = []
            skipped_channels = []
            for target in targets:
                if self._rule_exists(target["id"], pokemon_id, min_iv, min_cp):
                    skipped_channels.append(target["name"])
                    continue
                try:
                    await self.poliswag.poracle.add_pokemon_tracking(target["id"], rule)
                    added_channels.append(target["name"])
                    any_added = True
                except PoracleError as e:
                    failures.append(f"#{target['name']} ({_input_name}): {e}")
            if added_channels:
                mentions = ", ".join(f"#{n}" for n in added_channels)
                success_lines.append(f"✔ {pretty}{suffix} → {mentions}")
            if skipped_channels:
                mentions = ", ".join(f"#{n}" for n in skipped_channels)
                skipped.append(f"= {pretty}{suffix} já existe em {mentions}")

        if any_added:
            try:
                await self.poliswag.poracle.reload()
            except PoracleError:
                pass
            self.poliswag.utility.log_to_file(
                f"[NOTIFY] @{ctx.author} ({ctx.author.id}): added rules — "
                + "; ".join(success_lines)
            )

        lines = success_lines + skipped
        for n in unresolved:
            lines.append(f"✖ Não encontrei um pokémon único para '{n}'.")
        for f in failures:
            lines.append(f"✖ {f}")
        await self._reply(
            ctx,
            "\n".join(lines) if lines else "Sem alterações.",
            title="Adicionar regras",
            error=not (any_added or skipped),
        )

    def _rule_exists(
        self, human_id: str | int, pokemon_id: int, min_iv: int, min_cp: int
    ) -> bool:
        rows = self.poracle_db.get_data_from_database(
            "SELECT uid FROM monsters "
            "WHERE id = %s AND pokemon_id = %s AND min_iv = %s AND min_cp = %s",
            params=(str(human_id), pokemon_id, min_iv, min_cp),
        )
        return bool(rows)

    @notify.command(
        name="remove",
        brief="Remove regras por nome ou uid",
        help=(
            "Remove regras de alerta.\n"
            "Por nome: faz fan-out pela categoria (múltiplos nomes separados por vírgula).\n"
            "Por uid: apaga essa regra específica (vê `!notify list` para os uids).\n"
            "Exemplos:\n"
            "  `!notify remove raros Tyranitar, Mewtwo`\n"
            "  `!notify remove raros 172`"
        ),
    )
    async def remove_cmd(self, ctx, ref: str, target: str):
        targets = self._resolve_targets(ref)
        if not targets:
            await self._send_no_match(ctx, ref)
            return

        # UID path: delete exactly that rule regardless of ref.
        if target.isdigit():
            await self._remove_by_uid(ctx, target)
            return

        # Name path: fan-out across resolved channels.
        await self._remove_by_pokemon_name(ctx, targets, target)

    async def _remove_by_uid(self, ctx, uid: str):
        rows = self.poracle_db.get_data_from_database(
            "SELECT id FROM monsters WHERE uid = %s", params=(uid,)
        )
        if not rows:
            await self._reply(
                ctx, f"Não encontrei nenhuma regra com uid `{uid}`.", error=True
            )
            return
        human_id = rows[0]["id"]
        try:
            await self.poliswag.poracle.delete_pokemon_tracking_uid(human_id, uid)
            await self.poliswag.poracle.reload()
        except PoracleError as e:
            await self._reply(ctx, f"Erro ao remover regra: {e}", error=True)
            return
        await self._reply(
            ctx,
            f"✔ Regra `{uid}` removida de <#{human_id}>.",
            title="Remover regra",
        )
        self.poliswag.utility.log_to_file(
            f"[NOTIFY] @{ctx.author} ({ctx.author.id}): removed rule uid={uid} from channel {human_id}"
        )

    async def _remove_by_pokemon_name(self, ctx, targets: list[dict], names: str):
        parsed = [n.strip() for n in names.split(",") if n.strip()]
        if not parsed:
            await self._reply(ctx, "Não recebi nenhum nome de pokémon.", error=True)
            return

        resolved = []
        unresolved = []
        for n in parsed:
            pid = self._resolve_pokemon(n)
            if pid is None:
                unresolved.append(n)
            else:
                resolved.append((n, pid))

        if not resolved:
            await self._reply(
                ctx,
                f"Nenhum dos nomes ({', '.join(unresolved)}) resolveu para um pokémon único.",
                error=True,
            )
            return

        success_lines = []
        failures = []
        any_removed = False
        for _input_name, pokemon_id in resolved:
            pretty = self._pokemon_name(pokemon_id)
            removed = []
            for target in targets:
                rows = self.poracle_db.get_data_from_database(
                    "SELECT uid FROM monsters WHERE id = %s AND pokemon_id = %s",
                    params=(target["id"], pokemon_id),
                )
                for row in rows or []:
                    try:
                        await self.poliswag.poracle.delete_pokemon_tracking_uid(
                            target["id"], row["uid"]
                        )
                        removed.append((target["name"], row["uid"]))
                        any_removed = True
                    except PoracleError as e:
                        failures.append(f"#{target['name']} uid={row['uid']}: {e}")

            if removed:
                by_channel = {}
                for channel_name, uid in removed:
                    by_channel.setdefault(channel_name, []).append(str(uid))
                for channel_name, uids in by_channel.items():
                    success_lines.append(
                        f"✔ {pretty} removido de #{channel_name} (uids: {', '.join(uids)})"
                    )
            else:
                success_lines.append(
                    f"Nenhuma regra de {pretty} encontrada nos canais alvo."
                )

        if any_removed:
            try:
                await self.poliswag.poracle.reload()
            except PoracleError:
                pass
            self.poliswag.utility.log_to_file(
                f"[NOTIFY] @{ctx.author} ({ctx.author.id}): removed rules — "
                + "; ".join(line for line in success_lines if line.startswith("✔"))
            )

        lines = success_lines
        for n in unresolved:
            lines.append(f"✖ Não encontrei um pokémon único para '{n}'.")
        for f in failures:
            lines.append(f"✖ {f}")
        await self._reply(
            ctx,
            "\n".join(lines),
            title="Remover regras",
            error=not any_removed,
        )

    @notify.command(
        name="register",
        brief="Regista um canal novo em Poracle",
        help=(
            "Regista um canal Discord como alvo de alertas Poracle e activa-o. "
            "Depois podes usar `!notify add` para adicionar regras."
        ),
    )
    async def register_cmd(self, ctx, channel: discord.TextChannel):
        existing = await self.poliswag.poracle.get_human(channel.id)
        if existing:
            await self._reply(ctx, f"{channel.mention} já está registado em Poracle.")
            return
        try:
            await self.poliswag.poracle.create_channel(channel.id, channel.name)
            await self.poliswag.poracle.start(channel.id)
        except PoracleError as e:
            await self._reply(ctx, f"Erro ao registar canal: {e}", error=True)
            return
        await self._reply(
            ctx,
            f"✔ {channel.mention} registado e activo.",
            title="Registo de canal",
        )
        self.poliswag.utility.log_to_file(
            f"[NOTIFY] @{ctx.author} ({ctx.author.id}): registered channel #{channel.name} ({channel.id}) in Poracle"
        )

    @notify.command(
        name="enable",
        brief="Activa notificações num canal ou categoria",
        help=(
            "Volta a activar a entrega de alertas (as regras continuam "
            "guardadas mesmo quando o canal está desactivado)."
        ),
    )
    async def enable_cmd(self, ctx, ref: str):
        await self._toggle(ctx, ref, enable=True)

    @notify.command(
        name="disable",
        brief="Desactiva notificações num canal ou categoria",
        help=(
            "Silencia temporariamente um canal sem apagar regras. "
            "Usa `!notify enable` para reactivar."
        ),
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
            verb_log = "enabled" if enable else "disabled"
            self.poliswag.utility.log_to_file(
                f"[NOTIFY] @{ctx.author} ({ctx.author.id}): notifications {verb_log} "
                f"for {', '.join(f'#{n}' for n in changed)}"
            )
        verb = "activadas" if enable else "desactivadas"
        lines = []
        if changed:
            mentions = ", ".join(f"#{n}" for n in changed)
            lines.append(f"✔ Notificações {verb} em {mentions}.")
        for f in failures:
            lines.append(f"✖ {f}")
        await self._reply(
            ctx,
            "\n".join(lines) if lines else "Sem alterações.",
            title="Activar notificações" if enable else "Desactivar notificações",
            error=not changed,
        )

    @notify.command(
        name="test",
        brief="Envia uma notificação de teste",
        help=(
            "Simula um spawn 100% IV do pokémon indicado e envia para o alvo.\n"
            "Alvo pode ser um canal (mention/nome/categoria) ou `dm` para a tua DM.\n"
            "Exemplos:\n"
            "  `!notify test leiria-raros tyranitar`\n"
            "  `!notify test dm mewtwo`"
        ),
    )
    async def test_cmd(self, ctx, target: str, pokemon: str):
        pokemon_id = self._resolve_pokemon(pokemon)
        if pokemon_id is None:
            await self._reply(
                ctx,
                f"Não encontrei um pokémon único para '{pokemon}'. Tenta um nome exato.",
                error=True,
            )
            return

        if target.lower() == "dm":
            recipients = [
                {
                    "id": str(ctx.author.id),
                    "name": ctx.author.name,
                    "type": "discord:user",
                }
            ]
        else:
            resolved = self._resolve_targets(target)
            if not resolved:
                await self._send_no_match(ctx, target)
                return
            recipients = [
                {"id": str(r["id"]), "name": r["name"], "type": "discord:channel"}
                for r in resolved
            ]

        pretty = self._pokemon_name(pokemon_id)
        webhook = {
            "pokemon_id": pokemon_id,
            "latitude": 39.744,
            "longitude": -8.807,
            "individual_attack": 15,
            "individual_defense": 15,
            "individual_stamina": 15,
            "cp": 3000,
            "pokemon_level": 35,
        }
        sent, failures = [], []
        for recipient in recipients:
            payload = {**recipient, "language": "en"}
            try:
                await self.poliswag.poracle.test_pokemon(webhook, payload)
                sent.append(recipient["name"])
            except PoracleError as e:
                failures.append(f"{recipient['name']}: {e}")

        lines = []
        if sent:
            nice = ", ".join(
                f"DM ({n})" if target.lower() == "dm" else f"#{n}" for n in sent
            )
            lines.append(f"✔ Teste ({pretty}) enviado para {nice}.")
        for f in failures:
            lines.append(f"✖ {f}")
        await self._reply(
            ctx,
            "\n".join(lines) if lines else "Sem alterações.",
            title="Notificação de teste",
            error=not sent,
        )


async def setup(bot):
    await bot.add_cog(Notifications(bot))
