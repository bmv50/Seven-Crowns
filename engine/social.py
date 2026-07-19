# -*- coding: utf-8 -*-
"""
Группы (пати) и PvP-дуэли.
Состояние — в памяти процесса (как и мир).

Пати: общий опыт делится между участниками в одной комнате (это уже
работает в loop через aggro-киллеров, но пати позволяет делить опыт,
даже если добил один). Участники видят чат пати и HP друг друга.

Дуэли: согласованный PvP 1-на-1. Инициатор зовёт, цель принимает.
Бой пошаговый по кнопкам, до первого падения (HP не уходит в минус —
проигравший остаётся с 1 HP, без потери золота).
"""
import time
from typing import Dict, List, Optional


class PartyManager:
    def __init__(self):
        # party_id -> {"leader": uid, "members": [uid,...]}
        self.parties: Dict[int, dict] = {}
        self.member_of: Dict[int, int] = {}     # uid -> party_id
        self.invites: Dict[int, int] = {}        # invited_uid -> party_id

    def party_of(self, uid: int) -> Optional[dict]:
        pid = self.member_of.get(uid)
        return self.parties.get(pid) if pid else None

    def create(self, leader: int) -> int:
        pid = leader
        self.parties[pid] = {"leader": leader, "members": [leader]}
        self.member_of[leader] = pid
        return pid

    def invite(self, leader: int, target: int) -> bool:
        if leader not in self.member_of:
            self.create(leader)
        pid = self.member_of[leader]
        if self.parties[pid]["leader"] != leader:
            return False
        self.invites[target] = pid
        return True

    def accept(self, uid: int) -> Optional[dict]:
        pid = self.invites.pop(uid, None)
        if pid is None or pid not in self.parties:
            return None
        if uid in self.member_of:
            self.leave(uid)
        self.parties[pid]["members"].append(uid)
        self.member_of[uid] = pid
        return self.parties[pid]

    def leave(self, uid: int) -> Optional[int]:
        pid = self.member_of.pop(uid, None)
        if pid is None:
            return None
        party = self.parties.get(pid)
        if not party:
            return None
        if uid in party["members"]:
            party["members"].remove(uid)
        # лидер ушёл — передать или распустить
        if party["leader"] == uid:
            if party["members"]:
                party["leader"] = party["members"][0]
            else:
                self.parties.pop(pid, None)
        return pid

    def members(self, uid: int) -> List[int]:
        party = self.party_of(uid)
        return party["members"] if party else [uid]


class DuelManager:
    def __init__(self):
        # uid -> duel state
        self.duels: Dict[int, dict] = {}
        self.requests: Dict[int, int] = {}   # target_uid -> challenger_uid

    def in_duel(self, uid: int) -> bool:
        return uid in self.duels

    def challenge(self, challenger: int, target: int):
        self.requests[target] = challenger

    def accept(self, target: int) -> Optional[tuple]:
        challenger = self.requests.pop(target, None)
        if challenger is None:
            return None
        # чей ход — у инициатора
        state = {"opponent": target, "turn": challenger, "started": time.time()}
        self.duels[challenger] = state
        self.duels[target] = {"opponent": challenger, "turn": challenger,
                              "started": time.time()}
        return (challenger, target)

    def decline(self, target: int) -> Optional[int]:
        return self.requests.pop(target, None)

    def end(self, uid: int):
        opp = self.duels.get(uid, {}).get("opponent")
        self.duels.pop(uid, None)
        if opp is not None:
            self.duels.pop(opp, None)

    def opponent(self, uid: int) -> Optional[int]:
        return self.duels.get(uid, {}).get("opponent")

    def whose_turn(self, uid: int) -> Optional[int]:
        return self.duels.get(uid, {}).get("turn")

    def pass_turn(self, uid: int):
        opp = self.opponent(uid)
        if opp is None:
            return
        for u in (uid, opp):
            if u in self.duels:
                self.duels[u]["turn"] = opp
