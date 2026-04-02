from datetime import datetime, timedelta
import json
import random
import re


class EventManager:
    def __init__(self, poliswag, use_ai=True):
        self.poliswag = poliswag
        self.events = None
        self.use_ai = use_ai
        self.cache_failed_cooldown = None
        self.ai_cooldown_duration = timedelta(minutes=30)
        self.event_colors = {
            "community-day": 0xFF9D00,  # Orange
            "spotlight-hour": 0xFFD700,  # Gold
            "raid-day": 0xFF0000,  # Red
            "go-battle": 0x800080,  # Purple
            "research": 0x4169E1,  # Royal Blue
            "season": 0x32CD32,  # Lime Green
            "default": 0x3498DB,  # Blue
        }
        self.feature_emojis = {
            "shiny": "✨",
            "raid": "🛡️",
            "research": "🔍",
            "egg": "🥚",
            "stardust": "💫",
            "candy": "🍬",
            "xp": "📈",
            "evolution": "⚡",
            "trade": "🔄",
            "battle": "⚔️",
            "catch": "🎯",
            "hatch": "🐣",
            "walk": "👣",
            "friend": "👫",
            "item": "🎒",
            "default": "🎮",
        }

    async def fetch_events(self):
        response = await self.poliswag.utility.fetch_data("events")
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

        for event in self.events:
            try:
                event_id = event.get("id", "")
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

                query = self.build_upsert_query(
                    name, start, end, image, event_type, link, event
                )
                self.poliswag.db.execute_query_to_database(query)
            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error storing event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

    async def check_current_events_changes(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        query = f"""
            SELECT name, start, end, image, event_type, link, extra_data, notification_date, notification_end_date,
                CASE
                    WHEN start <= '{current_time}' AND end >= '{current_time}' THEN 'active'
                    WHEN start <= '{current_time}' AND end <= '{current_time}' THEN 'ended'
                    WHEN start > '{current_time}' THEN 'upcoming'
                END as event_status
            FROM event
            LEFT OUTER JOIN excluded_event_type ON excluded_event_type.type = event.event_type
            WHERE (
                (start <= '{current_time}' AND end >= '{current_time}') OR -- Active events
                (end <= '{current_time}' AND notification_end_date IS NULL) OR -- Recently ended, not notified
                (start > '{current_time}') -- Upcoming (though not directly used for notification here)
            )
            AND excluded_event_type.type IS NULL
            ORDER BY end ASC
        """
        events = self.poliswag.db.get_data_from_database(query)
        if not events:
            return None

        started_events = []
        ended_events = []
        active_events = []

        first_started_event_image = None
        first_ended_event_image = None

        for event in events:
            try:
                event_start = datetime.strptime(
                    str(event["start"]), "%Y-%m-%d %H:%M:%S"
                )
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")
                extra_data = self.parse_extra_data(event)

                is_newly_started = (
                    event["event_status"] == "active"
                    and event.get("notification_date") is None
                )
                is_already_active = (
                    event["event_status"] == "active"
                    and event.get("notification_date") is not None
                )
                is_newly_ended = (
                    event["event_status"] == "ended"
                    and event.get("notification_end_date") is None
                )

                parsed_content = None
                if is_newly_started or is_already_active:
                    parsed_content = await self.get_or_generate_parsed_content(
                        event, extra_data, event_start
                    )

                if parsed_content is None:
                    parsed_content = {
                        "featured_pokemon": None,
                        "bonuses": None,
                        "special_features": None,
                        "time_period": f"{event_start.strftime('%d/%m/%Y %H:%M')} até {event_end.strftime('%d/%m/%Y %H:%M')}",
                    }

                event_emoji = self.get_event_emoji(event["event_type"], parsed_content)
                event_link = self.get_event_link(event)
                event_name_linked = (
                    f"[{event['name']}]({event_link})"
                    if event_link
                    else f"{event['name']}"
                )

                if is_newly_started:
                    time_remaining = self.get_time_remaining(event_end)
                    event_description = await self.generate_event_description(
                        event, parsed_content
                    )
                    description_text = "\n".join(event_description)

                    event_entry = f"{event_emoji} **{event_name_linked}**"
                    event_entry += (
                        f"\n{description_text}\nTermina em {time_remaining}\n"
                    )
                    started_events.append(
                        {
                            "entry": event_entry,
                            "name": event["name"],
                            "emoji": event_emoji,
                            "image": event.get("image", ""),
                            "type": event["event_type"],
                        }
                    )
                    self.mark_event_notified(event, event_start, is_end=False)

                    if first_started_event_image is None and event.get("image"):
                        first_started_event_image = event.get("image")

                elif is_newly_ended:
                    event_entry = f"{event_emoji} **{event_name_linked}**\n"
                    ended_events.append(
                        {
                            "entry": event_entry,
                            "name": event["name"],
                            "emoji": event_emoji,
                            "image": event.get("image", ""),
                            "type": event["event_type"],
                        }
                    )
                    self.mark_event_notified(event, event_end, is_end=True)

                    if first_ended_event_image is None and event.get("image"):
                        first_ended_event_image = event.get("image")

                elif is_already_active:
                    time_remaining = self.get_time_remaining(event_end)
                    event_entry = f"{event_emoji} **{event_name_linked}**\n"
                    event_entry += f"Termina em {time_remaining} ({event_end.strftime('%d/%m/%Y %H:%M')})\n"
                    active_events.append(event_entry)

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing event {event.get('name', 'unknown')} during notification check: {str(e)}",
                    "ERROR",
                )

        if not (started_events or ended_events):
            return None

        result = {}
        current_datetime = datetime.now().strftime("%d/%m/%Y %H:%M")

        if started_events:
            first_event = started_events[0]
            event_type_key = self.get_event_type_key(first_event["type"])
            color = self.event_colors.get(event_type_key, self.event_colors["default"])

            result["started"] = {
                "content": "**🎉 EVENTOS INICIADOS 🎉**",
                "name": f"{first_event['emoji']} {first_event['name']}",
                "body": "\n\n".join([event["entry"] for event in started_events]),
                "footer": f"Atualizado a {current_datetime}",
                "color": color,
                "image": first_started_event_image,
                "thumbnail": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1301.png",
            }

        if ended_events:
            first_event = ended_events[0]
            result["ended"] = {
                "content": "**⏰ EVENTOS TERMINADOS ⏰**",
                "name": f"{first_event['emoji']} {first_event['name']} (Terminado)",
                "body": "\n\n".join([event["entry"] for event in ended_events]),
                "footer": f"Atualizado a {current_datetime}",
                "color": 0xAAAAAA,  # Grey for ended events
                "image": first_ended_event_image,
                "thumbnail": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1301.png",
            }

        if active_events:
            result["active"] = {
                "content": f"**🌟 EVENTOS ATIVOS ({len(active_events)}) 🌟**",
                "name": "",
                "body": "\n\n".join(active_events),
                "footer": f"Atualizado a {current_datetime}",
                "color": 0x3498DB,
                "image": "",
                "thumbnail": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1301.png",
            }

        return result

    def get_time_remaining(self, end_time):
        delta = end_time - datetime.now()
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"

    def build_upsert_query(self, name, start, end, image, event_type, link, event):
        return f"""
            INSERT INTO event(name, start, end, image, event_type, link, extra_data, notification_date, notification_end_date)
            VALUES (
                '{name.replace("'", "''")}',
                '{start}',
                '{end}',
                '{image.replace("'", "''")}',
                '{event_type.replace("'", "''")}',
                '{link.replace("'", "''")}',
                '{json.dumps(event).replace("'", "''")}'
                ,NULL,
                NULL
            )
            ON DUPLICATE KEY UPDATE
            image = '{image.replace("'", "''")}',
            event_type = '{event_type.replace("'", "''")}',
            link = '{link.replace("'", "''")}',
            extra_data = '{json.dumps(event).replace("'", "''")}'
        """

    def parse_extra_data(self, event):
        extra_data = {}
        if "extra_data" in event and event["extra_data"]:
            try:
                extra_data = json.loads(event["extra_data"])
            except:
                pass
        return extra_data

    async def get_or_generate_parsed_content(self, event, extra_data, event_start):
        parsed_content = None
        if "parsed_content" in extra_data:
            parsed_content = extra_data["parsed_content"]
        elif self.use_ai and self.cache_failed_cooldown is None:
            event_data = {
                "id": event.get("id", ""),
                "name": event["name"],
                "eventType": event["event_type"],
                "start": str(event["start"]),
                "end": str(event["end"]),
                "extraData": extra_data.get("extraData", {}),
            }

            parsed_content = await self.poliswag.poliwiz.process_event(event_data)

            if parsed_content:
                extra_data["parsed_content"] = parsed_content
                update_query = f"""
                    UPDATE event
                    SET extra_data = '{json.dumps(extra_data).replace("'", "''")}'
                    WHERE name = '{event["name"].replace("'", "''")}' AND start = '{event_start.strftime("%Y-%m-%d %H:%M:%S")}'
                """
                self.poliswag.db.execute_query_to_database(update_query)
        else:
            event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")
            parsed_content = {
                "featured_pokemon": None,
                "bonuses": None,
                "special_features": None,
                "time_period": f"{event_start.strftime('%d/%m/%Y %H:%M')} até {event_end.strftime('%d/%m/%Y %H:%M')}",
            }

        return parsed_content

    async def generate_event_description(self, event, parsed_content):
        event_description = []

        if not self.use_ai:
            event_description.append(f"🏷️ **Tipo de evento:** {event['event_type']}")

        if (
            parsed_content
            and parsed_content.get("featured_pokemon")
            and parsed_content["featured_pokemon"] != "None"
        ):
            featured = parsed_content["featured_pokemon"]
            if isinstance(featured, list):
                featured = ", ".join(featured)
            event_description.append(f"🔍 **Pokémon em destaque:**\n└─ {featured}")

        if (
            parsed_content
            and parsed_content.get("bonuses")
            and parsed_content["bonuses"] != "None"
        ):
            bonuses = parsed_content["bonuses"]
            bonuses_text = (
                "\n└─ ".join(bonuses) if isinstance(bonuses, list) else bonuses
            )
            event_description.append(f"🎁 **Bónus:**\n└─ {bonuses_text}")

        if (
            parsed_content
            and parsed_content.get("special_features")
            and parsed_content["special_features"] != "None"
        ):
            features = parsed_content["special_features"]
            features_text = (
                "\n└─ ".join(features) if isinstance(features, list) else features
            )
            event_description.append(f"✨ **Funcionalidades:**\n└─ {features_text}")

        return event_description

    def mark_event_notified(self, event, event_date, is_end=False):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        field_name = "notification_end_date" if is_end else "notification_date"
        date_field = "end" if is_end else "start"

        update_query = f"""
            UPDATE event
            SET {field_name} = '{current_time}'
            WHERE name = '{event["name"].replace("'", "''")}' AND {date_field} = '{event_date.strftime("%Y-%m-%d %H:%M:%S")}'
        """
        self.poliswag.db.execute_query_to_database(update_query)

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

    def get_event_emoji(self, event_type, parsed_content):
        event_type = event_type.lower()

        if "community" in event_type:
            return "🌟"
        elif "spotlight" in event_type:
            return "🔦"
        elif "raid" in event_type:
            return "🛡️"
        elif "battle" in event_type:
            return "⚔️"
        elif "research" in event_type:
            return "🔍"
        elif "season" in event_type:
            return "🍂"

        if parsed_content:
            features = []
            if parsed_content.get("special_features"):
                if isinstance(parsed_content["special_features"], list):
                    features.extend(parsed_content["special_features"])
                else:
                    features.append(parsed_content["special_features"])

            if parsed_content.get("bonuses"):
                if isinstance(parsed_content["bonuses"], list):
                    features.extend(parsed_content["bonuses"])
                else:
                    features.append(parsed_content["bonuses"])

            feature_text = " ".join(features).lower()

            for keyword, emoji in self.feature_emojis.items():
                if keyword in feature_text:
                    return emoji

        pokemon_emojis = ["🎮", "🎯", "🎪", "🎨", "🎭", "🎡"]
        return random.choice(pokemon_emojis)

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
