"""ISOLATED reproduction for BUG-EXEC-01.

Claim: In ``_execute_eager_core`` (src/haute/_execute_lazy.py), a node that
returns a per-port ``dict`` (a multi-port apiInput with 2+ emit-true tables)
records its timing in SECONDS:

    src/haute/_execute_lazy.py:1901-1902
        t1 = time.perf_counter()
        timings[nid] = round(t1 - t0, 6)
    ...
    src/haute/_execute_lazy.py:1907
        continue

while every other node path falls through to the canonical line:

    src/haute/_execute_lazy.py:2033
        timings[nid] = round((time.perf_counter() - t0) * 1000, 1)

which is in MILLISECONDS.  Because the dict branch ``continue``s, it never
reaches line 2033, so its ``timings[nid]`` value is ~1000x smaller than every
other node's in the same EagerResult.  The consumer copies it verbatim into the
GUI-facing ``timing_ms`` with no unit conversion:

    src/haute/executor.py:1238
        timing_ms=timings.get(nid, 0),

This repro is fully self-contained: it does NOT import haute, does NOT touch
src/ tests/ rating/ or any real project file.  It transcribes the two timing
assignments and the gating ``isinstance(result, dict)`` + ``continue`` exactly
as they appear in the source, drives them with (a) a deterministic fake clock
so the asserted wrong value is exact, and (b) the real wall clock to show the
defect is not an artifact of the fake clock.  It asserts on the specific wrong
value: an 800 ms multi-port node is reported as ~0.8 ms.

Run:  uv run python review/03-simplification/repro/execution__BUG-EXEC-01.py
Exit 0  => claim reproduced (units silently disagree, ~1000x under-report).
Exit 1  => claim refuted (both node types recorded the same unit).
"""

from __future__ import annotations

import time


# ---------------------------------------------------------------------------
# Verbatim transcription of the timing logic from _execute_eager_core.
# The ONLY behaviour under test is which of the two assignment lines runs for
# each result shape, and what unit each line produces.  Nothing here is a
# simplification of that decision: ``isinstance(result, dict)`` -> seconds +
# ``continue``; otherwise -> milliseconds.
# ---------------------------------------------------------------------------
def record_timing(result: object, t0: float, perf_counter) -> float:
    """Return the value written to ``timings[nid]`` for one node iteration.

    Mirrors src/haute/_execute_lazy.py:1868-2033 (only the timing-relevant
    statements are kept; column/contract bookkeeping is irrelevant to units).
    """
    timings: dict[str, float] = {}
    nid = "node-under-test"

    if isinstance(result, dict):
        # --- src/haute/_execute_lazy.py:1901-1902 (multi-port branch) ---
        t1 = perf_counter()
        timings[nid] = round(t1 - t0, 6)  # SECONDS
        # --- src/haute/_execute_lazy.py:1907 ---
        # ``continue`` in the real loop: skips the canonical line below.
        return timings[nid]

    # --- src/haute/_execute_lazy.py:2033 (all other node paths) ---
    timings[nid] = round((perf_counter() - t0) * 1000, 1)  # MILLISECONDS
    return timings[nid]


def consumer_timing_ms(timings_value: float) -> float:
    """Mirror src/haute/executor.py:1238 — verbatim, no unit conversion.

        timing_ms=timings.get(nid, 0),
    """
    timings = {"node-under-test": timings_value}
    return timings.get("node-under-test", 0)


# ---------------------------------------------------------------------------
# A deterministic fake perf_counter: each call advances by a fixed wall-clock
# delta so we can assert on the EXACT recorded value rather than a flaky range.
# A node iteration calls perf_counter() once for t0 and once for the timing
# line, so a 2-step clock with step = duration gives a precise elapsed time.
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self, *ticks_seconds: float) -> None:
        self._ticks = list(ticks_seconds)
        self._i = 0

    def __call__(self) -> float:
        val = self._ticks[self._i]
        self._i += 1
        return val


