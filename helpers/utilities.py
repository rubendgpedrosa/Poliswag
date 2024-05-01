import requests
from datetime import datetime, time
from helpers.database_connector import get_data_from_database, execute_query_to_database

import discord

import helpers.constants as constants

versionUrl = "https://pgorelease.nianticlabs.com/plfe/version"

boxUsersData = [
    {"owner": "Faynn", "boxes": ["Tx9s1", "Poco" "a95xF1"], "mention": "98846248865398784"},
    {"owner": "JMBoy", "boxes": ["Tx9s1_JMBoy", "Tx9s2_JMBoy", "Tx9s3_JMBoy"], "mention": "308000681271492610"},
    {"owner": "Ethix", "boxes": ["Tx9s1_Ethix"], "mention": "313738342904627200"},
    {"owner": "Anakin", "boxes": ["Tx9s1_Anakin"], "mention": "339466204638871552"}
]

def prepare_environment(env):
    if env == "prod":
        return "/root/poliswag/.env"
    elif env == "dev":
        return "dev.env"
    else:
        print("Invalid environment, usage: python3 main.py (dev|prod)")
        quit()

async def check_current_pokemongo_version():    
    response = requests.get(versionUrl)
    if (response.status_code == 200):
        retrievedVersion = response.text.strip()
        storedVersion = get_data_from_database(f"SELECT version FROM poliswag WHERE version = '{retrievedVersion}'", "poliswag")
        if len(storedVersion) == 0:
            execute_query_to_database(f"UPDATE poliswag SET version = '{retrievedVersion}'", "poliswag")
            await notify_new_version(retrievedVersion)

async def notify_new_version(retrievedVersion):
    channel = constants.CLIENT.get_channel(constants.CONVIVIO_CHANNEL_ID)
    log_to_file(f"Updated to new version {retrievedVersion}")
    await channel.send(embed=build_embed_object_title_description(
        "PAAAAAAAAAUUUUUUUUUU!!! FORCE UPDATE!",
        f"Nova versão: {retrievedVersion}"
    ))

def log_to_file(string, logType = "INFO"):
    with open(constants.LOG_FILE, 'r') as fileToRead:
        last_line = fileToRead.readlines()[-1]
        if string in last_line:
            return
        
    if logType == "ERROR":
        with open(constants.LOG_FILE, 'a') as file:
            file.write(logType + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))
    elif logType == "CRASH":
        with open(constants.ERROR_LOG_FILE, 'a') as file:
            file.write(logType + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))
    else:
        with open(constants.LOG_FILE, 'a') as file:
            file.write(logType + " | {0} -- {1}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M"), string))

def build_embed_object_title_description(title, description = "", footer = None):
    embed = discord.Embed(title=title, description=description, color=0x7b83b4)
    if footer != None:
        embed.set_footer(text=footer)
    return embed

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
        if message.lower().startswith((constants.POLISWAG_ROLE_ID, "!rules", "!location", "!questleiria", "!questmarinha")):
            return True
        # In case it's a random message for quest channel. We only accept the admins one
        if channel == constants.QUEST_CHANNEL_ID and author != constants.CLIENT.user and str(author.id) not in constants.ADMIN_USERS_IDS:
            return True
        return False
    return False

def to_bool(s):
    return 1 if s == 'True' else 0

async def add_button_event(button, callback):
    button.callback = callback

async def private_message_user_by_id(userId, message):
    user = await constants.CLIENT.fetch_user(userId)
    await user.send(embed=message)

def clear_quest_file():
    with open(constants.QUESTS_FILE, 'w') as filetowrite:
        filetowrite.write("{}")
    log_to_file('Cleared previous quest data')

def read_last_lines_from_log():
    with open(constants.LOG_FILE, 'r') as fileToRead:
        logs = ""
        lines = fileToRead.readlines()
        last_lines = lines[-10:]
        for line in last_lines:
            logs = logs + line.rstrip() + "\n"
    return logs

def time_now():
    date = datetime.now().date()  # get the current date
    timeHour = time(hour=0, minute=0, second=0)  # create a time object with 00:00:00
    dt = datetime.combine(date, timeHour)  # combine the date and time objects
    return str(dt)

async def clean_map_stats_channel(message):
    channel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
    async for msg in channel.history(limit=200):
        if message is not None and msg.author is not None and str(msg.author.id) not in constants.ADMIN_USERS_IDS and msg.author.id != constants.POLISWAG_ID:
            await msg.delete()
        elif message == "clear" and "DISPOSITIVOS ATIVOS" not in msg.content:
            await msg.delete()

def get_dict_embed_from_message(message):
    return message.embeds[0].to_dict()

async def is_there_message_to_be_deleted(mentionString):
    channel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
    async for msg in channel.history(limit=200):
        if msg.author.id == constants.POLISWAG_ID and mentionString != msg.content and "DISPOSITIVOS ATIVOS" not in msg.content:
            return True
    return False

async def is_message_spam_message(message):
    if message.author.id == constants.POLISWAG_ID or message.author.id in constants.ADMIN_USERS_IDS:
        return False
    if message.channel.id == constants.MOD_CHANNEL_ID:
        return False
    if message.channel.id == constants.QUEST_CHANNEL_ID:
        return False

    cached_messages = constants.CACHED_MESSAGES_BY_USER.get(message.author.id, [])
    cached_messages.append(message.content)
    
    if len(cached_messages) > 3:
        cached_messages = cached_messages[-3:]

    constants.CACHED_MESSAGES_BY_USER[message.author.id] = cached_messages
    
    if len(set(cached_messages)) == 1:
        log_to_file("User {0} is spamming: {1}".format(message.author.id, message.content))
        return True
    
    return False

async def remove_all_cached_messages_by_user(message):
    if message.author.id in constants.CACHED_MESSAGES_BY_USER:
        del constants.CACHED_MESSAGES_BY_USER[message.author.id]

    time_threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)

    async for message in message.author.history(limit=None, after=time_threshold):
        await message.delete()

async def message_me(embed):
    user_id = 98846248865398784
    user = await constants.CLIENT.fetch_user(user_id)
    if user:
        try:
            await user.send(
                content="TEST",
                embed=embed
            )
        except discord.Forbidden:
            print(f"I don't have permission to send a direct message to {user.name}")
    else:
        print("User not found")
