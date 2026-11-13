"""Post, read and close the nightly update plan in the owner's DM.

Called by Hermes on CT 109 through `docker exec -i poliswag python
/app/scripts/update_plan.py <post|status|finish>` with JSON on stdin:

  post    {"plan_id", "when", "run_date", "items": [{"key","label","from","to"}]}
  status  (no input) prints the plan state
  status-set {"plan_id", "status"}  (running, before the work starts)
  finish  {"plan_id", "results": {"<key>": {"ok","detail"}}}  per item; items
          still without a result keep the plan scheduled for 04:00

The message goes to the owner's private DM only, with mentions disabled;
the buttons are handled by cogs/update_plan.py while the bot runs.
"""

import json
import sys
import urllib.request

sys.path.insert(0, "/app")

from modules import update_plan  # noqa: E402
from modules.config import Config  # noqa: E402

API = "https://discord.com/api/v10"


def api(method, path, body=None):
    request = urllib.request.Request(
        API + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bot {Config.DISCORD_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Poliswag (https://pogoleiria.pt, 1.0)",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def _message_body(state):
    return {
        "embeds": [update_plan.embed(state)],
        "components": update_plan.components(state),
        "allowed_mentions": {"parse": []},
    }


def post(packet):
    state = {
        "plan_id": packet["plan_id"],
        "when": packet.get("when", "04:00"),
        "run_date": packet["run_date"],
        "items": packet["items"],
        "status": "scheduled",
    }
    dm = api("POST", "/users/@me/channels", {"recipient_id": str(Config.MY_ID)})
    if dm.get("type") != 1:
        raise RuntimeError("not a private DM")
    sent = api("POST", f"/channels/{dm['id']}/messages", _message_body(state))
    state.update(channel_id=dm["id"], message_id=sent["id"])
    update_plan.save(state)
    return {"plan_id": state["plan_id"], "message_id": sent["id"]}


def _edit(state):
    if state.get("channel_id") and state.get("message_id"):
        api(
            "PATCH",
            f"/channels/{state['channel_id']}/messages/{state['message_id']}",
            _message_body(state),
        )


def set_status(packet):
    state = update_plan.load()
    if state.get("plan_id") != packet.get("plan_id"):
        raise RuntimeError("plan mismatch")
    state["status"] = packet["status"]
    update_plan.save(state)
    _edit(state)
    return {"status": state["status"]}


def finish(packet):
    state = update_plan.load()
    if state.get("plan_id") != packet.get("plan_id"):
        raise RuntimeError("plan mismatch")
    results = packet.get("results") or {}
    for item in state.get("items", []):
        if item.get("key") in results:
            item["result"] = results[item["key"]]
    state["status"] = "scheduled" if update_plan.pending(state) else "done"
    update_plan.save(state)
    _edit(state)
    return {"status": "done"}


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "status":
        print(json.dumps(update_plan.load(), ensure_ascii=False))
        return 0
    packet = json.loads(sys.stdin.read() or "{}")
    handlers = {"post": post, "status-set": set_status, "finish": finish}
    if command not in handlers:
        print("usage: update_plan.py post|status|status-set|finish", file=sys.stderr)
        return 2
    print(json.dumps(handlers[command](packet), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
