#!/usr/bin/env bash
# Pull origin/main and rebuild containers. Run from the VPS clone.
# Discards uncommitted changes in this clone.
#
# The image is built while the old container keeps serving; the swap waits
# until no translation is running (see wait_for_idle.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git fetch origin
git checkout -f -B main origin/main

docker compose -f docker/docker-compose.yml build
bash scripts/wait_for_idle.sh
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
curl --fail --silent --show-error --retry 12 --retry-connrefused --retry-delay 5 --max-time 5 http://127.0.0.1:8080/api/health
