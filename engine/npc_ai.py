# -*- coding: utf-8 -*-
"""
Слой «живых» NPC: Utility AI + конечный автомат состояний (FSM).

Адаптация концепций из референс-движка TeleMud (Go): рядовые мобы получают
внутренние потребности (голод/усталость/страх/скука/жадность), которые растут
со временем; действие выбирается по кривым полезности + softmax с температурой,
чтобы поведение не было «роботным».

Живёт РЯДОМ со старой логикой и НИЧЕГО не меняет, пока ENABLED=False.
Когда включён (env NPC_AI=1) — loop.tick() вызывает tick_ambient(): мобы вне
боя обновляют потребности, могут забрести в соседнюю комнату, лечь отдохнуть;
раненый моб в бою при высоком страхе может сбежать (сбросить агро и уйти).

Состояние ИИ хранится прямо на экземпляре моба (mob._ai), лениво по дельте
времени — посекундный фоновый цикл не нужен (Catch-up как в референсе).
"""
import math
import random
import time
from typing import List, Tuple

from .content import WORLD

# ── глобальный переключатель слоя ──
ENABLED = False

# Сколько мобов максимум обрабатывать за один ambient-тик (бюджет CPU).
TICK_BUDGET = 120

# Скорость роста потребностей в секунду (нормированные [0..1]).
RATE_HUNGER = 0.0008
RATE_FATIGUE = 0.0012
RATE_BOREDOM = 0.0020
DECAY_FEAR = 0.010

# Состояния FSM.
IDLE, PATROL, COMBAT, FLEE, REST = "IDLE", "PATROL", "COMBAT", "FLEE", "REST"


# ───────── кривые полезности ─────────
def exp_curve(x: float, k: float = 3.0) -> float:
    """Экспонента: взрывной рост у порога (страх, критический голод)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x ** k


def sigmoid(x: float, k: float = 12.0, x0: float = 0.7) -> float:
    """S-кривая: резкий порог (сон при усталости > x0)."""
    return 1.0 / (1.0 + math.exp(-k * (x - x0)))


def log_curve(x: float, b: float = 10.0) -> float:
    """Логарифм: убывающая отдача (сбор золота, общение)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return math.log(x * (b - 1) + 1, b)


# ───────── состояние ИИ на мобе ─────────
class MobAI:
    __slots__ = ("needs", "state", "home", "last_update", "memory", "greed")

    def __init__(self, mob):
        self.needs = {"hunger": 0.0, "fatigue": 0.0, "fear": 0.0, "boredom": 0.0}
        self.state = IDLE
        self.home = mob.room                 # куда тянет вернуться
        self.last_update = time.time()
        self.memory: List[Tuple[float, str, str]] = []  # (ts, type, source)
        # «жадность» — характерная черта, стабильна на всю жизнь экземпляра
        self.greed = round(random.uniform(0.1, 0.6), 2)

    def remember(self, etype: str, source: str = ""):
        self.memory.append((time.time(), etype, source))
        if len(self.memory) > 20:           # кольцевой буфер на 20 событий
            self.memory.pop(0)


def get_ai(mob) -> MobAI:
    ai = getattr(mob, "_ai", None)
    if ai is None:
        ai = MobAI(mob)
        try:
            mob._ai = ai
        except Exception:
            pass
    return ai


# ───────── обновление потребностей (lazy, по дельте) ─────────
def update_needs(ai: MobAI, delta: float):
    n = ai.needs
    n["hunger"] = min(1.0, n["hunger"] + RATE_HUNGER * delta)
    n["fatigue"] = min(1.0, n["fatigue"] + RATE_FATIGUE * delta)
    n["boredom"] = min(1.0, n["boredom"] + RATE_BOREDOM * delta)
    n["fear"] = max(0.0, n["fear"] - DECAY_FEAR * delta)


# ───────── выбор цели (Utility + softmax) ─────────
def evaluate_goal(mob, ai: MobAI, room_has_players: bool, temperature: float = 0.25) -> str:
    n = ai.needs
    hp_pct = mob.hp / mob.max_hp if mob.max_hp else 1.0
    injured = 1.0 - hp_pct

    goals = {
        "SURVIVE": exp_curve(injured, 3.0) * (0.3 + 0.7 * n["fear"]),
        "REST":    sigmoid(n["fatigue"], 12.0, 0.7),
        "GATHER":  ai.greed * log_curve(n["hunger"]) if room_has_players else 0.0,
        "SOCIAL":  n["boredom"] * 0.8,
        "WANDER":  0.15,
    }
    if temperature <= 0.05:
        return max(goals, key=goals.get)

    names = list(goals)
    exps = [math.exp(goals[g] / temperature) for g in names]
    total = sum(exps) or 1.0
    r = random.random() * total
    acc = 0.0
    for name, e in zip(names, exps):
        acc += e
        if r <= acc:
            return name
    return names[-1]


