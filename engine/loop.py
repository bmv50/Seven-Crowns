# -*- coding: utf-8 -*-
"""
Реал-тайм игровой цикл (сердце MUD).
Запускается как фоновая asyncio-задача, тикает раз в секунду:
  • мобы с aggro атакуют игроков по своим таймерам tick_speed
  • тикают яды/эффекты на мобах
  • обрабатывается респавн
  • смерть игрока/моба -> награды, рассылка
Взаимодействие с Telegram — через callback-функции send/broadcast,
которые прокидываются извне (loop не знает про aiogram).
"""
import asyncio
import random
import time
from datetime import datetime
from typing import Callable, Dict, List, Awaitable

from .content import MOBS, ITEMS, RACES, WORLD
from . import money
from . import npc_ai
from . import log as _log

_logger = _log.get("engine.loop")
from . import catchup
from . import events
from . import seasons
from . import notify
from .character import Character
from .world import World, MobInstance
from . import combat
from . import quest
from . import errands
from . import achievements
from . import daily
from . import weekly
from . import streak
from . import content
from . import bestiary
from . import dungeon
from . import rarity
from . import equip as _equip

_EQUIP_POOL = [k for k, v in ITEMS.items()
               if v.get("type") in ("weapon", "armor", "accessory") and v.get("slot")]
# уровень предмета (для уровне-зависимого дропа)
_BASE_LVL = {k: _equip.level_req(ITEMS[k]) for k in _EQUIP_POOL}


def _pool_for(mob_level, max_remort=0):
    cap = mob_level + 3
    # реморт-предметы (вариант C) не выпадают, пока у убийц не хватает перерождений
    return [k for k in _EQUIP_POOL
            if _BASE_LVL[k] <= cap
            and int(ITEMS[k].get("remort_req", 0) or 0) <= max_remort]
from . import pets
from . import karma
from . import territory
from . import referral
from . import chronicle
from . import analytics

BOSS_CFG = content._load_optional("world_bosses.yaml") or []
RAID_IDS = {c["boss"] for c in BOSS_CFG if c.get("raid")}


# Пороги бонусного лута жёлтых/красных мобов (внутр. единицы).
# YELLOW — «средние материалы/расходники»: их цены НЕ поднимались в ребалансе
#   экипировки (спринт 6), поэтому полоса 1500–6000 сохраняет прежний смысл.
# RED — «дорогие аксессуары/топ-расходники»: после подъёма цен экипировки все
#   аксессуары стали ≥ ~500k внутр., поэтому старый порог 6000 (=60 монет) теперь
#   ловил бы ЛЮБОЙ аксессуар. Порог поднят пропорционально до RED_ACCESSORY_MIN
#   (~p70 распределения цен аксессуаров), чтобы полоса по-прежнему отбирала лишь
#   ДОРОГИЕ аксессуары. Порог расходников (RED_CONSUMABLE_MIN) не менялся — их
#   цены прежние.
RED_ACCESSORY_MIN = 1_800_000   # внутр.: «дорогой аксессуар» (топ ~30% по цене)
RED_CONSUMABLE_MIN = 6000       # внутр.: топ-расходник (цены расходников не росли)


def _build_rare_pool():
    """Пулы бонусного лута для жёлтых/красных мобов (существующие предметы)."""
    yellow = [k for k, v in ITEMS.items()
              if v.get("type") in ("material", "consumable") and 1500 <= v.get("price", 0) <= 6000]
    red = [k for k, v in ITEMS.items()
           if (v.get("type") == "accessory" and v.get("price", 0) >= RED_ACCESSORY_MIN)
           or (v.get("type") == "consumable" and v.get("price", 0) >= RED_CONSUMABLE_MIN)]
    return {"yellow": yellow, "red": red or yellow}


_RARE_POOL = _build_rare_pool()

# Анти-спам для анонса «забредает сюда»: не чаще раза в это число секунд
# на одну комнату (см. GameLoop.roam_announce_allowed).
ROAM_ANNOUNCE_COOLDOWN = 120.0


