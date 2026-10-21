#!/bin/sh
# Restore into a disposable database; the production database is never a target.
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$#" -eq 1 ] || { echo 'Usage: scripts/verify-backup.sh /absolute/path/to/backup.dump' >&2; exit 1; }
backup_path=$1
[ -f "$backup_path" ] && [ -s "$backup_path" ] || { echo 'Backup missing or empty' >&2; exit 1; }
cd "$suite_dir/deploy"
restore_db="aros_restore_$(date -u +%Y%m%d%H%M%S)_$$"
cleanup() { docker compose exec -T database dropdb -U aros --if-exists "$restore_db" >/dev/null; }
docker compose exec -T database createdb -U aros "$restore_db"
trap cleanup EXIT HUP INT TERM
docker compose exec -T database pg_restore -U aros -d "$restore_db" --exit-on-error < "$backup_path"
# Queries fail if required tables were not restored. Never print user records.
docker compose exec -T database psql -U aros -d "$restore_db" -v ON_ERROR_STOP=1 -Atc 'SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM items; SELECT COUNT(*) FROM agent_jobs; SELECT version_num FROM alembic_version;'
echo 'PASS: backup restored in a temporary database; production was not changed.'
