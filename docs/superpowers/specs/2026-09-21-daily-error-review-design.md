# Daily error review — design

**Date:** 2026-09-21
**Status:** Approved for planning
**Reads:** `logs/error.log`, the git history of `/root/Poliswag`, and (read-only) the `poliswag` / `pogoleiria` schemas.
**Runs:** on the host, from `crontab`, as root. Not inside the container.

## Goal

At 06:00 Lisbon, if anything new landed in `logs/error.log`, have Claude read it against the repo and write down **why** it happened — not that it happened. The result is a file on disk, `logs/error-review.md`.

The existing 09:00 Discord digest (`cogs/scheduled.py:516`) stays exactly as it is. It is the signal that something happened, on a phone. This is the explanation, on disk, an hour before anyone is awake to need it.

### Why a digest was not enough

`read_new_error_entries()` collapses every entry to its first line, by design (`modules/utility.py:121`). For 2026-09-20 it would have said `102 × Unknown column 'last_trade_notice_at'`. What was actually worth knowing — that migration `011` had never been applied, that applying it would immediately expose a second break on `pokemon_name`, and that both are now fixed — took reading the traceback, the schema and `migrations/` together. That is the job being automated.

## Scope

| In | Out (deliberately) |
|---|---|
| One run a day, 06:00 Europe/Lisbon, DST-correct | Any second schedule, any "urgent" path |
| Reads `logs/error.log` since a watermark | Rotated backups (`error.log.1`) — same caveat the digest already accepts |
| Calls `claude -p` **only when there are new errors** | A call on clean days; most days are clean |
| Writes `logs/error-review.md` + a dated copy | Posting to Discord — `scheduled.py` is untouched |
| Strictly read-only: diagnoses, never repairs | Applying migrations, restarting containers, editing code |
| A useful file even when Claude cannot be reached | Silence, or a stale file that still looks current |

## Schedule

The host is `Etc/UTC`. Lisbon is UTC+1 (WEST) in summer, UTC+0 (WET) in winter, so "06:00 Lisbon" is not a fixed UTC hour. This box runs Debian vixie cron `3.0pl1-162`, which has **no `CRON_TZ`** (`man 5 crontab` does not mention it).

```cron
0 5,6 * * * /root/Poliswag/scripts/error-review.sh >> /root/Poliswag/logs/error-review.cron.log 2>&1
```

Cron fires twice; the script's first act is to drop the wrong one:

```sh
[ "$(TZ=Europe/Lisbon date +%H)" = "06" ] || exit 0
```

Exactly one run lands at 06:00 Lisbon, all year, with no DST maintenance.

> systemd 252 is installed and `OnCalendar=*-*-* 06:00:00 Europe/Lisbon` would also work. Rejected: every other recurring job on this box is a crontab line, and a two-line guard is cheaper to find than a unit file.

`flock` on the script guards against a slow run overlapping the next one.

## Window

`logs/.error-review-watermark` holds the timestamp the last run read up to, in the log's own clock.

| Case | Window start |
|---|---|
| Watermark present | its value |
| Missing (first run, or deleted) | now − 24h |

The watermark advances **only on a run that produced a report** — `ok` or `clean`. A run that failed to get a diagnosis leaves it untouched, so tomorrow re-reads the same entries alongside the new ones and diagnoses them then. Being re-reviewed is harmless; being silently dropped undiagnosed is not.

> This means a long Claude outage grows the window rather than losing days. Acceptable: the log is low-volume, and the alternative is errors that no run ever explains.

> The log's timestamps are Lisbon time: the container's `TZ` is `Europe/Lisbon`, the host's is UTC. The script compares in the log's clock (`TZ=Europe/Lisbon`) and never in the host's, or the window is an hour wrong for half the year.

## Extraction

Done in `awk`, before Claude is involved, so the deterministic facts exist whatever happens next. An entry is a line matching `^YYYY-MM-DD HH:MM:SS,mmm - LEVEL - ` plus every following line until the next such line (the traceback). Entries whose timestamp is `> watermark` are the slice.

