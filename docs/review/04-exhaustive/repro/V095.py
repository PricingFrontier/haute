"""Reproduction for V095.

Claim: ``_group_mask`` in src/haute/modelling/_split.py:351-358 has a no-op
validation fallback. The 'ensure at least one validation group' correction
(lines 352-358) only flips a group whose current label is PARTITION_TRAIN.
When holdout_size is large relative to the few unique groups, it is possible
for EVERY group to hash below holdout_size and be assigned PARTITION_HOLDOUT.
The fallback loop then finds no TRAIN group, changes nothing, and breaks —
leaving validation EMPTY (despite validation_size > 0) AND train EMPTY
(every row is holdout). Training would then proceed on zero rows.

ISOLATION: this calls the pure in-memory function ``_group_mask`` only.
No disk I/O, no rating/, src/, tests/, or real project files are touched.
We build a tiny synthetic Polars DataFrame in memory.

The repro ASSERTS on the specific wrong partition counts:
  expected (correct behaviour): n_train >= 1 (so the engine never trains on
    an empty train set) and, given validation_size>0, n_validation >= 1.
  actual (the bug): n_train == 0 AND n_validation == 0 AND n_holdout == 5.
"""

import sys

import polars as pl

from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    _group_mask,
    split_mask,
)


def main() -> int:
    # Two unique groups, 5 rows total. Mirrors the candidate's example.
    df = pl.DataFrame({"g": ["A", "A", "B", "B", "B"]})
    seed = 2
    validation_size = 0.2
    holdout_size = 0.6

    # SplitConfig must be constructible (0.2 + 0.6 = 0.8 < 1, valid).
    config = SplitConfig(
        strategy="group",
        validation_size=validation_size,
        holdout_size=holdout_size,
        seed=seed,
        group_column="g",
    )

    # Call the real internal helper directly.
    mask = _group_mask(
        df,
        group_column="g",
        validation_size=validation_size,
        holdout_size=holdout_size,
        seed=seed,
    )

    # Also exercise the public entrypoint to confirm the same mask is produced
    # through the supported API surface (this is exactly what _split_data calls).
    mask_public = split_mask(len(df), config, df=df)

    n_train = int((mask == PARTITION_TRAIN).sum())
    n_validation = int((mask == PARTITION_VALIDATION).sum())
    n_holdout = int((mask == PARTITION_HOLDOUT).sum())

    pub_train = int((mask_public == PARTITION_TRAIN).sum())
    pub_validation = int((mask_public == PARTITION_VALIDATION).sum())
    pub_holdout = int((mask_public == PARTITION_HOLDOUT).sum())

    print(f"mask (internal _group_mask) = {mask.to_list()}")
    print(f"mask (public split_mask)    = {mask_public.to_list()}")
    print(
        f"internal counts: train={n_train} validation={n_validation} "
        f"holdout={n_holdout}"
    )
    print(
        f"public   counts: train={pub_train} validation={pub_validation} "
        f"holdout={pub_holdout}"
    )
    print(
        f"requested: validation_size={validation_size} (>0) "
        f"holdout_size={holdout_size}"
    )

    # Sanity: internal and public paths agree.
    assert mask.to_list() == mask_public.to_list(), (
        "internal _group_mask and public split_mask disagree — repro setup error"
    )

    # --- Demonstrate the SPECIFIC wrong values ---
    # The bug manifests as: every row holdout, train empty, validation empty.
    bug_present = (n_train == 0) and (n_validation == 0) and (n_holdout == 5)

    if bug_present:
        print(
            "\nBUG REPRODUCED: validation_size>0 was requested but the "
            "validation partition is EMPTY, and the train partition is ALSO "
            "EMPTY (all 5 rows -> holdout). The fallback at lines 352-358 fired "
            "(no_validation True, len(groups)=2>1) but found no PARTITION_TRAIN "
            "group to convert, so it was a no-op."
        )

    # Assertions encoding expected-vs-actual. These FAIL on current code,
    # demonstrating the wrong VALUES (not merely 'something raised').
    assert n_train >= 1, (
        f"EXPECTED at least one TRAIN row so the engine never trains on an "
        f"empty train set, but got n_train={n_train} (all rows holdout). "
        f"mask={mask.to_list()}"
    )
    assert n_validation >= 1, (
        f"EXPECTED at least one VALIDATION row because validation_size="
        f"{validation_size}>0 was requested, but got n_validation="
        f"{n_validation}. mask={mask.to_list()}"
    )

    print("No bug: train and validation both non-empty.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\nAssertionError: {exc}")
        sys.exit(1)
