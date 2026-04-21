"""Thin adapter exposing ``LRUCache``-shaped introspection over a
:func:`functools.lru_cache`-decorated helper.

Background
----------

Phase 6 #134 migrated three memoisation sites
(:data:`haute._io._object_cache`,
:data:`haute._optimiser_io._artifact_cache`, and
:data:`haute._optimiser_io._mlflow_cache`) from the custom
:class:`haute._lru_cache.LRUCache` to the stdlib
:func:`functools.lru_cache`.

Before the migration, callers reached into the module-level cache
objects with ``len(_object_cache)`` (to assert a single cache entry
after two reads) and ``_object_cache.clear()`` (for per-test
isolation).  ``functools.lru_cache`` hangs that information off the
decorated function's ``cache_info()`` / ``cache_clear()`` methods,
which is a different surface.

:class:`FunctoolsLRUCacheAdapter` is a thin proxy that forwards
``__len__`` / ``clear()`` to the ``functools`` cache so pre-existing
tests and downstream callers keep working without rewrite.  It is
intentionally *not* a drop-in replacement for :class:`LRUCache` —
callers that need ``evict_where``, pinning, or subclassing still use
the custom class (the "category-B" callers in #134).
"""

from __future__ import annotations

from typing import Any


class FunctoolsLRUCacheAdapter:
    """Forward ``__len__`` and ``clear()`` to a functools-cached helper.

    The adapter stores a reference to the decorated function and
    translates the two LRUCache-era operations that callers still use.
    Everything else (hit/miss counters, maxsize) is reachable via
    ``adapter._cached_fn.cache_info()``.

    The ``cached_fn`` parameter is typed as :data:`typing.Any` because
    the concrete type, ``functools._lru_cache_wrapper``, is a private
    symbol in the standard library whose :class:`Protocol` shape is
    fragile across mypy versions.  The class only uses two well-known
    methods (``cache_info`` and ``cache_clear``) that any
    ``functools.lru_cache``-decorated function exposes.

    Example::

        import functools
        from haute._functools_cache_adapter import FunctoolsLRUCacheAdapter

        @functools.lru_cache(maxsize=16)
        def _load(key: str) -> object:
            ...

        # Expose the adapter at module level so tests can call
        # ``len(_cache)`` and ``_cache.clear()``.
        _cache = FunctoolsLRUCacheAdapter(_load)
    """

    __slots__ = ("_cached_fn",)

    def __init__(self, cached_fn: Any) -> None:
        self._cached_fn = cached_fn

    def __len__(self) -> int:
        return int(self._cached_fn.cache_info().currsize)

    def clear(self) -> None:
        self._cached_fn.cache_clear()

    def __repr__(self) -> str:
        info = self._cached_fn.cache_info()
        return (
            f"FunctoolsLRUCacheAdapter(currsize={info.currsize}, "
            f"maxsize={info.maxsize}, hits={info.hits}, misses={info.misses})"
        )