From the slice the script computes, with no model involved:

- total entry count
- distinct final-line messages with counts (`sort | uniq -c`)
- the first full traceback

**If the count is zero:** write a one-line clean file, update the watermark, exit. Claude is never called. This is the common case and it costs nothing.

## The Claude call

```sh
timeout 900 claude -p "$(cat scripts/error-review-prompt.md)" \
  --model "${REVIEW_MODEL:-sonnet}" \
  --permission-mode default \
  --allowedTools Read Grep Glob \
                 'Bash(git log:*)' 'Bash(git show:*)' 'Bash(git -C /root/Poliswag log:*)' \
                 'Bash(docker exec db mariadb:*)'
```

| Decision | Value | Why |
|---|---|---|
| Permission mode | `default` | Anything off the allowlist is **denied**, non-interactively. Not `bypassPermissions`: an unattended agent at 6AM must not be able to act on what it finds. |
| Tools | read-only set | No `Write`, no `Edit`. `docker exec db mariadb` is how schema drift gets confirmed; it is a read path in practice and the prompt forbids DDL. |
| Timeout | 900s | A hung call must not sit until the 09:00 digest. |
| Model | `sonnet`, overridable | Daily log triage; `REVIEW_MODEL=opus` for a bad week. |

The slice is passed as a file path the prompt names, not inlined, so a 200KB day does not become a 200KB argv.

## Output

`logs/error-review.md`, overwritten each run, with a dated copy at `logs/reviews/YYYY-MM-DD.md`. The dated copies are what survive; the flat file is what you open.

Every file opens with a status header:

```
# Error review — 2026-09-21 06:00 WEST
status: ok | clean | claude-failed | limit-reached | timeout | claude-unavailable
window: 2026-09-20 06:00 → 2026-09-21 06:00
entries: 102
```

`status:` is the load-bearing line. It is never `ok` unless Claude actually returned an analysis.

## Failure handling

The rule: **the file is never empty, never absent, and never claims more than it knows.**

| Failure | Detection | Result |
|---|---|---|
| Usage limit | `limit reached` / `usage limit` in output, case-insensitive | `status: limit-reached` + deterministic summary |
| Any non-zero exit | `$?` | `status: claude-failed` + summary + captured stderr |
| Hang | `timeout` → 124 | `status: timeout` + summary |
| CLI missing | `command -v claude` at top | `status: claude-unavailable` + summary |
| Log unreadable | `[ -r ]` | Exit 1, cron log records it, no file rewritten |

In every failing case the file still carries the counts, the distinct messages and the first traceback — the digest's worth of information, plus an explicit note that the diagnosis is missing and why. **Yesterday's dated copy is never overwritten by a failure**, so a bad Claude day cannot erase a good analysis.

## Testing

`FORCE_HOUR=1` skips the Lisbon guard so the script can be run by hand.

The 2026-09-20 incident is still in `error.log` and the answer is already known — unapplied migration `011`, two-stage break, both since fixed. Running against that slice is a real test with a real expected result: the review has to name the migration gap. A run that describes 102 identical `OperationalError`s without reaching the cause has failed, and the prompt gets fixed.

The failure paths are tested by forcing them: `REVIEW_CLAUDE_BIN=/bin/false` for a non-zero exit, a stub that prints `Claude usage limit reached` for the limit path, and `REVIEW_TIMEOUT=1` against a sleeping stub for the timeout path. Each must produce a file with the right `status:` and an intact deterministic summary.

## Files

| Path | Role |
|---|---|
| `scripts/error-review.sh` | Guard, window, extraction, invocation, failure handling, output |
| `scripts/error-review-prompt.md` | What Claude is asked to produce |
| `logs/error-review.md` | Latest review |
| `logs/reviews/YYYY-MM-DD.md` | Dated copies |
| `logs/.error-review-watermark` | Window state |
| `logs/error-review.cron.log` | Cron's own stdout/stderr |
