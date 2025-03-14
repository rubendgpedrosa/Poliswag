from datetime import datetime


class EventManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.events = None

    async def fetch_events(self):
        response = await self.poliswag.utility.fetch_data("events")
        if response is None:
            return

        self.store_events_in_database()

    def store_events_in_database(self):
        if self.events is None:
            return

        for event in self.events:
            try:
                start = event["start"]
                end = event["end"]
                name = event["name"]
            except KeyError:
                continue

            if "unannounced" in name.lower():
                continue

            # Clean up date formatting
            start = start.replace("Z", "").split(".")[0]
            end = end.replace("Z", "").split(".")[0]

            query = f"""
                INSERT IGNORE INTO event(name, start, end)
                VALUES ('{name.replace("'", "''")}', '{start}', '{end}')
            """
            self.poliswag.db.execute_query_to_database(query)

    def get_active_events(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = f"""
            SELECT name, start, end, notification_date
            FROM event
            WHERE (notification_date IS NULL AND start < '{current_time}')
               OR (notification_date < end AND end < '{current_time}');
        """
        stored_events = self.poliswag.db.get_data_from_database(query)

        if len(stored_events) == 0:
            return None

        embeds_content = []
        for event in self.events:
            try:
                event_start = datetime.fromisoformat(event["start"].rstrip("Z"))
                event_end = datetime.fromisoformat(event["end"].rstrip("Z"))
            except (KeyError, ValueError):
                continue

            if event_start <= datetime.now() <= event_end:
                event_details = self._parse_event_details(event)
                if event_details:
                    embeds_content.append(
                        {
                            "content": (
                                "**ATUAIS EVENTOS ATIVOS:**"
                                if not embeds_content
                                else None
                            ),
                            "name": event["name"].upper(),
                            "body": "\n".join(event_details["bonuses"]),
                            "footer": f"Entre {event_start.strftime('%Y-%m-%d %H:%M')} e {event_end.strftime('%Y-%m-%d %H:%M')}",
                            "image": event.get("image", ""),
                        }
                    )

        return embeds_content

    def _parse_event_details(self, event):
        details = {"bonuses": []}
        extra = event.get("extraData", {})

        event_type = event.get("eventType", "").lower()

        if "spotlight" in event_type:
            if spotlight := extra.get("spotlight"):
                details["bonuses"].append(f"• Pokémon: {spotlight.get('name', '')}")
                if bonus := spotlight.get("bonus"):
                    details["bonuses"].append(f"• Bônus: {bonus}")
                if shiny := spotlight.get("canBeShiny"):
                    details["bonuses"].append("• Pokémon Shiny disponível")

        elif "raid" in event_type:
            if raids := extra.get("raidbattles"):
                for boss in raids.get("bosses", []):
                    shiny_status = (
                        " (Shiny disponível)" if boss.get("canBeShiny") else ""
                    )
                    details["bonuses"].append(
                        f"• Chefe de Raide: {boss['name']}{shiny_status}"
                    )

        elif generic := extra.get("generic"):
            if generic.get("hasFieldResearchTasks"):
                details["bonuses"].append("• Missões de Pesquisa Especiais")
            if generic.get("hasSpawns"):
                details["bonuses"].append("• Spawns Especiais")

        if "gbl" in event_type or "battle-league" in event_type:
            if leagues := [event.get("heading", "")]:
                details["bonuses"].append(f"• Ligas: {', '.join(leagues)}")

        if not details["bonuses"]:
            details["bonuses"].append("• Verifique o link para detalhes completos")

        details["bonuses"].append(f"• Mais informações: {event.get('link', '')}")

        return details
