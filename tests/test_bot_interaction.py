"""Реальные функции транспорта без импорта main/.env и без сетевых запросов."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from engine.character import Character
from engine import content, combat, notify
from engine.interaction import Presence, ActionPacer, PlayerClock
from engine.notification_delivery import NotificationDelivery
from engine.world import World, MobInstance
from engine.loop import GameLoop
from engine.social import DuelManager


def load_functions(names, env):
    path = Path(__file__).resolve().parents[1] / "bot" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    assert len(nodes) == len(names), "Не все функции найдены"
    env = {"asyncio": asyncio, "Character": Character, "CallbackQuery": object, **env}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


async def test_outgoing_does_not_mark_seen():
    seen = AsyncMock()
    env = load_functions({"send"}, {
        "bot": SimpleNamespace(send_message=AsyncMock()), "_touch_seen": seen,
        "_note_tg_error": lambda e: None,
    })
    await env["send"](1, "Текст")
    assert seen.await_count == 0, "Исходящее сообщение не является активностью игрока"


async def test_input_seen_and_transport_errors():
    db = SimpleNamespace(pool=object(), touch_last_seen=AsyncMock(side_effect=[RuntimeError("test"), None]),
                         log_notify=AsyncMock(side_effect=RuntimeError("log unavailable")))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    logger = SimpleNamespace(log_err=Mock())
    env = load_functions({"_touch_seen", "_send_notification"}, {
        "chars": {1: object()}, "db": db, "bot": bot, "_last_seen_at": {},
        "_elog": logger, "_log": None, "TelegramBadRequest": ValueError,
        "_note_tg_error": Mock(), "analytics": SimpleNamespace(track=Mock()),
        "_last_notify_sent": {},
    })
    await env["_touch_seen"](1)
    assert not env["_last_seen_at"]
    await env["_touch_seen"](1)
    await env["_touch_seen"](1)
    assert db.touch_last_seen.await_count == 2
    # Ошибка журнала не делает успешную отправку «неуспешной» для квоты.
    assert await env["_send_notification"](1, "daily_reset", "text") is True
    assert 1 in env["_last_notify_sent"]
    db.mark_notify_blocked = AsyncMock()
    bot.send_message.side_effect = RuntimeError("Forbidden: blocked")
    assert await env["_send_notification"](1, "daily_reset", "text") is False
    db.mark_notify_blocked.assert_awaited_once_with(1)
    # Медленная запись last_seen не заставляет ждать игровую команду.
    started = asyncio.Event()
    release = asyncio.Event()
    async def slow_seen(uid):
        started.set()
        await release.wait()
    env = load_functions({"_on_input"}, {"_mark_session": Mock(),
        "_touch_seen": slow_seen, "_seen_tasks": {}, "chars": {1: object()}})
    await asyncio.wait_for(env["_on_input"](1), timeout=0.2)
    await started.wait()
    await env["_on_input"](1)
    assert len(env["_seen_tasks"]) == 1
    task = env["_seen_tasks"][1]
    release.set()
    await task
    await asyncio.sleep(0)
    assert not env["_seen_tasks"]


def game_environment():
    now = [0.0]
    world = World()
    mob = next(m for mobs in world.mobs.values() for m in mobs if not m.meta.get("boss"))
    world.mobs = {mob.room: [mob]}
    mob.hp = mob.max_hp = 1000000
    ch = Character(uid=1, name="Игрок", cls="mage", race="human", room=mob.room)
    ch.init_vitals()
    ch.init_skills()
    chars = {1: ch}
    presence = Presence(clock=lambda: now[0])
    presence.touch(1)
    save = AsyncMock()
    loop = GameLoop(world, chars, AsyncMock(), save)
    cv = {}
    env = load_functions({"_in_combat", "_combat_mob", "_leave_combat", "_combat_party",
        "_combat_action_ready", "move_core", "do_attack", "do_skill", "do_use", "do_flee",
        "text_action", "_consume_item", "apply_attrbuff", "card_use", "do_dungeon",
        "duel_attack", "duel_potion", "pvp_attack", "duel_accept"}, {
        "world": world, "chars": chars, "gl": loop, "combat": combat,
        "content": content, "ITEMS": content.ITEMS, "SKILLS": content.SKILLS,
        "WORLD": content.WORLD, "_presence": presence,
        "duel_mgr": DuelManager(),
        "_action_pacer": ActionPacer(clock=lambda: now[0]),
        "_player_clock": PlayerClock(clock=lambda: now[0]),
        "quest": SimpleNamespace(on_use_item=Mock(return_value=["Квестовый зачёт"])),
        "_ATTR_RU": {}, "save": save, "send_tutorial": AsyncMock(),
        "analytics": SimpleNamespace(track_once=Mock()),
        "combat_view": cv, "_cv": lambda uid: cv.setdefault(uid, {}),
        "set_combat_line": Mock(), "safe_edit": AsyncMock(),
        "start_combat_view": AsyncMock(), "render_combat_cb": AsyncMock(),
        "drop_combat_photo": AsyncMock(), "show_room": AsyncMock(),
        "safe_edit_caption": AsyncMock(),
        "refresh_duel": AsyncMock(), "end_duel": AsyncMock(),
        "_has_heal_potion": lambda ch: next((k for k in ch.inventory
            if content.ITEMS[k].get("effect", {}).get("heal")), None),
        "enter_room": AsyncMock(), "others_in": lambda room: [],
        "ui": SimpleNamespace(render_room=lambda *a: "Комната", kb_room=lambda *a: None),
        "bot": SimpleNamespace(send_message=AsyncMock()),
        "random": SimpleNamespace(random=lambda: 0.0),
    })
    cb = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(
        chat=SimpleNamespace(id=1), message_id=100, delete=AsyncMock()))
    msg = SimpleNamespace(answer=AsyncMock())
    return env, ch, mob, cb, msg, now


async def test_buttons_and_text_share_tempo():
    env, ch, mob, cb, msg, now = game_environment()
    heal = next(k for k, v in content.ITEMS.items() if v.get("effect", {}).get("heal"))
    ch.inventory = [heal, heal]
    await env["do_attack"](cb, ch, mob.key)
    hp = mob.hp
    for _ in range(20):
        await env["do_attack"](cb, ch, mob.key)
    sid = next(s for s in ch.skills if content.SKILLS[s]["kind"] == "damage")
    await env["do_skill"](cb, ch, sid)
    await env["do_use"](cb, ch, heal)
    await env["card_use"](cb, ch, heal)
    await env["text_action"](msg, ch, "use", heal)
    await env["text_action"](msg, ch, "flee", "")
    assert mob.hp == hp and ch.inventory == [heal, heal]
    assert ch.cooldowns.get(sid, 0) == 0 and ch.uid in mob.aggro
    now[0] = 1
    await env["do_skill"](cb, ch, sid)
    assert ch.cooldowns[sid] == content.SKILLS[sid]["cooldown"]
    now[0] = 2
    await env["do_skill"](cb, ch, "__unknown__")
    # Неудачное умение возвращает слот, но НЕ тикает часы дополнительно.
    cd = ch.cooldowns[sid]
    await env["do_attack"](cb, ch, mob.key)
    assert ch.cooldowns[sid] == cd
    assert mob.hp < hp
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "advance_player_turn"
                   for n in ast.walk(ast.parse(Path(__file__).resolve().parents[1].joinpath(
                       "bot/main.py").read_text(encoding="utf-8"))))


async def test_item_parity_and_escape():
    env, ch, mob, cb, msg, now = game_environment()
    attr_item = next(k for k, v in content.ITEMS.items() if v.get("effect", {}).get("attrbuff"))
    ch.inventory = [attr_item, attr_item]
    await env["do_use"](cb, ch, attr_item)
    first = [dict(e) for e in ch.effects]
    ch.effects = []
    now[0] = 1
    await env["text_action"](msg, ch, "use", attr_item)
    assert ch.effects == first and not ch.inventory
    assert env["quest"].on_use_item.call_count == 2
    # Неактивный сосед не лечится просто потому, что был загружен из БД.
    offline = Character(uid=2, name="Оффлайн", cls="warrior", room=ch.room)
    offline.init_vitals()
    env["chars"][2] = offline
    assert env["_combat_party"](ch) == [ch]
    mob.aggro = [1, 2]
    assert offline in env["_combat_party"](ch)  # уже начатый бой не замораживаем
    extra = MobInstance("extra", mob.mob_id, ch.room)
    extra.aggro = [1]
    extra.threat[1] = 10
    env["world"].mobs[ch.room].append(extra)
    ch.target = None  # агрессор атаковал без выбранной цели
    direction = next(iter(content.WORLD[ch.room]["exits"]))
    original_room = ch.room
    assert await env["move_core"](ch, direction) == (False, False)
    assert ch.room == original_room
    now[0] = 2
    await env["do_flee"](cb, ch)
    assert not env["_in_combat"](ch) and ch.uid not in extra.threat
    assert mob.aggro == [2] and ch.target is None
    assert env["save"].await_count >= 3  # успешный побег тоже сохраняется
    # Ритуальный расходник без heal/mana не теряет use-цель квеста.
    ritual = next(k for k, v in content.ITEMS.items()
                  if v.get("type") == "consumable" and not v.get("effect"))
    ch.inventory = [ritual]
    now[0] = 3
    await env["card_use"](cb, ch, ritual)
    assert not ch.inventory and env["quest"].on_use_item.call_args.args[1] == ritual
    mob.aggro = [1]
    await env["do_dungeon"](cb, ch, "__any__")
    assert ch.room == original_room


async def test_duel_and_dead_player_guards():
    env, ch, mob, cb, msg, now = game_environment()
    pot = next(k for k, v in content.ITEMS.items() if v.get("effect", {}).get("heal"))
    ch.inventory = [pot, pot]
    opp = Character(uid=2, name="Соперник", cls="warrior", room=ch.room)
    opp.init_vitals()
    env["chars"][2] = opp
    dm = env["duel_mgr"]
    await env["pvp_attack"](cb, ch, 2)
    assert not dm.duels  # загруженный оффлайн-герой не является PvP-мишенью
    dm.challenge(2, 1)
    await env["duel_accept"](cb, ch)
    assert not dm.duels and not dm.requests  # старый вызов не переносит в бой
    dm.challenge(1, 2)
    dm.accept(2)
    assert env["_in_combat"](ch)
    await env["card_use"](cb, ch, pot)
    await env["text_action"](msg, ch, "use", pot)
    assert ch.inventory == [pot, pot]
    await env["duel_potion"](cb, ch)
    assert ch.inventory == [pot] and dm.whose_turn(1) == 2
    dm.pass_turn(2)
    await env["duel_potion"](cb, ch)
    assert ch.inventory == [pot]  # прежняя кнопка/быстрый возврат хода не обходят темп
    now[0] = 1
    await env["duel_attack"](cb, ch)
    assert dm.whose_turn(1) == 2
    dm.end(1)
    ch.hp = 0
    now[0] = 2
    await env["do_use"](cb, ch, pot)
    assert ch.hp == 0 and ch.inventory == [pot]


async def test_windup_has_response_window():
    env, ch, mob, cb, msg, now = game_environment()
    ch.learned = ch.loadout = ["frostbolt"]
    ch.target = mob.key
    mob.aggro = [ch.uid]
    env["_action_pacer"].acquire(ch.uid)  # только что походил перед предупреждением
    combat.start_windup(mob)
    await env["do_skill"](cb, ch, "frostbolt")
    assert combat.is_winding_up(mob) and not combat.mob_is_disabled(mob)
    now[0] = 1
    with patch("engine.combat.random.random", return_value=0), \
         patch("engine.combat.rules2.ENABLED", False):
        await env["do_skill"](cb, ch, "frostbolt")
    assert combat.mob_is_disabled(mob)
    assert min(m.get("tick_speed", 4) for m in content.MOBS.values()) >= 2
    mob.last_tick = 0
    env["gl"].on_combat_hit = AsyncMock()
    with patch("engine.loop.time.time", return_value=mob.meta.get("tick_speed", 4)), \
         patch("engine.loop.npc_ai.ENABLED", False), \
         patch("engine.loop.events.ENABLED", False), \
         patch("engine.loop.notify.ENABLED", False), \
         patch("engine.loop.BOSS_CFG", []), \
         patch.object(env["world"], "process_roaming", return_value=[]):
        await env["gl"].tick()
    assert not combat.is_winding_up(mob)
    assert env["gl"].on_combat_hit.await_count == 0


async def test_ambient_and_broadcast_policy():
    notify.ENABLED = True
    notify.clear()
    active = Presence()
    active.touch(1)
    chars = {uid: Character(uid=uid, name=str(uid), cls="warrior", room="room") for uid in (1, 2)}
    for ch in chars.values():
        ch.init_vitals()
        notify.set_opt_in(ch, True)
        notify.set_quiet_off(ch, True)
    sender = AsyncMock(return_value=True)
    delivery = NotificationDelivery(chars.get, sender)
    async def route(uid, category, text, **kwargs):
        return await delivery.deliver(uid, category, text, **kwargs)
    ephemeral = AsyncMock()
    db = SimpleNamespace(pool=object(), list_notify_targets=AsyncMock(return_value=[(1,), (2,)]))
    env = load_functions({"broadcast_ephemeral", "broadcast_all", "others_in",
                          "broadcast_world_event", "_queue_world_notify"}, {
        "chars": chars, "_presence": active, "EPHEMERAL_TTL": 40,
        "send_ephemeral": ephemeral, "_notify": notify, "db": db,
        "_notify_deliver": route,
    })
    await env["broadcast_ephemeral"]("room", "Шаги")
    assert ephemeral.await_args.args[0] == 1 and ephemeral.await_count == 1
    assert env["others_in"]("room") == [chars[1]]
    await env["broadcast_world_event"]("Событие", 40)
    await env["_queue_world_notify"]("Босс", "world_boss")
    assert sender.await_count == 0 and notify.pending() == 2
    assert notify._QUEUE[0]["uid"] == 1 and notify._QUEUE[0]["expires_at"] is not None
    assert notify._QUEUE[1]["uid"] is None
    notify.clear()
    notify.set_opt_in(chars[2], False)
    assert await env["broadcast_all"]("Босс", "world_boss", ttl=10) == 1
    assert sender.await_count == 1
    # Пустая БД-выборка не означает «всё равно написать всему кэшу».
    db.list_notify_targets.return_value = []
    assert await env["broadcast_all"]("Босс", "world_boss") == 0
    assert sender.await_count == 1
    notify.ENABLED = False


async def main():
    await test_outgoing_does_not_mark_seen()
    await test_input_seen_and_transport_errors()
    await test_buttons_and_text_share_tempo()
    await test_item_parity_and_escape()
    await test_duel_and_dead_player_guards()
    await test_windup_has_response_window()
    await test_ambient_and_broadcast_policy()
    print("OK: реальные обработчики кнопок/текста, предметы, побег, активность и рассылка")


if __name__ == "__main__":
    asyncio.run(main())
