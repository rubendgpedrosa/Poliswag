# Poliswag

Discord bot and operations glue for the [PoGoLeiria](https://pogoleiria.pt) scanner (Leiria / Marinha Grande). Handles quest scans, event tracking, account monitoring, scanner health, and user-facing Discord commands.

## Stack

- Python 3.11, `discord.py`
- MariaDB (shared with the scanner stack)
- Docker Compose for both dev and prod
- Tests: `pytest`
- Formatting: `black` + pre-commit

## Quick start

```bash
cp .env.example .env     # fill in tokens, channel IDs, DB creds
make install-hooks       # pre-commit + black
make up                  # ENV=DEVELOPMENT by default, PRODUCTION via .env
```

`make help` lists every target. Common ones:

| Target | What it does |
|---|---|
| `make up` | Build + start the stack |
| `make down` | Stop + remove containers and volumes |
| `make stop` | Stop without removing |
| `make reload` | Restart the bot and truncate log files |
| `make logs` | Tail the container logs |
| `make test` | Run `pytest` inside the container |
| `make format` / `make format-check` | `black` in write / check mode |
| `make lint` | Run all pre-commit hooks |
| `make dead-code` | `vulture` scan |

## Environments

Selected via `ENV=` in `.env`:

- **DEVELOPMENT** — mock DB seeded from `mock_database/data.sql`, mock JSON responses from `mock_data/`, logs tailed automatically.
- **PRODUCTION** — connects to the live scanner DB and real endpoints. No mock data.

## Discord commands

### Quests
- `!questleiria [area] [search]` — search active quests
- `!scan` — trigger a new quest scan
- `!exportquests` *(admin)* — export quest data to the PWA

### Quest reward tracking
- `!track <reward>` / `!untrack <reward>` — manage the watch list
- `!tracklist` — show current watch list
- `!untrackall` — clear the watch list
- `!tracked` — post today's tracked quests to the community channel

### Events
- `!eventtypes` — list known event types
- `!exclude <type>` / `!include <type>` — mute/unmute a type in notifications
- `!excludedlist` — show muted types
- `!excludeclear` — clear the mute list
- `!weeklydigest` *(admin)* — post the weekly event summary
- `!testevent HH:MM` *(admin)* — dry-run the event dispatcher at a given time

### Operations
- `!accounts` — render the current account-pool status as an image
- `!container start|stop` *(admin)* — control the scanner container

## Project layout

```
cogs/        Discord cog modules — one per command group
modules/     Core logic (scanner status, DB, HTTP, event manager, tracker, …)
templates/   HTML/Jinja templates for image generation (quests, accounts)
data/        Static + persistent JSON (events, pokemon/item name maps)
mock_data/   Fixture responses used in DEVELOPMENT
mock_database/data.sql  Seed data for the dev DB
migrations/  SQL migrations for the poliswag table
scripts/     One-off utilities
tests/       pytest suite
logs/        actions.log (activity) + error.log (errors)
```

## Endpoints and integrations

Poliswag talks to the scanner stack via URLs in `.env`:

- **Dragonite admin** (`/status`, `/accounts/stats`, `/quest/all/start`, `/status/quest-area/<id>`) — worker health, account pool, quest scan control.
- **Rotom** (`/api/status`) — device connectivity.
- **Home Assistant webhook** (`ALL_DOWN_ENDPOINT`) — alerts when the scanner pipeline degrades.
- **Golbat DB** — read-only queries for spawn/quest data.
- **ScrapedDuck events feed** — upstream event calendar.
- **PokeMiners masterfile** — Pokémon / move name translations.

## Tests

```bash
make test
```

Tests live under `tests/` and cover the scanner status logic, account monitor, event manager, quest tracker, HTTP client, and image generation.

## License

[MIT](https://choosealicense.com/licenses/mit/)
