# -*- coding: utf-8 -*-
"""
Единый обеззараживатель текста модели перед показом игроку (Этап 8).

Все пути LLM-текста к игроку (реплики NPC, анонс мирового события, летопись
сезона, озвучка поручения) обязаны пройти через sanitize_out() — раньше та же
логика была продублирована тремя копиями (ai/god.py::_esc,
ai/actions.py::_clean_errand_text) с чуть разными порогами. Теперь одна точка:

  • вырезаем markdown-инъекции (* _ ` [ ]) — модель не должна ломать разметку
    Telegram или подделывать жирный/ссылки;
  • вырезаем управляющие ASCII-символы (кроме \\t\\n\\r) — анти-мусор/анти-инъекция;
  • схлопываем пробелы (в keep_newlines=True сохраняем абзацы для летописи);
  • обрезаем до max_len с многоточием.

ВАЖНО про экранирование: sanitize_out НЕ делает esc-markdown — финальное
экранирование спецсимволов происходит на СТОРОНЕ ВСТАВКИ в bot/ (engine.textsafe.
esc_md), как и раньше. Здесь — только вычистка опасного/лишнего (defense in depth).
"""
import re

# символы, ломающие Markdown Telegram — вырезаем из текста модели (анти-инъекция)
_MD_INJECT = re.compile(r"[*_`\[\]]")
# управляющие ASCII, КРОМЕ таб/перенос/возврат (\x09 \x0a \x0d) — их сохраняем
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DEFAULT_MAX = 220


def sanitize_out(text, max_len: int = DEFAULT_MAX, keep_newlines: bool = False) -> str:
    """Обеззаразить и обрезать текст модели. Возвращает чистую строку (возможно
    пустую). keep_newlines=True — сохранить абзацные переносы (для летописи),
    иначе весь пробельный мусор схлопывается в один пробел."""
    if not text:
        return ""
    t = _MD_INJECT.sub("", str(text))
    t = _CTRL.sub("", t)
    if keep_newlines:
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
    else:
        t = re.sub(r"\s+", " ", t).strip()
    if max_len and len(t) > max_len:
        t = t[:max_len - 1].rstrip() + "…"
    return t
