import json
import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from modules.hermes_client import HermesBusy, HermesClient, HermesUnavailable


@asynccontextmanager
async def server(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def test_runs_contract_history_idempotency_and_final_output():
    calls = []

    async def handler(request):
        calls.append((request.method, request.path, dict(request.headers)))
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers["X-Hermes-Session-Key"] == "poliswag:owner:123"
        if request.method == "POST":
            body = await request.json()
            assert set(body) == {"input", "instructions", "session_id"}
            assert body["session_id"] == "existing-session"
            assert "administrador de sistemas" in body["instructions"]
            assert "Nunca reinicies o host" in body["instructions"]
            return web.json_response(
                {"run_id": "run-1", "status": "queued", "replayed": True}, status=202
            )
        return web.json_response(
            {"run_id": "run-1", "status": "completed", "output": "Final"}
        )

    async with server(handler) as url:
        client = HermesClient(url, "test-key", 123, "existing-session")
        run_a, text_a = await client.reply("DM", message_id=10)
        run_b, text_b = await client.reply("Another DM", message_id=11)
        assert (run_a, text_a) == ("run-1", "Final")
        assert (run_b, text_b) == ("run-1", "Final")
    assert [c[0] for c in calls] == ["POST", "GET", "POST", "GET"]
    assert calls[0][2]["Idempotency-Key"] == "discord:10"
    assert calls[2][2]["Idempotency-Key"] == "discord:11"


async def test_real_approval_exact_binding_only_once(monkeypatch):
    approval = {
        "event": "approval.request",
        "run_id": "run-1",
        "request_id": "req-1",
        "choices": ["once", "deny"],
        "command": "systemctl restart example",
    }
    polls, callbacks, decisions = [], [], []

    async def handler(request):
        if request.path.endswith("/approval"):
            decisions.append(await request.json())
            return web.json_response({"ok": True})
        if request.method == "POST":
            return web.json_response({"run_id": "run-1"}, status=202)
        polls.append(True)
        if len(polls) <= 2:
            return web.json_response(
                {"status": "waiting_for_approval", "approval": approval}
            )
        return web.json_response({"status": "completed", "output": "Done"})

    async def callback(data):
        callbacks.append(data)
        return "once"

    async with server(handler) as url:
        client = HermesClient(url, "key", 123)
        monkeypatch.setattr(client, "POLL_INTERVAL", 0)
        run_id, text = await client.reply(
            "fix", message_id=1, approval_callback=callback
        )
        assert (run_id, text) == ("run-1", "Done")
    assert callbacks == [approval]
    assert decisions == [{"request_id": "req-1", "choice": "once"}]


@pytest.mark.parametrize("kind", ["timeout", "invalid", "absent"])
async def test_approval_fails_closed_to_deny(kind, monkeypatch):
    decisions = []

    async def handler(request):
        if request.path.endswith("/approval"):
            decisions.append(await request.json())
            return web.json_response({"ok": True})
        if request.method == "POST":
            return web.json_response({"run_id": "r"}, status=202)
        if not decisions:
            return web.json_response(
                {
                    "status": "waiting_for_approval",
                    "approval": {
                        "event": "approval.request",
                        "run_id": "r",
                        "request_id": "q",
                        "choices": ["once", "deny"],
                    },
                }
            )
        return web.json_response({"status": "completed", "output": "Denied"})

    async def callback(data):
        if kind == "timeout":
            await asyncio.sleep(1)
        return "always"

    async with server(handler) as url:
        client = HermesClient(url, "key", 123)
        monkeypatch.setattr(client, "APPROVAL_TIMEOUT", 0.001)
        monkeypatch.setattr(client, "POLL_INTERVAL", 0)
        await client.reply(
            "fix",
            message_id=1,
            approval_callback=None if kind == "absent" else callback,
        )
    assert decisions == [{"request_id": "q", "choice": "deny"}]


async def test_mismatched_approval_never_calls_callback():
    from unittest.mock import AsyncMock

    callback = AsyncMock()

    async def handler(request):
        if request.method == "POST":
            return web.json_response({"run_id": "r"}, status=202)
        return web.json_response(
            {
                "status": "waiting_for_approval",
                "approval": {
                    "event": "approval.request",
                    "run_id": "forged",
                    "request_id": "q",
                    "choices": ["once", "deny"],
                },
            }
        )

    async with server(handler) as url:
        with pytest.raises(HermesUnavailable):
            await HermesClient(url, "key", 123).reply(
                "fix", message_id=1, approval_callback=callback
            )
    callback.assert_not_awaited()


async def test_missing_credentials_never_opens_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No HTTP session should be created")

    monkeypatch.setattr("modules.hermes_client.aiohttp.ClientSession", forbidden)
    with pytest.raises(HermesUnavailable):
        await HermesClient("http://unused", "", 123).reply("secret", message_id=1)


@pytest.mark.parametrize("status", [401, 500, 307])
async def test_http_error_no_body_leak_or_reexecution(status):
    calls = []

    async def handler(request):
        calls.append(request.path)
        return web.Response(
            status=status, text="SECRET", headers={"Location": "/elsewhere"}
        )

    async with server(handler) as url:
        with pytest.raises(HermesUnavailable) as exc:
            await HermesClient(url, "key", 123).reply("fix", message_id=1)
    assert calls == ["/v1/runs"]
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        {"status": "running", "output": "secret"},
        {"status": "completed", "output": []},
        {"status": "completed", "output": ""},
    ],
)
def test_rejects_malformed_or_nonfinal_output(data):
    with pytest.raises(HermesUnavailable):
        HermesClient.final_text(data)


