# Poliswag — LLM context reference (core)

Read every session. Every other document — context topics and the historical archive — is indexed in [`docs/README.md`](README.md); open a topic only when the task touches it.

## What it is
Discord bot (`discord.py`) for the **PoGoLeiria** Pokémon GO community (Leiria / Marinha Grande, Portugal) over the Dragonite/Rotom scanner stack: quests, events, accounts, shiny prediction, Poracle alerts. Sister project: the site at `/root/PoGoLeiria` (own `docs/context.md`) reads its quest JSON, `megas.json` and `trade_player`/`pokemon_name` writes.

## Stack
- Python 3.11 · `discord.py` · `pymysql` (MariaDB) · `aiohttp` · `jinja2` + `imgkit`
- Docker Compose (dev + prod) · `pytest` · `black` + `ruff` (pre-commit) · `vulture`

## Entry point
`main.py` — `Poliswag(commands.Bot)` instantiates all service modules and loads all cogs in `setup_hook`. Bot channels are resolved in `on_ready` → `get_channels()` one id at a time; an unset or failing id logs an ERROR and becomes `None` without blocking the rest.

## Module map (`modules/`) — one line each; behaviour in `context/modules.md`

| Module | Responsibility |
|--------|---------------|
| `config.py` / `database_connector.py` | Env config; pymysql wrapper that retries only before execution (never replays a statement); `connect(...)` for short-lived connections. |
| `http_client.py` | `fetch_data(key)`: shared aiohttp session; DEV reads `mock_data/`. |
| `scanner_status.py` / `stack_recovery.py` / `device_manager.py` | Dragonite/Rotom/Golbat polling, `STATUS:` channel rename, recovery ladder. |
| `scanner_manager.py` | docker-py control + `poliswag` scan state; `is_day_change()`. |
| `quest_search.py` / `quest_exporter.py` / `quest_map.py` | Quest lookup (only future expiry is live), `quests.json` export, map PNGs. See `quests.md`. |
| `event_manager.py` / `event_store.py` / `event_stats.py` / `event_images.py` | ScrapedDuck ingestion, `event` table, notices, end-of-event stats. See `events.md`. |
| `trade_digest.py` / `trade_dm.py` / `trade_announcer.py` | Trade digest, match posts and match DMs. See `trades.md`. |
| `hundo_alerts.py` / `hundo_confirmation.py` | Pokédex 100IV DMs via Poracle rules. See `hundo-alerts.md`. |
| `shiny_roll.py` / `shiny_store.py` / `shiny_alerts.py` / `shiny_odds.py` | Shiny prediction, tables, odds; alerts in private channels, DMs only for pilot users. See the internal shiny notes (not in this repo). |
| `shiny_channels.py` / `shiny_events.py` / `shiny_availability.py` / `shiny_leaderboard.py` | Private shiny channels, opt-in event digests, availability, leaderboard. See the internal shiny notes (not in this repo). |
| `spawn_history.py` | 48-hour raw spawn archive and statistics. See `spawn-history.md`. |
| `account_monitor.py` / `lure_manager.py` / `lure_watcher.py` | Dragonite account image; free accounts + `account_lure` budget; live lure count for presence. |
| `poracle_client.py` | Async Poracle-NG REST client; `PoracleError` on failure. |
| `tracker_store.py` / `role_manager.py` / `mega_exporter.py` | `tracked_quest_reward` CRUD; legacy role panel; mega sprites. |
| `hermes_client.py` / `public_conversation.py` / `conversation_model.py` / `permissions.py` / `help_command.py` / `dm_policy.py` | Public tag replies (Codex Luna low + public map data), audiences/help gating, DM policy. See `agents.md`. |
| `page_view_stats.py` / `trade_stats.py` / `tracking_health.py` / `migrations.py` | Landing analytics, trade aggregates, traffic-silence log, startup migrations. |
| `image_generator.py` / `embeds.py` / `utility.py` / `locale_pt.py` | imgkit cards, embed builders, logging/Lisbon time, PT date names. |

## Cogs (`cogs/`) — full command table in `context/cogs.md`
One cog per area; `scheduled.py` ticks every minute. Auth: **mods** for tracker/event/notifications/scheduled/lures/announcements and `!scan`/`!exportquests`; **owner only** for accounts, container, webstats, event_panel, shiny, agents, spawn_history; `!pokedex`/`!resumo` are member-facing (DMs), shiny-map buttons mod/owner in `MOD_CHANNEL_ID`. Mentions run before commands: the owner uses one persistent private Hermes conversation in DMs (plain text or a visible bot tag). Explicit tagged Claude/Codex jobs and blocked-job replies in owner DMs retain the read-only worker path. All tagged guild members, including the owner, get public game answers with cooldowns; non-owner conversational DMs stay silent. See `agents.md`.

## Databases — detail in `context/database.md`
- **poliswag** (read/write): scan state, events, quests, shiny, trades, 100IV. Schema = `migrations/*.sql`.
- **golbat** (`DB_SCANNER_NAME`, read-only): `pokestop` quests with `quest_expiry` / `alternative_quest_expiry`; only future rows are live (see `quests.md`). Daily `*_stats`. **dragonite** (read-only): `account`. **poracle**: `humans`, rules.

## Config and integrations — `context/config.md`
Endpoints, env vars, dev/prod. Key facts: `ENV=DEVELOPMENT|PRODUCTION`; DEV serves `mock_data/` for infra endpoints; `make up` = dev stack, `docker-compose.prod.yaml` = production. Tests pin `ENV=DEVELOPMENT` from `.env.test`; SQL suites run only against a throwaway MariaDB (see `conventions.md`).

## Hard conventions (detail: `context/conventions.md` — do not duplicate here)
- Modules/services, audiences/auth with owner-tag gating and public replies, embeds/PT-PT copy, idempotent migrations, logging/deterministic tests/disposable SQL CI, scheduler post-send markers — see `conventions.md`.

## Documentation
Every doc in this repository is indexed in [`docs/README.md`](README.md) — module/cog detail, quests, trades, shiny, events, host ops and the historical archive. Open a topic file only when the task touches it.
