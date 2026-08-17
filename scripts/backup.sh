#!/usr/bin/env bash
# MindBasic 数据库与文件备份（生产 cron 示例：0 2 * * * /path/scripts/backup.sh）
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:?请设置 DB_PASSWORD 环境变量}"
DB_NAME="${DB_NAME:-mindbasic}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

mysqldump -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" \
  | gzip > "$BACKUP_DIR/mindbasic_${TS}.sql.gz"

# uploads（头像/资质/身份证）与 exports（数据导出）随库一起备份
tar -czf "$BACKUP_DIR/files_${TS}.tar.gz" -C . uploads exports 2>/dev/null || true

# 保留期内自动清理旧备份
find "$BACKUP_DIR" -name "mindbasic_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name "files_*.tar.gz" -mtime +"$RETENTION_DAYS" -delete

echo "backup done: $BACKUP_DIR/mindbasic_${TS}.sql.gz"
