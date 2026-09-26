"""The 100IV alerts' SQL against a real, disposable MariaDB.

The unit tests fake every connection, so they cannot show that MariaDB
accepts these statements, enforces the column types, or compares ids across
the pogoleiria (general_ci) and poracle (unicode_ci) collations. These run
the module's real helpers, unchanged, against the live DDL
(hundo_schema.sql). Every test drops and recreates the three schemas, so the
server must be a throwaway one:

    docker run -d --rm --name hundo-sql-test -p 127.0.0.1:33991:3306 \\
      -e MARIADB_ROOT_PASSWORD=disposable -e MARIADB_DATABASE=hundo_disposable \\
      mariadb:10.11 --character-set-server=utf8mb4 \\
      --collation-server=utf8mb4_unicode_ci
    HUNDO_SQL_TEST_PORT=33991 HUNDO_SQL_TEST_PASSWORD=disposable \\
      python3 -m pytest tests/integration -q --no-cov

Skipped when HUNDO_SQL_TEST_PORT is unset. Refuses any server without the
`hundo_disposable` marker schema or with a scanner schema on it.
"""

import os
from pathlib import Path

import pymysql
import pytest

from modules import hundo_alerts
from modules.config import Config
from modules.hundo_alerts import (
    RULE_COLUMNS,
    TEMPLATE,
    HundoAlerts,
    areas_json,
    confirmation_due,
    is_active,
    rule_row,
)
from modules.migrations import split_statements

# The conftest patches pymysql.connect for every test; keep the real one.
_REAL_CONNECT = pymysql.connect
_PORT = os.environ.get("HUNDO_SQL_TEST_PORT")
_SCHEMA = Path(__file__).with_name("hundo_schema.sql")
_MIGRATIONS = [
    Path(__file__).parents[2] / "migrations" / name
    for name in (
        "014_add_hundo_dms.sql",
        "015_add_hundo_confirmed_settings.sql",
        "016_hundo_delivery_recovery.sql",
    )
]
_REFUSE = {"golbat", "dragonite", "reactmap", "koji", "stats", "fletchling"}

pytestmark = pytest.mark.skipif(
    not _PORT, reason="HUNDO_SQL_TEST_PORT unset: no disposable MariaDB"
)

# Settings writer and explicit retry, as the plan hands them to the site.
_SAVE_SQL = """
UPDATE trade_player
SET hundo_dms = %s, hundo_areas = %s,
    hundo_settings_revision = hundo_settings_revision + 1,
    hundo_settings_at = NOW(6)
WHERE discord_id = %s
  AND NOT (hundo_dms <=> %s AND hundo_areas <=> %s)
"""
_RETRY_SQL = """
UPDATE trade_player
SET hundo_settings_revision = hundo_settings_revision + 1,
    hundo_settings_at = NOW(6)
WHERE discord_id = %s AND left_at IS NULL
"""

FORMS = {19: 45, 132: 0}


def _root(database=None, **extra):
    return _REAL_CONNECT(
        host="127.0.0.1",
        port=int(_PORT),
        user="root",
        password=os.environ.get("HUNDO_SQL_TEST_PASSWORD", ""),
        database=database,
        autocommit=True,
        **extra,
    )


def _run(sql, params=None, database=None):
    db = _root(database, cursorclass=pymysql.cursors.DictCursor)
    try:
        with db.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.rowcount, list(cursor.fetchall())
    finally:
        db.close()


def _migrate():
    for migration in _MIGRATIONS:
        for statement in split_statements(migration.read_text()):
            _run(statement)


@pytest.fixture(autouse=True)
def disposable(monkeypatch):
    monkeypatch.setattr(pymysql, "connect", _REAL_CONNECT)
    _, schemas = _run("SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA")
    names = {row["name"] for row in schemas}
    if "hundo_disposable" not in names or names & _REFUSE:
        pytest.fail(f"Not a disposable test server: schemas {sorted(names)}")
    for name in ("pogoleiria", "poliswag", "poracle"):
        _run(f"DROP DATABASE IF EXISTS {name}")
    db = _root()
    try:
        with db.cursor() as cursor:
            for statement in split_statements(_SCHEMA.read_text()):
                cursor.execute(statement)
    finally:
        db.close()
    monkeypatch.setattr(Config, "DB_HOST", "127.0.0.1")
    monkeypatch.setattr(Config, "DB_PORT", int(_PORT))
    monkeypatch.setattr(Config, "DB_USER", "root")
    monkeypatch.setattr(
        Config, "DB_PASSWORD", os.environ.get("HUNDO_SQL_TEST_PASSWORD", "")
    )
    monkeypatch.setattr(Config, "DB_POGOLEIRIA", "pogoleiria")


