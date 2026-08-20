from unittest.mock import AsyncMock, MagicMock

from modules.cached_bool_setting import CachedBoolSetting


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


class TestSet:
    async def test_set_updates_cache_without_a_read(self):
        setting, poliswag = _setting()

        await setting.set(False)

        assert await setting.get() is False
        poliswag.db.get_data_from_database.assert_not_called()

    async def test_failed_write_does_not_update_cache(self):
        setting, poliswag = _setting()
        poliswag.db.get_data_from_database.return_value = [{"some_flag": 1}]
        poliswag.db.execute_query_to_database.side_effect = Exception("db down")

        await setting.set(False)

        # write failed, so the next read still goes to the DB
        assert await setting.get() is True
        poliswag.db.get_data_from_database.assert_called_once()
