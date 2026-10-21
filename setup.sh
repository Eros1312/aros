#!/bin/sh
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$suite_dir"
python_exec=${AROS_PYTHON:-python3}
"$python_exec" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else "Aros requires Python 3.12 or newer.")'
command -v node >/dev/null || { echo 'Install Node.js 24 before setup.' >&2; exit 1; }
node -e 'if (Number(process.versions.node.split(".")[0]) < 24) { console.error("Aros requires Node.js 24 or newer."); process.exit(1); }'
command -v pnpm >/dev/null || { echo 'Install pnpm 11.25.0 (npm install --global pnpm@11.25.0), then rerun setup.' >&2; exit 1; }
[ -d backend/.venv ] || "$python_exec" -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-lock.txt
(cd web && pnpm install --frozen-lockfile && pnpm build)
echo 'Aros installed. Start the local demo with ./scripts/run-local.sh'
