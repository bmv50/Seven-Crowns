"""Read-only MAX release checks. Never print secrets, raw exceptions or player data."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.max_terms import configuration
from bot.transport_runtime import telegram_enabled

TABLES = ('characters', 'platform_identities', 'max_inbox', 'max_outbox',
          'max_shop_intents', 'max_service_intents', 'max_choice_intents',
          'max_terms_acceptance', 'economy_ledger')


def check_config(env):
    def present(key):
        value = env.get(key, '').strip()
        return bool(value and '[УКАЖИТЕ' not in value and 'ВСТАВЬ' not in value)
    try:
        port_ok = 1 <= int(env.get('MAX_WEBHOOK_PORT', '8080')) <= 65535
    except ValueError:
        port_ok = False
    return {'max_enabled': env.get('MAX_ENABLED', '0').strip().lower() in ('1', 'true', 'yes', 'on'),
            'production_mode': env.get('PROD', '0').strip() == '1',
            'database_configured': present('DATABASE_URL'),
            'telegram_token_configured': not telegram_enabled(env) or present('BOT_TOKEN'),
            'max_token_configured': present('MAX_BOT_TOKEN'),
            'webhook_secret_configured': present('MAX_WEBHOOK_SECRET'),
            'webhook_port_valid': port_ok, 'legal_configuration_ready': configuration(env) is not None}


async def check_database(dsn):
    import asyncpg
    con = None
    try:
        con = await asyncpg.connect(dsn, timeout=10, command_timeout=10)
        # No Database.connect(): that method would apply migrations.
        async with con.transaction(readonly=True):
            missing = [table for table in TABLES if not await con.fetchval('SELECT to_regclass($1)', table)]
            result = {'connected': True, 'schema_ready': not missing, 'missing_tables': missing}
            if not missing:
                result['inbox_counts'] = {r['status']: r['n'] for r in await con.fetch(
                    'SELECT status,count(*) AS n FROM max_inbox GROUP BY status')}
                result['outbox_counts'] = {r['status']: r['n'] for r in await con.fetch(
                    'SELECT status,count(*) AS n FROM max_outbox GROUP BY status')}
                result['stalled_outbox'] = await con.fetchval("""SELECT count(*) FROM max_outbox
                    WHERE status='pending' AND created_at<now()-interval '5 minutes'""")
            return result
    except Exception:
        # Exception text can contain DSN/host/password. Do not echo it.
        return {'connected': False, 'schema_ready': False, 'error': 'database_check_failed'}
    finally:
        if con is not None:
            await con.close()


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', action='store_true', help='Explicit read-only SQL check using DATABASE_URL')
    args = parser.parse_args(argv)
    checks = check_config(os.environ)
    result = {'configuration': checks, 'configuration_ready': all(checks.values()),
              'live_delivery_verified': False,
              'note': 'Checks do not prove approved legal documents, backup restore, single process, webhook routing or payment readiness.'}
    if args.database:
        result['database'] = await check_database(os.getenv('DATABASE_URL', ''))
    ready = result['configuration_ready'] and (not args.database or result['database']['schema_ready'])
    result['checks_passed'] = ready
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ready else 2


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