def add_player(discord_id, **columns):
    row = {
        "discord_id": discord_id,
        "username": f"u{discord_id}",
        "display_name": f"Player {discord_id}",
        "code_hash": f"{discord_id:064d}",
        "code_issued_at": "2026-09-01 00:00:00",
        "collecting": "hundo",
        **columns,
    }
    names = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    _run(
        f"INSERT INTO trade_player ({names}) VALUES ({marks})",
        tuple(row.values()),
        "pogoleiria",
    )


def activate(discord_id, areas="leiria,marinha"):
    """Opt in through the site's writer, then a delivered confirmation."""
    _run(_SAVE_SQL, (1, areas, discord_id, 1, areas), "pogoleiria")
    revision = settings(discord_id)["hundo_settings_revision"]
    assert HundoAlerts(None)._record(discord_id, revision, delivered=True)


def add_human(discord_id, **columns):
    row = {
        "id": str(discord_id),
        "type": "discord:user",
        "name": f"Player {discord_id}",
        "area": "[]",
        "community_membership": "[]",
        **columns,
    }
    names = ", ".join(f"`{name}`" for name in row)
    marks = ", ".join(["%s"] * len(row))
    _run(
        f"INSERT INTO humans ({names}) VALUES ({marks})", tuple(row.values()), "poracle"
    )


def add_rule(discord_id, template=TEMPLATE, **patch):
    row = {**rule_row(discord_id, 19, 45, '["leiria"]', 1), "template": template}
    row.update(patch)
    names = ", ".join(f"`{name}`" for name in row)
    marks = ", ".join(["%s"] * len(row))
    _run(
        f"INSERT INTO monsters ({names}) VALUES ({marks})",
        tuple(row.values()),
        "poracle",
    )


def add_names(*tiles):
    for pokemon_id, form_id, costume in tiles:
        _run(
            "INSERT INTO pokemon_name (pokemon_id, form_id, name, is_costume)"
            " VALUES (%s, %s, %s, %s)",
            (pokemon_id, form_id, f"#{pokemon_id}", costume),
            "poliswag",
        )


def tick_off(discord_id, pokemon_id, form_id):
    _run(
        "INSERT INTO collection_entry (discord_id, category, pokemon_id, form_id)"
        " VALUES (%s, 'hundo', %s, %s)",
        (discord_id, pokemon_id, form_id),
        "pogoleiria",
    )


def settings(discord_id):
    return next(
        row
        for row in HundoAlerts(None)._read_players()
        if row["discord_id"] == discord_id
    )


def rules(discord_id=None, template=TEMPLATE):
    columns = ", ".join(f"`{column}`" for column in RULE_COLUMNS)
    sql = f"SELECT {columns} FROM monsters WHERE template <=> %s"
    params = [template]
    if discord_id is not None:
        sql += " AND id = %s"
        params.append(str(discord_id))
    return _run(sql + " ORDER BY id, pokemon_id, form", params, "poracle")[1]


def everything(table):
    return _run(f"SELECT * FROM {table} ORDER BY 1, 2", database="poracle")[1]


def test_migrations_replay_and_add_off_by_default_columns():
    _migrate()
    _migrate()
    _, columns = _run(
        "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type,"
        " COLUMN_DEFAULT AS dflt, IS_NULLABLE AS nullable"
        " FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'pogoleiria'"
        " AND TABLE_NAME = 'trade_player' AND COLUMN_NAME LIKE 'hundo%%'"
        " ORDER BY ORDINAL_POSITION"
    )
    assert {c["name"]: (c["type"], c["dflt"], c["nullable"]) for c in columns} == {
        "hundo_dms": ("tinyint(1)", "0", "NO"),
        "hundo_areas": ("set('leiria','marinha')", "'leiria,marinha'", "NO"),
        "hundo_settings_revision": ("bigint(20) unsigned", "0", "NO"),
        "hundo_settings_at": ("datetime(6)", "NULL", "YES"),
        "hundo_confirmed_revision": ("bigint(20) unsigned", "0", "NO"),
        "hundo_confirmed_at": ("datetime(6)", "NULL", "YES"),
        "hundo_dm_refused_revision": ("bigint(20) unsigned", "0", "NO"),
        "hundo_dm_refused_at": ("datetime(6)", "NULL", "YES"),
        "hundo_confirmed_setting": ("tinyint(3) unsigned", "NULL", "YES"),
        "hundo_health_revision": ("bigint(20) unsigned", "0", "NO"),
        "hundo_health_state": ("varchar(24)", "NULL", "YES"),
        "hundo_health_at": ("datetime(6)", "NULL", "YES"),
    }


