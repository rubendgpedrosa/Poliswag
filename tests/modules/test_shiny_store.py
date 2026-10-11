from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.shiny_store import ShinyStore


@pytest.fixture
def db():
    d = MagicMock()
    d.get_data_from_database = AsyncMock(return_value=[])
    d.execute_query_to_database = AsyncMock(return_value=1)
    d.execute_transaction = AsyncMock()
    return d


@pytest.fixture
def store(db):
    return ShinyStore(db)


async def test_get_is_none_for_a_stranger(store):
    assert await store.get(7) is None


async def test_get_returns_the_row(store, db):
    db.get_data_from_database.return_value = [{"status": "linked"}]
    assert await store.get(7) == {"status": "linked"}
    assert db.get_data_from_database.call_args.kwargs["params"] == (7,)


async def test_request_queues_the_code_and_resets_an_old_result(store, db):
    await store.request(7, "rita", "123456789012")
    sql = db.execute_query_to_database.call_args.args[0]
    assert db.execute_query_to_database.call_args.kwargs["params"] == (
        7,
        "rita",
        "123456789012",
    )
    assert "ON DUPLICATE KEY UPDATE" in sql
    for reset in (
        "status = 'pending'",
        "player_id = NULL",
        "trainer_name = NULL",
        "attempts = 0",
        "announced_at = NULL",
    ):
        assert reset in sql


async def test_request_truncates_a_long_name(store, db):
    await store.request(7, "x" * 80, "123456789012")
    assert len(db.execute_query_to_database.call_args.kwargs["params"][1]) == 64


async def test_remove_deletes_alerts_and_link_together(store, db):
    await store.remove(7)
    statements = db.execute_transaction.call_args.args[0]
    assert [s[0].split()[2] for s in statements] == ["shiny_alert", "shiny_link"]
    assert all(s[1] == (7,) for s in statements)


async def test_recipients_are_linked_players_with_a_channel_or_on_the_list(store, db):
    db.get_data_from_database.return_value = [{"discord_id": 7, "player_id": "a" * 16}]
    assert await store.recipients([7, 8]) == [{"discord_id": 7, "player_id": "a" * 16}]
    sql = db.get_data_from_database.call_args.args[0]
    assert "status = 'linked'" in sql
    assert "l.channel_id IS NULL AND l.discord_id IN (%s,%s)" in sql
    assert "p.shiny_access = 1" in sql and "p.shiny_map = 1" in sql
    assert "p.left_at IS NULL" in sql
    assert (
        "l.channel_archived = 0" in sql
        and "l.channel_intro_message_id IS NOT NULL" in sql
    )
    assert "alert_leiria" in sql and "alert_marinha" in sql
    # Which predicted shinies each player is told about (their Pokédex settings).
    assert "p.shiny_notify_all" in sql and "p.shiny_notify_hundo" in sql
    assert "discord_name" in sql  # Poracle wants a name for its target
    assert db.get_data_from_database.call_args.kwargs["params"] == (7, 8)


async def test_with_nobody_listed_players_with_a_channel_still_are(store, db):
    await store.recipients([])
    sql = db.get_data_from_database.call_args.args[0]
    assert "status = 'linked'" in sql and "l.channel_id IS NOT NULL" in sql
    assert "p.shiny_access = 1" in sql and "p.shiny_map = 1" in sql
    assert db.get_data_from_database.call_args.kwargs["params"] == ()


async def test_pending_settings_are_the_players_the_bot_is_behind_on(store, db):
    db.get_data_from_database.return_value = [
        {"discord_id": 7, "shiny_settings_revision": 3}
    ]
    assert await store.pending_settings() == [
        {"discord_id": 7, "shiny_settings_revision": 3}
    ]
    sql = db.get_data_from_database.call_args.args[0]
    assert "trade_player" in sql
    assert "shiny_applied_revision < shiny_settings_revision" in sql


async def test_confirming_stamps_the_revision_that_was_read_never_a_newer_one(
    store, db
):
    await store.confirm_settings(7, 3)
    sql = db.execute_query_to_database.call_args.args[0]
    assert "SET p.shiny_applied_revision = %s" in sql
    assert "p.discord_id = %s AND p.shiny_applied_revision < %s" in sql
    assert db.execute_query_to_database.call_args.kwargs["params"] == (3, 7, 3)


async def test_confirming_waits_while_the_channel_disagrees_with_the_switch(store, db):
    await store.confirm_settings(7, 3)
    sql = db.execute_query_to_database.call_args.args[0]
    # A player with no link row at all is still confirmed (LEFT JOIN, COALESCE).
    assert "LEFT JOIN shiny_link l" in sql
    # On and linked, but the channel is not ready yet.
    assert "COALESCE(l.status, '') = 'linked' AND NOT ((l.channel_id IS NOT NULL" in sql
    assert "l.channel_intro_message_id IS NOT NULL" in sql
    # Off (or held, or gone), but the channel is not archived yet.
    assert (
        "AND NOT (NOT (p.shiny_access = 1 AND p.shiny_map = 1 AND p.left_at IS NULL) "
        "AND (l.channel_id IS NOT NULL AND l.channel_archived = 0))" in sql
    )


async def test_watermark_is_none_before_the_first_tick(store, db):
    db.get_data_from_database.return_value = [{"shiny_alert_watermark": None}]
    assert await store.watermark() is None


