"""Allowlisted message buttons: labels map to existing MAX commands only."""
from . import content, game_actions, npc, quest, skills
import re

_PURCHASE = re.compile(r'(✅ Купить|❌ Отмена|✅ Продать|❌ Отмена продажи|✅ Починить|❌ Отмена ремонта|✅ Изучить|❌ Отмена обучения|✅ Подтвердить путь|❌ Отмена выбора) \[([0-9a-f]{32})\]\Z')
_CONFIRM_COMMANDS = {'✅ Купить': 'buyconfirm', '❌ Отмена': 'buycancel',
                     '✅ Продать': 'sellconfirm', '❌ Отмена продажи': 'sellcancel',
                     '✅ Починить': 'repairconfirm', '❌ Отмена ремонта': 'repaircancel',
                     '✅ Изучить': 'learnconfirm', '❌ Отмена обучения': 'learncancel',
                     '✅ Подтвердить путь': 'choiceconfirm', '❌ Отмена выбора': 'choicecancel'}

COMMANDS = {
    '🔍 Осмотр': '/look', '👤 Герой': '/stats', '🎒 Сумка': '/inv',
    '✨ Умения': '/skills', '📜 Задания': '/quests', '🗺 Карта': '/map',
    '⚙️ Настройки': '/settings', '🔔 Уведомления': '/notify', '❓ Помощь': '/help',
    '👥 Группа': '/group', '🏰 Гильдия': '/guild', '⚖️ Аукцион': '/auction',
    '✨ Возродиться': '/respawn',
    '💬 Персонажи': '/npcs', '🎓 Обучение': '/train', '💰 Скупка': '/sell',
    '🔧 Ремонт': '/repair',
    '↑ Север': 'север', '↓ Юг': 'юг', '→ Восток': 'восток',
    '← Запад': 'запад', '⇧ Вверх': 'вверх', '⇩ Вниз': 'вниз',
}
_DIRECTIONS = {value: label for label, value in COMMANDS.items() if not value.startswith('/')}


def _register(prefix, title, action, key, argument=None):
    # Stable content ID prevents ambiguous names and stale buttons retargeting an NPC.
    suffix = f' [{key}]'
    if not isinstance(key, str) or len(suffix) + len(prefix) >= 128 or any(c.isspace() for c in key):
        return
    title = ' '.join(str(title).split())
    label = prefix + title[:128-len(prefix)-len(suffix)] + suffix
    COMMANDS[label] = f'/{action} {argument if argument is not None else key}'


for _key, _entry in content.NPCS.items():
    _register('💬 ', npc.display_name(_key), 'talk', _key)
    if _entry.get('role') == 'vendor':
        _register('🛒 ', npc.display_name(_key), 'shop', _key)
for _key, _entry in content.QUESTS.items():
    _register('📜 Взять: ', _entry.get('name', _key), 'accept', _key)
    _register('✅ Сдать: ', _entry.get('name', _key), 'turnin', _key)
    for _option in quest.choose_options(_key):
        _register('🔀 ', _option.get('label', _option['id']), 'choose', f"{_key}:{_option['id']}",
                  f"{_key} {_option['id']}")
for _key, _entry in content.ITEMS.items():
    _register('🛍 ', _entry.get('name', _key), 'buyoffer', _key)
    _register('💰 Продать: ', _entry.get('name', _key), 'selloffer', _key)
for _key, _entry in content.SKILLS.items():
    _register('🎓 Изучить: ', _entry.get('name', _key), 'learnoffer', _key)
_LABELS = {action: label for label, action in COMMANDS.items()}


def command(text):
    match = _PURCHASE.fullmatch(text)
    if match:
        return f"/{_CONFIRM_COMMANDS[match[1]]} {match[2]}"
    return COMMANDS.get(text, text)


def purchase_keyboard(token, operation='buy'):
    if operation not in ('buy', 'sell'):
        raise ValueError('Unsupported shop operation')
    if not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('Invalid purchase token')
    return [[{'type': 'message', 'text': f'{label} [{token}]'}]
            for label in (('✅ Купить', '❌ Отмена') if operation == 'buy'
                          else ('✅ Продать', '❌ Отмена продажи'))] + [[{'type': 'message', 'text': '🔍 Осмотр'}]]


def sell_keyboard(ch, rooms, vendor):
    if ch.flags.get('dead') or ch.hp <= 0:
        return keyboard(ch, rooms)
    labels = [_LABELS[f'/selloffer {key}'] for key, _ in game_actions.shop_sellable_here(ch, vendor)
              if f'/selloffer {key}' in _LABELS][:12]
    return [[{'type': 'message', 'text': label}] for label in labels] + [
        [{'type': 'message', 'text': '💬 Персонажи'}, {'type': 'message', 'text': '🔍 Осмотр'}]]


