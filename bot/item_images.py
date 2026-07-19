# -*- coding: utf-8 -*-
"""Карточка-картинка предмета с цветовым решением по редкости.
Один базовый AI-рисунок предмета (images/items/<base>.png, опц.) + динамическая
цветная подложка/рамка под редкость. Если базового рисунка нет — рисуется
стилизованный плейсхолдер. Результат кэшируется в images/items_cache/."""
import os
from engine import rarity, equip
from engine.content import ITEMS

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL = True
except Exception:
    _PIL = False

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(_ROOT, "images")
CACHE = os.path.join(IMAGES, "items_cache")

# цвет фона/рамки по редкости (RGB) + затемнённый низ градиента
RARITY_RGB = {
    "common": (90, 90, 96),
    "green":  (38, 160, 64),
    "blue":   (40, 110, 220),
    "purple": (150, 70, 210),
    "gold":   (220, 170, 40),
    "red":    (210, 50, 50),
}
_FONTS = [
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size):
    for p in _FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _mix(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def card_image(key: str) -> str:
    """Вернуть путь к PNG-карточке предмета нужной редкости (с кэшем) или None."""
    if not _PIL:
        return None
    base = rarity.base_of(key)
    rar = rarity.rarity_of(key)
    if base not in ITEMS:
        return None
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{base}__{rar}_v2.png")
    art0 = os.path.join(IMAGES, "items", base + ".png")
    if os.path.exists(out):
        # пересобрать, если появился/обновился базовый AI-рисунок
        if not os.path.exists(art0) or os.path.getmtime(art0) <= os.path.getmtime(out):
            return out

    W = H = 512
    col = RARITY_RGB.get(rar, RARITY_RGB["common"])
    dark = _mix(col, (12, 12, 16), 0.78)
    img = Image.new("RGB", (W, H), dark)
    d = ImageDraw.Draw(img)
    # вертикальный градиент цвет редкости -> тёмный
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix(_mix(col, (20, 20, 26), 0.55), dark, y / H))
    # рамка редкости
    for i, w in enumerate(range(14, 0, -2)):
        d.rounded_rectangle([i, i, W - 1 - i, H - 1 - i], radius=26,
                            outline=_mix(col, (255, 255, 255), 0.15), width=2)
    # внутренняя панель (оставляем место под подпись внизу)
    d.rounded_rectangle([46, 46, W - 46, H - 156], radius=20, fill=_mix(dark, (0, 0, 0), 0.25))

    # базовый рисунок предмета или плейсхолдер
    art = os.path.join(IMAGES, "items", base + ".png")
    placed = False
    if os.path.exists(art):
        try:
            it = Image.open(art).convert("RGBA")
            it.thumbnail((300, 300))
            img.paste(it, ((W - it.width) // 2, 70 + (300 - it.height) // 2), it)
            placed = True
        except Exception:
            placed = False
    if not placed:
        # стилизованный ромб-самоцвет в цвете редкости
        cx, cy, r = W // 2, 210, 90
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                  fill=_mix(col, (255, 255, 255), 0.25), outline=(255, 255, 255))
        d.polygon([(cx, cy - r), (cx + r // 2, cy - r // 3), (cx - r // 2, cy - r // 3)],
                  fill=_mix(col, (255, 255, 255), 0.5))

    meta = ITEMS[key]
    name = meta.get("name", base)
    nm = name.split(" ", 1)[1] if name[:1] in "⚪🟢🔵🟣🟡🔴" else name
    fn = _font(32); fr = _font(23); fs = _font(21)
    nm_lines = _wrap(d, nm, fn, W - 60)[:2]
    rar_label = rarity.META[rar]["name"]
    b = meta.get("bonus", {})
    stat = (f"Атака {b['atk']}" if b.get("atk") else
            f"Защита {b['defense']}" if b.get("defense") else "")
    lr = equip.level_req(meta)
    foot = stat + ("  ·  " if stat else "") + f"ур.{lr}+"
    # стопка от низа: имя (1-2 стр) -> редкость -> статы
    lh = 34
    block_h = len(nm_lines) * lh + 28 + 26
    y = H - block_h - 8
    for line in nm_lines:
        tw = d.textlength(line, font=fn)
        d.text(((W - tw) / 2, y), line, font=fn, fill=(255, 255, 255))
        y += lh
    tw = d.textlength(rar_label, font=fr)
    d.text(((W - tw) / 2, y), rar_label, font=fr, fill=_mix(col, (255, 255, 255), 0.5))
    y += 28
    tw = d.textlength(foot, font=fs)
    d.text(((W - tw) / 2, y), foot, font=fs, fill=(225, 225, 230))

    img.save(out)
    return out
