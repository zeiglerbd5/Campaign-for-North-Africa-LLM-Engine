#!/bin/sh
# Run the CNA engine, then sync /data to S3 regardless of exit status.

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
S3_BUCKET="${S3_BUCKET:-}"

mkdir -p /data/saves /data/logs

python run_game.py --save-dir /data/saves --log-dir /data/logs "$@"
GAME_EXIT=$?

if [ -n "$S3_BUCKET" ]; then
    echo "Syncing /data to s3://$S3_BUCKET/runs/$RUN_ID/"
    python /app/s3_sync.py "$S3_BUCKET" "$RUN_ID" /data || echo "S3 sync failed (non-fatal)"
else
    echo "No S3_BUCKET set; skipping sync"
fi

exit $GAME_EXIT
