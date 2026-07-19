# -*- coding: utf-8 -*-
"""
Автономные тесты экономики золота (без Telegram и без PostgreSQL).
Запуск из каталога проекта:
    python3 test_economy.py

Проверяет калибровку золотостоков:
  • комиссия аукциона 5% с корректным округлением выручки продавца;
  • покупные титулы-косметика: списание денег, выдача extra_title,
    повторная покупка невозможна, отказ при нехватке денег;
  • награды подземелий равны новым значениям dungeons.yaml;
  • пороги/награды лиг (seasons.TIERS) равны новым числам;
  • уровне-зависимый срез боевой голды gold_rate_for (онбординг не задет,
    эндгейм обрезан) — реально подключён в loop.on_mob_death;
  • голда мировых боссов срезана у выявленных перекосов.
"""
import sys
import random

from engine.character import Character
from engine import money, titles, seasons, content, dungeon, combat
from engine.auction import AuctionManager, AUCTION_FEE

random.seed(2024)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def new_char(cls="warrior", race="human", uid=1, gold=None):
    ch = Character(uid=uid, name="Тест", cls=cls, race=race)
    ch.init_vitals()
    if gold is not None:
        ch.gold = gold
    return ch


# ─────────────────── 1. КОМИССИЯ АУКЦИОНА 5% ───────────────────
print("\n[1] Комиссия аукциона 5% и округление выручки")
check("AUCTION_FEE == 0.05 (5%)", abs(AUCTION_FEE - 0.05) < 1e-9)

import tempfile, os
_tmp = os.path.join(tempfile.gettempdir(), "test_auction_econ.json")
try:
    os.remove(_tmp)
except OSError:
    pass
_am = AuctionManager(_tmp)

# лот за 10000: продавец получает 95% = 9500
_lid = _am.create_listing(uid=1, name="Продавец", item="ржавый_меч", price=10000)
_st, _lot = _am.buy(_lid, buyer_uid=2)
check("покупка лота проходит (status ok)", _st == "ok")
check("продавец получает 95% от 10000 = 9500", _am.pending_payout(1) == 9500)

# округление: 999 -> int(999*0.95)=949 (усечение вниз, как в коде)
_lid2 = _am.create_listing(uid=1, name="Продавец", item="ржавый_меч", price=999)
_am.buy(_lid2, buyer_uid=2)
check("выручка с 999 округляется усечением: 9500 + 949 = 10449",
      _am.pending_payout(1) == 9500 + int(999 * 0.95))

# claim обнуляет почту
_claimed = _am.claim_payout(1)
check("claim_payout выдаёт всю накопленную выручку", _claimed == 10449)
check("после claim почта пуста", _am.pending_payout(1) == 0)

# продавец не может купить свой лот (комиссия не начисляется)
_lid3 = _am.create_listing(uid=5, name="Сам", item="ржавый_меч", price=5000)
_st3, _ = _am.buy(_lid3, buyer_uid=5)
check("нельзя купить собственный лот (status own)", _st3 == "own")
try:
    os.remove(_tmp)
except OSError:
    pass


# ─────────────────── 2. ПОКУПНЫЕ ТИТУЛЫ ───────────────────
print("\n[2] Покупные титулы-косметика (сток золота)")
check("в лавке ровно 3 титула", len(titles.TITLES) == 3)
_prices = sorted(t["price"] for t in titles.TITLES.values())
# спринт 6: цены титулов подняты под новый масштаб цен экипировки (якорь —
# снаряжение 45–60 ур.): 10M / 18M / 30M внутр. = 100k / 180k / 300k монет.
check("цены титулов = 10M / 18M / 30M внутр.", _prices == [10_000_000, 18_000_000, 30_000_000])
check("титулы дороже прежних 50k/200k/500k (ребаланс спринта 6)",
      _prices[0] > 50000 and _prices[1] > 200000 and _prices[2] > 500000)

# покупка списывает деньги и выдаёт extra_title
_patron_price = titles.TITLES["patron_aeldmar"]["price"]
_ch = new_char(gold=_patron_price + 10000)
_g0 = _ch.gold
_ok, _msg = titles.buy(_ch, "patron_aeldmar")
check("покупка титула проходит при достатке денег", _ok)
check("деньги списаны ровно на цену титула", _ch.gold == _g0 - _patron_price)
_tname = titles.TITLES["patron_aeldmar"]["name"]
check("титул добавлен в ch.flags['extra_titles']", _tname in _ch.flags.get("extra_titles", []))

# титул валиден для achievements.set_title (та же система)
from engine import achievements
check("купленный титул проходит set_title (валиден к показу)",
      achievements.set_title(_ch, _tname) is True)
