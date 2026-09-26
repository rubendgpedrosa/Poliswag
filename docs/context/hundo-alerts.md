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
- **Tick** (every minute, `cogs/scheduled.py`): cleanup (one DELETE of managed rows of anyone not active, stopped, or missing a human) → confirmations → re-read → per active player: create the Poracle `discord:user` if missing (never restart a stopped one), rebuild rules in one transaction when any managed field differs → cleanup again → an immediate `/api/reload` after cleanup and a final reload after rebuilding if needed, retried until it succeeds (Poracle also reloads every 60s).
- **Skip unchanged:** a player is rebuilt only when `_signature` moves (revision, areas, costumes, `hundo_ticks`/`hundo_ticked_at`/`hundo_tiles_fingerprint` marker from `_PLAYERS_SQL`, Poracle profile, masterfile load) or after a cleanup deleted rows, a failure, or `FULL_RESYNC_SECONDS` (1h).
- **Forms:** ordinary tile → masterfile `defaultFormId` (Poracle's form 0 is a wildcard); no safe form (Ogerpon) → tile skipped, never widened.
- **Cleanup SQL needs `COLLATE utf8mb4_unicode_ci`** on the `CAST(discord_id AS CHAR)` join: pymysql's connection collation clashes with `monsters.id` (error 1267). Caught only by the real-SQL tests.
- **Tests:** unit `tests/modules/test_hundo_alerts.py`; real SQL `tests/integration/test_hundo_alerts_sql.py` on a throwaway MariaDB (run instructions in its docstring; gated on `HUNDO_SQL_TEST_PORT`).
- **Rollback:** switch players off with fresh revisions (cleanup removes their rules), or delete `template = 'pokedex-100iv'` rows and `POST /api/reload`.

- **DM presentation (2026-09-25):** `confirmation_embed` sends the existing revision-specific settings copy as a compact title/description/footer embed. Spawn DTS `pokedex-100iv` inherits the standard channel monster layout (`@include mon.txt`), with a missing-from-Pokédex line and settings footer. Poracle `discord.upload_embed_images = true` attaches maps to Discord instead of relying on Discord fetching external tile URLs. This delivery setting applies to all Poracle Discord image embeds.

- **Failure hardening (2026-09-25):** confirmed/refused Discord outcomes are retained in memory when their conditional DB write fails, so the next tick retries the write without sending the DM again. New revisions supersede old outcomes. Missing Poracle humans invalidate the sync cache before recreation. Collection signatures include a 64-bit SHA-256-derived tile fingerprint to catch same-second swaps with unchanged counts/timestamps; hourly reconciliation remains the backstop. API timeouts become logged `PoracleError`s, including useful text for empty timeout exceptions.
- **Limits:** confirmation delivery and its database acknowledgement cannot be atomic; a crash in between can still duplicate a confirmation. The site’s confirmed state reflects the settings DM, not current Poracle delivery health. A stopped/disabled human is intentionally never restarted automatically. Rules and Poracle’s cached state converge on scheduled ticks/reloads; already queued alerts may still arrive after an opt-out. See [audit](hundo-alerts-audit-2026-09-25.md).

## Durable recovery and site health (2026-09-25)

Migration `016_hundo_delivery_recovery.sql` (site `db/010`) adds `hundo_confirmation`
and revision-scoped health columns on `trade_player`. `modules/hundo_confirmation.py`
claims a revision in the database before sending, with a stable nonce and Discord's
`enforce_nonce`. Only the claim owner sends. Sent/refused outcomes survive restarts.
A sending row older than 120 seconds is reconciled against up to 200 Discord messages
since the attempt. A matching bot-authored nonce acknowledges the original message.
An unprovable outcome remains uncertain: there is no automatic resend. The site
explains this and offers `Pedir nova confirmação`, whose atomic UPDATE creates one
new revision only while the old uncertain/refused revision remains current.

The worker records `ready`, `stopped`, `error`, `unavailable`, or `pending` for the
current revision after cleanup/reload and a bounded Poracle health check. The site
shows `unknown` after 180 seconds without a fresh check. Active settings continue
polling every 30 seconds; pending confirmations poll at 5 seconds initially. Failed
polls immediately remove the healthy indication. A stopped destination stays stopped.
`ready` means rules synchronized and service reachable, not a guarantee Discord
will accept the next spawn DM. No UI state claims exactly-once delivery.

Verified with durable restart/fault tests, real SQL atomic claims and retries,
health freshness/guard tests, site action/component tests and the production build.
No test DMs or subscription changes were needed for deployment.
- **Stats:** `!stats` shows `100IV por DM: N ativos (Leiria · Marinha)` plus waiting/DMs-closed/unhealthy counts when non-zero — `TradeStats.collect_hundo` → `summarize_hundo`, counted with `is_active`/`collects_hundo` so it matches the worker.
