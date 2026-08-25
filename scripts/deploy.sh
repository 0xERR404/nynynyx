#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

SYSTEM_USER="nyx"          # системный пользователь на сервере, от него работают контейнеры
SYSTEM_UID=1500            # тот же uid, что и внутри образа (см. hub/Dockerfile) — иначе права на volume разъедутся
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ "$EUID" -ne 0 ]; then
    echo "Запусти через sudo — нужны права на обновление пакетов и создание пользователей."
    exit 1
fi

# --- Безопасный ввод: при ошибке спрашиваем заново, не падаем и не молчим ---
prompt_nonempty() {
    # $1 — текст вопроса, $2 — имя переменной для результата
    local text="$1" __var="$2" value
    while true; do
        read -rp "$text" value < /dev/tty
        if [ -n "$value" ]; then
            printf -v "$__var" '%s' "$value"
            return
        fi
        echo "Пустой ввод не подходит, попробуй снова."
    done
}

prompt_matching_password() {
    # $1 — текст вопроса, $2 — имя переменной для результата
    local text="$1" __var="$2" p1 p2
    while true; do
        read -rsp "$text" p1 < /dev/tty; echo
        if [ -z "$p1" ]; then
            echo "Пароль не может быть пустым, попробуй снова."
            continue
        fi
        read -rsp "Повтори пароль: " p2 < /dev/tty; echo
        if [ "$p1" != "$p2" ]; then
            echo "Пароли не совпали, попробуй снова."
            continue
        fi
        printf -v "$__var" '%s' "$p1"
        return
    done
}

echo "=== NEXUS404 — развёртывание ==="

if [ -f Caddyfile ] && grep -qE '^[a-zA-Z0-9.-]+\s*\{' Caddyfile; then
    DOMAIN=$(head -1 Caddyfile | awk '{print $1}')
    echo "-> Домен уже настроен: $DOMAIN (беру из существующего Caddyfile)"
else
    while true; do
        prompt_nonempty "Домен (например nexus404.xyz), A-запись которого уже указывает на этот сервер: " DOMAIN
        if [[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$ ]]; then
            break
        fi
        echo "Не похоже на домен (example.com), попробуй снова."
    done
fi
echo "Домен: $DOMAIN (A-запись должна уже указывать на этот сервер, иначе Caddy не получит сертификат)"
echo

# --- 1. Обновление системы ---
echo "-> Обновляю пакеты сервера..."
apt-get update -y
apt-get upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"
echo

# --- 2. Docker и docker compose ---
if ! command -v docker &> /dev/null; then
    echo "-> Docker не найден, ставлю..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
else
    echo "-> Docker уже установлен."
fi

if docker compose version &> /dev/null; then
    DC="docker compose"
elif command -v docker-compose &> /dev/null; then
    DC="docker-compose"
else
    echo "-> docker compose plugin не найден, ставлю..."
    apt-get install -y docker-compose-plugin
    DC="docker compose"
fi
echo "-> Использую: $DC"
echo

# Caddy получает сертификат и обслуживает сайт на 80/443 — если ufw активен
# (частая настройка у хостеров по умолчанию) и эти порты не открыты, сайт
# физически недостижим снаружи, хотя контейнеры внутри выглядят живыми.
if command -v ufw &> /dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp
    ufw allow 443/tcp
    echo "-> ufw: порты 80/tcp и 443/tcp открыты для Caddy."
fi
echo

