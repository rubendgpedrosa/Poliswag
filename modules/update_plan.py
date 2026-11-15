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


# No emoji (owner, 2026-11-15): plain words for when and how each item goes.
_STATUS = {
    "scheduled": 'Os marcados "já" podem ir agora; o resto corre esta noite às {when}, a não ser que canceles.',
    "accepted": "Aceite. Corre esta noite às {when}.",
    "cancelled": "Cancelada. Nada vai ser atualizado.",
    "now": "Vai atualizar já: começa dentro de um minuto.",
    "running": "A atualizar…",
    "done": "Terminado.",
}
WHEN_LABEL = {"now": "já", "night": "04:00", "manual": "à mão"}


def pending(state, when=None):
    """Items without a result yet (optionally only those marked `when`).
    Items the owner left out of the plan are not pending."""
    return [
        item
        for item in state.get("items", [])
        if "result" not in item
        and not item.get("excluded")
        and (when is None or item.get("when", "night") == when)
    ]


def embed(state):
    """The plan as a Discord embed dict (REST and discord.Embed.from_dict):
    each item with when it goes (já, 04:00, à mão, fora), or its outcome."""
    lines, results = [], []
    for item in state.get("items", []):
        result = item.get("result")
        if result is not None:
            results.append(result)
            mark = (
                "à mão"
                if result.get("skipped")
                else "feito" if result.get("ok") else "FALHOU"
            )
            lines.append(f"**{item['label']}** — {result.get('detail', '')} ({mark})")
        elif item.get("excluded"):
            lines.append(
                f"~~{item['label']}~~ — {item.get('from', '?')} → {item.get('to', '?')} (fora)"
            )
        else:
            when = WHEN_LABEL.get(item.get("when", "night"), "04:00")
            lines.append(
                f"**{item['label']}** — {item.get('from', '?')} → {item.get('to', '?')} ({when})"
            )
    colour = COLOUR
    if results and not pending(state):
        colour = DONE_COLOUR if all(r.get("ok") for r in results) else FAILED_COLOUR
    status = state.get("status", "scheduled")
    if status == "scheduled" and not pending(state, "now"):
        footer = (
            f"Corre esta noite às {state.get('when', '04:00')}, a não ser que canceles."
        )
    else:
        footer = _STATUS.get(status, status).format(when=state.get("when", "04:00"))
    return {
        "title": f"Atualização agendada ({len(state.get('items', []))})",
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
    rows = [{"type": 1, "components": buttons}]
    # Owner, 2026-11-15: one message for every update, and he picks which go.
    choices = [
        item
        for item in state.get("items", [])
        if "result" not in item and item.get("when") != "manual"
    ][:25]
    if choices:
        rows.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": f"{PREFIX}pick:{plan}",
                        "placeholder": "O que entra nesta atualização",
                        "min_values": 0,
                        "max_values": len(choices),
                        "options": [
                            {
                                "label": item["label"][:100],
                                "value": item["key"][:100],
                                "description": (
                                    f"{item.get('from', '?')} → {item.get('to', '?')}"
                                )[:100],
                                "default": not item.get("excluded"),
                            }
                            for item in choices
                        ],
                    }
                ],
            },
        )
    return rows


def decide(state, action, plan_id, values=None):
    """Apply a button press (or the pick list, with its chosen keys in
    `values`); returns the new state or None when it no longer applies
    (another plan, or it already ran or was cancelled)."""
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
    elif action == "pick":
        chosen = set(values or ())
        for item in state.get("items", []):
            if "result" in item or item.get("when") == "manual":
                continue
            item["excluded"] = item.get("key") not in chosen
    else:
        return None
    return state
