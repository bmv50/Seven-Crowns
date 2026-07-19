# -*- coding: utf-8 -*-
"""
Контроль стоимости/латентности LLM (адаптация из референса TeleMud).

Три механизма, все ЛОКАЛЬНЫЕ (без сети, без эмбеддингов — работает офлайн):
  1. SemanticCache — кэш ответов по лексическому сходству вопроса (Jaccard
     по словам). Если игрок спрашивает почти то же самое (sim ≥ порога) —
     отдаём готовый ответ без обращения к модели.
  2. TokenBucket — суточный лимит обращений к модели на пару (игрок, NPC),
     чтобы один игрок не «сжёг» бюджет на одном персонаже.
  3. DialogueSession — тайм-аут диалога: после паузы история сжимается в одно
     краткое «воспоминание» и подставляется в следующий промпт (экономит токены
     и сохраняет преемственность).

Всё настраивается через окружение:
    AI_DAILY_PER_NPC   = 30     лимит запросов к модели на (игрок, NPC) в сутки
    AI_CACHE_SIM       = 0.9    порог сходства вопросов для кэша [0..1]
    AI_DIALOG_TIMEOUT  = 300    тайм-аут диалога, секунды
"""
import os
import re
import time
from typing import Dict, List, Optional, Tuple

_WORD_RE = re.compile(r"[\wа-яё]+", re.IGNORECASE)


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _wordset(text: str) -> frozenset:
    return frozenset(_WORD_RE.findall((text or "").lower()))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ───────── 1. Семантический (лексический) кэш ─────────
class SemanticCache:
    """Кэш реплик с поиском по сходству вопроса.

    ns — пространство имён кэша; вызывающий передаёт "{uid}:{npc_id}", чтобы
    реплики НЕ протекали между игроками (в промпт подставляется память/уровень
    конкретного игрока). У записей есть TTL (AI_CACHE_TTL, сек) — устаревшие
    (смена времени суток/мировых событий) не отдаются.
    """

    def __init__(self, max_per_ns: int = 64):
        # ns -> list of (wordset, query_text, reply, ts)
        self._store: Dict[str, List[Tuple[frozenset, str, str, float]]] = {}
        self.max_per_ns = max_per_ns

    def get(self, ns: str, query: str, threshold: float = None,
            now: float = None) -> Optional[str]:
        threshold = _cfg_float("AI_CACHE_SIM", 0.9) if threshold is None else threshold
        ttl = _cfg_int("AI_CACHE_TTL", 1800)
        now = now or time.time()
        qs = _wordset(query)
        best, best_sim = None, 0.0
        for ws, _q, reply, ts in self._store.get(ns, []):
            if ttl and now - ts > ttl:
                continue
            sim = jaccard(qs, ws)
            if sim > best_sim:
                best, best_sim = reply, sim
        return best if best_sim >= threshold else None

    def put(self, ns: str, query: str, reply: str, now: float = None):
        now = now or time.time()
        lst = self._store.setdefault(ns, [])
        lst.append((_wordset(query), query, reply, now))
        if len(lst) > self.max_per_ns:
            del lst[:len(lst) - self.max_per_ns]

    def clear(self, ns: str = None):
        if ns is None:
            self._store.clear()
        else:
            self._store.pop(ns, None)


