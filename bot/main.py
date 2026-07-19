# -*- coding: utf-8 -*-
"""
СЕМЬ КОРОН v3 — настоящее MUD-ядро для Telegram.
Классы, скиллы, реал-тайм бой, PostgreSQL, кооп.

Запуск:
    pip install "aiogram>=3,<4" asyncpg pyyaml
    export BOT_TOKEN="..."
    export DATABASE_URL="postgresql://user:ВСТАВЬ_СВОЙ_ПАРОЛЬ@localhost/mud"
    python -m bot.main      (из каталога mud2/)
"""
import asyncio
import json
import os
import random
import sys
import uuid

# Загрузка .env (если установлен python-dotenv и файл существует)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Флаги «живого мира» включены ПО УМОЛЧАНИЮ — `python -m bot.main` запускает их
# без дополнительной настройки. Любой флаг можно отключить через окружение или
# .env, напр. NPC_AI=0. setdefault — значит явно заданное значение (env/.env)
# имеет приоритет над этим дефолтом.
# ВАЖНО: до импорта engine.content, чтобы WILD_ZONES успел примениться при загрузке мира.
# RULES_V2 теперь В ЭТОМ КОРТЕЖЕ (спринт 7): единственный блокер — профиль spirit,
# резистивший ВСЕ физ. типы (bash/pierce/slash) — снят решением лида: дробящее
# (bash) сокрушает бесплотную форму и проходит, режущее/колющее по-прежнему
# резистится (см. комментарий у rules2.ENABLED ниже и rules2._CAT_PROFILE["spirit"]).
for _flag in ("NPC_AI", "LAZY_SIM", "WILD_ZONES", "WORLD_EVENTS", "SEASONS", "NOTIFY", "RULES_V2"):
    os.environ.setdefault(_flag, "1")

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, FSInputFile, BufferedInputFile,
                           InlineKeyboardButton, InlineKeyboardMarkup, BotCommand)
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.session.aiohttp import AiohttpSession

from engine import content
from engine.content import validate, CLASSES, SKILLS, ITEMS, WORLD, MOBS, RACES, QUESTS
from engine.character import Character
from engine.world import World, ground_items_for, take_ground_item
from engine.loop import GameLoop
from engine.db import Database
from engine.persist import CharDirtySet
from engine import persist as _persist
from engine import log as _elog
from engine import textsafe as _ts
from engine import combat
from engine import quest
from engine import errands
from engine import npc as npclib
from engine import skills as skillmod
from engine import money
from engine import achievements
from engine import reputation
from engine import arena
from engine import talents
from engine import bestiary
from engine import daily
from engine import weekly
from engine import streak
from engine import tutorial
from engine import starter as _starter          # Этап 4.1: стартовый набор по классу
from engine import uigate as _uigate            # Этап 4.2: прогрессивный UI (гейты фич)
from engine import referral
from engine import analytics
from engine.guild import GuildManager
from engine import guild as guildlib
from engine.social import PartyManager, DuelManager
from ai import npc_ai
from ai import memory as _aimem   # долгая память NPC (наставник помнит первую встречу)
from bot import ui
from bot import mapgen
from bot import commands as cmds
from bot import config_check   # Этап 9: fail-fast валидация окружения на старте

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

# Гибридное ядро правил (типы урона/резисты, спасброски, мультиатака, мировоззрение).
# Включается переменной окружения RULES_V2=1; ВКЛЮЧЕНО ПО УМОЛЧАНИЮ (спринт 7,
# см. setdefault выше) — отключить явным RULES_V2=0.
#
# ИСТОРИЯ (спринт 5): первый замер (все 6 классов, ур.~15/~45, Monte-Carlo
# N=350×30 раундов) показал, что rules2.num_attacks (мультиатака) сама по себе
# давала ПОЛНЫЙ урон за каждую доп. атаку — кратный прирост DPS: +51%..+202%
# против целевого коридора ±25%.
#
# БАЛАНСИРОВКА (спринт 6): rules2.multiattack_scale(n) ослабляет КАЖДЫЙ
# отдельный удар серии из n атак так, чтобы суммарный урон серии рос
# УМЕРЕННО и линейно (+12% за 2-ю атаку, +24% за 3-ю), а не кратно числу
# атак — см. rules2.multiattack_scale и engine/combat.py::player_basic_attack.
# Повторный замер (см. sim_rules2.py, тот же протокол N=350×30) подтверждает:
# мультиатака САМА ПО СЕБЕ теперь в коридоре ±25% для ВСЕХ 6 классов на
# ур.15 и 45 против профилей мобов без широкого физ-резиста (undead/demon/
# default): диапазон +10.6%..+23.7%.
#
# РЕШЕНИЕ ПО SPIRIT (спринт 7, последний блокер снят): профиль spirit резистил
# СРАЗУ все три физ. типа урона (bash/pierce/slash) — базовая атака ЛЮБОГО
# класса проседала -17%..-42% против духов. Лор-решение: бесплотную туманную
# форму не разрезать и не проколоть, НО дробящее (освящённая булава/посох)
# её сокрушает физической силой удара. rules2._CAT_PROFILE["spirit"]["resist"]
# теперь только ["pierce", "slash"] — bash проходит полным уроном.
# Итог повторного замера (см. sim_rules2.py, тот же протокол N=350×30) после
# правки: против undead/demon/default — ВСЕ 6 классов на ур.15/45 в коридоре
# ±25% (36/36 комбинаций). Против spirit: priest/paladin (дробящее оружие —
# булава/посох в стандартной экипировке items_gen) — в коридоре (-0%..+12%);
# warrior (топор/меч — режущее) — тоже в коридоре (-18%..-20%); mage/rogue/
# necromancer (кинжал/лук — колющее/режущее по правилам _pick_weapon в
# симуляции; посох ТОЖЕ доступен им как альтернатива) проседают -25%..-41% —
# ЭТО ОСОЗНАННЫЙ ДИЗАЙН (духи защищены от клинков), контрплей — 100% боевых
# скиллов всех 6 классов уже нефизические (energy/fire/cold/poison — ни один
# скилл в skills.yaml не размечен как bash/pierce/slash), плюс подсказки
# «уязв: holy/energy» в описании комнаты/моба. 6 из 48 комбинаций вне ±25%,
# все — mage/rogue/necromancer vs spirit (допустимо по критерию контрплея).
# sim_player --smart/--naive (RULES_V2=1) и headless GameLoop (50 тиков,
# 2 персонажа) проходят без исключений — флаг включён.
from engine import rules2
rules2.ENABLED = os.environ.get("RULES_V2", "0").strip() in ("1", "true", "True", "yes", "on")

# «Живые» NPC: Utility AI + FSM (мобы вне боя бродят/отдыхают, раненые в страхе бегут).
# Включается переменной окружения NPC_AI=1; по умолчанию ВКЛЮЧЕНО (см. setdefault выше).
# ВАЖНО: алиас npc_life — чтобы не затенять диалоговый модуль ai.npc_ai (строка 50).
from engine import npc_ai as npc_life
npc_life.ENABLED = os.environ.get("NPC_AI", "1").strip() in ("1", "true", "True", "yes", "on")

# Ленивая Catch-up симуляция: тикаем только активные комнаты, спящие догоняем
# при входе игрока. Включается env LAZY_SIM=1; по умолчанию ВКЛЮЧЕНО (см. setdefault выше).
from engine import catchup as _catchup
_catchup.ENABLED = os.environ.get("LAZY_SIM", "1").strip() in ("1", "true", "True", "yes", "on")

# Динамические мировые события (вторжения/ярмарки/аномалии). Env WORLD_EVENTS=1;
# по умолчанию ВКЛЮЧЕНО (см. setdefault выше).
from engine import events as _events
_events.ENABLED = os.environ.get("WORLD_EVENTS", "1").strip() in ("1", "true", "True", "yes", "on")
# Сезоны/лиги с ладдером. Env SEASONS=1; по умолчанию ВКЛЮЧЕНО (см. setdefault выше).
from engine import seasons as _seasons
_seasons.ENABLED = os.environ.get("SEASONS", "1").strip() in ("1", "true", "True", "yes", "on")
# Push-реактивация (бот пишет первым: боссы, аукцион, ежедневка). Env NOTIFY=1.
# По умолчанию ВКЛЮЧЕНА (см. setdefault выше) — отключить явным NOTIFY=0.
from engine import notify as _notify
_notify.ENABLED = os.environ.get("NOTIFY", "1").strip() in ("1", "true", "True", "yes", "on")

# Модерация (Этап 7.2): баны/муты + чат-rate-limit. Кэш в памяти решает гейты,
# БД (moderation-таблица) — источник истины (инъекция _mod.set_db в main()).
from engine import moderation as _mod


def _parse_admin_ids(raw: str):
    """Разобрать ADMIN_IDS из env: список uid через запятую/пробел. Мусор — пропускаем."""
    out = set()
    for tok in (raw or "").replace(";", ",").replace(" ", ",").split(","):
        tok = tok.strip()
        if tok.lstrip("-").isdigit():
            out.add(int(tok))
    return out


ADMIN_IDS = _parse_admin_ids(os.environ.get("ADMIN_IDS", ""))


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ───────── Этап 10: пользовательские документы-черновики ─────────
# Контакт поддержки и ссылки на полные тексты — плейсхолдеры, заполняются перед
# бетой (см. docs/legal/*.md — там же прямо указано «ЧЕРНОВИК — требует
# юридической проверки»). Ничего юридического тут не решаем — только ссылаемся.
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "[УКАЖИТЕ КОНТАКТ ПОДДЕРЖКИ]")
LEGAL_DOCS_URL = os.environ.get("LEGAL_DOCS_URL", "[УКАЖИТЕ ССЫЛКУ НА ПОЛНЫЕ ТЕКСТЫ]")


# Runtime-выключатель торговли (аукцион/банк). Экстренная «пауза экономики» из
# админки без рестарта; по умолчанию торговля включена. Меняется только админом.
TRADING_ENABLED = True
# callback-действия, двигающие золото/предметы через аукцион и гильд-банк —
# именно их блокирует пауза торговли (просмотр витрин остаётся доступен).
_TRADING_GATED = frozenset({
    "aucbuy", "auclist", "auccancel", "aucsell", "gdep", "gwd", "gdi", "gwi"})

# Ожидание текстового ввода в админке: uid -> {"kind": "player"|"mute"|"comp", ...}
admin_await: dict[int, dict] = {}

import time as _time_mod
import contextvars
START_TS = _time_mod.time()   # для аптайма в админ-Health

# ───────── Этап 9: эксплуатация — heartbeat / метрики / correlation id ─────────
# Heartbeat-файл: snapshot_worker обновляет его раз в такт; Docker HEALTHCHECK
# считает контейнер живым, пока mtime свежий (<120с). Путь можно переопределить.
_HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE", "/tmp/mud_heartbeat")

# Лёгкие рантайм-метрики для админ-Health (не персистятся — сбрасываются рестартом).
_METRICS = {"tg_429": 0, "tg_429_last": 0.0}

# Correlation id: on_text/on_cb кладут сюда короткий cid; логгер send/econ-ошибок
# прикладывает его к событию — так одну цепочку действий игрока видно сквозь логи.
_cid_var: contextvars.ContextVar = contextvars.ContextVar("cid", default="-")


def _note_tg_error(e: BaseException) -> bool:
    """Если это Telegram 429 (Too Many Requests) — инкремент счётчика Health.
    Возвращает True, если распознали 429."""
    code = getattr(e, "error_code", None) or getattr(
        getattr(e, "response", None), "status_code", None)
    msg = str(e).lower()
    if code == 429 or "too many requests" in msg or "retry after" in msg \
            or e.__class__.__name__ == "TelegramRetryAfter":
        _METRICS["tg_429"] += 1
        _METRICS["tg_429_last"] = _time_mod.time()
        return True
    return False


def _write_heartbeat() -> None:
    """Обновить heartbeat-файл (для HEALTHCHECK). Ошибки FS не критичны — молча."""
    try:
        with open(_HEARTBEAT_FILE, "w") as _hb:
            _hb.write(str(_time_mod.time()))
    except Exception:
        pass

# Бог-оркестратор: LLM (или fallback) периодически запускает мировое событие из
# каталога и пишет летопись при смене сезона. Работает только при _events.ENABLED.
# Интервал тика — env GOD_INTERVAL (сек, дефолт 3ч). Реальный вызов LLM внутри
# ограничен god.GOD_MIN_INTERVAL (1ч) независимо от частоты тика.
from ai import god as _god
from ai import provider as _provider
from ai import llmlog as _llmlog   # Этап 8: журнал вызовов LLM (флаш в snapshot_worker)
from ai import cost as _cost       # Этап 8: дневной HARD-бюджет (BUDGET_GUARD) + лимиты
try:
    _GOD_INTERVAL = int(os.environ.get("GOD_INTERVAL", "").strip() or 10800)
except ValueError:
    _GOD_INTERVAL = 10800

# ───────── глобальное состояние ─────────
# Если задан PROXY_URL — бот ходит к Telegram через прокси (обход блокировок).
if PROXY_URL:
    _session = AiohttpSession(proxy=PROXY_URL)
    bot = Bot(token=BOT_TOKEN, session=_session)
    print(f"🌐 Бот использует прокси: {PROXY_URL.split('@')[-1]}")
else:
    bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
world = World()
chars: dict[int, Character] = {}
# Общий стартовый хаб (сезон 0, Этап 4.2): ВСЕ новые персонажи рождаются здесь,
# независимо от расы — расовые столицы (races.yaml start_room) больше не
# раскидывают новичков по разным комнатам без наставника/туториала. Расовая
# столица сохраняется как ch.flags["home_room"] (см. cmd на создание ниже).
HUB_ROOM = "village"
# процесс создания: uid -> {"race": ..., "cls": ...}
creating: dict[int, dict] = {}
# ожидающие привязки реферера: uid нового игрока -> uid реферера (до создания
# персонажа; разбирается в on_text при вводе имени, см. cmd_start / referral.py)
pending_ref: dict[int, int] = {}
talking_to: dict[int, str] = {}   # uid -> npc_id (диалог свободным текстом)
party_mgr = PartyManager()
duel_mgr = DuelManager()
guild_mgr = GuildManager(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guilds.json"))
from engine.auction import AuctionManager
from engine import auction as auctionlib
from engine import econ_tx                    # Этап 3.1: транзакционное ядро аукциона (БД)
from engine import guild_tx                   # Этап 3.2: транзакционное ядро гильд-банка (БД)
auction_mgr = AuctionManager(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auction.json"))
# PROD=1 — публичный (бета) режим: аукцион работает ТОЛЬКО через БД; без пула
# торговля отключается, а на старте (main) отсутствие БД — фатально. PROD=0 —
# как раньше: без БД аукцион живёт в памяти (dev-фасад engine/auction.py).
PROD = os.environ.get("PROD", "0").strip() in ("1", "true", "True", "yes", "on")
# Пер-uid локи экономики: сериализуют операции над золотом/инвентарём одного
# персонажа В ПРОЦЕССЕ (БД-транзакция FOR UPDATE защищает межпроцессно; лок —
# согласованность in-memory chars). Покупка берёт локи покупателя и продавца в
# порядке возрастания uid — профилактика взаимной блокировки.
econ_locks: dict[int, asyncio.Lock] = {}


def _econ_lock(uid: int) -> asyncio.Lock:
    lk = econ_locks.get(uid)
    if lk is None:
        lk = asyncio.Lock()
        econ_locks[uid] = lk
    return lk
from engine import territory as _territory
_TERR_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "territory.json")
_territory.load(_TERR_PATH)
from engine import chronicle as _chronicle
guild_naming: dict[int, bool] = {}   # uid ждёт ввода названия гильдии
# uid -> unix ts запроса /reset (окно подтверждения 60с, см. persist.RESET_WINDOW_SEC).
# Двухшаговый /reset: первый вызов ставит метку и показывает кнопки, подтверждение
# сверяет окно и мягко удаляет персонажа (восстановимо в течение суток).
pending_reset: dict[int, float] = {}
# Двойной клик подтверждает разборку снаряжения в пыль (uid -> (item_key, ts)).
pending_salvage: dict[int, tuple] = {}
duel_view: dict[int, dict] = {}   # uid -> {"chat","id"} панель дуэли
intro_msg: dict[int, int] = {}     # uid -> id стартового сообщения (удаляется после 1-го действия)
# uid -> message_id последней НОВОЙ панели комнаты (не-edit). Плейтест владельца:
# при частых перемещениях в чате копились старые панели комнаты — теперь при
# отправке новой панели предыдущая удаляется (см. _track_room_panel ниже).
room_panel_msg: dict[int, int] = {}
IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")


async def send_entity_photo(uid: int, kind: str, eid: str, caption: str = None):
    """Отправить иконку сущности (images/<kind>/<id>.png), если она есть."""
    path = os.path.join(IMAGES_DIR, kind, eid + ".png")
    if not os.path.exists(path):
        return
    try:
        await bot.send_photo(uid, FSInputFile(path), caption=caption, parse_mode="Markdown")
    except Exception:
        pass
db: Database = None
gl: GameLoop = None
BOT_USERNAME: str = ""   # без @, заполняется в main() через bot.get_me()


# ───────── утилиты ─────────
_last_seen_at: dict[int, float] = {}   # uid -> unix последнего touch (троттлинг)

# ───────── Этап 7.1: аналитика — сессии + прокси «уведомление открыто» ─────────
_SESSION_GAP_SEC = 1800.0                  # 30 минут тишины -> новая сессия (session_start)
_analytics_seen: dict[int, float] = {}     # uid -> unix последней активности (для сессий)
_last_notify_sent: dict[int, float] = {}   # uid -> unix последней успешной push-доставки


def _mark_session(uid: int):
    """Трекнуть session_start, если тишина игрока была ≥30 мин (или это первая
    активность процесса для uid — после рестарта бота словарь пуст, поэтому
    первое же действие любого онлайн-игрока засчитается новой сессией; это
    ЗАВЫШАЕТ число сессий сразу после рестарта — известное допущение, в
    долгую на D1/D7 не влияет).

    session_end НЕ трекается: у Telegram-бота нет события «клиент закрыл
    чат» — конец сессии в scripts/funnel_report.py вычисляется постфактум по
    паузам ≥30 мин между зафиксированными session_start одного uid.

    notification_opened — ПРОКСИ, не точное измерение: если последняя
    успешная push-доставка (_last_notify_sent) была не более 30 мин назад,
    считаем, что именно она привела игрока в бота. Telegram Bot API не даёт
    подтверждения «открыл чат из уведомления», так что это допущение, а не
    факт (совпадение органического визита с недавним пушем даст ложный
    positive)."""
    import time as _time
    now = _time.time()
    last = _analytics_seen.get(uid)
    _analytics_seen[uid] = now
    if last is not None and (now - last) < _SESSION_GAP_SEC:
        return
    analytics.track(uid, "session_start", {})
    _sent = _last_notify_sent.get(uid)
    if _sent is not None and (now - _sent) <= _SESSION_GAP_SEC:
        analytics.track(uid, "notification_opened",
                        {"within_min": round((now - _sent) / 60.0, 1)})


async def _touch_seen(uid: int):
    """Обновить last_seen не чаще раза в 60 сек на игрока (не душим БД)."""
    if not (_notify.ENABLED and db and db.pool):
        return
    now = asyncio.get_event_loop().time()
    if now - _last_seen_at.get(uid, 0) < 60:
        return
    _last_seen_at[uid] = now
    try:
        await db.touch_last_seen(uid)
    except Exception:
        pass


async def send(uid: int, text: str):
    try:
        await bot.send_message(uid, text, parse_mode="Markdown")
    except Exception as e:
        # 403/blocked/deactivated — ожидаемо (игрок закрыл бота), не шумим;
        # прочее (BadRequest разметки, сеть) — логируем с контекстом.
        _note_tg_error(e)   # 429 -> счётчик Health
        _msg = str(e).lower()
        if not ("blocked" in _msg or "forbidden" in _msg or "deactivated" in _msg
                or "chat not found" in _msg):
            _elog.log_err(_log, "send_failed", e, uid=uid, cid=_cid_var.get())
    await _touch_seen(uid)


EPHEMERAL_TTL = 40   # сек — сколько живёт эфемерная строка окружения по умолчанию


async def _delete_after(uid: int, message_id: int, ttl: float):
    """Подождать ttl секунд и тихо удалить сообщение (ошибки — молча, в игноре)."""
    await asyncio.sleep(ttl)
    try:
        await bot.delete_message(uid, message_id)
    except Exception:
        pass


async def send_ephemeral(uid: int, text: str, ttl: float = EPHEMERAL_TTL):
    """Отправить самоудаляющуюся строку окружения (анонс забредания, шаги
    другого игрока, ambient-реплика NPC). Плейтест владельца: такие строки
    спамили чат — теперь исчезают сами через ttl секунд, не засоряя историю."""
    try:
        m = await bot.send_message(uid, text, parse_mode="Markdown")
    except Exception:
        return
    asyncio.create_task(_delete_after(uid, m.message_id, ttl))


async def broadcast_ephemeral(room: str, text: str, ttl: float = EPHEMERAL_TTL,
                              exclude: int = None):
    """broadcast, но каждое сообщение — эфемерное (см. send_ephemeral).
    Как и gl.broadcast, шлём только живым игрокам комнаты (мёртвый «заморожен»
    на экране смерти — ambient-спам ему ни к чему)."""
    for c in chars.values():
        if c.room == room and c.hp > 0 and c.uid != exclude:
            await send_ephemeral(c.uid, text, ttl)


# Дебаунс сохранения персонажей: обычный save лишь метит uid грязным, батч-флашер
# (flush_dirty_chars, вызывается snapshot_worker'ом) пишет их пачкой раз в 3с.
# force=True — немедленная запись (транзакционно важные точки). Сигнатура с
# дефолтом force=False совместима с колбэком save для GameLoop и всех вызовов.
_char_dirty = CharDirtySet()
_log = _elog.get("bot.main")
_flush_health = _persist.FlushHealth()   # трекер подряд-провальных проходов флашера


async def save(ch: Character, force: bool = False):
    if db and db.pool:
        if force:
            _char_dirty.discard(ch.uid)
            await db.save(ch)
        else:
            _char_dirty.mark(ch.uid)   # запись отложена флашеру
    # территории: в БД-режиме save() лишь метит dirty (флашит snapshot_worker),
    # без БД — прежняя запись файла (обратная совместимость).
    _territory.save(_TERR_PATH)


async def flush_dirty_chars():
    """Записать всех накопившихся «грязных» персонажей батчем (флашер дебаунса).

    Провалившиеся при ошибке БД uid возвращаются в набор (persist.flush_dirty —
    не теряем прогресс), ошибки логируются структурно. После 5 провальных
    проходов подряд — громкое предупреждение (не чаще раза в минуту)."""
    if not (db and db.pool):
        _char_dirty.drain()      # без БД копить незачем — просто очистим
        return
    ok, failed = await _persist.flush_dirty(
        _char_dirty, chars.get, db.save, logger=_log)
    if _flush_health.record(failed):
        _elog.log_err(_log, "char_flush_degraded",
                      consecutive=_flush_health.consecutive,
                      pending=len(_char_dirty), last_ok=ok)


async def broadcast_all(text: str, category: str):
    """Рассылка по ВСЕМ uid из БД (а не только онлайн из chars) с батчингом и
    rate-limit ~25 msg/сек. Учёт настроек/квоты per-uid; 403 -> пометить в БД.
    world_boss: кто был замечен (last_seen) в последние 10 минут — уже онлайн
    и получит внутриигровой анонс напрямую (см. GameLoop.tick), повторный
    push ему не нужен — исключаем через exclude_recent_sec."""
    import time as _time
    _exclude_recent = 600 if category == "world_boss" else None
    targets = await db.list_notify_targets(exclude_recent_sec=_exclude_recent) \
        if (db and db.pool) else []
    if not targets:
        # без БД — хотя бы онлайн-игрокам (деградация)
        targets = [(u,) for u in chars]
    now = _time.time()
    sent = 0
    for i, (uid,) in enumerate(targets):
        ch = chars.get(uid)
        # для онлайн-игроков уважаем их настройки/квоту; оффлайн — шлём (это
        # именно тот случай, ради которого push и придуман — вернуть игрока)
        if ch is not None and _notify.allow(ch, category, now) != "send":
            continue
        try:
            await bot.send_message(uid, text, parse_mode="Markdown")
            sent += 1
            # Этап 7.2 (bugfix): рассылка учитывает суточную квоту онлайн-игрока —
            # раньше allow() лишь ЧИТАЛ квоту, а счётчик здесь не двигался, и лимит
            # для world_event/world_boss де-факто не соблюдался. record_sent сам
            # пропускает off-quota категории (сделки аукциона).
            if ch is not None:
                _notify.record_sent(ch, category, now)
            if db and db.pool:
                await db.log_notify(uid, category, True)
        except Exception as e:
            code = getattr(e, "error_code", None) or getattr(
                getattr(e, "response", None), "status_code", None)
            msg = str(e).lower()
            if code == 403 or "blocked" in msg or "forbidden" in msg or "deactivated" in msg:
                if db and db.pool:
                    await db.mark_notify_blocked(uid)
            else:
                _note_tg_error(e)   # 429 -> счётчик Health
                # неожиданная ошибка доставки (не блокировка) — фиксируем контекст
                _elog.log_err(_log, "broadcast_send_failed", e, uid=uid,
                              category=category, cid=_cid_var.get())
            if db and db.pool:
                await db.log_notify(uid, category, False)
        if sent % 25 == 0 and sent:
            await asyncio.sleep(1.0)     # rate-limit Telegram (~25/сек)
    return sent


def others_in(room: str):
    return [c for c in chars.values() if c.room == room]


_ATTR_RU = {"str": "Сила", "dex": "Ловкость", "int": "Интеллект", "spi": "Дух"}


def apply_attrbuff(ch: Character, eff: dict) -> str:
    """Наложить временный бафф статов (бафф-еда/эликсиры)."""
    dur = eff.get("duration", 20)
    parts = []
    for attr, amt in eff["attrbuff"].items():
        ch.effects.append({"type": "attr", "attr": attr, "amount": amt, "turns": dur})
        parts.append(f"+{amt} {_ATTR_RU.get(attr, attr)}")
    return f"🍖 Подкрепление: {', '.join(parts)} ({dur} ходов)"


async def clear_intro(uid: int):
    """Убрать стартовое сообщение после первого действия игрока."""
    mid = intro_msg.pop(uid, None)
    if mid:
        try:
            await bot.delete_message(uid, mid)
        except Exception:
            pass


def kb_help():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Список команд", callback_data="help_cmds")],
        [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="invite")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="look")]])


async def safe_edit(cb: CallbackQuery, text: str, kb):
    try:
        await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        # сообщение нельзя редактировать как текст (например, это фото) —
        # удаляем его и шлём новое текстовое
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer(text, parse_mode="Markdown", reply_markup=kb)


# ───────── «живое» боевое сообщение ─────────
# combat_view[uid] = {"chat", "id", "player", "mob"} — одно сообщение на бой,
# которое обновляется на месте: показывает ТЕКУЩИЙ удар игрока и ответ моба.
combat_view: dict[int, dict] = {}


def _cv(uid: int) -> dict:
    return combat_view.setdefault(uid, {"chat": None, "id": None, "player": "", "mob": ""})


def set_combat_line(uid: int, who: str, text: str):
    _cv(uid)[who] = text


