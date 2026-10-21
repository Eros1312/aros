#!/bin/sh
set -eu
suite_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$suite_dir/deploy"
umask 077
mkdir -p ../backups
backup_file="../backups/aros-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T database pg_dump -U aros -d aros -Fc > "$backup_file"
[ -s "$backup_file" ] || { rm "$backup_file"; exit 1; }
echo "Backup saved: $backup_file. Copy it encrypted to separate storage."