@pytest.mark.parametrize(
    "extra",
    [
        {"partial": True},
        {"completed": False},
        {"interrupted": True},
        {"status": "interrupted"},
    ],
)
def test_partial_cannot_masquerade_as_success(extra):
    result = HermesClient.final_text(
        {"status": "completed", "output": "Useful", **extra}
    )
    assert result.startswith("⚠️") and "Useful" in result


async def test_fifo_single_turn_bounded_waiters_and_cancellation(monkeypatch):
    client = HermesClient("unused", "key", 123)
    started, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def request(text, message_id, channel_id, approval_callback, on_event):
        seen.append(text)
        if text == "0":
            started.set()
            await release.wait()
        return text

    monkeypatch.setattr(client, "_request", request)
    tasks = [asyncio.create_task(client.reply("0", message_id=0))]
    await started.wait()
    for i in range(1, 6):
        tasks.append(asyncio.create_task(client.reply(str(i), message_id=i)))
        await asyncio.sleep(0)
    with pytest.raises(HermesBusy):
        await client.reply("overflow", message_id=99)
    tasks[1].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[1]
    assert client._pending == 5
    release.set()
    assert await asyncio.gather(tasks[0], *tasks[2:]) == ["0", "2", "3", "4", "5"]
    assert seen == ["0", "2", "3", "4", "5"]
    assert client._pending == 0


