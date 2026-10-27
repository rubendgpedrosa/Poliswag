"""Shiny predictions, to a player's own channel or by direct message.

Two jobs each scheduler tick:

1. Tell a pilot user (Config.SHINY_ALERT_USER_IDS) their friend code resolved
   or failed. The host script scripts/shiny_linker writes the result; this
   sends one DM and stamps announced_at. Anyone else's link is stamped
   without a DM: during the pilot the bot messages nobody but its users.
2. For linked players with a channel of their own (`!shiny canal`) or in
   Config.SHINY_ALERT_USER_IDS (who get a DM), roll every new spawn
   (modules/shiny_roll.py) and alert the ones that come up 0, unless the
   species/form/costume lacks released or confirmed wild eligibility.

Owns shiny_alert and poliswag.shiny_alert_watermark; reads shiny_link,
shiny_species (confirmed sightings), golbat's pokemon and pokemon_shiny_stats, and
pokemon_name.
"""

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from modules.config import Config
from modules.pokemon_sprites import pokemon_sprite_url
from modules.embeds import build_embed
from modules.poracle_client import PoracleError
from modules.shiny_roll import is_background_at, personal_draws
from modules.background_prediction import background_rule_for
from modules.shiny_store import ShinyStore
from modules.shiny_channels import ShinyChannels

_LISBON = ZoneInfo("Europe/Lisbon")
# A spawn with less than this left is not worth a DM.
MIN_SECONDS_LEFT = 120
# A sighting has no IV until a scanner account encounters it, usually within
# a minute or two. An alert waits this long for it, then goes out without.
WAIT_FOR_IV = 180
# How far behind the watermark each tick reads, so a waiting spawn is seen
# again; shiny_alert keeps the ones already sent from going out twice.
LOOK_BACK = 300
# The base wild rate: the map's, and the one every species keeps
# whatever an event row says, since a better rate only adds spawns.
BASE_ODDS = 512
# Which spawns are rolled follows Golbat PR #423: only ones the scanner has
# seen itself (wild or encountered), never Ditto or anything shown as another
# species, and not one whose costume is unknown.
_SPAWNS_SQL = f"""
SELECT id, pokemon_id, COALESCE(form, 0) AS form_id, lat, lon, expire_timestamp,
       expire_timestamp_verified, seen_type, first_seen_timestamp,
       iv, atk_iv, def_iv, sta_iv, cp, level, costume, gender, move_1, move_2,
       weight, height, size, weather, spawn_id, pokestop_id, is_event, updated, pvp
FROM {Config.DB_SCANNER_NAME}.pokemon
WHERE first_seen_timestamp >= %s
  AND (expire_timestamp IS NULL OR expire_timestamp >= %s)
  AND seen_type IN ('wild', 'encounter')
  AND pokemon_id <> 132 AND is_ditto = 0
  AND (display_pokemon_id IS NULL OR display_pokemon_id IN (0, pokemon_id))
  AND COALESCE(costume, 0) <> 255
ORDER BY first_seen_timestamp
"""
_SEEN_SHINY_SQL = f"""
SELECT 1 AS seen FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
WHERE pokemon_id = %s AND form_id = %s AND `count` > 0 LIMIT 1
"""


def link_message(row):
    """The one DM a pilot user gets when the linker is done with their code."""
    if row["status"] == "linked":
        return (
            f"✨ O teu código ficou associado ao treinador **{row['trainer_name']}**."
            "\n-# Não és tu? Escreve `!shiny remover` e envia o código certo."
        )
    return (
        f"Não consegui associar o teu código: {row['fail_reason']}. "
        "Confirma-o no jogo e envia `!shiny <código>` outra vez."
    )


def plays_there(recipient, spawn):
    """Does this player want spawns where this one is? Marinha Grande is west
    of Config.MARINHA_LON_MAX, Leiria east, the same line the quests use."""
    in_marinha = float(spawn["lon"]) <= Config.MARINHA_LON_MAX
    return bool(recipient["alert_marinha"] if in_marinha else recipient["alert_leiria"])


def is_hundo(spawn):
    """15/15/15 by the scanner's reading. An IV not read yet is not 100%."""
    ivs = (spawn.get("atk_iv"), spawn.get("def_iv"), spawn.get("sta_iv"))
    return ivs == (15, 15, 15)


