"""Adversarial reproduction for claim
``feature-validation-cache-id-reuse-on-uncached-models``.

Claim under test
----------------
``haute._model_scorer._validate_features`` memoises ``(usable, missing)`` keyed
on::

    (id(scoring_model), _model_feature_contract_key(scoring_model),
     _schema_validation_cache_key(schema))

The ONLY safety net against CPython ``id()`` reuse is the ``_model_cache``
eviction cascade (``_invalidate_feature_validation_cache_for``).  But several
``ScoringModel`` instances NEVER enter ``_model_cache`` — notably the transient
carrier built in ``_score_batched_unified`` and the artifacts returned by
``load_local_model``.  When such a short-lived model is GC'd, CPython may reuse
its ``id()`` for a *different* ``ScoringModel``.  If that new model shares the
same feature-contract tuple AND the same input schema, the stale cache entry
(and the non-locked ``_feature_validation_last_entry`` fast path) is returned
WITHOUT validating the new object via ``_validate_features_uncached``.

The claim is explicitly a *latent* hazard, not a present-day wrong number: the
cached payload ``(usable, missing)`` is purely name/schema-derived, so it is
content-equivalent across two models that share the contract+schema.  What is
demonstrably real *today* is the MECHANISM: a cache HIT served to a distinct
object (different identity, never validated) purely because of ``id()`` reuse.

What this script proves
-----------------------
We exercise the real ``_validate_features`` against the real module-level cache
in two complementary ways and assert on the *specific behaviour* (a hit served
to an object that was never run through the validator):

  PART 1 — REAL allocator id reuse (no monkeypatching):
    * Build ScoringModel A OUTSIDE _model_cache, validate it -> cache populated.
    * Record id(A); drop A; churn ScoringModel allocations (same __slots__ size,
      same contract) until a fresh B lands on id(B) == id(A).
    * Call _validate_features(B, same schema) and show the UNCACHED worker is
      NOT invoked for B (stale entry reused), while the returned value equals
      what B's own validation WOULD have produced (content-equivalent -> latent,
      not active, wrong value).

  PART 2 — Deterministic proof via the permitted ``id`` monkeypatch:
    Guarantees the collision (PART 1 is probabilistic) so the mechanism is
    demonstrated unconditionally.  We shadow ``id`` inside the _model_scorer
    module so two genuinely distinct objects collide on the key, and assert the
    second object is served the first object's cached tuple WITHOUT validation.

A CONTROL confirms the cache is genuinely alive (a non-colliding model with the
same contract+schema but a distinct id DOES run the uncached validator).

ISOLATION: pure in-memory ScoringModels + pl.Schema; project root pinned to a
throwaway temp dir; no rating/ src/ tests/ access; no model training.
"""

from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path

import polars as pl

import haute._sandbox as _sandbox

_TMP = Path(tempfile.mkdtemp(prefix="featval_idreuse_repro_"))
_sandbox.set_project_root(_TMP)

import haute._model_scorer as ms
from haute._mlflow_io import ScoringModel, _model_cache


FEATURES = ["a", "b", "c"]
CAT = frozenset({"c"})
# Schema whose names present FEATURES in training order (so validation succeeds
# and returns a non-trivial (usable, missing) tuple).
SCHEMA = pl.Schema(
    [
        ("a", pl.Float64),
        ("b", pl.Int64),
        ("c", pl.Utf8),
    ]
)


def _fresh_model() -> ScoringModel:
    """A ScoringModel that is NEVER put into _model_cache (mirrors the transient
    carrier / load_local_model deploy artifact lifecycle)."""
    return ScoringModel(
        model=object(),
        feature_names=list(FEATURES),
        cat_feature_names=CAT,
        flavor="catboost",
    )


def _reset_caches() -> None:
    ms._clear_feature_validation_cache()
    _model_cache.clear()


