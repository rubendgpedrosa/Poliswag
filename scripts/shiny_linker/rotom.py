"""Rotom-NG as the linker sees it: the status endpoint, and one controller
session on an idle worker.

The session speaks Rotom's controller protocol (UnownHash/RotomProtos): a
registration, a login with the account's stored refresh token, then game
RPCs. It is a second controller beside Dragonite's. It takes an idle worker
when there is one; when Dragonite is using them all, the linker borrows one
(release_one) for the length of a run.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
import zlib

from .proto import blob, fields, integer, one, real

STATUS_URL = "http://127.0.0.1:7072/api/status"
CONTROLLER_URL = "ws://127.0.0.1:7071/controller"
NAME = "poliswag-shiny-linker"
# Seconds a single request may take. WebSocket pings keep a plain read
# timeout alive for ever, so the socket is aborted on an absolute deadline.
DEADLINE = 40
# What this session may send as itself: GET_PLAYER, the map around it, gym
# info, raid details. All four pass the scanner agent's own allowlist.
# Everything else the linker needs goes through the hook (phone.Hook.wrap)
# as a marked GET_PLAYER. Battle start (165) is in neither list.
DIRECT_METHODS = (2, 106, 156, 163)


class NoWorker(RuntimeError):
    """Rotom had no idle worker to give."""


def status():
    with urllib.request.urlopen(STATUS_URL, timeout=5) as response:
        return json.load(response)


def idle_workers(state):
    return sum(
        d.get("worker_count", 0) - d.get("worker_in_use_count", 0)
        for d in state.get("devices", [])
        if d.get("is_connected")
    )


def accounts_in_use(state):
    return {
        c["account_username"]
        for c in state.get("controllers", [])
        if c.get("account_username")
    }


def to_borrow(state):
    """The scanning controller to disconnect when no worker is idle: the
    last one of the area that has the most, so the small areas keep theirs.
    Controller ids look like "Leiria_07"."""
    scanning = [
        c
        for c in state.get("controllers", [])
        if c.get("uuid") and c.get("account_username") and c.get("user_agent") != NAME
    ]
    if not scanning:
        return None
    areas = {}
    for controller in scanning:
        areas.setdefault(str(controller.get("id", "")).rsplit("_", 1)[0], []).append(
            controller
        )
    biggest = max(areas.values(), key=len)
    return max(biggest, key=lambda c: str(c.get("id", "")))


def release_one(state):
    """Disconnect one scanning controller so its worker comes free. Dragonite
    reconnects it by itself and gets a worker back as soon as one is idle,
    which is when the linker's session closes."""
    controller = to_borrow(state)
    if controller is None:
        return False
    url = (
        STATUS_URL.rsplit("/", 1)[0]
        + "/controller/"
        + urllib.parse.quote(controller["uuid"], safe="")
        + "/action/disconnect"
    )
    with urllib.request.urlopen(urllib.request.Request(url, method="PUT"), timeout=5):
        return True


class Session:
    def __init__(self, lat, lon):
        import websocket  # only the linker's venv has it

        self.lat, self.lon = lat, lon
        self.host = b""  # the social proxy's assigned host, kept by lobby.py
        self._next_id = 1
        self.connection = websocket.create_connection(
            CONTROLLER_URL, timeout=45, header=[f"User-Agent: {NAME}"]
        )
        try:
            self.connection.send_binary(blob(1, NAME) + integer(2, 2) + integer(4, 1))
            reply = fields(self.connection.recv())
            if one(reply, 1) != 1:
                raise NoWorker("rotom gave no idle worker")
            self.worker_id = one(reply, 7, b"").decode("utf8")
            if not self.worker_id:
                raise NoWorker("registration returned no worker")
        except BaseException:
            self.connection.close()
            raise

    def _request(self, kind, payload):
        request_id = self._next_id
        self._next_id += 1
        self.connection.send_binary(
            integer(1, request_id)
            + integer(2, kind)
            + blob(3 if kind == 1 else 4, payload)
        )
        timer = threading.Timer(DEADLINE, self.connection.abort)
        timer.daemon = True
        timer.start()
        try:
            expires = time.monotonic() + DEADLINE
            while time.monotonic() < expires:
                data = self.connection.recv()
                if not isinstance(data, bytes):
                    raise RuntimeError("worker did not return a binary response")
                response = fields(data)
                if one(response, 1) != request_id:
                    continue
                if one(response, 2) != 200:
                    raise RuntimeError(
                        f"worker request failed with status {one(response, 2)}"
                    )
                return fields(one(response, 3 if kind == 1 else 4, b""))
            raise TimeoutError("worker response timeout")
        finally:
            timer.cancel()

    def login(self, account, refresh_token):
        # The scanner's auth wrapper carries the stored refresh token in
        # field 4; this is not the game's own PtcOAuthToken message.
        response = self._request(
            1,
            blob(1, account)
            + integer(2, 2)
            + blob(3, blob(4, refresh_token))
            + blob(4, self.worker_id),
        )
        if one(response, 2) != 3:
            raise RuntimeError(f"login did not reach a token ({one(response, 2)})")
        self.host = b""

    def rpc(self, method, payload=b""):
        if method not in DIRECT_METHODS:
            raise ValueError(f"method {method} is not one this session sends")
        response = self._request(
            2,
            blob(1, integer(1, method) + blob(2, payload))
            + real(2, self.lat)
            + real(3, self.lon),
        )
        if one(response, 1) != 1:
            raise RuntimeError(f"RPC dispatch failed with status {one(response, 1)}")
        matches = [
            fields(x) for x in response.get(2, []) if one(fields(x), 1) == method
        ]
        if len(matches) != 1:
            raise RuntimeError("RPC response method was not uniquely matched")
        data = one(matches[0], 2, b"")
        if one(matches[0], 3, 0):
            inflater = zlib.decompressobj(47)
            data = inflater.decompress(data, 4_000_001)
            if len(data) > 4_000_000 or not inflater.eof or inflater.unused_data:
                raise RuntimeError(
                    "compressed response exceeded bounds or was incomplete"
                )
        return data

    def close(self):
        self.connection.close()