def main() -> int:
    REAL_DURATION_S = 0.800  # the node genuinely took 800 ms of wall time

    # ----- Deterministic clock: t0 = 100.000s, end = 100.800s (=> 800 ms) -----
    # Multi-port (dict) result.
    mp_clock = FakeClock(100.000, 100.000 + REAL_DURATION_S)
    mp_t0 = mp_clock()  # the loop's ``t0 = time.perf_counter()`` (line 1780)
    multiport_result = {"orders": object(), "customers": object()}  # 2 ports
    mp_recorded = record_timing(multiport_result, mp_t0, mp_clock)

    # Single-port (DataFrame-shaped) result, identical 800 ms duration.
    sp_clock = FakeClock(200.000, 200.000 + REAL_DURATION_S)
    sp_t0 = sp_clock()
    singleport_result = object()  # not a dict -> falls through to line 2033
    sp_recorded = record_timing(singleport_result, sp_t0, sp_clock)

    # What the GUI actually receives as NodeResult.timing_ms:
    mp_timing_ms = consumer_timing_ms(mp_recorded)
    sp_timing_ms = consumer_timing_ms(sp_recorded)

    print("=== BUG-EXEC-01: multi-port apiInput timing unit mismatch ===")
    print(f"real node duration         : {REAL_DURATION_S * 1000:.1f} ms")
    print(f"multi-port timings[nid]    : {mp_recorded!r}   (line 1902, SECONDS)")
    print(f"single-port timings[nid]   : {sp_recorded!r}   (line 2033, MILLISECONDS)")
    print(f"multi-port  -> timing_ms   : {mp_timing_ms!r}")
    print(f"single-port -> timing_ms   : {sp_timing_ms!r}")
    ratio = sp_timing_ms / mp_timing_ms if mp_timing_ms else float("inf")
    print(f"single/multi timing_ms ratio: {ratio:.1f}x (same real duration!)")

    # ---- Specific-value assertions ----
    # Single-port: 800 ms reported correctly as 800.0.
    assert sp_timing_ms == 800.0, f"expected 800.0 ms, got {sp_timing_ms!r}"
    # Multi-port: 800 ms node reported as 0.8 (seconds value reused as "ms").
    assert mp_timing_ms == 0.8, (
        f"expected the bug's wrong value 0.8 for an 800ms multi-port node, "
        f"got {mp_timing_ms!r}"
    )
    # The two node types disagree by ~1000x for the SAME real duration.
    assert abs(ratio - 1000.0) < 1e-6, f"expected ~1000x disagreement, got {ratio}"

    # ----- Real-clock cross-check: defect is not a fake-clock artifact -----
    # Spin until ~30 ms of genuine wall time elapses, timing it both ways.
    rt0 = time.perf_counter()
    while time.perf_counter() - rt0 < 0.030:
        pass
    real_mp = record_timing({"a": 1, "b": 2}, rt0, time.perf_counter)
    rt0b = time.perf_counter()
    while time.perf_counter() - rt0b < 0.030:
        pass
    real_sp = record_timing(object(), rt0b, time.perf_counter)
    print(f"\nreal-clock multi-port value : {real_mp!r}  (seconds)")
    print(f"real-clock single-port value: {real_sp!r}  (milliseconds)")
    # ~30 ms => seconds value ~0.03, ms value ~30.  Multi-port is wildly low.
    assert real_mp < 1.0, f"multi-port real value should be sub-1 (seconds): {real_mp}"
    assert real_sp > 10.0, f"single-port real value should be tens of ms: {real_sp}"
    assert real_sp > real_mp * 100, (
        f"real-clock disagreement too small: sp={real_sp}, mp={real_mp}"
    )

    print(
        "\nRESULT: REPRODUCED — a multi-port apiInput that took 800 ms is "
        "reported as 0.8 ms (timing_ms), a ~1000x under-report, while every "
        "other node in the same response reports milliseconds. No error raised."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
