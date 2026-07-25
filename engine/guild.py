# -*- coding: utf-8 -*-
"""
Гильдии (кланы): постоянные объединения с банком (золото+предметы), иерархией
рангов и гильд-чатом. Состояние персистится в JSON (переживает перезапуск без БД).

Иерархия (сверху вниз):
  leader → deputy → senior_officer → officer → sergeant → member
Права по уровню:
  • приглашать: до сержанта включительно;
  • снимать из банка: до офицера включительно;
  • управлять составом (повышать/понижать/исключать): лидер и заместитель.
"""
import json
import os
import time
from typing import Optional

from . import log as _elog

_log = _elog.get("engine.guild")

CREATE_COST = 500000   # бронза (50 золотых)

RANK_ORDER = ["leader", "deputy", "senior_officer", "officer", "sergeant", "member"]
RANKS = {
    "leader": "👑 Лидер",
    "deputy": "🎖 Заместитель",
    "senior_officer": "🛡 Старший офицер",
    "officer": "⚔️ Офицер",
    "sergeant": "🔰 Сержант",
    "member": "🪖 Боец",
}
_INVITE_MAX = RANK_ORDER.index("sergeant")    # приглашать могут до сержанта
_WITHDRAW_MAX = RANK_ORDER.index("officer")   # снимать из банка — до офицера
_ADMIN_MAX = RANK_ORDER.index("deputy")       # управлять составом — лидер/зам


def _idx(rank) -> int:
    return RANK_ORDER.index(rank) if rank in RANK_ORDER else len(RANK_ORDER)


