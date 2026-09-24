# Pokédex 100IV alerts — design

**Status:** design approved 2026-09-24; revised after implementation-plan review.
Poliswag side first; the site's controls follow. Test on the owner's account
before enabling other players. This document specifies future work; editing
it does not apply migrations, deploy code, or send messages.

## Goal and scope

Send a private Discord alert when a 100IV spawns of a Pokémon the player
has not ticked in the Trades Pokédex. Poracle receives Golbat's webhooks and
sends the spawn alerts; Poliswag maintains the rules once per minute.

- Off by default. Eligible players have `hundo` in `collecting`,
  `left_at IS NULL`, and `hundo_dms = 1`.
- Areas: Leiria, Marinha Grande, or both (default both).
- Each missing tile gets a rule; costumes follow `show_costumes`.
- No daily cap, quiet hours, or bundling added by Poliswag. Poracle's existing
  delivery limits, stops, restrictions, and permissions still apply.
- Manage only `monsters.template = 'pokedex-100iv'`. Do not modify existing
  humans, profiles, or rules using other templates.
- During the pilot, only the owner is enabled by SQL. The later site controls
  are available only to `HUNDO_DM_TESTERS` until the site release.

## Data and settings revisions

Migration `014` adds eight columns to `pogoleiria.trade_player`. The future
Trades migration uses the same re-runnable DDL.

| Column | Default | Writer | Meaning |
|---|---|---|---|
| `hundo_dms` | 0 | site / pilot operator | switch |
| `hundo_areas` | `leiria,marinha` | site / pilot operator | SET of area keys |
| `hundo_settings_revision` | 0 | site / pilot operator | monotonic integer identifying settings or an explicit retry |
| `hundo_settings_at` | NULL | site / pilot operator | audit time of that revision, `DATETIME(6)` |
| `hundo_confirmed_revision` | 0 | Poliswag | revision whose confirmation was delivered |
| `hundo_confirmed_at` | NULL | Poliswag | delivery audit time, `DATETIME(6)` |
| `hundo_dm_refused_revision` | 0 | Poliswag | revision whose confirmation Discord refused |
| `hundo_dm_refused_at` | NULL | Poliswag | refusal audit time, `DATETIME(6)` |

The settings writer changes the switch/areas and increments the revision
**in the same SQL UPDATE**, using `revision = revision + 1`. Increment only
when either value changes, or when the player explicitly requests **Tentar
novamente**. An ordinary save of identical values does not retry. Unrelated
profile/collection edits do not increment this revision. Do not use
`updated_at` or timestamps to order confirmation outcomes.

A confirmation captures revision R and the settings read with it. Record its
outcome only if the database still has revision R and neither outcome has
already recorded R. A stale completion never acknowledges a newer revision.
Re-read players after the confirmation phase rather than mutating stale
in-memory snapshots. Delivery audit times are never used for eligibility.

## Tick and failure handling

The tick has independent cleanup, confirmation, and rule-building work:

1. **Cleanup first.** Delete this template's rows for missing/ineligible
   players, unconfirmed current revisions, missing humans, and stopped humans.
   Compute eligibility from current database rows. No Discord request,
   masterfile, or Poracle HTTP API call is required for cleanup.
2. **Confirm settings.** Send due confirmations independently per player.
   Record `Forbidden` / `NotFound` as refusals. Log transient failures without
   recording an outcome; retry on a later tick and continue other players.
   Skip departed players and enabled players no longer collecting hundo;
   still allow an off confirmation for a current member.
3. **Re-read, then build.** Read current players again, obtain the masterfile,
   and synchronize active users. Missing form data prevents rule creation,
   never cleanup. Isolate human-provisioning and rule-write failures per
   player. Each player's replacement is one transaction.
4. **Cleanup again in `finally`.** Catch settings/refusal changes made during
   the tick, including when another phase failed. Always attempt a pending
   reload afterwards, even if this cleanup fails.

A player is **active** when eligible, settings revision R is positive,
`hundo_confirmed_revision = R`, and `hundo_dm_refused_revision < R`.
A changed setting suspends the old managed rules until that revision is
confirmed. A refused confirmation is not retried automatically.

Confirmation is **due** when R exceeds both recorded outcome revisions.
Its Portuguese text states the whole current choice:

- On: "100IV por DM: **ligado** · Leiria e Marinha Grande. Vais receber aqui
  os 100IV que te faltam na Pokédex." (or "só Leiria" / "só Marinha Grande").
- Off: "100IV por DM: **desligado**. Vamos remover os teus alertas de 100IV."

The site shows a refusal when the current revision equals the refused
revision and exceeds the confirmed revision. Show pending while the current
revision has no outcome; a prior refusal must not label a newer retry failed.

## Rules and existing Poracle users

