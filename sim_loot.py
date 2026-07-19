# -*- coding: utf-8 -*-
"""Симулятор баланса дропа/аффиксов: распределение редкостей, частота дропа,
доля экипируемого (с учётом ограничений), вклад лута/аффиксов в силу героя."""
import random, collections
from engine import rarity, equip
from engine.content import ITEMS
from engine.character import Character
from engine.loop import _pool_for

RAR_ORDER = rarity.RARITY_ORDER


def _emo(r): return rarity.META[r]["emoji"]


def drop_distribution(seed=1, per_band=200_000):
    random.seed(seed)
    print("═"*70)
    print("  1) ЧАСТОТА ДРОПА И РАСПРЕДЕЛЕНИЕ РЕДКОСТЕЙ (на килл)")
    print("═"*70)
    bands = [(5, "1–9"), (20, "10–29"), (50, "30–69"), (85, "70–100")]
    for lvl, label in bands:
        pool = _pool_for(lvl)
        cnt = collections.Counter(); drops = 0
        for _ in range(per_band):
            d = rarity.roll_drop(lvl, pool, boss=False)
            if d:
                drops += 1; cnt[rarity.rarity_of(d)] += 1
        per = " ".join(f"{_emo(r)}{cnt[r]*100/max(1,drops):.1f}%" for r in RAR_ORDER if cnt[r])
        print(f"  ур.{lvl:>3} ({label:>6}): дроп {drops*100/per_band:.1f}% killов | {per}")
    # боссы и рейд
    for tag, boss, n in (("обычный босс", True, 40000),):
        cnt = collections.Counter()
        for _ in range(n):
            d = rarity.roll_drop(85, _pool_for(85), boss=boss)
            cnt[rarity.rarity_of(d)] += 1
        print(f"  {tag} ур.85: " + " ".join(f"{_emo(r)}{cnt[r]*100/n:.1f}%" for r in RAR_ORDER if cnt[r]))


def affix_power(seed=2):
    print("\n"+"═"*70)
    print("  2) ВКЛАД РЕДКОСТИ/АФФИКСОВ В СИЛУ (одинаковая база, воин ур.75)")
    print("═"*70)
    base = "g_sword_75"
    ch = Character(uid=1, name="t", cls="warrior", race="human"); ch.level=75; ch.init_vitals()
    base_atk = ch.attack_power
    print(f"  база-меч: {ITEMS[base]['name']} (atk {ITEMS[base]['bonus']['atk']})")
    for rar in RAR_ORDER:
        seeds = [random.randint(1,10**9) for _ in range(2000)]
        atks=[]
        for s in seeds:
            key = rarity.encode(base, rar, s if rar in rarity.AFFIX_COUNT else None)
            ch.equipment["weapon"]=key; ch.set_durab("weapon",100)
            atks.append(ch.attack_power)
        avg=sum(atks)/len(atks)
        spread = max(atks)-min(atks)
        print(f"  {_emo(rar)} {rarity.META[rar]['name']:13}: атака ~{avg:6.0f}  "
              f"(к голому {avg-base_atk:+5.0f}, разброс аффиксов ±{spread//2})")


def _biased_drop(mob_lvl, cls):
    """Дроп со смещением 60% к классу убийцы (как в loop)."""
    pool=_pool_for(mob_lvl)
    if random.random() < 0.6:
        cp=[k for k in pool if equip.class_can_use(cls,k)]
        if cp: pool=cp
    return rarity.roll_drop(mob_lvl, pool, boss=False)


def equippability(seed=3, n=40000):
    print("\n"+"═"*70)
    print("  3) ДОЛЯ ДРОПА, КОТОРУЮ КЛАСС МОЖЕТ НАДЕТЬ (ур.60, смещение к классу)")
    print("═"*70)
    random.seed(seed)
    for cls in ("warrior","mage","rogue","priest","necromancer"):
        ch=Character(uid=2,name="x",cls=cls,race="human"); ch.level=60; ch.init_vitals()
        ok=tot=0
        for _ in range(n):
            d=_biased_drop(60, cls)
            if not d: continue
            tot+=1
            if equip.can_equip(ch,d)[0]: ok+=1
        print(f"  {cls:11}: годного снаряжения {ok*100/max(1,tot):.0f}%  (из {tot} дропов)")


def upgrade_cadence(seed=4):
    print("\n"+"═"*70)
    print("  4) КАК ЧАСТО ЛУТ = АПГРЕЙД (воин фармит ровного моба, носит лучшее)")
    print("═"*70)
    random.seed(seed)
    for lvl in (10,40,80):
        ch=Character(uid=3,name="w",cls="warrior",race="human"); ch.level=lvl; ch.init_vitals()
        pool=_pool_for(lvl)
        upgrades=0; kills=0; wbest=0; abest=0
        def score():
            return ch.attack_power + ch.defense
        s0=None
        for _ in range(20000):
            d=rarity.roll_drop(lvl,pool,boss=False)
            kills+=1
            if not d: continue
            meta=ITEMS[d]
            if not equip.can_equip(ch,d)[0]: continue
            slot=meta.get("slot")
            if slot=="ring": slot="ring1"
            old=ch.equipment.get(slot)
            before=score()
            ch.equipment[slot]=d; ch.set_durab(slot,100)
            if score()>before: upgrades+=1
            else:
                ch.equipment[slot]=old   # откат, не апгрейд
        print(f"  ур.{lvl:>2}: апгрейдов {upgrades} за {kills} killов "
              f"(~1 апгрейд на {kills//max(1,upgrades)} killов) | финал атака {ch.attack_power}")


if __name__ == "__main__":
    drop_distribution()
    affix_power()
    equippability()
    upgrade_cadence()
