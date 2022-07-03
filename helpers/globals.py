import os, datetime

import discord, docker

def init():
    global DISCORD_API_KEY
    DISCORD_API_KEY = os.getenv('DISCORD_API_KEY')

    global BACKEND_ENDPOINT, WEBSITE_URL
    BACKEND_ENDPOINT = os.getenv('BACKEND_ENDPOINT')
    WEBSITE_URL = os.getenv('WEBSITE_URL')

    global CONVIVIO_CHANNEL_ID, EVENT_CHANNEL_ID, MAPSTATS_CHANNEL_ID, MOD_CHANNEL_ID, QUEST_CHANNEL_ID, VOICE_CHANNEL_ID
    CONVIVIO_CHANNEL_ID = int(os.getenv('CONVIVIO_CHANNEL_ID'))
    EVENT_CHANNEL_ID = int(os.getenv('EVENT_CHANNEL_ID'))
    MAPSTATS_CHANNEL_ID = int(os.getenv('MAPSTATS_CHANNEL_ID'))
    MOD_CHANNEL_ID = int(os.getenv('MOD_CHANNEL_ID'))
    QUEST_CHANNEL_ID = int(os.getenv('QUEST_CHANNEL_ID'))
    VOICE_CHANNEL_ID = int(os.getenv('VOICE_CHANNEL_ID'))

    global RUN_CONTAINER, DB_CONTAINER, REDIS_CONTAINER, ALARM_CONTAINER
    RUN_CONTAINER = os.getenv('RUN_CONTAINER')
    DB_CONTAINER = os.getenv('DB_CONTAINER')
    REDIS_CONTAINER = os.getenv('REDIS_CONTAINER')
    ALARM_CONTAINER = os.getenv('ALARM_CONTAINER')

    global FILTER_FILE, LOG_FILE, QUESTS_FILE, VERSION_FILE, POKEMON_LIST_FILE
    FILTER_FILE = os.getenv('FILTER_FILE')
    LOG_FILE = os.getenv('LOG_FILE')
    QUESTS_FILE = os.getenv('QUESTS_FILE')
    POKEMON_LIST_FILE = os.getenv('POKEMON_LIST_FILE')
    VERSION_FILE = os.getenv('VERSION_FILE')

    global ADMIN_USERS_IDS, POLISWAG_ID
    ADMIN_USERS_IDS = list(os.getenv('ADMIN_USERS_IDS').split(","))
    POLISWAG_ID = int(os.getenv('POLISWAG_ID'))

    global CLIENT
    intents = discord.Intents.all()
    intents.message_content = True
    CLIENT = discord.Client(intents=intents)

    global SAVED_VERSION
    with open(VERSION_FILE) as text:
        SAVED_VERSION = text.read(10) or 0
    
    global CURRENT_DAY, LEIRIA_QUESTS_TOTAL, MARINHA_QUESTS_TOTAL
    CURRENT_DAY = datetime.datetime.now().day
    LEIRIA_QUESTS_TOTAL = 247
    MARINHA_QUESTS_TOTAL = 107
    
    global DOCKER_CLIENT
    DOCKER_CLIENT = docker.from_env().api

    global DB_NAME, DB_USER, DB_PASSWORD
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')