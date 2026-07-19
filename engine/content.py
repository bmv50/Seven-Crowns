# -*- coding: utf-8 -*-
"""Загрузка и валидация игрового контента из YAML."""
import os
import yaml

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load(name: str) -> dict:
    path = os.path.join(DATA_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


from . import rarity as _rarity


class _ItemDict(dict):
    """Словарь предметов, разрешающий ключи «база#rarity» на лету:
    ITEMS["стальной_меч#blue"] вернёт характеристики, домноженные на редкость."""
    def __missing__(self, key):
        if isinstance(key, str) and "#" in key:
            base, rar, seed = _rarity.split(key)
            if dict.__contains__(self, base) and rar in _rarity.META:
                m = _rarity.scaled_meta(dict.__getitem__(self, base), rar, seed)
                dict.__setitem__(self, key, m)
                return m
        raise KeyError(key)

    def __contains__(self, key):
        if dict.__contains__(self, key):
            return True
        if isinstance(key, str) and "#" in key:
            base, rar, seed = _rarity.split(key)
            return dict.__contains__(self, base) and rar in _rarity.META
        return False

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, TypeError):
            return default


# Загружаем весь контент один раз при импорте
CLASSES = _load("classes.yaml")
SKILLS = _load("skills.yaml")
WORLD = _load("world.yaml")
MOBS = _load("mobs.yaml")
ITEMS = _ItemDict(_load("items.yaml"))
# сгенерированный каталог оружия/брони по уровням/классам (мержится поверх)
if os.path.exists(os.path.join(DATA_DIR, "items_gen.yaml")):
    ITEMS.update(_load("items_gen.yaml"))
if os.path.exists(os.path.join(DATA_DIR, "runes.yaml")):
    ITEMS.update(_load("runes.yaml"))
RACES = _load("races.yaml")
QUESTS = _load("quests.yaml")
# сюжетные квесты (история мира, цепочки через requires) — мержатся поверх
if os.path.exists(os.path.join(DATA_DIR, "quests_story.yaml")):
    QUESTS.update(_load("quests_story.yaml"))
if os.path.exists(os.path.join(DATA_DIR, "quests_endgame.yaml")):
    QUESTS.update(_load("quests_endgame.yaml"))
RECIPES = _load("recipes.yaml")
NPCS = _load("npcs.yaml")
FACTIONS = _load("factions.yaml")

# Расовые столицы мержатся поверх world.yaml (переопределяют village/temple/well)
import glob as _glob
for _cap in sorted(_glob.glob(os.path.join(DATA_DIR, "world_capital_*.yaml"))):
    _capdata = yaml.safe_load(open(_cap, encoding="utf-8")) or {}
    WORLD.update(_capdata)

# Опционально: процедурные «дикие» зоны (env WILD_ZONES=1; по умолчанию выключено).
# Подвешиваются к рукотворным комнатам, не затирая существующий контент.
if os.environ.get("WILD_ZONES", "0").strip() in ("1", "true", "True", "yes", "on"):
    try:
        from . import worldgen as _wg
        _seed = int(os.environ.get("WILD_SEED", "42").strip() or 42)
        _wg.apply_wild_zones(WORLD, base_seed=_seed)
    except Exception as _e:
        print("⚠️  WILD_ZONES: генерация диких зон пропущена:", _e)


def _load_optional(name: str) -> dict:
    try:
        return _load(name) or {}
    except FileNotFoundError:
        return {}


SHOPS = _load_optional("shops.yaml")   # ассортимент торговцев: npc_id -> [item id]


# Глобальный множитель здоровья: HP героев и мобов ×N, а также лечение и яды,
# чтобы бои были длиннее и тактичнее без поломки баланса (урон НЕ масштабируем).
HP_SCALE = 7

# ── Глобальные ручки баланса (правка геймплея по фидбеку) ──
XP_RATE = 0.6          # замедлитель набора опыта (×0.6 ко всему опыту с мобов)
# Золотосток №0 (калибровка экономики): голда с мобов росла как из пожарного
# рукава — на ур.60 час фарма давал ~24 000 монет при цене топ-предмета 4 000.
# GOLD_RATE — множитель боевой голды, ЗАВИСЯЩИЙ ОТ УРОВНЯ: на онбординге (≤
# GOLD_RATE_FULL_LEVEL) доход полный (новичок должен свободно покупать зелья —
# проверено sim --naive), затем линейно снижается к GOLD_RATE_FLOOR на капе.
# Это срезает эндгейм-«фонтан» базового фарма, не задевая первые уровни.
GOLD_RATE_FULL_LEVEL = 10   # до этого уровня включительно — 100% боевой голды
GOLD_RATE_FLOOR = 0.35      # доля боевой голды на LEVEL_CAP (эндгейм-срез)
GOLD_RATE_CAP_LEVEL = 60    # уровень, на котором достигается floor


