#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only, aggregate audit before changing legacy character progression.

The database URL is accepted only through an explicitly named environment
variable. This tool never loads characters into the game or runs migrations.
"""

import argparse
import asyncio
from collections import Counter
import json
import os
import sys

from engine.character import LEVEL_CAP
from engine.content import CLASSES
from engine.talents import talent_budget


def _object(value, fallback):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _nonnegative_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def select_characters(columns):
    """Only whitelisted fields: never fetch names, platform IDs or chat text."""
    required = {"level", "cls", "gold", "inventory", "equipment",
                "quests", "flags"}
    missing = required - set(columns)
    if missing:
        raise ValueError("Нет обязательных колонок characters: " + ", ".join(sorted(missing)))
    fields = sorted(required | ({"learned", "loadout"} & set(columns)))
    where = " WHERE deleted_at IS NULL" if "deleted_at" in columns else ""
    return "SELECT " + ", ".join(fields) + " FROM characters" + where


class Aggregate:
    def __init__(self):
        self.total = 0
        self.max_level = 0
        self.levels = Counter()
        self.classes = Counter()
        self.remorts = Counter()
        self.at_cap = 0
        self.over_cap = 0
        self.spent_over_budget = 0
        self.talent_points_total = 0
        self.talent_ranks_total = 0
        self.inventory_items = 0
        self.equipped_items = 0
        self.gold_total = 0
        self.active_quests = 0
        self.done_quests = 0
        self.characters_with_choices = 0
        self.characters_with_locks = 0
        self.learned_nonempty = 0
        self.loadout_nonempty = 0

    def add(self, row):
        row = dict(row)
        self.total += 1
        level = _nonnegative_int(row["level"])
        self.max_level = max(self.max_level, level)
        if level > LEVEL_CAP:
            bucket = f">{LEVEL_CAP}"
            self.over_cap += 1
        elif level == LEVEL_CAP:
            bucket = str(LEVEL_CAP)
            self.at_cap += 1
        elif level <= 5:
            bucket = "1-5"
        elif level <= 10:
            bucket = "6-10"
        elif level <= 20:
            bucket = "11-20"
        else:
            bucket = "21-24"
        self.levels[bucket] += 1
        cls = str(row["cls"])
        self.classes[cls if cls in CLASSES else "<unknown>"] += 1
        flags = _object(row["flags"], {})
        quests = _object(row["quests"], {})
        remorts = _nonnegative_int(flags.get("remort"))
        self.remorts["10+" if remorts >= 10 else str(remorts) if remorts <= 2
                     else "3-9"] += 1
        spent = sum(_nonnegative_int(rank) for rank in
                    _object(flags.get("talents"), {}).values())
        self.talent_ranks_total += spent
        self.talent_points_total += _nonnegative_int(flags.get("talent_points"))
        self.spent_over_budget += spent > talent_budget()
        self.inventory_items += len(_object(row["inventory"], []))
        self.equipped_items += sum(bool(item) for item in
                                   _object(row["equipment"], {}).values())
        self.gold_total += _nonnegative_int(row["gold"])
        self.active_quests += sum(status == "active" for qid, status in
                                  quests.items() if ":" not in qid)
        self.done_quests += sum(status == "done" for qid, status in
                                quests.items() if ":" not in qid)
        self.characters_with_choices += bool(_object(flags.get("quest_choices"), {}))
        self.characters_with_locks += bool(_object(flags.get("quest_locks"), []))
        if "learned" in row:
            self.learned_nonempty += bool(_object(row["learned"], []))
        if "loadout" in row:
            self.loadout_nonempty += bool(_object(row["loadout"], []))

    def report(self, deleted_count, columns):
        return {
            "read_only": True,
            "level_cap": LEVEL_CAP,
            "active_characters": self.total,
            "deleted_characters": deleted_count,
            "level_buckets": dict(sorted(self.levels.items())),
            "max_level": self.max_level,
            "at_cap": self.at_cap,
            "over_cap": self.over_cap,
            "classes": dict(sorted(self.classes.items())),
            "remort_buckets": dict(sorted(self.remorts.items())),
            "talent_budget_per_run": talent_budget(),
            "characters_with_spent_talents_over_budget": self.spent_over_budget,
            "total_spent_talent_ranks": self.talent_ranks_total,
            "total_unspent_talent_points": self.talent_points_total,
            "inventory_items": self.inventory_items,
            "equipped_items": self.equipped_items,
            "gold_total": self.gold_total,
            "active_quests": self.active_quests,
            "done_quests": self.done_quests,
            "characters_with_story_choices": self.characters_with_choices,
            "characters_with_quest_locks": self.characters_with_locks,
            "learned_column_present": "learned" in columns,
            "loadout_column_present": "loadout" in columns,
            "characters_with_learned": self.learned_nonempty if "learned" in columns else None,
            "characters_with_loadout": self.loadout_nonempty if "loadout" in columns else None,
        }


async def collect(con):
    # A repeatable snapshot avoids mixing character states mid-tick. PostgreSQL
    # rejects writes in this transaction even if a future edit adds one.
    async with con.transaction(isolation="repeatable_read", readonly=True):
        columns = {r["column_name"] for r in await con.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='characters'")}
        query = select_characters(columns)
        deleted_count = (await con.fetchval(
            "SELECT count(*) FROM characters WHERE deleted_at IS NOT NULL")
            if "deleted_at" in columns else None)
        aggregate = Aggregate()
        async for row in con.cursor(query, prefetch=100):
            aggregate.add(row)
        return aggregate.report(deleted_count, columns)


async def run(dsn):
    import asyncpg

    con = await asyncpg.connect(
        dsn, server_settings={"default_transaction_read_only": "on",
                              "statement_timeout": "15000"})
    try:
        return await collect(con)
    finally:
        await con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", required=True,
                        help="Имя переменной с DSN. URL не передавать аргументом.")
    args = parser.parse_args()
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        parser.error("Указанная переменная окружения не задана")
    try:
        report = asyncio.run(run(dsn))
    except Exception as exc:
        # Driver errors can contain connection data: never print exception text.
        print(f"Аудит не выполнен ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
