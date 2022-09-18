import requests, json, random
from datetime import datetime

import discord

import helpers.constants as constants

versionUrl = "https://pgorelease.nianticlabs.com/plfe/version"

def prepare_environment(env):
    if env == "prod":
        return "/root/poliswag/.env"
    elif env == "dev":
        return "dev.env"
    else:
        print("Invalid environment, usage: python3 main.py (dev|prod)")
        quit()

async def check_current_version():    
    response = requests.get(versionUrl)

    if (response.status_code == 200):
        retrievedVersion = response.text.strip().split(".",1)[1]
        if (constants.SAVED_VERSION != retrievedVersion):
            constants.SAVED_VERSION = retrievedVersion
            with open(constants.VERSION_FILE, 'w') as file:
                file.write(retrievedVersion)
            await notify_new_version()

async def notify_new_version():
    channel = constants.CLIENT.get_channel(constants.CONVIVIO_CHANNEL_ID)
    await channel.send(embed=build_embed_object_title_description(
        "PAAAAUUUUUUUU!!! FORCE UPDATE!",
        "Nova versão: 0." + constants.SAVED_VERSION
    ))

def log_to_file(string, logType = "INFO"):
    now = datetime.now()
    with open(constants.LOG_FILE, 'a') as file:
        file.write(logType + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))

def build_embed_object_title_description(title, description = "", footer = None):
    embed = discord.Embed(title=title, description=description, color=0x7b83b4)
    if footer != None:
        embed.set_footer(text=footer)
    return embed

def run_database_query(query, database = None):
    execId = constants.DOCKER_CLIENT.exec_create(constants.DB_CONTAINER, build_query(query, database))
    return constants.DOCKER_CLIENT.exec_start(execId)

def build_query(query, db = None):
    if db is None:
        db = constants.DB_NAME
    return f'mysql -u{constants.DB_USER} -p{constants.DB_PASSWORD} -D {db} -e "{query}"'

def log_actions(message):
    f = open(constants.LOG_FILE, "a")
    f.write("{0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), message))
    f.close()

def validate_message_for_deletion(message, channel, author = None):
    # Checks if any of these strings are in the command list and in the channels affected
    if channel in [constants.MOD_CHANNEL_ID, constants.QUEST_CHANNEL_ID, constants.CONVIVIO_CHANNEL_ID]:
        if message.lower().startswith(("!rules", "!location", "!add", "!remove", "!reload", "!quest", "!scan", "!comandos", "!questleiria", "!questmarinha", "<@" + str(constants.POLISWAG_ID) + ">")):
            return True
        # In case it's a random message for quest channel. We only accept the admins one
        if channel == constants.QUEST_CHANNEL_ID and author != constants.CLIENT.user and str(author.id) not in constants.ADMIN_USERS_IDS:
            return True
        return False
    return False

def did_day_change():
    dayChange = datetime.now().day > constants.CURRENT_DAY
    if dayChange:
        constants.CURRENT_DAY = datetime.now().day
    return dayChange

def to_bool(s):
    return 1 if s == 'True' else 0

async def add_button_event(button, callback):
    button.callback = callback
