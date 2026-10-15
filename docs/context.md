# Poliswag — LLM context reference (core)

Read every session. Detail lives in `docs/context/`: open a topic file only when the task touches it (table at the end).

## What it is
Discord bot (`discord.py`) for the **PoGoLeiria** Pokémon GO scanner community (Leiria / Marinha Grande, Portugal). Wraps scanner infrastructure (Dragonite + Rotom): quest search, event notifications, account monitoring, shiny prediction, Poracle alert management. Sister project: the site at `/root/PoGoLeiria` (own `docs/context.md`), which reads the quest JSON, `megas.json` and `trade_player`/`pokemon_name` data the bot writes.

## Stack
- Python 3.11 · `discord.py` · `pymysql` (MariaDB) · `aiohttp` · `jinja2` + `imgkit`
- Docker Compose (dev + prod) · `pytest` · `black` + `ruff` (pre-commit) · `vulture`

## Entry point
`main.py` — `Poliswag(commands.Bot)` instantiates all service modules and loads all cogs in `setup_hook`. Bot channels are resolved in `on_ready` → `get_channels()`; an unset id logs an ERROR and leaves the attribute `None` rather than raising.

## Module map (`modules/`) — one line each; behaviour in `context/modules.md`

| Module | Responsibility |
|--------|---------------|
| `config.py` | `Config`: every env var, via `dotenv`. |
| `database_connector.py` | pymysql wrapper, retries, `list[dict]` rows; `connect(...)` for short-lived connections. |
| `http_client.py` | `fetch_data(key)`: shared aiohttp session; DEV reads `mock_data/`. |
| `scanner_status.py` / `stack_recovery.py` / `device_manager.py` | Dragonite/Rotom/Golbat polling, `MAPA` channel rename, recovery ladder. |
| `scanner_manager.py` | docker-py control + `poliswag` scan state; `is_day_change()`. |
| `quest_search.py` / `quest_exporter.py` / `quest_map.py` | Quest lookup, `quests.json` export for the PWA (Marinha = lon ≤ −8.9), map PNGs. |
| `event_manager.py` / `event_store.py` / `event_stats.py` / `event_images.py` | ScrapedDuck ingestion, `event` table, CONVIVIO notices, end-of-event stats. See `events.md`. |
| `trade_digest.py` / `trade_dm.py` / `trade_announcer.py` | 09:00 trade digest; trade-match DMs; live match posts. |
| `hundo_alerts.py` / `hundo_confirmation.py` | Pokédex 100IV DMs via Poracle rules. See `hundo-alerts.md`. |
| `shiny_roll.py` / `shiny_store.py` / `shiny_alerts.py` / `shiny_odds.py` | Shiny prediction, tables, DMs, event odds. See `shiny.md`. |
| `spawn_history.py` | 48-hour raw spawn archive and statistics. See `spawn-history.md`. |
| `account_monitor.py` / `lure_manager.py` | Dragonite account stats image; free accounts + `account_lure` budget (Dragonite read-only). |
| `poracle_client.py` | Async Poracle-NG REST client; `PoracleError` on failure. |
| `tracker_store.py` / `role_manager.py` / `mega_exporter.py` | `tracked_quest_reward` CRUD; legacy role panel (unused by `event_panel.py`); mega sprites. |
| `image_generator.py` / `embeds.py` / `utility.py` / `locale_pt.py` | imgkit cards, shared embed builders, `log_to_file`/Lisbon time, PT date names. |

## Cogs (`cogs/`) — full command table in `context/cogs.md`
One cog per area: quests, tracker, event, notifications, accounts, container_manager, moderation, scheduled, lures, event_panel, announcements, shiny, agents, spawn_history, mentions.
Auth: **mods** (`ADMIN_USERS_IDS`, `cog_check`) for tracker/event/notifications/scheduled/lures/announcements and `!scan`/`!exportquests`; **owner only** (`MY_ID`) for accounts, container, event_panel, shiny (pilot), agents, spawn_history. `scheduled.py` runs one `@tasks.loop` per minute (all periodic work).

