# Events: storage, delivery, repair

### Event timezone repair (2026-09-26)

`event.start` / `event.end` store Lisbon wall time. ScrapedDuck timestamps without an offset already represent local time; timestamps with `Z` or an explicit offset must be converted with `ZoneInfo("Europe/Lisbon")` before formatting. `Utility.format_datetime_string` now does this, with summer/winter and DST transition tests. Host and database remain UTC; Poliswag's process uses Europe/Lisbon.

Production repair converted 10 current/upcoming rows in place, preserving notification markers, and edited three existing CONVIVIO posts (Brisbane, Marseille/Munich, Lisbon). Backup of original rows/messages: `logs/event-timezone-backup-20260926095958.json`. `scripts/repair_event_timezone.py` is the one-off repair tool (preview by default, `--apply` writes); stop the bot while applying. Historical rows were left untouched. The website now compares against an explicitly formatted Lisbon clock (`apps/landing/lib/events.ts`), reads DATETIME values as strings, and serializes real UTC instants.


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
