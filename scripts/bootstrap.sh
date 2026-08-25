#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Запусти через sudo — deploy.sh дальше обновляет пакеты и создаёт пользователя nyx."
    exit 1
fi

REPO_URL="https://github.com/0xERR404/nynynyx.git"
TARGET_DIR="/opt/nynynyx"   # НЕ /root/* — nyx не сможет туда зайти, /root закрыт для остальных
BRANCH="main"

# Репозиторий приватный — обычный HTTPS-клон попросит логин/пароль (GitHub
# больше не принимает пароль аккаунта для git, только Personal Access Token).
# Если репо публичное — просто нажми Enter, токен не нужен.
if [ -z "${GITHUB_TOKEN:-}" ] && [ ! -d "$TARGET_DIR/.git" ]; then
    read -rp "GitHub token (создать: https://github.com/settings/tokens, scope 'repo'; Enter — если репо публичное): " GITHUB_TOKEN < /dev/tty
fi

if [ -n "${GITHUB_TOKEN:-}" ]; then
    CLONE_URL="https://${GITHUB_TOKEN}@github.com/0xERR404/nynynyx.git"
else
    CLONE_URL="$REPO_URL"
fi

# deploy.sh отдаёт папку проекта пользователю nyx (chown -R). git при этом
# видит, что владелец репозитория — не root, и по умолчанию отказывается
# с ним работать ("dubious ownership"). Явно говорим git, что это ожидаемо.
mkdir -p "$TARGET_DIR"
git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$TARGET_DIR" \
    || git config --global --add safe.directory "$TARGET_DIR"

# modules/ (её правки через apply_file_edit) НЕ отслеживается git вообще —
# см. .gitignore. Поэтому `git reset --hard` ниже физически не может её
# задеть: git трогает только то, что сам же отслеживает. Раньше здесь была
# страховка с авто-коммитом её изменений перед reset — теперь она не нужна,
# потому что нечего коммитить и нечего терять.

if [ -d "$TARGET_DIR/.git" ]; then
    echo "Папка $TARGET_DIR уже существует, синхронизирую ядро с $BRANCH..."
    cd "$TARGET_DIR"
    git fetch --all
    git reset --hard "origin/$BRANCH"
else
    git clone --branch "$BRANCH" "$CLONE_URL" "$TARGET_DIR"
    cd "$TARGET_DIR"
    # Токен остаётся в .git/config (remote url) — это ожидаемо, но не выводим
    # его в лог/историю команд. При желании сменить/убрать: git remote set-url origin <url>
fi

chmod +x scripts/*.sh
exec ./scripts/deploy.sh < /dev/tty
