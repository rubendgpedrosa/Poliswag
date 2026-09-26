# Host-side error review (`scripts/error-review.sh`)

### Host-side error review (not part of the bot)

`scripts/error-review.sh`, run from root's crontab on the **host**, not in the container. Reads new `logs/error.log` entries once a day and has Claude diagnose them into `logs/error-review.md` (dated copies in `logs/reviews/`). Design: [spec](superpowers/specs/2026-09-21-daily-error-review-design.md).

| Fact | Value |
|---|---|
| Schedule | cron `0 5,6 * * *` UTC; script keeps only the 06:00 `Europe/Lisbon` run (Debian cron has no `CRON_TZ`) |
| Window | `logs/.error-review-watermark`, falling back to 24h; advances only on a run that produced a report |
| Cost | Claude is called **only when there are new errors**; clean days exit before any model call |
| Safety | `--permission-mode default` with a read-only allowlist; no `Write`/`Edit`. It diagnoses, never repairs |
| On failure | Always writes a report with `status: claude-failed\|limit-reached\|timeout\|claude-unavailable` plus the raw counts and first traceback |
| Manual run | `FORCE_HOUR=1 ./scripts/error-review.sh`; `REVIEW_MODEL=opus` for a harder week |

Independent of `_check_daily_error_digest` in `cogs/scheduled.py`, which still posts its 09:00 count to `MOD_CHANNEL`.
