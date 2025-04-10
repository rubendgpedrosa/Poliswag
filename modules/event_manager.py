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
        # Define theme colors for different event types
        self.event_colors = {
            "community-day": 0xFF9D00,  # Orange
            "spotlight-hour": 0xFFD700,  # Gold
            "raid-day": 0xFF0000,  # Red
            "go-battle": 0x800080,  # Purple
            "research": 0x4169E1,  # Royal Blue
            "season": 0x32CD32,  # Lime Green
            "default": 0x3498DB,  # Blue
        }
        # Emoji mappings for different event features
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

        # Fetch potentially relevant events including their notification status
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
            ORDER BY end ASC -- Keep sorting, might be useful
        """
        events = self.poliswag.db.get_data_from_database(query)
        if not events:
            return None

        embed_content = {
            "content": "**🔔 ATUALIZAÇÃO NOS EVENTOS 🔔**",
            "name": "",  # Will be set later if needed
            "body": "",
            "footer": f"Atualizado a {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            "color": 0x3498DB,  # Default color
            "image": "",
            "thumbnail": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1301.png",  # Default thumbnail
        }

        sections = []
        started_events = []
        ended_events = []
        active_events = []
        first_started_event_image = (
            None  # To store the image of the first *newly* started event
        )

        for event in events:
            try:
                event_start = datetime.strptime(
                    str(event["start"]), "%Y-%m-%d %H:%M:%S"
                )
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")
                extra_data = self.parse_extra_data(event)

                # --- Logic Modification Start ---
                # Determine the actual state change we care about for notifications
                is_newly_started = (
                    event["event_status"] == "active"
                    and event.get("notification_date") is None
                )
                is_already_active = (
                    event["event_status"] == "active"
                    and event.get("notification_date") is not None
                )
                # Check for ended events that haven't had their end notification sent
                is_newly_ended = (
                    event["event_status"] == "ended"
                    and event.get("notification_end_date") is None
                )
                # --- Logic Modification End ---

                parsed_content = None
                # Get/Generate content if it's active (either newly or ongoing) as we need info for display
                # Or if using AI and it hasn't been parsed yet for a newly started event.
                if is_newly_started or is_already_active:
                    # The condition inside get_or_generate_parsed_content checks 'active' status which is correct here
                    parsed_content = await self.get_or_generate_parsed_content(
                        event, extra_data, event_start
                    )
                else:  # For ended events or if AI fails/disabled
                    parsed_content = {
                        "featured_pokemon": None,
                        "bonuses": None,
                        "special_features": None,
                        "time_period": f"{event_start.strftime('%d/%m/%Y %H:%M')} até {event_end.strftime('%d/%m/%Y %H:%M')}",
                    }

                # Fallback if parsing fails
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

                # --- Process based on the corrected state ---
                if is_newly_started:
                    time_remaining = self.get_time_remaining(event_end)
                    event_description = await self.generate_event_description(
                        event, parsed_content
                    )
                    description_text = "\n".join(event_description)

                    event_entry = f"{event_emoji} **{event_name_linked}**\n└─ Termina em {time_remaining}\n{description_text}"
                    started_events.append(event_entry)
                    self.mark_event_notified(
                        event, event_start, is_end=False
                    )  # Mark start notification sent

                    # Store the image of the first newly started event for the embed
                    if first_started_event_image is None and event.get("image"):
                        first_started_event_image = event.get("image")
                    # Set embed color based on the first started event type
                    if not embed_content.get("name"):  # Only set color/title once
                        embed_content["name"] = f"{event_emoji} {event['name']}"
                        event_type_key = self.get_event_type_key(event["event_type"])
                        embed_content["color"] = self.event_colors.get(
                            event_type_key, self.event_colors["default"]
                        )
                        # Optionally set thumbnail based on event type
                        # embed_content["thumbnail"] = self.get_event_thumbnail(event["event_type"])

                elif is_newly_ended:
                    event_entry = f"{event_emoji} **{event_name_linked}**\n└─ Terminou a {event_end.strftime('%d/%m/%Y %H:%M')}\n"
                    ended_events.append(event_entry)
                    self.mark_event_notified(
                        event, event_end, is_end=True
                    )  # Mark end notification sent
                    # Set embed color/title if not already set by a started event
                    if not embed_content.get("name"):
                        embed_content["name"] = (
                            f"{event_emoji} {event['name']} (Terminado)"
                        )
                        embed_content["color"] = 0xAAAAAA  # Grey for ended events

                elif is_already_active:
                    time_remaining = self.get_time_remaining(event_end)
                    event_entry = f"{event_emoji} **{event_name_linked}**\n└─ Termina em {time_remaining} ({event_end.strftime('%d/%m/%Y %H:%M')})\n"
                    # Optional: Add brief details from parsed_content if desired
                    # if parsed_content and parsed_content.get("featured_pokemon"):
                    #    event_entry += f"   └─ Destaque: {parsed_content['featured_pokemon']}\n"
                    active_events.append(event_entry)

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing event {event.get('name', 'unknown')} during notification check: {str(e)}",
                    "ERROR",
                )
                # Optionally log traceback: import traceback; traceback.print_exc()

        # --- Assemble the notification ---
        # Only proceed if there's a change (started or ended event)
        if not (started_events or ended_events):
            return None  # No changes to notify

        if started_events:
            sections.append("🎉 **EVENTOS INICIADOS:**\n" + "\n".join(started_events))

        if ended_events:
            sections.append("⏰ **EVENTOS TERMINADOS:**\n" + "\n".join(ended_events))

        # Always include active events if there was a change notification
        if active_events:
            sections.append(
                f"🌟 **EVENTOS ATIVOS ({len(active_events)}):**\n"
                + "\n".join(active_events)
            )

        embed_content["body"] = "\n\n".join(sections)

        # Use the image of the first *newly started* event if available
        if first_started_event_image:
            embed_content["image"] = first_started_event_image
        # Fallback: if only ended events, maybe use a generic image or none?

        # Ensure embed name is set if only ended events occurred
        if not embed_content.get("name") and ended_events:
            embed_content["name"] = "Atualização de Eventos"  # Generic title

        return embed_content

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

        # Add featured Pokémon if available
        if (
            parsed_content
            and parsed_content.get("featured_pokemon")
            and parsed_content["featured_pokemon"] != "None"
        ):
            featured = parsed_content["featured_pokemon"]
            if isinstance(featured, list):
                featured = ", ".join(featured)
            event_description.append(f"🔍 **Pokémon em destaque:**\n└─ {featured}")

        # Add bonuses if available
        if (
            parsed_content
            and parsed_content.get("bonuses")
            and parsed_content["bonuses"] != "None"
        ):
            bonuses = parsed_content["bonuses"]
            if isinstance(bonuses, list):
                bonuses_text = "\n└─ ".join(bonuses)
                event_description.append(f"🎁 **Bónus:**\n└─ {bonuses_text}")
            else:
                event_description.append(f"🎁 **Bónus:**\n└─ {bonuses}")

        # Add special features if available
        if (
            parsed_content
            and parsed_content.get("special_features")
            and parsed_content["special_features"] != "None"
        ):
            features = parsed_content["special_features"]
            if isinstance(features, list):
                features_text = "\n└─ ".join(features)
                event_description.append(
                    f"✨ **Características especiais:**\n└─ {features_text}"
                )
            else:
                event_description.append(
                    f"✨ **Características especiais:**\n└─ {features}"
                )

        # Add link if available
        if event.get("link"):
            event_description.append(
                f"🔗 **Mais informações:** [Clique aqui]({event.get('link')})"
            )

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

    def count_active_events(self, current_time):
        active_query = f"""
            SELECT COUNT(*) as active_count
            FROM event
            WHERE start <= '{current_time}' AND end > '{current_time}';
        """
        active_result = self.poliswag.db.get_data_from_database(active_query)
        return active_result[0]["active_count"] if active_result else 0

    def get_event_type_key(self, event_type):
        """Convert event type to a standardized key for color mapping"""
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
        """Get appropriate emoji for the event type and content"""
        event_type = event_type.lower()

        # Event type specific emojis
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

        # Check parsed content for keywords
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

        # Random Pokemon-themed emoji as fallback
        pokemon_emojis = ["🎮", "🎯", "🎪", "🎨", "🎭", "🎡"]
        return random.choice(pokemon_emojis)

    def get_event_thumbnail(self, event_type):
        event_type = event_type.lower()

        # These are placeholder URLs - replace with actual Pokemon GO asset URLs
        thumbnails = {
            "community day": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1401.png",
            "spotlight hour": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1402.png",
            "raid": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1403.png",
            "battle": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1105.png",
            "research": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1106.png",
            "season": "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1301.png",
        }

        for key, url in thumbnails.items():
            if key in event_type:
                return url

        # Default thumbnail
        return "https://raw.githubusercontent.com/PokeMiners/pogo_assets/master/Images/Items/Item_1301.png"

    def get_event_link(self, event):
        """Generate a LeekDuck URL for the event"""
        # First check if the event already has a link
        if event.get("link") and "leekduck.com" in event.get("link"):
            return event.get("link")

        # Otherwise, generate a LeekDuck URL based on the event name
        event_name = event.get("name", "").strip()
        if not event_name:
            return None

        # Convert the event name to a URL-friendly format for LeekDuck
        # Remove special characters and replace spaces with hyphens
        url_name = re.sub(r"[^\w\s-]", "", event_name.lower())
        url_name = re.sub(r"[\s-]+", "-", url_name)

        # Determine event type for URL path
        event_type_path = "events"  # Default path
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

        # Construct the LeekDuck URL
        return f"https://www.leekduck.com/{event_type_path}/{url_name}"
