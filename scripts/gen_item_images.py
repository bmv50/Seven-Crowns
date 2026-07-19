# -*- coding: utf-8 -*-
"""
Генерация картинок предметов через ComfyUI (Flux) по workflow_final.json.
Запускать НА МАШИНЕ С ComfyUI (по умолчанию http://127.0.0.1:8188).

Картинки сохраняются в images/items/<base>.png (имя = ключ предмета).
Дальше бот сам накладывает на них цветную рамку редкости (bot/item_images.py).

Примеры:
  python scripts/gen_item_images.py                  # named-предметы (items.yaml) + руны
  python scripts/gen_item_images.py --type weapon    # только оружие
  python scripts/gen_item_images.py --only-missing    # пропустить уже существующие
  python scripts/gen_item_images.py --include-gen     # ещё и генерёный каталог (g_*)
  python scripts/gen_item_images.py --limit 5         # первые 5 (для теста)
  python scripts/gen_item_images.py --host 127.0.0.1:8188
"""
import os, sys, json, time, random, argparse, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.content import ITEMS  # noqa: E402

OUT_DIR = os.path.join(ROOT, "images", "items")
WORKFLOW = os.path.join(ROOT, "workflow_final.json")

TYPE_RU = {"weapon": "фэнтезийное оружие", "armor": "фэнтезийный доспех",
           "accessory": "магическое украшение/амулет", "consumable": "зелье/расходник",
           "rune": "магическая руна, светящийся гравированный камень",
           "material": "ремесленный материал"}

STYLE = ("один предмет строго по центру, тёмный нейтральный градиентный фон, "
         "студийный свет, детализированный игровой концепт-арт, чистая иконка, "
         "высокое качество")


# руна = гранёный цветной кристалл-самоцвет, внутри светится символ навыка.
# (цвет кристалла, символ-икона навыка). Альтернатива иконе — первая буква параметра.
RUNE_LOOK = {
    "str":  ("рубиново-красного", "сжатый кулак"),
    "dex":  ("изумрудно-зелёного", "летящая стрела"),
    "int":  ("сапфирово-синего", "раскрытая книга с искрами"),
    "spi":  ("аметистово-фиолетового", "язык пламени / капля души"),
    "def":  ("золотисто-янтарного", "рыцарский щит"),
    "crit": ("прозрачно-алмазного", "скрещённые мечи"),
}
RUNE_NEG = ("чистый гранёный самоцвет, НЕ камень, без мха, без растительности, "
            "без лиан, без трещин, без грязи")


def _rune_prompt(key: str, name: str) -> str:
    parts = key.split("_")          # rune_<stat>_<tier>
    stat = parts[1] if len(parts) > 1 else "str"
    tier = parts[2] if len(parts) > 2 else "lesser"
    color, icon = RUNE_LOOK.get(stat, ("золотистого", "древний символ"))
    if tier == "greater":
        intensity = ("крупный многогранный кристалл, очень яркое внутреннее свечение, "
                     "ореол магической энергии вокруг")
    else:
        intensity = "небольшой гладкий гранёный кристалл, мягкое внутреннее свечение"
    return (f"магическая руна — отдельный гранёный {color} кристалл-самоцвет в форме "
            f"рунического камня, в центре светится выгравированный символ: {icon}; "
            f"{intensity}; глянцевые полированные грани, прозрачный самоцвет; "
            f"{RUNE_NEG}. {STYLE}")


def build_prompt(key: str) -> str:
    meta = ITEMS.get(key, {})
    name = meta.get("name", key)
    if meta.get("type") == "rune":
        return _rune_prompt(key, name)
    kind = TYPE_RU.get(meta.get("type"), "фэнтезийный предмет")
    desc = (meta.get("desc") or "").split(".")[0]
    return f"{name} — {kind}. {desc}. {STYLE}"


def pick_items(args):
    keys = []
    for k, v in ITEMS.items():
        if "#" in k:                      # пропустить динамические редкости
            continue
        if not args.include_gen and k.startswith("g_"):
            continue
        t = v.get("type")
        if args.type and t != args.type:
            continue
        if not args.type and t not in ("weapon", "armor", "accessory", "consumable", "rune"):
            continue
        keys.append(k)
    keys.sort()
    if args.only_missing:
        keys = [k for k in keys if not os.path.exists(os.path.join(OUT_DIR, k + ".png"))]
    if args.limit:
        keys = keys[:args.limit]
    return keys


def comfy_post(host: str, path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"http://{host}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def comfy_get(host: str, path: str) -> bytes:
    with urllib.request.urlopen(f"http://{host}{path}", timeout=60) as r:
        return r.read()


def generate_one(host: str, base_wf: dict, key: str, size: int) -> bool:
    wf = json.loads(json.dumps(base_wf))             # копия
    wf["7"]["inputs"]["text"] = build_prompt(key)    # промпт
    wf["11"]["inputs"]["noise_seed"] = random.randint(1, 2**50)
    wf["8"]["inputs"]["width"] = size
    wf["8"]["inputs"]["height"] = size
    wf["15"]["inputs"]["filename_prefix"] = f"item_{key}"
    resp = comfy_post(host, "/prompt", {"prompt": wf})
    pid = resp.get("prompt_id")
    if not pid:
        print(f"  ! {key}: нет prompt_id ({resp})"); return False
    # ждать готовности
    for _ in range(180):                              # до ~3 мин на картинку
        time.sleep(1)
        try:
            hist = json.loads(comfy_get(host, f"/history/{pid}"))
        except Exception:
            continue
        if pid in hist:
            outs = hist[pid].get("outputs", {}).get("15", {}).get("images", [])
            if not outs:
                print(f"  ! {key}: нет выходных изображений"); return False
            img = outs[0]
            q = urllib.parse.urlencode({"filename": img["filename"],
                                        "subfolder": img.get("subfolder", ""),
                                        "type": img.get("type", "output")})
            data = comfy_get(host, f"/view?{q}")
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, key + ".png"), "wb") as f:
                f.write(data)
            return True
    print(f"  ! {key}: таймаут ожидания"); return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1:8188")
    ap.add_argument("--type", choices=list(TYPE_RU))
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--include-gen", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--size", type=int, default=1024)
    args = ap.parse_args()

    if not os.path.exists(WORKFLOW):
        print("Не найден workflow_final.json в корне проекта."); return
    base_wf = json.load(open(WORKFLOW, encoding="utf-8"))
    keys = pick_items(args)
    print(f"К генерации: {len(keys)} предметов через ComfyUI @ {args.host}")
    ok = fail = 0
    for i, key in enumerate(keys, 1):
        print(f"[{i}/{len(keys)}] {key} — {ITEMS[key].get('name', key)}")
        try:
            if generate_one(args.host, base_wf, key, args.size):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  ! {key}: ошибка {e}")
    print(f"\nГотово: ✅ {ok}, ❌ {fail}. Картинки в images/items/.")
    print("Бот сам наложит цветную рамку редкости при показе.")


if __name__ == "__main__":
    main()
