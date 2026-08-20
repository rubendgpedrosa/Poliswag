import pytest

from modules.config import Config


class TestValidate:
    def test_passes_when_all_required_vars_are_set(self, mocker):
        mocker.patch.object(Config, "DISCORD_API_KEY", "token")
        mocker.patch.object(Config, "DB_HOST", "host")
        mocker.patch.object(Config, "DB_USER", "user")
        mocker.patch.object(Config, "DB_PASSWORD", "pw")
        mocker.patch.object(Config, "DB_POLISWAG", "poliswag")
        mocker.patch.object(Config, "DB_SCANNER_NAME", "scanner")

        Config.validate()  # must not raise

    def test_raises_with_every_missing_var_named(self, mocker):
        mocker.patch.object(Config, "DISCORD_API_KEY", None)
        mocker.patch.object(Config, "DB_HOST", "host")
        mocker.patch.object(Config, "DB_USER", "")
        mocker.patch.object(Config, "DB_PASSWORD", "pw")
        mocker.patch.object(Config, "DB_POLISWAG", "poliswag")
        mocker.patch.object(Config, "DB_SCANNER_NAME", "scanner")

        with pytest.raises(RuntimeError) as exc_info:
            Config.validate()

        message = str(exc_info.value)
        assert "DISCORD_API_KEY" in message
        assert "DB_USER" in message
        assert "DB_HOST" not in message
