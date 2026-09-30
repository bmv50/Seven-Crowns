"""Transport IDs are distinct; no implicit account linking."""

import asyncio

from engine.db import Database
from engine.identity import identity_key


def test_identity_validation():
    assert identity_key("telegram", 123) == ("telegram", "123")
    assert identity_key("max", "abc") == ("max", "abc")
    assert identity_key("telegram", "123") != identity_key("max", "123")
    for platform, external in (("unknown", "1"), ("max", ""),
                               ("max", True), ("telegram", "x" * 129)):
        try:
            identity_key(platform, external)
        except ValueError:
            pass
        else:
            raise AssertionError((platform, external))


async def test_identity_requires_database():
    db = Database()
    assert await db.resolve_player_id("telegram", 123) is None
    try:
        await db.reserve_max_player_id("123")
    except RuntimeError:
        pass
    else:
        raise AssertionError("MAX identity must not be volatile")


if __name__ == "__main__":
    test_identity_validation()
    asyncio.run(test_identity_requires_database())
    print("OK: platform identity namespaces and durable MAX ID requirement")
