import os

from dotenv import load_dotenv

load_dotenv()

# Env vars the bot cannot run without. Everything else (channel ids, feature
# endpoints, image-gen settings, ...) degrades gracefully -- see e.g.
# Poliswag.get_channels(), which already logs and skips an unset channel
# rather than crashing.
_REQUIRED_ENV_VARS = (
    "DISCORD_API_KEY",
    "DB_HOST",
    "DB_USER",
    "DB_PASSWORD",
    "DB_POLISWAG",
    "DB_SCANNER_NAME",
)


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
    TRADES_CHANNEL_ID = int(os.environ.get("TRADES_CHANNEL_ID", "0"))
    # Honeypot channel: real members have no reason to post here, so anyone
    # who does is almost certainly a spam bot indiscriminately posting into
    # every channel it can see. See cogs/moderation.py.
    TRAP_CHANNEL_ID = int(os.environ.get("TRAP_CHANNEL_ID", "0"))
    # Announcements channel holding the !eventpanel opt-in message, and the
    # permanent Eventos role it grants. The event's own channel is not
    # configured -- it is written straight into the panel copy in
    # cogs/event_panel.py, which is edited per event anyway.
    EVENT_PANEL_CHANNEL_ID = int(os.environ.get("EVENT_PANEL_CHANNEL_ID", "0"))
    EVENTS_ROLE_ID = int(os.environ.get("EVENTS_ROLE_ID", "0"))
    # Combined Leiria+Marinha map status channel — reuses the old Leiria-only
    # env var name since Leiria's Discord channel is the one that survived
    # the merge (Marinha's separate channel was deleted).
    VOICE_CHANNEL_ID = int(os.environ.get("VOICE_CHANNEL_LEIRIA_ID", "0"))

    # Database
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")
    DB_POLISWAG = os.environ.get("DB_POLISWAG")
    DB_SCANNER_NAME = os.environ.get("DB_SCANNER_NAME")
    DB_DRAGONITE = os.environ.get("DB_DRAGONITE", "dragonite")
    # The landing app's analytics schema, read by modules/page_view_stats.py.
    # Deliberately not in _REQUIRED_ENV_VARS: the default is correct and an
    # unreachable analytics schema must not stop the bot booting.
    DB_POGOLEIRIA = os.environ.get("DB_POGOLEIRIA", "pogoleiria")
    # The Pokédex/Comunidade tool's public address (it moved from /trades,
    # which still redirects, on 2026-09-22).
    POKEDEX_URL = os.environ.get("POKEDEX_URL", "https://pogoleiria.pt/pokedex")
    # The private statistics report. The token issued by !stats is appended to
    # this, and the landing app renders the report live at that address.
    WEBSTATS_URL = os.environ.get("WEBSTATS_URL", "https://pogoleiria.pt/webstats")
    # Where modules/site_health.py reaches the site's apps (pm2 on the host,
    # ports 1080/3001/3003). The bot runs in docker, so not localhost.
    SITE_HEALTH_HOST = os.environ.get("SITE_HEALTH_HOST", "192.168.1.253")

    # Scanner / infrastructure
    SCANNER_CONTAINER_NAME = os.environ.get("SCANNER_CONTAINER_NAME")
    ADB_DEVICE = os.environ.get("ADB_DEVICE", "")  # e.g. "192.168.1.222:5555"
    UNOWNHASH_COMPOSE_FILE = os.environ.get(
        "UNOWNHASH_COMPOSE_FILE", "/root/unonwhash/docker-compose.yml"
    )
    RECREATE_SERVICES = os.environ.get("RECREATE_SERVICES", "dragonite rotom-ng")
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
    ACCOUNTS_TEMPLATE_HTML_FILE = os.environ.get("ACCOUNTS_TEMPLATE_HTML_FILE")

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

    # Pokestops at or west of this longitude are Marinha Grande, the rest
    # Leiria. Quest search, the quest export and the scan-progress counts
    # all split on it, so moving it here keeps them in agreement.
    MARINHA_LON_MAX = -8.9
    MOCK_DATA_DIR = "mock_data"

    @classmethod
    def validate(cls):
        """Raise if any env var the bot cannot run without is unset.

        Fails once at startup with every missing var listed, instead of a
        separate cryptic exception from whichever component (discord.py,
        pymysql, ...) happens to hit the missing value first.
        """
        missing = [name for name in _REQUIRED_ENV_VARS if not getattr(cls, name)]
        if missing:
            raise RuntimeError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )
