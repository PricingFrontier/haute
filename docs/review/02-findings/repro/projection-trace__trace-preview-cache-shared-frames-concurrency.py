"""Adversarial verification of claim
'trace-preview-cache-shared-frames-concurrency'.

The claim asserts (a definitively-NEGATIVE / "no bug" finding):

  1. FingerprintCache.try_get returns a fresh top-level dict but SHARES the
     inner Polars DataFrame objects across callers.
  2. execute_trace stores into trace.py's OWN module-level _cache and only
     READS the injected preview cache via try_get -- it never calls
     store/put/pin/evict on the injected preview. Therefore the Phase-0
     sub-concern 'a trace could evict/replace a preview entry the executor
     still relies on' cannot occur because they are SEPARATE instances.
  3. _fix_upstream_values mutates only the freshly-correlated Python
     output_values dict, never the cached/shared DataFrame, so concurrent
     reads of the shared immutable frame are safe.

This script empirically validates each load-bearing property and ASSERTS on
concrete expected-vs-actual values. If the safety reasoning were WRONG, one
of these assertions would fail.

ISOLATION: pure in-memory synthetic data, no project files touched.
"""

from __future__ import annotations

import threading

import polars as pl

from haute._fingerprint_cache import FingerprintCache
from haute import trace as trace_mod
from haute import executor as executor_mod
from haute._trace_enrichment import _fix_upstream_values
from haute.trace import TraceStep
from haute._trace_correlation import SchemaDiff


def _new_step(node_id: str, node_name: str, output_values: dict) -> TraceStep:
    return TraceStep(
        node_id=node_id,
        node_name=node_name,
        node_type="generic",
        schema_diff=SchemaDiff(
            columns_added=[],
            columns_removed=[],
            columns_modified=[],
            columns_passed=[],
        ),
        input_values={},
        output_values=output_values,
    )


def prop1_try_get_shares_inner_frames() -> None:
    """try_get returns a FRESH top dict but the SAME DataFrame object."""
    cache = FingerprintCache(slots=("eager_outputs", "order"))
    df = pl.DataFrame({"a": [1, 2, 3]})
    cache.store("fp", eager_outputs={"n1": df}, order=["n1"])

    got1 = cache.try_get("fp")
    got2 = cache.try_get("fp")
    assert got1 is not None and got2 is not None

    # Top-level slot dict is a fresh copy each call.
    assert got1 is not got2, "expected distinct top-level dicts"
    # Inner DataFrame is shared by identity (the documented design).
    assert got1["eager_outputs"]["n1"] is df, "inner frame must be the cached object"
    assert got1["eager_outputs"]["n1"] is got2["eager_outputs"]["n1"], (
        "two try_get calls must share the SAME frame object"
    )
    print("PROP1 ok: try_get shares inner frames (fresh top dict, shared frame)")


def prop2_trace_and_preview_are_separate_instances() -> None:
    """The trace cache and preview cache are distinct objects with distinct slots."""
    assert trace_mod._cache is not executor_mod._preview_cache, (
        "trace cache and preview cache must be SEPARATE instances"
    )
    # Distinct slot sets prove they are not accidentally aliased.
    assert trace_mod._cache._slots == (
        "eager_outputs",
        "order",
        "parents_of",
        "node_map",
        "source_ids",
    )
    assert "errors" in executor_mod._preview_cache._slots
    assert "parents_of" not in executor_mod._preview_cache._slots
    # The injected-preview consumer in trace only ever calls try_get; a
    # store into the trace cache must NOT appear in the preview cache.
    df = pl.DataFrame({"x": [9]})
    trace_mod._cache.store(
        "iso-fp",
        eager_outputs={"t": df},
        order=["t"],
        parents_of={},
        node_map={},
        source_ids=set(),
    )
    try:
        assert trace_mod._cache.try_get("iso-fp") is not None
        # Storing in the trace cache leaves the preview cache untouched.
        assert executor_mod._preview_cache.try_get("iso-fp") is None, (
            "writing trace cache must not write/evict the preview cache"
        )
    finally:
        trace_mod._cache.invalidate()
    print("PROP2 ok: trace cache and preview cache are independent instances")


