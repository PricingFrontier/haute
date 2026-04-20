"""Pinning tests for issue #98 — MLflow model cache observability.

Today, ``haute._mlflow_io.load_mlflow_model`` caches models in a
module-level LRU (``_model_cache``) but emits limited structured logs on
hits and nothing on misses, with no runtime-queryable metric of cache
effectiveness.  That leaves production on-call blind to cache thrash.

These tests pin the observability contract:

* **model_cache_miss** log event on a miss, with ``run_id``,
  ``artifact_path``, and ``flavor`` as structured fields (not just a
  stringified tuple key).
* **model_cache_hit** log event on a hit, with the same structured fields.
* A **``get_model_cache_stats()``** function exposing ``{"hits": N,
  "misses": M}`` for scrape-style metrics / debug endpoints.
* The counters are **monotonic**: repeated hits increment ``hits``;
  distinct misses increment ``misses``.
* **Reset semantics**: ``clear_model_cache()`` resets the counters.  We
  pin this choice (rather than "counters survive a clear") because the
  caller's mental model of "clear everything cache-related" is more
  intuitive than "clear data but keep stats".

Tests-only.  If any of these fail, the production change lands in a
sibling commit by the developer agent.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_mlflow_model_cache():
    """Reset the module-level model cache (and stats, if implemented)."""
    from haute._mlflow_io import _model_cache

    _model_cache.clear()
    # If the stats live on a separate counter object, clear_model_cache()
    # is the intended reset surface — call it so tests start from (0, 0).
    try:
        from haute._mlflow_io import clear_model_cache

        clear_model_cache()
    except Exception:
        # Cache dir may not exist in test env — that's fine; _model_cache.clear()
        # above is the load-bearing reset.
        pass
    yield
    _model_cache.clear()


@pytest.fixture()
def mock_mlflow_env():
    """Mock mlflow modules + resolve helpers shared across tests."""
    mock_mlflow = MagicMock()
    mock_mlflow.artifacts.download_artifacts.return_value = "/tmp/model.cbm"

    mock_client_instance = MagicMock()
    mock_mlflow_tracking = MagicMock()
    mock_mlflow_tracking.MlflowClient.return_value = mock_client_instance

    modules_patch = patch.dict(
        sys.modules,
        {"mlflow": mock_mlflow, "mlflow.tracking": mock_mlflow_tracking},
    )
    resolve_patch = patch(
        "haute.modelling._mlflow_log.resolve_tracking_backend",
        return_value=("file:///mlruns", "local"),
    )
    return mock_mlflow, mock_client_instance, modules_patch, resolve_patch


def _primed_load(mock_env, *, run_id: str = "abc123", artifact: str = "model.cbm"):
    """Call ``load_mlflow_model`` once with all the standard patches."""
    from haute._mlflow_io import load_mlflow_model

    _, _, modules_patch, resolve_patch = mock_env
    fake_model = MagicMock()
    fake_model.feature_names_ = ["a", "b"]
    fake_model.get_cat_feature_indices.return_value = []

    with (
        modules_patch,
        resolve_patch,
        patch("haute._mlflow_io._load_catboost_model", return_value=fake_model),
        patch("haute._mlflow_io._resolve_artifact_local", return_value="/tmp/model.cbm"),
        patch("haute._mlflow_io._find_cbm_artifact", return_value=artifact),
    ):
        return load_mlflow_model(
            source_type="run",
            run_id=run_id,
            artifact_path=artifact,
            task="regression",
        )


def _events(captured, name: str) -> list[dict]:
    """Filter captured structlog events by event name."""
    return [e for e in captured if e.get("event") == name]


# ---------------------------------------------------------------------------
# 1. Cache MISS emits a structured event
# ---------------------------------------------------------------------------


class TestCacheMissLogging:
    """On a cold load, a ``model_cache_miss`` event must fire with the
    triple (run_id, artifact_path, flavor) as structured fields."""

    def test_first_load_emits_cache_miss_event(self, mock_mlflow_env):
        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="miss_run_1", artifact="model.cbm")

        miss = _events(captured, "model_cache_miss")
        assert len(miss) >= 1, (
            "A cold load must emit a 'model_cache_miss' structlog event "
            "so we can measure cache effectiveness in production."
        )

    def test_cache_miss_event_carries_run_id(self, mock_mlflow_env):
        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="miss_run_rid", artifact="model.cbm")

        miss = _events(captured, "model_cache_miss")
        assert miss, "model_cache_miss event is required on first load"
        assert miss[0].get("run_id") == "miss_run_rid", (
            "model_cache_miss event must include run_id as a structured field"
        )

    def test_cache_miss_event_carries_artifact_path(self, mock_mlflow_env):
        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="rid", artifact="model.cbm")

        miss = _events(captured, "model_cache_miss")
        assert miss, "model_cache_miss event is required on first load"
        # Accept either "artifact_path" or "artifact" — pick one, but pin that
        # *some* field names the artifact.
        art = miss[0].get("artifact_path") or miss[0].get("artifact")
        assert art == "model.cbm", (
            "model_cache_miss event must include the artifact path "
            "(as 'artifact_path' or 'artifact') so operators can correlate "
            "misses to specific models."
        )

    def test_cache_miss_event_carries_flavor(self, mock_mlflow_env):
        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="rid", artifact="model.cbm")

        miss = _events(captured, "model_cache_miss")
        assert miss, "model_cache_miss event is required on first load"
        assert miss[0].get("flavor") == "catboost", (
            "model_cache_miss event must include flavor so we can bucket "
            "cache-effectiveness by model type (catboost / rustystats / pyfunc)."
        )


# ---------------------------------------------------------------------------
# 2. Cache HIT emits a structured event
# ---------------------------------------------------------------------------


class TestCacheHitLogging:
    """Second load of the same (run_id, artifact_path, task) must emit a
    ``model_cache_hit`` event with matching structured fields."""

    def test_second_load_emits_cache_hit_event(self, mock_mlflow_env):
        # Prime the cache.
        _primed_load(mock_mlflow_env, run_id="hit_run_1", artifact="model.cbm")

        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="hit_run_1", artifact="model.cbm")

        hits = _events(captured, "model_cache_hit")
        assert len(hits) >= 1, (
            "A repeat load must emit a 'model_cache_hit' structlog event "
            "with the same field vocabulary as model_cache_miss."
        )

    def test_cache_hit_event_carries_run_id(self, mock_mlflow_env):
        _primed_load(mock_mlflow_env, run_id="hit_rid", artifact="model.cbm")

        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="hit_rid", artifact="model.cbm")

        hits = _events(captured, "model_cache_hit")
        assert hits, "cache_hit event expected on repeat load"
        assert hits[0].get("run_id") == "hit_rid", (
            "model_cache_hit must include run_id as a structured field "
            "(stringified cache keys are not queryable in log aggregators)."
        )

    def test_cache_hit_event_carries_artifact_path(self, mock_mlflow_env):
        _primed_load(mock_mlflow_env, run_id="rid2", artifact="model.cbm")

        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="rid2", artifact="model.cbm")

        hits = _events(captured, "model_cache_hit")
        assert hits, "cache_hit event expected on repeat load"
        art = hits[0].get("artifact_path") or hits[0].get("artifact")
        assert art == "model.cbm", (
            "model_cache_hit must include artifact_path/artifact so we can "
            "bucket hit-rate per model."
        )

    def test_cache_hit_event_carries_flavor(self, mock_mlflow_env):
        _primed_load(mock_mlflow_env, run_id="rid3", artifact="model.cbm")

        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="rid3", artifact="model.cbm")

        hits = _events(captured, "model_cache_hit")
        assert hits, "cache_hit event expected on repeat load"
        assert hits[0].get("flavor") == "catboost", (
            "model_cache_hit must include flavor for cross-flavor hit-rate comparison."
        )


# ---------------------------------------------------------------------------
# 3. get_model_cache_stats() exposes hits/misses as a metric
# ---------------------------------------------------------------------------


class TestCacheStatsFunction:
    """A ``get_model_cache_stats()`` function must exist at module level
    and return a mapping with at least ``hits`` and ``misses`` keys."""

    def test_get_model_cache_stats_is_importable(self):
        from haute import _mlflow_io

        assert hasattr(_mlflow_io, "get_model_cache_stats"), (
            "haute._mlflow_io must expose a 'get_model_cache_stats' callable "
            "so operators / routes can scrape cache effectiveness at runtime."
        )
        assert callable(_mlflow_io.get_model_cache_stats)

    def test_stats_shape_hits_and_misses(self):
        from haute._mlflow_io import get_model_cache_stats

        stats = get_model_cache_stats()
        assert isinstance(stats, dict), (
            "get_model_cache_stats() must return a dict-shaped mapping "
            "so it serialises to JSON for ops endpoints."
        )
        assert "hits" in stats, "stats must include a 'hits' counter"
        assert "misses" in stats, "stats must include a 'misses' counter"

    def test_stats_are_integers_and_non_negative(self):
        from haute._mlflow_io import get_model_cache_stats

        stats = get_model_cache_stats()
        assert isinstance(stats["hits"], int)
        assert isinstance(stats["misses"], int)
        assert stats["hits"] >= 0
        assert stats["misses"] >= 0

    def test_fresh_counters_start_at_zero(self):
        """Right after the autouse fixture resets the cache, both counters
        are zero — so a test suite measuring effectiveness has a known start."""
        from haute._mlflow_io import get_model_cache_stats

        stats = get_model_cache_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0


# ---------------------------------------------------------------------------
# 4. Counter monotonicity (call-count sanity)
# ---------------------------------------------------------------------------


class TestCounterMonotonicity:
    """Counters must increment in the obvious direction: hits on repeat,
    misses on novel loads."""

    def test_miss_counter_increments_on_first_load(self, mock_mlflow_env):
        from haute._mlflow_io import get_model_cache_stats

        before = get_model_cache_stats()["misses"]
        _primed_load(mock_mlflow_env, run_id="novel_a", artifact="model.cbm")
        after = get_model_cache_stats()["misses"]

        assert after == before + 1, (
            f"First load of novel_a should increment misses by exactly 1 "
            f"(before={before}, after={after})."
        )

    def test_hit_counter_increments_on_repeat_load(self, mock_mlflow_env):
        from haute._mlflow_io import get_model_cache_stats

        _primed_load(mock_mlflow_env, run_id="repeat_a", artifact="model.cbm")
        before = get_model_cache_stats()["hits"]
        _primed_load(mock_mlflow_env, run_id="repeat_a", artifact="model.cbm")
        after = get_model_cache_stats()["hits"]

        assert after == before + 1, (
            f"Second load of repeat_a should increment hits by exactly 1 "
            f"(before={before}, after={after})."
        )

    def test_hits_monotonic_over_repeated_loads(self, mock_mlflow_env):
        """Hit counter is strictly non-decreasing across N repeats."""
        from haute._mlflow_io import get_model_cache_stats

        _primed_load(mock_mlflow_env, run_id="mono", artifact="model.cbm")  # prime

        observations: list[int] = [get_model_cache_stats()["hits"]]
        for _ in range(4):
            _primed_load(mock_mlflow_env, run_id="mono", artifact="model.cbm")
            observations.append(get_model_cache_stats()["hits"])

        # Must strictly increase (one hit per repeat).
        assert observations[-1] >= observations[0] + 4, (
            f"Hit counter should have grown by at least 4 across 4 repeats, "
            f"got observations={observations}"
        )
        # And must never go backwards.
        for prev, nxt in zip(observations, observations[1:]):
            assert nxt >= prev, (
                f"Hit counter went backwards: {prev} -> {nxt}. Full trace: {observations}"
            )

    def test_misses_monotonic_over_distinct_loads(self, mock_mlflow_env):
        """Miss counter grows by 1 per distinct model key."""
        from haute._mlflow_io import get_model_cache_stats

        baseline = get_model_cache_stats()["misses"]
        for i in range(5):
            _primed_load(mock_mlflow_env, run_id=f"distinct_{i}", artifact="model.cbm")

        final = get_model_cache_stats()["misses"]
        assert final == baseline + 5, (
            f"5 distinct loads should produce 5 misses (baseline={baseline}, final={final})."
        )

    def test_hits_and_misses_independent(self, mock_mlflow_env):
        """A hit does not increment misses; a miss does not increment hits."""
        from haute._mlflow_io import get_model_cache_stats

        # Miss: novel key — misses++ hits unchanged
        hits0 = get_model_cache_stats()["hits"]
        _primed_load(mock_mlflow_env, run_id="ind_a", artifact="model.cbm")
        hits1 = get_model_cache_stats()["hits"]
        assert hits1 == hits0, (
            f"A miss must not increment the hit counter (hits before={hits0}, after={hits1})."
        )

        # Hit: same key — hits++ misses unchanged
        misses0 = get_model_cache_stats()["misses"]
        _primed_load(mock_mlflow_env, run_id="ind_a", artifact="model.cbm")
        misses1 = get_model_cache_stats()["misses"]
        assert misses1 == misses0, (
            f"A hit must not increment the miss counter (misses before={misses0}, after={misses1})."
        )


# ---------------------------------------------------------------------------
# 5. Reset semantics: clear_model_cache() zeros the counters.
# ---------------------------------------------------------------------------


class TestResetSemantics:
    """Pin that ``clear_model_cache()`` also resets the stat counters.

    The alternative choice ("counters survive a clear") would be fine, but
    we pin the "reset both" behaviour because it matches the caller's
    mental model of ``clear_model_cache`` as a cache-wide reset.  A future
    change to the other semantics should update this test (and document
    the new choice), not silently flip the behaviour.
    """

    def test_clear_model_cache_resets_hits(self, mock_mlflow_env):
        from haute._mlflow_io import clear_model_cache, get_model_cache_stats

        _primed_load(mock_mlflow_env, run_id="clr_a", artifact="model.cbm")
        _primed_load(mock_mlflow_env, run_id="clr_a", artifact="model.cbm")  # hit
        assert get_model_cache_stats()["hits"] > 0

        clear_model_cache()

        stats = get_model_cache_stats()
        assert stats["hits"] == 0, (
            "clear_model_cache() should reset the 'hits' counter to 0. "
            "If the product chooses the opposite semantics ('counters survive "
            "clear'), update this test and document the rationale."
        )

    def test_clear_model_cache_resets_misses(self, mock_mlflow_env):
        from haute._mlflow_io import clear_model_cache, get_model_cache_stats

        _primed_load(mock_mlflow_env, run_id="clr_b", artifact="model.cbm")
        assert get_model_cache_stats()["misses"] > 0

        clear_model_cache()

        stats = get_model_cache_stats()
        assert stats["misses"] == 0, "clear_model_cache() should reset the 'misses' counter to 0."

    def test_post_clear_next_load_is_a_miss(self, mock_mlflow_env):
        """After clear, the same key is a miss again — not an orphan hit."""
        from haute._mlflow_io import clear_model_cache, get_model_cache_stats

        _primed_load(mock_mlflow_env, run_id="clr_c", artifact="model.cbm")
        _primed_load(mock_mlflow_env, run_id="clr_c", artifact="model.cbm")  # hit
        clear_model_cache()

        with structlog.testing.capture_logs() as captured:
            _primed_load(mock_mlflow_env, run_id="clr_c", artifact="model.cbm")

        assert _events(captured, "model_cache_miss"), (
            "After clear_model_cache(), a repeat load of the same key must "
            "fire a 'model_cache_miss' (the cache is truly empty) rather "
            "than silently still-hitting."
        )
        stats = get_model_cache_stats()
        assert stats["misses"] == 1, f"After clear, first load must produce misses=1 (got {stats})."
        assert stats["hits"] == 0, f"After clear, first load must produce hits=0 (got {stats})."
