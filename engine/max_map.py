"""Read-only quest compass and durable, bounded MAX map snapshots.

No game state is changed here. Walking re-enters the shared movement handler.
Snapshots contain content IDs only, never user IDs, names, arbitrary files or URLs.
"""
import base64
from collections import deque
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

from . import content, quest, errands

ROOT = Path(tempfile.gettempdir()) / 'seven-crowns-max-maps'
_KEY = (os.environ.get('MAX_WEBHOOK_SECRET') or secrets.token_hex(32)).encode()
TTL = 3600
PAGE_SIZE = 10
DIR_LABELS = {'север': '↑ Север', 'юг': '↓ Юг', 'восток': '→ Восток',
              'запад': '← Запад', 'вверх': '⇧ Вверх', 'вниз': '⇩ Вниз'}


def active(ch):
    qids = [qid for qid, status in ch.quests.items()
            if status == 'active' and qid in content.QUESTS]
    return qids + (['errand'] if errands.has_active(ch) else [])


def title(ch, qid):
    return 'Поручение персонажа' if qid == 'errand' else content.QUESTS[qid]['name']


def _mob_sources(mob):
    return [rid for rid, room in content.WORLD.items() if mob in room.get('spawns', [])]


def _item_sources(ch, item):
    taken = ch.flags.get('ground_taken') or {}
    return [rid for rid, room in content.WORLD.items()
            if (item in room.get('items', []) and item not in taken.get(rid, [])) or any(
                any(drop[0] == item and drop[1] > 0 for drop in content.MOBS.get(mob, {}).get('loot', []))
                for mob in room.get('spawns', []))]


def selected(ch):
    qid = ch.flags.get('map_quest')
    if qid == 'none':
        return None
    return qid if qid in active(ch) else next(iter(active(ch)), None)


def npc_rooms(npc):
    return [rid for rid, room in content.WORLD.items() if npc in room.get('npc', [])]


def objective(ch, qid):
    """Known candidate locations and an honest action hint, not live spawn claims."""
    if qid not in active(ch):
        return [], ''
    if qid == 'errand':
        e = ch.flags['errand']
        if errands.can_turn_in(ch, e['npc']):
            return npc_rooms(e['npc']), 'Поручение выполнено: вернитесь к выдавшему его персонажу.'
        if e['type'] == 'kill':
            return _mob_sources(e['mob']), 'Ищите нужного врага; его наличие сейчас не гарантировано.'
        return _item_sources(ch, e['item']), 'Соберите нужные предметы для поручения.'
    q = content.QUESTS[qid]
    obj = q['objective']
    if quest.is_complete(ch, qid):
        npc = q.get('turn_in', q['giver'])
        return npc_rooms(npc), 'Задание выполнено: поговорите с персонажем для сдачи.'
    kind = obj['type']
    if kind == 'reach':
        return [obj['room']], 'Доберитесь до указанной локации.'
    if kind == 'talk':
        return npc_rooms(obj['npc']), 'Поговорите с нужным персонажем.'
    if kind == 'choose':
        return npc_rooms(q['giver']), 'Поговорите с персонажем и выберите сюжетный путь.'
    if kind == 'kill':
        return _mob_sources(obj['mob']), 'Ищите нужного врага здесь; если его нет, дождитесь возрождения. Убийства засчитываются в бою.'
    if kind == 'collect':
        item = obj['item']
        return _item_sources(ch, item), 'Соберите нужный предмет с земли или получите его из добычи. Наличие и выпадение не гарантированы.'
    if kind == 'use':
        return [obj.get('room', ch.room)], 'Примените нужный предмет командой /use.'
    return [], 'Точное место действия неизвестно. Смотрите описание задания в журнале.'


def shortest(start, targets, rooms=None):
    """Directed exits, including vertical movement; never teleport shortcuts."""
    rooms = content.WORLD if rooms is None else rooms
    wanted = set(targets) & rooms.keys()
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        room, path = queue.popleft()
        if room in wanted:
            return path
        for direction, dest in rooms.get(room, {}).get('exits', {}).items():
            if direction in DIR_LABELS and dest in rooms and dest not in visited:
                visited.add(dest)
                queue.append((dest, path + [dest]))
    return []


