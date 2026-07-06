"""Adversarial repro for claim: dataframe-cache-artifact-unlink-vs-late-finalize.

Two coupled hazards alleged:

(1) PREMATURE UNLINK. scan(key)->lf_a; build derived plan d=lf_a.select(...).head(1);
    DROP lf_a (del + gc.collect) so the original scan LazyFrame is gone; force the
    cache entry to be evicted (LRU max_entries). The claim says the artifact is then
    unlinked while d is still pending collection, so d.collect() raises
    FileNotFoundError / CacheArtifactMissing.

(2) LEAK / WINDOWS. If finalize never runs the file leaks; and on Windows, if the
    parquet is mmapped open, unlink during invalidate() can raise PermissionError that
    _release_scan / _remove_key do not guard (path.unlink(missing_ok=True) only swallows
    ENOENT).

We test the *behaviour* the claim predicts and ASSERT expected-vs-actual.
"""

from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path

import polars as pl

import haute._sandbox as _sandbox
from haute._dataframe_execution_cache import (
    DataFrameExecutionCache,
    DataFrameExecutionCacheKey,
    dataframe_execution_policy_fingerprint,
)
from haute._polars_utils import bounded_sink, read_parquet_metadata


def _make_key(node_id: str, fingerprint: str) -> DataFrameExecutionCacheKey:
    policy_fp = dataframe_execution_policy_fingerprint({"profile": "lazy"})
    return DataFrameExecutionCacheKey(
        cache_key=f"dfexec:test:{node_id}:{fingerprint}",
        namespace="ns",
        node_id=node_id,
        lineage_fingerprint=f"lin:{fingerprint}",
        source="live",
        profile="lazy",
        input_fingerprint=f"in:{fingerprint}",
        execution_policy_fingerprint=policy_fp,
    )


def _materialize(cache: DataFrameExecutionCache, key: DataFrameExecutionCacheKey, df: pl.DataFrame) -> Path:
    """Materialize df into the cache under key. Returns the artifact path."""
    with cache.materialization_lock(key):
        path = cache.path_for_key(key)
        bounded_sink(df.lazy(), path, fast_checkpoint=True)
        metadata = read_parquet_metadata(path)
        entry = cache.store_artifact(key, path, metadata)
    return entry.path


def hazard1_premature_unlink(tmp: Path) -> dict:
    """Returns dict with the observed behaviour for hazard 1."""
    cache = DataFrameExecutionCache(root=tmp / "cache1", max_entries=2)

    key_a = _make_key("A", "a")
    path_a = _materialize(cache, key_a, pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}))

    # scan A -> lf_a (this pins the key + registers finalize)
    lf_a = cache.scan(key_a)
    assert lf_a is not None

    # Build a DERIVED plan from the scan; then DROP the original scan LazyFrame.
    derived = lf_a.select("x").head(2)
    del lf_a
    gc.collect()  # finalize on lf_a runs here -> _release_pinned_scan -> _release_scan

    # Force eviction of A by materializing B and C (max_entries=2).
    key_b = _make_key("B", "b")
    key_c = _make_key("C", "c")
    _materialize(cache, key_b, pl.DataFrame({"x": [4], "y": [40]}))
    _materialize(cache, key_c, pl.DataFrame({"x": [5], "y": [50]}))

    # A's entry should now be evicted from the LRU (2 newer entries pushed it out).
    entry_a_after = cache.get(key_a)
    artifact_exists = path_a.exists()

    # Now collect the derived plan. The claim: this raises FileNotFoundError because
    # the artifact was unlinked at A's eviction (no live scan after del lf_a).
    collect_error = None
    collect_value = None
    try:
        collect_value = derived.collect()
    except Exception as exc:  # noqa: BLE001 - we want to classify it
        collect_error = exc

    return {
        "entry_a_evicted": entry_a_after is None,
        "artifact_exists_after_evict": artifact_exists,
        "collect_error": collect_error,
        "collect_value": collect_value,
        "path_a": path_a,
    }


