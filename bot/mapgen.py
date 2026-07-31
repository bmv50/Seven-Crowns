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


_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _find_img(folder, stem):
    """Путь к картинке по имени без расширения. Перебор нужен потому, что арты
    после scripts/optimize_images.py лежат в JPEG, а раньше искались только
    .png — из-за этого в ячейках карты пропали изображения комнат."""
    for ext in _IMG_EXT:
        p = os.path.join(folder, stem + ext)
        if os.path.exists(p):
            return p
    return None


def _backdrop(W, H, zone=None):
    """Фон карты: images/maps/<зона>.jpg, иначе общий images/map_bg.*,
    иначе тёмный градиент. Зональный фон даёт каждой местности своё лицо —
    пустоши не выглядят как лес, — не требуя ручной расстановки комнат."""
    maps_dir = os.path.join(os.path.dirname(ROOMS_IMG), "maps")
    p = _find_img(maps_dir, str(zone)) if zone else None
    if not p:
        p = _find_img(os.path.dirname(ROOMS_IMG), "map_bg")
    if p:
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
    """Картинка самой комнаты для заливки прямоугольника (если есть).

    Кадрируем по центру, а не растягиваем: арт комнаты широкий (1280×704),
    ячейка — почти вдвое площе, и простой resize сплющивал бы башни и деревья.
    """
    p = _find_img(ROOMS_IMG, rid)
    if not p:
        return None
    try:
        im = Image.open(p).convert("RGB")
        # вписываем по ширине, обрезаем лишнее по высоте от центра
        k = CELL_W / float(im.width)
        nh = max(CELL_H, int(im.height * k))
        im = im.resize((CELL_W, nh), Image.LANCZOS)
        top = max(0, (nh - CELL_H) // 2)
        im = im.crop((0, top, CELL_W, top + CELL_H))
        return ImageEnhance.Brightness(im).enhance(0.5)
    except Exception:
        return None


def _free(taken, x, y, step=None):
    """Свободная клетка для комнаты, НЕ ломающая направление перехода.

    Раньше при занятой клетке искалась любая свободная по спирали — включая
    клетку с противоположной стороны. Из-за этого комната, лежащая к востоку,
    могла нарисоваться севернее, и игрок, шагнув на восток, «оказывался на
    севере» (отчёт беты, п.9). Теперь сначала уходим ДАЛЬШЕ по тому же
    направлению, потом пробуем сдвиг вбок — и никогда назад.

    step — (dx, dy) хода, по которому пришли. None (нет направления) —
    поведение как раньше, по спирали.
    """
    if (x, y) not in taken:
        return x, y
    if step:
        sx, sy = step
        px, py = (0, 1) if sx else (1, 0)      # ось поперёк хода
        ox, oy = x - sx, y - sy                # откуда шли
        # Перебираем клетки, у которых смещение ПО направлению строго больше
        # бокового: тогда «восток» и на глаз остаётся востоком, а не северо-
        # востоком. a — сколько ушли по направлению, b — вбок.
        for a in range(1, 12):
            for b in range(0, a):              # |b| < a — направление доминирует
                for s in ((1,) if b == 0 else (1, -1)):
                    c = (ox + sx * a + px * b * s, oy + sy * a + py * b * s)
                    if c not in taken:
                        return c
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
            # направление передаём в _free: при коллизии оно должно сохраниться
            nx, ny = _free(used, x + dx, y + dy, step=(dx, dy))
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
    img = _backdrop(W, H, zone)
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

    # Рёбра между показанными комнатами. Линию рисуем ТОЛЬКО если она идёт в ту
    # сторону, куда ведёт выход: в Пепельных Пустошах связи образуют кольцо,
    # которое на плоскую сетку не ложится ни при какой раскладке, и одна из
    # линий неизбежно пошла бы поперёк смысла — игрок видел бы «на восток»,
    # а стрелка тянулась на север (отчёт беты, п.9). Честнее не нарисовать
    # ребро совсем: комнаты всё равно подписаны, а переход виден в комнате.
    drawn = set()
    for rid in coords:
        for _dd, dest in WORLD[rid].get("exits", {}).items():
            if _dd in _VERT or dest not in coords or (dest, rid) in drawn:
                continue
            dx, dy = _DIRS.get(_dd, (0, 0))
            x0, y0 = coords[rid]
            x1, y1 = coords[dest]
            prim = (x1 - x0) * dx + (y1 - y0) * dy      # ушли по направлению
            perp = abs((y1 - y0) if dx else (x1 - x0))  # снесло вбок
            if prim <= 0 or perp >= prim:
                continue
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
