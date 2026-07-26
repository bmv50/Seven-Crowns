# -*- coding: utf-8 -*-
"""
Генерация артов МОБОВ и ЛОКАЦИЙ через ComfyUI (Flux) по workflow_final.json.
Тот же пайплайн, что scripts/gen_item_images.py — запускать НА МАШИНЕ С ComfyUI.

Сохраняет:
  мобы    → images/mobs/<mob_id>.png   (квадрат, портрет существа)
  локации → images/rooms/<room_id>.png (ландшафт, атмосферная сцена)

Примеры:
  python scripts/gen_world_images.py --type mob              # все мобы
  python scripts/gen_world_images.py --type room            # все рукотворные локации
  python scripts/gen_world_images.py --type room --include-wild   # + процедурные дикие
  python scripts/gen_world_images.py --type mob --only-missing
  python scripts/gen_world_images.py --type room --limit 5  # тест на 5
  python scripts/gen_world_images.py --type room --ids inn_room,temple  # перегенерить конкретные
  python scripts/gen_world_images.py --host 127.0.0.1:8188
"""
import os, sys, json, time, random, argparse, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.content import MOBS, WORLD, NPCS, RACES  # noqa: E402

WORKFLOW = os.path.join(ROOT, "workflow_final.json")
MOBS_DIR = os.path.join(ROOT, "images", "mobs")
ROOMS_DIR = os.path.join(ROOT, "images", "rooms")

STYLE_COMMON = ("тёмное фэнтези, кинематографичный свет, детализированный игровой "
                "концепт-арт, цифровая живопись, высокое качество, "
                # Flux 2 — дистиллированная модель, negative prompt не работает,
                # поэтому запреты формулируем прямо в позитивном промпте: иначе
                # она охотно дорисовывает подписи, рамки и элементы интерфейса.
                "без текста, без надписей и подписей, без водяных знаков, "
                "без рамок и интерфейса, без коллажа — цельный кадр")

# Ограничения кадра идут ПЕРВЫМИ (ранние токены весят больше, и Flux слушает
# начало промпта заметно охотнее, чем хвост). Плейтест владельца: модель
# дорисовывала подписи с именем и свиту из лишних персонажей.
MOB_CONSTRAINTS = ("В кадре ровно одна фигура — одно существо целиком, "
                   "крупным планом по центру, изолировано на простом тёмном фоне. "
                   "Без текста, надписей, подписей и имён на изображении. "
                   "Никаких других персонажей, людей и силуэтов рядом, "
                   "ни на переднем, ни на заднем плане")
# мобов рисуем без людей-моделей, чтобы не подменяло существо человеком
MOB_STYLE = ("портрет существа в полный рост, выразительная поза, "
             "глубина резкости, размытый тёмный фон, " + STYLE_COMMON)
ROOM_STYLE = ("атмосферная сцена локации, широкий план, глубина, кинематографичная "
              "композиция, " + STYLE_COMMON)


# освещение по типу локации (по ключевым словам зоны/комнаты/биома)
_LIGHT = [
    (("подземель", "пещер", "штольн", "рудник", "шахт", "катаком", "склеп", "грот",
      "cellar", "deep", "abyss", "mine", "warren", "нор", "тоннел", "туннел", "бездн"),
     "очень тёмное освещение, кромешный мрак с редкими факелами и свечами, глубокие "
     "тени, холодные блики на влажном камне"),
    (("болот", "гнилотоп", "топ", "swamp", "трясин", "мочаг"),
     "мутный зеленоватый сумрак, гнилостный туман низко над водой, тусклый рассеянный "
     "свет, сырость"),
    (("лес", "чащ", "лощин", "роща", "forest", "glade", "бурелом"),
     "рассеянный приглушённый свет сквозь густые кроны, пятна тени и сумрака, лёгкая "
     "дымка"),
    (("гавань", "причал", "порт", "берег", "вод", "harbor", "затонувш", "город"),
     "промозглый сумеречный морской свет, холодный туман над водой, серо-синяя палитра"),
    (("пепел", "пустош", "ash", "выжжен", "горел"),
     "багровое зарево на горизонте, пепел и угли в воздухе, тусклый красно-серый свет"),
    (("перевал", "пик", "горы", "хребет", "ущель", "ледник", "стонущ"),
     "холодный пасмурный свет, серое штормовое небо, ветер и позёмка"),
    (("чертог рассвета", "святилищ", "храм", "рассвет"),
     "тёплый золотистый свет витражей и свечей, лучи сквозь сумрак, торжественность"),
]
_LIGHT_DEFAULT = "приглушённый пасмурный свет в духе тёмного фэнтези, мягкие тени"


