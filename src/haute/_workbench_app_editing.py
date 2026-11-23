"""Who is editing each quote in the underwriter app (specs/workbench): one person at a time.

A quote opened to edit is held by the person editing it, a lease the page renews while it is
open (every ``EDIT_RENEW_SECONDS`` on the page) and lets go as it leaves; a lease not renewed
for :data:`EDIT_LEASE_SECONDS` lapses, so a closed laptop frees the quote. The same person
may hold it from several tabs. Leases are kept in the app's memory, so they are right while
the app runs as one process (one replica): a restart frees every quote. Nothing is lost
when one lapses early, as a save over a version the quote has moved past is refused anyway.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

#: How long a lease holds without being renewed. Several times the page's renewal, as a
#: browser runs a hidden tab's timers about once a minute.
EDIT_LEASE_SECONDS = 180.0

#: A quote by its reference and option.
QuoteKey = tuple[str, int]


@dataclass(frozen=True)
class EditLease:
    """*editor* holds the quote until *until* on the leases' clock."""

    editor: str
    until: float


class EditLeases:
    """The quotes being edited, each held by one person, on *clock* (seconds)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._held: dict[QuoteKey, EditLease] = {}

    def editor(self, quote: QuoteKey) -> str | None:
        """Who holds *quote* now, or None."""
        with self._lock:
            lease = self._live(quote)
            return None if lease is None else lease.editor

    def take(self, quote: QuoteKey, editor: str) -> str | None:
        """*quote* held by *editor* for another lease, taken when it is free or theirs: None
        once held, else who else holds it."""
        with self._lock:
            lease = self._live(quote)
            if lease is not None and lease.editor != editor:
                return lease.editor
            now = self._clock()
            # Lapsed leases are dropped as one is taken, so the leases never outgrow the
            # quotes being edited.
            for each in [key for key, held in self._held.items() if held.until <= now]:
                del self._held[each]
            self._held[quote] = EditLease(editor, now + EDIT_LEASE_SECONDS)
            return None

    def release(self, quote: QuoteKey, editor: str) -> None:
        """Let *quote* go, when *editor* holds it."""
        with self._lock:
            lease = self._held.get(quote)
            if lease is not None and lease.editor == editor:
                del self._held[quote]

    def other(self, quote: QuoteKey, actor: str) -> str | None:
        """Whoever but *actor* holds *quote* now, or None: what refuses *actor*'s change."""
        editor = self.editor(quote)
        return None if editor == actor else editor

    def _live(self, quote: QuoteKey) -> EditLease | None:
        # Under the lock.
        lease = self._held.get(quote)
        return lease if lease is not None and lease.until > self._clock() else None
