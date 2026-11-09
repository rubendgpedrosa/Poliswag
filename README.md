# Poliswag

Discord bot for the [PoGoLeiria](https://pogoleiria.pt) Pokémon GO community in the Leiria
district. It connects the community's Discord server to the scanner (scan areas Leiria,
Marinha Grande, Batalha, Porto de Mós and Moita), to the PoGoLeiria site and to the
community's database.

Discord in, HTTP to the scanner and to PoliSnatch (lures and shiny links), database out.
Nothing that runs inside the game lives here; `scripts/check_private_tech.py` checks that.

## What it does

Quest search (`!quest <area> <search>`) and the scheduled quest scans and exports for the
site. Event posts, a weekly calendar and an event panel. Scanner status in the moderation
channel, with account, worker, container and device checks for the owner. Shiny map access,
shiny alerts and 100 % IV alerts. Pokédex and trade logins by DM, trade digests, giveaways,
announcements, role buttons and moderation. Tag it and it answers in the channel.

All commands and who can use them: [docs/context/cogs.md](docs/context/cogs.md).

## Stack

- Python 3.11, `discord.py`
- MariaDB, shared with the scanner stack
- Docker Compose for development and production
- `pytest`, plus `black`, `ruff`, `vulture` and pre-commit (which also runs
  `scripts/check_private_tech.py`)

## Quick start

```bash
cp .env.example .env     # tokens, channel IDs, database credentials
make install-hooks       # pre-commit + black
make up                  # ENV=DEVELOPMENT by default, PRODUCTION via .env
```

`make help` lists every target. The common ones:

| Target | What it does |
|---|---|
| `make up` | Build and start the stack (runs migrations in development) |
| `make watch` | Like `make up`, restarting the bot on every saved `.py` file |
| `make down` | Stop and remove containers and volumes |
| `make stop` | Stop without removing |
| `make reload` | Restart the bot (keeps log files) |
| `make logs` | Follow the container logs |
| `make test` | Run `pytest` inside the container |
| `make test-local` | Run `pytest` without Docker (`pip install -r requirements-dev.txt` first) |
| `make check` | All quality gates: format, lint, tests |
| `make format` / `make format-check` | `black`, writing or checking |
| `make lint` | All pre-commit hooks |
| `make dead-code` | `vulture` scan |
| `make migrate` | Apply the SQL migrations by hand (the bot also replays them on every start) |
| `make mock-data` | Refresh `mock_data/` timestamps after a long break |

## Environments

Set with `ENV=` in `.env`:

`DEVELOPMENT` uses a mock database seeded from `mock_database/init.sql` and mock responses
from `mock_data/`. `PRODUCTION` uses the live database and real endpoints.

## Tests

`make test` runs the suite in Docker and `make test-local` runs it without. Unit tests never
open a real database connection. The SQL suites under `tests/integration/` are opt-in and run
against a throwaway MariaDB; CI runs them on every push. How the isolation works is in
[docs/context/conventions.md](docs/context/conventions.md).

## PoliSnatch

`!lure` and shiny link resolution send jobs to PoliSnatch over HTTPS; the in-game half
never runs here. Two settings point at it:

| Variable | What it is |
|---|---|
| `POLISNATCH_JOBS_URL` | The job API base, ending in `/v1/jobs`. Not committed. |
| `POLISNATCH_JOBS_KEY_FILE` | File holding the submit key, default `/etc/poliswag/secrets/polisnatch-jobs-key`. Read on each call, so a new key needs no restart. |

Without the key those commands answer with a configuration error.

## Project layout

```
cogs/             Discord cogs, one per command group
modules/          Core logic: scanner status, database, HTTP, events, images, alerts
templates/        HTML templates and images for generated cards
data/             Static and persistent JSON (events, Pokémon and item names)
resources/        Reference data (costume catalogue)
mock_data/        Fixture responses for DEVELOPMENT
mock_database/    Seed data for the development database
migrations/       SQL migrations, replayed on every start, so they must be re-runnable
tests/            pytest suite, with the opt-in SQL suites in tests/integration/
scripts/          Host scripts, service units and the policy guard
docs/             Context notes: commands, modules, settings, conventions
```

## Documentation

[docs/README.md](docs/README.md) maps every document; start with
[docs/context.md](docs/context.md). Module behaviour is in
[docs/context/modules.md](docs/context/modules.md) and settings and endpoints in
[docs/context/config.md](docs/context/config.md).
