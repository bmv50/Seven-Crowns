"""Allowlisted message buttons: labels map to existing MAX commands only."""
from . import content, game_actions, npc, quest

COMMANDS = {
    '🔍 Осмотр': '/look', '👤 Герой': '/stats', '🎒 Сумка': '/inv',
    '✨ Умения': '/skills', '📜 Задания': '/quests', '🗺 Карта': '/map',
    '⚙️ Настройки': '/settings', '🔔 Уведомления': '/notify', '❓ Помощь': '/help',
    '👥 Группа': '/group', '🏰 Гильдия': '/guild', '⚖️ Аукцион': '/auction',
    '✨ Возродиться': '/respawn',
    '💬 Персонажи': '/npcs', '🎓 Обучение': '/train', '💰 Скупка': '/sell',
    '↑ Север': 'север', '↓ Юг': 'юг', '→ Восток': 'восток',
    '← Запад': 'запад', '⇧ Вверх': 'вверх', '⇩ Вниз': 'вниз',
}
_DIRECTIONS = {value: label for label, value in COMMANDS.items() if not value.startswith('/')}


def _register(prefix, title, action, key):
    # Stable content ID prevents ambiguous names and stale buttons retargeting an NPC.
    suffix = f' [{key}]'
    if not isinstance(key, str) or len(suffix) + len(prefix) >= 128 or any(c.isspace() for c in key):
        return
    title = ' '.join(str(title).split())
    label = prefix + title[:128-len(prefix)-len(suffix)] + suffix
    COMMANDS[label] = f'/{action} {key}'


for _key, _entry in content.NPCS.items():
    _register('💬 ', npc.display_name(_key), 'talk', _key)
    if _entry.get('role') == 'vendor':
        _register('🛒 ', npc.display_name(_key), 'shop', _key)
for _key, _entry in content.QUESTS.items():
    _register('📜 Взять: ', _entry.get('name', _key), 'accept', _key)
    _register('✅ Сдать: ', _entry.get('name', _key), 'turnin', _key)
_LABELS = {action: label for label, action in COMMANDS.items()}


def command(text):
    return COMMANDS.get(text, text)


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
        actions += [f'/accept {key}' for key in quest.available_quests(ch, npc_id)]
        if npc_id in game_actions.vendors_here(ch):
            actions += [f'/shop {npc_id}', '/sell']
        if npc_id == game_actions.trainer_here(ch):
            actions.append('/train')
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
                    or button['text'] not in COMMANDS):
                raise ValueError('Unsupported MAX navigation button')
            clean.append(dict(button))
        result.append(clean)
    return result
