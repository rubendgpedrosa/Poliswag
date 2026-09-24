"""Tests for modules.migrations."""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.migrations import MIGRATIONS_DIR, apply_migrations, split_statements


class TestSplitStatements:
    def test_drops_comment_lines_and_splits(self):
        sql = (
            "-- Migration 099; a comment with a semicolon\n"
            "ALTER TABLE t\n  ADD COLUMN IF NOT EXISTS a INT;\n\n"
            "CREATE TABLE IF NOT EXISTS u (id INT);\n"
        )
        assert split_statements(sql) == [
            "ALTER TABLE t\n  ADD COLUMN IF NOT EXISTS a INT",
            "CREATE TABLE IF NOT EXISTS u (id INT)",
        ]

    def test_comment_only_file_has_no_statements(self):
        assert split_statements("-- nothing here\n") == []


class TestApplyMigrations:
    async def test_runs_files_in_order(self, tmp_path):
        (tmp_path / "002_b.sql").write_text("SELECT 2;")
        (tmp_path / "001_a.sql").write_text("SELECT 1;\nSELECT 11;")
        db = MagicMock()
        db.execute_query_to_database = AsyncMock()
        failures = await apply_migrations(db, MagicMock(), tmp_path)
        ran = [c.args[0] for c in db.execute_query_to_database.call_args_list]
        assert ran == ["SELECT 1", "SELECT 11", "SELECT 2"]
        assert failures == 0

    async def test_failure_is_logged_and_the_rest_still_run(self, tmp_path):
        (tmp_path / "001_a.sql").write_text("BAD;")
        (tmp_path / "002_b.sql").write_text("GOOD;")
        db = MagicMock()
        db.execute_query_to_database = AsyncMock(
            side_effect=[RuntimeError("denied"), None]
        )
        log = MagicMock()
        failures = await apply_migrations(db, log, tmp_path)
        assert failures == 1
        assert db.execute_query_to_database.await_count == 2
        msg, level = log.call_args_list[0].args
        assert level == "ERROR" and "001_a.sql" in msg and "denied" in msg


# Every file is replayed on every start, so each statement must be a no-op
# the second time. Anything else needs a real migration-tracking table first.
_RERUNNABLE = [
    re.compile(r"^CREATE TABLE IF NOT EXISTS\b", re.I),
    re.compile(
        r"^ALTER TABLE \S+\s+ADD COLUMN IF NOT EXISTS\b"
        r"(?:[^,]*?,\s*ADD COLUMN IF NOT EXISTS\b)*[^,]*$",
        re.I | re.S,
    ),
]


@pytest.mark.parametrize(
    "path", sorted(MIGRATIONS_DIR.glob("*.sql")), ids=lambda p: p.name
)
def test_every_migration_is_rerunnable(path):
    for statement in split_statements(path.read_text()):
        assert any(p.match(statement) for p in _RERUNNABLE), (
            f"{path.name} has a statement that is not safe to replay on every "
            f"start:\n{statement}"
        )
