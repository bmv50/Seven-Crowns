"""Both transports use the same room-scoped quest and vendor operations."""

from engine import errands, game_actions
from engine.character import Character, START_ROOM


def hero():
    ch = Character(uid=-101, name="Игрок", cls="warrior", race="human")
    ch.init_vitals()
    ch.init_skills()
    ch.room = START_ROOM
    return ch


def test_quest_proximity_and_rewards():
    ch = hero()
    ch.room = "market"
    ok, _ = game_actions.quest_accept_here(ch, "sample_reach_well")
    assert not ok and "sample_reach_well" not in ch.quests
    ch.room = START_ROOM
    assert game_actions.quest_accept_here(ch, "sample_reach_well")[0]
    assert not game_actions.quest_accept_here(ch, "sample_reach_well")[0]
    ch.quests["sample_reach_well:reach"] = "1"
    ch.room = "market"
    before = ch.gold
    assert not game_actions.quest_complete_here(ch, "sample_reach_well")[0]
    assert ch.gold == before
    ch.room = START_ROOM
    assert game_actions.quest_complete_here(ch, "sample_reach_well")[0]
    assert ch.gold > before
    assert not game_actions.quest_complete_here(ch, "sample_reach_well")[0]
    assert ch.gold == before + 1500


def test_vendor_stock_and_payment():
    ch = hero()
    ch.gold = 100_000
    assert not game_actions.shop_buy_here(ch, "малое_зелье")[0]
    ch.room = "market"
    vendor, stock = game_actions.shop_stock_here(ch, "лавочник_туманного_брода")
    assert vendor == "лавочник_туманного_брода" and "малое_зелье" in stock
    assert game_actions.shop_stock_here(ch, "кузнец") == (None, [])
    before = ch.gold
    assert not game_actions.shop_buy_here(ch, "железный_меч", vendor)[0]
    assert ch.gold == before
    assert game_actions.shop_buy_here(ch, "малое_зелье", vendor)[0]
    assert ch.inventory.count("малое_зелье") == 1
    assert ch.gold == before - game_actions.shop_price(ch, "малое_зелье", vendor)
    ch.room = START_ROOM
    assert not game_actions.shop_buy_here(ch, "малое_зелье", vendor)[0]


def test_sell_requires_vendor_and_excludes_equipped_last_copy():
    ch = hero()
    ch.room = "market"
    vendor = "лавочник_туманного_брода"
    ch.inventory.append("малое_зелье")
    before = ch.gold
    assert "малое_зелье" in dict(game_actions.shop_sellable_here(ch, vendor))
    ch.room = START_ROOM
    assert not game_actions.shop_sell_here(ch, "малое_зелье", vendor)[0]
    assert ch.inventory.count("малое_зелье") == 1 and ch.gold == before
    ch.room = "market"
    price = dict(game_actions.shop_sellable_here(ch, vendor))["малое_зелье"]
    assert game_actions.shop_sell_here(ch, "малое_зелье", vendor)[0]
    assert ch.gold == before + price and "малое_зелье" not in ch.inventory
    assert not game_actions.shop_sell_here(ch, "малое_зелье", vendor)[0]
    ch.inventory.append("железный_меч")
    ch.equipment["weapon"] = "железный_меч"
    weapons = "оружейник_брода"
    assert "железный_меч" not in dict(game_actions.shop_sellable_here(ch, weapons))
    ch.inventory.append("железный_меч")
    assert "железный_меч" in dict(game_actions.shop_sellable_here(ch, weapons))


def test_repair_checks_smith_again_at_confirmation():
    ch = hero()
    ch.gold = 100_000
    ch.equipment["weapon"] = "железный_меч"
    ch.set_durab("weapon", 50)
    cost = ch.repair_cost()
    assert cost > 0
    before = ch.gold
    assert not game_actions.repair_here(ch)[0]
    assert ch.gold == before and ch.repair_cost() == cost
    ch.room = "mine_entrance"
    assert game_actions.repair_here(ch)[0]
    assert ch.gold == before - cost and ch.repair_cost() == 0
    assert not game_actions.repair_here(ch)[0]


def test_errand_offer_accept_and_turn_in_require_npc_nearby():
    ch = hero()
    npc_id = "наставник"
    assert game_actions.errand_can_offer_here(ch, npc_id)
    offer = game_actions.errand_offer_here(ch, npc_id)
    assert offer and ch.flags["errand_pending"] == offer
    ch.room = "market"
    assert not game_actions.errand_accept_here(ch, npc_id)[0]
    assert not errands.has_active(ch)
    ch.room = START_ROOM
    assert game_actions.errand_accept_here(ch, npc_id)[0]
    assert errands.has_active(ch) and errands.taken_today(ch) == 1
    assert not game_actions.errand_accept_here(ch, npc_id)[0]
    active = ch.flags["errand"]
    if active["type"] == "kill":
        active["progress"] = active["count"]
    else:
        ch.inventory.extend([active["item"]] * active["count"])
    before = (ch.gold, ch.xp)
    ch.room = "market"
    assert not game_actions.errand_turn_in_here(ch, npc_id)[0]
    assert (ch.gold, ch.xp) == before
    ch.room = START_ROOM
    assert game_actions.errand_turn_in_here(ch, npc_id)[0]
    assert not errands.has_active(ch)
    assert ch.gold == before[0] + active["reward"]["gold"]
    assert ch.xp == before[1] + active["reward"]["xp"]
    assert not game_actions.errand_turn_in_here(ch, npc_id)[0]


if __name__ == "__main__":
    test_quest_proximity_and_rewards()
    test_vendor_stock_and_payment()
    test_sell_requires_vendor_and_excludes_equipped_last_copy()
    test_repair_checks_smith_again_at_confirmation()
    test_errand_offer_accept_and_turn_in_require_npc_nearby()
    print("OK: shared quest and shop guards, no remote or repeat rewards")