def gold_rate_for(level: int) -> float:
    """Множитель боевой голды по уровню игрока: 1.0 на онбординге, линейный
    спуск к GOLD_RATE_FLOOR к GOLD_RATE_CAP_LEVEL. Онбординг не задет."""
    if level <= GOLD_RATE_FULL_LEVEL:
        return 1.0
    if level >= GOLD_RATE_CAP_LEVEL:
        return GOLD_RATE_FLOOR
    span = GOLD_RATE_CAP_LEVEL - GOLD_RATE_FULL_LEVEL
    t = (level - GOLD_RATE_FULL_LEVEL) / span
    return 1.0 - t * (1.0 - GOLD_RATE_FLOOR)


RESPAWN_SCALE = 2.5    # множитель таймера респавна мобов (реже возрождаются)
MOB_ATK_SCALE = 1.5    # урон мобов ×N — чтобы они представляли угрозу (HP игрока ×7)
# Стартовые зоны: мобы низкого уровня возрождаются быстро, чтобы в коротких
# telegram-сессиях не было «мёртвого времени» ожидания респавна.
STARTER_MAX_LEVEL = 10       # моб ≤ этого уровня считается «стартовым»
STARTER_RESPAWN_SCALE = 0.8  # быстрый респавн для стартовых мобов
# Бродячие мобы: раз в ROAM_INTERVAL сек моб вне боя с шансом ROAM_CHANCE
# уходит в соседнюю комнату той же зоны (мир кажется живым, комнаты не пустуют).
# Плейтест владельца: анонсы «забредает сюда» были слишком частыми (спам в
# комнате) — интервал и шанс снижены, плюс анонс дедуплицируется в loop.py
# (roam_announce_allowed) не чаще раза в 120с на комнату.
ROAM_INTERVAL = 45.0
ROAM_CHANCE = 0.08
ROAM_CAP = 4                 # не сгонять в одну комнату больше стольких бродяг
ROAM_MAX_LEVEL = 30          # боссы/элита (≥) не бродят

# Агрессивные мобы (бросаются сами) — детерминированно ~1 к 10, кроме городских.
import hashlib as _hl
def _aggressive(mid, meta):
    if meta.get("aggressive") is not None:
        return bool(meta["aggressive"])
    if mid.startswith("городск") or mid.startswith("подвальн"):
        return False
    return int(_hl.md5(mid.encode("utf-8")).hexdigest(), 16) % 10 == 0
AGGRESSIVE = {mid for mid, m in MOBS.items() if _aggressive(mid, m)}

SELL_RATE = 0.6   # доля цены, которую кузнец платит при скупке (материалы/расходники)
# Отдельная — НИЗКАЯ — доля скупки для ЭКИПИРОВКИ (оружие/броня/аксессуары).
# Почему так мало: после ребаланса цен снаряжения (спринт 6) топ-предмет стоит
# 10–15 часов дохода уровня, а мобы роняют экипировку ~20% киллов. Если кузнец
# скупает весь дроп по 60%, это фонтан в ДЕСЯТКИ раз выше боевого дохода (sim_income:
# при 60% продажа дропа = +21000% к доходу/ч против калибровки спринта 5; ключевая
# причина — цена одной дропнутой вещи сопоставима с несколькими часами боёв).
# Продажа найденной экипировки — «утилизация»: реальную цену игрок берёт на
# АУКЦИОНЕ (там лот идёт за полный price, см. _auc_price в bot/main.py). Флэт-ставка
# не может удержать фонтан в пределах ±25% на ВСЕХ уровнях (отношение
# цена_дропа/боевая_голда меняется с 60× на ур.5 до ~350× на ур.45), поэтому берём
# минимальную осмысленную «ломбардную» долю — она срезает фонтан в ~20× против 60%
# и держит вклад продажи шмота в том же порядке, что был в спринте 5 (там продажа
# лута и так удваивала доход). Боевой фарм не задет вовсе (голда мобов не менялась,
# дрейф боевого дохода = 0%). Материалы/расходники — прежние 60% (их цены не росли).
SELL_RATE_EQUIP = 0.03
_EQUIP_TYPES = ("weapon", "armor", "accessory")


def sell_price(key: str) -> int:
    """Сколько кузнец платит за предмет. 0 = не скупается.
    Экипировка скупается по SELL_RATE_EQUIP (утилизация), остальное — SELL_RATE."""
    it = ITEMS.get(key)
    if not it or it.get("type") == "quest":
        return 0
    price = it.get("price", 0)
    if price <= 0:
        return 0
    rate = SELL_RATE_EQUIP if it.get("type") in _EQUIP_TYPES else SELL_RATE
    return max(1, int(price * rate))