check("active_title равен купленному после set_title",
      achievements.active_title(_ch) == _tname)

# повторная покупка невозможна и денег не списывает
_g1 = _ch.gold
_ok2, _msg2 = titles.buy(_ch, "patron_aeldmar")
check("повторная покупка того же титула невозможна", _ok2 is False)
check("при отказе повторной покупки деньги НЕ списаны", _ch.gold == _g1)
check("extra_titles не задвоился", _ch.flags["extra_titles"].count(_tname) == 1)

# отказ при нехватке денег
_poor = new_char(gold=1000)
_ok3, _msg3 = titles.buy(_poor, "abyss_benefactor")   # стоит 500000
check("покупка при нехватке денег отклонена", _ok3 is False)
check("при нехватке денег баланс не изменился (1000)", _poor.gold == 1000)
check("несуществующий титул -> отказ",
      titles.buy(_poor, "нет_такого")[0] is False)


# ─────────────────── 3. НАГРАДЫ ПОДЗЕМЕЛИЙ = YAML ───────────────────
print("\n[3] Награды подземелий равны новым значениям dungeons.yaml")
_expected_dung = {
    "mines": 15500, "forest": 15500, "swamp": 15500, "windpass": 15500,
    "ruins": 24000, "ashwaste": 24000, "deepcity": 36500,
}
for _did, _g in _expected_dung.items():
    _cfg = dungeon.DUNGEONS.get(_did, {})
    check(f"данж '{_did}' gold == {_g}", _cfg.get("reward", {}).get("gold") == _g)
# срез действительно понизил относительно старых конских сумм
check("данж 'deepcity' срезан с 220000 (стало <= 40000)",
      dungeon.DUNGEONS["deepcity"]["reward"]["gold"] <= 40000)

# on_kill выдаёт ровно reward.gold без ремортов
_dch = new_char(uid=42)
_dch.level = 14
_dch.flags["dungeon_run"] = "deepcity"
_g_before = _dch.gold
dungeon.on_kill(_dch, dungeon.DUNGEONS["deepcity"]["boss_mob"])
check("on_kill данжа без ремортов даёт ровно reward.gold (36500)",
      _dch.gold - _g_before == 36500)


# ─────────────────── 4. ЛИГИ (TIERS) = НОВЫЕ ЧИСЛА ───────────────────
print("\n[4] Пороги и награды лиг seasons.TIERS равны новым числам")
_expected_tiers = [
    (0,     "Бронза",   5000),
    (500,   "Серебро",  15000),
    (2000,  "Золото",   40000),
    (6000,  "Платина",  120000),
    (15000, "Алмаз",    240000),
    (40000, "Легенда",  480000),
]
check("в TIERS ровно 6 лиг", len(seasons.TIERS) == 6)
for (_thr, _name, _gold) in _expected_tiers:
    _row = next((t for t in seasons.TIERS if t[1] == _name), None)
    check(f"лига '{_name}': порог {_thr}, золото {_gold}",
          _row is not None and _row[0] == _thr and _row[3] == _gold)
check("верхняя лига 'Легенда' срезана с 1000000 (стало <= 500000)",
      seasons.TIERS[-1][3] <= 500000)
# _reward_for корректно берёт золото по очкам
check("_reward_for(40000+) == золото Легенды (480000)",
      seasons._reward_for(50000) == 480000)
check("_reward_for(0) == золото Бронзы (5000)", seasons._reward_for(0) == 5000)


# ─────────────────── 5. gold_rate_for: ОНБОРДИНГ vs ЭНДГЕЙМ ───────────────────
print("\n[5] Уровне-зависимый срез боевой голды gold_rate_for")
check("gold_rate_for(1) == 1.0 (онбординг полный)", content.gold_rate_for(1) == 1.0)
check("gold_rate_for(10) == 1.0 (граница онбординга)", content.gold_rate_for(10) == 1.0)
check("gold_rate_for(60) == 0.35 (эндгейм-пол)", abs(content.gold_rate_for(60) - 0.35) < 1e-9)
check("gold_rate_for монотонно убывает 10->30->60",
      content.gold_rate_for(10) > content.gold_rate_for(30) > content.gold_rate_for(60))

# срез РЕАЛЬНО подключён в loop.on_mob_death (регресс-защита от «объявлен, но не вызван»)
import asyncio
from engine.world import World, MobInstance
from engine.loop import GameLoop
from engine.content import MOBS

async def _combat_gold(level, mob_id):
    async def _noop(*a, **k): pass
    world = World()
    gl = GameLoop(world, {}, _noop, _noop)
    ch = new_char(uid=1)
    ch.level = level
    # выдать достижения заранее, чтобы их золото не искажало боевую голду
    ch.flags["kills"] = 10 ** 9
    achievements.check(ch)
    gl.chars = {1: ch}
    m = MobInstance("k", mob_id, "village")
    g0 = ch.gold
    await gl.on_mob_death(m, [ch])
    return ch.gold - g0