# ───────── FSM: переходы по событию и показателям ─────────
def step_fsm(mob, ai: MobAI, event_type: str = None, source: str = "") -> bool:
    """Вернуть True, если состояние изменилось."""
    old = ai.state
    hp_pct = mob.hp / mob.max_hp if mob.max_hp else 1.0

    # высокий приоритет: реакция на события
    if event_type == "attacked" and ai.state != FLEE:
        ai.state = COMBAT
        ai.needs["fear"] = min(1.0, ai.needs["fear"] + 0.4)
    elif event_type == "target_lost" and ai.state == COMBAT:
        ai.state = IDLE

    # переходы по текущему состоянию
    if ai.state == COMBAT:
        if hp_pct < 0.20 and ai.needs["fear"] > 0.6:
            ai.state = FLEE
    elif ai.state == FLEE:
        if ai.needs["fear"] < 0.2:
            ai.state = IDLE
    elif ai.state == REST:
        if ai.needs["fatigue"] < 0.1:
            ai.state = IDLE
    elif ai.state == IDLE:
        if ai.needs["fatigue"] > 0.9:
            ai.state = REST
        elif ai.needs["boredom"] > 0.7:
            ai.state = PATROL
    elif ai.state == PATROL:
        if ai.needs["fatigue"] > 0.8:
            ai.state = REST

    return ai.state != old


# ───────── вспомогательное: перемещение моба ─────────
def _adjacent_rooms(room: str) -> List[str]:
    exits = WORLD.get(room, {}).get("exits", {})
    return [dst for dst in exits.values() if dst in WORLD]


def _move_mob(world, mob, dst: str) -> bool:
    """Переставить экземпляр моба из текущей комнаты в dst. True при успехе."""
    src = mob.room
    if dst not in world.mobs or src == dst:
        return False
    lst = world.mobs.get(src, [])
    if mob in lst:
        lst.remove(mob)
    mob.room = dst
    world.mobs.setdefault(dst, []).append(mob)
    return True


# ───────── основной ambient-тик ─────────
def tick_ambient(world, occupied_rooms, now: float = None, rooms=None) -> List[Tuple[str, str]]:
    """
    Обновить «жизнь» мобов вне боя. Возвращает список (room, line) для рассылки.
    occupied_rooms — комнаты с живыми игроками (туда приходят флейвор-строки).
    rooms — какие комнаты обрабатывать (для ленивого режима — только активные);
            None = весь мир. Ничего не делает, если ENABLED=False.
    """
    if not ENABLED:
        return []
    now = now or time.time()
    occupied = set(occupied_rooms or [])
    room_iter = list(rooms) if rooms is not None else list(world.mobs.keys())
    lines: List[Tuple[str, str]] = []
    processed = 0

    for room in room_iter:
        for mob in list(world.mobs.get(room, [])):
            if processed >= TICK_BUDGET:
                return lines
            if not mob.alive:
                continue
            ai = get_ai(mob)
            delta = now - ai.last_update
            if delta < 5:                    # не чаще раза в ~5с на моба
                continue
            ai.last_update = now

            # бой обрабатывает loop/combat; ИИ только реагирует на состояние агро
            if mob.aggro:
                update_needs(ai, delta)
                step_fsm(mob, ai, "attacked")
                if ai.state == FLEE:
                    # моб сбегает: сбрасывает агро и уходит в соседнюю комнату
                    nbrs = _adjacent_rooms(mob.room)
                    mob.aggro = []
                    mob.threat.clear()
                    name = mob.meta.get("name", "Существо")
                    if nbrs and _move_mob(world, mob, random.choice(nbrs)):
                        if room in occupied:
                            lines.append((room, f"🏃 {name} в ужасе убегает прочь!"))
                    ai.state = IDLE
                    ai.needs["fear"] = 0.0
                processed += 1
                continue

            # вне боя — обычная жизнь
            update_needs(ai, delta)
            goal = evaluate_goal(mob, ai, room_has_players=room in occupied)
            step_fsm(mob, ai)
            name = mob.meta.get("name", "Существо")

            if ai.state == REST:
                ai.needs["fatigue"] = max(0.0, ai.needs["fatigue"] - 0.15)
                if room in occupied and random.random() < 0.10:
                    lines.append((room, f"💤 {name} дремлет в стороне."))
            elif goal == "WANDER" or ai.state == PATROL:
                # редкое блуждание в соседнюю комнату; домой тянет сильнее
                if random.random() < 0.12:
                    nbrs = _adjacent_rooms(mob.room)
                    if nbrs:
                        dst = ai.home if (ai.home in nbrs and random.random() < 0.5) \
                            else random.choice(nbrs)
                        if _move_mob(world, mob, dst):
                            ai.needs["boredom"] = max(0.0, ai.needs["boredom"] - 0.3)
                            if room in occupied:
                                lines.append((room, f"🚶 {name} уходит прочь."))
                            elif dst in occupied:
                                lines.append((dst, f"🚶 {name} забредает сюда."))
            processed += 1

    return lines
