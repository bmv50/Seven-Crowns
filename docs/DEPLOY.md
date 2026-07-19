# Развёртывание и эксплуатация — СЕМЬ КОРОН (Этап 9)

Регламент беты: новый сервер поднимается **одной документированной командой**,
бэкап реально восстанавливается, рестарт не теряет экономику, падение воркеров
даёт сигнал, а в проде без БД процесс падает сразу (fail-fast).

> **Что НЕ проверено в песочнице.** Живой `docker build`, реальный `docker
> compose up`, `pg_dump`/`pg_restore` на настоящей БД и Telegram long-polling в
> контейнере в среде разработки не запускались (нет Docker и внешней сети).
> Конфиги провалидированы синтаксически: `docker-compose.yml` — через
> `yaml.safe_load`; backup-цикл и healthcheck — через `sh -n` после эмуляции
> `$$→$` подстановки compose; скрипты — `sh -n`; Dockerfile — ручной вычиткой.
> Пункты, требующие живого прогона у владельца, помечены **[проверить вживую]**.

---

## 1. Быстрый старт через Docker (рекомендуется)

Требуется Docker Engine 24+ с плагином Compose v2.

```sh
# 1. Установить Docker + compose (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
# (compose-плагин ставится вместе с Docker Engine; проверьте: docker compose version)

# 2. Получить код
git clone <URL-репозитория> seven-crowns && cd seven-crowns

# 3. Конфигурация
cp .env.example .env
#   Обязательно заполнить в .env:
#     BOT_TOKEN           — токен от @BotFather (без заглушки ВСТАВЬ_/PASTE_)
#     POSTGRES_PASSWORD   — надёжный пароль БД
#     DATABASE_URL        — раскомментировать docker-строку (host = postgres):
#                           postgresql://mud:ПАРОЛЬ@postgres:5432/mud
#     ADMIN_IDS           — ваши Telegram uid (для /admin, Health, алертов)
#   Пароль в POSTGRES_PASSWORD и в DATABASE_URL должен совпадать.

# 4. Запуск (сборка + поднятие всех сервисов в фоне) — ОДНА КОМАНДА
docker compose up -d --build
```

Поднимаются три сервиса:

| Сервис     | Роль                                                                 |
|------------|----------------------------------------------------------------------|
| `postgres` | PostgreSQL 16, том `pgdata`, порт наружу **не** проброшен            |
| `bot`      | Игровой процесс (`PROD=1`, `LOG_JSON=1`), ждёт `postgres` healthy    |
| `backup`   | Суточный `pg_dump` в `./backups` в 04:00 UTC, ротация >14 дней        |

### Проверка после запуска

```sh
docker compose ps                 # bot и postgres — Up (healthy)
docker compose logs -f bot        # ищем "СЕМЬ КОРОН v3 запущена"
docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q bot)
```

Здоровье `bot` определяется heartbeat-файлом `/tmp/mud_heartbeat`, который
`snapshot_worker` трогает каждые ~3 c; если процесс завис, mtime устаревает и
контейнер уходит в `unhealthy`. **[проверить вживую]**

Логи — структурный JSON (одна строка = одно событие: `ts, level, logger, event,
uid?, cid?, …`), удобно скармливать в агрегатор (loki/ELK). Человекочитаемый
режим — убрать `LOG_JSON=1`.

---

## 2. Бэкап, восстановление, restore-test

Дампы кладутся в `./backups` (том сервиса `backup` и рабочий каталог скриптов).

### Ручной бэкап

```sh
# из контейнера backup (в нём есть pg_dump и доступ к БД):
docker compose exec backup sh /scripts/backup.sh   # если /scripts смонтирован
# либо с хоста при наличии клиента postgresql-client и доступа к БД:
DATABASE_URL=postgresql://mud:ПАРОЛЬ@localhost:5432/mud sh scripts/backup.sh
```

`scripts/backup.sh` → `pg_dump -Fc` (сжатый custom-формат) в
`backups/mud_YYYYMMDD_HHMMSS.dump` + удаление дампов старше 14 дней.

### Восстановление (ОПАСНО — двухшаговое с подтверждением)

```sh
DATABASE_URL=postgresql://mud:ПАРОЛЬ@localhost:5432/mud \
  sh scripts/restore.sh backups/mud_YYYYMMDD_HHMMSS.dump
# скрипт печатает предупреждение и ждёт ввод YES; далее dropdb → createdb → pg_restore
```

Целевая база пересоздаётся полностью. Перед восстановлением на боевом сервере
остановите `bot` (`docker compose stop bot`), после — запустите снова.

### Обязательный restore-test (проверка, что бэкап рабочий)

