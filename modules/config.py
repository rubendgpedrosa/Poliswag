import os

from dotenv import load_dotenv

load_dotenv()


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
    ADB_DEVICE = os.environ.get("ADB_DEVICE", "")  # e.g. "192.168.1.222:5555"
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

    # Image generation
    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
    TEMPLATE_HTML_DIR = os.environ.get("TEMPLATE_HTML_DIR")
    FOLLOWED_EVENTS_TEMPLATE_HTML_FILE = os.environ.get(
        "FOLLOWED_EVENTS_TEMPLATE_HTML_FILE"
    )
    ACCOUNTS_TEMPLATE_HTML_FILE = os.environ.get("ACCOUNTS_TEMPLATE_HTML_FILE")

    # Voice channels keyed by scan area name
    VOICE_CHANNELS: dict[str, int] = {}

    # PWA export
    QUEST_JSON_OUTPUT = os.environ.get("QUEST_JSON_OUTPUT", "/pogo-public/quests.json")
    MEGA_JSON_OUTPUT = os.environ.get("MEGA_JSON_OUTPUT", "/dex-public/megas.json")
    MEGA_SPRITES_DIR = os.environ.get("MEGA_SPRITES_DIR", "/dex-public/sprites/mega")

    # API endpoints
    ENDPOINTS = {
        "scanner_status": os.environ.get("SCANNER_STATUS_ENDPOINT"),
        "device_status": os.environ.get("DEVICE_STATUS_ENDPOINT"),
        "account_status": os.environ.get("SCANNER_ACCOUNTS_STATUS_ENDPOINT"),
        "all_down": os.environ.get("ALL_DOWN_ENDPOINT"),
        "events": os.environ.get("EVENTS_ENDPOINT"),
        "scan_quest_all": os.environ.get("SCAN_QUESTS_ALL_ENDPOINT"),
    }

    # Poracle API (for the notifications cog)
    PORACLE_API_URL = os.environ.get("PORACLE_API_URL", "http://poracle:3030")
    PORACLE_API_SECRET = os.environ.get("PORACLE_API_SECRET", "")

    # UI
    EMBED_COLOR = 0x4169E1
    MOCK_DATA_DIR = "mock_data"


Config.VOICE_CHANNELS = {
    "leiria": Config.VOICE_CHANNEL_LEIRIA_ID,
    "marinha": Config.VOICE_CHANNEL_MARINHA_ID,
}
