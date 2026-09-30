# -*- coding: utf-8 -*-
"""Stage 3's audit is aggregate-only and cannot mutate the character table."""
import asyncio
import json

from scripts.audit_stage3 import Aggregate, collect, select_characters
from engine.character import LEVEL_CAP


OLD_COLUMNS = {"level", "cls", "gold", "inventory", "equipment",
               "quests", "flags", "deleted_at"}


def character(**changes):
    row = {"level": 1, "cls": "warrior", "gold": 100,
           "inventory": '["sword", "herb"]', "equipment": '{"weapon":"sword"}',
           "quests": '{"story":"done", "story:kills":"3", "next":"active"}',
           "flags": '{"remort":0, "talents":{"a":13}, "talent_points":2, '
                    '"quest_choices":{"story":"left"}, "quest_locks":["other"]}'}
    row.update(changes)
    return row


def test_select_only_aggregate_fields():
    old = select_characters(OLD_COLUMNS)
    assert "learned" not in old and "loadout" not in old
    assert "deleted_at IS NULL" in old
    current = select_characters(OLD_COLUMNS | {"learned", "loadout"})
    assert "learned" in current and "loadout" in current
    for private in ("uid", "name", "last_seen", "notify_blocked"):
        assert private not in current
    try:
        select_characters({"level"})
    except ValueError:
        pass
    else:
        raise AssertionError("Incomplete schema must fail closed")


def test_aggregate_and_privacy():
    audit = Aggregate()
    audit.add(character(level=60, name="СекретноеИмя", uid=123456))
    audit.add(character(level=LEVEL_CAP, cls="mage", gold=200,
                        flags='{"remort":2, "talents":{"a":4}}',
                        quests='{"other":"done"}', inventory="[]",
                        equipment="{}", learned='["fire"]', loadout='["fire"]'))
    report = audit.report(1, OLD_COLUMNS | {"learned", "loadout"})
    assert report["active_characters"] == 2
    assert report["deleted_characters"] == 1
    assert report["over_cap"] == 1 and report["at_cap"] == 1
    assert report["max_level"] == 60
    assert report["characters_with_spent_talents_over_budget"] == 1
    assert report["active_quests"] == 1 and report["done_quests"] == 2
    assert report["characters_with_story_choices"] == 1
    assert report["characters_with_quest_locks"] == 1
    assert report["characters_with_learned"] == 1
    assert report["characters_with_loadout"] == 1
    assert report["gold_total"] == 300
    payload = json.dumps(report, ensure_ascii=False)
    assert "СекретноеИмя" not in payload and "123456" not in payload
    assert "uid" not in payload and "name" not in payload


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeConnection:
    def __init__(self):
        self.statements = []

    def transaction(self, **kwargs):
        assert kwargs == {"isolation": "repeatable_read", "readonly": True}
        return FakeTransaction()

    async def fetch(self, query):
        self.statements.append(query)
        return [{"column_name": c} for c in OLD_COLUMNS]

    async def fetchval(self, query):
        self.statements.append(query)
        return 1

    async def cursor(self, query, prefetch):
        assert prefetch == 100
        self.statements.append(query)
        yield character()


def test_read_only_snapshot():
    con = FakeConnection()
    report = asyncio.run(collect(con))
    assert report["active_characters"] == 1
    assert report["learned_column_present"] is False
    assert report["characters_with_learned"] is None
    assert all(s.startswith("SELECT ") for s in con.statements)


if __name__ == "__main__":
    test_select_only_aggregate_fields()
    test_aggregate_and_privacy()
    test_read_only_snapshot()
    print("OK: Stage 3 read-only aggregate audit and privacy")
