# Integrations, config and dev/prod

## External integrations

Host-side scanner update staging and session-protected recovery: [scanner-updates.md](scanner-updates.md).

| Service | Endpoint env var | Purpose |
|---------|-----------------|---------|
| Dragonite admin | `SCANNER_STATUS_ENDPOINT` | Worker health + account stats |
| Rotom | `DEVICE_STATUS_ENDPOINT` | Device liveness |
| Dragonite fences | `DRAGONITE_API_URL` (default `http://192.168.1.253:7272`; `SCAN_AREA_CACHE`, default `data/scan_areas.json`) | Area geofences for `modules/scan_areas.py`; the last good copy is cached on disk |
| Dragonite levelling (optional) | `SCANNER_LEVEL_ACCOUNTS_STATUS_ENDPOINT` | Levelling-pool account stats for the account card (`http://dragonite-level:7272/accounts/stats`); unset hides the section |
| Rotom levelling (optional) | `LEVEL_DEVICE_STATUS_ENDPOINT` | Levelling phone and workers (`http://rotom-ng-level:7072/api/status`) |
| Home Assistant | `ALL_DOWN_ENDPOINT` | Webhook when pokemon data goes stale (`map_status`) and when it flows again (`map_restored`) |
| Home Assistant | `OWNER_ALERT_ENDPOINT` | Generic backup phone notifications (`scripts/notify_owner.py`); Pokémon GO operational alerts use private Discord DMs (`modules/owner_alert.py`, host helper `--discord`). (Whether pogoleiria.pt, the Pokédex, the quest export and the map answer is watched by the home monitoring's probes since 2026-10-04, not by the bot.) Falls back to a DM. The daily error digest stays a DM. |
| Poracle-NG | `PORACLE_API_URL` + `PORACLE_API_SECRET` | Pokémon alert rule CRUD |
| PoliSnatch status | `POLISNATCH_STATUS_URL` + `POLISNATCH_JOBS_KEY_FILE` | Polled every 5 min for a new game build (owner DM); the submit key comes from `polisnatch-dist jobs-key`, is read per call and never logged |
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
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_POLISWAG, DB_SCANNER_NAME
ENV=DEVELOPMENT|PRODUCTION  (IS_PRODUCTION = ENV=="PRODUCTION")
PORACLE_API_URL, PORACLE_API_SECRET
POLISNATCH_JOBS_KEY_FILE (default /etc/poliswag/secrets/polisnatch-jobs-key), POLISNATCH_JOBS_KEY (inline fallback)
POLISNATCH_STATUS_URL (default https://polisnatch.pogoleiria.pt/v1/game/status; read with the same submit key,
  skipped when there is no key), POLISNATCH_UPDATE_STATE_FILE (default state/polisnatch-update-state.json)
POLISWAG_PRIVATE_NAMES  (comma-separated; names kept out of public replies; deployment only)
MBWAY_DETAILS  (free text `!mbway` sends by DM; empty points at the Revolut link)
OWNER_DM_DEFAULT  (hermes|claude, default hermes; read from the .env file itself at boot, see agents.md)
HERMISWAG_API_URL, HERMISWAG_API_KEY  (public agent; unset URL disables it, see agents.md)
ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, CAMPFIRE_MODEL  (`!sorteio campfire` reads screenshots with
  the Messages API; the key is the same user key Hermiswag uses, so it needs the workspace header;
  default model claude-sonnet-5-5; unset key makes the command say so)
SCANNER_CARD_FILE, SCANNER_GAME_VERSION_FILE  (scanner card and last game build, under logs/)
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
neither, the status poll is skipped: a keyless request only counts as a failed
login on the server. It is an admin credential there; the bot uses it for that
one read.

`MBWAY_DETAILS` is the owner's own number, so it lives only in the protected `.env`
(and the empty test value in `.env.test`). `!mbway` deletes the command message in a
channel and answers by DM; with the var empty it says so and links
`https://revolut.me/rubendgpedrosa`, the same route as the site's support card.
`env_file` is read at container creation, so after editing `.env` recreate the
container (`docker compose -f docker-compose.prod.yaml up -d --no-build poliswag`);
a plain restart keeps the old environment.

## Dev vs prod

- `ENV=DEVELOPMENT` → `http_client.fetch_data` returns mock JSON from `mock_data/*.json` for infra endpoints; real HTTP only for `all_down` + `events`. `scanner_status.json`/`device_status.json` are untracked and generated with current timestamps by `python mock_data/refresh.py` (the test session runs it first).
- `mock_database/init.sql` seeds a full scanner DB for local Docker Compose.
- `make up` starts dev stack; `docker-compose.prod.yaml` for production.
- `STATE_DIR` (default `state/`, under the `.:/app` bind mount) holds the JSON state files; see `conventions.md`. Tests get an empty one each (`tests/conftest.py`).
- **Deploy with `make deploy`**, never `docker restart poliswag`: the code is bind-mounted (`.:/app`) but the dependencies are in the image, so a restart after a change to `requirements.txt` runs new code on the old image and the bot crashes on every start (2026-11-10: eleven restarts in eighty seconds, and nothing alerts on that). `make deploy` rebuilds, recreates the container and runs `scripts/deploy_check.sh`, which waits for the scheduler's heartbeat and fails with the container log if the bot restarted or never passed.
