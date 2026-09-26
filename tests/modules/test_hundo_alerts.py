"""Settings, confirmation revisions, and eligibility for 100IV alerts."""

import json
from datetime import datetime
from unittest.mock import ANY, AsyncMock, MagicMock

import discord
import pytest

from modules import hundo_alerts
from modules.hundo_alerts import (
    HundoAlerts,
    RULE_COLUMNS,
    TEMPLATE,
    areas_json,
    confirmation_due,
    confirmation_message,
    settled_back,
    default_forms,
    is_active,
    rule_row,
    rules_equal,
    wanted_rules,
)


@pytest.fixture
def hundo_db(monkeypatch):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    connector = MagicMock(return_value=db)
    monkeypatch.setattr(hundo_alerts, "connect", connector)
    return connector, db, cursor


def test_read_players_returns_current_settings_and_closes_connection(hundo_db):
    connector, db, cursor = hundo_db
    rows = [{"discord_id": 1, "hundo_dms": 0, "hundo_settings_revision": 2}]
    cursor.fetchall.return_value = tuple(rows)
    assert HundoAlerts(MagicMock())._read_players() == rows
    connector.assert_called_once_with(hundo_alerts.Config.DB_POGOLEIRIA, dict_rows=True)
    cursor.execute.assert_called_once_with(hundo_alerts._PLAYERS_SQL)
    assert (
        "p.hundo_dms = 1 OR p.hundo_settings_revision > 0" in hundo_alerts._PLAYERS_SQL
    )
    db.close.assert_called_once()


@pytest.mark.parametrize("delivered", [True, False])
@pytest.mark.parametrize("affected", [0, 1])
def test_record_only_acknowledges_current_unhandled_revision(
    hundo_db, delivered, affected
):
    connector, db, cursor = hundo_db
    cursor.rowcount = affected
    assert HundoAlerts(MagicMock())._record(123, 7, delivered=delivered) is (
        affected == 1
    )
    prefix = "hundo_confirmed" if delivered else "hundo_dm_refused"
    cursor.execute.assert_called_once_with(
        f"UPDATE trade_player SET {prefix}_revision = %s,"
        f" {prefix}_at = NOW(6) WHERE discord_id = %s"
        " AND hundo_settings_revision = %s"
        " AND GREATEST(hundo_confirmed_revision, hundo_dm_refused_revision) < %s",
        (7, 123, 7, 7),
    )
    connector.assert_called_once_with(
        hundo_alerts.Config.DB_POGOLEIRIA, autocommit=True
    )
    db.close.assert_called_once()


def test_delivery_stores_the_settings_it_confirmed(hundo_db):
    _, _, cursor = hundo_db
    cursor.rowcount = 1
    assert HundoAlerts(MagicMock())._record(
        123, 7, delivered=True, settings=(1, "leiria")
    )
    sql, params = cursor.execute.call_args.args
    assert ", hundo_confirmed_setting = %s WHERE" in sql
    assert params == (7, 1, 123, 7, 7)  # Leiria only = 1


def test_refusal_never_touches_the_confirmed_settings(hundo_db):
    _, _, cursor = hundo_db
    HundoAlerts(MagicMock())._record(123, 7, delivered=False, settings=(1, "leiria"))
    assert "hundo_confirmed_setting" not in cursor.execute.call_args.args[0]


@pytest.mark.parametrize("operation", ["read", "record"])
def test_database_failure_propagates_and_closes_connection(hundo_db, operation):
    _, db, cursor = hundo_db
    cursor.execute.side_effect = RuntimeError("database unavailable")
    alerts = HundoAlerts(MagicMock())
    with pytest.raises(RuntimeError, match="database unavailable"):
        if operation == "read":
            alerts._read_players()
        else:
            alerts._record(123, 7, delivered=True)
    db.close.assert_called_once()


def player(**patch):
    return {
        "discord_id": 1,
        "display_name": "Rui",
        "show_costumes": 1,
        "hundo_areas": "leiria,marinha",
        "collecting": "hundo,shiny",
        "left_at": None,
        "hundo_dms": 1,
        "hundo_settings_revision": 1,
        "hundo_confirmed_revision": 1,
        "hundo_dm_refused_revision": 0,
        **patch,
    }