def wants_alert(recipient, spawn, owned):
    """Which predicted shinies a player is told about, by their Pokédex
    settings: one they lack, any at all under "Todos", or an owned one at
    100% while that switch is on (the default)."""
    if not recipient.get("shiny_notify_discord", 1):
        return False
    if spawn.get("predicted_background") and recipient.get("shiny_notify_background"):
        return True
    if spawn.get("predicted_shiny") is False:
        return False
    if not owned or recipient.get("shiny_notify_all"):
        return True
    return bool(recipient.get("shiny_notify_hundo", 1)) and is_hundo(spawn)


# Poracle's DTS entries for these alerts (monster templates in its dts.json;
# copies are kept in templates/poracle/). "shiny" is Poracle's standard
# Pokémon alert with a ✨ before the title. A predicted special background has
# two of its own, so a spawn that is not Shiny is never labelled one: 🖼️
# before the title (✨🖼️ when it is Shiny as well) and a line saying it is an
# experimental prediction.
PORACLE_TEMPLATE = "shiny"
PORACLE_BACKGROUND_TEMPLATE = "background"
PORACLE_SHINY_BACKGROUND_TEMPLATE = "shiny-background"
PORACLE_SHINY_HERE_TEMPLATE = "shiny-here"
PORACLE_SHINY_BACKGROUND_HERE_TEMPLATE = "shiny-background-here"
# The side bar says the same as the map's pins (owner, 2026-10-11): gold is a
# Shiny, with or without a background; sky blue is a background alone. The
# templates carry these too; here they colour the bot's own fallback embed.
SHINY_COLOR = 0xF2CD5C
BACKGROUND_COLOR = 0x5AA9E6


def is_shundo(spawn):
    """A predicted shiny at exactly 15/15/15, including legacy shiny rows."""
    return spawn.get("predicted_shiny") is not False and is_hundo(spawn)


def poracle_template(spawn, *, channel=False):
    # Only the recipient's own channel may ping. A 100% background prediction
    # without that player's shiny prediction must keep its ordinary template.
    if channel and is_shundo(spawn):
        return (
            PORACLE_SHINY_BACKGROUND_HERE_TEMPLATE
            if spawn.get("predicted_background")
            else PORACLE_SHINY_HERE_TEMPLATE
        )
    if not spawn.get("predicted_background"):
        return PORACLE_TEMPLATE
    if spawn.get("predicted_shiny"):
        return PORACLE_SHINY_BACKGROUND_TEMPLATE
    return PORACLE_BACKGROUND_TEMPLATE


def poracle_webhook(spawn):
    """The spawn as Golbat would have sent it to Poracle, so Poracle renders
    it exactly like its own alerts: map tile, IV, moves, address."""
    try:
        pvp = json.loads(spawn["pvp"]) if spawn.get("pvp") else None
    except (TypeError, ValueError):
        pvp = None

    def number(value):
        return float(value) if value is not None else None

    return {
        "encounter_id": str(spawn["id"]),
        "pokemon_id": spawn["pokemon_id"],
        "shiny": spawn.get("predicted_shiny") is not False,
        # COALESCE(form, 0) comes back from the driver as a Decimal, which
        # json refuses: every alert crashed here from 16:54 on 2026-10-10.
        "form": int(spawn["form_id"]),
        "costume": spawn.get("costume") or 0,
        "gender": spawn.get("gender"),
        "latitude": float(spawn["lat"]),
        "longitude": float(spawn["lon"]),
        "disappear_time": spawn["expire_timestamp"],
        "disappear_time_verified": bool(spawn["expire_timestamp_verified"]),
        "first_seen": spawn["first_seen_timestamp"],
        "last_modified_time": spawn.get("updated"),
        "individual_attack": spawn["atk_iv"],
        "individual_defense": spawn["def_iv"],
        "individual_stamina": spawn["sta_iv"],
        "cp": spawn["cp"],
        "pokemon_level": spawn["level"],
        "move_1": spawn.get("move_1"),
        "move_2": spawn.get("move_2"),
        "weight": number(spawn.get("weight")),
        "height": number(spawn.get("height")),
        "size": spawn.get("size"),
        "weather": spawn.get("weather") or 0,
        "spawnpoint_id": (
            format(spawn["spawn_id"], "x") if spawn.get("spawn_id") else "None"
        ),
        "pokestop_id": spawn.get("pokestop_id") or "None",
        "is_event": spawn.get("is_event") or 0,
        "seen_type": spawn["seen_type"],
        "pvp": pvp,
    }


