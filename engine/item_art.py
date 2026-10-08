"""Validated public item-art snapshots and rarity-colored UI cards.

AI creates transparent sprites; this renderer only lays out the native game card.
Item bonuses and seeded affixes come from shared content, not from image prompts.
"""
import base64
import hashlib
from pathlib import Path
import re
import tempfile

from . import content, rarity, equip

BASES = frozenset(k for k in content.ITEMS if '#' not in k)
ROOT = Path(tempfile.gettempdir()) / 'seven-crowns-item-cards'
VERSION = 'rarity-v3'
COLORS = {'common': (255, 255, 255), 'green': (32, 143, 66),
          'blue': (36, 100, 200), 'purple': (123, 56, 174),
          'gold': (183, 127, 22), 'red': (181, 42, 47)}


def valid_item(key):
    if not isinstance(key, str) or len(key) > 180:
        return False
    parts = key.split('#')
    if parts[0] not in BASES or len(parts) > 3:
        return False
    if len(parts) > 1 and parts[1] not in set(rarity.META)-{'common'}:
        return False
    if len(parts) == 3 and (not re.fullmatch(r'[0-9]{1,10}', parts[2]) or not 1 <= int(parts[2]) <= 10**9):
        return False
    return True


def image_key(key):
    if not valid_item(key):
        raise ValueError('Unknown item image')
    return 'item:'+base64.urlsafe_b64encode(key.encode()).decode().rstrip('=')


def snapshot(asset):
    if not isinstance(asset, str) or not asset.startswith('item:') or len(asset) > 1024:
        raise ValueError('Invalid item image snapshot')
    encoded = asset[5:]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded):
        raise ValueError('Invalid item image snapshot')
    try:
        key = base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)).decode()
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Invalid item image snapshot') from exc
    if not valid_item(key):
        raise ValueError('Unknown item image')
    return key


def asset_path(asset):
    snapshot(asset)
    return ROOT / (VERSION+'-'+hashlib.sha256(asset.encode()).hexdigest()+'.jpg')


def render_asset(asset):
    from PIL import Image, ImageDraw
    from bot.item_images import art_file, _font, _wrap, _mix
    key = snapshot(asset)
    base, rar, _ = rarity.split(key)
    art = art_file(base)
    output = asset_path(asset)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.is_file() and (not art or Path(art).stat().st_mtime <= output.stat().st_mtime):
        return output
    size = 768
    color = COLORS[rar]
    common = rar == 'common'
    image = Image.new('RGB', (size, size), color)
    draw = ImageDraw.Draw(image)
    if not common:
        for y in range(size):
            draw.line((0, y, size, y), fill=_mix(color, (12, 16, 26), 0.10+y/size*0.14))
    border = (190, 197, 206) if common else _mix(color, (255, 255, 255), 0.50)
    draw.rounded_rectangle((10, 10, 757, 757), radius=30, outline=border, width=4)
    if art:
        with Image.open(art) as source:
            sprite = source.convert('RGBA')
            sprite.thumbnail((520, 495), Image.Resampling.LANCZOS)
            image.paste(sprite, ((size-sprite.width)//2, 28+(495-sprite.height)//2), sprite)
    else:
        draw.text((80, 210), 'Изображение недоступно', font=_font(36), fill=(35, 44, 61) if common else 'white')
    meta = content.ITEMS[key]
    # Base title stays clean; rarity and seeded stats are explicit below it.
    name = content.ITEMS[base]['name']
    foreground = (27, 38, 56) if common else (248, 249, 255)
    font, small = _font(36), _font(27)
    lines = _wrap(draw, name, font, 700)[:2]
    y = 535
    for line in lines:
        draw.text(((size-draw.textlength(line, font=font))/2, y), line, font=font, fill=foreground)
        y += 43
    label = rarity.META[rar]['name']
    draw.text(((size-draw.textlength(label, font=small))/2, 637), label, font=small, fill=foreground)
    bonus = meta.get('bonus', {})
    stat = ('Атака '+str(bonus['atk'])) if bonus.get('atk') else ('Защита '+str(bonus['defense'])) if bonus.get('defense') else ''
    footer = stat+(' · ' if stat else '')+f'Уровень {equip.level_req(meta)}+'
    draw.text(((size-draw.textlength(footer, font=small))/2, 690), footer, font=small, fill=foreground)
    with tempfile.NamedTemporaryFile(dir=ROOT, suffix='.jpg', delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        image.save(temporary, 'JPEG', quality=92, optimize=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    files = sorted(ROOT.glob(VERSION+'-*.jpg'), key=lambda p: p.stat().st_mtime)
    for old in files[:-512]:
        if old != output:
            old.unlink(missing_ok=True)
    return output
