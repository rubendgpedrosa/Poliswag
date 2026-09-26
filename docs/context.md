# Poliswag — LLM context reference

## What it is
Discord bot (`discord.py`) for the **PoGoLeiria** Pokémon GO scanner community (Leiria / Marinha Grande, Portugal). Wraps scanner infrastructure (Dragonite + Rotom) and exposes quest search, event notifications, account monitoring, and Poracle alert management via `!` prefix commands.

## Stack
- Python 3.11 · `discord.py` · `pymysql` (MariaDB) · `aiohttp` · `jinja2` + `imgkit`
- Docker Compose (dev + prod) · `pytest` · `black` + `ruff` (pre-commit) · `vulture`

## Entry point
`main.py` — `Poliswag(commands.Bot)` instantiates all service modules and loads all cogs in `setup_hook`. Bot channels are resolved in `on_ready` → `get_channels()`, which is the list to read; an unset id logs an ERROR and leaves the attribute `None` rather than raising.

## Module map (`modules/`) — detail in `docs/context/modules.md`

| Module | Responsibility |
|--------|---------------|
| `config.py` | `Config`: every env var, via `dotenv`. |
| `database_connector.py` | `DatabaseConnector(db?)`: pymysql, 3 retries, reconnect on 2006/2013, `list[dict]` rows, `execute_transaction`. `connect(...)` for short-lived connections. |
| `http_client.py` | `fetch_data(key)`: shared aiohttp session over `Config.ENDPOINTS`; DEV reads `mock_data/`. |
| `scanner_status.py` | Polls Dragonite/Rotom/Golbat; renames the `MAPA` voice channel (rate-budgeted); HA webhook when fully down. |
| `scanner_manager.py` | docker-py control + `poliswag` scan state; `is_day_change()`. |
| `quest_search.py` | Quest lookup on `pokestop`; Marinha = lon ≤ `MARINHA_LON_MAX` (−8.9). |
| `quest_exporter.py` | Writes `quests.json` for the PWA; skips unchanged content (md5). |
| `event_manager.py` / `event_store.py` / `event_stats.py` | ScrapedDuck ingestion, `event` table, CONVIVIO notices, end-of-event stats. See `events.md`. |
| `trade_digest.py` / `trade_dm.py` / `trade_announcer.py` | 09:00 trade digest; per-minute trade-match DMs; live match posts. |
| `hundo_alerts.py` / `hundo_confirmation.py` | Pokédex 100IV DMs via Poracle rules. See `hundo-alerts.md`. |
| `site_health.py` | Probes pogoleiria.pt + landing/quests/Pokédex ports (`SITE_HEALTH_HOST`) every tick; DMs `MY_ID` after 3 failures in a row, every 6h while down, and on recovery. Production only. |
| `account_monitor.py` | Dragonite account stats → image in `ACCOUNTS_CHANNEL`. |
| `poracle_client.py` | Async Poracle-NG REST client (`X-Poracle-Secret`); humans, pokemon tracking, `test_pokemon`, `health`, `reload`. `PoracleError` on failure. |
| `tracker_store.py` | CRUD for `tracked_quest_reward`. |
| `lure_manager.py` | Free healthy Dragonite accounts + `account_lure` budget; Dragonite read-only. |
| `role_manager.py` | Legacy name-based role panel (not used by `event_panel.py`). |
| `mega_exporter.py` | Mega sprites for the site, shrunk to ≤160px. |
| `image_generator.py` / `embeds.py` | imgkit accounts card; shared embed builders (Discord limits). |
| `utility.py` / `locale_pt.py` | `log_to_file`, `time_now`, Lisbon-aware `format_datetime_string`; PT date names. |

## Cog map (`cogs/`)

| Cog | Commands | Auth |
|-----|----------|------|
| `quests.py` | `!questleiria [area] [search]` / `!questmarinha`, `!scan`, `!exportquests` | `!scan`+`!exportquests` admin-only |
| `tracker.py` | `!track`, `!untrack`, `!tracklist`, `!untrackall`, `!tracked` | admin-only (`cog_check`) |
| `event.py` | `!eventtypes`, `!exclude`, `!include`, `!excludedlist`, `!excludeclear` | admin-only |
| `notifications.py` | `!notify channels\|list\|add\|remove\|enable\|disable\|register\|test` | admin-only |
| `accounts.py` | `!accounts` | `MY_ID` only (`cog_check`) |
| `container_manager.py` | `!container start\|stop`, `!status` | `MY_ID` only |
| `moderation.py` | Listeners: `on_interaction` (role buttons), `on_message_delete`, trap channel (see `moderation.md`) | — |
| `scheduled.py` | `!weeklydigest`, `!testevent HH:MM`; `@tasks.loop` every minute (version/quest-scan/events/workers/accounts/weekly-digest + 30-min safety-net quest export) | admin-only |
| `lures.py` | `!lures`, `!uselure USERNAME NUMERO` | admin-only (`cog_check`) |
| `event_panel.py` | `!eventpanel` (publica o painel), `!eventpanel test` (DM de ensaio), `!eventpanel clear [confirm]` | `MY_ID` only (`cog_check`) |
| `announcements.py` | `!anunciar <texto>` — reposts the text (+ attachments) as typed in the announcements channel (`EVENT_PANEL_CHANNEL_ID`). Echoes a pings-off preview first; posts only on **Publicar** (author only, 5 min, one press), with `AllowedMentions.all()` so a typed `@everyone` pings. Deletes the command message once the preview is up (kept on errors); attachments are read into memory first so Publicar still has them | admin-only (`cog_check`) |

