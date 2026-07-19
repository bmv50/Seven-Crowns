# -*- coding: utf-8 -*-
"""
Бестиарий: счётчик убийств по видам мобов (ch.flags["bestiary"] = {mob_id: count}).
По мере истребления вида растёт пассивный бонус урона по нему (как «слаер»).

Коллекции (data/collections.yaml): набор видов мобов, которых нужно истребить
хотя бы по разу каждого. Прогресс считается по тем же счётчикам ch.flags
["bestiary"]. Собранные коллекции запоминаются в ch.flags["collections_done"]
(разовая награда — навсегда; повторный сбор ничего не даёт). Титул коллекции
кладётся в ch.flags["extra_titles"] — доступен в выборе титула наравне
с титулами достижений (engine/achievements.py).
"""
from .content import MOBS, ITEMS
from . import content, money
from . import chronicle

COLLECTIONS = content._load_optional("collections.yaml")


def kills(ch, mob_id: str) -> int:
    return int((ch.flags.get("bestiary") or {}).get(mob_id, 0))


def _reward_str(rew: dict, title: str = None) -> str:
    parts = []
    if rew.get("gold"):
        parts.append(money.fmt(rew["gold"]))
    if rew.get("items"):
        parts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
    if title:
        parts.append(f"титул «{title}»")
    return ", ".join(parts)


def _check_collections(ch, mob_id: str) -> list:
    """После убийства мобa проверить коллекции с его участием на полный сбор.
    -> список строк о только что собранных коллекциях (обычно пустой)."""
    out = []
    done = ch.flags.setdefault("collections_done", [])
    for cid, col in COLLECTIONS.items():
        if cid in done or mob_id not in col.get("mobs", []):
            continue
        if not all(kills(ch, m) >= 1 for m in col["mobs"]):
            continue
        done.append(cid)
        rew = col.get("reward", {})
        ch.gold += rew.get("gold", 0)
        for it in rew.get("items", []):
            ch.inventory.append(it)
        title = col.get("title")
        if title:
            ch.flags.setdefault("extra_titles", []).append(title)
            chronicle.record("collection",
                             f"{getattr(ch, 'name', 'Игрок')} собрал коллекцию «{col['name']}»")
        out.append(f"📖 Коллекция «{col['name']}» собрана! Награда: {_reward_str(rew, title)}.")
    return out


def record_kill(ch, mob_id: str) -> list:
    """Записать убийство вида; вернуть список строк о собранных коллекциях
    (обычно пустой список — коллекция закрывается редко)."""
    b = ch.flags.setdefault("bestiary", {})
    b[mob_id] = int(b.get(mob_id, 0)) + 1
    return _check_collections(ch, mob_id)


def _bonus_by(k: int) -> float:
    if k >= 100:
        return 0.15
    if k >= 50:
        return 0.10
    if k >= 10:
        return 0.05
    return 0.0


def bonus(ch, mob_id: str) -> float:
    return _bonus_by(kills(ch, mob_id))


def _collections_lines(ch) -> list:
    """Прогресс по коллекциям: x/y, ✅ у собранных. Пустой список, если коллекций нет."""
    if not COLLECTIONS:
        return []
    done = set(ch.flags.get("collections_done", []))
    L = ["", "📚 *Коллекции:*"]
    for cid, col in COLLECTIONS.items():
        total = len(col["mobs"])
        cur = sum(1 for m in col["mobs"] if kills(ch, m) >= 1)
        status = "✅ собрана" if cid in done else f"{cur}/{total}"
        L.append(f"{col['name']}: {status}")
    return L


def render(ch) -> str:
    b = ch.flags.get("bestiary") or {}
    L = [f"📖 *Бестиарий* — изучено видов: {len(b)}", ""]
    if not b:
        L.append("_Пока пусто. Сражайтесь с врагами, чтобы изучать их слабости._")
    else:
        for mid, cnt in sorted(b.items(), key=lambda x: -x[1])[:25]:
            meta = MOBS.get(mid, {})
            bn = int(_bonus_by(cnt) * 100)
            tag = f" — +{bn}% урона" if bn else ""
            L.append(f"{meta.get('emoji','•')} {meta.get('name', mid)}: убито {cnt}{tag}")
        L.append("\n_Бонусы: 10 убийств → +5%, 50 → +10%, 100 → +15% урона по виду._")
    L.extend(_collections_lines(ch))
    return "\n".join(L)
