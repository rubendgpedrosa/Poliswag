"""The one query of the linker's store that has a rule worth pinning: which
accounts a remote run may take."""

from unittest.mock import MagicMock

from scripts.shiny_linker.store import Store

ROWS = [
    {"username": "acct2", "refresh_token": "t2"},
    {"username": "acct1", "refresh_token": "t1"},
]


def store(rows):
    s = Store.__new__(Store)
    s.dragonite = "dragonite"
    s.asked = []

    def _all(sql, params=()):
        s.asked.append((sql, params))
        return rows

    s._all = _all
    return s


def test_free_accounts_keep_their_listed_order_and_skip_those_in_use():
    s = store(ROWS)
    assert s.accounts_free(["acct1", "acct3", "acct2"], {"acct3"}) == [
        ("acct1", "t1"),
        ("acct2", "t2"),
    ]
    sql, params = s.asked[0]
    assert params == ("acct1", "acct2")
    assert "last_released" not in sql


def test_rested_accounts_were_left_alone_by_dragonite_that_long():
    s = store(ROWS)
    s.accounts_free(["acct1", "acct2"], set(), rested=7200)
    sql, params = s.asked[0]
    assert "COALESCE(last_selected, 0) < UNIX_TIMESTAMP() - %s" in sql
    assert "COALESCE(last_released, 0) < UNIX_TIMESTAMP() - %s" in sql
    assert params == ("acct1", "acct2", 7200, 7200)


def test_processing_marks_only_the_selected_pending_players_and_clears_them():
    s = store([{"Field": "link_processing_at"}])
    s._run = MagicMock()
    s.start_processing([7, 8])
    sql, params = s._run.call_args.args
    assert "link_processing_at = UTC_TIMESTAMP()" in sql
    assert "status = 'pending' AND discord_id IN (%s,%s)" in sql
    assert params == (7, 8)
    s.finish_processing([7, 8])
    sql, params = s._run.call_args.args
    assert "link_processing_at = NULL" in sql and params == (7, 8)
    assert len(s.asked) == 1


def test_progress_is_safe_before_the_migration_is_deployed():
    s = store([])
    s._run = MagicMock()
    s.start_processing([7])
    s.finish_processing([7])
    s._run.assert_not_called()
    assert len(s.asked) == 1


def test_no_targets_require_no_progress_database_reads():
    s = store([])
    s._run = MagicMock()
    s.start_processing([])
    s.finish_processing([])
    assert s.asked == []
    s._run.assert_not_called()