- Read `poliswag.pokemon_name`, excluding tiles ticked in
  `pogoleiria.collection_entry` for `category = 'hundo'`; filter costumes by
  the player's setting. This matches the Pokédex tile model.
- Map ordinary tile form 0 to the species' masterfile `defaultFormId`, because
  Poracle's form 0 is a wildcard. Named forms keep their IDs. Only species
  explicitly present with no forms may retain 0. If an ordinary tile cannot
  be resolved safely (missing species or forms without a usable default),
  skip that player's rebuild and log it; do not introduce wildcard rules.
- Rules use IV 100–100, distance 0, area overrides from the setting,
  `clean = 0`, `ping = ''`, and otherwise the existing broad channel-rule
  defaults. All fields are managed except the generated `uid`.
- Rules **follow the human's current profile**, read each tick. A profile
  change rebuilds managed rules for that profile and removes the previous
  profile's managed rows. Never switch the user's profile. A switch can cause
  a gap until the next successful tick/reload; this is accepted.
- Create a missing `discord:user` through the API with its initial area
  supplied in the creation payload. The API creates an enabled human and its
  default profile. Do not separately call `start` or patch its area afterwards.
  Re-read the created human. On conflict/timeout, look it up again on the next
  tick; never infer a successful creation or restart an existing stopped user.
- Compare the complete managed row multiset, including profile, IV bounds,
  costume, distance, and all remaining filters. Compare parsed area arrays
  canonically. Duplicates and malformed area JSON require replacement.
- Replace only the player's managed rows in one transaction; rollback on
  failure, preserving their previous rows. A failure for one player must not
  roll back another player's successful cleanup or rule update.

## Reload and operational limits

After a committed change, set an in-memory pending-reload flag. Attempt
`POST /api/reload` once at the end of the tick. Clear the flag only on success;
retry next tick even if the database already matches. Do not add HTTP reloads
inside individual player operations.

The reviewed Poracle build (`c8901ad1`, 5.2.1-main) independently reloads
tracking state every 60 seconds by default. This is the recovery fallback if
Poliswag restarts after a commit or an explicit reload fails, not a guarantee
of immediate removal. Verify the installed version/interval during rollout.
Cleanup normally takes one tick plus a reload; outages can extend that delay,
and already queued messages cannot be recalled.

Existing stops and restrictions remain authoritative. A confirmation proves
DM delivery, not that an existing Poracle account is enabled. Log stopped
accounts without re-enabling them. Closing DMs after a successful confirmation
remains undetected until another settings revision or explicit retry.

## Template and rollout

Add `pokedex-100iv` (`monster`, `discord`, language `en`) to DTS after backing
up the file. Include Pokémon name, sprite, CP, level, `{{areas}}`, time left,
and `{{{reactMapUrl}}}?o=dm-100iv`.

During the owner-only pilot the footer is simply **Pokédex · teste 100IV**.
The operator disables it with the documented SQL switch-off/revision update.
Only after the site's controls and retry action work should the footer direct
players to `pogoleiria.pt/trocas`.

Implement and validate code first, then apply the migration/template and
restart services as a separate deployment phase. Do not infer that a document
review or edit authorizes executing the deployment or DM tests.

Before wider release:

- Validate the real sync helpers with fake connections and rollback failures,
  then use a disposable MariaDB schema to exercise the actual SQL and SET type.
- Test the scheduler invoking the real new tick with its dependencies faked;
  assert successful work and no swallowed crash.
- In an isolated Poracle instance with a fake delivery sink, pass synthetic
  webhooks through real matching: missing/owned tile, wrong area, non-100IV,
  alternate profile, and opt-out. Do not send synthetic webhooks to production.
- Preview the template on the owner only during the authorized rollout.
  `/api/test` supports `target.template`, but bypasses normal rule matching;
  it is not evidence that filtering works.
- Verify real rules, a real alert, tile removal, area/profile changes, and
  opt-out. Inspect only logs emitted after deployment. Restore pilot settings
  and profile after tests unless the owner explicitly wants alerts left on.

## Verified Poracle references

These describe the reviewed build; check compatibility before deployment:

- [Profile matching](https://github.com/jfberry/PoracleNG/blob/c8901ad146020e1a0174c18e59134af00853fd4d/processor/internal/matching/human.go)
- [Human creation with area and default profile](https://github.com/jfberry/PoracleNG/blob/c8901ad146020e1a0174c18e59134af00853fd4d/processor/internal/api/humans.go)
- [Periodic state reload](https://github.com/jfberry/PoracleNG/blob/c8901ad146020e1a0174c18e59134af00853fd4d/processor/cmd/processor/main.go)
- [Template preview target](https://github.com/jfberry/PoracleNG/blob/c8901ad146020e1a0174c18e59134af00853fd4d/processor/internal/api/test.go)
