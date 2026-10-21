#!/bin/sh
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$suite_dir"
[ -x backend/.venv/bin/python ] || { echo 'Run ./setup.sh first.' >&2; exit 1; }
(cd backend && .venv/bin/python -m pytest -q)
(cd web && pnpm typecheck && pnpm test:unit && pnpm test:dom && pnpm build)
echo 'Aros backend and web checks passed. External integrations are mocked in tests.'
