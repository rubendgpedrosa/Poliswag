"""The nightly update plan the owner can accept or cancel from his DM.

Hermes (CT 109) checks the homelab services in the morning and, when some
can be updated safely, posts one plan through scripts/update_plan.py: an
embed listing them, "tonight at 04:00", with Aceitar / Cancelar buttons.
The plan runs unless it is cancelled; at 04:00 Hermes reads the state and
reports the outcome on the same message. "Atualizar já" sets it to run now:
Hermes checks for that every minute. The state is one JSON file,
UPDATE_PLAN_FILE, shared by the script and the bot's button handler.
"""

import json
from pathlib import Path

from modules.config import Config

PREFIX = "updplan:"
COLOUR = 0xF0A030
DONE_COLOUR = 0x2ECC71
FAILED_COLOUR = 0xE74C3C


def load():
    try:
        data = json.loads(Path(Config.UPDATE_PLAN_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state):
    path = Path(Config.UPDATE_PLAN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(path)


_STATUS = {
    "scheduled": "Corre esta noite às {when}, a não ser que canceles.",
    "accepted": "✅ Aceite. Corre esta noite às {when}.",
    "cancelled": "🚫 Cancelada. Nada vai ser atualizado.",
    "now": "⚡ Vai atualizar já: começa dentro de um minuto.",
    "running": "⏳ A atualizar…",
    "done": "Terminado.",
}


def embed(state):
    """The plan as a Discord embed dict (REST and discord.Embed.from_dict)."""
    lines = [
        f"• **{item['label']}** — {item.get('from', '?')} → {item.get('to', '?')}"
        for item in state.get("items", [])
    ]
    results = state.get("results") or []
    colour = COLOUR
    if results:
        lines = [
            f"{'✅' if r.get('ok') else '❌'} **{r.get('label')}** — {r.get('detail', '')}"
            for r in results
        ]
        colour = DONE_COLOUR if all(r.get("ok") for r in results) else FAILED_COLOUR
    status = state.get("status", "scheduled")
    footer = _STATUS.get(status, status).format(when=state.get("when", "04:00"))
    return {
        "title": f"🛠️ Atualização agendada ({len(state.get('items', []))})",
        "description": "\n".join(lines)[:3900],
        "color": colour,
        "footer": {"text": footer},
    }


def components(state):
    """Buttons while the plan can still change: Atualizar já, Aceitar (until
    accepted) and Cancelar."""
    status = state.get("status")
    if status not in ("scheduled", "accepted"):
        return []
    plan = state.get("plan_id", "")

    def button(style, label, action):
        return {
            "type": 2,
            "style": style,
            "label": label,
            "custom_id": f"{PREFIX}{action}:{plan}",
        }

    buttons = [button(1, "Atualizar já", "now")]
    if status == "scheduled":
        buttons.append(button(3, "Aceitar", "accept"))
    buttons.append(button(4, "Cancelar", "cancel"))
    return [{"type": 1, "components": buttons}]


def decide(state, action, plan_id):
    """Apply a button press; returns the new state or None when it no longer
    applies (another plan, or it already ran or was cancelled)."""
    if state.get("plan_id") != plan_id or state.get("status") not in (
        "scheduled",
        "accepted",
    ):
        return None
    if action == "accept":
        state["status"] = "accepted"
    elif action == "cancel":
        state["status"] = "cancelled"
    elif action == "now":
        state["status"] = "now"
    else:
        return None
    return state
