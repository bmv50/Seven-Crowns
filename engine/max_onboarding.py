"""Persisted MAX character-creation wizard; presentation never changes balance."""
import json
import re
import uuid
from pathlib import Path

from . import content, rules2

ROOT = Path(__file__).resolve().parent.parent / 'images' / 'onboarding'
STEPS = ('welcome', 'races', 'race_preview', 'classes', 'class_preview', 'name')
ATTR = {'str': 'Сила', 'dex': 'Ловкость', 'int': 'Интеллект', 'spi': 'Дух'}
DAMAGE = {'bash': 'дробящий', 'pierce': 'колющий', 'slash': 'рубящий',
          'fire': 'огонь', 'cold': 'холод', 'lightning': 'молния', 'acid': 'кислота',
          'poison': 'яд', 'disease': 'болезнь', 'negative': 'тьма', 'holy': 'святой урон',
          'energy': 'энергия', 'mental': 'ментальный урон', 'light': 'свет'}
CLASS_CONS = {
    'warrior': ['Нужен ближний бой: нельзя безопасно атаковать издалека.',
                'Нет собственного лечения — пригодятся зелья и союзники.'],
    'mage': ['Нет базового поглощения урона; запас здоровья ниже, чем у воина.',
             'Заклинания расходуют ману: важны паузы и выбор умений.'],
    'rogue': ['Меньше здоровья и поглощения урона, чем у воина.',
              'Критические и двойные удары случайны; собственного лечения нет.'],
    'priest': ['Слабее в прямом бою; лечение требует времени и маны.',
               'Нужно вовремя лечиться и следить за ресурсом.'],
    'paladin': ['Удары и лечение расходуют один запас маны.',
                'Меньше здоровья и базовой защиты, чем у воина.'],
    'necromancer': ['Самый низкий базовый запас здоровья, нет базового поглощения урона.',
                   'Зависит от маны и умений; несколько противников особенно опасны.'],
}


def allowed_classes(race):
    return [key for key in content.RACES[race]['allowed_classes'] if key in content.CLASSES]


def valid_callback(payload):
    if not isinstance(payload, str):
        return False
    return bool(re.fullmatch(r'/termsagree [0-9a-f]{32}', payload)
                or re.fullmatch(r'/onboard [0-9a-f]{32} (begin|confirm|cancel|race [a-z]+|class [a-z]+)', payload))


def asset_keys():
    return {'world'} | set(content.RACES) | {
        f'{race}-{cls}' for race in content.RACES for cls in allowed_classes(race)}


def asset_path(key):
    # Only bundled, allowlisted art: no user-controlled paths, URLs or files.
    if not isinstance(key, str) or key not in asset_keys():
        raise ValueError('Unknown onboarding image')
    return ROOT / f'{key}.jpg'


def fresh(step='welcome', race=None, cls=None):
    return dict(step=step, race=race, cls=cls, token=uuid.uuid4().hex)


def valid_state(state):
    if not isinstance(state, dict) or state.get('step') not in STEPS:
        return False
    if not isinstance(state.get('token'), str) or not re.fullmatch('[0-9a-f]{32}', state['token']):
        return False
    race, cls = state.get('race'), state.get('cls')
    if state['step'] in ('race_preview', 'classes', 'class_preview', 'name') and race not in content.RACES:
        return False
    return state['step'] not in ('class_preview', 'name') or cls in allowed_classes(race)


def advance(state, payload):
    """Validate action against both the current revision and current step."""
    if not valid_state(state) or not valid_callback(payload):
        return None
    parts = payload.split()
    if parts[0] != '/onboard' or parts[1] != state['token']:
        return None
    step, race, cls = state['step'], state.get('race'), state.get('cls')
    action = parts[2]
    if action == 'cancel':
        previous = {'races': 'welcome', 'race_preview': 'races', 'classes': 'race_preview',
                    'class_preview': 'classes', 'name': 'class_preview'}
        return fresh(previous[step], race, cls) if step in previous else None
    if step == 'welcome' and action == 'begin':
        return fresh('races')
    if step == 'races' and action == 'race' and parts[3] in content.RACES:
        return fresh('race_preview', parts[3])
    if step == 'race_preview' and action == 'confirm':
        return fresh('classes', race)
    if step == 'classes' and action == 'class' and parts[3] in allowed_classes(race):
        return fresh('class_preview', race, parts[3])
    if step == 'class_preview' and action == 'confirm':
        return fresh('name', race, cls)
    return None


def race_card(key):
    entry = content.RACES[key]
    plus, minus = [], []
    for attr, value in entry.get('attr_mod', {}).items():
        if value:
            (plus if value > 0 else minus).append(f'{ATTR[attr]} {value:+g}')
    for field, label in (('hp_mod', 'Запас здоровья'), ('mp_mod', 'Запас ресурса')):
        value = round((entry.get(field, 1) - 1) * 100)
        if value:
            (plus if value > 0 else minus).append(f'{label} {value:+d}%')
    for field, label, baseline in (
            ('xp_bonus', 'Получаемый опыт', 1), ('gold_find', 'Золото с врагов', 1),
            ('crit', 'Шанс критического удара', 0), ('dodge', 'Шанс уклонения', 0),
            ('damage_reduction', 'Поглощение урона', 0), ('atk_pct', 'Атака', 0)):
        value = round((entry.get('passives', {}).get(field, baseline) - baseline) * 100)
        if value:
            unit = ' п.п.' if field in ('crit', 'dodge', 'damage_reduction') else '%'
            (plus if value > 0 else minus).append(f'{label} {value:+d}{unit}')
    if rules2.ENABLED:
        profile = rules2._race_profile(key)
        for dtype in sorted(profile['resist'] & set(DAMAGE)):
            plus.append(f"Сопротивление ({DAMAGE[dtype]}): урон −{round((1-rules2.RESIST_MULT)*100)}%")
        for dtype in sorted(profile['immune'] & set(DAMAGE)):
            plus.append(f'Иммунитет: {DAMAGE[dtype]}')
        for dtype in sorted(profile['vuln'] & set(DAMAGE)):
            minus.append(f"Уязвимость ({DAMAGE[dtype]}): урон +{round((rules2.VULN_MULT-1)*100)}%")
    if not minus:
        minus = ['Нет прямых штрафов, но нет узкой специализации других рас.']
    return (f"{entry['emoji']} {entry['name']}\n\n{entry['desc']}\n\n"
            '✅ Преимущества\n' + '\n'.join('• '+p for p in plus) +
            '\n\n⚠️ Недостатки\n' + '\n'.join('• '+p for p in minus) +
            '\n\nДоступные классы: ' + ', '.join(content.CLASSES[c]['name'] for c in allowed_classes(key)))


