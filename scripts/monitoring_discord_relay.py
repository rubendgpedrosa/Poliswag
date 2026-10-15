"""Authenticated Grafana alerts to private owner Discord DMs (stdlib only)."""

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from scripts import dm_owner

MAX_BODY = 256 * 1024
MAX_STATE = 10000
RETENTION = 30 * 86400
STATE_PATH = (
    Path(__file__).resolve().parent.parent / "logs/monitoring-discord-relay.json"
)


def _host(labels):
    value = labels.get("host", labels.get("instance", ""))
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("missing host")
    try:
        parsed = urlsplit(value if "://" in value else "//" + value)
        host = (parsed.hostname or "").lower().rstrip(".")
        parsed.port  # Reject malformed port specifications.
    except ValueError:
        raise ValueError("invalid host") from None
    if (
        parsed.username
        or parsed.password
        or not (host == "pogoleiria.pt" or host.endswith(".pogoleiria.pt"))
    ):
        raise ValueError("host outside Pokemon GO route")
    return host


def _alerts(payload):
    if not isinstance(payload, dict):
        raise ValueError("expected object")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not 0 < len(alerts) <= 100:
        raise ValueError("expected 1 to 100 alerts")
    result = []
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get("status") not in (
            "firing",
            "resolved",
        ):
            raise ValueError("invalid alert status")
        labels, annotations = alert.get("labels"), alert.get("annotations", {})
        if not isinstance(labels, dict) or not isinstance(annotations, dict):
            raise ValueError("invalid labels or annotations")
        if not all(
            isinstance(k, str) and isinstance(v, str) for k, v in labels.items()
        ):
            raise ValueError("invalid labels")
        if not all(
            isinstance(k, str) and isinstance(v, str) for k, v in annotations.items()
        ):
            raise ValueError("invalid annotations")
        host = _host(labels)
        fingerprint = alert.get("fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or not fingerprint
            or len(fingerprint) > 1024
        ):
            raise ValueError("invalid fingerprint")
        identity = fingerprint or json.dumps(labels, sort_keys=True)
        key = hashlib.sha256((host + "|" + identity).encode()).hexdigest()
        summary = (
            annotations.get("summary")
            or labels.get("alertname")
            or "Alerta operacional"
        )
        description = annotations.get("description", "")
        status = alert["status"]
        message = (
            f"**Estado:** {'Problema detetado' if status == 'firing' else 'Recuperado'}\n"
            f"**Serviço:** {host[:253]}\n**Resumo:** {summary[:1024]}"
        )
        if description:
            message += f"\n\n{description[:2500]}"
        body = dm_owner.embed(message, "PoGoLeiria — Monitorização")
        result.append((key, status, body))
    return result


def _save(state_path, state):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".monitoring-relay-", dir=state_path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, state_path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def process_alerts(payload, *, send, state_path=STATE_PATH, now=None):
    """Validate the full batch, deliver transitions, then atomically record each success.

    ValueError means a rejected request; other exceptions mean retryable failure.
    The HTTP server serializes requests to avoid concurrent state updates.
    """
    alerts = _alerts(payload)
    now = time.time() if now is None else now
    state_path = Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not isinstance(state, dict) or any(
        not isinstance(v, dict)
        or v.get("status") not in ("firing", "resolved")
        or not isinstance(v.get("updated"), (int, float))
        for v in state.values()
    ):
        raise RuntimeError("invalid relay state")
    state = {k: v for k, v in state.items() if v["updated"] >= now - RETENTION}
    sent = 0
    for key, status, body in alerts:
        previous = state.get(key)
        if previous and previous["status"] == status:
            # Retain active alerts across long incidents without sending reminders.
            state[key]["updated"] = now
            continue
        if status == "resolved" and previous is None:
            continue
        send(body)
        state[key] = {"status": status, "updated": now}
        if len(state) > MAX_STATE:
            state = dict(
                sorted(state.items(), key=lambda item: item[1]["updated"])[-MAX_STATE:]
            )
        _save(state_path, state)
        sent += 1
    _save(state_path, state)
    return sent


def send_discord(body):
    env = dm_owner.read_env(dm_owner.ENV_FILE)
    token, owner = env["DISCORD_API_KEY"], env["MY_ID"]
    channel = dm_owner.post("/users/@me/channels", token, {"recipient_id": owner})
    dm_owner.post(
        f"/channels/{channel['id']}/messages",
        token,
        {"embeds": [body], "allowed_mentions": {"parse": []}},
    )


def make_handler(token, allowed_ip, *, send=send_discord, state_path=STATE_PATH):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log attacker-controlled paths, payloads, or credentials.

        def reply(self, code):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            self.connection.settimeout(15)
            if self.path != "/alerts":
                return self.reply(404)
            if self.client_address[0] != allowed_ip:
                return self.reply(403)
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth.encode(), ("Bearer " + token).encode()):
                return self.reply(401)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                return self.reply(400)
            try:
                length = int(lengths[0])
            except ValueError:
                return self.reply(400)
            if not 0 < length <= MAX_BODY:
                return self.reply(413)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    return self.reply(400)
                payload = json.loads(raw)
                _alerts(payload)
            except (ValueError, UnicodeError):
                return self.reply(400)
            except OSError:
                return self.reply(408)
            try:
                process_alerts(payload, send=send, state_path=state_path)
            except Exception:
                logging.error("Monitoring relay delivery/state failure; retry required")
                return self.reply(503)
            return self.reply(200)

    return Handler


def main():
    token = os.environ.get("MONITORING_RELAY_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("MONITORING_RELAY_TOKEN must contain at least 32 characters")
    server = HTTPServer(
        (
            os.environ.get("MONITORING_RELAY_BIND", "192.168.1.253"),
            int(os.environ.get("MONITORING_RELAY_PORT", "8990")),
        ),
        make_handler(
            token, os.environ.get("MONITORING_RELAY_ALLOWED_IP", "192.168.1.248")
        ),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
