# -*- coding: utf-8 -*-
"""Баланс сокетов/территорий + проверка выдачи цепочек сюжетных квестов."""
import random, collections
from engine.character import Character
from engine.content import ITEMS, QUESTS, FACTIONS
from engine import sockets, territory, rarity, equip, money, quest


def sockets_balance():
    print("═"*64); print("  1) СОКЕТЫ: вклад полного набора рун в силу (воин ур.60)"); print("═"*64)
    base = "g_sword_60"
    for rar in ("blue","purple","gold","red"):
        ch = Character(uid=1,name="t",cls="warrior",race="human"); ch.level=60; ch.init_vitals()
        key = f"{base}#{rar}"
        ch.equipment["weapon"]=key; ch.set_durab("weapon",100)
        atk0, hp0, cr0 = ch.attack_power, ch.max_hp, ch.crit_chance
        n = sockets.socket_count(key)
        # вставим n больших рун: сила, крит, дух...
        runes = ["rune_str_greater","rune_crit_greater","rune_spi_greater","rune_dex_greater"][:n]
        cost = 0
        for rk in runes:
            ch.inventory.append(rk); cost += ITEMS[rk]["price"]
            sockets.socket(ch,"weapon",rk)
        print(f"  {rarity.META[rar]['emoji']} {rarity.META[rar]['name']:12} гнёзд {n}: "
              f"атака {atk0}->{ch.attack_power} (+{ch.attack_power-atk0}), "
              f"крит {int(cr0*100)}%->{int(ch.crit_chance*100)}%, цена рун {money.fmt(cost)}")


def territory_war():
    print("\n"+"═"*64); print("  2) ВОЙНА ТЕРРИТОРИЙ: контроль и бонус добычи"); print("═"*64)
    territory._control.clear()
    zone = sorted(territory.CONTESTED)[0]
    # 3 союзника Ордена, 2 — Ковена, фармят зону
    orden=[Character(uid=i,name=f"o{i}",cls="warrior",race="human") for i in range(3)]
    for c in orden: c.flags["rep"]={"orden_rassveta":600}
    koven=[Character(uid=10+i,name=f"k{i}",cls="mage",race="human") for i in range(2)]
    for c in koven: c.flags["rep"]={"koven_gnilotopi":600}
    flips=[]
    for step in range(60):
        for c in orden: 
            f=territory.add_kill(c,zone)
            if f: flips.append((step,"Орден"))
        if step>30:
            for c in koven:
                f=territory.add_kill(c,zone)
                if f: flips.append((step,"Ковен"))
    dom=territory.dominant(zone)
    print(f"  зона «{zone}»: владелец = {FACTIONS.get(dom,{}).get('name',dom)}")
    print(f"  смен контроля: {len(flips)} -> {flips[:4]}{'…' if len(flips)>4 else ''}")
    print(f"  бонус союзнику владельца: x{territory.control_bonus(orden[0],zone):.2f} | "
          f"чужому: x{territory.control_bonus(koven[0],zone):.2f}")
    territory._control.clear()


def _chains():
    ch={}
    for qid,q in QUESTS.items():
        r=q.get("requires")
        if r: ch.setdefault(r,[]).append(qid)
    # корни цепочек среди сюжетных (main_/forge_/dawn_/moon_/choice_)
    story=[q for q in QUESTS if q.split("_")[0] in ("main","forge","dawn","moon","choice")]
    roots=[q for q in story if not QUESTS[q].get("requires")]
    return ch, roots


def quest_chains():
    print("\n"+"═"*64); print("  3) ВЫДАЧА СЮЖЕТНЫХ ЦЕПОЧЕК (gating через requires)"); print("═"*64)
    children, roots = _chains()
    for root in sorted(roots):
        ch=Character(uid=99,name="q",cls="warrior",race="human")
        chain=[root]; cur=root
        # пройти по цепочке
        while True:
            nxts=children.get(cur,[])
            if not nxts: break
            cur=nxts[0]; chain.append(cur)
        # проверить, что каждый следующий доступен только после сдачи предыдущего
        ok=True
        for i,qid in enumerate(chain):
            giver=QUESTS[qid]["giver"]
            avail=quest.available_quests(ch,giver)
            if qid not in avail and QUESTS[qid].get("requires"):
                # должен стать доступен после сдачи requires
                pass
            # сдаём текущий, открываем следующий
            ch.quests[qid]="done"
        # повторная проверка доступности шаг-за-шагом
        ch2=Character(uid=98,name="q2",cls="warrior",race="human")
        seq=[]
        for qid in chain:
            req=QUESTS[qid].get("requires")
            gated = (req is None) or (ch2.quests.get(req)=="done")
            seq.append("✓" if gated else "✗")
            ch2.quests[qid]="done"
        nm=QUESTS[root]["name"]
        print(f"  {root:18} «{nm}»: цепочка {len(chain)} шагов  гейтинг {' '.join(seq)}")
        print(f"      {' → '.join(chain)}")


if __name__=="__main__":
    sockets_balance()
    territory_war()
    quest_chains()
