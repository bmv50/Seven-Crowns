# -*- coding: utf-8 -*-
"""
Группы (пати) и PvP-дуэли.
Менеджер групп умеет экспортировать состояние для PostgreSQL; дуэли живут
в памяти процесса.

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
        if leader in self.member_of:
            return self.member_of[leader]
        # После передачи лидерства прежний leader может создать новую группу.
        # Старый pid при этом остаётся занят и не должен быть перезаписан.
        pid = leader if leader not in self.parties else max([abs(k) for k in self.parties] + [0]) + 1
        self.parties[pid] = {"leader": leader, "members": [leader]}
        self.member_of[leader] = pid
        return pid

    def invite(self, leader: int, target: int) -> bool:
        if leader == target or target in self.member_of or target in self.invites:
            return False
        if leader not in self.member_of:
            self.create(leader)
        pid = self.member_of[leader]
        if self.parties[pid]["leader"] != leader:
            return False
        self.invites[target] = pid
        return True

    def accept(self, uid: int) -> Optional[dict]:
        if uid in self.member_of:
            return None
        pid = self.invites.pop(uid, None)
        if pid is None or pid not in self.parties:
            return None
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
            # Старые приглашения выданы прежним лидером. При роспуске и смене
            # лидера они не должны оживать после создания новой группы с тем же pid.
            self.invites = {target: invited_pid for target, invited_pid in self.invites.items()
                            if invited_pid != pid}
            if party["members"]:
                party["leader"] = party["members"][0]
            else:
                self.parties.pop(pid, None)
        return pid

    def members(self, uid: int) -> List[int]:
        party = self.party_of(uid)
        return party["members"] if party else [uid]

    def export_state(self) -> dict:
        return {"version": 1,
                "parties": {str(pid): {"leader": party["leader"],
                                       "members": list(party["members"])}
                            for pid, party in self.parties.items()},
                "invites": {str(uid): pid for uid, pid in self.invites.items()}}

    def import_state(self, state: dict | None, valid_uids: set[int] | None = None):
        state = state if state is not None else {"version": 1, "parties": {}, "invites": {}}
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("Unsupported party state")
        parties, member_of, invites, changed_leaders = {}, {}, {}, set()
        for raw_pid, entry in state["parties"].items():
            pid, leader = int(raw_pid), int(entry["leader"])
            original = [int(uid) for uid in entry["members"]]
            if leader not in original or len(original) != len(set(original)):
                raise ValueError("Invalid party membership")
            members = [uid for uid in original if valid_uids is None or uid in valid_uids]
            if not members:
                continue
            if leader not in members:
                leader = members[0]
                changed_leaders.add(pid)
            for uid in members:
                if uid in member_of:
                    raise ValueError("Player belongs to multiple parties")
                member_of[uid] = pid
            parties[pid] = {"leader": leader, "members": members}
        for raw_uid, raw_pid in state["invites"].items():
            uid, pid = int(raw_uid), int(raw_pid)
            if (pid in parties and pid not in changed_leaders and uid not in member_of
                    and (valid_uids is None or uid in valid_uids)):
                invites[uid] = pid
        self.parties, self.member_of, self.invites = parties, member_of, invites


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