# Чтобы изолировать именно gold_rate_for (без примеси DIFF_GOLD за сложность),
# берём НИЗКОУРОВНЕВОГО моба (ур. ≤ 8): для игроков ур.30 и ур.60 он «зелёный»
# (diff ≤ 0), значит DIFF_GOLD одинаков (1.0). Тогда отношение голды = отношение
# самих gold_rate: gold_rate_for(60)/gold_rate_for(30) = 0.35/0.74 ≈ 0.47.
_low_mob = next((k for k, v in MOBS.items()
                 if not v.get("boss") and v.get("level", 1) <= 8 and v.get("gold", 0) > 0),
                next(k for k, v in MOBS.items() if not v.get("boss")))
_g30 = asyncio.run(_combat_gold(30, _low_mob))
_g60 = asyncio.run(_combat_gold(60, _low_mob))
check("боевая голда на ур.60 срезана относительно ур.30 (gold_rate подключён)",
      _g60 < _g30)
_ratio_expected = content.gold_rate_for(60) / content.gold_rate_for(30)  # ~0.473
check("на «зелёном» мобе голда ур.60/ур.30 ≈ gold_rate(60)/gold_rate(30) (±0.06)",
      _g30 > 0 and abs(_g60 / _g30 - _ratio_expected) < 0.06)


# ─────────────────── 6. ГОЛДА БОССОВ (срез перекосов) ───────────────────
print("\n[6] Голда мировых боссов: срез выявленных перекосов")
check("предвечная_бездна срезана с 350000 -> 250000",
      MOBS["предвечная_бездна"]["gold"] == 250000)
check("сердце_глуби срезано с 120000 -> 52000",
      MOBS["сердце_глуби"]["gold"] == 52000)
check("падший_король срезан с 50000 -> 22000",
      MOBS["падший_король"]["gold"] == 22000)
# честно фиксируемые НЕ тронутые (корректны против цены топ-предмета)
check("титан_первотумана оставлен (170000, ~0.4x топ-предмета)",
      MOBS["титан_первотумана"]["gold"] == 170000)


# ─────────────── 7. ЦЕНЫ ЭКИПИРОВКИ: ТОП ЗА 8–15 Ч ДОХОДА (спринт 6) ───────────────
# Всё считаем ОТ ДАННЫХ ИГРЫ (цены предметов + якоря дохода спринта 5), а не
# хардкодим цены — тест переживёт будущие ребалансы, если кривая сохраняет цель.
print("\n[7] Цены экипировки: топ-предмет за 8–15 часов дохода уровня")
from engine.content import ITEMS
from engine import equip as _equip
from engine.loop import _build_rare_pool

# доход/час (монеты) по уровням — якоря калибровки спринта 5 (боевая голда)
_INCOME_ANCHOR = {5: 2450, 15: 7000, 30: 9450, 45: 16750, 60: 16275}


def _income_per_hour(level):
    xs = sorted(_INCOME_ANCHOR)
    if level <= xs[0]:
        return _INCOME_ANCHOR[xs[0]]
    if level >= xs[-1]:
        return _INCOME_ANCHOR[xs[-1]]
    for i in range(len(xs) - 1):
        a, b = xs[i], xs[i + 1]
        if a <= level <= b:
            return _INCOME_ANCHOR[a] + (_INCOME_ANCHOR[b] - _INCOME_ANCHOR[a]) * (level - a) / (b - a)
    return _INCOME_ANCHOR[xs[-1]]


