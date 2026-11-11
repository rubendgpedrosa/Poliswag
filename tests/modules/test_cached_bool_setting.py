from unittest.mock import AsyncMock, MagicMock

from modules.cached_bool_setting import MAX_AGE_SECONDS, CachedBoolSetting


def _setting(**kwargs):
    poliswag = MagicMock()
    poliswag.db = AsyncMock()
    poliswag.utility.log_to_file = MagicMock()
    return CachedBoolSetting(poliswag, "some_flag", **kwargs), poliswag


class TestGet:
    async def test_reads_and_caches(self):
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.return_value = [{"some_flag": 1}]

        assert await setting.get() is True
        assert await setting.get() is True

        poliswag.db.get_data_from_database.assert_called_once()

    async def test_no_rows_returns_default(self):
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.return_value = []

        assert await setting.get() is True

    async def test_default_can_be_false(self):
        setting, poliswag = _setting(default=False)
        poliswag.db.get_data_from_database.return_value = []

        assert await setting.get() is False

    async def test_failed_read_fails_open_and_is_not_cached(self):
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.side_effect = Exception("db down")

        assert await setting.get() is True
        assert await setting.get() is True

        assert poliswag.db.get_data_from_database.call_count == 2
        poliswag.utility.log_to_file.assert_called_with(
            "Failed to read some_flag, defaulting to enabled: db down", "ERROR"
        )

    async def test_failed_read_with_false_default_logs_disabled(self):
        setting, poliswag = _setting(default=False)
        poliswag.db.get_data_from_database.side_effect = Exception("db down")

        assert await setting.get() is False
        poliswag.utility.log_to_file.assert_called_with(
            "Failed to read some_flag, defaulting to disabled: db down", "ERROR"
        )


class TestTheColumnIsTheSwitch:
    """No command sets it any more: a change made in SQL has to be noticed."""

    async def test_a_changed_column_is_read_again_after_five_minutes(self, mocker):
        clock = mocker.patch("modules.cached_bool_setting.time.monotonic")
        clock.return_value = 1000.0
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.return_value = [{"some_flag": 1}]
        assert await setting.get() is True

        poliswag.db.get_data_from_database.return_value = [{"some_flag": 0}]
        clock.return_value += MAX_AGE_SECONDS - 1
        assert await setting.get() is True  # still the cached value
        clock.return_value += 1
        assert await setting.get() is False
        assert poliswag.db.get_data_from_database.call_count == 2

    async def test_a_failed_reread_keeps_the_last_value(self, mocker):
        clock = mocker.patch("modules.cached_bool_setting.time.monotonic")
        clock.return_value = 1000.0
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.return_value = [{"some_flag": 0}]
        assert await setting.get() is False

        # The database is down: a switch that was off must not come back on.
        poliswag.db.get_data_from_database.side_effect = Exception("db down")
        clock.return_value += MAX_AGE_SECONDS
        assert await setting.get() is False
        poliswag.utility.log_to_file.assert_not_called()

    async def test_and_is_tried_again_on_the_next_call(self, mocker):
        clock = mocker.patch("modules.cached_bool_setting.time.monotonic")
        clock.return_value = 1000.0
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.return_value = [{"some_flag": 1}]
        await setting.get()
        poliswag.db.get_data_from_database.side_effect = [
            Exception("db down"),
            [{"some_flag": 0}],
        ]
        clock.return_value += MAX_AGE_SECONDS
        assert await setting.get() is True
        assert await setting.get() is False
