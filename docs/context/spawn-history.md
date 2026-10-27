# Unfiltered spawn history

`cogs/spawn_history.py` records the scanner's raw Pokémon independently every
30 seconds, using `modules/spawn_history.py` and a separate bounded database
connection. It waits for Discord readiness but does not depend on notification
delivery, linked trainers, collection entries, selected areas or IV preferences.
Scanner tables and account settings are never modified.

Migration 031 adds three tables in `poliswag`: `spawn_history` (one row per
encounter), `spawn_history_state` (persistent start/checkpoint) and
`spawn_history_gap` (intervals between successful polls longer than 90 seconds).
Capture, cleanup, gap records and the checkpoint commit together. Failed
transactions roll back and retry next poll; restart does not reset the start
time. Capture has a 10-second statement limit and five-second lock limit. Its
task is separate from the main scheduler so unrelated slow steps do not delay it.

## Stored attributes

- Encounter ID as an exact string, preserving unsigned 64-bit values in JSON.
- First scanner sighting, first/last capture and expiry, as UTC epoch seconds;
  whether the expiry is verified.
- Species, raw scanner form/costume IDs and gender.
- Latitude/longitude, observation type, displayed species and Ditto flag.
- Attack/defence/stamina IVs (NULL means unknown), CP, level and weather.
- Shiny eligibility, odds and their source (`base`, `auto` or `manual`) at first
  capture, with the prediction-configuration timestamp. These snapshots stay
  fixed on subsequent polls unless the species/form/costume is corrected. Late
  IVs and scanner metadata update the same encounter without duplicating it.

All raw species and observation types are kept, including ineligible species,
nearby sightings and unknown IVs, so a later analysis can inspect the inputs.
No passwords, trainer player IDs or account preferences enter the archive.
Rows with first sightings older than 48 hours are removed on each successful
poll; the gap table has the same retention. The initial seed includes whatever
Golbat still retains, but does not imply complete coverage before capture starts.

## Owner commands

- `!spawnhistory [hours]`: capture health, row count and coverage (default 24 h).
- `!spawnhistory export [hours]`: streamed, gzip-compressed JSON, including
  `schema_version`, coverage metadata and raw `spawns`. Maximum 8 MiB compressed;
  request fewer hours if the export exceeds it. The file goes to the invoking
  channel or DM. The export contains all stored observation types.
- `!spawnhistory leaderboard [hours]`: JSON with an unfiltered per-trainer
  leaderboard computed from the archive. Both commands accept 1–48 hours.
- `!spawnhistory image`: renders the 24-hour leaderboard and DMs only `MY_ID`.

## Daily owner leaderboard

`SHINY_LEADERBOARD_ENABLED=1` enables a separate, one-minute task in the
SpawnHistory cog. At or after 09:00 Europe/Lisbon, `modules/shiny_leaderboard.py`
calculates the preceding 24 hours, renders a PNG with the bundled PoGoLeiria
mark and fonts, and sends it directly through Poliswag to `MY_ID`. No agent,
LLM or image-generation API is invoked. Discord avatars come from community
profiles; failed downloads fall back to initials.

`poliswag.last_shiny_leaderboard_date` persists successful delivery across
restarts (migration 032). A failed send retries on the next tick; a late
restart catches up once for that local date. Manual image requests do not
change the daily delivery date. The automatic report uses the same aggregate
as the JSON command: no personal zone, collection, IV or notification filters.
The PNG shows only the first 15 trainers (podium plus 12 rows), labelled as a
top 15 when more trainers exist. Totals, trainer count and general averages
still cover every trainer in the aggregate; the JSON report remains complete.
Incomplete coverage leaves the JSON full-window averages unavailable. The image
shows recorded totals divided by the requested hours, marked with an asterisk
and a warning that missing history may underestimate the rate. The overall
image rate also divides by trainer count. Stale collection is identified in the DM. Reports are predictions, not catches.

For owner agent requests, the private `poliswag_db.spawn_history_report` MCP tool
provides the same aggregate report, without transmitting raw player IDs or
requiring hundreds of paginated SQL reads. Its `hours` argument defaults to 24.
Claude/Codex are instructed to use this instead of the filtered `shiny_alert`
table. The tool is served only for a verified owner's running job, like the
existing read-only query bridge; public jobs do not receive it.

## Report semantics

Every currently approved, linked, active map user is tested against the same
spawn pool, including those with Discord notifications off. Collection, area
and IV settings are ignored. Game eligibility still applies: released/confirmed
variants, wild/encounter observations, no Ditto/disguises/unknown costumes.

The RNG is `modules.shiny_roll`'s SHA-1/Java nextFloat rule. `predicted_shinies`
uses the recorded odds; boosts from `shiny_rate` are estimates inferred from
scanner encounters, not guaranteed game rates. `base_512_shinies` supplies a
comparable base-rate total. Rates are captured when a spawn is first archived,
not retrospectively read from today's live settings. These are predictions,
not confirmed catches or delivered alerts.

Windows are `[last_capture - hours, last_capture)`, UTC. Hourly averages remain
NULL until capture has covered the whole requested window without a recorded
gap. Stale collectors are flagged separately and the report always states its
fixed end time. Normal 30-second polling can miss an exceptionally short-lived
record deleted between polls; coverage means continuous polling of scanner
observations, not a guarantee of seeing every Pokémon in the game. A first full
24-hour window needs 24 hours of continuous collection after deployment.

Up to 100 trainers are supported per aggregate report; exceeding the cap returns
an explicit error rather than truncating the leaderboard. File exports are
streamed to a spooled temporary file, avoiding a large in-memory JSON document.
