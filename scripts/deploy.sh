#!/bin/sh
# Deploy only on the host where this command is explicitly run.
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$suite_dir/deploy"
command -v docker >/dev/null || { echo 'Install Docker Engine and Compose first.' >&2; exit 1; }
[ -f .env ] || { echo 'Run python3 scripts/prepare_env.py --domain YOUR_HOST --email YOUR_EMAIL first.' >&2; exit 1; }
docker compose config --quiet
docker compose build api worker agent_worker migrate
docker compose up -d database
docker compose run --rm migrate
docker compose up -d api worker agent_worker proxy
docker compose ps
echo 'Core deployment started. Verify HTTPS health and sign in with ADMIN_EMAIL/ADMIN_PASSWORD from deploy/.env.'
echo 'AI providers and external connectors remain optional. Back up existing data before upgrades.'
