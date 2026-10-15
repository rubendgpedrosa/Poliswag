"""Keep generic backup alerts on HA and explicitly route game alerts to Discord."""

import json

import pytest

from scripts import notify_owner


@pytest.fixture
def routes(mocker):
    env = mocker.patch.object(
        notify_owner.dm_owner,
        "read_env",
        return_value={"OWNER_ALERT_ENDPOINT": "http://ha.test/alert"},
    )
    http = mocker.patch.object(notify_owner.urllib.request, "urlopen")
    dm = mocker.patch.object(notify_owner.dm_owner, "main")
    return env, http, dm


def test_discord_flag_skips_home_assistant_and_uses_dm(routes, mocker):
    env, http, dm = routes
    mocker.patch.object(
        notify_owner.sys, "argv", ["notify", "--discord", "**down**", "Tracking"]
    )
    notify_owner.main()
    env.assert_not_called()
    http.assert_not_called()
    dm.assert_called_once_with()
    assert notify_owner.sys.argv == ["notify", "**down**", "Tracking"]


def test_generic_alert_still_uses_home_assistant(routes, mocker):
    _, http, dm = routes
    mocker.patch.object(notify_owner.sys, "argv", ["notify", "**backup**", "Backup"])
    notify_owner.main()
    request = http.call_args.args[0]
    assert request.full_url == "http://ha.test/alert"
    assert json.loads(request.data) == {
        "title": "Backup",
        "message": "backup",
        "tag": "poliswag-host",
    }
    dm.assert_not_called()


@pytest.mark.parametrize("endpoint", [None, "http://ha.test/alert"])
def test_generic_alert_falls_back_when_ha_unavailable(routes, mocker, endpoint):
    env, http, dm = routes
    env.return_value = {"OWNER_ALERT_ENDPOINT": endpoint}
    http.side_effect = OSError("unavailable")
    mocker.patch.object(notify_owner.sys, "argv", ["notify", "backup"])
    notify_owner.main()
    dm.assert_called_once()
    assert notify_owner.sys.argv == ["notify", "backup", "Poliswag"]


def test_discord_failure_propagates_without_ha_fallback(routes, mocker):
    env, http, dm = routes
    dm.side_effect = OSError("Discord unavailable")
    mocker.patch.object(notify_owner.sys, "argv", ["notify", "--discord", "down"])
    with pytest.raises(OSError, match="Discord unavailable"):
        notify_owner.main()
    env.assert_not_called()
    http.assert_not_called()
