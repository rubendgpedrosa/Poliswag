"""Apply migrations/*.sql at startup.

Migrations used to be applied by hand (`make migrate`), and on 2026-09-20
the trades code went live before 010 was run: ~100 errors, one per tick,
until someone noticed. Every migration here is written to be re-runnable
(CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS — enforced by
tests/modules/test_migrations.py), so the bot simply replays all of them on
every start instead of tracking which ones already ran.
"""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def split_statements(sql):
    """Statements of one migration file: `--` comment lines dropped, then
    split on `;`. Good enough for these files, which hold plain DDL only
    (no procedures, no `;` inside string literals)."""
    code = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in code.split(";") if s.strip()]


async def apply_migrations(db, log, migrations_dir=MIGRATIONS_DIR):
    """Run every migration in filename order. A failing statement is logged
    and the rest still run, so one bad file can't keep the bot from
    starting. Returns the number of failed statements."""
    failures = 0
    for path in sorted(migrations_dir.glob("*.sql")):
        for statement in split_statements(path.read_text()):
            try:
                await db.execute_query_to_database(statement)
            except Exception as e:
                failures += 1
                log(f"Migration {path.name} failed: {e}", "ERROR")
    if not failures:
        log(f"Migrations up to date ({migrations_dir.name}/)", "INFO")
    return failures
