import os

import discord, docker

def init():
    global DISCORD_API_KEY, OPENAI_API_KEY, ENABLE_POLISWAGGPT
    DISCORD_API_KEY = os.getenv('DISCORD_API_KEY')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    ENABLE_POLISWAGGPT = False
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

    global BACKEND_ENDPOINT, WEBSITE_URL
    BACKEND_ENDPOINT = os.getenv('BACKEND_ENDPOINT')
    WEBSITE_URL = os.getenv('WEBSITE_URL')

    global CONVIVIO_CHANNEL_ID, EVENT_CHANNEL_ID, MAPSTATS_CHANNEL_ID, MOD_CHANNEL_ID, QUEST_CHANNEL_ID, VOICE_CHANNEL_ID, VOICE_CHANNEL_MARINHA_ID
    CONVIVIO_CHANNEL_ID = int(os.getenv('CONVIVIO_CHANNEL_ID'))
    EVENT_CHANNEL_ID = int(os.getenv('EVENT_CHANNEL_ID'))
    MAPSTATS_CHANNEL_ID = int(os.getenv('MAPSTATS_CHANNEL_ID'))
    MOD_CHANNEL_ID = int(os.getenv('MOD_CHANNEL_ID'))
    QUEST_CHANNEL_ID = int(os.getenv('QUEST_CHANNEL_ID'))
    VOICE_CHANNEL_ID = int(os.getenv('VOICE_CHANNEL_ID'))
    VOICE_CHANNEL_MARINHA_ID = int(os.getenv('VOICE_CHANNEL_MARINHA_ID'))

    global DOCKER_CLIENT_API, DOCKER_CLIENT
    DOCKER_CLIENT_API = docker.from_env().api
    DOCKER_CLIENT = docker.from_env()
    
    global RUN_CONTAINER, DB_CONTAINER, REDIS_CONTAINER, ALARM_CONTAINER
    RUN_CONTAINER = os.getenv('RUN_CONTAINER')
    DB_CONTAINER = os.getenv('DB_CONTAINER')
    REDIS_CONTAINER = os.getenv('REDIS_CONTAINER')
    ALARM_CONTAINER = os.getenv('ALARM_CONTAINER')

    global EVENT_FILE, FILTER_FILE, LOG_FILE, QUESTS_FILE, POKEMON_LIST_FILE, ERROR_LOG_FILE, PLAYINTEGRITY_UPDATER_FILE
    EVENT_FILE = os.getenv('EVENT_FILE')
    FILTER_FILE = os.getenv('FILTER_FILE')
    LOG_FILE = os.getenv('LOG_FILE')
    ERROR_LOG_FILE = os.getenv('ERROR_LOG_FILE')
    QUESTS_FILE = os.getenv('QUESTS_FILE')
    POKEMON_LIST_FILE = os.getenv('POKEMON_LIST_FILE')
    PLAYINTEGRITY_UPDATER_FILE = os.getenv('PLAYINTEGRITY_UPDATER_FILE')

    global ADMIN_USERS_IDS, POLISWAG_ID, MY_ID, POLISWAG_ROLE_ID
    ADMIN_USERS_IDS = list(os.getenv('ADMIN_USERS_IDS').split(','))
    MY_ID = int(os.getenv('MY_ID'))
    POLISWAG_ID = int(os.getenv('POLISWAG_ID'))
    POLISWAG_ROLE_ID = os.getenv('POLISWAG_ROLE_ID')

    global CLIENT
    intents = discord.Intents.all()
    intents.message_content = True
    CLIENT = discord.Client(intents=intents)

    global DB_IP, DB_NAME, DB_USER, DB_PASSWORD
    DB_IP = os.getenv('DB_IP')
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')

    global TIME_MULTIPLIER, TOTAL_BOXES
    TIME_MULTIPLIER = 1
    TOTAL_BOXES = 7
