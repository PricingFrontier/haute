"""Adversarial reproduction for BUG-3.

Claim: ``_apply_rating_table`` deduplicates lookup rows on RAW factor values
(``unique(subset=factors, keep="last")`` at _rating.py:572) BEFORE the
``_rating_key_expr`` canonicalisation (line 583) that produces the actual
join key used at line 602.  The B14 comment (lines 571-572) asserts the
dedup guarantees "a left join cannot fan out rows", but it enforces that
invariant against PRE-canonical keys, not the post-canonical keys the join
uses.

The claim is that this is a LATENT defect:
  * No live fan-out is reproducible today, because for a homogeneous-dtype
    factor column ``_rating_key_expr`` never maps two DISTINCT pre-canonical
    values onto the same canonical key (Utf8 = verbatim cast = injective;
    Float int-like collapse 25.0->'25' maps only values already equal).
  * But the no-fan-out guarantee depends on that unasserted property.  If
    the canonicaliser ever gained a many-to-one rule on a homogeneous
    column, the dedup-then-canonicalise ORDER would silently start fanning
    out rows (inflating the frame, multiplying premium).

This script verifies BOTH halves with the REAL ``_apply_rating_table`` and
the REAL ``_rating_key_expr``:

  PART A  Source-order proof: confirm the dedup (unique) executes before the
          canonicalisation in the real call path, and that with the REAL
          canonicaliser there is NO fan-out today (so the bug is latent, not
          live).  Tried mixed str/int/float entry constructions: Polars
          coerces a factor column to a single dtype, so the column is
          homogeneous and no fan-out occurs.

  PART B  Latent-defect proof (the load-bearing part): drive the SAME
          ``_apply_rating_table`` body but with ``_rating_key_expr``
          monkeypatched to a canonicaliser that has a many-to-one rule on a
          homogeneous Utf8 column ("a"->"X" and "b"->"X").  Because the
          real code deduplicates BEFORE canonicalising, rows "a" and "b"
          (distinct pre-canonical, so both survive unique) both become "X"
          and the left join FANS OUT a single input row into 2 rows.  Then
          show that the robust fix (dedup AFTER canonicalisation) collapses
          them to 1 -- i.e. the ordering is exactly what makes the B14
          invariant false under a many-to-one canonical rule.

PART B does NOT assert a bug in shipped behaviour (the real canonicaliser
has no such rule).  It isolates the cause: the dedup/canonicalise ORDER.
A real bug-reproduction of the live behaviour is PART A's negative result
(no fan-out today) -- consistent with the claim's "latent" severity.

All data is synthetic in-memory; no disk I/O; src/tests/rating untouched.
Run:  uv run python review/03-simplification/repro/optimiser__BUG-3.py
"""

from __future__ import annotations

import polars as pl

import haute._rating as R
from haute._rating import _apply_rating_table


def _collect(lf):
    return lf.collect() if hasattr(lf, "collect") else lf


# ---------------------------------------------------------------------------
# PART A — live behaviour: ordering is dedup-before-canonicalise, and with the
# REAL canonicaliser there is NO fan-out today (latent, not live).
# ---------------------------------------------------------------------------
def part_a() -> None:
    print("=== PART A: real canonicaliser, attempt to force fan-out ===")

    # A1. Confirm the source ordering: unique() at the dedup line precedes the
    #     _rating_key_expr canonicalisation, which precedes the join.
    import inspect

    src = inspect.getsource(_apply_rating_table)
    i_unique = src.index('unique(subset=factors')
    i_canon = src.index('_rating_key_expr(f, lookup_schema[f])')
    i_join = src.index('.join(lookup.lazy()')
    assert i_unique < i_canon < i_join, (
        "expected dedup BEFORE canonicalisation BEFORE join"
    )
    print(f"[A1] source order: unique@{i_unique} < canonicalise@{i_canon} "
          f"< join@{i_join}  -> dedup runs on RAW factor values")

    # A2. Try the mixed-type entry construction the claim says collapses:
    #     str '25', int 25, float 25.0 for one factor.  Polars coerces the
    #     factor column to ONE dtype, so post-construction it is homogeneous;
    #     the real canonicaliser then cannot fan out.
    entries = [
        {"age": "25", "value": 1.0},
        {"age": 25, "value": 2.0},
        {"age": 25.0, "value": 3.0},
    ]
    coerced = pl.DataFrame(entries)
    print(f"[A2] pl.DataFrame(entries) coerced 'age' dtype = {coerced.schema['age']} "
          f"(homogeneous -> no mixed-type column survives)")

    table = {
        "factors": ["age"],
        "outputColumn": "age_factor",
        "entries": entries,
        "onMissing": "neutral",
    }
    # One input row whose canonical key is '25'.
    frame = pl.LazyFrame({"age": [25.0]})
    out = _collect(_apply_rating_table(frame, table)).sort("age")
    print(f"[A2] input rows = 1 ; output rows = {out.height} ; "
          f"age_factor = {out['age_factor'].to_list()}")
    assert out.height == 1, (
        f"LIVE FAN-OUT with real canonicaliser! got {out.height} rows -- "
        f"the bug would be live, not latent"
    )
    print("[A2] NO live fan-out with the real canonicaliser (claim: latent). OK")

    # A3. Homogeneous Float column with two int-like floats that the real
    #     canonicaliser would collapse only if they were equal; distinct
    #     floats stay distinct canonical -> no fan-out.
    entries2 = [{"x": 25.0, "value": 10.0}, {"x": 26.0, "value": 20.0}]
    table2 = {
        "factors": ["x"],
        "outputColumn": "xf",
        "entries": entries2,
        "onMissing": "neutral",
    }
    out2 = _collect(_apply_rating_table(pl.LazyFrame({"x": [25.0]}), table2))
    assert out2.height == 1
    print(f"[A3] distinct int-like floats 25.0/26.0 -> output rows = {out2.height} "
          f"(injective canonical, no fan-out). OK")

    print("[A2/A3] CONFIRMED: bug is LATENT -- ordering enforces the no-fan-out "
          "invariant against pre-canonical keys, but the real canonicaliser is "
          "injective per homogeneous dtype today, so no live fan-out.\n")


