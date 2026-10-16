"""Tests for modules.webstats_access.WebStatsAccess.

The link token is a password: only its SHA-256 may touch the database, and
rotating must leave exactly one row so every previous link stops working.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from modules.webstats_access import WebStatsAccess

BASE = "https://pogoleiria.pt/webstats"


def make_db(fetchone=None):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = fetchone
    return db, cursor


@pytest.fixture
def access():
    return WebStatsAccess(base_url=BASE)


def test_default_base_url_comes_from_config():
    with patch("modules.webstats_access.Config") as config:
        config.WEBSTATS_URL = "https://example.test/st"
        assert WebStatsAccess().base_url == "https://example.test/st"


class TestRotate:
    async def test_returns_a_link_with_a_64_hex_token(self, access):
        db, _ = make_db()
        with patch("modules.webstats_access.connect", return_value=db):
            url = await access.rotate()
        assert url.startswith(BASE + "/")
        token = url.rsplit("/", 1)[1]
        assert len(token) == 64
        int(token, 16)

    async def test_stores_only_the_sha256_and_replaces_every_row(self, access):
        db, cursor = make_db()
        with patch("modules.webstats_access.connect", return_value=db):
            url = await access.rotate()
        token = url.rsplit("/", 1)[1]
        digest = hashlib.sha256(token.encode()).hexdigest()
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert statements[0].startswith("DELETE FROM webstats_access")
        assert statements[1].startswith("INSERT INTO webstats_access")
        assert cursor.execute.call_args_list[1].args[1] == (digest,)
        assert token not in str(cursor.execute.call_args_list)
        db.commit.assert_called_once()

    async def test_two_rotations_issue_different_tokens(self, access):
        db, _ = make_db()
        with patch("modules.webstats_access.connect", return_value=db):
            first = await access.rotate()
            second = await access.rotate()
        assert first != second

    async def test_a_replace_failure_closes_without_committing(self, access):
        db, cursor = make_db()
        cursor.execute.side_effect = RuntimeError("down")
        with patch("modules.webstats_access.connect", return_value=db):
            with pytest.raises(RuntimeError):
                await access.rotate()
        db.close.assert_called_once()
        db.commit.assert_not_called()


class TestCurrentOrIssue:
    async def test_an_existing_link_returns_none_not_a_second_one(self, access):
        db, cursor = make_db(fetchone=(3,))
        with patch("modules.webstats_access.connect", return_value=db) as connect:
            assert await access.current_or_issue() is None
        connect.assert_called_once()
        assert cursor.execute.call_count == 1

    async def test_without_a_link_it_issues_one(self, access):
        db, _ = make_db(fetchone=(0,))
        with patch("modules.webstats_access.connect", return_value=db):
            url = await access.current_or_issue()
        assert url.startswith(BASE + "/")

    async def test_an_empty_count_result_counts_as_no_link(self, access):
        db, _ = make_db(fetchone=None)
        with patch("modules.webstats_access.connect", return_value=db):
            assert (await access.current_or_issue()).startswith(BASE + "/")

    async def test_a_count_failure_closes_the_connection(self, access):
        db, cursor = make_db()
        cursor.execute.side_effect = RuntimeError("down")
        with patch("modules.webstats_access.connect", return_value=db):
            with pytest.raises(RuntimeError):
                await access.current_or_issue()
        db.close.assert_called_once()
