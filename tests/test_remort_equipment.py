"""High-level gear stays owned after remort but cannot bypass level gates."""

from engine.character import Character, LEVEL_CAP
from engine.content import ITEMS
from engine import combat, equip, sockets


def _gear(slot):
    return next(key for key, meta in ITEMS.items()
                if meta.get("slot") == slot
                and meta.get("bonus")
                and 2 <= equip.level_req(meta) <= LEVEL_CAP
                and not meta.get("remort_req")
                and equip.class_can_use("warrior", key))


def test_remort_suspends_high_level_equipment_and_restores_it():
    ch = Character(uid=42, name="Ветеран", cls="warrior")
    weapon, armor = _gear("weapon"), _gear("armor")
    ch.equipment["weapon"] = weapon
    ch.equipment["armor"] = armor
    ch.inventory.extend([weapon, armor])
    ch.flags["ench"] = {"weapon": 5, "armor": 5}
    rune = next(key for key, meta in ITEMS.items()
                if meta.get("rune_bonus", {}).get("str", 0) > 0)
    ch.flags["sockets"] = {"weapon": [rune]}
    ch.level = LEVEL_CAP
    assert ch.active_item("weapon") == weapon
    assert ch.active_item("armor") == armor

    assert ch.remort()
    assert ch.equipment["weapon"] == weapon and ch.equipment["armor"] == armor
    assert ch.inventory == [weapon, armor]
    assert ch.active_item("weapon") is None
    assert ch.active_item("armor") is None
    assert sockets.stat_bonus(ch, "str") == 0
    from bot.mudview import render_score
    assert "🔒 до уровня" in render_score(ch)
    suspended = (ch.attack_power, ch.defense, ch.crit_chance,
                 ch.attr("str"), ch.attr("dex"), combat.weapon_dtype(ch))
    ch.equipment["weapon"] = ch.equipment["armor"] = None
    assert suspended == (ch.attack_power, ch.defense, ch.crit_chance,
                         ch.attr("str"), ch.attr("dex"), combat.weapon_dtype(ch))

    ch.equipment["weapon"], ch.equipment["armor"] = weapon, armor
    ch.level = max(equip.level_req(ITEMS[weapon]), equip.level_req(ITEMS[armor]))
    assert ch.active_item("weapon") == weapon
    assert ch.active_item("armor") == armor
    assert sockets.stat_bonus(ch, "str") > 0


def test_broken_gear_has_no_enchant_bonus():
    ch = Character(uid=43, name="Ветеран", cls="warrior", level=LEVEL_CAP)
    ch.equipment["weapon"] = _gear("weapon")
    ch.flags["ench"] = {"weapon": 5}
    ch.set_durab("weapon", 0)
    assert ch.active_item("weapon") is None
    attack_broken = ch.attack_power
    ch.equipment["weapon"] = None
    assert ch.attack_power == attack_broken


if __name__ == "__main__":
    test_remort_suspends_high_level_equipment_and_restores_it()
    test_broken_gear_has_no_enchant_bonus()
    print("OK: remort equipment level gates and broken enchant")