async def test_record_alert_is_true_only_the_first_time(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.record_alert(7, "123", 25, 0) is True
    db.execute_query_to_database.return_value = 0
    assert await store.record_alert(7, "123", 25, 0) is False
    assert "INSERT IGNORE" in db.execute_query_to_database.call_args.args[0]


async def test_owned_shiny_uses_the_players_collection_and_pokedex_form(store, db):
    db.get_data_from_database.return_value = [{"owned": 1}]
    assert await store.owns_shiny(7, 570, 2108) is True
    sql = db.get_data_from_database.call_args.args[0]
    assert "collection_entry" in sql and "c.category = 'shiny'" in sql
    assert "COALESCE((SELECT n.form_id FROM pokemon_name" in sql
    assert "n.form_id IN (%s, 0)" in sql
    assert db.get_data_from_database.call_args.kwargs["params"] == (7, 570, 570, 2108)


async def test_a_missing_shiny_does_not_suppress_the_alert(store):
    assert await store.owns_shiny(7, 25, 0) is False


async def test_a_verdict_is_stored_once(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.set_verdict(7, "123", "shiny") is True
    assert "verdict IS NULL" in db.execute_query_to_database.call_args.args[0]
    db.execute_query_to_database.return_value = 0
    assert await store.set_verdict(7, "123", "not_shiny") is False


async def test_a_linked_player_is_found_by_discord_or_trainer_name(store, db):
    db.get_data_from_database.return_value = [
        {"discord_id": 7, "trainer_name": "Rita99"}
    ]
    assert await store.find_linked("rita99") == {
        "discord_id": 7,
        "trainer_name": "Rita99",
    }
    sql = db.get_data_from_database.call_args.args[0]
    assert (
        "status = 'linked'" in sql and "discord_name" in sql and "trainer_name" in sql
    )
    assert db.get_data_from_database.call_args.kwargs["params"] == ("rita99", "rita99")


async def test_nobody_by_that_name_is_none(store):
    assert await store.find_linked("ninguém") is None


async def test_the_players_channel_is_stored(store, db):
    await store.set_channel(7, 999)
    assert "channel_id" in db.execute_query_to_database.call_args.args[0]
    assert db.execute_query_to_database.call_args.kwargs["params"] == (999, 7)


async def test_find_linked_also_returns_the_channel(store, db):
    await store.find_linked("rita99")
    assert "channel_id" in db.get_data_from_database.call_args.args[0]


async def test_shiny_eligible_reads_the_shared_exact_variant_policy(store, db):
    db.get_data_from_database.return_value = [
        {"pokemon_id": 931, "form_id": 2987, "costume_id": 0},
        {"pokemon_id": 835, "form_id": 3172, "costume_id": 7},
    ]
    assert await store.shiny_eligible() == {(931, 2987, 0), (835, 3172, 7)}
    sql = db.get_data_from_database.call_args.args[0]
    assert "costume_id FROM shiny_eligible_variant" in sql


async def test_discord_switch_filters_channels_and_pilot_dms(store, db):
    await store.recipients([7])
    assert "p.shiny_notify_discord = 1" in db.get_data_from_database.call_args.args[0]
    await store.unannounced()
    assert "p.shiny_notify_discord = 1" in db.get_data_from_database.call_args.args[0]


async def test_rechecks_latest_notification_setting_before_delivery(store, db):
    db.get_data_from_database.return_value = []
    assert await store.notifications_enabled(7) is False
    sql = db.get_data_from_database.call_args.args[0]
    assert (
        "shiny_notify_discord = 1" in sql
        and "shiny_map = 1" in sql
        and "shiny_access = 1" in sql
    )
    db.get_data_from_database.return_value = [{"1": 1}]
    assert await store.notifications_enabled(7) is True


async def test_notification_changes_are_unacknowledged_preferences(store, db):
    await store.notification_changes()
    sql = db.get_data_from_database.call_args.args[0]
    assert "p.shiny_notify_discord <> p.shiny_notify_discord_applied" in sql
    assert "LEFT JOIN shiny_link" in sql and "p.left_at IS NULL" in sql


async def test_claiming_a_change_is_atomic_and_preserves_a_newer_choice(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.claim_notification_change(7, False) is True
    sql = db.execute_query_to_database.call_args.args[0]
    assert "AND shiny_notify_discord = %s AND shiny_notify_discord_applied <> %s" in sql
    assert db.execute_query_to_database.call_args.kwargs["params"] == (0, 7, 0, 0)
    db.execute_query_to_database.return_value = 0
    assert await store.claim_notification_change(7, False) is False


async def test_channels_include_real_setup_progress_and_pending_trainers(store, db):
    await store.channel_players()
    sql = db.get_data_from_database.call_args.args[0]
    assert "l.channel_intro_state" in sql
    assert "link_processing_at > UTC_TIMESTAMP() - INTERVAL 15 MINUTE" in sql
    assert "status = 'linked'" not in sql
    assert "p.shiny_access = 1 AND p.shiny_map = 1" in sql


async def test_intro_state_is_persisted_after_successful_message_update(store, db):
    await store.set_channel_intro_state(7, "processing")
    assert db.execute_query_to_database.call_args.kwargs["params"] == ("processing", 7)


async def test_requeue_clears_old_processing_and_replacement_channel_resets_feedback(
    store, db
):
    await store.request(7, "trainer", "123456789012")
    assert "link_processing_at = NULL" in db.execute_query_to_database.call_args.args[0]
    await store.set_channel(7, 99)
    assert (
        "channel_intro_state = NULL" in db.execute_query_to_database.call_args.args[0]
    )