def render_combat_view(ch: Character) -> str:
    cv = combat_view.get(ch.uid, {})
    mob = world.find(ch.room, ch.target) if ch.target else None
    L = []
    if cv.get("player"):
        L.append(cv["player"])
    if cv.get("mob"):
        L.append(cv["mob"])
    L.append("")
    if mob:
        L.append(f"{mob.meta['emoji']} *{mob.meta['name']}* "
                 f"[{ui.bar(mob.hp, mob.max_hp, 8)}] {mob.hp}/{mob.max_hp}")
        # Дух (RULES_V2): бесплотную форму не разрезать и не проколоть — режущее/
        # колющее резистится, но дробящее и «нефизические» умения проходят
        # полновесно. Подсказка контрплея видна прямо в шапке боя (только когда
        # резисты фактически действуют — т.е. при rules2.ENABLED).
        if rules2.ENABLED and rules2.mob_profile(mob.meta)["category"] == "spirit":
            L.append("👻 _Бесплотное: режущее/колющее слабо — бей дробящим или умениями_")
    L.append(f"❤️ {ch.name} [{ui.bar(ch.hp, ch.max_hp)}] {ch.hp}/{ch.max_hp}  "
             f"{ch.resource_emoji} {ch.mp}/{ch.max_resource}")
    return "\n".join(L)


async def render_combat_cb(cb: CallbackQuery, ch: Character):
    """Обновить боевое сообщение через нажатую кнопку (правка на месте)."""
    text = render_combat_view(ch)
    kb = ui.kb_combat(ch, world)
    try:
        await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            try:
                m = await cb.message.answer(text, parse_mode="Markdown", reply_markup=kb)
                cv = _cv(ch.uid); cv["chat"] = m.chat.id; cv["id"] = m.message_id
                return
            except Exception:
                return
    cv = _cv(ch.uid)
    cv["chat"] = cb.message.chat.id
    cv["id"] = cb.message.message_id


async def combat_hit(victim: Character, mob, lines):
    """Колбэк из игрового цикла: моб ударил игрока — обновить его боевое сообщение."""
    set_combat_line(victim.uid, "mob", "\n".join(lines))
    cv = combat_view.get(victim.uid)
    if victim.hp <= 0:
        if cv and cv.get("photo"):
            try:
                await bot.delete_message(victim.uid, cv["photo"])
            except Exception:
                pass
        combat_view.pop(victim.uid, None)   # умер — следующее действие покажет комнату
    if cv and cv.get("id"):
        text = render_combat_view(victim)
        kb = ui.kb_combat(victim, world)
        try:
            await bot.edit_message_text(text, chat_id=cv["chat"], message_id=cv["id"],
                                        parse_mode="Markdown", reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                pass
        except Exception:
            pass
    else:
        for ln in lines:
            await send(victim.uid, ln)


async def drop_combat_photo(uid: int):
    """Удалить фото моба над боевой панелью (если есть)."""
    cv = combat_view.get(uid)
    if cv and cv.get("photo"):
        try:
            await bot.delete_message(uid, cv["photo"])
        except Exception:
            pass
        cv["photo"] = None


async def death_screen(ch: Character):
    """Экран смерти с кнопкой возрождения. Игрок «заморожен» до возрождения."""
    await drop_combat_photo(ch.uid)
    cv = combat_view.pop(ch.uid, None)
    if cv and cv.get("id"):
        try:
            await bot.delete_message(ch.uid, cv["id"])
        except Exception:
            pass
    from engine import karma
    if ch.level < karma.SOFT_DEATH_LEVEL:
        txt = ("💀 *ВЫ ПАЛИ*\n\n"
               "Тьма смыкается вокруг, и дух ускользает из остывающего тела...\n\n"
               "Не бойся: пока ты новичок, монеты и вещи при смерти не теряются.\n"
               "💡 Возродись, вернись в храм подлечиться и купи зелий у торговца.")
    else:
        txt = ("💀 *ВЫ ПАЛИ*\n\n"
               "Тьма смыкается вокруг, и дух ускользает из остывающего тела...\n\n"
               "Возродиться можно у точки возрождения — но часть монет останется здесь, "
               "в холодных руках смерти.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚰️ Возродиться", callback_data="respawn")]])
    try:
        await bot.send_message(ch.uid, txt, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass


async def do_respawn(cb: CallbackQuery, ch: Character):
    if not ch.flags.get("dead"):
        await cb.answer(); return
    from engine import karma
    # мягкая смерть новичка: до 5 ур. монеты не теряются
    soft = ch.level < karma.SOFT_DEATH_LEVEL
    if soft:
        lost = 0
    else:
        lost = ch.gold - int(ch.gold * 0.7)
        ch.gold = int(ch.gold * 0.7)
    ch.hp = ch.max_hp
    ch.mp = ch.start_resource()
    ch.effects = []
    ch.target = None
    bind = ch.flags.get("bind")
    room = bind if (bind in WORLD) else (RACES.get(ch.race, {}).get("respawn_room")
                                         or RACES.get(ch.race, {}).get("start_room", "temple"))
    ch.room = room
    ch.flags["dead"] = False
    await save(ch)
    try:
        await cb.message.delete()
    except Exception:
        pass
    if soft:
        _msg = (f"✨ Вы возрождаетесь в *{WORLD[ch.room]['name']}*. "
                f"Новичок не теряет монет при смерти.\n"
                f"💡 Совет: вернись в храм подлечиться и купи зелий у торговца, "
                f"прежде чем идти дальше.")
    else:
        _msg = (f"✨ Вы возрождаетесь в *{WORLD[ch.room]['name']}*. "
                f"Потеряно при смерти: 💰{money.fmt(lost)}.")
    await bot.send_message(ch.uid, _msg, parse_mode="Markdown")
    await enter_room(ch)
    await cb.answer()


def _auc_open(ch: Character) -> bool:
    return bool(WORLD[ch.room].get("bank") or WORLD[ch.room].get("auction"))


def _auc_price(item: str) -> int:
    base = ITEMS.get(item, {}).get("price", 100)
    return max(100, int(base * 2))


def _kb_auction(ch: Character, lots=None) -> InlineKeyboardMarkup:
    # lots=None → dev/без-БД (память auction_mgr). Иначе — активные лоты из
    # econ_tx.load_active_lots (ключи id/seller_uid/item/price); имя продавца
    # берём из памяти персонажей (в БД-строке лота его нет).
    rows = []
    if lots is None:
        _for_sale = auction_mgr.for_sale(exclude_uid=ch.uid)[:12]
        mine = auction_mgr.my_listings(ch.uid)
    else:
        _for_sale = [l for l in lots if l["seller_uid"] != ch.uid][:12]
        mine = [l for l in lots if l["seller_uid"] == ch.uid]
    for l in _for_sale:
        nm = ITEMS.get(l["item"], {}).get("name", l["item"])
        sname = l.get("seller_name")
        if not sname:
            _s = chars.get(l["seller_uid"])
            sname = _s.name if _s is not None else "торговец"
        rows.append([InlineKeyboardButton(
            text=f"🛒 {nm} — 💰{money.fmt(l['price'])} · {sname}",
            callback_data=f"aucbuy:{l['id']}")])
    for l in mine:
        nm = ITEMS.get(l["item"], {}).get("name", l["item"])
        rows.append([InlineKeyboardButton(
            text=f"❌ Снять: {nm} (💰{money.fmt(l['price'])})", callback_data=f"auccancel:{l['id']}")])
    rows.append([InlineKeyboardButton(text="📤 Выставить предмет", callback_data="aucsell")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_auction(cb: CallbackQuery, ch: Character):
    if not _auc_open(ch):
        await cb.answer("Аукцион доступен в банке столицы", show_alert=True); return
    if db and db.pool:
        # БД-режим: витрина — из транзакционной таблицы (истина). Выручка от
        # продаж зачисляется продавцу сразу, «почты-ожидания» больше нет; строка
        # pending — только легаси-выручка, накопленная до миграции на БД.
        lots = await econ_tx.load_active_lots(db.pool.acquire)
        _mine = [l for l in lots if l["seller_uid"] == ch.uid]
        pend = auction_mgr.pending_payout(ch.uid)
        txt = (f"🏛 *Аукцион*\nВсего лотов: {len(lots)} (ваших: {len(_mine)}). Комиссия продавца — "
               f"{int(auctionlib.AUCTION_FEE*100)}%.")
        if pend:
            txt += f"\n💰 Ожидает выручка: {money.fmt(pend)} (зачислится при входе в комнату)."
        await safe_edit(cb, txt, _kb_auction(ch, lots))
        return
    if PROD:
        await cb.answer("⚙️ Торговля временно недоступна", show_alert=True); return
    _mine = auction_mgr.my_listings(ch.uid)
    n = len(auction_mgr.for_sale(exclude_uid=ch.uid)) + len(_mine)
    pend = auction_mgr.pending_payout(ch.uid)
    txt = (f"🏛 *Аукцион*\nВсего лотов: {n} (ваших: {len(_mine)}). Комиссия продавца — "
           f"{int(auctionlib.AUCTION_FEE*100)}%.")
    if pend:
        txt += f"\n💰 Ожидает выручка: {money.fmt(pend)} (зачислится при входе в комнату)."
    await safe_edit(cb, txt, _kb_auction(ch))


async def show_auction_sell(cb: CallbackQuery, ch: Character):
    if not _auc_open(ch):
        await cb.answer("Аукцион доступен в банке столицы", show_alert=True); return
    rows = []
    seen = {}
    for it in ch.inventory:
        if it in seen:
            continue
        seen[it] = True
        nm = ITEMS.get(it, {}).get("name", it)
        rows.append([InlineKeyboardButton(
            text=f"📤 {nm} → 💰{money.fmt(_auc_price(it))}", callback_data=f"auclist:{it}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="auc")])
    if not seen:
        await safe_edit(cb, "🎒 В сумке нет предметов для продажи.",
                        InlineKeyboardMarkup(inline_keyboard=rows))
        return
    await safe_edit(cb, "📤 *Выберите предмет для продажи* (цена = база ×2):",
                    InlineKeyboardMarkup(inline_keyboard=rows))


async def do_auc_list(cb: CallbackQuery, ch: Character, item: str):
    if item not in ch.inventory:
        await cb.answer("Предмета нет в сумке", show_alert=True); return
    price = _auc_price(item)
    if db and db.pool:
        # БД-путь: снятие предмета, создание лота и запись в ledger — одной
        # транзакцией; при обрыве процесса предмет не потеряется и не удвоится.
        lot_id = uuid.uuid4().hex
        async with _econ_lock(ch.uid):
            ok, msg, data = await econ_tx.list_lot(
                db.pool.acquire, ch.uid, item, price, lot_id, f"list:{lot_id}")
            if ok and data is not None:
                ch.gold = data["gold"]
                ch.inventory = list(data["inventory"])
                await save(ch, force=True)
        if not ok:
            await cb.answer(msg, show_alert=True)
            return
        await cb.answer("📤 Лот выставлен!")
        await show_auction(cb, ch)
        return
    if PROD:
        await cb.answer("⚙️ Торговля временно недоступна", show_alert=True); return
    ch.inventory.remove(item)
    lid = auction_mgr.create_listing(ch.uid, ch.name, item, price)
    if not lid:
        ch.inventory.append(item)
        await cb.answer("Лимит лотов исчерпан", show_alert=True); return
    await save(ch, force=True)      # выставление лота — фиксируем сразу
    await cb.answer("📤 Лот выставлен!")
    await show_auction(cb, ch)


async def do_auc_buy(cb: CallbackQuery, ch: Character, lid: str):
    if db and db.pool:
        await _do_auc_buy_db(cb, ch, lid)
        return
    if PROD:
        await cb.answer("⚙️ Торговля временно недоступна", show_alert=True); return
    l = auction_mgr.get(lid)
    if not l:
        await cb.answer("Лот уже продан", show_alert=True); await show_auction(cb, ch); return
    if l["seller_uid"] == ch.uid:
        await cb.answer("Это ваш лот", show_alert=True); return
    if ch.gold < l["price"]:
        await cb.answer("Не хватает монет", show_alert=True); return
    status, lot = auction_mgr.buy(lid, ch.uid)
    if status != "ok":
        await cb.answer("Лот недоступен", show_alert=True); await show_auction(cb, ch); return
    ch.gold -= lot["price"]
    ch.inventory.append(lot["item"])
    await save(ch, force=True)      # покупка на аукционе — фиксируем сразу
    # зачислить продавцу сразу, если он онлайн
    _iname = ITEMS.get(lot['item'], {}).get('name', lot['item'])
    seller = chars.get(lot["seller_uid"])
    if seller is not None:
        pay = auction_mgr.claim_payout(lot["seller_uid"])
        if pay:
            seller.gold += pay
            _wsell = weekly.on_sell_lot(seller)   # недельная цель sell_lot (Этап 6.1)
            await save(seller, force=True)   # выручка продавцу — фиксируем сразу
            _sold = f"💰 Ваш лот «{_iname}» продан! +{money.fmt(pay)}."
            if _wsell:
                _sold += "\n" + _wsell
            # auction_sold — вне суточного лимита; онлайн-продавцу шлём напрямую
            if _notify.ENABLED:
                _notify.emit(seller.uid, "auction_sold", _sold)
            else:
                try:
                    await bot.send_message(seller.uid, _sold)
                except Exception:
                    pass
    elif _notify.ENABLED:
        # продавец оффлайн — push напрямую (выручку он заберёт при заходе)
        await _notify_deliver(lot["seller_uid"], "auction_sold",
                              f"💰 Ваш лот «{_iname}» продан! Выручка ждёт в почте — заходите забрать.")
    await cb.answer("🛒 Покупка совершена!")
    await show_auction(cb, ch)


async def _do_auc_buy_db(cb: CallbackQuery, ch: Character, lid: str):
    """БД-путь покупки: одна транзакция (econ_tx.buy_lot) под локами покупателя
    и продавца в порядке возрастания uid. Выручка зачисляется продавцу сразу — и
    в БД, и в память (если персонаж загружен). Память обновляем из возвращённых
    data ДО отпускания локов."""
    # предчтение лота: узнать продавца (для порядка локов) и быстрые отказы
    lots = await econ_tx.load_active_lots(db.pool.acquire)
    lot0 = next((x for x in lots if str(x["id"]) == str(lid)), None)
    if lot0 is None:
        await cb.answer("Лот уже продан", show_alert=True); await show_auction(cb, ch); return
    seller_uid = lot0["seller_uid"]
    if seller_uid == ch.uid:
        await cb.answer("Это ваш лот", show_alert=True); return
    if ch.gold < lot0["price"]:
        await cb.answer("Не хватает монет", show_alert=True); return
    op_id = f"buy:{lid}:{ch.uid}"
    locks = [_econ_lock(u) for u in sorted({ch.uid, seller_uid})]
    for lk in locks:
        await lk.acquire()
    try:
        ok, msg, bd, sd, lot = await econ_tx.buy_lot(db.pool.acquire, ch.uid, lid, op_id)
        if ok and bd is not None:
            ch.gold = bd["gold"]
            ch.inventory = list(bd["inventory"])
            await save(ch, force=True)
            if sd is not None:
                seller = chars.get(seller_uid)
                if seller is not None:
                    seller.gold = sd["gold"]
                    seller.inventory = list(sd["inventory"])
                    await save(seller, force=True)
    finally:
        for lk in reversed(locks):
            lk.release()
    if not ok:
        await cb.answer(msg, show_alert=True); await show_auction(cb, ch); return
    if lot is not None:
        # свежая продажа (не идемпотентный повтор) — уведомить продавца;
        # выручка уже зачислена продавцу в транзакции econ_tx.buy_lot.
        _iname = ITEMS.get(lot["item"], {}).get("name", lot["item"])
        _proceeds = lot.get("proceeds", 0)
        seller = chars.get(seller_uid)
        _sold = f"💰 Ваш лот «{_iname}» продан! +{money.fmt(_proceeds)}."
        if seller is not None:
            _wsell = weekly.on_sell_lot(seller)   # недельная цель sell_lot (Этап 6.1)
            if _wsell:
                _sold += "\n" + _wsell
            await save(seller, force=True)   # прогресс недельника — фиксируем вместе с выручкой
            if _notify.ENABLED:
                _notify.emit(seller.uid, "auction_sold", _sold)
            else:
                try:
                    await bot.send_message(seller.uid, _sold)
                except Exception:
                    pass
        elif _notify.ENABLED:
            await _notify_deliver(seller_uid, "auction_sold", _sold)
    await cb.answer("🛒 Покупка совершена!")
    await show_auction(cb, ch)


async def do_auc_cancel(cb: CallbackQuery, ch: Character, lid: str):
    if db and db.pool:
        async with _econ_lock(ch.uid):
            ok, msg, data = await econ_tx.cancel_lot(
                db.pool.acquire, ch.uid, lid, f"cancel:{lid}")
            if ok and data is not None:
                ch.gold = data["gold"]
                ch.inventory = list(data["inventory"])
                await save(ch, force=True)
        if not ok:
            await cb.answer("Нельзя снять лот", show_alert=True); return
        await cb.answer("❌ Лот снят, предмет возвращён в сумку")
        await show_auction(cb, ch)
        return
    if PROD:
        await cb.answer("⚙️ Торговля временно недоступна", show_alert=True); return
    item = auction_mgr.cancel(lid, ch.uid)
    if not item:
        await cb.answer("Нельзя снять лот", show_alert=True); return
    ch.inventory.append(item)
    await save(ch, force=True)      # снятие лота (возврат предмета) — фиксируем сразу
    await cb.answer("❌ Лот снят, предмет возвращён в сумку")
    await show_auction(cb, ch)


async def do_gather(cb: CallbackQuery, ch: Character, idx: str):
    from engine import professions
    try:
        i = int(idx)
    except ValueError:
        await cb.answer(); return
    status, lines = professions.gather(ch, ch.room, i)
    if status in ("ok", "fail"):
        await save(ch)
    if status in ("locked", "cd", "none"):
        await cb.answer(lines[0].replace("*", ""), show_alert=True)
        return
    await cb.answer("⛏ Добыча!")
    await bot.send_message(ch.uid, "\n".join(lines), parse_mode="Markdown")
    await safe_edit(cb, ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))


async def do_pets(cb: CallbackQuery, ch: Character, action: str, arg: str):
    from engine import pets as _pets
    if action == "petbuy":
        ok, msg = _pets.adopt_pet(ch, arg)
    elif action == "petset":
        ok = _pets.set_active_pet(ch, arg); msg = "✅ Питомец активирован" if ok else "Нет такого питомца"
    elif action == "mountbuy":
        ok, msg = _pets.buy_mount(ch, arg)
    else:
        ok = _pets.set_active_mount(ch, arg); msg = "🐎 Маунт активирован" if ok else "Нет такого маунта"
    if ok:
        await save(ch)
    await cb.answer(msg, show_alert=not ok)
    await safe_edit(cb, _pets.render(ch), ui.kb_pets(ch))


async def do_dungeon(cb: CallbackQuery, ch: Character, did: str):
    from engine import dungeon
    ok, reason = dungeon.can_enter(ch, did)
    if not ok:
        await cb.answer(reason, show_alert=True)
        return
    lines = dungeon.enter(ch, did, world)
    await save(ch)
    # push «данж снова доступен» на момент истечения кулдауна
    if _notify.ENABLED and db and db.pool:
        _ra = dungeon.ready_at(did)
        if _ra:
            _dn = dungeon.DUNGEONS.get(did, {}).get("name", did)
            await db.upsert_schedule(ch.uid, "dungeon_ready", _ra,
                                     f"🏰 Кулдаун подземелья «{_dn}» истёк — можно снова войти!")
    await cb.answer("⚔️ Вход в подземелье!")
    await bot.send_message(ch.uid, "\n".join(lines), parse_mode="Markdown")
    await enter_room(ch, cb=cb)


async def start_combat_view(cb: CallbackQuery, ch: Character, mob):
    """Начало боя: удалить меню комнаты, отправить фото моба СВЕРХУ,
    затем боевую панель новым сообщением ПОД фото."""
    try:
        await cb.message.delete()
    except Exception:
        pass
    cv = _cv(ch.uid)
    cv["photo"] = None
    path = os.path.join(IMAGES_DIR, "mobs", mob.mob_id + ".png")
    if os.path.exists(path):
        try:
            ph = await bot.send_photo(ch.uid, FSInputFile(path),
                                      caption=f"⚔️ {mob.meta['emoji']} *{mob.meta['name']}*",
                                      parse_mode="Markdown")
            cv["photo"] = ph.message_id
        except Exception:
            cv["photo"] = None
    text = render_combat_view(ch)
    kb = ui.kb_combat(ch, world)
    try:
        m = await bot.send_message(ch.uid, text, parse_mode="Markdown", reply_markup=kb)
        cv["chat"] = m.chat.id
        cv["id"] = m.message_id
    except Exception:
        pass


async def combat_reward(ch: Character, text: str):
    """Моб убит: удаляем боевую панель и публикуем итог (удар+награда+комната)
    НОВЫМ сообщением внизу, чтобы ничего не оставалось «под меню»."""
    cv = combat_view.pop(ch.uid, None)
    # строки урона НЕ показываем — только итог боя (убит/награда) и комнату
    parts = [text, ui.render_room(ch, world, others_in(ch.room))]
    body = "\n\n".join(p for p in parts if p)
    if cv:
        for mid in (cv.get("photo"), cv.get("id")):
            if mid:
                try:
                    await bot.delete_message(cv.get("chat") or ch.uid, mid)
                except Exception:
                    pass
    try:
        await bot.send_message(ch.uid, body, parse_mode="Markdown",
                               reply_markup=ui.kb_room(ch, world))
    except Exception:
        pass


async def _track_room_panel(uid: int, message_id: int):
    """Запомнить id НОВОЙ панели комнаты, удалив предыдущую (если была).
    Только для панели комнаты — боевые/смерть-панели используют свои
    собственные словари (combat_view/death_screen) и это не трогает."""
    prev = room_panel_msg.get(uid)
    if prev and prev != message_id:
        try:
            await bot.delete_message(uid, prev)
        except Exception:
            pass
    room_panel_msg[uid] = message_id


async def enter_room(ch: Character, cb=None):
    """Вход в комнату: если включены картинки локаций и файл есть — показать
    фото комнаты с описанием и клавиатурой; иначе обычный текст."""
    # reach-цели квестов: засчитать достижение комнаты (идемпотентно)
    _reach = quest.on_enter_room(ch, ch.room)
    # недельная цель explore: посещение НОВОЙ для персонажа комнаты (Этап 6.1)
    _wexp = weekly.on_room_visit(ch, ch.room)
    if _wexp:
        _reach = (_reach or []) + [_wexp]
    if _reach:
        await save(ch)
        try:
            await bot.send_message(ch.uid, "\n".join(_reach), parse_mode="Markdown")
        except Exception:
            pass
    # зачислить выручку с аукциона (продажи, пока игрок был оффлайн)
    _pay = auction_mgr.claim_payout(ch.uid)
    if _pay:
        ch.gold += _pay
        await save(ch, force=True)      # зачисление выручки с аукциона — фиксируем сразу
        try:
            await bot.send_message(ch.uid, f"💰 Аукцион: получена выручка с продаж — +{money.fmt(_pay)}.")
        except Exception:
            pass
    img = os.path.join(IMAGES_DIR, "rooms", ch.room + ".png")
    if ch.flags.get("roompics", True) and os.path.exists(img):
        caption = ui.render_room(ch, world, others_in(ch.room))
        if len(caption) > 1000:
            caption = caption[:1000] + "…"
        if cb:
            try:
                await cb.message.delete()
            except Exception:
                pass
        try:
            ph = await bot.send_photo(ch.uid, FSInputFile(img), caption=caption,
                                      parse_mode="Markdown", reply_markup=ui.kb_room(ch, world))
            await _track_room_panel(ch.uid, ph.message_id)
            return
        except Exception:
            pass
    if cb:
        await show_room(cb.message, ch, edit_cb=cb)
    else:
        m = await bot.send_message(ch.uid, ui.render_room(ch, world, others_in(ch.room)),
                                   parse_mode="Markdown", reply_markup=ui.kb_room(ch, world))
        await _track_room_panel(ch.uid, m.message_id)


async def show_room(target, ch: Character, edit_cb=None):
    txt = ui.render_room(ch, world, others_in(ch.room))
    kb = ui.kb_room(ch, world)
    if edit_cb:
        await safe_edit(edit_cb, txt, kb)
    else:
        m = await target.answer(txt, parse_mode="Markdown", reply_markup=kb)
        await _track_room_panel(ch.uid, m.message_id)


# ───────── команды ─────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    # Этап 7.1: атрибуция источника — на КАЖДЫЙ /start (и новый, и вернувшийся
    # игрок). first_source фиксируется в БД один раз, last_source — всегда
    # (см. Database.upsert_attribution). source_from_start_arg — чистая
    # функция, приоритет ref: (рефералка) > src: (кампания) > organic.
    _src = analytics.source_from_start_arg(message.text)
    if db and db.pool:
        await db.upsert_attribution(uid, _src)
    if uid in chars:
        ch = chars[uid]
        await message.answer(f"С возвращением, *{ch.name}*!", parse_mode="Markdown")
        for line in streak.touch(ch):
            await message.answer(line, parse_mode="Markdown")
        await enter_room(ch)
        await broadcast_ephemeral(ch.room, f"🚶 {_ts.esc_md(ch.name)} входит в игру.", exclude=uid)
    else:
        analytics.track(uid, "registration_started", {"source": _src})   # Этап 7.1
        # недавно (в течение суток) сброшенный персонаж — предложить восстановление
        _deleted = await db.find_deleted(uid) if (db and db.pool) else None
        if _deleted is not None:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="♻️ Восстановить персонажа", callback_data="reset_restore")]])
            await message.answer(
                f"Найден недавно удалённый персонаж *{_ts.esc_md(_deleted.name)}* "
                f"(ур. {_deleted.level}). Восстановить его или создать нового?\n\n"
                + ui.render_races(),
                parse_mode="Markdown", reply_markup=kb)
            # клавиатуру выбора рас тоже покажем — игрок волен создать нового
            await message.answer("Или выберите расу нового героя:", reply_markup=ui.kb_races())
            return
        # deep-link приглашения (/start ref_123): запоминаем реферера до
        # создания персонажа — привяжется в on_text после выбора имени.
        _ref_arg = referral.parse_start_arg(message.text)
        if _ref_arg is not None and _ref_arg != uid:
            pending_ref[uid] = _ref_arg
        await message.answer(ui.render_races(), parse_mode="Markdown",
                             reply_markup=ui.kb_races())


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    """Шаг 1 из 2: показать предупреждение и кнопки подтверждения/отмены.
    Само удаление — только по кнопке «Да» в окне 60с (см. cb reset_confirm)."""
    import time as _time
    uid = message.from_user.id
    ch = chars.get(uid)
    if not ch:
        await message.answer("У вас нет персонажа. Напишите /start, чтобы создать.")
        return
    pending_reset[uid] = _time.time()
    warn = (
        f"⚠️ *Удалить персонажа?*\n\n"
        f"Имя: *{_ts.esc_md(ch.name)}*\n"
        f"Уровень: *{ch.level}*\n\n"
        "Это *необратимо* (восстановить можно лишь в течение 24 часов). "
        "Весь прогресс — уровни, вещи, золото — будет сброшен.\n\n"
        "Подтвердите в течение минуты."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Да, удалить (необратимо)", callback_data="reset_confirm"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="reset_cancel"),
    ]])
    await message.answer(warn, parse_mode="Markdown", reply_markup=kb)


