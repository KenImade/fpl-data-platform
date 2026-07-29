#!/bin/sh
set -eu

INTERVAL=$(( ${BACKUP_INTERVAL_HOURS:-6} * 3600 ))

while true; do
    STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
    FILE="/tmp/fpl-${STAMP}.dump"

    echo "backup ${STAMP} starting"

    if pg_dump --format=custom --compress=9 --file="${FILE}" \
        && aws --endpoint-url "${S3_ENDPOINT_URL}" s3 cp \
            "${FILE}" "s3://${S3_BUCKET}/backups/postgres/fpl-${STAMP}.dump"
    then
        SIZE=$(stat -c%s "${FILE}")
        echo "backup ${STAMP} ok (${SIZE} bytes)"
        [ -n "${BACKUP_HEARTBEAT_URL:-}" ] && curl -fsS -m 10 "${BACKUP_HEARTBEAT_URL}" || true
    else
        echo "backup ${STAMP} FAILED"
        [ -n "${BACKUP_HEARTBEAT_URL:-}" ] && curl -fsS -m 10 "${BACKUP_HEARTBEAT_URL}/fail" || true
    fi

    rm -f "${FILE}"
    sleep "${INTERVAL}"
done
