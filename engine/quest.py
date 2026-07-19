# -*- coding: utf-8 -*-
"""
Логика квестов. Состояние хранится в Character.quests:
    quests[qid] = "active" | "done"

Прогресс целей (все — строки-числа/флаги для JSONB-совместимости, ключ содержит
двоеточие, поэтому циклы по «настоящим» квестам его пропускают: `":" in qid`):
    quests[qid + ":kills"] = "<N>"   убийства (kill)
    quests[qid + ":talk"]  = "1"     разговор состоялся (talk)
    quests[qid + ":reach"] = "1"     комната достигнута (reach)
    quests[qid + ":use"]   = "1"     предмет использован (use)
Для collect прогресс не хранится — считается по инвентарю.
Для choose выбор хранится в ch.flags["quest_choices"][qid] = <option_id>.

── ФОРМАТ КВЕСТА (контракт для контента, этап 5.2) ──────────────────────────
    objective:
      { type: kill,    mob: <mob_id>,  count: <N> }
      { type: collect, item: <item_id>, count: <N> }
      { type: talk,    npc: <npc_id> }
      { type: reach,   room: <room_id> }
      { type: use,     item: <item_id> }
      { type: choose,  options: [ {id, label, text}, ... ] }
    requires: <qid>            выполнить раньше (уже было; unlocks — обратная
                               документирующая связь, отдельного поля не нужно)
    exclusive_group: <строка>  приняв квест группы, прочие члены группы навсегда
                               недоступны (пишется в ch.flags["quest_locks"])
    locks: [<qid>, ...]        сдача квеста навсегда блокирует эти квесты
    on_complete:               применяется при сдаче (turn_in):
      flags: { key: val, ... }         выставить ch.flags[key]=val
      reputation: { faction: delta }   применить через engine.reputation
      chronicle: <строка|null>         запись в хронику ("{name}" → имя игрока)
"""
from typing import List, Tuple, Optional, Dict

from .content import QUESTS, MOBS, ITEMS, NPCS, WORLD
from . import money
from .character import Character


# ───────────────────────── блокировки/группы ─────────────────────────
def _locks(ch: Character) -> list:
    """Список навсегда заблокированных квестов (последствия/эксклюзив-группы)."""
    return ch.flags.setdefault("quest_locks", [])


def is_locked(ch: Character, qid: str) -> bool:
    return qid in (ch.flags.get("quest_locks") or [])


def _lock(ch: Character, qid: str) -> None:
    locks = _locks(ch)
    if qid not in locks:
        locks.append(qid)


def _group_members(group: str) -> List[str]:
    return [qid for qid, q in QUESTS.items() if q.get("exclusive_group") == group]


def _apply_exclusive(ch: Character, qid: str) -> None:
    """Приняв/сдав квест эксклюзивной группы — навсегда закрыть прочих её членов."""
    group = QUESTS.get(qid, {}).get("exclusive_group")
    if not group:
        return
    for other in _group_members(group):
        if other != qid:
            _lock(ch, other)


# ───────────────────────── выбор (choose) ─────────────────────────
def choose_options(qid: str) -> list:
    q = QUESTS.get(qid) or {}
    obj = q.get("objective", {})
    if obj.get("type") == "choose":
        return obj.get("options", []) or []
    return []


def choose_option(qid: str, opt_id: str) -> Optional[dict]:
    for o in choose_options(qid):
        if o.get("id") == opt_id:
            return o
    return None


def choice_made(ch: Character, qid: str) -> Optional[str]:
    """Сделанный выбор (option_id) для choose-квеста или None."""
    return (ch.flags.get("quest_choices") or {}).get(qid)


