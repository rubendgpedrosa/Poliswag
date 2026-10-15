import json
from unittest.mock import Mock

import pytest

from scripts import pokedex_moderation_alert as alerts


def row(
    discord_id="123",
    requested="2026-10-15 12:00:00.123456",
    friend_code="123456789012",
):
    return {
        "discord_id": discord_id,
        "name": "Trainer",
        "friend_code": friend_code,
        "shiny_access_requested_at": requested,
    }


def test_partial_failure_persists_each_success_and_retries_only_unsent(tmp_path):
    state = tmp_path / "state.json"
    rows = [row(), row("456")]
    send = Mock(side_effect=[None, OSError("failed")])
    with pytest.raises(OSError):
        alerts.notify_pending(rows, state, send, "https://site/pokedex")
    assert json.loads(state.read_text()) == [["123", "20261015120000123456"]]
    retry = Mock()
    assert alerts.notify_pending(rows, state, retry, "https://site/pokedex") == 1
    assert retry.call_args.args[0]["embeds"][0]["url"].endswith("/jogador/456")
    assert alerts.notify_pending(rows, state, retry, "https://site/pokedex") == 0


def test_resolved_and_new_timestamp_requests_notify_again(tmp_path):
    state = tmp_path / "state.json"
    send = Mock()
    assert alerts.notify_pending([row()], state, send, "https://site") == 1
    assert alerts.notify_pending([], state, send, "https://site") == 0
    assert alerts.notify_pending([row()], state, send, "https://site") == 1
    assert (
        alerts.notify_pending(
            [row(requested="2026-10-15 12:00:00.123457")], state, send, "https://site"
        )
        == 1
    )


def test_request_embed_shows_the_saved_friend_code_or_a_fallback(tmp_path):
    send = Mock()
    state = tmp_path / "state.json"
    alerts.notify_pending([row()], state, send, "https://site")
    assert (
        send.call_args.args[0]["embeds"][0]["description"]
        == "Friend Code: 123456789012"
    )
    alerts.notify_pending([row("456", friend_code=None)], state, send, "https://site")
    assert (
        send.call_args.args[0]["embeds"][0]["description"]
        == "Friend Code: Não indicado"
    )


def test_main_posts_directly_to_moderation_channel(mocker, tmp_path):
    from modules.config import Config

    mocker.patch.object(Config, "IS_PRODUCTION", True)
    mocker.patch.object(Config, "MOD_CHANNEL_ID", 321)
    mocker.patch.object(Config, "DISCORD_API_KEY", "fake")
    mocker.patch.object(alerts, "STATE", tmp_path / "state.json")
    mocker.patch.object(alerts, "FAILURE_STATE", tmp_path / "failures.json")
    connection = mocker.patch("modules.database_connector.connect").return_value
    connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [
        [row()],
        [],
    ]
    mocker.patch.object(alerts, "member_enricher", return_value=lambda value: value)
    post = mocker.patch.object(alerts.dm_owner, "post")
    alerts.main()
    path, token, payload = post.call_args.args
    assert (path, token) == ("/channels/321/messages", "fake")
    assert payload["allowed_mentions"] == {"parse": []}
    assert len(payload["components"][0]["components"]) == 2
    post.assert_called_once()
    connection.close.assert_called_once()
    sql = connection.cursor.return_value.__enter__.return_value.execute.call_args_list[
        0
    ].args[0]
    assert "display_name, username, avatar_url, friend_code" in sql
    assert payload["embeds"][0]["description"] == "Friend Code: 123456789012"


def test_enriches_only_unsent_requests(tmp_path):
    enrich = Mock(
        side_effect=lambda value: {
            **value,
            "display_name": "Guild Nick",
            "username": "discord.account",
        }
    )
    send = Mock()
    state = tmp_path / "state.json"
    alerts.notify_pending([row()], state, send, "https://site", enrich=enrich)
    alerts.notify_pending([row()], state, send, "https://site", enrich=enrich)
    enrich.assert_called_once()
    fields = send.call_args.args[0]["embeds"][0]["fields"]
    assert fields[0]["value"] == "Guild Nick"
    assert fields[1]["value"] == "@discord.account"


@pytest.mark.parametrize("avatar_kind", ["guild", "user", "default"])
def test_member_enrichment_uses_server_identity_and_avatar(mocker, avatar_kind):
    member = {
        "nick": "Guild Nick",
        "avatar": "guild_hash" if avatar_kind == "guild" else None,
        "user": {
            "username": "discord.account",
            "global_name": "Global Name",
            "avatar": "user_hash" if avatar_kind == "user" else None,
            "discriminator": "0",
        },
    }
    get = mocker.patch.object(
        alerts, "discord_get", side_effect=[{"guild_id": "789"}, member, member]
    )
    enrich = alerts.member_enricher(321, "fake")
    result = enrich(row())
    assert result["display_name"] == "Guild Nick"
    assert result["username"] == "discord.account"
    expected_path = {
        "guild": "/guilds/789/users/123/avatars/guild_hash.webp",
        "user": "/avatars/123/user_hash.webp",
        "default": "/embed/avatars/0.png",
    }[avatar_kind]
    assert expected_path in result["avatar_url"]
    enrich(row())
    assert [call.args[0] for call in get.call_args_list] == [
        "/channels/321",
        "/guilds/789/members/123",
        "/guilds/789/members/123",
    ]