def _despawn_line(spawn):
    expires = spawn["expire_timestamp"]
    if not expires:
        return "⏳ Hora de desaparecer desconhecida"
    hour = datetime.fromtimestamp(expires, _LISBON).strftime("%H:%M")
    if spawn["expire_timestamp_verified"]:
        return f"⏳ Desaparece às {hour}"
    return f"⏳ Desaparece por volta das {hour} (estimado)"


def _iv_line(spawn):
    if spawn["iv"] is None:
        return "💪 IV ainda desconhecido"
    line = (
        f"💪 IV **{round(spawn['iv'])}%** "
        f"({spawn['atk_iv']}/{spawn['def_iv']}/{spawn['sta_iv']})"
    )
    if spawn["cp"]:
        line += f" · CP {spawn['cp']}"
    if spawn["level"]:
        line += f" · nível {spawn['level']}"
    return line


def alert_embed(spawn, name, seen_shiny):
    lines = [
        _iv_line(spawn),
        f"📍 [Abrir no mapa](https://www.google.com/maps?q={spawn['lat']},{spawn['lon']})",
        _despawn_line(spawn),
        (
            f"🖼️ Fundo especial previsto · {spawn.get('background_label', 'evento')} (experimental)."
            if spawn.get("predicted_background")
            else (
                "✅ O scanner já viu este Pokémon shiny."
                if seen_shiny
                else "⚠️ O scanner nunca viu este Pokémon shiny: pode não existir em shiny."
            )
        ),
    ]
    thumbnail = pokemon_sprite_url(
        spawn["pokemon_id"],
        form=spawn.get("form_id", 0),
        costume=spawn.get("costume", 0),
        gender=spawn.get("gender", 0),
        shiny=spawn.get("predicted_shiny") is not False,
    )
    headline = name if spawn["iv"] is None else f"{name} {round(spawn['iv'])}%"
    if spawn.get("predicted_background"):
        kind = (
            "✨🖼️ Shiny + fundo especial"
            if spawn.get("predicted_shiny")
            else "🖼️ Fundo especial"
        )
        embed = build_embed(f"{kind} · {headline}", "\n".join(lines))
        embed.set_thumbnail(url=thumbnail)
        embed.color = SHINY_COLOR if spawn.get("predicted_shiny") else BACKGROUND_COLOR
        return embed
    embed = build_embed(f"✨ {headline}", "\n".join(lines))
    embed.set_thumbnail(url=thumbnail)
    embed.color = SHINY_COLOR
    return embed


