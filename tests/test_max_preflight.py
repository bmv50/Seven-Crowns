"""Release diagnostics never migrate a database or echo secrets."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('max_preflight', Path('scripts/max_preflight.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


async def run():
    env = {'MAX_ENABLED': '1', 'PROD': '1', 'DATABASE_URL': 'sensitive-dsn',
           'BOT_TOKEN': 'sensitive-tg', 'MAX_BOT_TOKEN': 'sensitive-max',
           'MAX_WEBHOOK_SECRET': 'sensitive-secret', 'MAX_WEBHOOK_PORT': '8080',
           'LEGAL_DOCS_URL': 'https://example.org/terms', 'MAX_LEGAL_VERSION': 'v1',
           'SUPPORT_CONTACT': 'support@example.org'}
    result = module.check_config(env)
    assert all(result.values())
    assert 'sensitive' not in str(result)
    assert all(module.check_config(dict(env, TELEGRAM_ENABLED='0', BOT_TOKEN='')).values())
    for key in ('DATABASE_URL', 'BOT_TOKEN', 'MAX_BOT_TOKEN', 'MAX_WEBHOOK_SECRET', 'MAX_LEGAL_VERSION'):
        assert not all(module.check_config(dict(env, **{key: ''})).values())
    assert not module.check_config(dict(env, MAX_WEBHOOK_PORT='no'))['webhook_port_valid']
    assert not module.check_config(dict(env, MAX_ENABLED='0'))['max_enabled']
    assert not module.check_config(dict(env, LEGAL_DOCS_URL='http://example.org'))['legal_configuration_ready']
    import asyncpg
    with patch.object(asyncpg, 'connect', AsyncMock(side_effect=RuntimeError('password: secret'))):
        result = await module.check_database('secret-dsn')
        assert result == {'connected': False, 'schema_ready': False, 'error': 'database_check_failed'}
        assert 'password' not in str(result) and 'secret' not in str(result)
    con = SimpleNamespace(transaction=lambda **kwargs: Transaction(kwargs),
        fetchval=AsyncMock(return_value=None), close=AsyncMock())
    class Transaction:
        def __init__(self, kwargs): assert kwargs == {'readonly': True}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    with patch.object(asyncpg, 'connect', AsyncMock(return_value=con)):
        result = await module.check_database('secret-dsn')
    assert result['missing_tables'] == list(module.TABLES) and not result['schema_ready']
    assert con.fetchval.await_count == len(module.TABLES)
    assert all(call.args[0] == 'SELECT to_regclass($1)' for call in con.fetchval.await_args_list)
    con.close.assert_awaited_once()
    con.fetchval = AsyncMock(side_effect=list(module.TABLES)+[0])
    con.fetch = AsyncMock(return_value=[])
    with patch.object(asyncpg, 'connect', AsyncMock(return_value=con)):
        result = await module.check_database('secret-dsn')
    assert result['schema_ready'] and result['stalled_outbox'] == 0
    assert result['inbox_counts'] == result['outbox_counts'] == {}


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX release configuration, read-only schema checks, no migrations and secret-safe errors')
