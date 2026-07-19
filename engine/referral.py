# -*- coding: utf-8 -*-
"""
Реферальная система: приглашение друга по deep-link (/start ref_<uid>).

Приглашение фиксируется при создании персонажа (ch.flags["ref_by"] = uid
реферера). Награда обеим сторонам выдаётся один раз — когда приглашённый
достигает REWARD_LEVEL (см. on_level, вызывается из engine/loop.py на
левелапе). Чтобы реферер не фармил бесконечные аккаунты, число оплаченных
приглашений на одного реферера ограничено MAX_REWARDED — сверх лимита новый
игрок всё равно получает свою награду, а реферер — уже нет.

Модуль чистый (без Telegram): интеграция — в bot/main.py (deep-link при
/start, кнопка «Пригласить друга») и engine/loop.py (хук на левелапе).
"""
import re

REWARD_LEVEL = 5                                    # уровень приглашённого, дающий награду
NEW_PLAYER_REWARD = {"gold": 2000, "items": ["эликсир"]}   # награда новому игроку
REFERRER_REWARD = {"gold": 10000}                   # награда рефереру
MAX_REWARDED = 20                                   # макс. оплаченных приглашений на реферера

_START_ARG_RE = re.compile(r"^ref_(\d+)$")


def set_referrer(ch, ref_uid) -> bool:
    """Назначить реферера при создании персонажа. -> True при успехе.
    Отказ: ref_uid == ch.uid, у ch уже есть ref_by, либо ref_uid не int."""
    if not isinstance(ref_uid, int) or isinstance(ref_uid, bool):
        return False
    if ref_uid == ch.uid:
        return False
    if ch.flags.get("ref_by"):
        return False
    ch.flags["ref_by"] = ref_uid
    return True


def on_level(ch, referrer_or_none):
    """Вызывается на левелапе (после изменения ch.level). -> (new_lines, referrer_line).
    new_lines: list[str] для нового игрока (обычно пустой).
    referrer_line: str|None — текст для реферера (отправить отдельно, если есть)."""
    new_lines = []
    referrer_line = None
    ref_by = ch.flags.get("ref_by")
    if ch.level < REWARD_LEVEL or not ref_by or ch.flags.get("ref_rewarded"):
        return new_lines, referrer_line
    ch.flags["ref_rewarded"] = True
    ch.gold += NEW_PLAYER_REWARD.get("gold", 0)
    for it in NEW_PLAYER_REWARD.get("items", []):
        ch.inventory.append(it)
    new_lines.append(
        f"🤝 Награда за приглашение друга: +{NEW_PLAYER_REWARD.get('gold', 0)} золота"
        + (", " + ", ".join(NEW_PLAYER_REWARD.get("items", [])) if NEW_PLAYER_REWARD.get("items") else "")
        + "!"
    )
    if referrer_or_none is not None:
        ref_count = int(referrer_or_none.flags.get("ref_count", 0))
        if ref_count < MAX_REWARDED:
            referrer_or_none.gold += REFERRER_REWARD.get("gold", 0)
            referrer_or_none.flags["ref_count"] = ref_count + 1
            referrer_line = (f"🤝 Ваш друг {ch.name} достиг {REWARD_LEVEL} уровня! "
                             f"+{REFERRER_REWARD.get('gold', 0)} золота")
    return new_lines, referrer_line


def link(bot_username: str, uid: int) -> str:
    """Персональная реферальная ссылка."""
    return f"https://t.me/{bot_username}?start=ref_{uid}"


def parse_start_arg(text: str):
    """Из текста команды /start ref_123 вытащить 123 (int) -> None при мусоре/отсутствии."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    m = _START_ARG_RE.match(parts[1].strip())
    if not m:
        return None
    return int(m.group(1))


def render(ch, bot_username: str) -> str:
    """Экран «Пригласи друга»: ссылка, статистика, условия награды."""
    ref_count = int(ch.flags.get("ref_count", 0))
    lim_note = ("_Лимит наград достигнут — новых оплаченных приглашений больше нет._"
                if ref_count >= MAX_REWARDED else
                f"_Осталось оплаченных приглашений: {MAX_REWARDED - ref_count}._")
    L = [
        "🤝 *Пригласи друга*",
        "",
        f"Твоя ссылка:\n`{link(bot_username, ch.uid)}`",
        "",
        f"Приглашено друзей: *{ref_count}/{MAX_REWARDED}*",
        lim_note,
        "",
        f"🎁 Когда друг, пришедший по ссылке, достигнет {REWARD_LEVEL} уровня:",
        f"   • друг получит +{NEW_PLAYER_REWARD.get('gold', 0)} золота"
        + (f" и {', '.join(NEW_PLAYER_REWARD.get('items', []))}"
           if NEW_PLAYER_REWARD.get("items") else "") + ";",
        f"   • ты получишь +{REFERRER_REWARD.get('gold', 0)} золота.",
    ]
    return "\n".join(L)