```sh
DATABASE_URL=postgresql://mud:ПАРОЛЬ@localhost:5432/mud \
  sh scripts/restore_test.sh   # берёт свежайший дамп из backups/
```

Скрипт поднимает **временную** базу `mud_restore_test`, льёт в неё дамп, делает
проверочные `SELECT` (`characters`, `kv_state`, целостность `economy_ledger`
суммой), затем дропает временную базу. `exit 0` — бэкап восстанавливается,
`exit 1` — проблема. Рекомендуется гонять его периодически (напр. cron раз в
неделю) и после каждого изменения схемы. **[проверить вживую]**

> **Почему рестарт не теряет экономику.** Золото/инвентарь пишутся
> транзакционно (`economy_ledger`, `econ_tx`/`guild_tx`), состояние мира,
> аукциона, территорий, LLM-бюджетов — в `kv_state` (снимок раз в 60 c и при
> graceful shutdown). После рестарта всё поднимается из БД (см. `main()`).

---

## 3. Обновление

```sh
git pull
docker compose up -d --build      # пересборка образа bot и рестарт
```

Миграции схемы применяются автоматически при старте (`engine/db.py`:
`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, всё
идемпотентно и прогоняется на каждом подключении). Отдельная таблица
`schema_version` **не заведена** осознанно — см. п. 6.

Перед крупным обновлением снимите бэкап (`scripts/backup.sh`) и прогоните
restore-test.

---

## 4. Мониторинг и сигналы о падении

- **Heartbeat + HEALTHCHECK** — контейнер `bot` метится `unhealthy`, если
  процесс перестал обновлять `/tmp/mud_heartbeat` (>120 c). С
  `restart: unless-stopped` Docker перезапустит зависший контейнер.
- **Watchdog воркеров** — задача внутри процесса раз в 60 c проверяет живость
  `game_loop`, `notify_worker`, `god_worker`, `snapshot_worker`. Упавший воркер
  логируется (`worker_died`) и перезапускается — до 3 раз в час; при исчерпании
  лимита шлётся алерт **всем `ADMIN_IDS`** («⚠️ Воркер X упал…») и пишется
  `worker_restart_exhausted`.
- **Админ-экран `/admin → 💚 Health`** — аптайм, живость воркеров, длительность
  тика (last/avg), задержка БД (`SELECT 1`), счётчик Telegram 429, очереди
  записи/уведомлений, последние ошибки из кольцевого буфера.

---

## 5. Альтернатива без Docker (systemd)

Нужны Python 3.12+, PostgreSQL 16, установленные зависимости.

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -c constraints.txt
cp .env.example .env   # заполнить BOT_TOKEN, DATABASE_URL (host=localhost), ADMIN_IDS
```

`/etc/systemd/system/seven-crowns.service`:

```ini
[Unit]
Description=Seven Crowns MUD bot
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=mud
WorkingDirectory=/opt/seven-crowns
EnvironmentFile=/opt/seven-crowns/.env
Environment=PROD=1
Environment=LOG_JSON=1
Environment=HEARTBEAT_FILE=/run/seven-crowns/heartbeat
RuntimeDirectory=seven-crowns
ExecStart=/opt/seven-crowns/.venv/bin/python -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now seven-crowns
journalctl -u seven-crowns -f      # JSON-логи
```

Бэкапы — cron на `scripts/backup.sh`, restore-test — cron на
`scripts/restore_test.sh`. **[проверить вживую]**

---

## 6. Проектные решения

- **Таблица `schema_version` — НЕ заведена.** Текущие миграции уже де-факто
  идемпотентны и forward-only (`CREATE … IF NOT EXISTS`, `ADD COLUMN IF NOT
  EXISTS`) и повторно прогоняются на каждом старте. Полноценный фреймворк
  версионирования (нумерованные ревизии, up/down) для одно-процессного бота без
  ветвящихся окружений — церемония без выигрыша и лишний риск рассинхрона.
  Решение пересмотреть, если появятся деструктивные миграции (переименование/
  удаление колонок), которые нельзя выразить идемпотентно.
- **Что в образе.** Только рантайм: `engine/ bot/ ai/ data/ scripts/` +
  `requirements/constraints`. Исключены `.env` (секреты — через `env_file`),
  тесты и симуляции (нужен живой Postgres и фикстуры; smoke делается через
  HEALTHCHECK и стартовые логи), `TeleMud/`, `images/` (240+ МБ), `docs/`.
- **Политика watchdog.** До 3 авто-перезапусков воркера в час; дальше —
  только громкий лог + алерт админам (перезапуски прекращаются, чтобы не
  зациклить рестарт-шторм на устойчивой ошибке).