def test_only_changes_and_explicit_retry_increment_the_revision():
    _migrate()
    add_player(1)
    save = (1, "leiria", 1, 1, "leiria")
    assert _run(_SAVE_SQL, save, "pogoleiria")[0] == 1
    assert _run(_SAVE_SQL, save, "pogoleiria")[0] == 0  # unchanged save
    assert settings(1)["hundo_settings_revision"] == 1
    assert _run(_RETRY_SQL, (1,), "pogoleiria")[0] == 1
    row = settings(1)
    assert row["hundo_settings_revision"] == 2
    assert (row["hundo_confirmed_revision"], row["hundo_dm_refused_revision"]) == (
        0,
        0,
    )


def test_outcome_for_a_superseded_revision_is_not_recorded():
    _migrate()
    add_player(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    _run(_SAVE_SQL, (1, "marinha", 1, 1, "marinha"), "pogoleiria")  # during DM
    assert not HundoAlerts(None)._record(1, 1, delivered=True)
    row = settings(1)
    assert row["hundo_confirmed_revision"] == 0
    assert confirmation_due(row)


def test_successful_retry_in_the_same_second_activates_after_refusal():
    _migrate()
    add_player(1)
    _run(_SAVE_SQL, (1, "leiria,marinha", 1, 1, "leiria,marinha"), "pogoleiria")
    alerts = HundoAlerts(None)
    assert alerts._record(1, 1, delivered=False)
    assert not is_active(settings(1)) and not confirmation_due(settings(1))
    _run(_RETRY_SQL, (1,), "pogoleiria")
    assert confirmation_due(settings(1))
    assert alerts._record(1, 2, delivered=True)
    assert not alerts._record(1, 2, delivered=True)  # already acknowledged
    assert is_active(settings(1))


@pytest.mark.parametrize(
    "show_costumes,expected",
    [(0, [(19, 45), (132, 0)]), (1, [(19, 45), (25, 99), (132, 0)])],
)
def test_missing_tiles_follow_ticks_forms_and_costumes(show_costumes, expected):
    _migrate()
    add_player(1, show_costumes=show_costumes)
    add_player(2)
    activate(1)
    # Ordinary Rattata missing, Alolan (46) ticked; Pikachu costume 99.
    add_names((19, 0, 0), (19, 46, 0), (25, 99, 1), (132, 0, 0), (1, 0, 0))
    tick_off(1, 19, 46)
    tick_off(1, 1, 0)
    tick_off(2, 132, 0)  # another player's tick is not theirs
    # The change marker counts this player's 100IV ticks only.
    assert settings(1)["hundo_ticks"] == 2 and settings(1)["hundo_ticked_at"]
    MANY = {**FORMS, 1: 163, 25: 0}
    assert HundoAlerts(None)._sync_player(settings(1), MANY, 2)
    got = rules(1)
    assert [(r["pokemon_id"], r["form"]) for r in got] == expected
    assert all(r["profile_no"] == 2 and r["min_iv"] == 100 for r in got)
    assert all(r["override_areas"] == areas_json("leiria,marinha") for r in got)


@pytest.mark.parametrize(
    "state",
    ["off", "not_collecting", "departed", "pending", "refused", "no_human", "stopped"],
)
def test_cleanup_removes_only_inactive_players_managed_rules(state):
    _migrate()
    add_player(1)
    add_player(2)
    activate(1)
    activate(2)
    add_human(1)
    add_human(2)
    add_rule(1)
    add_rule(2)
    add_rule(1, template="other")
    add_rule(1, template=None)
    _run(
        "INSERT INTO profiles (id, profile_no, name, area) VALUES ('1', 1, 'p', '[]')",
        database="poracle",
    )
    if state == "off":
        _run(_SAVE_SQL, (0, "leiria,marinha", 1, 0, "leiria,marinha"), "pogoleiria")
    elif state == "not_collecting":
        _run(
            "UPDATE pogoleiria.trade_player SET collecting = 'shiny' WHERE discord_id = 1"
        )
    elif state == "departed":
        _run("UPDATE pogoleiria.trade_player SET left_at = NOW() WHERE discord_id = 1")
    elif state == "pending":
        _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    elif state == "refused":
        _run(_RETRY_SQL, (1,), "pogoleiria")
        HundoAlerts(None)._record(1, 2, delivered=False)
    elif state == "no_human":
        _run("DELETE FROM humans WHERE id = '1'", database="poracle")
    elif state == "stopped":
        _run("UPDATE humans SET enabled = 0 WHERE id = '1'", database="poracle")
    humans, profiles = everything("humans"), everything("profiles")

    assert HundoAlerts(None)._cleanup()
    assert rules(1) == []
    assert len(rules(2)) == 1
    assert len(rules(1, "other")) == 1 and len(rules(1, None)) == 1
    assert everything("humans") == humans and everything("profiles") == profiles
    assert not HundoAlerts(None)._cleanup()  # nothing left to remove


def test_cleanup_keeps_active_rules_and_removes_all_when_nobody_is_eligible():
    _migrate()
    add_player(1)
    activate(1)
    add_human(1)
    add_rule(1)
    add_rule(99)  # rules of an id with no trade_player row
    assert HundoAlerts(None)._cleanup()
    assert len(rules(1)) == 1 and rules(99) == []
    _run("UPDATE pogoleiria.trade_player SET hundo_dms = 0")
    assert HundoAlerts(None)._cleanup()
    assert rules() == []


@pytest.mark.parametrize(
    "stale",
    [
        {"override_areas": '["marinhagrande"]'},
        {"profile_no": 1},
        {"min_iv": 0},
        {"costume": 0},
        "duplicate",
    ],
)
def test_rebuild_corrects_every_managed_value_then_is_a_no_op(stale):
    _migrate()
    add_player(1)
    activate(1, "leiria")
    add_names((19, 0, 0))
    alerts = HundoAlerts(None)
    assert alerts._sync_player(settings(1), FORMS, 2)
    good = rules(1)
    if stale == "duplicate":
        add_rule(1, override_areas='["leiria"]', profile_no=2)
    else:
        column, value = next(iter(stale.items()))
        _run(
            f"UPDATE monsters SET `{column}` = %s WHERE id = '1'",
            (value,),
            "poracle",
        )
    assert alerts._sync_player(settings(1), FORMS, 2)
    assert rules(1) == good
    assert not alerts._sync_player(settings(1), FORMS, 2)


def test_failed_insert_restores_the_previous_rules():
    _migrate()
    add_player(1)
    add_player(2)
    activate(1)
    activate(2)
    add_names((19, 0, 0))
    alerts = HundoAlerts(None)
    assert alerts._sync_player(settings(1), FORMS, 1)
    before = rules(1)
    # An out-of-range form is refused by MariaDB, after the DELETE ran.
    with pytest.raises(pymysql.err.DataError):
        alerts._sync_player(settings(1), {19: 2**40}, 1)
    assert rules(1) == before
    # One player's failure does not undo another's committed change.
    assert alerts._sync_player(settings(2), FORMS, 1)
    with pytest.raises(pymysql.err.DataError):
        alerts._sync_player(settings(1), {19: 2**40}, 3)
    assert len(rules(2)) == 1 and rules(1) == before


def test_human_lookup_reads_the_poracle_row():
    add_human(1, current_profile_no=3, admin_disable=1)
    assert HundoAlerts(None)._human(1) == {
        "id": "1",
        "type": "discord:user",
        "enabled": 1,
        "admin_disable": 1,
        "current_profile_no": 3,
    }
    assert HundoAlerts(None)._human(2) is None


def test_module_connects_to_the_disposable_server():
    assert hundo_alerts.Config.DB_PORT == int(_PORT)


def test_a_change_undone_before_its_dm_keeps_the_rules_and_is_settled():
    _migrate()
    add_player(1)
    add_human(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    assert HundoAlerts(None)._record(1, 1, delivered=True, settings=(1, "leiria"))
    add_rule(1)
    # Add Marinha Grande, then take it away again, before any DM.
    _run(_SAVE_SQL, (1, "leiria,marinha", 1, 1, "leiria,marinha"), "pogoleiria")
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    row = settings(1)
    assert row["hundo_settings_revision"] == 3
    assert row["hundo_confirmed_setting"] == 1  # Leiria only
    assert confirmation_due(row) and hundo_alerts.settled_back(row) and is_active(row)
    assert not HundoAlerts(None)._cleanup()  # the rules stay
    assert len(rules(1)) == 1
    # A real change is pending again: its rules pause until the DM.
    _run(_SAVE_SQL, (1, "leiria,marinha", 1, 1, "leiria,marinha"), "pogoleiria")
    assert HundoAlerts(None)._cleanup()
    assert rules(1) == []


def test_quiet_period_flag_follows_the_database_clock():
    _migrate()
    add_player(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    assert settings(1)["hundo_settling"] == 1
    _run(
        "UPDATE trade_player SET hundo_settings_at = NOW(6) - INTERVAL 31 SECOND",
        database="pogoleiria",
    )
    assert settings(1)["hundo_settling"] == 0


def test_collection_marker_detects_same_second_tile_swap():
    _migrate()
    add_player(1)
    activate(1)
    tick_off(1, 19, 0)
    _run(
        "UPDATE collection_entry SET created_at = '2026-09-25 00:00:00'",
        database="pogoleiria",
    )
    before = settings(1)
    _run(
        "UPDATE collection_entry SET pokemon_id = 25 WHERE discord_id = 1",
        database="pogoleiria",
    )
    after = settings(1)
    assert before["hundo_ticks"] == after["hundo_ticks"]
    assert before["hundo_ticked_at"] == after["hundo_ticked_at"]
    assert hundo_alerts._signature(before, 1, None) != hundo_alerts._signature(
        after, 1, None
    )


def test_durable_confirmation_claim_survives_recreation_and_is_exclusive():
    from modules.hundo_confirmation import claim, finish

    _migrate()
    add_player(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    owned, notice = claim(1, 1, 123)
    assert owned and notice["status"] == "sending"
    again, same = claim(1, 1, 123)
    assert not again and same["nonce"] == notice["nonce"]
    finish(1, 1, "sent", 987)
    assert claim(1, 1, 123)[1]["message_id"] == 987
    # A late reconciler must never replace a known outcome with uncertain.
    finish(1, 1, "uncertain")
    assert claim(1, 1, 123)[1]["status"] == "sent"
    # A stale settings snapshot cannot claim a new send.
    assert claim(1, 99, 123) == (False, None)


def test_health_write_cannot_acknowledge_a_newer_settings_revision():
    _migrate()
    add_player(1)
    activate(1)
    old = settings(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    alerts = HundoAlerts(None)
    alerts._record_health(old, "ready")
    _, rows = _run(
        "SELECT hundo_health_revision FROM trade_player WHERE discord_id=1",
        database="pogoleiria",
    )
    assert rows[0]["hundo_health_revision"] == 0
    alerts._record_health(settings(1), "stopped")
    _, rows = _run(
        "SELECT hundo_health_revision, hundo_health_state, TIMESTAMPDIFF(SECOND, hundo_health_at, UTC_TIMESTAMP(6)) age FROM trade_player WHERE discord_id=1",
        database="pogoleiria",
    )
    assert rows[0]["hundo_health_revision"] == 2
    assert rows[0]["hundo_health_state"] == "stopped" and rows[0]["age"] < 5


def test_uncertain_confirmation_retry_is_atomic_and_waits_for_inflight_send():
    from modules.hundo_confirmation import claim

    _migrate()
    add_player(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    claim(1, 1, 123)
    retry = """UPDATE trade_player SET hundo_settings_revision = hundo_settings_revision + 1,
      hundo_settings_at = NOW(6) WHERE discord_id = 1 AND left_at IS NULL
      AND hundo_confirmed_revision < hundo_settings_revision
      AND (hundo_dm_refused_revision = hundo_settings_revision OR EXISTS
        (SELECT 1 FROM hundo_confirmation hc WHERE hc.discord_id = trade_player.discord_id
          AND hc.revision = trade_player.hundo_settings_revision AND hc.status IN ('sending','uncertain')
          AND hc.started_at < UTC_TIMESTAMP(6) - INTERVAL 120 SECOND))"""
    assert _run(retry, database="pogoleiria")[0] == 0
    _run(
        "UPDATE hundo_confirmation SET started_at=UTC_TIMESTAMP(6)-INTERVAL 3 MINUTE",
        database="pogoleiria",
    )
    assert _run(retry, database="pogoleiria")[0] == 1
    assert _run(retry, database="pogoleiria")[0] == 0


@pytest.mark.parametrize("change", ["left_at=NOW()", "collecting='shiny'"])
def test_confirmation_cannot_claim_after_eligibility_changes(change):
    from modules.hundo_confirmation import claim

    _migrate()
    add_player(1)
    _run(_SAVE_SQL, (1, "leiria", 1, 1, "leiria"), "pogoleiria")
    _run(f"UPDATE trade_player SET {change} WHERE discord_id=1", database="pogoleiria")
    assert claim(1, 1, 123) == (False, None)
