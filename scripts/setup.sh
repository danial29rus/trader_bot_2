#!/usr/bin/env bash
# One-command install for HonestPlay.
#
#   On a fresh Ubuntu/Debian server (as root, from the project folder):
#       bash scripts/setup.sh
#   Locally (Docker Desktop must be running):
#       bash scripts/setup.sh --local
#
# Safe to re-run: existing .env values are kept unless you type a new one, and
# generated secrets are only created when missing.
set -euo pipefail

MODE=server
[[ "${1:-}" == "--local" ]] && MODE=local
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;35m▶ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

get_env() { { grep -E "^$1=" .env 2>/dev/null || true; } | tail -1 | cut -d= -f2- | sed -E "s/^'(.*)'$/\1/"; }
set_env() {
  local key=$1 value=$2 tmp
  tmp=$(mktemp)
  grep -vE "^$key=" .env > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}
secret() { head -c 64 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-"$1"; }
is_placeholder() { [[ -z "$1" || "$1" == replace_me || "$1" == change_me || "$1" == 123456789 || "$1" == @your_support ]]; }

# ask KEY "question" [hidden]: keeps the current value on Enter, shows only its edges.
ask() {
  local key=$1 question=$2 hidden=${3:-} current value shown
  current=$(get_env "$key")
  if is_placeholder "$current"; then shown=""; else shown=" [сейчас: ${current:0:6}…${current: -4}, Enter — оставить]"; fi
  while true; do
    if [[ -n "$hidden" ]]; then read -rsp "$question$shown: " value; echo; else read -rp "$question$shown: " value; fi
    value=${value:-$current}
    is_placeholder "$value" || break
    echo "  Нужно значение."
  done
  set_env "$key" "$value"
}

say "Проверяю Docker"
if ! command -v docker >/dev/null 2>&1; then
  [[ $MODE == local ]] && die "Установите и запустите Docker Desktop, затем повторите."
  [[ $EUID -eq 0 ]] || die "Запустите от root: sudo bash scripts/setup.sh"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "Нужен Docker Compose v2 (docker compose)."
docker info >/dev/null 2>&1 || die "Docker не запущен."

say "Настраиваю .env"
[[ -f .env ]] || cp .env.example .env
echo "Токен — от НОВОГО бота из @BotFather. Токен другого бота использовать нельзя."
ask BOT_TOKEN "BOT_TOKEN" hidden
ask ADMIN_IDS "Ваш Telegram ID (узнать: @userinfobot), несколько — через запятую"
ask SUPPORT_CONTACT "Контакт поддержки, например @honestplay_support"
ask RESELLCODES_API_KEY "API-ключ ReSellCodes" hidden

db_user=$(get_env POSTGRES_USER); db_user=${db_user:-dkmarket}
db_name=$(get_env POSTGRES_DB); db_name=${db_name:-dkmarket}
db_pass=$(get_env POSTGRES_PASSWORD)
if is_placeholder "$db_pass"; then db_pass=$(secret 32); fi
set_env POSTGRES_USER "$db_user"; set_env POSTGRES_DB "$db_name"; set_env POSTGRES_PASSWORD "$db_pass"
set_env DATABASE_URL "postgresql+asyncpg://$db_user:$db_pass@db:5432/$db_name"

jwt=$(get_env ADMIN_JWT_SECRET)
(( ${#jwt} >= 32 )) || set_env ADMIN_JWT_SECRET "$(secret 48)"
set_env ADMIN_WEB_BIND 127.0.0.1
set_env ADMIN_WEB_PORT "$(get_env ADMIN_WEB_PORT | grep -E '^[0-9]+$' || echo 8080)"

if [[ $MODE == server ]]; then
  detected=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
  host=$(get_env PUBLIC_HOST)
  read -rp "Адрес админки — IP или домен сервера [${host:-$detected}]: " answer
  set_env PUBLIC_HOST "${answer:-${host:-$detected}}"
fi
chmod 600 .env

say "Собираю контейнеры (первый раз — несколько минут)"
docker compose build

admin_user=$(get_env ADMIN_USERNAME); admin_user=${admin_user:-admin}
set_env ADMIN_USERNAME "$admin_user"
if [[ -z "$(get_env ADMIN_PASSWORD_HASH)" ]]; then
  say "Пароль для веб-админки (логин: $admin_user)"
  while true; do
    read -rsp "Пароль (от 12 символов): " p1; echo
    read -rsp "Повторите: " p2; echo
    [[ "$p1" == "$p2" ]] || { echo "  Не совпадают."; continue; }
    (( ${#p1} >= 12 )) || { echo "  Слишком короткий."; continue; }
    break
  done
  hash=$(printf '%s' "$p1" | docker compose run --rm -T --no-deps admin-api \
    python -c "import sys; from argon2 import PasswordHasher; print(PasswordHasher().hash(sys.stdin.read()))" | tail -1)
  # Single quotes keep the "$" characters of the hash away from Compose interpolation.
  set_env ADMIN_PASSWORD_HASH "'$hash'"
  unset p1 p2
fi

say "Запускаю"
if [[ $MODE == server ]]; then
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then ufw allow 80/tcp && ufw allow 443/tcp; fi
  docker compose --profile public up -d
else
  docker compose up -d
fi

sleep 5
docker compose ps
docker compose logs --tail=15 bot

admin_url="http://localhost:$(get_env ADMIN_WEB_PORT)"
[[ $MODE == server ]] && admin_url="https://$(get_env PUBLIC_HOST)"
cat <<EOF

✅ Готово.
   Бот:     откройте его в Telegram и нажмите /start
   Админка: $admin_url  (логин $admin_user)

Дальше — каталог:
   docker compose run --rm bot python -m scripts.snapshot_import --probe
   docker compose run --rm bot python -m scripts.snapshot_import --usd-rub 95 --markup 15
EOF