## Databases — detail in `context/database.md`
- **poliswag** (read/write): scan state, `event`, `excluded_event_type`, `tracked_quest_reward`, `account_lure`, `shiny_*`, trade/100IV state. Schema = `migrations/*.sql`.
- **golbat** scanner DB (`DB_SCANNER_NAME`, read-only): `pokestop` quests, daily `*_stats`. **dragonite** (read-only): `account`. **poracle**: `humans`, tracking rules.

## Config and integrations — `context/config.md`
Endpoints (Dragonite, Rotom, Home Assistant, Poracle-NG, ScrapedDuck, masterfile, Niantic version), the env var list and dev/prod behaviour. Key facts: `ENV=DEVELOPMENT|PRODUCTION` (`IS_PRODUCTION`); DEV serves `mock_data/` for infra endpoints; `make up` = dev stack, `docker-compose.prod.yaml` = production.

## Hard conventions (full list: `context/conventions.md`)
- Modules take the bot instance and reach services via `self.poliswag.<module>`.
- Three audiences via `modules/permissions.py`: members, mods, owner. Gate commands with `mods_only()`/`owner_only()` or `cog_check`; a command gated only in its body shows in `!help` to members.
- Automated notices go out as embeds (`Config.EMBED_COLOR`), never plain text. Copy is European Portuguese (PT-PT).
- Migrations replay on every start: every statement must be idempotent (`IF NOT EXISTS`); a test enforces it.
- Log with `utility.log_to_file(msg, level)`. Tests: `make test` (Docker). Scheduler ticks touch `logs/heartbeat` for the host watchdog.

## Topic files — read when…

| File | Read when |
|---|---|
| `docs/context/modules.md` | you need a module's full behaviour, limits or rationale |
| `docs/context/cogs.md` | a command's exact syntax, auth or behaviour |
| `docs/context/config.md` | endpoints, env vars, dev vs prod |
| `docs/context/conventions.md` | permissions/`!help`, `!notify` refs, migrations, scheduler, copy rules |
| `docs/context/database.md` | table columns, or reading scanner/Dragonite/Poracle tables |
| `docs/context/hundo-alerts.md` | the 100IV DMs, their Poracle rules/template, or the site's switch |
| `docs/context/events.md` | event ingestion, Lisbon-time storage, CONVIVIO delivery/acks, the repair tool, event tests |
| `docs/context/event-panel.md` | `!eventpanel`, the Eventos role button, its @everyone behaviour |
| `docs/context/moderation.md` | the `ignorar-este-canal` trap: ban/purge/unban, invite DM |
| `docs/context/error-review.md` | the host cron that has Claude review `logs/error.log` daily |
| `docs/context/host-ops.md` | the host watchdog restarting Poliswag, `scripts/dm_owner.py`, backup-failure DMs |
| `docs/context/scanner-updates.md` | host-side scanner update staging and session-protected recovery |
| `docs/context/shiny.md` | `!shiny`, the host linker and its guards/breaker, shiny alert DMs |
| `docs/context/shiny-access-requests.md` | Shiny map access request embeds and moderator approval/rejection buttons |
| `docs/context/shundo-mentions.md` | Per-player Shundo channel mentions and Poracle templates |
| `docs/context/spawn-history.md` | raw spawn retention, unfiltered leaderboards, JSON export and coverage gaps |
| `docs/context/agents.md` | owner-requested Claude/Codex jobs, runtime awareness, host executor and review worktrees |
| `docs/context/background-predictions.md` | background-based shiny predictions |
| `alert-noise-cleanup.md`, `pokedex-validation-alerts.md`, `adb-server.md` (all in `docs/context/`) | alert noise, Pokédex validation alerts, shared ADB server |
