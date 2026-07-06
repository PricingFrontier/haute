"""Isolated reproduction for V046.

Claim: build_report only realigns input_df to the prediction length inside the
`len(staging_preds) != len(prod_preds)` branch (src/haute/deploy/_impact.py:424-427).
When staging and production predictions unwrap to the SAME length L but L is
shorter than len(input_df), the branch is skipped, input_df keeps its full
height, and _segment_breakdown (line 360) builds
    pl.DataFrame({"seg": input_df[col], "chg": change, "stg": stg_vals, "prd": prd_vals})
with columns of mismatched height -> an unhandled polars ShapeError instead of a
domain error or a consistent truncation.

This is fully in-memory: build_report does no disk I/O and needs no project root,
so no tempfile / set_project_root is required.

The repro asserts on the SPECIFIC wrong behaviour: build_report raises
polars.exceptions.ShapeError originating from _segment_breakdown's DataFrame
construction (column 'chg' height L != column 'seg' height len(input_df)).
A correct implementation would either truncate input_df to L (so segments are
computed over the scored rows) or raise a clear domain error; it would NOT leak
a raw polars ShapeError.
"""

from __future__ import annotations

import traceback

import polars as pl
import polars.exceptions as plexc

from haute.deploy._impact import build_report

L = 12  # equal-length predictions returned by BOTH endpoints
N_INPUT = 20  # input rows submitted (L < N_INPUT)


def main() -> None:
    # Both endpoints return the SAME reduced row count (L == L == 12), so the
    # `len(staging_preds) != len(prod_preds)` guard at line 424 is skipped and
    # input_df is NOT truncated.
    staging_preds = [{"price": 110.0} for _ in range(L)]
    prod_preds = [{"price": 100.0} for _ in range(L)]

    # input_df has N_INPUT rows with a qualifying categorical column:
    # dtype Utf8, n_unique == 2 (in [2, 50]) -> _segment_breakdown will try to
    # build the mismatched DataFrame.
    input_df = pl.DataFrame({"region": ["A"] * (N_INPUT // 2) + ["B"] * (N_INPUT // 2)})

    assert len(staging_preds) == len(prod_preds) == L
    assert len(input_df) == N_INPUT
    assert len(input_df) > L, "precondition: input longer than equal-length preds"

    raised: BaseException | None = None
    report = None
    try:
        report = build_report(
            staging_preds,
            prod_preds,
            input_df,
            pipeline_name="t",
            staging_endpoint="s",
            prod_endpoint="p",
            dataset_path="d",
            total_rows=N_INPUT,
        )
    except BaseException as exc:  # noqa: BLE001 - we want to classify it
        raised = exc

    if raised is None:
        # No crash -> the alignment was handled. Report the values so a future
        # correct implementation is visible.
        assert report is not None
        print(
            f"NO BUG: build_report succeeded. scored_rows={report.scored_rows} "
            f"sampled_rows={report.sampled_rows} failed_rows={report.failed_rows} "
            f"segments={ {k: len(v) for k, v in report.segments.items()} }"
        )
        return

    tb = "".join(traceback.format_exception(type(raised), raised, raised.__traceback__))
    print(f"build_report raised: {type(raised).__module__}.{type(raised).__name__}: {raised}")

    # The bug predicts a polars ShapeError (height mismatch) leaking out of
    # _segment_breakdown's pl.DataFrame({...}) construction.
    is_shape_error = isinstance(raised, plexc.ShapeError)
    from_segment = "_segment_breakdown" in tb
    height_mismatch = "height" in str(raised).lower()

    assert is_shape_error, (
        f"Expected a polars ShapeError but got {type(raised).__name__}: {raised}\n{tb}"
    )
    assert from_segment, (
        f"Expected the error to originate in _segment_breakdown, traceback was:\n{tb}"
    )
    assert height_mismatch, (
        f"Expected a column-height mismatch message, got: {raised}"
    )

    print(
        "BUG REPRODUCED: equal-length predictions (L="
        f"{L}) shorter than input_df ({N_INPUT} rows) -> input_df left untruncated "
        "-> _segment_breakdown builds a DataFrame with column 'seg' height "
        f"{N_INPUT} != column 'chg' height {L} -> unhandled polars ShapeError."
    )


if __name__ == "__main__":
    main()
