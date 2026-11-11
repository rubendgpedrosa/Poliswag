#!/usr/bin/env bash
#
# Tells the owner (a private Discord DM) when Poliswag is not doing its job,
# and restarts it when a restart can help. Runs every minute from
# poliswag-watchdog.timer.
#
# Docker's restart policy only covers a process that exits. Two cases are left:
#
# - A bot that hangs (stuck event loop, a scheduler loop that stopped) stays
#   "Up" forever. Each scheduler pass writes logs/heartbeat (cogs/scheduled.py);
#   when it is ten minutes old the container is restarted.
# - A bot that crashes while starting is restarted by Docker for ever and never
#   gets to send anything: it is the bot that sends every other alert. A
#   restart cannot help, so the owner is told once per container (2026-11-10:
#   eleven crashes in eighty seconds, noticed only because someone was looking).
#
set -uo pipefail

ROOT="${POLISWAG_ROOT:-/root/Poliswag}"
HEARTBEAT="$ROOT/logs/heartbeat"
CRASH_MARK="$ROOT/state/watchdog-crash-loop"
CONTAINER=poliswag
# A pass can legitimately take minutes (a full recovery of the scanner); ten
# without one is a hang. The same grace after a (re)start covers boot.
STALE_AFTER=600
# Docker counts the restarts it made since the container was created; `make
# deploy` creates a new one. Three is past any one-off.
CRASH_RESTARTS=3

log() { logger -t poliswag-watchdog "$*"; }
inspect() { docker inspect -f "$1" "$CONTAINER" 2>/dev/null; }
alert() { python3 "$ROOT/scripts/notify_owner.py" --discord "$1" "$2"; }

id=$(inspect '{{.Id}}')
[ -n "$id" ] || exit 0

now=$(date +%s)
started=$(date -d "$(inspect '{{.State.StartedAt}}')" +%s 2>/dev/null || echo 0)
beat=$(stat -c %Y "$HEARTBEAT" 2>/dev/null || echo 0)
running=$(inspect '{{.State.Running}}')
restarts=$(inspect '{{.RestartCount}}')

if [ "${restarts:-0}" -ge "$CRASH_RESTARTS" ]; then
  if [ "$beat" -ge "$started" ]; then
    # It passed since its last start: it came up after all. A later loop in
    # this same container is told again.
    rm -f "$CRASH_MARK"
  else
    # Up for under two minutes without a pass yet: the next run will tell a
    # slow start from one more crash.
    [ "$running" = true ] && [ $((now - started)) -lt 120 ] && exit 0
    # Once per container: the count only grows, and the fix is a deploy.
    [ "$(cat "$CRASH_MARK" 2>/dev/null)" = "$id" ] && exit 0
    log "crash loop: Docker restarted $CONTAINER ${restarts} times and it has not passed since"
    if alert "⚠️ **O Poliswag não arranca**: o Docker já o reiniciou ${restarts} vezes e o agendador não voltou a correr. Reiniciar não resolve: ver \`docker logs poliswag\`, corrigir o erro e fazer \`make deploy\`." \
      "Poliswag em ciclo de reinícios"; then
      mkdir -p "$(dirname "$CRASH_MARK")" && printf '%s' "$id" > "$CRASH_MARK"
    else
      log "could not alert the owner; trying again next minute"
    fi
    exit 0
  fi
fi

# Stopped on purpose (docker stop, compose down) or mid-recreate: not ours.
[ "$running" = true ] || exit 0
[ $((now - started)) -ge "$STALE_AFTER" ] || exit 0
age=$((now - beat))
[ "$age" -ge "$STALE_AFTER" ] || exit 0

log "heartbeat ${age}s old; restarting $CONTAINER"
docker restart "$CONTAINER" >/dev/null
alert "⚠️ **O Poliswag parou** (sem tick há $((age / 60)) min) e foi reiniciado pelo watchdog. Ver \`docker logs poliswag\` e \`journalctl -t poliswag-watchdog\`." \
  "Poliswag reiniciado" \
  || log "could not alert the owner"
