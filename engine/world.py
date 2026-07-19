# -*- coding: utf-8 -*-
"""
Рантайм-состояние мира: живые мобы в комнатах, их HP, таймеры респавна,
агрессия (кого моб бьёт). Это состояние живёт в памяти процесса.
"""
import time
import random
from typing import Dict, List, Optional

from .content import (WORLD, MOBS, HP_SCALE, RESPAWN_SCALE,
                      STARTER_MAX_LEVEL, STARTER_RESPAWN_SCALE,
                      ROAM_INTERVAL, ROAM_CHANCE, ROAM_CAP, ROAM_MAX_LEVEL)


class MobInstance:
    """Конкретный экземпляр моба в конкретной комнате."""
    __slots__ = ("key", "mob_id", "room", "home", "hp", "max_hp", "last_tick",
                 "aggro", "effects", "dead_at", "threat", "_ai", "exploited_by")

    def __init__(self, key: str, mob_id: str, room: str):
        self.key = key                 # уникальный: "room:mob_id:index"
        self.mob_id = mob_id
        self.room = room
        self.home = room               # родная комната (куда вернуться при респавне)
        m = MOBS[mob_id]
        self.hp = m["hp"] * HP_SCALE
        self.max_hp = m["hp"] * HP_SCALE
        self.last_tick = time.time()
        self.aggro: List[int] = []     # uid игроков, кого моб атакует
        self.effects: List[dict] = []  # яды и т.п. на мобе
        self.dead_at: Optional[float] = None
        self.threat: Dict[int, float] = {}   # uid -> накопленная угроза
        # uid игроков, попавших по этому мобу типом урона из его vuln (rules2)
        # хотя бы раз за его жизнь — недельная цель dtype_kill (Этап 6.1).
        self.exploited_by: set = set()

    def add_threat(self, uid: int, amount: float):
        if uid not in self.aggro:
            self.aggro.append(uid)
        self.threat[uid] = self.threat.get(uid, 0.0) + max(0.0, amount)

    def top_threat(self, valid_uids):
        """Вернуть uid с наибольшей угрозой среди valid_uids, либо None."""
        cand = [(u, self.threat.get(u, 0.0)) for u in valid_uids]
        cand = [c for c in cand if c[1] > 0]
        if not cand:
            return None
        return max(cand, key=lambda x: x[1])[0]

    @property
    def meta(self) -> dict:
        return MOBS[self.mob_id]

    @property
    def alive(self) -> bool:
        return self.dead_at is None


