# Databases

**`poliswag`** — single-row config table:
- `last_scanned_date DATE` — last quest scan date
- `scanned TINYINT` — 1=finished, 0=scanning
- `last_weekly_digest_date DATE` — guards digest deduplication

**`tracked_quest_reward`** — `target VARCHAR`, `creator VARCHAR`, `createddate DATETIME`

**`excluded_event_type`** — `type VARCHAR`

**`event`** — event calendar rows (type, name, start/end timestamps, etc.)

**`account_lure`** — `username VARCHAR(50)` PK, `nb_lures INT DEFAULT 12`. Per-account lure budget for `!lures`/`!uselure` (managed by `lure_manager.py`). Migration `004`.

**`polisnatch_job`** — Poliswag's ledger of the jobs it submitted to PoliSnatch: `job_id` PK, `kind` (`link`/`lure`), `state`, `discord_id`, the `channel_id`/`message_id` its outcome edits, `stop_key`, `lat`/`lon`/`nearest`, the outcome columns and `submitted_at`/`updated_at`/`notified_at`. Migration `038`, written by `modules/polisnatch_store.py`; the server owns the job itself, this table only survives a restart and drives the startup sweep.

**Scanner DB** (`DB_SCANNER_NAME`) — read-only. Key table:

`pokestop` — lat/lon, quest fields (`quest_type`, `quest_title`, `quest_target`, `quest_reward_type`, `quest_item_id`, `quest_pokemon_id`, `quest_reward_amount`) plus `alternative_quest_*` mirrors for AR quests (generated columns). Area split by `lon ≤ −8.9` for Marinha Grande.

**Poracle DB** (`DatabaseConnector("poracle")`) — `humans` table: `id`, `name`, `type='discord:channel'`, `enabled`.

**Dragonite DB** (`DatabaseConnector(DB_DRAGONITE)`, default `dragonite`) — read-only. `account` table: `username`, `password`, health flags (`banned`, `suspended`, `warn`, `invalid`, `auth_banned`), and scheduler timestamps (`last_selected`, `last_released`, `next_available_time`). Read by `lure_manager.py` to find free+healthy accounts.
