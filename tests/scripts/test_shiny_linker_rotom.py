import zlib

import pytest

from scripts.shiny_linker import rotom
from scripts.shiny_linker.proto import blob, fields, integer, one


class Wire:
    """A controller socket that answers each request with a canned reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def send_binary(self, data):
        self.sent.append(fields(data))

    def recv(self):
        request_id = one(self.sent[-1], 1)
        return self.replies.pop(0)(request_id)

    def abort(self):
        self.closed = True

    def close(self):
        self.closed = True


def session(replies):
    s = rotom.Session.__new__(rotom.Session)
    s.connection = Wire(replies)
    s.worker_id, s.lat, s.lon, s.host, s._next_id = "w1", 39.7, -8.8, b"", 1
    return s


def login_ok(request_id):
    return integer(1, request_id) + integer(2, 200) + blob(3, integer(2, 3))


def rpc_reply(method, payload, compressed=False):
    def build(request_id):
        if compressed:
            body = zlib.compress(payload)
            entry = integer(1, method) + blob(2, body) + integer(3, 1)
        else:
            entry = integer(1, method) + blob(2, payload)
        return (
            integer(1, request_id)
            + integer(2, 200)
            + blob(4, integer(1, 1) + blob(2, entry))
        )

    return build


def test_idle_workers_counts_only_connected_devices():
    state = {
        "devices": [
            {"is_connected": True, "worker_count": 16, "worker_in_use_count": 9},
            {"is_connected": False, "worker_count": 8, "worker_in_use_count": 0},
        ]
    }
    assert rotom.idle_workers(state) == 7


def test_accounts_in_use_ignores_idle_controllers():
    state = {"controllers": [{"account_username": "a"}, {"account_username": None}, {}]}
    assert rotom.accounts_in_use(state) == {"a"}


def test_login_sends_the_refresh_token_in_the_scanner_wrapper():
    s = session([login_ok])
    s.login("acct", "token")
    request = fields(one(s.connection.sent[0], 3))
    assert one(request, 1) == b"acct"
    assert one(fields(one(request, 3)), 4) == b"token"
    assert one(request, 4) == b"w1"


def test_login_that_does_not_reach_a_token_is_an_error():
    s = session([lambda i: integer(1, i) + integer(2, 200) + blob(3, integer(2, 5))])
    with pytest.raises(RuntimeError, match="login"):
        s.login("acct", "token")


def test_rpc_returns_the_payload_for_its_method():
    s = session([rpc_reply(163, b"details")])
    assert s.rpc(163, b"q") == b"details"


def test_rpc_inflates_a_compressed_payload():
    s = session([rpc_reply(2, b"x" * 500, compressed=True)])
    assert s.rpc(2) == b"x" * 500


def test_rpc_refuses_methods_outside_its_short_list():
    with pytest.raises(ValueError):
        session([]).rpc(165)  # battle start must be impossible to send


def test_a_failed_worker_request_is_an_error():
    s = session([lambda i: integer(1, i) + integer(2, 500)])
    with pytest.raises(RuntimeError, match="500"):
        s.rpc(2)


def controllers(*ids):
    return {
        "controllers": [
            {"id": name, "uuid": f"uuid-{name}", "account_username": f"acct-{name}"}
            for name in ids
        ]
    }


def test_the_worker_to_borrow_comes_from_the_area_with_the_most():
    state = controllers("MarinhaGrande_01", "Leiria_01", "Leiria_03", "Leiria_02")
    assert rotom.to_borrow(state)["id"] == "Leiria_03"


def test_nothing_to_borrow_without_scanning_controllers():
    assert rotom.to_borrow({"controllers": []}) is None
    # A controller with no account is not scanning, and our own is never borrowed.
    state = {
        "controllers": [
            {"id": "Leiria_01", "uuid": "u1", "account_username": None},
            {
                "id": "x",
                "uuid": "u2",
                "account_username": "a",
                "user_agent": rotom.NAME,
            },
        ]
    }
    assert rotom.to_borrow(state) is None


def test_release_one_disconnects_exactly_that_controller(mocker):
    opened = mocker.patch.object(rotom.urllib.request, "urlopen")
    assert rotom.release_one(controllers("Leiria_01", "Leiria_02")) is True
    request = opened.call_args.args[0]
    assert request.get_method() == "PUT"
    assert request.full_url.endswith("/api/controller/uuid-Leiria_02/action/disconnect")


def test_release_one_with_nothing_to_borrow_does_nothing(mocker):
    opened = mocker.patch.object(rotom.urllib.request, "urlopen")
    assert rotom.release_one({"controllers": []}) is False
    opened.assert_not_called()


def test_the_map_request_is_one_the_session_sends():
    s = session([rpc_reply(106, b"map")])
    assert s.rpc(106) == b"map"
