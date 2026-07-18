"""Adversarial repro for claim: group-temporal-split-fraction-drift.

Claim: hash-bucket group split (_group_mask/_assign_group_split) and temporal
holdout (_temporal_mask) give NO guarantee on the realised validation/holdout
ROW fraction. Per-group Bernoulli draw => with few / skewed groups the realised
row-fraction can be far from the requested validation_size, and the only
correction is a "force one group" fallback that fires solely when ZERO groups
land in validation (it never corrects an over- or under-shoot). Temporal holdout
sorts post-cutoff rows by arg_sort of dates with ties, so tie ordering decides
which equal-date rows fall in holdout vs validation.

This script ASSERTS on the specific wrong VALUE (realised fraction far from
requested; tie-dependent holdout membership), not merely that something raised.
"""

from __future__ import annotations

import polars as pl

from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    _assign_group_split,
    split_mask,
)


def realised_val_fraction(df: pl.DataFrame, cfg: SplitConfig) -> float:
    mask = split_mask(len(df), cfg, df=df)
    return float((mask == PARTITION_VALIDATION).sum()) / len(df)


# ---------------------------------------------------------------------------
# PART 1: Group split — realised validation row-fraction drifts far from the
# requested validation_size when groups are few and unequal in size.
# ---------------------------------------------------------------------------
print("=" * 72)
print("PART 1: group split realised fraction vs requested validation_size=0.2")
print("=" * 72)

# 5 groups of very unequal sizes. One giant group dominates the row count.
group_sizes = {"A": 600, "B": 250, "C": 100, "D": 40, "E": 10}  # total 1000 rows
n_total = sum(group_sizes.values())
grp_values: list[str] = []
for g, k in group_sizes.items():
    grp_values.extend([g] * k)
df_group = pl.DataFrame({"grp": grp_values, "x": list(range(n_total))})

requested = 0.2
worst_dev = 0.0
worst_seed = None
worst_frac = None
deviating_seeds = 0
n_seeds = 30
for seed in range(n_seeds):
    cfg = SplitConfig(
        strategy="group", group_column="grp", validation_size=requested, seed=seed
    )
    frac = realised_val_fraction(df_group, cfg)
    dev = abs(frac - requested)
    if dev > 0.10:  # more than 10 percentage points off the requested 20%
        deviating_seeds += 1
    if dev > worst_dev:
        worst_dev = dev
        worst_seed = seed
        worst_frac = frac

print(f"requested validation_size = {requested}")
print(f"group sizes               = {group_sizes}")
print(
    f"seeds with |realised - requested| > 0.10 : {deviating_seeds}/{n_seeds}"
)
print(
    f"WORST seed={worst_seed}: realised validation fraction = {worst_frac:.3f} "
    f"(deviation {worst_dev:.3f} from requested {requested})"
)

# Show one concrete seed where a single giant group lands in validation so the
# realised fraction blows well past the requested 0.2 (toward 0.6).
giant_seed = None
giant_frac = None
for seed in range(500):
    # _assign_group_split mirrors the validation-only group selection
    test_groups = _assign_group_split(list(group_sizes.keys()), requested, seed)
    if "A" in test_groups:  # the 600-row group (60% of rows)
        cfg = SplitConfig(
            strategy="group", group_column="grp", validation_size=requested, seed=seed
        )
        giant_frac = realised_val_fraction(df_group, cfg)
        giant_seed = seed
        break

assert giant_seed is not None, "Expected to find a seed routing giant group 'A' to validation"
print(
    f"\nGiant-group case: seed={giant_seed} routes 600-row group 'A' to validation"
)
print(
    f"  realised validation fraction = {giant_frac:.3f}  (requested {requested})"
)

# ASSERTIONS for Part 1: the realised fraction is demonstrably far from request.
assert giant_frac is not None
assert giant_frac >= 0.55, (
    f"Expected giant-group seed to push validation fraction >= 0.55, got {giant_frac}"
)
assert deviating_seeds > 0, (
    "Expected at least some seeds to deviate >10pp from requested fraction; "
    f"got {deviating_seeds}/{n_seeds}"
)
assert worst_dev > 0.10, (
    f"Expected worst-case deviation > 0.10, got {worst_dev}"
)
print("\nPART 1 ASSERTIONS PASSED: realised group-split fraction drifts far "
      "from requested with no correction.")


# ---------------------------------------------------------------------------
# PART 1b: confirm the fallback ONLY fires on ZERO validation groups and never
# corrects an over-/under-shoot. Pick a seed where >=1 group lands in validation
# but the realised fraction is still badly under (or over) target — fallback
# does nothing.
# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print("PART 1b: fallback does not correct over/under-shoot")
print("=" * 72)

