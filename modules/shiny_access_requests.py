"""Durable moderation buttons and atomic website shiny-access decisions."""

import copy
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import discord

from modules.config import Config
from modules.database_connector import connect

PREFIX = "shiny-access:"
BUTTON = re.compile(r"^shiny-access:(accept|reject):(\d{1,20}):(\d{20})$")


def utc_datetime(value):
    """Database DATETIME values are UTC even when the host uses Lisbon time."""
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def identity_text(value, fallback):
    return discord.utils.escape_markdown(" ".join(str(value or fallback).split())[:100])


def avatar_thumbnail(value):
    """Accept HTTPS images; request small static Discord avatars."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        if parsed.hostname in {"cdn.discordapp.com", "media.discordapp.net"}:
            path = (
                parsed.path
                if parsed.path.startswith("/embed/avatars/")
                else re.sub(r"\.(?:gif|png|jpe?g|webp)$", ".webp", parsed.path)
            )
            query = dict(parse_qsl(parsed.query))
            query["size"] = "128"
            return urlunsplit(
                (parsed.scheme, parsed.netloc, path, urlencode(query), "")
            )
        return value
    except ValueError:
        return None


def decision_embed(original, moderator, accepted, decided_at):
    """Keep the request identity and show the decision in the title/footer."""
    embed = copy.deepcopy(original)
    embed.color = 0x2ECC71 if accepted else 0xE74C3C
    verdict = "aceite" if accepted else "rejeitado"
    embed.title = f"Pokédex: acesso ao mapa de shinies {verdict}"
    embed.clear_fields()
    for field in original.fields:
        if field.name in {"Nome no servidor", "Conta Discord"}:
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
    name = " ".join(str(moderator.display_name or moderator.name).split())
    embed.set_footer(text=name)
    embed.timestamp = utc_datetime(decided_at)
    return embed


def request_stamp(value):
    """Keep the full UTC DATETIME(6), including microseconds, in button IDs."""
    return utc_datetime(value).strftime("%Y%m%d%H%M%S%f")


def parse_button(custom_id):
    match = BUTTON.fullmatch(custom_id)
    if not match:
        return None
    action, discord_id, stamp = match.groups()
    try:
        datetime.strptime(stamp, "%Y%m%d%H%M%S%f")
    except ValueError:
        return None
    return action, discord_id, stamp


def identity_embed(row, base_url):
    """Shared server/account identity for requests and association failures."""
    discord_id = str(row["discord_id"])
    display_name = identity_text(
        row.get("display_name"), row.get("name") or "Treinador"
    )
    account = identity_text(row.get("username"), discord_id)
    thumbnail = avatar_thumbnail(row.get("avatar_url"))
    return {
        "url": f"{base_url.rstrip('/')}/jogador/{discord_id}",
        "color": Config.EMBED_COLOR,
        "fields": [
            {"name": "Nome no servidor", "value": display_name, "inline": True},
            {"name": "Conta Discord", "value": f"@{account}", "inline": True},
        ],
        **({"thumbnail": {"url": thumbnail}} if thumbnail else {}),
    }


def failure_payload(row, base_url):
    """Describe a failed association without exposing infrastructure details."""
    reason = str(row.get("fail_reason") or "").casefold()
    if reason.startswith("código não encontrado"):
        explanation = "O Friend Code não foi encontrado no Pokémon GO."
    elif reason.startswith("convite recusado pelo jogo"):
        explanation = "O Pokémon GO recusou o convite necessário à associação."
    else:
        explanation = (
            "Não foi possível concluir a associação. "
            "O jogador deve confirmar o Friend Code e tentar novamente."
        )
    embed = identity_embed(row, base_url)
    embed.update(
        title="Pokédex: não foi possível associar o Friend Code",
        color=0xE74C3C,
        footer={"text": "Associação falhada"},
        timestamp=utc_datetime(row["resolved_at"]).isoformat(),
    )
    embed["fields"].append({"name": "Motivo", "value": explanation, "inline": False})
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


def request_payload(row, base_url):
    discord_id = str(row["discord_id"])
    stamp = request_stamp(row["shiny_access_requested_at"])
    embed = identity_embed(row, base_url)
    embed.update(
        title="Pokédex: pedido de acesso ao mapa de shinies",
        description=f"Friend Code: {row.get('friend_code') or 'Não indicado'}",
    )
    return {
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": style,
                        "label": label,
                        "custom_id": f"{PREFIX}{action}:{discord_id}:{stamp}",
                    }
                    for action, label, style in (
                        ("accept", "Aceitar", 3),
                        ("reject", "Rejeitar", 4),
                    )
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def decide_request(discord_id, stamp, accept):
    """Lock the pending version; commit access, revision and link as one unit.

    Return False without writes for departed/resolved/replaced requests.
    No retry after a failed commit: the outcome may already be durable.
    """
    schema = Config.DB_POLISWAG
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", schema):
        raise ValueError("Invalid Poliswag schema")
    links = f"`{schema}`.shiny_link"
    connection = connect(Config.DB_POGOLEIRIA, dict_rows=True, autocommit=False)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT discord_id, username, friend_code, shiny_areas, shiny_relink, "
                "shiny_access_requested_at FROM trade_player "
                "WHERE discord_id = %s AND left_at IS NULL AND shiny_access = 0 "
                "AND shiny_access_requested_at IS NOT NULL FOR UPDATE",
                (discord_id,),
            )
            player = cursor.fetchone()
            if (
                not player
                or request_stamp(player["shiny_access_requested_at"]) != stamp
            ):
                connection.rollback()
                return False
            if accept:
                cursor.execute(
                    f"SELECT status FROM {links} WHERE discord_id = %s FOR UPDATE",
                    (discord_id,),
                )
                link = cursor.fetchone()
                code = player["friend_code"]
                if (not link or link["status"] != "linked") and not re.fullmatch(
                    r"\d{12}", str(code or "")
                ):
                    raise ValueError(
                        "O jogador precisa de guardar um Código de amizade válido."
                    )
                areas = [
                    area
                    for area in ("leiria", "marinha")
                    if area in str(player["shiny_areas"] or "").split(",")
                ] or ["leiria"]
                leiria, marinha = int("leiria" in areas), int("marinha" in areas)
                cursor.execute(
                    "UPDATE trade_player SET collecting = CASE "
                    "WHEN FIND_IN_SET('shiny', collecting) = 0 THEN "
                    "CONCAT_WS(',', NULLIF(collecting, ''), 'shiny') ELSE collecting END, "
                    "shiny_access = 1, shiny_map = 1, shiny_access_requested_at = NULL, "
                    "shiny_relink = 0, shiny_areas = %s, "
                    "shiny_settings_revision = shiny_settings_revision + 1 "
                    "WHERE discord_id = %s",
                    (",".join(areas), discord_id),
                )
                cursor.execute(
                    f"UPDATE {links} SET alert_leiria = %s, alert_marinha = %s "
                    "WHERE discord_id = %s",
                    (leiria, marinha, discord_id),
                )
                if code and (
                    player["shiny_relink"] or not link or link["status"] == "failed"
                ):
                    cursor.execute(
                        f"INSERT INTO {links} (discord_id, discord_name, friend_code, "
                        "status, requested_at, alert_leiria, alert_marinha) "
                        "VALUES (%s, %s, %s, 'pending', UTC_TIMESTAMP(), %s, %s) "
                        "ON DUPLICATE KEY UPDATE discord_name = VALUES(discord_name), "
                        "friend_code = VALUES(friend_code), status = 'pending', "
                        "trainer_name = NULL, player_id = NULL, fail_reason = NULL, "
                        "attempts = 0, requested_at = UTC_TIMESTAMP(), resolved_at = NULL, "
                        "announced_at = NULL, link_processing_at = NULL, "
                        "alert_leiria = VALUES(alert_leiria), alert_marinha = VALUES(alert_marinha)",
                        (
                            discord_id,
                            str(player["username"])[:64],
                            code,
                            leiria,
                            marinha,
                        ),
                    )
            else:
                cursor.execute(
                    "UPDATE trade_player SET shiny_access = 0, shiny_map = 0, "
                    "shiny_access_requested_at = NULL, "
                    "shiny_settings_revision = shiny_settings_revision + 1 "
                    "WHERE discord_id = %s",
                    (discord_id,),
                )
        connection.commit()
        return True
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
