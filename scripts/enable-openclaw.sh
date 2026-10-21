#!/bin/sh
# Optional integration: the core demo never invokes this script.
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$suite_dir/deploy"
[ -f .env ] || { echo 'Create deploy/.env first.' >&2; exit 1; }
docker compose --profile providers build openclaw
docker compose --profile providers run --rm --cap-add CHOWN --cap-add FOWNER --user root --entrypoint sh openclaw -c 'mkdir -p /home/node/.openclaw && chown node:node /home/node/.openclaw && chmod 700 /home/node/.openclaw'
docker compose --profile providers run --rm -T --entrypoint node openclaw --input-type=module < "$suite_dir/scripts/configure-openclaw.mjs"
docker compose --profile providers run --rm openclaw config validate
docker compose --profile providers up -d openclaw
echo 'Gateway started. Configure its provider with the provider documentation; no account or API subscription was connected.'
echo 'Set OPENCLAW_URL=http://openclaw:18789 in deploy/.env and recreate API/workers after authorization.'
