from unittest.mock import MagicMock

from modules.logging_mixin import LoggingMixin


class _Thing(LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag


def test_log_forwards_to_utility_log_to_file():
    poliswag = MagicMock()
    thing = _Thing(poliswag)

    thing._log("boom")

    poliswag.utility.log_to_file.assert_called_once_with("boom", "ERROR")


def test_log_level_is_overridable():
    poliswag = MagicMock()
    thing = _Thing(poliswag)

    thing._log("heads up", "INFO")

    poliswag.utility.log_to_file.assert_called_once_with("heads up", "INFO")
