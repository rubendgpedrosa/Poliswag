# Shiny prediction: linking and owner alerts — design

**Status:** design approved in conversation 2026-10-10; not implemented. This
document specifies future work; editing it applies no migration, deploys no
code, touches no phone and sends no message.

## Goal and scope

A wild spawn is shiny for a player when
`SHA-1(encounter_id as 8 little-endian bytes + player_id text)` seeds a Java
`Random` whose `nextInt(512)` is 0. The player ID is the entry a raid-lobby
creator sees in `LobbyProto.invited_player_ids` after inviting that player
(16 lowercase hex, or 21 decimal digits for Google sign-ups). Confirmed on
2026-10-10 against three scanner accounts: 3 of 3 recorded shinies roll 0,
0 of 201 recorded non-shinies do. Evidence and procedure:
`/root/unonwhash/CLAUDE-HANDOVER.md`.

This design covers three pieces that share only database tables:

1. **Collect** — any server member gives Poliswag a friend code.
   **Owner only at first** (decided at go-live, 2026-10-10): the command
   carries `owner_only` until the pilot is over.
2. **Link** — a host script has a scanner account resolve the code to the
   player ID.
3. **Alert** — Poliswag DMs a linked player when a live spawn rolls 0.
   **Owner only at first.**

Out of scope: a map filter, alerts for other players, boosted or event shiny
rates, more than one lobby per linker run, any Golbat or Dragonite change.

## 1. Collect (Poliswag cog `cogs/shiny.py`)

| Command | Effect |
|---|---|
| `!shiny <código>` | Stores the code as `pending` for the author. Accepts 12 digits with spaces or hyphens. Re-sending replaces a pending or failed code and re-queues a linked one. |
| `!shiny` | Shows the author's status: pending, linked (trainer name), or failed (reason). |
| `!shiny remover` | Deletes the author's row and their alert history. |

- Replies are in Portuguese, like the rest of the bot. The reply to a new code
  says it resolves when a raid is live (normally within the hour) and that an
  in-game raid invite may appear and can be ignored.
- Works in the server and in DMs. The cog joins `DM_EXEMPT_COGS` and carries
  the same guild-membership check as the Pokédex cog.
- Nothing proves a code belongs to its sender. The resolution DM names the
  trainer the code matched, so a wrong code is visible to the person who sent it.

Each tick Poliswag also announces finished links: a row that is `linked` or
`failed` with `announced_at IS NULL` gets one DM (trainer name, or the reason
and how to retry), then `announced_at` is set. A refused DM still sets it.

## 2. Link (host script `scripts/shiny-linker`)

Runs on the host, outside the bot, because it drives the phone and must not
share the bot's 60-second tick. Source in `scripts/shiny_linker/` (protobuf
helpers, Rotom controller session, hook attach/detach, lobby flow, queue) plus
`native-method-redirect.js`; these are lifted from the proven tools in
`/root/unonwhash/tools/` (`raid-lobby-id.py`, `raid-id-session.py`), which stay
where they are as investigation records. Started every 10 minutes by
`shiny-linker.timer`; unit files live beside the script. Dependencies
(`websocket-client`, `pymysql`) go in `/opt/shiny-linker/venv`. Database and
alert settings come from Poliswag's `.env`.

### Guards — a run does nothing unless all hold

