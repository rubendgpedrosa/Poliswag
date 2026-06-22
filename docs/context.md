# Poliswag — LLM context reference

## What it is
Discord bot (`discord.py`) for the **PoGoLeiria** Pokémon GO scanner community (Leiria / Marinha Grande, Portugal). Wraps scanner infrastructure (Dragonite + Rotom) and exposes quest search, event notifications, account monitoring, and Poracle alert management via `!` prefix commands.

## Stack
- Python 3.11 · `discord.py` · `pymysql` (MariaDB) · `aiohttp` · `jinja2` + `imgkit`
- Docker Compose (dev + prod) · `pytest` · `black` + `ruff` (pre-commit) · `vulture`

## Entry point
`main.py` — `Poliswag(commands.Bot)` instantiates all service modules and loads all cogs in `setup_hook`. Bot channels (`QUEST_CHANNEL`, `CONVIVIO_CHANNEL`, `MOD_CHANNEL`, `ACCOUNTS_CHANNEL`) are resolved in `on_ready`.

## Module map (`modules/`)

| Module | Responsibility |
|--------|---------------|
| `config.py` | Single `Config` class reading all env vars via `dotenv`. Source of truth for all settings. |
| `database_connector.py` | `DatabaseConnector(database?)` — pymysql wrapper with retry (3 attempts), auto-reconnect on errno 2006/2013, returns `list[dict]` from `cursor.description`. Default DB = `Config.DB_POLISWAG`; scanner DB passed explicitly. |
| `http_client.py` | `fetch_data(endpoint_key, …)` — single shared `aiohttp.ClientSession`, reads from `Config.ENDPOINTS[key]`. In DEV (`not IS_PRODUCTION`) returns mock JSON from `mock_data/` for infra endpoints. |
| `scanner_status.py` | `ScannerStatus` — polls Dragonite (`/status`) + Rotom (`/api/status`) + Golbat DB. Renames Discord voice channels, fires HA webhook when scanner is fully down (15 min cooldown). Tracks per-area worker expectations (`LeiriaBigger`=4, `MarinhaGrande`=1). |
| `scanner_manager.py` | Docker control (via `docker-py`) + `poliswag` table state (`last_scanned_date`, `scanned` flag). `is_day_change()` triggers a new scan cycle. |
| `quest_search.py` | `QuestSearch` — owns scanner DB connection. Loads pokemon/item name maps + masterfile. `find_quest_by_search_keyword(term, is_leiria)` queries `pokestop` table. Area split: Marinha Grande = lon ≤ −8.9. Handles AR/standard quest field duality via `_quest_fields()`. |
| `quest_exporter.py` | `QuestExporter.export()` — reads `pokestop` and writes a JSON file to `QUEST_JSON_OUTPUT` (default `/pogo-public/quests.json`) for the PWA. |
| `event_manager.py` | Fetches events from ScrapedDuck (15 min cache). Stores/updates `event` table. Dispatches embed notifications to `CONVIVIO_CHANNEL` when events start/end, respecting `excluded_event_type`. Weekly digest on Mondays. |
| `event_store.py` | Thin DB wrapper for `excluded_event_type` and `event` tables. |
| `account_monitor.py` | Polls Dragonite `/accounts/stats`. Aggregates disabled statuses. Posts account image to `ACCOUNTS_CHANNEL`. |
| `poracle_client.py` | `PoracleClient` — async REST client for Poracle-NG (`X-Poracle-Secret` header). Methods: `get_channels`, `get_pokemon`, `add_pokemon`, `delete_pokemon`, `set_enabled`, `register_channel`, `send_test`. Raises `PoracleError` on non-2xx. |
| `tracker_store.py` | CRUD for `tracked_quest_reward` table. |
| `lure_manager.py` | `LureManager` — owns a read-only `DatabaseConnector(DB_DRAGONITE)`. `list_available_with_lures()` reads available+healthy accounts from `dragonite.account` (not banned/suspended/warned/invalid/auth_banned, off cooldown, `last_released >= last_selected`), seeds new usernames into `account_lure` at 12, returns up to 5 fewest-first with `{username, password, nb_lures}`. `adjust_lure_count(username, delta)` → `GREATEST(nb_lures+delta, 0)` UPDATE. Writes only `poliswag.account_lure`; dragonite is read-only. |
| `role_manager.py` | Handles Discord role button interactions (team + notification roles). |
| `image_generator.py` | `imgkit` + Jinja2 → PNG bytes. Two templates: `followed_events.html` (quest map) and `accounts.html`. |
| `embeds.py` | Shared embed builders (`build_embed`, `build_tracked_list_embed`, `build_excluded_list_embed`). Discord limits: 25 fields, 256 field name, 1024 field value, 4096 description. |
| `utility.py` | `log_to_file(msg, level)` — dual-logger (`poliswag` info + `poliswag.error`). `time_now()`. |
| `locale_pt.py` | Portuguese month/day short-name dicts (`PT_MONTHS_SHORT`, `PT_DAYS_SHORT`, `MONTH_NAMES`). |

## Cog map (`cogs/`)