class GameLoop:
    def __init__(self, world: World, characters: Dict[int, Character],
                 send: Callable[[int, str], Awaitable],
                 save: Callable[[Character], Awaitable]):
        self.world = world
        self.chars = characters
        self.send = send            # async send(uid, text)
        self.save = save            # async save(character)
        self.running = False
        # опц. колбэк: async on_combat_hit(victim, mob, lines) — обновить
        # «живое» боевое сообщение жертвы вместо отдельных сообщений-тиков
        self.on_combat_hit = None
        # опц. колбэк: async on_combat_reward(ch, text) — показать награду за
        # убийство, пере-постив боевую панель игрока вниз (вместо send)
        self.on_combat_reward = None
        self.on_death = None   # async on_death(ch) — экран смерти с кнопкой
        # опц. колбэк: async on_world_notify(text, category) — широковещательный
        # push по всем uid из БД (bot привяжет broadcast_all). loop не знает про
        # Telegram; при None — молча пропускаем (поведение без NOTIFY не меняется).
        self.on_world_notify = None
        # опц. колбэк: callable uid -> Character|None — найти реферера по uid
        # (bot привяжет chars.get). Нужен для награды рефереру на левелапе.
        self.referral_lookup = None
        # опц. колбэк: async on_referral(uid, text) — доставить текст рефереру
        # (может быть оффлайн; bot привяжет send с try/except). При None — молча пропускаем.
        self.on_referral = None
        # опц. колбэк: async on_ambient(room, text) — эфемерная (самоудаляющаяся)
        # строка окружения: анонс забредания моба и ambient-реплики NPC
        # (npc_ai.tick_ambient). bot привяжет broadcast_ephemeral. При None —
        # поведение как раньше: обычный self.broadcast (не эфемерно).
        self.on_ambient = None
        self.boss_last = {}    # таймеры мировых боссов
        # Этап 9 (мониторинг): длительность тика для админ-Health. last — последний
        # замер, avg — экспоненциальное скользящее среднее (сглаживает всплески).
        self.tick_last_ms = 0.0
        self.tick_avg_ms = 0.0
        # анонс «забредает сюда» — не чаще раза в ROAM_ANNOUNCE_COOLDOWN на
        # комнату (плейтест владельца: частые анонсы спамили комнату). Сам
        # моб при этом всё равно перемещается — молчит только анонс.
        self._roam_announced: Dict[str, float] = {}

    def party_in(self, room: str) -> List[Character]:
        return [c for c in self.chars.values() if c.room == room and c.hp > 0]

    def roam_announce_allowed(self, room: str, now: float = None) -> bool:
        """Можно ли анонсировать «забредает сюда» в этой комнате прямо сейчас.
        Чистая функция: не мутирует состояние, ЕСЛИ ответ True — фиксирует
        момент анонса (следующий вызов для той же комнаты в течение
        ROAM_ANNOUNCE_COOLDOWN секунд вернёт False)."""
        now = now if now is not None else time.time()
        last = self._roam_announced.get(room)
        if last is not None and now - last < ROAM_ANNOUNCE_COOLDOWN:
            return False
        self._roam_announced[room] = now
        return True

    async def broadcast(self, room: str, text: str, exclude: int = None):
        for c in self.party_in(room):
            if c.uid != exclude:
                await self.send(c.uid, text)

    async def on_mob_death(self, mob: MobInstance, killers: List[Character]):
        """Раздать награды всем, кто был в аггро-листе (кооп)."""
        self.world.kill(mob)
        m = mob.meta
        # пати: к убийцам добавляем сопартийцев в той же комнате (общий опыт)
        pm = getattr(self, "party_mgr", None)
        if pm and killers:
            seen = {k.uid for k in killers}
            extra = []
            for k in list(killers):
                for uid in pm.members(k.uid):
                    if uid not in seen and uid in self.chars:
                        c = self.chars[uid]
                        if c.room == mob.room and c.hp > 0:
                            extra.append(c); seen.add(uid)
            killers = killers + extra
        # лут падает убийцам; xp/gold делится поровну
        base_xp = max(1, m["xp"] // max(1, len(killers)))
        base_gold = max(1, m["gold"] // max(1, len(killers)))
        mob_lvl = m.get("level", 1)
        _zone = WORLD.get(mob.room, {}).get("zone")
        _emod = events.modifiers(_zone) if events.ENABLED else {"xp": 1.0, "gold": 1.0, "loot": 1.0}
        _terr_flip = None
        # лут: шанс выпадения растёт за сложность (по самому слабому из убийц)
        weak_lvl = min((k.level for k in killers), default=1)
        loot_mult = combat.DIFF_LOOT[combat.mob_difficulty(weak_lvl, mob_lvl)] * _emod["loot"]
        loot_items = [ik for ik, ch_ in m.get("loot", [])
                      if random.random() < min(0.95, ch_ * loot_mult)]
        # квест-токены (знак посвящения и т.п.) падают «один раз на игрока»:
        # только если хоть кому-то из убийц они ещё нужны
        loot_items = [ik for ik in loot_items
                      if ITEMS.get(ik, {}).get("type") != "quest"
                      or any(quest.needs_token(k, ik) for k in killers)]
        # бонусный «качественный» лут с жёлтых/красных
        _bdiff = combat.mob_difficulty(weak_lvl, mob_lvl)
        if _bdiff in ("yellow", "red"):
            _pool = _RARE_POOL.get(_bdiff) or _RARE_POOL.get("yellow")
            if _pool and random.random() < (0.30 if _bdiff == "red" else 0.15):
                loot_items.append(random.choice(_pool))
        # дроп экипировки по редкости (модель «два броска»: шанс дропа → редкость)
        _maxr = max((k.remort_count for k in killers), default=0)
        _dpool = _pool_for(mob_lvl, _maxr)
        _pref = random.choice(killers).cls if killers else None
        if _pref and random.random() < 0.6:
            _cp = [k for k in _dpool if _equip.class_can_use(_pref, k)]
            if _cp:
                _dpool = _cp
        _edrop = rarity.roll_drop(mob_lvl, _dpool, boss=bool(m.get("boss")))
        if _edrop:
            loot_items.append(_edrop)
        # рейд-босс: гарантированный 🔴 божественный дроп при групповом убийстве
        _is_raid = mob.mob_id in RAID_IDS and len({k.uid for k in killers}) >= 2
        if _is_raid:
            _rpool = [k for k in _pool_for(mob_lvl, _maxr)
                      if _pref and _equip.class_can_use(_pref, k)] or _pool_for(mob_lvl, _maxr)
            _red = rarity.encode(random.choice(_rpool), "red", random.randint(1, 10**9))
            loot_items.append(_red)
        corpse = self.world.add_corpse(mob.room, mob, loot_items) if loot_items else None
        _diff_tag = {"green": "", "yellow": " 🟡", "red": " 🔴"}
        for ch in killers:
            _df = combat.mob_difficulty(ch.level, mob_lvl)
            _xp = int(base_xp * ch.xp_mult * combat.DIFF_XP[_df] * content.XP_RATE * _emod["xp"] * streak.xp_mult(ch))
            # Золотосток эндгейма: боевая голда домножается на уровне-зависимый
            # множитель gold_rate_for (1.0 на онбординге ≤ур.10, спуск к 0.35 на
            # капе). Раньше функция была объявлена, но НЕ подключена — базовый
            # фарм на ур.60 давал ~100k монет/час, тривиализируя цены. Теперь срез
            # активен: онбординг не задет (naive-доход на зелья прежний), эндгейм
            # обрезан ~вдвое-втрое (см. калибровку экономики).
            _g = int(base_gold * ch.gold_mult * combat.DIFF_GOLD[_df] * _emod["gold"]
                     * content.gold_rate_for(ch.level))
            if seasons.ENABLED:
                seasons.add_points(ch, max(1, mob_lvl) * (5 if m.get("boss") else 1))
            _tb = territory.control_bonus(ch, _zone)
            if _tb > 1.0:
                _xp = int(_xp * _tb); _g = int(_g * _tb)
            _fl = territory.add_kill(ch, _zone)
            if _fl:
                _terr_flip = _fl
            # отдохнувший опыт: пока есть запас — удваиваем добытый опыт
            _rest = ch.flags.get("rested", 0)
            _rb = min(_rest, _xp) if _rest > 0 else 0
            if _rb:
                ch.flags["rested"] = _rest - _rb
            ch.xp += _xp + _rb
            ch.gold += _g
            ch.flags["kills"] = int(ch.flags.get("kills", 0)) + 1
            _col_lines = bestiary.record_kill(ch, mob.mob_id)
            _rt = " 💤×2" if _rb else ""
            msg = [f"☠️ *{m['name']}*{_diff_tag[_df]} повержен! "
                   f"+{_xp + _rb} опыта{_rt}, +{money.fmt(_g)}."]
            msg.extend(_col_lines)
            if corpse and corpse.get("loot") and ch.flags.get("autoloot", False):
                taken = self.world.loot_corpse(mob.room, corpse["key"])
                ch.inventory.extend(taken)
                if taken:
                    msg.append("🎒 Положено в сумку: " + ", ".join(ITEMS[i]["name"] for i in taken))
                corpse = None
            elif corpse and corpse.get("loot"):
                msg.append(f"💀 Осталось тело — обыщи его (кнопка в комнате).")
            # прогресс квестов на убийство
            for qline in quest.on_kill(ch, mob.mob_id):
                msg.append(qline)
            _dl = daily.on_kill(ch, mob.mob_id)
            if _dl:
                msg.append(_dl)
            _el = errands.on_kill(ch, mob.mob_id)
            if _el:
                msg.append(_el)
            _wl = weekly.on_kill(ch, m)
            if _wl:
                msg.append(_wl)
            if ch.uid in mob.exploited_by:
                _wld = weekly.on_dtype_kill(ch)
                if _wld:
                    msg.append(_wld)
            for _dgl in dungeon.on_kill(ch, mob.mob_id, group=len(killers) >= 2):
                msg.append(_dgl)
            for _ptl in pets.on_kill_xp(ch, _xp):
                msg.append(_ptl)
            await self._check_levelup(ch, msg)
            for aline in achievements.check(ch):
                msg.append(aline)
            ch.target = None
            ch.reset_combat_resource()       # ярость спадает после боя
            await self.save(ch)
            # награду показываем через боевую панель (пере-пост вниз), иначе send
            if self.on_combat_reward:
                await self.on_combat_reward(ch, "\n".join(msg))
            else:
                await self.send(ch.uid, "\n".join(msg))
        # смена контроля над территорией
        if _terr_flip:
            _fn = MOBS and __import__("engine.content", fromlist=["FACTIONS"]).FACTIONS.get(_terr_flip, {}).get("name", _terr_flip)
            for c in self.chars.values():
                await self.send(c.uid, f"⚔️ *{_fn}* установил контроль над зоной «{_zone}»!")
            chronicle.record("territory", f"«{_fn}» установил контроль над зоной «{_zone}»")
        # боссовое событие
        if m.get("boss"):
            for ch in killers:
                ch.flags["won"] = True
            names = ", ".join(c.name for c in killers) or "герои"
            _raid_note = (" 🔴 С тела пала БОЖЕСТВЕННАЯ добыча!"
                          if mob.mob_id in RAID_IDS and len({k.uid for k in killers}) >= 2 else "")
            _title = "РЕЙД-БОСС" if mob.mob_id in RAID_IDS else "МИРОВОЙ БОСС"
            for c in self.chars.values():
                await self.send(c.uid, f"🏆 *{_title} {m['name']} ПОВЕРЖЕН!* "
                                       f"Слава победителям: {names}.{_raid_note}")
            chronicle.record("boss", f"{names} сразил(и) {m['name']}")

    async def _check_levelup(self, ch: Character, msg: List[str]):
        from .character import LEVEL_CAP
        while ch.level < LEVEL_CAP and ch.xp >= ch.xp_to_next:
            ch.xp -= ch.xp_to_next
            ch.level += 1
            # очко таланта выдаётся раз в 4 уровня (уровни 4, 8, ..., 60) → 15 очков к капу
            _got_tp = ch.level % 4 == 0
            if _got_tp:
                ch.flags["talent_points"] = int(ch.flags.get("talent_points", 0)) + 1
            ch.init_vitals()
            _tp_note = " +1 очко таланта 🌳" if _got_tp else ""
            msg.append(f"⬆️ *УРОВЕНЬ {ch.level}!* Характеристики выросли, вы исцелены.{_tp_note}")
            analytics.track(ch.uid, "level_up", {"level": ch.level})   # Этап 7.1
            # рефералка: на достижении REWARD_LEVEL — награда новому игроку и рефереру
            _ref_by = ch.flags.get("ref_by")
            if _ref_by and getattr(self, "referral_lookup", None):
                _referrer = self.referral_lookup(_ref_by)
            else:
                _referrer = None
            _ref_lines, _ref_text = referral.on_level(ch, _referrer)
            msg.extend(_ref_lines)
            if _ref_text and _referrer is not None:
                if self.on_referral:
                    await self.on_referral(_referrer.uid, _ref_text)
                await self.save(_referrer)
        if ch.level >= LEVEL_CAP:
            ch.xp = min(ch.xp, ch.xp_to_next)
            if not ch.flags.get("maxlvl_note"):
                ch.flags["maxlvl_note"] = True
                msg.append(f"🌟 *Максимальный уровень {LEVEL_CAP}!* Доступен реморт у наставника.")

    async def on_player_death(self, ch: Character):
        # снять аггро, заморозить как «мертвого» — возрождение по кнопке
        for mob in self.world.living_in(ch.room):
            if ch.uid in mob.aggro:
                mob.aggro.remove(ch.uid)
        ch.target = None
        ch.effects = []
        ch.hp = 0
        ch.flags["dead"] = True
        analytics.track(ch.uid, "death", {"level": ch.level, "room": ch.room})   # Этап 7.1
        _drop = karma.maybe_drop_on_death(ch)
        if _drop:
            await self.send(ch.uid, f"💸 От удара вы выронили предмет: {ITEMS.get(_drop,{}).get('name',_drop)}.")
        await self.broadcast(ch.room, f"💀 {ch.name} пал в бою!", exclude=ch.uid)
        await self.save(ch)
        if self.on_death:
            await self.on_death(ch)
        else:
            await self.send(ch.uid, "💀 Вы пали. Возродитесь: /start")

    async def tick(self):
        now = time.time()
        occupied = {c.room for c in self.chars.values()
                    if c.hp > 0 and not c.flags.get("dead")}
        # Ленивый режим: обрабатываем только активные комнаты (игроки + соседи).
        # Бой возможен лишь там, где есть игроки, поэтому ограничение безопасно.
        rooms_iter = catchup.active_set(occupied) if catchup.ENABLED \
            else list(self.world.mobs.keys())
        # 1) мобы атакуют по таймерам
        for room in rooms_iter:
            lst = self.world.mobs.get(room, [])
            party = self.party_in(room)
            for mob in lst:
                if not mob.alive or not mob.aggro:
                    continue
                speed = mob.meta.get("tick_speed", 4)
                if now - mob.last_tick < speed:
                    continue
                mob.last_tick = now
                # эффекты на мобе (яд/горение/кровотечение) — копим строки
                poison_lines = combat.tick_effects_mob(mob)
                if mob.hp <= 0:
                    killers = [self.chars[u] for u in mob.aggro if u in self.chars]
                    await self.on_mob_death(mob, killers)
                    continue
                # заморозка/оглушение — моб пропускает ход
                if combat.mob_is_disabled(mob):
                    if poison_lines and self.on_combat_hit:
                        pass
                    for line in poison_lines:
                        await self.broadcast(room, line)
                    await self.broadcast(room, f"🧊 {mob.meta['name']} не может действовать!")
                    continue
                # выбрать цель из аггро
                targets = [self.chars[u] for u in mob.aggro
                           if u in self.chars and self.chars[u].room == room
                           and self.chars[u].hp > 0]
                if not targets:
                    mob.aggro = []
                    mob.threat.clear()
                    continue
                # цель по угрозе: танк удерживает мобов на себе; иначе — случайно
                top_uid = mob.top_threat([c.uid for c in targets])
                if top_uid is not None:
                    victim = next(c for c in targets if c.uid == top_uid)
                else:
                    victim = random.choice(targets)
                all_lines = poison_lines + combat.mob_attack(mob, victim)
                if self.on_combat_hit:
                    await self.on_combat_hit(victim, mob, all_lines)
                else:
                    for line in all_lines:
                        await self.broadcast(room, line)
                if victim.hp <= 0:
                    await self.on_player_death(victim)
        # карма медленно угасает
        for ch in self.chars.values():
            karma.decay(ch)
        # отдохнувший опыт копится в комнатах отдыха (гостиница/храм)
        for ch in self.chars.values():
            if WORLD.get(ch.room, {}).get("rest") and not ch.flags.get("dead"):
                cap = ch.level * 200
                cur = int(ch.flags.get("rested", 0))
                if cur < cap:
                    ch.flags["rested"] = min(cap, cur + 8)
                    # push «отдых накоплен полностью» — раз при достижении капа,
                    # с дедупом раз в сутки (не спамить, если игрок торчит в
                    # комнате отдыха с уже полным баком)
                    if cur + 8 >= cap and notify.ENABLED:
                        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
                        if ch.flags.get("notify_rested_day") != today:
                            ch.flags["notify_rested_day"] = today
                            notify.emit(ch.uid, "rested_full",
                                       "💤 Отдохнувший опыт накоплен до предела! "
                                       "Иди в бой, пока бонус не пропадает зря.")
        # мировые боссы по таймеру
        for cfg in BOSS_CFG:
            key = f"{cfg['boss']}@{cfg['room']}"
            if self.world.find_by_mob_id(cfg["room"], cfg["boss"]):
                self.boss_last[key] = now   # пока жив — таймер не идёт
                continue
            last = self.boss_last.setdefault(key, now)
            if now - last >= cfg.get("interval", 900):
                inst = self.world.spawn_mob(cfg["room"], cfg["boss"])
                self.boss_last[key] = now
                if inst:
                    rn = WORLD.get(cfg["room"], {}).get("name", cfg["room"])
                    _bmsg = (f"🐉 *МИРОВОЙ БОСС*: {inst.meta['name']} "
                             f"объявился в «{rn}»! Соберите отряд за щедрой наградой!")
                    # онлайн-игрокам — сразу; всем зарегистрированным — через push
                    if self.on_world_notify:
                        await self.on_world_notify(_bmsg, "world_boss")
                    else:
                        for c in self.chars.values():
                            await self.send(c.uid, _bmsg)
        # «живые» NPC: Utility AI + FSM (вне боя), только если слой включён.
        # В ленивом режиме обрабатываем только активные комнаты (как и бой/респавн).
        # Ambient-реплики — эфемерные (self-destruct через bot.on_ambient), чтобы
        # не засорять чат постоянными сообщениями (плейтест владельца).
        if npc_ai.ENABLED:
            ambient_rooms = rooms_iter if catchup.ENABLED else None
            for room, line in npc_ai.tick_ambient(self.world, occupied, now,
                                                  rooms=ambient_rooms):
                if self.on_ambient:
                    await self.on_ambient(room, line)
                else:
                    await self.broadcast(room, line)
        # мировые события: запуск/завершение по таймеру (рассылка всем)
        if events.ENABLED:
            _started = events.maybe_start(self.world, now)
            for line in _started:
                chronicle.record("event", line)
            for line in _started + events.tick(self.world, now):
                for c in self.chars.values():
                    await self.send(c.uid, line)
        # 2) респавн и истлевание трупов
        if catchup.ENABLED:
            catchup.tick(self.world, occupied, now, npc_ai)
        else:
            self.world.process_respawns()
        self.world.process_corpse_decay()
        # 3) бродячие мобы: изредка перетекают в соседние комнаты (живой мир).
        # Анонс «забредает сюда» дедуплицируется (не чаще раза в
        # ROAM_ANNOUNCE_COOLDOWN на комнату) — сам моб перемещается в любом
        # случае, молчит только повторный анонс (плейтест владельца: спам).
        for room, mob in self.world.process_roaming(now):
            if room in occupied and self.roam_announce_allowed(room, now):
                _line = f"{mob.meta.get('emoji','👣')} *{mob.meta['name']}* забредает сюда."
                if self.on_ambient:
                    await self.on_ambient(room, _line)
                else:
                    await self.broadcast(room, _line)

    async def run(self, interval: float = 1.0):
        self.running = True
        while self.running:
            try:
                _t0 = time.perf_counter()
                await self.tick()
                _dt = (time.perf_counter() - _t0) * 1000.0
                self.tick_last_ms = _dt
                # EMA: avg = 0.9*avg + 0.1*last (первый замер сразу задаёт базу)
                self.tick_avg_ms = _dt if self.tick_avg_ms == 0.0 \
                    else 0.9 * self.tick_avg_ms + 0.1 * _dt
            except Exception as e:
                _log.log_err(_logger, "game_loop_tick_failed", e)
            await asyncio.sleep(interval)
