"""Reproduction for V025.

Claim: in the continuous-banding path, _apply_banding sanitises NaN/Inf in a
Float column by reassigning the SAME source column name::

    lf = lf.with_columns(
        pl.when(col.is_nan() | col.is_infinite())
          .then(pl.lit(None))
          .otherwise(col)
          .alias(column)        # <-- same name as the input factor column
    )

Because the alias targets ``column`` (not a temporary), the frame returned to
callers carries a permanently mutated SOURCE column: every NaN / +/-Inf in the
input is silently replaced by null. The band output is a SEPARATE
``output_column``; the source-column nulling is a pure, silent side effect.
It only manifests when ``output_column != column`` (when equal, the final band
overwrite makes the intermediate moot).

This repro ISOLATES on pure in-memory Polars frames (no disk, no project root,
no rating/ files). It exercises BOTH:
  (1) the low-level ``_apply_banding`` helper, and
  (2) the public entry point ``apply_banding_from_config`` (dict config),
and ASSERTS on the specific wrong VALUE: the returned source column has null
where the input had Inf/NaN, while a correct (non-mutating) implementation
would leave the source column untouched and only derive the band column.

To prove the side effect is unnecessary for correctness of the band itself,
it also confirms the band column is computed correctly in BOTH the buggy
same-name case and a control where the sanitisation is done into a temp column.
"""

import math
import sys

import polars as pl

from haute._rating import _apply_banding, apply_banding_from_config


def _band_rules() -> list[dict]:
    # Two-sided continuous rules; finite inputs band normally.
    return [
        {"op1": "<=", "val1": 5, "assignment": "low"},
        {"op1": ">", "val1": 5, "assignment": "high"},
    ]


def main() -> None:
    inf = float("inf")
    nan = float("nan")

    # ------------------------------------------------------------------ #
    # 1. Low-level _apply_banding: output_column ("band") != column ("x") #
    # ------------------------------------------------------------------ #
    lf = pl.DataFrame({"x": [nan, 1.0, inf, 5.0, -inf, 8.0]}).lazy()
    out = _apply_banding(
        lf, "x", "band", "continuous", _band_rules(), default="dflt"
    ).collect()

    src_after = out["x"].to_list()
    band_after = out["band"].to_list()

    print(f"[low-level] source 'x' after banding : {src_after}")
    print(f"[low-level] band  'band' after banding: {band_after}")

    # The band column must be correct (this is the legitimate output).
    assert band_after == ["dflt", "low", "dflt", "low", "dflt", "high"], (
        f"band output unexpected: {band_after}"
    )

    # THE BUG: the SOURCE column 'x' has been mutated -- NaN/Inf replaced with
    # null. A correct implementation derives 'band' WITHOUT touching 'x'.
    # Positions 0 (NaN), 2 (+Inf), 4 (-Inf) are now None; finite values survive.
    null_mask = [v is None for v in src_after]
    assert null_mask == [True, False, True, False, True, False], (
        "BUG NOT PRESENT: expected source 'x' to have null at the NaN/Inf "
        f"positions (corruption); got null_mask={null_mask} src={src_after}"
    )
    # The surviving finite values are unchanged (confirms it's the NaN/Inf that
    # got dropped, not a wholesale column replacement).
    assert src_after[1] == 1.0 and src_after[3] == 5.0 and src_after[5] == 8.0, (
        f"finite values unexpectedly altered: {src_after}"
    )
    print(
        "[low-level] CONFIRMED: source column 'x' lost its NaN/Inf -> "
        "null at positions [0, 2, 4] as a silent side effect of banding."
    )

    # Sanity: the ORIGINAL frame did contain NaN / +Inf / -Inf, so the loss is
    # real data, not an artefact of construction.
    orig = lf.collect()["x"].to_list()
    assert math.isnan(orig[0]) and math.isinf(orig[2]) and math.isinf(orig[4]), (
        f"input did not contain NaN/Inf as expected: {orig}"
    )

    # ------------------------------------------------------------------ #
    # 2. Public entry point apply_banding_from_config (dict config).      #
    #    Exact scenario from the finding's evidence: outputColumn != col. #
    # ------------------------------------------------------------------ #
    config = {
        "factors": [
            {
                "banding": "continuous",
                "column": "si",
                "outputColumn": "si_band",
                "rules": [
                    {"op1": ">=", "val1": 0, "op2": "<", "val2": 1e9, "assignment": "band"},
                ],
            }
        ]
    }
    df_in = pl.DataFrame({"si": [inf, 100.0]}).lazy()
    df_out = apply_banding_from_config(df_in, config).collect()

    si_after = df_out["si"].to_list()
    si_band = df_out["si_band"].to_list()
    print(f"[public]    source 'si' after banding : {si_after}")
    print(f"[public]    band  'si_band'          : {si_band}")

    # The public API returns the frame with the corrupted SOURCE column: the
    # input +Inf is gone (replaced by null); the finite 100.0 survives and
    # bands to "band".
    assert si_after[0] is None, (
        "BUG NOT PRESENT: expected public apply_banding_from_config to null the "
        f"input +Inf in source 'si'; got {si_after}"
    )
    assert si_after[1] == 100.0, f"finite source value altered: {si_after}"
    assert si_band == [None, "band"], f"band output unexpected: {si_band}"
    print(
        "[public]    CONFIRMED: apply_banding_from_config returns a frame whose "
        "source 'si' lost +Inf -> null with no warning."
    )

    # ------------------------------------------------------------------ #
    # 3. Control: sanitising into a TEMP column would compute the SAME    #
    #    band while leaving the source column intact -> proves the        #
    #    source-column mutation is gratuitous, not required for the band. #
    # ------------------------------------------------------------------ #
    lf2 = pl.DataFrame({"x": [nan, 1.0, inf, 5.0, -inf, 8.0]}).lazy()
    tmp = pl.col("x")
    sanitized = (
        pl.when(tmp.is_nan() | tmp.is_infinite())
        .then(pl.lit(None))
        .otherwise(tmp)
        .alias("__x_sanitized__")
    )
    band_expr = (
        pl.when(pl.col("__x_sanitized__") <= 5)
        .then(pl.lit("low"))
        .when(pl.col("__x_sanitized__") > 5)
        .then(pl.lit("high"))
        .otherwise(pl.lit("dflt"))
        .alias("band")
    )
    control = (
        lf2.with_columns(sanitized)
        .with_columns(band_expr)
        .drop("__x_sanitized__")
        .collect()
    )
    control_src = control["x"].to_list()
    control_band = control["band"].to_list()
    # Source preserved exactly (NaN stays NaN, Inf stays Inf), band identical.
    assert math.isnan(control_src[0]) and math.isinf(control_src[2]), (
        f"control unexpectedly mutated source: {control_src}"
    )
    assert control_band == band_after, (
        f"control band differs from buggy band: {control_band} vs {band_after}"
    )
    print(
        "[control]   Sanitising into a temp column yields the SAME band "
        f"{control_band} while preserving source NaN/Inf -> mutation is gratuitous."
    )

    print(
        "\nV025 REPRODUCED: continuous banding silently overwrites the source "
        "float column, replacing NaN/Inf with null in the frame returned to "
        "callers (both _apply_banding and apply_banding_from_config)."
    )


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"REPRO ASSERTION FAILED (bug NOT demonstrated): {exc}", file=sys.stderr)
        sys.exit(1)
