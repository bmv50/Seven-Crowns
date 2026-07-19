# -*- coding: utf-8 -*-
"""Фракционная война за территории. Открытые зоны можно контролировать:
союзники фракции набивают очки контроля, убивая мобов в зоне. Доминирующая
фракция «владеет» зоной → её союзники получают бонус к добыче там.
Принадлежность игрока = фракция с наибольшей репутацией (engine.reputation).
Состояние в памяти процесса (опц. сохраняется в territory.json)."""
import json
from .content import FACTIONS, WORLD
from . import reputation

# контролируемые (открытые) зоны и тематически «коренная» фракция
ZONE_FACTION = {
    "Шепчущий лес": "shepchuschiy_krug",
    "Гнилотопь": "koven_gnilotopi",
    "Рудники": "volnye_rudokopy",
    "Пепельные Пустоши": "voinstvo_korolya",
    "Кровавый Кряж": "voinstvo_korolya",
    "Стылая Гавань": "volnye_torgovcy",
    "Чертоги Рассвета": "orden_rassveta",
}
CONTESTED = set(ZONE_FACTION)
CONTROL_TO_HOLD = 50          # очков для смены владельца
DECAY = 1                     # пассивное угасание (вызывать редко)
CONTROL_BONUS = 0.10          # +10% к добыче союзникам владельца

# zone -> {faction: points}
_control = {}

# Режим хранения (как в auction). В db-режиме save(path) не пишет файл, а лишь
# помечает _dirty — фоновый snapshot_worker сбрасывает состояние в kv_state.
_db_mode = False
_dirty = False


def set_db_mode(on: bool):
    """Включить БД-режим: save(path) больше не пишет файл, копит dirty-флаг."""
    global _db_mode
    _db_mode = bool(on)


def is_dirty() -> bool:
    return _dirty


def mark_clean():
    global _dirty
    _dirty = False


def export_state() -> dict:
    """JSON-safe снимок контроля территорий (для kv_state['territory'])."""
    return dict(_control)


def import_state(data: dict):
    """Загрузить контроль территорий из снимка (kv_state) вместо файла."""
    global _control, _dirty
    _control = dict(data) if data else {}
    _dirty = False


def allegiance(ch):
    """Фракция игрока = с наибольшей положительной репутацией, иначе None."""
    rep = ch.flags.get("rep") or {}
    best, bp = None, 0
    for fac, p in rep.items():
        if p > bp:
            bp, best = p, fac
    return best


def dominant(zone: str):
    d = _control.get(zone, {})
    if not d:
        return None
    fac = max(d, key=lambda f: d[f])
    return fac if d[fac] > 0 else None


def add_kill(ch, zone: str):
    """Начислить очко контроля фракции игрока. Возвращает нового владельца, если сменился."""
    if zone not in CONTESTED:
        return None
    fac = allegiance(ch)
    if not fac:
        return None
    before = dominant(zone)
    d = _control.setdefault(zone, {})
    d[fac] = min(CONTROL_TO_HOLD * 3, d.get(fac, 0) + 1)
    after = dominant(zone)
    return after if after != before else None


def control_bonus(ch, zone: str) -> float:
    """Множитель добычи: союзники владельца зоны получают бонус."""
    if zone not in CONTESTED:
        return 1.0
    dom = dominant(zone)
    if dom and allegiance(ch) == dom:
        return 1.0 + CONTROL_BONUS
    return 1.0


def render() -> str:
    lines = ["🗺 *Контроль территорий*"]
    for zone in sorted(CONTESTED):
        dom = dominant(zone)
        if dom:
            pts = _control.get(zone, {}).get(dom, 0)
            lines.append(f"• {zone}: {FACTIONS.get(dom, {}).get('name', dom)} ({pts})")
        else:
            lines.append(f"• {zone}: _ничей_")
    return "\n".join(lines)


def save(path: str):
    # В db-режиме файл не трогаем — помечаем dirty (флашит snapshot_worker).
    global _dirty
    if _db_mode:
        _dirty = True
        return
    try:
        json.dump(_control, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def load(path: str):
    global _control
    try:
        _control = json.load(open(path, encoding="utf-8"))
    except Exception:
        _control = {}
