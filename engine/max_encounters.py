"""Read-only MAX room/entity presentation over the live shared world."""
import copy
from . import content, combat, rules2, npc, max_ui
from .textsafe import esc_md

DT = {'bash': 'дробящий', 'slash': 'режущий', 'pierce': 'колющий', 'fire': 'огонь',
      'cold': 'холод', 'holy': 'свет', 'poison': 'яд', 'negative': 'тьма',
      'energy': 'энергия', 'mental': 'разум', 'disease': 'болезнь',
      'lightning': 'молния', 'acid': 'кислота', 'light': 'свет'}


def living(ch, world):
    return [m for m in world.living_in(ch.room) if m.hp > 0]


def room_card(ch, world, others):
    room = content.WORLD[ch.room]
    lines = ['📍 *'+room['name']+'*', '', ' '.join(room.get('desc', '').split())]
    monsters = living(ch, world)
    if monsters:
        lines += ['', '*Противники*']
        for mob in monsters:
            lines += [f"{mob.meta.get('emoji', '🐾')} *{mob.meta['name']}*",
                      f"   ⭐ {mob.meta.get('level', 1)} · ❤️ {mob.hp}/{mob.max_hp}"]
    residents = room.get('npc', [])
    if residents:
        lines += ['', '*Персонажи рядом*']
        lines += [f"💬 *{npc.display_name(key)}* · {npc.role_label(key)}" for key in residents]
    from .world import ground_items_for
    ground = ground_items_for(ch, ch.room)
    if ground:
        lines += ['', '*На земле*', ', '.join(content.ITEMS[key]['name'] for key in ground)]
    for corpse in world.corpses_in(ch.room):
        if corpse.get('loot'):
            lines.append('💀 '+corpse.get('name', 'Останки')+' · есть добыча')
    players = [other for other in others if other.uid != ch.uid]
    if players:
        lines += ['', '*Игроки рядом*', ', '.join(esc_md(other.name) for other in players)]
    lines += ['', '🗺 '+room.get('zone', 'Мир'),
              '🕊️ Безопасная зона' if room.get('safe') else '⚠️ Опасная местность',
              'Нажмите имя ниже, чтобы открыть карточку. Переходы — кнопками меню.']
    return '\n'.join(lines)


def entity_rows(ch, world):
    rows = [[max_ui.button(ch, mob.meta.get('emoji', '🐾')+' '+mob.meta['name']+
                          f" · ур.{mob.meta.get('level', 1)}", 'mob', mob.key)]
            for mob in living(ch, world)[:6]]
    rows += [[max_ui.button(ch, '💬 '+npc.display_name(key), 'npc', key)]
             for key in content.WORLD[ch.room].get('npc', [])[:6]]
    if len(living(ch, world)) > 6:
        rows.append([{'type': 'message', 'text': '🐾 Противники'}])
    return rows


def mob_card(ch, mob, compare=False):
    if compare:
        # Derived equipment/socket properties can lazily normalize flags.
        # Assessment is presentation only, so evaluate them on a snapshot.
        ch = copy.deepcopy(ch)
    meta = mob.meta
    lines = [f"{meta.get('emoji', '🐾')} *{meta['name']}*",
             f"⭐ Уровень {meta.get('level', 1)} · ❤️ {mob.hp}/{mob.max_hp}", '',
             meta.get('desc', 'Противник в текущей локации.'), '',
             f"⚔️ Атака: {meta.get('atk', 0)} · 🛡 Защита: {meta.get('defense', 0)}",
             f"⏱ Интервал ударов: {meta.get('tick_speed', 4)} с"]
    if rules2.ENABLED:
        profile = rules2.mob_profile(meta)
        lines += ['', '*Слабости и сильные стороны*',
                  '🔻 Уязвимости: '+(', '.join(DT.get(x, x) for x in sorted(profile['vuln'])) or 'нет особых'),
                  '🛡 Сопротивления: '+(', '.join(DT.get(x, x) for x in sorted(profile['resist'])) or 'нет особых'),
                  '🚫 Иммунитеты: '+(', '.join(DT.get(x, x) for x in sorted(profile['immune'])) or 'нет'),
                  '💥 Тип урона: '+DT.get(profile['dmg_type'], profile['dmg_type'])]
    if compare:
        difficulty = combat.mob_difficulty(ch.level, meta.get('level', 1))
        level_hint = {'green': '🟢 Ниже вашего уровня', 'yellow': '🟡 Сопоставим с вашим уровнем',
                      'red': '🔴 Выше вашего уровня'}[difficulty]
        lines += ['', '*Оценка боя*', level_hint,
                  f'Вы: ❤️ {ch.hp}/{ch.max_hp} · ⚔️ {ch.attack_power} · 🛡 {ch.defense}']
        if rules2.ENABLED:
            effect = rules2.dtype_effect(combat.weapon_dtype(ch), mob)
            lines.append({'immune': '🚫 Цель невосприимчива к типу вашего оружия.',
                          'resist': '🛡 Цель сопротивляется вашему оружию.',
                          'vuln': '🔻 Ваше оружие попадает в слабость.',
                          'normal': '⚔️ Тип вашего оружия действует обычно.'}.get(effect, ''))
        lines.append('Оценка приблизительная: здоровье, умения, экипировка и случайность влияют на бой. Победа не гарантируется.')
    return '\n'.join(lines)


def mob_keyboard(ch, mob):
    rows = [[max_ui.button(ch, '⚔️ Ударить' if ch.target == mob.key else '⚔️ Напасть', 'attack', mob.key),
             max_ui.button(ch, '🔍 Оценить силы', 'consider', mob.key)]]
    if ch.target:
        rows.append([{'type': 'message', 'text': '🏃 Отступить'}])
    return rows


def npc_card(key):
    meta = content.NPCS[key]
    greeting = next((line for line in meta.get('lines', []) if isinstance(line, str)), '')
    return '\n'.join(['💬 *'+npc.display_name(key)+'*', npc.role_label(key), '',
                      meta.get('desc') or meta.get('greeting') or greeting or
                      'С этим персонажем можно поговорить и узнать о доступных услугах.'])