def is_sellable(key: str) -> bool:
    return sell_price(key) > 0


def validate():
    """Проверка ссылочной целостности контента. Бросает ValueError при ошибке."""
    errors = []

    # выходы комнат ведут в существующие комнаты
    for rid, room in WORLD.items():
        for direction, dest in room.get("exits", {}).items():
            if dest not in WORLD:
                errors.append(f"Комната '{rid}': выход '{direction}' → несуществующая '{dest}'")
        for mob in room.get("spawns", []):
            if mob not in MOBS:
                errors.append(f"Комната '{rid}': спавн несуществующего моба '{mob}'")
        for it in room.get("items", []):
            if it not in ITEMS:
                errors.append(f"Комната '{rid}': предмет '{it}' не найден")

    # скиллы классов существуют
    for cid, cls in CLASSES.items():
        for sk in cls.get("skills", []):
            if sk not in SKILLS:
                errors.append(f"Класс '{cid}': скилл '{sk}' не найден")

    # расы ссылаются на существующие классы
    for rid, race in RACES.items():
        for cid in race.get("allowed_classes", []):
            if cid not in CLASSES:
                errors.append(f"Раса '{rid}': разрешённый класс '{cid}' не найден")

    # витрина классов (Этап 4.2): обязательные поля для карточки выбора класса
    # (bot/ui.py render_classes) — role/difficulty/style/newbie_ok/pros у КАЖДОГО класса.
    _CLASS_SHOWCASE_FIELDS = ("role", "difficulty", "style", "newbie_ok", "pros")
    for cid, cls in CLASSES.items():
        for f in _CLASS_SHOWCASE_FIELDS:
            if f not in cls:
                errors.append(f"Класс '{cid}': отсутствует поле витрины '{f}'")
        _pros = cls.get("pros")
        if "pros" in cls and (not isinstance(_pros, list) or len(_pros) == 0):
            errors.append(f"Класс '{cid}': поле 'pros' должно быть непустым списком")

    # квесты: цели и награды существуют
    _exclusive_groups = {}                        # группа -> число квестов-членов
    for qid, q in QUESTS.items():
        obj = q.get("objective", {})
        otype = obj.get("type")
        if otype == "kill" and obj.get("mob") not in MOBS:
            errors.append(f"Квест '{qid}': цель-моб '{obj.get('mob')}' не найден")
        if otype == "collect" and obj.get("item") not in ITEMS:
            errors.append(f"Квест '{qid}': цель-предмет '{obj.get('item')}' не найден")
        # ── новые типы целей (этап 5.1) ──
        if otype == "talk" and obj.get("npc") not in NPCS:
            errors.append(f"Квест '{qid}': talk-цель — NPC '{obj.get('npc')}' не найден")
        if otype == "reach" and obj.get("room") not in WORLD:
            errors.append(f"Квест '{qid}': reach-цель — комната '{obj.get('room')}' не найдена")
        if otype == "use" and obj.get("item") not in ITEMS:
            errors.append(f"Квест '{qid}': use-цель — предмет '{obj.get('item')}' не найден")
        if otype == "choose":
            opts = obj.get("options") or []
            if not opts:
                errors.append(f"Квест '{qid}': choose-цель без опций")
            ids = [o.get("id") for o in opts]
            if any(not i for i in ids):
                errors.append(f"Квест '{qid}': choose-опция без id")
            if len(ids) != len(set(ids)):
                errors.append(f"Квест '{qid}': choose-опции с неуникальными id")
        for it in q.get("reward", {}).get("items", []):
            if it not in ITEMS:
                errors.append(f"Квест '{qid}': награда-предмет '{it}' не найдена")
        req = q.get("requires")
        if req and req not in QUESTS:
            errors.append(f"Квест '{qid}': требует несуществующий квест '{req}'")
        # ── последствия (этап 5.1) ──
        grp = q.get("exclusive_group")
        if grp:
            _exclusive_groups[grp] = _exclusive_groups.get(grp, 0) + 1
        for lq in (q.get("locks") or []):
            if lq not in QUESTS:
                errors.append(f"Квест '{qid}': locks ссылается на несуществующий квест '{lq}'")
        _oc = q.get("on_complete") or {}
        for _fac in (_oc.get("reputation") or {}):
            if _fac not in FACTIONS:
                errors.append(f"Квест '{qid}': on_complete.reputation — фракция '{_fac}' не найдена")
    # эксклюзив-группа осмысленна только при ≥2 членах (иначе нечего исключать)
    for grp, cnt in _exclusive_groups.items():
        if cnt < 2:
            errors.append(f"Эксклюзив-группа '{grp}': только {cnt} квест — нужно ≥2")

    # NPC: ссылочная целостность и ИИ-поля
    _IMPORTANCE = {"ambient", "common", "named", "key"}
    for nid, n in NPCS.items():
        fac = n.get("faction")
        if fac and fac not in FACTIONS:
            errors.append(f"NPC '{nid}': фракция '{fac}' не найдена в factions.yaml")
        imp = n.get("importance", "ambient")
        if imp not in _IMPORTANCE:
            errors.append(f"NPC '{nid}': importance '{imp}' вне {sorted(_IMPORTANCE)}")
    # каждый NPC, стоящий в комнате, должен быть описан в npcs.yaml
    for rid, room in WORLD.items():
        for nid in room.get("npc", []):
            if nid not in NPCS:
                errors.append(f"Комната '{rid}': NPC '{nid}' не описан в npcs.yaml")
    # квестодатели/приёмщики тоже должны существовать как NPC
    for qid, q in QUESTS.items():
        for key in ("giver", "turn_in"):
            who = q.get(key)
            if who and who not in NPCS:
                errors.append(f"Квест '{qid}': {key} '{who}' не описан в npcs.yaml")

    # рецепты крафта: входы и выход существуют
    for rid, r in RECIPES.items():
        for entry in r.get("inputs", []):
            if entry[0] not in ITEMS:
                errors.append(f"Рецепт '{rid}': ингредиент '{entry[0]}' не найден")
        if r.get("output") not in ITEMS:
            errors.append(f"Рецепт '{rid}': результат '{r.get('output')}' не найден")

    # лут мобов существует
    for mid, mob in MOBS.items():
        for entry in mob.get("loot", []):
            if entry[0] not in ITEMS:
                errors.append(f"Моб '{mid}': лут '{entry[0]}' не найден")
        for sk in mob.get("skills", []):
            if sk not in SKILLS:
                errors.append(f"Моб '{mid}': скилл '{sk}' не найден")

    # ── прогрессия к капу 60 (Этап 2 подготовки к бете) ──
    try:
        from .character import LEVEL_CAP
    except Exception:                # страховка от возможного цикла импортов
        LEVEL_CAP = 60               # дубль: синхронизировать с character.LEVEL_CAP

    # (а) классовые умения изучаются не позже капа
    for sid, sk in SKILLS.items():
        if isinstance(sk, dict) and sk.get("class") and "learn_level" in sk:
            if int(sk["learn_level"]) > LEVEL_CAP:
                errors.append(f"Умение '{sid}': learn_level {sk['learn_level']} > капа {LEVEL_CAP}")

    # (б) предмет с level_req выше капа обязан требовать реморт (вариант C)
    for iid, it in ITEMS.items():
        if not isinstance(it, dict):
            continue
        lr = it.get("level_req")
        if lr and int(lr) > LEVEL_CAP and int(it.get("remort_req", 0) or 0) < 1:
            errors.append(f"Предмет '{iid}': level_req {lr} > капа {LEVEL_CAP} без remort_req≥1")

    # (в) активные квесты: квестодатель существует (см. проверку giver/turn_in выше)
    #     и вход по уровню не выше капа
    for qid, q in QUESTS.items():
        ml = q.get("min_level")
        if ml and int(ml) > LEVEL_CAP:
            errors.append(f"Квест '{qid}': min_level {ml} > капа {LEVEL_CAP}")

    # (г) талантов хватает, чтобы вложить все очки к капу (сумма max_rank по классу ≥ очков)
    _talents = _load_optional("talents.yaml")
    _need = LEVEL_CAP // 4            # столько очков талантов даёт прогрессия к капу
    _tsum = {}
    for tid, t in _talents.items():
        if isinstance(t, dict) and t.get("class"):
            _tsum[t["class"]] = _tsum.get(t["class"], 0) + int(t.get("max_rank", 1))
    for cid in CLASSES:
        if _tsum.get(cid, 0) < _need:
            errors.append(f"Класс '{cid}': сумма max_rank талантов {_tsum.get(cid, 0)} < {_need}")

    # (д) реморт-предметы в лавках — предупреждение (не ошибка): их не надеть без реморта
    for _shop, _items in (SHOPS or {}).items():
        for _iid in (_items or []):
            _it = ITEMS.get(_iid)
            if isinstance(_it, dict) and int(_it.get("remort_req", 0) or 0) >= 1:
                print(f"⚠️  Лавка '{_shop}': предмет '{_iid}' требует реморт — "
                      f"покупатель не сможет надеть без перерождения")

    if errors:
        raise ValueError("Ошибки контента:\n" + "\n".join(errors))


if __name__ == "__main__":
    # Быстрая проверка ссылочной целостности контента из CLI:
    #   python3 -m engine.content
    try:
        validate()
        print("✅ Контент валиден: ссылочная целостность в порядке.")
    except ValueError as e:
        print(e)
        raise SystemExit(1)