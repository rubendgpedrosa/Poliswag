"""Deterministic shiny report images and the owner's daily Discord delivery."""

import asyncio
from datetime import datetime
from io import BytesIO
from functools import lru_cache
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

from modules.config import Config
from modules.http_client import get_session
from modules import spawn_history

LISBON = ZoneInfo("Europe/Lisbon")
ASSETS = Path(__file__).resolve().parent.parent / "templates/shiny-leaderboard"
# sRGB equivalents of packages/ui/src/tokens.css (PoGoLeiria platform).
BG = "#01040a"
SURFACE = "#0c131d"
TEXT = "#f1f5fc"
MUTED = "#a7b0be"
LINE = "#262e3b"
GOLD = "#f4cc64"
ELEVATED = "#161e2a"
MAX_DISPLAYED_TRAINERS = 15


def number(value, places=0):
    return f"{value:,.{places}f}".replace(",", " ").replace(".", ",")


def render_report(data, avatars=None):
    """Render the platform's podium layout from the unchanged report aggregate."""
    avatars = avatars or {}
    all_rows = data["trainers"]
    rows = all_rows[:MAX_DISPLAYED_TRAINERS]
    meta = data["metadata"]
    partial = not meta["complete_window"]
    try:
        hours = float(meta.get("hours", 24))
    except (TypeError, ValueError, OverflowError):
        hours = None
    if hours is not None and (not isfinite(hours) or hours <= 0):
        hours = None
    hours_label = number(hours) if hours is not None else "—"
    shift = 72 if partial else 0
    width, margin, row_height = 1080, 64, 79
    podium_top, podium_bottom = 355 + shift, 672 + shift
    table_y, rows_y = 707 + shift, 747 + shift
    remaining = rows[3:]
    if remaining:
        footer_y = rows_y + len(remaining) * row_height + 38
    elif rows:
        footer_y = podium_bottom + 48
    else:
        footer_y = 535 + shift
    image = Image.new("RGB", (width, footer_y + 250), BG)
    draw = ImageDraw.Draw(image)

    @lru_cache(maxsize=96)
    def font(size, display=False, weight=500):
        face = "bricolage.ttf" if display else "figtree.ttf"
        result = ImageFont.truetype(str(ASSETS / face), size)
        result.set_variation_by_axes([96, weight, 100] if display else [weight])
        return result

    def text(
        x, y, content, size=24, color=TEXT, display=False, weight=500, anchor="lt"
    ):
        draw.text(
            (x, y),
            str(content),
            font=font(size, display, weight),
            fill=color,
            anchor=anchor,
        )

    def fitted(x, y, content, max_width, size, min_size=21, **kwargs):
        content = str(content)
        display, weight = kwargs.get("display", False), kwargs.get("weight", 500)
        while (
            size > min_size
            and draw.textlength(content, font=font(size, display, weight)) > max_width
        ):
            size -= 1
        if draw.textlength(content, font=font(size, display, weight)) > max_width:
            while (
                content
                and draw.textlength(content + "…", font=font(size, display, weight))
                > max_width
            ):
                content = content[:-1]
            content += "…"
        text(x, y, content, size, **kwargs)

    def line(y):
        draw.line((margin, y, width - margin, y), fill=LINE, width=1)

    ranks, previous, rank = [], None, 0
    for index, row in enumerate(rows):
        if row["predicted_shinies"] != previous:
            rank = index + 1
        previous = row["predicted_shinies"]
        ranks.append(rank)

    def avatar(row, x, y, size, leader=False):
        x, y = round(x), round(y)
        draw.ellipse(
            (x, y, x + size, y + size),
            fill=ELEVATED,
            outline=GOLD if leader else None,
            width=1,
        )
        content = avatars.get(row["trainer"])
        if content:
            try:
                with Image.open(BytesIO(content)) as source:
                    pic = ImageOps.fit(source.convert("RGB"), (size - 4, size - 4))
                mask = Image.new("L", pic.size)
                ImageDraw.Draw(mask).ellipse((0, 0, size - 5, size - 5), fill=255)
                image.paste(pic, (x + 2, y + 2), mask)
                return
            except (OSError, ValueError):
                pass
        text(
            x + size / 2,
            y + size / 2,
            row["trainer"][:1].upper(),
            round(size * 0.38),
            GOLD if leader else MUTED,
            weight=700,
            anchor="mm",
        )

    def rate(row):
        if hours is None:
            return "—"
        value = number(row["predicted_shinies"] / hours, 2)
        return value + ("*" if partial else "")

    with Image.open(ASSETS / "brand-header-400.webp") as source:
        logo = source.convert("RGBA").resize((450, 450), Image.Resampling.LANCZOS)
    logo = logo.rotate(-8, resample=Image.Resampling.BICUBIC, expand=True)
    logo.putalpha(logo.getchannel("A").point(lambda p: round(p * 0.12)))
    header = Image.new("RGBA", (width, 267))
    header.alpha_composite(logo, (730, -125))
    image.paste(header, (0, 0), header)
    text(margin, 45, "POGOLEIRIA", 30, weight=900)
    text(margin, 110, "Ranking Shiny", 62, display=True, weight=800)
    text(margin, 190, "Leiria · Marinha Grande", 30, MUTED)
    text(width - margin, 204, "DIÁRIO · 09:00", 22, MUTED, weight=600, anchor="rt")
    line(265)
    start = datetime.fromisoformat(meta["window_start_utc"]).astimezone(LISBON)
    end = datetime.fromisoformat(meta["window_end_utc"]).astimezone(LISBON)
    text(margin, 296, f"Últimas {hours_label} h", 26, weight=650)
    text(
        width - margin,
        296,
        f"{start:%d/%m %H:%M} → {end:%d/%m %H:%M}",
        25,
        MUTED,
        anchor="rt",
    )
    if partial:
        draw.rounded_rectangle(
            (margin, 348, width - margin, 398), radius=12, fill=SURFACE
        )
        coverage = "cobertura incompleta"
        text(margin + 18, 362, f"HISTÓRICO PARCIAL · {coverage}", 23, GOLD, weight=700)

    # Keep the winner in the middle; one or two trainers need no empty cards.
    for index, x in ((1, margin), (0, 390), (2, 716)):
        if index >= len(rows):
            continue
        row = rows[index]
        top = podium_top if index == 0 else podium_top + 65
        leader = ranks[index] == 1
        draw.rounded_rectangle(
            (x, top, x + 300, podium_bottom), radius=18, fill=SURFACE
        )
        text(
            x + 24,
            top + 23,
            f"{ranks[index]}.º",
            23,
            GOLD if leader else MUTED,
            weight=650,
        )
        avatar(row, x + 124, top + 23, 52, leader)
        fitted(x + 150, top + 88, row["trainer"], 260, 26, weight=650, anchor="mt")
        fitted(
            x + 150,
            top + 128,
            number(row["predicted_shinies"]),
            260,
            60 if index == 0 else 54,
            min_size=30,
            display=True,
            weight=800,
            anchor="mt",
        )
        text(
            x + 150,
            top + (210 if index == 0 else 190),
            "SHINIES",
            17,
            GOLD if leader else MUTED,
            weight=650,
            anchor="mt",
        )
        text(x + 150, podium_bottom - 25, f"{rate(row)} / hora", 23, MUTED, anchor="mm")

    if remaining:
        text(margin, table_y, "#", 19, MUTED, weight=650)
        text(margin + 126, table_y, "TREINADOR", 19, MUTED, weight=650)
        text(823, table_y, "SHINIES", 19, MUTED, weight=650, anchor="rt")
        text(
            width - margin,
            table_y,
            "/ HORA" + ("*" if partial else ""),
            19,
            MUTED,
            weight=650,
            anchor="rt",
        )
        line(table_y + 34)
        for index, row in enumerate(remaining, 3):
            y = rows_y + (index - 3) * row_height
            middle = y + row_height / 2
            text(
                margin + 13,
                middle,
                ranks[index],
                30,
                GOLD if ranks[index] == 1 else MUTED,
                display=True,
                weight=600,
                anchor="mm",
            )
            avatar(row, margin + 55, middle - 25, 50, ranks[index] == 1)
            fitted(
                margin + 126, middle, row["trainer"], 400, 31, weight=650, anchor="lm"
            )
            fitted(
                823,
                middle,
                number(row["predicted_shinies"]),
                180,
                40,
                min_size=25,
                display=True,
                weight=750,
                anchor="rm",
            )
            fitted(
                width - margin,
                middle,
                rate(row),
                170,
                28,
                min_size=21,
                color=MUTED,
                anchor="rm",
            )
            line(y + row_height)
    elif not rows:
        text(
            width / 2,
            418 + shift,
            "Sem treinadores neste período.",
            32,
            MUTED,
            display=True,
            weight=650,
            anchor="mt",
        )

    total = data["total_predicted_shinies"]
    if hours is None:
        mean = "Média geral: indisponível · duração do período inválida."
    elif not rows:
        mean = "Média geral: indisponível · sem treinadores."
    else:
        average = total / len(all_rows) / hours
        marker = "*" if partial else ""
        mean = (
            f"Média geral: {number(average, 2)}{marker} shinies/conta/h · "
            f"Total ÷ ({len(all_rows)} "
            f"{'treinador' if len(all_rows) == 1 else 'treinadores'} × {hours_label} h)."
        )
    paragraphs = [(mean, 21)]
    if partial:
        paragraphs.append(
            (
                "* Contagens registadas ÷ horas do período pedido; "
                "as médias podem estar subestimadas por falta de histórico.",
                21,
            )
        )
    paragraphs.extend(
        [
            ("Previsões, não capturas confirmadas.", 23),
            ("Mesmos spawns para todos · Sem filtros pessoais", 22),
        ]
    )
    footer_lines = []
    cursor = 103
    for paragraph, size in paragraphs:
        words, current = paragraph.split(), ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if (
                current
                and draw.textlength(candidate, font=font(size)) > width - 2 * margin
            ):
                footer_lines.append((cursor, current, size))
                cursor += 29
                current = word
            else:
                current = candidate
        if current:
            footer_lines.append((cursor, current, size))
            cursor += 29
        cursor += 6 if size == 23 else 11
    footer_height = max(250, cursor + 22)
    if image.height < footer_y + footer_height:
        expanded = Image.new("RGB", (width, footer_y + footer_height), BG)
        expanded.paste(image, (0, 0))
        image = expanded
        draw = ImageDraw.Draw(image)

    line(footer_y)
    text(margin, footer_y + 22, "TOTAL GERAL", 19, MUTED, weight=650)
    fitted(
        width - margin,
        footer_y + 18,
        number(total),
        320,
        36,
        display=True,
        weight=750,
        anchor="rt",
    )
    text(
        margin,
        footer_y + 65,
        (
            f"Top {MAX_DISPLAYED_TRAINERS} · {len(all_rows)} treinadores no total"
            if len(all_rows) > MAX_DISPLAYED_TRAINERS
            else f"{len(all_rows)} "
            + ("treinador" if len(all_rows) == 1 else "treinadores")
        ),
        22,
        MUTED,
    )
    for offset, content, size in footer_lines:
        text(margin, footer_y + offset, content, size, MUTED)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


