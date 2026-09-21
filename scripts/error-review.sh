#!/usr/bin/env bash
#
# Daily 06:00 Lisbon review of logs/error.log.
#
# Cron fires this at 05:00 and 06:00 UTC; the hour guard below drops the wrong
# one, so exactly one run lands at 06:00 Lisbon whether or not DST is in effect.
# Debian's cron has no CRON_TZ, which is why the guard exists at all.
#
# Read-only by construction: it summarises the log itself, and asks Claude for
# the diagnosis with writing tools withheld. See
# docs/superpowers/specs/2026-09-21-daily-error-review-design.md
#
set -uo pipefail

ROOT="/root/Poliswag"
LOG="$ROOT/logs/error.log"
OUT="$ROOT/logs/error-review.md"
REVIEW_DIR="$ROOT/logs/reviews"
MARK="$ROOT/logs/.error-review-watermark"
PROMPT="$ROOT/scripts/error-review-prompt.md"
LOCK="$ROOT/logs/.error-review.lock"

CLAUDE_BIN="${REVIEW_CLAUDE_BIN:-/root/.local/bin/claude}"
REVIEW_TIMEOUT="${REVIEW_TIMEOUT:-900}"
REVIEW_MODEL="${REVIEW_MODEL:-sonnet}"

# The log is written by the container, whose TZ is Europe/Lisbon. The host is
# UTC. Every timestamp here must be in the log's clock or the window is an hour
# wrong for half the year.
export TZ=Europe/Lisbon

log() { printf '%s error-review: %s\n' "$(date '+%F %T')" "$*"; }

# --- one run only ------------------------------------------------------------
exec 9>"$LOCK"
if ! flock -n 9; then
    log "another run holds the lock, exiting"
    exit 0
fi

# --- 06:00 Lisbon, and only 06:00 -------------------------------------------
if [ -z "${FORCE_HOUR:-}" ] && [ "$(date +%H)" != "06" ]; then
    exit 0
fi

if [ ! -r "$LOG" ]; then
    log "cannot read $LOG"
    exit 1
fi

NOW="$(date '+%F %T')"
NOW_HUMAN="$(date '+%F %H:%M %Z')"
DATE_TAG="$(date '+%F')"

if [ -s "$MARK" ]; then
    WATERMARK="$(cat "$MARK")"
else
    WATERMARK="$(date -d '24 hours ago' '+%F %T')"
fi

mkdir -p "$REVIEW_DIR"
SLICE="$(mktemp)"
SUMMARY="$(mktemp)"
CLAUDE_OUT="$(mktemp)"
CLAUDE_ERR="$(mktemp)"
trap 'rm -f "$SLICE" "$SUMMARY" "$CLAUDE_OUT" "$CLAUDE_ERR"' EXIT

# --- the slice ---------------------------------------------------------------
# An entry is a timestamped header line plus every line after it until the next
# header (its traceback). Keep entries newer than the watermark; the timestamp
# format sorts correctly as a string.
awk -v wm="$WATERMARK" '
    /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9],/ {
        keep = (substr($0, 1, 19) > wm)
    }
    keep
' "$LOG" >"$SLICE"

ENTRIES="$(grep -c '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9],' "$SLICE" || true)"

header() {
    cat <<EOF
# Error review — $NOW_HUMAN

    status:  $1
    window:  $WATERMARK  →  $NOW
    entries: $ENTRIES

EOF
}

publish() {
    # Only a run that actually produced an analysis writes the dated copy and
    # advances the watermark. A failed run leaves both alone: the dated copy so
    # it cannot overwrite a good report from earlier, the watermark so tomorrow
    # re-reviews these same entries instead of dropping them undiagnosed.
    case "$1" in
        ok | clean)
            cp "$OUT" "$REVIEW_DIR/$DATE_TAG.md"
            echo "$NOW" >"$MARK"
            ;;
    esac
}

# --- nothing new: no model, no cost -----------------------------------------
if [ "$ENTRIES" -eq 0 ]; then
    { header "clean"; echo "Nothing new in the log since the last review."; } >"$OUT"
    publish clean
    log "clean, no new entries since $WATERMARK"
    exit 0
fi

# --- what the log says, with no model involved -------------------------------
# This is the floor: whatever happens to the Claude call, the report still
# carries these facts.
{
    echo "## What the log says"
    echo
    echo "| Count | Message |"
    echo "|------:|---------|"
    sed -n 's/^[0-9-]\{10\} [0-9:,]\{12\} - [A-Z]* - //p' "$SLICE" \
        | sort | uniq -c | sort -rn \
        | sed 's/^ *\([0-9]*\) \(.*\)$/| \1 | `\2` |/'
    echo
    echo "First occurrence:"
    echo
    echo '```'
    awk '
        /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9],/ { if (seen) exit; seen = 1 }
        { print }
    ' "$SLICE" | head -40
    echo '```'
} >"$SUMMARY"

fallback() {
    local status="$1" note="$2"
    {
        header "$status"
        echo "> **The diagnosis is missing.** $note"
        echo ">"
        echo "> What follows is the log's own account, unanalysed."
        echo
        cat "$SUMMARY"
        if [ -s "$CLAUDE_ERR" ]; then
            echo
            echo "<details><summary>claude stderr</summary>"
            echo
            echo '```'
            tail -20 "$CLAUDE_ERR"
            echo '```'
            echo
            echo "</details>"
        fi
    } >"$OUT"
    publish "$status"
    log "$status — wrote fallback report ($ENTRIES entries)"
}

# --- ask Claude --------------------------------------------------------------
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
    fallback "claude-unavailable" "\`$CLAUDE_BIN\` is not executable on this host."
    exit 0
fi

cd "$ROOT" || exit 1

PROMPT_TEXT="$(cat "$PROMPT")
---
The error slice for this run is at: $SLICE
It covers $ENTRIES entries logged between $WATERMARK and $NOW (Europe/Lisbon)."

timeout "$REVIEW_TIMEOUT" "$CLAUDE_BIN" -p "$PROMPT_TEXT" \
    --model "$REVIEW_MODEL" \
    --permission-mode default \
    --allowedTools 'Read,Grep,Glob,Bash(git log:*),Bash(git show:*),Bash(git diff:*),Bash(docker exec db mariadb:*)' \
    --disallowedTools 'Write,Edit,NotebookEdit,WebFetch,WebSearch' \
    >"$CLAUDE_OUT" 2>"$CLAUDE_ERR"
RC=$?

if grep -qiE 'usage limit|limit reached|rate limit|quota' "$CLAUDE_OUT" "$CLAUDE_ERR" 2>/dev/null; then
    fallback "limit-reached" "Claude's usage limit was hit, so no review ran today."
    exit 0
fi

if [ "$RC" -eq 124 ]; then
    fallback "timeout" "The review did not finish within ${REVIEW_TIMEOUT}s and was killed."
    exit 0
fi

if [ "$RC" -ne 0 ] || [ ! -s "$CLAUDE_OUT" ]; then
    fallback "claude-failed" "\`claude\` exited $RC without a usable report."
    exit 0
fi

{
    header "ok"
    cat "$CLAUDE_OUT"
    echo
    echo "---"
    echo
    cat "$SUMMARY"
} >"$OUT"
publish ok
log "ok — reviewed $ENTRIES entries"
