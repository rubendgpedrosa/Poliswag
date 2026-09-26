#!/usr/bin/env bash
#
# Restarts Poliswag when its scheduler stops ticking, and tells the owner.
#
# Docker's restart policy only covers a process that exits; a bot that hangs
# (stuck event loop, dead task loop) stays "Up" forever, and it is the bot that
# sends every other alert. Each tick touches logs/heartbeat (cogs/scheduled.py);
# this runs every minute from poliswag-watchdog.timer.
#
set -uo pipefail

ROOT=/root/Poliswag
HEARTBEAT="$ROOT/logs/heartbeat"
CONTAINER=poliswag
# A tick can legitimately run past a minute (a slow step is logged at 60s);
# ten without one is a hang. The same grace after a (re)start covers boot.
STALE_AFTER=600

log() { logger -t poliswag-watchdog "$*"; }

# Stopped on purpose (docker stop, compose down) or mid-recreate: not ours.
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = true ] || exit 0

now=$(date +%s)
started=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")" +%s)
[ $((now - started)) -ge "$STALE_AFTER" ] || exit 0

beat=$(stat -c %Y "$HEARTBEAT" 2>/dev/null || echo 0)
age=$((now - beat))
[ "$age" -ge "$STALE_AFTER" ] || exit 0

log "heartbeat ${age}s old; restarting $CONTAINER"
docker restart "$CONTAINER" >/dev/null
python3 "$ROOT/scripts/dm_owner.py" \
  "⚠️ **O Poliswag parou** (sem tick há $((age / 60)) min) e foi reiniciado pelo watchdog. Ver \`docker logs poliswag\` e \`journalctl -t poliswag-watchdog\`." \
  || log "could not DM the owner"
