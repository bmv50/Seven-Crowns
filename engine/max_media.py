"""Allowlisted bundled MAX art: onboarding and four location-style previews."""
from pathlib import Path

from . import content, max_onboarding

ROOT = Path(__file__).resolve().parent.parent / 'images' / 'room_previews'
# Intentionally bounded pilot. Existing Telegram art is not overwritten or
# implicitly enabled: the owner will approve the new location style first.
PREVIEW_ROOMS = frozenset(('village', 'market', 'temple', 'cellar'))


def asset_path(key):
    if isinstance(key, str) and key.startswith('room:'):
        room = key[5:]
        if room not in PREVIEW_ROOMS or room not in content.WORLD:
            raise ValueError('Unknown MAX room illustration')
        return ROOT / f'{room}.jpg'
    return max_onboarding.asset_path(key)


def room_asset(room, *, enabled=True):
    if not enabled or room not in PREVIEW_ROOMS:
        return None
    key = f'room:{room}'
    # A missing optional preview must not prevent entering/seeing the room.
    return key if asset_path(key).is_file() else None
