"""Small per-engine schema diagnostic cache; never caches database liveness."""
import threading
import time
from weakref import WeakKeyDictionary

from sqlalchemy import inspect
from sqlalchemy.exc import NoSuchTableError

TTL_SECONDS = 60
_cache = WeakKeyDictionary()
_lock = threading.Lock()


def schema_columns(engine, *, connection=None):
    with _lock:
        now = time.monotonic()
        cached = _cache.get(engine)
        if cached is not None and 0 <= now - cached[0] < TTL_SECONDS:
            return {name: list(columns) for name, columns in cached[1].items()}
        inspector = inspect(connection if connection is not None else engine)
        result = {}
        for name in ('users', 'notion_integrations'):
            try:
                result[name] = tuple(sorted(column['name'] for column in inspector.get_columns(name)))
            except NoSuchTableError:
                result[name] = ()
        # Failed reflection is never installed as a healthy cached value.
        _cache[engine] = (now, result)
        return {name: list(columns) for name, columns in result.items()}


def invalidate(engine=None):
    with _lock:
        if engine is None:
            _cache.clear()
        else:
            _cache.pop(engine, None)
