# -*- coding: utf-8 -*-
"""
Фоны карт зон: images/maps/<зона>.jpg через локальный ComfyUI.

Зачем. Карта окрестностей (bot/mapgen.py) рисует комнаты панелями поверх фона.
Раньше фон был один на всю игру — пустоши выглядели как лес, а подгорные залы
как поле. Один фон на зону даёт каждой местности своё лицо, и при этом ничего
не нужно расставлять руками: комнаты по-прежнему кладутся на сетку сами, а
значит новая комната или выход не ломают картинку. Рисованная карта под
топологию выглядела бы лучше, но её пришлось бы перерисовывать после каждой
правки мира — на живой бете это дорого.

Кадр строится как ВИД СВЕРХУ: карта, а не пейзаж. Панели комнат непрозрачны,
поэтому фон работает атмосферой и рамкой, а не носителем деталей — отсюда
установка на приглушённость и пустой центр.

ВАЖНО про текст: названия комнат рисует код (Pillow). Модели просить подписи
нельзя — она выдаёт псевдобуквы («ПлощадВвата Фонтана» из образца владельца).
Поэтому запрет надписей стоит первым и повторяется в хвосте промпта.

Запуск (из корня проекта, ComfyUI слушает 127.0.0.1:8188):
    py scripts/gen_map_backgrounds.py --dry-run     # показать промпты
    py scripts/gen_map_backgrounds.py               # все зоны без картинки
    py scripts/gen_map_backgrounds.py --all         # перерисовать все
    py scripts/gen_map_backgrounds.py --zones "Рудники,Гнилотопь"
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.content import WORLD                       # noqa: E402

OUT_DIR = os.path.join(ROOT, "images", "maps")
WORKFLOW = os.path.join(ROOT, "workflow_final.json")
W, H = 1024, 704          # пропорции близки к типовой карте (шире, чем выше)

NO_TEXT = ("Без единой буквы, цифры, надписи, подписи, легенды, компаса с "
           "буквами сторон света, гербов и картуша с текстом — все названия "
           "нанесёт программа поверх")

STYLE = ("вид строго сверху, старинная нарисованная от руки карта местности, "
         "тушь и акварель по тонированной бумаге, приглушённые тона, "
         "мягкое виньетирование по краям, без рамки-бордюра, "
         "детали разрежены — центр кадра спокойный и пустой, "
         "чтобы поверх легли панели с названиями. " + NO_TEXT)

# Характер каждой зоны. Пишем ландшафт и фактуру, а не постройки: конкретные
# дома нарисованы в панелях комнат, а фон должен их поддерживать, не споря.
ZONE_ART = {
    "Туманный Брод": "черепичные крыши городка вдоль реки, мощёные улочки, "
                     "мосты, туман в низинах, огороды и сады по краям",
    "Железный Острог": "суровая крепость у подножия рудных гор, отвалы породы, "
                       "частоколы, дым кузниц, серо-стальная гамма",
    "Стылая Гавань": "холодная бухта, пирсы и лодки, склады у воды, солёный "
                     "туман, сине-серая гамма",
    "Пепельные Пустоши": "выжженная равнина, пепел и угли, обугленные стволы, "
                         "багровое зарево у горизонта, серо-красная гамма",
    "Чертоги Рассвета": "горная обитель на террасах, ступени и мосты, "
                        "золотистый свет, светлый камень",
    "Перевал Стонущих Ветров": "скалы и снежные седловины, узкие тропы, "
                               "пропасти, позёмка, холодная гамма",
    "Затонувший Город": "затопленные кварталы под толщей воды, купола и "
                        "колонны в иле, сине-зелёная гамма, лучи сверху",
    "Гномий Чертог": "разрез подгорных залов и штреков, тёсаный камень, "
                     "жаровни и горны, тёплые оранжевые отсветы",
    "Лунный Предел": "серебристый лес эльфов, светящиеся прожилки в стволах, "
                     "поляны и родники, холодное лунное сияние",
    "Кровавый Кряж": "орочье становище на кряже, частоколы из брёвен и костей, "
                     "дым костров, бурая гамма",
    "Гоблинская Нора": "разрез земляных нор и лазов, подпорки из кривых досок, "
                       "грибы и грязь, тесно и хаотично",
    "Сердце Бездны": "подземная бездна, каменные своды и провалы, "
                     "фиолетово-чёрная гамма, редкие холодные огни",
    "Шепчущий лес": "густой лиственный лес, тропы между стволами, поляны, "
                    "туман между деревьями, зелёно-серая гамма",
    "Рудники": "склоны с входами штолен, отвалы, вагонетки и рельсы, "
               "пыльная бурая гамма",
    "Подземелья": "разрез катакомб и колодцев, ниши с костями, узкие ходы, "
                  "холодный камень, почти монохром",
    "Гнилотопь": "болото с гатями и кочками, гнилые деревья, зелёная муть, "
                 "туман над водой",
    "Руины Эха": "древние руины на плато, обломки колонн и стен, "
                 "выветренный камень, тревожная серо-охристая гамма",
}


def zones_in_world():
    """Зоны рукотворного мира (дикие процедурные не трогаем)."""
    seen = []
    for r in WORLD.values():
        z = r.get("zone")
        if z and not r.get("wild") and z not in seen:
            seen.append(z)
    return seen


def build_prompt(zone: str) -> str:
    art = ZONE_ART.get(zone) or "фэнтезийная местность, ландшафт вид сверху"
    return f"{NO_TEXT}. Карта местности: {art}. {STYLE}"


def _art_exists(zone: str) -> bool:
    return any(os.path.exists(os.path.join(OUT_DIR, zone + e))
               for e in (".jpg", ".jpeg", ".png", ".webp"))


def comfy_post(host, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"http://{host}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def comfy_get(host, path):
    with urllib.request.urlopen(f"http://{host}{path}", timeout=60) as r:
        return r.read()


def generate_one(host, base_wf, zone, seed=0):
    wf = json.loads(json.dumps(base_wf))
    wf["7"]["inputs"]["text"] = build_prompt(zone)
    wf["11"]["inputs"]["noise_seed"] = int(seed) if seed else random.randint(1, 2 ** 50)
    wf["8"]["inputs"]["width"] = W
    wf["8"]["inputs"]["height"] = H
    wf["15"]["inputs"]["filename_prefix"] = "map_" + zone.replace(" ", "_")
    resp = comfy_post(host, "/prompt", {"prompt": wf})
    pid = resp.get("prompt_id")
    if not pid:
        print(f"  ! {zone}: нет prompt_id ({resp})")
        return False
    for _ in range(240):
        time.sleep(1)
        try:
            hist = json.loads(comfy_get(host, f"/history/{pid}").decode("utf-8"))
        except Exception:
            continue
        if pid not in hist:
            continue
        outs = [im for o in hist[pid].get("outputs", {}).values()
                for im in o.get("images", [])]
        if not outs:
            continue
        img = outs[0]
        q = urllib.parse.urlencode({"filename": img["filename"],
                                    "subfolder": img.get("subfolder", ""),
                                    "type": img.get("type", "output")})
        data = comfy_get(host, f"/view?{q}")
        os.makedirs(OUT_DIR, exist_ok=True)
        tmp = os.path.join(OUT_DIR, zone + ".png")
        with open(tmp, "wb") as f:
            f.write(data)
        # сразу в JPEG: фон всё равно затемняется и уходит под панели,
        # альфа ему не нужна, а PNG на 1024×704 весит впятеро больше
        try:
            from PIL import Image
            with Image.open(tmp) as im:
                im.convert("RGB").save(os.path.join(OUT_DIR, zone + ".jpg"),
                                       "JPEG", quality=85, optimize=True)
            os.remove(tmp)
        except Exception as e:                       # noqa: BLE001
            print(f"  ~ {zone}: оставлен PNG ({e})")
        return True
    print(f"  ! {zone}: таймаут ожидания")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1:8188")
    ap.add_argument("--zones", default="", help="через запятую; по умолчанию все")
    ap.add_argument("--all", action="store_true", help="и те, у кого фон уже есть")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    want = [z.strip() for z in args.zones.split(",") if z.strip()]
    zones = want or zones_in_world()
    unknown = [z for z in zones if z not in ZONE_ART]
    if unknown:
        print(f"⚠️  нет описания фона для зон: {', '.join(unknown)} — "
              f"будет общий фэнтезийный ландшафт")
    if not args.all and not want:
        zones = [z for z in zones if not _art_exists(z)]

    if args.dry_run:
        for z in zones:
            print(f"\n=== {z} ===\n{build_prompt(z)}")
        print(f"\n({len(zones)} шт. — генерация не запускалась)")
        return

    if not zones:
        print("Все фоны уже на месте. --all — перерисовать.")
        return
    if not os.path.exists(WORKFLOW):
        print("Не найден workflow_final.json в корне проекта.")
        return

    base_wf = json.load(open(WORKFLOW, encoding="utf-8"))
    print(f"К генерации: {len(zones)} фонов через ComfyUI @ {args.host}")
    ok = fail = 0
    for i, z in enumerate(zones, 1):
        print(f"[{i}/{len(zones)}] {z}")
        try:
            if generate_one(args.host, base_wf, z, seed=args.seed):
                ok += 1
            else:
                fail += 1
        except Exception as e:                        # noqa: BLE001
            fail += 1
            print(f"  ! {z}: ошибка {e}")
    print(f"\nГотово: ✅ {ok}, ❌ {fail}. Фоны в images/maps/.")
    print("Карта подхватит их сама — перезапуск бота не нужен только локально;")
    print("на сервере не забудьте Redeploy с Force rebuild.")


if __name__ == "__main__":
    main()
