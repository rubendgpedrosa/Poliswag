# Poliswag — LLM context reference

## What it is
Discord bot (`discord.py`) for the **PoGoLeiria** Pokémon GO scanner community (Leiria / Marinha Grande, Portugal). Wraps scanner infrastructure (Dragonite + Rotom) and exposes quest search, event notifications, account monitoring, and Poracle alert management via `!` prefix commands.

## Stack
- Python 3.11 · `discord.py` · `pymysql` (MariaDB) · `aiohttp` · `jinja2` + `imgkit`
- Docker Compose (dev + prod) · `pytest` · `black` + `ruff` (pre-commit) · `vulture`

## Entry point
`main.py` — `Poliswag(commands.Bot)` instantiates all service modules and loads all cogs in `setup_hook`. Bot channels are resolved in `on_ready` → `get_channels()`, which is the list to read; an unset id logs an ERROR and leaves the attribute `None` rather than raising.

## Module map (`modules/`)

| Module | Responsibility |
|--------|---------------|
| `config.py` | Single `Config` class reading all env vars via `dotenv`. Source of truth for all settings. |
| `database_connector.py` | `DatabaseConnector(database?)` — pymysql wrapper with retry (3 attempts), auto-reconnect on errno 2006/2013, returns `list[dict]` from `cursor.description`. Default DB = `Config.DB_POLISWAG`; scanner DB passed explicitly. |
| `http_client.py` | `fetch_data(endpoint_key, …)` — single shared `aiohttp.ClientSession`, reads from `Config.ENDPOINTS[key]`. In DEV (`not IS_PRODUCTION`) returns mock JSON from `mock_data/` for infra endpoints. |
| `scanner_status.py` | `ScannerStatus` — polls Dragonite (`/status`) + Rotom (`/api/status`) + Golbat DB. Renames a single `MAPA` Discord voice channel with a combined down/expected-worker indicator (expected counts read live from Dragonite per area, not hardcoded), fires HA webhook when scanner is fully down (15 min cooldown). Missing/unreachable data renders as ❌ but is excluded from the recovery-ladder trigger. |
| `scanner_manager.py` | Docker control (via `docker-py`) + `poliswag` table state (`last_scanned_date`, `scanned` flag). `is_day_change()` triggers a new scan cycle. |
| `quest_search.py` | `QuestSearch` — owns scanner DB connection. Loads pokemon/item name maps + masterfile. `find_quest_by_search_keyword(term, is_leiria)` queries `pokestop` table. Area split: Marinha Grande = lon ≤ −8.9. Handles AR/standard quest field duality via `_quest_fields()`. |
| `quest_exporter.py` | `QuestExporter.export(force=False)` — reads `pokestop` and writes a JSON file to `QUEST_JSON_OUTPUT` (default `/pogo-public/quests.json`) for the PWA. Skips the write when quest content is unchanged (md5 hash stored as `contentHash` in `quests-meta.json`); returns `True` only when rewritten. `force=True` always rewrites. Triggered on scan completion, every 30 min by `scheduled.py` as a safety net, and via `!exportquests`. |
| `event_manager.py` | Fetches events from ScrapedDuck (15 min cache). Stores/updates `event` table. Dispatches embed notifications to `CONVIVIO_CHANNEL` when events start/end, respecting `excluded_event_type`. Weekly digest on Mondays. |
| `event_store.py` | Thin DB wrapper for `excluded_event_type` and `event` tables. |
| `event_stats.py` | `EventStats.get_summary(event)` → stats text for an ended CD / Spotlight (species from ScrapedDuck `extra_data` icons, name as fallback) or Raid Hour/Day (the tier that grew vs. the day before, and its bosses); `None` for every other type, which then gets one line under the "Eventos que terminaram" header instead of a card. Golbat daily `*_stats` tables, ~7 days kept. |
| `trade_digest.py` | 09:00 post of trade entries added since the last one (`last_trade_digest_at`); names link to `/trocas/jogador/<id>`, 🤝 marks entries that already have a partner, 3 lines per player. `trade_announcer.py` posts matches as they happen. |
| `account_monitor.py` | Polls Dragonite `/accounts/stats`. Aggregates disabled statuses. Posts account image to `ACCOUNTS_CHANNEL`. |
| `poracle_client.py` | `PoracleClient` — async REST client for Poracle-NG (`X-Poracle-Secret` header). Methods: `get_channels`, `get_pokemon`, `add_pokemon`, `delete_pokemon`, `set_enabled`, `register_channel`, `send_test`. Raises `PoracleError` on non-2xx. |
| `tracker_store.py` | CRUD for `tracked_quest_reward` table. |
| `lure_manager.py` | `LureManager` — owns a read-only `DatabaseConnector(DB_DRAGONITE)`. `list_available_with_lures()` reads available+healthy accounts from `dragonite.account` (not banned/suspended/warned/invalid/auth_banned, off cooldown, `last_released >= last_selected`), seeds new usernames into `account_lure` at 12, returns up to 5 fewest-first with `{username, password, nb_lures}`. `adjust_lure_count(username, delta)` → `GREATEST(nb_lures+delta, 0)` UPDATE. Writes only `poliswag.account_lure`; dragonite is read-only. |
| `role_manager.py` | Handles Discord role button interactions for the **legacy** team/notification panel: resolves roles by *name*, auto-grants all `Alertas*` roles to a brand-new member, and `defer()`s with no reply. `cogs/event_panel.py` deliberately does not reuse it — see that file's header. |
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
| `scheduled.py` | `!weeklydigest`, `!testevent HH:MM`; `@tasks.loop` every minute (version/quest-scan/events/workers/accounts/weekly-digest + 30-min safety-net quest export) | admin-only |
| `lures.py` | `!lures`, `!uselure USERNAME NUMERO` | admin-only (`cog_check`) |
| `event_panel.py` | `!eventpanel` (publica o painel), `!eventpanel test` (DM de ensaio), `!eventpanel clear [confirm]` | admin-only (`cog_check`) |
| `announcements.py` | `!anunciar <texto>` — reposts the text (+ attachments) as typed in the announcements channel (`EVENT_PANEL_CHANNEL_ID`). Echoes a pings-off preview first; posts only on **Publicar** (author only, 5 min, one press), with `AllowedMentions.all()` so a typed `@everyone` pings | admin-only (`cog_check`) |

