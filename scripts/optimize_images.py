# -*- coding: utf-8 -*-
"""
Оптимизация артов под Telegram: PNG 1–1,5 МБ → JPEG ~150–250 КБ.

Зачем. ComfyUI отдаёт PNG 1024² / 1216×704 весом больше мегабайта. Telegram
всё равно пережимает фото в JPEG и показывает шириной ~800–1280 px, поэтому
три четверти веса выбрасываются впустую: медленнее отправка, тяжелее образ
Docker, репозиторий на сотни мегабайт. После прогона вся папка images/
ужимается примерно в 8 раз без заметной на телефоне разницы.

Что делает:
  • images/rooms/*.png  → JPEG, ширина ≤ 1280 (широкий кадр локации)
  • images/mobs/*.png   → JPEG, сторона ≤ 1024 (портрет существа)
  • images/items/*.png  → JPEG, сторона ≤ 768  (иконка, её ещё уменьшает бот)
  • исходные PNG удаляет (--keep-png — оставить), кэш рамок редкости чистит
    (images/items_cache пересоберётся сам при первом показе предмета).

Бот ищет и .jpg, и .png (см. bot/item_images.py и send_entity_photo), поэтому
смена расширения безопасна.

Запуск (из корня проекта):
    py scripts/optimize_images.py              # прогнать всё
    py scripts/optimize_images.py --dry-run    # только посчитать выигрыш
    py scripts/optimize_images.py --quality 88 # качество JPEG (по умолчанию 85)
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "images")

# папка → максимальная сторона (px). Локации широкие, поэтому у них лимит по
# ширине больше: в чате Telegram они разворачиваются на всю ширину экрана.
TARGETS = {
    "rooms": 1280,
    "mobs": 1024,
    "items": 768,
}
CACHE_DIR = os.path.join(IMAGES, "items_cache")


def _human(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} ГБ"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", type=int, default=85, help="качество JPEG (80–92 разумно)")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что будет")
    ap.add_argument("--keep-png", action="store_true", help="не удалять исходные PNG")
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        print("Нужна библиотека Pillow:  py -m pip install Pillow")
        sys.exit(1)

    total_before = total_after = 0
    converted = skipped = 0

    for sub, max_side in TARGETS.items():
        d = os.path.join(IMAGES, sub)
        if not os.path.isdir(d):
            print(f"— {sub}: папки нет, пропуск")
            continue
        pngs = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
        print(f"\n{sub}: {len(pngs)} PNG, целевая сторона ≤ {max_side}px")
        for i, fname in enumerate(pngs, 1):
            src = os.path.join(d, fname)
            dst = os.path.splitext(src)[0] + ".jpg"
            before = os.path.getsize(src)
            total_before += before
            if args.dry_run:
                # оценка: JPEG q85 после ресайза ≈ 12–18% от исходного PNG
                total_after += int(before * 0.15)
                converted += 1
                continue
            try:
                with Image.open(src) as im:
                    im = im.convert("RGB")          # снимаем альфу: JPEG её не хранит
                    w, h = im.size
                    if max(w, h) > max_side:
                        k = max_side / float(max(w, h))
                        im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
                    im.save(dst, "JPEG", quality=args.quality, optimize=True,
                            progressive=True)
                after = os.path.getsize(dst)
                total_after += after
                converted += 1
                if not args.keep_png:
                    os.remove(src)
                if i % 20 == 0 or i == len(pngs):
                    print(f"  [{i}/{len(pngs)}] …")
            except Exception as e:                  # noqa: BLE001
                skipped += 1
                print(f"  ! {fname}: {e}")

    # кэш карточек предметов (рамки редкости) пересобирается автоматически
    if os.path.isdir(CACHE_DIR) and not args.dry_run:
        n = 0
        for f in os.listdir(CACHE_DIR):
            try:
                os.remove(os.path.join(CACHE_DIR, f)); n += 1
            except OSError:
                pass
        if n:
            print(f"\nОчищен кэш карточек предметов: {n} файлов (пересоберётся сам)")

    print("\n" + "═" * 46)
    if args.dry_run:
        print(f"ОЦЕНКА: {converted} файлов, {_human(total_before)} → "
              f"~{_human(total_after)} (прогон без изменений)")
    else:
        saved = total_before - total_after
        pct = (saved / total_before * 100) if total_before else 0
        print(f"Готово: {converted} файлов, ошибок {skipped}")
        print(f"Было {_human(total_before)} → стало {_human(total_after)} "
              f"(экономия {_human(saved)}, {pct:.0f}%)")
    print("═" * 46)


if __name__ == "__main__":
    main()