def class_card(race, cls):
    entry = content.CLASSES[cls]
    bonuses = []
    for field, label, unit in (('damage_reduction', 'Базовое поглощение урона', '%'),
                               ('crit_bonus', 'Критический удар', ' п.п.'),
                               ('double_strike', 'Шанс двойного удара', '%'),
                               ('lifesteal', 'Вампиризм', '%')):
        value = entry.get(field, 0)
        if value:
            bonuses.append(f'{label}: {round(value*100)}{unit}')
    resource = {'mana': 'Мана', 'energy': 'Энергия', 'rage': 'Ярость'}[entry['resource']]
    skills = ', '.join(content.SKILLS[key]['name'] for key in entry['skills'])
    return (f"{entry['emoji']} {content.RACES[race]['name']} · {entry['name']}\n\n{entry['desc']}\n"
            f"Роль: {entry['role']}. Сложность: {entry['difficulty']}/3.\n"
            f"Главная характеристика: {ATTR[entry['primary']]}. Ресурс: {resource}.\n"
            f'Умения класса: {skills}.\n' + ('\n'.join(bonuses)+'\n' if bonuses else '') + '\n' +
            '✅ Преимущества\n' + '\n'.join('• '+p for p in entry['pros']) +
            '\n\n⚠️ Недостатки\n' + '\n'.join('• '+p for p in CLASS_CONS[cls]) +
            ('\n\n'+entry['resource_note'] if entry.get('resource_note') else '') +
            ('\n\n👍 Подходит для первого героя.' if entry.get('newbie_ok') else ''))


def screen(state):
    if not valid_state(state):
        raise ValueError('Invalid onboarding state')
    def button(label, action):
        return {'type': 'callback', 'text': label,
                'payload': f"/onboard {state['token']} {action}"}
    step, race, cls = state['step'], state.get('race'), state.get('cls')
    cancel = [button('Отмена', 'cancel')]
    confirms = [[button('Подтвердить', 'confirm')], cancel]
    if step == 'welcome':
        return ('👑 СЕМЬ КОРОН\n\nПять народов, древние города и опасные земли. '
                'Сражайтесь, выполняйте задания, находите сокровища и объединяйтесь с другими героями. '
                'Мир живёт в реальном времени — вашу историю определяют ваши решения.',
                [[button('Создать героя', 'begin')]], 'world')
    if step == 'races':
        return ('🧬 Выберите расу\n\n'+ '\n'.join(
            f"{r['emoji']} {r['name']} — {r['desc']}" for r in content.RACES.values()),
            [[button(r['name'], 'race '+key)] for key, r in content.RACES.items()] + [cancel], None)
    if step == 'race_preview':
        return race_card(race), confirms, race
    if step == 'classes':
        keys = allowed_classes(race)
        return (f"🎭 Выберите класс · {content.RACES[race]['name']}\n\n" + '\n'.join(
            f"{content.CLASSES[k]['emoji']} {content.CLASSES[k]['name']} — {content.CLASSES[k]['role']}"
            for k in keys), [[button(content.CLASSES[k]['name'], 'class '+k)] for k in keys]+[cancel], None)
    if step == 'class_preview':
        return class_card(race, cls), confirms, f'{race}-{cls}'
    return (f"✍️ Как зовут вашего героя?\n{content.RACES[race]['name']} · {content.CLASSES[cls]['name']}\n\n"
            'Отправьте имя одним сообщением: от 2 до 20 букв или цифр, без пробелов. '
            'После этого герой появится в игре.', [cancel], None)


class Store:
    def __init__(self, pool):
        self.pool = pool

    async def load(self, uid):
        value = await self.pool.fetchval("SELECT state FROM max_onboarding WHERE uid=$1 "
                                         "AND updated_at>now()-interval '7 days'", uid)
        value = json.loads(value) if isinstance(value, str) else value
        return value if valid_state(value) else None

    async def begin(self, uid):
        state = fresh()
        await self.pool.execute('''INSERT INTO max_onboarding(uid,state) VALUES($1,$2)
            ON CONFLICT(uid) DO UPDATE SET state=EXCLUDED.state,updated_at=now()''', uid, json.dumps(state))
        return state

    async def transition(self, uid, old, new):
        return await self.pool.fetchval('''UPDATE max_onboarding SET state=$3,updated_at=now()
            WHERE uid=$1 AND state->>'token'=$2 RETURNING uid''', uid, old['token'], json.dumps(new)) is not None

    async def clear(self, uid):
        await self.pool.execute('DELETE FROM max_onboarding WHERE uid=$1', uid)
