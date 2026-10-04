"""Presentation-only MAX combat snapshots; never changes combat state."""

HEADER = '⚔️ Боевая сводка\n'
WINDOW_SECONDS = 2
TTL_SECONDS = 300


def status(ch, mob=None):
    hp, maximum = max(0, int(ch.hp)), max(1, int(ch.max_hp))
    lines = [f'❤️ Вы: {hp}/{maximum}']
    if mob is not None:
        name = str(mob.meta.get('name', 'Противник')).replace('*', '')[:160]
        lines.append(f'👹 {name}: {max(0, int(mob.hp))}/{max(1, int(mob.max_hp))}')
    if 0 < hp <= maximum * 0.25:
        lines.append('⚠️ Мало здоровья! Лечитесь или попробуйте /flee.')
    return '\n'.join(lines)


def low_health(ch):
    return ch.hp <= max(1, ch.max_hp) * 0.25


def render(log, snapshot):
    return HEADER + log + '\n\n' + snapshot