# ---------------------------------------------------------------------------
# PART B — latent-defect proof: a many-to-one canonical rule on a homogeneous
# column makes the dedup-before-canonicalise ORDER fan out the left join.
# ---------------------------------------------------------------------------
def _many_to_one_key_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """Stand-in canonicaliser with a MANY-TO-ONE rule on a Utf8 column.

    Maps distinct raw values 'a' and 'b' both onto 'X'.  This is the kind of
    rule the claim warns about (e.g. case-folding, trimming, locale collapse,
    a future int-like-string rule).  Everything else verbatim.
    """
    col = pl.col(name)
    if dtype == pl.Utf8:
        return (
            pl.when(col.is_in(["a", "b"]))
            .then(pl.lit("X"))
            .otherwise(col)
            .alias(name)
        )
    return col.cast(pl.Utf8).alias(name)


def _run_with_dedup_after(table, output_col, frame):
    """Re-implement the robust fix: dedup AFTER canonicalisation.

    Mirrors _apply_rating_table's lookup build but moves the unique() below
    the canonicalisation, so the dedup runs on the SAME (canonical) key the
    join uses.  Uses the patched _many_to_one_key_expr (already installed on
    R) so both variants see the identical canonical rule.
    """
    factors = table["factors"]
    entries = table["entries"]
    lookup = pl.DataFrame(entries).select([*factors, "value"])
    lookup = lookup.rename({"value": R._LOOKUP_VAL})
    schema = lookup.schema
    # Canonicalise FIRST ...
    lookup = lookup.with_columns(
        [R._rating_key_expr(f, schema[f]) for f in factors]
    )
    # ... THEN dedup on the canonical key (keep="last").
    lookup = lookup.unique(subset=factors, keep="last")
    fl = frame.with_columns(
        [R._rating_key_expr(f, frame.collect_schema()[f]) for f in factors]
    )
    joined = fl.join(lookup.lazy(), on=factors, how="left", maintain_order="left")
    joined = joined.with_columns(pl.col(R._LOOKUP_VAL).alias(output_col)).drop(
        R._LOOKUP_VAL
    )
    return _collect(joined)


def part_b() -> None:
    print("=== PART B: many-to-one canonical rule exposes the ordering defect ===")

    entries = [
        {"k": "a", "value": 1.0},  # distinct raw 'a'
        {"k": "b", "value": 2.0},  # distinct raw 'b'  -> both canonicalise to 'X'
    ]
    table = {
        "factors": ["k"],
        "outputColumn": "kf",
        "entries": entries,
        "onMissing": "neutral",
    }
    # One input row whose canonical key is 'X' (raw 'a').
    frame = pl.LazyFrame({"k": ["a"]})

    original = R._rating_key_expr
    R._rating_key_expr = _many_to_one_key_expr  # type: ignore[assignment]
    try:
        # SHIPPED ORDER (dedup before canonicalise): 'a' and 'b' both survive
        # unique() (distinct raw), then both -> 'X', so the join fans out.
        shipped = _collect(_apply_rating_table(frame, table)).sort("kf")
        # ROBUST FIX (dedup after canonicalise): 'a','b' -> 'X','X', unique
        # collapses to one 'X' row, so the join does NOT fan out.
        fixed = _run_with_dedup_after(table, "kf", frame)
    finally:
        R._rating_key_expr = original  # type: ignore[assignment]

    print(f"[B] SHIPPED order (dedup BEFORE canonicalise): input rows = 1, "
          f"output rows = {shipped.height}, kf = {shipped['kf'].to_list()}")
    print(f"[B] ROBUST fix (dedup AFTER canonicalise):    input rows = 1, "
          f"output rows = {fixed.height}, kf = {fixed['kf'].to_list()}")

    # The defect: shipped order fans 1 input row -> 2 rows under a many-to-one
    # canonical rule; the fix keeps it at 1.  This isolates the dedup/canon
    # ORDER as the cause of the (latent) broken B14 invariant.
    assert shipped.height == 2, (
        f"expected fan-out to 2 rows under shipped dedup-before-canon order, "
        f"got {shipped.height}"
    )
    assert fixed.height == 1, (
        f"expected NO fan-out under dedup-after-canon fix, got {fixed.height}"
    )
    print("[B] CONFIRMED: dedup-before-canonicalisation fans out (2 rows) where "
          "dedup-after-canonicalisation does not (1 row).")
    print("[B] -> The B14 'left join cannot fan out' invariant is enforced "
          "against PRE-canonical keys; it becomes false the moment the "
          "canonicaliser gains a many-to-one rule on a homogeneous column.\n")


if __name__ == "__main__":
    print(f"polars {pl.__version__}\n")
    part_a()
    part_b()
    print("RESULT: BUG-3 substantiated as LATENT.")
    print(" - Live behaviour today: NO fan-out (real canonicaliser injective "
          "per homogeneous dtype) -> PART A.")
    print(" - Latent defect: dedup runs on RAW factor values before "
          "canonicalisation, so the no-fan-out invariant depends on an "
          "unasserted property of _rating_key_expr; a future many-to-one "
          "canonical rule silently fans out the join -> PART B.")