async def test_transient_poll_disconnect_retries_without_readmission(monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return web.json_response({"run_id": "r"}, status=202)
        if calls == ["POST", "GET"]:
            request.transport.close()
            return web.Response()
        return web.json_response({"status": "completed", "output": "Done"})

    async with server(handler) as url:
        client = HermesClient(url, "key", 123)
        monkeypatch.setattr(client, "POLL_INTERVAL", 0)
        run_id, text = await client.reply("fix", message_id=1)
        assert (run_id, text) == ("r", "Done")
    assert calls.count("POST") == 1
    assert calls.count("GET") >= 2


async def test_admission_disconnect_reuses_identical_idempotency_payload(monkeypatch):
    calls = []

    async def handler(request):
        if request.method == "POST":
            calls.append((request.headers["Idempotency-Key"], await request.json()))
            if len(calls) == 1:
                request.transport.close()
                return web.Response()
            return web.json_response({"run_id": "r", "replayed": True}, status=202)
        return web.json_response({"status": "completed", "output": "Done"})

    async with server(handler) as url:
        client = HermesClient(url, "key", 123)
        monkeypatch.setattr(client, "POLL_INTERVAL", 0)
        run_id, text = await client.reply("fix", message_id=11)
        assert (run_id, text) == ("r", "Done")
    assert len(calls) == 2
    assert calls[0] == calls[1]


async def test_execution_deadline_does_not_readmit(monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request.method)
        return web.json_response({"run_id": "r", "status": "running"}, status=202)

    async with server(handler) as url:
        client = HermesClient(url, "key", 123)
        monkeypatch.setattr(client, "RUN_TIMEOUT", 0)
        with pytest.raises(HermesUnavailable):
            await client.reply("fix", message_id=1)
    assert calls == ["POST"]


def test_persist_forget_and_list_pending(tmp_path):
    client = HermesClient("http://unused", "key", 123, runs_dir=str(tmp_path))
    client._persist("run-9", 777, 55)
    pending = client.list_pending()
    assert [(r["run_id"], r["channel_id"], r["message_id"]) for r in pending] == [
        ("run-9", 777, 55)
    ]
    client.forget("run-9")
    assert client.list_pending() == []


def test_list_pending_ignores_malformed_records(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "missing.json").write_text('{"run_id": "x"}')
    client = HermesClient("http://unused", "key", 123, runs_dir=str(tmp_path))
    client._persist("run-ok", 1, 2)
    assert [r["run_id"] for r in client.list_pending()] == ["run-ok"]


async def test_recover_pending_delivers_and_forgets(tmp_path):
    async def handler(request):
        if request.method == "POST":
            return web.json_response({"run_id": "run-1"}, status=202)
        return web.json_response({"status": "completed", "output": "Late result"})

    delivered = []

    async def on_terminal(channel_id, text):
        delivered.append((channel_id, text))
        return True

    async with server(handler) as url:
        client = HermesClient(url, "key", 123, runs_dir=str(tmp_path))
        client._persist("run-1", 777, 55)
        await client.recover_pending(on_terminal)
    assert delivered == [(777, "Late result")]
    assert client.list_pending() == []


async def test_recover_pending_keeps_record_when_delivery_fails(tmp_path):
    async def handler(request):
        return web.json_response({"status": "completed", "output": "Late"})

    async def on_terminal(channel_id, text):
        return False

    async with server(handler) as url:
        client = HermesClient(url, "key", 123, runs_dir=str(tmp_path))
        client._persist("run-1", 777, 55)
        await client.recover_pending(on_terminal)
    assert [r["run_id"] for r in client.list_pending()] == ["run-1"]


async def test_recover_pending_auto_denies_stale_approval(tmp_path, monkeypatch):
    decisions = []

    async def handler(request):
        if request.path.endswith("/approval"):
            decisions.append(await request.json())
            return web.json_response({"ok": True})
        if request.method == "POST":
            return web.json_response({"run_id": "run-1"}, status=202)
        if not decisions:
            return web.json_response(
                {
                    "status": "waiting_for_approval",
                    "approval": {
                        "event": "approval.request",
                        "run_id": "run-1",
                        "request_id": "q",
                        "choices": ["once", "deny"],
                    },
                }
            )
        return web.json_response({"status": "completed", "output": "Denied"})

    delivered = []

    async def on_terminal(channel_id, text):
        delivered.append(text)
        return True

    async with server(handler) as url:
        client = HermesClient(url, "key", 123, runs_dir=str(tmp_path))
        monkeypatch.setattr(client, "POLL_INTERVAL", 0)
        client._persist("run-1", 777, 55)
        await client.recover_pending(on_terminal)
    assert decisions == [{"request_id": "q", "choice": "deny"}]
    assert delivered == ["Denied"]


async def test_recover_pending_drops_stale_record(tmp_path):
    import json as _json

    async def handler(request):
        return web.json_response({"status": "completed", "output": "Never sent"})

    sent = []

    async def on_terminal(channel_id, text):
        sent.append(text)
        return True

    async with server(handler) as url:
        client = HermesClient(url, "key", 123, runs_dir=str(tmp_path))
        client._persist("run-old", 777, 55)
        record_path = client._record_path("run-old")
        data = _json.loads(record_path.read_text())
        data["created_at"] -= client.MAX_RECORD_AGE + 1
        record_path.write_text(_json.dumps(data))
        await client.recover_pending(on_terminal)
    assert sent == []
    assert client.list_pending() == []


def _sse(*events):
    body = ": open\n\n"
    for seq, event in enumerate(events):
        body += f"id: {seq}\ndata: {json.dumps({'run_id': 'run-1', 'seq': seq, **event})}\n\n"
    return body + ": stream closed\n\n"


async def test_progress_relays_sse_events_and_polled_status():
    seen = []
    polls = []
    stream_done = asyncio.Event()

    async def handler(request):
        if request.method == "POST":
            return web.json_response(
                {"run_id": "run-1", "status": "queued"}, status=202
            )
        if request.path.endswith("/events"):
            assert request.headers["X-Hermes-Session-Key"] == "poliswag:owner:123"
            response = web.Response(
                text=_sse(
                    {"event": "tool.started", "tool": "terminal", "preview": "ls"},
                    {"event": "tool.completed", "tool": "terminal", "duration": 0.5},
                    {"event": "message.interim", "text": "A verificar"},
                ),
                content_type="text/event-stream",
            )
            stream_done.set()
            return response
        polls.append(1)
        if not stream_done.is_set() or len(polls) < 3:
            return web.json_response({"run_id": "run-1", "status": "running"})
        return web.json_response(
            {"run_id": "run-1", "status": "completed", "output": "Feito"}
        )

    async with server(handler) as url:
        client = HermesClient(url, "test-key", 123)
        client.POLL_INTERVAL = 0.01
        result = await client.reply("DM", message_id=10, on_event=seen.append)
    assert result == ("run-1", "Feito")
    kinds = [event["event"] for event in seen]
    assert kinds[:2] == ["run.starting", "run.admitted"]
    assert {"tool.started", "tool.completed", "message.interim"} <= set(kinds)
    statuses = [event["status"] for event in seen if event["event"] == "status"]
    assert statuses == ["running", "completed"]
    assert client.active_run is None


async def test_broken_event_stream_and_callback_never_affect_result():
    async def handler(request):
        if request.method == "POST":
            return web.json_response(
                {"run_id": "run-1", "status": "queued"}, status=202
            )
        if request.path.endswith("/events"):
            return web.Response(status=404)
        return web.json_response(
            {"run_id": "run-1", "status": "completed", "output": "Feito"}
        )

    def explode(event):
        raise RuntimeError("display bug")

    async with server(handler) as url:
        client = HermesClient(url, "test-key", 123)
        assert await client.reply("DM", message_id=10, on_event=explode) == (
            "run-1",
            "Feito",
        )


async def test_stop_and_steer_post_to_exact_run():
    calls = []

    async def handler(request):
        calls.append((request.path, await request.json()))
        if request.path.endswith("/steer"):
            return web.json_response({"run_id": "run-1", "accepted": True})
        return web.json_response({"run_id": "run-1", "status": "stopping"})

    async with server(handler) as url:
        client = HermesClient(url, "test-key", 123)
        assert await client.steer("run-1", "  usa a outra porta  ")
        assert await client.stop("run-1")
        assert not await client.steer("run-1", "   ")
        assert not await client.stop("../evil")
    assert calls == [
        ("/v1/runs/run-1/steer", {"input": "usa a outra porta"}),
        ("/v1/runs/run-1/stop", {}),
    ]


async def test_refused_steer_returns_false():
    async def handler(request):
        return web.json_response({"error": {"message": "secret"}}, status=409)

    async with server(handler) as url:
        client = HermesClient(url, "test-key", 123)
        assert not await client.steer("run-1", "x")
