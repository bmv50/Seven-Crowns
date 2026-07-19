# -*- coding: utf-8 -*-
"""Подземелья-инстансы: цепочка комнат с мини-боссом.
Вход с кулдауном и уровневым порогом, при входе спавнится босс, за его
убийство — разовая бонус-награда (раз за забег). Хранение в ch.flags:
  dungeon_cd  = {did: unix_ts_последнего_входа}
  dungeon_run = did | None   (активный незавершённый забег)
"""
import time
from . import content
from . import weekly

DUNGEONS = content._load_optional("dungeons.yaml") or {}


def find_by_entrance(room: str):
    for did, cfg in DUNGEONS.items():
        if cfg.get("entrance_room") == room:
            return did, cfg
    return None


def cooldown_left(ch, did: str) -> int:
    cd = (ch.flags.get("dungeon_cd") or {}).get(did, 0)
    interval = DUNGEONS.get(did, {}).get("cooldown", 0)
    left = int(cd + interval - time.time())
    return max(0, left)


def can_enter(ch, did: str):
    cfg = DUNGEONS.get(did)
    if not cfg:
        return False, "Подземелье не найдено."
    if ch.level < cfg.get("min_level", 1):
        return False, f"Нужен {cfg['min_level']} уровень."
    left = cooldown_left(ch, did)
    if left > 0:
        return False, f"Кулдаун: ещё {left // 60} мин {left % 60} сек."
    return True, ""


def ready_at(did: str) -> float:
    """Момент (unix), когда кулдаун данжа истечёт для только что вошедшего.
    Для push-уведомления «данж снова доступен». 0 — если кулдауна нет."""
    interval = DUNGEONS.get(did, {}).get("cooldown", 0)
    return time.time() + interval if interval else 0.0


def enter(ch, did: str, world):
    """Запустить забег: телепорт в стартовую комнату, спавн босса. Возвращает строки."""
    cfg = DUNGEONS[did]
    ch.flags.setdefault("dungeon_cd", {})[did] = time.time()
    ch.flags["dungeon_run"] = did
    ch.room = cfg["start_room"]
    # заспавнить босса, если его ещё нет
    if not world.find_by_mob_id(cfg["boss_room"], cfg["boss_mob"]):
        world.spawn_mob(cfg["boss_room"], cfg["boss_mob"])
    return [f"⚔️ Вы вступаете в подземелье «{cfg['name']}». "
            f"Мини-босс ждёт в глубине. Удачи!"]


def on_kill(ch, mob_id: str, group: bool = False):
    """Если убит босс активного забега — выдать бонус-награду (разово). Строки.
    group=True — забег завершён в группе (≥2 killers у loop.on_mob_death):
    засчитывает недельную цель dungeon_group (Этап 6.1)."""
    did = ch.flags.get("dungeon_run")
    if not did:
        return []
    cfg = DUNGEONS.get(did)
    if not cfg or cfg.get("boss_mob") != mob_id:
        return []
    ch.flags["dungeon_run"] = None
    rw = cfg.get("reward", {})
    # престиж: за каждый реморт владельца забега +20% к golda/xp награды босса
    _prestige_mult = 1 + 0.2 * ch.remort_count
    g = int(rw.get("gold", 0) * _prestige_mult)
    xp = int(rw.get("xp", 0) * _prestige_mult)
    ch.gold += g
    ch.xp += xp
    out = [f"🏰 *Подземелье «{cfg['name']}» пройдено!* Бонус: +{xp} опыта, +{g} бронзы."]
    item = rw.get("item")
    if item:
        ch.inventory.append(item)
        out.append(f"🎁 Награда-предмет добавлена в сумку.")
    if group:
        _wl = weekly.on_dungeon_group(ch)
        if _wl:
            out.append(_wl)
    return out
