# Pokédex 100IV DMs (`modules/hundo_alerts.py`)

Read when touching the 100IV alerts, their Poracle rules, or the site's switch.
Spec/plan: `docs/superpowers/{specs,plans}/2026-09-24-pokedex-100iv-alerts*.md` (plan ends with the pilot record).

| Piece | Where |
|---|---|
| Settings | `pogoleiria.trade_player`: `hundo_dms`, `hundo_areas` SET(leiria,marinha), `hundo_settings_revision`/`_at` (site writes), `hundo_confirmed_revision`/`_at`, `hundo_dm_refused_revision`/`_at` (Poliswag writes). Migration 014 = site `db/008`. |
| Site switch | Pokédex → Perfil e outras opções (`apps/pokedex/components/hundo-settings.tsx`), only for ids in the site's `HUNDO_DM_TESTERS` until release. |
| Rules | `poracle.monsters` rows with `template = 'pokedex-100iv'`, 100/100 IV, one per missing tile, `override_areas` from the setting, in the human's `current_profile_no`. Nothing else in Poracle is touched. |
| DM look | DTS entry `pokedex-100iv` in `/root/poracleng/config/dts.json` (backups `dts.json.bak-100iv-*`). Footer points at pogoleiria.pt/pokedex. |

- **Revision contract.** Every site change bumps `hundo_settings_revision` in the same UPDATE (identical save = no row). Poliswag DMs one confirmation per revision and records delivered/refused only for that exact revision (`_record`'s conditional UPDATE). State is read from revision numbers, never timestamps.
- **Active** = switch on, `collects_hundo` (member + collecting hundo), current revision confirmed. `may_confirm`: an "off" confirms for any member, an "on" only for a collector (the site refuses the rest).
- **Tick** (every minute, `cogs/scheduled.py`): cleanup (one DELETE of managed rows of anyone not active, stopped, or missing a human) → confirmations → re-read → per active player: create the Poracle `discord:user` if missing (never restart a stopped one), rebuild rules in one transaction when any managed field differs → cleanup again → one `/api/reload` if anything changed, retried until it succeeds (Poracle also reloads every 60s).
- **Skip unchanged:** a player is rebuilt only when `_signature` moves (revision, areas, costumes, `hundo_ticks`/`hundo_ticked_at` marker from `_PLAYERS_SQL`, Poracle profile, masterfile load) or after a cleanup deleted rows, a failure, or `FULL_RESYNC_SECONDS` (1h).
- **Forms:** ordinary tile → masterfile `defaultFormId` (Poracle's form 0 is a wildcard); no safe form (Ogerpon) → tile skipped, never widened.
- **Cleanup SQL needs `COLLATE utf8mb4_unicode_ci`** on the `CAST(discord_id AS CHAR)` join: pymysql's connection collation clashes with `monsters.id` (error 1267). Caught only by the real-SQL tests.
- **Tests:** unit `tests/modules/test_hundo_alerts.py`; real SQL `tests/integration/test_hundo_alerts_sql.py` on a throwaway MariaDB (run instructions in its docstring; gated on `HUNDO_SQL_TEST_PORT`).
- **Rollback:** switch players off with fresh revisions (cleanup removes their rules), or delete `template = 'pokedex-100iv'` rows and `POST /api/reload`.
