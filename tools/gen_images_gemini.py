# -*- coding: utf-8 -*-
"""
Генерация иконок предметов и мобов через Google Gemini «Nano Banana»
(модель gemini-2.5-flash-image). Без ComfyUI и без локальной GPU.

НУЖЕН API-КЛЮЧ Gemini из Google AI Studio (aistudio.google.com → Get API key).
Подписка Gemini Pro в приложении — это НЕ API; нужен именно ключ.

ЗАПУСК:
    set GEMINI_API_KEY=AIza...        (или пропиши в .env: GEMINI_API_KEY=...)
    python gen_images_gemini.py                 # все предметы и мобы
    python gen_images_gemini.py --only mobs      # только мобы (проба)
    python gen_images_gemini.py --force          # перезаписать существующие
    python gen_images_gemini.py --limit 8        # сгенерить только первые 8 (тест)

Особенности: последовательно (бережём лимиты), докачка (готовые пропускает),
русские названия Gemini понимает сам — отдельное описание не нужно.
"""
import os
import sys
import json
import time
import base64
import urllib.request
import urllib.error

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "images")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except Exception:
    pass

API_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
MODEL = (os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image").strip()
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
DELAY = float(os.environ.get("GEMINI_DELAY", "2"))   # пауза между запросами, сек

ITEM_KIND = {
    "weapon": "weapon (sword, axe, mace, bow or staff)",
    "armor": "piece of armor (an empty armor set, nobody wearing it)",
    "accessory": "small piece of jewelry (ring, amulet or talisman)",
    "consumable": "potion bottle or flask",
    "material": "crafting material or resource",
    "quest": "mysterious quest artifact",
}


def item_prompt(meta):
    etype = meta.get("type", "")
    kind = ITEM_KIND.get(etype, "fantasy object")
    desc = meta.get("desc", "")
    return (
        f"A single dark-fantasy RPG game item icon: {meta.get('name','')} — a {kind}. "
        f"{desc} "
        "Show ONLY the object itself, centered on a plain dark background. "
        "Painterly digital game art, detailed, dramatic lighting. "
        "Absolutely no people, no characters, no human figures, no faces, no text."
    )


def mob_prompt(meta):
    return (
        f"A dark-fantasy RPG monster illustration: {meta.get('name','')}. "
        "Full body creature, dynamic menacing pose, centered, atmospheric foggy background. "
        "Painterly digital game art, dark medieval style, detailed, dramatic lighting. "
        "A single creature only, no text, no UI, no watermark."
    )


def gen_image(prompt):
    """Вернуть bytes PNG или None."""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    req = urllib.request.Request(
        ENDPOINT + f"?key={API_KEY}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    data = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                wait = 15 * (attempt + 1)
                print(f"      429 (квота/лимит), жду {wait}с и повторяю...")
                time.sleep(wait)
                continue
            raise
    if data is None:
        raise RuntimeError("нет ответа после ретраев")
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    # ничего не нашли — покажем кусок ответа для диагностики
    raise RuntimeError("в ответе нет картинки: " + json.dumps(data)[:300])


def load_yaml(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_batch(kind, entities, prompt_fn, force, limit):
    out_dir = os.path.join(OUT, kind)
    os.makedirs(out_dir, exist_ok=True)
    total = len(entities)
    ok = skip = fail = 0
    done = 0
    for i, (eid, meta) in enumerate(entities.items(), 1):
        out_path = os.path.join(out_dir, eid + ".png")
        if os.path.exists(out_path) and not force:
            skip += 1
            print(f"[{kind} {i}/{total}] {eid}: skip")
            continue
        if limit and done >= limit:
            break
        try:
            png = gen_image(prompt_fn(meta))
            with open(out_path, "wb") as f:
                f.write(png)
            ok += 1
            print(f"[{kind} {i}/{total}] {eid}: ok")
        except KeyboardInterrupt:
            print("\n⏹  Прервано — запусти снова, докачает.")
            raise
        except Exception as e:
            fail += 1
            print(f"[{kind} {i}/{total}] {eid}: FAIL — {e}")
        done += 1
        time.sleep(DELAY)
    print(f"== {kind}: готово {ok}, пропущено {skip}, ошибок {fail} ==")


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    force = "--force" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0

    if not API_KEY:
        print("⛔ Нет ключа. Получи в https://aistudio.google.com → Get API key и задай:\n"
              "   set GEMINI_API_KEY=AIza...   (или впиши в .env)")
        sys.exit(1)
    print(f"🍌 Модель: {MODEL}")

    if only in (None, "items"):
        items = load_yaml("items.yaml")
        print(f"🖼  Предметов: {len(items)}")
        run_batch("items", items, item_prompt, force, limit)
    if only in (None, "mobs"):
        mobs = load_yaml("mobs.yaml")
        print(f"🖼  Мобов: {len(mobs)}")
        run_batch("mobs", mobs, mob_prompt, force, limit)

    print(f"✅ Готово. Иконки в {OUT}/items и {OUT}/mobs")


if __name__ == "__main__":
    main()