## Events role panel (`cogs/event_panel.py`)

Self-serve opt-in: a button in the announcements channel grants/removes the permanent **Eventos** role, and a channel permission overwrite turns that role into access to the current event channel.

- A new event = a new channel with the role's overwrite; everyone already opted in sees it immediately. Only `_PANEL_TEXT` is edited (the event's name), then `!eventpanel` re-run — a redeploy is needed for that edit to reach the container.
- The panel is a plain message plus the button, not an embed, and **carries its own `@everyone`** — the panel *is* the announcement. `allowed_mentions` is set explicitly (`everyone=True` on the post, `AllowedMentions.none()` on the DM rehearsal, so a rehearsal can never ping). **Every `!eventpanel` run pings the whole server**, including a re-run to fix a typo.
- `_PANEL_TEXT` ships with a `<NOME DO EVENTO>` placeholder; `!eventpanel` refuses to post while it is still there, because that post would `@everyone` the server with the raw template. `!eventpanel test` deliberately still sends it.
- It names no channel on purpose: its audience cannot see the channel yet, and Discord renders a hidden channel's mention as a dead link for them.
- `EventPanelView` is persistent (`timeout=None`, `custom_id="event_panel:toggle"`), re-registered by `main.py`'s `setup_hook` via `add_view` — without that, buttons on panels from past events die on the next restart. It must stay in `setup_hook`: `discord.ui.View.__init__` needs a running loop, so `__init__` would raise `RuntimeError` at boot.
- The role is resolved **by id**, and the clicker is resolved through the panel channel's guild — so the button behaves identically in a DM, which is what makes `!eventpanel test` a real rehearsal rather than a preview.
- `_preflight()` → `(role, channel, error)` refuses to publish when the channel is unset, the role is missing, or the role's position is `>=` Poliswag's top role. That last one would otherwise surface as a `Forbidden` once per member who clicks, hours later.
- Does **not** reuse `modules/role_manager.py`: that one is name-based, grants every `Alertas*` role to a brand-new member, and `defer()`s without replying.
- Role and channel overwrites are set up by hand in Discord; the bot only adds/removes the role, never touches channel permissions.

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
VOICE_CHANNEL_LEIRIA_ID  (combined MAPA status channel; var name kept for continuity)
EVENT_PANEL_CHANNEL_ID   (announcements channel holding the !eventpanel message)
EVENTS_ROLE_ID           (permanent Eventos role the panel button grants)
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

## Host-side error review (not part of the bot)

`scripts/error-review.sh`, run from root's crontab on the **host**, not in the container. Reads new `logs/error.log` entries once a day and has Claude diagnose them into `logs/error-review.md` (dated copies in `logs/reviews/`). Design: [spec](superpowers/specs/2026-09-21-daily-error-review-design.md).

| Fact | Value |
|---|---|
| Schedule | cron `0 5,6 * * *` UTC; script keeps only the 06:00 `Europe/Lisbon` run (Debian cron has no `CRON_TZ`) |
| Window | `logs/.error-review-watermark`, falling back to 24h; advances only on a run that produced a report |
| Cost | Claude is called **only when there are new errors**; clean days exit before any model call |
| Safety | `--permission-mode default` with a read-only allowlist; no `Write`/`Edit`. It diagnoses, never repairs |
| On failure | Always writes a report with `status: claude-failed\|limit-reached\|timeout\|claude-unavailable` plus the raw counts and first traceback |
| Manual run | `FORCE_HOUR=1 ./scripts/error-review.sh`; `REVIEW_MODEL=opus` for a harder week |

Independent of `_check_daily_error_digest` in `cogs/scheduled.py`, which still posts its 09:00 count to `MOD_CHANNEL`.
