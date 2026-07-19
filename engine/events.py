# -*- coding: utf-8 -*-
"""
Динамические мировые события («ИИ-бог»): вторжения, ярмарки, аномалии, пророчества.

За флагом ENABLED. Есть два пути запуска:
  • maybe_start() — фоновый случайный запуск по таймеру (loop.tick() раз в
    CHECK_EVERY с шансом START_CHANCE). Наследие Фазы 2, работает как раньше.
  • start(eid, zone, duration) — ПРЯМОЙ запуск конкретного события с валидацией
    и клампом длительности. Его дёргает бог-оркестратор (ai/god.py) после
    решения LLM (или fallback). Движок — арбитр: невалидный eid/зона/длительность
    не исполняются as-is, а либо отклоняются, либо приводятся к валидным.

Событие живёт duration секунд и даёт множители xp/gold/loot (глобально или в
своей зоне), а вторжение/миграция ещё и спавнит усиленных мобов. По истечении
событие закрывается (вторженцы деспавнятся).

Состояние событий — в памяти процесса (как мировые боссы); на множители влияет
modifiers(zone), которые loop применяет к награде за убийство.
"""
import random
import time

from . import content
from .content import WORLD, MOBS

ENABLED = False
_DEFS = (content._load_optional("events.yaml") or {}).get("events", {})

CHECK_EVERY = 600     # как часто пытаться запустить событие (сек)
START_CHANCE = 0.5    # шанс запуска при проверке
MAX_ACTIVE = 1        # одновременно активных событий

# Классификация зон для allowed_zones="city"/"wild". Города — обжитые хабы
# (столицы/поселения/чертоги), где уместны ярмарки; всё прочее с комнатами —
# «дикие» земли (миграции стай и т.п.). Список зон берётся из карты мира.
_CITY_ZONES = frozenset({
    "Железный Острог", "Стылая Гавань", "Гномий Чертог", "Чертоги Рассвета",
})

_active = []          # [{id, def, zone, ends_at, spawned:[(room, inst)]}]
_last_check = 0.0


def reset():
    _active.clear()
    global _last_check
    _last_check = 0.0


def active():
    return _active


def _rooms_of_zone(zone):
    return [rid for rid, r in WORLD.items() if r.get("zone") == zone]


def _all_zones():
    """Все зоны, у которых есть комнаты в карте мира (детерминированно)."""
    seen = []
    for r in WORLD.values():
        z = r.get("zone")
        if z and z not in seen:
            seen.append(z)
    return seen


def _zones_for(spec):
    """Развернуть allowed_zones-спецификацию в конкретный список зон.

    spec: None | "any" | "city" | "wild" | [зона, ...]. Возвращает только зоны,
    реально присутствующие в карте мира (чтобы спавн/эффект имели смысл)."""
    world_zones = _all_zones()
    if spec is None or spec == "any":
        return list(world_zones)
    if spec == "city":
        return [z for z in world_zones if z in _CITY_ZONES]
    if spec == "wild":
        return [z for z in world_zones if z not in _CITY_ZONES]
    if isinstance(spec, (list, tuple)):
        return [z for z in spec if z in world_zones]
    if isinstance(spec, str):
        return [spec] if spec in world_zones else []
    return []


def zone_allowed(d, zone):
    """Разрешена ли зона zone для события d по его allowed_zones.

    zone=None (глобальный эффект) разрешён только если событие само глобально
    (zone: null в yaml) или allowed_zones включает "any"/не задан."""
    spec = d.get("allowed_zones")
    if zone is None:
        # глобальный эффект уместен для событий без фиксированной зоны и без
        # ограничения на конкретные зоны
        return d.get("zone") is None and (spec is None or spec == "any")
    return zone in _zones_for(spec)


def _resolve_zone(d, zone, rng):
    """Определить зону старта: явную (если валидна) или случайную из allowed.

    -> (zone|None, reason|None). reason!=None означает провал подбора."""
    # Событие с жёстко зафиксированной зоной в yaml (старый формат) — она главная.
    fixed = d.get("zone")
    spec = d.get("allowed_zones")

    # Явно переданная зона: принимаем, только если проходит allowed_zones.
    if zone is not None:
        if zone_allowed(d, zone):
            return zone, None
        # невалидна — попробуем подобрать случайную валидную ниже (движок-арбитр)

    # Событие глобальное (zone: null) и без списка зон → эффект глобальный.
    if fixed is None and (spec is None or spec == "any"):
        return None, None

    # Есть фиксированная зона в yaml — используем её.
    if fixed is not None:
        return fixed, None

    # Иначе подобрать случайную из allowed_zones.
    pool = _zones_for(spec)
    if not pool:
        return None, "нет подходящих зон для события"
    return rng.choice(pool), None