class _UncachedSpy:
    """Wrap ms._validate_features_uncached to count + record which object ids it
    was actually asked to validate."""

    def __init__(self) -> None:
        self._orig = ms._validate_features_uncached
        self.calls: list[int] = []

    def __enter__(self) -> "_UncachedSpy":
        def _spy(scoring_model, schema):  # type: ignore[no-untyped-def]
            self.calls.append(id(scoring_model))
            return self._orig(scoring_model, schema)

        ms._validate_features_uncached = _spy  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        ms._validate_features_uncached = self._orig  # type: ignore[assignment]


def _part1_real_idreuse(failures: list[str]) -> None:
    print("=== PART 1: real allocator id() reuse (no monkeypatch) ===")
    _reset_caches()

    # Confirm the transient/deploy carrier really is absent from _model_cache:
    # if it were cached, an eviction cascade could clean up after it.
    a = _fresh_model()
    in_cache = any(v is a for v in _model_cache._data.values())
    print(f"[1] freshly built ScoringModel present in _model_cache? {in_cache} (must be False)")
    if in_cache:
        failures.append("(1) premise broken: the transient model is in _model_cache")

    # Validate A -> populate _feature_validation_cache + _feature_validation_last_entry.
    res_a = ms._validate_features(a, SCHEMA)
    id_a = id(a)
    print(f"[1] validated A: id(A)={id_a} -> (usable,missing)={res_a}")
    # The last-entry fast path is now keyed on id(A).
    last = ms._feature_validation_last_entry
    assert last is not None and last[0][0] == id_a, "A's entry should be the last-entry"

    # Drop A and force allocator churn until a fresh B reuses id(A).
    del a
    gc.collect()

    b: ScoringModel | None = None
    keepalive: list[ScoringModel] = []
    for _ in range(2_000_000):
        cand = _fresh_model()
        if id(cand) == id_a:
            b = cand
            break
        # Keep a few recent allocations so we don't immediately free into the
        # same slot every time (lets the address pool move around); bounded so
        # memory stays tiny.
        keepalive.append(cand)
        if len(keepalive) > 64:
            keepalive.pop(0)

    if b is None:
        print(
            "[1] could not force a natural id() collision in the allocation "
            "budget on this interpreter — PART 1 inconclusive (PART 2 proves "
            "the mechanism deterministically)."
        )
        return

    assert b is not None
    # B is a genuinely different object from A (A is gone) but shares id and the
    # whole feature contract; the schema is identical.
    print(f"[1] forced B with id(B)={id(b)} == id(A) (distinct object, same contract)")

    # What WOULD B's own uncached validation return?  (Used to confirm the
    # served value is content-equivalent -> latent, not active, wrong value.)
    expected_for_b = ms._validate_features_uncached(b, SCHEMA)

    with _UncachedSpy() as spy:
        served = ms._validate_features(b, SCHEMA)
    print(f"[1] _validate_features(B) -> {served}; uncached-worker calls for B: {spy.calls}")

    if id(b) in spy.calls:
        failures.append(
            "(1) the uncached validator WAS called for B -> no stale entry was "
            "served; mechanism not demonstrated on this run"
        )
    else:
        print(
            "[1] PROVEN: B was served the cache entry created for A WITHOUT "
            "running _validate_features_uncached on B (id() reuse -> stale hit "
            "across a distinct, never-validated object)."
        )

    # Latent-not-active check: the served value matches B's own validation.
    if served != expected_for_b:
        # This would actually be an *active* wrong value (stronger than claimed).
        print(
            "[1] NOTE: served value DIFFERS from B's own validation "
            f"({served} != {expected_for_b}) -> would be an ACTIVE wrong value."
        )
    else:
        print(
            "[1] served value equals B's own validation -> content-equivalent "
            "TODAY; the hazard is latent (escalates if the payload ever grows "
            "model-specific fields)."
        )

    keepalive.clear()