def service_keyboard(token, operation):
    labels = {'repair': ('✅ Починить', '❌ Отмена ремонта'),
              'learn': ('✅ Изучить', '❌ Отмена обучения')}
    if operation not in labels or not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('Invalid service confirmation')
    return [[{'type': 'message', 'text': f'{label} [{token}]'}] for label in labels[operation]] + [
        [{'type': 'message', 'text': '🔍 Осмотр'}]]


def choice_keyboard(token):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('Invalid story choice token')
    return [[{'type': 'message', 'text': f'{label} [{token}]'}]
            for label in ('✅ Подтвердить путь', '❌ Отмена выбора')] + [
                [{'type': 'message', 'text': '🔍 Осмотр'}]]


def train_keyboard(ch, rooms):
    if ch.flags.get('dead') or ch.hp <= 0 or game_actions.trainer_here(ch) is None:
        return keyboard(ch, rooms)
    labels = [_LABELS[f'/learnoffer {sid}'] for sid in skills.learnable_now(ch)
              if f'/learnoffer {sid}' in _LABELS][:12]
    return [[{'type': 'message', 'text': label}] for label in labels] + [
        [{'type': 'message', 'text': '💬 Персонажи'}, {'type': 'message', 'text': '🔍 Осмотр'}]]


def shop_keyboard(ch, rooms, vendor):
    if ch.flags.get('dead') or ch.hp <= 0:
        return keyboard(ch, rooms)
    found, stock = game_actions.shop_stock_here(ch, vendor)
    if found is None:
        return context_keyboard(ch, rooms)
    labels = [_LABELS[f'/buyoffer {key}'] for key in stock
              if f'/buyoffer {key}' in _LABELS][:12]
    return [[{'type': 'message', 'text': label}] for label in labels] + [
        [{'type': 'message', 'text': '💬 Персонажи'}, {'type': 'message', 'text': '🔍 Осмотр'}]]


def keyboard(ch, rooms):
    labels = ['❓ Помощь']
    if ch is not None:
        if ch.flags.get('dead') or ch.hp <= 0:
            labels = ['✨ Возродиться', '⚙️ Настройки', '🔔 Уведомления', '❓ Помощь']
        else:
            labels = [_DIRECTIONS[d] for d in rooms.get(ch.room, {}).get('exits', {}) if d in _DIRECTIONS]
            labels += ['💬 Персонажи', '🔍 Осмотр', '👤 Герой', '🎒 Сумка', '✨ Умения', '📜 Задания',
                       '🗺 Карта', '⚙️ Настройки', '🔔 Уведомления']
            if ch.level >= 3:
                labels.append('👥 Группа')
            if ch.level >= 10:
                labels.append('🏰 Гильдия')
            if ch.level >= 12:
                labels.append('⚖️ Аукцион')
            labels.append('❓ Помощь')
    return [[{'type': 'message', 'text': label} for label in labels[i:i+2]]
            for i in range(0, len(labels), 2)]


def context_keyboard(ch, rooms, npc_id=None):
    """Presentation only; all actions re-enter room-scoped application commands."""
    if ch is None or ch.flags.get('dead') or ch.hp <= 0:
        return keyboard(ch, rooms)
    here = rooms.get(ch.room, {}).get('npc', [])
    actions = []
    if npc_id is None:
        actions = [f'/talk {key}' for key in here]
    elif npc_id in here:
        actions = [f'/turnin {key}' for key in quest.turn_in_quests(ch, npc_id)]
        actions += [f'/choose {qid} {option["id"]}' for qid, options in quest.pending_choices(ch, npc_id)
                    for option in options]
        actions += [f'/accept {key}' for key in quest.available_quests(ch, npc_id)]
        if npc_id in game_actions.vendors_here(ch):
            actions += [f'/shop {npc_id}', '/sell']
        if npc_id == game_actions.trainer_here(ch):
            actions.append('/train')
        if npc_id == 'кузнец':
            actions.append('/repair')
    # Keep the mobile menu bounded; /talk still lists all actions as text commands.
    labels = [_LABELS[action] for action in dict.fromkeys(actions) if action in _LABELS][:12]
    rows = [[{'type': 'message', 'text': label}] for label in labels]
    rows += [[{'type': 'message', 'text': label} for label in pair]
             for pair in (('🔍 Осмотр', '💬 Персонажи'), ('📜 Задания', '❓ Помощь'))]
    return rows


def validate(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ValueError('Invalid MAX navigation keyboard')
    result = []
    for row in rows:
        if not isinstance(row, list) or not 1 <= len(row) <= 2:
            raise ValueError('Invalid MAX navigation row')
        clean = []
        for button in row:
            if (not isinstance(button, dict) or set(button) != {'type', 'text'}
                    or button['type'] != 'message' or not isinstance(button['text'], str)
                    or (button['text'] not in COMMANDS and not _PURCHASE.fullmatch(button['text']))):
                raise ValueError('Unsupported MAX navigation button')
            clean.append(dict(button))
        result.append(clean)
    return result
