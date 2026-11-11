"""Tests for modules.webstats_access.WebStatsAccess.

The link token is a password: only its SHA-256 may touch the database, and
rotating must leave exactly one row so every previous link stops working.
"""

import hashlib
from unittest.mock import patch

import pytest

from modules.webstats_access import WebStatsAccess
from tests.fakes import FakeHandle

BASE = "https://pogoleiria.pt/webstats"


@pytest.fixture
def db():
    return FakeHandle()


@pytest.fixture
def cursor(db):
    return db.cursor


@pytest.fixture
def access(db):
    return WebStatsAccess(db, base_url=BASE)


def test_default_base_url_comes_from_config():
    with patch("modules.webstats_access.Config") as config:
        config.WEBSTATS_URL = "https://example.test/st"
        assert WebStatsAccess(FakeHandle()).base_url == "https://example.test/st"


class TestRotate:
    async def test_returns_a_link_with_a_64_hex_token(self, access):
        url = await access.rotate()
        assert url.startswith(BASE + "/")
        token = url.rsplit("/", 1)[1]
        assert len(token) == 64
        int(token, 16)

    async def test_stores_only_the_sha256_and_replaces_every_row(
        self, access, db, cursor
    ):
        url = await access.rotate()
        token = url.rsplit("/", 1)[1]
        digest = hashlib.sha256(token.encode()).hexdigest()
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert statements[0].startswith("DELETE FROM webstats_access")
        assert statements[1].startswith("INSERT INTO webstats_access")
        assert cursor.execute.call_args_list[1].args[1] == (digest,)
        assert token not in str(cursor.execute.call_args_list)
        # Both statements in one unit, so one transaction; tuple rows, as the
        # old connect(DB_POGOLEIRIA) gave.
        ((work, _args, options),) = db.units
        assert work.__name__ == "_replace"
        assert options == {"dict_rows": False}
        assert token not in str(db.units)

    async def test_two_rotations_issue_different_tokens(self, access):
        first = await access.rotate()
        second = await access.rotate()
        assert first != second

    async def test_a_replace_failure_reaches_the_caller(self, access, db, cursor):
        # The unit rolls back and closes; the error is not swallowed here.
        cursor.execute.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await access.rotate()
        assert db.units[0][2] == {"dict_rows": False}


class TestCurrentOrIssue:
    async def test_an_existing_link_returns_none_not_a_second_one(
        self, access, db, cursor
    ):
        cursor.fetchone.return_value = (3,)
        assert await access.current_or_issue() is None
        ((work, _args, options),) = db.units
        assert work.__name__ == "_count"
        assert options == {"dict_rows": False}
        assert cursor.execute.call_count == 1

    async def test_without_a_link_it_issues_one(self, access, db, cursor):
        cursor.fetchone.return_value = (0,)
        url = await access.current_or_issue()
        assert url.startswith(BASE + "/")
        # The count and the replace are separate units, as they were separate
        # connections.
        assert [work.__name__ for work, _args, _options in db.units] == [
            "_count",
            "_replace",
        ]

    async def test_an_empty_count_result_counts_as_no_link(self, access, cursor):
        cursor.fetchone.return_value = None
        assert (await access.current_or_issue()).startswith(BASE + "/")

    async def test_a_count_failure_reaches_the_caller(self, access, db, cursor):
        cursor.execute.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await access.current_or_issue()
        assert len(db.units) == 1
