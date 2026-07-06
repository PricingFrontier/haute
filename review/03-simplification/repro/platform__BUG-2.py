"""Adversarial reproduction for BUG-2.

Claim: haute._ram_estimate._csv_row_count returns max(physical_line_count - 1, 0)
over a raw binary line iterator, so it counts PHYSICAL LINES, not RFC-4180 CSV
RECORDS.  Two independent over-count modes:

  (A) A quoted field that embeds a newline (legal RFC-4180, common in free-text
      insurance fields like address/notes) spans >1 physical line but is ONE
      record.  _csv_row_count counts each embedded newline as an extra row.

  (B) [REFUTED sub-claim] A trailing blank line.  The claim said this also
      over-counts vs the real reader.  It does NOT: pl.scan_csv / pl.read_csv
      ALSO parse a trailing blank line as a (null) record, so estimator and
      Polars AGREE (both 4 for 3 data rows + 1 blank; both 5 for 2 blanks).
      There is no divergence vs the execution reader in this mode -- the
      auxiliary claim is false.  This script demonstrates that agreement
      explicitly so the refutation is on the record.

The execution-time reader haute._io uses (pl.scan_csv / pl.read_csv, RFC-4180
aware -- src/haute/_io.py:509,514) parses EMBEDDED-NEWLINE records correctly,
so for mode (A) the estimator's row count is LARGER than the number of rows
the pipeline actually processes.

This script asserts on the SPECIFIC wrong integers and contrasts them with the
correct Polars record count, proving the (A) divergence is real (not "Polars
also miscounts") while documenting that (B) is NOT a divergence.  It is fully
isolated: every CSV is written into a fresh tempfile.TemporaryDirectory; no
src/, tests/, or rating/ file is touched.

Run:  uv run python review/03-simplification/repro/platform__BUG-2.py
Exit 0 + "REPRO RESULT: CLAIM REPRODUCED" => bug confirmed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

from haute._ram_estimate import _csv_row_count


def _truth_polars(path: Path) -> int:
    """Records the execution-time reader (pl.scan_csv) actually yields.

    src/haute/_io.py:509 uses pl.scan_csv(path) for .csv sources; .collect() of
    pl.len() is the count of parsed RFC-4180 records (header excluded).
    """
    return int(pl.scan_csv(str(path)).select(pl.len()).collect().item())


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # ── Scenario A: quoted field embeds a newline ────────────────────────
        # Header + 2 DATA RECORDS.  Record 2's "notes" field contains a literal
        # newline inside quotes (RFC-4180 legal).  Physical lines = 4 (header,
        # rec1, rec2-line1, rec2-line2).  True records = 2.
        csv_a = tmpdir / "embedded_newline.csv"
        csv_a.write_bytes(
            b'quote_id,notes\n'
            b'1,"single line note"\n'
            b'2,"line one of address\nline two of address"\n'
        )

        a_est = _csv_row_count(str(csv_a))
        a_truth = _truth_polars(csv_a)
        print(f"[A] embedded-newline quoted field:")
        print(f"[A]   _csv_row_count (estimator)      = {a_est}")
        print(f"[A]   pl.scan_csv records (execution) = {a_truth}")
        # The bug: estimator counts the embedded newline as an extra record.
        if a_est != 3:
            failures.append(f"[A] expected estimator over-count 3, got {a_est}")
        if a_truth != 2:
            failures.append(f"[A] expected true record count 2, got {a_truth}")
        if not (a_est > a_truth):
            failures.append(f"[A] expected over-count (est {a_est} > truth {a_truth})")

        # ── Scenario B: trailing blank line (REFUTED sub-claim) ──────────────
        # Header + 3 DATA RECORDS + a trailing blank line.  _csv_row_count does
        # max(5-1,0)=4.  The claim said Polars would yield 3 (skip the blank);
        # in fact pl.scan_csv ALSO yields 4 -- it parses the trailing blank as a
        # null record -- so there is NO divergence vs the execution reader here.
        # We assert the AGREEMENT to put the refutation on the record.
        csv_b = tmpdir / "trailing_blank.csv"
        csv_b.write_bytes(
            b'quote_id,premium\n'
            b'1,100\n'
            b'2,200\n'
            b'3,300\n'
            b'\n'  # trailing blank line
        )

        b_est = _csv_row_count(str(csv_b))
        b_truth = _truth_polars(csv_b)
        print(f"[B] trailing blank line (refuted sub-claim):")
        print(f"[B]   _csv_row_count (estimator)      = {b_est}")
        print(f"[B]   pl.scan_csv records (execution) = {b_truth}")
        if b_est != 4:
            failures.append(f"[B] expected estimator 4, got {b_est}")
        # Refutation assertion: estimator and the execution reader AGREE, so the
        # trailing-blank sub-claim ("over-counts vs real reader") is FALSE.
        if b_est != b_truth:
            failures.append(
                f"[B] sub-claim refutation expected agreement, "
                f"est={b_est} truth={b_truth}"
            )

        # ── Control: plain CSV (no embedded newlines, no trailing blank) ─────
        # Estimator and Polars MUST agree here, proving the divergence above is
        # caused specifically by quoting/blank-line handling, not a constant
        # off-by-one in the harness.
        csv_c = tmpdir / "plain.csv"
        df = pl.DataFrame({"x": range(50)})
        df.write_csv(str(csv_c))
        c_est = _csv_row_count(str(csv_c))
        c_truth = _truth_polars(csv_c)
        print(f"[C] control plain CSV: estimator={c_est} polars={c_truth}")
        if c_est != 50 or c_truth != 50:
            failures.append(f"[C] control mismatch: est={c_est} truth={c_truth}")
        if c_est != c_truth:
            failures.append(f"[C] control should agree but est={c_est} truth={c_truth}")

    print()
    if failures:
        print("REPRO RESULT: NOT REPRODUCED (setup error or claim wrong)")
        for f in failures:
            print("  FAIL:", f)
        return 1

    print("REPRO RESULT: CLAIM REPRODUCED (core mode A only)")
    print(
        "  [A] CONFIRMED: _csv_row_count counts physical lines, so an embedded-"
        "newline quoted field over-counts (est 3) vs the RFC-4180 record count "
        "pl.scan_csv -- the execution reader, _io.py:509 -- yields (truth 2)."
    )
    print(
        "  [B] REFUTED: the trailing-blank-line sub-claim does NOT hold; "
        "pl.scan_csv also counts the trailing blank (est 4 == truth 4), so "
        "there is no divergence vs the real reader in that mode."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