# ───────── 2. Суточный лимит обращений (token bucket по дням) ─────────
class TokenBucket:
    """Считает обращения к модели на (uid, npc_id) за текущие сутки (UTC-день)."""

    def __init__(self):
        # (uid, npc_id) -> (day_index, count)
        self._counts: Dict[Tuple[int, str], Tuple[int, int]] = {}

    @staticmethod
    def _day(now: float = None) -> int:
        return int((now or time.time()) // 86400)

    def limit(self) -> int:
        return _cfg_int("AI_DAILY_PER_NPC", 30)

    def allow(self, uid: int, npc_id: str, now: float = None) -> bool:
        day = self._day(now)
        d, cnt = self._counts.get((uid, npc_id), (day, 0))
        if d != day:
            cnt = 0
        return cnt < self.limit()

    def record(self, uid: int, npc_id: str, now: float = None):
        day = self._day(now)
        d, cnt = self._counts.get((uid, npc_id), (day, 0))
        if d != day:
            cnt = 0
        self._counts[(uid, npc_id)] = (day, cnt + 1)
        if len(self._counts) > 5000:        # защита от утечки: чистим прошлые дни
            self._sweep(day)

    def _sweep(self, day: int):
        for k in [k for k, (d, _) in self._counts.items() if d != day]:
            self._counts.pop(k, None)

    def used(self, uid: int, npc_id: str, now: float = None) -> int:
        day = self._day(now)
        d, cnt = self._counts.get((uid, npc_id), (day, 0))
        return cnt if d == day else 0

    # ───────── Этап 8: персист лимитов в БД (kv_state['llm_buckets']) ─────────
    # Рестарт процесса НЕ должен сбрасывать суточные лимиты на пару (игрок, NPC) —
    # иначе игрок «дожимает» бюджет перезапуском. Снимок пишет snapshot_worker
    # при флаше llmlog, восстановление — на старте (bot/main.py). Сохраняем только
    # счётчики ТЕКУЩЕГО дня (прошлые всё равно сброшены логикой allow/record).
    def export_state(self, now: float = None) -> dict:
        """JSON-safe снимок счётчиков за текущий день -> {day, counts:[[uid,npc,cnt],...]}."""
        day = self._day(now)
        items = [[uid, npc_id, cnt]
                 for (uid, npc_id), (d, cnt) in self._counts.items() if d == day]
        return {"day": day, "counts": items}

    def import_state(self, data: dict, now: float = None):
        """Восстановить счётчики из снимка. Снимок из ПРОШЛОГО дня игнорируется
        (день сменился — лимиты и так обнулены). Идемпотентно перезаписывает."""
        self._counts = {}
        if not data:
            return
        day = self._day(now)
        if data.get("day") != day:
            return
        for item in data.get("counts", []) or []:
            try:
                uid, npc_id, cnt = item
                self._counts[(int(uid), str(npc_id))] = (day, int(cnt))
            except (ValueError, TypeError):
                continue


def summarize_history(history) -> str:
    """Сжать диалоговую историю в короткую строку тем (без LLM). Возвращает '' если пусто."""
    if not history:
        return ""
    topics = []
    for m in history:
        if m.get("role") == "user":
            txt = re.sub(r"^.*?говорит тебе:\s*«?", "", m.get("content", ""))
            txt = txt.strip("«»  ").strip()
            if txt:
                topics.append(txt)
    if not topics:
        return ""
    joined = "; ".join(topics[-3:])
    return joined[:157] + "…" if len(joined) > 160 else joined


# ───────── 3. Сессии диалога + сжатие в воспоминание ─────────
class DialogueSession:
    """Отслеживает активность диалога; по тайм-ауту сжимает историю в summary."""

    def __init__(self):
        self._last: Dict[Tuple[int, str], float] = {}
        self._summary: Dict[Tuple[int, str], str] = {}

    def timeout(self) -> int:
        return _cfg_int("AI_DIALOG_TIMEOUT", 300)

    def touch(self, uid: int, npc_id: str, now: float = None):
        self._last[(uid, npc_id)] = now or time.time()

    def expired(self, uid: int, npc_id: str, now: float = None) -> bool:
        last = self._last.get((uid, npc_id))
        if last is None:
            return False
        return (now or time.time()) - last > self.timeout()

    def summary(self, uid: int, npc_id: str) -> Optional[str]:
        return self._summary.get((uid, npc_id))

    def summarize(self, uid: int, npc_id: str, history: List[dict]):
        """Локально сжать историю в одну короткую строку-воспоминание (без LLM)."""
        joined = summarize_history(history)
        if not joined:
            return
        prev = self._summary.get((uid, npc_id))
        self._summary[(uid, npc_id)] = joined if not prev else (prev + " | " + joined)[-200:]

    def reset(self, uid: int, npc_id: str = None):
        if npc_id is None:
            for k in [k for k in self._last if k[0] == uid]:
                self._last.pop(k, None)
            for k in [k for k in self._summary if k[0] == uid]:
                self._summary.pop(k, None)
        else:
            self._last.pop((uid, npc_id), None)
            self._summary.pop((uid, npc_id), None)


# ───────── 4. Дневной HARD-бюджет + аварийное отключение (Этап 8) ─────────
def daily_budget_usd() -> float:
    """Дневной потолок расхода на LLM (USD). env AI_DAILY_BUDGET_USD, дефолт 5.0."""
    return _cfg_float("AI_DAILY_BUDGET_USD", 5.0)


def _today_str(now: float = None) -> str:
    """UTC-дата 'YYYY-MM-DD' — ключ дня для дневного расхода (совпадает с
    посуточной логикой TokenBucket, но человекочитаемо для kv_state['llm_spend'])."""
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


class BudgetGuard:
    """Дневной расход на LLM и проверка исчерпания бюджета.

    Источник истины — БД (kv_state['llm_spend'] = {date, usd}); здесь держим
    БЫСТРУЮ копию в памяти, чтобы provider.enabled() не дёргал БД на каждый вызов
    (сам provider кэширует проверку ещё на 60с). Расход инкрементится при флаше
    журнала LLM (ai/llmlog) в snapshot_worker; при смене суток обнуляется.

    При spent ≥ лимита provider.enabled() возвращает False до конца дня —
    АВАРИЙНОЕ ОТКЛЮЧЕНИЕ: игра целиком переходит на шаблоны, деньги не жгутся."""

    def __init__(self):
        self._date: str = None
        self._spent: float = 0.0

    def load(self, snap: dict, now: float = None):
        """Восстановить из kv_state['llm_spend'] на старте. Снимок из прошлого дня
        трактуется как нулевой расход сегодня (день сменился)."""
        today = _today_str(now)
        if not snap or snap.get("date") != today:
            self._date, self._spent = today, 0.0
            return
        self._date = snap.get("date")
        try:
            self._spent = max(0.0, float(snap.get("usd") or 0.0))
        except (ValueError, TypeError):
            self._spent = 0.0

    def add(self, usd: float, now: float = None) -> dict:
        """Проинкрементить дневной расход (при смене суток — сброс). Возвращает
        снимок {date, usd} для записи в kv_state."""
        today = _today_str(now)
        if self._date != today:
            self._date, self._spent = today, 0.0
        self._spent += max(0.0, float(usd or 0.0))
        return self.snapshot(now)

    def snapshot(self, now: float = None) -> dict:
        today = _today_str(now)
        if self._date != today:
            return {"date": today, "usd": 0.0}
        return {"date": self._date, "usd": round(self._spent, 6)}

    def spent_today(self, now: float = None) -> float:
        today = _today_str(now)
        return self._spent if self._date == today else 0.0

    def exhausted(self, now: float = None) -> bool:
        """Исчерпан ли дневной бюджет. На смене суток -> False (расход обнулён)."""
        return self.spent_today(now) >= daily_budget_usd()


# единые экземпляры на процесс
CACHE = SemanticCache()
BUCKET = TokenBucket()
SESSIONS = DialogueSession()
BUDGET_GUARD = BudgetGuard()