def plan(ch):
    qid = selected(ch)
    candidates, hint = objective(ch, qid)
    return qid, shortest(ch.room, candidates) if candidates else [], hint


def _signature(ch, src, dest, stamp):
    state = [ch.uid, getattr(ch, 'generation', 0), ch.flags.get('map_revision', 0),
             ch.flags.get('map_quest'), ch.quests, sorted(ch.inventory), src, dest, stamp]
    state.append(ch.flags.get('errand'))
    # Read at execution time: development .env may be loaded after module import.
    key = os.environ.get('MAX_WEBHOOK_SECRET')
    return hmac.new(key.encode() if key else _KEY,
                    json.dumps(state, sort_keys=True, ensure_ascii=True).encode(), hashlib.sha256).hexdigest()[:32]


def walk_payload(ch, dest, now=None):
    stamp = str(int(time.time() if now is None else now))
    return f'/mapwalk {ch.room} {dest} {stamp} {_signature(ch, ch.room, dest, stamp)}'


def valid_callback(payload):
    if not isinstance(payload, str) or len(payload) > 512:
        return False
    parts = payload.split()
    if parts == ['/map'] or parts == ['/maplist']:
        return True
    if len(parts) == 2 and parts[0] == '/maplist':
        return parts[1].isdigit() and len(parts[1]) <= 3
    if len(parts) == 2 and parts[0] == '/mapquest':
        return parts[1] in ('none', 'errand') or parts[1] in content.QUESTS
    if len(parts) == 5 and parts[0] == '/mapwalk':
        return (parts[1] in content.WORLD and parts[2] in content.WORLD
                and bool(re.fullmatch(r'[0-9]{1,12}', parts[3]))
                and bool(re.fullmatch(r'[0-9a-f]{32}', parts[4])))
    return False


def resolve_walk(ch, payload, now=None):
    if not valid_callback(payload) or not payload.startswith('/mapwalk '):
        return None
    _, src, dest, stamp, signature = payload.split()
    age = (time.time() if now is None else now) - int(stamp)
    if src != ch.room or not 0 <= age <= TTL or ch.hp <= 0 or ch.flags.get('dead'):
        return None
    if not hmac.compare_digest(signature, _signature(ch, src, dest, stamp)):
        return None
    return next((direction for direction, target in content.WORLD[src]['exits'].items()
                 if target == dest and direction in DIR_LABELS), None)


def button(text, payload):
    return {'type': 'callback', 'text': text[:128], 'payload': payload}