- `shiny_linker_enabled = 1`.
- At least one `pending` row, and the last run started 60 or more minutes ago.
- A raid of level 1 or 3, not EX, with 15 or more minutes left, in `golbat.gym`.
- Rotom reports the device connected and a worker to use: an idle one, or,
  when all are scanning, one scanning controller it can disconnect for the
  run (owner's decision at go-live, 2026-10-10; Dragonite was using all 16).
- An account from `SHINY_LINKER_ACCOUNTS` is healthy in `dragonite.account`
  and not attached to any Rotom controller.
- The installed game version is listed in `SHINY_LINKER_GAME_VERSIONS`.
- The game and the Aegis mapping process are both running.

`shiny-linker --check` prints each guard's state and changes nothing.

### One run

1. Record `shiny_linker_last_run_at` and the game PID.
2. Take an idle worker; log the linker account in (after re-checking that
   Rotom does not show it in use); confirm `GET_PLAYER` succeeds. Doing this
   before the hook means a failed login never touches the game.
3. Pause `dragonite` (about 5 s), start the injector with the hook, wait for
   its ready line, unpause. The hook only rewrites requests carrying this run's
   random nonce, and only to methods 5012, 159, 160, 815, 1504.
4. For up to 10 pending codes, oldest first: search the code (social 10000) to
   get the trainer name and the ID as this account sees it.
5. Gym info (156), raid details (163), claim the free ticket if offered (815),
   join a private lobby (159) with no `raid_entry_cost`.
6. For each resolved code, one at a time: invite (1504), poll raid details
   until exactly one new `invited_player_ids` entry appears, check its shape,
   and write `linked` with trainer name and player ID, clearing the friend code.
7. Leave the lobby (160). Never send battle start (165) or any purchase.
8. Ask the hook to detach; pause `dragonite`; once the hook logs detached,
   kill the injector; unpause; delete the hook's log.

`dragonite` is always unpaused in a `finally`. The linker never disconnects a
scanning controller while a worker is idle, never restarts the phone and never logs in an account
that Rotom shows in use. Leaving before the battle keeps the free pass.

### Failures

| What happened | Result |
|---|---|
| Code not found by search | Row `failed`, reason `código não encontrado` |
| Invite refused, or the new entry is not 16 hex / 21 digits | Row `failed`, reason recorded |
| Run-level error before any invite (login, lobby join, no reply in 40 s) | Rows stay `pending`, `attempts + 1`; a row reaching 5 attempts becomes `failed` |
| Game PID changed during the run, hook setup error, or hook not detached | **Breaker:** `shiny_linker_enabled = 0` with a reason, owner alerted through `scripts/notify_owner.py`. Stays off until the owner sets it back. A hook that did not detach is left alone with its injector (it removes itself after 10 minutes); the injector is never killed while the hook is attached |
| Game version not in the validated list | No run; owner alerted once per version |

### Validating a game version

`shiny-linker --validate` is run by hand after a game update. It skips the
version, pending-row and 60-minute guards and performs one run whose only
target is an owned scanner account with a known player ID
(`SHINY_LINKER_SELFTEST_CODE` and `SHINY_LINKER_SELFTEST_PLAYER_ID` in `.env`)
and succeeds only if the lobby returns that same ID. The owner then adds the
version to `SHINY_LINKER_GAME_VERSIONS`.

## 3. Alert (Poliswag `modules/shiny_alerts.py`, one scheduler tick step)

- Recipients: `linked` rows whose `discord_id` is in `SHINY_ALERT_USER_IDS`
  (default: `MY_ID` only). Widening the pilot is a config change.
- Each tick reads `golbat.pokemon` rows with `first_seen_timestamp` at or above
  the watermark and either no expiry or 2 or more minutes left, of `seen_type`
  `wild`, `encounter` or `nearby_stop` (lure, tappable and cell-only sightings
  are left out: the formula is untested on them, or they have no usable
  location). The first tick only sets the watermark.
- `modules/shiny_roll.py` holds the roll. A row alerts when the roll is 0.
- The DM is an embed: Portuguese name from `pokemon_name`, a maps link,
  despawn time in Lisbon time (or "desconhecido"), and a line saying whether
  the scanner has ever seen that species and form shiny
  (`golbat.pokemon_shiny_stats.count > 0`). Roll-0 spawns of species never
  seen shiny still alert; the line tells the reader how much to trust it.
- Two buttons, `Era shiny` and `Não era`, usable only by the recipient. The
  answer is stored and the message edited to show it. Custom IDs carry the
  encounter ID so the buttons survive a bot restart.
- An alert row is inserted before sending, so a spawn is never sent twice.
  A refused DM is logged and not retried.

Expected volume for one recipient: about 2,700 new spawns an hour gives about
5 alerts an hour across Leiria and Marinha Grande.

Known limit: roll 0 is shiny at 1/512 and at 1/64, 1/128 and 1/256 (those
are the same roll shifted). Extra shinies at boosted or event odds are missed.

## Data (migration `017_add_shiny.sql`, `poliswag` database)

`shiny_link` — one row per Discord user.

| Column | Type | Meaning |
|---|---|---|
| `discord_id` | BIGINT UNSIGNED, PK | |
| `discord_name` | VARCHAR(64) | Username when the code was sent |
| `friend_code` | CHAR(12) NULL | Cleared once the row is `linked` |
| `trainer_name` | VARCHAR(32) NULL | From the code search |
| `player_id` | VARCHAR(21) NULL | 16 hex or 21 digits |
| `status` | ENUM('pending','linked','failed') | |
| `fail_reason` | VARCHAR(64) NULL | |
| `attempts` | TINYINT UNSIGNED, default 0 | Run-level retries |
| `requested_at`, `resolved_at`, `announced_at` | DATETIME | |

`shiny_alert` — primary key (`discord_id`, `encounter_id`); `pokemon_id`,
`form_id`, `sent_at`, `verdict` ENUM('shiny','not_shiny') NULL, `verdict_at`.

`poliswag` (single state row) gains `shiny_linker_enabled` (default 1),
`shiny_linker_disabled_reason`, `shiny_linker_last_run_at`,
`shiny_linker_alerted_version` (the unvalidated game version the owner was
last told about), `shiny_alert_watermark`.

## Testing

- **Roll:** vectors generated with the original JavaScript snippet, including
  encounter IDs above 2^53 and a 21-digit player ID.
- **Cog:** code validation, replace/re-queue rules, DM membership check.
- **Alerts:** first tick sends nothing; roll 0 alerts once; non-recipients and
  unlinked rows never alert; verdict buttons reject other users.
- **Linker:** each guard blocks a run by itself; failure table above, against
  a fake Rotom transport; `dragonite` unpaused on every exit path; hook script
  against a mocked sender (allowed methods only, wrong nonce untouched).
- **Live, in order:** `--validate` on the owned account; the owner links their
  own code; the owner answers the first alerts in game. Three `Era shiny`
  answers with no `Não era` on species the scanner has seen shiny is the bar
  for inviting a second tester.

## Risks

- Niantic can stop returning `invited_player_ids` at any time; linking then
  fails per row and existing links keep working.
- The hook has crashed the game twice, both before the pause around attach
  and detach existed; nine cycles since were clean. The breaker limits a
  repeat to one occurrence.
- Linker accounts open and leave raid lobbies, which scanning accounts never
  do. Keeping them to a short configured list contains any account action.
- A player ID is something the game hides. The table is readable by anyone
  with the shared database credentials.

## Documentation to update with the code

`docs/context.md` (cog, module and table rows), a new `docs/context/shiny.md`,
`docs/context/host-ops.md` (the timer and script), and a pointer from
`/root/unonwhash/CLAUDE-HANDOVER.md`.
