"""Both transports use the same room-scoped quest and vendor operations."""

from engine import game_actions
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


if __name__ == "__main__":
    test_quest_proximity_and_rewards()
    test_vendor_stock_and_payment()
    print("OK: shared quest and shop guards, no remote or repeat rewards")
