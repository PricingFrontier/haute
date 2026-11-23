"""Who is editing each quote in the underwriter app (specs/workbench): one person at a time.

A quote opened to edit is held by the person editing it, a lease each of their pages takes
under a session of its own, renews while it is open (every ``EDIT_RENEW_MS`` on the page) and
lets go as it leaves; a session not renewed for :data:`EDIT_LEASE_SECONDS` lapses, so a
closed laptop frees the quote. The same person may hold it from several tabs, each a session:
the quote is theirs until the last is let go or lapses. A save or a change holds the quote
for its actor while it is made (:meth:`EditLeases.writing`), so no one else takes it between
the check and the write. Leases are kept in the app's memory, so they are right while the
app runs as one process (one replica): a restart frees every quote. Nothing is lost when one
lapses early, as a save over a version the quote has moved past is refused anyway.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

#: How long a session holds without being renewed. Several times the page's renewal, as a
#: browser runs a hidden tab's timers about once a minute.
EDIT_LEASE_SECONDS = 180.0

#: A quote by its reference and option.
QuoteKey = tuple[str, int]


class EditHeldError(Exception):
    """Someone else, *editor*, holds the quote."""

    def __init__(self, editor: str) -> None:
        super().__init__(editor)
        self.editor = editor


@dataclass
class _Held:
    """A quote held by *editor*: each page's session with when it lapses on the leases'
    clock, and the writes of theirs being made."""

    editor: str
    sessions: dict[str, float] = field(default_factory=dict)
    writes: int = 0


class EditLeases:
    """The quotes being edited, each held by one person, on *clock* (seconds)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._held: dict[QuoteKey, _Held] = {}

    def editor(self, quote: QuoteKey) -> str | None:
        """Who holds *quote* now, or None."""
        with self._lock:
            held = self._live(quote)
            return None if held is None else held.editor

    def take(self, quote: QuoteKey, editor: str, session: str) -> str | None:
        """*quote* held by *editor*'s *session* for another lease, taken when it is free or
        theirs: None once held, else who else holds it."""
        with self._lock:
            held = self._live(quote)
            if held is not None and held.editor != editor:
                return held.editor
            # Lapsed leases are dropped as one is taken, so the leases never outgrow the
            # quotes being edited.
            for each in list(self._held):
                self._live(each)
            if held is None:
                held = self._held[quote] = _Held(editor)
            held.sessions[session] = self._clock() + EDIT_LEASE_SECONDS
            return None

    def release(self, quote: QuoteKey, editor: str, session: str) -> None:
        """Let *editor*'s *session* of *quote* go: the quote stays theirs while another of
        their sessions holds it."""
        with self._lock:
            held = self._held.get(quote)
            if held is not None and held.editor == editor:
                held.sessions.pop(session, None)
                self._live(quote)

    @contextmanager
    def writing(self, quote: QuoteKey, actor: str) -> Iterator[None]:
        """*quote* held for *actor* while a write of theirs is made, whether or not they are
        editing it: :class:`EditHeldError` when someone else holds it, and no one else takes
        it until the write ends."""
        with self._lock:
            held = self._live(quote)
            if held is not None and held.editor != actor:
                raise EditHeldError(held.editor)
            if held is None:
                held = self._held[quote] = _Held(actor)
            held.writes += 1
        try:
            yield
        finally:
            with self._lock:
                held.writes -= 1
                self._live(quote)

    def _live(self, quote: QuoteKey) -> _Held | None:
        """The hold on *quote* while a session of it has not lapsed or a write is being made;
        its lapsed sessions dropped, and the hold with them once nothing is left. Under the
        lock."""
        held = self._held.get(quote)
        if held is None:
            return None
        now = self._clock()
        for session in [name for name, until in held.sessions.items() if until <= now]:
            del held.sessions[session]
        if held.sessions or held.writes > 0:
            return held
        del self._held[quote]
        return None
