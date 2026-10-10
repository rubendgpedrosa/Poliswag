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
wrong. The roll, its 24-bit cutoff, the UTF-8 ID and which spawns are
rolled (wild or encountered; never Ditto, a disguised spawn or a nearby-only
sighting) follow Golbat PR #423, whose test vectors are in our tests. Event odds per species live in `shiny_rate` (pokemon_id, rate, ends_at,
source); the site's map uses them, the DMs do not yet (base odds only).

## Odds that set themselves (`modules/shiny_odds.py`, every tick)

| What | How |
|---|---|
| Event odds | Per species, two counts of the scanner's own encounters: the last 30 min of `golbat.pokemon`, and today's `golbat.pokemon_shiny_stats` (UTC day). `estimate` picks the likeliest of the game's odds (512, 256, 128, 64, 25, 10) and takes it only if the count is 1,000 times likelier under it than under 1/512 and rests on 5+ shinies. The live count decides when it can; the day's count otherwise |
| When it ends | An `auto` row is deleted and rewritten every tick (`ends_at` 15 min ahead, so a stopped bot leaves nothing standing). The day's count is ignored once the species is seen at under a quarter of the day's own pace (`collapsed`): a Community Day's odds go about 30 min after it ends |
| By hand | A `manual` row wins while it runs; expired manual rows are deleted by the same tick |
| Never shiny | Finished UTC days of Golbat's counts (it keeps a week) are added to `shiny_species`; `poliswag.shiny_species_through` is the last day added. A species and form with 5,000+ encounters and no shiny, today included, is `never_shiny`: no DM, no pin on the site's map. One shiny unmarks it |

Checked on 2026-10-10 against the scanner's week (3,879 species-days): it
finds Zorua's Community Day (1/25) and the Slugma and Smoliv event of 3 to 5
October (1/128), and nothing else. Limits: a slow boost on a species that
stays common is found again each morning only after a few thousand
encounters, and lasts to midnight UTC even if it ended earlier; odds are per
species, not per form or costume.

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
| Alert | same tick | New `golbat.pokemon` rows rolled for linked players who have their own channel (`!shiny canal`, the standard way since 2026-10-10) or are in `SHINY_ALERT_USER_IDS` (DM); roll 0 → a Poracle alert there |

## Linker rules

- Runs only if: enabled, pending codes, 20+ min since the last run, a level 1
  or 3 raid with 15+ min left, a worker (idle, or one it can borrow), a listed account not scanning,
  game and Aegis running, game version validated. `--check` prints them.
- One run works through the queue (up to 50 codes) on **one worker**: a
  lobby of up to 10 codes at a time, the next free account from
  `SHINY_LINKER_ACCOUNTS` (shuffled each run) logged in on the same session
  for each lobby, the hook attached once. No new lobby starts 6 min after the
  hook went in (it removes itself at 10); codes left over wait, unpenalised.
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
  the version to `SHINY_LINKER_GAME_VERSIONS` in `.env`. It resolves the test
  account twice, a lobby and an account each, so the account switch is proven
  too (passed on 0.431.0, 2026-10-10: 22 s, scanner undisturbed).

## Settings (`.env`)

| Variable | Meaning |
|---|---|
| `SHINY_ALERT_USER_IDS` | Discord IDs that get alerts; default `MY_ID` |
| `SHINY_LINKER_ACCOUNTS` | Scanner usernames allowed to open lobbies; a run rotates through the ones not scanning |
| `SHINY_LINKER_GAME_VERSIONS` | Game versions the hook was validated on |
| `SHINY_LINKER_SELFTEST_CODE`, `SHINY_LINKER_SELFTEST_PLAYER_ID` | An owned scanner account's friend code and known player ID, for `--validate`; must not be one of `SHINY_LINKER_ACCOUNTS` |

## Tables (`poliswag`, migration 017)

`shiny_link` (one row per Discord user; the linker is the only writer of
`linked`/`failed`; `alert_leiria` / `alert_marinha` say where the player plays,
Leiria only by default (migration 018), set by SQL for now; meant to follow the player's community profile once the site has the setting), `shiny_alert` (dedupe and the player's verdict), and on
`poliswag`: `shiny_linker_enabled`, `shiny_linker_disabled_reason`,
`shiny_linker_last_run_at`, `shiny_linker_alerted_version`,
`shiny_alert_watermark`.

Migrations 019 and 021: `shiny_rate` (pokemon_id, rate, ends_at, source
`manual`/`auto`), `shiny_species` (pokemon_id, form_id, encounters, shinies,
never_shiny) and `poliswag.shiny_species_through`. The site reads both tables.

## How an alert is delivered

Through Poracle, so it looks like every other Poracle alert (map tile, IV, CP,
level, address, Google / Apple buttons). Poliswag builds the spawn as a Golbat
webhook and hands it to Poracle's `/api/test` with the `shiny` monster
template, which is Poracle's standard one with a ✨ before the title. The template lives in `/root/poracleng/config/dts.json` (not in git;
a copy is in `templates/poracle/shiny-monster.json`; after editing it,
`POST /api/dts/reload`, not `/api/reload`). Poracle and Poliswag are the same
Discord bot, so the alert arrives in the same DM. If Poracle refuses, the
bot's own plainer embed goes out instead.

`!shiny canal [nome]` (owner) creates a private channel for a linked player
under the "Shinies" category: only that player sees it, read-only. Their map
link is pinned there and, from then on, their alerts go to the channel
(`shiny_link.channel_id`) instead of a DM. Having a channel is what switches
alerts on for a player; nobody outside `SHINY_ALERT_USER_IDS` is ever DMed,
not even when their channel has been deleted (the alert is logged and dropped).
Server administrators see every such channel.

## The site's map

`!shiny mapa [nome]` (owner) DMs a private link, `pogoleiria.pt/shiny/<token>`:
that player's predicted shinies on a live map. The token's SHA-256 is in
`shiny_link.map_token_hash`. The page is the landing app's
(`/root/PoGoLeiria/docs/context/shiny-map.md`).

## Known limits

The DMs use base odds only (event odds would mean 100+ DMs an hour on a Community Day). Lure, tappable and nearby-only sightings, Ditto and disguised spawns are not alerted. Nothing proves a friend code belongs to its sender.
