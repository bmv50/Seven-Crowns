"""Allowlisted MAX art and validated, reconstructable map snapshots."""
from pathlib import Path

from . import content, max_onboarding

ROOT = Path(__file__).resolve().parent.parent / 'images' / 'room_previews'
# The owner approved the four pilots. Every current room now has a new MAX
# illustration; the original Telegram artwork remains untouched.
PREVIEW_ROOMS = frozenset(content.WORLD)


def asset_path(key):
    if isinstance(key, str) and key.startswith('mob:'):
        mob = key[4:]
        if mob not in content.MOBS:
            raise ValueError('Unknown MAX mob illustration')
        return ROOT.parent / 'mobs' / (mob+'.jpg')
    if isinstance(key, str) and key.startswith('item:'):
        from .item_art import asset_path as item_path
        return item_path(key)
    if isinstance(key, str) and key.startswith('map:'):
        from .max_map import asset_path as map_path
        return map_path(key)
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
