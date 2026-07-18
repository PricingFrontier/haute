"""ISOLATED reproduction for BUG-2.

Claim: a training node config with ``row_limit: true`` (JSON boolean) is
accepted by ``_clamp_row_limit`` as the numeric limit 1, because
``isinstance(True, int)`` is True and ``int(True) == 1``. The downstream
``if row_limit:`` guard then fires (True is truthy) and
``_seeded_training_sample(lf, 1)`` silently trains the model on a SINGLE
random row -- no validation error is raised.

This repro imports the REAL production functions from
``haute.routes._train_service`` (read-only; nothing in src/ is modified)
and drives them with synthetic in-memory data only. It asserts on the
specific wrong values:
  * _clamp_row_limit(100, True) == 1   (bool accepted as numeric 1)
  * the resulting frame collapses from 100 rows to 1 row
  * by contrast _clamp_row_limit(100, False) leaves the limit untouched
    AND the `if row_limit:` consumption guard never fires for False.

A correct system would either reject a non-int (bool) row_limit loudly or
ignore it; instead it corrupts the training set to 1 row.

Run: uv run python review/03-simplification/repro/routes__BUG-2.py
"""

from __future__ import annotations

import sys

import polars as pl

from haute.routes._train_service import _clamp_row_limit, _seeded_training_sample


def _materialise(lf: pl.LazyFrame) -> pl.DataFrame:
    return lf.collect()


def main() -> int:
    # Synthetic 100-row "training" frame (in-memory; no disk I/O).
    n_source_rows = 100
    base = pl.LazyFrame({"feature": list(range(n_source_rows)), "target": list(range(n_source_rows))})
    assert _materialise(base).height == n_source_rows, "setup: base frame must have 100 rows"

    # Mimic the route logic at _train_service.py:458-459 and :888-889.
    #   user_limit = config.get("row_limit")   # comes straight from user JSON
    #   row_limit  = _clamp_row_limit(ram_row_limit, user_limit)
    #   ...
    #   if row_limit:
    #       target_lf = _seeded_training_sample(target_lf, row_limit)
    ram_row_limit = None  # RAM estimator did NOT impose a cap (plenty of memory)

    # ---- BUGGY INPUT: JSON `row_limit: true` reaches the clamp ----
    user_limit_true = True
    clamped_true = _clamp_row_limit(ram_row_limit, user_limit_true)
    print(f"[true ] _clamp_row_limit(None, True)  = {clamped_true!r} (type={type(clamped_true).__name__})")

    # The exact downstream consumption guard.
    if clamped_true:
        trained_true = _seeded_training_sample(base, clamped_true)
    else:
        trained_true = base
    trained_true_rows = _materialise(trained_true).height
    print(f"[true ] rows the model is trained on  = {trained_true_rows} (source had {n_source_rows})")

    # ---- CONTROL: JSON `row_limit: false` is falsy and harmless ----
    user_limit_false = False
    clamped_false = _clamp_row_limit(ram_row_limit, user_limit_false)
    if clamped_false:
        trained_false = _seeded_training_sample(base, clamped_false)
    else:
        trained_false = base
    trained_false_rows = _materialise(trained_false).height
    print(f"[false] _clamp_row_limit(None, False) = {clamped_false!r}; rows trained = {trained_false_rows}")

    # ---- ASSERTIONS on the specific wrong values ----
    failures: list[str] = []

    # Core of the bug: bool True is accepted as the numeric limit 1.
    if clamped_true != 1:
        failures.append(f"expected _clamp_row_limit(None, True) == 1, got {clamped_true!r}")
    # Python truthiness/typing facts the bug relies on.
    if not isinstance(True, int):
        failures.append("expected isinstance(True, int) is True")
    if int(True) != 1:
        failures.append("expected int(True) == 1")
    # The model is silently trained on a single random row.
    if trained_true_rows != 1:
        failures.append(f"expected training set collapsed to 1 row, got {trained_true_rows}")
    # Control: False does NOT corrupt training.
    if trained_false_rows != n_source_rows:
        failures.append(
            f"expected row_limit=False to leave all {n_source_rows} rows, got {trained_false_rows}"
        )

    if failures:
        print("\nREPRO RESULT: SETUP/EXPECTATION MISMATCH (not a clean reproduction)")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        "\nREPRO RESULT: CLAIM REPRODUCED -- JSON row_limit:true is silently "
        f"accepted as numeric 1; training set corrupted from {n_source_rows} rows to "
        f"{trained_true_rows} row with no validation error (row_limit:false is harmless)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
