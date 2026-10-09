"""Ready combat buttons use shared skill rules and cannot retarget stale casts."""
import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import commands, ui
from bot.max_transport import MaxInput, parse_update
from engine import combat, content, max_encounters, max_navigation, max_ui, quest, skills
from engine.character import Character
from engine.world import World
from test_max_gameplay import load_handler


def selections(ch, menu):
    return [max_ui.resolve(ch, b['payload']) for row in menu for b in row
            if b.get('payload', '').startswith('/ui ')]


def test_menus():
    world = World()
    mob = world.living_in('cellar')[0]
    for cls in content.CLASSES:
        ch = Character(uid=-501, name='Боец', race='human', cls=cls, room='cellar')
        ch.init_vitals(); ch.init_skills(); ch.mp = 1000
        menu = max_encounters.mob_keyboard(ch, mob)
        assert ('consider', mob.key) in selections(ch, menu)
        assert not any(a == 'cast' for a, _ in selections(ch, menu))
        ch.target = mob.key
        before = copy.deepcopy(ch)
        menu = max_ui.with_back(max_encounters.mob_keyboard(ch, mob))
        assert ch == before  # Rendering must never consume or normalize state.
        assert max_navigation.validate(menu) == menu
        casts = {key.split('|')[0] for action, key in selections(ch, menu) if action == 'cast'}
        assert casts == set(ch.skills)
        assert not any(a == 'consider' for a, _ in selections(ch, menu))
        for row in menu:
            for b in row:
                selection = max_ui.resolve(ch, b.get('payload', ''))
                if selection and selection[0] == 'cast':
                    sk = content.SKILLS[selection[1].split('|')[0]]
                    assert sk['name'] in b['text']
                    if sk['mp']:
                        assert ch.resource_emoji+str(sk['mp']) in b['text']
        advanced = next(s for s in skills.all_class_skills(cls) if s not in ch.class_basics)
        ch.learned.append(advanced); ch.loadout.append(advanced)
        assert advanced in max_encounters.combat_skills(ch)
        ch.loadout.remove(advanced)
        assert advanced not in max_encounters.combat_skills(ch)
        sid = ch.skills[0]
        ch.cooldowns[sid] = 1
        assert sid not in max_encounters.combat_skills(ch)
        ch.cooldowns.clear(); ch.mp = 0
        assert all(content.SKILLS[s]['mp'] == 0 for s in max_encounters.combat_skills(ch))
        ch.mp = 1000
        ch.loadout = [sid, sid, 'unknown', 'fireball' if cls != 'mage' else 'heal']
        assert max_encounters.combat_skills(ch) == [sid]
        ch.learned = [s for s in ch.class_basics if s != sid]
        assert not max_encounters.combat_skills(ch)
        ch.learned = list(ch.class_basics); ch.flags['dead'] = True
        assert not max_encounters.combat_skills(ch)
        ch.flags.clear(); ch.hp = 0
        assert not max_encounters.combat_skills(ch)
    ch = Character(uid=-502, name='Маг', race='human', cls='mage', room='cellar')
    ch.init_vitals(); ch.init_skills(); ch.target = mob.key
    ch.target = 'other:enemy:0'
    assert not any(a == 'cast' for a, _ in selections(ch, max_encounters.mob_keyboard(ch, mob)))
    ch.target = mob.key
    token = max_ui.payload(ch, 'cast', 'fireball|'+mob.key, now=1000)
    assert max_ui.resolve(ch, token, now=1001) == ('cast', 'fireball|'+mob.key)
    assert max_ui.resolve(ch, token, now=1181) is None
    for field, value in [('uid', -503), ('generation', ch.generation+1), ('room', 'village')]:
        other = copy.deepcopy(ch); setattr(other, field, value)
        assert max_ui.resolve(other, token, now=1001) is None
    for key in ('unknown|'+mob.key, 'fireball', 'fireball|', 'fireball|'+mob.key+'|extra'):
        try:
            max_ui.payload(ch, 'cast', key)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed cast accepted: '+key)
    update = {'update_type': 'message_callback', 'callback': {'user': {'user_id': 42},
              'callback_id': 'skill', 'payload': max_ui.payload(ch, 'cast', 'fireball|'+mob.key)},
              'message': {'recipient': {'chat_type': 'dialog'}}}
    assert parse_update(update).text == update['callback']['payload']