def prop3_resolve_preview_only_reads_via_try_get() -> None:
    """A reader stub that explodes on any mutating method survives a trace
    preview-resolution -- proving only try_get is invoked on the injected
    preview object."""
    calls: list[str] = []
    df = pl.DataFrame({"a": [1]})
    snapshot = {"eager_outputs": {"n1": df}}

    class ReadOnlyTripwire:
        def try_get(self, fp: str):
            calls.append(("try_get", fp))
            return snapshot if fp == "match" else None

        # Any of these being called would be a structural violation.
        def store(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("preview.store called -- claim violated")

        def put(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("preview.put called -- claim violated")

        def pin(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("preview.pin called -- claim violated")

        def evict(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("preview.evict called -- claim violated")

        def invalidate(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("preview.invalidate called -- claim violated")

        def update_slot(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("preview.update_slot called -- claim violated")

    resolved = trace_mod._resolve_preview_snapshot(
        ReadOnlyTripwire(), ["nope", "match"]
    )
    assert resolved is not None
    data, matched = resolved
    assert matched == "match"
    assert data["eager_outputs"]["n1"] is df
    # Only try_get was ever touched.
    assert all(name == "try_get" for name, _ in calls)
    assert calls == [("try_get", "nope"), ("try_get", "match")]
    print("PROP3 ok: injected preview is read-only (only try_get invoked)")


def prop4_fix_upstream_values_does_not_mutate_shared_frame() -> None:
    """_fix_upstream_values updates a Python output_values dict, never the
    shared DataFrame. Verify the frame is byte-for-byte unchanged and that
    the step's dict (not the frame) carries the corrected value."""
    df = pl.DataFrame({"price": [10.0, 20.0, 30.0]})
    before = df.clone()  # snapshot for equality comparison

    # Source step erroneously has a null for 'price' (post-hoc correlator
    # matched the wrong row). The known-good value is 20.0.
    src_step = _new_step("src_id", "SourceNode", {"price": None})
    steps = [src_step]
    input_sources = {
        "price": {
            "node_name": "SourceNode",
            "result_value": 20.0,
        }
    }
    eager_outputs = {"src_id": df}

    _fix_upstream_values(input_sources, steps, eager_outputs)

    # The Python dict on the step got corrected...
    assert src_step.output_values["price"] == 20.0, (
        f"expected fixup to set 20.0, got {src_step.output_values['price']!r}"
    )
    # ...but the shared DataFrame is COMPLETELY untouched (same object,
    # identical data). This is the crux of the concurrency-safety claim.
    assert eager_outputs["src_id"] is df, "frame object identity must be preserved"
    assert df.equals(before), "shared frame must not be mutated by fixup"
    assert df.to_dicts() == [
        {"price": 10.0},
        {"price": 20.0},
        {"price": 30.0},
    ]
    print("PROP4 ok: _fix_upstream_values mutates step dict, not the shared frame")


def prop5_concurrent_shared_frame_reads_are_consistent() -> None:
    """Under HAUTE_TRACE_MAX_CONCURRENCY>1 two traces over the same
    fingerprint read the SAME frame object. Simulate concurrent readers of a
    shared cached frame doing the same read-only operations the trace does
    (.row, .filter) and assert every thread sees identical, correct values
    and the frame is unchanged afterwards."""
    cache = FingerprintCache(slots=("eager_outputs", "order"))
    df = pl.DataFrame({"id": list(range(1000)), "v": [i * 2 for i in range(1000)]})
    cache.store("fp", eager_outputs={"n": df}, order=["n"])

    before = df.clone()
    results: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait()
            for _ in range(200):
                got = cache.try_get("fp")
                frame = got["eager_outputs"]["n"]
                # Same read-only ops the trace performs.
                row = frame.row(500, named=True)
                matched = frame.filter(pl.col("v") == 1000)
                assert row == {"id": 500, "v": 1000}
                assert matched.row(0, named=True) == {"id": 500, "v": 1000}
            results.append(1)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent readers saw inconsistency: {errors!r}"
    assert len(results) == 8
    # Shared frame survived concurrent reads unchanged.
    assert df.equals(before)
    print("PROP5 ok: concurrent shared-frame reads are consistent and non-mutating")


if __name__ == "__main__":
    prop1_try_get_shares_inner_frames()
    prop2_trace_and_preview_are_separate_instances()
    prop3_resolve_preview_only_reads_via_try_get()
    prop4_fix_upstream_values_does_not_mutate_shared_frame()
    prop5_concurrent_shared_frame_reads_are_consistent()
    print(
        "\nALL PROPERTIES HOLD: the documented 'trace evicts a preview entry' "
        "concern does NOT occur (separate instances; injected preview is "
        "read-only; shared frames are read-only and immutable). CLAIM SUPPORTED."
    )