def _clamp_duration(d, duration):
    """Клампнуть запрошенную длительность в границы события [min, max].

    Границы: duration_min/duration_max, если заданы; иначе — фиксированный
    duration (старый формат). Если ничего не задано — 1800с по умолчанию.
    duration=None → взять центр диапазона (или duration/дефолт)."""
    dmin = d.get("duration_min")
    dmax = d.get("duration_max")
    fixed = d.get("duration")
    if dmin is None and dmax is None:
        # старый формат: единственное значение duration — оно же и границы
        base = int(fixed if fixed is not None else 1800)
        lo = hi = base
    else:
        lo = int(dmin if dmin is not None else (fixed if fixed is not None else 1800))
        hi = int(dmax if dmax is not None else (fixed if fixed is not None else lo))
    if lo > hi:
        lo, hi = hi, lo
    if duration is None:
        # без запроса: центр диапазона (для нового формата) или fixed/база
        if fixed is not None and lo <= int(fixed) <= hi:
            return int(fixed)
        return (lo + hi) // 2
    try:
        val = int(duration)
    except (TypeError, ValueError):
        return (lo + hi) // 2
    return max(lo, min(hi, val))


def modifiers(zone):
    """Множители xp/gold/loot от активных событий для данной зоны (None=глобально)."""
    xp = gold = loot = 1.0
    for e in _active:
        d = e["def"]
        # действующая зона события: сохранённая при старте (start/_start), иначе —
        # из yaml (совместимость со старым _start, где зона бралась из d["zone"]).
        ez = e.get("zone", d.get("zone"))
        if ez is None or ez == zone:
            xp *= d.get("xp_mult", 1.0)
            gold *= d.get("gold_mult", 1.0)
            loot *= d.get("loot_mult", 1.0)
    return {"xp": xp, "gold": gold, "loot": loot}


def _spawn_invasion(world, d, zone, now, rng, ev):
    """Заспавнить вторженцев/мигрантов события в комнатах зоны (мутирует ev)."""
    mob = d.get("mob")
    if mob not in MOBS:
        return
    rooms = _rooms_of_zone(zone if zone is not None else d.get("zone"))
    if not rooms:
        return
    rng.shuffle(rooms)
    rooms = rooms[:int(d.get("rooms_max", 3))]
    # Общее число зверей: новый формат count_min/count_max (миграция 3–5),
    # иначе старый count на комнату.
    cmin = d.get("count_min")
    cmax = d.get("count_max")
    if cmin is not None or cmax is not None:
        lo = int(cmin if cmin is not None else 1)
        hi = int(cmax if cmax is not None else lo)
        if lo > hi:
            lo, hi = hi, lo
        total = rng.randint(lo, hi)
        # распределяем total зверей по доступным комнатам по кругу
        i = 0
        placed = 0
        while placed < total and rooms:
            rid = rooms[i % len(rooms)]
            inst = world.spawn_mob(rid, mob)
            if inst:
                ev["spawned"].append((rid, inst))
                placed += 1
            i += 1
            if i > total * 4 + len(rooms):   # страховка от бесконечного цикла
                break
    else:
        per_room = int(d.get("count", 1))
        for rid in rooms:
            for _ in range(per_room):
                inst = world.spawn_mob(rid, mob)
                if inst:
                    ev["spawned"].append((rid, inst))


def _announce(d):
    return f"🌐 *{d.get('name', 'Событие')}!* {d.get('desc', '')}"


def _start(world, eid, d, now, rng, zone="__default__"):
    """Низкоуровневый запуск: создать активное событие и (для invasion) спавн.

    zone="__default__" — обратная совместимость: берём d["zone"] (старый вызов
    events._start(w, eid, d, now, rng) в тестах). Иначе — использовать переданную
    (уже разрешённую) зону. Возвращает строку-анонс, добавляет запись в _active."""
    zval = d.get("zone") if zone == "__default__" else zone
    ev = {"id": eid, "def": d, "zone": zval,
          "ends_at": now + int(d.get("duration", 1800)), "spawned": []}
    if d.get("type") == "invasion":
        _spawn_invasion(world, d, zval, now, rng, ev)
    _active.append(ev)
    return _announce(d)


