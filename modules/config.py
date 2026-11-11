import os

from dotenv import dotenv_values, load_dotenv

# python-dotenv >= 1.2 skips load_dotenv() while PYTHON_DOTENV_DISABLED is set
# (the test suite sets it to block reading a real .env); older versions, like
# the host's 1.0.1, ignore the flag. Honour it here so both behave the same.
if not os.environ.get("PYTHON_DOTENV_DISABLED"):
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
    # Small JSON state that has to survive a restart (modules/state_store.py
    # and the *_STATE / *_FILE paths below). Git-ignored and bind-mounted like
    # logs/, where these files lived until 2026-11-11.
    STATE_DIR = os.environ.get("STATE_DIR", "state")
    SHINY_LEADERBOARD_ENABLED = os.environ.get("SHINY_LEADERBOARD_ENABLED", "0") == "1"
    SHUNDO_LEADERBOARD_ENABLED = (
        os.environ.get("SHUNDO_LEADERBOARD_ENABLED", "0") == "1"
    )
    # Discord
    DISCORD_API_KEY = os.environ.get("DISCORD_API_KEY")
    ADMIN_USERS_IDS = os.environ.get("ADMIN_USERS_IDS", "").split(",")
    # PoliSnatch's game-status endpoint, polled for a new game build
    # (cogs/scheduled.py). The submit key it authenticates with is read per
    # call by modules/polisnatch_api.py (POLISNATCH_JOBS_KEY or its file), so
    # rotating it needs no restart, and it is never logged. The production
    # container mounts /etc/poliswag/secrets read-only (docker-compose.prod.yaml).
    POLISNATCH_STATUS_URL = os.environ.get(
        "POLISNATCH_STATUS_URL", "https://polisnatch.pogoleiria.pt/v1/game/status"
    )
    POLISNATCH_UPDATE_STATE_FILE = os.environ.get(
        "POLISNATCH_UPDATE_STATE_FILE", f"{STATE_DIR}/polisnatch-update-state.json"
    )
    MY_ID = int(os.environ.get("MY_ID", "0"))
    # Apoio por MB Way (!mbway). Free text so the owner can change the
    # wording, not just the number, without touching code; empty means the
    # command points at the Revolut link instead. Never committed.
    MBWAY_DETAILS = os.environ.get("MBWAY_DETAILS", "")
    # Who gets shiny-prediction DMs (modules/shiny_alerts.py). The owner alone
    # while the prediction is being checked in the field; widening the pilot
    # is this list.
    SHINY_ALERT_USER_IDS = [
        int(part)
        for part in os.environ.get("SHINY_ALERT_USER_IDS", "").split(",")
        if part.strip()
    ] or [MY_ID]
    # Shiny spawn alerts and link-result DMs (modules/shiny_alerts.py). Off
    # stops delivery only: private channels, !shiny and the odds/availability
    # ticks keep running. The watermark keeps advancing, so switching it back
    # on does not replay the spawns of the days it was off.
    SHINY_ALERTS_ENABLED = os.environ.get("SHINY_ALERTS_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    # Event summaries also require each recipient to opt in. The existing
    # pilot recipient list remains the delivery boundary.
    SHINY_EVENT_DIGESTS_ENABLED = os.environ.get(
        "SHINY_EVENT_DIGESTS_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

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
    # The combined map status channel. The env var name kept the first area's
    # name from before the areas were merged into one channel.
    VOICE_CHANNEL_ID = int(os.environ.get("VOICE_CHANNEL_LEIRIA_ID", "0"))

    # Database
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")
    DB_POLISWAG = os.environ.get("DB_POLISWAG")
    DB_SCANNER_NAME = os.environ.get("DB_SCANNER_NAME")
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

    # Scanner / infrastructure
    ADB_DEVICE = os.environ.get("ADB_DEVICE", "")  # e.g. "192.168.1.222:5555"
    UNOWNHASH_COMPOSE_FILE = os.environ.get(
        "UNOWNHASH_COMPOSE_FILE", "/root/unonwhash/docker-compose.yml"
    )
    RECREATE_SERVICES = os.environ.get("RECREATE_SERVICES", "dragonite rotom-ng")
    ENV = os.environ.get("ENV", "DEV")
    IS_PRODUCTION = ENV == "PRODUCTION"

    # Written after every pass of each scheduler loop; scripts/watchdog.sh on
    # the host restarts the container when it goes stale (bind-mounted).
    HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE", "logs/heartbeat")
    AGENT_JOBS_DIR = os.environ.get("AGENT_JOBS_DIR", "logs/agent-jobs")

    # Hermes stores the owner conversation in CT 109; empty key disables calls.
    HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://192.168.1.60:8642")
    HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "")
    HERMES_SESSION_ID = os.environ.get("HERMES_SESSION_ID", "")
    HERMES_RUNS_DIR = os.environ.get("HERMES_RUNS_DIR", "logs/hermes-runs")
    # Hermiswag: the separate public agent on this host (modules/hermiswag_client.py).
    # Unset = public tags use the agent worker only.
    HERMISWAG_API_URL = os.environ.get("HERMISWAG_API_URL", "")
    HERMISWAG_API_KEY = os.environ.get("HERMISWAG_API_KEY", "")
    # The community conversation channels (convívio, chat-raids,
    # suporte-tecnico): learning there keeps only members' messages.
    HERMISWAG_SESSION_CHANNELS = [
        int(x)
        for x in os.environ.get(
            "HERMISWAG_SESSION_CHANNELS",
            "329321552241754115,867882807648321567,331442170802339842",
        ).split(",")
        if x.strip().isdigit()
    ]
    # Channels Hermiswag learns from: the Informação category plus the three
    # conversation channels. Never moderation or the alert channels.
    HERMISWAG_LEARN_CHANNELS = [
        int(x)
        for x in os.environ.get(
            "HERMISWAG_LEARN_CHANNELS",
            "331439180288622603,378108892816867329,684179755259527253,"
            "643508455159627776,573239192750915594,530056668894199839,"
            "329321552241754115,867882807648321567,331442170802339842",
        ).split(",")
        if x.strip().isdigit()
    ]
    # Where Poliswag answers questions nobody answered for 10 min (convívio,
    # suporte-tecnico), and the ids it already looked at.
    HERMISWAG_PROACTIVE_CHANNELS = [
        int(x)
        for x in os.environ.get(
            "HERMISWAG_PROACTIVE_CHANNELS", "329321552241754115,331442170802339842"
        ).split(",")
        if x.strip().isdigit()
    ]
    HERMISWAG_PROACTIVE_STATE = os.environ.get(
        "HERMISWAG_PROACTIVE_STATE", f"{STATE_DIR}/hermiswag-proactive.json"
    )
    # The Pokémon GO build the scanner phone last scanned with (DeviceManager).
    SCANNER_GAME_VERSION_FILE = os.environ.get(
        "SCANNER_GAME_VERSION_FILE", f"{STATE_DIR}/scanner-game-version.json"
    )
    # The live scanner card in the mod channel and when the incident began.
    SCANNER_CARD_FILE = os.environ.get(
        "SCANNER_CARD_FILE", f"{STATE_DIR}/scanner-card.json"
    )
    HERMISWAG_LEARNING_STATE = os.environ.get(
        "HERMISWAG_LEARNING_STATE", f"{STATE_DIR}/hermiswag-learning.json"
    )
    # claude-bridge in CT 109 (owner DMs prefixed `c `); empty key disables calls.
    CLAUDE_API_URL = os.environ.get("CLAUDE_API_URL", "http://192.168.1.240:8643")
    CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
    # !sorteio campfire: leitura das capturas do Campfire (Messages API).
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
    CAMPFIRE_MODEL = os.environ.get("CAMPFIRE_MODEL", "claude-sonnet-5-5")
    CLAUDE_RUNS_DIR = os.environ.get("CLAUDE_RUNS_DIR", "logs/claude-runs")
    # Who gets the owner's unprefixed DMs: "hermes" or "claude". Switched by
    # !usehermes / !useclaude, which also write it to .env for the next boot.
    ENV_FILE = os.environ.get("POLISWAG_ENV_FILE", ".env")
    # Read from the file itself: docker compose's env_file is frozen into the
    # container at creation, so a plain restart would keep the old value.
    OWNER_DM_DEFAULT = (
        (
            (
                None
                if os.environ.get("PYTHON_DOTENV_DISABLED")
                else dotenv_values(ENV_FILE).get("OWNER_DM_DEFAULT")
            )
            or os.environ.get("OWNER_DM_DEFAULT", "hermes")
        )
        .strip()
        .lower()
    )
    # Names kept out of anything public. They are deployment data, so the
    # repository never carries them; unset means nothing is filtered.
    PRIVATE_NAMES = tuple(
        part.strip()
        for part in os.environ.get("POLISWAG_PRIVATE_NAMES", "").split(",")
        if part.strip()
    )

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
    QUEST_MAP_URL = os.environ.get("QUEST_MAP_URL", "https://tileserver.pogoleiria.pt")
    QUEST_MAP_STYLE = os.environ.get("QUEST_MAP_STYLE", "osm-bright")
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
        # Levelling pool (dragonite-level / rotom-ng-level). Optional: unset
        # hides the levelling section of the account card.
        "level_account_status": os.environ.get(
            "SCANNER_LEVEL_ACCOUNTS_STATUS_ENDPOINT"
        ),
        "level_device_status": os.environ.get("LEVEL_DEVICE_STATUS_ENDPOINT"),
        "all_down": os.environ.get("ALL_DOWN_ENDPOINT"),
        # Home Assistant webhook: owner alerts become phone notifications.
        "owner_alert": os.environ.get("OWNER_ALERT_ENDPOINT"),
        "events": os.environ.get("EVENTS_ENDPOINT"),
        "scan_quest_all": os.environ.get("SCAN_QUESTS_ALL_ENDPOINT"),
    }

    # Poracle API (for the notifications cog)
    PORACLE_API_URL = os.environ.get("PORACLE_API_URL", "http://poracle:3030")
    PORACLE_API_SECRET = os.environ.get("PORACLE_API_SECRET", "")

    # Dragonite's HTTP API, read by modules/scan_areas.py for the scan-area
    # fences. Areas themselves come from the scan_area table, never from code.
    DRAGONITE_API_URL = os.environ.get("DRAGONITE_API_URL", "http://192.168.1.253:7272")
    # Last good fence copy on disk, so a Dragonite restart never empties the
    # areas. Under the bot's data directory (git-ignored, bind-mounted).
    SCAN_AREA_CACHE = os.environ.get("SCAN_AREA_CACHE", "data/scan_areas.json")

    # UI
    EMBED_COLOR = 0x4169E1

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
