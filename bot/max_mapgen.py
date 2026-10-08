"""Phone-readable one-hop schematic. Geometry is not a claim of world geography."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from engine.content import WORLD
from bot.mapgen import _font, _wrap, _fit

POSITIONS = {'север': (540, 210), 'юг': (540, 650), 'восток': (910, 430),
             'запад': (170, 430), 'вверх': (170, 210), 'вниз': (910, 650)}
LABELS = {'север': 'СЕВЕР', 'юг': 'ЮГ', 'восток': 'ВОСТОК',
          'запад': 'ЗАПАД', 'вверх': 'ВВЕРХ · другой этаж', 'вниз': 'ВНИЗ · другой этаж'}
ROOM_ART = Path(__file__).resolve().parents[1] / 'images' / 'room_previews'


def terrain(room, size, brightness=0.40):
    """Use the actual location artwork, not an invented geographical map."""
    if room not in WORLD:
        return None
    path = ROOM_ART / (room+'.jpg')
    try:
        with Image.open(path) as source:
            fitted = ImageOps.fit(source.convert('RGB'), size, method=Image.Resampling.LANCZOS)
        return ImageEnhance.Brightness(fitted).enhance(brightness)
    except (OSError, ValueError):
        return None


def render(room, route):
    im = terrain(room, (1080, 980))
    if im is None:
        im = Image.new('RGB', (1080, 980), '#111827')
    draw = ImageDraw.Draw(im)
    white, muted, gold, green = '#edf2fa', '#adb9ce', '#ffd479', '#71e2b1'
    title, name, small = _font(32), _font(23), _font(18)
    draw.rounded_rectangle((15, 10, 1065, 112), radius=18, fill='#111827')
    draw.text((34, 25), 'КАРТА ОКРЕСТНОСТЕЙ', font=title, fill=gold)
    draw.text((34, 73), _fit(draw, WORLD[room].get('zone', 'Мир'), name, 990), font=name, fill=muted)
    center = (540, 430)
    exits = WORLD[room]['exits']
    next_room = route[1] if len(route) > 1 else None
    target = route[-1] if route else None
    for direction, dest in exits.items():
        if direction not in POSITIONS:
            continue
        pos = POSITIONS[direction]
        dx, dy = pos[0]-center[0], pos[1]-center[1]
        length = math.hypot(dx, dy)
        # Clip to node rectangles, not circles: arrowheads stay visible outside
        # horizontal boxes instead of disappearing underneath their fill.
        boundary = min(135/abs(dx) if dx else float('inf'),
                       69/abs(dy) if dy else float('inf'))
        gap = 10/length
        start = (center[0]+dx*(boundary+gap), center[1]+dy*(boundary+gap))
        end = (pos[0]-dx*(boundary+gap), pos[1]-dy*(boundary+gap))
        color = green if dest == next_room else '#a4b5cc'
        draw.line([start, end], fill=color, width=7 if dest == next_room else 4)
        ux, uy = dx/length, dy/length
        draw.polygon([end, (end[0]-ux*16-uy*9, end[1]-uy*16+ux*9),
                      (end[0]-ux*16+uy*9, end[1]-uy*16-ux*9)], fill=color)

    def node(pos, rid, heading, highlight=None):
        x, y = pos
        border = highlight or '#879bb8'
        tile = terrain(rid, (270, 138), brightness=0.30)
        if tile is not None:
            mask = Image.new('L', (270, 138), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 269, 137), radius=20, fill=255)
            im.paste(tile, (x-135, y-69), mask)
            draw.rounded_rectangle((x-135, y-69, x+135, y+69), radius=20, outline=border, width=4)
        else:
            draw.rounded_rectangle((x-135, y-69, x+135, y+69), radius=20,
                                   fill='#253147', outline=border, width=4)
        draw.text((x-123, y-56), heading, font=small, fill=border if highlight else muted)
        lines = _wrap(draw, WORLD[rid]['name'], name, 246, max_lines=3)
        for i, line in enumerate(lines):
            draw.text((x-123, y-23+i*27), line, font=name, fill=white)
        if rid == target:
            draw.ellipse((x+108, y-57, x+125, y-40), fill=green)

    for direction, dest in exits.items():
        if direction in POSITIONS:
            node(POSITIONS[direction], dest, LABELS[direction], green if dest == next_room else None)
    node(center, room, 'ВЫ ЗДЕСЬ', gold)
    draw.rounded_rectangle((34, 785, 1046, 935), radius=20, fill='#1e2a3d', outline='#53637b')
    if target:
        draw.text((54, 801), 'ЦЕЛЬ: '+_fit(draw, WORLD[target]['name'], name, 910), font=name, fill=green)
        text = 'Цель в текущей локации' if len(route) == 1 else f'Переходов до цели: {len(route)-1}. Зелёная стрелка — следующий шаг.'
    else:
        draw.text((54, 801), 'СВОБОДНОЕ ИССЛЕДОВАНИЕ', font=name, fill=gold)
        text = 'Выберите задание под картой, чтобы увидеть маршрут.'
    draw.text((54, 844), text, font=small, fill=white)
    draw.text((54, 882), 'Схема реальных выходов. Вверх / вниз — переход между этажами.', font=small, fill=muted)
    draw.rectangle((0, 941, 1080, 980), fill='#111827')
    draw.text((34, 950), 'Жёлтая рамка — герой. Зелёная — следующий шаг. Одна кнопка = один переход.', font=small, fill=muted)
    return im
