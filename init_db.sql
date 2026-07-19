-- ─────────────────────────────────────────────────────────────
--  Инициализация базы данных для ЭХО ГЛУБИН
--  Запускать от пользователя postgres:
--      sudo -u postgres psql -f init_db.sql
--  (или скопировать команды вручную в psql)
-- ─────────────────────────────────────────────────────────────

-- Пользователь приложения
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'mud') THEN
      CREATE ROLE mud LOGIN PASSWORD 'mudpass';
   END IF;
END
$$;

-- База данных (CREATE DATABASE нельзя в DO-блоке, поэтому отдельно;
-- если база уже есть — команда выдаст ошибку, это нормально)
-- Выполните вручную, если нужно:
--   CREATE DATABASE mud OWNER mud;

GRANT ALL PRIVILEGES ON DATABASE mud TO mud;

-- Таблицы создаст само приложение при первом запуске (engine/db.py).
-- Расширение pgvector понадобится позже (Этап памяти NPC):
--   CREATE EXTENSION IF NOT EXISTS vector;
