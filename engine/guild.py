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
        g = self.guild_of(by)
        if not g or target not in g["members"] or target == by:
            return False
        # исключать может тот, кто умеет снимать из банка и стоит выше цели
        if self.can_withdraw(by) and self._ri(by) < self._ri(target):
            self.leave(target)
            return True
        return False

    def set_rank(self, by: int, target: int, rank: str) -> bool:
        """Назначить ранг (лидер/зам). Ранг строго ниже ранга назначающего."""
        if not self.can_admin(by) or rank not in RANK_ORDER or rank == "leader":
            return False
        g = self.guild_of(by)
        if not g or target not in g["members"] or target == by:
            return False
        if _idx(rank) <= self._ri(by) or self._ri(target) <= self._ri(by):
            return False
        g["ranks"][str(target)] = rank
        self.save()
        return True

    def promote(self, by: int, target: int) -> bool:
        new = max(self._ri(target) - 1, _ADMIN_MAX)   # не выше заместителя
        if new <= self._ri(by):
            return False
        return self.set_rank(by, target, RANK_ORDER[new])

    def demote(self, by: int, target: int) -> bool:
        new = min(self._ri(target) + 1, _idx("member"))
        if new == self._ri(target):
            return True
        return self.set_rank(by, target, RANK_ORDER[new])

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
