import requests
from datetime import datetime


class EventManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.events = None

    def fetch_events(self):
        response = requests.get(
            "https://raw.githubusercontent.com/ccev/pogoinfo/v2/active/events.json"
        )
        response.raise_for_status()
        self.events = response.json()
        self.store_events_in_database()

    def store_events_in_database(self):
        if self.events is None:
            return

        for event in self.events:
            if event.get("start") and event.get("end"):
                event_name = event["name"].replace("'", "\\'")
                name_lower = event["name"].lower()
                if "unannounced" not in name_lower:
                    query = f"INSERT IGNORE INTO event(name, start, end) VALUES ('{event_name}', '{event['start']}', '{event['end']}');"
                    self.poliswag.db.execute_query_to_database(query)

    def get_active_events(self):
        current_time = datetime.now()
        query = f"""
            SELECT name, start, end, notification_date 
            FROM event 
            WHERE (notification_date IS NULL AND start < '{current_time}') 
               OR (notification_date < end AND end < '{current_time}');
        """
        stored_events = self.poliswag.db.get_data_from_database(query)

        if not stored_events:
            return None

        event_names = [
            f"'{event['name'].replace('\'', '\\\'')}'" for event in stored_events
        ]
        names_string = ", ".join(event_names)

        embeds_content = []
        for event in self.events:
            event_start = datetime.strptime(event["start"], "%Y-%m-%d %H:%M").replace(
                second=0, microsecond=0
            )
            event_end = datetime.strptime(event["end"], "%Y-%m-%d %H:%M").replace(
                second=0, microsecond=0
            )

            if event_start <= current_time <= event_end:
                event_bonuses = []
                for key, value in event.items():
                    if (
                        key
                        not in [
                            "has_quests",
                            "has_spawnpoints",
                            "start",
                            "end",
                            "name",
                            "type",
                        ]
                        and value
                    ):
                        if key == "bonuses":
                            event_bonuses.extend(
                                [f"• {bonus['text']}" for bonus in value]
                            )
                        else:
                            event_bonuses.append(f"• {key.capitalize()}: {value}")

                embeds_content.append(
                    {
                        "content": (
                            "**ATUAIS EVENTOS ATIVOS:**" if not embeds_content else None
                        ),
                        "name": event["name"].upper(),
                        "body": "\n".join(event_bonuses),
                        "footer": f"Entre {event_start} e {event_end}",
                    }
                )

        update_query = f"UPDATE event SET notification_date = CASE WHEN notification_date IS NULL THEN start ELSE end END WHERE name IN({names_string});"
        self.poliswag.db.execute_query_to_database(update_query)

        return embeds_content
