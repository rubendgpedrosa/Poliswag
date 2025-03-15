import discord
import os
from datetime import datetime, time
from pathlib import Path
import aiohttp
import logging
import json


class Utility:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.LOG_FILE = Path(os.environ.get("LOG_FILE"))
        self.ERROR_LOG_FILE = Path(os.environ.get("ERROR_LOG_FILE"))
        self.POKEMON_VERSION_ENDPOINT = os.environ.get(
            "NIANTIC_FORCED_VERSION_ENDPOINT"
        )
        self.MOCK_DATA_DIR = "mock_data"
        self.DEV = os.environ.get("ENV") != "PRODUCTION"

        self.ENDPOINTS = {
            "scanner_status": os.environ.get("SCANNER_STATUS_ENDPOINT"),
            "device_status": os.environ.get("DEVICE_STATUS_ENDPOINT"),
            "account_status": os.environ.get("SCANNER_ACCOUNTS_STATUS_ENDPOINT"),
            "leiria_quest_scanning": os.environ.get("LEIRIA_QUEST_SCANNING_ENDPOINT"),
            "marinha_quest_scanning": os.environ.get("MARINHA_QUEST_SCANNING_ENDPOINT"),
            "all_down": os.environ.get("ALL_DOWN_ENDPOINT"),
            "events": os.environ.get("EVENTS_ENDPOINT"),
            "scan_quest_all": os.environ.get("SCAN_QUESTS_ALL_ENDPOINT"),
        }
        self.UI_ICONS_URL = os.environ.get("UI_ICONS_URL")
        self.embed_color = 0x4169E1

        self.LOG_FILE.touch(exist_ok=True)
        self.ERROR_LOG_FILE.touch(exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.LOG_FILE),
                logging.StreamHandler(),  # This will also add it to the log output
            ],
        )

        self.error_logger = logging.getLogger("error_logger")
        self.error_logger.setLevel(logging.ERROR)
        file_handler = logging.FileHandler(self.ERROR_LOG_FILE)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.error_logger.addHandler(file_handler)

    async def get_new_pokemongo_version(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.POKEMON_VERSION_ENDPOINT) as response:
                    if response.status == 200:
                        retrievedVersion = (
                            (await response.text()).strip().replace("\x07", "")
                        )

                        result = self.poliswag.db.get_data_from_database(
                            "SELECT version FROM poliswag"
                        )

                        if result and len(result) > 0:
                            current_version = result[0]["version"]
                        else:
                            current_version = None

                        if retrievedVersion != current_version:
                            self.poliswag.db.execute_query_to_database(
                                f"UPDATE poliswag SET version = '{retrievedVersion}'"
                            )
                            return retrievedVersion
                        return None
                    return None
        except Exception as e:
            self.log_to_file(f"Error fetching Pokemon version: {e}", "ERROR")
            return None

    def log_to_file(self, message, log_type="INFO"):
        if log_type == "ERROR" or log_type == "CRASH":
            self.error_logger.error(message)
        else:
            logging.info(message)

    def build_embed_object_title_description(self, title, description="", footer=None):
        embed = discord.Embed(
            title=title,
            description=description,
            color=self.embed_color,
            timestamp=datetime.now(),
        )
        if footer:
            embed.set_footer(text=footer)
        return embed

    async def add_button_event(self, button, callback):
        try:
            button.callback = callback
        except Exception as e:
            self.log_to_file(f"Failed to add button callback: {e}", "ERROR")

    def read_last_lines_from_log(self, numLines=10):
        try:
            with open(self.LOG_FILE, "r") as file:
                from collections import deque

                return "".join(deque(file, numLines))
        except Exception as e:
            self.log_to_file(f"Error reading log file: {e}", "ERROR")
            return "Error reading logs"

    def time_now(self):
        return datetime.combine(datetime.now().date(), time.min).isoformat()

    async def send_message_to_channel(self, channel, message):
        try:
            await channel.send(message)
        except discord.errors.Forbidden:
            self.log_to_file(
                f"No permission to send message in {channel.name}", "ERROR"
            )
        except Exception as e:
            self.log_to_file(f"Failed to send message: {e}", "ERROR")

    async def send_embed_to_channel(self, channel, embed):
        try:
            await channel.send(embed=embed)
        except discord.errors.Forbidden:
            self.log_to_file(f"No permission to send embed in {channel.name}", "ERROR")
        except Exception as e:
            self.log_to_file(f"Failed to send embed: {e}", "ERROR")

    async def fetch_data(self, endpoint_key, timeout=20, method="GET", data=None):
        if self.DEV and endpoint_key not in ["all_down", "events"]:
            mock_file_map = {
                "scanner_status": "scanner_status.json",
                "device_status": "device_status.json",
                "account_status": "account_status.json",
                "leiria_quest_scanning": "leiria_quest_scanning.json",
                "marinha_quest_scanning": "marinha_quest_scanning.json",
                "all_down": "",
            }

            try:
                file_path = os.path.join(
                    self.MOCK_DATA_DIR, mock_file_map.get(endpoint_key, "default.json")
                )
                with open(file_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                self.log_to_file(
                    f"[DEV] Error loading mock data for {endpoint_key}: {e}", "ERROR"
                )
                return None

        async with aiohttp.ClientSession() as session:
            try:
                endpoint_ENDPOINT = self.ENDPOINTS.get(endpoint_key)
                if not endpoint_ENDPOINT:
                    self.log_to_file(
                        f"No URL defined for endpoint: {endpoint_key}", "ERROR"
                    )
                    return None

                async with session.request(
                    method, endpoint_ENDPOINT, json=data, timeout=timeout
                ) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "")

                    if "application/json" in content_type:
                        return await response.json()
                    else:
                        text_response = await response.text()

                        if text_response.strip():
                            try:
                                return json.loads(text_response)
                            except json.JSONDecodeError:
                                if endpoint_key == "events" and text_response:
                                    return text_response

                                self.log_to_file(
                                    f"Error decoding JSON from {endpoint_key}. Content-Type: {content_type}",
                                    "ERROR",
                                )
                        else:
                            self.log_to_file(
                                f"Empty response from {endpoint_key}", "ERROR"
                            )

                        return None
            except aiohttp.ClientResponseError as e:
                self.log_to_file(
                    f"HTTP Error fetching data from {endpoint_key}: {e.status} - {e.message}",
                    "ERROR",
                )
                return None
            except Exception as e:
                self.log_to_file(
                    f"Error fetching data from {endpoint_key}: {e}", "ERROR"
                )
                return None

    async def build_tracked_summary_embeds(self, channel, reward_groups, footer_text):
        for reward_slug, group_data in reward_groups.items():
            title = group_data["title"]
            reward_text = group_data.get("reward_text", "")
            count = len(group_data["pokestops"])
            image_url = f"{self.UI_ICONS_URL}{reward_slug}"

            embed = discord.Embed(color=self.embed_color)
            embed.description = f"**{count} quest de {title}**"
            embed.set_footer(text=footer_text)
            embed.set_author(name=f"{reward_text}", icon_url=image_url)
            await channel.send(embed=embed)

    async def build_tracked_list_embed(self, title="Quests Seguidas", footer_text=None):
        """Helper method to build a consistent embed for tracked quests"""
        tracked_quests = self.poliswag.db.get_data_from_database(
            "SELECT target, creator, createddate FROM tracked_quest_reward ORDER BY createddate DESC"
        )

        if len(tracked_quests) == 0:
            embed = discord.Embed(
                title=title,
                description="Não há quests/rewards a serem seguidas atualmente.",
                color=self.embed_color,
            )
        else:
            embed = discord.Embed(title=title, color=self.embed_color)

            for quest_data in tracked_quests:
                quest_name = quest_data["target"]
                creator = quest_data["creator"]
                createddate = quest_data.get("createddate", "Data desconhecida")

                if isinstance(createddate, datetime):
                    createddate_str = createddate.strftime("%d/%m/%Y %H:%M")
                else:
                    createddate_str = str(createddate)

                embed.add_field(
                    name=quest_name,
                    value=f"Adicionado por: {creator}\n{createddate_str}",
                    inline=False,
                )

        if footer_text:
            embed.set_footer(text=footer_text)

        return embed

    async def find_quest_scanning_message(self, channel):
        try:
            today = datetime.now().date()
            async for message in channel.history(limit=100):
                if (
                    message.author == self.poliswag.user
                    and message.embeds
                    and "SCAN DE QUESTS" in message.embeds[0].title.upper()
                    and message.created_at.date() == today
                ):
                    return message
            return None  # No matching message found today
        except Exception as e:
            self.log_to_file(f"Error finding quest scanning message: {e}", "ERROR")
            return None
