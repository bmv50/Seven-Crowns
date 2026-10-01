"""Transport-neutral, room-scoped quest and vendor actions.

The public quest functions describe progression, not where the player stands.
Both chat transports must apply the same proximity and stock checks before
they mutate a character. Persistence and notifications remain with the caller.
"""

from . import content, karma, money, npc, quest, reputation


def _npcs_here(ch) -> list[str]:
    return content.WORLD.get(ch.room, {}).get("npc", [])


def quest_accept_here(ch, qid: str) -> tuple[bool, str]:
    entry = content.QUESTS.get(qid)
    if not entry:
        return False, "Нет такого задания."
    giver = entry.get("giver")
    if giver not in _npcs_here(ch):
        return False, "Сначала найдите того, кто выдаёт это задание."
    if qid not in quest.available_quests(ch, giver):
        return False, "Это задание сейчас недоступно."
    return quest.accept(ch, qid)


def quest_complete_here(ch, qid: str) -> tuple[bool, str]:
    entry = content.QUESTS.get(qid)
    if not entry:
        return False, "Нет такого задания."
    turn_in = entry.get("turn_in")
    if turn_in not in _npcs_here(ch):
        return False, "Для сдачи найдите нужного персонажа."
    if qid not in quest.turn_in_quests(ch, turn_in):
        return False, "Цель ещё не выполнена или задание не активно."
    return quest.complete(ch, qid)


def quest_choose_here(ch, qid: str, option_id: str) -> tuple[bool, str]:
    entry = content.QUESTS.get(qid)
    if not entry:
        return False, "Нет такого задания."
    giver = entry.get("giver")
    if giver not in _npcs_here(ch):
        return False, "Для выбора вернитесь к нужному персонажу."
    if not any(qid == pending for pending, _ in quest.pending_choices(ch, giver)):
        return False, "Этот выбор недоступен."
    return quest.on_choose(ch, qid, option_id)


def vendors_here(ch) -> list[str]:
    return [key for key in _npcs_here(ch)
            if (npc.get(key) or {}).get("role") == "vendor"]


def shop_stock_here(ch, vendor_id: str | None = None) -> tuple[str | None, list[str]]:
    vendors = vendors_here(ch)
    if not vendors:
        return None, []
    if vendor_id is not None and vendor_id not in vendors:
        return None, []
    vendor = vendor_id or vendors[0]
    return vendor, list(content.SHOPS.get(vendor)
                        or content.SHOPS.get("_default") or [])


def shop_price(ch, key: str, vendor_id: str) -> int:
    base = int(content.ITEMS[key].get("price", 0))
    faction = (npc.get(vendor_id) or {}).get("faction")
    return max(1, int(base * (1 - reputation.discount(ch, faction))))


def shop_buy_here(ch, key: str, vendor_id: str | None = None) -> tuple[bool, str]:
    vendor, stock = shop_stock_here(ch, vendor_id)
    if vendor is None:
        return False, "Торговца рядом нет."
    if karma.vendor_refuses(ch):
        return False, "🚫 Торговец отказывается иметь дело с изгоем!"
    if key not in stock or key not in content.ITEMS:
        return False, "Этот торговец не продаёт такой предмет."
    item = content.ITEMS[key]
    classes = item.get("class_req")
    if classes and ch.cls not in classes:
        return False, "Не для вашего класса."
    price = shop_price(ch, key, vendor)
    if ch.gold < price:
        return False, f"💰 Не хватает: нужно {money.fmt(price)}, есть {money.fmt(ch.gold)}."
    ch.gold -= price
    ch.inventory.append(key)
    return True, (f"✅ Куплено: {item['name']} за 💰{money.fmt(price)}. "
                  f"Осталось: {money.fmt(ch.gold)}.")
