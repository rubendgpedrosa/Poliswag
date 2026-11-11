#!/usr/bin/env bash
#
# After `docker compose up -d --build`: wait until the bot has started and its
# scheduler has passed once, or say why it has not. Run by `make deploy`.
#
# A bot that crashes while starting is restarted for ever by `restart:
# unless-stopped`, and nothing tells anyone: the watchdog only looks at a
# container that has been up ten minutes, and the bot is what sends every other
# alert. On 2026-11-10 that was eleven restarts in eighty seconds, new code
# running on an image that did not have its new dependency yet.
#
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${1:-poliswag}"
HEARTBEAT="${HEARTBEAT_FILE:-$ROOT/logs/heartbeat}"
WAIT="${DEPLOY_WAIT_SECONDS:-180}"

inspect() { docker inspect -f "$1" "$CONTAINER" 2>/dev/null; }

fail() {
  echo "deploy: $1" >&2
  echo "--- last lines of the container log" >&2
  docker logs --tail 40 "$CONTAINER" >&2 2>&1
  exit 1
}

[ -n "$(inspect '{{.Id}}')" ] || { echo "deploy: no container named $CONTAINER" >&2; exit 1; }

deadline=$(( $(date +%s) + WAIT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  restarts=$(inspect '{{.RestartCount}}')
  if [ "${restarts:-0}" -gt 0 ]; then
    fail "the bot crashed while starting and Docker restarted it ${restarts} time(s)"
  fi
  [ "$(inspect '{{.State.Running}}')" = true ] || fail "the container is not running"
  started=$(date -d "$(inspect '{{.State.StartedAt}}')" +%s)
  beat=$(stat -c %Y "$HEARTBEAT" 2>/dev/null || echo 0)
  if [ "$beat" -ge "$started" ]; then
    echo "deploy: $CONTAINER is up and its scheduler is passing ($(( $(date +%s) - started ))s after the start)"
    exit 0
  fi
  sleep 3
done
fail "no scheduler heartbeat ${WAIT}s after the start"
