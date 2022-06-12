import os

import discord

def init():
    global DISCORD_API_KEY
    DISCORD_API_KEY = os.getenv('DISCORD_API_KEY')

    global BACKEND_ENDPOINT, WEBSITE_URL
    BACKEND_ENDPOINT = os.getenv('BACKEND_ENDPOINT')
    WEBSITE_URL = os.getenv('WEBSITE_URL')

    global CONVIVIO_CHANNEL_ID, EVENT_CHANNEL_ID, MAPSTATS_CHANNEL_ID, MOD_CHANNEL_ID, QUEST_CHANNEL_ID
    CONVIVIO_CHANNEL_ID = int(os.getenv('CONVIVIO_CHANNEL_ID'))
    EVENT_CHANNEL_ID = int(os.getenv('EVENT_CHANNEL_ID'))
    MAPSTATS_CHANNEL_ID = int(os.getenv('MAPSTATS_CHANNEL_ID'))
    MOD_CHANNEL_ID = int(os.getenv('MOD_CHANNEL_ID'))
    QUEST_CHANNEL_ID = int(os.getenv('QUEST_CHANNEL_ID'))

    global CLEAR_QUESTS_FILE, FILTER_FILE, LOG_FILE, QUESTS_FILE, SCANNED_FILE, VERSION_FILE
    CLEAR_QUESTS_FILE = os.getenv('CLEAR_QUESTS_FILE')
    FILTER_FILE = os.getenv('FILTER_FILE')
    LOG_FILE = os.getenv('LOG_FILE')
    QUESTS_FILE = os.getenv('QUESTS_FILE')
    SCANNED_FILE = os.getenv('SCANNED_FILE')
    VERSION_FILE = os.getenv('VERSION_FILE')

    global ADMIN_USERS_IDS
    ADMIN_USERS_IDS = list(os.getenv('ADMIN_USERS_IDS').split(","))

    global CLIENT
    CLIENT = discord.Client()