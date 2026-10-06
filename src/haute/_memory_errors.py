"""Finding the ``MemoryError`` behind a translated exception."""

from __future__ import annotations


def memory_error_in(exc: BaseException) -> MemoryError | None:
    """Return a ``MemoryError`` behind *exc*, however many translations wrap it.

    A native cap's refusal surfaces as ``MemoryError`` wherever the allocation
    happened, and the layers above often re-raise it as their own error type.
    Code that records failures as diagnostics or as generic errors checks this
    first, so running out of memory is never mistaken for anything else.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, MemoryError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None