@pytest.mark.parametrize(
    "settings,confirmed,refused,due",
    [
        (0, 0, 0, False),  # Untouched settings.
        (1, 0, 0, True),  # First opt-in.
        (1, 1, 0, False),  # Already delivered.
        (1, 0, 1, False),  # Refusal is not retried automatically.
        (2, 1, 0, True),  # Changed settings after delivery.
        (2, 0, 1, True),  # Explicit retry after refusal.
        (2, 1, 2, False),  # New revision was refused.
        (3, 1, 2, True),  # Must exceed both recorded outcomes.
    ],
)
def test_confirmation_due(settings, confirmed, refused, due):
    assert (
        confirmation_due(
            player(
                hundo_settings_revision=settings,
                hundo_confirmed_revision=confirmed,
                hundo_dm_refused_revision=refused,
            )
        )
        is due
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"hundo_dms": 0},
        {"collecting": "shiny"},
        {"collecting": "not_hundo"},
        {"collecting": ""},
        {"collecting": None},
        {"left_at": datetime(2026, 9, 24)},
        {"hundo_settings_revision": 0, "hundo_confirmed_revision": 0},
        {"hundo_confirmed_revision": 0},
        {"hundo_settings_revision": 2},
        {"hundo_dm_refused_revision": 1},
    ],
)
def test_ineligible_or_unconfirmed_player_is_inactive(patch):
    assert not is_active(player(**patch))


def test_confirmed_collector_is_active():
    assert is_active(player())


def test_successful_retry_supersedes_old_refusal_even_with_identical_timestamps():
    stamp = datetime(2026, 9, 24, 12)
    row = player(
        hundo_settings_revision=2,
        hundo_confirmed_revision=1,
        hundo_dm_refused_revision=1,
        hundo_settings_at=stamp,
        hundo_confirmed_at=stamp,
        hundo_dm_refused_at=stamp,
    )
    assert confirmation_due(row)
    assert not is_active(row)

    row["hundo_confirmed_revision"] = 2
    assert not confirmation_due(row)
    assert is_active(row)


def test_old_delivery_does_not_acknowledge_a_new_settings_revision():
    row = player(hundo_settings_revision=3, hundo_confirmed_revision=2)
    assert confirmation_due(row)
    assert not is_active(row)


def test_off_setting_can_need_confirmation_without_activating_alerts():
    row = player(hundo_dms=0, hundo_settings_revision=2)
    assert confirmation_due(row)
    assert not is_active(row)


@pytest.mark.parametrize(
    "setting,geofences,zones",
    [
        ("leiria", ["leiria"], "Zona ativa: **Leiria**."),
        ("marinha", ["marinhagrande"], "Zona ativa: **Marinha Grande**."),
        (
            "leiria,marinha",
            ["leiria", "marinhagrande"],
            "Zonas ativas: **Leiria** e **Marinha Grande**.",
        ),
        (
            "marinha,leiria",
            ["leiria", "marinhagrande"],
            "Zonas ativas: **Leiria** e **Marinha Grande**.",
        ),
        (
            "",
            ["leiria", "marinhagrande"],
            "Zonas ativas: **Leiria** e **Marinha Grande**.",
        ),
        (
            None,
            ["leiria", "marinhagrande"],
            "Zonas ativas: **Leiria** e **Marinha Grande**.",
        ),
    ],
)
def test_first_switch_on_names_the_zones_and_what_comes(setting, geofences, zones):
    assert json.loads(areas_json(setting)) == geofences
    row = player(hundo_areas=setting, hundo_confirmed_setting=None)
    assert confirmation_message(row, 1499).split("\n") == [
        "💯 **Alertas de 100IV ligados.**",
        zones,
        "Quando aparecer um 100IV que te falta na Pokédex, avisamos-te aqui.",
        "Faltam-te 1\u00a0499 na Pokédex de 100IV.",
        "-# Para mudar as zonas ou desligar: "
        "pogoleiria.pt/pokedex → Perfil e outras opções.",
    ]


def test_switch_on_without_a_count_leaves_it_out():
    text = confirmation_message(player(hundo_confirmed_setting=0), None)
    assert text.startswith("💯 **Alertas de 100IV ligados.**")
    assert "Faltam-te" not in text


@pytest.mark.parametrize(
    "before,after,headline,zones",
    [
        (
            1,
            "leiria,marinha",
            "📍 **Começaste a seguir também Marinha Grande.**",
            "Zonas ativas: **Leiria** e **Marinha Grande**.",
        ),
        (
            3,
            "leiria",
            "📍 **Deixaste de seguir Marinha Grande.**",
            "Zona ativa: **Leiria**.",
        ),
        (
            1,
            "marinha",
            "📍 **Trocaste Leiria por Marinha Grande.**",
            "Zona ativa: **Marinha Grande**.",
        ),
    ],
)
def test_zone_change_says_what_changed(before, after, headline, zones):
    row = player(hundo_areas=after, hundo_confirmed_setting=before)
    assert confirmation_message(row, 1499).split("\n") == [
        headline,
        zones,
        "-# Para mudar as zonas ou desligar: "
        "pogoleiria.pt/pokedex → Perfil e outras opções.",
    ]


