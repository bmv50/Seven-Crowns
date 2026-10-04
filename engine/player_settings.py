"""Allowlisted, explicit player preferences shared by both messengers."""
from . import notify


def patch_for(key, raw):
    raw = str(raw).lower()
    if key in ('autoloot', 'roompics', 'push', 'quiet') or key in notify.CATEGORIES:
        if raw not in ('on', 'off'):
            raise ValueError('Укажите on или off.')
        value = raw == 'on'
        if key in ('autoloot', 'roompics'):
            return {key: value}
        field = {'push': 'push_enabled', 'quiet': 'quiet_off'}.get(key, key)
        return {'notify': {field: not value if key == 'quiet' else value}}
    if key in ('limit', 'tz'):
        try:
            if len(raw) > 3:
                raise ValueError()
            value = int(raw)
        except ValueError:
            raise ValueError('Укажите целое число.') from None
        if key == 'limit' and value not in notify.LIMIT_PRESETS:
            raise ValueError('Лимит: 1, 2 или 5 уведомлений в сутки.')
        if key == 'tz' and not notify.TZ_MIN <= value <= notify.TZ_MAX:
            raise ValueError(f'Часовой пояс: от {notify.TZ_MIN} до +{notify.TZ_MAX}.')
        return {'notify': {'limit' if key == 'limit' else 'tz_offset': value}}
    raise ValueError('Неизвестная настройка.')


def apply(flags, patch):
    """Merge only owned preference fields, preserving gameplay and quota flags."""
    for key, value in patch.items():
        if key == 'notify':
            if not isinstance(flags.get(key), dict):
                flags[key] = {}
            flags[key].update(value)
        else:
            flags[key] = value