## Databases — detail in `docs/context/database.md`

- **poliswag** (read/write): `poliswag` (scan state, digest guards), `event`, `excluded_event_type`, `tracked_quest_reward`, `account_lure`, trade/100IV state. Schema = `migrations/*.sql`.
- **golbat** scanner DB (`DB_SCANNER_NAME`, read-only): `pokestop` quests, daily `*_stats`.
- **dragonite** (read-only): `account` health for lures. **poracle**: `humans`, tracking rules.

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
VOICE_CHANNEL_LEIRIA_ID  (combined MAPA status channel; var name kept for continuity)
EVENT_PANEL_CHANNEL_ID   (announcements channel holding the !eventpanel message)
EVENTS_ROLE_ID           (permanent Eventos role the panel button grants)
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_POLISWAG, DB_SCANNER_NAME, DB_DRAGONITE
SCANNER_CONTAINER_NAME
ENV=DEVELOPMENT|PRODUCTION  (IS_PRODUCTION = ENV=="PRODUCTION")
PORACLE_API_URL, PORACLE_API_SECRET
POKEMON_NAME_FILE, ITEM_NAME_FILE  (JSON name maps in data/)
MASTERFILE_ENDPOINT, TRANSLATIONFILE_ENDPOINT
UI_ICONS_URL, TEMPLATE_HTML_DIR, ACCOUNTS_TEMPLATE_HTML_FILE
QUEST_JSON_OUTPUT  (default /pogo-public/quests.json)
LOG_FILE, ERROR_LOG_FILE
```

## Dev vs prod

- `ENV=DEVELOPMENT` → `http_client.fetch_data` returns mock JSON from `mock_data/*.json` for infra endpoints; real HTTP only for `all_down` + `events`. `scanner_status.json`/`device_status.json` are untracked and generated with current timestamps by `python mock_data/refresh.py` (the test session runs it first).
- `mock_database/init.sql` seeds a full scanner DB for local Docker Compose.
- `make up` starts dev stack; `docker-compose.prod.yaml` for production.

## Conventions

- All modules receive `poliswag` (the bot instance) and access services via `self.poliswag.<module>`.
- Admin guard: `str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS` or `cog_check`.
- Three audiences: members, mods (`ADMIN_USERS_IDS`), owner (`MY_ID`), via `modules/permissions.py` (`is_mod`/`is_owner`; `mods_only()`/`owner_only()` for single commands). `!help` (`modules/help_command.py`) sections by the same gates: `_OWNER_COGS`, any `cog_check` (mods), or those predicates in `command.checks`. A command gated only inside its body shows to members — add the decorator. `!help` deletes its command message (in `prepare_help_command`). A bare `@Poliswag` mention answers like `!help` (`Poliswag.process_commands`, same DM gate).
- `!notify` **ref** resolution: `#mention` → raw id → exact name → `%-<suffix>` LIKE; category suffixes (`raros`, `100iv`, `0iv`, `uteis`) fan out to `leiria-<suffix>` and `marinha-<suffix>`.
- `_PAIRED_PREFIXES = ("leiria-", "marinha-")` drives fan-out logic in `notifications.py`.
- Embed color: `Config.EMBED_COLOR = 0x4169E1`.
- Tests under `tests/` (pytest). Run via `make test` inside Docker.
- Logging: `utility.log_to_file(msg, level)` → `logs/actions.log` + `logs/error.log`.
- Migrations: `setup_hook` replays every `migrations/*.sql` on each start (`modules/migrations.py`), before cogs load. No tracking table, so every statement must be `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`; `test_every_migration_is_rerunnable` enforces it.
- Scheduler: a tick step taking ≥60s is logged as ERROR (`_run_tick_step`) so stalls reach the daily error review. Each tick ends by touching `logs/heartbeat` for the host watchdog (`docs/context/host-ops.md`).

## Topic files — read when…

| File | Read when |
|---|---|
| `docs/context/hundo-alerts.md` | the 100IV DMs, their Poracle rules/template, or the site's switch |
| `docs/context/database.md` | table columns, or reading scanner/Dragonite/Poracle tables |
| `docs/context/modules.md` | you need a module's full behaviour, limits or rationale |
| `docs/context/events.md` | event ingestion, Lisbon-time storage, CONVIVIO delivery/acks, the repair tool, event tests |
| `docs/context/event-panel.md` | `!eventpanel`, the Eventos role button, its @everyone behaviour |
| `docs/context/moderation.md` | the `ignorar-este-canal` trap: ban/purge/unban, invite DM |
| `docs/context/error-review.md` | the host cron that has Claude review `logs/error.log` daily |
| `docs/context/host-ops.md` | the host watchdog restarting Poliswag, `scripts/dm_owner.py`, backup-failure DMs |
