"""Validate routing, delivery transitions and authenticated webhook handling."""

import copy
from http.client import HTTPConnection
from http.server import HTTPServer
import json
import stat
from threading import Thread

import pytest

from scripts import monitoring_discord_relay as relay


def payload(status="firing", host="pogoleiria.pt", fingerprint="abc"):
    return {
        "alerts": [
            {
                "status": status,
                "fingerprint": fingerprint,
                "labels": {"host": host, "alertname": "SiteUnavailable"},
                "annotations": {
                    "summary": "Serviço indisponível",
                    "description": "Sem resposta",
                },
            }
        ]
    }


def test_only_delivered_transitions_send_and_state_is_private(tmp_path):
    state = tmp_path / "state.json"
    sent = []

    def run(status):
        return relay.process_alerts(payload(status), send=sent.append, state_path=state)

    assert run("resolved") == 0
    assert run("firing") == 1
    assert run("firing") == 0
    assert run("resolved") == 1
    assert run("resolved") == 0
    assert run("firing") == 1
    assert len(sent) == 3
    assert "Problema detetado" in sent[0]["description"]
    assert "Recuperado" in sent[1]["description"]
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "host",
    [
        "evil.pt",
        "pogoleiria.pt.evil.test",
        "notpogoleiria.pt",
        "",
        "https://user@pogoleiria.pt",
        "pogoleiria.pt:bad",
    ],
)
def test_rejects_entire_batch_before_delivery(tmp_path, host):
    body = payload()
    body["alerts"] += payload(host=host)["alerts"]
    sent = []
    with pytest.raises(ValueError):
        relay.process_alerts(body, send=sent.append, state_path=tmp_path / "state")
    assert sent == []


@pytest.mark.parametrize(
    "host", ["pogoleiria.pt", "mapa.pogoleiria.pt", "https://POGOLEIRIA.PT:443/status"]
)
def test_accepts_project_hosts_and_stable_fingerprint_fallback(tmp_path, host):
    body = payload(host=host)
    del body["alerts"][0]["fingerprint"]
    state = tmp_path / "state"
    sent = []
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 1
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 0


def test_delivery_failure_remains_retryable_and_partial_success_dedupes(tmp_path):
    body = payload()
    body["alerts"] += payload(fingerprint="paired")["alerts"]
    body["alerts"] += payload(host="mapa.pogoleiria.pt", fingerprint="second")["alerts"]
    state = tmp_path / "state"
    sent = []

    def send(embed):
        if sent:
            raise OSError("unavailable")
        sent.append(embed)

    with pytest.raises(OSError):
        relay.process_alerts(body, send=send, state_path=state)
    assert len(json.loads(state.read_text())) == 2
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 1
    assert len(sent) == 2


def test_failed_first_send_does_not_record_delivery(tmp_path):
    state = tmp_path / "state"

    def fail(_body):
        raise OSError("unavailable")

    with pytest.raises(OSError):
        relay.process_alerts(payload(), send=fail, state_path=state)
    assert not state.exists()


def test_clips_discord_embed(tmp_path):
    body = payload(host="sub.pogoleiria.pt")
    body["alerts"][0]["annotations"] = {
        "summary": "x" * 10000,
        "description": "y" * 10000,
    }
    sent = []
    relay.process_alerts(body, send=sent.append, state_path=tmp_path / "state")
    assert len(sent[0]["title"]) <= 256
    assert len(sent[0]["description"]) <= 4096
    assert "Serviço" in sent[0]["description"]


def test_prunes_expired_entries_and_skips_historical_recovery(tmp_path):
    state = tmp_path / "state"
    relay.process_alerts(payload(), send=lambda _: None, state_path=state, now=0)
    assert (
        relay.process_alerts(
            payload("resolved"),
            send=lambda _: pytest.fail("historical"),
            state_path=state,
            now=relay.RETENTION + 1,
        )
        == 0
    )
    assert json.loads(state.read_text()) == {}


def test_caps_retained_state(tmp_path, monkeypatch):
    monkeypatch.setattr(relay, "MAX_STATE", 2)
    state = tmp_path / "state"
    for i in range(3):
        relay.process_alerts(
            payload(fingerprint=str(i)), send=lambda _: None, state_path=state, now=i
        )
    assert len(json.loads(state.read_text())) == 2


def test_discord_rest_is_private_and_disables_mentions(monkeypatch):
    monkeypatch.setattr(
        relay.dm_owner,
        "read_env",
        lambda _: {"DISCORD_API_KEY": "secret", "MY_ID": "owner"},
    )
    calls = []

    def post(path, token, body):
        calls.append((path, token, body))
        return {"id": "dm-channel"}

    monkeypatch.setattr(relay.dm_owner, "post", post)
    relay.send_discord({"description": "@everyone"})
    assert calls[0][2] == {"recipient_id": "owner"}
    assert calls[1][0] == "/channels/dm-channel/messages"
    assert calls[1][2]["allowed_mentions"] == {"parse": []}


