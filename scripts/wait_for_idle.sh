#!/usr/bin/env bash
# Block until the running app reports no unfinished translation tasks.
#
# The deploy calls this between `docker compose build` and `docker compose up -d`:
# recreating the app container kills every in-flight translation and there is
# no resume, so we only swap when nobody is translating.
#
# Exit codes: 0 - idle (or app unreachable, nothing to protect); 1 - gave up
# after MAX_WAIT_SECONDS. Environment: HEALTH_URL, POLL_SECONDS, MAX_WAIT_SECONDS.
set -euo pipefail

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/api/health}"
POLL_SECONDS="${POLL_SECONDS:-15}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-10800}"

deadline=$(( $(date +%s) + MAX_WAIT_SECONDS ))

while :; do
  if ! body="$(curl -fsS --max-time 5 "$HEALTH_URL")"; then
    echo "wait_for_idle: $HEALTH_URL unreachable, nothing to wait for"
    exit 0
  fi

  active="$(printf '%s' "$body" | grep -oE '"active_tasks":[[:space:]]*[0-9]+' | grep -oE '[0-9]+$' || true)"
  if [ -z "$active" ]; then
    echo "wait_for_idle: no active_tasks in health response ($body), assuming idle"
    exit 0
  fi

  if [ "$active" -eq 0 ]; then
    echo "wait_for_idle: no active translations"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "wait_for_idle: still $active active translation(s) after ${MAX_WAIT_SECONDS}s, giving up" >&2
    exit 1
  fi

  echo "wait_for_idle: $active active translation(s), retrying in ${POLL_SECONDS}s"
  sleep "$POLL_SECONDS"
done