@pytest.mark.parametrize("setting", ["leiria", "marinha", "leiria,marinha", "", None])
def test_off_confirmation_does_not_depend_on_area(setting):
    row = player(hundo_dms=0, hundo_areas=setting, hundo_confirmed_setting=3)
    assert confirmation_message(row).split("\n") == [
        "🔕 **Alertas de 100IV desligados.**",
        "Já não vais receber aqui os 100IV que te faltam.",
        "-# Para voltar a ligar: pogoleiria.pt/pokedex → Perfil e outras opções.",
    ]


def test_back_on_after_off_reads_as_switched_on():
    row = player(hundo_confirmed_setting=0)
    assert confirmation_message(row).startswith("💯 **Alertas de 100IV ligados.**")


@pytest.mark.parametrize(
    "dms,areas,code",
    [
        (0, "leiria,marinha", 0),
        (1, "leiria", 1),
        (1, "marinha", 2),
        (1, "leiria,marinha", 3),
        (1, "", 3),
    ],
)
def test_setting_code_is_zero_off_then_zone_bits(dms, areas, code):
    assert hundo_alerts.setting_code(dms, areas) == code


def settled(**patch):
    # Confirmed Leiria at revision 1, then changed and changed back: revision 3.
    return player(
        **{
            "hundo_areas": "leiria",
            "hundo_settings_revision": 3,
            "hundo_confirmed_revision": 1,
            "hundo_confirmed_setting": 1,
            **patch,
        }
    )


def test_change_undone_before_its_dm_is_settled_and_stays_active():
    assert confirmation_due(settled()) and settled_back(settled())
    assert is_active(settled())


@pytest.mark.parametrize(
    "patch",
    [
        {"hundo_areas": "leiria,marinha"},  # really changed
        {"hundo_dms": 0},  # switched off
        {"hundo_confirmed_setting": None},  # unknown
        {"hundo_dm_refused_revision": 2},  # a DM was refused since
        {"hundo_confirmed_revision": 0},  # never confirmed
    ],
)
def test_not_settled_when_anything_differs(patch):
    assert not settled_back(settled(**patch))


def test_settled_player_off_is_not_active():
    row = settled(hundo_dms=0, hundo_confirmed_setting=0)
    assert settled_back(row) and not is_active(row)


def test_default_forms_reads_species_and_default_form_ids():
    assert default_forms(
        {
            "19": {"defaultFormId": 45, "forms": {"45": {}, "46": {}}},
            "25": {"defaultFormId": "598"},
            "132": {},
        }
    ) == {19: 45, 25: 598, 132: 0}


@pytest.mark.parametrize("default", [None, 0, "0"])
def test_species_with_alternative_forms_requires_a_usable_default(default):
    forms = default_forms(
        {"19": {"defaultFormId": default, "forms": {"46": {"name": "Alola"}}}}
    )
    assert forms == {19: None}
    assert wanted_rules([(19, 0), (19, 46)], forms) == {(19, 46)}


def test_missing_ordinary_tile_uses_exact_default_form():
    assert wanted_rules([(19, 0)], {19: 45}) == {(19, 45)}


def test_owned_alolan_form_is_not_added_for_missing_ordinary_tile():
    # Only the ordinary tile is missing: no wildcard or Alolan rule is wanted.
    forms = default_forms({"19": {"defaultFormId": 45, "forms": {"46": {}}}})
    assert wanted_rules([(19, 0)], forms) == {(19, 45)}


def test_named_forms_keep_their_ids():
    assert wanted_rules([(19, 46), (25, 2332)], {19: 45, 25: 598}) == {
        (19, 46),
        (25, 2332),
    }


def test_known_species_without_forms_can_use_zero():
    assert wanted_rules([(132, 0)], default_forms({"132": {}})) == {(132, 0)}


def test_unresolved_ordinary_tile_is_skipped_not_widened_to_wildcard():
    # Ogerpon: defaultFormId 0 with named forms. Its ordinary tile must not
    # become form 0 (every form), nor block the other rules.
    forms = default_forms(
        {"1017": {"defaultFormId": 0, "forms": {"0": {}, "2875": {}}}, "132": {}}
    )
    assert wanted_rules([(132, 0), (1017, 0), (1017, 2875)], forms) == {
        (132, 0),
        (1017, 2875),
    }
    assert wanted_rules([(132, 0), (19, 0)], {132: 0}) == {(132, 0)}


def test_named_tile_does_not_require_ordinary_form_mapping():
    assert wanted_rules([(19, 46)], {}) == {(19, 46)}