def _lighting(r):
    key = (str(r.get("zone", "")) + " " + str(r.get("name", "")) + " "
           + str(r.get("biome", ""))).lower()
    rid = ""  # rid недоступен здесь; ключевые слова берём из имени/зоны/биома
    if r.get("safe") and any(w in key for w in ("брод", "острог", "гавань город",
                                                "столиц", "площад", "рынок", "ворота")):
        # дневной город — но всё равно тёмное фэнтези
        return "пасмурный дневной свет, факелы и фонари, тёплые акценты на камне"
    for kws, light in _LIGHT:
        if any(w in key for w in kws):
            return light
    return _LIGHT_DEFAULT


# расовый стиль архитектуры по зоне столицы (zone -> описание)
def _race_zone_map():
    m = {}
    theme = {
        # ВНИМАНИЕ: не упоминать вывески/указатели/таблички — модель рисует на
        # них кривые псевдобуквы (плейтест владельца на village). Вместо вывесок
        # — предметный антураж без письменности.
        "human":  "людское поселение в духе тёмного средневековья: камень и брус, "
                  "черепичные крыши, кованые фонари и ставни, бочки и корзины, "
                  "мощёные улицы",
        "orc":    "орочье становище: частоколы из заострённых брёвен и костей, грубое "
                  "ржавое железо, черепа и кровавые тотемы, дым костров",
        "elf":    "эльфийская архитектура: серебристое живое дерево, изящные арки, "
                  "светящиеся руны, лунное сияние, цветущие лозы",
        "dwarf":  "гномий чертог в скале: массивная резьба по камню, рунические колонны, "
                  "золото и горны, кузнечное зарево",
        "goblin": "гоблинские норы: хлам и кривые доски, ржавое железо, грибы и грязь, "
                  "теснота, самодельные подпорки",
    }
    for rid, rc in RACES.items():
        z = WORLD.get(rc.get("start_room"), {}).get("zone")
        if z and rid in theme:
            m[z] = theme[rid]
    return m


_RACE_ZONE = _race_zone_map()

_ROLE_VIS = {
    "vendor": "торговец за прилавком", "guard": "вооружённый страж в доспехе",
    "trainer": "наставник-воин", "mentor": "мудрый наставник в мантии",
    "priest": "жрец в облачении", "innkeeper": "трактирщик", "banker": "банкир",
    "questgiver": "пожилой советник", "faction_leader": "властный предводитель",
    "guild_master": "глава гильдии", "gossip": "горожанин", "arena_master": "мастер арены",
    "entity": "призрачная сущность",
}


# ───────── ручные промпты (data/art_prompts.yaml) ─────────
# Автоматический промпт — хорошая база, но для важных кадров (стартовый хаб,
# первые зоны, боссы) её не хватает: художнику нужно сказать конкретику словами.
# Формат файла (любая секция необязательна):
#     rooms:
#       village: "Туманная площадь на рассвете, покосившиеся дома…"
#     mobs:
#       падший_король: "Иссохший король в ржавой короне на троне из костей…"
# Если ключ найден — берётся ЭТОТ текст (стиль дописывается автоматически,
# если в тексте нет слова «концепт-арт»); иначе работает генератор промпта.
_ART_OVERRIDES = {}
_ART_PROMPTS_PATH = os.path.join(ROOT, "data", "art_prompts.yaml")
try:
    if os.path.exists(_ART_PROMPTS_PATH):
        import yaml as _yaml
        _ART_OVERRIDES = _yaml.safe_load(open(_ART_PROMPTS_PATH, encoding="utf-8")) or {}
except Exception as _e:                                   # noqa: BLE001
    print(f"⚠️  art_prompts.yaml не прочитан ({_e}) — работают авто-промпты")


def _override(kind: str, key: str):
    """Ручной промпт для конкретной комнаты/моба или None."""
    txt = ((_ART_OVERRIDES.get(kind) or {}).get(key) or "").strip()
    if not txt:
        return None
    style = MOB_STYLE if kind == "mobs" else ROOM_STYLE
    return txt if "концепт-арт" in txt else f"{txt}. {style}"


def _npc_clause(r):
    """Только визуальные роли обитателей, БЕЗ имён (имена модель рисует как подписи)."""
    roles = []
    for n in (r.get("npc", []) or [])[:2]:
        role = _ROLE_VIS.get((NPCS.get(n, {})).get("role"))
        if role and role not in roles:
            roles.append(role)
    return ("в кадре: " + ", ".join(roles)) if roles else ""


def _first_sentence(text: str) -> str:
    return (text or "").replace("\n", " ").split(".")[0].strip()


# Сколько текста описания уходит в промпт локации. Раньше бралось ОДНО первое
# предложение — и 70-80% визуальных деталей (что стоит в комнате, чем пахнет,
# что видно вдали) модель просто не получала, отсюда «пустые» однотипные сцены.
# Берём первые предложения целиком до ART_DESC_CHARS символов: Flux 2 держит
# длинный промпт, а детали из desc — единственный источник уникальности сцены.
ART_DESC_CHARS = 420


