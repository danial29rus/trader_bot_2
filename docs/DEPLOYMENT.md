# Деплой LVLUP рядом с первым ботом

Второй бот — отдельный проект со своей базой, бэкапами и админкой. На том же сервере он живёт в своей папке; Docker Compose называет контейнеры и тома по имени папки, поэтому они не пересекаются с первым ботом.

## 1. Подготовка

1. Создайте нового бота в @BotFather. **Токен первого бота использовать нельзя**: два процесса с одним токеном мешают друг другу получать сообщения.
2. На сервере:
   ```bash
   git clone <репозиторий LVLUP> ~/lvlup && cd ~/lvlup
   cp .env.example .env
   ```
3. В `.env` задайте: `BOT_TOKEN` (новый), `POSTGRES_PASSWORD` и тот же пароль в `DATABASE_URL`, `ADMIN_IDS`, `SUPPORT_CONTACT`, `RESELLCODES_API_KEY`, `ADMIN_JWT_SECRET` (случайная строка 32+ символов). `ADMIN_WEB_PORT=8081` — порт 8080 занят первым ботом.

## 2. Запуск

```bash
docker compose up -d --build
docker compose run --rm admin-api python -m admin_api.password   # хэш пароля → ADMIN_PASSWORD_HASH в .env
docker compose up -d
docker compose logs --tail=50 bot
```

## 3. Каталог

```bash
docker compose run --rm bot python -m scripts.snapshot_import --probe
docker compose run --rm bot python -m scripts.snapshot_import --usd-rub 95 --markup 15
```

Если API-ключ общий с первым ботом, у обоих общий дневной лимит поставщика (5000 запросов). Выгрузка LVLUP тратит примерно 300–700 запросов.

## 4. Админ-панель

Профиль `public` с Caddy **не запускайте**: порты 80/443 уже заняты Caddy первого бота. Откройте панель через SSH-туннель со своего компьютера:

```bash
ssh -L 8081:127.0.0.1:8081 root@<IP сервера>
```

и затем http://localhost:8081. Для постоянного доступа позже можно привязать домен и добавить его в Caddy первого бота.

## 5. Бэкапы

Контейнер `db-backup` раз в сутки делает дамп в том `lvlup_postgres_backups`, хранит 14 дней. Копируйте дампы в отдельное хранилище и хотя бы раз проверьте восстановление.