# Find a seed where exactly the tiny group 'E' (10 rows) is the only validation
# group -> realised fraction ~0.01 despite requesting 0.2. Fallback is NOT
# triggered (a group *is* present), so the under-shoot stands.
under_seed = None
under_frac = None
for seed in range(2000):
    test_groups = _assign_group_split(list(group_sizes.keys()), requested, seed)
    if test_groups == {"E"}:  # only the 10-row group selected
        cfg = SplitConfig(
            strategy="group", group_column="grp", validation_size=requested, seed=seed
        )
        under_frac = realised_val_fraction(df_group, cfg)
        under_seed = seed
        break

if under_seed is not None:
    print(
        f"seed={under_seed}: only tiny group 'E' (10 rows) in validation -> "
        f"realised fraction = {under_frac:.3f} (requested {requested})"
    )
    assert under_frac is not None and under_frac < 0.02, (
        f"Expected severe under-shoot ~0.01, got {under_frac}"
    )
    print("PART 1b ASSERTION PASSED: a non-empty validation group still leaves "
          "the realised fraction ~0.01, and the fallback never corrects it.")
else:
    print("(no single-'E' seed found in range; under-shoot still demonstrated "
          "by Part 1 deviation stats)")


# ---------------------------------------------------------------------------
# PART 2: Temporal holdout tie-break dependence on arg_sort ordering.
#
# We demonstrate that the holdout/validation boundary lands among rows with
# IDENTICAL dates, so *which* equal-date rows become holdout vs validation is
# decided purely by arg_sort's tie ordering (a sort-stability/version detail),
# not by anything semantically meaningful. We prove this by permuting the input
# row order: with stable arg_sort the holdout set tracks original positions, so
# different input orderings of the same equal-date rows yield DIFFERENT holdout
# membership for the tied rows even though every post-cutoff row has the SAME
# date and is therefore semantically interchangeable.
# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print("PART 2: temporal holdout boundary among tied (identical) dates")
print("=" * 72)

# 4 pre-cutoff rows (train) + 10 post-cutoff rows all sharing ONE identical date.
# validation_size=0.4, holdout_size=0.4 => holdout_frac = 0.5 of the 10 tied
# post-cutoff rows => 5 of the 10 identical-date rows go to holdout, 5 to
# validation. Which 5? Decided by arg_sort tie order over equal keys.
pre = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]
tied = ["2024-09-01"] * 10
cfg_t = SplitConfig(
    strategy="temporal",
    date_column="date",
    cutoff_date="2024-06-01",
    validation_size=0.4,
    holdout_size=0.4,
)


def holdout_marker_set(order: list[int]) -> set[int]:
    """Build a df where each post-cutoff row carries a stable 'marker' id, in a
    given input row order; return the marker ids that land in holdout."""
    dates = pre + tied
    # marker: pre rows get -1..-4, tied rows get 0..9 (their identity)
    markers = [-(i + 1) for i in range(len(pre))] + list(range(len(tied)))
    df = pl.DataFrame({"date": dates, "marker": markers})
    # permute only the tied block per `order`
    tied_block = df.slice(len(pre), len(tied))
    tied_perm = tied_block[order]
    df = pl.concat([df.slice(0, len(pre)), tied_perm])
    mask = split_mask(len(df), cfg_t, df=df)
    holdout_markers = {
        m for m, p in zip(df["marker"].to_list(), mask.to_list()) if p == PARTITION_HOLDOUT
    }
    return holdout_markers


identity = list(range(10))
reversed_order = list(reversed(range(10)))

h_identity = holdout_marker_set(identity)
h_reversed = holdout_marker_set(reversed_order)

print(f"cutoff=2024-06-01  validation_size=0.4 holdout_size=0.4")
print(f"post-cutoff rows: 10 rows ALL dated 2024-09-01 (identical/tied)")
print(f"holdout markers (input order = identity ): {sorted(h_identity)}")
print(f"holdout markers (input order = reversed ): {sorted(h_reversed)}")

# Both runs must put 5 tied rows in holdout (size respected), but membership
# differs because the boundary cuts through identical-date rows.
n_total_t = len(pre) + len(tied)
mask_check = split_mask(n_total_t, cfg_t, df=pl.DataFrame(
    {"date": pre + tied, "marker": [-(i + 1) for i in range(len(pre))] + list(range(len(tied)))}
))
n_holdout = int((mask_check == PARTITION_HOLDOUT).sum())
print(f"holdout row count = {n_holdout} (of 10 tied rows)")

assert n_holdout == 5, f"Expected 5 tied rows in holdout, got {n_holdout}"
assert h_identity != h_reversed, (
    "Expected holdout membership among identical-date rows to depend on input "
    f"row order (tie ordering); got identical sets {h_identity}"
)
print(
    "\nPART 2 ASSERTION PASSED: among rows with IDENTICAL dates, which rows fall "
    "in holdout vs validation depends purely on arg_sort tie ordering / input "
    "row order — semantically interchangeable rows get different partitions."
)

print("\n" + "=" * 72)
print("ALL REPRO ASSERTIONS PASSED — claim behaviour demonstrated.")
print("=" * 72)
