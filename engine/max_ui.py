"""Owner/hero/room-bound controls and bounded, read-only menu history."""
import base64
from collections import OrderedDict
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from . import content, item_art

TTL = 180
_KEY = secrets.token_bytes(32)
_HISTORY = OrderedDict()
READ = {'look', 'stats', 'inv', 'invlist', 'item', 'mobs', 'mob', 'consider',
        'npcs', 'npc', 'skills', 'quests', 'map', 'settings', 'notify', 'group',
        'guild', 'auction', 'train', 'shop', 'sell', 'help', 'terms', 'privacy', 'rules', 'support'}
ACTIONS = {'equip', 'unequip', 'use', 'sell', 'attack', 'mob', 'consider', 'npc', 'talk'}


def _digest(data):
    secret = os.environ.get('MAX_WEBHOOK_SECRET')
    return hmac.new(secret.encode() if secret else _KEY, data.encode(), hashlib.sha256).hexdigest()[:32]


def payload(ch, action, key, now=None):
    data = json.dumps([ch.uid, ch.generation, ch.room, action, key,
                       int(time.time() if now is None else now)], ensure_ascii=False, separators=(',', ':'))
    encoded = base64.urlsafe_b64encode(data.encode()).decode().rstrip('=')
    result = '/ui '+encoded+' '+_digest(encoded)
    if not valid_callback(result):
        raise ValueError('Invalid MAX action')
    return result


def _decode(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError('Invalid action')
    parts = value.split()
    if (len(parts) != 3 or parts[0] != '/ui'
            or not re.fullmatch(r'[A-Za-z0-9_-]+', parts[1])
            or not re.fullmatch(r'[0-9a-f]{32}', parts[2])):
        raise ValueError('Invalid action')
    values = json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)).decode())
    if not isinstance(values, list) or len(values) != 6:
        raise ValueError('Invalid action')
    uid, generation, room, action, key, stamp = values
    if (type(uid) is not int or type(generation) is not int or type(stamp) is not int
            or room not in content.WORLD or action not in ACTIONS
            or not isinstance(key, str) or not 1 <= len(key) <= 180):
        raise ValueError('Invalid action')
    if action in {'equip', 'unequip', 'use', 'sell'} and not item_art.valid_item(key):
        raise ValueError('Invalid item')
    if action in {'npc', 'talk'} and key not in content.NPCS:
        raise ValueError('Invalid NPC')
    if action in {'mob', 'attack', 'consider'} and not re.fullmatch(r'[\w:-]+', key):
        raise ValueError('Invalid mob')
    return parts, values


def valid_callback(value):
    if isinstance(value, str) and re.fullmatch(r'/mobs(?: [0-9]{1,3})?', value):
        return True
    try:
        _decode(value)
        return True
    except (ValueError, TypeError, UnicodeError):
        return False


def resolve(ch, value, now=None):
    try:
        parts, data = _decode(value)
    except (ValueError, TypeError, UnicodeError):
        return None
    uid, generation, room, action, key, stamp = data
    age = (time.time() if now is None else now)-stamp
    if ((uid, generation, room) != (ch.uid, ch.generation, ch.room)
            or not 0 <= age <= TTL or not hmac.compare_digest(parts[2], _digest(parts[1]))):
        return None
    return action, key


def button(ch, title, action, key):
    return {'type': 'callback', 'text': title[:128], 'payload': payload(ch, action, key)}


def record(ch, command, parts):
    previous = _HISTORY.pop(ch.uid, None)
    stack = previous[2] if previous and previous[:2] == (ch.generation, ch.room) else ['/look']
    held = set(ch.inventory) | set(ch.equipment.values())
    stack = [route for route in stack if not route.startswith('/item ') or route[6:] in held] or ['/look']
    is_view = command in READ and not (command in {'sell', 'train', 'settings', 'notify', 'guild', 'auction'} and len(parts) > 1)
    if is_view:
        route = '/'+command+(' '+' '.join(parts[1:]) if parts[1:] else '')
        if len(route) > 512:
            return
        if command == 'look':
            stack = ['/look']
        elif route in stack:
            stack = stack[:stack.index(route)+1]
        else:
            stack = (stack+[route])[-12:]
    _HISTORY[ch.uid] = (ch.generation, ch.room, stack, is_view)
    if len(_HISTORY) > 4096:
        _HISTORY.popitem(last=False)


def back(ch):
    state = _HISTORY.get(ch.uid)
    if not state or state[:2] != (ch.generation, ch.room):
        return '/look'
    stack = list(state[2])
    if state[3] and len(stack) > 1:
        stack.pop()
    _HISTORY[ch.uid] = (ch.generation, ch.room, stack, True)
    return stack[-1]


def with_back(rows):
    if not rows or any(b.get('text') == '⬅️ Назад' for row in rows for b in row):
        return rows
    return rows+[[{'type': 'message', 'text': '⬅️ Назад'}]]