@pytest.fixture
def webhook(tmp_path):
    sent = []
    server = HTTPServer(
        ("127.0.0.1", 0),
        relay.make_handler(
            "test-secret", "127.0.0.1", send=sent.append, state_path=tmp_path / "state"
        ),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, sent
    server.shutdown()
    thread.join()
    server.server_close()


def request(server, body=None, token="test-secret", path="/alerts", headers=None):
    connection = HTTPConnection(*server.server_address, timeout=3)
    raw = json.dumps(body or payload()).encode()
    request_headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    connection.request("POST", path, body=raw, headers=request_headers)
    response = connection.getresponse()
    status = response.status
    response.read()
    connection.close()
    return status


def test_webhook_auth_size_routing_and_duplicates(webhook):
    server, sent = webhook
    assert request(server, token="wrong") == 401
    assert request(server, path="/bad") == 404
    assert request(server, headers={"Content-Length": str(relay.MAX_BODY + 1)}) == 413
    assert request(server, payload(host="home.test")) == 400
    assert request(server) == 200
    assert request(server) == 200
    assert len(sent) == 1


def test_webhook_delivery_failure_returns_retryable_status(tmp_path):
    def fail(_):
        raise OSError("failed")

    server = HTTPServer(
        ("127.0.0.1", 0),
        relay.make_handler(
            "test-secret", "127.0.0.1", send=fail, state_path=tmp_path / "state"
        ),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert request(server) == 503
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_webhook_source_ip_allowlist(tmp_path):
    server = HTTPServer(
        ("127.0.0.1", 0),
        relay.make_handler("test-secret", "192.0.2.1", state_path=tmp_path / "state"),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert request(server) == 403
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(alerts=[]),
        lambda b: b["alerts"][0].update(status="unknown"),
        lambda b: b["alerts"][0].update(annotations=[]),
        lambda b: b["alerts"][0].update(fingerprint=3),
    ],
)
def test_invalid_structure_is_rejected(tmp_path, mutate):
    body = copy.deepcopy(payload())
    mutate(body)
    with pytest.raises(ValueError):
        relay.process_alerts(
            body, send=lambda _: pytest.fail("sent"), state_path=tmp_path / "state"
        )


def test_related_vantages_send_once_with_distinct_safe_endpoints(tmp_path):
    body = payload()
    first = body["alerts"][0]
    first["labels"].update(vantage="public", instance="https://pogoleiria.pt/")
    second = copy.deepcopy(first)
    second["fingerprint"] = "origin"
    second["labels"].update(
        vantage="origin",
        instance="http://user:password@192.168.1.253:1080/pokedex/procurar?token=secret#private",
        token="never-show-this",
    )
    body["alerts"] += [second, copy.deepcopy(second)]
    state = tmp_path / "state"
    sent = []
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 1
    assert len(json.loads(state.read_text())) == 2
    text = sent[0]["description"]
    assert "vantage=public" in text and "vantage=origin" in text
    assert "192.168.1.253:1080/pokedex/procurar" in text
    for secret in ("password", "token=secret", "private", "never-show-this"):
        assert secret not in text
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 0
    for alert in body["alerts"]:
        alert["status"] = "resolved"
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 1


def test_failed_group_preserves_all_transitions_for_retry(tmp_path):
    body = payload()
    body["alerts"] += payload(fingerprint="second")["alerts"]
    state = tmp_path / "state"

    def fail(_):
        raise OSError("unavailable")

    with pytest.raises(OSError):
        relay.process_alerts(body, send=fail, state_path=state)
    assert not state.exists()
    sent = []
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 1
    assert len(json.loads(state.read_text())) == 2


def test_historical_recovery_is_excluded_from_delivered_group(tmp_path):
    state = tmp_path / "state"
    relay.process_alerts(payload(), send=lambda _: None, state_path=state)
    body = payload("resolved")
    body["alerts"] += payload("resolved", fingerprint="historical")["alerts"]
    sent = []
    assert relay.process_alerts(body, send=sent.append, state_path=state) == 1
    assert "**Verificações:** 1" in sent[0]["description"]
    assert len(json.loads(state.read_text())) == 1


def test_large_group_fits_one_discord_embed(tmp_path):
    body = {"alerts": []}
    for i in range(100):
        alert = payload(fingerprint=str(i))["alerts"][0]
        alert["labels"].update(vantage="origin", check=str(i) + "x" * 1000)
        alert["annotations"]["description"] = "y" * 1000
        body["alerts"].append(alert)
    sent = []
    assert (
        relay.process_alerts(body, send=sent.append, state_path=tmp_path / "state") == 1
    )
    assert len(sent[0]["description"]) <= 4096
    assert sent[0]["description"].count("• ") == 100


def test_distinct_alert_types_are_delivered_separately(tmp_path):
    body = payload()
    other = payload(fingerprint="second")["alerts"][0]
    other["labels"]["alertname"] = "CertificateExpiring"
    body["alerts"].append(other)
    sent = []
    assert (
        relay.process_alerts(body, send=sent.append, state_path=tmp_path / "state") == 2
    )
