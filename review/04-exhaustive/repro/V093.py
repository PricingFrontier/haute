"""Isolated reproduction for V093.

Claim (src/haute/modelling/_rustystats.py:88-95, ``_build_interactions``):
The ``include_main`` guard is all-or-nothing. It forces ``include_main=False``
ONLY when EVERY interaction factor already appears in the main ``terms`` dict.
On a PARTIAL overlap -- some factors already standalone main terms, at least one
not -- it forwards ``include_main=True`` for the WHOLE interaction. RustyStats'
``include_main=True`` re-adds the main effect for EVERY factor, so a factor that
is already a standalone main term gets its main effect added a SECOND time. The
docstring (lines 69-74) says this duplication causes "perfect collinearity and a
singular matrix error" -- exactly the failure the guard is meant to prevent.

This repro proves the claim three ways, all on small synthetic in-memory data
(no rating/, src/, tests/, or real project files touched; no project root
needed, so no disk I/O at all):

1. UNIT: ``_build_interactions`` forwards ``include_main=True`` on partial
   overlap (the wrong all-or-nothing decision).
2. STRUCTURAL: feeding that interaction through RustyStats'
   ``dict_to_parsed_formula`` yields ``parsed.main_effects`` with a DUPLICATE
   entry for the already-present factor, and ``InteractionBuilder`` then emits a
   design matrix with TWO IDENTICAL columns for that factor (the concrete wrong
   value). The all-overlap case (guard fires) does NOT duplicate.
3. END-TO-END: a real ``rs.glm_dict(...).fit()`` with the partial-overlap
   interaction RAISES (singular / rank-deficient), while the otherwise-identical
   all-overlap config fits cleanly -- isolating the duplicated main effect as the
   cause.
"""

from __future__ import annotations

import numpy as np
import polars as pl

import rustystats as rs
from rustystats.formula import dict_to_parsed_formula
from rustystats.interactions import InteractionBuilder

from haute.modelling._rustystats import _build_interactions


def _synthetic_df(n: int = 400) -> pl.DataFrame:
    rng = np.random.default_rng(0)
    age = rng.uniform(18, 70, n)
    region = rng.choice(["A", "B", "C"], n)
    # Poisson response loosely depending on age and region.
    region_eff = np.select(
        [region == "A", region == "B", region == "C"],
        [0.0, 0.3, -0.2],
    )
    lam = np.exp(-3.0 + 0.02 * age + region_eff)
    y = rng.poisson(lam)
    return pl.DataFrame({"age": age, "region": region, "claims": y})


def _column_names_for(parsed) -> list[str]:
    """Build the design matrix for a parsed formula and return its column names."""
    df = _synthetic_df()
    builder = InteractionBuilder(df)
    _y, _X, names = builder.build_design_matrix_from_parsed(parsed)
    return names, _X


