"""Tests for modules.utility.Utility pure-logic methods.

Utility.__init__ does heavy filesystem I/O (creates log directories, log files,
and attaches logging handlers). We bypass __init__ with Utility.__new__(Utility)
and manually attach only the attributes each test needs. This keeps the tests
fast and isolated from the filesystem.
"""

import logging
import logging.handlers
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.config import Config
from modules.utility import LOG_BACKUP_COUNT, LOG_MAX_BYTES, Utility


@pytest.fixture
def util():
    """A Utility instance with its I/O-heavy __init__ bypassed."""
    u = Utility.__new__(Utility)
    u.poliswag = MagicMock()
    u.poliswag.db = AsyncMock()
    u.logger = MagicMock(spec=logging.Logger)
    u.error_logger = MagicMock(spec=logging.Logger)
    return u


class TestInit:
    """Exercises the real __init__ (bypassed everywhere else in this file)
    against tmp_path, so the actual logging setup — including the
    RotatingFileHandler config — gets verified end-to-end at least once."""

    def _configure(self, tmp_path, mocker):
        log_file = tmp_path / "logs" / "actions.log"
        error_file = tmp_path / "logs" / "error.log"
        mocker.patch.object(Config, "LOG_FILE", str(log_file))
        mocker.patch.object(Config, "ERROR_LOG_FILE", str(error_file))
        return log_file, error_file

    def test_creates_directories_and_files(self, tmp_path, mocker):
        log_file, error_file = self._configure(tmp_path, mocker)
        u = Utility(poliswag=MagicMock())
        assert log_file.exists()
        assert error_file.exists()
        assert u.LOG_FILE == log_file
        assert u.ERROR_LOG_FILE == error_file

    def test_configures_rotating_handlers(self, tmp_path, mocker):
        self._configure(tmp_path, mocker)
        u = Utility(poliswag=MagicMock())

        info_handlers = [
            h
            for h in u.logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(info_handlers) == 1
        assert info_handlers[0].maxBytes == LOG_MAX_BYTES
        assert info_handlers[0].backupCount == LOG_BACKUP_COUNT
        assert u.logger.level == logging.INFO

        error_handlers = [
            h
            for h in u.error_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(error_handlers) == 1
        assert u.error_logger.level == logging.ERROR
        assert u.error_logger.propagate is False

    def test_log_to_file_writes_through_to_disk(self, tmp_path, mocker):
        log_file, error_file = self._configure(tmp_path, mocker)
        u = Utility(poliswag=MagicMock())
        u.log_to_file("hello world", "INFO")
        u.log_to_file("boom", "ERROR")
        assert "hello world" in log_file.read_text()
        assert "boom" in error_file.read_text()

    def test_reinitializing_does_not_duplicate_handlers(self, tmp_path, mocker):
        self._configure(tmp_path, mocker)
        Utility(poliswag=MagicMock())
        u2 = Utility(poliswag=MagicMock())
        assert len(u2.logger.handlers) == 2  # rotating file + console
        assert len(u2.error_logger.handlers) == 1


class TestLogToFile:
    def test_info_goes_to_info_logger(self, util):
        util.log_to_file("hello", "INFO")
        util.logger.info.assert_called_once_with("hello")
        util.error_logger.error.assert_not_called()

    def test_default_log_type_is_info(self, util):
        util.log_to_file("hello")
        util.logger.info.assert_called_once_with("hello")

    def test_error_goes_to_error_logger(self, util):
        util.log_to_file("boom", "ERROR")
        util.error_logger.error.assert_called_once_with("boom")
        util.logger.info.assert_not_called()

    def test_crash_goes_to_error_logger(self, util):
        util.log_to_file("crashed", "CRASH")
        util.error_logger.error.assert_called_once_with("crashed")

    def test_unknown_log_type_falls_through_to_info(self, util):
        # Current behaviour: anything not ERROR/CRASH is treated as INFO.
        util.log_to_file("debug message", "DEBUG")
        util.logger.info.assert_called_once_with("debug message")


class TestTimeNow:
    def test_returns_iso_string_at_midnight(self, util):
        result = util.time_now()
        # Format: YYYY-MM-DDT00:00:00
        assert isinstance(result, str)
        assert "T00:00:00" in result
        # Date portion matches today.
        today = datetime.now().date().isoformat()
        assert result.startswith(today)


class TestFormatDatetimeString:
    def test_strips_Z_T_and_millis(self, util):
        assert (
            util.format_datetime_string("2024-01-02T15:04:05.123Z")
            == "2024-01-02 15:04:05"
        )

    def test_handles_no_fractional_seconds(self, util):
        assert (
            util.format_datetime_string("2024-01-02T15:04:05Z") == "2024-01-02 15:04:05"
        )

    def test_handles_no_trailing_Z(self, util):
        assert (
            util.format_datetime_string("2024-01-02T15:04:05") == "2024-01-02 15:04:05"
        )

    def test_handles_no_T_separator(self, util):
        # Nothing to split → pass-through minus the Z.
        assert (
            util.format_datetime_string("2024-01-02 15:04:05Z") == "2024-01-02 15:04:05"
        )


class TestReadLastLinesFromLog:
    def test_returns_last_n_lines(self, util, tmp_path):
        log = tmp_path / "actions.log"
        log.write_text("line1\nline2\nline3\nline4\nline5\n")
        util.LOG_FILE = log
        assert util.read_last_lines_from_log(numLines=2) == "line4\nline5\n"

    def test_default_ten_lines(self, util, tmp_path):
        log = tmp_path / "actions.log"
        log.write_text("".join(f"l{i}\n" for i in range(20)))
        util.LOG_FILE = log
        result = util.read_last_lines_from_log()
        assert result.count("\n") == 10
        assert result.startswith("l10\n")

    def test_returns_error_string_on_missing_file(self, util, tmp_path):
        util.LOG_FILE = tmp_path / "nope.log"
        assert util.read_last_lines_from_log() == "Error reading logs"
        util.error_logger.error.assert_called_once()


class TestReadNewErrorEntries:
    def test_returns_entries_strictly_after_since(self, util, tmp_path):
        log = tmp_path / "error.log"
        log.write_text(
            "2026-04-07 09:00:00,000 - ERROR - old one\n"
            "2026-04-07 10:00:00,000 - ERROR - new one\n"
            "2026-04-07 11:00:00,000 - CRASH - newer one\n"
        )
        util.ERROR_LOG_FILE = log
        since = datetime(2026, 4, 7, 9, 30, 0)
        entries = util.read_new_error_entries(since)
        assert entries == [
            "2026-04-07 10:00:00,000 - ERROR - new one",
            "2026-04-07 11:00:00,000 - CRASH - newer one",
        ]

    def test_multiline_traceback_collapses_to_first_line(self, util, tmp_path):
        log = tmp_path / "error.log"
        log.write_text(
            "2026-04-07 10:00:00,000 - CRASH - boom\n"
            "Traceback (most recent call last):\n"
            '  File "main.py", line 1, in <module>\n'
            "ValueError: boom\n"
        )
        util.ERROR_LOG_FILE = log
        entries = util.read_new_error_entries(datetime(2026, 4, 7, 9, 0, 0))
        assert entries == ["2026-04-07 10:00:00,000 - CRASH - boom"]

    def test_no_entries_after_since_returns_empty_list(self, util, tmp_path):
        log = tmp_path / "error.log"
        log.write_text("2026-04-07 09:00:00,000 - ERROR - old one\n")
        util.ERROR_LOG_FILE = log
        entries = util.read_new_error_entries(datetime(2026, 4, 7, 10, 0, 0))
        assert entries == []

    def test_missing_file_logs_and_returns_empty_list(self, util, tmp_path):
        util.ERROR_LOG_FILE = tmp_path / "nope.log"
        entries = util.read_new_error_entries(datetime(2026, 4, 7, 9, 0, 0))
        assert entries == []
        util.error_logger.error.assert_called_once()

    def test_syntactically_matching_but_invalid_timestamp_is_skipped(
        self, util, tmp_path
    ):
        # Matches the digit-shaped regex but is not a real calendar date/time
        # (month 13) — strptime raises, and the entry is dropped rather than
        # crashing the whole read.
        log = tmp_path / "error.log"
        log.write_text(
            "2026-13-45 25:99:99,000 - ERROR - corrupted line\n"
            "2026-04-07 10:00:00,000 - ERROR - real one\n"
        )
        util.ERROR_LOG_FILE = log
        entries = util.read_new_error_entries(datetime(2026, 4, 7, 9, 0, 0))
        assert entries == ["2026-04-07 10:00:00,000 - ERROR - real one"]


class TestAddButtonEvent:
    async def test_assigns_callback_to_button(self, util):
        button = MagicMock()
        cb = MagicMock()
        await util.add_button_event(button, cb)
        assert button.callback is cb

    async def test_logs_error_when_callback_assignment_fails(self, util):
        # A button whose callback setter raises.
        class BadButton:
            @property
            def callback(self):
                return None

            @callback.setter
            def callback(self, value):
                raise RuntimeError("no setter")

        await util.add_button_event(BadButton(), lambda: None)
        util.error_logger.error.assert_called_once()


class TestSendMessageToChannel:
    async def test_sends_message(self, util):
        channel = MagicMock()
        channel.send = MagicMock()

        async def fake_send(msg):
            channel.sent = msg

        channel.send = fake_send
        await util.send_message_to_channel(channel, "hi")
        assert channel.sent == "hi"

    async def test_logs_on_forbidden(self, util, mocker):
        channel = MagicMock()
        channel.name = "general"

        async def raise_forbidden(msg):
            raise discord.errors.Forbidden(MagicMock(status=403, reason=""), "nope")

        channel.send = raise_forbidden
        await util.send_message_to_channel(channel, "hi")
        util.error_logger.error.assert_called_once()
        assert "No permission" in util.error_logger.error.call_args.args[0]

    async def test_logs_on_generic_exception(self, util):
        channel = MagicMock()
        channel.name = "general"

        async def raise_boom(msg):
            raise RuntimeError("boom")

        channel.send = raise_boom
        await util.send_message_to_channel(channel, "hi")
        util.error_logger.error.assert_called_once()
        assert "Failed to send message" in util.error_logger.error.call_args.args[0]


class TestSendEmbedToChannel:
    async def test_sends_embed(self, util):
        channel = MagicMock()

        async def fake_send(*, embed):
            channel.sent_embed = embed

        channel.send = fake_send
        await util.send_embed_to_channel(channel, "EMBED")
        assert channel.sent_embed == "EMBED"

    async def test_logs_on_generic_exception(self, util):
        channel = MagicMock()
        channel.name = "general"

        async def raise_boom(*, embed):
            raise RuntimeError("boom")

        channel.send = raise_boom
        await util.send_embed_to_channel(channel, "EMBED")
        util.error_logger.error.assert_called_once()
        assert "Failed to send embed" in util.error_logger.error.call_args.args[0]

    async def test_logs_on_forbidden(self, util):
        channel = MagicMock()
        channel.name = "general"

        async def raise_forbidden(*, embed):
            raise discord.errors.Forbidden(MagicMock(status=403, reason=""), "nope")

        channel.send = raise_forbidden
        await util.send_embed_to_channel(channel, "EMBED")
        util.error_logger.error.assert_called_once()
        assert "No permission" in util.error_logger.error.call_args.args[0]


class _AsyncMessageIter:
    """Async-iterable stand-in for channel.history()."""

    def __init__(self, messages):
        self._messages = messages

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m

        return gen()


class TestFindQuestScanningMessage:
    async def test_returns_none_when_channel_is_none(self, util):
        assert await util.find_quest_scanning_message(None) is None

    async def test_finds_matching_message_from_today(self, util, mocker):
        from datetime import timezone, timedelta

        embed = MagicMock()
        embed.title = "SCAN DE QUESTS — Leiria"
        match = MagicMock()
        match.author = util.poliswag.user
        match.embeds = [embed]
        # created_at must be timezone-aware (as Discord always provides) and within 25 h
        match.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        channel = MagicMock()
        channel.history = MagicMock(return_value=_AsyncMessageIter([match]))
        result = await util.find_quest_scanning_message(channel)
        assert result is match

    async def test_returns_none_when_author_differs(self, util):
        today = datetime.now().date()
        embed = MagicMock()
        embed.title = "SCAN DE QUESTS"
        other = MagicMock()
        other.author = MagicMock(name="someone_else")
        other.embeds = [embed]
        other.created_at = datetime.combine(today, datetime.min.time())
        channel = MagicMock()
        channel.history = MagicMock(return_value=_AsyncMessageIter([other]))
        assert await util.find_quest_scanning_message(channel) is None

    async def test_returns_none_when_no_matching_embed(self, util):
        msg = MagicMock()
        msg.author = util.poliswag.user
        msg.embeds = []
        channel = MagicMock()
        channel.history = MagicMock(return_value=_AsyncMessageIter([msg]))
        assert await util.find_quest_scanning_message(channel) is None

    async def test_exception_is_logged_and_returns_none(self, util):
        channel = MagicMock()
        channel.history = MagicMock(side_effect=RuntimeError("boom"))
        assert await util.find_quest_scanning_message(channel) is None
        util.error_logger.error.assert_called_once()


class TestGetNewPokemongoVersion:
    async def test_returns_none_when_version_unchanged(self, util, mocker):
        session_get_response = MagicMock()
        session_get_response.status = 200

        async def fake_text():
            return "1.2.3\x07"

        session_get_response.text = fake_text

        class _CM:
            async def __aenter__(self_inner):
                return session_get_response

            async def __aexit__(self_inner, *exc):
                return False

        class _Session:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

            def get(self_inner, url):
                return _CM()

        mocker.patch("modules.utility.get_session", return_value=_Session())
        util.poliswag.db.get_data_from_database.return_value = [{"version": "1.2.3"}]
        result = await util.get_new_pokemongo_version()
        assert result is None
        util.poliswag.db.execute_query_to_database.assert_not_called()

    async def test_updates_db_and_returns_version_when_changed(self, util, mocker):
        response = MagicMock()
        response.status = 200

        async def fake_text():
            return "2.0.0"

        response.text = fake_text

        class _CM:
            async def __aenter__(self_inner):
                return response

            async def __aexit__(self_inner, *exc):
                return False

        class _Session:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

            def get(self_inner, url):
                return _CM()

        mocker.patch("modules.utility.get_session", return_value=_Session())
        util.poliswag.db.get_data_from_database.return_value = [{"version": "1.0.0"}]
        result = await util.get_new_pokemongo_version()
        assert result == "2.0.0"
        util.poliswag.db.execute_query_to_database.assert_called_once()

    async def test_non_200_returns_none(self, util, mocker):
        response = MagicMock()
        response.status = 500

        class _CM:
            async def __aenter__(self_inner):
                return response

            async def __aexit__(self_inner, *exc):
                return False

        class _Session:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

            def get(self_inner, url):
                return _CM()

        mocker.patch("modules.utility.get_session", return_value=_Session())
        assert await util.get_new_pokemongo_version() is None

    async def test_exception_logged_and_returns_none(self, util, mocker):
        mocker.patch("modules.utility.get_session", side_effect=RuntimeError("boom"))
        assert await util.get_new_pokemongo_version() is None
        util.error_logger.error.assert_called_once()

    async def test_empty_db_result_treats_current_as_none(self, util, mocker):
        response = MagicMock()
        response.status = 200

        async def fake_text():
            return "3.0.0"

        response.text = fake_text

        class _CM:
            async def __aenter__(self_inner):
                return response

            async def __aexit__(self_inner, *exc):
                return False

        class _Session:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

            def get(self_inner, url):
                return _CM()

        mocker.patch("modules.utility.get_session", return_value=_Session())
        util.poliswag.db.get_data_from_database.return_value = []
        result = await util.get_new_pokemongo_version()
        assert result == "3.0.0"
        util.poliswag.db.execute_query_to_database.assert_called_once()
