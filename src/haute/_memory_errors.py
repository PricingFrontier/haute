"""Finding the out-of-memory failure behind a translated exception."""

from __future__ import annotations

import errno
import re
import sys

# Windows' refusals of an allocation: ERROR_NOT_ENOUGH_MEMORY, ERROR_OUTOFMEMORY and
# ERROR_COMMITMENT_LIMIT (the paging file too small for the commit).
_WINDOWS_OUT_OF_MEMORY = frozenset({8, 14, 1455})
# The text a native library gives the same refusal, a Polars error's included: the
# operating system's own message, or its error number as Rust prints it.
# The base error types of the native libraries whose errors carry the operating system's
# own text, by the module that defines each and its name there.
_NATIVE_ERROR_TYPES = (("polars.exceptions", "PolarsError"), ("pyarrow.lib", "ArrowException"))
_OUT_OF_MEMORY_TEXT = re.compile(
    r"cannot allocate memory|not enough memory|out of memory|\(os error (?:12|8|14|1455)\)",
    re.IGNORECASE,
)


def memory_error_in(exc: BaseException) -> MemoryError | None:
    """Return the ``MemoryError`` behind *exc*, however many translations wrap it.

    A native cap's refusal surfaces as ``MemoryError`` wherever the allocation
    happened, and the layers above often re-raise it as their own error type.
    An allocation the operating system refuses arrives instead as an ``OSError``
    (``ENOMEM``, or Windows' commit limit) or as a native library's error carrying
    that refusal's text, as Polars gives "Cannot allocate memory (os error 12)":
    for one of those a ``MemoryError`` standing for it is returned, caused by it.
    Code that records failures as diagnostics or as generic errors checks this
    first, so running out of memory is never mistaken for anything else.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, MemoryError):
            return current
        if _refused_allocation(current):
            standing = MemoryError(str(current))
            standing.__cause__ = current
            return standing
        seen.add(id(current))
        # ``raise ... from None`` says the failure is not the one it was raised during.
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return None


def _refused_allocation(exc: BaseException) -> bool:
    if isinstance(exc, OSError) and (
        exc.errno == errno.ENOMEM or getattr(exc, "winerror", None) in _WINDOWS_OUT_OF_MEMORY
    ):
        return True
    # Only a native library's errors carry the operating system's text: a Haute error or
    # a user's own exception that mentions memory is not an allocation refused.
    return isinstance(exc, _native_error_types()) and bool(_OUT_OF_MEMORY_TEXT.search(str(exc)))


def _native_error_types() -> tuple[type[BaseException], ...]:
    """The native libraries' error types, of those loaded: an error of a library not loaded
    cannot have been raised, so the check imports nothing."""
    found = (getattr(sys.modules.get(module), name, None) for module, name in _NATIVE_ERROR_TYPES)
    return tuple(kind for kind in found if isinstance(kind, type))
