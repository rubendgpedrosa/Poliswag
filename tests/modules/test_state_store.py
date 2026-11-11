"""Tests for modules.state_store: the JSON files that survive a restart."""

import json
import os
from unittest.mock import MagicMock

from modules.config import Config
from modules.state_store import EPISODE_MAX_AGE, StateFile, still_running


class TestStateFile:
    def test_lives_in_the_state_directory(self):
        state = StateFile("stack-recovery")
        assert str(state.path) == os.path.join(Config.STATE_DIR, "stack-recovery.json")

    def test_nothing_saved_yet_is_an_empty_state(self):
        assert StateFile("fresh").load() == {}

    def test_what_is_saved_is_what_the_next_process_loads(self):
        StateFile("device").save({"offline_since": 1234.5, "seen_at": 1300})
        assert StateFile("device").load() == {"offline_since": 1234.5, "seen_at": 1300}

    def test_an_empty_state_needs_no_file(self):
        state = StateFile("idle")
        state.load()
        state.save({})
        assert not state.path.exists()

    def test_clearing_a_state_empties_its_file(self):
        state = StateFile("episode")
        state.save({"red_since": 1.0})
        state.save({})
        assert StateFile("episode").load() == {}

    def test_an_unchanged_state_is_not_written_again(self, mocker):
        state = StateFile("same")
        state.save({"a": 1})
        replace = mocker.patch("modules.state_store.os.replace")
        state.save({"a": 1})
        replace.assert_not_called()
        state.save({"a": 2})
        replace.assert_called_once()

    def test_loading_counts_as_what_is_on_disk(self, mocker):
        StateFile("loaded").save({"a": 1})
        state = StateFile("loaded")
        state.load()
        replace = mocker.patch("modules.state_store.os.replace")
        state.save({"a": 1})
        replace.assert_not_called()

    def test_the_file_is_replaced_whole_and_no_temporary_is_left(self):
        state = StateFile("atomic")
        state.save({"a": 1})
        state.save({"a": 2})
        assert json.loads(state.path.read_text()) == {"a": 2}
        assert [p.name for p in state.path.parent.iterdir()] == ["atomic.json"]

    def test_a_damaged_file_is_an_empty_state_and_gets_rewritten(self):
        state = StateFile("damaged")
        state.path.parent.mkdir(parents=True)
        state.path.write_text("{not json")
        assert state.load() == {}
        state.save({})
        assert json.loads(state.path.read_text()) == {}

    def test_a_file_that_is_not_an_object_is_an_empty_state(self):
        state = StateFile("list")
        state.path.parent.mkdir(parents=True)
        state.path.write_text("[1, 2]")
        assert state.load() == {}

    def test_a_failed_write_is_logged_and_tried_again_next_time(self, mocker):
        log = MagicMock()
        state = StateFile("unwritable", log)
        mocker.patch(
            "modules.state_store.tempfile.mkstemp", side_effect=OSError("read-only")
        )
        state.save({"a": 1})
        message, level = log.call_args.args
        assert level == "ERROR" and "unwritable.json" in message
        mocker.stopall()
        state.save({"a": 1})
        assert StateFile("unwritable").load() == {"a": 1}

    def test_a_failed_replace_leaves_no_temporary_behind(self, mocker):
        state = StateFile("replace")
        mocker.patch("modules.state_store.os.replace", side_effect=OSError("busy"))
        state.save({"a": 1})
        assert list(state.path.parent.iterdir()) == []


class TestStillRunning:
    def test_an_episode_seen_a_moment_ago_continues(self):
        assert still_running({"seen_at": 1000.0}, now=1060.0)

    def test_up_to_the_limit(self):
        assert still_running({"seen_at": 1000.0}, now=1000.0 + EPISODE_MAX_AGE)
        assert not still_running({"seen_at": 1000.0}, now=1001.0 + EPISODE_MAX_AGE)

    def test_nothing_recorded_does_not(self):
        assert not still_running({}, now=1000.0)
        assert not still_running({"seen_at": "yesterday"}, now=1000.0)

    def test_a_time_in_the_future_does_not(self):
        # The clock went back: nothing to trust.
        assert not still_running({"seen_at": 2000.0}, now=1000.0)

    def test_defaults_to_the_clock(self, mocker):
        mocker.patch("modules.state_store.time.time", return_value=5000.0)
        assert still_running({"seen_at": 4990.0})
