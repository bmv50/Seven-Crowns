# -*- coding: utf-8 -*-
"""Навигация: поиск кратчайшего пути по комнатам (BFS) к цели квеста."""
from collections import deque

from .content import WORLD, MOBS


def bfs_path(start: str, goal_pred):
    """Список направлений от start до ближайшей комнаты, где goal_pred(room) истинно.
    [] — цель в текущей комнате; None — не найдено."""
    if start not in WORLD:
        return None
    if goal_pred(start):
        return []
    seen = {start}
    q = deque([(start, [])])
    while q:
        r, path = q.popleft()
        for d, dest in WORLD[r].get("exits", {}).items():
            if dest in seen or dest not in WORLD:
                continue
            np = path + [d]
            if goal_pred(dest):
                return np
            seen.add(dest)
            q.append((dest, np))
    return None


def _mob_pred(mob_id):
    return lambda r: mob_id in WORLD[r].get("spawns", [])


def _item_pred(item_id):
    def pred(r):
        if item_id in WORLD[r].get("items", []):
            return True
        for mob in WORLD[r].get("spawns", []):
            for ik, _ in MOBS.get(mob, {}).get("loot", []):
                if ik == item_id:
                    return True
        return False
    return pred


def path_to_mob(start, mob_id):
    return bfs_path(start, _mob_pred(mob_id))


def path_to_item(start, item_id):
    return bfs_path(start, _item_pred(item_id))
