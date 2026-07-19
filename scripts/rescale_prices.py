# -*- coding: utf-8 -*-
"""Ребаланс цен ЭКИПИРОВКИ под цель «топ-предмет уровня L ≈ 10–15 часов дохода L».

Зачем: до спринта 6 цены снаряжения были сжаты в десятки–сотни раз (генерируемые
g_*-предметы почти не дорожали с уровнем: топ L60 стоил ~210 монет при доходе
~16 000/ч). Этот скрипт задаёт ПРИНЦИПИАЛЬНУЮ кривую цен от данных игры и
переписывает поле price у предметов type ∈ {weapon, armor, accessory}.

Модель цены (всё в ОТОБРАЖАЕМЫХ монетах, потом ×COIN обратно во внутр.):
  income(L)      — доход/час на уровне L (кусочно-линейная интерполяция якорей
                   калибровки спринта 5: L5=2450 … L60=16275 монет/ч).
  ceiling(L)     — потолок цены = HOURS_TOP × income (монотонно неубывающий по L):
                   лучший предмет уровня стоит ≈ HOURS_TOP часов дохода уровня.
  power(item)    — взвешенная сумма бонусов (atk/defense=1, атрибуты=1.5,
                   hp=0.03, crit=2) — прокси силы предмета.
  ref_power(L)   — «сила топа уровня»: интерполяция по чистой лестнице g_*-предметов
                   (у них явные level_req 1/15/30/45/60/75/90).
  rank           = power/ref_power (1.0 ≈ топ уровня).
  price(item)    = ceiling(L) × clamp(rank, FLOOR_FRAC, TOP_FRAC).
                   Обычные предметы уровня выходят 15–~100% от топа (см. FLOOR_FRAC).

Уровень предмета берём через engine.equip.level_req (та же функция, что гейтит
надевание): уважает явное поле level_req, иначе выводит из суммы бонусов.

ИДЕМПОТЕНТНОСТЬ: ref_power считается по СИЛЕ (не по цене) g_*-предметов, поэтому
повторный прогон даёт те же цены. Предметы без поля price (луто-эксклюзивы вроде
проклятый_клинок, серебряное_кольцо) НЕ дописываются — остаются без цены (их
нельзя ни продать, ни выставить осмысленно; так задумано).

НЕ трогаем: consumable / material / quest / rune (расходники, материалы, квесты,
руны — их экономика отдельная; подъём сломал бы онбординг и крафт).

CLI:
  python3 -m scripts.rescale_prices --dry           # показать таблицу, не писать
  python3 -m scripts.rescale_prices --write         # переписать data/items*.yaml
  python3 -m scripts.rescale_prices --map           # выгрузить JSON {key: new_price_internal}
"""
import os
import sys
import json
import collections

# ── якоря дохода/час (монеты), калибровка спринта 5 ──
INCOME_ANCHORS = {5: 2450, 15: 7000, 30: 9450, 45: 16750, 60: 16275}
HOURS_TOP = 11.0            # целимся в середину коридора 10–15 ч
FLOOR_FRAC = 0.15          # обычный/слабый предмет уровня ≥ 15% от топа
TOP_FRAC = 1.15            # редкий предмет чуть выше «эталонного топа» (аффиксы поверх)
COIN = 100                 # внутр. единиц в 1 отображаемой монете (money.COIN)


def income(level: int) -> float:
    """Доход/час (монеты) на уровне L: кусочно-линейно между якорями, плоско вне."""
    xs = sorted(INCOME_ANCHORS)
    if level <= xs[0]:
        # ниже L5 — линейно к «полу онбординга» ~500 монет/ч на L0
        return 500 + (INCOME_ANCHORS[xs[0]] - 500) * (level / xs[0])
    if level >= xs[-1]:
        return INCOME_ANCHORS[xs[-1]]
    for i in range(len(xs) - 1):
        a, b = xs[i], xs[i + 1]
        if a <= level <= b:
            return INCOME_ANCHORS[a] + (INCOME_ANCHORS[b] - INCOME_ANCHORS[a]) * (level - a) / (b - a)
    return INCOME_ANCHORS[xs[-1]]


def ceiling(level: int) -> float:
    """Потолок цены уровня (монеты), монотонно неубывающий: HOURS_TOP × max income≤L."""
    return max(income(x) for x in range(1, max(1, level) + 1)) * HOURS_TOP


def power(meta: dict) -> float:
    """Прокси силы предмета: взвешенная сумма модулей бонусов."""
    b = meta.get("bonus", {}) or {}
    p = 0.0
    for k, x in b.items():
        if not isinstance(x, (int, float)):
            continue
        if k in ("atk", "defense"):
            p += abs(x) * 1.0
        elif k == "hp":
            p += abs(x) * 0.03
        elif k == "crit":
            p += abs(x) * 2.0
        else:                      # str/dex/int/spi и прочие атрибуты
            p += abs(x) * 1.5
    return max(0.5, p)


def _ref_power_table(items: dict):
    """Лестница «сила топа уровня» по чистым g_*-предметам (явные level_req)."""
    gpow = collections.defaultdict(float)
    for k, v in items.items():
        if k.startswith("g_") and "#" not in k and isinstance(v, dict):
            lr = int(v.get("level_req", 1) or 1)
            gpow[lr] = max(gpow[lr], power(v))
    return gpow


