"""Read-only inventory browsing. Old buttons cannot view absent inventory items."""
from . import content, rarity, item_art, max_ui, game_actions, equip

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


def inventory_caption(ch, page=0):
    keys = owned(ch)
    if not keys:
        return '🎒 *Сумка пуста*'
    page = max(0, min(page, (len(keys)-1)//PAGE_SIZE))
    lines = ['🎒 *Сумка*', f'Страница {page+1}/{(len(keys)-1)//PAGE_SIZE+1}',
             'Нажмите имя предмета ниже: откроются изображение, описание и действия.', '']
    for key in keys[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
        meta = content.ITEMS[key]
        status = ' · надето' if key in ch.equipment.values() else ''
        lines += [f"{rarity.emoji(key)} *{content.ITEMS[rarity.base_of(key)]['name']}* ×{ch.inventory.count(key)}{status}",
                  ' '.join(meta.get('desc', '').split())[:180], '']
    return '\n'.join(lines)


def inventory_keyboard(ch, page=0):
    keys = owned(ch)
    page = max(0, min(page, max(0, (len(keys)-1)//PAGE_SIZE)))
    rows = []
    for key in keys[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
        label = rarity.emoji(key)+' '+content.ITEMS[rarity.base_of(key)]['name']
        bonus = content.ITEMS[key].get('bonus', {})
        if bonus.get('atk'):
            label += ' · ⚔️'+str(bonus['atk'])
        elif bonus.get('defense'):
            label += ' · 🛡'+str(bonus['defense'])
        count = ch.inventory.count(key)
        label += f' ×{count}' if count else ' · надето'
        rows.append([{'type': 'callback', 'text': label[:128], 'payload': '/item '+key}])
    nav = []
    if page:
        nav.append({'type': 'callback', 'text': '← Предыдущие', 'payload': '/invlist '+str(page-1)})
    if (page+1)*PAGE_SIZE < len(keys):
        nav.append({'type': 'callback', 'text': 'Далее →', 'payload': '/invlist '+str(page+1)})
    if nav:
        rows.append(nav)
    rows.append([{'type': 'message', 'text': '🎒 Сумка'}, {'type': 'message', 'text': '🔍 Осмотр'}])
    return rows


def actions_keyboard(ch, key):
    if key not in owned(ch):
        return []
    meta = content.ITEMS[key]
    rows = []
    if meta.get('slot'):
        equipped = key in ch.equipment.values()
        if equipped or equip.can_equip(ch, key)[0]:
            rows.append([max_ui.button(ch, '🚫 Снять' if equipped else '⚙️ Надеть',
                                       'unequip' if equipped else 'equip', key)])
    elif meta.get('type') == 'consumable' and key in ch.inventory:
        rows.append([max_ui.button(ch, '🧪 Использовать', 'use', key)])
    vendors = game_actions.vendors_here(ch)
    if not ch.target and vendors and key in dict(game_actions.shop_sellable_here(ch, vendors[0])):
        rows.append([max_ui.button(ch, '💰 Продать', 'sell', key)])
    rows.append([{'type': 'callback', 'text': '🎒 К сумке', 'payload': '/inv'}])
    return rows
