"""Exercise the repair without touching MariaDB, Discord, or production backups.

SQLite executes the repair's SELECT/UPDATE/transaction statements; the adapter
only translates DB-API placeholders and dictionary rows.
"""

import copy
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts import repair_event_timezone as repair
from modules.event_manager import EventManager

NAME = "Lisbon, Portugal - Pokémon GO City Safari"


@pytest.fixture
def sandbox(mocker, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 26, 12, tzinfo=tz)

    mocker.patch.object(repair, "datetime", Clock)
    mocker.patch.object(repair.Config, "DISCORD_API_KEY", "test-only")
    mocker.patch.object(repair.Config, "CONVIVIO_CHANNEL_ID", 123)
    mocker.patch.object(repair, "Path", side_effect=lambda p: tmp_path / Path(p).name)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE event (
        name TEXT, start TEXT, end TEXT, extra_data TEXT,
        notification_date TEXT, notification_end_date TEXT,
        PRIMARY KEY (name, start))""")
    source = {"start": "2026-09-26T09:00:00.000Z", "end": "2026-09-27T17:00:00.000Z"}
    db.execute(
        "INSERT INTO event VALUES (?, ?, ?, ?, ?, ?)",
        (
            NAME,
            "2026-09-26 09:00:00",
            "2026-09-27 17:00:00",
            json.dumps(source),
            "2026-09-26 09:00:26",
            None,
        ),
    )
    db.commit()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            self.cursor = db.execute(sql.replace("%s", "?"), params)
            self.rowcount = self.cursor.rowcount

        def fetchall(self):
            return [dict(row) for row in self.cursor.fetchall()]

    conn = SimpleNamespace(
        cursor=Cursor, commit=db.commit, rollback=db.rollback, close=lambda: None
    )
    mocker.patch.object(repair, "connect", return_value=conn)
    snowflake = str(
        (int(datetime(2026, 9, 26, 10).timestamp() * 1000) - 1420070400000) << 22
    )
    message = {
        "id": snowflake,
        "author": {"id": "bot"},
        "content": "**Novos eventos**",
        "embeds": [
            {
                "title": "📅 " + NAME,
                "description": "Termina a 27 set às 17:00",
                "url": "https://example.test/event",
                "thumbnail": {"url": "https://example.test/image"},
            },
            {"title": "Unrelated event", "description": "Unchanged"},
        ],
    }
    other_author = copy.deepcopy(message)
    other_author["author"]["id"] = "someone-else"
    old_message = copy.deepcopy(message)
    old_message["id"] = "1"
    ended_message = copy.deepcopy(message)
    ended_message["content"] = "**Eventos que terminaram**"
    messages = [message, other_author, ended_message, old_message]
    calls = []

    class Response:
        def __init__(self, result):
            self.result = result
            self.status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        async def json(self):
            return copy.deepcopy(self.result)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "GET" and url.endswith("/users/@me"):
                return Response({"id": "bot"})
            if method == "GET" and url.endswith("/channels/123/messages"):
                return Response(messages)
            if method == "GET" and url.endswith("/messages/" + message["id"]):
                return Response(message)
            if method == "PATCH" and url.endswith("/messages/" + message["id"]):
                message["embeds"] = copy.deepcopy(kwargs["json"]["embeds"])
                return Response(message)
            raise AssertionError((method, url))

    mocker.patch.object(repair.aiohttp, "ClientSession", return_value=Session())
    state = SimpleNamespace(
        db=db,
        calls=calls,
        message=message,
        tmp_path=tmp_path,
        session=Session,
        response=Response,
        messages=messages,
    )
    yield state
    db.close()


async def test_preview_never_writes_or_edits(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair"])
    before = list(sandbox.db.execute("SELECT * FROM event"))
    await repair.main()
    assert list(sandbox.db.execute("SELECT * FROM event")) == before
    assert all(method == "GET" for method, *_ in sandbox.calls)
    assert not list(sandbox.tmp_path.iterdir())


async def test_repair_preserves_notice_edits_existing_post_and_is_repeatable(
    sandbox, mocker
):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    original = copy.deepcopy(sandbox.message)
    await repair.main()
    row = dict(sandbox.db.execute("SELECT * FROM event").fetchone())
    assert row["start"] == "2026-09-26 10:00:00"
    assert row["end"] == "2026-09-27 18:00:00"
    assert row["notification_date"] == "2026-09-26 09:00:26"
    assert row["notification_end_date"] is None
    writes = [call for call in sandbox.calls if call[0] != "GET"]
    assert len(writes) == 1
    assert writes[0][0] == "PATCH"
    assert writes[0][2]["json"]["allowed_mentions"] == {"parse": []}
    assert sandbox.message["embeds"][1] == original["embeds"][1]
    for key in ("title", "url", "thumbnail"):
        assert sandbox.message["embeds"][0][key] == original["embeds"][0][key]
    assert sandbox.message["embeds"][0]["description"] == (
        "Início: 26 set às 10:00\nTermina a 27 set às 18:00\nHorários de Lisboa (corrigidos)."
    )
    backup = json.loads(next(sandbox.tmp_path.iterdir()).read_text())
    assert backup["edits"][0]["before"] == original
    assert backup["changes"][0]["row"]["start"] == "2026-09-26 09:00:00"

    bot = MagicMock()
    bot.db = AsyncMock()
    bot.db.get_data_from_database.return_value = [{**row, "event_status": "active"}]
    assert (
        await EventManager(bot).check_current_events_changes(
            at_time=datetime(2026, 9, 26, 10)
        )
        is None
    )
    bot.db.execute_query_to_database.assert_not_awaited()

    sandbox.calls.clear()
    await repair.main()
    assert sandbox.db.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 1
    assert all(method == "GET" for method, *_ in sandbox.calls)


async def test_conflicting_corrected_row_rolls_back_without_editing_discord(
    sandbox, mocker
):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    sandbox.db.execute(
        "INSERT INTO event SELECT name, ?, end, extra_data, notification_date, notification_end_date FROM event",
        ("2026-09-26 10:00:00",),
    )
    sandbox.db.commit()
    before = list(sandbox.db.execute("SELECT * FROM event"))
    with pytest.raises(sqlite3.IntegrityError):
        await repair.main()
    assert list(sandbox.db.execute("SELECT * FROM event")) == before
    assert all(method == "GET" for method, *_ in sandbox.calls)


async def test_resume_retries_discord_after_database_commit(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    original_request = sandbox.session.request
    failed = False

    def request(self, method, url, **kwargs):
        nonlocal failed
        if method == "PATCH" and not failed:
            failed = True
            raise RuntimeError("connection lost")
        return original_request(self, method, url, **kwargs)

    mocker.patch.object(sandbox.session, "request", request)
    with pytest.raises(RuntimeError, match="connection lost"):
        await repair.main()
    assert (
        sandbox.db.execute("SELECT start FROM event").fetchone()[0]
        == "2026-09-26 10:00:00"
    )
    backup = next(sandbox.tmp_path.glob("*.json"))
    assert json.loads(backup.read_text())["database_status"] == "committed"
    # Resume must use the persisted work even though no database rows need repair.
    mocker.patch.object(
        repair, "connect", side_effect=AssertionError("Resume must not touch DB")
    )
    mocker.patch.object(repair.sys, "argv", ["repair", "--resume", str(backup)])
    await repair.main()
    assert json.loads(backup.read_text())["edits"][0]["status"] == "done"
    assert "às 18:00" in sandbox.message["embeds"][0]["description"]
    sandbox.calls.clear()
    await repair.main()
    assert not any(method == "PATCH" for method, *_ in sandbox.calls)


async def test_deleted_message_is_recorded_without_replacement(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    original_request = sandbox.session.request

    def request(self, method, url, **kwargs):
        if method == "GET" and url.endswith("/messages/" + sandbox.message["id"]):
            raise repair.aiohttp.ClientResponseError(MagicMock(), (), status=404)
        return original_request(self, method, url, **kwargs)

    mocker.patch.object(sandbox.session, "request", request)
    await repair.main()
    assert not any(method == "PATCH" for method, *_ in sandbox.calls)
    journal = json.loads(next(sandbox.tmp_path.glob("*.json")).read_text())
    assert journal["edits"][0]["status"] == "deleted"


async def test_rate_limit_retries_same_edit_without_a_new_message(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    sleep = mocker.patch.object(repair.asyncio, "sleep", new=AsyncMock())
    original_request = sandbox.session.request
    attempts = 0

    def request(self, method, url, **kwargs):
        nonlocal attempts
        if method == "PATCH":
            attempts += 1
            if attempts == 1:
                response = sandbox.response({"retry_after": 0.1})
                response.status = 429
                return response
        return original_request(self, method, url, **kwargs)

    mocker.patch.object(sandbox.session, "request", request)
    await repair.main()
    assert attempts == 2
    sleep.assert_awaited_once_with(0.1)
    assert [method for method, *_ in sandbox.calls if method != "GET"] == ["PATCH"]


async def test_history_pagination_finds_older_today_message(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    original_request = sandbox.session.request
    pages = []

    def request(self, method, url, **kwargs):
        if method == "GET" and url.endswith("/messages"):
            pages.append(kwargs["params"])
            if "before" not in kwargs["params"]:
                recent = copy.deepcopy(sandbox.message)
                recent["id"] = str(int(recent["id"]) + 100)
                recent["author"]["id"] = "another-author"
                return sandbox.response([recent])
        return original_request(self, method, url, **kwargs)

    mocker.patch.object(sandbox.session, "request", request)
    await repair.main()
    assert len(pages) == 2 and "before" in pages[1]
    assert "às 18:00" in sandbox.message["embeds"][0]["description"]


def test_exact_names_and_headerless_continuation_batches(sandbox):
    row = dict(sandbox.db.execute("SELECT * FROM event").fetchone())
    changes = repair.plan_changes([row])
    continuation = copy.deepcopy(sandbox.message)
    continuation["content"] = ""
    assert len(repair.plan_edits([continuation], changes)) == 1
    continuation["embeds"][0]["title"] = "📅 Other " + NAME
    assert repair.plan_edits([continuation], changes) == []


def test_unrelated_source_revision_requires_review(sandbox):
    row = dict(sandbox.db.execute("SELECT * FROM event").fetchone())
    row["end"] = "2026-09-29 17:00:00"
    with pytest.raises(ValueError, match="Unrelated schedule change"):
        repair.plan_changes([row])


async def test_uncertain_patch_resumes_without_repeating_accepted_edit(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    original_request = sandbox.session.request
    patch_count = 0

    def request(self, method, url, **kwargs):
        nonlocal patch_count
        result = original_request(self, method, url, **kwargs)
        if method == "PATCH":
            patch_count += 1
            if patch_count == 1:
                # Server accepted the edit, but its response was lost.
                raise RuntimeError("response lost")
        return result

    mocker.patch.object(sandbox.session, "request", request)
    with pytest.raises(RuntimeError, match="response lost"):
        await repair.main()
    backup = next(sandbox.tmp_path.glob("*.json"))
    mocker.patch.object(repair.sys, "argv", ["repair", "--resume", str(backup)])
    await repair.main()
    assert patch_count == 1
    assert json.loads(backup.read_text())["edits"][0]["status"] == "done"


async def test_concurrent_message_edit_is_not_overwritten(sandbox, mocker):
    mocker.patch.object(repair.sys, "argv", ["repair", "--apply"])
    original_request = sandbox.session.request

    def request(self, method, url, **kwargs):
        if method == "GET" and url.endswith("/messages/" + sandbox.message["id"]):
            sandbox.message["embeds"][0][
                "description"
            ] = "Manually corrected description"
        return original_request(self, method, url, **kwargs)

    mocker.patch.object(sandbox.session, "request", request)
    with pytest.raises(ValueError, match="description changed"):
        await repair.main()
    assert not any(method == "PATCH" for method, *_ in sandbox.calls)
