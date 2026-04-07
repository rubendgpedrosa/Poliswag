import os


class Config:
    # Discord
    DISCORD_API_KEY = os.environ.get("DISCORD_API_KEY")
    ADMIN_USERS_IDS = os.environ.get("ADMIN_USERS_IDS", "").split(",")
    MY_ID = int(os.environ.get("MY_ID", "0"))

    # Channels
    QUEST_CHANNEL_ID = int(os.environ.get("QUEST_CHANNEL_ID", "0"))
    CONVIVIO_CHANNEL_ID = int(os.environ.get("CONVIVIO_CHANNEL_ID", "0"))
    MOD_CHANNEL_ID = int(os.environ.get("MOD_CHANNEL_ID", "0"))
    ACCOUNTS_CHANNEL_ID = int(os.environ.get("ACCOUNTS_CHANNEL_ID", "0"))
    VOICE_CHANNEL_LEIRIA_ID = int(os.environ.get("VOICE_CHANNEL_LEIRIA_ID", "0"))
    VOICE_CHANNEL_MARINHA_ID = int(os.environ.get("VOICE_CHANNEL_MARINHA_ID", "0"))

    # Database
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")
    DB_POLISWAG = os.environ.get("DB_POLISWAG")
    DB_SCANNER_NAME = os.environ.get("DB_SCANNER_NAME")

    # Scanner / infrastructure
    SCANNER_CONTAINER_NAME = os.environ.get("SCANNER_CONTAINER_NAME")
    ENV = os.environ.get("ENV", "DEV")
    IS_PRODUCTION = ENV == "PRODUCTION"

    # Logging
    LOG_FILE = os.environ.get("LOG_FILE", "logs/app.log")
    ERROR_LOG_FILE = os.environ.get("ERROR_LOG_FILE", "logs/error.log")

    # External data files
    TRANSLATIONFILE_ENDPOINT = os.environ.get("TRANSLATIONFILE_ENDPOINT")
    MASTERFILE_ENDPOINT = os.environ.get("MASTERFILE_ENDPOINT")
    POKEMON_NAME_FILE = os.environ.get("POKEMON_NAME_FILE")
    ITEM_NAME_FILE = os.environ.get("ITEM_NAME_FILE")
    UI_ICONS_URL = os.environ.get("UI_ICONS_URL")
    NIANTIC_FORCED_VERSION_ENDPOINT = os.environ.get("NIANTIC_FORCED_VERSION_ENDPOINT")

    # PWA export
    QUEST_JSON_OUTPUT = os.environ.get("QUEST_JSON_OUTPUT", "/pogo-public/quests.json")

    # API endpoints
    ENDPOINTS = {
        "scanner_status": os.environ.get("SCANNER_STATUS_ENDPOINT"),
        "device_status": os.environ.get("DEVICE_STATUS_ENDPOINT"),
        "account_status": os.environ.get("SCANNER_ACCOUNTS_STATUS_ENDPOINT"),
        "leiria_quest_scanning": os.environ.get("LEIRIA_QUEST_SCANNING_ENDPOINT"),
        "marinha_quest_scanning": os.environ.get("MARINHA_QUEST_SCANNING_ENDPOINT"),
        "all_down": os.environ.get("ALL_DOWN_ENDPOINT"),
        "events": os.environ.get("EVENTS_ENDPOINT"),
        "scan_quest_all": os.environ.get("SCAN_QUESTS_ALL_ENDPOINT"),
    }

    # UI
    EMBED_COLOR = 0x4169E1
    MOCK_DATA_DIR = "mock_data"