def test_duplicate_tiles_produce_one_rule_pair():
    assert wanted_rules([(19, 0), (19, 0), (19, 45)], {19: 45}) == {(19, 45)}


def test_no_missing_tiles_needs_no_form_mapping():
    assert wanted_rules([], {}) == set()
    assert default_forms({}) == {}


def test_rule_targets_exact_form_area_and_current_profile():
    row = rule_row(123, 19, 45, areas_json("marinha"), 2)
    assert row["id"] == "123"
    assert row["template"] == TEMPLATE == "pokedex-100iv"
    assert (row["pokemon_id"], row["form"]) == (19, 45)
    assert (row["min_iv"], row["max_iv"]) == (100, 100)
    assert row["profile_no"] == 2
    assert row["distance"] == 0
    assert json.loads(row["override_areas"]) == ["marinhagrande"]
    assert row["clean"] == 0
    assert row["ping"] == ""


def test_rule_defaults_are_independent_between_calls():
    first = rule_row(1, 19, 45, areas_json("leiria"), 2)
    first["min_iv"] = 0
    assert rule_row(2, 19, 45, areas_json("marinha"), 3)["min_iv"] == 100


@pytest.mark.parametrize("column", RULE_COLUMNS)
def test_comparison_detects_every_managed_field_change(column):
    wanted = rule_row(1, 19, 45, areas_json("leiria"), 2)
    previous = wanted[column]
    changed = (
        '["marinhagrande"]'
        if column == "override_areas"
        else previous + 1 if isinstance(previous, int) else "changed"
    )
    assert not rules_equal([{**wanted, column: changed}], [wanted])


def test_comparison_ignores_generated_uid_row_order_and_area_order():
    first = rule_row(1, 19, 45, areas_json("leiria,marinha"), 2)
    second = rule_row(1, 19, 46, areas_json("leiria,marinha"), 2)
    current = [
        {**second, "uid": 72},
        {**first, "uid": 71, "override_areas": '["marinhagrande","leiria"]'},
    ]
    assert rules_equal(current, [first, second])


def test_comparison_detects_duplicate_rows():
    row = rule_row(1, 19, 45, areas_json("leiria"), 2)
    assert not rules_equal([row, row], [row])


@pytest.mark.parametrize("areas", [None, "broken", "null", "{}", '"leiria"', "[1]"])
def test_comparison_rebuilds_malformed_area_overrides(areas):
    wanted = rule_row(1, 19, 45, areas_json("leiria"), 2)
    assert not rules_equal([{**wanted, "override_areas": areas}], [wanted])


def test_comparison_rebuilds_when_managed_field_is_missing():
    wanted = rule_row(1, 19, 45, areas_json("leiria"), 2)
    current = dict(wanted)
    del current["max_iv"]
    assert not rules_equal([current], [wanted])


def test_comparison_handles_empty_current_and_wanted_rules():
    row = rule_row(1, 19, 45, areas_json("leiria"), 2)
    assert rules_equal([], [])
    assert not rules_equal([], [row])
    assert not rules_equal([row], [])


def human(**patch):
    return {
        "id": "1",
        "type": "discord:user",
        "enabled": 1,
        "admin_disable": 0,
        "current_profile_no": 2,
        **patch,
    }


def sender(players):
    bot = MagicMock()
    bot.quest_search.masterfile_data = {"pokemon": {"19": {"defaultFormId": 45}}}
    bot.poracle.reload = AsyncMock()
    bot.poracle.health = AsyncMock()
    bot.poracle.create_user = AsyncMock()
    bot.get_user.return_value.send = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=bot.get_user.return_value)
    alerts = HundoAlerts(bot)
    alerts._read_players = MagicMock(return_value=players)
    alerts._cleanup = MagicMock(return_value=False)
    alerts._human = MagicMock(return_value=human())
    alerts._sync_player = MagicMock(return_value=False)
    alerts._record = MagicMock(return_value=True)
    alerts._missing_count = MagicMock(return_value=1499)
    alerts._record_health = MagicMock()

    async def deliver(row, embed):
        user = bot.get_user(int(row["discord_id"]))
        if user is None:
            user = await bot.fetch_user(int(row["discord_id"]))
        await user.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True

    alerts._deliver_confirmation = deliver
    return alerts, bot


async def test_transient_confirmation_does_not_block_other_users_or_cleanup():
    first = player(hundo_settings_revision=2)
    alerts, bot = sender([first, player(discord_id=2)])
    bot.get_user.return_value.send.side_effect = discord.HTTPException(
        MagicMock(status=503), "unavailable"
    )
    await alerts.tick()
    alerts._record.assert_not_called()
    assert alerts._cleanup.call_count == 2
    assert alerts._sync_player.call_args.args[0]["discord_id"] == 2