async def run():
    ch = Character(uid=-501, name='Маг', race='human', cls='mage', room='cellar')
    ch.init_vitals(); ch.init_skills()
    world = World(); mob = world.living_in(ch.room)[0]
    mob.hp = 100000; ch.target = mob.key
    sent, saved, ready = AsyncMock(), AsyncMock(), AsyncMock(return_value=True)
    use = Mock(wraps=combat.use_skill)
    env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character, content=content,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=ch.uid)),
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False), chars={ch.uid: ch},
        send=sent, cmds=commands, WORLD=content.WORLD, ITEMS=content.ITEMS,
        world=world, ui=ui, max_encounters=max_encounters, quest=quest,
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), _max_auction_command=AsyncMock(return_value=False),
        _in_combat=lambda actor: bool(actor.target), _combat_action_ready=ready,
        _combat_party=lambda actor: [actor], _combat_mob=lambda actor: world.find(actor.room, actor.target),
        send_tutorial=AsyncMock(), save=saved, others_in=lambda _: [],
        analytics=SimpleNamespace(track_once=Mock()), combat=SimpleNamespace(use_skill=use),
        _action_pacer=SimpleNamespace(refund=Mock()), _max_combat_progress=AsyncMock(),
        gl=SimpleNamespace(on_mob_death=AsyncMock()))
    handler = load_handler(env)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'text_action')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'shared skill action', 'exec'), env)
    token = max_ui.payload(ch, 'cast', 'fireball|'+mob.key)
    async def click():
        await handler(MaxInput('42', 'skill', token))
    before_mp, before_hp = ch.mp, mob.hp
    with patch('engine.combat.random.random', return_value=0.5):
        await click()
    assert use.call_args.args == (ch, 'fireball', world, [ch])
    assert ch.mp == before_mp-content.SKILLS['fireball']['mp'] and mob.hp < before_hp
    assert ch.cooldowns['fireball'] == content.SKILLS['fireball']['cooldown']
    saved.assert_awaited_with(ch)
    env['_max_combat_progress'].assert_awaited()
    assert 'fireball' not in max_encounters.combat_skills(ch)
    # Reusing a button cannot bypass a cooldown or spend resource twice.
    before = copy.deepcopy(ch); hp = mob.hp
    await click()
    assert ch == before and mob.hp == hp
    env['_action_pacer'].refund.assert_called_with(ch.uid)
    ch.cooldowns.clear(); ch.mp = 0
    await click()
    assert ch.mp == 0 and mob.hp == hp and not ch.cooldowns
    ch.mp = ch.max_mp
    count = use.call_count
    # Active target, panel, learned ownership, class and life are rechecked.
    for mutation in ('target', 'panel', 'learned', 'class', 'dead', 'mob_dead', 'mob_zero'):
        ch.target = mob.key; ch.loadout = list(ch.class_basics); ch.learned = list(ch.class_basics)
        ch.flags.clear(); mob.dead_at = None; mob.hp = hp; ch.cls = 'mage'
        if mutation == 'target': ch.target = 'different:enemy:0'
        elif mutation == 'panel': ch.loadout = ['frost_armor']
        elif mutation == 'learned': ch.learned = ['frost_armor']
        elif mutation == 'class': ch.cls = 'priest'
        elif mutation == 'dead': ch.flags['dead'] = True
        elif mutation == 'mob_dead': mob.dead_at = 1
        elif mutation == 'mob_zero': mob.hp = 0
        await click()
        assert use.call_count == count
    ch.cls = 'mage'; ch.flags.clear(); ch.target = mob.key
    ch.loadout = list(ch.class_basics); ch.learned = list(ch.class_basics)
    mob.hp = hp; mob.dead_at = None
    # A tick while waiting for the combat gate must not trigger auto-retarget.
    async def changed_target(*_):
        ch.target = None
        return True
    ready.side_effect = changed_target
    await click()
    assert use.call_count == count and mob.hp == hp
    ch.target = mob.key; ready.side_effect = None; ready.return_value = False
    await click()
    assert use.call_count == count  # Existing action pacing remains authoritative.


if __name__ == '__main__':
    test_menus()
    asyncio.run(run())
    print('OK: all-class ready skills, signed target binding, real shared casting, cooldown/resource/pacing and stale guards')