@dp.message(Command("delete_me"))
async def cmd_delete_me(message: Message):
    """Этап 10: экран «Мои данные и удаление». Ничего не удаляет сам по себе —
    честно объясняет реальный процесс (см. docs/legal/PRIVACY.md, раздел 5) и
    предлагает: (1) экспорт данных JSON-файлом кнопкой «📤 Мои данные»
    (см. cb export_data ниже), (2) собственно удаление — ПЕРЕИСПОЛЬЗУЕТ уже
    существующий двухшаговый flow /reset (та же pending_reset-метка, те же
    кнопки reset_confirm/reset_cancel, тот же db.soft_delete с окном
    восстановления 24ч) — код удаления НЕ дублируется, cmd_reset вызывается
    напрямую."""
    uid = message.from_user.id
    ch = chars.get(uid)
    if not ch:
        await message.answer("У вас нет персонажа. Нечего экспортировать или удалять.")
        return
    text = (
        "🗂 *Мои данные и удаление персонажа*\n\n"
        "Честно о том, что произойдёт при удалении:\n"
        "• персонаж *скрывается сразу* (как и по команде /reset) — вас "
        "больше не видно в игре;\n"
        "• в течение *24 часов* его ещё можно восстановить через /start;\n"
        "• *полное и необратимое* удаление всех записей (включая журналы "
        f"аналитики/модерации) — по отдельному запросу через {SUPPORT_CONTACT} "
        "в срок [УКАЖИТЕ СРОК] — это ручной процесс, не мгновенный.\n\n"
        "Перед удалением можно выгрузить свои данные кнопкой ниже."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Мои данные", callback_data="export_data"),
    ]])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)
    # шаг 2 (подтверждение/отмена удаления) — уже существующий /reset flow
    await cmd_reset(message)


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Точка входа в админку. Доступ только uid ∈ ADMIN_IDS (env). Никаких
    свободных команд с аргументами — всё через инлайн-меню (кроме поиска игрока)."""
    uid = message.from_user.id
    if not is_admin(uid):
        # молча — не раскрываем существование админки посторонним
        return
    admin_await.pop(uid, None)
    await message.answer(_admin_menu_text(), parse_mode="Markdown",
                         reply_markup=_admin_menu_kb())


@dp.message(Command("support"))
async def cmd_support(message: Message):
    """Обращение в поддержку (упоминается в отказе забаненным). Этап 10:
    добавлен контакт-плейсхолдер, ссылка на правила сообщества и напоминание
    про кнопку жалобы — сам текст поддержки остаётся заглушкой (без бэкенда
    обращений на этом этапе)."""
    await message.answer(
        "🛟 *Поддержка «Семи Корон»*\n\n"
        "Опишите проблему одним сообщением здесь — модераторы разберут "
        "обращение. Спасибо за терпение.\n\n"
        f"Прямой контакт: {SUPPORT_CONTACT}\n\n"
        "Жалоба на другого игрока: кнопка «⚠️ Пожаловаться» рядом с его "
        "именем в списке игроков комнаты — так обращение сразу попадает "
        "к администраторам с журналом.\n"
        "Правила сообщества и порядок апелляции на меры модерации — "
        f"полный текст: {LEGAL_DOCS_URL}",
        parse_mode="Markdown")


@dp.message(Command("privacy"))
async def cmd_privacy(message: Message):
    """Краткая выжимка политики конфиденциальности (Этап 10, черновик).
    Полный текст — docs/legal/PRIVACY.md, ссылка на него — плейсхолдер
    LEGAL_DOCS_URL (см. константы выше), пока документ не опубликован отдельно."""
    await message.answer(
        "🔒 *Конфиденциальность — кратко (черновик)*\n\n"
        "Мы храним: ваш Telegram-id, имя персонажа, игровой прогресс "
        "(уровень, золото, инвентарь, квесты), источник перехода по ссылке "
        "и технические журналы модерации/экономики. Свободный текст команд "
        "в аналитику не попадает.\n\n"
        "Часть реплик в диалоге с NPC уходит внешнему ИИ-провайдеру "
        "(DeepSeek), чтобы сгенерировать живой ответ — подробности в полном "
        "документе.\n\n"
        "Паролей и платёжных данных в системе нет — их негде хранить, вход "
        "только через Telegram.\n\n"
        "Экспорт и удаление ваших данных — команда /delete_me.\n\n"
        f"Полный текст: {LEGAL_DOCS_URL}",
        parse_mode="Markdown")


@dp.message(Command("terms"))
async def cmd_terms(message: Message):
    """Краткая выжимка пользовательского соглашения (Этап 10, черновик)."""
    await message.answer(
        "📜 *Условия использования — кратко (черновик)*\n\n"
        "Игра бесплатна. В будущем планируются цифровые товары через "
        "Telegram Stars — без вывода средств в реальные деньги.\n"
        "Действуют правила сообщества (недопустимое поведение, порядок "
        "жалоб и мер модерации) — обращение по ним через /support.\n"
        "Мы не гарантируем бесперебойную работу бота (беты) и вправе "
        "менять условия по мере развития сервиса.\n\n"
        f"Полный текст: {LEGAL_DOCS_URL}",
        parse_mode="Markdown")


def _origin_flavor(race: str) -> str:
    """Строка расового флейвора для интро после создания (Этап 4.2, общий хаб):
    намекает на расовую столицу (races.yaml start_room, теперь она же
    ch.flags["home_room"] — задел «дом/регионы»), которую герой покинул ради
    общего хаба. Название столицы и хаба берётся из данных (WORLD[...]["zone"]),
    без хардкода — human стартует прямо в хабе (start_room=village), для него
    отдельная (не про «путь издалека») формулировка."""
    home = RACES.get(race, {}).get("start_room", HUB_ROOM)
    hub_zone = WORLD.get(HUB_ROOM, {}).get("zone", HUB_ROOM)
    if home == HUB_ROOM:
        return f"Ты родом из самого {hub_zone} — эти места тебе не чужие."
    home_zone = WORLD.get(home, {}).get("zone", home)
    return f"Ты выросл(а) в {home_zone}, но путь привёл тебя в {hub_zone}."


HELP = (
    "═══════ КОМАНДЫ ═══════\n"
    "Действуй *кнопками* под сообщениями.\n\n"
    "Текстом:\n"
    "• `сказать <текст>` — чат комнаты\n"
    "• `кто` — игроки онлайн\n"
    "• `купить <предмет>` — в кузнице\n"
    "• направления словами: `север`, `юг`...\n"
    "/start /reset /delete_me /support /privacy /terms\n"
    "═══════════════════════"
)


async def _chat_blocked(message, ch) -> bool:
    """Гейт публичных чатов (комната/гильдия/группа): мут и анти-спам (Этап 7.2).
    True — сообщение НЕ пропускаем (уже ответили игроку почему)."""
    import time as _t
    now = _t.time()
    if _mod.is_muted(ch.uid, now):
        mins = max(1, int((_mod.muted_until(ch.uid) - now) // 60) + 1)
        await message.answer(f"🔇 Вы временно не можете писать в чат (ещё ~{mins} мин).")
        return True
    if not _mod.chat_allowed(ch.uid, now):
        await message.answer("⏳ Не так быстро — подождите пару секунд.")
        return True
    return False


@dp.message(F.text)
async def on_text(message: Message):
    uid = message.from_user.id
    text = (message.text or "").strip()
    _cid_var.set(uuid.uuid4().hex[:8])   # Этап 9: correlation id этой цепочки
    _mark_session(uid)   # Этап 7.1: session_start по тишине ≥30 мин

    # Этап 7.2: гейт бана — забаненный не проходит дальше (админов не трогаем).
    if _mod.is_banned(uid) and not is_admin(uid):
        await message.answer("⛔️ Доступ ограничен. По вопросам — /support")
        return
    # Этап 7.2: ввод в админке (uid/имя игрока, минуты мута, сумма компенсации).
    if uid in admin_await and is_admin(uid):
        await _admin_on_text(message, uid, text)
        return

    # ожидаем имя после выбора расы и класса
    if uid in creating and "cls" in creating[uid]:
        # валидируем имя ДО pop — чтобы при отказе игрок мог прислать другое,
        # не потеряв выбранные расу/класс.
        name = _ts.clean_name(text)
        if name is None:
            await message.answer(
                "🚫 Такое имя не подойдёт. Пришлите имя из 2–20 символов "
                "(буквы/цифры), без невидимых спецсимволов и не начиная с @ или /.")
            return
        info = creating.pop(uid)
        cls, race = info["cls"], info["race"]
        ch = Character(uid=uid, name=name, cls=cls, race=race)
        ch.init_vitals()
        ch.init_skills()
        # Сезон 0 (Этап 4.2): ВСЕ новые герои стартуют в общем хабе с наставником
        # и туториалом. Расовая столица (races.yaml start_room) уходит в
        # ch.flags["home_room"] — задел под будущие "дом"/регионы; сам ключ
        # start_room в data НЕ переименован (его читают и другие модули), но
        # смысл здесь — "родной город расы", а не "где герой рождается".
        ch.room = HUB_ROOM
        ch.flags["home_room"] = RACES.get(race, {}).get("start_room", HUB_ROOM)
        # стартовый набор новичка — единый источник правды (engine/starter.py):
        # база 4 малых зелья; хрупким классам набор усилен зельями (Этап 4.1,
        # обоснование и цифры до/после — docs/BALANCE_ONBOARDING.md).
        ch.inventory = list(_starter.starting_consumables(cls))
        # рефералка: привязать реферера, если игрок пришёл по deep-link (/start ref_123)
        _ref = pending_ref.pop(uid, None)
        if _ref is not None and _ref in chars:
            referral.set_referrer(ch, _ref)
        chars[uid] = ch
        analytics.track(uid, "character_created", {"race": race, "cls": cls})   # Этап 7.1
        await save(ch, force=True)      # создание персонажа — фиксируем сразу
        c = CLASSES[cls]
        rr = RACES[race]
        # Этап 8, фишка первых 10 минут: наставник боя (наставник_боя из хаба)
        # ЗАПОМИНАЕТ первое знакомство — при первом же диалоге его реплика может
        # опираться на этот факт (ai/memory.retrieve в промпте NPC).
        try:
            await _aimem.store(ch, "наставник_боя",
                               f"Впервые пришёл в Туманный Брод, выбрал путь {c['name']}")
        except Exception as e:
            _elog.log_err(_log, "mentor_first_memory_failed", e)
        # очистить экран создания: убрать сообщение-приглашение и введённое имя
        if info.get("msg"):
            try:
                await bot.delete_message(uid, info["msg"])
            except Exception:
                pass
        try:
            await message.delete()
        except Exception:
            pass
        start_city = WORLD[ch.room]["name"]
        _ename = _ts.esc_md(name)   # имя — UGC, экранируем для Markdown
        intro = (
            f"🌅 *Добро пожаловать в «Семь Корон», {rr['emoji']}{c['emoji']} {_ename}!*\n\n"
            f"Ты — {rr['name']} {c['name']}. Твой путь начинается в городе *{start_city}*.\n"
            f"_{_origin_flavor(race)}_\n\n"
            "Осмотрись, поговори с *наставником* — он даст первое испытание. "
            "Учись умениям, торгуй, бейся, объединяйся в группы.\n\n"
            "Играй *кнопками* под сообщениями. Освоишься — вводи команды с «/». "
            "Жми «❓ Помощь» в меню, чтобы узнать о механиках."
        )
        _intro = await bot.send_message(
            uid, intro, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🗡 Начать приключение", callback_data="begin")]]))
        intro_msg[uid] = _intro.message_id
        await gl.broadcast(ch.room, f"✨ Новый герой: {rr['emoji']}{c['emoji']} {_ename}!", exclude=uid)
        return

    if uid not in chars:
        await message.answer("Напишите /start, чтобы начать.")
        return

    ch = chars[uid]
    await clear_intro(uid)
    for line in streak.touch(ch):
        await message.answer(line, parse_mode="Markdown")
    if ch.flags.get("dead"):
        await death_screen(ch)
        return
    if uid in guild_naming:
        name = _ts.clean_name(text)
        if name is None:
            # флаг НЕ снимаем — ждём корректное название следующим сообщением
            await message.answer(
                "🚫 Такое название не подойдёт. Пришлите 2–20 символов "
                "(буквы/цифры), без невидимых спецсимволов и не начиная с @ или /.")
            return
        guild_naming.pop(uid, None)
        if guild_mgr.guild_of(ch.uid):
            await message.answer("Вы уже состоите в гильдии.")
        elif ch.gold < guildlib.CREATE_COST:
            await message.answer("Не хватает монет на основание гильдии.")
        elif db and db.pool:
            # БД-путь: списание cost и создание гильдии — одной транзакцией
            # (guild_tx.create_guild). gid резервируем из guild_mgr._next ДО await
            # (профилактика гонки в процессе); op_id = f"gcreate:{gid}".
            import time as _time
            gid = str(guild_mgr._next)
            guild_mgr._next += 1
            async with _econ_lock(ch.uid):
                ok, gmsg, char_gold = await guild_tx.create_guild(
                    db.pool.acquire, gid, name, ch.uid, guildlib.CREATE_COST, f"gcreate:{gid}")
                if ok and char_gold is not None:
                    ch.gold = char_gold
                    # зеркалим гильдию в guild_mgr (источник отображения) с тем же gid
                    guild_mgr.guilds[gid] = {
                        "name": name[:24], "leader": ch.uid, "members": [ch.uid],
                        "ranks": {str(ch.uid): "leader"}, "bank_gold": 0, "bank_items": [],
                        "founded": int(_time.time()),
                    }
                    guild_mgr.member_of[ch.uid] = gid
                    await save(ch, force=True)
            if ok:
                await message.answer(f"🏰 Гильдия «{_ts.esc_md(name)}» основана! Вы её лидер. "
                                     "Откройте 👥 Группа → 🏰 Гильдия.", parse_mode="Markdown")
            else:
                await message.answer("⚙️ Банк гильдии временно недоступен." if PROD else gmsg)
        elif PROD:
            await message.answer("⚙️ Банк гильдии временно недоступен.")
        else:
            ch.gold -= guildlib.CREATE_COST
            guild_mgr.create(ch.uid, name)
            await save(ch, force=True)      # основание гильдии (списано золото) — фиксируем сразу
            await message.answer(f"🏰 Гильдия «{_ts.esc_md(name)}» основана! Вы её лидер. "
                                 "Откройте 👥 Группа → 🏰 Гильдия.", parse_mode="Markdown")
        return
    low = text.lower()
    parts = low.split()
    cmd = parts[0] if parts else ""
    arg = " ".join(parts[1:])
    ccmd = cmds.canonical(cmd)

    # команды-просмотры (работают и с «/», и словом)
    if ccmd in ui.DIR_ICONS and ccmd in WORLD[ch.room]["exits"]:
        await do_move(message, ch, ccmd); return
    if ccmd == "help":
        await message.answer(_help_text(ch), parse_mode="Markdown", reply_markup=kb_help()); return
    if ccmd == "look":
        await enter_room(ch); return
    if ccmd == "stats":
        await message.answer(ui.render_stats(ch), parse_mode="Markdown", reply_markup=ui.kb_player(ch)); return
    if ccmd == "inv":
        await message.answer(ui.render_inventory(ch), parse_mode="Markdown", reply_markup=ui.kb_inventory(ch)); return
    if ccmd == "skills":
        await message.answer(ui.render_skills(ch), parse_mode="Markdown", reply_markup=ui.kb_skills(ch)); return
    if ccmd == "quests":
        _jt = quest.journal(ch)
        _er = errands.render(ch)
        if _er:
            _jt += "\n\n" + _er
        _jt += "\n" + _seven_crowns_block(ch)
        await message.answer(_jt, parse_mode="Markdown", reply_markup=ui.kb_journal(ch)); return
    if ccmd == "group":
        if not _uigate.unlocked("party", ch.level):
            await message.answer(_uigate.hint("party")); return
        await message.answer(render_group(ch), parse_mode="Markdown",
                             reply_markup=_kb([[InlineKeyboardButton(text="👥 Меню группы", callback_data="group")]])); return
    if ccmd == "map":
        await show_map_photo(ch)
        return
    if ccmd in ("territory", "war", "терр", "война"):
        if not _uigate.unlocked("factions", ch.level):
            await message.answer(_uigate.hint("factions")); return
        from engine import territory
        await message.answer(territory.render() +
                             "\n\n_Контроль набирают союзники фракции (макс. репутация), убивая мобов в зоне. "
                             "Владелец даёт союзникам +10% к опыту/золоту там._",
                             parse_mode="Markdown", reply_markup=ui.kb_back())
        return
    if ccmd in ("cast", "bash", "flee", "get", "use", "wield", "drop", "kill"):
        await text_action(message, ch, ccmd, arg)
        return

    if cmd in ("сказать", "say", "ск"):
        # реплика игрока — UGC: чистим управляющие символы и экранируем Markdown
        _said = _ts.esc_md(_ts.clean_chat(arg))
        if _said and await _chat_blocked(message, ch):
            return
        if _said:
            await gl.broadcast(ch.room, f"💬 *{_ts.esc_md(ch.name)}:* {_said}", exclude=uid)
            await message.answer(f"💬 Вы: {_said}")
        return
    if cmd in ("г", "гильдия", "guild"):
        if not guild_mgr.guild_of(ch.uid):
            await message.answer("Вы не в гильдии.")
        else:
            _gsaid = _ts.esc_md(_ts.clean_chat(arg))
            if _gsaid and await _chat_blocked(message, ch):
                return
            if _gsaid:
                for u in guild_mgr.members(ch.uid):
                    if u != uid:
                        await send(u, f"🏰 *{_ts.esc_md(ch.name)}:* {_gsaid}")
                await message.answer(f"🏰 Вы (гильдии): {_gsaid}")
        return
    if cmd in ("п", "пати", "party"):
        mates = [u for u in party_mgr.members(ch.uid) if u != uid]
        if not mates:
            await message.answer("Вы не в группе. Откройте 👥 Группа.")
        elif arg and await _chat_blocked(message, ch):
            return
        elif arg:
            for u in mates:
                await send(u, f"👥 *{ch.name}:* {arg}")
            await message.answer(f"👥 Вы (группе): {arg}")
        return
    if cmd in ("кто", "who"):
        if not chars:
            await message.answer("Никого нет.")
        else:
            L = ["🌐 *Онлайн:*"]
            for c in chars.values():
                L.append(f"• {CLASSES[c.cls]['emoji']} {c.name} (ур.{c.level}) — {WORLD[c.room]['name']}")
            await message.answer("\n".join(L), parse_mode="Markdown")
        return
    if cmd in ui.DIR_ICONS:
        await do_move(message, ch, cmd)
        return
    if cmd in ("купить", "buy"):
        key = _resolve_shop(arg, ch)
        if key:
            await do_buy(message, ch, key)
        else:
            await message.answer("🤷 Нет товара. Откройте 🏪 Магазин.")
        return

    if uid in talking_to and talking_to[uid] in WORLD[ch.room].get("npc", []):
        npc_id = talking_to[uid]
        reply, act = await npc_ai.say_action(ch, npc_id, player_text=text)
        if reply:
            _stash_errand(ch, npc_id, act)
            # реплика LLM — потенциально с «сырой» разметкой: экранируем для Markdown
            await message.answer(f"{npclib.emoji(npc_id)} {_ts.esc_md(reply)}",
                                 parse_mode="Markdown",
                                 reply_markup=ui.kb_npc(ch, npc_id, highlight=act))
            return
    await message.answer("🤔 Используйте кнопки или «команды».")


# ───────── действия ─────────
async def move_core(ch: Character, direction: str) -> bool:
    """Переместить игрока (без показа экрана). True — успех."""
    exits = WORLD[ch.room]["exits"]
    if direction not in exits:
        return False
    old = ch.room
    for m in world.living_in(old):
        if ch.uid in m.aggro:
            m.aggro.remove(ch.uid)
    ch.target = None
    ch.reset_combat_resource()
    await drop_combat_photo(ch.uid)
    combat_view.pop(ch.uid, None)
    talking_to.pop(ch.uid, None); npc_ai.reset(ch.uid)
    ch.room = exits[direction]
    await broadcast_ephemeral(old, f"🚶 {ch.name} уходит ({direction}).", exclude=ch.uid)
    await broadcast_ephemeral(ch.room, f"🚶 {ch.name} приходит.", exclude=ch.uid)
    if WORLD[ch.room].get("rest"):
        ch.hp = ch.max_hp; ch.mp = ch.start_resource()
    engaged = False
    if not WORLD[ch.room].get("safe"):
        for _m in world.living_in(ch.room):
            if _m.mob_id in content.AGGRESSIVE and ch.uid not in _m.aggro:
                _m.aggro.append(ch.uid)
                engaged = True
                await send(ch.uid, f"⚠️ {_m.meta['emoji']} *{_m.meta['name']}* бросается на вас!")
    await save(ch)
    return True, engaged


async def send_tutorial(ch: Character, event: str):
    """Отправить подсказки/награды туториала по событию (если сработал шаг)."""
    lines = tutorial.on_event(ch, event)
    if lines:
        await save(ch)      # награды/бонус туториала — фиксируем сразу
    for line in lines:
        await send(ch.uid, line)


def _help_text(ch: Character) -> str:
    """Текст экрана помощи + прогресс обучения новичка (если актуально)."""
    base = cmds.render_help()
    tut = tutorial.render(ch)
    return base + "\n\n" + tut if tut else base


async def do_move(target, ch: Character, direction: str, cb=None):
    moved, _ = await move_core(ch, direction)
    if not moved:
        if cb: await cb.answer("Туда нельзя", show_alert=True)
        return
    await enter_room(ch, cb=cb)
    await send_tutorial(ch, "move")
    analytics.track_once(ch, "first_move")   # Этап 7.1
    # Этап 8, фишка первых 10 минут: первый шаг новичка попадает в летопись мира
    # (record_once дедупит по uid — событие пишется ровно раз). Имя героя звучит
    # в хронике → NPC начинают о нём сплетничать (_chronicle_context в промпте).
    _chronicle.record_once("newcomer", str(ch.uid),
                           f"{ch.name} ступил(а) на туманные улицы Брода")


async def do_attack(cb: CallbackQuery, ch: Character, mob_key: str):
    mob = world.find(ch.room, mob_key)
    if not mob:
        combat_view.pop(ch.uid, None)
        await safe_edit(cb, "🤷 Враг повержен или ушёл.\n\n" +
                        ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))
        return
    started = bool(combat_view.get(ch.uid, {}).get("id"))
    ch.target = mob.key
    if ch.uid not in mob.aggro:
        mob.aggro.append(ch.uid)
    # на первом ходу панель временно привязана к меню комнаты (для пере-поста при мгновенном килле)
    cv = _cv(ch.uid); cv["chat"] = cb.message.chat.id; cv["id"] = cb.message.message_id
    combat.advance_player_turn(ch)          # тик кулдаунов/баффов
    ev = combat.player_basic_attack(ch, mob)
    set_combat_line(ch.uid, "player", "\n".join(ev))
    await send_tutorial(ch, "attack")       # первый удар — шаг обучения
    analytics.track_once(ch, "first_combat")   # Этап 7.1
    if mob.hp <= 0:
        killers = [chars[u] for u in mob.aggro if u in chars]
        await gl.on_mob_death(mob, killers)  # combat_reward пере-постит панель вниз
        return
    await save(ch)
    if not started:
        await start_combat_view(cb, ch, mob)   # фото моба сверху + панель под ним
    else:
        await render_combat_cb(cb, ch)


async def do_skill(cb: CallbackQuery, ch: Character, sid: str):
    party = [c for c in chars.values() if c.room == ch.room and c.hp > 0]
    ok, ev = combat.use_skill(ch, sid, world, party)
    if not ok:
        await cb.answer(ev[0].replace("*", ""), show_alert=True)
        return
    sk = SKILLS[sid]
    # успешный скилл = ход прошёл: тикаем кулдауны/баффы, но сохраняем
    # только что выставленный кулдаун этого скилла
    _fresh_cd = ch.cooldowns.get(sid, 0)
    combat.advance_player_turn(ch)
    ch.cooldowns[sid] = _fresh_cd
    set_combat_line(ch.uid, "player", "\n".join(ev))
    await send_tutorial(ch, "skill")        # первое умение — шаг обучения
    analytics.track_once(ch, "first_skill")   # Этап 7.1
    mob = world.find(ch.room, ch.target) if ch.target else None
    # привязываем панель к нажатому сообщению (для пере-поста на убийстве)
    cv = _cv(ch.uid); cv["chat"] = cb.message.chat.id; cv["id"] = cb.message.message_id
    if mob and mob.hp <= 0:
        killers = [chars[u] for u in mob.aggro if u in chars]
        await gl.on_mob_death(mob, killers)  # combat_reward пере-постит панель вниз
        return
    await save(ch)
    if mob:
        await render_combat_cb(cb, ch)
    else:
        # лечение/бафф вне боя — показываем комнату
        combat_view.pop(ch.uid, None)
        await safe_edit(cb, "\n".join(ev) + "\n\n" +
                        ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))


def _seven_crowns_block(ch) -> str:
    """Экран прогресса мета-коллекции «Семь Корон» для журнала (этап 5.2).
    N/7 венценосцев по данным bestiary.kills — повержены/ещё правят."""
    col = (bestiary.COLLECTIONS or {}).get("col_seven_crowns")
    if not col:
        return ""
    mobs = col.get("mobs", [])
    done = "col_seven_crowns" in (ch.flags.get("collections_done") or [])
    rows, slain = [], 0
    for m in mobs:
        nm = MOBS.get(m, {}).get("name", m)
        em = MOBS.get(m, {}).get("emoji", "•")
        if bestiary.kills(ch, m) >= 1:
            slain += 1
            rows.append(f"👑 {em} ~{nm}~ — повержен")
        else:
            rows.append(f"🔒 {em} {nm} — ещё правит")
    L = ["", f"👑 *Семь Корон* — {slain}/{len(mobs)}"]
    if col.get("desc"):
        L.append(f"_{col['desc']}_")
    L.extend(rows)
    if done:
        L.append(f"✅ Короны собраны — титул «{col.get('title','')}».")
    return "\n".join(L)


async def do_use(cb: CallbackQuery, ch: Character, key: str):
    if key not in ch.inventory:
        await cb.answer("Нет такого предмета", show_alert=True)
        return
    eff = ITEMS[key].get("effect", {})
    _is_potion = "heal" in eff        # зелье лечения — шаг обучения
    msg = []
    if "heal" in eff:
        before = ch.hp; ch.hp = min(ch.max_hp, ch.hp + eff["heal"] * content.HP_SCALE)
        msg.append(f"🧪 +{ch.hp - before} HP")
    if "mana" in eff and ch.resource_type == "mana":
        before = ch.mp; ch.mp = min(ch.max_resource, ch.mp + eff["mana"])
        msg.append(f"💙 +{ch.mp - before} MP")
    if "attrbuff" in eff:
        msg.append(apply_attrbuff(ch, eff))
    ch.inventory.remove(key)
    # use-цели квестов: расход предмета уже по обычным правилам выше, здесь — зачёт
    _quse = quest.on_use_item(ch, key)
    if _quse:
        msg.extend(_quse)
    # если в бою — остаёмся в боевой клаве
    in_combat = ch.target and world.find(ch.room, ch.target)
    if in_combat:
        combat.advance_player_turn(ch)      # глоток зелья в бою — ход
    await save(ch)
    if _is_potion:
        await send_tutorial(ch, "potion")
    if in_combat:
        set_combat_line(ch.uid, "player", ", ".join(msg))
        await render_combat_cb(cb, ch)
    else:
        txt = (", ".join(msg) + f"\n❤️ {ch.hp}/{ch.max_hp} 💙 {ch.mp}/{ch.max_mp}")
        await safe_edit(cb, txt + "\n\n" + ui.render_room(ch, world, others_in(ch.room)),
                        ui.kb_room(ch, world))


async def do_equip(cb: CallbackQuery, ch: Character, key: str):
    if key not in ch.inventory:
        await cb.answer("Нет предмета", show_alert=True); return
    from engine import equip as _equip
    ok, reason = _equip.can_equip(ch, key)
    if not ok:
        await cb.answer(reason, show_alert=True); return
    meta = ITEMS[key]
    slot = meta.get("slot")
    if slot == "ring":
        slot = "ring1" if not ch.equipment.get("ring1") else "ring2"
    ch.equipment[slot] = key
    ch.set_durab(slot, 100)
    await save(ch)
    await safe_edit(cb, f"⚙️ Экипировано: {meta['name']}.\n\n" + ui.render_stats(ch),
                    ui.kb_inventory(ch))


async def do_flee(cb: CallbackQuery, ch: Character):
    mob = world.find(ch.room, ch.target) if ch.target else None
    if not mob:
        await show_room(cb.message, ch, edit_cb=cb); return
    combat.advance_player_turn(ch)          # побег — тоже ход
    if random.random() < 0.5:
        if ch.uid in mob.aggro:
            mob.aggro.remove(ch.uid)
        ch.target = None
        await drop_combat_photo(ch.uid)
        combat_view.pop(ch.uid, None)
        await gl.broadcast(ch.room, f"🏃 {ch.name} отступает.", exclude=ch.uid)
        await safe_edit(cb, "🏃 Вы вырвались из боя!\n\n" +
                        ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))
    else:
        ev = combat.mob_attack(mob, ch)
        set_combat_line(ch.uid, "player", "🏃 Сбежать не вышло!")
        set_combat_line(ch.uid, "mob", " ".join(ev))
        await save(ch)
        if ch.hp <= 0:
            combat_view.pop(ch.uid, None)
            await gl.on_player_death(ch)
        else:
            await render_combat_cb(cb, ch)


async def text_action(message, ch: Character, verb: str, arg: str):
    """Текстовые команды действий: cast/bash/flee/get/use/wield/drop/kill."""
    from bot import mudnames
    if verb == "flee":
        mob = world.find(ch.room, ch.target) if ch.target else None
        if not mob:
            await message.answer("Вы не в бою."); return
        combat.advance_player_turn(ch)
        if random.random() < 0.5:
            if ch.uid in mob.aggro:
                mob.aggro.remove(ch.uid)
            ch.target = None
            await drop_combat_photo(ch.uid)
            combat_view.pop(ch.uid, None)
            await save(ch)
            await bot.send_message(ch.uid, "🏃 Вы вырвались из боя!")
            await enter_room(ch)
        else:
            ev = combat.mob_attack(mob, ch)
            await save(ch)
            await message.answer("🏃 Сбежать не вышло!\n" + " ".join(ev))
            if ch.hp <= 0:
                combat_view.pop(ch.uid, None)
                await gl.on_player_death(ch)
        return
    if verb in ("cast", "bash"):
        if verb == "bash":
            sid = (mudnames.match_skill("bash", ch.skills)
                   or mudnames.match_skill("trip", ch.skills)
                   or mudnames.match_skill("kick", ch.skills))
        else:
            sid = mudnames.match_skill(arg, ch.skills)
        if not sid:
            await message.answer("🚫 Такого умения нет в вашей боевой панели."); return
        party = [c for c in chars.values() if c.room == ch.room and c.hp > 0]
        ok, lines = combat.use_skill(ch, sid, world, party)
        await save(ch)
        await message.answer("\n".join(lines), parse_mode="Markdown")
        return
    if verb == "get":
        key = mudnames.match_item(arg, ground_items_for(ch, ch.room))
        if not key:
            await message.answer("🚫 Здесь нет такого предмета."); return
        take_ground_item(ch, ch.room, key)
        await save(ch)
        await message.answer(f"✋ Вы подняли: {mudnames.item_label(key)}")
        return
    if verb in ("use", "wield", "drop"):
        key = mudnames.match_item(arg, ch.inventory)
        if not key:
            await message.answer("🚫 Нет такого предмета в сумке."); return
        if verb == "drop":
            # ВАЖНО: НЕ дописываем ключ обратно в статический WORLD[room]["items"] —
            # это был бы побочный канал мутации мирового списка, который свёл бы на
            # нет персональный лут (см. engine.world.ground_items_for/take_ground_item):
            # выброшенный предмет стал бы «вечным» и общим для всех игроков комнаты.
            ch.inventory.remove(key)
            await save(ch)
            await message.answer(f"🗑 Выброшено: {mudnames.item_label(key)}")
        elif verb == "wield":
            from engine import equip as _equip
            okq, reason = _equip.can_equip(ch, key)
            if not okq:
                await message.answer("🚫 " + reason); return
            slot = ITEMS[key].get("slot")
            if slot == "ring":
                slot = "ring1" if not ch.equipment.get("ring1") else "ring2"
            ch.equipment[slot] = key
            ch.set_durab(slot, 100)
            await save(ch)
            await message.answer(f"⚙️ Экипировано: {mudnames.item_label(key)}")
        else:
            eff = ITEMS[key].get("effect", {})
            if eff.get("heal"):
                ch.hp = min(ch.max_hp, ch.hp + eff["heal"] * content.HP_SCALE)
            if eff.get("mana"):
                ch.mp = min(ch.max_resource, ch.mp + eff["mana"])
            ch.inventory.remove(key)
            await save(ch)
            await message.answer(f"🧪 Использовано: {mudnames.item_label(key)}")
        return
    if verb == "kill":
        mob = None
        for m in world.living_in(ch.room):
            if not arg or arg in m.meta["name"].lower() or arg in m.mob_id.lower():
                mob = m; break
        if not mob:
            await message.answer("🚫 Здесь некого атаковать."); return
        await message.answer(
            "⚔️ В бой!",
            reply_markup=_kb([[InlineKeyboardButton(
                text=f"⚔️ {mob.meta['emoji']} {mob.meta['name']}",
                callback_data=f"atk:{mob.key}")]]))
        return


def _resolve_shop(name: str, ch: Character = None):
    """Найти id товара по названию в ассортименте торговца в комнате игрока."""
    stock = ui.shop_stock(ch) if ch else ui.SHOP_ITEMS
    name = name.strip().lower()
    key = name.replace(" ", "_")
    if key in stock:
        return key
    by_name = {ITEMS[k]["name"].lower(): k for k in stock if k in ITEMS}
    return by_name.get(name)


async def do_buy(target, ch: Character, key: str, cb=None):
    it = ITEMS[key]
    req = it.get("class_req")
    if req and ch.cls not in req:
        if cb: await cb.answer("Не для вашего класса", show_alert=True)
        return
    price = _buy_price(ch, key)
    if ch.gold < price:
        if cb: await cb.answer(f"Нужно {money.fmt(price)}", show_alert=True)
        else: await target.answer(f"💰 Не хватает: нужно {money.fmt(price)}, есть {money.fmt(ch.gold)}.")
        return
    ch.gold -= price
    ch.inventory.append(key)
    await save(ch, force=True)      # покупка в лавке — фиксируем сразу
    msg = f"✅ Куплено: {it['name']} за 💰{money.fmt(price)}. (Осталось {money.fmt(ch.gold)})"
    if cb:
        await safe_edit(cb, msg, ui.kb_shop(ch))
    else:
        await target.answer(msg)


# ───────── карточка предмета (фото + статы + действие) ─────────
async def safe_edit_caption(cb: CallbackQuery, caption: str, kb):
    try:
        await cb.message.edit_caption(caption=caption, parse_mode="Markdown", reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            pass
        else:
            await cb.message.answer(caption, parse_mode="Markdown", reply_markup=kb)


async def send_item_card(cb: CallbackQuery, ch: Character, ctx: str, key: str):
    caption = ui.item_caption(key, ctx, ch)
    kb = ui.kb_item_card(key, ctx, ch)
    try:
        from bot import item_images
        path = item_images.card_image(key) or os.path.join(IMAGES_DIR, "items", key + ".png")
    except Exception:
        path = os.path.join(IMAGES_DIR, "items", key + ".png")
    try:
        if os.path.exists(path):
            await bot.send_photo(cb.message.chat.id, FSInputFile(path),
                                 caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            await cb.message.answer(caption, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        await cb.message.answer(caption, parse_mode="Markdown", reply_markup=kb)
    await cb.answer()


async def card_buy(cb: CallbackQuery, ch: Character, key: str):
    from engine import karma
    if karma.vendor_refuses(ch):
        await cb.answer("🚫 Торговец отказывается иметь дело с изгоем!", show_alert=True); return
    it = ITEMS[key]
    req = it.get("class_req")
    if req and ch.cls not in req:
        await cb.answer("Не для вашего класса", show_alert=True); return
    price = _buy_price(ch, key)
    if ch.gold < price:
        await cb.answer(f"Нужно {money.fmt(price)}, есть {money.fmt(ch.gold)}", show_alert=True); return
    ch.gold -= price
    ch.inventory.append(key)
    await save(ch, force=True)      # покупка (карточка) — фиксируем сразу
    await cb.answer()
    await cb.message.answer(
        f"🛍 Куплено: *{it['name']}* за 💰{money.fmt(price)}. "
        f"Осталось: 💰{money.fmt(ch.gold)}.", parse_mode="Markdown")
    await safe_edit_caption(cb, ui.item_caption(key, "shop", ch), ui.kb_item_card(key, "shop", ch))


async def card_use(cb: CallbackQuery, ch: Character, key: str):
    if key not in ch.inventory:
        await cb.answer("Нет предмета", show_alert=True); return
    eff = ITEMS[key].get("effect", {})
    msg = []
    if "heal" in eff:
        before = ch.hp; ch.hp = min(ch.max_hp, ch.hp + eff["heal"] * content.HP_SCALE)
        msg.append(f"+{ch.hp - before} HP")
    if "mana" in eff and ch.resource_type == "mana":
        before = ch.mp; ch.mp = min(ch.max_resource, ch.mp + eff["mana"])
        msg.append(f"+{ch.mp - before} MP")
    if "attrbuff" in eff:
        msg.append(apply_attrbuff(ch, eff))
    if not msg:
        await cb.answer("Этот предмет нельзя использовать так", show_alert=True); return
    ch.inventory.remove(key)
    await save(ch)
    await cb.answer("🧪 " + ", ".join(msg))
    if key in ch.inventory:
        await safe_edit_caption(cb, ui.item_caption(key, "inv", ch), ui.kb_item_card(key, "inv", ch))
    else:
        try:
            await cb.message.delete()
        except Exception:
            pass


async def card_unequip(cb: CallbackQuery, ch: Character, key: str):
    slot = None
    for s, v in ch.equipment.items():
        if v == key:
            slot = s
            break
    if not slot:
        await cb.answer("Этот предмет не надет", show_alert=True); return
    ch.equipment[slot] = None
    await save(ch)
    await cb.answer("🚫 Снято")
    await safe_edit_caption(cb, ui.item_caption(key, "inv", ch), ui.kb_item_card(key, "inv", ch))


async def card_equip(cb: CallbackQuery, ch: Character, key: str):
    if key not in ch.inventory:
        await cb.answer("Нет предмета", show_alert=True); return
    from engine import equip as _equip
    ok, reason = _equip.can_equip(ch, key)
    if not ok:
        await cb.answer(reason, show_alert=True); return
    meta = ITEMS[key]
    slot = meta.get("slot")
    if slot == "ring":
        slot = "ring1" if not ch.equipment.get("ring1") else "ring2"
    ch.equipment[slot] = key
    ch.set_durab(slot, 100)
    await save(ch)
    await cb.answer(f"⚙️ Надето: {meta['name']}")
    await safe_edit_caption(cb, ui.item_caption(key, "inv", ch), ui.kb_item_card(key, "inv", ch))


async def do_sell(cb: CallbackQuery, ch: Character, key: str):
    if not _vendor_here(ch):
        await cb.answer("Продажа только у торговца", show_alert=True); return
    _allowed = ui.vendor_sell_types(ch)
    if _allowed and ITEMS.get(key, {}).get("type") not in _allowed:
        await cb.answer("Этот торговец такое не скупает — поищите нужную лавку", show_alert=True); return
    from engine import content
    price = content.sell_price(key)
    # нельзя продать надетое или то, чего нет
    equipped = set(v for v in ch.equipment.values() if v)
    have = ch.inventory.count(key)
    avail = have - (1 if key in equipped else 0)
    if price <= 0 or avail <= 0:
        await cb.answer("Это не продаётся", show_alert=True); return
    ch.inventory.remove(key)
    ch.gold += price
    await save(ch, force=True)      # продажа торговцу — фиксируем сразу
    await safe_edit(cb, f"💰 Продано: {ITEMS[key]['name']} за {money.fmt(price)}. "
                    f"(Всего: {money.fmt(ch.gold)})", ui.kb_sell(ch))


async def card_salvage(cb: CallbackQuery, ch: Character, key: str):
    """Разобрать снаряжение в туманную пыль. Двойной клик = подтверждение (как reset).
    Доступно из сумки где угодно — утилизация в поле часть цикла."""
    import time as _time
    from engine import salvage
    ok, why = salvage.can_salvage(ch, key)
    if not ok:
        await cb.answer(why, show_alert=True); return
    pend = pending_salvage.get(ch.uid)
    now = _time.time()
    if not pend or pend[0] != key or now - pend[1] > 20:
        pending_salvage[ch.uid] = (key, now)
        d = salvage.dust_for(key)
        await cb.answer(f"🔨 Разобрать безвозвратно в {d} пыли? "
                        f"Нажмите «Разобрать» ещё раз для подтверждения.", show_alert=True)
        return
    pending_salvage.pop(ch.uid, None)
    ok, msg, dust = salvage.salvage(ch, key)
    if not ok:
        await cb.answer(msg.replace("*", ""), show_alert=True); return
    await save(ch, force=True)      # разборка (расход предмета) — фиксируем сразу
    await cb.answer(f"🔨 +{dust} пыли")
    if key in ch.inventory:         # ещё остались экземпляры — обновить карточку
        await safe_edit_caption(cb, ui.item_caption(key, "inv", ch), ui.kb_item_card(key, "inv", ch))
    else:                            # последний разобран — закрыть карточку
        try:
            await cb.message.delete()
        except Exception:
            pass


async def do_craft(cb: CallbackQuery, ch: Character, rid: str):
    if "кузнец" not in WORLD[ch.room].get("npc", []):
        await cb.answer("Ковка только у кузнеца", show_alert=True); return
    from engine import craft
    ok, msg = craft.craft(ch, rid)
    if not ok:
        await cb.answer(msg.replace("*", ""), show_alert=True)
        await safe_edit(cb, ui.render_craft(ch), ui.kb_craft(ch))
        return
    await save(ch, force=True)      # крафт (расход материалов) — фиксируем сразу
    await safe_edit(cb, msg + "\n\nОткрой 🎒 Сумку, чтобы надеть.", ui.kb_craft(ch))


def _kb_ench(ch: Character) -> InlineKeyboardMarkup:
    from engine import enchant
    rows = []
    for slot, label in (("weapon", "🗡 Оружие"), ("armor", "🛡 Броня")):
        item = ch.equipment.get(slot)
        if not item:
            rows.append([InlineKeyboardButton(text=f"{label}: пусто", callback_data="noop")])
            continue
        lvl = enchant.level(ch, slot)
        if lvl >= enchant.MAX_ENCH:
            rows.append([InlineKeyboardButton(text=f"{label} +{lvl} (макс.)", callback_data="noop")])
            continue
        c = enchant.cost(lvl)
        ch_pct = int(enchant.success_chance(lvl) * 100)
        rows.append([InlineKeyboardButton(
            text=f"{label} +{lvl}→+{lvl+1} · {ch_pct}% · 💰{money.fmt(c)}",
            callback_data=f"ench:{slot}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="shop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_ench(cb: CallbackQuery, ch: Character):
    if "кузнец" not in WORLD[ch.room].get("npc", []):
        await cb.answer("Зачарование — у кузнеца", show_alert=True); return
    from engine import enchant
    txt = ("✨ *Зачарование снаряжения*\n"
           f"Безопасно до +{enchant.SAFE}. Выше — риск: при провале уровень падает на 1.\n"
           f"🗡 +{enchant.level(ch,'weapon')} (+{enchant.bonus_atk(ch)} к атаке)\n"
           f"🛡 +{enchant.level(ch,'armor')} (+{enchant.bonus_def(ch)} к защите)")
    await safe_edit(cb, txt, _kb_ench(ch))


async def do_ench(cb: CallbackQuery, ch: Character, slot: str):
    if "кузнец" not in WORLD[ch.room].get("npc", []):
        await cb.answer("Зачарование — у кузнеца", show_alert=True); return
    from engine import enchant
    status, lvl = enchant.attempt(ch, slot)
    msgs = {
        "empty": "Нет предмета в этом слоте",
        "max": "Уже максимальный уровень",
        "poor": "Не хватает монет",
        "ok": f"✨ Успех! Теперь +{lvl}",
        "fail": f"💢 Неудача, но уровень сохранён (+{lvl})",
        "fail_down": f"💥 Провал! Уровень упал до +{lvl}",
    }
    if status in ("ok", "fail", "fail_down"):
        await save(ch)
        await cb.answer(msgs[status], show_alert=True)
        await safe_edit(cb, "✨ *Зачарование снаряжения*", _kb_ench(ch))
    else:
        await cb.answer(msgs.get(status, "Нельзя"), show_alert=True)


def _kb_sockets(ch: Character) -> InlineKeyboardMarkup:
    from engine import sockets
    rows = []
    # руны из сумки — вставить в первый свободный слот
    seen = set()
    for it in ch.inventory:
        if sockets.is_rune(it) and it not in seen:
            seen.add(it)
            rows.append([InlineKeyboardButton(
                text=f"💠 Вставить: {ITEMS[it]['name']}", callback_data=f"socketput:{it}")])
    rows.append([InlineKeyboardButton(text="🛒 Купить руны", callback_data="runeshop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="shop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_sockets(cb: CallbackQuery, ch: Character):
    if "кузнец" not in WORLD[ch.room].get("npc", []):
        await cb.answer("Сокеты — у кузнеца", show_alert=True); return
    from engine import sockets
    await safe_edit(cb, sockets.render(ch) + "\n\n_Гнёзда — на синих предметах и выше._",
                    _kb_sockets(ch))


async def do_socket_put(cb: CallbackQuery, ch: Character, rune: str):
    from engine import sockets
    # найти первый надетый слот со свободным гнездом
    target = next((s for s in ("weapon", "armor", "shield", "head", "legs", "hands", "feet")
                   if sockets.free_sockets(ch, s) > 0), None)
    if not target:
        await cb.answer("Нет свободных гнёзд (нужен синий+ предмет)", show_alert=True); return
    ok, msg = sockets.socket(ch, target, rune)
    await save(ch)
    await cb.answer(msg.replace("*", ""), show_alert=not ok)
    await safe_edit(cb, sockets.render(ch), _kb_sockets(ch))


async def show_runeshop(cb: CallbackQuery, ch: Character):
    from engine import content as _c
    rows = []
    runes = [k for k, v in _c.ITEMS.items() if v.get("type") == "rune"]
    runes.sort(key=lambda k: (_c.ITEMS[k].get("price", 0)))
    for k in runes:
        it = _c.ITEMS[k]
        rows.append([InlineKeyboardButton(
            text=f"{it.get('emoji','💠')} {it['name']} — 💰{money.fmt(it['price'])}",
            callback_data=f"runebuy:{k}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="socketmenu")])
    await safe_edit(cb, "🛒 *Руны для сокетов*\nКупи и вставь в гнездо снаряжения.",
                    InlineKeyboardMarkup(inline_keyboard=rows))


async def do_runebuy(cb: CallbackQuery, ch: Character, key: str):
    if ITEMS.get(key, {}).get("type") != "rune":
        await cb.answer("Нет такой руны", show_alert=True); return
    price = ITEMS[key].get("price", 0)
    if ch.gold < price:
        await cb.answer("Не хватает монет", show_alert=True); return
    ch.gold -= price
    ch.inventory.append(key)
    await save(ch)
    await cb.answer(f"Куплено: {ITEMS[key]['name']}")
    await show_runeshop(cb, ch)


# ───────── Этап 10: экспорт данных игрока (/delete_me → «📤 Мои данные») ─────────
# Служебные ключи флагов, не нужные самому игроку в выгрузке (внутренняя
# бухгалтерия рантайма: квота пуш-уведомлений notify_*, дедуп аналитики,
# список недоступных квестов). Остальные флаги (напр. ref_by, ref_count,
# квестовые выборы, память NPC) — это как раз то, что игрок вправе увидеть.
_EXPORT_DROP_FLAGS = {"analytics_once", "quest_locks"}


def _filtered_flags(ch: Character) -> dict:
    flags = ch.flags or {}
    return {k: v for k, v in flags.items()
           if not str(k).startswith("notify_") and k not in _EXPORT_DROP_FLAGS}


async def _export_payload(ch: Character) -> dict:
    """Собрать JSON-выгрузку данных игрока: персонаж (без служебных ключей
    флагов) + атрибуция источника перехода + последние 20 записей
    economy_ledger. Без db.pool — только персонаж (attribution/ledger — []/None)."""
    payload = {
        "character": {
            "uid": ch.uid, "name": ch.name, "cls": ch.cls, "race": ch.race,
            "room": ch.room, "level": ch.level, "xp": ch.xp,
            "gold": ch.gold, "equipment": ch.equipment, "inventory": ch.inventory,
            "quests": ch.quests, "flags": _filtered_flags(ch),
        },
        "attribution": None,
        "economy_ledger_recent": [],
    }
    if db and db.pool:
        payload["attribution"] = await db.get_attribution(ch.uid)
        payload["economy_ledger_recent"] = await db.ledger_recent(ch.uid, limit=20)
    return payload


# ───────── callbacks ─────────
@dp.callback_query()
async def on_cb(cb: CallbackQuery):
    uid = cb.from_user.id
    data = cb.data or ""
    action, _, arg = data.partition(":")
    _cid_var.set(uuid.uuid4().hex[:8])   # Этап 9: correlation id этой цепочки
    _mark_session(uid)   # Этап 7.1: session_start по тишине ≥30 мин

    # Этап 7.2: гейт бана (админов не трогаем).
    if _mod.is_banned(uid) and not is_admin(uid):
        await cb.answer("⛔️ Доступ ограничен. /support", show_alert=True)
        return

    # Этап 7.2: пауза торговли — блок сделок аукциона/банка (просмотр витрин ок).
    if not TRADING_ENABLED and action in _TRADING_GATED:
        await cb.answer("⚙️ Торговля приостановлена.", show_alert=True)
        return

    # Этап 7.2: админские колбэки (adm:*) — до общей диспетчеризации, требуют прав.
    if action == "adm":
        await _admin_cb(cb, uid, arg)
        return

    # Этап 4.2: заблокированная кнопка гейтованной фичи — просто подсказка,
    # без изменения текущего экрана (ui._gated_btn ставит этот callback).
    if action == "locked":
        await cb.answer(_uigate.hint(arg))
        return

    # выбор расы — первый шаг создания
    if action == "race":
        if arg not in RACES:
            await cb.answer(); return
        creating[uid] = {"race": arg}
        analytics.track(uid, "race_selected", {"race": arg})   # Этап 7.1
        r = RACES[arg]
        await safe_edit(cb,
            f"{r['emoji']} Раса: *{r['name']}*\n_{r['desc']}_\n\n" + ui.render_classes(arg),
            ui.kb_classes(arg))
        await cb.answer()
        return

    # выбор класса — второй шаг
    if action == "pick":
        if uid not in creating or "race" not in creating[uid]:
            # без расы — отправим на начало
            await safe_edit(cb, ui.render_races(), ui.kb_races())
            await cb.answer("Сначала выбери расу"); return
        race = creating[uid]["race"]
        if arg not in CLASSES or arg not in RACES[race]["allowed_classes"]:
            await cb.answer("Этот класс недоступен расе", show_alert=True); return
        creating[uid]["cls"] = arg
        analytics.track(uid, "class_selected", {"cls": arg, "race": race})   # Этап 7.1
        c = CLASSES[arg]; r = RACES[race]
        await safe_edit(cb,
            f"{r['emoji']}{c['emoji']} *{r['name']} {c['name']}*\n_{c['desc']}_\n\n"
            "✍️ Теперь напиши своё имя одним сообщением.", None)
        creating[uid]["msg"] = cb.message.message_id   # чтобы очистить экран после ввода имени
        await cb.answer()
        return

    # ── /reset: подтверждение / отмена (шаг 2) ──
    if action == "reset_cancel":
        pending_reset.pop(uid, None)
        try:
            await cb.message.edit_text("Сброс отменён. Персонаж на месте.")
        except Exception:
            pass
        await cb.answer("Отменено")
        return
    if action == "reset_confirm":
        import time as _time
        ch = chars.get(uid)
        if ch is None:
            # уже удалён (double-click) — идемпотентно
            pending_reset.pop(uid, None)
            await cb.answer("Персонаж уже сброшен.")
            try:
                await cb.message.edit_text("🔄 Прогресс сброшен. Напишите /start, чтобы начать заново.")
            except Exception:
                pass
            return
        ts = pending_reset.get(uid)
        if not _persist.reset_pending_valid(ts, _time.time()):
            pending_reset.pop(uid, None)
            await cb.answer("Подтверждение истекло", show_alert=True)
            try:
                await cb.message.edit_text("⌛️ Подтверждение истекло — вызови /reset снова.")
            except Exception:
                pass
            return
        pending_reset.pop(uid, None)
        # аудит ДО удаления (пока есть данные персонажа)
        if db and db.pool:
            await db.add_audit(uid, "reset",
                               {"name": ch.name, "level": ch.level, "gold": ch.gold})
            await db.soft_delete(uid)     # мягкое удаление (восстановимо 24ч)
        chars.pop(uid, None)
        _char_dirty.discard(uid)
        try:
            await cb.message.edit_text("🔄 Прогресс сброшен. Напишите /start, чтобы начать заново.")
        except Exception:
            pass
        await cb.answer("Персонаж удалён")
        return
    if action == "reset_restore":
        if uid in chars:
            await cb.answer("Персонаж уже активен."); return
        restored = await db.restore_deleted(uid) if (db and db.pool) else None
        if restored is None:
            await cb.answer("Восстанавливать нечего.", show_alert=True)
            return
        chars[uid] = restored
        await db.add_audit(uid, "restore", {"name": restored.name, "level": restored.level})
        try:
            await cb.message.edit_text(
                f"♻️ Персонаж *{_ts.esc_md(restored.name)}* восстановлен!", parse_mode="Markdown")
        except Exception:
            pass
        await enter_room(restored)
        await cb.answer("Восстановлено")
        return

    if uid not in chars:
        await cb.answer("Напишите /start", show_alert=True); return
    ch = chars[uid]
    await clear_intro(uid)

    # Этап 10: экспорт данных игрока (/delete_me → «📤 Мои данные»). Не игровое
    # действие — работает и с мёртвым персонажем, до общего гейта "dead" ниже.
    if action == "export_data":
        payload = await _export_payload(ch)
        blob = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        doc = BufferedInputFile(blob, filename=f"my_data_{uid}.json")
        try:
            await bot.send_document(cb.message.chat.id, doc,
                                    caption="📤 Ваши данные (экспорт, JSON)")
            await cb.answer("Файл отправлен")
        except Exception:
            await cb.answer("Не удалось отправить файл.", show_alert=True)
        return

    if ch.flags.get("dead") and action != "respawn":
        await cb.answer("Вы мертвы. Нажмите «⚰️ Возродиться».", show_alert=True)
        return
    if action == "respawn":
        await do_respawn(cb, ch)
        return
    if action == "bind":
        if WORLD[ch.room].get("respawn"):
            ch.flags["bind"] = ch.room
            await save(ch)
            await cb.answer("🪦 Точка возрождения привязана здесь.", show_alert=True)
            await safe_edit(cb, ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))
        else:
            await cb.answer("Здесь нельзя привязаться.", show_alert=True)
        return

    if action == "begin":
        await enter_room(ch)
        await cb.answer()
        return
    if action == "noop":
        await cb.answer("Недоступно", show_alert=True)
    elif action == "look":
        await show_room(cb.message, ch, edit_cb=cb)
    elif action == "mobs":
        await safe_edit(cb, ui.render_room(ch, world, others_in(ch.room)), ui.kb_mobs_all(ch, world))
    elif action == "npcs":
        await safe_edit(cb, ui.render_room(ch, world, others_in(ch.room)), ui.kb_npcs_all(ch, world))
    elif action == "more":
        await safe_edit(cb, "☰ *Ещё*", ui.kb_more(ch))
    elif action == "stats":
        await safe_edit(cb, ui.render_stats(ch), ui.kb_player(ch))
    elif action == "inv":
        await safe_edit(cb, ui.render_inventory(ch), ui.kb_inventory(ch))
    elif action == "rest":
        if not WORLD[ch.room].get("rest"):
            await cb.answer("Здесь не отдохнуть.", show_alert=True)
        else:
            ch.hp = ch.max_hp
            ch.mp = ch.start_resource()
            cap = ch.level * 200
            bonus = min(cap, int(ch.flags.get("rested", 0)) + ch.level * 50)
            ch.flags["rested"] = bonus
            await save(ch)
            await cb.answer("💤 Вы отдохнули: здоровье и силы восстановлены, "
                            "накоплен отдохнувший опыт.", show_alert=True)
            await safe_edit(cb, ui.render_room(ch, world, others_in(ch.room)),
                            ui.kb_room(ch, world))
    elif action == "stash":
        if not WORLD[ch.room].get("personal"):
            await cb.answer("Личный сундук есть только в вашей комнате.", show_alert=True)
        else:
            await safe_edit(cb, ui.render_stash(ch), ui.kb_stash(ch))
    elif action == "stashput":
        if WORLD[ch.room].get("personal") and arg in ch.inventory:
            ch.inventory.remove(arg)
            ch.flags.setdefault("stash", []).append(arg)
            await save(ch)
        await safe_edit(cb, ui.render_stash(ch), ui.kb_stash(ch))
    elif action == "stashget":
        _st = ch.flags.get("stash") or []
        if arg in _st:
            _st.remove(arg)
            ch.flags["stash"] = _st
            ch.inventory.append(arg)
            await save(ch)
        await safe_edit(cb, ui.render_stash(ch), ui.kb_stash(ch))
    elif action == "go":
        await do_move(cb.message, ch, arg, cb=cb)
    elif action == "dungeon":
        await do_dungeon(cb, ch, arg)
    elif action == "gather":
        await do_gather(cb, ch, arg)
    elif action == "profs":
        if not _uigate.unlocked("professions", ch.level):
            await cb.answer(_uigate.hint("professions"), show_alert=True); return
        from engine import professions
        await safe_edit(cb, professions.render(ch), ui.kb_player(ch))
    elif action == "remort":
        if ch.remort():
            await save(ch, force=True)      # реморт (сброс уровня) — фиксируем сразу
            await cb.answer("🌟 Перерождение!", show_alert=True)
            _cap_note = ("\nСила достигла предела смертных — дальше только слава."
                         if ch.remort_bonus_maxed else "")
            await safe_edit(cb,
                f"🌟 *Перерождение №{ch.remort_count}!* Вы вновь 1 уровня, но навсегда сильнее "
                f"(+{int(ch.remort_bonus*100)}% к силе и HP). Снаряжение, монеты и таланты сохранены."
                f"{_cap_note}",
                ui.kb_room(ch, world))
        else:
            from engine.character import LEVEL_CAP
            await cb.answer(f"Реморт доступен только на {LEVEL_CAP} уровне", show_alert=True)
    elif action == "pets":
        from engine import pets as _pets
        await safe_edit(cb, _pets.render(ch), ui.kb_pets(ch))
    elif action in ("petbuy", "petset", "mountbuy", "mountset"):
        await do_pets(cb, ch, action, arg)
    elif action == "auc":
        if not _uigate.unlocked("auction", ch.level):
            await cb.answer(_uigate.hint("auction"), show_alert=True); return
        await show_auction(cb, ch)
    elif action == "aucsell":
        await show_auction_sell(cb, ch)
    elif action == "auclist":
        await do_auc_list(cb, ch, arg)
    elif action == "aucbuy":
        await do_auc_buy(cb, ch, arg)
    elif action == "auccancel":
        await do_auc_cancel(cb, ch, arg)
    elif action == "atk":
        await do_attack(cb, ch, arg)
    elif action == "skill":
        await do_skill(cb, ch, arg)
    elif action == "use":
        await do_use(cb, ch, arg)
    elif action == "equip":
        await do_equip(cb, ch, arg)
    elif action == "flee":
        await do_flee(cb, ch)
    elif action == "take":
        if take_ground_item(ch, ch.room, arg):
            await save(ch)
        await show_room(cb.message, ch, edit_cb=cb)
    elif action == "takeall":
        _taken_all = [it for it in list(ground_items_for(ch, ch.room))
                     if take_ground_item(ch, ch.room, it)]
        if _taken_all:
            await save(ch)
            await cb.answer("Подобрано: " + ", ".join(ITEMS[i]["name"] for i in _taken_all)[:180])
        else:
            await cb.answer()
        await show_room(cb.message, ch, edit_cb=cb)
    elif action == "talk":
        # Мгновенно погасить спиннер кнопки: LLM-реплика может думать до 20с,
        # а протухший callback Telegram затем молча «съедает» ответ — игрок
        # видел «ничего не происходит» (баг живого прогона на VPS).
        try:
            await cb.answer()
        except Exception:
            pass
        talking_to[uid] = arg
        try:
            ai_line, act = await npc_ai.say_action(ch, arg)
        except Exception as e:
            # ИИ-реплика никогда не должна ронять диалог: откат на шаблон
            _elog.log_err(_log, "npc_talk_failed", e, uid=uid, npc=arg)
            ai_line, act = None, None
        _stash_errand(ch, arg, act)
        _qtalk = quest.on_talk(ch, arg)           # talk-цели квестов
        # недельная цель event_talk: разговор с NPC во время мирового события (Этап 6.1)
        if _events.ENABLED and _events.active():
            _wtalk = weekly.on_event_talk(ch)
            if _wtalk:
                _qtalk = (_qtalk or []) + [_wtalk]
        if _qtalk:
            await save(ch)
        await safe_edit(cb, npc_dialog(ch, arg, line=ai_line), ui.kb_npc(ch, arg, highlight=act))
        if _qtalk:
            # callback уже отвечен выше — квест-прогресс шлём обычным сообщением
            await send(uid, "\n".join(_qtalk))
    elif action == "erroffer":
        off = errands.offer(ch, arg)            # fallback без ИИ: случайный кандидат
        if not off:
            await cb.answer("Сейчас поручений нет.", show_alert=True)
        else:
            ch.flags["errand_pending"] = off
            _txt = (f"{npclib.emoji(arg)} _«{off['text']}»_\n\n"
                    + errands._goal_line({"type": off["type"],
                                          "mob": off.get("mob"), "item": off.get("item"),
                                          "progress": 0, "count": off["count"]})
                    + f"\n🎁 Награда: {off['reward'].get('xp', 0)} опыта, "
                    f"💰{money.fmt(off['reward'].get('gold', 0))}")
            await safe_edit(cb, _txt, ui.kb_errand_offer(ch, arg))
    elif action == "erraccept":
        _msg = errands.accept(ch, ch.flags.get("errand_pending"))
        await save(ch)
        await safe_edit(cb, _msg, ui.kb_npc(ch, arg))
    elif action == "errturnin":
        _msg = errands.turn_in(ch, arg)
        if _msg is None:
            await cb.answer("Условия поручения ещё не выполнены.", show_alert=True)
        else:
            _lv = []
            await gl._check_levelup(ch, _lv)
            if _lv:
                _msg += "\n" + "\n".join(_lv)
            for _aline in achievements.check(ch):
                _msg += "\n" + _aline
            await save(ch, force=True)      # награда за поручение — фиксируем сразу
            await safe_edit(cb, _msg, ui.kb_npc(ch, arg))
    elif action == "errabandon":
        _msg = errands.abandon(ch)
        await save(ch)
        await cb.answer(_msg.replace("*", "")[:190])
        _jt = quest.journal(ch)
        _er = errands.render(ch)
        if _er:
            _jt += "\n\n" + _er
        _jt += "\n" + _seven_crowns_block(ch)
        await safe_edit(cb, _jt, ui.kb_journal(ch))
    elif action == "qaccept":
        ok, msg = quest.accept(ch, arg)
        if ok:
            analytics.track_once(ch, "first_quest_accept", {"quest": arg})   # Этап 7.1
        await save(ch)
        npc = QUESTS[arg]["giver"]
        await safe_edit(cb, msg, ui.kb_npc(ch, npc))
    elif action == "choice":
        # первый клик по опции choose-квеста → шаг подтверждения (двухшаговость)
        _cq, _, _co = arg.partition(":")
        _opt = quest.choose_option(_cq, _co)
        if not _opt or quest.choice_made(ch, _cq) is not None:
            await cb.answer("Этот выбор недоступен.", show_alert=True); return
        _cnpc = QUESTS[_cq]["giver"]
        _ctxt = (f"🔀 *{_opt.get('label', _co)}*\n_{(_opt.get('text') or '').strip()}_\n\n"
                 "⚠️ Уверен? Это изменит твой путь.")
        _ckb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить выбор", callback_data=f"choicec:{_cq}:{_co}")],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"talk:{_cnpc}")]])
        await safe_edit(cb, _ctxt, _ckb)
    elif action == "choicec":
        # подтверждение выбора → фиксация в движке
        _cq, _, _co = arg.partition(":")
        ok, msg = quest.on_choose(ch, _cq, _co)
        await save(ch)
        _cnpc = QUESTS[_cq]["turn_in"]
        if not ok:
            await cb.answer(msg.replace("*", "")[:190], show_alert=True)
        await safe_edit(cb, msg, ui.kb_npc(ch, _cnpc))
    elif action == "achv":
        await safe_edit(cb, achievements.render(ch), ui.kb_titles(ch))
    elif action == "season":
        if not _uigate.unlocked("season", ch.level):
            await cb.answer(_uigate.hint("season"), show_alert=True); return
        from engine import seasons
        if not seasons.ENABLED:
            await safe_edit(cb, "🏅 Сезоны сейчас выключены администратором.", ui.kb_back("stats"))
        else:
            rew = seasons.ensure(ch)
            if rew:
                await cb.message.answer(
                    f"🏁 Сезон завершён! Ваша лига: {rew['emoji']} {rew['tier']}. "
                    f"Награда: 💰{money.fmt(rew['gold'])}. Новый сезон начался!")
                await save(ch, force=True)   # награда за завершённый сезон — фиксируем сразу
            txt = (seasons.render(ch) + "\n\n" + seasons.leaderboard(list(chars.values()), ch)
                   + "\n\n" + seasons.track_render(ch))
            await safe_edit(cb, txt, ui.kb_season(ch))
    elif action == "strack":
        from engine import seasons
        res = seasons.track_claim(ch, int(arg))
        await save(ch, force=True)      # claim награды сезонного трека — фиксируем сразу
        await cb.answer(res.replace("*", "")[:190])
        txt = (seasons.render(ch) + "\n\n" + seasons.leaderboard(list(chars.values()), ch)
               + "\n\n" + seasons.track_render(ch))
        await safe_edit(cb, txt, ui.kb_season(ch))
    elif action == "events":
        from engine import events
        await safe_edit(cb, events.render(), ui.kb_back("look"))
    elif action == "settitle":
        achievements.set_title(ch, arg or None)
        await save(ch)
        await cb.answer("Титул обновлён" if arg else "Титул скрыт")
        await safe_edit(cb, achievements.render(ch), ui.kb_titles(ch))
    elif action == "rep":
        await safe_edit(cb, reputation.render(ch), ui.kb_back("stats"))
    elif action == "arenaboard":
        if not _uigate.unlocked("arena", ch.level):
            await cb.answer(_uigate.hint("arena"), show_alert=True); return
        await safe_edit(cb, arena.render_leaderboard(list(chars.values()), ch), ui.kb_back("look"))
    elif action == "bestiary":
        await safe_edit(cb, bestiary.render(ch), ui.kb_back("stats"))
    elif action == "chronicle":
        await safe_edit(cb, _chronicle.render(), ui.kb_back("more"))
    elif action == "talents":
        if not _uigate.unlocked("talents", ch.level):
            await cb.answer(_uigate.hint("talents"), show_alert=True); return
        talents.migrate_v2(ch)
        await safe_edit(cb, talents.render(ch), ui.kb_talents(ch))
    elif action == "talinv":
        ok, msg = talents.invest(ch, arg)
        await save(ch)
        await cb.answer(msg.replace("*", ""), show_alert=not ok)
        await safe_edit(cb, talents.render(ch), ui.kb_talents(ch))
    elif action == "talreset":
        n = talents.reset(ch)
        await save(ch)
        await cb.answer(f"♻️ Сброшено, возвращено {n} очк.")
        await safe_edit(cb, talents.render(ch), ui.kb_talents(ch))
    elif action == "daily":
        if not _uigate.unlocked("daily", ch.level):
            await cb.answer(_uigate.hint("daily"), show_alert=True); return
        rows = []
        if daily.is_complete(ch) and not (ch.flags.get("daily") or {}).get("claimed"):
            rows.append([InlineKeyboardButton(text="🎁 Получить награду", callback_data="dailyclaim")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
        _screen = daily.render(ch) + "\n\n" + weekly.render(ch) + "\n\n" + streak.render(ch)
        await safe_edit(cb, _screen, InlineKeyboardMarkup(inline_keyboard=rows))
    elif action == "dailyclaim":
        res = daily.claim(ch)
        await save(ch, force=True)      # claim награды ежедневки — фиксируем сразу
        await cb.answer(res.replace("*", "")[:190])
        _screen = daily.render(ch) + "\n\n" + weekly.render(ch) + "\n\n" + streak.render(ch)
        await safe_edit(cb, _screen + "\n\n" + res,
                        InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="⬅️ Назад", callback_data="look")]]))
    elif action == "qdone":
        ok, msg = quest.complete(ch, arg)
        await save(ch)
        npc = QUESTS[arg]["turn_in"]
        # левелап от награды
        lv = []
        await gl._check_levelup(ch, lv)
        if lv:
            msg += "\n" + "\n".join(lv)
        for aline in achievements.check(ch):
            msg += "\n" + aline
        if ok:
            _fac = (npclib.get(QUESTS[arg].get("giver")) or {}).get("faction")
            if _fac:
                _amt = 100 + QUESTS[arg].get("reward", {}).get("xp", 0)
                _wrep = reputation.gain(ch, _fac, _amt)   # -> строка недельника faction_rep или None
                _fn = content.FACTIONS.get(_fac, {}).get("name", _fac)
                msg += f"\n🤝 Репутация с «{_fn}»: +{_amt}"
                if _wrep:
                    msg += "\n" + _wrep
            analytics.track_once(ch, "first_quest_complete", {"quest": arg})   # Этап 7.1
            # первая сдача квеста — финальный шаг обучения (строки в этот же ответ)
            for _tl in tutorial.on_event(ch, "quest"):
                msg += "\n" + _tl
        await save(ch, force=True)      # сдача квеста (награда xp/gold/предмет) — фиксируем сразу
        await safe_edit(cb, msg, ui.kb_npc(ch, npc))
    elif action == "holy_water":
        if "святая_вода" not in ch.inventory:
            ch.inventory.append("святая_вода")
            await save(ch)
        await safe_edit(cb, "💧 Жрец наполняет флягу святой водой.\n"
                        "«Ступай, очисти осквернённый алтарь.»", ui.kb_npc(ch, "жрец_храма"))
    elif action == "journal":
        _jt = quest.journal(ch)
        _er = errands.render(ch)
        if _er:
            _jt += "\n\n" + _er
        _jt += "\n" + _seven_crowns_block(ch)
        await safe_edit(cb, _jt, ui.kb_journal(ch))
    elif action == "skills":
        await safe_edit(cb, ui.render_skills(ch), ui.kb_skills(ch))
    elif action == "psave":
        skillmod.save_preset(ch, arg)
        await save(ch)
        await cb.answer(f"💾 Набор сохранён в слот {arg}")
        await safe_edit(cb, ui.render_skills(ch), ui.kb_skills(ch))
    elif action == "pload":
        if skillmod.load_preset(ch, arg):
            await save(ch)
            await cb.answer(f"📥 Набор {arg} загружен")
        else:
            await cb.answer("Слот пуст", show_alert=True)
        await safe_edit(cb, ui.render_skills(ch), ui.kb_skills(ch))
    elif action == "slot":
        ok, msg = skillmod.toggle_loadout(ch, arg)
        await save(ch)
        await cb.answer(msg.replace("*", ""))
        await safe_edit(cb, ui.render_skills(ch), ui.kb_skills(ch))
    elif action == "map":
        await send_map(cb, ch)
    elif action == "mapgo":
        moved, engaged = await move_core(ch, arg)
        if moved and engaged:
            await enter_room(ch, cb=cb)      # бой — закрыть карту, выйти в главное меню
        elif moved:
            await show_map_photo(ch, cb)
        await cb.answer()
    elif action == "home":
        try:
            await cb.message.delete()
        except Exception:
            pass
        await bot.send_message(ch.uid, ui.render_room(ch, world, others_in(ch.room)),
                               parse_mode="Markdown", reply_markup=ui.kb_room(ch, world))
        await cb.answer()
    elif action == "help":
        await safe_edit(cb, _help_text(ch), kb_help())
    elif action == "help_cmds":
        await safe_edit(cb, cmds.render_command_list(), ui.kb_back("help"))
    elif action == "invite":
        await safe_edit(cb, referral.render(ch, BOT_USERNAME), ui.kb_back("help"))
    elif action == "group":
        if not _uigate.unlocked("party", ch.level):
            await cb.answer(_uigate.hint("party"), show_alert=True); return
        await show_group(cb, ch)
    elif action == "guild":
        if not _uigate.unlocked("guild", ch.level):
            await cb.answer(_uigate.hint("guild"), show_alert=True); return
        await show_guild(cb, ch)
    elif action == "gcreate":
        await guild_create_prompt(cb, ch)
    elif action == "ginvmenu":
        await guild_invite_menu(cb, ch)
    elif action == "ginv":
        await do_guild_invite(cb, ch, int(arg))
    elif action == "gaccept":
        await guild_accept(cb, ch)
    elif action == "gdecline":
        await guild_decline(cb, ch)
    elif action == "gleave":
        await guild_leave(cb, ch)
    elif action == "gdepmenu":
        await guild_dep_menu(cb, ch)
    elif action == "gwdmenu":
        await guild_wd_menu(cb, ch)
    elif action == "gdep":
        await guild_deposit(cb, ch, int(arg))
    elif action == "gwd":
        await guild_withdraw(cb, ch, int(arg))
    elif action == "gbank":
        await guild_bank_items(cb, ch)
    elif action == "gdepitem":
        await guild_dep_item_menu(cb, ch)
    elif action == "gdi":
        await guild_deposit_item(cb, ch, arg)
    elif action == "gwi":
        await guild_withdraw_item(cb, ch, arg)
    elif action == "gmanage":
        await guild_manage(cb, ch)
    elif action == "gpromote":
        _gtarget = int(arg)
        if guild_mgr.promote(ch.uid, _gtarget):
            _nr_key = guild_mgr.rank(_gtarget)
            _nr = guildlib.RANKS.get(_nr_key, "")
            if db and db.pool:
                # Персистентность состава (Этап 3.3): зеркалим новый ранг.
                _gid = guild_mgr.gid_of(_gtarget)
                try:
                    await guild_tx.set_rank(db.pool.acquire, _gtarget, _gid, _nr_key)
                except Exception as e:
                    _elog.log_err(_log, "guild_roster_sync_failed", e,
                                 uid=_gtarget, gid=_gid, op="promote")
            await send(_gtarget, f"🏰 Ваш ранг в гильдии повышен: {_nr}.")
            await cb.answer("Повышен")
        else:
            await cb.answer("Нельзя повысить", show_alert=True)
        await guild_manage(cb, ch)
    elif action == "gdemote":
        _gtarget = int(arg)
        if guild_mgr.demote(ch.uid, _gtarget):
            if db and db.pool:
                # Персистентность состава (Этап 3.3): зеркалим новый ранг.
                _gid = guild_mgr.gid_of(_gtarget)
                _nr_key = guild_mgr.rank(_gtarget)
                try:
                    await guild_tx.set_rank(db.pool.acquire, _gtarget, _gid, _nr_key)
                except Exception as e:
                    _elog.log_err(_log, "guild_roster_sync_failed", e,
                                 uid=_gtarget, gid=_gid, op="demote")
            await cb.answer("Понижен")
        else:
            await cb.answer("Нельзя понизить", show_alert=True)
        await guild_manage(cb, ch)
    elif action == "gkick":
        _gtarget = int(arg)
        _gkick_gid = guild_mgr.gid_of(_gtarget)   # захватить ДО kick — kick чистит member_of
        if guild_mgr.kick(ch.uid, _gtarget):
            if _gkick_gid and db and db.pool:
                # Персистентность состава (Этап 3.3): зеркалим исключение.
                try:
                    await guild_tx.remove_member(db.pool.acquire, _gtarget, _gkick_gid)
                except Exception as e:
                    _elog.log_err(_log, "guild_roster_sync_failed", e,
                                 uid=_gtarget, gid=_gkick_gid, op="kick")
            await send(_gtarget, "🏰 Вас исключили из гильдии.")
            await cb.answer("Исключён")
        else:
            await cb.answer("Нельзя", show_alert=True)
        await guild_manage(cb, ch)
    elif action == "pinvite":
        await party_invite(cb, ch, int(arg))
    elif action == "paccept":
        await party_accept(cb, ch)
    elif action == "pdecline":
        await party_decline(cb, ch)
    elif action == "pleave":
        await party_leave(cb, ch)
    elif action == "duel":
        await duel_challenge(cb, ch, int(arg))
    elif action == "pvpatk":
        await pvp_attack(cb, ch, int(arg))
    elif action == "daccept":
        await duel_accept(cb, ch)
    elif action == "ddecline":
        await duel_decline(cb, ch)
    elif action == "datk":
        await duel_attack(cb, ch)
    elif action == "dpot":
        await duel_potion(cb, ch)
    elif action == "dyield":
        await duel_yield(cb, ch)
    elif action == "loot":
        items = world.loot_corpse(ch.room, arg)
        if items:
            ch.inventory.extend(items)
            await save(ch)
            lines = "\n".join(f"   {ITEMS[i].get('emoji','•')} {ITEMS[i]['name']}" for i in items)
            note = f"🎒 *Вы обыскали тело и забрали:*\n{lines}"
            await cb.answer("Забрано: " + ", ".join(ITEMS[i]["name"] for i in items)[:180])
        else:
            note = "💨 Тело пустое или уже истлело."
            await cb.answer()
        await safe_edit(cb, note + "\n\n" + ui.render_room(ch, world, others_in(ch.room)),
                        ui.kb_room(ch, world))
    elif action == "loots":
        _all_items = []
        for _c in list(world.corpses_in(ch.room)):
            if _c.get("loot"):
                _all_items.extend(world.loot_corpse(ch.room, _c["key"]))
        if _all_items:
            ch.inventory.extend(_all_items)
            await save(ch)
            _lines = "\n".join(f"   {ITEMS[i].get('emoji','•')} {ITEMS[i]['name']}" for i in _all_items)
            note = f"🎒 *Вы обыскали все тела и забрали:*\n{_lines}"
            await cb.answer("Забрано: " + ", ".join(ITEMS[i]["name"] for i in _all_items)[:180])
        else:
            note = "💨 Тела пусты или уже истлели."
            await cb.answer()
        await safe_edit(cb, note + "\n\n" + ui.render_room(ch, world, others_in(ch.room)),
                        ui.kb_room(ch, world))
    elif action == "settings":
        await safe_edit(cb, ui.render_settings(ch), ui.kb_settings(ch))
    elif action == "notify":
        await safe_edit(cb, ui.render_notify(ch), ui.kb_notify(ch))
    elif action == "ntog":
        if arg in _notify.CATEGORIES:
            _notify.toggle_pref(ch, arg)
            await save(ch)
        await safe_edit(cb, ui.render_notify(ch), ui.kb_notify(ch))
    elif action == "nlim":
        new_lim = _notify.cycle_limit(ch)
        await save(ch)
        await cb.answer(f"🔔 Лимит push: {new_lim}/день")
        await safe_edit(cb, ui.render_notify(ch), ui.kb_notify(ch))
    elif action == "nquiet":
        new_off = _notify.toggle_quiet_off(ch)
        await save(ch)
        await cb.answer("🌙 Тихие часы выключены" if new_off else "🌙 Тихие часы включены")
        await safe_edit(cb, ui.render_notify(ch), ui.kb_notify(ch))
    elif action == "ntz":
        await safe_edit(cb, ui.render_tz(ch), ui.kb_tz(ch))
    elif action == "ntzset":
        try:
            _off = _notify.set_tz_offset(ch, int(arg))
        except (TypeError, ValueError):
            _off = _notify.tz_offset(ch)
        await save(ch)
        await cb.answer(f"🕐 Часовой пояс: UTC{_off:+d}")
        await safe_edit(cb, ui.render_tz(ch), ui.kb_tz(ch))
    elif action == "learnprof":
        from engine import professions as _pf
        pm = _pf.PROFS.get(arg, {})
        _npc_t = talking_to.get(uid)
        if _pf.is_learned(ch, arg):
            await cb.answer("Уже освоено")
        elif _pf.learn(ch, arg):
            await save(ch)
            await cb.message.answer(
                f"📚 Вы освоили профессию *{pm.get('name', arg)}*!\n_{pm.get('desc','')}_",
                parse_mode="Markdown")
        if _npc_t:
            await safe_edit(cb, npc_dialog(ch, _npc_t), ui.kb_npc(ch, _npc_t))
    elif action == "pkclear":
        from engine import karma
        if not karma.pvp_marked(ch):
            await cb.answer("На вас нет PvP-метки", show_alert=True)
        elif ch.gold < karma.CLEAR_COST:
            await cb.answer(f"Нужно 💰{money.fmt(karma.CLEAR_COST)}", show_alert=True)
        else:
            ch.gold -= karma.CLEAR_COST
            karma.clear_mark(ch)
            await save(ch)
            await cb.message.answer("🕊 Жрец снимает с вас PvP-метку. Совесть чиста.")
            await safe_edit(cb, npc_dialog(ch, "жрец_храма"), ui.kb_npc(ch, "жрец_храма"))
    elif action == "set":
        if arg == "autoloot":
            ch.flags["autoloot"] = not ch.flags.get("autoloot", False)
            await save(ch)
        elif arg == "roompics":
            ch.flags["roompics"] = not ch.flags.get("roompics", True)
            await save(ch)
        await safe_edit(cb, ui.render_settings(ch), ui.kb_settings(ch))
    elif action == "train":
        tr = _trainer_here(ch)
        if tr:
            await safe_edit(cb, ui.render_trainer(ch), ui.kb_trainer(ch))
        else:
            await cb.answer("Учитель есть только в городах", show_alert=True)
    elif action == "learn":
        if not _trainer_here(ch):
            await cb.answer("Учиться можно только у учителя", show_alert=True)
        else:
            ok, msg = skillmod.learn(ch, arg)
            await save(ch)
            await cb.answer(msg.replace("*", ""), show_alert=not ok)
            await safe_edit(cb, ui.render_trainer(ch), ui.kb_trainer(ch))
    elif action == "shop":
        if arg and arg in ui.vendors_here(ch):
            ui.active_vendor[ch.uid] = arg
        v = ui.current_vendor(ch)
        if v:
            analytics.track(uid, "shop_view", {"vendor": v})   # Этап 7.1
            await safe_edit(cb, f"🏪 *{npclib.display_name(v)} — товары:*", ui.kb_shop(ch))
        else:
            await cb.answer("Торговец есть в городах", show_alert=True)
    elif action == "shopmenu":
        rows = [[InlineKeyboardButton(text=f"🛒 {npclib.display_name(n)}", callback_data=f"shop:{n}")]
                for n in ui.vendors_here(ch)]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
        await safe_edit(cb, "🏪 *Выберите лавку:*", _kb(rows))
    elif action == "titleshop":
        # Лавка престижных титулов доступна у любого торговца в городе.
        if _vendor_here(ch):
            await safe_edit(cb, ui.render_title_shop(ch), ui.kb_title_shop(ch))
        else:
            await cb.answer("Титулы продаются у торговца в городе", show_alert=True)
    elif action == "buytitle":
        from engine import titles as titlemod
        if not _vendor_here(ch):
            await cb.answer("Титулы продаются у торговца в городе", show_alert=True)
        else:
            ok, msg = titlemod.buy(ch, arg)
            if ok:
                await save(ch, force=True)    # покупка титула — фиксируем сразу
            await cb.answer(msg.replace("*", "")[:190], show_alert=True)
            await safe_edit(cb, ui.render_title_shop(ch), ui.kb_title_shop(ch))
    elif action == "buy":
        await do_buy(cb.message, ch, arg, cb=cb)
    elif action == "card":
        ctx, _, key = arg.partition(":")
        if key in ITEMS:
            await send_item_card(cb, ch, ctx, key)
        else:
            await cb.answer("Нет такого предмета", show_alert=True)
    elif action == "cbuy":
        await card_buy(cb, ch, arg)
    elif action == "cuse":
        await card_use(cb, ch, arg)
    elif action == "cequip":
        await card_equip(cb, ch, arg)
    elif action == "cunequip":
        await card_unequip(cb, ch, arg)
    elif action == "salv":
        await card_salvage(cb, ch, arg)
    elif action == "cardx":
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.answer()
    elif action == "sellmenu":
        if _vendor_here(ch):
            await safe_edit(cb, "💰 *Скупка добычи.* Торговец платит ~60% цены.\n"
                            "Надетое снаряжение не продаётся.", ui.kb_sell(ch))
        else:
            await cb.answer("Продажа только у торговца", show_alert=True)
    elif action == "sell":
        await do_sell(cb, ch, arg)
    elif action == "repairmenu":
        if "кузнец" not in WORLD[ch.room].get("npc", []):
            await cb.answer("Ремонт — у кузнеца", show_alert=True)
        else:
            cost = ch.repair_cost()
            if cost <= 0:
                await safe_edit(cb, "🔧 Снаряжение в полном порядке.", ui.kb_shop(ch))
            else:
                await safe_edit(cb, f"🔧 *Ремонт снаряжения*\nСтоимость: 💰{money.fmt(cost)}",
                    _kb([[InlineKeyboardButton(text=f"🔧 Починить (💰{money.fmt(cost)})", callback_data="repair")],
                         [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop")]]))
    elif action == "repair":
        cost = ch.repair_cost()
        if ch.gold < cost:
            await cb.answer("Не хватает монет", show_alert=True)
        else:
            ch.gold -= cost
            ch.repair_all()
            await save(ch)
            await cb.answer("🔧 Снаряжение починено!")
            await safe_edit(cb, "🔧 Снаряжение полностью починено.", ui.kb_shop(ch))
    elif action == "enchmenu":
        await show_ench(cb, ch)
    elif action == "ench":
        await do_ench(cb, ch, arg)
    elif action == "socketmenu":
        await show_sockets(cb, ch)
    elif action == "socketput":
        await do_socket_put(cb, ch, arg)
    elif action == "runeshop":
        await show_runeshop(cb, ch)
    elif action == "runebuy":
        await do_runebuy(cb, ch, arg)
    elif action == "craftmenu":
        if not _uigate.unlocked("craft", ch.level):
            await cb.answer(_uigate.hint("craft"), show_alert=True); return
        if "кузнец" in WORLD[ch.room].get("npc", []):
            await safe_edit(cb, ui.render_craft(ch), ui.kb_craft(ch))
        else:
            await cb.answer("Ковка только у кузнеца", show_alert=True)
    elif action == "craft":
        await do_craft(cb, ch, arg)
    elif action == "report":
        await _do_report(cb, ch, arg)
    await cb.answer()


# ═══════════════════════ Этап 7.2: жалобы и админка ═══════════════════════
async def _do_report(cb: CallbackQuery, ch: Character, arg: str):
    """Жалоба на игрока комнаты: запись в audit_log + пинг всем ADMIN_IDS."""
    try:
        target = int(arg)
    except (TypeError, ValueError):
        await cb.answer(); return
    tgt = chars.get(target)
    tname = tgt.name if tgt else str(target)
    if db and db.pool:
        await db.add_audit(ch.uid, "report",
                           {"target": target, "reporter": ch.uid, "room": ch.room})
    room_name = WORLD.get(ch.room, {}).get("name", ch.room)
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"⚠️ *Жалоба*\nОт: {_ts.esc_md(ch.name)} (uid `{ch.uid}`)\n"
                f"На: {_ts.esc_md(tname)} (uid `{target}`)\n"
                f"Комната: {_ts.esc_md(str(room_name))}\n"
                f"Разбор: /admin → 👤 Игрок → `{target}`",
                parse_mode="Markdown")
        except Exception:
            pass
    await cb.answer("⚠️ Жалоба отправлена модераторам. Спасибо.", show_alert=True)


# ── меню ──
def _admin_menu_text() -> str:
    return ("🛠 *Админка «Семь Корон»*\n\n"
            f"Торговля: {'🟢 включена' if TRADING_ENABLED else '🔴 выключена'}\n"
            f"Игроков в памяти: {len(chars)}\n\n"
            "_Все действия журналируются (audit_log)._")


def _admin_menu_kb() -> InlineKeyboardMarkup:
    trade_label = "🛑 Торговля: выкл" if TRADING_ENABLED else "✅ Торговля: вкл"
    return _kb([
        [InlineKeyboardButton(text="👤 Игрок", callback_data="adm:player")],
        [InlineKeyboardButton(text=trade_label, callback_data="adm:trade")],
        [InlineKeyboardButton(text="🌩 Событие", callback_data="adm:events")],
        [InlineKeyboardButton(text="💚 Health", callback_data="adm:health")],
        [InlineKeyboardButton(text="🤖 LLM", callback_data="adm:llm")],
    ])


def _admin_back_kb() -> InlineKeyboardMarkup:
    return _kb([[InlineKeyboardButton(text="⬅️ В админку", callback_data="adm:menu")]])


# ── карточка игрока ──
async def _admin_card_data(target: int):
    ch = chars.get(target)
    if ch is not None:
        return {"uid": target, "name": ch.name, "level": ch.level, "gold": ch.gold,
                "room": WORLD.get(ch.room, {}).get("name", ch.room), "online": True}
    if db and db.pool:
        row = await db.pool.fetchrow(
            "SELECT uid,name,level,gold,room FROM characters "
            "WHERE uid=$1 AND deleted_at IS NULL", target)
        if row:
            return {"uid": target, "name": row["name"], "level": row["level"],
                    "gold": row["gold"],
                    "room": WORLD.get(row["room"], {}).get("name", row["room"]),
                    "online": False}
    return None


def _admin_card_text(d: dict) -> str:
    import time as _t
    now = _t.time()
    ban = "🔴 ЗАБАНЕН" if _mod.is_banned(d["uid"]) else "—"
    if _mod.is_muted(d["uid"], now):
        mu = int((_mod.muted_until(d["uid"]) - now) // 60) + 1
        mute = f"🔇 ещё ~{mu} мин"
    else:
        mute = "—"
    return (f"👤 *{_ts.esc_md(d['name'])}*  (uid `{d['uid']}`)\n"
            f"{'🟢 онлайн' if d['online'] else '⚪️ оффлайн'}\n\n"
            f"Уровень: *{d['level']}*\n"
            f"Золото: *{d['gold']}*\n"
            f"Комната: {_ts.esc_md(str(d['room']))}\n"
            f"Бан: {ban}\n"
            f"Мут: {mute}")


def _admin_card_kb(d: dict) -> InlineKeyboardMarkup:
    u = d["uid"]
    rows = []
    if _mod.is_banned(u):
        rows.append([InlineKeyboardButton(text="✅ Разбанить", callback_data=f"adm:unban:{u}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Бан", callback_data=f"adm:ban:{u}")])
    rows.append([
        InlineKeyboardButton(text="🔇 Мут 1ч", callback_data=f"adm:mute:{u}:60"),
        InlineKeyboardButton(text="🔇 Мут 24ч", callback_data=f"adm:mute:{u}:1440"),
        InlineKeyboardButton(text="🔊 Анмут", callback_data=f"adm:unmute:{u}"),
    ])
    rows.append([InlineKeyboardButton(text="💰 Компенсация", callback_data=f"adm:comp:{u}")])
    rows.append([InlineKeyboardButton(text="📒 Леджер", callback_data=f"adm:ledger:{u}")])
    rows.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="adm:menu")])
    return _kb(rows)


async def _admin_show_card(cb: CallbackQuery, target: int):
    d = await _admin_card_data(target)
    if d is None:
        await cb.answer("Игрок не найден", show_alert=True); return
    await safe_edit(cb, _admin_card_text(d), _admin_card_kb(d))


async def _admin_send_card(message: Message, target: int):
    d = await _admin_card_data(target)
    if d is None:
        await message.answer("Игрок не найден. /admin — вернуться."); return
    await message.answer(_admin_card_text(d), parse_mode="Markdown",
                         reply_markup=_admin_card_kb(d))


async def _admin_show_ledger(cb: CallbackQuery, target: int):
    import datetime as _dt
    rows = await db.ledger_recent(target, 15) if (db and db.pool) else []
    L = [f"📒 *Леджер uid {target}* (последние 15)", ""]
    if not rows:
        L.append("_Записей нет (или БД недоступна)._")
    else:
        for r in rows:
            tt = (_dt.datetime.fromtimestamp(r["created"]).strftime("%m-%d %H:%M")
                  if r.get("created") else "?")
            item = f" [{_ts.esc_md(str(r['item']))}]" if r.get("item") else ""
            L.append(f"`{tt}` {_ts.esc_md(str(r['operation']))} "
                     f"{int(r['gold_delta']):+d}{item}")
    await safe_edit(cb, "\n".join(L), _kb([[InlineKeyboardButton(
        text="⬅️ К карточке", callback_data=f"adm:card:{target}")]]))


# ── компенсация ──
async def _admin_compensate(message: Message, admin_uid: int, target: int, amount: int):
    import time as _t
    op_id = f"comp:{target}:{int(_t.time())}"
    new_gold = None
    if db and db.pool:
        new_gold = await db.grant_gold(target, amount, op_id, "compensation", by_admin=admin_uid)
        await db.add_audit(target, "compensation",
                           {"amount": amount, "by": admin_uid, "op_id": op_id})
        if new_gold is not None and target in chars:
            chars[target].gold = int(new_gold)
            _char_dirty.discard(target)   # золото уже записано транзакцией grant_gold
    elif target in chars:
        chars[target].gold += int(amount)
        new_gold = chars[target].gold
    if new_gold is None:
        await message.answer("Не удалось начислить (нет персонажа или повторная операция).")
    else:
        await message.answer(
            f"💰 Начислено *{amount}* золота игроку `{target}`.\n"
            f"Новый баланс: *{new_gold}*. Операция записана в леджер и аудит.",
            parse_mode="Markdown")


# ── события ──
def _admin_events_kb() -> InlineKeyboardMarkup:
    rows = []
    for eid, d in _events._DEFS.items():
        rows.append([InlineKeyboardButton(
            text=f"🌩 {d.get('name', eid)}", callback_data=f"adm:evstart:{eid}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="Каталог событий пуст", callback_data="adm:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="adm:menu")])
    return _kb(rows)


async def _admin_start_event(cb: CallbackQuery, admin_uid: int, eid: str):
    msgs, reason = _events.start(eid, world=world)
    if msgs:
        announce = msgs[0]
        _chronicle.record("event", announce.replace("🌐 ", "").replace("*", ""))
        if _notify.ENABLED:
            await broadcast_all(f"🌩 {announce}", "world_event")
        else:
            for c in list(chars.values()):
                await send(c.uid, f"🌩 {announce}")
        if db and db.pool:
            await db.add_audit(admin_uid, "admin_event", {"event": eid, "by": admin_uid})
        await cb.answer("Событие запущено")
        await safe_edit(cb, _admin_menu_text(), _admin_menu_kb())
    else:
        await cb.answer(f"Не удалось: {reason}", show_alert=True)


# ── Health / LLM ──
def _admin_health_text(db_latency_ms=None) -> str:
    import time as _t
    now = _t.time()
    up = int(now - START_TS)
    online30 = sum(1 for ts in _analytics_seen.values() if now - ts <= 1800)
    errs = _elog.recent_errors(5)
    # Этап 9: живость воркеров из реестра watchdog (кто done — упал/не запущен)
    _alive = sum(1 for r in _WORKERS.values()
                 if r.get("task") is not None and not r["task"].done())
    # длительность игрового тика (loop замеряет сам)
    _tick = (f"{gl.tick_last_ms:.1f} / {gl.tick_avg_ms:.1f} мс"
             if gl is not None else "—")
    # задержка БД: None — БД нет, <0 — ошибка запроса
    if db_latency_ms is None:
        _dbl = "нет БД"
    elif db_latency_ms < 0:
        _dbl = "⛔ ошибка"
    else:
        _dbl = f"{db_latency_ms:.1f} мс"
    _t429 = _METRICS["tg_429"]
    _t429s = (f"*{_t429}*" if _t429 == 0 else
              f"*{_t429}* (посл. {int((now - _METRICS['tg_429_last']) / 60)} мин назад)")
    L = ["💚 *Health*", "",
         f"Аптайм: *{up // 3600}ч {(up % 3600) // 60}м*",
         f"Воркеры живы: *{_alive}/{len(_WORKERS)}*",
         f"Тик (посл/avg): *{_tick}*",
         f"Задержка БД (SELECT 1): *{_dbl}*",
         f"Telegram 429: {_t429s}",
         f"Игроков в памяти: *{len(chars)}*",
         f"Онлайн за 30 мин: *{online30}*",
         f"Очередь записи (dirty): *{len(_char_dirty)}*",
         f"Очередь уведомлений: *{_notify.pending()}*",
         f"Торговля: *{'вкл' if TRADING_ENABLED else 'ВЫКЛ'}*",
         ""]
    if errs:
        import datetime as _dt
        L.append("*Последние ошибки:*")
        for e in errs:
            tt = _dt.datetime.fromtimestamp(e["ts"]).strftime("%H:%M")
            L.append(f"• `{tt}` {_ts.esc_md(e['event'])}: {_ts.esc_md(e['err'])[:80]}")
    else:
        L.append("_Ошибок в буфере нет._")
    return "\n".join(L)


def _admin_llm_text() -> str:
    import time as _t
    now = _t.time()
    day = _cost.TokenBucket._day()
    pairs = [(k, c) for k, (d, c) in _cost.BUCKET._counts.items() if d == day]
    total = sum(c for _, c in pairs)
    lim = _cost.BUCKET.limit()
    prov_on = _provider.enabled()
    # рантайм-тумблер (kill-switch): None — по окружению, True/False — ручной override
    rt = _provider.runtime_state()
    rt_label = "по окружению" if rt is None else ("ВКЛ (ручной)" if rt else "ВЫКЛ (ручной)")
    # дневной HARD-бюджет
    spent = _cost.BUDGET_GUARD.spent_today(now)
    budget = _cost.daily_budget_usd()
    exhausted = _provider.budget_exhausted(now)
    since = int(now - _god._last_llm_call) if _god._last_llm_call else None
    L = ["🤖 *LLM — бюджет*", "",
         f"Провайдер: *{'включён' if prov_on else 'выключен (fallback)'}*",
         f"Тумблер (kill-switch): *{rt_label}*",
         f"Бюджет: *{spent:.4f} / {budget:.2f} USD*"
         + ("  ⛔ *исчерпан*" if exhausted else ""),
         f"Лимит на (игрок, NPC) / сутки: *{lim}*",
         f"Диалог-пар с запросами сегодня: *{len(pairs)}*",
         f"Всего запросов к модели сегодня: *{total}*",
         f"Мин. интервал бога: *{_god.GOD_MIN_INTERVAL // 60} мин*"]
    L.append(f"Последний вызов бога: *{since // 60} мин назад*" if since is not None
             else "Бог ещё не обращался к модели в этом процессе.")
    return "\n".join(L)


def _admin_llm_kb() -> InlineKeyboardMarkup:
    """Клавиатура LLM-экрана: аварийный тумблер ИИ + возврат. Метка кнопки
    отражает текущее рантайм-состояние (runtime_state)."""
    rt = _provider.runtime_state()
    # если сейчас выключено вручную — предлагаем включить, иначе — выключить
    if rt is False:
        label, act = "⏻ ИИ: включить", "on"
    else:
        label, act = "⏻ ИИ: выключить", "off"
    return _kb([
        [InlineKeyboardButton(text=label, callback_data=f"adm:llmtoggle:{act}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:menu")],
    ])


# ── диспетчер админ-колбэков ──
async def _admin_cb(cb: CallbackQuery, uid: int, arg: str):
    global TRADING_ENABLED
    if not is_admin(uid):
        await cb.answer("Недоступно", show_alert=True); return
    sub, _, rest = arg.partition(":")
    if sub == "menu":
        admin_await.pop(uid, None)
        await safe_edit(cb, _admin_menu_text(), _admin_menu_kb()); await cb.answer(); return
    if sub == "player":
        admin_await[uid] = {"kind": "player"}
        await safe_edit(cb, "👤 *Поиск игрока*\n\nПришлите uid или точное имя игрока "
                        "одним сообщением.", _admin_back_kb()); await cb.answer(); return
    if sub == "card":
        await _admin_show_card(cb, int(rest)); await cb.answer(); return
    if sub == "ban":
        await _mod.ban(int(rest), reason="admin", by=uid)
        await _admin_show_card(cb, int(rest)); await cb.answer("Забанен"); return
    if sub == "unban":
        await _mod.unban(int(rest), by=uid)
        await _admin_show_card(cb, int(rest)); await cb.answer("Разбанен"); return
    if sub == "mute":
        tgt, _, mins = rest.partition(":")
        await _mod.mute(int(tgt), minutes=int(mins or 60), reason="admin", by=uid)
        await _admin_show_card(cb, int(tgt)); await cb.answer(f"Мут {mins} мин"); return
    if sub == "unmute":
        await _mod.unmute(int(rest), by=uid)
        await _admin_show_card(cb, int(rest)); await cb.answer("Мут снят"); return
    if sub == "comp":
        admin_await[uid] = {"kind": "comp", "target": int(rest)}
        await safe_edit(cb, f"💰 *Компенсация игроку {rest}*\n\nПришлите сумму золота "
                        "(целое число) одним сообщением.", _admin_back_kb())
        await cb.answer(); return
    if sub == "ledger":
        await _admin_show_ledger(cb, int(rest)); await cb.answer(); return
    if sub == "trade":
        TRADING_ENABLED = not TRADING_ENABLED
        if db and db.pool:
            await db.add_audit(uid, "admin_trading", {"enabled": TRADING_ENABLED, "by": uid})
        await safe_edit(cb, _admin_menu_text(), _admin_menu_kb())
        await cb.answer("Торговля " + ("включена" if TRADING_ENABLED else "выключена"))
        return
    if sub == "events":
        await safe_edit(cb, "🌩 *Запуск мирового события*\n\nВыберите событие из каталога:",
                        _admin_events_kb()); await cb.answer(); return
    if sub == "evstart":
        await _admin_start_event(cb, uid, rest); return
    if sub == "health":
        _lat = await _measure_db_latency_ms()   # Этап 9: замер SELECT 1 при открытии
        await safe_edit(cb, _admin_health_text(_lat), _admin_back_kb()); await cb.answer(); return
    if sub == "llm":
        await safe_edit(cb, _admin_llm_text(), _admin_llm_kb()); await cb.answer(); return
    if sub == "llmtoggle":
        # аварийный тумблер ИИ (kill-switch): выключаем — provider.enabled()
        # мгновенно уходит в False (кэш сброшен внутри set_runtime), вся игра
        # переходит на шаблоны; включаем — возврат к решению по окружению/бюджету.
        turn_on = (rest == "on")
        _provider.set_runtime(None if turn_on else False)
        if db and db.pool:
            await db.add_audit(uid, "llm_toggle", {"on": turn_on, "by": uid})
        await safe_edit(cb, _admin_llm_text(), _admin_llm_kb())
        await cb.answer("ИИ включён" if turn_on else "ИИ выключен (fallback)"); return
    await cb.answer()


# ── ввод текста в админке ──
async def _admin_on_text(message: Message, uid: int, text: str):
    st = admin_await.get(uid) or {}
    kind = st.get("kind")
    admin_await.pop(uid, None)
    if kind == "player":
        t = text.strip()
        target = None
        if t.lstrip("-").isdigit():
            target = int(t)
        elif db and db.pool:
            row = await db.find_by_name(t)
            if row:
                target = int(row["uid"])
        if target is None:
            await message.answer("Игрок не найден. /admin — вернуться."); return
        await _admin_send_card(message, target); return
    if kind == "comp":
        target = st.get("target")
        try:
            amount = int(text.strip())
        except (TypeError, ValueError):
            await message.answer("Нужно целое число. /admin — вернуться."); return
        await _admin_compensate(message, uid, target, amount); return


def _quest_brief(qid: str) -> str:
    """Краткое описание квеста для показа ДО взятия: цель + награда.
    Поддерживает ВСЕ типы целей (kill/collect/talk/reach/use/choose — этап 5.1);
    неизвестный тип или битая ссылка не роняют диалог (баг живого прогона:
    collect-квест без 'item' валил весь разговор с NPC через KeyError)."""
    q = QUESTS[qid]
    obj = q.get("objective", {}) or {}
    typ = obj.get("type")
    cnt = obj.get("count", 1)
    if typ == "kill":
        nm = MOBS.get(obj.get("mob"), {}).get("name", obj.get("mob", "?"))
        goal = f"🎯 Убить: {nm} ×{cnt}"
    elif typ == "collect":
        nm = ITEMS.get(obj.get("item"), {}).get("name", obj.get("item", "?"))
        goal = f"🎯 Собрать: {nm} ×{cnt}"
    elif typ == "talk":
        goal = f"🎯 Поговорить: {npclib.display_name(obj.get('npc', '?'))}"
    elif typ == "reach":
        nm = WORLD.get(obj.get("room"), {}).get("name", obj.get("room", "?"))
        goal = f"🎯 Дойти до: {nm}"
    elif typ == "use":
        nm = ITEMS.get(obj.get("item"), {}).get("name", obj.get("item", "?"))
        goal = f"🎯 Использовать: {nm}"
    elif typ == "choose":
        goal = "🎯 Сделать выбор"
    else:
        goal = "🎯 Задание"
    rw = q.get("reward", {})
    rparts = []
    if rw.get("xp"):
        rparts.append(f"✨{rw['xp']}")
    if rw.get("gold"):
        rparts.append(f"💰{money.fmt(rw['gold'])}")
    for it in rw.get("items", []):
        rparts.append(ITEMS[it]["name"])
    reward = ", ".join(rparts) if rparts else "—"
    return f"📜 *{q['name']}*\n_{q['desc'].strip()}_\n{goal}\n🎁 Награда: {reward}"


def _guard_remark(ch: Character, npc: str) -> str:
    """Реплика стражи по репутации игрока с её фракцией (стража квестов не даёт)."""
    from engine import reputation as _rep
    fac = (npclib.get(npc) or {}).get("faction")
    p = _rep.points(ch, fac) if fac else 0
    if p >= 50:
        return "Доброго пути! Такие, как ты, — опора этих стен."
    if p >= 15:
        return "Рад знакомому лицу. Держись закона — и горя не будет."
    if p >= 0:
        return "Проходи. За порядком я слежу, так что без глупостей."
    if p > -30:
        return "Гляди в оба, чужак. С тебя глаз не спущу."
    return "Тебе тут не рады. Ещё шаг не туда — и окажешься в колодках."


def _stash_errand(ch: Character, npc_id: str, act):
    """Собрать конкретное предложение поручения по выбору LLM и сложить в flags.
    Кнопка «✨✉️ Взять поручение» затем примет этот черновик (erraccept)."""
    if not act or act.get("action") != "offer_errand":
        return
    off = errands.offer(ch, npc_id,
                        choice={"idx": act.get("idx"), "text": act.get("text")})
    if off:
        ch.flags["errand_pending"] = off


def npc_dialog(ch: Character, npc: str, line: str = None) -> str:
    """Реплика NPC (ИИ или шаблон) + превью квестов и активного поручения."""
    role = (npclib.get(npc) or {}).get("role")
    header = f"{npclib.emoji(npc)} *{npclib.display_name(npc)}* — {npclib.role_label(npc)}"
    if role == "guard" and not line:
        line = _guard_remark(ch, npc)
    # реакция NPC на выбор игрока (не-ИИ путь): reactions: {flag_key: {val: реплика}}
    if not line:
        _react = (npclib.get(npc) or {}).get("reactions") or {}
        for _fk, _mp in _react.items():
            _v = ch.flags.get(_fk)
            if _v is not None and str(_v) in _mp:
                line = _mp[str(_v)]
                break
    # реплика (ИИ или шаблон) вставляется в италик Markdown — экранируем спецсимволы
    parts = [header + "\n_«" + _ts.esc_md(line or npclib.line(npc)) + "»_"]
    for qid in quest.available_quests(ch, npc):
        parts.append(_quest_brief(qid))
    for qid in quest.turn_in_quests(ch, npc):
        parts.append(f"✅ *{QUESTS[qid]['name']}* — цель выполнена, можно сдать!")
    # активное поручение, выданное этим NPC — строка прогресса
    _e = ch.flags.get("errand")
    if _e and _e.get("npc") == npc:
        parts.append(errands.brief(_e))
    if len(parts) == 1 and role != "guard":
        parts.append("_Заданий для тебя сейчас нет._")
    return "\n\n".join(parts)


def _vendor_here(ch: Character):
    """Вернуть id NPC-торговца в комнате (role=vendor) или None."""
    for n in WORLD[ch.room].get("npc", []):
        if (npclib.get(n) or {}).get("role") == "vendor":
            return n
    return None


def _buy_price(ch: Character, key: str) -> int:
    """Цена покупки с учётом скидки по репутации с фракцией торговца."""
    base = ITEMS[key].get("price", 0)
    v = _vendor_here(ch)
    if v:
        fac = (npclib.get(v) or {}).get("faction")
        base = int(base * (1 - reputation.discount(ch, fac)))
    return max(1, base)


def _trainer_here(ch: Character):
    for n in WORLD[ch.room].get("npc", []):
        if (npclib.get(n) or {}).get("role") == "trainer":
            return n
    return None


def kb_map(ch: Character) -> InlineKeyboardMarkup:
    """Клавиатура карты: кнопки передвижения + вход в комнату."""
    rows = ui.move_grid(WORLD[ch.room]["exits"], prefix="mapgo")
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_map_photo(ch: Character, cb: CallbackQuery = None):
    """Показать карту окрестностей с кнопками движения (фото или текст-фолбэк)."""
    path = None
    try:
        path = mapgen.render_zone_map(ch)
    except Exception:
        path = None
    cur = WORLD[ch.room]
    caption = f"🗺 *{cur.get('zone','?')}* — {cur['name']}"
    kb = kb_map(ch)
    if path and os.path.exists(path):
        if cb:
            try:
                await cb.message.delete()
            except Exception:
                pass
        try:
            await bot.send_photo(ch.uid, FSInputFile(path), caption=caption,
                                 parse_mode="Markdown", reply_markup=kb)
            return
        except Exception:
            pass
    if cb:
        await safe_edit(cb, render_map(ch), kb)
    else:
        await bot.send_message(ch.uid, render_map(ch), parse_mode="Markdown", reply_markup=kb)


async def send_map(cb: CallbackQuery, ch: Character):
    await show_map_photo(ch, cb)


def render_map(ch: Character) -> str:
    cur = WORLD[ch.room]
    L = ["🗺 *Карта мира*", ""]
    zones = {}
    for rid, r in WORLD.items():
        z = r.get("zone", "?")
        zones[z] = zones.get(z, 0) + 1
    for z, cnt in zones.items():
        mark = "📍" if z == cur.get("zone") else "•"
        L.append(f"{mark} {z}")
    L.append("")
    L.append(f"📍 Вы здесь: *{cur['name']}*")
    L.append("🚪 Выходы: " + ", ".join(
        f"{d}→{WORLD[dest]['name']}" for d, dest in cur["exits"].items()))
    return "\n".join(L)


# ───────── ГРУППЫ И ДУЭЛИ ─────────
def players_in_room(ch: Character):
    """Другие онлайн-игроки в этой же комнате."""
    return [c for c in chars.values() if c.uid != ch.uid and c.room == ch.room and c.hp > 0]


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _has_heal_potion(ch: Character):
    for it in ch.inventory:
        meta = ITEMS.get(it, {})
        if meta.get("type") == "consumable" and "heal" in meta.get("effect", {}):
            return it
    return None


# ---- группа ----
def render_group(ch: Character) -> str:
    party = party_mgr.party_of(ch.uid)
    L = ["👥 *Группа*", ""]
    if party and len(party["members"]) > 1:
        leader = party["leader"]
        for uid in party["members"]:
            c = chars.get(uid)
            if not c:
                continue
            crown = "👑" if uid == leader else "•"
            where = "" if c.room == ch.room else f" — _{WORLD[c.room]['name']}_"
            L.append(f"{crown} *{c.name}* ур.{c.level} [{ui.bar(c.hp, c.max_hp, 6)}] "
                     f"{c.hp}/{c.max_hp}{where}")
        L.append("\n_Опыт и монеты делятся между группой в одной комнате._")
    else:
        L.append("_Вы пока не в группе._ Пригласите игрока из своей комнаты.")
    others = players_in_room(ch)
    L.append("\n*В этой комнате:* " + (", ".join(o.name for o in others) if others
                                       else "_никого нет_"))
    L.append("\n💬 Чат группы: напишите `п ваш текст`")
    return "\n".join(L)


async def show_group(cb: CallbackQuery, ch: Character):
    party = party_mgr.party_of(ch.uid)
    members = set(party["members"]) if party else set()
    rows = []
    for o in players_in_room(ch):
        line = []
        if o.uid not in members:
            line.append(InlineKeyboardButton(text=f"➕ В группу: {o.name}",
                                             callback_data=f"pinvite:{o.uid}"))
        line.append(InlineKeyboardButton(text=f"⚔️ Дуэль: {o.name}",
                                         callback_data=f"duel:{o.uid}"))
        if not WORLD.get(ch.room, {}).get("safe"):
            line.append(InlineKeyboardButton(text=f"🗡 Напасть: {o.name}",
                                             callback_data=f"pvpatk:{o.uid}"))
        rows.append(line)
        # Этап 7.2: жалоба на игрока комнаты — отдельной строкой (журнал + пинг админам)
        rows.append([InlineKeyboardButton(text=f"⚠️ Пожаловаться: {o.name}",
                                          callback_data=f"report:{o.uid}")])
    if party and len(party["members"]) > 1:
        rows.append([InlineKeyboardButton(text="🚪 Покинуть группу", callback_data="pleave")])
    if _uigate.unlocked("guild", ch.level):
        rows.append([InlineKeyboardButton(text="🏰 Гильдия", callback_data="guild")])
    elif _uigate.next_unlock_visible("guild", ch.level):
        rows.append([InlineKeyboardButton(
            text=f"🔒 Гильдия (с {_uigate.FEATURES['guild']} ур.)", callback_data="locked:guild")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    await safe_edit(cb, render_group(ch), _kb(rows))


async def party_invite(cb: CallbackQuery, ch: Character, target_uid: int):
    target = chars.get(target_uid)
    if not target or target.room != ch.room:
        await cb.answer("Игрок не в этой комнате", show_alert=True); return
    party_mgr.invite(ch.uid, target_uid)
    await bot.send_message(target_uid, f"👥 *{ch.name}* зовёт вас в группу.",
                           parse_mode="Markdown",
                           reply_markup=_kb([[
                               InlineKeyboardButton(text="✅ Принять", callback_data="paccept"),
                               InlineKeyboardButton(text="❌ Отклонить", callback_data="pdecline"),
                           ]]))
    await cb.answer("Приглашение отправлено")


async def party_accept(cb: CallbackQuery, ch: Character):
    party = party_mgr.accept(ch.uid)
    if not party:
        await cb.answer("Приглашение истекло", show_alert=True); return
    analytics.track(ch.uid, "party_join", {"size": len(party.get("members", []))})   # Этап 7.1
    names = ", ".join(chars[u].name for u in party["members"] if u in chars)
    for u in party["members"]:
        await send(u, f"👥 *{ch.name}* вступает в группу! Состав: {names}")
    await safe_edit(cb, render_group(ch), _kb([[InlineKeyboardButton(text="⬅️ Назад", callback_data="look")]]))


async def party_decline(cb: CallbackQuery, ch: Character):
    party_mgr.invites.pop(ch.uid, None)
    await safe_edit(cb, "❌ Приглашение отклонено.\n\n" +
                    ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))
    await cb.answer()


async def party_leave(cb: CallbackQuery, ch: Character):
    party = party_mgr.party_of(ch.uid)
    mates = [u for u in (party["members"] if party else []) if u != ch.uid]
    party_mgr.leave(ch.uid)
    for u in mates:
        await send(u, f"👥 *{ch.name}* покидает группу.")
    await cb.answer("Вы вышли из группы")
    await show_group(cb, ch)


# ---- дуэль ----
def render_duel(ch: Character) -> str:
    opp = chars.get(duel_mgr.opponent(ch.uid))
    turn = duel_mgr.whose_turn(ch.uid)
    L = ["⚔️ *ДУЭЛЬ*", ""]
    L.append(f"❤️ *{ch.name}* [{ui.bar(ch.hp, ch.max_hp)}] {ch.hp}/{ch.max_hp}  "
             f"{ch.resource_emoji}{ch.mp}")
    if opp:
        L.append(f"🛡 *{opp.name}* [{ui.bar(opp.hp, opp.max_hp)}] {opp.hp}/{opp.max_hp}")
    L.append("")
    L.append("🟢 *Ваш ход!*" if turn == ch.uid else
             f"⏳ Ход соперника ({opp.name if opp else '...'})...")
    return "\n".join(L)


def kb_duel(ch: Character, your_turn: bool) -> InlineKeyboardMarkup:
    rows = []
    if your_turn:
        row = [InlineKeyboardButton(text="⚔️ Атаковать", callback_data="datk")]
        if _has_heal_potion(ch):
            row.append(InlineKeyboardButton(text="🧪 Зелье", callback_data="dpot"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏳️ Сдаться", callback_data="dyield")])
    return _kb(rows)


async def refresh_duel(c: Character, header):
    dv = duel_view.get(c.uid)
    if not dv:
        return
    your = duel_mgr.whose_turn(c.uid) == c.uid
    text = ("\n".join(header) + "\n\n" if header else "") + render_duel(c)
    try:
        await bot.edit_message_text(text, chat_id=dv["chat"], message_id=dv["id"],
                                    parse_mode="Markdown", reply_markup=kb_duel(c, your))
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            pass
    except Exception:
        pass


async def start_duel(a_uid: int, b_uid: int):
    a, b = chars.get(a_uid), chars.get(b_uid)
    if not a or not b:
        return
    ranked = bool(WORLD.get(a.room, {}).get("arena") and a.room == b.room)
    for c in (a, b):
        c.hp = c.max_hp
        c.mp = c.start_resource()
        c.target = None
        if c.uid in duel_mgr.duels:
            duel_mgr.duels[c.uid]["ranked"] = ranked
    for c in (a, b):
        your = duel_mgr.whose_turn(c.uid) == c.uid
        try:
            m = await bot.send_message(c.uid, render_duel(c), parse_mode="Markdown",
                                       reply_markup=kb_duel(c, your))
            duel_view[c.uid] = {"chat": m.chat.id, "id": m.message_id}
        except Exception:
            pass


async def end_duel(winner: Character, loser: Character, lines):
    ranked = duel_mgr.duels.get(winner.uid, {}).get("ranked")
    is_open = duel_mgr.duels.get(winner.uid, {}).get("open")
    rank_line = {winner.uid: "", loser.uid: ""}
    if is_open:
        from engine import karma
        # ставки: проигравший роняет 10% монет победителю
        stake = int(loser.gold * 0.10)
        if stake > 0:
            loser.gold -= stake
            winner.gold += stake
            lines.append(f"💰 Трофей: {money.fmt(stake)} переходит к {winner.name}.")
        # карма убийце, если жертва была мирной
        for kl in karma.on_pvp_kill(winner, loser, safe_zone=WORLD.get(winner.room, {}).get("safe", False)):
            lines.append(kl)
        # выпавший предмет жертвы достаётся победителю
        drop = karma.maybe_drop_on_death(loser)
        if drop:
            winner.inventory.append(drop)
            lines.append(f"🎒 {winner.name} забирает выроненное: {ITEMS.get(drop,{}).get('name',drop)}.")
        await save(winner); await save(loser)
    if ranked:
        dw, dl = arena.update(winner, loser)
        rank_line[winner.uid] = f"\n🏟 Рейтинг арены: {arena.rating(winner)} ({dw:+d})"
        rank_line[loser.uid] = f"\n🏟 Рейтинг арены: {arena.rating(loser)} ({dl:+d})"
        await save(winner); await save(loser)
    duel_mgr.end(winner.uid)
    for c in (winner, loser):
        c.hp = c.max_hp
        c.mp = c.start_resource()
    for c, res in ((winner, "🏆 *Победа!*" + rank_line.get(winner.uid, "")),
                   (loser, "🏳️ *Поражение.*" + rank_line.get(loser.uid, ""))):
        dv = duel_view.pop(c.uid, None)
        body = ("\n".join(lines) + f"\n\n{res} Дуэль окончена, вы восстановлены.\n\n" +
                ui.render_room(c, world, others_in(c.room)))
        if dv:
            try:
                await bot.edit_message_text(body, chat_id=dv["chat"], message_id=dv["id"],
                                            parse_mode="Markdown", reply_markup=ui.kb_room(c, world))
                continue
            except Exception:
                pass
        try:
            await bot.send_message(c.uid, body, parse_mode="Markdown", reply_markup=ui.kb_room(c, world))
        except Exception:
            pass


async def duel_challenge(cb: CallbackQuery, ch: Character, target_uid: int):
    target = chars.get(target_uid)
    if not target or target.room != ch.room:
        await cb.answer("Игрок не в этой комнате", show_alert=True); return
    if world.living_in(ch.room) or ch.target:
        await cb.answer("Сначала закончите бой с монстрами", show_alert=True); return
    if duel_mgr.in_duel(ch.uid) or duel_mgr.in_duel(target_uid):
        await cb.answer("Кто-то уже на дуэли", show_alert=True); return
    duel_mgr.challenge(ch.uid, target_uid)
    await bot.send_message(target_uid, f"⚔️ *{_ts.esc_md(ch.name)}* вызывает вас на дуэль!",
                           parse_mode="Markdown",
                           reply_markup=_kb([[
                               InlineKeyboardButton(text="✅ Принять", callback_data="daccept"),
                               InlineKeyboardButton(text="❌ Отклонить", callback_data="ddecline"),
                           ]]))
    await cb.answer("Вызов отправлен")


async def pvp_attack(cb: CallbackQuery, ch: Character, target_uid: int):
    """Открытое нападение без согласия (только в опасных зонах)."""
    target = chars.get(target_uid)
    if not target or target.room != ch.room:
        await cb.answer("Игрок не в этой комнате", show_alert=True); return
    if WORLD.get(ch.room, {}).get("safe"):
        await cb.answer("В безопасной зоне нападать нельзя", show_alert=True); return
    if world.living_in(ch.room) or ch.target:
        await cb.answer("Сначала закончите бой с монстрами", show_alert=True); return
    if duel_mgr.in_duel(ch.uid) or duel_mgr.in_duel(target_uid):
        await cb.answer("Кто-то уже в бою", show_alert=True); return
    # форсированный бой через дуэльную инфраструктуру, помеченный как открытый
    duel_mgr.challenge(ch.uid, target_uid)
    duel_mgr.accept(target_uid)
    for u in (ch.uid, target_uid):
        if u in duel_mgr.duels:
            duel_mgr.duels[u]["open"] = True
    await cb.answer("🗡 Вы нападаете!")
    await bot.send_message(target_uid, f"🗡 *{ch.name} нападает на вас!* Защищайтесь!",
                           parse_mode="Markdown")
    await start_duel(ch.uid, target_uid)


async def duel_accept(cb: CallbackQuery, ch: Character):
    pair = duel_mgr.accept(ch.uid)
    if not pair:
        await cb.answer("Вызов истёк", show_alert=True); return
    challenger, target = pair
    try:
        await cb.message.delete()
    except Exception:
        pass
    await start_duel(challenger, target)
    await cb.answer()


async def duel_decline(cb: CallbackQuery, ch: Character):
    challenger = duel_mgr.decline(ch.uid)
    if challenger and challenger in chars:
        await send(challenger, f"🏳️ {ch.name} отклоняет дуэль.")
    await safe_edit(cb, "❌ Дуэль отклонена.\n\n" +
                    ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))
    await cb.answer()


async def duel_attack(cb: CallbackQuery, ch: Character):
    if not duel_mgr.in_duel(ch.uid):
        await cb.answer("Дуэль завершена", show_alert=True); return
    if duel_mgr.whose_turn(ch.uid) != ch.uid:
        await cb.answer("Сейчас не ваш ход"); return
    opp = chars.get(duel_mgr.opponent(ch.uid))
    if not opp:
        await end_duel(ch, ch, ["Соперник вышел."]); return
    ev = combat.player_vs_player(ch, opp)
    if opp.hp <= 1:
        await end_duel(ch, opp, ev)
        await cb.answer()
        return
    duel_mgr.pass_turn(ch.uid)
    await refresh_duel(ch, ev)
    await refresh_duel(opp, ev)
    await cb.answer()


async def duel_potion(cb: CallbackQuery, ch: Character):
    if duel_mgr.whose_turn(ch.uid) != ch.uid:
        await cb.answer("Сейчас не ваш ход"); return
    pot = _has_heal_potion(ch)
    if not pot:
        await cb.answer("Нет зелья", show_alert=True); return
    eff = ITEMS[pot].get("effect", {})
    before = ch.hp
    ch.hp = min(ch.max_hp, ch.hp + eff.get("heal", 0) * content.HP_SCALE)
    ch.inventory.remove(pot)
    line = [f"🧪 {ch.name} пьёт зелье (+{ch.hp - before} HP)."]
    opp = chars.get(duel_mgr.opponent(ch.uid))
    duel_mgr.pass_turn(ch.uid)
    await refresh_duel(ch, line)
    if opp:
        await refresh_duel(opp, line)
    await cb.answer()


async def duel_yield(cb: CallbackQuery, ch: Character):
    if not duel_mgr.in_duel(ch.uid):
        await cb.answer(); return
    opp = chars.get(duel_mgr.opponent(ch.uid))
    if opp:
        await end_duel(opp, ch, [f"🏳️ {ch.name} сдаётся."])
    else:
        duel_mgr.end(ch.uid)
        duel_view.pop(ch.uid, None)
        await safe_edit(cb, ui.render_room(ch, world, others_in(ch.room)), ui.kb_room(ch, world))
    await cb.answer()


# ───────── ГИЛЬДИИ ─────────
def render_guild(ch: Character) -> str:
    g = guild_mgr.guild_of(ch.uid)
    if not g:
        L = ["🏰 *Гильдии*", "", "Вы не состоите в гильдии."]
        if guild_mgr.invites.get(ch.uid) in guild_mgr.guilds:
            gn = guild_mgr.guilds[guild_mgr.invites[ch.uid]]["name"]
            L.append(f"\n📨 Вас приглашают в гильдию «{gn}».")
        L.append(f"\nОснование гильдии стоит 💰{money.fmt(guildlib.CREATE_COST)}.")
        return "\n".join(L)
    L = [f"🏰 *{g['name']}*", ""]
    order = {"leader": 0, "officer": 1, "member": 2}
    for uid in sorted(g["members"], key=lambda u: order.get(g["ranks"].get(str(u), "member"), 9)):
        c = chars.get(uid)
        nm = c.name if c else f"id{uid}"
        lvl = f" ур.{c.level}" if c else ""
        rk = guildlib.RANKS.get(g["ranks"].get(str(uid), "member"), "")
        L.append(f"{rk}: {nm}{lvl}")
    L.append("")
    L.append(f"🏦 Казна: 💰{money.fmt(g.get('bank_gold', 0))}")
    L.append(f"📦 Склад: {len(g.get('bank_items', []))} предметов")
    L.append("\n💬 Чат гильдии: напишите `г ваш текст`")
    return "\n".join(L)


def kb_guild(ch: Character) -> InlineKeyboardMarkup:
    g = guild_mgr.guild_of(ch.uid)
    rows = []
    if not g:
        if guild_mgr.invites.get(ch.uid) in guild_mgr.guilds:
            rows.append([InlineKeyboardButton(text="✅ Принять", callback_data="gaccept"),
                         InlineKeyboardButton(text="❌ Отклонить", callback_data="gdecline")])
        rows.append([InlineKeyboardButton(text=f"🏰 Основать гильдию (💰{money.fmt(guildlib.CREATE_COST)})",
                                          callback_data="gcreate")])
    else:
        rows.append([InlineKeyboardButton(text="💰 Вклад в казну", callback_data="gdepmenu")])
        if guild_mgr.can_withdraw(ch.uid):
            rows.append([InlineKeyboardButton(text="💰 Снять из казны", callback_data="gwdmenu")])
        rows.append([InlineKeyboardButton(text="📦 Склад", callback_data="gbank")])
        if guild_mgr.can_invite(ch.uid):
            rows.append([InlineKeyboardButton(text="➕ Пригласить", callback_data="ginvmenu")])
        if guild_mgr.can_admin(ch.uid):
            rows.append([InlineKeyboardButton(text="⚙️ Управление составом", callback_data="gmanage")])
        rows.append([InlineKeyboardButton(text="🚪 Выйти из гильдии", callback_data="gleave")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_guild(cb: CallbackQuery, ch: Character):
    await safe_edit(cb, render_guild(ch), kb_guild(ch))


async def guild_create_prompt(cb: CallbackQuery, ch: Character):
    if guild_mgr.guild_of(ch.uid):
        await cb.answer("Вы уже в гильдии", show_alert=True); return
    if PROD and not (db and db.pool):
        await cb.answer("⚙️ Банк гильдии временно недоступен", show_alert=True); return
    if ch.gold < guildlib.CREATE_COST:
        await cb.answer(f"Нужно {money.fmt(guildlib.CREATE_COST)}", show_alert=True); return
    guild_naming[ch.uid] = True
    await safe_edit(cb, "🏰 Введите название гильдии одним сообщением (до 24 символов).",
                    _kb([[InlineKeyboardButton(text="⬅️ Отмена", callback_data="guild")]]))
    await cb.answer()


async def guild_invite_menu(cb: CallbackQuery, ch: Character):
    rows = []
    for o in players_in_room(ch):
        if o.uid not in guild_mgr.member_of:
            rows.append([InlineKeyboardButton(text=f"➕ {o.name}", callback_data=f"ginv:{o.uid}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="(в комнате некого пригласить)", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="guild")])
    await safe_edit(cb, "➕ *Пригласить в гильдию* (игроки в этой комнате):", _kb(rows))


async def do_guild_invite(cb: CallbackQuery, ch: Character, target_uid: int):
    target = chars.get(target_uid)
    if not target:
        await cb.answer("Игрок не найден", show_alert=True); return
    if not guild_mgr.invite(ch.uid, target_uid):
        await cb.answer("Не удалось пригласить", show_alert=True); return
    g = guild_mgr.guild_of(ch.uid)
    await bot.send_message(target_uid, f"📨 *{_ts.esc_md(ch.name)}* зовёт вас в гильдию «{_ts.esc_md(g['name'])}».",
                           parse_mode="Markdown",
                           reply_markup=_kb([[InlineKeyboardButton(text="✅ Принять", callback_data="gaccept"),
                                              InlineKeyboardButton(text="❌ Отклонить", callback_data="gdecline")]]))
    await cb.answer("Приглашение отправлено")


async def guild_accept(cb: CallbackQuery, ch: Character):
    g = guild_mgr.accept(ch.uid)
    if not g:
        await cb.answer("Приглашение истекло", show_alert=True); return
    analytics.track(ch.uid, "guild_join", {"gid": guild_mgr.gid_of(ch.uid)})   # Этап 7.1
    if db and db.pool:
        # Персистентность состава (Этап 3.3): guild_mgr.accept уже подтвердил
        # вступление в памяти — здесь только зеркалим в guild_members, чтобы
        # guild_tx-банк узнал о новом члене сразу, без рестарта/миграции.
        _gid = guild_mgr.gid_of(ch.uid)
        try:
            await guild_tx.add_member(db.pool.acquire, ch.uid, _gid, "member")
        except Exception as e:
            _elog.log_err(_log, "guild_roster_sync_failed", e, uid=ch.uid, gid=_gid, op="accept")
    for uid in g["members"]:
        await send(uid, f"🏰 *{_ts.esc_md(ch.name)}* вступает в гильдию «{_ts.esc_md(g['name'])}».")
    await show_guild(cb, ch)


async def guild_decline(cb: CallbackQuery, ch: Character):
    guild_mgr.decline(ch.uid)
    await show_guild(cb, ch)


async def guild_leave(cb: CallbackQuery, ch: Character):
    g = guild_mgr.guild_of(ch.uid)
    gid = guild_mgr.gid_of(ch.uid)     # захватить ДО leave — leave чистит member_of
    mates = [u for u in (g["members"] if g else []) if u != ch.uid]
    name = g["name"] if g else ""
    guild_mgr.leave(ch.uid)
    if gid and db and db.pool:
        # Персистентность состава (Этап 3.3): зеркалим выход в guild_members.
        try:
            await guild_tx.remove_member(db.pool.acquire, ch.uid, gid)
        except Exception as e:
            _elog.log_err(_log, "guild_roster_sync_failed", e, uid=ch.uid, gid=gid, op="leave")
    for u in mates:
        await send(u, f"🏰 *{_ts.esc_md(ch.name)}* покидает гильдию «{_ts.esc_md(name)}».")
    await cb.answer("Вы вышли из гильдии")
    await show_guild(cb, ch)


def _gold_menu(title, action):
    amounts = [(1000, "10с"), (10000, "1з"), (100000, "10з")]
    rows = [[InlineKeyboardButton(text=lbl, callback_data=f"{action}:{amt}") for amt, lbl in amounts]]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="guild")])
    return title, _kb(rows)


async def guild_dep_menu(cb: CallbackQuery, ch: Character):
    t, kb = _gold_menu(f"💰 Вклад в казну. У вас: 💰{money.fmt(ch.gold)}", "gdep")
    await safe_edit(cb, t, kb)


async def guild_wd_menu(cb: CallbackQuery, ch: Character):
    g = guild_mgr.guild_of(ch.uid)
    t, kb = _gold_menu(f"💰 Снять из казны. В казне: 💰{money.fmt(g.get('bank_gold',0))}", "gwd")
    await safe_edit(cb, t, kb)


async def guild_deposit(cb: CallbackQuery, ch: Character, amount: int):
    gid = guild_mgr.gid_of(ch.uid)
    if not gid:
        await cb.answer("Вы не в гильдии", show_alert=True); return
    if db and db.pool:
        # Догоняющая синхронизация (Этап 3.3): игрок мог вступить/быть повышен
        # ДО этого фикса — guild_mgr уже подтверждает членство, а guild_members
        # о нём мог ещё не знать (иначе _member_rank внутри guild_tx откажет
        # «Вы не состоите в этой гильдии»). Не денежная операция — лок не нужен.
        _rank = guild_mgr.rank(ch.uid)
        if _rank:
            try:
                await guild_tx.ensure_member(db.pool.acquire, ch.uid, gid, _rank)
            except Exception as e:
                _elog.log_err(_log, "guild_roster_sync_failed", e,
                             uid=ch.uid, gid=gid, op="ensure_member_dep")
        # БД-путь: списание у игрока и пополнение казны — одной транзакцией
        # (guild_tx.deposit_gold). op_id с таймстампом-секундой: двойной клик в ту
        # же секунду дедуплицируется идемпотентностью ledger (осознанно).
        import time as _time
        op_id = f"gdep:{ch.uid}:{int(_time.time())}"
        async with _econ_lock(ch.uid):
            ok, msg, char_gold, bank_gold = await guild_tx.deposit_gold(
                db.pool.acquire, ch.uid, gid, amount, op_id)
            if ok and char_gold is not None:
                # обновляем память ДО отпускания лока: ch и guild_mgr (источник
                # отображения) из возвращённых транзакцией значений
                ch.gold = char_gold
                g = guild_mgr.guilds.get(gid)
                if g is not None:
                    g["bank_gold"] = bank_gold
                await save(ch, force=True)
        if not ok:
            await cb.answer(msg, show_alert=True); return
        await cb.answer(f"Внесено 💰{money.fmt(amount)}")
        await guild_dep_menu(cb, ch)
        return
    if PROD:
        await cb.answer("⚙️ Банк гильдии временно недоступен", show_alert=True); return
    if ch.gold < amount:
        await cb.answer("Недостаточно монет", show_alert=True); return
    ch.gold -= amount
    guild_mgr.deposit_gold(ch.uid, amount)
    await save(ch)
    await cb.answer(f"Внесено 💰{money.fmt(amount)}")
    await guild_dep_menu(cb, ch)


async def guild_withdraw(cb: CallbackQuery, ch: Character, amount: int):
    gid = guild_mgr.gid_of(ch.uid)
    if not gid:
        await cb.answer("Вы не в гильдии", show_alert=True); return
    if db and db.pool:
        # Догоняющая синхронизация (Этап 3.3): см. guild_deposit — тот же приём
        # перед проверкой guild_members.rank внутри guild_tx.withdraw_gold.
        _rank = guild_mgr.rank(ch.uid)
        if _rank:
            try:
                await guild_tx.ensure_member(db.pool.acquire, ch.uid, gid, _rank)
            except Exception as e:
                _elog.log_err(_log, "guild_roster_sync_failed", e,
                             uid=ch.uid, gid=gid, op="ensure_member_wd")
        # БД-путь: снятие из казны на игрока — одной транзакцией; право withdraw
        # проверяется по guild_members.rank внутри guild_tx.withdraw_gold.
        import time as _time
        op_id = f"gwd:{ch.uid}:{int(_time.time())}"
        async with _econ_lock(ch.uid):
            ok, msg, char_gold, bank_gold = await guild_tx.withdraw_gold(
                db.pool.acquire, ch.uid, gid, amount, op_id)
            if ok and char_gold is not None:
                ch.gold = char_gold
                g = guild_mgr.guilds.get(gid)
                if g is not None:
                    g["bank_gold"] = bank_gold
                await save(ch, force=True)
        if not ok:
            await cb.answer(msg, show_alert=True); return
        await cb.answer(f"Снято 💰{money.fmt(amount)}")
        await guild_wd_menu(cb, ch)
        return
    if PROD:
        await cb.answer("⚙️ Банк гильдии временно недоступен", show_alert=True); return
    if not guild_mgr.withdraw_gold(ch.uid, amount):
        await cb.answer("Нельзя снять (права/казна)", show_alert=True); return
    ch.gold += amount
    await save(ch)
    await cb.answer(f"Снято 💰{money.fmt(amount)}")
    await guild_wd_menu(cb, ch)


async def guild_bank_items(cb: CallbackQuery, ch: Character):
    g = guild_mgr.guild_of(ch.uid)
    rows = [[InlineKeyboardButton(text="📥 Сдать предмет на склад", callback_data="gdepitem")]]
    seen = {}
    for it in g.get("bank_items", []):
        seen[it] = seen.get(it, 0) + 1
    for it, cnt in seen.items():
        cc = f" x{cnt}" if cnt > 1 else ""
        # выдача предмета со склада = снятие из банка → право can_withdraw
        # (до офицера включительно), а не управление составом.
        if guild_mgr.can_withdraw(ch.uid):
            rows.append([InlineKeyboardButton(text=f"📤 Забрать: {ITEMS[it]['name']}{cc}",
                                              callback_data=f"gwi:{it}")])
        else:
            rows.append([InlineKeyboardButton(text=f"{ITEMS[it]['name']}{cc} (нужен ранг)",
                                              callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="guild")])
    await safe_edit(cb, "📦 *Склад гильдии*", _kb(rows))


async def guild_dep_item_menu(cb: CallbackQuery, ch: Character):
    rows = []
    for it in dict.fromkeys(ch.inventory):
        if ITEMS[it].get("type") == "quest":
            continue
        rows.append([InlineKeyboardButton(text=f"📥 {ITEMS[it]['name']}", callback_data=f"gdi:{it}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="(нечего сдавать)", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gbank")])
    await safe_edit(cb, "📥 *Сдать на склад* (выберите предмет):", _kb(rows))


async def guild_deposit_item(cb: CallbackQuery, ch: Character, key: str):
    gid = guild_mgr.gid_of(ch.uid)
    if not gid:
        await cb.answer("Вы не в гильдии", show_alert=True); return
    if db and db.pool:
        # Догоняющая синхронизация (Этап 3.3): см. guild_deposit.
        _rank = guild_mgr.rank(ch.uid)
        if _rank:
            try:
                await guild_tx.ensure_member(db.pool.acquire, ch.uid, gid, _rank)
            except Exception as e:
                _elog.log_err(_log, "guild_roster_sync_failed", e,
                             uid=ch.uid, gid=gid, op="ensure_member_depitem")
        # БД-путь: перемещение предмета сумка→склад — одной транзакцией.
        import time as _time
        op_id = f"gitem_d:{ch.uid}:{key}:{int(_time.time())}"
        async with _econ_lock(ch.uid):
            ok, msg, inv, bank_items = await guild_tx.deposit_item(
                db.pool.acquire, ch.uid, gid, key, op_id)
            if ok and inv is not None:
                ch.inventory = list(inv)
                g = guild_mgr.guilds.get(gid)
                if g is not None:
                    g["bank_items"] = list(bank_items)
                await save(ch, force=True)
        if not ok:
            await cb.answer(msg, show_alert=True); return
        await cb.answer(f"Сдано: {ITEMS[key]['name']}")
        await guild_bank_items(cb, ch)
        return
    if PROD:
        await cb.answer("⚙️ Банк гильдии временно недоступен", show_alert=True); return
    if key not in ch.inventory:
        await cb.answer("Нет предмета", show_alert=True); return
    ch.inventory.remove(key)
    guild_mgr.deposit_item(ch.uid, key)
    await save(ch)
    await cb.answer(f"Сдано: {ITEMS[key]['name']}")
    await guild_bank_items(cb, ch)


async def guild_withdraw_item(cb: CallbackQuery, ch: Character, key: str):
    gid = guild_mgr.gid_of(ch.uid)
    if not gid:
        await cb.answer("Вы не в гильдии", show_alert=True); return
    if db and db.pool:
        # Догоняющая синхронизация (Этап 3.3): см. guild_deposit.
        _rank = guild_mgr.rank(ch.uid)
        if _rank:
            try:
                await guild_tx.ensure_member(db.pool.acquire, ch.uid, gid, _rank)
            except Exception as e:
                _elog.log_err(_log, "guild_roster_sync_failed", e,
                             uid=ch.uid, gid=gid, op="ensure_member_wditem")
        # БД-путь: перемещение предмета склад→сумка — одной транзакцией; право
        # withdraw проверяется по guild_members.rank внутри guild_tx.withdraw_item.
        import time as _time
        op_id = f"gitem_w:{ch.uid}:{key}:{int(_time.time())}"
        async with _econ_lock(ch.uid):
            ok, msg, inv, bank_items = await guild_tx.withdraw_item(
                db.pool.acquire, ch.uid, gid, key, op_id)
            if ok and inv is not None:
                ch.inventory = list(inv)
                g = guild_mgr.guilds.get(gid)
                if g is not None:
                    g["bank_items"] = list(bank_items)
                await save(ch, force=True)
        if not ok:
            await cb.answer(msg, show_alert=True); return
        await cb.answer(f"Забрано: {ITEMS[key]['name']}")
        await guild_bank_items(cb, ch)
        return
    if PROD:
        await cb.answer("⚙️ Банк гильдии временно недоступен", show_alert=True); return
    if not guild_mgr.withdraw_item(ch.uid, key):
        await cb.answer("Нельзя забрать (права/нет на складе)", show_alert=True); return
    ch.inventory.append(key)
    await save(ch)
    await cb.answer(f"Забрано: {ITEMS[key]['name']}")
    await guild_bank_items(cb, ch)


async def guild_manage(cb: CallbackQuery, ch: Character):
    g = guild_mgr.guild_of(ch.uid)
    rows = []
    for uid in g["members"]:
        if uid == ch.uid:
            continue
        c = chars.get(uid); nm = c.name if c else f"id{uid}"
        rk = g["ranks"].get(str(uid), "member")
        line = []
        if rk == "member":
            line.append(InlineKeyboardButton(text=f"⬆ {nm}", callback_data=f"gpromote:{uid}"))
        elif rk == "officer":
            line.append(InlineKeyboardButton(text=f"⬇ {nm}", callback_data=f"gdemote:{uid}"))
        line.append(InlineKeyboardButton(text=f"❌ {nm}", callback_data=f"gkick:{uid}"))
        rows.append(line)
    if not rows:
        rows.append([InlineKeyboardButton(text="(в гильдии только вы)", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="guild")])
    await safe_edit(cb, "⚙️ *Управление составом*\n⬆ повысить · ⬇ понизить · ❌ исключить", _kb(rows))

async def _notify_deliver(uid: int, category: str, text: str):
    """Доставить одну персональную запись (оффлайн — прямо; онлайн — по политике)."""
    import time as _time
    ch = chars.get(uid)
    if ch is not None and _notify.allow(ch, category, _time.time()) != "send":
        return
    try:
        await bot.send_message(uid, text, parse_mode="Markdown")
        analytics.track(uid, "notification_sent", {"category": category})   # Этап 7.1
        _last_notify_sent[uid] = _time.time()
        if db and db.pool:
            await db.log_notify(uid, category, True)
    except Exception as e:
        code = getattr(e, "error_code", None) or getattr(
            getattr(e, "response", None), "status_code", None)
        if code == 403 or "blocked" in str(e).lower() or "forbidden" in str(e).lower():
            if db and db.pool:
                await db.mark_notify_blocked(uid)
        else:
            _elog.log_err(_log, "notify_deliver_failed", e, uid=uid, category=category)
        if db and db.pool:
            await db.log_notify(uid, category, False)


async def _daily_reset_broadcast():
    """Раз в сутки в 09:00 серверного времени — «Новое задание дня» всем целям.
    Отметка последней рассылки: в памяти + строка notify_schedule с uid=0."""
    import time as _time
    from datetime import datetime
    now = _time.time()
    if datetime.fromtimestamp(now).hour != 9:
        return
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if _daily_reset_broadcast._done == today:
        return
    # защита от повторной рассылки после рестарта: маркер в БД (uid=0)
    if db and db.pool:
        marker = await db.pool.fetchval(
            "SELECT payload FROM notify_schedule WHERE uid=0 AND category=$1",
            "daily_reset")
        if marker == today:
            _daily_reset_broadcast._done = today
            return
        await db.upsert_schedule(0, "daily_reset", now + 86400, today)
    _daily_reset_broadcast._done = today
    await broadcast_all("📅 *Новое задание дня!* Загляни к наставнику за свежим "
                        "ежедневным заданием и наградой.", "daily_reset")
_daily_reset_broadcast._done = None


async def notify_worker(interval: float = 60.0):
    """Фон: раз в минуту разбирает БД-расписание и очередь notify.due()."""
    import time as _time
    await asyncio.sleep(10)      # дать боту прогреться
    while True:
        try:
            now = _time.time()
            # 1) созревшие записи из БД-расписания (dungeon_ready и т.п.)
            if db and db.pool:
                for uid, category, payload in await db.pop_due_schedule(now):
                    if uid == 0:
                        continue
                    text = payload or _notify.LABELS.get(category, "🔔 Уведомление")
                    await _notify_deliver(uid, category, text)
            # 2) очередь в памяти (боссы-broadcast, отложенные emit)
            for rec in _notify.due(now, chars):
                if rec["uid"] is None:
                    await broadcast_all(rec["text"], rec["category"])
                else:
                    await _notify_deliver(rec["uid"], rec["category"], rec["text"])
            # 3) ежедневный ресет в 09:00
            await _daily_reset_broadcast()
        except Exception as e:
            _elog.log_err(_log, "notify_worker_tick_failed", e)
        await asyncio.sleep(interval)


async def _god_load_state() -> dict:
    """Прочитать состояние бога из kv_state['god'] (или пустое). Без БД — память."""
    if db and db.pool:
        try:
            st = await db.kv_get("god")
            if isinstance(st, dict):
                return st
        except Exception:
            pass
    return dict(getattr(_god_load_state, "_mem", {}) or {})


async def _god_save_state(st: dict):
    """Сохранить состояние бога напрямую в kv_state (объём мизерный) + память."""
    _god_load_state._mem = dict(st)
    if db and db.pool:
        try:
            await db.kv_set("god", st)
        except Exception as e:
            _elog.log_err(_log, "god_state_save_failed", e)
_god_load_state._mem = {}


async def god_worker(interval: float = None):
    """Фон: бог-оркестратор. Раз в interval (env GOD_INTERVAL):
      (а) при смене сезона — летопись минувшего сезона (LLM/шаблон), рассылка
          категорией season_rollover, запись в хронику через chronicle.set_epic;
      (б) решение decide() → events.start(...); если стартовало — рассылка
          анонса категорией world_event + chronicle.record("event", announce).

    Дедуп с loop-хуком: фоновые СЛУЧАЙНЫЕ события идут через events.maybe_start()
    в loop.tick() (там же пишется chronicle.record("event", ...)). god_worker же
    зовёт events.start() НАПРЯМУЮ и пишет свою запись сам — пути не пересекаются.
    Оба делят events._active/MAX_ACTIVE: если лимит занят (случайным событием
    или предыдущим решением бога), start() вернёт [] — просто ждём следующий тик."""
    interval = interval if interval is not None else _GOD_INTERVAL
    await asyncio.sleep(15)      # дать боту прогреться
    import time as _time
    while True:
        try:
            st = await _god_load_state()
            # (а) сезонный ролловер — по season_id, а не по флагу
            sid = _seasons.season_id()
            prev = st.get("season")
            if prev is None:
                # первый запуск: зафиксировать текущий сезон без ложного ролловера
                st["season"] = sid
                await _god_save_state(st)
            elif prev != sid:
                epic = await _god.epic_chronicle(prev)
                if epic:
                    _chronicle.set_epic(epic)
                    _chronicle.record("season", f"🏛 Подведены итоги сезона {prev}.")
                    if _notify.ENABLED:
                        await broadcast_all(
                            f"🏛 *Летопись сезона {prev}*\n\n{epic}", "season_rollover")
                st["season"] = sid
                await _god_save_state(st)

            # (б) решение о мировом событии (только если события включены)
            if _events.ENABLED:
                decision = await _god.decide(chars, world)
                msgs, reason = _events.start(
                    decision["event_id"], zone=decision.get("zone"),
                    duration=decision.get("duration"), world=world)
                if msgs:
                    announce = decision.get("announce") or msgs[0]
                    _chronicle.record("event", announce)
                    if _notify.ENABLED:
                        await broadcast_all(f"🌐 {announce}", "world_event")
                    else:
                        # без пуш-слоя — хотя бы онлайн-игрокам эфемерно
                        for c in list(chars.values()):
                            await send(c.uid, f"🌐 {announce}")
                    _log.info("god_event_started event=%s source=%s",
                              decision["event_id"], decision.get("source"))
                # если занято (reason) — молча ждём следующий тик
            st["last"] = _time.time()
            await _god_save_state(st)
        except Exception as e:
            _elog.log_err(_log, "god_worker_tick_failed", e)
        await asyncio.sleep(interval)


async def _flush_world_snapshot():
    """Сбросить в kv_state снимок мира, таймеры боссов, аукцион и территории.
    Без БД (pool=None) — no-op (kv_set сам деградирует), файлы уже пишутся синхронно."""
    if not (db and db.pool):
        return
    try:
        await db.kv_set("world", world.snapshot())
        if gl is not None:
            await db.kv_set("boss_last", {"boss_last": gl.boss_last})
        # аукцион/территории: пишем только если менялись (dirty), но не реже
        # обязательного мирового снапшота — так реже дёргаем БД зря.
        if getattr(auction_mgr, "dirty", False):
            await db.kv_set("auction", auction_mgr.export_state())
            auction_mgr.dirty = False
        if _territory.is_dirty():
            await db.kv_set("territory", _territory.export_state())
            _territory.mark_clean()
        if _chronicle.is_dirty():
            await db.kv_set("chronicle", _chronicle.export_state())
            _chronicle.mark_clean()
        # Этап 8: снимок суточных лимитов LLM на пару (игрок, NPC) — чтобы рестарт
        # процесса не обнулял счётчики и игрок не «дожимал» бюджет перезапуском.
        await db.kv_set("llm_buckets", _cost.BUCKET.export_state())
    except Exception as e:
        _elog.log_err(_log, "world_snapshot_failed", e)


async def _flush_llm_log():
    """Этап 8: слить журнал вызовов LLM в БД и проинкрементить дневной HARD-бюджет.

    Порядок важен: сначала читаем стоимость накопленного окна (buffered_cost),
    затем flush_to_db() дренирует буфер. Сумму окна прибавляем к BUDGET_GUARD
    (при исчерпании provider.enabled() уходит в False — аварийное отключение ИИ)
    и персистим новый дневной расход в kv_state['llm_spend']. Телеметрия НИКОГДА
    не роняет тик снапшота — любые сбои глотаем в лог."""
    try:
        cost_window = _llmlog.buffered_cost()   # ЧИТАЕМ до flush (flush чистит буфер)
        n = await _llmlog.flush_to_db()
        if cost_window > 0:
            snap = _cost.BUDGET_GUARD.add(cost_window)
            if db and db.pool:
                await db.kv_set("llm_spend", snap)
        return n
    except Exception as e:
        _elog.log_err(_log, "llm_log_flush_failed", e)
        return 0


async def snapshot_worker(snap_interval: float = 60.0, flush_interval: float = 3.0):
    """Фон: раз в flush_interval сбрасывает грязных персонажей батчем; раз в
    snap_interval — цельный снимок мира/боссов/аукциона/территорий в kv_state."""
    await asyncio.sleep(5)      # дать боту прогреться
    _since_snap = 0.0
    while True:
        try:
            _write_heartbeat()   # Этап 9: раз в такт обновляем heartbeat для HEALTHCHECK
            await flush_dirty_chars()
            await analytics.flush_to_db()   # Этап 7.1: тем же тактом, что и персонажей
            await _flush_llm_log()          # Этап 8: журнал LLM + инкремент дневного бюджета
            _since_snap += flush_interval
            if _since_snap >= snap_interval:
                _since_snap = 0.0
                await _flush_world_snapshot()
        except Exception as e:
            _elog.log_err(_log, "snapshot_worker_tick_failed", e)
        await asyncio.sleep(flush_interval)


# ───────── Этап 9: watchdog воркеров + метрики Health ─────────
# Реестр фоновых воркеров: name -> {"factory": ()->coro, "task": Task,
# "restarts": [ts,…]}. factory пересоздаёт корутину при перезапуске.
_WORKERS: dict[str, dict] = {}
_WORKER_MAX_RESTARTS_PER_HOUR = 3


def _spawn_worker(name: str, factory):
    """Запустить воркер и зарегистрировать его в реестре (для watchdog)."""
    rec = _WORKERS.get(name) or {"restarts": []}
    rec["factory"] = factory
    rec["task"] = asyncio.create_task(factory())
    _WORKERS[name] = rec
    return rec["task"]


async def _alert_admins(text: str):
    """Разослать текст всем ADMIN_IDS (алерт о падении воркера). Ошибки — молча."""
    for _uid in ADMIN_IDS:
        try:
            await bot.send_message(_uid, text)
        except Exception:
            pass


async def watchdog_worker(interval: float = 60.0):
    """Раз в interval проверяет живость всех воркеров. Упавший (task.done):
      • логируем причину (исключение задачи, если было);
      • перезапускаем, но не чаще _WORKER_MAX_RESTARTS_PER_HOUR раз в час;
      • при исчерпании лимита — только громкий лог + алерт всем админам."""
    await asyncio.sleep(30)      # дать воркерам прогреться
    while True:
        now = _time_mod.time()
        for name, rec in list(_WORKERS.items()):
            task = rec.get("task")
            if task is None or not task.done():
                continue
            # причина падения: исключение задачи (если не отменена штатно)
            exc = None
            if not task.cancelled():
                try:
                    exc = task.exception()
                except Exception:
                    exc = None
            _elog.log_err(_log, "worker_died", exc, worker=name)
            rec["restarts"] = [r for r in rec.get("restarts", []) if now - r < 3600]
            if len(rec["restarts"]) < _WORKER_MAX_RESTARTS_PER_HOUR:
                rec["restarts"].append(now)
                rec["task"] = asyncio.create_task(rec["factory"]())
                _elog.log_err(_log, "worker_restarted", worker=name,
                              restarts_last_hour=len(rec["restarts"]))
            else:
                _elog.log_err(_log, "worker_restart_exhausted", worker=name,
                              restarts_last_hour=len(rec["restarts"]))
                await _alert_admins(
                    f"⚠️ Воркер *{name}* упал и превысил лимит перезапусков "
                    f"({_WORKER_MAX_RESTARTS_PER_HOUR}/час). Требуется вмешательство "
                    f"— проверьте логи процесса.")
        await asyncio.sleep(interval)


async def _measure_db_latency_ms():
    """Замер задержки простого SELECT 1 (для админ-Health). None — БД нет,
    -1.0 — ошибка запроса."""
    if not (db and db.pool):
        return None
    t0 = _time_mod.perf_counter()
    try:
        await db.pool.fetchval("SELECT 1")
    except Exception:
        return -1.0
    return (_time_mod.perf_counter() - t0) * 1000.0


async def main():
    global db, gl, BOT_USERNAME
    # Мягкая проверка версии Python: требование проекта — 3.12+ (см. pyproject.toml).
    # Не падаем — более новые версии (3.13, 3.14...) тоже подходят, это просто
    # предупреждение для тех, кто ещё сидит на устаревшем интерпретаторе.
    if sys.version_info < (3, 12):
        print(
            f"⚠️  Обнаружен Python {sys.version_info.major}.{sys.version_info.minor} — "
            f"проекту требуется 3.12+. Игра попробует запуститься, но это не поддерживается."
        )
    validate()
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    db = Database()
    try:
        await db.connect()
        loaded = await db.load_all()
        chars.update(loaded)
        print(f"✅ PostgreSQL подключён, загружено персонажей: {len(loaded)}")
    except Exception as e:
        if PROD:
            # Публичная бета без БД недопустима: аукцион/золото должны быть
            # транзакционными. Падаем громко, чтобы оркестратор перезапустил
            # процесс или поднял тревогу, а не пускал игроков в режим-в-память.
            _elog.log_err(_log, "prod_db_unavailable_fatal", e)
            print(f"❌ PROD=1, но PostgreSQL недоступен ({e}). Останов: публичная игра без БД запрещена.")
            sys.exit(1)
        print(f"⚠️  PostgreSQL недоступен ({e}). Игра запустится без сохранения.")
        db.pool = None
    from ai import memory as _aimem
    _aimem.set_db(db)   # инъекция БД для долгой памяти NPC (npc_memories); pool=None -> fallback
    analytics.set_db(db)   # Этап 7.1: инъекция БД для flush_to_db() (analytics_events/attribution)
    _llmlog.set_db(db)     # Этап 8: инъекция БД для журнала LLM (llm_log); pool=None -> дренаж впустую
    _mod.set_db(db)        # Этап 7.2: инъекция БД для модерации (moderation-таблица)
    try:
        await _mod.load()  # поднять кэш банов/мутов из БД (без пула — пустой кэш)
        print(f"🛡 Модерация: загружено записей {len(_mod._state)}; "
              f"админов: {len(ADMIN_IDS)}.")
    except Exception as e:
        _elog.log_err(_log, "moderation_load_failed", e)

    # ───────── персистентность рантайма: восстановление на старте ─────────
    # Порядок: сперва мир/боссы/аукцион/территории поднимаем из kv_state (если
    # БД доступна), затем создаём GameLoop. Без БД (pool=None) — всё как раньше:
    # мир свежий, аукцион/территории живут в файлах (поведение не меняется).
    if db and db.pool:
        # аукцион и территории переводим в db-режим: их save() теперь копит
        # dirty, а фактическую запись делает snapshot_worker в kv_state.
        auction_mgr.db_mode = True
        _territory.set_db_mode(True)
        _chronicle.set_db_mode(True)
        try:
            _auc_kv = await db.kv_get("auction")
            if _auc_kv is not None:
                auction_mgr.import_state(_auc_kv)
            elif os.path.exists(auction_mgr.path):
                # одноразовый импорт из старого файла; далее источник — kv_state
                auction_mgr.load()
                await db.kv_set("auction", auction_mgr.export_state())
            _terr_kv = await db.kv_get("territory")
            if _terr_kv is not None:
                _territory.import_state(_terr_kv)
            elif os.path.exists(_TERR_PATH):
                _territory.load(_TERR_PATH)
                await db.kv_set("territory", _territory.export_state())
            _chr_kv = await db.kv_get("chronicle")
            if _chr_kv is not None:
                _chronicle.import_state(_chr_kv)
            # Этап 8: восстановить дневной расход LLM и суточные лимиты на пару
            # (игрок, NPC). Снимок из прошлого дня трактуется как нулевой расход
            # сегодня (логика внутри load/import_state) — аварийное отключение
            # ИИ переживает рестарт, но не «застревает» на вчерашнем дне.
            _spend_kv = await db.kv_get("llm_spend")
            _cost.BUDGET_GUARD.load(_spend_kv)
            _buckets_kv = await db.kv_get("llm_buckets")
            if _buckets_kv is not None:
                _cost.BUCKET.import_state(_buckets_kv)
        except Exception as e:
            print(f"⚠️  Не удалось поднять аукцион/территории из БД: {e}")
        # Этап 3.1: одноразовая миграция активных лотов в транзакционную таблицу
        # auction_listings. Выполняется только если таблица пуста, а в памяти
        # (поднятой выше из kv_state/auction.json) есть активные лоты.
        try:
            _auc_cnt = await db.pool.fetchval("SELECT count(*) FROM auction_listings")
            if not _auc_cnt and auction_mgr.listings:
                _migrated = await econ_tx.import_lots(
                    db.pool.acquire, list(auction_mgr.listings.values()))
                print(f"🔁 Аукцион: мигрировано активных лотов в БД: {_migrated}.")
        except Exception as e:
            print(f"⚠️  Миграция аукциона в БД не удалась: {e}")
        # Этап 3.2: гильдии — источник истины в БД. Если таблица guilds непуста —
        # грузим состав в guild_mgr (guilds.json игнорируется). Если пуста, а в
        # guilds.json (guild_mgr.guilds загружен при старте) что-то есть — одноразовая
        # миграция в БД. Далее банк/создание идут через guild_tx.
        try:
            _g_cnt = await db.pool.fetchval("SELECT count(*) FROM guilds")
            if _g_cnt:
                _gd = await guild_tx.load_guilds(db.pool.acquire)
                guild_mgr.guilds = _gd
                guild_mgr.member_of = {}
                for _gid, _g in _gd.items():
                    for _u in _g.get("members", []):
                        guild_mgr.member_of[int(_u)] = _gid
                guild_mgr._next = max([int(_gid) for _gid in _gd if str(_gid).isdigit()] + [0]) + 1
                print(f"🏰 Гильдии загружены из БД: {len(_gd)}.")
            elif guild_mgr.guilds:
                _gmig = await guild_tx.import_from_manager(
                    db.pool.acquire, {"guilds": guild_mgr.guilds, "next": guild_mgr._next})
                print(f"🔁 Гильдии: мигрировано в БД: {_gmig}.")
        except Exception as e:
            _elog.log_err(_log, "guild_db_init_failed", e)

    gl = GameLoop(world, chars, send, save)
    # восстановить снимок мира и таймеры боссов (после создания World, ДО gl.run)
    if db and db.pool:
        try:
            _wsnap = await db.kv_get("world")
            if _wsnap:
                _n = world.restore(_wsnap)
                print(f"🗺 Мир восстановлен из снимка: применено записей {_n}.")
            _bl = await db.kv_get("boss_last")
            if _bl and isinstance(_bl.get("boss_last"), dict):
                gl.boss_last = dict(_bl["boss_last"])
        except Exception as e:
            print(f"⚠️  Не удалось восстановить снимок мира: {e}")
    gl.party_mgr = party_mgr             # дележ опыта в группе
    gl.on_combat_hit = combat_hit        # удары мобов обновляют боевую панель
    gl.on_combat_reward = combat_reward  # награда за убийство пере-постит панель вниз
    gl.on_death = death_screen           # экран смерти с кнопкой возрождения
    gl.referral_lookup = chars.get       # найти реферера по uid (награда на левелапе друга)
    gl.on_referral = send                # доставить текст рефереру (оффлайн — молча, см. send())
    gl.on_ambient = broadcast_ephemeral  # анонс забредания + ambient NPC — эфемерные строки
    if _notify.ENABLED:
        gl.on_world_notify = broadcast_all   # анонс босса — по всем uid из БД
    # Этап 9: воркеры регистрируются в реестре (_spawn_worker) — watchdog держит
    # их ссылки, ловит падение и перезапускает (до 3/час, дальше — алерт админам).
    _spawn_worker("game_loop", lambda: gl.run(interval=1.0))
    if _notify.ENABLED:
        _spawn_worker("notify_worker", lambda: notify_worker(interval=60.0))
        print("🔔 Push-реактивация включена (NOTIFY=1).")
    # бог-оркестратор: мировые события по решению LLM (или fallback) + летопись
    # сезона. Запускаем только если мировые события включены.
    if _events.ENABLED:
        _spawn_worker("god_worker", lambda: god_worker(interval=_GOD_INTERVAL))
        _mode = "llm" if _provider.enabled() else "fallback"
        print(f"🌩 Бог-оркестратор активен (интервал {_GOD_INTERVAL // 3600}ч, режим {_mode}).")
    # фоновый снапшот мира + дебаунс-флашер персонажей (работает и без БД:
    # там флашер просто очищает набор, а снапшот — no-op)
    _spawn_worker("snapshot_worker",
                  lambda: snapshot_worker(snap_interval=60.0, flush_interval=3.0))
    # watchdog: сторож живости всех воркеров выше (сам в реестр не входит).
    asyncio.create_task(watchdog_worker(interval=60.0))
    if db and db.pool:
        print("💾 Персистентность рантайма включена (снимок мира раз в 60с).")
    print("⚔️  СЕМЬ КОРОН v3 запущена. Реал-тайм цикл активен.")
    try:
        await dp.start_polling(bot)
    finally:
        # graceful shutdown: финальный снимок мира и флаш всех грязных персонажей,
        # чтобы не потерять прогресс между последним тиком снапшота и остановкой.
        try:
            if db and db.pool:
                # graceful shutdown: добить провалившихся до 3 попыток с паузой 1с
                ok, left = await _persist.flush_until_clean(
                    _char_dirty, chars.get, db.save, attempts=3, pause=1.0, logger=_log)
                if left:
                    _elog.log_err(_log, "shutdown_flush_incomplete", pending=left, saved=ok)
            else:
                _char_dirty.drain()
            await _flush_world_snapshot()
            print("💾 Финальный снимок сохранён.")
        except Exception as e:
            _elog.log_err(_log, "final_snapshot_failed", e)


if __name__ == "__main__":
    # Этап 9: fail-fast — заглушка BOT_TOKEN, а в PROD=1 и отсутствие DATABASE_URL,
    # роняют старт с понятным сообщением ДО запуска event loop (см. bot/config_check).
    config_check.validate_or_die()
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("👋 Бот остановлен.")