def test_member_enrichment_falls_back_when_discord_is_unavailable(mocker):
    get = mocker.patch.object(alerts, "discord_get", side_effect=OSError("forbidden"))
    enrich = alerts.member_enricher(321, "fake")
    original = row()
    assert enrich(original) is original
    assert enrich(original) is original
    get.assert_called_once()


def test_member_without_nickname_uses_global_name(mocker):
    mocker.patch.object(
        alerts,
        "discord_get",
        side_effect=[
            {"guild_id": "789"},
            {"user": {"username": "discord.account", "global_name": "Global Name"}},
        ],
    )
    assert alerts.member_enricher(321, "fake")(row())["display_name"] == "Global Name"


def failed_row(discord_id="123", **changes):
    return {
        "discord_id": discord_id,
        "display_name": "Guild Nick",
        "username": "discord.account",
        "requested_at": "2026-10-15 12:00:00.123456",
        "resolved_at": "2026-10-15 12:05:00",
        "fail_reason": "código não encontrado (1)",
        **changes,
    }


def test_failed_attempt_persists_success_and_retries_unsent(tmp_path):
    state = tmp_path / "failed.json"
    rows = [failed_row(), failed_row("456")]
    send = Mock(side_effect=[None, OSError("delivery failed")])
    with pytest.raises(OSError):
        alerts.notify_failures(rows, state, send, "https://site")
    assert json.loads(state.read_text()) == [
        ["123", "20261015120000123456", "20261015120500000000"]
    ]
    send = Mock()
    enrich = Mock(side_effect=lambda value: value)
    assert alerts.notify_failures(rows, state, send, "https://site", enrich) == 1
    assert enrich.call_args.args[0]["discord_id"] == "456"
    assert alerts.notify_failures(rows, state, send, "https://site", enrich) == 0
    enrich.assert_called_once()
    assert alerts.notify_failures([], state, send, "https://site") == 0
    assert json.loads(state.read_text()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"requested_at": "2026-10-15 12:00:00.123457"},
        {"resolved_at": "2026-10-15 12:06:00"},
    ],
)
def test_new_failed_attempt_notifies_again(tmp_path, changes):
    state = tmp_path / "failed.json"
    send = Mock()
    assert alerts.notify_failures([failed_row()], state, send, "https://site") == 1
    assert (
        alerts.notify_failures([failed_row(**changes)], state, send, "https://site")
        == 1
    )


def test_failure_query_includes_map_only_but_excludes_unapproved_or_departed():
    sql = alerts.failure_sql("community_test", "bot_test")
    assert "FROM `community_test`.trade_player p" in sql
    assert "JOIN `bot_test`.shiny_link l" in sql
    assert "p.left_at IS NULL AND p.shiny_access = 1" in sql
    assert "l.status = 'failed'" in sql
    assert "shiny_notify_discord" not in sql
    assert "shiny_map" not in sql


@pytest.mark.parametrize("schema", ["bad.schema", "name; DROP", "", None, "`bot`"])
def test_failure_query_rejects_invalid_schema(schema):
    with pytest.raises(ValueError, match="schema"):
        alerts.failure_sql(schema, "bot")
    with pytest.raises(ValueError, match="schema"):
        alerts.failure_sql("community", schema)


@pytest.mark.parametrize("pending_fails", [False, True])
def test_main_sends_failure_even_if_pending_delivery_fails(
    mocker, tmp_path, pending_fails
):
    from modules.config import Config

    mocker.patch.object(Config, "IS_PRODUCTION", True)
    mocker.patch.object(Config, "MOD_CHANNEL_ID", 321)
    mocker.patch.object(Config, "DISCORD_API_KEY", "fake")
    mocker.patch.object(alerts, "STATE", tmp_path / "pending.json")
    mocker.patch.object(alerts, "FAILURE_STATE", tmp_path / "failed.json")
    mocker.patch.object(alerts, "member_enricher", return_value=lambda value: value)
    connection = mocker.patch("modules.database_connector.connect").return_value
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [[row()], [failed_row()]]
    post = mocker.patch.object(alerts.dm_owner, "post")
    if pending_fails:
        post.side_effect = [OSError("pending delivery failed"), None]
        with pytest.raises(ExceptionGroup, match="Moderation notifications"):
            alerts.main()
    else:
        alerts.main()
    assert post.call_count == 2
    path, token, payload = post.call_args.args
    assert (path, token) == ("/channels/321/messages", "fake")
    assert payload["embeds"][0]["title"].endswith("associar o Friend Code")
    assert "components" not in payload
    assert payload["allowed_mentions"] == {"parse": []}
    assert (tmp_path / "failed.json").exists()
    connection.close.assert_called_once()
