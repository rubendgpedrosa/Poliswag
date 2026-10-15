from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import time

import discord
from unittest.mock import MagicMock

import pytest

from modules import shiny_access_requests as requests

STAMP = "20261015120000123456"


def player(**overrides):
    return {
        "discord_id": "123",
        "username": "trainer",
        "friend_code": "123456789012",
        "shiny_areas": "marinha",
        "shiny_relink": 0,
        "shiny_access_requested_at": datetime(2026, 10, 15, 12, 0, 0, 123456),
        **overrides,
    }


@pytest.fixture
def database(mocker):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    mocker.patch.object(requests, "connect", return_value=connection)
    return connection, cursor


def test_button_is_timestamp_bound_and_payload_has_no_pings():
    payload = requests.request_payload(
        {**player(), "name": "@everyone"}, "https://site/pokedex/"
    )
    assert payload["allowed_mentions"] == {"parse": []}
    embed = payload["embeds"][0]
    assert embed["description"] == "Friend Code: 123456789012"
    assert [field["name"] for field in embed["fields"]] == [
        "Nome no servidor",
        "Conta Discord",
    ]
    for button in payload["components"][0]["components"]:
        assert len(button["custom_id"]) <= 100
        assert requests.parse_button(button["custom_id"])[1:] == ("123", STAMP)
    assert requests.parse_button("shiny-access:accept:123:20269915120000123456") is None


@pytest.mark.parametrize(
    "row", [None, player(shiny_access_requested_at="2026-10-15 13:00:00")]
)
def test_stale_request_has_no_writes(database, row):
    connection, cursor = database
    cursor.fetchone.return_value = row
    assert requests.decide_request("123", STAMP, True) is False
    assert cursor.execute.call_count == 1
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "link,relink,queues",
    [
        (None, 0, True),
        ({"status": "failed"}, 0, True),
        ({"status": "pending"}, 0, False),
        ({"status": "linked"}, 0, False),
        ({"status": "linked"}, 1, True),
    ],
)
def test_approval_mirrors_site_queue_rules(database, link, relink, queues):
    connection, cursor = database
    cursor.fetchone.side_effect = [player(shiny_relink=relink), link]
    assert requests.decide_request("123", STAMP, True)
    sql = [call.args[0] for call in cursor.execute.call_args_list]
    update = next(statement for statement in sql if "collecting =" in statement)
    assert "FIND_IN_SET('shiny', collecting)" in update
    assert "shiny_access = 1, shiny_map = 1" in update
    assert "shiny_relink = 0" in update
    assert "shiny_settings_revision + 1" in update
    assert any("INSERT INTO" in statement for statement in sql) is queues
    if queues:
        queue = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO" in call.args[0]
        )
        assert queue.args[1] == ("123", "trainer", "123456789012", 0, 1)
        assert "link_processing_at = NULL" in queue.args[0]
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


def test_rejection_does_not_queue_or_clear_relink(database):
    connection, cursor = database
    cursor.fetchone.return_value = player(shiny_relink=1)
    assert requests.decide_request("123", STAMP, False)
    assert cursor.execute.call_count == 2
    sql = cursor.execute.call_args.args[0]
    assert "shiny_access = 0, shiny_map = 0" in sql
    assert "shiny_access_requested_at = NULL" in sql
    assert "shiny_relink" not in sql
    connection.commit.assert_called_once()


def test_invalid_friend_code_rolls_back(database):
    connection, cursor = database
    cursor.fetchone.side_effect = [player(friend_code="invalid"), None]
    with pytest.raises(ValueError, match="Código"):
        requests.decide_request("123", STAMP, True)
    assert cursor.execute.call_count == 2
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_queue_failure_rolls_back_access(database):
    connection, cursor = database
    cursor.fetchone.side_effect = [player(), None]
    cursor.execute.side_effect = [None, None, None, None, RuntimeError("queue failed")]
    with pytest.raises(RuntimeError, match="queue failed"):
        requests.decide_request("123", STAMP, True)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("accepted", [True, False])
