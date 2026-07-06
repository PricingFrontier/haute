"""Adversarial repro for claim: auto-range-null-batch-drop-asymmetry-latent.

Claim: ``_ScenarioFrontierRangeAccumulator.add_batch`` returns early for the
ENTIRE batch when ``batch[quote_id].null_count() > 0``, incrementing
``null_quote_id_count`` by only the null count while silently EXCLUDING the
co-batched NON-null quotes from the min/max envelope. ``finish()`` then raises
because ``null_quote_id_count > 0``.

The asymmetry under test (independent of the upstream validation that masks it
in production):

  * accounting: ``null_quote_id_count`` is bumped by ONLY the null-row count.
  * exclusion : ALL rows in the tainted batch (including valid quotes) are
    dropped from the bucket files / envelope.

This script isolates the accumulator directly (the documented repro strategy),
bypassing ``_validate_and_project_auto_range`` which would otherwise raise 400
before the accumulator ever sees null data.

ISOLATION: only uses Python tempfile for the accumulator's parts_root. No
reads/writes of rating/, src/, tests/, or any real project file. No project
root is required by the accumulator constructor.

Run:
    uv run python review/02-findings/repro/optimiser__auto-range-null-batch-drop-asymmetry-latent.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import polars as pl

from haute.routes._optimiser_service import (
    _NULL_QUOTE_ID_DETAIL_PREFIX,
    _ScenarioFrontierRangeAccumulator,
)


def _count_bucket_part_files(parts_root: Path) -> int:
    """Number of parquet part files the accumulator actually wrote."""
    return len(list(parts_root.rglob("*.parquet")))


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="haute_repro_auto_range_") as raw_dir:
        parts_root = Path(raw_dir)
        acc = _ScenarioFrontierRangeAccumulator(
            quote_id_col="quote_id",
            constraint_cols=["loss"],
            partition_count=8,
            parts_root=parts_root,
        )

        # One read-batch: TWO valid quotes (q1, q2) + ONE null quote_id.
        # In a correct, non-fatal handling the two valid quotes would still be
        # accounted in the envelope and only the single null row dropped.
        tainted_batch = pl.DataFrame(
            {
                "quote_id": ["q1", "q2", None],
                "loss": [10.0, 20.0, 99.0],
            }
        )
        valid_rows = tainted_batch.filter(pl.col("quote_id").is_not_null()).height  # 2
        null_rows = int(tainted_batch["quote_id"].null_count())  # 1

        acc.add_batch(tainted_batch, batch_index=0)

        # ---- Assertion 1: accounting covers ONLY the null rows -----------------
        # If the accumulator counted dropped *valid* rows too, this would be 3.
        # The bug: it only records the null count.
        if acc.null_quote_id_count != null_rows:
            failures.append(
                f"[setup] expected null_quote_id_count == {null_rows} (null rows only), "
                f"got {acc.null_quote_id_count}"
            )

        # row_count, by contrast, was incremented by the FULL batch height (3),
        # so the accumulator *knows* 3 rows arrived but accounts only 1 as a
        # problem and silently swallows the other 2 valid quotes.
        if acc.row_count != tainted_batch.height:
            failures.append(
                f"[setup] expected row_count == {tainted_batch.height} (full batch), "
                f"got {acc.row_count}"
            )

        # ---- Assertion 2: the WHOLE batch was excluded, not just the null row --
        # This is the core asymmetry. The two valid quotes (q1, q2) must have
        # produced bucket part files if they were accounted. They did NOT.
        part_files = _count_bucket_part_files(parts_root)
        if part_files != 0:
            failures.append(
                f"[core] expected 0 bucket part files (whole batch dropped), got {part_files}"
            )
        if acc.bucket_files != {}:
            failures.append(
                f"[core] expected bucket_files == {{}} (no valid quotes persisted), "
                f"got {acc.bucket_files!r}"
            )

        # ---- Control: a CLEAN batch with the SAME valid quotes IS persisted ----
        # Proves the absence above is caused by the null co-batching, not by the
        # quotes being inherently unwritable.
        with tempfile.TemporaryDirectory(prefix="haute_repro_auto_range_ctl_") as ctl_dir:
            ctl_root = Path(ctl_dir)
            ctl = _ScenarioFrontierRangeAccumulator(
                quote_id_col="quote_id",
                constraint_cols=["loss"],
                partition_count=8,
                parts_root=ctl_root,
            )
            clean_batch = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "loss": [10.0, 20.0],
                }
            )
            ctl.add_batch(clean_batch, batch_index=0)
            ctl_part_files = _count_bucket_part_files(ctl_root)
            if ctl_part_files == 0:
                failures.append(
                    "[control] expected >0 bucket part files for a clean batch with the "
                    "same valid quotes, got 0 (would invalidate the comparison)"
                )

        # ---- Assertion 3: finish() raises on the null guard --------------------
        raised_detail: str | None = None
        try:
            acc.finish()
        except ValueError as exc:
            raised_detail = str(exc)
        if raised_detail is None:
            failures.append("[fail-only] expected finish() to raise ValueError, it did not")
        elif _NULL_QUOTE_ID_DETAIL_PREFIX not in raised_detail:
            failures.append(
                "[fail-only] expected finish() ValueError to mention the null-quote_id "
                f"detail prefix, got: {raised_detail!r}"
            )

    print("=" * 72)
    print("REPRO: auto-range null-batch drop asymmetry (accumulator-isolated)")
    print("=" * 72)
    print(f"  valid rows in tainted batch        : {valid_rows}")
    print(f"  null  rows in tainted batch        : {null_rows}")
    print(f"  accumulator.row_count              : {acc.row_count}  (full batch counted)")
    print(f"  accumulator.null_quote_id_count    : {acc.null_quote_id_count}  (ONLY null rows)")
    print(f"  bucket part files written          : {part_files}  (valid quotes DROPPED)")
    print(f"  accumulator.bucket_files           : {acc.bucket_files!r}")
    print(f"  finish() raised detail             : {raised_detail!r}")
    print("-" * 72)

    if failures:
        print("RESULT: claim NOT reproduced as stated (asymmetry differs from prediction):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("RESULT: claim REPRODUCED.")
    print(
        "  A single tainted read-batch (2 valid + 1 null quote_id) was dropped in\n"
        "  its ENTIRETY: 0 valid quotes persisted to any bucket, yet only the 1\n"
        "  null row was accounted via null_quote_id_count. finish() then fails\n"
        "  loudly. The dropped-valid-quote exclusion is masked today only by the\n"
        "  upstream _validate_input_value_contracts(validate_quote_id_nulls=True)\n"
        "  scan; the accumulator itself is fail-only with asymmetric accounting."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