def pending_choices(ch: Character, npc: str) -> List[Tuple[str, list]]:
    """Активные choose-квесты, выданные этим NPC, по которым выбор ещё не сделан.
    Возвращает [(qid, options), ...] — рендерер строит кнопки."""
    out = []
    for qid, st in ch.quests.items():
        if st != "active" or ":" in qid:
            continue
        q = QUESTS.get(qid)
        if not q:
            continue
        obj = q["objective"]
        if obj.get("type") == "choose" and q.get("giver") == npc and choice_made(ch, qid) is None:
            out.append((qid, obj.get("options", []) or []))
    return out


def on_choose(ch: Character, qid: str, opt_id: str) -> Tuple[bool, str]:
    """Зафиксировать выбор игрока в choose-квесте и завершить цель.
    Двухшаговое подтверждение — на стороне бота; движок только фиксирует итог."""
    q = QUESTS.get(qid)
    if not q or q.get("objective", {}).get("type") != "choose":
        return False, "Здесь нечего выбирать."
    if ch.quests.get(qid) != "active":
        return False, "Задание не активно."
    if choice_made(ch, qid) is not None:
        return False, "Выбор уже сделан — второго не дано."
    opt = choose_option(qid, opt_id)
    if not opt:
        return False, "Нет такого варианта."
    ch.flags.setdefault("quest_choices", {})[qid] = opt_id
    body = (opt.get("text") or "").strip() or f"Ты выбрал: {opt.get('label', opt_id)}."
    tail = f"\n✅ «{q['name']}»: выбор сделан — вернись к {q['turn_in'].replace('_', ' ')}."
    return True, "🔀 " + body + tail


# ───────────────────────── collect-токены (как было) ─────────────────────────
def _token_quests(item_id: str) -> List[str]:
    """Квесты, требующие собрать этот предмет (collect-цель)."""
    out = []
    for qid, q in QUESTS.items():
        o = q.get("objective", {})
        if o.get("type") == "collect" and o.get("item") == item_id:
            out.append(qid)
    return out


def needs_token(ch: Character, item_id: str) -> bool:
    """
    Нужен ли игроку этот квест-предмет (для дропа «один раз на игрока»).
    Квест-токен (type=quest) падает, только если игрок ещё не держит его и
    связанный квест НЕ сдан. Обычные предметы — всегда True.
    """
    if ITEMS.get(item_id, {}).get("type") != "quest":
        return True
    if item_id in ch.inventory:
        return False
    qs = _token_quests(item_id)
    if not qs:
        return True                      # токен без квеста — обычный дроп
    return any(ch.quests.get(qid) != "done" for qid in qs)


# ───────────────────────── доступность/сдача ─────────────────────────
def available_quests(ch: Character, npc: str) -> List[str]:
    """Квесты, которые этот NPC может выдать игроку сейчас."""
    out = []
    for qid, q in QUESTS.items():
        if q.get("giver") != npc:
            continue
        status = ch.quests.get(qid)
        if status in ("active", "done"):
            continue
        # последствия: квест навсегда закрыт (locks/эксклюзив-группа)
        if is_locked(ch, qid):
            continue
        # эксклюзив-группа: если другой её член уже взят/сдан — этот недоступен
        group = q.get("exclusive_group")
        if group and any(o != qid and ch.quests.get(o) in ("active", "done")
                         for o in _group_members(group)):
            continue
        req = q.get("requires")
        if req and ch.quests.get(req) != "done":
            continue
        if q.get("min_level") and ch.level < int(q["min_level"]):
            continue
        out.append(qid)
    return out


def turn_in_quests(ch: Character, npc: str) -> List[str]:
    """Квесты, которые можно сдать этому NPC прямо сейчас (цель выполнена)."""
    out = []
    for qid, q in QUESTS.items():
        if q.get("turn_in") != npc:
            continue
        if ch.quests.get(qid) != "active":
            continue
        if is_complete(ch, qid):
            out.append(qid)
    return out


