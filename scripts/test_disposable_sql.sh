#!/usr/bin/env bash
# Run only against containers created here. No compose or application DB is used.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-all}" in
  all) suites=(events hundo spawn) ;;
  events|hundo|spawn) suites=("$1") ;;
  *) echo "Usage: $0 [events|hundo|spawn]" >&2; exit 2 ;;
esac
container_id=""
output_file="$(mktemp)"
cleanup() {
  if [[ -n "$container_id" ]]; then docker rm -f "$container_id" >/dev/null 2>&1 || true; fi
  rm -f "$output_file" "${output_file}.coverage"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for suite in "${suites[@]}"; do
  case "$suite" in
    events) marker=event_disposable; password=""; envarg=MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1; file=tests/integration/test_events_sql.py ;;
    hundo) marker=hundo_disposable; password=disposable; envarg=MARIADB_ROOT_PASSWORD=disposable; file=tests/integration/test_hundo_alerts_sql.py ;;
    spawn) marker=spawn_history_disposable; password=disposable; envarg=MARIADB_ROOT_PASSWORD=disposable; file=tests/integration/test_spawn_history_sql.py ;;
  esac
  container_id=$(docker run -d --rm --label com.poliswag.disposable-sql=true -p 127.0.0.1::3306 -e "MARIADB_DATABASE=$marker" -e "$envarg" mariadb:10.11 --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci)
  port=$(docker port "$container_id" 3306/tcp | awk -F: 'NR==1 {print $NF}')
  SQL_TEST_PORT="$port" SQL_TEST_PASSWORD="$password" python3 - <<'PY'
import os
import time
import pymysql

deadline = time.monotonic() + 120
while True:
    try:
        connection = pymysql.connect(host="127.0.0.1", port=int(os.environ["SQL_TEST_PORT"]),
                                     user="root", password=os.environ["SQL_TEST_PASSWORD"], connect_timeout=2)
        connection.close()
        break
    except pymysql.MySQLError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(1)
PY
  echo "Running $suite SQL suite on disposable loopback database"
  # Clear inherited selectors so each invocation can only reach its own container.
  env -u EVENT_SQL_TEST_PORT -u HUNDO_SQL_TEST_PORT -u HUNDO_SQL_TEST_PASSWORD \
      -u SPAWN_HISTORY_SQL_TEST_HOST -u SPAWN_HISTORY_SQL_TEST_PORT \
      EVENT_SQL_TEST_PORT="$( [[ "$suite" == events ]] && printf '%s' "$port" )" \
      HUNDO_SQL_TEST_PORT="$( [[ "$suite" == hundo ]] && printf '%s' "$port" )" \
      HUNDO_SQL_TEST_PASSWORD=disposable COVERAGE_FILE="${output_file}.coverage" \
      SPAWN_HISTORY_SQL_TEST_HOST="$( [[ "$suite" == spawn ]] && printf 127.0.0.1 )" \
      SPAWN_HISTORY_SQL_TEST_PORT="$port" \
      python3 -m pytest "$file" -q -rs | tee "$output_file"
  if ! grep -Eq '^[0-9]+ passed' "$output_file" || grep -Eiq 'skipped' "$output_file"; then
    echo "SQL suite must pass without skips" >&2; exit 1
  fi
  docker rm -f "$container_id" >/dev/null
  container_id=""
done