class ShinyAlerts:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)
        self.channels = ShinyChannels(poliswag)

    async def tick(self):
        # Read first: only a change saved before this tick began is confirmed
        # by it. The steps below read every setting fresh.
        pending = await self.store.pending_settings()
        await self._confirm_notification_changes()
        await self.channels.tick()
        await self._announce_links()
        await self._alert_spawns()
        # The site says "A aguardar pelo Poliswag…" until this lands. A step
        # that raised above confirms nothing, and neither does a stopped bot.
        for row in pending:
            await self.store.confirm_settings(
                row["discord_id"], row["shiny_settings_revision"]
            )
        # Lazy construction avoids changing baseline reads when summaries are
        # disabled. This path never calls the game or the linker.
        if Config.SHINY_EVENT_DIGESTS_ENABLED:
            from modules.shiny_events import ShinyEventDigests

            await ShinyEventDigests(self.poliswag).tick()

    async def _alert_spawns(self):
        now = int(time.time())
        watermark = await self.store.watermark()
        recipients = await self.store.recipients(Config.SHINY_ALERT_USER_IDS)
        if watermark is None or not recipients:
            # Never run, or nobody to tell: start from now, so the first
            # linked player isn't sent everything already on the map.
            await self.store.set_watermark(now)
            return

        spawns = await self.poliswag.db.get_data_from_database(
            _SPAWNS_SQL, params=(watermark - LOOK_BACK, now + MIN_SECONDS_LEFT)
        )
        eligible = await self.store.shiny_eligible() if spawns else set()
        # A species boosted right now (a Community Day, a costume event) is
        # rolled at that rate, as the map does — the alert used to stay at
        # 1/512 and so arrived four times less often than the event did.
        odds_by_species = await self.store.event_odds() if spawns else {}
        background_rules = (
            await self.store.background_rules(now)
            if spawns and any(r.get("shiny_notify_background") for r in recipients)
            else []
        )
        for spawn in spawns:
            shiny_eligible = (
                spawn["pokemon_id"],
                spawn["form_id"],
                spawn.get("costume") or 0,
            ) in eligible
            rule = background_rule_for(spawn, background_rules, now)
            if not shiny_eligible and rule is None:
                continue
            for recipient in recipients:
                if not plays_there(recipient, spawn):
                    continue
                try:
                    shiny_draw, background_draw = personal_draws(
                        spawn["id"], recipient["player_id"]
                    )
                except (ValueError, OverflowError):
                    break  # not an encounter ID; no recipient can roll it
                odds = odds_by_species.get(spawn["pokemon_id"], BASE_ODDS)
                shiny = shiny_eligible and shiny_draw < 1 / odds
                background = bool(
                    rule
                    and recipient.get("shiny_notify_background")
                    and is_background_at(background_draw, rule["probability"])
                )
                if not shiny and not background:
                    continue
                waited = now - spawn["first_seen_timestamp"]
                if spawn["iv"] is None and waited < WAIT_FOR_IV:
                    continue  # read again next tick, IV in hand or time up
                prediction = {
                    **spawn,
                    "predicted_shiny": shiny,
                    "predicted_background": background,
                }
                if background:
                    prediction["background_label"] = rule["label"]
                    prediction["background_ends_at"] = rule["ends_at"]
                await self._alert(recipient, prediction)
        if spawns:
            # `>=` above re-reads the newest second next tick, in case more
            # rows land in it; shiny_alert keeps those from going out twice.
            await self.store.set_watermark(
                max(s["first_seen_timestamp"] for s in spawns)
            )

    async def _alert(self, recipient, spawn):
        discord_id = recipient["discord_id"]
        if not recipient.get(
            "shiny_notify_discord", 1
        ) or not await self.store.notifications_enabled(discord_id):
            return
        # Recheck the independent background preference just before delivery.
        if spawn.get("predicted_background") and (
            time.time() >= spawn.get("background_ends_at", float("inf"))
            or not await self.store.background_notifications_enabled(discord_id)
        ):
            if not spawn.get("predicted_shiny"):
                return
            spawn = {**spawn, "predicted_background": False}
        # The map shows all predictions; which ones are notified is the
        # player's choice (wants_alert). "Todos" needs no look at the
        # collection. A skipped spawn is not recorded, so an IV that lands
        # while it is still inside LOOK_BACK is judged again.
        owned = (
            not spawn.get("predicted_background")
            and not recipient.get("shiny_notify_all")
            and await self.store.owns_shiny(
                discord_id, spawn["pokemon_id"], spawn["form_id"]
            )
        )
        if not wants_alert(recipient, spawn, owned):
            return
        # Recorded before sending: a spawn told twice is worse than one lost
        # to a failed send.
        if not await self.store.record_alert(
            discord_id, spawn["id"], spawn["pokemon_id"], spawn["form_id"]
        ):
            return
        # Through Poracle, with the template that says what was predicted
        # (poracle_template), so a background alert has the same map tile,
        # address and buttons as a shiny one.
        if await self._through_poracle(recipient, spawn):
            return
        # Poracle could not take it: the bot's own, plainer embed instead.
        key = (spawn["pokemon_id"], spawn["form_id"])
        # Golbat names a species' ordinary form by its own id (Zorua is 2108);
        # pokemon_name files that form under 0, so 0 is the fallback.
        names = await self.poliswag.db.get_data_from_database(
            "SELECT name, form_name FROM pokemon_name WHERE pokemon_id = %s "
            "AND form_id IN (%s, 0) ORDER BY form_id DESC LIMIT 1",
            params=key,
        )
        name = f"#{spawn['pokemon_id']}"
        if names:
            name = names[0]["name"]
            if names[0]["form_name"]:
                name += f" ({names[0]['form_name']})"
        seen = (
            await self.poliswag.db.get_data_from_database(_SEEN_SHINY_SQL, params=key)
            if not spawn.get("predicted_background")
            else []
        )
        embed = alert_embed(spawn, name, bool(seen))
        channel = (
            self.poliswag.get_channel(int(recipient["channel_id"]))
            if recipient.get("channel_id")
            else None
        )
        if channel is not None:
            if is_shundo(spawn):
                await channel.send(
                    content="**✨💯 SHINY 100% 💯✨** @everyone",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=True, users=False, roles=False, replied_user=False
                    ),
                )
            else:
                await channel.send(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none()
                )
        elif discord_id in Config.SHINY_ALERT_USER_IDS:
            await self._dm(discord_id, embed=embed)
        else:
            # Their channel is gone and they are not one of the pilot's
            # users: nobody else is ever messaged directly.
            self.poliswag.utility.log_to_file(
                f"Shiny alert for {discord_id} dropped: their channel is gone", "ERROR"
            )

    async def _through_poracle(self, recipient, spawn):
        """Have Poracle render and deliver the alert, as it does its own.
        Its test endpoint is the one that takes a spawn and a destination
        and skips matching; nothing about the result says "test"."""
        name = recipient.get("discord_name") or str(recipient["discord_id"])
        # Their own channel if they have one (`!shiny canal`), else a DM.
        if recipient.get("channel_id"):
            destination = {
                "id": str(recipient["channel_id"]),
                "name": f"shiny-{name}",
                "type": "discord:channel",
            }
        else:
            destination = {
                "id": str(recipient["discord_id"]),
                "name": name,
                "type": "discord:user",
            }
        target = {
            **destination,
            "language": "en",
            "template": poracle_template(
                spawn, channel=destination["type"] == "discord:channel"
            ),
        }
        try:
            await self.poliswag.poracle.test_pokemon(poracle_webhook(spawn), target)
            return True
        except PoracleError as e:
            self.poliswag.utility.log_to_file(
                f"Shiny alert for {recipient['discord_id']} not sent through Poracle: {e}",
                "ERROR",
            )
            return False

    async def _confirm_notification_changes(self):
        for row in await self.store.notification_changes():
            enabled = bool(row["shiny_notify_discord"])
            if not await self.store.claim_notification_change(
                row["discord_id"], enabled
            ):
                continue
            if enabled:
                continue
            text = "As notificações de shinies no Discord foram desligadas."
            if row["shiny_map"]:
                text += " O teu mapa de shinies continua ativo."
            text += " Podes voltar a ligá-las em Perfil e definições no Pokédex."
            channel = (
                self.poliswag.get_channel(int(row["channel_id"]))
                if row.get("channel_id") and not row.get("channel_archived")
                else None
            )
            if channel is None:
                await self._dm(row["discord_id"], content=text)
                continue
            try:
                await channel.send(
                    content=text, allowed_mentions=discord.AllowedMentions.none()
                )
            except (discord.Forbidden, discord.NotFound):
                await self._dm(row["discord_id"], content=text)

    async def _announce_links(self):
        for row in await self.store.unannounced():
            # Only the pilot's users hear from the bot about shinies. A link
            # the owner made for someone else is closed without a word.
            if row["discord_id"] in Config.SHINY_ALERT_USER_IDS:
                await self._dm(row["discord_id"], content=link_message(row))
            # Stamped even when the DM was refused, or it would be retried
            # every minute for ever; `!shiny` still shows the status.
            await self.store.mark_announced(row["discord_id"])

    async def _dm(self, discord_id, **message):
        try:
            user = self.poliswag.get_user(
                int(discord_id)
            ) or await self.poliswag.fetch_user(int(discord_id))
            await user.send(**message, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound) as e:
            self.poliswag.utility.log_to_file(
                f"Shiny DM to {discord_id} not delivered: {e}"
            )
