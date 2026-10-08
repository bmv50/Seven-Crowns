"""Read-only inventory browsing. Old buttons cannot view absent inventory items."""
from . import content, rarity, item_art

PAGE_SIZE = 10


def owned(ch):
    return list(dict.fromkeys(key for key in ch.inventory+list(ch.equipment.values())
                             if item_art.valid_item(key)))


def valid_callback(payload):
    if not isinstance(payload, str) or len(payload) > 512:
        return False
    parts = payload.split()
    return (parts == ['/inv'] or
            (len(parts) == 2 and parts[0] == '/invlist' and parts[1].isdigit() and len(parts[1]) <= 3) or
            (len(parts) == 2 and parts[0] == '/item' and item_art.valid_item(parts[1])))


def inventory_keyboard(ch, page=0):
    keys = owned(ch)
    page = max(0, min(page, max(0, (len(keys)-1)//PAGE_SIZE)))
    rows = []
    for key in keys[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
        label = rarity.emoji(key)+' '+content.ITEMS[rarity.base_of(key)]['name']
        count = ch.inventory.count(key)
        label += f' ×{count}' if count else ' · надето'
        rows.append([{'type': 'callback', 'text': label[:128], 'payload': '/item '+key}])
    nav = []
    if page:
        nav.append({'type': 'callback', 'text': '← Назад', 'payload': '/invlist '+str(page-1)})
    if (page+1)*PAGE_SIZE < len(keys):
        nav.append({'type': 'callback', 'text': 'Далее →', 'payload': '/invlist '+str(page+1)})
    if nav:
        rows.append(nav)
    rows.append([{'type': 'message', 'text': '🎒 Сумка'}, {'type': 'message', 'text': '🔍 Осмотр'}])
    return rows
