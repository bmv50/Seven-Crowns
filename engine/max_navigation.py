"""Allowlisted message buttons: labels map to existing MAX commands only."""

COMMANDS = {
    '🔍 Осмотр': '/look', '👤 Герой': '/stats', '🎒 Сумка': '/inv',
    '✨ Умения': '/skills', '📜 Задания': '/quests', '🗺 Карта': '/map',
    '⚙️ Настройки': '/settings', '🔔 Уведомления': '/notify', '❓ Помощь': '/help',
    '👥 Группа': '/group', '🏰 Гильдия': '/guild', '⚖️ Аукцион': '/auction',
    '✨ Возродиться': '/respawn',
    '↑ Север': 'север', '↓ Юг': 'юг', '→ Восток': 'восток',
    '← Запад': 'запад', '⇧ Вверх': 'вверх', '⇩ Вниз': 'вниз',
}
_DIRECTIONS = {value: label for label, value in COMMANDS.items() if not value.startswith('/')}


def command(text):
    return COMMANDS.get(text, text)


def keyboard(ch, rooms):
    labels = ['❓ Помощь']
    if ch is not None:
        if ch.flags.get('dead') or ch.hp <= 0:
            labels = ['✨ Возродиться', '⚙️ Настройки', '🔔 Уведомления', '❓ Помощь']
        else:
            labels = [_DIRECTIONS[d] for d in rooms.get(ch.room, {}).get('exits', {}) if d in _DIRECTIONS]
            labels += ['🔍 Осмотр', '👤 Герой', '🎒 Сумка', '✨ Умения', '📜 Задания',
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
