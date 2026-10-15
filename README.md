# Poliswag

Discord bot and operations glue for the [PoGoLeiria](https://pogoleiria.pt) scanner (Leiria / Marinha Grande). Handles quest scans, event tracking, account monitoring, scanner health, and user-facing Discord commands.

## Documentation

The map of every document is [docs/README.md](docs/README.md); start with [docs/context.md](docs/context.md). Every Discord command is in [docs/context/cogs.md](docs/context/cogs.md), module behaviour in [docs/context/modules.md](docs/context/modules.md), and endpoints/env vars in [docs/context/config.md](docs/context/config.md).

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
| `make up` | Build + start the stack (runs migrations automatically in dev) |
| `make watch` | Like `make up` but restarts the bot on every saved `.py` file |
| `make down` | Stop + remove containers and volumes |
| `make stop` | Stop without removing |
| `make reload` | Restart the bot (keeps log files) |
| `make logs` | Tail the container logs |
| `make test` | Run `pytest` inside the container |
| `make test-local` | Run `pytest` locally (no Docker needed — requires `pip install -r requirements-dev.txt`) |
| `make check` | Run all quality gates (format, lint, tests) |
| `make format` / `make format-check` | `black` in write / check mode |
| `make lint` | Run all pre-commit hooks |
| `make dead-code` | `vulture` scan |
| `make migrate` | Apply SQL migrations in `migrations/` by hand (the bot also replays them on every start) |
| `make mock-data` | Refresh `mock_data/` timestamps (run after a long dev gap) |

## Local test runs (no Docker)

```bash
pip install -r requirements-dev.txt
make test-local
```

Tests never open real DB connections — a `pytest` autouse fixture in `tests/conftest.py` patches `pymysql.connect` globally. `.env.test` is loaded automatically if no `DISCORD_API_KEY` is set.

## Environments

Selected via `ENV=` in `.env`:

- **DEVELOPMENT** — mock DB seeded from `mock_database/data.sql`, mock JSON responses from `mock_data/`, logs tailed automatically.
- **PRODUCTION** — connects to the live scanner DB and real endpoints. No mock data.

## Project layout

```
cogs/        Discord cog modules — one per command group
modules/     Core logic (scanner status, DB, HTTP, event manager, tracker, …)
templates/   HTML/Jinja templates for image generation (quests, accounts)
data/        Static + persistent JSON (events, pokemon/item name maps)
mock_data/   Fixture responses used in DEVELOPMENT
mock_database/data.sql  Seed data for the dev DB
migrations/  SQL migrations, replayed on every bot start — must be re-runnable (IF NOT EXISTS)
tests/       pytest suite
logs/        actions.log (activity) + error.log (errors)
```

## Tests

```bash
make test          # inside Docker
make test-local    # locally (requires pip install -r requirements-dev.txt)
```

Tests live under `tests/` and cover the scanner status logic, account monitor, event manager, quest tracker, HTTP client, and image generation.

## License

[MIT](https://choosealicense.com/licenses/mit/)
