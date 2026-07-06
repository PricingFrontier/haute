"""Isolated reproduction for V101.

Claim: ``SupersessionCoordinator._acquire_limiter_unless_superseded`` releases
the just-acquired limiter permit TWICE when, after
``asyncio.wait(..., FIRST_COMPLETED)``, BOTH ``acquire_task`` and
``superseded_task`` are in ``done``.

That both-``done`` state is reachable whenever, at the moment the limiter
acquire is attempted, (a) a permit is free (so ``Semaphore.acquire()`` returns
True without suspending) AND (b) the request is already superseded
(``generation != state.latest_generation``) AND (c) the condition lock is free
(so ``_wait_until_superseded`` returns without suspending). Both coroutines then
run to completion in a single event-loop step, so ``asyncio.wait`` returns with
both tasks done.

Buggy control flow (src/haute/routes/_supersession.py:182-196):
  L182 superseded_task in done -> L183 acquired True -> L184 limiter.release()
       (1st release), L185 acquired=False, L186 raise SupersededRequestError
  caught at L189: L190 drain acquire_task (already done, result True) ->
  L191 ``True is True and not False`` -> L192 acquired=True (NO memory that the
  permit was already released at L184) -> L194 True -> L195 limiter.release()
  (2nd release of the SAME permit).

Net: the semaphore's internal permit count is incremented by one, permanently
raising the per-key concurrency cap and defeating the bounded-worker guarantee.

ISOLATION: no disk, no network, no project files. We use a REAL
``asyncio.Semaphore(1)`` and a REAL ``_SupersessionState`` and drive the REAL
private coroutine ``_acquire_limiter_unless_superseded``. We assert on the
SPECIFIC wrong VALUE: the semaphore's permit count after the (failed,
superseded) acquire attempt -- it must be restored to exactly 1, but the bug
leaves it at 2 -- and we then demonstrate the cap is broken by acquiring twice
with no intervening release.
"""

from __future__ import annotations

import asyncio

from haute.routes._supersession import (
    SupersededRequestError,
    SupersessionCoordinator,
    _SupersessionState,
)


async def _drive_both_done() -> tuple[int, int]:
    """Drive the real private method in the both-``done`` window.

    Returns ``(value_before, value_after)`` for the real semaphore's permit
    count (``Semaphore._value``).
    """
    coordinator = SupersessionCoordinator()
    limiter = asyncio.Semaphore(1)

    # A real state whose latest_generation is AHEAD of the generation we pass
    # in: i.e. this request is already superseded. _wait_until_superseded will
    # therefore see ``generation (1) != latest_generation (2)`` immediately and
    # return WITHOUT awaiting condition.wait() -> its task completes in the same
    # event-loop step that the (permit-available) acquire task completes.
    state = _SupersessionState()
    state.latest_generation = 2
    stale_generation = 1

    value_before = limiter._value  # 1 permit free
    assert value_before == 1, value_before

    raised_superseded = False
    try:
        await coordinator._acquire_limiter_unless_superseded(
            limiter,
            state,
            stale_generation,
            "superseded",
        )
    except SupersededRequestError:
        raised_superseded = True

    # The method must signal supersession (it never "acquired" for the caller).
    assert raised_superseded, (
        "Expected SupersededRequestError because generation != latest_generation"
    )

    value_after = limiter._value
    return value_before, value_after


async def _confirm_cap_broken(limiter: asyncio.Semaphore) -> int:
    """A correctly-restored Semaphore(1) permits exactly ONE acquire without a
    release. Count how many back-to-back acquires succeed with NO release in
    between; >1 proves the per-key cap is broken (a leaked permit)."""
    successes = 0
    for _ in range(3):
        try:
            await asyncio.wait_for(limiter.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            break
        successes += 1
    return successes


async def main() -> None:
    value_before, value_after = await _drive_both_done()
    print(f"semaphore _value before attempt = {value_before}")
    print(f"semaphore _value after  attempt = {value_after}")

    # ---- Assert the SPECIFIC wrong VALUE (expected vs actual) ----
    # CORRECT behaviour: the acquire was rolled back exactly once, so the permit
    # count returns to its starting value of 1.
    # BUGGY behaviour: it is released twice, leaving _value == 2.
    assert value_after == 2, (
        "Repro premise failed: expected the BUGGY double-release leaving the "
        f"semaphore _value at 2, but got {value_after}. (Correct value is 1.)"
    )
    assert value_after != 1, (
        "If _value were restored to 1 the bug would already be fixed "
        "(single release)."
    )
    print(
        "\nBUG CONFIRMED (wrong value): a single superseded acquire attempt "
        "raised the Semaphore(1) permit count from 1 to 2 -- the permit was "
        "released twice (L184 then L195)."
    )

    # ---- Demonstrate the operational consequence: cap is broken ----
    # Reproduce the same leak on a fresh limiter so we can probe how many
    # concurrent acquires the now-corrupted Semaphore(1) grants with no release.
    leaked = asyncio.Semaphore(1)
    coordinator = SupersessionCoordinator()
    state = _SupersessionState()
    state.latest_generation = 2
    try:
        await coordinator._acquire_limiter_unless_superseded(
            leaked, state, 1, "superseded"
        )
    except SupersededRequestError:
        pass
    assert leaked._value == 2, leaked._value

    successes = await _confirm_cap_broken(leaked)
    print(f"back-to-back acquires granted with NO release in between = {successes}")
    assert successes == 2, (
        "Expected a Semaphore(1) corrupted to _value==2 to grant exactly 2 "
        f"concurrent acquires without release; got {successes}."
    )
    print(
        "CAP BROKEN: a bounded Semaphore(1) now admits 2 concurrent holders "
        "with no release between them -- the per-key concurrency bound is "
        "permanently raised by one for every time this race fires."
    )


if __name__ == "__main__":
    asyncio.run(main())
    print("\nALL ASSERTIONS PASSED -- V101 reproduced.")
