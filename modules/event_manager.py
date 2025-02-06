import requests
from datetime import datetime

class EventManager():
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.events = None

    def get_events(self):
        request = requests.get('https://raw.githubusercontent.com/ccev/pogoinfo/v2/active/events.json')
        if request.status_code != 200:
            return None
        
        self.events = request.json()
        self.write_events_to_database()

    def write_events_to_database(self):
        for event in self.events:
            if event["start"] and event["end"]:
                event_name_escaped = event["name"].replace("'", "\\'")
                name_lower = event["name"].lower()
                unannounced = "unannounced" in name_lower

                if not unannounced:
                    self.poliswag.db.execute_query_to_database(f"INSERT IGNORE INTO event(name, start, end) VALUES ('{event_name_escaped}', '{event['start']}', '{event['end']}');")

    def get_current_active_events(self):
        current_time = datetime.now()
        stored_events = self.poliswag.db.get_data_from_database(f"SELECT name, start, end, notification_date FROM event WHERE (notification_date IS NULL AND start < '{current_time}') OR (notification_date < end AND end < '{current_time}');")
        if len(stored_events) == 0:
            return

        if isinstance(stored_events, list):
            names_stored_events = ", ".join([f"'{event["name"].replace("'", "\\'")}'" for event in stored_events])
        else:
            names_stored_events = f"'{stored_events['name'].replace("'", "\\'")}'"

        embeds_content = []
        for event in self.events:
            event_bonuses = []
            content = None
            if not embeds_content:
                content = "**ATUAIS EVENTOS ATIVOS:**"

            event_start = datetime.strptime(event["start"], "%Y-%m-%d %H:%M").replace(second=0, microsecond=0)
            event_end = datetime.strptime(event["end"], "%Y-%m-%d %H:%M").replace(second=0, microsecond=0)
            if event_start <= current_time <= event_end:
                for key, value in event.items():
                    if key not in ["has_quests", "has_spawnpoints", "start", "end", "name", "type"] and value:
                        if key == "bonuses":
                            for bonus in value:
                                event_bonuses.append(f"• {bonus['text']}")
                        else:
                            event_bonuses.append(f"• {key.capitalize()}: {value}")
                        # check if no event has been added to the embeds_content list
                embeds_content.append({"content": content, "name": event["name"].upper(), "body": "\n".join(event_bonuses), "footer": f"Entre {event_start} e {event_end}"})
                    
        self.poliswag.db.execute_query_to_database(f"UPDATE event SET notification_date = CASE WHEN notification_date IS NULL THEN start ELSE end END WHERE name IN({names_stored_events});")

        return embeds_content