class ShinyLeaderboard:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.last_date = None
        self.lock = asyncio.Lock()

    async def avatars(self):
        rows = await self.poliswag.db.get_data_from_database(
            f"SELECT l.trainer_name, p.avatar_url FROM shiny_link l JOIN {Config.DB_POGOLEIRIA}.trade_player p "
            "ON p.discord_id=l.discord_id WHERE l.status='linked' AND p.shiny_access=1 "
            "AND p.shiny_map=1 AND p.left_at IS NULL"
        )
        semaphore = asyncio.Semaphore(4)

        async def fetch(row):
            url = row.get("avatar_url") or ""
            # Community profiles store Discord CDN avatars. Never fetch arbitrary URLs.
            if not url.startswith(
                ("https://cdn.discordapp.com/", "https://media.discordapp.net/")
            ):
                return row["trainer_name"], None
            try:
                async with semaphore, get_session().get(
                    url.replace("size=1024", "size=128"),
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={"User-Agent": "DiscordBot (https://pogoleiria.pt, 1.0)"},
                ) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 512 * 1024:
                            return row["trainer_name"], None
                        chunks.append(chunk)
                    return row["trainer_name"], b"".join(chunks)
            except (aiohttp.ClientError, TimeoutError):
                return row["trainer_name"], None

        return dict(await asyncio.gather(*(fetch(row) for row in rows)))

    async def send(self, data=None):
        if data is None:
            data = await asyncio.to_thread(spawn_history.leaderboard, 24)
        if not data["metadata"].get("available"):
            raise ValueError("A recolha ainda não começou.")
        png = await asyncio.to_thread(render_report, data, await self.avatars())
        owner = self.poliswag.get_user(Config.MY_ID) or await self.poliswag.fetch_user(
            Config.MY_ID
        )
        partial = not data["metadata"]["complete_window"]
        content = "Leaderboard shiny sem filtros pessoais. " + (
            "Histórico parcial; a imagem indica a cobertura e a base da média."
            if partial
            else "Últimas 24 horas."
        )
        if data["metadata"].get("collector_stale"):
            content += " A recolha está atrasada; o horário de fim está na imagem."
        attachment = discord.File(
            BytesIO(png), filename="pogoleiria-shiny-leaderboard.png"
        )
        try:
            return await owner.send(
                content,
                file=attachment,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            attachment.close()

    async def tick(self, now=None):
        if not Config.SHINY_LEADERBOARD_ENABLED:
            return
        local = (now or datetime.now(LISBON)).astimezone(LISBON)
        if local.hour < 9:
            return
        async with self.lock:
            today = local.date()
            if self.last_date == today:
                return
            rows = await self.poliswag.db.get_data_from_database(
                "SELECT last_shiny_leaderboard_date FROM poliswag"
            )
            saved = rows[0].get("last_shiny_leaderboard_date") if rows else None
            if saved and str(saved) >= today.isoformat():
                self.last_date = today
                return
            await self.send()
            self.last_date = today
            await self.poliswag.db.execute_query_to_database(
                "UPDATE poliswag SET last_shiny_leaderboard_date=%s",
                params=(today.isoformat(),),
            )
