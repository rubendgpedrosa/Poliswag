# Databases

**`poliswag`** — single-row config table:
- `last_scanned_date DATE` — last quest scan date
- `scanned TINYINT` — 1=finished, 0=scanning
- `last_weekly_digest_date DATE` — guards digest deduplication

**`tracked_quest_reward`** — `target VARCHAR`, `creator VARCHAR`, `createddate DATETIME`

**`excluded_event_type`** — `type VARCHAR`

**`event`** — event calendar rows (type, name, start/end timestamps, etc.)

**`scan_area`** — the scanned-area registry (migration `040`): `area_key VARCHAR(32)` PK, `label`, `dragonite_area` (the Dragonite area whose geofence is the fence), `poracle_area` (geofence name), `alert_role` (Discord role), `sort_order`, `active` (since 2026-11-21 Dragonite decides it: `scan_areas.merge_registry` makes an *enabled* `dragonite.area` active and a disabled or missing one inactive, whatever the row says). Seed: `leiria`, `marinha`, `batalha`, `portodemos`, `moita`. A row only supplies the label, role, Poracle name and order; an enabled Dragonite area with no row gets defaults from its name (key and Poracle name lowercase, role `Alertas<Name>`, label split on capitals, next `sort_order`), so adding an area is just its Dragonite area. The per-player SET columns below still hold the five seeded keys only, so a derived area is not a selectable choice there until a migration adds it. Readers never name an area: they go through `modules/scan_areas.py`.

**`shiny_link.alert_areas`** — `SET('leiria','marinha','batalha','portodemos','moita')`, migration `040`, backfilled from `alert_leiria`/`alert_marinha`; readers use this column; the bot no longer writes the two old flags, which stay only until the site stops writing them and a migration drops them. **`pogoleiria.trade_player.shiny_areas`** (the site's own selection, migration `022`) is widened to the same five keys by `040`, so saving a newly activated area is not truncated; it is not a bot reader.

**`account_lure`**, **`lure_lock`**, **`polisnatch_job`** — left from the lure commands removed on 2026-11-11 (migrations `004`, `038` and the lock table's); nothing reads or writes them.


**Scanner DB** (`DB_SCANNER_NAME`) — read-only. Key table:

`pokestop` — lat/lon, quest fields (`quest_type`, `quest_title`, `quest_target`, `quest_reward_type`, `quest_item_id`, `quest_pokemon_id`, `quest_reward_amount`) plus `alternative_quest_*` mirrors for AR quests (generated columns). A stop's area is decided by `modules/scan_areas.py` (the active registry fences), never by a longitude line.

**Poracle DB** — `poliswag.poracle_db`, used by `hundo_alerts.py` in units of work (its own rules and humans rows). The channel rows that `!notify` managed are Poracle's to manage now.

**Dragonite DB** — no longer read directly (it was, for the lure commands); account figures come from Dragonite's HTTP API (`account_monitor.py`).