def accept(ch: Character, qid: str) -> Tuple[bool, str]:
    if qid not in QUESTS:
        return False, "Нет такого задания."
    if ch.quests.get(qid) in ("active", "done"):
        return False, "Задание уже взято."
    if is_locked(ch, qid):
        return False, "Этот путь для тебя закрыт."
    ch.quests[qid] = "active"
    q = QUESTS[qid]
    obj = q["objective"]
    if obj["type"] == "kill":
        ch.quests[qid + ":kills"] = "0"
    # эксклюзив-группа: приняв квест группы, прочие её члены закрываются навсегда
    _apply_exclusive(ch, qid)
    return True, f"📜 Принято задание: *{q['name']}*\n{q['desc'].strip()}"


def is_complete(ch: Character, qid: str) -> bool:
    q = QUESTS[qid]
    obj = q["objective"]
    t = obj["type"]
    if t == "kill":
        return int(ch.quests.get(qid + ":kills", "0")) >= obj["count"]
    if t == "collect":
        return ch.inventory.count(obj["item"]) >= obj["count"]
    if t == "talk":
        return ch.quests.get(qid + ":talk") == "1"
    if t == "reach":
        return ch.quests.get(qid + ":reach") == "1"
    if t == "use":
        return ch.quests.get(qid + ":use") == "1"
    if t == "choose":
        return choice_made(ch, qid) is not None
    return False


# ───────────────────────── хуки прогресса ─────────────────────────
def on_kill(ch: Character, mob_id: str) -> List[str]:
    """Вызывается при убийстве моба — обновляет счётчики kill-квестов."""
    msgs = []
    for qid, status in list(ch.quests.items()):
        if status != "active" or ":" in qid:
            continue
        q = QUESTS.get(qid)
        if not q:
            continue
        obj = q["objective"]
        if obj["type"] == "kill" and obj["mob"] == mob_id:
            key = qid + ":kills"
            kills = int(ch.quests.get(key, "0")) + 1
            ch.quests[key] = str(kills)
            need = obj["count"]
            if kills >= need:
                msgs.append(f"✅ Задание «{q['name']}»: цель выполнена! Вернись к "
                            f"{q['turn_in'].replace('_',' ')}.")
            else:
                msgs.append(f"📜 «{q['name']}»: {kills}/{need}")
    return msgs


def _flag_hook(ch: Character, otype: str, field: str, value) -> List[str]:
    """Общий хук для однократных целей talk/reach/use: помечает цель выполненной.
    Идемпотентен — повторный вызов уже засчитанной цели ничего не делает."""
    msgs = []
    for qid, status in list(ch.quests.items()):
        if status != "active" or ":" in qid:
            continue
        q = QUESTS.get(qid)
        if not q:
            continue
        obj = q["objective"]
        if obj.get("type") == otype and obj.get(field) == value:
            key = f"{qid}:{otype}"
            if ch.quests.get(key) == "1":
                continue
            ch.quests[key] = "1"
            msgs.append(f"✅ Задание «{q['name']}»: цель выполнена! Вернись к "
                        f"{q['turn_in'].replace('_',' ')}.")
    return msgs


def on_talk(ch: Character, npc_id: str) -> List[str]:
    """Разговор с NPC — засчитывает talk-цели (бот зовёт из talk-колбэка)."""
    return _flag_hook(ch, "talk", "npc", npc_id)


def on_enter_room(ch: Character, room_id: str) -> List[str]:
    """Вход в комнату — засчитывает reach-цели (бот зовёт из do_move/enter_room)."""
    return _flag_hook(ch, "reach", "room", room_id)


def on_use_item(ch: Character, item_id: str) -> List[str]:
    """Использование предмета — засчитывает use-цели (бот зовёт из do_use).
    Расход предмета — забота бота (обычные правила расходников); здесь только
    отметка выполнения, нерасходуемые предметы не тратятся."""
    return _flag_hook(ch, "use", "item", item_id)


