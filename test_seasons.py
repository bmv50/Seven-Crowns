# -*- coding: utf-8 -*-
"""Тесты сезонов/лиг. Запуск: python test_seasons.py"""
from engine import seasons
from engine.character import Character


def _ch():
    c = Character(uid=1, name="t", cls="warrior", race="human"); c.init_vitals()
    c.flags = {}
    return c


def test_disabled_no_points():
    seasons.ENABLED = False
    c = _ch()
    seasons.add_points(c, 100)
    assert seasons.points(c) == 0
    print("✓ выключено = очки не идут")


def test_points_and_tier():
    seasons.ENABLED = True
    c = _ch()
    seasons.add_points(c, 600)
    assert seasons.points(c) == 600
    assert seasons.tier(600)[0] == "Серебро"
    assert seasons.tier(0)[0] == "Бронза"
    assert seasons.tier(50000)[0] == "Легенда"
    print("✓ очки начисляются, лиги по порогам")


def test_rollover_reward_and_reset():
    seasons.ENABLED = True
    c = _ch()
    now = seasons.SEASON_LENGTH * 10 + 100      # фиксируем сезон
    seasons.ensure(c, now)
    seasons.add_points(c, 2500, now)            # Золото
    g0 = c.gold
    # следующий сезон
    nxt = now + seasons.SEASON_LENGTH
    rew = seasons.ensure(c, nxt)
    assert rew and rew["gold"] > 0 and rew["tier"] == "Золото"
    assert c.gold == g0 + rew["gold"]           # награда начислена
    assert seasons.points(c) == 0               # счёт сброшен
    assert c.flags["season"]["best"] == 2500    # лучший сохранён
    print("✓ ролловер: награда по лиге + сброс, лучший результат сохранён")


def test_leaderboard():
    seasons.ENABLED = True
    a = _ch(); a.uid = 1; a.name = "A"; seasons.add_points(a, 300)
    b = _ch(); b.uid = 2; b.name = "B"; seasons.add_points(b, 900)
    lb = seasons.leaderboard([a, b], me=a)
    assert lb.index("B") < lb.index("A")        # B выше по очкам
    assert "вы" in lb
    print("✓ таблица лидеров сортируется по очкам")


if __name__ == "__main__":
    test_disabled_no_points()
    test_points_and_tier()
    test_rollover_reward_and_reset()
    test_leaderboard()
    seasons.ENABLED = False
    print("\n=== seasons OK ===")
