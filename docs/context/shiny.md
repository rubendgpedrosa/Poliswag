# Shiny prediction

Read when touching `!shiny`, the linker, or shiny alert DMs.
Spec: `docs/superpowers/specs/2026-10-10-shiny-prediction-design.md`.
How the formula and the ID route were established, with the phone's crash
history: `/root/unonwhash/CLAUDE-HANDOVER.md`.

## The rule

`modules/shiny_roll.shiny_die(encounter_id, player_id)` is 0 for a spawn that
is shiny for that player at 1/512 (and at 1/64, 1/128, 1/256). `player_id` is
the game's internal ID: 16 hex (PTC) or 21 digits (Google). It is **not** the
ID a friend-code search or GET_PLAYER returns; those differ per viewer.

## Flow

| Step | Where | What |
|---|---|---|
| `!shiny <código>` | `cogs/shiny.py` | Row in `shiny_link`, `pending` |
| Resolve | `scripts/shiny_linker` (host, `shiny-linker.timer`, every 10 min) | Scanner account searches the code, opens a private raid lobby, invites, reads `invited_player_ids` → `linked` or `failed` |
| Announce | `modules/shiny_alerts.py` tick | One DM per finished link |
| Alert | same tick | New `golbat.pokemon` rows rolled for linked users in `SHINY_ALERT_USER_IDS`; roll 0 → DM with Era shiny / Não era |

## Linker rules

- Runs only if: enabled, pending codes, 60+ min since the last run, a level 1
  or 3 raid with 15+ min left, an idle worker, a listed account not scanning,
  game and Aegis running, game version validated. `--check` prints them.
- Logs in first, then attaches the hook with `dragonite` paused (~5 s), and
  pauses again to remove it. Never kills the injector with the hook attached.
- Breaker: a hook error, a hook that did not leave, or a game restart during
  a run sets `poliswag.shiny_linker_enabled = 0` and alerts the owner. Turn it
  back on with `UPDATE poliswag SET shiny_linker_enabled = 1`.
- After a game update: `python -m scripts.shiny_linker --validate`, then add
  the version to `SHINY_LINKER_GAME_VERSIONS` in `.env`.

## Settings (`.env`)

| Variable | Meaning |
|---|---|
| `SHINY_ALERT_USER_IDS` | Discord IDs that get alerts; default `MY_ID` |
| `SHINY_LINKER_ACCOUNTS` | Scanner usernames allowed to open lobbies, in order of preference |
| `SHINY_LINKER_GAME_VERSIONS` | Game versions the hook was validated on |
| `SHINY_LINKER_SELFTEST_CODE`, `SHINY_LINKER_SELFTEST_PLAYER_ID` | An owned scanner account's friend code and known player ID, for `--validate`; must not be one of `SHINY_LINKER_ACCOUNTS` |

## Tables (`poliswag`, migration 017)

`shiny_link` (one row per Discord user; the linker is the only writer of
`linked`/`failed`), `shiny_alert` (dedupe and the player's verdict), and on
`poliswag`: `shiny_linker_enabled`, `shiny_linker_disabled_reason`,
`shiny_linker_last_run_at`, `shiny_linker_alerted_version`,
`shiny_alert_watermark`.

## Known limits

Boosted and event shiny odds are not predicted. Lure, tappable and cell-only
sightings are not alerted. Nothing proves a friend code belongs to its sender.