def start(eid, zone=None, duration=None, world=None, now=None, rng=None):
    """Прямой запуск события богом/движком. Возвращает (list[str], reason|None).

    Валидация (движок — арбитр):
      • eid существует в каталоге, иначе ([], "нет такого события");
      • MAX_ACTIVE соблюдён, иначе ([], "занято");
      • zone ∈ allowed_zones (или подобрать случайную валидную);
      • duration клампится в границы события.
    Успех → ([анонс], None). world нужен для спавна вторженцев (invasion/миграция);
    если world=None и событие со спавном — стартует без мобов (только эффект)."""
    global _last_check
    now = now or time.time()
    rng = rng or random
    if not _DEFS:
        return [], "каталог событий пуст"
    if eid not in _DEFS:
        return [], f"нет такого события: {eid}"
    if len(_active) >= MAX_ACTIVE:
        return [], "достигнут лимит активных событий"
    d = _DEFS[eid]
    zresolved, reason = _resolve_zone(d, zone, rng)
    if reason:
        return [], reason
    dur = _clamp_duration(d, duration)
    ev = {"id": eid, "def": d, "zone": zresolved, "ends_at": now + dur, "spawned": []}
    if d.get("type") == "invasion" and world is not None:
        _spawn_invasion(world, d, zresolved, now, rng, ev)
    _active.append(ev)
    _last_check = now
    return [_announce(d)], None


def maybe_start(world, now=None, rng=None):
    """Возможно запустить случайное событие по таймеру. -> список сообщений."""
    global _last_check
    now = now or time.time()
    rng = rng or random
    if not ENABLED or not _DEFS or len(_active) >= MAX_ACTIVE:
        return []
    if now - _last_check < CHECK_EVERY:
        return []
    _last_check = now
    if rng.random() > START_CHANCE:
        return []
    eid = rng.choice(list(_DEFS))
    d = _DEFS[eid]
    # переиспользуем общую логику подбора зоны/кламп-длительности через start(),
    # но start() проверяет MAX_ACTIVE/last_check заново — здесь они уже пройдены,
    # поэтому зовём напрямую через _resolve_zone + _start для той же семантики.
    zresolved, reason = _resolve_zone(d, None, rng)
    if reason:
        return []
    return [_start(world, eid, d, now, rng, zone=zresolved)]


def tick(world, now=None):
    """Закрыть истёкшие события (деспавн вторженцев). -> список сообщений."""
    now = now or time.time()
    msgs = []
    for e in list(_active):
        if now >= e["ends_at"]:
            for rid, inst in e["spawned"]:
                lst = world.mobs.get(rid, [])
                if inst in lst:
                    lst.remove(inst)
            _active.remove(e)
            msgs.append(f"🌐 Событие «{e['def'].get('name', '?')}» завершилось.")
    return msgs


def render():
    if not _active:
        return "🌐 Сейчас активных мировых событий нет."
    now = time.time()
    L = ["🌐 *Активные мировые события:*", ""]
    for e in _active:
        d = e["def"]
        left = max(0, int(e["ends_at"] - now)) // 60
        mods = []
        if d.get("xp_mult", 1) != 1:
            mods.append(f"опыт ×{d['xp_mult']}")
        if d.get("gold_mult", 1) != 1:
            mods.append(f"золото ×{d['gold_mult']}")
        if d.get("loot_mult", 1) != 1:
            mods.append(f"добыча ×{d['loot_mult']}")
        zone = e.get("zone", d.get("zone")) or "везде"
        L.append(f"• *{d.get('name')}* ({zone}) — {d.get('desc', '')}")
        L.append(f"  {', '.join(mods) or '—'} · ещё ~{left} мин")
    return "\n".join(L)


def banner():
    """Короткая строка-баннер для экрана комнаты (или '')."""
    if not _active:
        return ""
    names = ", ".join(e["def"].get("name", "?") for e in _active)
    return f"🌐 _Идёт событие: {names}_"