def main() -> None:
    # ------------------------------------------------------------------
    # 1. UNIT: partial overlap -> include_main forwarded as True.
    # ------------------------------------------------------------------
    terms_partial = {"age": {"type": "linear"}}  # only `age` is a main term
    config = [{"factors": ["age", "region"], "include_main": True}]
    rs_int = _build_interactions(config, terms_partial)
    print("partial-overlap rs_interactions =", rs_int)
    assert len(rs_int) == 1
    assert rs_int[0]["include_main"] is True, (
        "expected guard to forward include_main=True on partial overlap"
    )

    # ------------------------------------------------------------------
    # 2. STRUCTURAL: duplicated main effect + duplicated design column.
    # ------------------------------------------------------------------
    # Partial overlap: terms has `age` (linear); interaction adds age*region
    # with include_main=True -> `age` main effect re-added.
    parsed_partial = dict_to_parsed_formula(
        response="claims",
        terms=terms_partial,
        interactions=rs_int,
        intercept=True,
    )
    print("partial main_effects =", parsed_partial.main_effects)
    age_count = parsed_partial.main_effects.count("age")
    assert age_count == 2, (
        "V093 NOT reproduced at parse layer: expected `age` to appear TWICE in "
        f"main_effects (standalone + re-added by include_main), got {age_count}: "
        f"{parsed_partial.main_effects}"
    )

    names_partial, X_partial = _column_names_for(parsed_partial)
    print("partial design column names =", names_partial)
    age_cols = [i for i, nm in enumerate(names_partial) if nm == "age"]
    assert len(age_cols) == 2, (
        "V093 NOT reproduced: expected TWO identical `age` columns in the design "
        f"matrix, got {len(age_cols)} (names={names_partial})"
    )
    # The two `age` columns are byte-for-byte identical -> rank deficiency.
    assert np.allclose(X_partial[:, age_cols[0]], X_partial[:, age_cols[1]]), (
        "the two `age` columns should be identical (perfect collinearity)"
    )
    print(
        "CONFIRMED duplicate design columns at indices",
        age_cols,
        "(identical -> singular).",
    )

    # Control: all-overlap (both factors are main terms) -> guard fires,
    # include_main forced False -> NO duplication.
    terms_full = {"age": {"type": "linear"}, "region": {"type": "categorical"}}
    rs_int_full = _build_interactions(
        [{"factors": ["age", "region"], "include_main": True}], terms_full
    )
    assert rs_int_full[0]["include_main"] is False
    parsed_full = dict_to_parsed_formula(
        response="claims",
        terms=terms_full,
        interactions=rs_int_full,
        intercept=True,
    )
    print("all-overlap main_effects =", parsed_full.main_effects)
    assert parsed_full.main_effects.count("age") == 1, (
        "control failed: all-overlap should NOT duplicate `age`"
    )

    # ------------------------------------------------------------------
    # 3. END-TO-END: real fit fails (singular) on partial overlap, succeeds
    #    on all-overlap. This is the exact failure the guard docstring claims
    #    to prevent.
    # ------------------------------------------------------------------
    df = _synthetic_df()

    # Control fit: all-overlap config fits cleanly.
    full_ok = True
    full_err = None
    try:
        rs.glm_dict(
            response="claims",
            terms=terms_full,
            data=df,
            family="poisson",
            interactions=rs_int_full,
        ).fit()
    except Exception as exc:  # pragma: no cover - control should succeed
        full_ok = False
        full_err = f"{type(exc).__name__}: {exc}"
    print("all-overlap fit ok? ", full_ok, "" if full_ok else f"({full_err})")
    assert full_ok, (
        "control fit (all-overlap, guard fires) unexpectedly failed; cannot "
        f"isolate the duplicate as the cause: {full_err}"
    )

    # Bug fit: partial-overlap config forwards include_main=True -> duplicate
    # `age` main effect -> singular / rank-deficient design.
    partial_raised = False
    partial_err = None
    try:
        rs.glm_dict(
            response="claims",
            terms=terms_partial,
            data=df,
            family="poisson",
            interactions=rs_int,
        ).fit()
    except Exception as exc:
        partial_raised = True
        partial_err = f"{type(exc).__name__}: {exc}"
    print("partial-overlap fit raised?", partial_raised, partial_err or "")

    assert partial_raised, (
        "V093 NOT reproduced end-to-end: the partial-overlap interaction "
        "(include_main forwarded True, duplicating the `age` main effect) was "
        "expected to produce a singular/rank-deficient fit, but fit() SUCCEEDED. "
        "The duplicate columns were confirmed at the design-matrix layer above, "
        "so if this fires the singularity is masked by a pseudo-inverse rather "
        "than prevented by the guard."
    )

    print(
        "\nV093 REPRODUCED: partial factor overlap forwards include_main=True, "
        "duplicates the already-present `age` main effect (two identical design "
        "columns), and the GLM fit fails with a singular/rank-deficient error -- "
        "exactly the failure _build_interactions' guard claims to prevent."
    )


if __name__ == "__main__":
    main()
