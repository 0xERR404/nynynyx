#!/usr/bin/env bash
set -euo pipefail

# Расписание: этот скрипт сам себя не планирует. Добавь в crontab на сервере:
#   crontab -e
#   0 4 * * * /path/to/nexus404/scripts/backup.sh >> /path/to/nexus404/shared/logs/backup.log 2>&1
#
# Шифрование: если задан BACKUP_GPG_RECIPIENT (email/ключ) — архив шифруется
# gpg и исходный .tar.gz удаляется. Обязательно для passscope/walletscope,
# если такие модули появятся — их данные в shared/db иначе лежат открытым
# текстом в бэкапе.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="shared/backups/nexus404_backup_${TIMESTAMP}.tar.gz"

mkdir -p shared/backups

tar -czf "$BACKUP_FILE" \
    modules \
    shared/db \
    2>/dev/null || true

if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
    gpg --yes --trust-model always --encrypt --recipient "$BACKUP_GPG_RECIPIENT" "$BACKUP_FILE"
    rm -f "$BACKUP_FILE"
    BACKUP_FILE="${BACKUP_FILE}.gpg"
fi

echo "Бэкап сохранён: $BACKUP_FILE"

# Храним последние 14 бэкапов, старые чистим — иначе shared/backups растёт вечно
find shared/backups -name 'nexus404_backup_*' -type f | sort | head -n -14 | xargs -r rm -f
