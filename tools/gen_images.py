# -*- coding: utf-8 -*-
"""
Батч-генерация иконок предметов и мобов через ComfyUI (DreamShaper XL).
Инструкция: docs/image_generation.md. Шаблон: docs/workflow_api.json.

КЛЮЧЕВОЕ: визуальные промпты пишутся на АНГЛИЙСКОМ через DeepSeek
(русские названия модель понимает плохо → одинаковые мобы и «женщина вместо
меча»). DeepSeek превращает «Железный меч / +8 атаки» в "a rusty iron longsword".
Промпты кэшируются в images/prompts.json (LLM зовётся один раз на сущность).
Для предметов жёстко запрещены люди в negative-промпте.

ЗАПУСК (на машине с ComfyUI; для качества — с ключом DeepSeek):
    set DEEPSEEK_API_KEY=sk-...        (или пропиши в .env)
    python gen_images.py              # все предметы и мобы
    python gen_images.py --only mobs  # только мобы
    python gen_images.py --force      # перегенерировать даже существующие png
    python gen_images.py --redescribe # заново описать (сбросить кэш промптов)

Без DEEPSEEK_API_KEY скрипт работает на запасных шаблонах (хуже качеством).
ComfyUI по умолчанию http://127.0.0.1:8188 (или COMFY_URL).
"""
import os
import sys
import json
import time
import hashlib
import urllib.request
import urllib.parse

import yaml

API = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WF_PATH = os.path.join(HERE, "docs", "workflow_api.json")
OUT = os.path.join(HERE, "images")
SEEDS_FILE = os.path.join(OUT, "image_seeds.json")
PROMPTS_FILE = os.path.join(OUT, "prompts.json")

# .env (если есть)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except Exception:
    pass

DEEPSEEK_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
DEEPSEEK_URL = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()

ICON_SIZE = 768
SAMPLER_NODE, POS_NODE, NEG_NODE, LATENT_NODE, SAVE_NODE = "3", "6", "7", "5", "9"

# ── рамки и негативы ──
ITEM_FRAMING = {
    "weapon":     "a single fantasy weapon, game item icon, only the weapon object",
    "armor":      "a single empty suit of fantasy armor, game item icon, armor object only, nobody wearing it",
    "accessory":  "a single small fantasy jewelry item (ring, amulet or talisman), game item icon",
    "consumable": "a single fantasy potion bottle or flask, game item icon",
    "material":   "a single fantasy crafting material or resource, game item icon",
    "quest":      "a single fantasy quest artifact object, game item icon",
}
ITEM_STYLE = ("centered on a plain dark background, fantasy RPG inventory art, "
              "painterly digital illustration, highly detailed, sharp focus")
ITEM_NEG = ("person, woman, man, human, character, face, portrait, full body figure, "
            "people, soldier, knight wearing armor, hands, photo, photorealistic, text, "
            "watermark, blurry, low quality, modern objects, cartoon, anime")

MOB_STYLE = ("full body, dynamic pose, fantasy game art, dark medieval style, "
             "painterly digital illustration, dramatic atmospheric lighting, highly detailed")
MOB_NEG = ("text, watermark, signature, ui, frame, border, photo, photorealistic, "
           "blurry, low quality, modern objects, cartoon, anime, multiple creatures")

prompts_cache = {}


# ───────── HTTP ─────────
def _get(path, timeout=15):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return r.read()


def _get_json(path, timeout=15):
    return json.loads(_get(path, timeout))


