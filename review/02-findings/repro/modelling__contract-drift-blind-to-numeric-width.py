"""Adversarial reproduction for claim ``contract-drift-blind-to-numeric-width``.

Claim under test
----------------
The feature-contract drift gate (``assert_contracts_match``) cannot detect
Float32-vs-Float64 / Int32-vs-Int64 schema drift, nor String<->Categorical
drift, because BOTH the training-time canonicaliser
(``haute.modelling._training_job._polars_dtype_name``) and the deploy-time
canonicaliser (``haute.deploy._scorer._canonical_dtype``) collapse every
integer width to ``"Int64"``, every float width to ``"Float64"``, and both
``String`` and ``Categorical`` to ``"String"``. Since the contract stores
only those canonical strings in ``feature_types`` and ``assert_contracts_match``
compares those strings, the advertised guarantee ("dtype change fails loudly")
does not hold at sub-family granularity.

Strategy
--------
This is a pure in-memory exercise of the canonicalisers + contract builder +
drift gate. NO model training, NO real project files, NO rating/ or src/ or
tests/ access. We assert on the *specific wrong values/behaviour*:

  (A) ``_canonical_dtype(pl.Float32) == "Float64"``           (deploy side)
  (B) ``_polars_dtype_name(pl.Float32) == "Float64"``         (training side)
  (C) a Float64-trained contract vs a Float32-runtime contract
      passes ``assert_contracts_match`` WITHOUT raising.
  (D) the same for Int64-vs-Int32.
  (E) the same for String(trained)-vs-Categorical(runtime).

If (A)-(E) all hold, the drift gate is demonstrably blind to sub-family
numeric-width and String/Categorical drift -> claim REPRODUCED.

As a control we also confirm a *cross-family* change (Float64 -> Int64) IS
detected, proving the gate is not simply inert.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import polars as pl

# Keep any incidental project-root lookups inside a throwaway temp dir so we
# never touch the real repo tree.
import haute._sandbox as _sandbox

_TMP = Path(tempfile.mkdtemp(prefix="contract_width_repro_"))
_sandbox.set_project_root(_TMP)

from haute.deploy._scorer import _canonical_dtype
from haute.modelling._training_job import _polars_dtype_name
from haute.modelling._feature_contract import (
    FeatureMismatchError,
    assert_contracts_match,
    build_contract,
)


def _build(feature_types: dict[str, str]) -> object:
    """Build a minimal contract with the given feature dtype strings."""
    return build_contract(
        features=list(feature_types),
        feature_types=feature_types,
        categorical_features=[],
        target_name="loss",
        target_type="Float64",
        task="regression",
    )


def _passes(expected: object, actual: object) -> bool:
    """Return True iff the drift gate does NOT raise (i.e. no drift detected)."""
    try:
        assert_contracts_match(expected, actual)
        return True
    except FeatureMismatchError:
        return False


def main() -> None:
    failures: list[str] = []

    # ---- (A) deploy-side canonicaliser collapses Float32 -> "Float64" -------
    a_f32 = _canonical_dtype(pl.Float32)
    a_f64 = _canonical_dtype(pl.Float64)
    print(f"[A] _canonical_dtype(Float32)={a_f32!r}  _canonical_dtype(Float64)={a_f64!r}")
    if not (a_f32 == "Float64" == a_f64):
        failures.append(f"(A) expected both Float32 and Float64 -> 'Float64', got {a_f32!r}/{a_f64!r}")

    # deploy-side Categorical collapses to "String"
    a_cat = _canonical_dtype(pl.Categorical)
    a_str = _canonical_dtype(pl.Utf8)
    print(f"[A] _canonical_dtype(Categorical)={a_cat!r}  _canonical_dtype(Utf8)={a_str!r}")
    if not (a_cat == "String" == a_str):
        failures.append(f"(A) expected Categorical and Utf8 -> 'String', got {a_cat!r}/{a_str!r}")

    # ---- (B) training-side canonicaliser collapses Float32 -> "Float64" -----
    b_f32 = _polars_dtype_name(pl.Float32)
    b_i32 = _polars_dtype_name(pl.Int32)
    print(f"[B] _polars_dtype_name(Float32)={b_f32!r}  _polars_dtype_name(Int32)={b_i32!r}")
    if b_f32 != "Float64":
        failures.append(f"(B) expected Float32 -> 'Float64', got {b_f32!r}")
    if b_i32 != "Int64":
        failures.append(f"(B) expected Int32 -> 'Int64', got {b_i32!r}")

    # ---- (C) Float64-trained vs Float32-runtime: drift gate is BLIND --------
    # Training pinned x as Float64 (via _polars_dtype_name(Float64)).
    trained_f = _build({"x": _polars_dtype_name(pl.Float64)})
    # At score time the live schema delivers x as Float32; the runtime contract
    # is rebuilt with the deploy canonicaliser exactly as _scorer does.
    runtime_f = _build({"x": _canonical_dtype(pl.Float32)})
    blind_float = _passes(trained_f, runtime_f)
    print(
        f"[C] trained feature_types={trained_f.feature_types} "
        f"runtime feature_types={runtime_f.feature_types} "
        f"drift_gate_passes(no error)={blind_float}"
    )
    if not blind_float:
        failures.append("(C) expected Float64-vs-Float32 to slip past the drift gate, but it raised")
    # contract_hash must also collide for the artifacts to be indistinguishable.
    if trained_f.contract_hash != runtime_f.contract_hash:
        failures.append(
            "(C) expected identical contract_hash for Float64-vs-Float32 "
            f"(hashes {trained_f.contract_hash[:12]} vs {runtime_f.contract_hash[:12]})"
        )

    # ---- (D) Int64-trained vs Int32-runtime: drift gate is BLIND ------------
    trained_i = _build({"n": _polars_dtype_name(pl.Int64)})
    runtime_i = _build({"n": _canonical_dtype(pl.Int32)})
    blind_int = _passes(trained_i, runtime_i)
    print(
        f"[D] trained feature_types={trained_i.feature_types} "
        f"runtime feature_types={runtime_i.feature_types} "
        f"drift_gate_passes(no error)={blind_int}"
    )
    if not blind_int:
        failures.append("(D) expected Int64-vs-Int32 to slip past the drift gate, but it raised")

    # ---- (E) String-trained vs Categorical-runtime: drift gate is BLIND -----
    trained_s = _build({"cat": _polars_dtype_name(pl.Utf8)})
    runtime_c = _build({"cat": _canonical_dtype(pl.Categorical)})
    blind_strcat = _passes(trained_s, runtime_c)
    print(
        f"[E] trained feature_types={trained_s.feature_types} "
        f"runtime feature_types={runtime_c.feature_types} "
        f"drift_gate_passes(no error)={blind_strcat}"
    )
    if not blind_strcat:
        failures.append("(E) expected String-vs-Categorical to slip past the drift gate, but it raised")

    # ---- CONTROL: a genuine cross-family change MUST be detected ------------
    trained_ctrl = _build({"x": _polars_dtype_name(pl.Float64)})
    runtime_ctrl = _build({"x": _canonical_dtype(pl.Int64)})
    control_passes = _passes(trained_ctrl, runtime_ctrl)
    print(
        f"[CONTROL] Float64-vs-Int64 drift_gate_passes(no error)={control_passes} "
        "(must be False -> gate is alive)"
    )
    if control_passes:
        failures.append(
            "(CONTROL) Float64-vs-Int64 should be DETECTED; gate let it through "
            "-> gate may be inert, weakening the targeted claim"
        )

    print()
    if failures:
        print("RESULT: NOT reproduced — the drift gate behaved differently than claimed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print(
        "RESULT: REPRODUCED — assert_contracts_match passed for "
        "Float64-vs-Float32, Int64-vs-Int32, and String-vs-Categorical "
        "(sub-family / String<->Categorical drift is invisible), while the "
        "cross-family Float64-vs-Int64 control was correctly detected."
    )


if __name__ == "__main__":
    main()
