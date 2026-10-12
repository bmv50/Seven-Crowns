"""Ephemeral reply routing only; never stores or changes game state."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re

_SOURCE = ContextVar('max_reply_source', default=None)


def enabled():
    return os.environ.get('MAX_EDIT_CARDS', '1').strip().lower() in ('1', 'true', 'yes', 'on')


def valid_mid(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', value))


@contextmanager
def reply_scope(external_id, mid):
    token = _SOURCE.set((external_id, mid if valid_mid(mid) else None))
    try:
        yield
    finally:
        _SOURCE.reset(token)


def target(external_id):
    source = _SOURCE.get()
    return source[1] if enabled() and source and source[0] == external_id else None


def is_input(external_id):
    source = _SOURCE.get()
    return bool(source and source[0] == external_id)


def key(ch):
    return f'{ch.generation}:{ch.room}'
