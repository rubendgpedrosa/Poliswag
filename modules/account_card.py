"""View model for the account card (templates/accounts.html).

Everything that needs arithmetic -- bar segment widths, which optional lines
exist, the card's pixel height -- is decided here, because the renderer is
wkhtmltoimage's old WebKit: no CSS grid, no gap, unreliable flex-grow. The
template only places fixed-size boxes.
"""

from modules import scan_areas

# Pixel geometry. The template's CSS uses these same numbers; the tests check
# that a rendered layout and build_card_view's height stay in step.
CARD_WIDTH = 800
STAGE_PAD = 16
INNER_PAD_TOP = 20
INNER_PAD_BOTTOM = 18
CONTENT_WIDTH = 712  # 800 - 2*16 stage padding - 30 left - 26 right padding
HEADER_H = 30
POOL_DIVIDER_H = 27  # 12 margin + 1 line + 14 margin
POOL_TITLE_H = 30
POOL_STATS_H = 50  # 6 + 14 label + 4 + 26 figure-lg line
POOL_STATS_HERO_H = 66  # 14 label + 4 + 48 hero line (scanning READY only)
POOL_BAR_H = 22  # 12 margin + 10 bar
POOL_NOTE_H = 24  # 10 margin + 14 line
POOL_LOW_H = 24  # 10 margin + 14 line, the urgent "running low" note
POOL_UNAVAILABLE_H = 36
FOOTER_H = 61  # 18 margin + 1 line + 16 margin + 26 row

# Rendered at 2x (wkhtmltoimage --zoom) so text stays sharp when Discord
# downscales the image on phones; CSS stays at the token pixel sizes.
RENDER_SCALE = 2

BAR_SEGMENT_GAP = 3
BAR_MIN_SEGMENT = 6

# Card order of the four numbers; css class suffix, label, summary key.
STAT_KINDS = (
    ("in-use", "In use", "in_use"),
    ("ready", "Ready", "ready"),
    ("cooldown", "Cooldown", "cooldown"),
    ("out", "Out", "out"),
)

OUT_LABELS = {
    "banned": "banned",
    "auth_banned": "auth-banned",
    "suspended": "suspended",
    "invalid": "invalid",
    "warned": "warned",
    "disabled": "disabled",
    "provider_disabled": "provider-disabled",
}


def bar_segments(values, width=CONTENT_WIDTH):
    """Pixel widths for the non-zero values, summing exactly to `width`.

    Each non-zero value gets at least BAR_MIN_SEGMENT px so a single banned
    account still shows; the rounding remainder goes to the largest segment.
    Returns [(index, px), ...] for the non-zero values in input order.
    """
    items = [(i, v) for i, v in enumerate(values) if v > 0]
    if not items:
        return []
    usable = width - BAR_SEGMENT_GAP * (len(items) - 1)
    total = sum(v for _, v in items)
    widths = [max(BAR_MIN_SEGMENT, round(usable * v / total)) for _, v in items]
    largest = max(range(len(items)), key=lambda k: items[k][1])
    widths[largest] += usable - sum(widths)
    return [(items[k][0], widths[k]) for k in range(len(items))]


def _device_view(device):
    # Offline is "stopped", not an error: a hollow neutral dot, muted text.
    if device is None:
        return {"state": "off", "text": "Rotom unavailable", "names": ""}
    names = ", ".join(device.get("names") or [])
    if not device.get("connected"):
        return {"state": "off", "text": "Device offline", "names": names}
    workers = device.get("workers") or {}
    if workers.get("total"):
        text = f"{workers.get('in_use', 0)}/{workers['total']} workers"
    else:
        text = "Device online"
    return {"state": "on", "text": text, "names": names}


def _note(accounts):
    parts = []
    if accounts["never_used"]:
        parts.append(f"ready includes {accounts['never_used']} never used")
    if accounts["out_detail"]:
        detail = ", ".join(
            f"{n} {OUT_LABELS.get(k, k)}" for k, n in accounts["out_detail"].items()
        )
        parts.append(f"out: {detail}")
    return " · ".join(parts)