| Cog | Commands | Auth |
|-----|----------|------|
| `quests.py` | `!questleiria [area] [search]` / `!questmarinha`, `!scan`, `!exportquests` | `!scan`+`!exportquests` admin-only |
| `tracker.py` | `!track`, `!untrack`, `!tracklist`, `!untrackall`, `!tracked` | admin-only (`cog_check`) |
| `event.py` | `!eventtypes`, `!exclude`, `!include`, `!excludedlist`, `!excludeclear` | admin-only |
| `notifications.py` | `!notify channels\|list\|add\|remove\|enable\|disable\|register\|test` | admin-only |
| `accounts.py` | `!accounts` | open |
| `container_manager.py` | `!container start\|stop`, `!status` | `MY_ID` only |
| `moderation.py` | Listeners: `on_interaction` (role buttons), `on_message_delete` | — |
| `scheduled.py` | `!weeklydigest`, `!testevent HH:MM`; `@tasks.loop` every minute | admin-only |
| `lures.py` | `!lures`, `!uselure USERNAME NUMERO` | admin-only (`cog_check`) |

## Database schema (Poliswag DB)

**`poliswag`** — single-row config table:
- `last_scanned_date DATE` — last quest scan date
- `scanned TINYINT` — 1=finished, 0=scanning
- `last_weekly_digest_date DATE` — guards digest deduplication

**`tracked_quest_reward`** — `target VARCHAR`, `creator VARCHAR`, `createddate DATETIME`

**`excluded_event_type`** — `type VARCHAR`

**`event`** — event calendar rows (type, name, start/end timestamps, etc.)

**`account_lure`** — `username VARCHAR(50)` PK, `nb_lures INT DEFAULT 12`. Per-account lure budget for `!lures`/`!uselure` (managed by `lure_manager.py`). Migration `004`.

**Scanner DB** (`DB_SCANNER_NAME`) — read-only. Key table:

`pokestop` — lat/lon, quest fields (`quest_type`, `quest_title`, `quest_target`, `quest_reward_type`, `quest_item_id`, `quest_pokemon_id`, `quest_reward_amount`) plus `alternative_quest_*` mirrors for AR quests (generated columns). Area split by `lon ≤ −8.9` for Marinha Grande.

**Poracle DB** (`DatabaseConnector("poracle")`) — `humans` table: `id`, `name`, `type='discord:channel'`, `enabled`.

**Dragonite DB** (`DatabaseConnector(DB_DRAGONITE)`, default `dragonite`) — read-only. `account` table: `username`, `password`, health flags (`banned`, `suspended`, `warn`, `invalid`, `auth_banned`), and scheduler timestamps (`last_selected`, `last_released`, `next_available_time`). Read by `lure_manager.py` to find free+healthy accounts.

## External integrations

| Service | Endpoint env var | Purpose |
|---------|-----------------|---------|
| Dragonite admin | `SCANNER_STATUS_ENDPOINT` | Worker health + account stats |
| Rotom | `DEVICE_STATUS_ENDPOINT` | Device liveness |
| Home Assistant | `ALL_DOWN_ENDPOINT` | Webhook when scanner fully down |
| Poracle-NG | `PORACLE_API_URL` + `PORACLE_API_SECRET` | Pokémon alert rule CRUD |
| ScrapedDuck | `EVENTS_ENDPOINT` | Event calendar JSON |
| PokeMiners masterfile | `MASTERFILE_ENDPOINT` | Pokémon/item name translations |
| Niantic version | `NIANTIC_FORCED_VERSION_ENDPOINT` | Version check |

## Config env vars (key ones)

```
DISCORD_API_KEY, ADMIN_USERS_IDS (comma-sep), MY_ID
QUEST_CHANNEL_ID, CONVIVIO_CHANNEL_ID, MOD_CHANNEL_ID, ACCOUNTS_CHANNEL_ID
VOICE_CHANNEL_LEIRIA_ID, VOICE_CHANNEL_MARINHA_ID
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_POLISWAG, DB_SCANNER_NAME, DB_DRAGONITE
SCANNER_CONTAINER_NAME
ENV=DEVELOPMENT|PRODUCTION  (IS_PRODUCTION = ENV=="PRODUCTION")
PORACLE_API_URL, PORACLE_API_SECRET
POKEMON_NAME_FILE, ITEM_NAME_FILE  (JSON name maps in data/)
MASTERFILE_ENDPOINT, TRANSLATIONFILE_ENDPOINT
UI_ICONS_URL, TEMPLATE_HTML_DIR, FOLLOWED_EVENTS_TEMPLATE_HTML_FILE, ACCOUNTS_TEMPLATE_HTML_FILE
QUEST_JSON_OUTPUT  (default /pogo-public/quests.json)
LOG_FILE, ERROR_LOG_FILE
```

## Dev vs prod

- `ENV=DEVELOPMENT` → `http_client.fetch_data` returns mock JSON from `mock_data/*.json` for infra endpoints; real HTTP only for `all_down` + `events`.
- `mock_database/init.sql` seeds a full scanner DB for local Docker Compose.
- `make up` starts dev stack; `docker-compose.prod.yaml` for production.

## Conventions

- All modules receive `poliswag` (the bot instance) and access services via `self.poliswag.<module>`.
- Admin guard: `str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS` or `cog_check`.
- `!notify` **ref** resolution: `#mention` → raw id → exact name → `%-<suffix>` LIKE; category suffixes (`raros`, `100iv`, `0iv`, `uteis`) fan out to `leiria-<suffix>` and `marinha-<suffix>`.
- `_PAIRED_PREFIXES = ("leiria-", "marinha-")` drives fan-out logic in `notifications.py`.
- Embed color: `Config.EMBED_COLOR = 0x4169E1`.
- Tests under `tests/` (pytest). Run via `make test` inside Docker.
- Logging: `utility.log_to_file(msg, level)` → `logs/actions.log` + `logs/error.log`.
