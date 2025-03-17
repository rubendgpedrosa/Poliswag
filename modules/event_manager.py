from datetime import datetime, timedelta
import json


class EventManager:
    def __init__(self, poliswag, use_ai=True):
        self.poliswag = poliswag
        self.events = None
        self.use_ai = use_ai

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

        stored_count = 0
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

                start = start.replace("Z", "").split(".")[0].replace("T", " ")
                end = end.replace("Z", "").split(".")[0].replace("T", " ")

                check_query = f"""
                    SELECT name, extra_data FROM event
                    WHERE name = '{name.replace("'", "''")}' AND start = '{start}'
                """
                existing_event = self.poliswag.db.get_data_from_database(check_query)

                parsed_content = None

                if not existing_event and self.use_ai:
                    event_data = {
                        "id": event_id,
                        "name": name,
                        "eventType": event_type,
                        "start": start,
                        "end": end,
                        "extraData": event.get("extraData", {}),
                    }

                    # Use PoliWiz to generate structured data (only if AI is enabled)
                    parsed_content = await self.poliswag.poliwiz.process_event(
                        event_data
                    )

                    if parsed_content:
                        event["parsed_content"] = parsed_content

                # Insert or update event in database
                query = f"""
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
                self.poliswag.db.execute_query_to_database(query)
                stored_count += 1
            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error storing event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

        self.poliswag.utility.log_to_file(
            f"Stored/updated {stored_count} events", "INFO"
        )

    async def get_active_events(self):
        """Get newly active events (that haven't been notified yet)"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        query = f"""
            SELECT name, start, end, notification_date, image, event_type, link, extra_data
            FROM event
            WHERE start <= '{current_time}' AND end >= '{current_time}'
            AND notification_date IS NULL
            ORDER BY end ASC;
        """

        active_events = self.poliswag.db.get_data_from_database(query)

        if len(active_events) == 0:
            return None

        embeds_content = []
        for event in active_events:
            try:
                event_start = datetime.strptime(
                    str(event["start"]), "%Y-%m-%d %H:%M:%S"
                )
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")

                extra_data = {}
                if "extra_data" in event and event["extra_data"]:
                    try:
                        extra_data = json.loads(event["extra_data"])
                    except:
                        pass

                parsed_content = None
                if "parsed_content" in extra_data:
                    parsed_content = extra_data["parsed_content"]
                elif self.use_ai:
                    event_data = {
                        "id": event.get("id", ""),
                        "name": event["name"],
                        "eventType": event["event_type"],
                        "start": str(event["start"]),
                        "end": str(event["end"]),
                        "extraData": extra_data.get("extraData", {}),
                    }

                    parsed_content = await self.poliswag.poliwiz.process_event(
                        event_data
                    )

                    if parsed_content:
                        extra_data["parsed_content"] = parsed_content
                        update_query = f"""
                            UPDATE event
                            SET extra_data = '{json.dumps(extra_data).replace("'", "''")}'
                            WHERE name = '{event["name"].replace("'", "''")}' AND start = '{event_start.strftime("%Y-%m-%d %H:%M:%S")}'
                        """
                        self.poliswag.db.execute_query_to_database(update_query)
                else:
                    parsed_content = {
                        "featured_pokemon": None,
                        "bonuses": None,
                        "special_features": None,
                        "time_period": f"{event_start.strftime('%d/%m/%Y %H:%M')} até {event_end.strftime('%d/%m/%Y %H:%M')}",
                    }

                event_description = []

                # Add event type as first line if no AI-generated content
                if not self.use_ai:
                    event_description.append(f"• Tipo de evento: {event['event_type']}")

                # Add featured Pokémon if available
                if (
                    parsed_content
                    and parsed_content.get("featured_pokemon")
                    and parsed_content["featured_pokemon"] != "None"
                ):
                    event_description.append(
                        f"• Pokémon: {parsed_content['featured_pokemon']}"
                    )

                # Add bonuses if available
                if (
                    parsed_content
                    and parsed_content.get("bonuses")
                    and parsed_content["bonuses"] != "None"
                ):
                    event_description.append(f"• Bónus: {parsed_content['bonuses']}")

                # Add special features if available
                if (
                    parsed_content
                    and parsed_content.get("special_features")
                    and parsed_content["special_features"] != "None"
                ):
                    event_description.append(
                        f"• Características: {parsed_content['special_features']}"
                    )

                # Add time period
                time_period = parsed_content.get(
                    "time_period",
                    f"{event_start.strftime('%d/%m/%Y %H:%M')} até {event_end.strftime('%d/%m/%Y %H:%M')}",
                )

                # Add link if available
                if event.get("link"):
                    event_description.append(f"• Mais informações: {event.get('link')}")

                # Create embed
                embeds_content.append(
                    {
                        "content": (
                            "**NOVOS EVENTOS ATIVOS:**" if not embeds_content else None
                        ),
                        "name": event["name"].upper(),
                        "body": (
                            "\n".join(event_description)
                            if event_description
                            else "Novo evento activo."
                        ),
                        "footer": f"Ativo entre {time_period}",
                        "image": event.get("image", ""),
                    }
                )

                # Mark as notified
                update_query = f"""
                    UPDATE event
                    SET notification_date = '{current_time}'
                    WHERE name = '{event["name"].replace("'", "''")}' AND start = '{event_start.strftime("%Y-%m-%d %H:%M:%S")}'
                """
                self.poliswag.db.execute_query_to_database(update_query)

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing active event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

        return embeds_content

    async def get_ending_events(self, hours=24):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        end_threshold = (datetime.now() + timedelta(hours=hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        query = f"""
            SELECT name, start, end, notification_date, image, event_type, link, extra_data
            FROM event
            WHERE end > '{current_time}' AND end <= '{end_threshold}'
            AND notification_end_date IS NULL
            ORDER BY end ASC;
        """

        ending_events = self.poliswag.db.get_data_from_database(query)

        if len(ending_events) == 0:
            return None

        embeds_content = []
        for event in ending_events:
            try:
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")

                embeds_content.append(
                    {
                        "content": (
                            "**EVENTOS A TERMINAR EM BREVE:**"
                            if not embeds_content
                            else None
                        ),
                        "name": event["name"].upper(),
                        "body": f"Este evento termina em {self.get_time_remaining(event_end)}",
                        "footer": f"Termina em {event_end.strftime('%d/%m/%Y %H:%M')}",
                        "image": event.get("image", ""),
                    }
                )

                update_query = f"""
                    UPDATE event
                    SET notification_end_date = '{current_time}'
                    WHERE name = '{event["name"].replace("'", "''")}' AND start = '{event["start"]}'
                """
                self.poliswag.db.execute_query_to_database(update_query)

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing ending event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

        return embeds_content

    async def get_all_current_active_events(self):
        """Get all currently active events (including previously notified ones)"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        query = f"""
            SELECT name, start, end, notification_date, image, event_type, link, extra_data
            FROM event
            WHERE start <= '{current_time}' AND end >= '{current_time}'
            ORDER BY end ASC;
        """

        active_events = self.poliswag.db.get_data_from_database(query)

        if len(active_events) == 0:
            return None

        embeds_content = [
            {
                "content": "**TODOS OS EVENTOS ATIVOS ATUALMENTE:**",
                "name": "RESUMO DE EVENTOS",
                "body": f"Existem atualmente {len(active_events)} eventos ativos.",
                "footer": f"Atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                "image": "",
            }
        ]

        for event in active_events:
            try:
                event_start = datetime.strptime(
                    str(event["start"]), "%Y-%m-%d %H:%M:%S"
                )
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")

                # Process extra data
                extra_data = {}
                if "extra_data" in event and event["extra_data"]:
                    try:
                        extra_data = json.loads(event["extra_data"])
                    except:
                        pass

                parsed_content = extra_data.get("parsed_content", {})
                if not parsed_content:
                    parsed_content = {
                        "time_period": f"{event_start.strftime('%d/%m/%Y %H:%M')} até {event_end.strftime('%d/%m/%Y %H:%M')}"
                    }

                event_description = []

                if event.get("event_type"):
                    event_description.append(f"• Tipo de evento: {event['event_type']}")

                event_description.append(
                    f"• Termina em: {self.get_time_remaining(event_end)}"
                )

                if event.get("link"):
                    event_description.append(f"• Mais informações: {event.get('link')}")

                embeds_content.append(
                    {
                        "content": None,
                        "name": event["name"].upper(),
                        "body": (
                            "\n".join(event_description)
                            if event_description
                            else "Evento activo."
                        ),
                        "footer": f"Ativo até {event_end.strftime('%d/%m/%Y %H:%M')}",
                        "image": event.get("image", ""),
                    }
                )

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing current active event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

        return embeds_content

    async def check_ended_events(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        query = f"""
            SELECT name, start, end, image, event_type, link
            FROM event
            WHERE end > '{one_hour_ago}' AND end <= '{current_time}'
            ORDER BY end DESC;
        """

        ended_events = self.poliswag.db.get_data_from_database(query)

        if len(ended_events) == 0:
            return None

        active_query = f"""
            SELECT COUNT(*) as active_count
            FROM event
            WHERE start <= '{current_time}' AND end > '{current_time}';
        """

        active_result = self.poliswag.db.get_data_from_database(active_query)
        active_count = active_result[0]["active_count"] if active_result else 0

        embeds_content = [
            {
                "content": "**EVENTOS TERMINADOS:**",
                "name": "EVENTOS QUE ACABARAM DE TERMINAR",
                "body": f"{len(ended_events)} evento(s) terminaram recentemente. Ainda existem {active_count} evento(s) ativos.",
                "footer": f"Atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                "image": "",
            }
        ]

        for event in ended_events:
            try:
                event_end = datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")

                embeds_content.append(
                    {
                        "content": None,
                        "name": f"{event['name'].upper()} - TERMINADO",
                        "body": f"Este evento terminou. Tipo: {event.get('event_type', 'N/A')}",
                        "footer": f"Terminou em {event_end.strftime('%d/%m/%Y %H:%M')}",
                        "image": event.get("image", ""),
                    }
                )

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error processing ended event {event.get('name', 'unknown')}: {str(e)}",
                    "ERROR",
                )

        if active_count > 0:
            embeds_content.append(
                {
                    "content": None,
                    "name": "EVENTOS AINDA ATIVOS",
                    "body": f"Existem ainda {active_count} evento(s) em progresso! Use o comando para ver eventos ativos para mais detalhes.",
                    "footer": "Bons jogos!",
                    "image": "",
                }
            )

        return embeds_content

    def get_time_remaining(self, end_time):
        delta = end_time - datetime.now()
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        if days > 0:
            return f"{days} dias, {hours} horas"
        elif hours > 0:
            return f"{hours} horas, {minutes} minutos"
        else:
            return f"{minutes} minutos"
