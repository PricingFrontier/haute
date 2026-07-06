"""Isolated reproduction for V094.

Claim: In _temporal_mask, when validation_size == 0 and holdout_size > 0,
holdout_frac = holdout_size / (validation_size + holdout_size) = 1.0, so
EVERY post-cutoff row becomes holdout regardless of the requested holdout_size.

This repro:
  1. Measures the ACTUAL temporal partition counts for
     validation_size=0.0, holdout_size=0.1 on a 20-row frame with 6 post-cutoff rows.
  2. Measures the random sibling under the IDENTICAL config for contrast.
  3. Asserts on the specific wrong VALUE (HOLDOUT count) the finder predicts.

No project files are read/written; only in-memory synthetic polars frames are used.
"""

import polars as pl

from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    split_mask,
)


def counts(mask: pl.Series) -> dict[int, int]:
    vals = mask.to_list()
    return {
        PARTITION_TRAIN: sum(1 for v in vals if v == PARTITION_TRAIN),
        PARTITION_VALIDATION: sum(1 for v in vals if v == PARTITION_VALIDATION),
        PARTITION_HOLDOUT: sum(1 for v in vals if v == PARTITION_HOLDOUT),
    }


# --- Build synthetic 20-row frame: 14 pre-cutoff, 6 post-cutoff ---------------
pre = ["2024-01-01"] * 14
post = ["2024-08-01", "2024-08-02", "2024-08-03", "2024-08-04", "2024-08-05", "2024-08-06"]
df = pl.DataFrame({"date": pre + post})
assert len(df) == 20

cutoff = "2024-06-01"
n_post = 6  # rows on/after cutoff

# --- TEMPORAL: validation_size=0, holdout_size=0.1 ----------------------------
temporal_cfg = SplitConfig(
    strategy="temporal",
    date_column="date",
    cutoff_date=cutoff,
    validation_size=0.0,
    holdout_size=0.1,
)
temporal_mask = split_mask(len(df), temporal_cfg, df=df)
t = counts(temporal_mask)
print(f"TEMPORAL (val=0.0, holdout=0.1): {t}")

# --- RANDOM: identical sizes --------------------------------------------------
random_cfg = SplitConfig(
    strategy="random",
    validation_size=0.0,
    holdout_size=0.1,
    seed=42,
)
random_mask = split_mask(len(df), random_cfg)
r = counts(random_mask)
print(f"RANDOM   (val=0.0, holdout=0.1): {r}")

# Fraction of WHOLE dataset implied by holdout_size=0.1 on 20 rows == 2.
holdout_size_rows_whole = int(len(df) * 0.1)
print(f"holdout_size=0.1 of whole 20-row dataset == {holdout_size_rows_whole} rows")
print(f"post-cutoff rows == {n_post} ({n_post / len(df):.0%} of dataset)")

# --- Assertions: the finder's predicted WRONG values --------------------------
# 1. Random honours holdout_size as a fraction of the whole dataset: HOLDOUT==2.
assert r[PARTITION_HOLDOUT] == 2, f"random holdout expected 2, got {r[PARTITION_HOLDOUT]}"
assert r[PARTITION_VALIDATION] == 0
assert r[PARTITION_TRAIN] == 18

# 2. Temporal swallows ALL 6 post-cutoff rows into holdout, ignoring holdout_size.
assert t[PARTITION_HOLDOUT] == n_post, (
    f"PREDICTED BUG: temporal holdout should be {n_post} (all post-cutoff) "
    f"but got {t[PARTITION_HOLDOUT]}"
)
assert t[PARTITION_VALIDATION] == 0
assert t[PARTITION_TRAIN] == 20 - n_post  # == 14

# 3. The realised temporal holdout fraction is driven by cutoff_date (6/20 = 30%),
#    NOT by the requested holdout_size (0.1 → 2 rows). Demonstrate divergence.
assert t[PARTITION_HOLDOUT] != holdout_size_rows_whole, (
    "If temporal honoured holdout_size like random, holdout would be 2, not 6"
)

print()
print("RESULT: temporal HOLDOUT =", t[PARTITION_HOLDOUT], "vs random HOLDOUT =", r[PARTITION_HOLDOUT])
print("Realised temporal holdout fraction =", t[PARTITION_HOLDOUT] / len(df), "(== post-cutoff frac)")
print("Requested holdout_size = 0.1")
print("CONFIRMED: when validation_size==0, temporal holdout == ALL post-cutoff rows.")
