#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ $# -ne 1 ]; then
    echo "Использование: ./scripts/restore.sh shared/backups/nexus404_backup_XXXXXXXX_XXXXXX.tar.gz[.gpg]"
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
    echo "Файл не найден: $BACKUP_FILE"
    exit 1
fi

if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    DC="docker compose"
else
    DC="docker-compose"
fi

echo "-> Останавливаю hub перед восстановлением (иначе возможна гонка записи)..."
$DC stop hub 2>/dev/null || true

WORK_FILE="$BACKUP_FILE"
if [[ "$BACKUP_FILE" == *.gpg ]]; then
    echo "-> Архив зашифрован, расшифровываю..."
    WORK_FILE="${BACKUP_FILE%.gpg}"
    gpg --output "$WORK_FILE" --decrypt "$BACKUP_FILE"
fi

echo "Восстанавливаю из $WORK_FILE ..."
tar -xzf "$WORK_FILE" -C .

if [ "$WORK_FILE" != "$BACKUP_FILE" ]; then
    rm -f "$WORK_FILE"  # расшифрованная копия не должна оставаться на диске
fi

echo "-> Запускаю hub обратно..."
$DC start hub

echo "Готово."