class World:
    """Управляет живыми мобами и респавном."""
    def __init__(self):
        # room -> list[MobInstance]
        self.mobs: Dict[str, List[MobInstance]] = {}
        # трупы: room -> list[{key,mob_id,name,emoji,loot,dead_at}]
        self.corpses: Dict[str, List[dict]] = {}
        self._corpse_seq = 0
        self._last_roam = 0.0
        self._spawn_initial()

    def _spawn_initial(self):
        for rid, room in WORLD.items():
            self.mobs[rid] = []
            for idx, mob_id in enumerate(room.get("spawns", [])):
                key = f"{rid}:{mob_id}:{idx}"
                self.mobs[rid].append(MobInstance(key, mob_id, rid))

    def living_in(self, room: str) -> List[MobInstance]:
        return [m for m in self.mobs.get(room, []) if m.alive]

    def spawn_mob(self, room: str, mob_id: str) -> Optional[MobInstance]:
        """Динамически создать моба (мировой босс/событие)."""
        if mob_id not in MOBS or room not in self.mobs:
            return None
        key = f"{room}:{mob_id}:b{len(self.mobs[room])}"
        inst = MobInstance(key, mob_id, room)
        self.mobs[room].append(inst)
        return inst

    def find(self, room: str, key: str) -> Optional[MobInstance]:
        for m in self.mobs.get(room, []):
            if m.key == key and m.alive:
                return m
        return None

    def find_by_mob_id(self, room: str, mob_id: str) -> Optional[MobInstance]:
        for m in self.mobs.get(room, []):
            if m.mob_id == mob_id and m.alive:
                return m
        return None

    def kill(self, inst: MobInstance):
        inst.dead_at = time.time()
        inst.aggro = []
        inst.effects = []

    # ───────── трупы и лут ─────────
    def add_corpse(self, room: str, mob: "MobInstance", loot: List[str]) -> dict:
        self._corpse_seq += 1
        c = {"key": f"corpse:{self._corpse_seq}", "mob_id": mob.mob_id,
             "name": mob.meta["name"], "emoji": mob.meta.get("emoji", "💀"),
             "loot": list(loot), "dead_at": time.time()}
        self.corpses.setdefault(room, []).append(c)
        return c

    def corpses_in(self, room: str) -> List[dict]:
        return self.corpses.get(room, [])

    def find_corpse(self, room: str, key: str) -> Optional[dict]:
        for c in self.corpses.get(room, []):
            if c["key"] == key:
                return c
        return None

    def loot_corpse(self, room: str, key: str) -> List[str]:
        """Забрать всё с трупа и убрать его. Возвращает список предметов."""
        c = self.find_corpse(room, key)
        if not c:
            return []
        items = list(c["loot"])
        c["loot"] = []
        self.corpses[room] = [x for x in self.corpses.get(room, []) if x["key"] != key]
        return items

    def process_corpse_decay(self, ttl: float = 180.0):
        """Убрать истлевшие трупы."""
        now = time.time()
        for room in list(self.corpses):
            self.corpses[room] = [c for c in self.corpses[room] if now - c["dead_at"] < ttl]

    def _relocate(self, inst: "MobInstance", dest: str):
        """Переместить экземпляр моба в другую комнату (для роуминга/возврата домой)."""
        if dest not in self.mobs or dest == inst.room:
            return
        src = self.mobs.get(inst.room)
        if src and inst in src:
            src.remove(inst)
        self.mobs[dest].append(inst)
        inst.room = dest

    def process_respawns(self):
        """Возродить мобов, у которых вышел таймер. Стартовые мобы — быстрее.
        Возрождённый бродяга возвращается в родную комнату."""
        now = time.time()
        returned = []
        for room, lst in list(self.mobs.items()):
            for inst in lst:
                if inst.dead_at is not None:
                    lvl = inst.meta.get("level", 1)
                    scale = STARTER_RESPAWN_SCALE if lvl <= STARTER_MAX_LEVEL else RESPAWN_SCALE
                    respawn = inst.meta.get("respawn", 9999) * scale
                    if now - inst.dead_at >= respawn:
                        inst.hp = inst.max_hp
                        inst.dead_at = None
                        inst.last_tick = now
                        inst.aggro = []
                        inst.effects = []
                        inst.threat.clear()
                        if inst.room != inst.home:
                            returned.append(inst)
        for inst in returned:
            self._relocate(inst, inst.home)

    # ───────── персональный лут с земли ─────────
    # Статический список предметов комнаты (world.yaml, WORLD[room]["items"])
    # больше НЕ мутируется при подборе: первый подобравший больше не забирает
    # предмет у остальных до рестарта. Вместо этого у КАЖДОГО игрока в
    # ch.flags["ground_taken"][room] копится список уже подобранных им ключей,
    # и предмет просто перестаёт быть видимым/доступным именно этому игроку.
    # Трупы (self.corpses) — общий, расходуемый ресурс, эта логика их не касается.
    def process_roaming(self, now: float = None):
        """Мобы вне боя изредка забредают в соседнюю комнату той же зоны.
        Возвращает список (комната, моб) для анонса пришедшему в комнату игроку."""
        now = now or time.time()
        if now - self._last_roam < ROAM_INTERVAL:
            return []
        self._last_roam = now
        moves = []
        moved = set()
        for room in list(self.mobs.keys()):
            src_zone = WORLD.get(room, {}).get("zone")
            for inst in list(self.mobs.get(room, [])):
                if inst.key in moved or not inst.alive or inst.aggro:
                    continue
                if inst.meta.get("level", 1) >= ROAM_MAX_LEVEL:
                    continue
                if inst.meta.get("no_roam") or inst.meta.get("boss") or inst.meta.get("raid"):
                    continue
                if random.random() > ROAM_CHANCE:
                    continue
                exits = list(WORLD.get(room, {}).get("exits", {}).values())
                random.shuffle(exits)
                for dest in exits:
                    d = WORLD.get(dest)
                    if not d or d.get("safe"):
                        continue
                    if d.get("zone") != src_zone:
                        continue
                    if len(self.living_in(dest)) >= ROAM_CAP:
                        continue
                    self._relocate(inst, dest)
                    moved.add(inst.key)
                    moves.append((dest, inst))
                    break
        return moves

    # ───────── снапшот/восстановление рантайма (персистентность) ─────────
    # Идентичность моба между рестартами: (комната, порядковый индекс в списке
    # комнаты, mob_id). Ключ .key нестабилен (для боссов зависит от длины списка),
    # а вот позиция статического спавна в WORLD[room]["spawns"] стабильна, пока
    # YAML не меняли. Поэтому пишем именно (room, idx, mob_id): при несовпадении
    # (контент правили — другой mob_id на этом индексе, или индекса больше нет)
    # запись при restore молча пропускается. Динамически заспавненные боссы тоже
    # попадают в снапшот по своему индексу, но при рестарте список пересобирается
    # из YAML (боссов там нет) — их записи просто не найдут пары и отсеются, а
    # заспавнятся заново штатным таймером boss_last. Это осознанный компромисс:
    # переживают рестарт статические мобы (их HP/смерть/респавн) и трупы.
    def snapshot(self) -> dict:
        """Сериализуемый снимок рантайм-состояния мира (JSON-safe)."""
        mobs = []
        for room, lst in self.mobs.items():
            for idx, inst in enumerate(lst):
                # снимаем только то, что отличается от «свежего» инстанса —
                # но для простоты и надёжности пишем всегда hp/dead_at
                mobs.append({
                    "room": room,
                    "idx": idx,
                    "mob_id": inst.mob_id,
                    "hp": inst.hp,
                    "dead_at": inst.dead_at,
                })
        corpses = []
        for room, lst in self.corpses.items():
            for c in lst:
                corpses.append({
                    "room": room,
                    "key": c["key"],
                    "mob_id": c["mob_id"],
                    "name": c["name"],
                    "emoji": c.get("emoji", "💀"),
                    "loot": list(c["loot"]),
                    "dead_at": c["dead_at"],
                })
        return {"mobs": mobs, "corpses": corpses, "corpse_seq": self._corpse_seq}

    def restore(self, data: dict) -> int:
        """Применить снимок к текущему миру. Возвращает число применённых
        записей (мобов + трупов). Несовпадения контента молча пропускаются.
        Идемпотентно и безопасно к частично устаревшим данным."""
        if not data:
            return 0
        applied = 0
        # 1) мобы: применяем hp/dead_at к инстансу по (room, idx, mob_id)
        for rec in data.get("mobs", []):
            room = rec.get("room")
            idx = rec.get("idx")
            mob_id = rec.get("mob_id")
            lst = self.mobs.get(room)
            if lst is None or idx is None or idx < 0 or idx >= len(lst):
                continue                      # комнаты/индекса больше нет
            inst = lst[idx]
            if inst.mob_id != mob_id:
                continue                      # на этом индексе теперь другой моб
            hp = rec.get("hp")
            if hp is not None:
                # не даём HP выйти за максимум текущего инстанса
                inst.hp = min(inst.max_hp, hp)
            inst.dead_at = rec.get("dead_at")
            if inst.dead_at is None and inst.hp <= 0:
                # защита от «живого трупа»: живой моб не может иметь hp<=0
                inst.hp = inst.max_hp
            applied += 1
        # 2) трупы: пересоздаём (общий расходуемый ресурс). Кладём как есть —
        # process_corpse_decay сам уберёт те, чей возраст превысил TTL.
        restored_corpses: Dict[str, List[dict]] = {}
        for rec in data.get("corpses", []):
            room = rec.get("room")
            if room not in self.mobs:         # комнаты больше нет в мире
                continue
            restored_corpses.setdefault(room, []).append({
                "key": rec.get("key", f"corpse:{rec.get('mob_id')}"),
                "mob_id": rec.get("mob_id"),
                "name": rec.get("name", rec.get("mob_id")),
                "emoji": rec.get("emoji", "💀"),
                "loot": list(rec.get("loot", [])),
                "dead_at": rec.get("dead_at", time.time()),
            })
            applied += 1
        if restored_corpses:
            self.corpses = restored_corpses
        # продолжить нумерацию трупов, чтобы ключи не пересеклись
        seq = data.get("corpse_seq")
        if isinstance(seq, int) and seq > self._corpse_seq:
            self._corpse_seq = seq
        return applied