async def test_confirmation_rereads_newer_settings_before_sync():
    old = player(hundo_settings_revision=2)
    new = player(hundo_settings_revision=3)
    alerts, _ = sender([old])
    alerts._read_players.side_effect = [[old], [new]]
    alerts._record.return_value = False  # conditional UPDATE rejected old revision
    await alerts.tick()
    alerts._record.assert_called_once_with(1, 2, delivered=True, settings=ANY)
    alerts._sync_player.assert_not_called()


async def test_confirmed_current_revision_can_sync_in_same_tick():
    old = player(hundo_settings_revision=2)
    alerts, _ = sender([old])
    alerts._read_players.side_effect = [
        [old],
        [player(hundo_settings_revision=2, hundo_confirmed_revision=2)],
    ]
    await alerts.tick()
    alerts._sync_player.assert_called_once()
    assert alerts._sync_player.call_args.args[2] == 2  # existing active profile


@pytest.mark.parametrize("error", [discord.Forbidden, discord.NotFound])
async def test_refusal_records_exact_revision(error):
    alerts, bot = sender([player(hundo_settings_revision=2)])
    bot.get_user.return_value.send.side_effect = error(MagicMock(status=403), "closed")
    await alerts.tick()
    alerts._record.assert_called_once_with(1, 2, delivered=False, settings=ANY)
    alerts._sync_player.assert_not_called()
    assert alerts._cleanup.call_count == 2


async def test_missing_masterfile_still_cleans_and_reloads():
    alerts, bot = sender([player(hundo_dms=0)])
    bot.quest_search.masterfile_data = {}
    alerts._cleanup.side_effect = [True, False]
    await alerts.tick()
    assert alerts._cleanup.call_count == 2
    alerts._sync_player.assert_not_called()
    bot.poracle.reload.assert_awaited_once()


async def test_failed_creation_does_not_block_other_users():
    alerts, bot = sender([player(), player(discord_id=2)])
    alerts._human.side_effect = [None, human(id="2")]
    bot.poracle.create_user.side_effect = RuntimeError("API unavailable")
    await alerts.tick()
    assert alerts._sync_player.call_args.args[0]["discord_id"] == 2
    assert alerts._cleanup.call_count == 2


async def test_creation_supplies_area_and_does_not_start_or_patch_user():
    alerts, bot = sender([player()])
    alerts._human.side_effect = [None, human()]
    await alerts.tick()
    bot.poracle.create_user.assert_awaited_once_with(
        1, "Rui", area=areas_json("leiria,marinha")
    )
    bot.poracle.start.assert_not_called()
    alerts._sync_player.assert_called_once()


@pytest.mark.parametrize("patch", [{"enabled": 0}, {"admin_disable": 1}])
async def test_stopped_user_is_never_reenabled(patch):
    alerts, bot = sender([player()])
    alerts._human.return_value = human(**patch)
    await alerts.tick()
    bot.poracle.create_user.assert_not_awaited()
    bot.poracle.start.assert_not_called()
    alerts._sync_player.assert_not_called()


async def test_failed_reload_is_retried_without_new_changes():
    alerts, bot = sender([])
    alerts._cleanup.side_effect = [True, False, False, False]
    bot.poracle.reload.side_effect = [
        RuntimeError("unavailable"),
        RuntimeError("still down"),
        None,
    ]
    await alerts.tick()
    assert alerts._reload_pending
    await alerts.tick()
    assert not alerts._reload_pending
    assert bot.poracle.reload.await_count == 3


async def test_player_failure_does_not_prevent_committed_cleanup_reload():
    alerts, bot = sender([player(), player(discord_id=2)])
    alerts._cleanup.side_effect = [True, False]
    alerts._sync_player.side_effect = [RuntimeError("insert failed"), True]
    await alerts.tick()
    assert alerts._sync_player.call_count == 2
    assert bot.poracle.reload.await_count == 2  # removals first, then new rules


async def test_read_failure_still_runs_final_cleanup_and_pending_reload():
    alerts, bot = sender([])
    alerts._cleanup.side_effect = [True, RuntimeError("cleanup failed")]
    alerts._read_players.side_effect = RuntimeError("read failed")
    with pytest.raises(RuntimeError, match="read failed"):
        await alerts.tick()
    bot.poracle.reload.assert_awaited_once()


def connection(rows=(), rowcount=0):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = rows
    cursor.rowcount = rowcount
    return db, cursor


