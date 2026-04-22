import time
from datetime import datetime, timedelta
import json
import random
import re
from modules.http_client import fetch_data
from modules.locale_pt import MONTH_NAMES, PT_MONTHS_SHORT


# LeekDuck's scraped source rarely changes within a 15-minute window; hitting
# it every minute is wasted load on both our DB and their CDN.
FETCH_EVENTS_INTERVAL_SECONDS = 900


class EventManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.events = None
        self._last_events_fetch = 0.0
        self.event_colors = {
            "community-day": 0xFF9D00,  # Orange
            "spotlight-hour": 0xFFD700,  # Gold
            "raid-day": 0xFF0000,  # Red
            "go-battle": 0x800080,  # Purple
            "research": 0x4169E1,  # Royal Blue
            "season": 0x32CD32,  # Lime Green
            "default": 0x3498DB,  # Blue
        }

    async def fetch_events(self):
        now = time.time()
        if now - self._last_events_fetch < FETCH_EVENTS_INTERVAL_SECONDS:
            return
        self._last_events_fetch = now

        response = await fetch_data("events", log_fn=self.poliswag.utility.log_to_file)
        if response is None:
            self.poliswag.utility.log_to_file(
                "Failed to fetch events from API", "ERROR"
            )
            return

        try:
            self.events = (
                json.loads(response) if isinstance(response, str) else response
            )
            await self.process_and_store_events()
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error processing events: {str(e)}", "ERROR"
            )

    async def process_and_store_events(self):
        if self.events is None:
            return

        api_names = {
            event.get("name")
            for event in self.events
            if event.get("name") and "unannounced" not in event.get("name", "").lower()
        }

        # Remove future events from DB that are no longer in the API response
        # (covers renames: old name disappears, new name gets upserted below)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        future_db_events = self.poliswag.db.get_data_from_database(
            "SELECT name FROM event WHERE start > %s",
            params=(now,),
        )
        for db_event in future_db_events:
            if db_event["name"] not in api_names:
                self.poliswag.db.execute_query_to_database(
                    "DELETE FROM event WHERE name = %s AND start > %s",
                    params=(db_event["name"], now),
                )

        for event in self.events:
            try:
                start = event.get("start")
                end = event.get("end")
                name = event.get("name")
                image = event.get("image", "")
                event_type = event.get("eventType", "")
                link = event.get("link", "")

                if not all([start, end, name]) or "unannounced" in name.lower():
                    continue

                start = self.poliswag.utility.format_datetime_string(start)
                end = self.poliswag.utility.format_datetime_string(end)

                query, params = self.build_upsert_query(
                    name, start, end, image, event_type, link, event
                )
                self.poliswag.db.execute_query_to_database(query, params=params)
            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error storing event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

    async def check_current_events_changes(self, at_time=None, dry_run=False):
        if dry_run and at_time is not None:
            return self._dry_run_changes(at_time)

        current_time = (at_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        events = self.poliswag.db.get_data_from_database(
            """
            SELECT name, start, end, event_type, image, link, notification_date, notification_end_date,
                CASE
                    WHEN start <= %s AND end >= %s THEN 'active'
                    WHEN start <= %s AND end <= %s THEN 'ended'
                END as event_status
            FROM event
            LEFT OUTER JOIN excluded_event_type ON excluded_event_type.type = event.event_type
            WHERE (
                (start <= %s AND end >= %s) OR
                (end <= %s AND notification_end_date IS NULL)
            )
            AND excluded_event_type.type IS NULL
            ORDER BY end ASC
            """,
            params=(
                current_time,
                current_time,
                current_time,
                current_time,
                current_time,
                current_time,
                current_time,
            ),
        )
        if not events:
            return None

        started = []
        ended = []
        for event in events:
            try:
                event_start = datetime.strptime(
                    str(event["start"]), "%Y-%m-%d %H:%M:%S"
                )
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")

                if (
                    event["event_status"] == "active"
                    and event.get("notification_date") is None
                ):
                    if not dry_run:
                        self.mark_event_notified(event, event_start, is_end=False)
                    started.append(event)
                elif (
                    event["event_status"] == "ended"
                    and event.get("notification_end_date") is None
                ):
                    if not dry_run:
                        self.mark_event_notified(event, event_end, is_end=True)
                    ended.append(event)
            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing event {event.get('name', 'unknown')} during notification check: {str(e)}",
                    "ERROR",
                )

        if not started and not ended:
            return None
        return {"started": started, "ended": ended}

    def _dry_run_changes(self, at_time):
        """Find events that transitioned within the minute starting at at_time.
        Ignores notification state — for debugging via !testevent HH:MM."""
        window_start = at_time.strftime("%Y-%m-%d %H:%M:00")
        window_end = (at_time + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:00")
        started = self.poliswag.db.get_data_from_database(
            """
            SELECT e.name, e.start, e.end, e.event_type, e.image, e.link FROM event e
            LEFT JOIN excluded_event_type ext ON ext.type = e.event_type
            WHERE e.start >= %s AND e.start < %s AND ext.type IS NULL
            ORDER BY e.start ASC
            """,
            params=(window_start, window_end),
        )
        ended = self.poliswag.db.get_data_from_database(
            """
            SELECT e.name, e.start, e.end, e.event_type, e.image, e.link FROM event e
            LEFT JOIN excluded_event_type ext ON ext.type = e.event_type
            WHERE e.end >= %s AND e.end < %s AND ext.type IS NULL
            ORDER BY e.end ASC
            """,
            params=(window_start, window_end),
        )
        if not started and not ended:
            return None
        return {"started": started, "ended": ended}

    def format_end_time(self, end_time, verb="Termina"):
        now = datetime.now()
        if end_time.date() == now.date():
            return f"{verb} às {end_time.strftime('%H:%M')}"
        return (
            f"{verb} a {end_time.day:02d} {PT_MONTHS_SHORT[end_time.month]} "
            f"- {end_time.strftime('%H:%M')}"
        )

    def build_upsert_query(self, name, start, end, image, event_type, link, event):
        extra = json.dumps(event)
        return (
            """
            INSERT INTO event(name, start, end, image, event_type, link, extra_data, notification_date, notification_end_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
            ON DUPLICATE KEY UPDATE
            image = %s,
            event_type = %s,
            link = %s,
            extra_data = %s
            """,
            (
                name,
                start,
                end,
                image,
                event_type,
                link,
                extra,
                image,
                event_type,
                link,
                extra,
            ),
        )

    def mark_event_notified(self, event, event_date, is_end=False):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        field_name = "notification_end_date" if is_end else "notification_date"
        date_field = "end" if is_end else "start"

        self.poliswag.db.execute_query_to_database(
            f"UPDATE event SET {field_name} = %s WHERE name = %s AND {date_field} = %s",
            params=(
                current_time,
                event["name"],
                event_date.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    def get_event_type_key(self, event_type):
        event_type = event_type.lower()
        if "community" in event_type or "day" in event_type:
            return "community-day"
        elif "spotlight" in event_type or "hour" in event_type:
            return "spotlight-hour"
        elif "raid" in event_type:
            return "raid-day"
        elif "battle" in event_type or "league" in event_type:
            return "go-battle"
        elif "research" in event_type:
            return "research"
        elif "season" in event_type:
            return "season"
        return "default"

    def get_event_emoji(self, event_type):
        event_type = event_type.lower()

        if "community" in event_type:
            return "🌟"
        if "spotlight" in event_type:
            return "🔦"
        if "raid" in event_type:
            return "🛡️"
        if "battle" in event_type:
            return "⚔️"
        if "research" in event_type:
            return "🔍"
        if "season" in event_type:
            return "🍂"

        return random.choice(["🎮", "🎯", "🎪", "🎨", "🎭", "🎡"])

    def get_weekly_events(self):
        now = datetime.now()
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        rows = self.poliswag.db.get_data_from_database(
            """
            SELECT e.name, MIN(e.start) AS start, MAX(e.end) AS end, e.image, e.event_type, e.link
            FROM event e
            LEFT JOIN excluded_event_type ext ON ext.type = e.event_type
            WHERE e.end >= %s AND e.start <= %s
            AND ext.type IS NULL
            AND e.name NOT LIKE '[Promo Code]%%'
            GROUP BY e.name, e.image, e.event_type, e.link
            ORDER BY MIN(e.start) ASC
            """,
            params=(
                now.strftime("%Y-%m-%d %H:%M:%S"),
                week_end.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

        def is_generic_name(name):
            return any(word in MONTH_NAMES for word in name.lower().split())

        # Deduplicate by exact name first
        by_name = {}
        for row in rows:
            if row["name"] not in by_name:
                by_name[row["name"]] = row

        # Then deduplicate by (start, end, event_type), preferring specific over generic names
        by_slot = {}
        for row in by_name.values():
            slot_key = (str(row["start"]), str(row["end"]), row["event_type"])
            if slot_key not in by_slot:
                by_slot[slot_key] = row
            else:
                existing = by_slot[slot_key]
                if is_generic_name(existing["name"]) and not is_generic_name(
                    row["name"]
                ):
                    by_slot[slot_key] = row

        return list(by_slot.values())

    def get_event_link(self, event):
        if event.get("link") and "leekduck.com" in event.get("link"):
            return event.get("link")

        event_name = event.get("name", "").strip()
        if not event_name:
            return None

        url_name = re.sub(r"[^\w\s-]", "", event_name.lower())
        url_name = re.sub(r"[\s-]+", "-", url_name)

        event_type_path = "events"
        event_type = event.get("event_type", "").lower()

        if "community" in event_type and "day" in event_type:
            event_type_path = "community-day"
        elif "spotlight" in event_type:
            event_type_path = "spotlight-hour"
        elif "raid" in event_type or "battle" in event_type:
            event_type_path = "raid-day"
        elif "research" in event_type:
            event_type_path = "research"
        elif "season" in event_type:
            event_type_path = "season"

        return f"https://www.leekduck.com/{event_type_path}/{url_name}"
