"""The silent-collector alert.

Pure decision logic, so the thresholds are pinned without a database or a
clock. The whole point of the alert is that nobody is watching when it
matters, which is exactly why its edges have to be tested.
"""

from datetime import datetime, timedelta

from modules.tracking_health import decide, message

NOW = datetime(2026, 9, 20, 14, 0)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


class TestSilence:
    def test_a_quiet_night_is_not_an_outage(self):
        # The site has no traffic between about midnight and dawn. Alerting on
        # that would train the reader to ignore the alert.
        action, _ = decide(ago(hours=9), None, NOW)
        assert action is None

    def test_a_full_day_without_events_is(self):
        action, silence = decide(ago(hours=24), None, NOW)
        assert action == "alert"
        assert silence == timedelta(hours=24)

    def test_just_under_a_day_is_not(self):
        action, _ = decide(ago(hours=23, minutes=59), None, NOW)
        assert action is None

    def test_an_empty_table_is_an_outage_not_a_healthy_pause(self):
        action, silence = decide(None, None, NOW)
        assert action == "alert"
        assert silence is None


class TestNotNagging:
    def test_the_same_outage_is_not_repeated_every_minute(self):
        # The scheduler calls this every 60 seconds.
        action, _ = decide(ago(hours=30), ago(hours=1), NOW)
        assert action is None

    def test_a_long_outage_is_mentioned_again_after_twelve_hours(self):
        action, _ = decide(ago(hours=40), ago(hours=12), NOW)
        assert action == "alert"


class TestRecovery:
    def test_recovery_is_announced_only_to_someone_who_was_warned(self):
        action, _ = decide(ago(minutes=5), ago(hours=20), NOW)
        assert action == "recovered"

    def test_a_healthy_collector_says_nothing_at_all(self):
        action, _ = decide(ago(minutes=5), None, NOW)
        assert action is None


class TestMessages:
    def test_the_alert_states_the_silence_and_refuses_to_guess_the_cause(self):
        text = message("alert", timedelta(hours=26))
        assert "26 horas" in text
        assert "não dá para distinguir" in text

    def test_the_alert_handles_an_empty_table_without_inventing_a_duration(self):
        text = message("alert", None)
        assert "todo o histórico" in text
        assert "None" not in text

    def test_recovery_does_not_backfill_the_silent_period(self):
        text = message("recovered", timedelta(minutes=5))
        assert "não prova que não houve visitas" in text


class TestWhatCountsAsAlive:
    def test_only_page_views_count_not_apk_downloads(self, monkeypatch):
        # /pogoleiria.apk writes download counts to the same table; one must
        # not make a dead page-view beacon look alive.
        from unittest.mock import MagicMock

        import modules.tracking_health as tracking_health

        cursor = MagicMock()
        cursor.fetchone.return_value = (NOW,)
        db = MagicMock()
        db.cursor.return_value.__enter__.return_value = cursor
        monkeypatch.setattr(tracking_health, "connect", lambda *_, **__: db)

        assert tracking_health.TrackingHealth()._last_event_sync() == NOW
        query = cursor.execute.call_args_list[-1].args[0]
        assert "event_name = 'tool_view'" in query
