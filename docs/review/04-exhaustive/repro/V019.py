"""V019 reproduction — load_mlflow_model fast/slow cache-key split for run sources.

Claim under test
----------------
``load_mlflow_model`` builds the model cache key at TWO sites that DISAGREE for
``source_type="run"`` whenever a non-empty ``version`` is supplied:

* fast-path (src/haute/_mlflow_io.py:893-900) hardcodes ``version=""`` ->
  ``_model_cache_key`` returns the 4-tuple ``("run", run_id, artifact, task)``.
* slow-path (lines 968-974) passes ``version=resolved_version``; for a run
  source ``resolve_mlflow_source`` returns ``resolved_version = version``
  unchanged (_mlflow_utils.py:101,109-111).  With the production default
  ``version="latest"`` (truthy) the slow path stores the 5-tuple
  ``("run", run_id, "latest", artifact, task)``.

Production callers ALWAYS pass ``version=config.get("version","latest")`` even
for run sources (_builders.py:1196, _model_explainability.py:569, threaded via
ModelScorer.version -> _load_scoring_model_uncached at _model_scorer.py:1063).

Predicted CONSEQUENCE for catboost/rustystats run artifacts:
  call 1 (cold): fast 4-tuple miss; disk file absent -> slow path downloads,
                 loads, put(5-tuple).
  call 2:        fast 4-tuple miss AGAIN (model is under the 5-tuple); disk
                 file now present -> disk-cache fast path re-loads from disk
                 and put(4-tuple).

So for ONE logical model the loader (a) deserializes from disk TWICE, (b)
records TWO cache MISSES (instead of one miss + one hit), and (c) leaves a
permanently-orphaned 5-tuple entry, so the cache holds TWO entries where a
coherent cache holds ONE.

This script reproduces (a), (b) and (c) with explicit expected-vs-actual
asserts.  Everything is isolated: a tempdir is used for the on-disk cache, all
MLflow / loader boundaries are monkeypatched with in-memory stubs, and no
rating/, src/, tests/, or real project files are read or written.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import haute._mlflow_io as mio
from haute._mlflow_io import (
    ScoringModel,
    _artifact_cache_path,
    _model_cache,
    _model_cache_key,
    clear_model_cache,
    get_model_cache_stats,
    load_mlflow_model,
)

RUN_ID = "run_abc123"
ARTIFACT = "model.rsglm"  # rustystats flavor -> exercises the disk-cache fast path
TASK = "regression"
VERSION = "latest"  # the production default threaded through ModelScorer.version


def _make_sm(tag: str) -> ScoringModel:
    return ScoringModel(object(), ["a"], frozenset(), "rustystats")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="v019_"))
    prev_cwd = Path.cwd()
    # The fast path reads Path.cwd()/.cache/models, so we must run inside tmp.
    os.chdir(tmp)
    try:
        clear_model_cache()  # start from a clean cache + zeroed stats

        # Disk-cache identity the fast path will probe on call 2.
        cached_path = _artifact_cache_path(tmp / ".cache" / "models", RUN_ID, ARTIFACT)

        disk_load_calls: list[str] = []
        slow_load_calls: list[str] = []

        sm_slow = _make_sm("slow")  # produced by the slow path on the cold call
        sm_disk = _make_sm("disk")  # produced by the disk-cache fast path on call 2

        def fake_load_local_model(path: str, task: str = "regression") -> ScoringModel:
            # Stand-in for load_local_model (the disk-cache fast path loader).
            # Simulates the FIRST disk read by also materialising the cache file
            # so the subsequent call's `local_path.is_file()` probe succeeds.
            disk_load_calls.append(path)
            return sm_disk

        def fake_resolve(*, source_type, run_id, registered_model, version, tracking_uri):
            # Stand-in for resolve_mlflow_source: run source returns version
            # UNCHANGED (mirrors _mlflow_utils.py:101 + the run branch at 109-111).
            return (run_id, version, object(), object())

        def fake_bounded_retry(*, mlflow_mod, run_id, artifact, flavor, task):
            # Stand-in for the slow-path download+load. Materialise the on-disk
            # cache file as the real download would, so the next call's
            # disk-cache fast path finds it.
            slow_load_calls.append(artifact)
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            cached_path.write_bytes(b"rustystats bytes")
            return sm_slow

        with (
            patch.object(mio, "load_local_model", side_effect=fake_load_local_model),
            patch.object(mio, "resolve_mlflow_source", side_effect=fake_resolve),
            patch.object(mio, "_load_with_bounded_retry", side_effect=fake_bounded_retry),
        ):
            # ---- Call 1 (cold) ------------------------------------------------
            assert not cached_path.is_file(), "precondition: disk cache empty"
            r1 = load_mlflow_model(
                source_type="run",
                run_id=RUN_ID,
                artifact_path=ARTIFACT,
                version=VERSION,
                task=TASK,
            )
            # ---- Call 2 (warm; SAME logical model) ----------------------------
            r2 = load_mlflow_model(
                source_type="run",
                run_id=RUN_ID,
                artifact_path=ARTIFACT,
                version=VERSION,
                task=TASK,
            )

        # ---------------------------------------------------------------------
        # Observed behaviour
        # ---------------------------------------------------------------------
        four_tuple = _model_cache_key(
            source_type="run", run_id=RUN_ID, version="", artifact_path=ARTIFACT, task=TASK
        )
        five_tuple = _model_cache_key(
            source_type="run", run_id=RUN_ID, version=VERSION, artifact_path=ARTIFACT, task=TASK
        )
        stats = get_model_cache_stats()
        n_entries = len(_model_cache)

        print("four_tuple key       :", four_tuple)
        print("five_tuple key       :", five_tuple)
        print("cache entry count    :", n_entries)
        print("cache has 4-tuple    :", _model_cache.get(four_tuple) is not None)
        print("cache has 5-tuple    :", _model_cache.get(five_tuple) is not None)
        print("disk-cache loads     :", len(disk_load_calls))
        print("slow-path loads      :", len(slow_load_calls))
        print("stats                :", stats)
        print("call1 is slow object :", r1 is sm_slow)
        print("call2 is disk object :", r2 is sm_disk)

        # ---------------------------------------------------------------------
        # Asserts on the SPECIFIC wrong values predicted by the finding.
        # A coherent single-key cache would yield: entries==1, disk loads==0
        # (call 2 an in-memory hit), slow loads==1, stats hits==1/misses==1.
        # ---------------------------------------------------------------------

        # (1) Keys are genuinely disjoint: a 4-tuple and a distinct 5-tuple.
        assert len(four_tuple) == 4, f"expected 4-tuple, got {four_tuple!r}"
        assert len(five_tuple) == 5, f"expected 5-tuple, got {five_tuple!r}"
        assert four_tuple != five_tuple

        # (2) ORPHANED DUPLICATE: cache holds TWO entries for ONE logical model.
        assert n_entries == 2, (
            f"BUG NOT REPRODUCED: expected the orphaned-duplicate cache "
            f"(2 entries), got {n_entries}"
        )
        assert _model_cache.get(four_tuple) is sm_disk, "4-tuple should hold the disk reload"
        assert _model_cache.get(five_tuple) is sm_slow, "5-tuple should hold the slow-path load"

        # (3) REDUNDANT DISK LOAD: call 2 deserialised from disk instead of an
        #     in-memory hit -> exactly one redundant disk load occurred.
        assert len(slow_load_calls) == 1, f"expected 1 slow-path load, got {len(slow_load_calls)}"
        assert len(disk_load_calls) == 1, (
            f"BUG NOT REPRODUCED: expected 1 REDUNDANT disk reload on call 2, "
            f"got {len(disk_load_calls)} (a coherent cache would do 0)"
        )

        # (4) SKEWED COUNTERS: two MISSES recorded, ZERO hits, for one logical
        #     model.  A coherent cache records hits==1, misses==1.
        assert stats["misses"] == 2, (
            f"BUG NOT REPRODUCED: expected 2 cache misses for one logical model, "
            f"got misses={stats['misses']}"
        )
        assert stats["hits"] == 0, (
            f"BUG NOT REPRODUCED: expected 0 hits (a coherent cache records 1), "
            f"got hits={stats['hits']}"
        )

        # (5) The two calls returned DIFFERENT ScoringModel objects for the same
        #     logical model — direct evidence the fast path never reused the
        #     slow-path result.
        assert r1 is sm_slow and r2 is sm_disk and r1 is not r2

        print()
        print("V019 REPRODUCED: fast/slow cache-key split for run source with version='latest'.")
        print("  - cache holds 2 entries (4-tuple + orphaned 5-tuple) for ONE logical model")
        print("  - model deserialised from disk a 2nd time on the warm call")
        print("  - 2 misses / 0 hits recorded (coherent cache: 1 miss / 1 hit)")
        return 0
    finally:
        os.chdir(prev_cwd)
        # Best-effort cleanup of the tempdir; never touch project files.
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
        clear_model_cache()


if __name__ == "__main__":
    sys.exit(main())
