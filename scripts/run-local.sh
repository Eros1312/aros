#!/bin/sh
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$suite_dir"
[ -x backend/.venv/bin/python ] && [ -f web/out/index.html ] || "$suite_dir/setup.sh"
# A dedicated demo database cannot accidentally load an existing application .env.
mkdir -p .local/demo
cd .local/demo
export APP_ENV=development DEMO_MODE=true PUBLIC_ORIGIN=http://127.0.0.1:8000
export DATABASE_URL=sqlite:///./aros-demo.db ADMIN_EMAIL=demo@aros.local ADMIN_PASSWORD=
export WEB_DIR="$suite_dir/web/out" PYTHONPATH="$suite_dir/backend"
export OPENAI_API_KEY= OPENAI_MODEL= OPENCLAW_URL= OPENCLAW_TOKEN= OPENCLAW_MODEL= OPENCLAW_DESIGN_MODEL=
export GOOGLE_CLIENT_ID= GOOGLE_CLIENT_SECRET= FREELLM_URL= FREELLM_TOKEN= FREELLM_MODELS=
export SERVER_AGENT_URL= SERVER_AGENT_TOKEN= RSS_URLS= ENCRYPTION_KEY=
python_exec="$suite_dir/backend/.venv/bin/python"
"$python_exec" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log &
api_pid=$!
cleanup() {
  kill "$api_pid" ${worker_pid:+"$worker_pid"} ${agent_pid:+"$agent_pid"} 2>/dev/null || true
  wait "$api_pid" ${worker_pid:+"$worker_pid"} ${agent_pid:+"$agent_pid"} 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
# Wait for this API process, rather than starting workers against an uncreated schema.
"$python_exec" - "$api_pid" <<'PY'
import os, sys, time, urllib.request
for attempt in range(60):
    try:
        os.kill(int(sys.argv[1]),0)
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=1): break
    except ProcessLookupError: raise SystemExit('Aros API exited; check the error above.')
    except OSError: time.sleep(.25)
else: raise SystemExit('Aros API did not become healthy within 15 seconds.')
PY
"$python_exec" -m app.worker &
worker_pid=$!
"$python_exec" -m app.agent_worker &
agent_pid=$!
echo 'Aros demo: http://127.0.0.1:8000 — synthetic data, local summaries, Ctrl-C stops all processes.'
wait "$api_pid"