def test_record_is_conditional_on_captured_revision(monkeypatch):
    db, cursor = connection(rowcount=0)
    monkeypatch.setattr(hundo_alerts, "connect", MagicMock(return_value=db))
    alerts = HundoAlerts(MagicMock())
    assert not alerts._record(1, 7, delivered=True)
    sql, params = cursor.execute.call_args.args
    assert "hundo_settings_revision = %s" in sql
    assert "GREATEST(hundo_confirmed_revision" in sql
    assert params == (7, 1, 7, 7)
    db.close.assert_called_once()


@pytest.mark.parametrize(
    "current_kind",
    ["same", "empty", "wrong_iv", "duplicate", "wrong_profile", "wrong_area"],
)
def test_real_sync_helper_compares_and_writes_full_rows(monkeypatch, current_kind):
    wanted = rule_row(1, 19, 45, areas_json("leiria,marinha"), 2)
    current = {
        "same": [wanted],
        "empty": [],
        "wrong_iv": [{**wanted, "min_iv": 0}],
        "duplicate": [wanted, wanted],
        "wrong_profile": [{**wanted, "profile_no": 1}],
        "wrong_area": [{**wanted, "override_areas": '["leiria"]'}],
    }[current_kind]
    pogo, read = connection([{"pokemon_id": 19, "form_id": 0}])
    poracle, write = connection(current)
    monkeypatch.setattr(hundo_alerts, "connect", MagicMock(side_effect=[pogo, poracle]))
    alerts = HundoAlerts(MagicMock())
    assert alerts._sync_player(player(), {19: 45}, 2) is (current_kind != "same")
    assert read.execute.call_args.args[1] == (1, 1)
    select_sql = write.execute.call_args_list[0].args[0]
    assert all(f"`{column}`" in select_sql for column in RULE_COLUMNS)
    if current_kind == "same":
        write.executemany.assert_not_called()
    else:
        assert write.execute.call_args_list[1].args == (
            "DELETE FROM monsters WHERE id = %s AND template = %s",
            ("1", TEMPLATE),
        )
        assert write.executemany.call_args.args[1] == [
            tuple(wanted[c] for c in RULE_COLUMNS)
        ]
    poracle.commit.assert_called_once()
    poracle.rollback.assert_not_called()
    pogo.close.assert_called_once()
    poracle.close.assert_called_once()


def test_empty_wanted_removes_owned_tile_without_inserting(monkeypatch):
    pogo, _ = connection([])
    poracle, write = connection([rule_row(1, 19, 45, '["leiria"]', 2)])
    monkeypatch.setattr(hundo_alerts, "connect", MagicMock(side_effect=[pogo, poracle]))
    assert HundoAlerts(MagicMock())._sync_player(player(), {19: 45}, 2)
    write.executemany.assert_not_called()
    assert "DELETE" in write.execute.call_args.args[0]


def test_failed_insert_rolls_back_player_transaction(monkeypatch):
    pogo, _ = connection([{"pokemon_id": 19, "form_id": 0}])
    poracle, write = connection([])
    write.executemany.side_effect = RuntimeError("insert failed")
    monkeypatch.setattr(hundo_alerts, "connect", MagicMock(side_effect=[pogo, poracle]))
    with pytest.raises(RuntimeError, match="insert failed"):
        HundoAlerts(MagicMock())._sync_player(player(), {19: 45}, 2)
    poracle.rollback.assert_called_once()
    poracle.commit.assert_not_called()
    poracle.close.assert_called_once()


def test_cleanup_uses_its_own_transaction(monkeypatch):
    db, cursor = connection(rowcount=3)
    monkeypatch.setattr(hundo_alerts, "connect", MagicMock(return_value=db))
    assert HundoAlerts(MagicMock())._cleanup()
    cursor.execute.assert_called_once_with(hundo_alerts._CLEANUP_SQL, (TEMPLATE,))
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


async def test_unchanged_tick_does_not_reload():
    alerts, bot = sender([player()])
    await alerts.tick()
    bot.poracle.reload.assert_not_awaited()


async def test_off_confirmation_and_cleanup():
    alerts, bot = sender([player(hundo_dms=0, hundo_settings_revision=2)])
    await alerts.tick()
    assert (
        "desligados" in bot.get_user.return_value.send.call_args.kwargs["embed"].title
    )
    alerts._record.assert_called_once_with(1, 2, delivered=True, settings=ANY)
    alerts._sync_player.assert_not_called()
    assert alerts._cleanup.call_count == 2


async def test_departed_player_does_not_receive_confirmation():
    alerts, bot = sender([player(left_at="left", hundo_settings_revision=2)])
    await alerts.tick()
    bot.get_user.return_value.send.assert_not_awaited()
    alerts._record.assert_not_called()
    assert alerts._cleanup.call_count == 2


