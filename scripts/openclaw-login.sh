#!/bin/sh
set -eu
[ "$#" -eq 1 ] || { echo 'Usage: scripts/openclaw-login.sh PROVIDER_ID (requires your own authorized provider account)' >&2; exit 1; }
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$suite_dir/deploy"
docker compose --profile providers exec openclaw openclaw models auth login --provider "$1" --agent aros
# Never pass --set-default: model selection is an explicit configuration choice.
docker compose --profile providers exec openclaw openclaw models list --provider "$1" --agent aros
