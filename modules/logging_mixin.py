class LoggingMixin:
    """Adds a `_log` shortcut for classes that hold a `poliswag` reference
    and want to write to its shared log file."""

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)