def _low_note(pool):
    """Urgent note when the scanning pool has fewer ready accounts than workers."""
    accounts, device = pool.get("accounts"), pool.get("device")
    if pool["key"] != "scanning" or not accounts or not device:
        return ""
    workers = (device.get("workers") or {}).get("total") or 0
    if device.get("connected") and 0 < accounts["ready"] < workers:
        return f"Running low: {accounts['ready']} ready for {workers} workers"
    return ""


def _pool_view(pool):
    hero = pool["key"] == "scanning"
    view = {
        "title": pool["title"],
        "device": _device_view(pool.get("device")),
        "accounts": None,
        "hero": hero,
    }
    accounts = pool.get("accounts")
    if accounts is None:
        view["height"] = POOL_DIVIDER_H + POOL_TITLE_H + POOL_UNAVAILABLE_H
        return view
    values = [accounts[key] for _, _, key in STAT_KINDS]
    segments = bar_segments(values)
    view["accounts"] = {
        "total": accounts["total"],
        "stats": [
            {"kind": kind, "label": label, "value": accounts[key]}
            for kind, label, key in STAT_KINDS
        ],
        "segments": [
            {"kind": STAT_KINDS[i][0], "width": px, "last": n == len(segments) - 1}
            for n, (i, px) in enumerate(segments)
        ],
        "note": _note(accounts),
        "low": _low_note(pool),
    }
    view["height"] = (
        POOL_DIVIDER_H
        + POOL_TITLE_H
        + (POOL_STATS_HERO_H if hero else POOL_STATS_H)
        + POOL_BAR_H
        + (POOL_LOW_H if view["accounts"]["low"] else 0)
        + (POOL_NOTE_H if view["accounts"]["note"] else 0)
    )
    return view


def _status(pools):
    """(tone, words) for the header's status: success, urgent or destructive.

    Only the scanning pool decides it. The card says it in words next to the
    title -- no coloured strip or edge accent (owner, 2026-11-05).
    """
    scanning = next((p for p in pools if p["key"] == "scanning"), None)
    if scanning is None:
        return "urgent", "Stats unavailable"
    device = scanning.get("device")
    accounts = scanning.get("accounts")
    if device is not None and not device.get("connected"):
        return "destructive", "Scanner offline"
    if accounts is None or device is None:
        return "urgent", "Stats unavailable"
    if accounts["ready"] == 0:
        return "destructive", "No accounts ready"
    workers_total = (device.get("workers") or {}).get("total") or 0
    if accounts["ready"] < workers_total:
        return "urgent", "Running low"
    return "success", "Healthy"


def area_lines(area_performance):
    lines = []
    for row in scan_areas.active_areas():
        stats = (area_performance or {}).get(row["dragonite_area"])
        if not stats or not stats.get("total"):
            continue
        rate = round(stats.get("iv", 0) / stats["total"] * 100)
        lines.append({"name": row["label"], "rate": rate, "spawns": stats["total"]})
    return lines


def build_card_view(pools, area_performance=None):
    """Everything accounts.html needs, including the exact card height."""
    pool_views = [_pool_view(p) for p in pools]
    status_tone, status_label = _status(pools)
    footer = area_lines(area_performance)
    grand_total = sum(
        p["accounts"]["total"] for p in pools if p.get("accounts") is not None
    )
    card_h = (
        INNER_PAD_TOP
        + HEADER_H
        + sum(v["height"] for v in pool_views)
        + (FOOTER_H if footer else 0)
        + INNER_PAD_BOTTOM
    )
    return {
        "pools": pool_views,
        "area_lines": footer,
        "grand_total": grand_total,
        "status": status_tone,
        "status_label": status_label,
        "card_height": card_h,
        "height": card_h + 2 * STAGE_PAD,
        "width": CARD_WIDTH,
        "scale": RENDER_SCALE,
    }
