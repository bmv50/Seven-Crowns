"""Common quest/errand base rewards are durable before failing level callbacks."""
import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine import game_actions
from engine.character import Character, START_ROOM
from engine.db import Database
from test_shared_npc_quests import load_core


async def run(dsn):
    import asyncpg
    schema = 'test_reward_checkpoint_' + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    factory = asyncpg.create_pool
    def scoped(*args, **kwargs):
        kwargs['server_settings'] = {'search_path': schema}
        return factory(*args, **kwargs)
    async def connect():
        with patch('engine.db.asyncpg.create_pool', side_effect=scoped):
            await db.connect()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await connect()
        for platform in ('telegram', 'max'):
            for index, kind in enumerate(('quest', 'errand')):
                uid = (100+index if platform == 'telegram' else
                       await db.reserve_max_player_id('checkpoint-'+kind))
                ch = Character(uid=uid, name=f'Запись{platform}{kind}', race='human', cls='warrior')
                ch.init_vitals()
                ch.room = START_ROOM
                if kind == 'quest':
                    ch.quests.update({'sample_reach_well': 'active', 'sample_reach_well:reach': '1'})
                else:
                    ch.flags['errand'] = {'npc': 'наставник', 'type': 'kill', 'mob': 'крыса',
                        'count': 1, 'progress': 1, 'reward': {'gold': 123, 'xp': 45, 'items': []}}
                initial_gold = ch.gold
                await db.create_character(ch)
                checkpoints = []
                async def save(actor, force=False):
                    checkpoints.append(force)
                    if force:
                        await db.save(actor)
                env = load_core(dict(game_actions=game_actions, save=save,
                    gl=SimpleNamespace(_check_levelup=AsyncMock(side_effect=RuntimeError('callback failed')))))
                action = env['complete_quest_core'] if kind == 'quest' else env['complete_errand_core']
                key = 'sample_reach_well' if kind == 'quest' else 'наставник'
                try:
                    await action(ch, key)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Callback failure not injected')
                assert checkpoints == [True]
                # Immediate restart before any dirty snapshot flush.
                await db.close()
                await connect()
                restored = (await db.load_all())[uid]
                assert restored.gold == initial_gold + (1500 if kind == 'quest' else 123)
                assert (restored.quests['sample_reach_well'] == 'done' if kind == 'quest'
                        else 'errand' not in restored.flags)
                balance = restored.gold
                assert not (await action(restored, key))[0]
                assert restored.gold == balance and checkpoints == [True]
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL Telegram/MAX quest and errand reward checkpoint, failing callbacks, immediate restart and no second reward')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL common reward tests run in CI')
