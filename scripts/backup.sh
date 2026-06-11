#!/bin/bash
# Veritas Daily Backup Script
# Backs up web_data and outputs directories
# Retains backups for 7 days by default

set -e

BACKUP_DIR="/data/veritas/backups"
DATA_DIR="/data/veritas"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-7}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Veritas backup..."

# Create backup directory if it doesn't exist
mkdir -p "${BACKUP_DIR}"

# Create compressed backup
BACKUP_FILE="${BACKUP_DIR}/veritas_${DATE}.tar.gz"
tar -czf "${BACKUP_FILE}" \
    -C "${DATA_DIR}" \
    --exclude='backups' \
    web_data outputs 2>/dev/null || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Backup failed"
    exit 1
}

# Calculate backup size
BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup completed: ${BACKUP_FILE} (${BACKUP_SIZE})"

# Clean up old backups
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleaning backups older than ${RETENTION_DAYS} days..."
DELETED_COUNT=$(find "${BACKUP_DIR}" -name "veritas_*.tar.gz" -mtime +${RETENTION_DAYS} | wc -l)
find "${BACKUP_DIR}" -name "veritas_*.tar.gz" -mtime +${RETENTION_DAYS} -delete

if [ "${DELETED_COUNT}" -gt 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deleted ${DELETED_COUNT} old backup(s)"
fi

# Show remaining backups
REMAINING_COUNT=$(find "${BACKUP_DIR}" -name "veritas_*.tar.gz" | wc -l)
TOTAL_SIZE=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup job finished. ${REMAINING_COUNT} backup(s) retained (${TOTAL_SIZE} total)"
