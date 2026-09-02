#!/usr/bin/env bash
# Pull origin/main and rebuild containers. Run from the VPS clone.
# Discards uncommitted changes in this clone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git fetch origin
git checkout -f -B main origin/main

docker compose -f docker/docker-compose.yml up --build -d
docker compose -f docker/docker-compose.yml ps