def _part2_monkeypatch_id(failures: list[str]) -> None:
    print()
    print("=== PART 2: deterministic id() collision via permitted monkeypatch ===")
    _reset_caches()

    a = _fresh_model()
    b = _fresh_model()
    assert a is not b, "need two genuinely distinct objects"
    real_a_id = id(a)
    real_b_id = id(b)
    print(f"[2] distinct objects: real id(A)={real_a_id}, real id(B)={real_b_id}")

    # Shadow ``id`` inside the _model_scorer module so BOTH A and B map to the
    # SAME key component (simulates allocator reuse deterministically). The
    # builtin is untouched globally; only ms.* resolution is affected.
    COLLIDE = 0xC0FFEE
    had_attr = "id" in ms.__dict__
    saved = ms.__dict__.get("id")
    ms.id = lambda _obj: COLLIDE  # type: ignore[assignment]
    try:
        res_a = ms._validate_features(a, SCHEMA)
        print(f"[2] validated A under shadowed id -> {res_a} (key id-component={COLLIDE:#x})")

        with _UncachedSpy() as spy:
            res_b = ms._validate_features(b, SCHEMA)
        print(
            f"[2] _validate_features(B) -> {res_b}; uncached-worker invoked? "
            f"{len(spy.calls) > 0} (calls={spy.calls})"
        )

        if spy.calls:
            failures.append(
                "(2) uncached validator ran for B despite identical key -> the "
                "memoised fast path did NOT serve the stale entry"
            )
        else:
            print(
                "[2] PROVEN deterministically: B (a distinct, never-validated "
                "object) was served A's cached validation result purely because "
                "the (id, contract, schema) key collided — no per-object check."
            )

        # Confirm it really is A's cached tuple object that got served (identity,
        # not just equality) — the strongest statement of "A's result for B".
        if res_b is res_a:
            print("[2] served tuple is the SAME object A cached (is-identity).")
        elif res_b == res_a:
            print("[2] served tuple equals A's cached tuple (value-identity).")
        else:
            failures.append(
                "(2) served value for B does not match A's cached value — "
                "unexpected; weakens the mechanism claim"
            )
    finally:
        if had_attr:
            ms.id = saved  # type: ignore[assignment]
        else:
            del ms.id


def _control_cache_is_alive(failures: list[str]) -> None:
    print()
    print("=== CONTROL: cache is genuinely alive (distinct id -> validator runs) ===")
    _reset_caches()
    a = _fresh_model()
    ms._validate_features(a, SCHEMA)  # populate for A

    # A genuinely distinct id (no collision, no monkeypatch): same contract +
    # schema, but because the id component differs, the validator MUST run.
    b = _fresh_model()
    assert id(b) != id(a), "control requires distinct live ids"
    with _UncachedSpy() as spy:
        ms._validate_features(b, SCHEMA)
    ran_for_b = id(b) in spy.calls
    print(f"[C] distinct-id B ran the uncached validator? {ran_for_b} (must be True)")
    if not ran_for_b:
        failures.append(
            "(C) cache served a hit for a DISTINCT id -> key is too coarse "
            "(would over-broaden the claim); expected a real validation"
        )


def main() -> None:
    failures: list[str] = []
    try:
        _part1_real_idreuse(failures)
        _part2_monkeypatch_id(failures)
        _control_cache_is_alive(failures)
    finally:
        _reset_caches()

    print()
    if failures:
        print("RESULT: NOT reproduced — observed behaviour contradicts the claim:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print(
        "RESULT: REPRODUCED (mechanism) — _validate_features serves a cached "
        "(usable, missing) tuple to a DISTINCT, never-validated ScoringModel "
        "whenever id() is reused and the feature-contract + schema match, and "
        "the colliding instances (transient carrier / load_local_model "
        "artifacts) never enter _model_cache so the eviction cascade cannot "
        "clear them. The served value is content-equivalent TODAY (latent, not "
        "active, wrong value) — exactly the latent correctness hazard claimed."
    )


if __name__ == "__main__":
    main()