def _top_item_price_coins(level):
    """Цена (монеты) самого дорогого предмета экипировки, надеваемого на уровне L."""
    cands = [v.get("price", 0) for k, v in ITEMS.items()
             if v.get("type") in ("weapon", "armor", "accessory") and "#" not in k
             and _equip.level_req(v) <= level and v.get("price", 0) > 0]
    return (max(cands) // money.COIN) if cands else 0


# ЦЕЛЬ: топ-предмет уровня L стоит 8–15 часов дохода L (на 15/30/45/60).
for _L in (15, 30, 45, 60):
    _top = _top_item_price_coins(_L)
    _inc = _income_per_hour(_L)
    _hours = _top / _inc if _inc > 0 else 0
    check(f"ур.{_L}: топ-предмет ({_top} монет) = {_hours:.1f} ч дохода — в коридоре 8–15",
          8.0 <= _hours <= 15.0)

# после ребаланса топ-предметы стали ДОРОГО (не 340–4000 монет, как в спринте 5):
check("топ-предмет ур.45 теперь дороже 100 000 монет (был ≤4000)",
      _top_item_price_coins(45) > 100_000)
check("топ-предмет ур.60 теперь дороже 100 000 монет",
      _top_item_price_coins(60) > 100_000)

# ─────────────── 8. ПРОДАЖА: ЭКИПИРОВКА << МАТЕРИАЛЫ (по доле) ───────────────
print("\n[8] Продажа добычи: доля скупки экипировки НИЖЕ доли материалов")
check("SELL_RATE_EQUIP < SELL_RATE (экипировка скупается дешевле в доле)",
      content.SELL_RATE_EQUIP < content.SELL_RATE)
# доля от цены: у экипировки строго меньше, чем у материала/расходника
_eq_key = "стальной_меч"      # weapon с ценой
_mat_key = "самоцвет"          # material с ценой
_eq_frac = content.sell_price(_eq_key) / ITEMS[_eq_key]["price"]
_mat_frac = content.sell_price(_mat_key) / ITEMS[_mat_key]["price"]
check("доля скупки экипировки < доля скупки материала",
      _eq_frac < _mat_frac)
check("материал скупается по 60% (не изменилось)", abs(_mat_frac - 0.6) < 0.02)
check("экипировка скупается заметно дешевле 60% (утилизация, анти-фонтан)",
      _eq_frac <= 0.10)
# квест-предмет по-прежнему не продаётся
check("квест-предмет не скупается (sell_price=0)", content.sell_price("знак_посвящения") == 0)

# ─────────────── 9. ПОЛОСЫ РЕДКОГО ЛУТА НЕПУСТЫ (пересчёт порогов) ───────────────
print("\n[9] Полосы бонусного лута rare_pool непусты после ребаланса")
_rp = _build_rare_pool()
check("жёлтая полоса (средние материалы/расходники) непуста", len(_rp["yellow"]) > 0)
check("красная полоса (дорогие аксессуары/топ-расходники) непуста", len(_rp["red"]) > 0)
# красная полоса действительно про ДОРОГИЕ аксессуары (а не про любой аксессуар)
_red_acc = [k for k in _rp["red"] if ITEMS[k].get("type") == "accessory"]
check("в красной полосе есть дорогие аксессуары", len(_red_acc) > 0)
_all_acc = [k for k, v in ITEMS.items()
            if v.get("type") == "accessory" and "#" not in k and v.get("price", 0) > 0]
check("красная полоса — ПОДМНОЖЕСТВО аксессуаров (порог отсекает дешёвые)",
      len(_red_acc) < len(_all_acc))

# ─────────────── 10. КРАФТ РЕНТАБЕЛЕН (вход < результат, ~60–80%) ───────────────
print("\n[10] Крафт рентабелен: стоимость входа < цены результата (~60–80%)")
from engine.content import RECIPES as _RECIPES
_rent_ok = 0
_rent_total = 0
for _rid, _r in _RECIPES.items():
    _out = _r.get("output")
    _rp2 = ITEMS.get(_out, {}).get("price", 0)
    if _rp2 <= 0:
        continue
    _mat = sum(ITEMS.get(_i, {}).get("price", 0) * _q for _i, _q in _r.get("inputs", []))
    _in = _mat + _r.get("gold", 0)
    _rent_total += 1
    # результат должен быть строго дороже входа (иначе крафт мёртв)
    if _in < _rp2:
        _rent_ok += 1
check("у каждого рецепта результат дороже суммарного входа (крафт жив)",
      _rent_ok == _rent_total and _rent_total > 0)
# и вход в разумном коридоре 55–85% цены результата (выгодно, но не даром)
_band_ok = True
for _rid, _r in _RECIPES.items():
    _out = _r.get("output")
    _rp2 = ITEMS.get(_out, {}).get("price", 0)
    if _rp2 <= 0:
        continue
    # pity-крафт «конденсации» (этап 6.2) — намеренно ВНЕ коридора: вход —
    # туманная пыль (price 0) + скромное золото, выход дороже входа. Это
    # страховка от засухи после серии неудач, а не выгодная ковка; проверяется
    # отдельно в test_salvage.py.
    if any(_i == "туманная_пыль" for _i, _q in _r.get("inputs", [])):
        continue
    _in = sum(ITEMS.get(_i, {}).get("price", 0) * _q for _i, _q in _r.get("inputs", [])) + _r.get("gold", 0)
    if not (0.55 <= _in / _rp2 <= 0.85):
        _band_ok = False
check("стоимость входа крафта в коридоре 55–85% цены результата (без pity)", _band_ok)
check("у крафт-результата 'кристальный_амулет' есть цена (был без price)",
      ITEMS.get("кристальный_амулет", {}).get("price", 0) > 0)


# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
