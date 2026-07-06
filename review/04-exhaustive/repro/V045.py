"""Isolated reproduction for V045.

Claim: build_report reports post-truncation length as sampled_rows, breaking
the invariant sampled_rows == scored_rows + failed_rows when staging and
production prediction lists differ in length.

The intended meaning of sampled_rows (the count of rows submitted for scoring)
is established by the sibling first-deploy path in src/haute/cli/_impact.py:141-143:
    sampled_rows = len(records)                     # full input submitted
    scored_rows  = len(staging_preds)               # how many came back
    failed_rows  = len(records) - len(staging_preds)
=> sampled_rows == scored_rows + failed_rows.

This repro is fully in-memory: build_report does no disk I/O and needs no
project root, so no tempfile / set_project_root is required. It asserts on the
specific wrong VALUE (sampled_rows) and on the broken invariant.
"""

from __future__ import annotations

import polars as pl

from haute.deploy._impact import build_report


def main() -> None:
    # Staging returned 1 prediction, production returned 2. Two rows were
    # submitted (input_df has 2 rows), so 1 row "failed" (mismatched lengths).
    staging_preds = [{"price": 110.0}]
    prod_preds = [{"price": 100.0}, {"price": 200.0}]
    input_df = pl.DataFrame({"x": ["a", "b"]})

    report = build_report(
        staging_preds,
        prod_preds,
        input_df,
        pipeline_name="t",
        staging_endpoint="s",
        prod_endpoint="p",
        dataset_path="d",
        total_rows=2,
    )

    print(
        f"sampled_rows={report.sampled_rows} "
        f"scored_rows={report.scored_rows} "
        f"failed_rows={report.failed_rows}"
    )

    # scored/failed are already correct and asserted by the existing test.
    assert report.scored_rows == 1, report.scored_rows
    assert report.failed_rows == 1, report.failed_rows

    # The bug: sampled_rows is the POST-truncation length (== scored == 1),
    # not the number of rows actually submitted for scoring (== 2).
    invariant_holds = report.sampled_rows == report.scored_rows + report.failed_rows
    print(
        f"invariant (sampled == scored + failed): {invariant_holds} "
        f"({report.sampled_rows} == {report.scored_rows} + {report.failed_rows})"
    )

    # Demonstrate the precise wrong value: expected 2, actual 1.
    expected_sampled = 2  # input_df originally had 2 rows -> 2 rows submitted
    assert report.sampled_rows == expected_sampled, (
        f"BUG REPRODUCED: sampled_rows={report.sampled_rows} "
        f"(post-truncation length) but expected {expected_sampled} "
        f"(rows submitted for scoring). Invariant sampled==scored+failed is "
        f"{report.sampled_rows} == {report.scored_rows}+{report.failed_rows} "
        f"-> {invariant_holds}."
    )

    print("NO BUG: sampled_rows is correct and the invariant holds.")


if __name__ == "__main__":
    main()
