import requests
import discord
import os
from datetime import datetime, time
from pathlib import Path
import aiohttp


class Utility:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.LOG_FILE = Path(os.environ.get("LOG_FILE"))
        self.ERROR_LOG_FILE = Path(os.environ.get("ERROR_LOG_FILE"))
        self.POKEMON_VERSION_URL = os.environ.get("NIANTIC_FORCED_VERSION_URL")

        # Ensure log files exist
        self.LOG_FILE.touch(exist_ok=True)
        self.ERROR_LOG_FILE.touch(exist_ok=True)

    async def get_new_pokemongo_version(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.POKEMON_VERSION_URL) as response:
                    if response.status == 200:
                        retrievedVersion = (
                            (await response.text()).strip().replace("\x07", "")
                        )

                        result = self.poliswag.db.get_data_from_database(
                            "SELECT version FROM poliswag"
                        )

                        storedVersion = (
                            result[0] if isinstance(result, tuple) else result
                        )

                        if isinstance(storedVersion, dict):
                            current_version = storedVersion.get("version")
                        else:
                            current_version = storedVersion

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
        try:
            logFile = (
                self.ERROR_LOG_FILE
                if (log_type == "CRASH" or log_type == "ERROR")
                else self.LOG_FILE
            )
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            logEntry = f"{log_type} | {timestamp} -- {message}\n"

            try:
                with open(logFile, "r") as f:
                    lastLine = f.readlines()[-1] if f.readable() else ""
                    if message in lastLine:
                        return
            except (IndexError, FileNotFoundError):
                pass

            with open(logFile, "a") as f:
                f.write(logEntry)

        except Exception as e:
            print(f"Logging failed: {e}")

    def build_embed_object_title_description(self, title, description="", footer=None):
        embed = discord.Embed(
            title=title,
            description=description,
            color=0x7B83B4,
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