# ───────── персональный лут с земли (чистые функции) ─────────
# Статический список WORLD[room]["items"] — общий шаблон комнаты из world.yaml,
# он НЕ мутируется. У каждого игрока в ch.flags["ground_taken"][room] хранится
# список ключей уже подобранных ИМ предметов этой комнаты — по этому списку
# и фильтруем видимость/доступность. Трупы (World.corpses) сюда не относятся —
# они расходуются взаправду и остаются общими для всех.

def ground_items_for(ch, room: str) -> List[str]:
    """Предметы на земле комнаты, которые ЭТОМУ игроку ещё доступны
    (статический список комнаты минус то, что он уже подобрал сам)."""
    static_items = WORLD.get(room, {}).get("items", [])
    taken = (ch.flags.get("ground_taken") or {}).get(room, [])
    if not taken:
        return list(static_items)
    out = list(static_items)
    for key in taken:
        if key in out:
            out.remove(key)
    return out


def take_ground_item(ch, room: str, key: str) -> bool:
    """Подобрать предмет `key` с земли комнаты `room` для игрока `ch`.
    Кладёт предмет в инвентарь и помечает его взятым лично этим игроком —
    мировой список комнаты не трогаем, остальным предмет по-прежнему виден.
    Повторный подбор того же предмета тем же игроком невозможен.
    -> True при успехе, False если предмета для этого игрока уже нет."""
    if key not in ground_items_for(ch, room):
        return False
    ch.flags.setdefault("ground_taken", {}).setdefault(room, []).append(key)
    ch.inventory.append(key)
    return True
