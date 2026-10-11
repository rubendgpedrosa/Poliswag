# Experimental Special Background predictions

Implemented 11 October 2026 in the existing personal shiny stream. This work
does not upgrade Golbat, restart services, apply migrations or activate an event.
Claude is independently redesigning the map popovers; preserve that work.

`personal_draws`/`personalDraws` hash the eight little-endian encounter bytes
followed by the exact verified internal player ID text with SHA-1. The first
eight digest bytes, little endian, seed Java Random. The first nextFloat remains
the shiny candidate; the second nextFloat is the experimental background
candidate. A background qualifies when that second fraction is below an
explicitly configured probability. Changing shiny odds never moves the draw.

The arithmetic and ID-preserving synthetic vectors follow
[Golbat PR 423](https://github.com/UnownHash/Golbat/pull/423), inspected at
`40c1a2f2d52434d4570140e1e127db5cc523ff4e`. Its API `background_for` filters
encounters for supplied IDs; `annotate_for` retains normal encounters and adds
matching `shiny_for`/`background_for` ID arrays. Those IDs belong server-side.
The implementation here computes locally in the existing bot and map server;
it does not send account IDs to the browser or require the unmerged PR and its
in-process-derivation dependency. The map sends prediction flags and trainer
names. Its event rules include geography, which the inspected MR did not.

Evidence supplied by the user: 319 Seattle wild Zorua encounters, 8 backgrounds;
44 prospectively recorded Hawaii encounters, 2 backgrounds; reported zero
classification errors at 1/25 with no threshold change for Hawaii. These are
clustered observations, not independent population-rate trials. Referenced raw
audits and original seed-proof files were not available in this workspace, so
this is reported evidence, not our independent reproduction. Arithmetic tests
and synthetic fixture IDs validate the implementation, not the game's policy.
Height and weight predictions are outside this implementation.

## Event eligibility

`poliswag.background_prediction_rule` stores enabled, raw species/form/costume,
source (`wild` only), explicit probability, UTC epoch start/end and a mandatory
latitude/longitude rectangle. Start is inclusive; end exclusive. Use the raw
scanner form, not the normalized Pokédex form. Wild provenance requires a
nonzero spawn ID, no PokéStop ID and scanner state `wild` or `encounter`.
Unknown/lure/research/raid sources do not inherit the wild rule. Ditto,
disguised records, nearby-only sightings and unknown costumes remain excluded
by the existing spawn queries. Split regions crossing the date line.

No rules are seeded. There is no global 4% default or background rate inferred
from shiny statistics. 4% is only the supplied wild Zorua event hypothesis;
other species/events/rates need their own prospective validation. Conflicting
overlapping probabilities suppress the forecast. Same-rate matches select a
deterministic rule ID. Rules are refreshed each bot tick/map read. Cached map
backgrounds expire at the rule end on the existing 20-second clock, including
when refresh fails; joint predictions retain their shiny component.

## User behavior

The map supports Tudo/Shinies/Fundos filters and a Fundo pin badge. Background
popovers say “previsto” and “experimental”; background-only encounters use
ordinary art and do not show shiny odds. Combined maps carry independent
`shinyTrainers` and `backgroundTrainers` lists, so different trainers' results
are not presented as a shared joint result.

Discord preference `shiny_notify_background` defaults off and is independent
of shiny ownership. Existing access, map/Discord master switches, region
preferences, IV wait, expiry and deduplication remain in force. Preference and
event expiry are checked again immediately before delivery. A joint result is
one alert. Since 2026-10-11 (owner's choice) background alerts go through
Poracle like shiny ones, with their own monster templates so a non-shiny is
never labelled one: `background` (🖼️ before the title) and `shiny-background`
(✨🖼️), each with a "🖼️ **Fundo especial previsto** (experimental)" line above
the standard body. The side bar follows the map's pins: gold (`F2CD5C`) for
`shiny` and `shiny-background`, sky blue (`5AA9E6`) for `background`
(`SHINY_COLOR` / `BACKGROUND_COLOR` for the fallback embed). `poracle_template(spawn)` in
`modules/shiny_alerts.py` picks one. They live in
`/root/poracleng/config/dts.json` (not in git; copies in `templates/poracle/`,
which a test holds to the shiny template plus those three differences; after
editing, `POST /api/dts/reload`). If Poracle refuses, the bot's own embed goes
out with the same emoji and colour. The event's label is only in that
fallback: Poracle's template knows nothing of the rule.

## Deployment handoff, after Claude finishes

1. Review/apply `migrations/029_add_background_predictions.sql` once. The site
   mirrors it as `apps/landing/db/pokedex/018_background_predictions.sql`.
   Both explicitly create the shared table in `poliswag` and the preference
   in `pogoleiria.trade_player`. Applying either is sufficient on the shared
   databases. Apply before deploying code that selects the new table/column.
2. Generate and review a rule with `python3 scripts/background_rule.py`.
   Required options: `--rule-id`, `--label`, `--pokemon-id`, `--form-id`,
   `--probability`, offset-bearing `--starts-at`/`--ends-at`, and
   `--bounds SOUTH NORTH WEST EAST`; optional `--costume-id` defaults 0.
   Optional `--image NAME.png` (migration 030) is the event's background, a
   file name from PokeMiners/pogo_assets `Images/LocationCards`: the map draws
   it behind the Pokémon, and a plain landscape without it.
   JSON is the default; `--sql` prints an INSERT. This tool never connects to
   a database. Rules default disabled; `--enabled` only changes the generated
   output. Use actual verified eligibility, local window converted with the
   correct offset, and reviewed geography. Do not reuse expired research
   windows as current rules. Duplicate IDs fail rather than overwrite a rule.
3. Deploy the bot and site in coordination with the popover redesign. No
   Golbat image/config change is necessary. Enable the user's background
   notification preference and only a reviewed, eligible rule.
4. For a new event, record predictions before encounters and compare the
   complete positive/negative outcomes against the frozen threshold and rule.
   Unknown background eligibility or probability produces no forecast.

Removing/disabling the rules stops new background predictions. The existing
shiny dedup record is per player/encounter, so one previously delivered shiny
will not later produce a second background notification for the same spawn.

Validation is in `tests/modules/test_background_prediction.py`,
`tests/scripts/test_background_rule.py`, the existing shiny tests, and the
site's `background-model.test.ts`/`background-snapshot.test.ts` with settings
and model regressions. No game encounters or outbound alerts were generated
by this implementation's tests.
