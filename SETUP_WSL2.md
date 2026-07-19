# 🛠 Этап 0 — Локальное развёртывание на WSL2

Цель: поднять текущий движок игры (v3) локально на ноуте, с PostgreSQL, и убедиться, что бот запускается и отвечает в Telegram. ИИ-слой пока выключен (`AI_PROVIDER=none`) — он подключится на следующих этапах.

> **Все команды выполняются ВНУТРИ WSL2** (терминал Ubuntu), а НЕ в PowerShell. Открой «Ubuntu» из меню Пуск или набери `wsl` в терминале Windows.

---

## Шаг 1. Проверка базовых инструментов

```bash
python3 --version      # нужен 3.11+
git --version
psql --version 2>/dev/null || echo "PostgreSQL ещё не установлен — поставим в Шаге 3"
```

Если Python младше 3.11 или отсутствует:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

---

## Шаг 2. Получить файлы проекта

Распакуй присланный архив проекта в домашнюю директорию WSL, например в `~/mud`. Если переносишь из Windows, клади в Linux-раздел (`~/`), **не** в `/mnt/c/...` — на Windows-разделе Python и Postgres работают медленнее и капризнее.

```bash
cd ~
# (распакуй архив сюда, чтобы получился каталог ~/mud/mud2 или ~/mud)
cd ~/mud           # или туда, где лежит requirements.txt
ls                 # должны быть: bot/ engine/ data/ requirements.txt .env.example
```

---

## Шаг 3. Установить PostgreSQL

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib

# Запустить сервер (в WSL2 systemd может быть выключен, поэтому так):
sudo service postgresql start

# Проверить, что работает:
sudo -u postgres psql -c "SELECT version();"
```

Создать пользователя и базу для игры:
```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE mud LOGIN PASSWORD 'mudpass';
CREATE DATABASE mud OWNER mud;
GRANT ALL PRIVILEGES ON DATABASE mud TO mud;
SQL
```

Проверить подключение под новым пользователем:
```bash
psql "postgresql://mud:mudpass@localhost:5432/mud" -c "\conninfo"
```
Если выводит «You are connected to database "mud"...» — БД готова.

---

## Шаг 4. Виртуальное окружение Python и зависимости

```bash
cd ~/mud
python3 -m venv .venv
source .venv/bin/activate      # активировать (в начале строки появится (.venv))
pip install --upgrade pip
pip install -r requirements.txt
```

> Каждый раз перед запуском бота активируй окружение: `source .venv/bin/activate`

---

## Шаг 5. Настроить .env

```bash
cp .env.example .env
nano .env       # или открой через VS Code
```

Заполни:
- `BOT_TOKEN` — получи у [@BotFather](https://t.me/BotFather): `/newbot`, следуй инструкциям, скопируй токен.
- `DATABASE_URL` — оставь как есть, если использовал логин/пароль из Шага 3:
  `postgresql://mud:mudpass@localhost:5432/mud`
- `AI_PROVIDER=none` — пока оставь выключенным.

Сохрани (в nano: `Ctrl+O`, `Enter`, `Ctrl+X`).

---

## Шаг 6. Проверить контент и запустить бота

```bash
# Активируй окружение, если ещё не активно:
source .venv/bin/activate

# Проверка целостности игрового контента:
python -m engine.content
# Ожидается: ✅ Контент валиден: 15 комнат, 12 мобов, ...

# Запуск бота:
python -m bot.main
```

Если всё хорошо, в консоли появится:
```
✅ PostgreSQL подключён, загружено персонажей: 0
⚔️  СЕМЬ КОРОН ... запущена. Реал-тайм цикл активен.
```

Теперь открой своего бота в Telegram, напиши `/start` — должен предложить выбор расы.

Остановить бота: `Ctrl+C`.

---

## Шаг 7. VS Code + WSL (удобная разработка)

1. В VS Code установи расширение **WSL** (от Microsoft).
2. Открой проект: в WSL-терминале из каталога проекта набери `code .` — VS Code откроется уже подключённым к WSL.
3. Выбери интерпретатор Python: `Ctrl+Shift+P` → «Python: Select Interpreter» → укажи `./.venv/bin/python`.

---

## Частые проблемы

**`psql: could not connect to server`** — Postgres не запущен. Выполни `sudo service postgresql start`. В WSL2 он не стартует сам после перезагрузки — либо запускай вручную, либо включи systemd (создай `/etc/wsl.conf` с `[boot]\nsystemd=true` и перезапусти WSL через `wsl --shutdown` в PowerShell).

**`password authentication failed`** — пароль в `.env` не совпадает с заданным в Шаге 3. Проверь строку `DATABASE_URL`.

**`ModuleNotFoundError`** — не активировано окружение. Выполни `source .venv/bin/activate`.

**Бот не отвечает в Telegram** — проверь, что `BOT_TOKEN` верный и бот запущен (консоль показывает «запущена»). Один токен — один запущенный экземпляр.

**`Unauthorized` при старте** — неверный `BOT_TOKEN`.

---

## Что дальше (Этап 0, продолжение)

После того как бот заработал локально, следующие шаги фундамента:
1. **Git-репозиторий** — `git init`, первый коммит (файл `.gitignore` уже готов, секреты не попадут).
2. **Абстракция провайдера моделей** (`ai/` пакет) — чтобы один код работал и с локальной Ollama на ноуте, и с облаком на VPS.
3. **Установка Ollama** на ноут (использует твою RTX 4060) для локальной разработки ИИ-NPC — бесплатно.
4. **Структурное логирование** LLM-вызовов с первого дня.

Эти шаги пойдут по дорожной карте из аналитического отчёта (Этап 0 → Этап 1 → масштаб).