def make_pricer(items: dict):
    """Вернёт функцию new_price_coins(meta) на основе лестницы g_* из items."""
    import engine.equip as _equip
    gpow = _ref_power_table(items)
    refL = sorted(gpow)
    if not refL:
        refL = [1]
        gpow[1] = 10.0

    def ref_power(level: int) -> float:
        if level <= refL[0]:
            return gpow[refL[0]] * (level / refL[0]) if refL[0] > 0 else gpow[refL[0]]
        if level >= refL[-1]:
            return gpow[refL[-1]]
        for i in range(len(refL) - 1):
            a, b = refL[i], refL[i + 1]
            if a <= level <= b:
                return gpow[a] + (gpow[b] - gpow[a]) * (level - a) / (b - a)
        return gpow[refL[-1]]

    def new_price_coins(meta: dict) -> int:
        L = _equip.level_req(meta)
        rp = ref_power(L)
        rank = power(meta) / rp if rp > 0 else 0.5
        frac = max(FLOOR_FRAC, min(TOP_FRAC, rank))
        return int(round(ceiling(L) * frac))

    return new_price_coins


EQUIP_TYPES = ("weapon", "armor", "accessory")


def compute_map(items: dict):
    """{key: new_price_internal} для всех предметов-экипировки (внутр. единицы)."""
    pricer = make_pricer(items)
    out = {}
    for k, v in items.items():
        if not isinstance(v, dict):
            continue
        if v.get("type") in EQUIP_TYPES and "#" not in k:
            out[k] = pricer(v) * COIN
    return out


def _rewrite_file(path: str, new_map: dict) -> int:
    """Переписать поле price построчно (сохраняя формат YAML). -> число изменений.

    Поддерживает оба стиля из data/*: блочный (price: на своей строке под ключом
    предмета) и флоу-инлайн ({ ... price: N ... }). Меняем ТОЛЬКО существующие
    поля price у предметов, чьи ключи есть в new_map (не дописываем новые)."""
    import re
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changed = 0
    cur_key = None
    # ключ предмета: юникод-слово в начале строки (поддержка кириллических ключей)
    key_re = re.compile(r"^(\w[\w]*):\s*(.*)$", re.UNICODE)
    price_block_re = re.compile(r"^(\s+price:\s*)(\d+)(.*)$")

    for i, line in enumerate(lines):
        m = key_re.match(line)
        if m:
            cur_key = m.group(1)
            rest = m.group(2)
            # флоу-инлайн: цена в той же строке ({ ... price: N ... })
            if cur_key in new_map and "price:" in rest:
                newp = new_map[cur_key]
                rest2 = re.sub(r"price:\s*\d+", f"price: {newp}", rest)
                if rest2 != rest:
                    lines[i] = f"{cur_key}: {rest2}" + ("\n" if not rest2.endswith("\n") else "")
                    changed += 1
            continue
        # блочный price под текущим ключом
        pm = price_block_re.match(line)
        if pm and cur_key in new_map:
            newp = new_map[cur_key]
            tail = pm.group(3)
            lines[i] = f"{pm.group(1)}{newp}{tail}" + ("\n" if not tail.endswith("\n") else "")
            changed += 1

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return changed


def main():
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = os.path.join(root, "data")
    # объединённый каталог для лестницы g_* и вычисления цен
    merged = {}
    for name in ("items.yaml", "items_gen.yaml"):
        p = os.path.join(data, name)
        if os.path.exists(p):
            merged.update(yaml.safe_load(open(p, encoding="utf-8")) or {})
    price_map = compute_map(merged)

    if "--map" in sys.argv:
        print(json.dumps(price_map, ensure_ascii=False))
        return

    if "--dry" in sys.argv or "--write" not in sys.argv:
        # таблица: топ по уровню
        import engine.equip as _equip
        byL = collections.defaultdict(list)
        for k, v in merged.items():
            if isinstance(v, dict) and v.get("type") in EQUIP_TYPES and "#" not in k:
                byL[_equip.level_req(v)].append((power(v), v.get("price", 0), price_map[k], k))
        print("L    ceil(coins)  top_item              cur_c   new_c    mult")
        for L in sorted(byL):
            items = sorted(byL[L], reverse=True)
            pw, cur, new, k = items[0]
            cur_c = cur // COIN
            new_c = new // COIN
            mult = (new_c / cur_c) if cur_c > 0 else float("inf")
            ms = f"{mult:.0f}x" if cur_c > 0 else "n/a"
            print(f"{L:>3}  {ceiling(L):>10.0f}  {k[:20]:<20} {cur_c:>6} {new_c:>7}  {ms}")
        if "--write" not in sys.argv:
            print("\n(сухой прогон; для записи: --write)")
            return

    if "--write" in sys.argv:
        total = 0
        for name in ("items.yaml", "items_gen.yaml"):
            p = os.path.join(data, name)
            if os.path.exists(p):
                n = _rewrite_file(p, price_map)
                total += n
                print(f"  {name}: изменено price у {n} предметов")
        print(f"ИТОГО переписано: {total}")


if __name__ == "__main__":
    main()
