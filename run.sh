#!/usr/bin/env bash
set -e
cd "/root/hl-jobs/hl-position-collector"
# secrets (cron has no environment of its own)
set -a; [ -f "/root/hl-jobs/.env" ] && . "/root/hl-jobs/.env"; set +a
git pull --quiet --rebase || true
python3 "hl_snapshot.py" >> run.log 2>&1 || true
git add -A
git diff --cached --quiet || git commit -q -m "droplet $(date -u +%FT%TZ)"
git push --quiet || true