async def test_fetch_user_when_not_cached():
    alerts, bot = sender([player(hundo_settings_revision=2)])
    bot.get_user.return_value = None
    await alerts.tick()
    bot.fetch_user.assert_awaited_once_with(1)
    bot.fetch_user.return_value.send.assert_awaited_once()
    alerts._record.assert_called_once_with(1, 2, delivered=True, settings=ANY)


async def test_stopped_user_logging_resets_after_restart():
    alerts, bot = sender([player()])
    alerts._human.side_effect = [
        human(enabled=0),
        human(enabled=0),
        human(),
        human(enabled=0),
    ]
    for _ in range(4):
        await alerts.tick()
    stops = [
        c.args[0]
        for c in bot.utility.log_to_file.call_args_list
        if "is stopped" in c.args[0]
    ]
    assert len(stops) == 2
    bot.poracle.start.assert_not_called()


def test_cleanup_failure_rolls_back(monkeypatch):
    db, cursor = connection()
    cursor.execute.side_effect = RuntimeError("DB failed")
    monkeypatch.setattr(hundo_alerts, "connect", MagicMock(return_value=db))
    with pytest.raises(RuntimeError, match="DB failed"):
        HundoAlerts(MagicMock())._cleanup()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_called_once()


async def test_unchanged_player_is_not_rebuilt_every_tick():
    alerts, _ = sender([player(hundo_ticks=3, hundo_ticked_at="t1")])
    alerts._sync_player.return_value = True
    await alerts.tick()
    await alerts.tick()
    assert alerts._sync_player.call_count == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"hundo_ticks": 4},  # a tick or an untick
        {"hundo_ticked_at": "t2"},  # untick one, tick another
        {"hundo_tiles_fingerprint": 123},  # same-second swap, same count/time
        {"hundo_settings_revision": 2, "hundo_confirmed_revision": 2},
        {"show_costumes": 0},
    ],
)
async def test_any_input_change_rebuilds(patch):
    before = player(hundo_ticks=3, hundo_ticked_at="t1")
    alerts, _ = sender([before])
    await alerts.tick()
    alerts._read_players.return_value = [{**before, **patch}]
    await alerts.tick()
    assert alerts._sync_player.call_count == 2


async def test_profile_switch_masterfile_reload_and_cleanup_rebuild():
    alerts, bot = sender([player()])
    await alerts.tick()
    alerts._human.return_value = human(current_profile_no=3)
    await alerts.tick()
    bot.quest_search.masterfile_data = {
        **bot.quest_search.masterfile_data,
        "date": "new",
    }
    await alerts.tick()
    alerts._cleanup.side_effect = [True, False]
    await alerts.tick()
    assert alerts._sync_player.call_count == 4


async def test_unchanged_player_is_still_rebuilt_hourly(monkeypatch):
    clock = iter([0.0, 10.0, 4000.0, 4000.0])
    monkeypatch.setattr(hundo_alerts, "_monotonic", lambda: next(clock))
    alerts, _ = sender([player()])
    await alerts.tick()  # sync, stamped 0
    await alerts.tick()  # 10s later: skipped
    await alerts.tick()  # past the hour: rebuilt, stamped 4000
    assert alerts._sync_player.call_count == 2


async def test_failed_sync_is_retried_next_tick():
    alerts, _ = sender([player()])
    alerts._sync_player.side_effect = [RuntimeError("down"), False]
    await alerts.tick()
    await alerts.tick()
    assert alerts._sync_player.call_count == 2


async def test_player_who_leaves_and_returns_is_rebuilt():
    row = player()
    alerts, _ = sender([row])
    await alerts.tick()
    alerts._read_players.return_value = [{**row, "hundo_dms": 0}]
    await alerts.tick()
    alerts._read_players.return_value = [row]
    await alerts.tick()
    assert alerts._sync_player.call_count == 2


async def test_change_undone_before_the_dm_is_acknowledged_silently():
    row = settled()
    alerts, bot = sender([row])
    await alerts.tick()
    bot.get_user.return_value.send.assert_not_awaited()
    alerts._record.assert_called_once_with(1, 3, delivered=True, settings=(1, "leiria"))


async def test_real_change_dm_says_what_changed_and_stores_it():
    row = settled(hundo_areas="leiria,marinha")
    alerts, bot = sender([row])
    await alerts.tick()
    embed = bot.get_user.return_value.send.call_args.kwargs["embed"]
    assert embed.title == "📍 Começaste a seguir também Marinha Grande."
    assert "Marinha Grande" in embed.description
    assert "pogoleiria.pt/pokedex" in embed.footer.text
    assert not bot.get_user.return_value.send.call_args.args
    alerts._record.assert_called_once_with(
        1, 3, delivered=True, settings=(1, "leiria,marinha")
    )


