# Poliswag

Discord bot and operations glue for the [PoGoLeiria](https://pogoleiria.pt) scanner (Leiria / Marinha Grande). Handles quest scans, event tracking, account monitoring, scanner health, the shiny map with its predictions and alerts, and the Discord commands around them.

It is the Discord bridge between services: Discord in, HTTP to the scanner stack and to PoliSnatch (lure placement and shiny link resolution), database out. Nothing that runs inside the game lives here, and `scripts/check_private_tech.py` keeps it that way. Some commands are deliberately narrow: `!pn`/`!lure` are admin commands, and `!shiny` is owner-only while the predictions are being checked in the field.

## Documentation

The map of every document is [docs/README.md](docs/README.md); start with [docs/context.md](docs/context.md). Every Discord command is in [docs/context/cogs.md](docs/context/cogs.md), module behaviour in [docs/context/modules.md](docs/context/modules.md), and endpoints/env vars in [docs/context/config.md](docs/context/config.md).

## Stack

- Python 3.11, `discord.py`
- MariaDB (shared with the scanner stack)
- Docker Compose for both dev and prod
- Tests: `pytest`
- Quality gates: `black`, `ruff`, `vulture` and pre-commit — which also runs
  `scripts/check_private_tech.py`, so game-side or interception code cannot land here

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

Unit tests never open real DB connections — a `pytest` autouse fixture in `tests/conftest.py` patches `pymysql.connect` globally, and the suite pins a deterministic environment (`dotenv_values` on the explicit repo-root `.env.test` path plus explicit `os.environ` scratch defaults, then `PYTHON_DOTENV_DISABLED`) so ambient values and production `.env` discovery cannot leak in. The `tests/integration/` SQL suites are the opt-in exception: they run only when their selector env is set, against a throwaway MariaDB each, and refuse any server without its marker schema or with a production-like schema on it. Detail and the exact disposable invocations: `docs/context/conventions.md`.

## Environments

Selected via `ENV=` in `.env`:

- **DEVELOPMENT** — mock DB seeded from `mock_database/data.sql`, mock JSON responses from `mock_data/`, logs tailed automatically.
- **PRODUCTION** — connects to the live scanner DB and real endpoints. No mock data.

## PoliSnatch (lures and link resolution)

`!lure`, `!pn` and the shiny link resolution submit jobs to PoliSnatch over
HTTPS; the in-game half never runs here. Two settings point at it:

| Variable | What it is |
|---|---|
| `POLISNATCH_JOBS_URL` | The deployment's job API base, ending in `/v1/jobs`. Not committed. |
| `POLISNATCH_JOBS_KEY_FILE` | File holding the submit key, default `/etc/poliswag/secrets/polisnatch-jobs-key`. Read per call, so rotating it needs no restart. Production mounts `/etc/poliswag/secrets` read-only. |

The key is minted on the PoliSnatch host and is never put in the environment or a log line. Without it those commands answer with a configuration error instead of failing silently.

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
scripts/     Host scripts and service units, including the policy guard
docs/        Context notes: commands, modules, env vars, conventions
.github/     CI workflow
```

## Tests

```bash
make test          # inside Docker
make test-local    # locally (requires pip install -r requirements-dev.txt)
```

Tests live under `tests/` and cover the scanner status logic, account monitor, event manager, quest tracker, HTTP client, image generation, the shiny roll and its alerts, and the PoliSnatch job client.

## License

[MIT](https://choosealicense.com/licenses/mit/)
