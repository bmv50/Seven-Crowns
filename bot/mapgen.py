# -*- coding: utf-8 -*-
"""
Карта окрестностей игрока (PNG, Pillow). Игроцентрична: комната игрока в центре,
вокруг — соседние комнаты (в т.ч. из других зон, подгружаются на несколько шагов).
У граничных комнат — стрелки с названием следующей локации («дальше сюда»).
Фон — затемнённое изображение текущей комнаты (если есть), иначе тёмная сетка.
"""
import os
import tempfile

try:
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

from engine.content import WORLD
import engine.npc as npclib

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOMS_IMG = os.path.join(os.path.dirname(_HERE), "images", "rooms")

_DIRS = {"север": (0, -1), "юг": (0, 1), "восток": (1, 0), "запад": (-1, 0)}
_VERT = {"вверх", "вниз"}

_FONT_PATHS = [
    "C:\\Windows\\Fonts\\arialbd.ttf", "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_BG = (24, 26, 32)
_GRID = (40, 44, 54)
_EDGE = (120, 130, 150)
_ROOM = (52, 58, 72)
_ROOM_BORDER = (96, 104, 124)
_CITY = (46, 66, 98)
_PLAYER = (120, 92, 30)
_PLAYER_BORDER = (240, 195, 75)
_TEXT = (232, 236, 244)
_SUB = (150, 205, 165)
_TITLE = (240, 205, 115)
_ARROW = (240, 205, 115)

CELL_W, CELL_H = 196, 88
GAP_X, GAP_Y = 70, 74          # широкие промежутки под граничные стрелки
MARGIN = 64
TOP = 116
MAX_STEPS = 2                  # на сколько шагов подгружать соседей
CAP = 28                       # максимум комнат на карте


def _font(size):
    for pth in _FONT_PATHS:
        if os.path.exists(pth):
            try:
                return ImageFont.truetype(pth, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _backdrop(W, H):
    """Единый нейтрально-фэнтезийный фон карты (images/map_bg.png или градиент)."""
    p = os.path.join(os.path.dirname(ROOMS_IMG), "map_bg.png")
    if os.path.exists(p):
        try:
            return ImageEnhance.Brightness(
                Image.open(p).convert("RGB").resize((W, H))).enhance(0.6)
        except Exception:
            pass
    top, bot = (36, 33, 52), (17, 18, 27)
    col = Image.new("RGB", (1, H))
    for yy in range(H):
        t = yy / max(1, H - 1)
        col.putpixel((0, yy), (int(top[0] + (bot[0] - top[0]) * t),
                               int(top[1] + (bot[1] - top[1]) * t),
                               int(top[2] + (bot[2] - top[2]) * t)))
    return col.resize((W, H))


def _box_img(rid):
    """Картинка самой комнаты для заливки прямоугольника (если есть)."""
    p = os.path.join(ROOMS_IMG, rid + ".png")
    if os.path.exists(p):
        try:
            im = Image.open(p).convert("RGB").resize((CELL_W, CELL_H))
            return ImageEnhance.Brightness(im).enhance(0.5)
        except Exception:
            return None
    return None


def _free(taken, x, y):
    if (x, y) not in taken:
        return x, y
    for r in range(1, 14):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) == r and (x + dx, y + dy) not in taken:
                    return x + dx, y + dy
    return x, y


def _layout_local(start):
    """BFS на MAX_STEPS шагов во ВСЕ стороны (любые зоны)."""
    coords = {start: (0, 0)}
    used = {(0, 0)}
    depth = {start: 0}
    queue = [start]
    while queue:
        rid = queue.pop(0)
        if depth[rid] >= MAX_STEPS or len(coords) >= CAP:
            continue
        for d, dest in WORLD.get(rid, {}).get("exits", {}).items():
            if dest in coords or dest not in WORLD or d not in _DIRS:
                continue
            dx, dy = _DIRS[d]
            x, y = coords[rid]
            nx, ny = _free(used, x + dx, y + dy)
            coords[dest] = (nx, ny)
            used.add((nx, ny))
            depth[dest] = depth[rid] + 1
            queue.append(dest)
            if len(coords) >= CAP:
                break
    return coords


def _room_roles(room):
    tags = []
    for n in room.get("npc", []):
        role = (npclib.get(n) or {}).get("role")
        lbl = {"trainer": "Учитель", "vendor": "Торговец", "questgiver": "Задания",
               "priest": "Жрец", "guard": "Стража", "mentor": "Наставник",
               "banker": "Банк", "innkeeper": "Трактир", "arena_master": "Арена",
               "guild_master": "Гильдия", "faction_leader": "Глава"}.get(role)
        if lbl and lbl not in tags:
            tags.append(lbl)
    return tags


def _fit(draw, text, font, max_w):
    """Обрезать строку под ширину с многоточием."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _wrap(draw, text, font, max_w, max_lines=2):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return [_fit(draw, ln, font, max_w) for ln in lines[:max_lines]]


def render_zone_map(ch) -> str:
    if not _HAS_PIL:
        return None
    cur = WORLD[ch.room]
    zone = cur.get("zone", "?")
    coords = _layout_local(ch.room)
    px0, py0 = coords[ch.room]

    dxs = [x - px0 for x, _ in coords.values()]
    dys = [y - py0 for _, y in coords.values()]
    rad_x = max(1, max(abs(min(dxs)), abs(max(dxs))))
    rad_y = max(1, max(abs(min(dys)), abs(max(dys))))
    cols = 2 * rad_x + 1
    rows = 2 * rad_y + 1
    W = MARGIN * 2 + cols * CELL_W + (cols - 1) * GAP_X
    H = TOP + MARGIN + rows * CELL_H + (rows - 1) * GAP_Y

    # единый нейтральный фон карты
    img = _backdrop(W, H)
    d = ImageDraw.Draw(img)
    f_title = _font(32)
    f_name = _font(19)
    f_sub = _font(15)
    f_you = _font(14)
    f_arr = _font(16)

    d.text((MARGIN, 24), "Карта: " + zone, font=f_title, fill=_TITLE)

    def box_xy(rid):
        x, y = coords[rid]
        cc = (x - px0) + rad_x
        rr = (y - py0) + rad_y
        return (MARGIN + cc * (CELL_W + GAP_X), TOP + rr * (CELL_H + GAP_Y))

    def cen(rid):
        x, y = box_xy(rid)
        return x + CELL_W // 2, y + CELL_H // 2

    # рёбра между показанными комнатами
    drawn = set()
    for rid in coords:
        for _dd, dest in WORLD[rid].get("exits", {}).items():
            if _dd in _VERT:
                continue
            if dest in coords and (dest, rid) not in drawn:
                d.line([cen(rid), cen(dest)], fill=_EDGE, width=4)
                drawn.add((rid, dest))

    # граничные стрелки: выходы в НЕпоказанные комнаты
    for rid in coords:
        bx, by = box_xy(rid)
        cx, cy = bx + CELL_W // 2, by + CELL_H // 2
        for d2, dest in WORLD[rid].get("exits", {}).items():
            if dest in coords or dest not in WORLD:
                continue
            rn = WORLD[dest]["name"]
            if d2 == "север":
                ax, ay = cx, by - 10
                d.polygon([(ax, ay - 18), (ax - 12, ay), (ax + 12, ay)], fill=_ARROW)
                d.text((ax, ay - 34), _fit(d, rn, f_arr, GAP_X + 80),
                       font=f_arr, fill=_ARROW, anchor="ma")
            elif d2 == "юг":
                ax, ay = cx, by + CELL_H + 10
                d.polygon([(ax, ay + 18), (ax - 12, ay), (ax + 12, ay)], fill=_ARROW)
                d.text((ax, ay + 22), _fit(d, rn, f_arr, GAP_X + 80),
                       font=f_arr, fill=_ARROW, anchor="ma")
            elif d2 == "восток":
                ax, ay = bx + CELL_W + 10, cy
                d.polygon([(ax + 18, ay), (ax, ay - 12), (ax, ay + 12)], fill=_ARROW)
                d.text((ax + 4, ay - 20), _fit(d, rn, f_arr, max(24, W - ax - 12)),
                       font=f_arr, fill=_ARROW, anchor="la")
            elif d2 == "запад":
                ax, ay = bx - 10, cy
                d.polygon([(ax - 18, ay), (ax, ay - 12), (ax, ay + 12)], fill=_ARROW)
                d.text((ax - 4, ay - 20), _fit(d, rn, f_arr, max(24, ax - 26)),
                       font=f_arr, fill=_ARROW, anchor="ra")
            elif d2 in _VERT:
                continue

    # вертикальные выходы (вверх/вниз) — диагональными стрелками у угла комнаты
    for rid in coords:
        bx, by = box_xy(rid)
        for d2, dest in WORLD[rid].get("exits", {}).items():
            if d2 not in _VERT:
                continue
            rn = WORLD.get(dest, {}).get("name", dest)
            if d2 == "вверх":
                tx, ty = bx + CELL_W + 14, by - 14
                d.line([(bx + CELL_W - 6, by + 6), (tx, ty)], fill=_ARROW, width=5)
                d.polygon([(tx + 4, ty - 4), (tx - 14, ty + 2), (tx - 2, ty + 14)], fill=_ARROW)
                d.text((tx + 8, ty - 4), _fit(d, rn, f_sub, max(40, W - tx - 16)),
                       font=f_sub, fill=_ARROW, anchor="lb")
            else:
                tx, ty = bx + CELL_W + 14, by + CELL_H + 14
                d.line([(bx + CELL_W - 6, by + CELL_H - 6), (tx, ty)], fill=_ARROW, width=5)
                d.polygon([(tx + 4, ty + 4), (tx - 14, ty - 2), (tx - 2, ty - 14)], fill=_ARROW)
                d.text((tx + 8, ty + 4), _fit(d, rn, f_sub, max(40, W - tx - 16)),
                       font=f_sub, fill=_ARROW, anchor="lt")

    # комнаты
    for rid in coords:
        x, y = box_xy(rid)
        room = WORLD[rid]
        is_player = (rid == ch.room)
        fill = _PLAYER if is_player else (_CITY if room.get("npc") else _ROOM)
        border = _PLAYER_BORDER if is_player else _ROOM_BORDER
        bgim = _box_img(rid)
        if bgim is not None:
            mask = Image.new("L", (CELL_W, CELL_H), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, CELL_W, CELL_H], radius=13, fill=255)
            img.paste(bgim, (x, y), mask)
            d.rounded_rectangle([x, y, x + CELL_W, y + CELL_H], radius=13,
                                outline=border, width=4 if is_player else 2)
        else:
            d.rounded_rectangle([x, y, x + CELL_W, y + CELL_H], radius=13,
                                fill=fill, outline=border, width=4 if is_player else 2)
        if is_player:
            d.text((x + CELL_W // 2, y - 19), "● ВЫ ЗДЕСЬ", font=f_you,
                   fill=_PLAYER_BORDER, anchor="ma")
        ty = y + 9
        for ln in _wrap(d, room.get("name", rid), f_name, CELL_W - 20, 2):
            d.text((x + 11, ty), ln, font=f_name, fill=_TEXT)
            ty += 21
        tags = _room_roles(room)
        if tags:
            d.text((x + 11, y + CELL_H - 22),
                   _fit(d, " · ".join(tags), f_sub, CELL_W - 20), font=f_sub, fill=_SUB)

    out = os.path.join(tempfile.gettempdir(), f"map_{ch.uid}.png")
    img.save(out)
    return out
