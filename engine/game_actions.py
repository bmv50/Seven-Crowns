"""Transport-neutral, room-scoped quest and vendor actions.

The public quest functions describe progression, not where the player stands.
Both chat transports must apply the same proximity and stock checks before
they mutate a character. Persistence and notifications remain with the caller.
"""

from . import content, errands, karma, money, npc, quest, reputation, skills


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


def shop_sell_here(ch, key: str, vendor_id: str | None = None) -> tuple[bool, str]:
    vendor, stock = shop_stock_here(ch, vendor_id)
    if vendor is None:
        return False, "Продажа доступна только у торговца."
    if karma.vendor_refuses(ch):
        return False, "🚫 Торговец отказывается иметь дело с изгоем!"
    if key not in dict(shop_sellable_here(ch, vendor)):
        return False, "Этот предмет нельзя продать этому торговцу."
    price = content.sell_price(key)
    ch.inventory.remove(key)
    ch.gold += price
    return True, (f"💰 Продано: {content.ITEMS[key]['name']} за {money.fmt(price)}. "
                  f"Всего: {money.fmt(ch.gold)}.")


def shop_sellable_here(ch, vendor_id: str | None = None) -> list[tuple[str, int]]:
    vendor, stock = shop_stock_here(ch, vendor_id)
    if vendor is None:
        return []
    allowed_types = {content.ITEMS[item].get("type") for item in stock
                     if item in content.ITEMS}
    equipped = {item for item in ch.equipment.values() if item}
    result = []
    for key in dict.fromkeys(ch.inventory):
        if key not in content.ITEMS:
            continue
        if allowed_types and content.ITEMS[key].get("type") not in allowed_types:
            continue
        if ch.inventory.count(key) <= (1 if key in equipped else 0):
            continue
        price = content.sell_price(key)
        if price > 0:
            result.append((key, price))
    return result


def repair_here(ch) -> tuple[bool, str]:
    if "кузнец" not in _npcs_here(ch):
        return False, "Ремонт доступен только у кузнеца."
    cost = ch.repair_cost()
    if cost <= 0:
        return False, "Снаряжение в полном порядке."
    if ch.gold < cost:
        return False, f"Не хватает монет: нужно {money.fmt(cost)}."
    ch.gold -= cost
    ch.repair_all()
    return True, f"🔧 Снаряжение починено за {money.fmt(cost)}."


def trainer_here(ch) -> str | None:
    return next((key for key in _npcs_here(ch)
                 if (npc.get(key) or {}).get("role") == "trainer"), None)


def learn_skill_here(ch, skill_id: str) -> tuple[bool, str]:
    if trainer_here(ch) is None:
        return False, "Учиться можно только у учителя."
    return skills.learn(ch, skill_id)


def errand_can_offer_here(ch, npc_id: str) -> bool:
    if npc_id not in _npcs_here(ch):
        return False
    if (npc.get(npc_id) or {}).get("role") not in {
        "vendor", "trainer", "mentor", "guard", "questgiver"
    }:
        return False
    return errands.can_offer(ch, npc_id)


def errand_offer_here(ch, npc_id: str):
    if not errand_can_offer_here(ch, npc_id):
        return None
    offer = errands.offer(ch, npc_id)
    if offer:
        ch.flags["errand_pending"] = offer
    return offer


def errand_accept_here(ch, npc_id: str) -> tuple[bool, str]:
    pending = ch.flags.get("errand_pending")
    if (npc_id not in _npcs_here(ch) or not pending
            or pending.get("npc") != npc_id):
        return False, "Сначала получите предложение у персонажа рядом."
    before = ch.flags.get("errand")
    msg = errands.accept(ch, pending)
    return ch.flags.get("errand") is not None and ch.flags.get("errand") is not before, msg


def errand_turn_in_here(ch, npc_id: str) -> tuple[bool, str]:
    if npc_id not in _npcs_here(ch):
        return False, "Вернитесь к персонажу, выдавшему поручение."
    msg = errands.turn_in(ch, npc_id)
    return (True, msg) if msg is not None else (False, "Условия поручения ещё не выполнены.")
