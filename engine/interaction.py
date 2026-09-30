"""Активность и боевое время; общий uid игрока, без привязки к транспорту."""
import time

from . import combat

ACTION_INTERVAL = 1.0
ACTIVE_WINDOW = 300.0


class Presence:
    def __init__(self, window=ACTIVE_WINDOW, clock=time.monotonic):
        self.window = window
        self.clock = clock
        self.last_input = {}

    def touch(self, uid):
        self.last_input[uid] = self.clock()

    def active(self, uid):
        last = self.last_input.get(uid)
        return last is not None and 0 <= self.clock() - last < self.window

    def forget(self, uid):
        self.last_input.pop(uid, None)


class ActionPacer:
    """Общий бюджет атак, умений, зелий и побега; не копит запас действий."""
    def __init__(self, interval=ACTION_INTERVAL, clock=time.monotonic):
        if interval <= 0:
            raise ValueError("Action interval must be positive")
        self.interval = interval
        self.clock = clock
        self.next_action = {}

    def acquire(self, uid):
        now = self.clock()
        wait = max(0.0, self.next_action.get(uid, now) - now)
        if wait > 1e-9:
            return wait
        self.next_action[uid] = now + self.interval
        return 0.0

    def refund(self, uid):
        """Неудачная проверка умения не расходует ход. До неё нет await."""
        self.next_action.pop(uid, None)


class PlayerClock:
    """Откаты/эффекты меняются от времени сервера, а не числа нажатий.

    Остаток доли секунды сохраняется. Долгая пауза не даёт очередь действий
    и не требует тысяч итераций: таймеры истекают арифметически.
    """
    def __init__(self, step=ACTION_INTERVAL, clock=time.monotonic, changed=None):
        if step <= 0:
            raise ValueError("Clock step must be positive")
        self.step = step
        self.clock = clock
        self.changed = changed
        self.last_tick = {}

    def advance(self, ch, regenerate=True):
        now = self.clock()
        last = self.last_tick.setdefault(ch.uid, now)
        turns = int((now - last + 1e-9) / self.step)
        if turns <= 0:
            return False
        self.last_tick[ch.uid] = last + turns * self.step
        before = (ch.mp, [dict(e) for e in ch.effects], dict(ch.cooldowns))
        combat.tick_effects_char(ch, turns=turns)
        if regenerate:
            # Каждый пассивный прирост >=1 и ограничен max_resource;
            # больше этого числа итераций не изменят результат.
            for _ in range(min(turns, max(0, ch.max_resource))):
                ch.regen_resource()
        changed = before != (ch.mp, ch.effects, ch.cooldowns)
        if changed and self.changed:
            self.changed(ch.uid)
        return changed

    def forget(self, uid):
        self.last_tick.pop(uid, None)
