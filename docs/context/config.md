# Integrations, config and dev/prod

## External integrations

Host-side scanner update staging and session-protected recovery: [scanner-updates.md](scanner-updates.md).

| Service | Endpoint env var | Purpose |
|---------|-----------------|---------|
| Dragonite admin | `SCANNER_STATUS_ENDPOINT` | Worker health + account stats |
| Rotom | `DEVICE_STATUS_ENDPOINT` | Device liveness |
| Home Assistant | `ALL_DOWN_ENDPOINT` | Webhook when pokemon data goes stale (`map_status`) and when it flows again (`map_restored`) |
| Home Assistant | `OWNER_ALERT_ENDPOINT` | Generic backup phone notifications (`scripts/notify_owner.py`); Pokémon GO operational alerts use private Discord DMs (`modules/owner_alert.py`, host helper `--discord`). (Whether pogoleiria.pt, the Pokédex, the quest export and the map answer is watched by the home monitoring's probes since 2026-10-04, not by the bot.) Falls back to a DM. The daily error digest stays a DM. |
| Poracle-NG | `PORACLE_API_URL` + `PORACLE_API_SECRET` | Pokémon alert rule CRUD |
| PoliSnatch jobs | `POLISNATCH_JOBS_URL` + `POLISNATCH_JOBS_KEY_FILE` | `!lure`/`!pn` submit, poll and control jobs; the submit key comes from `polisnatch-dist jobs-key`, is read per call and never logged |
| ScrapedDuck | `EVENTS_ENDPOINT` | Event calendar JSON |
| PokeMiners masterfile | `MASTERFILE_ENDPOINT` | Pokémon/item name translations |
| Niantic version | `NIANTIC_FORCED_VERSION_ENDPOINT` | Version check |

## Config env vars (key ones)

```
DISCORD_API_KEY, ADMIN_USERS_IDS (comma-sep), MY_ID
QUEST_CHANNEL_ID, CONVIVIO_CHANNEL_ID, MOD_CHANNEL_ID, ACCOUNTS_CHANNEL_ID
VOICE_CHANNEL_LEIRIA_ID  (combined `STATUS:` scanner channel; var name kept from its old MAPA name)
EVENT_PANEL_CHANNEL_ID   (announcements channel holding the !eventpanel message)
EVENTS_ROLE_ID           (permanent Eventos role the panel button grants)
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_POLISWAG, DB_SCANNER_NAME, DB_DRAGONITE
SCANNER_CONTAINER_NAME
ENV=DEVELOPMENT|PRODUCTION  (IS_PRODUCTION = ENV=="PRODUCTION")
PORACLE_API_URL, PORACLE_API_SECRET
POLISNATCH_JOBS_URL (default https://mitm.pogoleiria.pt/v1/jobs), POLISNATCH_JOBS_KEY_FILE
  (default /etc/poliswag/secrets/polisnatch-jobs-key), POLISNATCH_JOBS_KEY (inline fallback)
POKEMON_NAME_FILE, ITEM_NAME_FILE  (JSON name maps in data/)
MASTERFILE_ENDPOINT, TRANSLATIONFILE_ENDPOINT
UI_ICONS_URL, TEMPLATE_HTML_DIR, ACCOUNTS_TEMPLATE_HTML_FILE
QUEST_MAP_URL, QUEST_MAP_STYLE  (Rampardos /staticmap; default style osm-bright)
QUEST_JSON_OUTPUT  (default /pogo-public/quests.json)
LOG_FILE, ERROR_LOG_FILE
```

The PoliSnatch submit key (server side: `polisnatch-dist jobs-key`) is read per
call from `POLISNATCH_JOBS_KEY`, or from `POLISNATCH_JOBS_KEY_FILE` (mode 0600).
The production container mounts `/etc/poliswag/secrets` read-only for it. With
neither, nothing is submitted and `!lure`/`!pn` report the jobs as disabled. Job
ids are stored in `polisnatch_job` (see `database.md`) so a restart resumes
polling what is still open.

## Dev vs prod

- `ENV=DEVELOPMENT` → `http_client.fetch_data` returns mock JSON from `mock_data/*.json` for infra endpoints; real HTTP only for `all_down` + `events`. `scanner_status.json`/`device_status.json` are untracked and generated with current timestamps by `python mock_data/refresh.py` (the test session runs it first).
- `mock_database/init.sql` seeds a full scanner DB for local Docker Compose.
- `make up` starts dev stack; `docker-compose.prod.yaml` for production.
