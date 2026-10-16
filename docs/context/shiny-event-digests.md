# Event shiny summaries

Prepared on `feature/shiny-event-digests`; not deployed during Community Day.

## Behavior

The existing base 1/512 individual alerts continue. Optional event summaries use the same `shiny_draw` / `is_shiny_at` threshold as the private map, with active species odds from `shiny_rate`. Only event-only matches are included; base matches remain individual alerts.

Each pilot recipient must opt in. Summaries respect their existing Leiria/Marinha area preferences, optionally filter species, and contain up to 10 unique live spawns with at least two minutes left. A database claim limits each recipient to one summary every five minutes. Additional candidates remain available for later summaries while still alive.

Commands:

- `!shiny eventos`: show status and usage.
- `!shiny eventos ligar`: all configured event species.
- `!shiny eventos ligar 570,25`: chosen Pokédex species numbers.
- `!shiny eventos desligar`: disable summaries.

The existing pilot command/recipient restrictions remain. A user's personal map link is outside this feature; summaries neither issue nor rotate links and do not add link expiry.

## Delivery and persistence

Migration `034_add_shiny_event_digests.sql` adds opt-in/species/cooldown fields on `shiny_link` and event prediction/delivery fields on `shiny_alert`. The same `(discord_id, encounter_id)` key deduplicates event summaries and individual alerts. Event records retain the odds, first draw and delivery outcome.

Claiming the digest interval and recording its spawns happen before sending. Failed deliveries are marked failed and are not retried indefinitely. A process crash between recording and sending can leave pending rows; durable retry/reconciliation is a later extension. Event summaries do not include individual verdict buttons; existing individual-alert buttons retain their current behavior.

## Release boundary

`SHINY_EVENT_DIGESTS_ENABLED` defaults to false. When false, the scheduler adds no event-related reads or writes and the command reports summaries inactive. Apply migration 034 in a later deployment before enabling the feature; per-user opt-in is also required. Nothing in this feature calls the game, linker, Rotom or Dragonite.

Prepared code passes 393 focused checks covering prediction, command, store, configuration, migration and scheduler behavior. Database and Discord operations were mocked. No live migration, message or runtime action was performed.
