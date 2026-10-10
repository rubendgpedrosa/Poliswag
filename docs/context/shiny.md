# Shiny prediction

Read when touching `!shiny`, the linker, or shiny alert DMs.
Spec: `docs/superpowers/specs/2026-10-10-shiny-prediction-design.md`.
How the formula and the ID route were established, with the phone's crash
history: `/root/unonwhash/CLAUDE-HANDOVER.md`.

## The rule

`modules/shiny_roll.shiny_die(encounter_id, player_id)` is 0 for a spawn that
is shiny for that player at 1/512. In general a spawn is shiny when
`shiny_draw(...)`, the first draw of the game's Random as a fraction, is under
1/odds (`is_shiny_at`). That was measured on 2026-10-10 with a proven scanner
account on a Zorua Community Day (161 encounters, the 7 shinies had the 7
lowest draws); `next_int(odds) == 0`, the community's rule for event odds, is
wrong. Event odds per species live in `shiny_rate` (pokemon_id, rate, ends_at);
the site's map uses them, the DMs do not yet (base odds only).

`player_id` is the game's internal ID. Shapes seen: 16 hex (PTC scanner
accounts), 24 hex (the owner's account), 21 digits (a Google sign-up). It is
**not** the ID a friend-code search or GET_PLAYER returns; those differ per
viewer.

## Flow

| Step | Where | What |
|---|---|---|
| `!shiny <código>` | `cogs/shiny.py` | Row in `shiny_link`, `pending`. **Owner only during the pilot** (`@owner_only()` on the command; remove it to open to members) |
| Resolve | `scripts/shiny_linker` (host, `shiny-linker.timer`, every 10 min) | Scanner account searches the code, opens a private raid lobby, invites, reads `invited_player_ids` → `linked` or `failed` |
| Announce | `modules/shiny_alerts.py` tick | One DM per finished link, **only to users in `SHINY_ALERT_USER_IDS`**; other links are stamped announced without a DM |
| Alert | same tick | New `golbat.pokemon` rows rolled for linked users in `SHINY_ALERT_USER_IDS`; roll 0 → DM with Era shiny / Não era |

## Linker rules

- Runs only if: enabled, pending codes, 60+ min since the last run, a level 1
  or 3 raid with 15+ min left, a worker (idle, or one it can borrow), a listed account not scanning,
  game and Aegis running, game version validated. `--check` prints them.
- With every worker scanning it borrows one: `rotom.release_one` disconnects the
  last controller of the biggest area, the linker takes the freed worker, and
  Dragonite gets it back when the run's session closes (30-60 s). Losing the
  race for the worker three times ends the run without penalising the codes.
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
`linked`/`failed`; `alert_leiria` / `alert_marinha` say where the player plays,
both on by default, set by SQL for now, migration 018), `shiny_alert` (dedupe and the player's verdict), and on
`poliswag`: `shiny_linker_enabled`, `shiny_linker_disabled_reason`,
`shiny_linker_last_run_at`, `shiny_linker_alerted_version`,
`shiny_alert_watermark`.

## The site's map

`!shiny mapa [nome]` (owner) DMs a private link, `pogoleiria.pt/shiny/<token>`:
that player's predicted shinies on a live map. The token's SHA-256 is in
`shiny_link.map_token_hash`. The page is the landing app's
(`/root/PoGoLeiria/docs/context/shiny-map.md`).

## Known limits

The DMs use base odds only (event odds would mean 100+ DMs an hour on a Community Day). Lure, tappable and cell-only
sightings are not alerted. Nothing proves a friend code belongs to its sender.