def _desc_for_art(text: str, limit: int = ART_DESC_CHARS) -> str:
    """Первые НЕСКОЛЬКО предложений описания (не одно), обрезанные по границе
    предложения до limit символов."""
    raw = " ".join((text or "").replace("\n", " ").split())
    if not raw:
        return ""
    if len(raw) <= limit:
        return raw.rstrip(".")
    cut = raw[:limit]
    dot = cut.rfind(".")
    return (cut[:dot] if dot > 60 else cut).rstrip(".")


_MOB_KIND = [
    (("скелет","зомби","упырь","мертвец","утоплен","личь","костя","нежить","призрак","фантом","дух"),
     "нежить, иссохшая фигура в лохмотьях"),
    (("демон","инфернал","падш","бес"), "рогатый демон"),
    (("дракон","змей","виверн","ящер"), "чешуйчатый дракон-ящер"),
    (("голем","истукан","автоматон","титан"), "каменный голем-исполин"),
    (("паук","пряха"), "гигантский паук"),
    (("волк","лис","секач","кабан","зверь","хищник"), "хищный зверь"),
    (("крыса","мышь","падальщик"), "мерзкий грызун-падальщик"),
    (("слизень","слизь"), "склизкий слизень"),
    (("левиафан","тварь","бездн","ужас"), "бесформенный монстр из глубин"),
    (("кобольд","гоблин","оборван"), "мелкий злобный гоблиноид"),
    (("рыцарь","страж","воин"), "латный воин в тёмной броне"),
]
def _mob_kind(name):
    low = (name or "").lower()
    for kws, vis in _MOB_KIND:
        if any(k in low for k in kws):
            return vis
    return "фэнтезийное чудовище"


def mob_prompt(mid: str) -> str:
    _ov = _override("mobs", mid)
    if _ov:
        return _ov
    m = MOBS[mid]
    lvl = m.get("level", 1)
    menace = "колоссальный могучий босс" if lvl >= 30 else ("опасный" if lvl >= 12 else "")
    dt = m.get("dmg_type")
    elem = {"fire": "в огне и пепле", "cold": "во льду и инее",
            "lightning": "в искрах молний", "poison": "в ядовитой дымке",
            "negative": "в тёмной некротической ауре", "holy": "в священном свете"}.get(dt, "")
    kind = _mob_kind(m.get("name", mid))
    # У мобов в данных НЕТ поля desc, поэтому единственный источник уникальности —
    # само название. Раньше оно отбрасывалось после сопоставления с _MOB_KIND, и
    # 78 разных существ сходились в 12 архетипов («на одно лицо»).
    # ВАЖНО: имя идёт БЕЗ кавычек и с маленькой буквы — закавыченное имя с
    # заглавной модель принимает за надпись и рисует её на картинке (плейтест).
    title = (m.get("name", mid) or "").lower()
    # ВАЖНО: в шкале размера НЕЛЬЗЯ упоминать человека («размером с человека»,
    # «возвышается над человеком») — модель рисует человека для сравнения, и на
    # портрете моба появляется лишняя фигура внизу кадра (плейтест владельца).
    # Масштаб задаём через кадрирование и сложение существа, без эталона роста.
    size = ("исполинское, туша заполняет весь кадр, ракурс снизу вверх" if lvl >= 40 else
            "массивное, тяжёлое, широкие плечи и мощные конечности" if lvl >= 20 else
            "жилистое, поджарое сложение" if lvl >= 8 else
            "мелкое, приземистое, компактное тельце")
    # среда обитания: где моб водится — задаёт фон и палитру
    habitat = ""
    for _rid, _r in WORLD.items():
        if mid in (_r.get("spawns") or []):
            habitat = _r.get("zone") or _r.get("biome") or ""
            if habitat:
                habitat = f"обитает: {habitat}"
            break
    # Порядок: ограничения кадра → образ существа → стиль.
    subject = ", ".join(p for p in (kind, title, menace, elem, size) if p)
    parts = [MOB_CONSTRAINTS, subject]
    if habitat:
        parts.append(habitat)
    return ". ".join(parts) + f". {MOB_STYLE}"


def room_prompt(rid: str) -> str:
    _ov = _override("rooms", rid)
    if _ov:
        return _ov
    r = WORLD[rid]
    zone = r.get("zone", "")
    biome = r.get("biome", "")
    desc = _desc_for_art(r.get("desc", ""))     # описание сцены, без названия-подписи
    race = _RACE_ZONE.get(zone, "")
    light = _lighting(r)
    npc = _npc_clause(r)
    # Ограничение — первым (см. MOB_CONSTRAINTS): иначе модель подписывает
    # локацию названием и лепит вывески с текстом на стенах.
    parts = ["Без текста, надписей, вывесок с буквами и водяных знаков в кадре."]
    if race:
        parts.append(race + ".")
    elif biome:
        parts.append(biome + ".")
    if desc:
        parts.append(desc + ".")
    parts.append("Освещение: " + light + ".")
    if npc:
        # «вдали, мелко» — чтобы обитатели читались как часть сцены, а не
        # выходили на первый план портретами (кадр про место, а не про людей).
        parts.append(npc + ", мелко и вдали, спиной или в профиль.")
    parts.append(ROOM_STYLE)
    return " ".join(parts)


