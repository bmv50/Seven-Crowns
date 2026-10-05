"""MAX-only must construct no Telegram client or network session."""
import asyncio
import os
import signal
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.config_check import check_config
from bot import transport_runtime as runtime


BASE = {'TELEGRAM_ENABLED': '0', 'MAX_ENABLED': '1', 'PROD': '1',
        'DATABASE_URL': 'postgresql://user:password@localhost/game', 'ADMIN_IDS': '42',
        'SUPPORT_CONTACT': 'support@example.org', 'LEGAL_DOCS_URL': 'https://example.org/legal',
        'MAX_LEGAL_VERSION': 'v1', 'MAX_BOT_TOKEN': 'test-max-token',
        'MAX_WEBHOOK_SECRET': 'test-secret'}


async def run():
    assert check_config(BASE).ok
    assert check_config({**BASE, 'BOT_TOKEN': 'invalid token', 'PROXY_URL': 'invalid'}).ok
    for key in ('MAX_BOT_TOKEN', 'MAX_WEBHOOK_SECRET', 'DATABASE_URL', 'ADMIN_IDS'):
        assert not check_config({**BASE, key: ''}).ok
    assert not check_config({**BASE, 'MAX_ENABLED': '0'}).ok
    assert not check_config({**BASE, 'TELEGRAM_ENABLED': 'typo'}).ok
    assert not check_config({**BASE, 'STARS_ENABLED': '1'}).ok
    for port in ('0', '65536', 'oops'):
        assert not check_config({**BASE, 'MAX_WEBHOOK_PORT': port}).ok
    assert not check_config({**BASE, 'TELEGRAM_ENABLED': '1'}).ok
    assert not check_config({'BOT_TOKEN': ''}).ok  # Original default remains strict.

    factory, sessions = Mock(), Mock()
    assert runtime.create_telegram_bot(BASE, factory, sessions) is None
    factory.assert_not_called()
    sessions.assert_not_called()
    env = {'BOT_TOKEN': 'test', 'PROXY_URL': 'socks5://example.org'}
    runtime.create_telegram_bot(env, factory, sessions)
    factory.assert_called_once_with(token='test', session=sessions.return_value)
    sessions.assert_called_once_with(proxy=env['PROXY_URL'])
    assert await runtime.telegram_username(None) == ''
    tg = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='old_bot')))
    assert await runtime.telegram_username(tg) == 'old_bot'
    dp = SimpleNamespace(start_polling=AsyncMock())
    with patch.object(runtime, 'wait_for_shutdown', AsyncMock()) as wait:
        await runtime.run_transport(None, dp)
        wait.assert_awaited_once()
        dp.start_polling.assert_not_awaited()
    await runtime.run_transport(tg, dp)
    dp.start_polling.assert_awaited_once_with(tg)

    handlers = {}
    removed = []
    fake_loop = SimpleNamespace(add_signal_handler=lambda sig, cb: handlers.setdefault(sig, cb),
                                remove_signal_handler=removed.append)
    with patch.object(runtime.asyncio, 'get_running_loop', return_value=fake_loop):
        task = asyncio.create_task(runtime.wait_for_shutdown())
        await asyncio.sleep(0)
        assert signal.SIGTERM in handlers
        handlers[signal.SIGTERM]()
        await asyncio.wait_for(task, 1)
    assert set(removed) == {signal.SIGINT, signal.SIGTERM}

    # Import the real application with invalid TG credentials and forbidden networking.
    # Then execute its real startup/shutdown sequence with local transport/DB stand-ins.
    with patch.dict(os.environ, {**BASE, 'BOT_TOKEN': '', 'PROXY_URL': 'invalid',
                                'AI_PROVIDER': 'none', 'NOTIFY': '0', 'WORLD_EVENTS': '0'}, clear=True), \
         patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
        import bot.main as app
        assert app.bot is None
        database = SimpleNamespace(pool=SimpleNamespace(fetchval=AsyncMock(return_value=1), acquire=Mock()),
            connect=AsyncMock(), load_all=AsyncMock(return_value={}),
            kv_get=AsyncMock(return_value=None), kv_set=AsyncMock(), close=AsyncMock(), save=AsyncMock())
        client = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
        runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
        site = SimpleNamespace(start=AsyncMock())

        async def lifetime(bot, dispatcher):
            assert bot is None
            client.start.assert_awaited_once()
            runner.setup.assert_awaited_once()
            site.start.assert_awaited_once()

        with patch.object(app, 'Database', return_value=database), \
             patch.object(app, 'MaxClient', return_value=client), \
             patch.object(app.web, 'AppRunner', return_value=runner), \
             patch.object(app.web, 'TCPSite', return_value=site) as listener, \
             patch.object(app, '_party_restore', AsyncMock()), \
             patch.object(app._mod, 'load', AsyncMock()), \
             patch.object(app.guild_tx, 'load_guilds', AsyncMock(return_value=[])), \
             patch.object(app.guild_store, 'load', AsyncMock(return_value={})), \
             patch.object(app, '_spawn_worker', Mock()) as spawn, \
             patch.object(app, '_stop_max_workers', AsyncMock()), \
             patch.object(app._persist, 'flush_until_clean', AsyncMock(return_value=(0, 0))), \
             patch.object(app, '_flush_world_snapshot', AsyncMock()) as snapshot, \
             patch.object(app, 'run_transport', lifetime), \
             patch.object(app.dp, 'start_polling', AsyncMock()) as polling:
            await app.main()
            polling.assert_not_awaited()
            listener.assert_called_once_with(runner, '0.0.0.0', 8080)
            names = {call.args[0] for call in spawn.call_args_list}
            assert {'game_loop', 'snapshot_worker', 'max_inbox_worker', 'max_outbox_worker'} <= names
            snapshot.assert_awaited_once()
            database.close.assert_awaited_once()
            client.close.assert_awaited_once()
            runner.cleanup.assert_awaited_once()
            # A failed listener bind must still close MAX and snapshot the world.
            site.start.side_effect = OSError('port already in use')
            try:
                await app.main()
            except OSError:
                pass
            else:
                raise AssertionError('Listener failure was swallowed')
            assert snapshot.await_count == database.close.await_count == client.close.await_count == 2


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX-only configuration, no Telegram construction/network, real startup and shutdown')
