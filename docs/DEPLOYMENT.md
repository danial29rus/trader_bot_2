# Запуск LVLUP

Всё ставится одной командой `scripts/setup.sh`. Скрипт спрашивает токен бота, ваш Telegram ID, контакт поддержки, ключ ReSellCodes и пароль админки. Пароль базы и секреты он генерирует сам, затем собирает и запускает контейнеры: бот, PostgreSQL, ежедневные бэкапы, админку. Повторный запуск безопасен: Enter оставляет текущие значения.

**Токен — только от нового бота из @BotFather.** Если один токен запущен в двух местах (например, локально и на сервере), бот работает с перебоями. Перед запуском на сервере остановите локальную копию: `docker compose down`.

## Локальная проверка (Mac, Docker Desktop)

```bash
cd trader_bot_2
bash scripts/setup.sh --local
```

- Бот: откройте его в Telegram, нажмите /start.
- Админка: http://localhost:8080.
- Каталог для проверки можно загрузить с небольшим лимитом запросов:
  ```bash
  docker compose run --rm bot python -m scripts.snapshot_import --probe
  docker compose run --rm bot python -m scripts.snapshot_import --usd-rub 95 --markup 15 --budget 300
  ```
- Логи: `docker compose logs -f bot`. Остановить: `docker compose down`. Удалить вместе с локальной базой: `docker compose down -v`.

## Сервер (Ubuntu/Debian, отдельный)

```bash
ssh root@<IP сервера>
apt-get update && apt-get install -y git
git clone <репозиторий LVLUP> ~/lvlup && cd ~/lvlup
bash scripts/setup.sh
```

Docker скрипт поставит сам. Адрес админки он спросит и подставит IP сервера. Админка откроется по `https://<IP или домен>`: Caddy сам получит сертификат Let's Encrypt. В панели провайдера должны быть открыты порты 80 и 443.

Затем загрузите каталог:

```bash
docker compose run --rm bot python -m scripts.snapshot_import --probe
docker compose run --rm bot python -m scripts.snapshot_import --usd-rub 95 --markup 15
```

Если ключ ReSellCodes общий с другим ботом, у них общий дневной лимит поставщика (5000 запросов). Полная выгрузка LVLUP тратит примерно 300–700.

## Обновление кода

```bash
cd ~/lvlup && git pull && docker compose --profile public up -d --build
```

## Бэкапы

Контейнер `db-backup` раз в сутки делает дамп базы и хранит дампы 14 дней (том `lvlup_postgres_backups`). Копируйте их в отдельное хранилище и хотя бы раз проверьте восстановление:

```bash
docker compose exec db-backup ls -lh /backups
```
