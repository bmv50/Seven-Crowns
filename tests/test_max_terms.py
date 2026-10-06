"""Explicit versioned terms gate: no inferred acceptance or notification consent."""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine import max_terms, max_navigation as nav


async def run():
    values = {'LEGAL_DOCS_URL': 'https://example.org/legal', 'MAX_LEGAL_VERSION': '2026-10-04',
              'SUPPORT_CONTACT': 'support@example.org'}
    with patch.dict(os.environ, values, clear=True):
        config = max_terms.configuration()
        assert config and len(config['token']) == 32
        token = config['token']
        assert nav.validate(nav.terms_keyboard(token)) == nav.terms_keyboard(token)
        assert nav.terms_keyboard(token)[0][0]['text'] == '✅ Принимаю условия'
        assert nav.terms_keyboard(token)[0][0]['payload'] == '/termsagree '+token
        # Old delivered keyboards remain usable; new labels hide the token.
        assert nav.command('✅ Принимаю условия ['+token+']') == '/termsagree '+token
        with patch.dict(os.environ, {'MAX_LEGAL_VERSION': '2026-10-05'}):
            assert max_terms.configuration()['token'] != token
        for url in ('', 'http://example.org/legal', 'https://user:password@example.org', 'https://[УКАЖИТЕ]'):
            with patch.dict(os.environ, {'LEGAL_DOCS_URL': url}):
                try:
                    invalid = max_terms.configuration()
                except ValueError:
                    invalid = None
                assert invalid is None
        store = SimpleNamespace(accepted=AsyncMock(return_value=False), accept=AsyncMock(), revoke=AsyncMock())
        env = dict(max_terms=SimpleNamespace(configuration=max_terms.configuration, ConsentStore=lambda _: store),
                   db=object(), _max_reply=AsyncMock(), _max_navigation=nav, _max_intro=AsyncMock())
        tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_legal_command')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'legal handler', 'exec'), env)
        route = env['_max_legal_command']
        for command in ('start', 'create', 'party', 'say', 'attack'):
            assert await route(-1, command, [command])
            store.accept.assert_not_awaited()
        assert await route(-1, 'termsagree', ['termsagree', 'f'*32])
        store.accept.assert_not_awaited()
        assert await route(-1, 'agree', ['agree'])
        store.accept.assert_not_awaited()
        assert await route(-1, 'termsagree', ['termsagree', token])
        store.accept.assert_awaited_once_with(-1, token)
        env['_max_intro'].assert_awaited_once_with(-1)
        store.accepted.return_value = True
        assert not await route(-1, 'attack', ['attack'])
        assert await route(-1, 'privacy', ['privacy'])
        assert values['LEGAL_DOCS_URL'] in env['_max_reply'].await_args.args[1]
        assert await route(-1, 'termsdecline', ['termsdecline'])
        store.revoke.assert_awaited_once_with(-1)
        assert not await route(-1, 'notify', ['notify', 'off'])
        with patch.dict(os.environ, {'MAX_LEGAL_VERSION': ''}):
            assert await route(-1, 'create', ['create'])
            assert 'готовится к запуску' in env['_max_reply'].await_args.args[1]
        # Database failures fail closed: caller never proceeds to game actions.
        store.accepted.side_effect = ConnectionError()
        try:
            await route(-1, 'attack', ['attack'])
        except ConnectionError:
            pass
        else:
            raise AssertionError('Terms gate ignored unavailable database')


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX explicit terms version, stale buttons, refusal, separate push permission, configuration and database fail-closed')
