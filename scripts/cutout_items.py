# -*- coding: utf-8 -*-
"""
Вырезать фон у спрайтов предметов: images/items/*.jpg|png → *.webp с альфой.

Зачем. Бот рисует карточку предмета сам: цветная подложка и рамка по редкости,
а сверху — AI-спрайт. Спрайт приходит из ComfyUI со СВОИМ фоном (серый градиент),
и на подложке он виден отчётливым прямоугольником — предмет будто в рамке внутри
рамки (отчёт тестировщика беты, п.16). JPEG прозрачности не хранит, поэтому
фон надо снять и сохранить формат с альфой.

Почему WebP, а не PNG. Альфа есть у обоих, но PNG со сложным объектом весит
300–500 КБ, и папка предметов раздулась бы до 70+ МБ — больше, чем сейчас весь
арт игры. WebP с альфой даёт те же 60–90 КБ. Бот ищет .webp наравне с .jpg и
.png (bot/item_images.art_file), а в Telegram уходит уже собранная карточка PNG,
так что формат исходника снаружи не виден.

Как снимается фон. Заливкой ОТ КРАЁВ: прозрачным становится только то, что
связано с границей кадра и близко к ней по цвету. Простое «убрать все серые
пиксели» дырявило бы стальные клинки и каменные амулеты. Край маски слегка
размывается, чтобы предмет не выглядел вырезанным ножницами.

Запуск (из корня проекта):
    py scripts/cutout_items.py                # все предметы
    py scripts/cutout_items.py --dry-run      # только показать, что будет
    py scripts/cutout_items.py --ids волчья_шкура,шёлк
    py scripts/cutout_items.py --tolerance 40 # мягче/строже отбор фона
    py scripts/cutout_items.py --keep-src     # не удалять исходные jpg/png
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITEMS_DIR = os.path.join(ROOT, "images", "items")
CACHE_DIR = os.path.join(ROOT, "images", "items_cache")
SRC_EXT = (".jpg", ".jpeg", ".png")

TOLERANCE = 32      # насколько цвет может отличаться от фонового и всё ещё быть фоном
FEATHER = 1.2       # радиус размытия края маски, px

# Признаки неудачного вырезания. Отсев «по доле кадра» пришлось убрать: посох
# бездны — тонкий стержень и честно занимает 3% кадра, а прежний порог в 4%
# принимал корректный результат за брак. Настоящая неудача выглядит иначе:
#   • от предмета почти ничего не осталось — фон совпал с ним по цвету и
#     заливка проела объект насквозь;
#   • не удалено почти ничего — фон не распознан, толку от файла нет.
MIN_KEEP = 0.005    # меньше половины процента — объект съеден
MAX_KEEP = 0.97     # больше 97% непрозрачно — фон не найден


def _bg_color(im):
    """Опорный цвет фона — медиана рамки в 3 px по краю кадра."""
    w, h = im.size
    px = im.load()
    edge = []
    step = max(1, w // 120)
    for x in range(0, w, step):
        for y in (0, 1, 2, h - 3, h - 2, h - 1):
            edge.append(px[x, y])
    for y in range(0, h, step):
        for x in (0, 1, 2, w - 3, w - 2, w - 1):
            edge.append(px[x, y])
    if not edge:
        return (0, 0, 0)
    return tuple(sorted(c[i] for c in edge)[len(edge) // 2] for i in range(3))


def _bg_mask(im, bg, tol):
    """Маска фона: 255 там, где фон, связанный с краем кадра. Заливка волной."""
    w, h = im.size
    px = im.load()
    br, bg_, bb = bg
    close = bytearray(w * h)          # 1 — пиксель похож на фон
    for y in range(h):
        row = y * w
        for x in range(w):
            r, g, b = px[x, y][:3]
            if abs(r - br) <= tol and abs(g - bg_) <= tol and abs(b - bb) <= tol:
                close[row + x] = 1

    seen = bytearray(w * h)
    stack = []
    for x in range(w):
        for y in (0, h - 1):
            i = y * w + x
            if close[i] and not seen[i]:
                seen[i] = 1; stack.append(i)
    for y in range(h):
        for x in (0, w - 1):
            i = y * w + x
            if close[i] and not seen[i]:
                seen[i] = 1; stack.append(i)

    while stack:
        i = stack.pop()
        x, y = i % w, i // w
        if x > 0:
            j = i - 1
            if close[j] and not seen[j]: seen[j] = 1; stack.append(j)
        if x < w - 1:
            j = i + 1
            if close[j] and not seen[j]: seen[j] = 1; stack.append(j)
        if y > 0:
            j = i - w
            if close[j] and not seen[j]: seen[j] = 1; stack.append(j)
        if y < h - 1:
            j = i + w
            if close[j] and not seen[j]: seen[j] = 1; stack.append(j)
    return seen


MAX_SIDE = 768      # больше не нужно: в карточке спрайт всё равно ужимается до 300 px


def cutout(path, tol=TOLERANCE, max_side=MAX_SIDE):
    """-> (RGBA-изображение, доля непрозрачных пикселей) либо (None, 0.0)."""
    from PIL import Image, ImageFilter
    with Image.open(path) as src:
        im = src.convert("RGB")
    # Уменьшаем ДО заливки: на 1024² чистый Python обходил бы миллион пикселей
    # заметно дольше, а качество карточки от этого не выигрывает.
    if max(im.size) > max_side:
        k = max_side / float(max(im.size))
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    w, h = im.size
    seen = _bg_mask(im, _bg_color(im), tol)
    alpha = Image.frombytes("L", (w, h), bytes(255 if not v else 0 for v in seen))
    kept = sum(1 for v in seen if not v) / float(w * h)
    alpha = alpha.filter(ImageFilter.GaussianBlur(FEATHER))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    return out, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="точечно: ключи через запятую")
    ap.add_argument("--tolerance", type=int, default=TOLERANCE)
    ap.add_argument("--quality", type=int, default=88)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-src", action="store_true", help="не удалять исходные jpg/png")
    args = ap.parse_args()

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Нужна Pillow:  py -m pip install Pillow"); sys.exit(1)

    if not os.path.isdir(ITEMS_DIR):
        print(f"Нет папки {ITEMS_DIR}"); sys.exit(1)

    want = {s.strip() for s in args.ids.split(",") if s.strip()}
    files = [f for f in sorted(os.listdir(ITEMS_DIR))
             if f.lower().endswith(SRC_EXT)
             and (not want or os.path.splitext(f)[0] in want)]
    if not files:
        print("Нечего обрабатывать (возможно, всё уже в .webp)."); return

    print(f"К обработке: {len(files)} спрайтов, допуск цвета {args.tolerance}")
    ok = skipped = failed = 0
    before = after = 0
    for i, fname in enumerate(files, 1):
        src = os.path.join(ITEMS_DIR, fname)
        dst = os.path.splitext(src)[0] + ".webp"
        before += os.path.getsize(src)
        if args.dry_run:
            ok += 1
            continue
        try:
            img, kept = cutout(src, args.tolerance)
            if kept < MIN_KEEP:
                print(f"  ~ {fname}: объект съеден заливкой ({kept:.2%}) — пропуск. "
                      f"Попробуйте --tolerance {max(6, args.tolerance // 2)}")
                skipped += 1
                continue
            if kept > MAX_KEEP:
                print(f"  ~ {fname}: фон не распознан ({kept:.0%} непрозрачно) — пропуск. "
                      f"Попробуйте --tolerance {args.tolerance * 2}")
                skipped += 1
                continue
            img.save(dst, "WEBP", quality=args.quality, method=6)
            after += os.path.getsize(dst)
            ok += 1
            if not args.keep_src and os.path.abspath(dst) != os.path.abspath(src):
                os.remove(src)
        except Exception as e:                      # noqa: BLE001
            failed += 1
            print(f"  ! {fname}: {e}")
        if i % 25 == 0 or i == len(files):
            print(f"  [{i}/{len(files)}] …")

    # карточки собираются из спрайтов — старый кэш теперь неверен
    if not args.dry_run and os.path.isdir(CACHE_DIR):
        n = 0
        for f in os.listdir(CACHE_DIR):
            try:
                os.remove(os.path.join(CACHE_DIR, f)); n += 1
            except OSError:
                pass
        if n:
            print(f"\nОчищен кэш карточек: {n} файлов (пересоберётся сам)")

    print("\n" + "═" * 46)
    if args.dry_run:
        print(f"ОЦЕНКА: обработали бы {ok} файлов (прогон без изменений)")
    else:
        print(f"Готово: ✅ {ok}, пропущено {skipped}, ошибок {failed}")
        if after:
            print(f"Вес: {before/1048576:.1f} МБ → {after/1048576:.1f} МБ")
    print("═" * 46)


if __name__ == "__main__":
    main()