def hazard2_windows_permissionerror(tmp: Path) -> dict:
    """Hold lf_a, force an mmap-open reader, then invalidate() and observe."""
    cache = DataFrameExecutionCache(root=tmp / "cache2", max_entries=4)
    key_a = _make_key("A", "win")
    path_a = _materialize(cache, key_a, pl.DataFrame({"x": list(range(1000)), "y": list(range(1000))}))

    lf_a = cache.scan(key_a)
    assert lf_a is not None

    # Force polars to actually open / memory-map the parquet by collecting once and
    # keeping a derived lazy frame that references the file. Also open a raw mmap to
    # maximally reproduce the Windows file-sharing hazard.
    _ = lf_a.collect()  # touches the file
    held_lazy = lf_a.select("x")  # derived plan still referencing the path

    raw_handle = open(path_a, "rb")  # keep an OS handle open (sharing-violation source on win32)

    invalidate_error = None
    artifact_after = None
    try:
        cache.invalidate()  # clear() -> _remove_key -> path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        invalidate_error = exc
    finally:
        artifact_after = path_a.exists()

    # cleanup
    raw_handle.close()
    del lf_a, held_lazy
    gc.collect()

    return {
        "invalidate_error": invalidate_error,
        "artifact_exists_after_invalidate": artifact_after,
        "path_a": path_a,
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _sandbox.set_project_root(tmp)

        print("=" * 70)
        print("HAZARD 1: premature unlink of derived-plan artifact")
        print("=" * 70)
        h1 = hazard1_premature_unlink(tmp)
        print(f"  entry A evicted from LRU:        {h1['entry_a_evicted']}")
        print(f"  artifact still on disk after evict: {h1['artifact_exists_after_evict']}")
        if h1["collect_error"] is not None:
            print(f"  derived.collect() RAISED:        {type(h1['collect_error']).__name__}: {h1['collect_error']}")
        else:
            print(f"  derived.collect() SUCCEEDED:     {h1['collect_value'].to_dict(as_series=False)}")

        # The claim predicts: artifact unlinked at eviction AND collect raises
        # FileNotFoundError-like error. Verify expected-vs-actual.
        claim1_reproduced = (
            h1["collect_error"] is not None
            and isinstance(h1["collect_error"], (FileNotFoundError, OSError))
            and not h1["artifact_exists_after_evict"]
        )
        print(f"  >>> HAZARD-1 PREDICTION (collect fails due to unlinked artifact): {claim1_reproduced}")

        # Expected-correct behaviour: derived plan collects the right value.
        expected_value = {"x": [1, 2]}
        if h1["collect_error"] is None:
            actual = h1["collect_value"].to_dict(as_series=False)
            print(f"  correctness check: expected {expected_value}, got {actual} -> "
                  f"{'OK' if actual == expected_value else 'WRONG VALUE'}")

        print()
        print("=" * 70)
        print("HAZARD 2: Windows PermissionError on invalidate with open mmap")
        print("=" * 70)
        h2 = hazard2_windows_permissionerror(tmp)
        if h2["invalidate_error"] is not None:
            print(f"  invalidate() RAISED:             {type(h2['invalidate_error']).__name__}: {h2['invalidate_error']}")
        else:
            print("  invalidate() did NOT raise")
        print(f"  artifact still on disk after invalidate: {h2['artifact_exists_after_invalidate']}")
        print(f"  platform: {sys.platform}")

        claim2_reproduced = isinstance(h2["invalidate_error"], PermissionError)
        print(f"  >>> HAZARD-2 PREDICTION (PermissionError propagates from invalidate): {claim2_reproduced}")

        print()
        print("=" * 70)
        print("VERDICT INPUTS")
        print("=" * 70)
        print(f"  hazard1_reproduced = {claim1_reproduced}")
        print(f"  hazard2_reproduced = {claim2_reproduced}")
        if claim1_reproduced or claim2_reproduced:
            print("  RESULT: at least one hazard REPRODUCED")
            return 1
        print("  RESULT: NEITHER hazard reproduced (claim not substantiated by this repro)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
