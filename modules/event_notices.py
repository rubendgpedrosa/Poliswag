"""The event posts in CONVIVIO: "Novos eventos", "Eventos que terminaram" and
Monday's "Eventos da semana" calendar.

This module builds and sends them (one image per event, or the text cards when
an image doesn't render) and acknowledges delivered events. The scheduler
(cogs/scheduled.py) decides when: it asks EventManager what changed each tick
and keeps the weekly calendar's Monday marker.
"""

import dataclasses
import datetime
import io
import json

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.event_images import usable_image
from modules.locale_pt import PT_DAYS_SHORT
from modules.stat_card import StatCard
from modules.week_board import WeekEvent


def _event_time(event, key) -> datetime.datetime:
    """Event rows hold Lisbon wall time as `%Y-%m-%d %H:%M:%S`."""
    return datetime.datetime.strptime(str(event[key]), "%Y-%m-%d %H:%M:%S")


def _extra(event):
    try:
        data = json.loads(event.get("extra_data") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _event_extras(event):
    """(Pokémon, bonuses) for a new event's panel, from ScrapedDuck's
    extraData: raid bosses, Community Day spawns and bonuses, the Spotlight
    Hour Pokémon and its bonus. Pokémon are (name, icon URL, can be shiny)."""
    extra = _extra(event).get("extraData") or {}
    pokemon, bonuses = [], []
    raids = extra.get("raidbattles") or {}
    for boss in raids.get("bosses") or []:
        pokemon.append(
            (boss.get("name"), boss.get("image"), bool(boss.get("canBeShiny")))
        )
    cd = extra.get("communityday") or {}
    shinies = {mon.get("name") for mon in cd.get("shinies") or []}
    for mon in cd.get("spawns") or []:
        pokemon.append((mon.get("name"), mon.get("image"), mon.get("name") in shinies))
    bonuses += [bonus.get("text") for bonus in cd.get("bonuses") or []]
    spotlight = extra.get("spotlight") or {}
    for mon in spotlight.get("list") or []:
        pokemon.append((mon.get("name"), mon.get("image"), bool(mon.get("canBeShiny"))))
    if spotlight.get("bonus"):
        bonuses.append(spotlight["bonus"])
    return (
        tuple((name, image or "", shiny) for name, image, shiny in pokemon if name),
        tuple(bonus for bonus in bonuses if bonus),
    )


def _duration(span):
    """'1 h', '3 h 30 min', '5 dias'."""
    minutes = round(span.total_seconds() / 60)
    if minutes >= 24 * 60:
        days = round(minutes / (24 * 60))
        return f"{days} dia" if days == 1 else f"{days} dias"
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


# The weekly query has no ScrapedDuck heading; these read like one.
_TYPE_LABELS = {
    "pokemon-spotlight-hour": "Spotlight Hour",
    "pokemon-go-tour": "Pokémon GO Tour",
    "go-pass": "GO Pass",
    "event": "Evento",
    "season": "Temporada",
    "research": "Pesquisa",
}


def _type_label(event_type):
    """'raid-hour' -> 'Raid Hour'; a few read better spelled out."""
    key = (event_type or "").lower()
    if key in _TYPE_LABELS:
        return _TYPE_LABELS[key]
    return key.replace("-", " ").title().replace("Go ", "GO ") or "Evento"


def _event_heading(event):
    """ScrapedDuck's own label for the event ("Raid Battles", "Wild Area"),
    for the kicker of a panel without stats; "Evento" without one."""
    return _extra(event).get("heading") or "Evento"


# Discord caps an embed description at 4096 chars and a message's embeds at
# 6000 chars total. The weekly calendar is chunked at line boundaries so no
# entry is dropped or truncated.
_WEEKLY_DESCRIPTION_LIMIT = 4096


def _split_weekly_description(text, limit=_WEEKLY_DESCRIPTION_LIMIT):
    """Split a long digest at line boundaries, preserving every entry."""
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


class EventNotices:
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def send_change_notifications(self, channel, changed, acknowledge=False):
        """Acknowledge each successful message, leaving failed batches retryable.

        Manual previews use acknowledge=False. Discord acceptance and the DB
        update cannot be atomic: a crash between them may still cause a repeat.
        """
        if changed["ended"]:
            panels, summaries, groups = [], [], []
            for event in changed["ended"]:
                summary = await self.poliswag.event_stats.get_summary(event)
                if summary and summary in summaries:
                    # "Super Mega Raid Day" and "Staraptor Super Mega Raid
                    # Day" are one event under two names: one panel.
                    groups[summaries.index(summary)].append(event)
                    continue
                summaries.append(summary)
                groups.append([event])
                panels.append(await self._ended_panel(event, summary))

            async def ended_cards():
                return [
                    await self.build_event_embed(
                        group[0], is_ended=True, summary=summary
                    )
                    for group, summary in zip(groups, summaries)
                ]

            await self._send_boards(
                channel,
                "Eventos que terminaram",
                panels,
                groups,
                ended_cards,
                is_end=True,
                acknowledge=acknowledge,
            )
        if changed["started"]:
            started = changed["started"]

            async def started_cards():
                return [await self.build_event_embed(event) for event in started]

            await self._send_boards(
                channel,
                "Novos eventos",
                [await self._started_panel(event) for event in started],
                [[event] for event in started],
                started_cards,
                is_end=False,
                acknowledge=acknowledge,
            )

    async def _ack_events(self, events, is_end):
        for event in events:
            date = datetime.datetime.fromisoformat(
                str(event["end" if is_end else "start"])
            )
            await self.poliswag.event_manager.mark_event_notified(
                event, date, is_end=is_end
            )

    async def _send_event_batches(
        self, channel, header, embeds, groups, plain, *, is_end, acknowledge
    ):
        offset = 0
        first = True
        while first or offset < len(embeds):
            end, size = offset, 0
            while end < len(embeds) and end - offset < 10:
                length = len(embeds[end])
                if length > 6000:
                    raise ValueError("Event embed exceeds Discord's message limit")
                if size + length > 6000:
                    break
                size += length
                end += 1
            batch = embeds[offset:end]
            if first:
                await channel.send(
                    header,
                    embeds=batch,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await channel.send(
                    embeds=batch, allowed_mentions=discord.AllowedMentions.none()
                )
            if acknowledge:
                events = [event for group in groups[offset:end] for event in group]
                await self._ack_events((plain if first else []) + events, is_end=is_end)
            first = False
            offset = end

    async def _ended_panel(self, event, summary):
        """The event's panel on the "Eventos que terminaram" image: its game
        image and name, then its stats, or when it ended if it has none."""
        title = event["name"]
        image = await usable_image(event.get("image")) or ""
        if summary and summary.card:
            return dataclasses.replace(summary.card, title=title, image=image)
        event_end = _event_time(event, "end")
        return StatCard(
            kicker=_event_heading(event),
            hero=None,
            title=title,
            image=image,
            ended=self.poliswag.event_manager.format_end_time(
                event_end, verb="Terminou"
            ),
        )

    async def _started_panel(self, event):
        """A new event's panel: its game image and name, until when it runs
        and for how long, then its Pokémon and bonuses where ScrapedDuck
        lists them."""
        end = _event_time(event, "end")
        start = None
        if event.get("start"):
            start = _event_time(event, "start")
        if start and start.date() == end.date():
            chips = [("Horário", f"{start:%H:%M} – {end:%H:%M}")]
        else:
            day = PT_DAYS_SHORT[end.weekday()]
            chips = [("Até", f"{day} {end:%d/%m} · {end:%H:%M}")]
        if start:
            chips.append(("Duração", _duration(end - start)))
        pokemon, bonuses = _event_extras(event)
        return StatCard(
            kicker=_event_heading(event),
            hero=None,
            title=event["name"],
            image=await usable_image(event.get("image")) or "",
            chips=tuple(chips),
            pokemon=pokemon,
            bonuses=bonuses,
        )

    async def _send_boards(
        self, channel, heading, panels, groups, text_cards, *, is_end, acknowledge
    ):
        """One image per event, each in its own message as the embeds were,
        so Discord shows every one full width; the first message carries the
        post's title. If any image doesn't render, the whole post goes out as
        the text cards (`text_cards()`) instead, so an event is never dropped
        for a renderer fault."""
        pngs = [
            await self.poliswag.image_generator.generate_stat_board([panel], "")
            for panel in panels
        ]
        if pngs and all(pngs):
            kind = "evento-terminado" if is_end else "novo-evento"
            for index, (png, group) in enumerate(zip(pngs, groups)):
                await channel.send(
                    f"**{heading}**" if index == 0 else None,
                    file=discord.File(
                        io.BytesIO(png), filename=f"{kind}-{index + 1}.png"
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                if acknowledge:
                    await self._ack_events(group, is_end=is_end)
            return
        await self._send_event_batches(
            channel,
            f"**{heading}**",
            await text_cards(),
            groups,
            [],
            is_end=is_end,
            acknowledge=acknowledge,
        )

    async def build_event_embed(self, event, is_ended=False, summary=None):
        event_end = _event_time(event, "end")
        emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])
        event_link = self.poliswag.event_manager.get_event_link(event)
        event_type_key = self.poliswag.event_manager.get_event_type_key(
            event["event_type"]
        )
        color = self.poliswag.event_manager.event_colors.get(event_type_key, 0x3498DB)
        if is_ended:
            description = (
                summary.headline
                if summary
                else self.poliswag.event_manager.format_end_time(
                    event_end, verb="Terminou"
                )
            )
        else:
            description = self.poliswag.event_manager.format_end_time(event_end)
        embed = discord.Embed(
            title=f"{emoji} {event['name']}"[:256],
            url=event_link,
            description=description,
            color=color,
        )
        if is_ended and summary:
            # One column per area, side by side; the caveat as the footer.
            for name, value in summary.areas:
                embed.add_field(name=name, value=value, inline=True)
            embed.set_footer(text=summary.note)
        image = await usable_image(event.get("image"))
        if image:
            embed.set_thumbnail(url=image)
        return embed

    async def send_weekly_digest(self, channel=None) -> bool:
        """Post the weekly calendar; split across messages within Discord limits.

        Returns True when at least one message was sent, False on a quiet
        week (no events after filtering) or when the channel is truly
        unconfigured (id 0). A quiet week or unconfigured channel still
        counts as done for _check_weekly_digest's dedup marker, so neither
        retries every tick. A configured id whose object is missing (startup
        lookup failed) is recovered via fetch_channel; a lookup failure
        raises so _check_weekly_digest leaves the marker unset for retry.

        Discord caps an embed description at 4096 chars and a message's
        embeds at 6000 chars total. The calendar is chunked at line
        boundaries so no entry is dropped or truncated; mentions stay off on
        every send. Discord acceptance and the DB marker cannot be atomic: a
        crash between two batches, or between the last send and the marker
        write in _check_weekly_digest, may resend an earlier batch on retry
        (partial-batch duplicates).
        """
        now = datetime.datetime.now()
        today = now.date()
        events = await self.poliswag.event_manager.get_weekly_events()
        if not events:
            return False

        week_end = today + datetime.timedelta(days=6)
        date_range = f"{today.strftime('%d/%m')} a {week_end.strftime('%d/%m')}"

        def when(moment):
            # The year only when it isn't this one: "até 11/04" read as next
            # month for an event that ends next April.
            fmt = "%d/%m" if moment.year == today.year else "%d/%m/%Y"
            return moment.strftime(fmt)

        ongoing = []
        upcoming_by_day = {}
        week_events = []

        for event in events:
            event_type = event["event_type"].lower()
            if "battle" in event_type or "league" in event_type:
                continue
            week_events.append(event)

            event_start = _event_time(event, "start")
            event_end = _event_time(event, "end")
            emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])

            if event_start <= now:
                ongoing.append(
                    (event_end, f"{emoji} **{event['name']}** · até {when(event_end)}")
                )
            else:
                day_key = (
                    f"Hoje {event_start.strftime('%d/%m')}"
                    if event_start.date() == today
                    else f"{PT_DAYS_SHORT[event_start.weekday()]} {event_start.strftime('%d/%m')}"
                )
                if day_key not in upcoming_by_day:
                    upcoming_by_day[day_key] = []
                end_str = (
                    event_end.strftime("%H:%M")
                    if event_end.date() == event_start.date()
                    else f"{when(event_end)} {event_end.strftime('%H:%M')}"
                )
                upcoming_by_day[day_key].append(
                    f"{emoji} **{event['name']}** · {event_start.strftime('%H:%M')}–{end_str}"
                )

        blocks = []
        if ongoing:
            # Soonest to end first: that's the one to hurry for.
            ongoing.sort(key=lambda item: item[0])
            blocks.append(
                "\n".join(["**A decorrer**", *(line for _end, line in ongoing)])
            )
        for day, day_events in upcoming_by_day.items():
            blocks.append("\n".join([f"**{day}**", *day_events]))

        if not blocks:
            return False

        target = channel or self.poliswag.CONVIVIO_CHANNEL
        if target is None:
            if channel is None and Config.CONVIVIO_CHANNEL_ID:
                # Configured id but the object is missing (startup lookup
                # failed): recover here instead of losing the delivery.
                try:
                    target = await self.poliswag.fetch_channel(
                        Config.CONVIVIO_CHANNEL_ID
                    )
                except (
                    discord.Forbidden,
                    discord.NotFound,
                    discord.HTTPException,
                ) as e:
                    self.poliswag.utility.log_to_file(
                        f"Weekly digest: CONVIVIO lookup failed: {e}", "ERROR"
                    )
                    raise
                self.poliswag.CONVIVIO_CHANNEL = target
            if target is None:
                self.poliswag.utility.log_to_file(
                    "Weekly digest: no channel configured", "ERROR"
                )
                return False

        board = await self.poliswag.image_generator.generate_week_board(
            [
                WeekEvent(
                    title=event["name"],
                    kicker=_type_label(event["event_type"]),
                    image=event.get("image") or "",
                    start=_event_time(event, "start"),
                    end=_event_time(event, "end"),
                )
                for event in week_events
            ],
            now,
        )
        if board:
            await target.send(
                file=discord.File(io.BytesIO(board), filename="eventos-da-semana.png"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        # The image didn't render: the text calendar, as before.
        title = f"Eventos da semana · {date_range}"
        footer = f"Atualizado a {now.strftime('%d/%m/%Y %H:%M')}"
        chunks = _split_weekly_description("\n\n".join(blocks))
        for index, chunk in enumerate(chunks):
            embed = build_embed(
                title if index == 0 else f"{title} (cont.)",
                description=chunk,
                footer=footer if index == len(chunks) - 1 else None,
            )
            await target.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        return True
