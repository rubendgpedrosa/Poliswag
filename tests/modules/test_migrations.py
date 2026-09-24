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
def is_rerunnable(statement):
    if re.match(r"^CREATE TABLE IF NOT EXISTS\b", statement, re.I):
        return True
    match = re.fullmatch(r"ALTER TABLE \S+\s+(.+)", statement, re.I | re.S)
    if not match:
        return False
    text = match.group(1)
    clauses = []
    start = depth = index = 0
    quote = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
        elif char == "," and depth == 0:
            clauses.append(text[start:index].strip())
            start = index + 1
        index += 1
    if quote or depth:
        return False
    clauses.append(text[start:].strip())
    return all(
        re.match(r"ADD COLUMN IF NOT EXISTS\b", clause, re.I) for clause in clauses
    )


@pytest.mark.parametrize(
    "path", sorted(MIGRATIONS_DIR.glob("*.sql")), ids=lambda p: p.name
)
def test_every_migration_is_rerunnable(path):
    for statement in split_statements(path.read_text()):
        assert is_rerunnable(statement), (
            f"{path.name} has a statement that is not safe to replay on every "
            f"start:\n{statement}"
        )


@pytest.mark.parametrize(
    "definition",
    [
        "areas SET('leiria','marinha') NOT NULL DEFAULT 'leiria,marinha'",
        "mode ENUM('on','off') DEFAULT 'off'",
        "label VARCHAR(64) DEFAULT 'a,b'",
        "label VARCHAR(64) DEFAULT 'can''t, split'",
        r"label VARCHAR(64) DEFAULT 'can\'t, split'",
        "`column,with,commas` INT DEFAULT 0",
    ],
)
def test_rerunnable_accepts_commas_inside_column_definitions(definition):
    sql = (
        f"ALTER TABLE t ADD COLUMN IF NOT EXISTS {definition},\n"
        "ADD COLUMN IF NOT EXISTS settings_at DATETIME(6) NULL"
    )
    assert is_rerunnable(sql)


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE t ADD COLUMN a INT",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a INT, DROP COLUMN b",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a INT, ADD COLUMN b INT",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a INT, MODIFY COLUMN b INT",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a INT,",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a SET('a','b'",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a INT)",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a VARCHAR(64) DEFAULT 'unclosed",
        "CREATE TABLE t (id INT)",
        "DROP TABLE t",
    ],
)
def test_rerunnable_rejects_unsafe_or_unbalanced_statements(statement):
    assert not is_rerunnable(statement)