def _post_json(path, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_available():
    try:
        _get_json("/system_stats", timeout=8)
        return True
    except Exception as e:
        print(f"⚠️  ComfyUI недоступен по {API}: {e}")
        return False


# ───────── описание через DeepSeek ─────────
DESCRIBE_SYS = (
    "You write very short English prompts for an AI image generator, for a dark-fantasy RPG. "
    "Given a game entity (Russian name + optional description + type), reply with ONLY a concise "
    "English visual phrase (4-12 words) describing how it LOOKS — no stats, no story, no quotes, "
    "no extra words. For ITEMS describe ONLY the object itself, never a person or character. "
    "For MONSTERS describe the creature's appearance (a humanoid monster is fine, a normal human is not)."
)


def deepseek_describe(name, desc, kind, etype):
    if not DEEPSEEK_KEY:
        return None
    if kind == "item":
        u = f"ITEM. type={etype}. name={name}. description={desc or '-'}. Visual phrase:"
    else:
        u = f"MONSTER. name={name}. Visual phrase of the creature:"
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": DESCRIBE_SYS},
                     {"role": "user", "content": u}],
        "max_tokens": 40, "temperature": 0.7, "stream": False,
    }
    req = urllib.request.Request(DEEPSEEK_URL + "/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {DEEPSEEK_KEY}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        txt = (data["choices"][0]["message"]["content"] or "").strip().strip('"').strip()
        return txt or None
    except Exception as e:
        print(f"   (DeepSeek ошибка для {name}: {e})")
        return None


def fallback_phrase(name, kind, etype):
    if kind == "item":
        return {
            "weapon": "an ornate fantasy weapon",
            "armor": "a piece of fantasy armor",
            "accessory": "a fantasy magic amulet",
            "consumable": "a glowing fantasy potion bottle",
            "material": "a fantasy crafting material",
            "quest": "a mysterious fantasy artifact",
        }.get(etype, "a fantasy object")
    return "a fearsome fantasy monster creature"


def describe(eid, name, desc, kind, etype):
    if eid in prompts_cache and prompts_cache[eid]:
        return prompts_cache[eid]
    phrase = deepseek_describe(name, desc, kind, etype) or fallback_phrase(name, kind, etype)
    prompts_cache[eid] = phrase
    return phrase


# ───────── финальный промпт ComfyUI ─────────
def item_prompt(phrase, etype):
    framing = ITEM_FRAMING.get(etype, "a single fantasy object, game item icon")
    return f"{phrase}, {framing}, {ITEM_STYLE}"


def mob_prompt(phrase):
    return f"{phrase}, {MOB_STYLE}"


# ───────── генерация ─────────
def seed_for(eid):
    return int(hashlib.sha1(eid.encode("utf-8")).hexdigest()[:12], 16)


def wait_for_image(pid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hist = _get_json(f"/history/{pid}")
        if pid in hist:
            imgs = hist[pid].get("outputs", {}).get(SAVE_NODE, {}).get("images", [])
            if imgs:
                return imgs[0]
        time.sleep(1)
    return None


def fetch_image(img):
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    return _get(f"/view?{q}", timeout=60)


def generate(base_wf_text, eid, positive, negative, out_dir, seeds, force):
    out_path = os.path.join(out_dir, eid + ".png")
    if os.path.exists(out_path) and not force:
        return "skip"
    wf = json.loads(base_wf_text)
    seed = seed_for(eid)
    wf[POS_NODE]["inputs"]["text"] = positive
    wf[NEG_NODE]["inputs"]["text"] = negative
    s = wf[SAMPLER_NODE]["inputs"]
    s["seed"] = seed; s["steps"] = 7; s["cfg"] = 2
    s["sampler_name"] = "dpmpp_sde"; s["scheduler"] = "karras"
    wf[LATENT_NODE]["inputs"]["width"] = ICON_SIZE
    wf[LATENT_NODE]["inputs"]["height"] = ICON_SIZE
    wf[SAVE_NODE]["inputs"]["filename_prefix"] = eid
    pid = _post_json("/prompt", {"prompt": wf})["prompt_id"]
    img = wait_for_image(pid)
    if not img:
        return "fail"
    with open(out_path, "wb") as f:
        f.write(fetch_image(img))
    seeds[eid] = seed
    return "ok"


def load_yaml(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save(obj, path):
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def run_batch(kind, entities, base_wf_text, seeds, force):
    out_dir = os.path.join(OUT, kind)
    os.makedirs(out_dir, exist_ok=True)
    total = len(entities)
    ok = skip = fail = 0
    for i, (eid, meta) in enumerate(entities.items(), 1):
        try:
            name = meta.get("name", eid)
            if kind == "items":
                phrase = describe(eid, name, meta.get("desc"), "item", meta.get("type", ""))
                pos = item_prompt(phrase, meta.get("type", ""))
                neg = ITEM_NEG
            else:
                phrase = describe(eid, name, None, "mob", None)
                pos = mob_prompt(phrase)
                neg = MOB_NEG
            res = generate(base_wf_text, eid, pos, neg, out_dir, seeds, force)
        except KeyboardInterrupt:
            _save(prompts_cache, PROMPTS_FILE); _save(seeds, SEEDS_FILE)
            print("\n⏹  Прервано. Прогресс сохранён — запусти снова, докачает.")
            raise
        except Exception as e:
            res = "fail"; print(f"   ! {eid}: {e}")
        ok += res == "ok"; skip += res == "skip"; fail += res == "fail"
        print(f"[{kind} {i}/{total}] {eid}: {res}  «{prompts_cache.get(eid,'')}»")
        if i % 8 == 0:
            _save(prompts_cache, PROMPTS_FILE); _save(seeds, SEEDS_FILE)
    print(f"== {kind}: готово {ok}, пропущено {skip}, ошибок {fail} ==")


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    force = "--force" in sys.argv
    redescribe = "--redescribe" in sys.argv

    if not os.path.exists(WF_PATH):
        print(f"❌ Нет шаблона: {WF_PATH}"); sys.exit(1)
    if not api_available():
        print("⛔ Запусти ComfyUI (--listen 0.0.0.0) и повтори: python gen_images.py")
        sys.exit(1)
    if not DEEPSEEK_KEY:
        print("⚠️  DEEPSEEK_API_KEY не задан — промпты будут на запасных шаблонах "
              "(качество ниже). Лучше задать ключ для английских описаний.")

    global prompts_cache
    if os.path.exists(PROMPTS_FILE) and not redescribe:
        prompts_cache = json.load(open(PROMPTS_FILE, encoding="utf-8"))
    base_wf_text = open(WF_PATH, "r", encoding="utf-8").read()
    seeds = json.load(open(SEEDS_FILE, encoding="utf-8")) if os.path.exists(SEEDS_FILE) else {}

    if only in (None, "items"):
        items = load_yaml("items.yaml")
        print(f"🖼  Предметов: {len(items)}")
        run_batch("items", items, base_wf_text, seeds, force)
    if only in (None, "mobs"):
        mobs = load_yaml("mobs.yaml")
        print(f"🖼  Мобов: {len(mobs)}")
        run_batch("mobs", mobs, base_wf_text, seeds, force)

    _save(prompts_cache, PROMPTS_FILE); _save(seeds, SEEDS_FILE)
    print(f"✅ Готово. Иконки в {OUT}/items и {OUT}/mobs; промпты в {PROMPTS_FILE}")


if __name__ == "__main__":
    main()