def pick(args):
    if args.type == "mob":
        keys = sorted(MOBS.keys())
        out = MOBS_DIR
    else:
        keys = sorted(k for k, v in WORLD.items()
                      if args.include_wild or not v.get("wild"))
        out = ROOMS_DIR
    if args.ids:
        want = [s.strip() for s in args.ids.split(",") if s.strip()]
        missing = [k for k in want if k not in keys]
        if missing:
            print(f"  ! нет таких {args.type}: {', '.join(missing)}")
        keys = [k for k in keys if k in set(want)]
    if args.only_missing:
        keys = [k for k in keys if not os.path.exists(os.path.join(out, k + ".png"))]
    if args.limit:
        keys = keys[:args.limit]
    return keys, out


def comfy_post(host, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"http://{host}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def comfy_get(host, path):
    with urllib.request.urlopen(f"http://{host}{path}", timeout=60) as r:
        return r.read()


def generate_one(host, base_wf, key, prompt, out_dir, w, h, seed=None):
    wf = json.loads(json.dumps(base_wf))
    wf["7"]["inputs"]["text"] = prompt
    # seed=None → случайный (повторный запуск даёт другой вариант того же промпта);
    # заданный seed → воспроизводимость: можно менять ТОЛЬКО текст и честно
    # сравнивать формулировки промпта между собой.
    wf["11"]["inputs"]["noise_seed"] = int(seed) if seed else random.randint(1, 2**50)
    wf["8"]["inputs"]["width"] = w
    wf["8"]["inputs"]["height"] = h
    wf["15"]["inputs"]["filename_prefix"] = f"gen_{key}"
    resp = comfy_post(host, "/prompt", {"prompt": wf})
    pid = resp.get("prompt_id")
    if not pid:
        print(f"  ! {key}: нет prompt_id ({resp})"); return False
    for _ in range(180):
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
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, key + ".png"), "wb") as f:
                f.write(data)
            return True
    print(f"  ! {key}: таймаут ожидания"); return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=["mob", "room"], required=True)
    ap.add_argument("--host", default="127.0.0.1:8188")
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--include-wild", action="store_true")
    ap.add_argument("--ids", default="", help="точечно: id через запятую (напр. inn_room,temple)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--size", type=int, default=0, help="квадрат NxN (по умолчанию: моб 1024², локация 1216×704)")
    ap.add_argument("--seed", type=int, default=0,
                    help="фиксированный сид (сравнивать формулировки промпта честно)")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать промпты, НЕ генерировать (быстрая правка текста)")
    args = ap.parse_args()

    if args.dry_run:
        # Печатаем промпты и выходим: правку формулировок удобно смотреть глазами,
        # не гоняя модель (одна картинка Flux — десятки секунд).
        _keys, _ = pick(args)
        _fn = mob_prompt if args.type == "mob" else room_prompt
        for _k in _keys:
            print(f"\n=== {_k} ===\n{_fn(_k)}")
        print(f"\n({len(_keys)} шт. — генерация не запускалась)")
        return

    if not os.path.exists(WORKFLOW):
        print("Не найден workflow_final.json в корне проекта."); return
    base_wf = json.load(open(WORKFLOW, encoding="utf-8"))
    keys, out_dir = pick(args)
    if args.size:
        w = h = args.size
    elif args.type == "mob":
        w = h = 1024
    else:
        w, h = 1216, 704
    prompt_fn = mob_prompt if args.type == "mob" else room_prompt
    name_of = (lambda k: MOBS[k].get("name", k)) if args.type == "mob" \
        else (lambda k: WORLD[k].get("name", k))
    print(f"К генерации ({args.type}): {len(keys)} шт через ComfyUI @ {args.host}, {w}×{h}")
    ok = fail = 0
    for i, key in enumerate(keys, 1):
        print(f"[{i}/{len(keys)}] {key} — {name_of(key)}")
        try:
            if generate_one(args.host, base_wf, key, prompt_fn(key), out_dir, w, h,
                            seed=args.seed):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  ! {key}: ошибка {e}")
    print(f"\nГотово: ✅ {ok}, ❌ {fail}. Арты в images/{'mobs' if args.type=='mob' else 'rooms'}/.")


if __name__ == "__main__":
    main()
