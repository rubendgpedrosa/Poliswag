"""Opt-in event-only predictions, grouped and capped independently of base DMs."""

import json
import time

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.shiny_roll import is_shiny_at, shiny_draw
from modules.shiny_store import ShinyStore

MAX_DIGEST_SPAWNS = 10
MIN_SECONDS_LEFT = 120

_EVENT_SPAWNS_SQL = f"""
SELECT p.id, p.pokemon_id, COALESCE(p.form, 0) AS form_id, p.lat, p.lon,
       p.expire_timestamp, p.expire_timestamp_verified, p.seen_type, p.iv,
       COALESCE(n.name, n0.name) AS name, r.rate
FROM {Config.DB_SCANNER_NAME}.pokemon p
JOIN shiny_rate r ON r.pokemon_id = p.pokemon_id
LEFT JOIN pokemon_name n ON n.pokemon_id = p.pokemon_id AND n.form_id = COALESCE(p.form, 0)
LEFT JOIN pokemon_name n0 ON n0.pokemon_id = p.pokemon_id AND n0.form_id = 0
LEFT JOIN shiny_alert a ON a.encounter_id = p.id AND a.discord_id = %s
WHERE a.encounter_id IS NULL
  AND (r.ends_at IS NULL OR r.ends_at > UTC_TIMESTAMP())
  AND r.rate >= 2 AND r.rate < 512
  AND (p.expire_timestamp IS NULL OR p.expire_timestamp >= %s)
  AND p.seen_type IN ('wild', 'encounter', 'nearby_stop')
ORDER BY p.expire_timestamp IS NULL, p.expire_timestamp, p.id
"""


def species_filter(value):
    """None means all; malformed persisted preferences match no species."""
    if value is None:
        return None
    try:
        values = json.loads(value) if isinstance(value, str) else value
        if (
            not isinstance(values, list)
            or len(values) > 100
            or any(type(item) is not int or not 1 <= item <= 65535 for item in values)
        ):
            return frozenset()
        return frozenset(values) if values else None
    except (ValueError, TypeError):
        return frozenset()


def event_predictions(rows, recipient, now):
    """Use the existing first-draw threshold; base-rate matches stay separate."""
    wanted = species_filter(recipient.get("event_pokemon_ids"))
    predictions = []
    seen_ids = set()
    for spawn in rows:
        if spawn["id"] in seen_ids:
            continue
        seen_ids.add(spawn["id"])
        if wanted is not None and spawn["pokemon_id"] not in wanted:
            continue
        in_marinha = float(spawn["lon"]) <= Config.MARINHA_LON_MAX
        if not recipient["alert_marinha" if in_marinha else "alert_leiria"]:
            continue
        if spawn["seen_type"] not in ("wild", "encounter", "nearby_stop"):
            continue
        if (
            spawn["expire_timestamp"] is not None
            and spawn["expire_timestamp"] < now + MIN_SECONDS_LEFT
        ):
            continue
        try:
            odds = int(spawn["rate"])
            if not 2 <= odds < 512:
                continue
            draw = shiny_draw(spawn["id"], recipient["player_id"])
        except (ValueError, TypeError, UnicodeError):
            continue
        if is_shiny_at(draw, 512) or not is_shiny_at(draw, odds):
            continue
        predictions.append((spawn, odds, draw))
        if len(predictions) == MAX_DIGEST_SPAWNS:
            break
    return predictions


def digest_embed(predictions):
    from modules.shiny_alerts import _despawn_line

    lines = []
    for spawn, odds, _ in predictions:
        name = str(spawn.get("name") or f"#{spawn['pokemon_id']}")[:80]
        iv = "" if spawn["iv"] is None else f" · {round(spawn['iv'])}% IV"
        approximate = (
            " · localização aproximada" if spawn["seen_type"] == "nearby_stop" else ""
        )
        lines.append(
            f"**{name}{iv}** · 1/{odds}{approximate}\n"
            f"[Direções](https://www.google.com/maps?q={spawn['lat']},{spawn['lon']}) · {_despawn_line(spawn)}"
        )
    return build_embed(
        "✨ Shinies de evento previstos para ti",
        "\n\n".join(lines),
        footer="Até 10 previsões por resumo, no máximo um resumo a cada 5 minutos. O teu mapa mostra as restantes.",
    )


class ShinyEventDigests:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)

    async def tick(self):
        if not Config.SHINY_EVENT_DIGESTS_ENABLED:
            return
        now = int(time.time())
        for recipient in await self.store.event_recipients(Config.SHINY_ALERT_USER_IDS):
            rows = await self.poliswag.db.get_data_from_database(
                _EVENT_SPAWNS_SQL,
                params=(recipient["discord_id"], now + MIN_SECONDS_LEFT),
            )
            predictions = event_predictions(rows, recipient, now)
            if not predictions or not await self.store.claim_event_digest(
                recipient["discord_id"]
            ):
                continue
            recorded = []
            for spawn, odds, draw in predictions:
                if await self.store.record_event_alert(
                    recipient["discord_id"], spawn, odds, draw
                ):
                    recorded.append((spawn, odds, draw))
            if not recorded:
                continue
            delivered = False
            try:
                user = self.poliswag.get_user(
                    int(recipient["discord_id"])
                ) or await self.poliswag.fetch_user(int(recipient["discord_id"]))
                await user.send(
                    embed=digest_embed(recorded),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                delivered = True
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                self.poliswag.utility.log_to_file("Shiny event summary not delivered.")
            finally:
                await self.store.mark_event_delivery(
                    recipient["discord_id"],
                    [item[0]["id"] for item in recorded],
                    delivered,
                )