# --- 3. Твоя личная sudo-учётка — чтобы не сидеть на сервере под root ---
echo "Хочешь создать личную учётку с sudo для входа на сервер (вместо root)?"
while true; do
    read -rp "Имя пользователя (Enter — пропустить этот шаг и root/SSH не трогать): " ADMIN_USER < /dev/tty
    if [ -z "$ADMIN_USER" ]; then
        break
    fi
    if [[ "$ADMIN_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
        break
    fi
    echo "Некорректное имя (латиница в нижнем регистре, цифры, _/-, с буквы или _). Попробуй снова или Enter, чтобы пропустить."
done
if [ -n "$ADMIN_USER" ]; then
    if id "$ADMIN_USER" &> /dev/null; then
        echo "-> Пользователь $ADMIN_USER уже есть, пропускаю создание."
    else
        useradd -m -s /bin/bash "$ADMIN_USER"
        usermod -aG sudo,docker "$ADMIN_USER"
        echo "-> Задай пароль для $ADMIN_USER:"
        until passwd "$ADMIN_USER" < /dev/tty; do
            echo "Не получилось — попробуй ещё раз."
        done
        if [ -f /root/.ssh/authorized_keys ]; then
            mkdir -p "/home/$ADMIN_USER/.ssh"
            cp /root/.ssh/authorized_keys "/home/$ADMIN_USER/.ssh/authorized_keys"
            chown -R "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
            chmod 700 "/home/$ADMIN_USER/.ssh"
            chmod 600 "/home/$ADMIN_USER/.ssh/authorized_keys"
            echo "-> SSH-ключи root скопированы для $ADMIN_USER — можешь заходить сразу под ним."
        fi
        echo "-> Готово: $ADMIN_USER теперь может sudo и docker."
    fi

    # --- 3.1 SSH-порт и блокировка root — только если личная учётка реально есть,
    # иначе это верный способ потерять доступ к серверу насовсем.
    echo
    echo "Сейчас перенесём SSH на другой порт и заблокируем вход под root."
    echo "Твоя текущая SSH-сессия не оборвётся, но НОВЫЕ подключения пойдут уже иначе."
    read -rp "Новый порт для SSH (Enter — оставить 22, но root всё равно заблокируется): " SSH_PORT < /dev/tty
    SSH_PORT="${SSH_PORT:-22}"
    while ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || [ "$SSH_PORT" -lt 1 ] || [ "$SSH_PORT" -gt 65535 ]; do
        echo "Порт должен быть числом от 1 до 65535."
        read -rp "Новый порт для SSH (Enter — оставить 22): " SSH_PORT < /dev/tty
        SSH_PORT="${SSH_PORT:-22}"
    done

    set_sshd_option() {
        local key="$1" value="$2"
        if grep -qE "^\s*#?\s*${key}\b" /etc/ssh/sshd_config; then
            sed -i "s/^\s*#\?\s*${key}\b.*/${key} ${value}/" /etc/ssh/sshd_config
        else
            echo "${key} ${value}" >> /etc/ssh/sshd_config
        fi
    }
    set_sshd_option "Port" "$SSH_PORT"
    set_sshd_option "PermitRootLogin" "no"

    # На части современных систем (Ubuntu 22.04+) SSH запускается через
    # systemd socket activation — реальный порт слушает ssh.socket, а не
    # sshd напрямую, и правка Port в sshd_config тогда НЕ действует сама
    # по себе. Проверяем и правим оба возможных места.
    SSH_SOCKET_ACTIVE=false
    if systemctl list-unit-files ssh.socket &> /dev/null && systemctl is-enabled ssh.socket &> /dev/null; then
        SSH_SOCKET_ACTIVE=true
        mkdir -p /etc/systemd/system/ssh.socket.d
        cat > /etc/systemd/system/ssh.socket.d/override.conf << EOF
[Socket]
ListenStream=
ListenStream=${SSH_PORT}
EOF
        systemctl daemon-reload
    fi

    UFW_ACTIVE=false
    if command -v ufw &> /dev/null && ufw status | grep -q "Status: active"; then
        UFW_ACTIVE=true
        ufw allow "${SSH_PORT}/tcp"
    fi

    if [ "$SSH_SOCKET_ACTIVE" = true ]; then
        systemctl restart ssh.socket
    fi
    systemctl restart ssh

    echo "-> Проверяю, что sshd реально слушает порт ${SSH_PORT}..."
    PORT_OK=false
    for _ in 1 2 3 4 5; do
        if ss -tln | grep -q ":${SSH_PORT} "; then
            PORT_OK=true
            break
        fi
        sleep 1
    done

    if [ "$PORT_OK" = true ]; then
        echo "-> Порт ${SSH_PORT} подтверждён (sshd слушает)."
        if [ "$UFW_ACTIVE" = true ] && [ "$SSH_PORT" != "22" ]; then
            ufw delete allow 22/tcp 2>/dev/null || true
            echo "-> ufw: старый порт 22 закрыт."
        fi
        echo "root заблокирован, порт SSH: ${SSH_PORT}."
    else
        echo "!! Не вижу sshd на порту ${SSH_PORT} — root/22 НЕ трогаю, проверь вручную:"
        echo "     systemctl status ssh.socket ssh.service"
        echo "     ss -tln | grep ssh"
        echo "     journalctl -u ssh -u ssh.socket -n 30"
    fi
    echo "-> Проверь доступ В НОВОМ ОКНЕ ТЕРМИНАЛА, не закрывая текущую сессию:"
    echo "     ssh -p ${SSH_PORT} ${ADMIN_USER}@<ip>"
fi
echo

# --- 4. Пользователь nyx — контейнеры работают от него, БЕЗ sudo, отдельно от твоей учётки ---
if ! id "$SYSTEM_USER" &> /dev/null; then
    echo "-> Создаю сервисного пользователя $SYSTEM_USER (uid $SYSTEM_UID)..."
    useradd -m -u "$SYSTEM_UID" -s /bin/bash "$SYSTEM_USER"
else
    echo "-> Пользователь $SYSTEM_USER уже есть."
fi
usermod -aG docker "$SYSTEM_USER"
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$SCRIPT_DIR"
echo

# --- 5. DEEPSEEK_API_KEY ---
if [ -f .env ] && grep -q "^DEEPSEEK_API_KEY=" .env; then
    echo "-> .env уже содержит ключ, оставляю как есть."
else
    prompt_nonempty "Введи DEEPSEEK_API_KEY: " DEEPSEEK_API_KEY
    echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}" > .env
fi
chown "$SYSTEM_USER:$SYSTEM_USER" .env
echo

# --- 6. Логин и пароль для входа в чат (Caddy basic auth, временно до authscope) ---
prompt_nonempty "Логин для входа в чат: " CADDY_LOGIN
while [[ "$CADDY_LOGIN" =~ [[:space:]] ]]; do
    echo "Логин не должен содержать пробелов."
    prompt_nonempty "Логин для входа в чат: " CADDY_LOGIN
done
prompt_matching_password "Пароль: " CADDY_PASSWORD

CADDY_HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$CADDY_PASSWORD")
[ -f Caddyfile.template ] || { echo "Caddyfile.template не найден."; exit 1; }
sed -e "s/__DOMAIN__/${DOMAIN}/" -e "s/__CADDY_LOGIN__/${CADDY_LOGIN}/" -e "s/__CADDY_HASH__/${CADDY_HASH//\//\\/}/" Caddyfile.template > Caddyfile
chown "$SYSTEM_USER:$SYSTEM_USER" Caddyfile
echo "-> Caddyfile готов."
echo

# --- 7. Дефолты Никс/интерфейса ---
# hub/seed/ — плоский список файлов, без вложенных папок. Данные (память,
# промпт, факты, реплики, интерфейс) — только если их ещё нет (её правки не
# трогаем). Код (router.py/manifest.json) — обновляется всегда, это не её
# содержимое, а логика чата: правки из git должны долетать до сервера.
mkdir -p modules/nyx modules/interface

DATA_FILES=(
    "nyx-memory.json:modules/nyx/memory.json"
    "nyx-prompt.md:modules/nyx/prompt.md"
    "nyx-facts.json:modules/nyx/facts.json"
    "nyx-replies.json:modules/nyx/replies.json"
)
for pair in "${DATA_FILES[@]}"; do
    src="hub/seed/${pair%%:*}"
    dst="${pair##*:}"
    [ -f "$dst" ] || cp "$src" "$dst"
done

# CODE_FILES — не её содержимое, а код/конвенции, которые пишу я. Обновляются
# ВСЕГДА при каждом деплое: nyx_readme.md — актуальная конвенция для модулей,
# modules/interface/* — чат-оболочка, которая "должна жить всегда" и которую
# она не трогает никогда ни при каких обстоятельствах, поэтому здесь нечего
# защищать от перезаписи — наоборот, редеплой обязан гарантированно вернуть
# канонический вариант, даже если что-то туда случайно попало в обход правил.
CODE_FILES=(
    "nyx-router.py:modules/nyx/router.py"
    "nyx-manifest.json:modules/nyx/manifest.json"
    "nyx_readme.md:modules/nyx_readme.md"
    "interface-index.html:modules/interface/index.html"
    "interface-app.js:modules/interface/app.js"
    "interface-style.css:modules/interface/style.css"
    "interface-manifest.webmanifest:modules/interface/manifest.webmanifest"
    "interface-sw.js:modules/interface/sw.js"
    "interface-icon-192.png:modules/interface/icon-192.png"
    "interface-icon-512.png:modules/interface/icon-512.png"
)
for pair in "${CODE_FILES[@]}"; do
    cp "hub/seed/${pair%%:*}" "${pair##*:}"
done

for f in modules/nyx/memory.json modules/nyx/prompt.md modules/nyx/facts.json modules/nyx/replies.json; do
    [ -f "$f" ] || { echo "Не хватает $f даже после сева — проверь hub/seed/."; exit 1; }
done

mkdir -p modules/nyx/data/threads modules/nyx/data/inbox modules/nyx/data/outbox modules/nyx/logs \
         modules/interface/logs hub/internal shared/db shared/logs shared/backups
chown -R "$SYSTEM_USER:$SYSTEM_USER" "$SCRIPT_DIR"
echo

# --- 8. Поднимаем — от имени nyx, не root ---
echo "-> Собираю и запускаю контейнеры от имени $SYSTEM_USER..."
su - "$SYSTEM_USER" -c "cd '$SCRIPT_DIR' && $DC up -d --build"

echo
echo "=== Готово ==="
echo "Открой: https://${DOMAIN}"
echo "Логин в чат: ${CADDY_LOGIN} / пароль, который ты только что задал"
if [ -n "$ADMIN_USER" ]; then
    if [ "${PORT_OK:-false}" = true ]; then
        echo "Вход на сервер: ssh -p ${SSH_PORT} $ADMIN_USER@<ip> (sudo доступен, root заблокирован)"
    else
        echo "Вход на сервер: ssh -p 22 $ADMIN_USER@<ip> (root заблокирован; порт ${SSH_PORT} НЕ подтвердился при проверке — проверь ss -tln | grep ssh)"
    fi
fi
echo
echo "Контейнеры работают от имени '$SYSTEM_USER' — без sudo, отдельно от твоей учётки:"
echo "  su - $SYSTEM_USER -c 'cd $SCRIPT_DIR && $DC logs -f hub'"
echo "  su - $SYSTEM_USER -c 'cd $SCRIPT_DIR && $DC restart hub'"
echo "  ./scripts/backup.sh"
echo
echo "Примечание: сам Docker-демон всегда работает от root (архитектура Docker,"
echo "без rootless-режима иначе не бывает) — но и ты, и контейнеры работаете"
echo "не под root: ты — под своей sudo-учёткой, хаб — под nyx."
