"""The one query of the linker's store that has a rule worth pinning: which
accounts a remote run may take."""

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