# ───────────────────────── сдача и последствия ─────────────────────────
def _apply_consequences(ch: Character, qid: str) -> None:
    """Применить on_complete/locks/эксклюзив-группу при сдаче квеста."""
    q = QUESTS[qid]
    oc = q.get("on_complete") or {}
    # флаги
    for k, v in (oc.get("flags") or {}).items():
        ch.flags[k] = v
    # репутация фракций
    rep = oc.get("reputation") or {}
    if rep:
        from . import reputation as _rep
        for fac, delta in rep.items():
            _rep.gain(ch, fac, int(delta))
    # хроника мира (с именем игрока через textsafe.esc_md)
    ctext = oc.get("chronicle")
    if ctext:
        from . import chronicle as _chr, textsafe as _ts
        name = _ts.esc_md(getattr(ch, "name", "") or "Странник")
        text = ctext.replace("{name}", name) if "{name}" in ctext else f"{name}: {ctext}"
        _chr.record("quest", text)
    # явные блокировки прочих квестов
    for lq in (q.get("locks") or []):
        _lock(ch, lq)
    # закрепить эксклюзив-группу (на случай, если приём не записал)
    _apply_exclusive(ch, qid)


def complete(ch: Character, qid: str) -> Tuple[bool, str]:
    """Сдать квест: проверить, забрать предметы, выдать награду, применить последствия."""
    if ch.quests.get(qid) != "active":
        return False, "Задание не активно."
    if not is_complete(ch, qid):
        return False, "Цель ещё не выполнена."
    q = QUESTS[qid]
    obj = q["objective"]
    # забрать собранные предметы
    if obj["type"] == "collect":
        for _ in range(obj["count"]):
            if obj["item"] in ch.inventory:
                ch.inventory.remove(obj["item"])
    # награда
    rw = q.get("reward", {})
    ch.xp += int(rw.get("xp", 0) * ch.xp_mult)
    ch.gold += int(rw.get("gold", 0) * ch.gold_mult)
    got = []
    for it in rw.get("items", []):
        ch.inventory.append(it)
        got.append(ITEMS[it]["name"])
    ch.quests[qid] = "done"
    # очистить служебные ключи прогресса
    for suf in (":kills", ":talk", ":reach", ":use"):
        ch.quests.pop(qid + suf, None)
    # последствия (флаги/репутация/хроника/блокировки/эксклюзив-группа)
    _apply_consequences(ch, qid)
    line = f"🎉 Задание «{q['name']}» выполнено! +{rw.get('xp',0)} опыта, +{money.fmt(rw.get('gold',0))}."
    if got:
        line += "\n🎁 Награда: " + ", ".join(got)
    return True, line


# ───────────────────────── отрисовка целей ─────────────────────────
def _goal_text(ch: Character, qid: str, q: dict) -> str:
    """Строка цели квеста для журнала/трекера (все типы целей)."""
    obj = q["objective"]
    t = obj["type"]
    if t == "kill":
        cnt = int(ch.quests.get(qid + ":kills", "0"))
        nm = MOBS.get(obj["mob"], {}).get("name", obj["mob"])
        return f"🎯 Убить: {nm} — {cnt}/{obj['count']}"
    if t == "collect":
        have = ch.inventory.count(obj["item"])
        nm = ITEMS.get(obj["item"], {}).get("name", obj["item"])
        return f"🎯 Собрать: {nm} — {have}/{obj['count']}"
    if t == "talk":
        nm = NPCS.get(obj["npc"], {}).get("name", obj["npc"])
        mark = "✔️" if ch.quests.get(qid + ":talk") == "1" else "…"
        return f"🎯 Поговорить: {nm} {mark}"
    if t == "reach":
        nm = WORLD.get(obj["room"], {}).get("name", obj["room"])
        mark = "✔️" if ch.quests.get(qid + ":reach") == "1" else "…"
        return f"🎯 Дойти: {nm} {mark}"
    if t == "use":
        nm = ITEMS.get(obj["item"], {}).get("name", obj["item"])
        mark = "✔️" if ch.quests.get(qid + ":use") == "1" else "…"
        return f"🎯 Использовать: {nm} {mark}"
    if t == "choose":
        made = choice_made(ch, qid)
        if made:
            opt = choose_option(qid, made)
            return f"🎯 Выбор сделан: {opt.get('label', made) if opt else made}"
        return f"🎯 Сделать выбор (у {q['turn_in'].replace('_', ' ')})"
    return "🎯 Цель"


