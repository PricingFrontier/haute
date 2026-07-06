"""Tests for Phase 3 Wave 7 package 7B — item #93.

Pins the expected behaviour of the ``(model_id, schema_hash)`` cache that
short-circuits :func:`haute._model_scorer._validate_features` on repeated
calls with the same ``ScoringModel`` and same input schema.

Background
----------

``_validate_features`` at ``src/haute/_model_scorer.py:81`` walks the
model's expected feature list, checks presence against the input schema,
checks categorical dtype compatibility, and enforces that the model's
features appear in the same relative order as at training time.

For the batch / preview hot path (thousands of ``score`` calls against
the same model with the same input schema) this per-call validation is
pure overhead — the answer is identical for every call.  Item #93 asks
for a content-addressed ``(feature_contract, schema)`` cache that returns
the previously-computed ``(usable, missing)`` tuple on hits and cascades
its eviction into ``haute._mlflow_io._model_cache`` so a model-reload
drops the validation state pinned for the stale model's contract.

API this file pins
------------------

The developer must expose, on ``haute._model_scorer``:

* ``_feature_validation_cache`` — a bounded module-level
  ``LRUCache`` keyed by ``(feature_contract, schema_items)``.  The key is
  content-addressed: the validation result depends only on the model's
  feature contract and the input schema, so two instances of the same
  contract share one entry (object identity is intentionally *not* part
  of the key).
* ``_compute_schema_hash(schema: pl.Schema) -> str`` — a stable xxh64
  hex digest of the ordered ``(name, dtype_str)`` pairs, so that column
  reorders, renames, and dtype changes produce different digests.
* ``_clear_feature_validation_cache()`` — drops every entry (used as the
  blanket cascade for ``clear_model_cache()``).
* ``_invalidate_feature_validation_cache_for(scoring_model)`` —
  targeted cascade: drops only the entries whose first key component
  matches the model's feature contract.  Invoked from the ``_model_cache``
  eviction path so evicting a ``(run_id, artifact)`` model drops the
  validation entries pinned for its contract.

Cascading is wired by having ``_model_cache`` (in ``_mlflow_io``) invoke
``_invalidate_feature_validation_cache_for`` during eviction.  Details
of the wiring are the developer's to design; this file pins only the
observable invariant (cache entries for a model must disappear when the
model is evicted).

Test-driven — no production code is edited here.  The tests are marked
``xfail(strict=True)`` for every assertion that only holds *after* the
cache lands.  Once the refactor ships the ``xfail`` decorators can be
removed and these become the regression suite.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._mlflow_io import ScoringModel, _model_cache
from haute._model_scorer import (
    FeatureMismatchError,
    _model_feature_contract_key,
    _validate_features,
)

# ---------------------------------------------------------------------------
# Small helpers — shared across every test class in this file.
# ---------------------------------------------------------------------------


def _make_scoring_model(
    feature_names: list[str] | None = None,
    cat_feature_names: frozenset[str] | None = None,
    flavor: str = "catboost",
) -> ScoringModel:
    """Build a ScoringModel backed by a MagicMock so tests do not need MLflow."""
    feature_names = feature_names or ["a", "b", "c"]
    cat_feature_names = cat_feature_names or frozenset()
    raw = MagicMock()
    raw.feature_names_ = list(feature_names)
    return ScoringModel(
        model=raw,
        feature_names=list(feature_names),
        cat_feature_names=cat_feature_names,
        flavor=flavor,
    )


def _schema_for(feature_names: list[str], extra: dict[str, pl.DataType] | None = None) -> pl.Schema:
    """Build a polars Schema with Float64 columns for each feature (+ optional extras)."""
    base: dict[str, pl.DataType] = {c: pl.Float64 for c in feature_names}
    if extra:
        base.update(extra)
    return pl.Schema(base)


def _clear_caches() -> None:
    """Drop every cached validation entry plus the MLflow model cache.

    Imported lazily: the cache attribute only exists *after* the refactor
    lands.  Before that, the attribute lookup raises ``AttributeError`` —
    caller treats that as "nothing to clear".
    """
    import haute._model_scorer as ms

    _model_cache.clear()
    clear_fn = getattr(ms, "_clear_feature_validation_cache", None)
    if clear_fn is not None:
        clear_fn()


@pytest.fixture(autouse=True)
def _reset_caches() -> Any:
    """Give every test a cold cache, regardless of module-import order."""
    _clear_caches()
    yield
    _clear_caches()


def _cache_exists() -> bool:
    """Return True once the feature-validation cache has landed in production."""
    import haute._model_scorer as ms

    return hasattr(ms, "_feature_validation_cache")


# ---------------------------------------------------------------------------
# Core behaviour — hit / miss / key distinctness / error non-caching.
# ---------------------------------------------------------------------------


class TestValidationCacheHitsAndMisses:
    """Validator is called exactly once per ``(model_id, schema_hash)``."""

    def test_first_call_invokes_validator(self) -> None:
        """A cold cache must run the real validation code path."""
        import haute._model_scorer as ms

        sm = _make_scoring_model(["a", "b"])
        schema = _schema_for(["a", "b"])

        # Spy on the raw validation worker: the cached façade delegates to
        # it on misses.  Name pinned for the developer to expose.
        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            usable, missing = _validate_features(sm, schema)

        assert spy.call_count == 1
        assert usable == ["a", "b"]
        assert missing == []

    def test_second_call_same_schema_hits_cache(self) -> None:
        """Identical ``(scoring_model, schema)`` → validator not re-run."""
        import haute._model_scorer as ms

        sm = _make_scoring_model(["a", "b"])
        schema = _schema_for(["a", "b"])

        # Prime the cache.
        _validate_features(sm, schema)

        with patch.object(ms, "_validate_features_uncached") as spy:
            usable, missing = _validate_features(sm, schema)

        spy.assert_not_called()
        assert usable == ["a", "b"]
        assert missing == []

    def test_new_schema_object_same_content_hits_cache(self) -> None:
        """Fresh schema objects with identical content share the same cache key."""
        import haute._model_scorer as ms

        sm = _make_scoring_model(["a", "b"])

        # Prime with one Schema instance.
        _validate_features(sm, _schema_for(["a", "b"]))

        # Production callers build a fresh Schema on each collect_schema()
        # call, so the cache key must be content-based rather than
        # object-identity-based.
        equivalent_schema = _schema_for(["a", "b"])
        with patch.object(ms, "_validate_features_uncached") as spy:
            usable, missing = _validate_features(sm, equivalent_schema)

        spy.assert_not_called()
        assert usable == ["a", "b"]
        assert missing == []

    def test_cache_returns_identical_tuple_content(self) -> None:
        """Cache hit must not mutate or re-order the result.

        This is a baseline invariant that must hold both pre- and post-
        refactor — a working cache returns the same value as the uncached
        path, so this test is NOT ``xfail``.  It guards against the
        refactor accidentally reordering or mutating the result tuple.
        """
        sm = _make_scoring_model(["a", "b", "c"])
        schema = _schema_for(["a", "b", "c"])

        first_usable, first_missing = _validate_features(sm, schema)
        second_usable, second_missing = _validate_features(sm, schema)

        assert first_usable == second_usable == ["a", "b", "c"]
        assert first_missing == second_missing == []

    def test_different_schema_column_renamed_is_miss(self) -> None:
        """Renaming a column yields a new ``schema_hash`` → cache miss."""
        import haute._model_scorer as ms

        sm = _make_scoring_model(["a", "b"])

        # Prime with ["a", "b"].
        _validate_features(sm, _schema_for(["a", "b"]))

        # Now rename: model still expects ["a", "b"] but schema has "a2" —
        # that is a FeatureMismatchError *and* a distinct cache key.
        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            with pytest.raises(FeatureMismatchError):
                _validate_features(sm, _schema_for(["a2", "b"]))

        # Miss means the uncached path ran — even though it ended in an
        # exception, the cache key itself must have been resolved.
        assert spy.call_count == 1

    def test_different_schema_dtype_changed_is_miss(self) -> None:
        """Dtype changing on a categorical column → distinct ``schema_hash``."""
        import haute._model_scorer as ms

        sm = _make_scoring_model(
            feature_names=["region", "amount"],
            cat_feature_names=frozenset({"region"}),
        )
        schema_ok = pl.Schema({"region": pl.Utf8, "amount": pl.Float64})
        _validate_features(sm, schema_ok)  # priming call passes

        # Now the caller hands us the same column names but region is
        # numeric — this must fail validation *and* miss the cache.
        schema_bad = pl.Schema({"region": pl.Int64, "amount": pl.Float64})
        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            with pytest.raises(FeatureMismatchError):
                _validate_features(sm, schema_bad)

        assert spy.call_count == 1

    def test_different_schema_order_is_miss(self) -> None:
        """A warm cache must not hide feature-order mismatches.

        CatBoost categorical feature indices are positional. The validation
        worker enforces training feature order, so the cache key must include
        schema order. If it does not, a valid first call can prime the cache
        and a reversed second call incorrectly returns success.
        """
        import haute._model_scorer as ms

        sm = _make_scoring_model(["region", "age"])
        _validate_features(sm, pl.Schema({"region": pl.Utf8, "age": pl.Float64}))

        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            with pytest.raises(FeatureMismatchError) as exc_info:
                _validate_features(sm, pl.Schema({"age": pl.Float64, "region": pl.Utf8}))

        assert spy.call_count == 1
        assert exc_info.value.context["actual"] == ["age", "region"]

    def test_same_contract_new_instance_is_content_addressed_hit(self) -> None:
        """Two ``ScoringModel`` objects with an identical contract share an entry.

        The cache key is content-addressed on ``(feature_contract, schema)``,
        not object identity.  The validation result depends only on those two,
        so a fresh instance of the same contract must reuse the cached answer
        instead of re-running the O(n) validator — and the reused answer must
        still be correct.  (Keying on ``id(scoring_model)`` previously forced a
        needless miss and left a per-instance dead entry behind.)
        """
        import haute._model_scorer as ms

        sm_a = _make_scoring_model(["a", "b"])
        sm_b = _make_scoring_model(["a", "b"])  # same features, new object
        schema = _schema_for(["a", "b"])

        _validate_features(sm_a, schema)  # prime

        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            usable, missing = _validate_features(sm_b, schema)

        assert spy.call_count == 0  # content-addressed hit — no re-validation
        assert usable == ["a", "b"]
        assert missing == []


# ---------------------------------------------------------------------------
# Error semantics — failures must NOT be cached.
# ---------------------------------------------------------------------------


class TestValidationCacheErrorHandling:
    """Failed validations must surface on every call, not be cached."""

    def test_first_call_with_broken_schema_raises(self) -> None:
        """Baseline — the validator still raises exactly as before.

        Pre- *and* post-refactor invariant.  Not ``xfail``: the cache
        must never swallow this error.
        """
        sm = _make_scoring_model(["a", "b", "c"])
        schema = _schema_for(["a", "b"])  # missing "c"

        with pytest.raises(FeatureMismatchError) as exc_info:
            _validate_features(sm, schema)

        # Context preserved — errors must remain rich, not collapsed.
        assert exc_info.value.context["missing"] == ["c"]

    def test_second_call_with_same_broken_schema_re_raises(self) -> None:
        """A cached *error* result would silently swallow a later fix.

        If someone repairs the schema between calls, the cache must not
        keep returning the old failure.  Simpler still: never cache
        exceptions — re-run the validator every time and let it raise.
        """
        import haute._model_scorer as ms

        sm = _make_scoring_model(["a", "b", "c"])
        schema = _schema_for(["a", "b"])  # missing "c"

        # First call: raises.
        with pytest.raises(FeatureMismatchError):
            _validate_features(sm, schema)

        # Second call must also hit the uncached path — a cached
        # exception is indistinguishable from a real one and hides
        # operator intent.
        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            with pytest.raises(FeatureMismatchError):
                _validate_features(sm, schema)

        assert spy.call_count == 1, "errors must not be cached — re-validate every time"

    def test_repair_after_error_is_detected(self) -> None:
        """If the schema is fixed after an error, a later call succeeds.

        Pre- *and* post-refactor invariant — not ``xfail``.  A cached
        exception for the *old* schema must not leak into calls that
        pass a corrected schema: the keys differ, so the lookup misses.
        """
        sm = _make_scoring_model(["a", "b", "c"])

        # Call with a broken schema — raises.
        with pytest.raises(FeatureMismatchError):
            _validate_features(sm, _schema_for(["a", "b"]))

        # Caller fixes the frame and calls again with a complete schema —
        # this must pass cleanly, not reuse the cached exception.
        usable, missing = _validate_features(sm, _schema_for(["a", "b", "c"]))
        assert usable == ["a", "b", "c"]
        assert missing == []

    def test_error_context_preserved_on_cache_miss(self) -> None:
        """Rich error context (missing, expected, type_mismatches) must survive.

        Pre- *and* post-refactor invariant — not ``xfail``.  Pins the
        shape of ``FeatureMismatchError.context`` so the cache façade
        cannot strip or remap any diagnostic field.
        """
        sm = _make_scoring_model(
            feature_names=["a", "b", "c"],
            cat_feature_names=frozenset({"a"}),
        )
        # "a" expected categorical but schema types it numeric, *and* "c"
        # is missing — exercises both diagnostic branches.
        schema = pl.Schema({"a": pl.Int64, "b": pl.Float64})

        with pytest.raises(FeatureMismatchError) as exc_info:
            _validate_features(sm, schema)

        ctx = exc_info.value.context
        assert "c" in ctx["missing"]
        assert ctx["expected"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Schema-hash primitive — isolated characterisation tests.
# ---------------------------------------------------------------------------


class TestSchemaHashStability:
    """``_compute_schema_hash`` must be stable, collision-resistant, and pure."""

    def test_identical_schemas_hash_identically(self) -> None:
        from haute._model_scorer import _compute_schema_hash

        a = _schema_for(["x", "y", "z"])
        b = _schema_for(["x", "y", "z"])
        assert _compute_schema_hash(a) == _compute_schema_hash(b)

    def test_column_rename_changes_hash(self) -> None:
        from haute._model_scorer import _compute_schema_hash

        assert _compute_schema_hash(_schema_for(["x", "y"])) != _compute_schema_hash(
            _schema_for(["x2", "y"])
        )

    def test_dtype_change_changes_hash(self) -> None:
        from haute._model_scorer import _compute_schema_hash

        a = pl.Schema({"col": pl.Utf8})
        b = pl.Schema({"col": pl.Int64})
        assert _compute_schema_hash(a) != _compute_schema_hash(b)

    def test_column_order_changes_hash(self) -> None:
        """Feature order is part of the scoring contract and cache key."""
        from haute._model_scorer import _compute_schema_hash

        a = pl.Schema({"x": pl.Float64, "y": pl.Int64})
        b = pl.Schema({"y": pl.Int64, "x": pl.Float64})
        assert _compute_schema_hash(a) != _compute_schema_hash(b)

    def test_hash_is_hex_string(self) -> None:
        """xxh64 hexdigest — 16 hex chars, all lowercase."""
        from haute._model_scorer import _compute_schema_hash

        h = _compute_schema_hash(_schema_for(["a", "b"]))
        assert isinstance(h, str)
        assert len(h) == 16
        int(h, 16)  # raises ValueError on anything non-hex


# ---------------------------------------------------------------------------
# Eviction cascade — when _model_cache drops a model, validation entries go too.
# ---------------------------------------------------------------------------


class TestValidationCacheCascadeEviction:
    """``_model_cache`` eviction must purge the validation cache for that model."""

    def test_manual_clear_empties_validation_cache(self) -> None:
        """``_clear_feature_validation_cache`` drops every entry."""
        import haute._model_scorer as ms

        sm = _make_scoring_model(["a", "b"])
        _validate_features(sm, _schema_for(["a", "b"]))

        assert len(ms._feature_validation_cache) == 1

        ms._clear_feature_validation_cache()
        assert len(ms._feature_validation_cache) == 0

    def test_targeted_invalidation_drops_only_matching_entries(self) -> None:
        """``_invalidate_feature_validation_cache_for(model)`` scopes to one model."""
        import haute._model_scorer as ms

        sm_keep = _make_scoring_model(["x", "y"])
        sm_drop = _make_scoring_model(["a", "b"])
        _validate_features(sm_keep, _schema_for(["x", "y"]))
        _validate_features(sm_drop, _schema_for(["a", "b"]))

        assert len(ms._feature_validation_cache) == 2

        ms._invalidate_feature_validation_cache_for(sm_drop)

        # Only the surviving model's entry should remain.
        assert len(ms._feature_validation_cache) == 1
        # And the survivor is specifically the kept model — pin by walking
        # the keys (content-addressed on the feature contract).
        surviving = {k[0] for k in list(ms._feature_validation_cache._data.keys())}
        assert _model_feature_contract_key(sm_keep) in surviving
        assert _model_feature_contract_key(sm_drop) not in surviving

    def test_model_cache_eviction_clears_validation_entries(self) -> None:
        """Forcing a model out of ``_model_cache`` cascades into validation."""
        import haute._model_scorer as ms
        from haute._mlflow_io import _MODEL_CACHE_MAX_SIZE

        # Put a model in the MLflow cache + prime its validation.
        sm_evicted = _make_scoring_model(["a", "b"])
        _model_cache.put(("run", "evict_me", "model.cbm", "regression"), sm_evicted)
        _validate_features(sm_evicted, _schema_for(["a", "b"]))

        assert len(ms._feature_validation_cache) == 1

        # Fill the LRU past capacity with fresh models so ``sm_evicted``
        # is forced out.  After eviction, its validation entry must be
        # gone too — the cascade is wired through whatever eviction hook
        # ``_model_cache`` exposes.
        for i in range(_MODEL_CACHE_MAX_SIZE + 1):
            filler = _make_scoring_model([f"f{i}"])
            _model_cache.put(
                ("run", f"filler_{i}", f"model_{i}.cbm", "regression"),
                filler,
            )

        # The original entry must be gone from the MLflow cache…
        assert ("run", "evict_me", "model.cbm", "regression") not in _model_cache
        # …and its validation cache entry must be gone too.  We look up
        # by the contract key the cache uses.
        surviving = {k[0] for k in list(ms._feature_validation_cache._data.keys())}
        assert _model_feature_contract_key(sm_evicted) not in surviving

    def test_clear_model_cache_cascades_to_validation_cache(self) -> None:
        """``clear_model_cache()`` must also blow away every validation entry."""
        import haute._model_scorer as ms
        from haute._mlflow_io import clear_model_cache

        sm = _make_scoring_model(["a", "b"])
        _model_cache.put(("run", "rid", "model.cbm", "regression"), sm)
        _validate_features(sm, _schema_for(["a", "b"]))
        assert len(ms._feature_validation_cache) >= 1

        clear_model_cache()  # no run_id → wipe everything

        assert len(_model_cache) == 0
        assert len(ms._feature_validation_cache) == 0

    def test_reload_same_contract_reuses_validation_state(self) -> None:
        """A reload with the *same* feature contract reuses the cached result.

        The user-visible guarantee is correctness, not coldness: the
        validation answer is a pure function of ``(feature_contract, schema)``,
        so a fresh ``ScoringModel`` for the same model+schema must return the
        same ``(usable, missing)`` — reusing the entry is both correct and the
        point of content-addressing.  A reload whose *contract changed* still
        misses (its key differs), which the dtype/order tests cover.
        """
        import haute._model_scorer as ms

        sm_v1 = _make_scoring_model(["a", "b"])
        schema = _schema_for(["a", "b"])
        primed = _validate_features(sm_v1, schema)  # prime with v1

        # Simulate the reload: brand-new ScoringModel for the "same" model.
        sm_v2 = _make_scoring_model(["a", "b"])

        with patch.object(
            ms, "_validate_features_uncached", wraps=ms._validate_features_uncached
        ) as spy:
            reloaded = _validate_features(sm_v2, schema)

        # Content-addressed hit — no re-validation, and the same answer.
        assert spy.call_count == 0
        assert reloaded == primed == (["a", "b"], [])


# ---------------------------------------------------------------------------
# Thread safety — the scoring hot path runs under FastAPI worker threads.
# ---------------------------------------------------------------------------


class TestValidationCacheThreadSafety:
    """Concurrent ``_validate_features`` must not corrupt the cache."""

    def test_concurrent_hits_do_not_race(self) -> None:
        """Parallel hits on the same key must all return the same result.

        Pre- *and* post-refactor invariant — not ``xfail``.  The
        pre-refactor path is already stateless and thread-safe; the
        cache layer must preserve that by using a proper lock (the
        existing ``LRUCache`` does).
        """
        sm = _make_scoring_model(["a", "b"])
        schema = _schema_for(["a", "b"])
        _validate_features(sm, schema)  # prime

        results: list[tuple[list[str], list[str]]] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()  # align threads to hit concurrently
            try:
                results.append(_validate_features(sm, schema))
            except BaseException as exc:  # noqa: BLE001 - test recorder
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 8
        for usable, missing in results:
            assert usable == ["a", "b"]
            assert missing == []

    def test_concurrent_misses_with_different_models_do_not_corrupt_cache(self) -> None:
        """Parallel cold fills on disjoint keys must each produce one entry."""
        import haute._model_scorer as ms

        models = [_make_scoring_model([f"m{i}", f"n{i}"]) for i in range(8)]
        schemas = [_schema_for([f"m{i}", f"n{i}"]) for i in range(8)]

        barrier = threading.Barrier(len(models))
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                _validate_features(models[i], schemas[i])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(models))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Every model's key should now be present — no entry lost to a race.
        surviving = {k[0] for k in list(ms._feature_validation_cache._data.keys())}
        for m in models:
            assert _model_feature_contract_key(m) in surviving


# ---------------------------------------------------------------------------
# Benchmark — cache-hit path must be materially faster than cold validation.
# ---------------------------------------------------------------------------


class TestValidationCacheBenchmark:
    """Cache-hit path — production realism (fresh ``pl.Schema`` every call).

    Production always feeds ``_validate_features`` a fresh ``pl.Schema``
    returned by ``lf.collect_schema()``.  That means the per-call path
    pays the full xxh64 content hash of ``(name, dtype)`` pairs; the
    LRU cache's only saving over the uncached worker is the validator's
    list walks + set / order checks.  We benchmark that realistic path
    directly — no same-object shortcut — so the assertion matches what
    ``score_frame`` actually observes.
    """

    pytestmark = pytest.mark.perf

    def test_bulk_hit_path_under_fixed_budget(self) -> None:
        """1000 hits on a 50-feature frame complete well under 200 ms.

        Each call pays an xxh64 over the schema's ``(name, dtype)`` pairs
        plus an LRU dict lookup and the schema construction itself.  At
        50 columns the hash + construction dominates per-call cost; the
        LRU avoids only the validator's O(n_features) walk.  The 200 ms
        budget (200 µs/call) is the cached-path floor on modern hardware
        with CI headroom against single-trial scheduler jitter — a
        regression that restored the full validator path on every hit
        would not push past the budget unless the schema hashing itself
        broke too.

        Uses the min of several trials so that a single scheduler
        interrupt does not flake the budget check.

        This is a looser absolute-time guard than the head-to-head ratio
        test below; the ratio is the load-bearing signal.
        """
        features = [f"f{i:02d}" for i in range(50)]
        sm = _make_scoring_model(features)
        df = pl.DataFrame({c: [1.0] for c in features})

        _validate_features(sm, df.lazy().collect_schema())  # prime

        def time_one_trial() -> float:
            t0 = time.perf_counter()
            for _ in range(1000):
                _validate_features(sm, df.lazy().collect_schema())
            return time.perf_counter() - t0

        elapsed = min(time_one_trial() for _ in range(5))

        assert elapsed < 0.200, (
            f"1000 cached validations should complete in <200ms, took {elapsed * 1e3:.2f}ms"
        )

    def test_bulk_hit_path_with_fresh_schema_per_call(self) -> None:
        """Realistic production scenario: fresh ``pl.Schema`` every call.

        Mirrors the prod call site where ``lf.collect_schema()`` returns
        a brand-new ``pl.Schema`` object on every ``score_frame`` call.
        The ``id(schema)``-keyed side-table (deleted in Phase 3 audit
        item A) never helped this path; what helps is the LRU keyed by
        the content hash, which lets repeat schemas collapse to the same
        cache slot regardless of object identity.

        Measured reality on a 50-column schema (developer box, CPython
        3.11):

        * cached path: ~40-70 µs/call
          (xxh64 over 50 ``(name, dtype)`` pairs + LRU dict lookup)
        * uncached path: ~50-95 µs/call
          (validator walks expected / available lists, builds sets,
          checks dtypes, re-checks order)

        Typical speedup: 1.1-1.3x.  We assert ``>=1.05`` — a floor that
        catches "the cache was removed" (speedup → 1.0x) without
        policing microbenchmark jitter.  The two paths are close enough
        in absolute time that a few microseconds of OS scheduling
        jitter can swing the ratio by ±10%, so the threshold stays
        modest and the docstring documents what the test is really
        guarding against.

        This is deliberately lower than the original plan's ``>=3.0``
        target.  At 50 columns the xxh64 hashing cost roughly equals the
        validator's list-walking cost, so the LRU only wins on the
        validator half of the work — a ~1.3x ceiling by construction.
        Chasing bigger numbers would require either (a) larger schemas,
        where the walks dominate more, or (b) caching the schema hash
        itself, which the side-table attempted but which cost more in
        weakref bookkeeping than it saved (see Phase 3 audit item A).

        The regression to guard against is NOT "cache too slow" — it's
        "cache silently disabled", which produces a ~1.0x ratio.

        Uses min-of-many measurements so a single noisy run (OS
        scheduler interrupt, background process) does not flake the
        test — only a sustained regression on every trial can lower
        the minimum.
        """
        import haute._model_scorer as ms

        features = [f"f{i:02d}" for i in range(50)]
        sm = _make_scoring_model(features)

        # Build a template frame and rebuild the schema via
        # ``.lazy().collect_schema()`` on every call — the exact pattern
        # ``_score_eager`` uses in production.
        df = pl.DataFrame({c: [1.0] for c in features})

        # Warm the LRU and any lazy polars state before timing.
        for _ in range(10):
            _validate_features(sm, df.lazy().collect_schema())

        uncached = ms._validate_features_uncached

        def time_cached() -> float:
            t0 = time.perf_counter()
            for _ in range(200):
                _validate_features(sm, df.lazy().collect_schema())
            return time.perf_counter() - t0

        def time_uncached() -> float:
            t0 = time.perf_counter()
            for _ in range(200):
                uncached(sm, df.lazy().collect_schema())
            return time.perf_counter() - t0

        # Min-of-many: take the best of 10 runs of each side.  The
        # minimum is robust against scheduling interrupts and GC
        # pauses; only a systematic regression on every trial can push
        # the minimum upward.
        cached_min = min(time_cached() for _ in range(10))
        uncached_min = min(time_uncached() for _ in range(10))

        speedup = uncached_min / max(cached_min, 1e-9)
        assert speedup >= 1.05, (
            f"LRU cache must provide a measurable (>=1.05x) speedup over the "
            f"uncached validator on the fresh-schema-per-call production path. "
            f"Got {speedup:.2f}x "
            f"(cached_min={cached_min * 1e3:.2f}ms, "
            f"uncached_min={uncached_min * 1e3:.2f}ms).  A ratio near 1.0x "
            f"indicates the cache is no longer wired into _validate_features."
        )

    def test_same_object_cache_hit_path_is_faster_than_uncached(self) -> None:
        """Supplementary "cold-path" check: same ``pl.Schema`` object repeatedly.

        This is NOT production — production always hands us a fresh
        ``pl.Schema`` (see ``test_bulk_hit_path_with_fresh_schema_per_call``
        above for the realistic benchmark).  This test exists as a floor
        measurement in the unrealistic best case where the same Python
        object is reused, because even that best case should still beat
        the uncached path by a measurable margin.

        Historical context: before the Phase 3 audit, this test asserted
        a 5x speedup because an ``id(schema)``-keyed side-table cached
        the xxh64 digest per schema object.  That side-table was deleted
        (Phase 3 audit item A, "removed"): it was permanent cold cache
        against production's fresh-schema pattern, and its weakref +
        lock bookkeeping cost more than it saved.  Post-deletion the
        same-object path pays the full xxh64 on every call, same as the
        fresh-schema path, so the 5x claim collapsed to a measured
        1.25-1.75x range across runs.

        We keep this test as a supplementary check; the fresh-schema
        benchmark is the load-bearing perf claim.  Threshold is ``>=1.10``
        to survive CI timing jitter while still catching a regression
        that silently disabled the cache (ratio would collapse to ~1.0x).
        Uses min-of-many measurements so a single noisy trial does not
        flake the test.
        """
        import haute._model_scorer as ms

        features = [f"f{i:02d}" for i in range(50)]
        sm = _make_scoring_model(features)
        schema = _schema_for(features)

        # Warm imports + initial load before we time anything.
        for _ in range(5):
            _validate_features(sm, schema)

        uncached = ms._validate_features_uncached

        def time_cached() -> float:
            ms._clear_feature_validation_cache()
            _validate_features(sm, schema)  # prime
            t0 = time.perf_counter()
            for _ in range(200):
                _validate_features(sm, schema)
            return time.perf_counter() - t0

        def time_uncached() -> float:
            t0 = time.perf_counter()
            for _ in range(200):
                uncached(sm, schema)
            return time.perf_counter() - t0

        cached_min = min(time_cached() for _ in range(10))
        uncached_min = min(time_uncached() for _ in range(10))

        speedup = uncached_min / max(cached_min, 1e-9)
        assert speedup >= 1.10, (
            f"Same-object cache path must be >=1.10x faster than uncached "
            f"validation; got {speedup:.2f}x "
            f"(cached_min={cached_min * 1e3:.2f}ms, "
            f"uncached_min={uncached_min * 1e3:.2f}ms).  A ratio near 1.0x "
            f"indicates the cache is no longer wired into _validate_features."
        )


# ---------------------------------------------------------------------------
# Readiness probe — skipped until the cache lands, then flipped to assert.
# ---------------------------------------------------------------------------


class TestAPIShape:
    """Shape assertions for the API surface the developer must expose."""

    def test_required_symbols_exist(self) -> None:
        import haute._model_scorer as ms

        assert hasattr(ms, "_feature_validation_cache")
        assert hasattr(ms, "_compute_schema_hash")
        assert hasattr(ms, "_clear_feature_validation_cache")
        assert hasattr(ms, "_invalidate_feature_validation_cache_for")
        assert hasattr(ms, "_validate_features_uncached")

    def test_cache_is_bounded_lru(self) -> None:
        """The cache must be a bounded LRU — an unbounded dict is a leak.

        Scoring runs under long-lived FastAPI workers: a dict that never
        evicts will grow until the process is recycled.  The existing
        ``LRUCache`` has the right shape and already powers ``_model_cache``.
        """
        import haute._model_scorer as ms
        from haute._lru_cache import LRUCache

        assert isinstance(ms._feature_validation_cache, LRUCache)
        # Capacity should be sensibly sized — at least as big as
        # ``_model_cache`` so we don't evict validation faster than models.
        from haute._mlflow_io import _MODEL_CACHE_MAX_SIZE

        assert ms._feature_validation_cache._max_size >= _MODEL_CACHE_MAX_SIZE
