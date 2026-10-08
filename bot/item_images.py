# -*- coding: utf-8 -*-
"""Shared Telegram/MAX item cards: transparent AI sprites on rarity backgrounds."""
import os
import re

from engine import item_art

try:
    from PIL import ImageFont
    _PIL = True
except ImportError:
    _PIL = False

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(_ROOT, 'images')
CACHE = str(item_art.ROOT)
RARITY_RGB = item_art.COLORS
_ART_EXT = ('.webp', '.png', '.jpg', '.jpeg')
_TIER_SUFFIX = re.compile(r'_\d+$')
_FONTS = ['C:\\Windows\\Fonts\\arialbd.ttf',
          '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
          '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']


def art_file(base):
    if base not in item_art.BASES:
        return None
    names = (base, _TIER_SUFFIX.sub('', base) if base.startswith('g_') else base)
    for folder in ('items_v2', 'items'):
        for name in names:
            for extension in _ART_EXT:
                path = os.path.join(IMAGES, folder, name+extension)
                if os.path.isfile(path):
                    return path
    return None


def _font(size):
    for path in _FONTS:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _mix(first, second, amount):
    return tuple(int(a+(b-a)*amount) for a, b in zip(first, second))


def _wrap(draw, text, font, width):
    lines, current = [], ''
    for word in text.split():
        candidate = (current+' '+word).strip()
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def card_image(key):
    """Rendered JPEG; snapshot includes seed, so affix stats never leak across cards."""
    if not _PIL or not item_art.valid_item(key):
        return None
    return str(item_art.render_asset(item_art.image_key(key)))
