# -*- coding: utf-8 -*-
"""
Генерация иконок предметов и мобов через OpenAI Images API.
Модели: gpt-image-1 (по умолчанию) или dall-e-3.

НОВОЕ: мобам подставляется ФОН по их локации (зона из world.yaml), а качество
разведено по типу: мобы — medium (детальные существа в сцене), предметы — low
(чистые иконки на тёмном фоне). Картинки идут в images/items|mobs (бот читает их).

ЗАПУСК:
    set OPENAI_API_KEY=sk-...
    python gen_images_openai.py --only mobs --limit 8 --force   # проба мобов
    python gen_images_openai.py --force                          # всё (190)

Переменные:
    OPENAI_IMAGE_MODEL=gpt-image-1 | dall-e-3
    OPENAI_IMAGE_QUALITY_MOBS=medium      (low|medium|high)
    OPENAI_IMAGE_QUALITY_ITEMS=low
    OPENAI_IMAGE_QUALITY=...   — общий override для обоих
    OPENAI_IMAGE_SIZE=1024x1024
Стоимость gpt-image-1 1024: low ~$0.011, medium ~$0.04, high ~$0.17 за шт.
По умолчанию (мобы medium ~$2.5 + предметы low ~$1.4) ≈ $4 за все 190.
Если gpt-image-1 требует верификацию — set OPENAI_IMAGE_MODEL=dall-e-3.
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

API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
BASE = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
MODEL = (os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()
SIZE = (os.environ.get("OPENAI_IMAGE_SIZE") or "1024x1024").strip()
DELAY = float(os.environ.get("OPENAI_DELAY", "1"))
Q_OVERRIDE = (os.environ.get("OPENAI_IMAGE_QUALITY") or "").strip()
Q_MOBS = Q_OVERRIDE or (os.environ.get("OPENAI_IMAGE_QUALITY_MOBS") or "medium").strip()
Q_ITEMS = Q_OVERRIDE or (os.environ.get("OPENAI_IMAGE_QUALITY_ITEMS") or "low").strip()
Q_ROOMS = Q_OVERRIDE or (os.environ.get("OPENAI_IMAGE_QUALITY_ROOMS") or "medium").strip()
SIZE_ROOMS = (os.environ.get("OPENAI_IMAGE_SIZE_ROOMS") or "1024x1024").strip()

QUALITY_WORDS = ("highly detailed, intricate, sharp focus, dramatic cinematic lighting, "
                 "concept art, masterpiece, high quality")

ITEM_KIND = {
    "weapon": "weapon (sword, axe, mace, bow or staff)",
    "armor": "piece of armor (an empty armor set, nobody wearing it)",
    "accessory": "small piece of jewelry (ring, amulet or talisman)",
    "consumable": "potion bottle or flask",
    "material": "crafting material or resource",
    "quest": "mysterious quest artifact",
}

# фон по зоне (русское имя zone из world.yaml -> английская сцена)
ZONE_BG = {
    "Туманный Брод": "a foggy ramshackle medieval village square at dusk",
    "Подземелья": "dark damp catacombs with old bones and dripping stone",
    "Шепчущий лес": "a misty ancient whispering pine forest",
    "Рудники": "a dark abandoned mine tunnel with ore veins and broken timber",
    "Гнилотопь": "a fetid rotting swamp with black water and dead trees",
    "Руины Эха": "crumbling ancient temple ruins shrouded in grey mist",
    "Железный Острог": "a grim fortified mining town of iron and dark stone",
    "Стылая Гавань": "a cold misty harbor with black water and ship masts",
    "Чертоги Рассвета": "a sunlit mountain monastery of golden stone",
    "Перевал Стонущих Ветров": "a windswept snowy mountain pass with black peaks",
    "Пепельные Пустоши": "a grey ashen wasteland with thick fog and burnt bones",
    "Затонувший Город": "a flooded sunken black-stone city under dark water",
    "Гномий Чертог": "an underground dwarven hall lit by glowing crystals",
    "Лунный Предел": "a silver moonlit elven grove among great trees",
    "Кровавый Кряж": "a savage orc war-camp of wooden palisades above a swamp",
    "Гоблинская Нора": "a cramped torchlit goblin tunnel market underground",
}
DEFAULT_BG = "a dark atmospheric fantasy environment in fog"


def build_mob_zone():
    """mob_id -> zone (первая локация, где встречается моб)."""
    world = load_yaml("world.yaml")
    mz = {}
    for rid, room in world.items():
        zone = room.get("zone", "")
        for mob in room.get("spawns", []):
            mz.setdefault(mob, zone)
    return mz


def item_prompt(meta):
    kind = ITEM_KIND.get(meta.get("type", ""), "fantasy object")
    return (
        f"A single dark-fantasy RPG game item icon: {meta.get('name','')} — a {kind}. "
        f"{meta.get('desc','')} "
        "Show ONLY the object itself, centered on a dark vignette background, like an "
        f"inventory icon. {QUALITY_WORDS}. "
        "Absolutely no people, no characters, no human figures, no faces, no text."
    )


def mob_prompt(meta, zone):
    bg = ZONE_BG.get(zone or "", DEFAULT_BG)
    return (
        f"A dark-fantasy RPG monster: {meta.get('name','')}, full body, dynamic menacing pose, "
        f"in the foreground. Background scene: {bg}. "
        f"Dark medieval painterly digital game art. {QUALITY_WORDS}. "
        "A single creature only, no text, no UI, no watermark."
    )


def room_prompt(meta):
    zone = meta.get("zone", "")
    bg = ZONE_BG.get(zone, DEFAULT_BG)
    desc = (meta.get("desc", "") or "").strip().replace("\n", " ")
    return (
        f"A dark-fantasy RPG location background art: {meta.get('name','')}. {desc} "
        f"Setting: {bg}. Wide atmospheric establishing shot of the environment, empty scene. "
        f"Painterly digital game art, moody, detailed, {QUALITY_WORDS}. "
        "No people, no characters, no creatures, no text, no UI, no watermark."
    )


def gen_image(prompt, quality, size=None):
    body = {"model": MODEL, "prompt": prompt, "size": size or SIZE, "n": 1}
    if MODEL == "gpt-image-1":
        body["quality"] = quality
    else:
        body["response_format"] = "b64_json"
        if MODEL == "dall-e-3":
            body["quality"] = "hd" if quality in ("medium", "high") else "standard"
    req = urllib.request.Request(
        BASE + "/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    data = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            if e.code == 429 and attempt < 4:
                wait = 15 * (attempt + 1)
                print(f"      429, жду {wait}с... {detail[:100]}")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}")
    d0 = data["data"][0]
    if d0.get("b64_json"):
        return base64.b64decode(d0["b64_json"])
    if d0.get("url"):
        with urllib.request.urlopen(d0["url"], timeout=120) as r:
            return r.read()
    raise RuntimeError("нет картинки в ответе")


def load_yaml(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_batch(kind, entities, force, limit, quality, mob_zone=None, size=None):
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
            if kind == "items":
                prompt = item_prompt(meta)
            elif kind == "rooms":
                prompt = room_prompt(meta)
            else:
                prompt = mob_prompt(meta, (mob_zone or {}).get(eid, ""))
            png = gen_image(prompt, quality, size)
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
        print("⛔ Нет ключа. Задай: set OPENAI_API_KEY=sk-...  (или впиши в .env)")
        sys.exit(1)
    print(f"🤖 OpenAI {MODEL}, размер={SIZE} | мобы={Q_MOBS}, предметы={Q_ITEMS}")

    if only in (None, "mobs"):
        mobs = load_yaml("mobs.yaml")
        mz = build_mob_zone()
        print(f"🖼  Мобов: {len(mobs)} (фон по локации)")
        run_batch("mobs", mobs, force, limit, Q_MOBS, mz)
    if only in (None, "items"):
        items = load_yaml("items.yaml")
        print(f"🖼  Предметов: {len(items)}")
        run_batch("items", items, force, limit, Q_ITEMS)
    if only == "rooms":
        rooms = load_yaml("world.yaml")
        print(f"🖼  Комнат: {len(rooms)} (фон по зоне, качество={Q_ROOMS}, размер={SIZE_ROOMS})")
        run_batch("rooms", rooms, force, limit, Q_ROOMS, size=SIZE_ROOMS)

    print(f"✅ Готово. Картинки в {OUT}/items, {OUT}/mobs, {OUT}/rooms")


if __name__ == "__main__":
    main()
