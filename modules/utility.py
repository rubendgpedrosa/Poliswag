import requests, discord, os
from datetime import datetime, time

class Utility:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.LOG_FILE = os.environ.get("LOG_FILE")
        self.ERROR_LOG_FILE = os.environ.get("ERROR_LOG_FILE")
        self.POKEMON_VERSION_URL = os.environ.get("NIANTIC_FORCED_VERSION_URL")

    async def get_new_pokemongo_version(self):    
        response = requests.get(self.POKEMON_VERSION_URL)
        if response.status_code == 200:
            retrieved_version = response.text.strip()
            stored_version = self.poliswag.db.get_data_from_database(f"SELECT version FROM poliswag")
            if retrieved_version != stored_version['version']:
                self.poliswag.db.execute_query_to_database(f"UPDATE poliswag SET version = '{retrieved_version}'")
                return retrieved_version
        return None

    def log_to_file(self, string, log_type="INFO"):
        with open(self.LOG_FILE, 'r') as file_to_read:
            lines = file_to_read.readlines()
            if lines and string in lines[-1]:
                return
            
        if log_type == "ERROR":
            with open(self.LOG_FILE, 'a') as file:
                file.write(log_type + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))
        elif log_type == "CRASH":
            with open(self.ERROR_LOG_FILE, 'a') as file:
                file.write(log_type + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))
        else:
            with open(self.LOG_FILE, 'a') as file:
                file.write(log_type + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))

    def build_embed_object_title_description(self, title, description="", footer=None):
        embed = discord.Embed(title=title, description=description, color=0x7b83b4)
        if footer != None:
            embed.set_footer(text=footer)
        return embed

    async def add_button_event(self, button, callback):
        button.callback = callback

    def read_last_lines_from_log(self):
        with open(self.LOG_FILE, 'r') as file_to_read:
            logs = ""
            lines = file_to_read.readlines()
            last_lines = lines[-10:]
            for line in last_lines:
                logs = logs + line.rstrip() + "\n"
        return logs

    def time_now(self):
        date = datetime.now().date()
        time_hour = time(hour=0, minute=0, second=0)  # create a time object with 00:00:00
        dt = datetime.combine(date, time_hour)
        return str(dt)
