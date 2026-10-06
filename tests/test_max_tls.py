"""Custom CA trust is local to MAX; TLS verification cannot be disabled."""
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import ssl
import tempfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.max_tls import max_ssl_context
from bot.config_check import check_config
from bot import max_transport


spec = importlib.util.spec_from_file_location('max_subscribe', Path('scripts/max_subscribe.py'))
subscribe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subscribe)
URL = 'https://example.org/max/webhook'
ENV = {'MAX_BOT_TOKEN': 'test-token', 'MAX_WEBHOOK_SECRET': 'test-secret'}


def call(*args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = subscribe.main(args)
    return code, json.loads(output.getvalue())


async def run():
    baseline = ssl.create_default_context()
    roots_before = baseline.get_ca_certs(binary_form=True)
    assert roots_before, 'System root store is required for the test'
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / 'max-ca.pem'
        bundle.write_text(ssl.DER_cert_to_PEM_cert(roots_before[0]), encoding='ascii')
        invalid = Path(tmp) / 'invalid.pem'
        invalid.write_text('not a certificate', encoding='ascii')
        context = max_ssl_context(str(bundle))
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert not context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
        assert baseline.get_ca_certs(binary_form=True) == roots_before
        assert context is not baseline
        # In an empty context, exactly this selected CA is added; no global mutation.
        empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch('bot.max_tls.ssl.create_default_context', return_value=empty):
            assert max_ssl_context(str(bundle)) is empty
        assert empty.cert_store_stats()['x509_ca'] == 1
        for path in (str(invalid), str(Path(tmp) / 'missing.pem')):
            try:
                max_ssl_context(path)
            except ValueError as e:
                assert str(e) == 'MAX_CA_FILE cannot be loaded as a PEM CA bundle'
                assert path not in str(e)
            else:
                raise AssertionError('Invalid CA bundle accepted')
        config = {'BOT_TOKEN': '123:token', 'MAX_ENABLED': '1',
            'DATABASE_URL': 'db', **ENV, 'MAX_CA_FILE': str(bundle)}
        assert check_config(config).ok
        assert not check_config({**config, 'MAX_CA_FILE': str(invalid)}).ok
        with patch.dict(os.environ, {'MAX_CA_FILE': str(bundle)}):
            fake_session = SimpleNamespace(close=AsyncMock())
            with patch.object(max_transport, 'ClientSession', return_value=fake_session) as factory:
                client = max_transport.MaxClient('test-token')
                await client.start()
                connector = factory.call_args.kwargs['connector']
                assert isinstance(connector._ssl, ssl.SSLContext)
                assert connector._ssl.check_hostname
                assert connector._ssl.verify_mode == ssl.CERT_REQUIRED
                await client.close()
                fake_session.close.assert_awaited_once()
                await connector.close()

        with patch.dict(os.environ, {**ENV, 'MAX_CA_FILE': str(bundle)}, clear=True), \
             patch.object(subscribe, 'request') as request:
            request.return_value = {'subscriptions': []}
            code, data = call('--url', URL)
            assert code == 0 and data == {'target_found': False, 'changed': False}
            assert request.call_count == 1  # No POST unless --apply.
            request.reset_mock()
            request.side_effect = [{'subscriptions': []}, {'success': True},
                                   {'subscriptions': [{'url': URL}]}]
            code, data = call('--url', URL, '--apply')
            assert code == 0 and data == {'registration_success': True, 'target_found': True}
            body = request.call_args_list[1].args[2]
            assert body == {'url': URL, 'update_types': ['message_created', 'bot_started', 'message_callback'],
                            'secret': ENV['MAX_WEBHOOK_SECRET']}
            request.reset_mock()
            request.side_effect = None
            request.return_value = {'subscriptions': [{'url': 'https://other.example/webhook'}]}
            code, data = call('--url', URL, '--apply')
            assert code == 2 and data['changed'] is False and request.call_count == 1
            for malformed in ({}, {'subscriptions': None}, {'subscriptions': [None]}):
                request.return_value = malformed
                assert call('--url', URL, '--apply')[0] == 2
            request.side_effect = urllib.error.HTTPError(URL, 401, 'secret-token', {}, None)
            assert call('--url', URL)[1] == {'error': 'max_http_error', 'status': 401}
            request.side_effect = urllib.error.URLError('secret-token')
            code, data = call('--url', URL)
            assert code == 2 and 'secret-token' not in str(data)
            request.reset_mock()
            assert call('--url', 'http://example.org', '--apply')[0] == 2
            request.assert_not_called()
            assert subscribe.NoRedirect().redirect_request(None, None, None, None, None, None) is None


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX scoped CA, strict TLS/hostname checks, fail-closed bundle and secret-safe subscription')