def quest_menu(ch, page=0):
    qids = active(ch)
    page = max(0, min(page, max(0, (len(qids)-1)//PAGE_SIZE)))
    rows = [[button('📜 '+title(ch, qid), '/mapquest '+qid)]
            for qid in qids[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
    nav = []
    if page:
        nav.append(button('← Назад', '/maplist '+str(page-1)))
    if (page+1)*PAGE_SIZE < len(qids):
        nav.append(button('Далее →', '/maplist '+str(page+1)))
    if nav:
        rows.append(nav)
    rows += [[button('🧭 Без маршрута', '/mapquest none'), button('🗺 К карте', '/map')]]
    return rows


def keyboard(ch, path):
    rows = []
    if len(path) > 1:
        dest = path[1]
        direction = next(d for d, r in content.WORLD[ch.room]['exits'].items() if r == dest)
        rows.append([button('🧭 Следующий шаг: '+DIR_LABELS[direction], walk_payload(ch, dest))])
    exits = [button(DIR_LABELS[d]+' · '+content.WORLD[dest]['name'], walk_payload(ch, dest))
             for d, dest in content.WORLD[ch.room]['exits'].items() if d in DIR_LABELS]
    rows += [exits[i:i+2] for i in range(0, len(exits), 2)]
    rows += [[button('📜 Выбрать задание', '/maplist'), button('↻ Обновить', '/map')],
             [{'type': 'message', 'text': '🔍 Осмотр'}, {'type': 'message', 'text': '📜 Задания'}]]
    return rows


def caption(ch, qid, path, hint):
    cur = content.WORLD[ch.room]
    lines = ['🗺 '+cur.get('zone', 'Мир'), '📍 Вы здесь: '+cur['name'], 'Выходы:']
    lines += [DIR_LABELS[d]+' → '+content.WORLD[dest]['name']
              for d, dest in cur['exits'].items() if d in DIR_LABELS]
    if qid:
        if qid == 'errand':
            e = ch.flags['errand']
            progress = e.get('progress', 0) if e['type'] == 'kill' else ch.inventory.count(e['item'])
            goal = f'Прогресс поручения: {progress}/{e["count"]}'
        else:
            goal = quest._goal_text(ch, qid, content.QUESTS[qid])
        lines += ['', '📜 '+title(ch, qid), goal, hint]
        if not path:
            lines.append('Маршрут не найден: сверяйтесь с описанием в журнале.')
        elif len(path) == 1:
            lines.append('⭐ Цель здесь. Выполните действие в этой локации.')
        else:
            direction = next(d for d, dest in cur['exits'].items() if dest == path[1])
            lines += ['⭐ Цель: '+content.WORLD[path[-1]]['name'],
                      f'Переходов: {len(path)-1}. Следующий шаг: {DIR_LABELS[direction]} → '+content.WORLD[path[1]]['name']]
        danger = max((content.MOBS.get(m, {}).get('level', 0) for rid in path[1:]
                      for m in content.WORLD[rid].get('spawns', [])), default=0)
        if danger > ch.level+2:
            lines.append(f'⚠️ На маршруте есть враги до {danger} уровня. Кратчайший путь не означает безопасный.')
    else:
        lines += ['', 'Выберите активное задание для маршрута или исследуйте мир свободно.']
    lines.append('Кнопка перемещения делает один шаг. Во время боя сначала используйте /flee.')
    return '\n'.join(lines)


def image_key(room, path):
    data = json.dumps([room, path], ensure_ascii=True, separators=(',', ':')).encode()
    key = 'map:'+base64.urlsafe_b64encode(data).decode().rstrip('=')
    snapshot(key)
    return key


def snapshot(key):
    if not isinstance(key, str) or not key.startswith('map:') or len(key) > 8192:
        raise ValueError('Invalid map snapshot')
    encoded = key[4:]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded):
        raise ValueError('Invalid map snapshot')
    try:
        room, path = json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
    except (ValueError, TypeError, UnicodeError) as e:
        raise ValueError('Invalid map snapshot') from e
    if (not isinstance(room, str) or room not in content.WORLD or not isinstance(path, list)
            or len(path) > len(content.WORLD) or any(not isinstance(r, str) or r not in content.WORLD for r in path)
            or len(set(path)) != len(path) or (path and path[0] != room)
            or any(b not in content.WORLD[a]['exits'].values() for a, b in zip(path, path[1:]))):
        raise ValueError('Invalid map route')
    return room, path


def asset_path(key):
    snapshot(key)
    return ROOT / (hashlib.sha256(key.encode()).hexdigest()+'.jpg')


def render_asset(key):
    """Reconstruct after restart from the outbox snapshot; bounded disposable cache."""
    room, path = snapshot(key)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = asset_path(key)
    if output.is_file():
        return output
    from bot.max_mapgen import render
    # A single transport upload lock serializes writers. Atomic replace prevents
    # exposing an incomplete image; files can always be recreated from the key.
    with tempfile.NamedTemporaryFile(dir=ROOT, suffix='.jpg', delete=False) as tmp:
        temp = Path(tmp.name)
    try:
        render(room, path).save(temp, 'JPEG', quality=88, optimize=True)
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    files = sorted(ROOT.glob('*.jpg'), key=lambda p: p.stat().st_mtime)
    for old in files[:-512]:
        if old != output:
            old.unlink(missing_ok=True)
    return output
