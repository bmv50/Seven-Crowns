"""Inspect or register MAX webhook without printing tokens or response contents."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.max_tls import max_ssl_context
from bot.max_transport import API_URL


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward Authorization to a redirect target.


def request(opener, token, body=None):
    req = urllib.request.Request(API_URL + '/subscriptions',
        data=None if body is None else json.dumps(body).encode('utf-8'),
        headers={'Authorization': token, 'Content-Type': 'application/json'},
        method='GET' if body is None else 'POST')
    with opener.open(req, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError('invalid_response')
    return data


def subscriptions(data):
    rows = data.get('subscriptions')
    if not isinstance(rows, list) or any(not isinstance(r, dict) or
            not isinstance(r.get('url'), str) for r in rows):
        raise ValueError('invalid_subscriptions')
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Public HTTPS webhook URL')
    parser.add_argument('--apply', action='store_true', help='Explicitly register/update this URL')
    args = parser.parse_args(argv)
    try:
        parsed = urlsplit(args.url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(c.isspace() for c in args.url)):
            raise ValueError('invalid_webhook_url')
        token = os.environ.get('MAX_BOT_TOKEN', '').strip()
        if not token:
            raise ValueError('missing_max_token')
        secret = os.environ.get('MAX_WEBHOOK_SECRET', '').strip()
        if args.apply and not re.fullmatch(r'[A-Za-z0-9_-]{5,256}', secret):
            raise ValueError('invalid_webhook_secret')
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=max_ssl_context()), NoRedirect())
        rows = subscriptions(request(opener, token))
        if any(row['url'] != args.url for row in rows):
            print(json.dumps({'other_subscription_found': True, 'changed': False}))
            return 2
        if not args.apply:
            print(json.dumps({'target_found': any(r['url'] == args.url for r in rows), 'changed': False}))
            return 0
        result = request(opener, token, {'url': args.url,
            'update_types': ['message_created', 'bot_started'], 'secret': secret})
        success = result.get('success') is True
        found = success and any(row['url'] == args.url for row in subscriptions(request(opener, token)))
        print(json.dumps({'registration_success': success, 'target_found': found}))
        return 0 if success and found else 2
    except urllib.error.HTTPError as e:
        print(json.dumps({'error': 'max_http_error', 'status': e.code}))
    except Exception as e:
        # Raw responses, URLs and exception messages may contain secrets.
        print(json.dumps({'error': 'max_subscription_check_failed', 'type': type(e).__name__}))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
