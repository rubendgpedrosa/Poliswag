# Events: storage, delivery, repair

### Event timezone repair (2026-09-26)

`event.start` / `event.end` store Lisbon wall time. ScrapedDuck timestamps without an offset already represent local time; timestamps with `Z` or an explicit offset must be converted with `ZoneInfo("Europe/Lisbon")` before formatting. `Utility.format_datetime_string` now does this, with summer/winter and DST transition tests. Host and database remain UTC; Poliswag's process uses Europe/Lisbon.

Production repair converted 10 current/upcoming rows in place, preserving notification markers, and edited three existing CONVIVIO posts (Brisbane, Marseille/Munich, Lisbon). Backup of original rows/messages: `logs/event-timezone-backup-20260926095958.json`. `scripts/repair_event_timezone.py` is the one-off repair tool (preview by default, `--apply` writes); stop the bot while applying. Historical rows were left untouched. The website now compares against an explicitly formatted Lisbon clock (`apps/landing/lib/events.ts`), reads DATETIME values as strings, and serializes real UTC instants.


### Ended-event shiny statistics (2026-10-15)

Species-focused ended-event summaries (Community Day and Spotlight Hour) also read Golbat's `pokemon_shiny_stats` table. They show scanner-wide shiny and encounter totals for the complete calendar day(s), not an exact event-hour count: the table has daily rows and no event-window timestamps. The summary labels this provenance and keeps the existing outside-event-hours caveat; raid summaries do not claim shiny counts because no equivalent raid-period shiny aggregate is available.

### Weekly and error digests: post-send acknowledgment

`Scheduled._send_weekly_digest` / `_check_weekly_digest` (Monday 09:00 calendar) and `_check_daily_error_digest` (daily owner DM) persist their dedup markers only after Discord accepts. A quiet week (no events after filtering) or a truly unconfigured channel (id `0`), and a quiet error day or missing recipient, also advance the marker so neither retries every tick. A transport failure — including a configured `CONVIVIO_CHANNEL_ID` whose object is still missing after `fetch_channel` recovery (`Forbidden`/`NotFound`/`HTTPException` re-raised and logged) or a `fetch_user` failure — leaves the marker unset, so the next 60 s tick retries (bounded, no tight loop). `Poliswag.get_channels()` resolves each channel independently for the same reason: one failed lookup clears only that attribute to `None`, logs which channel failed, and still attempts the rest.

The weekly calendar is chunked at line boundaries within Discord's 4096-char description / 6000-char per-message embed limits (`_split_weekly_description`; over-long single lines are hard-split), with `AllowedMentions.none()` on every send, so no entry is dropped or truncated. The error digest sends at most the first 10 entries (`description[:4000]`) with the remainder counted. Discord acceptance and the DB marker are never atomic: a crash between two weekly batches, or between the last send and the marker write, may resend an earlier batch or the whole digest on retry (partial-batch duplicates). The lure presence line follows the same post-success rule: `_update_lure_status` caches the count only after `change_presence` succeeds, so a transport failure retries next tick.

### Event delivery and regression coverage (2026-09-26)

- Event selection is read-only. `Scheduled._send_event_change_notifications(..., acknowledge=True)` marks each successfully sent batch afterward; manual `!testevent` previews never mark delivery. Failed batches remain eligible on the next tick/restart. Plain ended-event lines split at Discord's message limit instead of being truncated. Acceptance by Discord and the subsequent DB marker cannot be atomic: a crash/DB failure between them can still repeat a delivered batch.
- Active intervals are start-inclusive and end-exclusive. Exact end-minute previews remain inclusive at the minute's lower boundary.
- Feed `eventID` identifies a reschedule/rename, preserving the existing row's notification markers. End-only changes update on upsert. Distinct IDs with the same name remain distinct rows.
- The repair tool now journals each edit, supports `--resume <journal>` after a committed DB repair, re-reads before editing, retries bounded rate limits, records deleted messages, and never replaces a missing message with a new post. New journal filenames are unique. Old pre-journal backups remain evidence for manual recovery, not resumable journals.
- Tests: `pytest tests/modules/test_utility.py tests/modules/test_event_manager.py tests/cogs/test_scheduled.py tests/scripts/test_repair_event_timezone.py -q --no-cov`. `python3 scripts/test_events_mariadb.py` on the host creates/removes an isolated UTC MariaDB and runs the bot lifecycle plus sibling landing event-query tests; no production DB settings are used.

### Further event audit (2026-09-26)

- Empty/malformed feeds and reversed intervals fail before writes; incomplete named placeholders retain their stored schedule. Failed fetches/storage do not start the 15-minute success cache. Future-row cleanup runs only after successful ingestion and uses exact row keys plus source IDs, so cancelling one recurring-name event cannot remove its sibling.
- Multiple stored revisions with the same `eventID` now reconcile to the current feed row in a single `DatabaseConnector.execute_transaction`: upsert current values, preserve delivery markers, delete obsolete keys. Conflicting same-ID entries in one feed are rejected before writes. The transaction does not retry uncertain commits.
- Live audit found six duplicated source IDs; the current feed reconciled the three future ones (Buddy Trek and Halloween I/II) to 10:00 starts, removing obsolete midnight revisions. Historical IDs absent from the feed were left alone. Pre-deploy snapshot: `logs/event-audit-before-20260926103317.json` (194 rows). Lisbon Safari's existing delivery marker was preserved.
- Event batches respect both ten embeds and 6000 combined embed characters, disable mentions, and cap titles at 256 characters. Successful batches alone receive delivery markers.
- Validation for this audit: 238 targeted Python tests, 12 real MariaDB lifecycle/rollback tests and 2 website/MariaDB integration tests; lint and diff checks passed. Live bot restarted and refreshed successfully.
