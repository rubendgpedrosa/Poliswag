import discord
from datetime import datetime, time
from pathlib import Path
import aiohttp
import logging
from modules.config import Config


class Utility:
    def __init__(self, poliswag):
        self.poliswag = poliswag

        # Setup logging directories
        log_dir = Path(Config.LOG_FILE).parent
        error_log_dir = Path(Config.ERROR_LOG_FILE).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        error_log_dir.mkdir(parents=True, exist_ok=True)

        # Define log files
        self.LOG_FILE = Path(Config.LOG_FILE)
        self.ERROR_LOG_FILE = Path(Config.ERROR_LOG_FILE)

        # Create log files if they don't exist
        self.LOG_FILE.touch(exist_ok=True)
        self.ERROR_LOG_FILE.touch(exist_ok=True)

        # Configure logging
        self.logger = logging.getLogger("poliswag")
        self.logger.setLevel(logging.INFO)

        if self.logger.handlers:
            self.logger.handlers.clear()

        info_file_handler = logging.FileHandler(self.LOG_FILE)
        info_file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.logger.addHandler(info_file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.logger.addHandler(console_handler)

        self.error_logger = logging.getLogger("poliswag.error")
        self.error_logger.setLevel(logging.ERROR)

        if self.error_logger.handlers:
            self.error_logger.handlers.clear()

        error_file_handler = logging.FileHandler(self.ERROR_LOG_FILE)
        error_file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.error_logger.addHandler(error_file_handler)

    async def get_new_pokemongo_version(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    Config.NIANTIC_FORCED_VERSION_ENDPOINT
                ) as response:
                    if response.status == 200:
                        retrieved_version = (
                            (await response.text()).strip().replace("\x07", "")
                        )

                        result = self.poliswag.db.get_data_from_database(
                            "SELECT version FROM poliswag"
                        )
                        current_version = result[0]["version"] if result else None

                        if retrieved_version != current_version:
                            self.poliswag.db.execute_query_to_database(
                                "UPDATE poliswag SET version = %s",
                                params=(retrieved_version,),
                            )
                            return retrieved_version
                        return None
                    return None
        except Exception as e:
            self.log_to_file(f"Error fetching Pokemon version: {e}", "ERROR")
            return None

    def log_to_file(self, message, log_type="INFO"):
        if log_type == "ERROR" or log_type == "CRASH":
            self.error_logger.error(message)
        else:
            self.logger.info(message)

    def build_embed_object_title_description(self, title, description="", footer=None):
        embed = discord.Embed(
            title=title,
            description=description,
            color=Config.EMBED_COLOR,
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

                lines = deque(file, maxlen=numLines)
                return "".join(lines)
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

    async def find_quest_scanning_message(self, channel):
        if channel is None:
            return None
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
            return None
        except Exception as e:
            self.log_to_file(f"Error finding quest scanning message: {e}", "ERROR")
            return None

    def format_datetime_string(self, dt_string):
        return dt_string.replace("Z", "").split(".")[0].replace("T", " ")
