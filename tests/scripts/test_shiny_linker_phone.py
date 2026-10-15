import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.shiny_linker import phone
from scripts.shiny_linker.proto import fields, one

HOOK = Path(phone.__file__).with_name("native-method-redirect.js")
HARNESS = Path(__file__).with_name("shiny_hook_harness.cjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_hook_only_redirects_marked_requests_to_listed_methods():
    out = json.loads(
        subprocess.check_output(["node", str(HARNESS), str(HOOK)], timeout=20)
    )
    assert out["lobbyJoin"] == {"method": 159, "length": 2, "bytes": [9, 9]}
    assert out["invite"] == 1504
    assert out["search"] == 5012
    # Left as an ordinary GET_PLAYER: the replacement method is never written.
    for untouched in (
        "battleStart",
        "friendRemoval",
        "wrongNonce",
        "notGetPlayer",
        "plainGetPlayer",
    ):
        assert out[untouched] is None, untouched
    assert out["log"][0] == "OWNED_REDIRECT_READY"
    assert out["log"][-2:] == ["UNPATCHED", "OWNED_REDIRECT_DETACHED requested"]


@pytest.fixture
def shell(mocker):
    """Every adb/docker command the module runs, and what each one prints."""
    calls = []
    answers = {}

    def run(argv, **_):
        calls.append(argv)
        text = " ".join(argv)
        for needle, answer in answers.items():
            if needle in text:
                if isinstance(answer, Exception):
                    raise answer
                return MagicMock(returncode=0, stdout=answer.encode(), stderr=b"")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    mocker.patch.object(phone.subprocess, "run", side_effect=run)
    mocker.patch.object(phone.time, "sleep")
    return calls, answers


def docker_calls(calls):
    return [c[1] for c in calls if c[0] == "docker"]


def test_pids_and_version_are_read_from_the_phone(shell):
    _, answers = shell
    answers["pidof com.nianticlabs.pokemongo"] = "16473\n"
    answers["pidof com.pokemod.aegis:mapping"] = ""
    answers["dumpsys package"] = "  versionName=0.431.0\n"
    assert phone.game_pid() == 16473
    assert phone.mapping_pid() is None
    assert phone.game_version() == "0.431.0"


def test_an_unreachable_phone_reads_as_not_running(shell, mocker):
    mocker.patch.object(
        phone.subprocess,
        "run",
        return_value=MagicMock(
            returncode=1, stdout=b"", stderr=b"error: device offline"
        ),
    )
    assert phone.game_pid() is None
    assert phone.game_version() is None


def test_quiet_scanner_unpauses_even_when_the_body_raises(shell):
    calls, _ = shell
    with pytest.raises(RuntimeError):
        with phone.quiet_scanner():
            raise RuntimeError("boom")
    assert docker_calls(calls) == ["pause", "unpause"]


def test_attach_pauses_starts_the_injector_and_waits_for_ready(shell):
    calls, answers = shell
    answers["echo $!"] = "4242\n"
    answers["cat /data/data"] = "OWNED_REDIRECT_READY\n"
    hook = phone.Hook()
    hook.attach(16473)
    assert hook.injector_pid == 4242
    assert docker_calls(calls) == ["pause", "unpause"]
    pushed = [c for c in calls if "push" in c]
    assert len(pushed) == 1


def test_attach_reports_a_setup_error_and_still_unpauses(shell):
    calls, answers = shell
    answers["echo $!"] = "4242\n"
    answers["cat /data/data"] = "OWNED_REDIRECT_SETUP_ERROR no module\n"
    with pytest.raises(phone.HookError, match="setup"):
        phone.Hook().attach(16473)
    assert docker_calls(calls) == ["pause", "unpause"]


def test_wrap_marks_the_request_with_nonce_method_and_body():
    hook = phone.Hook()
    envelope = fields(hook.wrap(159, b"body"))
    assert one(envelope, 4095) == hook.nonce.encode()
    assert one(envelope, 4094) == b"body"
    assert one(envelope, 4093) == 159


def test_detach_asks_then_kills_the_injector_only_after_the_hook_left(shell):
    calls, answers = shell
    hook = phone.Hook()
    hook.injector_pid = 4242
    answers["cat /data/data"] = (
        "OWNED_REDIRECT_READY\nOWNED_REDIRECT_DETACHED requested\n"
    )
    sent = []
    # First log read says attached, so detach has to ask.
    logs = iter(["OWNED_REDIRECT_READY\n", "OWNED_REDIRECT_DETACHED requested\n"])
    hook.log = lambda: next(logs)
    assert hook.detach(sent.append) is True
    assert one(fields(sent[0]), 4093) == 0
    assert any("kill 4242" in " ".join(c) for c in calls)
    assert docker_calls(calls) == ["pause", "unpause"]


def test_detach_leaves_the_injector_alone_when_the_hook_did_not_leave(shell):
    calls, _ = shell
    hook = phone.Hook()
    hook.injector_pid = 4242
    hook.log = lambda: "OWNED_REDIRECT_READY\n"
    assert hook.detach(lambda payload: None) is False
    assert not any("kill" in " ".join(c) for c in calls)
    assert docker_calls(calls) == ["pause", "unpause"]


def test_detach_without_an_attach_is_a_no_op(shell):
    calls, _ = shell
    assert phone.Hook().detach(lambda payload: None) is True
    assert calls == []


def test_adb_failure_preserves_bounded_stderr_and_exit_status(shell, mocker):
    mocker.patch.object(
        phone.subprocess,
        "run",
        return_value=MagicMock(
            returncode=1, stdout=b"", stderr=b"error: device offline\n" + b"x" * 1000
        ),
    )
    with pytest.raises(
        phone.PhoneError, match=r"adb failed \(exit 1\): error: device offline"
    ) as caught:
        phone.adb("pidof hidden-command-argument")
    assert len(str(caught.value)) < 550
    assert "hidden-command-argument" not in str(caught.value)


def test_unreachable_phone_logs_transport_failure(shell, mocker, caplog):
    mocker.patch.object(
        phone.subprocess,
        "run",
        return_value=MagicMock(
            returncode=1, stdout=b"", stderr=b"error: device offline"
        ),
    )
    assert phone.game_pid() is None
    assert "device offline" in caplog.text


def test_failed_push_is_a_hook_error_with_tool_details(shell):
    calls, answers = shell
    answers["push"] = subprocess.CalledProcessError(
        1, ["adb", "push", "private-argument"], stderr=b"error: device offline"
    )
    with pytest.raises(
        phone.HookError, match=r"adb failed \(exit 1\): error: device offline"
    ) as caught:
        phone.Hook().attach(16473)
    assert "private-argument" not in str(caught.value)
    assert docker_calls(calls) == []


def test_attach_timeout_is_a_hook_error_with_duration(shell):
    _, answers = shell
    answers["push"] = subprocess.TimeoutExpired(["adb", "push", "private-argument"], 30)
    with pytest.raises(phone.HookError, match="adb timed out after 30s"):
        phone.Hook().attach(16473)


def test_unknown_subprocess_does_not_expose_arguments_or_stderr():
    error = subprocess.CalledProcessError(
        1, ["curl", "secret-token", "rpc-payload"], stderr=b"secret-token rpc-payload"
    )
    assert phone.subprocess_detail(error) == "subprocess failed (exit 1)"


def test_generic_subprocess_failure_has_a_safe_fallback():
    assert (
        phone.subprocess_detail(subprocess.SubprocessError("private detail"))
        == "subprocess failed"
    )