class GuildManager:
    def __init__(self, path: str):
        self.path = path
        self.guilds = {}        # gid(str) -> dict
        self.member_of = {}     # uid(int) -> gid
        self.invites = {}       # uid(int) -> gid
        self._next = 1
        self.load()

    # ── персистентность ──
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.guilds = {str(k): v for k, v in data.get("guilds", {}).items()}
            self._next = data.get("next", 1)
            self.member_of = {}
            for gid, g in self.guilds.items():
                for uid in g.get("members", []):
                    self.member_of[int(uid)] = gid
        except (FileNotFoundError, ValueError):
            self.guilds = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"guilds": self.guilds, "next": self._next}, f, ensure_ascii=False)
        except Exception as e:
            # Больше НЕ глотаем молча: запись guilds.json (fallback без БД) могла
            # тихо потерять вклад в казну. В БД-режиме источник истины — таблицы
            # guilds/guild_members (engine/guild_tx.py), так что сбой файла не
            # критичен, но обязан быть виден в логах, а не исчезать в except: pass.
            _elog.log_err(_log, "guilds_save_failed", e, path=self.path)

    # ── доступ ──
    def guild_of(self, uid: int) -> Optional[dict]:
        gid = self.member_of.get(uid)
        return self.guilds.get(gid) if gid else None

    def gid_of(self, uid: int):
        return self.member_of.get(uid)

    def rank(self, uid: int) -> Optional[str]:
        g = self.guild_of(uid)
        return g["ranks"].get(str(uid)) if g else None

    def _ri(self, uid: int) -> int:
        return _idx(self.rank(uid))

    def can_invite(self, uid: int) -> bool:
        return self.guild_of(uid) is not None and self._ri(uid) <= _INVITE_MAX

    def can_withdraw(self, uid: int) -> bool:
        return self.guild_of(uid) is not None and self._ri(uid) <= _WITHDRAW_MAX

    def can_admin(self, uid: int) -> bool:
        return self.guild_of(uid) is not None and self._ri(uid) <= _ADMIN_MAX

    def is_leader(self, uid: int) -> bool:
        return self.rank(uid) == "leader"

    # ── чистые проверки управления составом (Аудит-2а.2) ──
    # Дефект внешнего аудита: kick() проверял can_withdraw (право снимать из
    # банка, до офицера включительно) вместо can_admin (лидер/зам) — офицер
    # мог исключать сослуживцев, хотя составом управлять не должен ("Права по
    # уровню" в шапке файла всегда требовали именно admin для кика/рангов).
    # Ниже — чистые методы (состояние НЕ меняют, только отвечают bool), общие
    # для kick/promote/demote И для server-side guard в bot/main.py — колбэк
    # обязан звать их заново перед мутацией, а не доверять тому, что кнопка
    # была видна в момент рендера меню (кнопки могли устареть).
    def _can_manage_target(self, by: int, target: int) -> bool:
        """База для kick/promote/demote: не на себя; инициатор администрирует
        состав (can_admin — лидер/зам); обе стороны в ОДНОЙ гильдии; инициатор
        строго выше цели по рангу; лидера трогать нельзя (ни кикнуть, ни
        понизить, ни назначить поверх)."""
        if target == by:
            return False
        if not self.can_admin(by):
            return False
        if self.guild_of(by) is None or self.gid_of(by) != self.gid_of(target):
            return False
        if self._ri(by) >= self._ri(target):
            return False
        if self.rank(target) == "leader":
            return False
        return True

    def _valid_new_rank(self, by: int, new_rank: str) -> bool:
        """Назначаемый ранг должен существовать, не быть 'leader' (лидерство
        не раздаётся set_rank'ом) и быть строго НИЖЕ ранга инициатора —
        нельзя назначить ранг ≥ ранга инициатора (иначе цель сравняется или
        превзойдёт назначающего)."""
        if new_rank not in RANK_ORDER or new_rank == "leader":
            return False
        return _idx(new_rank) > self._ri(by)

    def can_kick(self, by: int, target: int) -> bool:
        """Может ли by исключить target из гильдии прямо сейчас (без побочных
        эффектов) — используется и внутри kick(), и как server-side guard в
        bot/main.py перед любым обращением к БД/памяти."""
        return self._can_manage_target(by, target)

    def can_promote(self, by: int, target: int, new_rank: Optional[str] = None) -> bool:
        """Может ли by повысить target. Без new_rank проверяется рангом,
        который выдал бы promote() (на ступень выше, не выше заместителя);
        с явным new_rank — назначение конкретного ранга (используется и
        set_rank для проверки направления-агностичного назначения)."""
        if not self._can_manage_target(by, target):
            return False
        if new_rank is None:
            new_rank = RANK_ORDER[max(self._ri(target) - 1, _ADMIN_MAX)]
        return self._valid_new_rank(by, new_rank)

    def can_demote(self, by: int, target: int, new_rank: Optional[str] = None) -> bool:
        """Может ли by понизить target. Без new_rank — ранг на ступень ниже
        (не ниже 'member')."""
        if not self._can_manage_target(by, target):
            return False
        if new_rank is None:
            new_rank = RANK_ORDER[min(self._ri(target) + 1, _idx("member"))]
        return self._valid_new_rank(by, new_rank)

    # ── жизненный цикл ──
    def create(self, leader: int, name: str) -> str:
        gid = str(self._next); self._next += 1
        self.guilds[gid] = {
            "name": name[:24], "leader": leader, "members": [leader],
            "ranks": {str(leader): "leader"}, "bank_gold": 0, "bank_items": [],
            "founded": int(time.time()),
        }
        self.member_of[leader] = gid
        self.save()
        return gid

    def invite(self, inviter: int, target: int) -> bool:
        if not self.can_invite(inviter) or target in self.member_of:
            return False
        self.invites[target] = self.gid_of(inviter)
        return True

    def accept(self, uid: int) -> Optional[dict]:
        gid = self.invites.pop(uid, None)
        if not gid or gid not in self.guilds or uid in self.member_of:
            return None
        g = self.guilds[gid]
        g["members"].append(uid)
        g["ranks"][str(uid)] = "member"
        self.member_of[uid] = gid
        self.save()
        return g

    def decline(self, uid: int):
        self.invites.pop(uid, None)

    def leave(self, uid: int) -> Optional[str]:
        gid = self.member_of.pop(uid, None)
        if not gid:
            return None
        g = self.guilds.get(gid)
        if not g:
            return None
        if uid in g["members"]:
            g["members"].remove(uid)
        g["ranks"].pop(str(uid), None)
        if g["leader"] == uid:
            if g["members"]:
                # лидерство — самому высокому по рангу
                new = min(g["members"], key=lambda m: _idx(g["ranks"].get(str(m), "member")))
                g["leader"] = new
                g["ranks"][str(new)] = "leader"
            else:
                self.guilds.pop(gid, None)
        self.save()
        return gid

    def kick(self, by: int, target: int) -> bool:
        """Исключить target из гильдии. Управляют составом ТОЛЬКО лидер и
        заместитель (can_admin) — см. can_kick. Офицер (can_withdraw без
        admin) кикать не может, даже если стоит выше цели по рангу."""
        if not self.can_kick(by, target):
            return False
        self.leave(target)
        return True

    def set_rank(self, by: int, target: int, rank: str) -> bool:
        """Назначить ранг напрямую (произвольный скачок, направление не
        важно) — базовый примитив, которым пользуются promote()/demote() и
        которым может воспользоваться вызывающая сторона (напр. bot/main.py
        после успешной guild_tx-транзакции) для явного значения ранга."""
        if not self.can_promote(by, target, new_rank=rank):
            return False
        g = self.guild_of(by)
        g["ranks"][str(target)] = rank
        self.save()
        return True

    def promote(self, by: int, target: int) -> bool:
        if not self.can_promote(by, target):
            return False
        new = max(self._ri(target) - 1, _ADMIN_MAX)   # не выше заместителя
        return self.set_rank(by, target, RANK_ORDER[new])

    def demote(self, by: int, target: int) -> bool:
        if not self.can_demote(by, target):
            return False
        new = min(self._ri(target) + 1, _idx("member"))
        if new == self._ri(target):
            return True
        return self.set_rank(by, target, RANK_ORDER[new])

    def preview_promote(self, target: int) -> str:
        """Ранг, который получит target при promote() — ЧИСТЫЙ расчёт (без
        проверки прав, без мутации). Нужен вызывающей стороне (bot/main.py),
        чтобы в БД-режиме сначала записать точный будущий ранг транзакцией
        guild_tx.set_rank и ТОЛЬКО ПОСЛЕ её успеха применить его к памяти —
        без этого пришлось бы либо мутировать guild_mgr раньше БД, либо
        гадать, какой ранг записывать в БД."""
        return RANK_ORDER[max(self._ri(target) - 1, _ADMIN_MAX)]

    def preview_demote(self, target: int) -> str:
        """Симметрично preview_promote — для demote()."""
        return RANK_ORDER[min(self._ri(target) + 1, _idx("member"))]

    def _force_rank(self, target: int, rank: str) -> bool:
        """Применить УЖЕ ПОДТВЕРЖДЁННЫЙ ранг напрямую в память, без повторной
        проверки прав инициатора — последний шаг конвейера «право → БД →
        память» в bot/main.py, когда guild_tx-транзакция уже закоммичена.
        Единственная защита здесь — target всё ещё должен состоять в гильдии
        (мог успеть выйти/быть кикнут, пока шла БД-транзакция); в этом случае
        — честный отказ, а не тихая порча состояния."""
        g = self.guild_of(target)
        if not g:
            return False
        g["ranks"][str(target)] = rank
        self.save()
        return True

    def members(self, uid: int):
        g = self.guild_of(uid)
        return list(g["members"]) if g else [uid]

    # ── банк ──
    def deposit_gold(self, uid: int, amount: int) -> bool:
        g = self.guild_of(uid)
        if not g or amount <= 0:
            return False
        g["bank_gold"] = int(g.get("bank_gold", 0)) + amount
        self.save()
        return True

    def withdraw_gold(self, uid: int, amount: int) -> bool:
        g = self.guild_of(uid)
        if not g or not self.can_withdraw(uid) or amount <= 0 or g.get("bank_gold", 0) < amount:
            return False
        g["bank_gold"] -= amount
        self.save()
        return True

    def deposit_item(self, uid: int, item: str) -> bool:
        g = self.guild_of(uid)
        if not g:
            return False
        g.setdefault("bank_items", []).append(item)
        self.save()
        return True

    def withdraw_item(self, uid: int, item: str) -> bool:
        g = self.guild_of(uid)
        if not g or not self.can_withdraw(uid) or item not in g.get("bank_items", []):
            return False
        g["bank_items"].remove(item)
        self.save()
        return True