async def test_switch_on_dm_carries_the_missing_count():
    alerts, bot = sender(
        [player(hundo_settings_revision=2, hundo_confirmed_setting=None)]
    )
    await alerts.tick()
    assert (
        "Faltam-te 1\u00a0499"
        in bot.get_user.return_value.send.call_args.kwargs["embed"].description
    )


async def test_count_failure_still_confirms():
    alerts, bot = sender(
        [player(hundo_settings_revision=2, hundo_confirmed_setting=None)]
    )
    alerts._missing_count.side_effect = RuntimeError("db down")
    await alerts.tick()
    embed = bot.get_user.return_value.send.call_args.kwargs["embed"]
    assert embed.title.startswith("💯") and "Faltam-te" not in embed.description
    alerts._record.assert_called_once()


async def test_dm_waits_out_the_quiet_period():
    row = player(
        hundo_settings_revision=2, hundo_confirmed_setting=None, hundo_settling=1
    )
    alerts, bot = sender([row])
    await alerts.tick()
    bot.get_user.return_value.send.assert_not_awaited()
    alerts._record.assert_not_called()
    alerts._read_players.return_value = [{**row, "hundo_settling": 0}]
    await alerts.tick()
    bot.get_user.return_value.send.assert_awaited_once()


async def test_undo_is_acknowledged_even_inside_the_quiet_period():
    alerts, bot = sender([settled(hundo_settling=1)])
    await alerts.tick()
    bot.get_user.return_value.send.assert_not_awaited()
    alerts._record.assert_called_once()


@pytest.mark.parametrize("refused", [False, True])
async def test_acknowledgement_failure_retries_database_without_resending_dm(refused):
    row = player(hundo_settings_revision=2)
    alerts, bot = sender([row])
    if refused:
        bot.get_user.return_value.send.side_effect = discord.Forbidden(
            MagicMock(status=403), "closed"
        )
    alerts._record.side_effect = [RuntimeError("database down"), True]
    await alerts.tick()
    await alerts.tick()
    bot.get_user.return_value.send.assert_awaited_once()
    assert alerts._record.call_count == 2
    assert alerts._record.call_args.kwargs["delivered"] is not refused


async def test_failed_acknowledgement_does_not_hide_a_new_settings_revision():
    alerts, bot = sender([player(hundo_settings_revision=2)])
    alerts._record.side_effect = [RuntimeError("database down"), True]
    await alerts.tick()
    alerts._read_players.return_value = [
        player(hundo_settings_revision=3, hundo_areas="leiria")
    ]
    await alerts.tick()
    assert bot.get_user.return_value.send.await_count == 2
    assert alerts._record.call_args.args == (1, 3)


async def test_recreated_human_rebuilds_even_with_an_unchanged_signature():
    alerts, bot = sender([player()])
    await alerts.tick()
    alerts._human.side_effect = [None, human()]
    await alerts.tick()
    assert alerts._sync_player.call_count == 2
    bot.poracle.create_user.assert_awaited_once()


async def test_cleanup_is_reloaded_before_sending_confirmations():
    alerts, bot = sender([player(hundo_settings_revision=2)])
    alerts._cleanup.side_effect = [True, False]

    async def send(**kwargs):
        bot.poracle.reload.assert_awaited_once()

    bot.get_user.return_value.send.side_effect = send
    await alerts.tick()
    alerts._record.assert_called_once()


@pytest.mark.parametrize(
    "human_patch,health",
    [({}, "ready"), ({"enabled": 0}, "stopped"), ({"admin_disable": 1}, "stopped")],
)
async def test_health_reflects_actual_poracle_destination(human_patch, health):
    alerts, _ = sender([player()])
    alerts._human.return_value = human(**human_patch)
    await alerts.tick()
    assert alerts._record_health.call_args.args[1] == health


async def test_unreachable_poracle_is_not_reported_healthy():
    alerts, bot = sender([player()])
    bot.poracle.health.side_effect = RuntimeError("offline")
    await alerts.tick()
    assert alerts._record_health.call_args.args[1] == "unavailable"


async def test_failed_sync_is_not_reported_healthy():
    alerts, _ = sender([player()])
    alerts._sync_player.side_effect = RuntimeError("DB down")
    await alerts.tick()
    assert alerts._record_health.call_args.args[1] == "error"


async def test_uncertain_confirmation_is_not_acknowledged_or_activated():
    alerts, _ = sender([player(hundo_settings_revision=2)])
    alerts._deliver_confirmation = AsyncMock(return_value=None)
    await alerts.tick()
    alerts._record.assert_not_called()
    alerts._sync_player.assert_not_called()