def journal(ch: Character) -> str:
    active = [qid for qid, s in ch.quests.items() if s == "active" and ":" not in qid]
    done = [qid for qid, s in ch.quests.items() if s == "done"]
    L = ["📖 *Журнал заданий:*", ""]
    for qid in active:
        q = QUESTS.get(qid)
        if not q:
            continue
        goal = _goal_text(ch, qid, q)
        hint = _path_hint(ch, qid)
        mark = "✅ Готово к сдаче" if is_complete(ch, qid) else "⏳ В процессе"
        desc = (q.get("desc") or "").strip()
        L.append(f"*{q['name']}*  —  {mark}")
        if desc:
            L.append(f"_{desc}_")
        L.append(goal)
        if hint and not is_complete(ch, qid):
            L.append(hint)
        rew = q.get("reward", {})
        rparts = []
        if rew.get("xp"): rparts.append(f"{rew['xp']} опыта")
        if rew.get("gold"): rparts.append(f"💰{money.fmt(rew['gold'])}")
        if rew.get("items"): rparts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
        if rparts:
            L.append("🎁 Награда: " + ", ".join(rparts))
        L.append("")
    # ежедневное задание — тоже в журнале
    try:
        from . import daily as _daily
        if _daily.DAILY:
            d = _daily.ensure(ch)
            dq = _daily.DAILY.get(d["id"])
            if dq:
                mob_name = MOBS.get(dq.get("mob", ""), {}).get("name", dq.get("mob", ""))
                prog = f"{d.get('progress', 0)}/{dq['count']}"
                if d.get("claimed"):
                    mark = "🔁 Награда получена сегодня"
                elif _daily.is_complete(ch):
                    mark = "✅ Готово — заберите у наставника"
                else:
                    mark = "⏳ В процессе"
                L.append(f"📅 *Задание дня: {dq['name']}*  —  {mark}")
                L.append(f"🎯 Убить: {mob_name} — {prog}")
                L.append("")
    except Exception:
        pass
    if done:
        L.append("— — —")
        for qid in done:
            q = QUESTS.get(qid)
            if q:
                L.append(f"✔️ ~{q['name']}~ (выполнено)")
    if len(L) <= 2:
        return "📖 Журнал заданий пуст. Поговорите с NPC (💬), чтобы получить квесты."
    return "\n".join(L)


def active_brief(ch):
    """Краткий трекер активных квестов для показа в комнате (до 3)."""
    out = []
    for qid, st in ch.quests.items():
        if st != "active" or ":" in qid:
            continue
        q = QUESTS.get(qid)
        if not q:
            continue
        out.append(f"{_goal_text(ch, qid, q).replace('🎯 ', '🎯 ' + q['name'] + ': ', 1)}")
    return out[:3]


def _path_hint(ch, qid):
    """Подсказка маршрута к цели квеста (автопуть)."""
    from . import nav
    q = QUESTS.get(qid)
    if not q:
        return None
    obj = q["objective"]
    t = obj["type"]
    if t == "kill":
        p = nav.path_to_mob(ch.room, obj["mob"])
    elif t == "collect":
        p = nav.path_to_item(ch.room, obj["item"])
    elif t == "reach":
        p = nav.bfs_path(ch.room, lambda r, _rid=obj["room"]: r == _rid)
    elif t == "talk":
        p = nav.bfs_path(ch.room, lambda r, _n=obj["npc"]: _n in WORLD.get(r, {}).get("npc", []))
    else:
        return None                       # use/choose — маршрут не нужен
    if p is None:
        return None
    if not p:
        return "🧭 Цель в этой комнате."
    return "🧭 Путь: " + " → ".join(p[:8])
