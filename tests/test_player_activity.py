"""Активные комнаты, пассивные герои, продолжение боя и повтор награды в GameLoop."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

from engine import content
from engine.character import Character
from engine.world import World
from engine.loop import GameLoop
from engine.interaction import PlayerClock


async def main():
    world = World()
    rooms = [rid for rid, mobs in world.mobs.items() if mobs]
    r1, r2, r3 = rooms[:3]
    mob = world.mobs[r2][0]
    chars = {i: Character(uid=i, name=f"Герой{i}", cls="mage", room=room)
             for i, room in enumerate((r1, r2, r3), 1)}
    for ch in chars.values():
        ch.init_vitals()
        ch.mp = 0
        ch.cooldowns = {"synthetic": 4}
    mob.aggro = [2]
    mob.last_tick = 0
    now = [0.0]
    save, send = AsyncMock(), AsyncMock()
    loop = GameLoop(world, chars, send, save)
    loop.is_active = lambda uid: uid == 1
    loop.player_clock = PlayerClock(clock=lambda: now[0])
    loop.on_combat_hit = AsyncMock()
    expected_rooms = {r1, r2}
    for ch in chars.values():
        loop.player_clock.advance(ch)
    now[0] = 2
    caught = Mock()
    with patch("engine.loop.catchup.ENABLED", True), \
         patch("engine.loop.catchup.active_set", side_effect=lambda rooms: list(rooms)), \
         patch("engine.loop.catchup.tick", caught), \
         patch("engine.loop.npc_ai.ENABLED", False), \
         patch("engine.loop.events.ENABLED", False), \
         patch("engine.loop.notify.ENABLED", False), \
         patch("engine.loop.BOSS_CFG", []), \
         patch.object(world, "process_roaming", return_value=[]), \
         patch("engine.loop.combat.mob_attack", return_value=["Тестовый удар"]) as attack:
        await loop.tick()
    assert caught.call_args.args[1] == expected_rooms
    assert attack.call_count >= 1 and attack.call_args.args[1].uid == 2
    assert chars[1].mp > 0 and chars[2].mp > 0 and chars[3].mp == 0
    assert all(ch.cooldowns["synthetic"] == 2 for ch in chars.values())
    assert loop.party_in(r3) == []
    loop.on_world_notify = AsyncMock()
    await loop.announce("Мировой босс", "world_boss")
    loop.on_world_notify.assert_awaited_once_with("Мировой босс", "world_boss")
    loop.on_world_notify = None
    send.reset_mock()
    await loop.announce("Объявление", "world_event")
    assert [c.args[0] for c in send.await_args_list] == [1]

    # Неактивный участник сохраняет награду, доставка идёт по push-политике.
    loop.on_personal_notify = AsyncMock()
    loop._check_levelup = AsyncMock()
    before = chars[3].gold
    await loop._reward_event_goal({"name": "Событие", "share": 0.5, "contrib": {3: 1}})
    assert chars[3].gold > before
    loop.on_personal_notify.assert_awaited_once()

    # Даже второй обработчик смерти не выдаёт двойные XP/деньги/событийный зачёт.
    mob = next(m for mobs in world.mobs.values() for m in mobs if not m.meta.get("boss"))
    ch = chars[1]
    ch.room = mob.room
    mob.aggro = [1]
    mob.add_contrib(1, 10)
    mob.hp = 0
    await loop.on_mob_death(mob, [ch])
    reward = (ch.xp, ch.gold, len(ch.inventory))
    await loop.on_mob_death(mob, [ch])
    assert (ch.xp, ch.gold, len(ch.inventory)) == reward
    # Летальный удар фиксируется до сетевых ответов; HUD не даёт окно для «воскрешения зельем».
    victim = chars[2]
    victim.hp = victim.max_hp
    victim.flags["dead"] = False
    fatal_mob = world.mobs[r2][0]
    fatal_mob.aggro = [2]
    fatal_mob.last_tick = 0
    def fatal_attack(mob, target, **kwargs):
        target.hp = 0
        return ["Летальный удар"]
    async def death_screen(ch):
        assert ch.flags["dead"] and ch.hp == 0 and ch.target is None
    loop.on_death = death_screen
    loop.on_combat_hit.reset_mock()
    with patch("engine.loop.npc_ai.ENABLED", False), \
         patch("engine.loop.events.ENABLED", False), \
         patch("engine.loop.notify.ENABLED", False), \
         patch("engine.loop.BOSS_CFG", []), \
         patch("engine.loop.combat.telegraph_due", return_value=False), \
         patch("engine.loop.combat.mob_attack", side_effect=fatal_attack), \
         patch.object(world, "process_roaming", return_value=[]):
        await loop.tick()
    assert victim.flags["dead"] and victim.hp == 0 and 2 not in fatal_mob.aggro
    assert loop.on_combat_hit.await_count == 0
    print("OK: активные комнаты, незамороженный бой, таймеры оффлайн и идемпотентная награда")


if __name__ == "__main__":
    asyncio.run(main())
