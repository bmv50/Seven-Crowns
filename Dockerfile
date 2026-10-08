# СЕМЬ КОРОН — образ бота (Этап 9: деплой).
# Чистый Python-процесс (aiogram + asyncpg), без компиляции C-расширений в рантайме
# сверх колёс PyPI, поэтому multi-stage не нужен — берём стандартный slim.
FROM python:3.12-slim

# PYTHONUNBUFFERED=1  — логи идут в stdout сразу (важно для docker logs / агрегатора).
# PROD=1              — публичный режим: без БД процесс падает (fail-fast, см. bot/main).
# LOG_JSON=1          — структурные JSON-логи (engine/log.py) для сбора в проде.
# PYTHONDONTWRITEBYTECODE=1 — не мусорим .pyc в слое контейнера.
# PYTHONUTF8=1 — половина артов лежит под кириллическими именами (images/mobs/
# волк.jpg), потому что имя файла равно id сущности из data/mobs.yaml. В slim-
# образе локаль не задана, и полагаться на то, что Python сам догадается про
# UTF-8, не хочется: одна неверная кодировка пути — и мобы молча остаются без
# картинок. Явный флаг снимает вопрос.
ENV PYTHONUNBUFFERED=1 \
    PROD=1 \
    LOG_JSON=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEARTBEAT_FILE=/tmp/mud_heartbeat

WORKDIR /app

# Шрифты с кириллицей для PNG-карты (bot/mapgen.py ищет DejaVuSans-Bold.ttf в
# /usr/share/fonts/truetype/dejavu/). В python:*-slim шрифтов нет вовсе —
# фолбэк PIL не умеет кириллицу, карта рисовалась кракозябрами (баг с VPS).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Зависимости отдельным слоем (кэшируется, пока не менялись requirements/constraints).
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Код по белому списку. НЕ копируем: .env (секреты — только через env_file в рантайме),
# tests (нужен живой Postgres и dev-фикстуры; в образе лишний вес — smoke делаем через
# HEALTHCHECK и стартовые логи), TeleMud/ (референс), docs/.
# Арты отдельным слоем и ДО кода: картинки меняются редко, код — часто, и при
# таком порядке правка бота переиспользует закэшированный слой с картинками
# вместо повторной укладки десятков мегабайт.
# Через .dockerignore сюда попадают JPEG/WebP; исходные PNG остаются локально.
COPY images/ ./images/

COPY engine/ ./engine/
COPY bot/ ./bot/
COPY ai/ ./ai/
COPY data/ ./data/
COPY scripts/ ./scripts/

# Non-root: создаём пользователя mud и отдаём ему /app и /tmp (heartbeat пишется в /tmp).
RUN useradd --create-home --uid 10001 mud \
    && chown -R mud:mud /app
USER mud

# HEALTHCHECK: контейнер здоров, пока snapshot_worker свежо трогает heartbeat-файл.
# Если процесс завис/умер — mtime устаревает и Docker метит контейнер unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import pathlib,time,sys; p=pathlib.Path('/tmp/mud_heartbeat'); sys.exit(0 if p.exists() and time.time()-p.stat().st_mtime < 120 else 1)"

CMD ["python", "-m", "bot.main"]