def test_decision_preserves_identity_avatar_and_records_footer(accepted):
    row = {
        **player(),
        "name": "Trainer",
        "display_name": "Server Nick",
        "username": "discord.account",
        "avatar_url": "https://cdn.discordapp.com/avatars/123/a_hash.gif?size=1024",
    }
    original = discord.Embed.from_dict(
        requests.request_payload(row, "https://site")["embeds"][0]
    )
    before = deepcopy(original.to_dict())
    decided = datetime(2026, 10, 15, 13, tzinfo=timezone.utc)
    moderator = SimpleNamespace(
        id=456, display_name="Moderator_* Nick\nSecond line", name="mod.account"
    )
    # Existing posted requests may still have the old request-date field/timestamp.
    original.add_field(name="Pedido a", value="old request date", inline=False)
    original.timestamp = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    legacy = original.to_dict()
    result = requests.decision_embed(original, moderator, accepted, decided).to_dict()
    assert original.to_dict() == legacy
    assert result["fields"] == before["fields"][:2]
    assert [field["name"] for field in before["fields"]] == [
        "Nome no servidor",
        "Conta Discord",
    ]
    assert before["fields"][0]["value"] == "Server Nick"
    assert before["fields"][1]["value"] == "@discord.account"
    assert before["description"] == "Friend Code: 123456789012"
    assert (
        result["thumbnail"]
        == before["thumbnail"]
        == {"url": "https://cdn.discordapp.com/avatars/123/a_hash.webp?size=128"}
    )
    assert "timestamp" not in before
    assert result["timestamp"] == decided.isoformat()
    assert result["footer"] == {"text": "Moderator_* Nick Second line"}
    verdict = "aceite" if accepted else "rejeitado"
    assert result["title"] == f"Pokédex: acesso ao mapa de shinies {verdict}"
    assert result["color"] == (0x2ECC71 if accepted else 0xE74C3C)


def test_database_datetime_is_utc_even_in_lisbon_summer_time():
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Europe/Lisbon"
        time.tzset()
        naive = datetime(2026, 10, 15, 12)
        assert requests.utc_datetime(naive) == naive.replace(tzinfo=timezone.utc)
        aware = datetime(2026, 10, 15, 13, tzinfo=timezone(timedelta(hours=1)))
        assert requests.utc_datetime(aware) == requests.utc_datetime(naive)
        assert requests.request_stamp(aware) == requests.request_stamp(naive)
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


@pytest.mark.parametrize(
    "avatar",
    [
        None,
        "",
        "http://site/avatar.png",
        "javascript:alert(1)",
        "https://user:password@site/avatar.png",
    ],
)
def test_unsafe_or_missing_avatar_is_omitted(avatar):
    payload = requests.request_payload(
        {**player(), "name": None, "avatar_url": avatar}, "https://site"
    )
    assert "thumbnail" not in payload["embeds"][0]


def test_default_discord_avatar_keeps_png_asset():
    assert requests.avatar_thumbnail(
        "https://cdn.discordapp.com/embed/avatars/0.png"
    ) == ("https://cdn.discordapp.com/embed/avatars/0.png?size=128")


@pytest.mark.parametrize(
    "reason,expected",
    [
        (
            "código não encontrado (1)",
            "O Friend Code não foi encontrado no Pokémon GO.",
        ),
        (
            "convite recusado pelo jogo (7)",
            "O Pokémon GO recusou o convite necessário à associação.",
        ),
        ("invalid internal ID: secrets", "Não foi possível concluir a associação."),
        (None, "Não foi possível concluir a associação."),
    ],
)
def test_failure_embed_has_identity_avatar_safe_reason_and_failure_date(
    reason, expected
):
    row = {
        "discord_id": "123",
        "display_name": "Server Nick",
        "username": "discord.account",
        "avatar_url": "https://cdn.discordapp.com/avatars/123/hash.png?size=1024",
        "fail_reason": reason,
        "resolved_at": "2026-10-15 12:05:00",
    }
    payload = requests.failure_payload(row, "https://site/pokedex/")
    assert payload["allowed_mentions"] == {"parse": []}
    assert "components" not in payload
    embed = payload["embeds"][0]
    assert embed["url"] == "https://site/pokedex/jogador/123"
    assert embed["title"] == "Pokédex: não foi possível associar o Friend Code"
    assert [field["name"] for field in embed["fields"]] == [
        "Nome no servidor",
        "Conta Discord",
        "Motivo",
    ]
    assert embed["fields"][0]["value"] == "Server Nick"
    assert embed["fields"][1]["value"] == "@discord.account"
    assert embed["fields"][2]["value"].startswith(expected)
    assert "secrets" not in str(payload)
    assert embed["thumbnail"]["url"].endswith("/hash.webp?size=128")
    assert embed["footer"] == {"text": "Associação falhada"}
    assert embed["timestamp"] == "2026-10-15T12:05:00+00:00"
