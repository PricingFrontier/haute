"""Adversarial repro: normalise_rating_key (Python mirror) vs _rating_key_expr
(Polars twin) disagree on a non-integer Float32 value, producing a WRONG
matched/default flag in trace enrichment (_enrich_single_table).

Claim under test: rating-key-float32-mirror-twin-divergence.

Mechanism:
  * _rating_key_expr casts the Float32 column directly to Utf8 -> "0.1"
    (Polars formats f32 with its shortest round-trip repr).
  * normalise_rating_key receives the f32 cell already widened to a Python
    float (the f64 image of the f32 bits = 0.10000000149011612) because the
    trace layer jsonifies rows via _trace_correlation._jsonify_row /
    _json_safe.to_json_safe, which leaves finite floats unchanged. It then
    formats via a Float64 Utf8 cast -> "0.10000000149011612".
  * The two canonical forms differ, so the mirror-only trace path
    (_enrich_single_table) cannot match the entry that the engine matched.

We prove three independent facts, all by ASSERTING on exact values:

  (A) The raw twin/mirror divergence for Float32 0.1.
  (B) The engine (twin on both join sides) MATCHES a Float32 0.1 column
      against a JSON-f64 entry key 0.1 -> output value present.
  (C) Trace enrichment, fed the *jsonified* Float32 row exactly as the real
      trace pipeline produces it, MISREPORTS that matched row: it cannot
      find the matched_entry and reports status "unmatched_value" (matched
      flag True but no entry) instead of the engine-true "matched". With a
      default present it flips to "default" / matched=False — a hard wrong
      flag in the actuary-facing waterfall.

A pass (no AssertionError, exit 0) == bug reproduced.
"""

from __future__ import annotations

import struct
import sys

import polars as pl

from haute._json_safe import to_json_safe
from haute._rating import (
    _apply_rating_table,
    _rating_key_expr,
    normalise_rating_key,
)
from haute._trace_correlation import _jsonify_row
from haute._trace_enrichment import _enrich_single_table


def _f64_image_of_f32(x: float) -> float:
    """Return the Python float (f64) holding the bit pattern of float32(x)."""
    return struct.unpack("f", struct.pack("f", x))[0]


def _expr_canonical(series: pl.Series) -> str:
    frame = pl.DataFrame({"k": series})
    return frame.select(_rating_key_expr("k", frame.schema["k"]))["k"][0]


def main() -> None:
    # ----------------------------------------------------------------------
    # (A) Raw twin vs mirror divergence on a non-integer Float32 value.
    # ----------------------------------------------------------------------
    f32_series = pl.Series("k", [0.1], dtype=pl.Float32)
    twin_form = _expr_canonical(f32_series)

    # The trace layer never sees the Float32 dtype: it extracts the cell as a
    # Python float (the f64 image of the f32 bits) and jsonifies it. Reproduce
    # that exact value the way the trace pipeline does.
    f32_cell_as_python_float = f32_series.to_list()[0]
    widened = _f64_image_of_f32(0.1)
    assert f32_cell_as_python_float == widened, (
        f"sanity: f32 cell extracts as f64 image; got {f32_cell_as_python_float!r}"
    )
    # to_json_safe leaves finite floats unchanged -> mirror sees the widened f64.
    assert to_json_safe(f32_cell_as_python_float) == widened
    mirror_form = normalise_rating_key(f32_cell_as_python_float)

    print(f"(A) twin (f32 column cast) -> {twin_form!r}")
    print(f"(A) mirror (f64 image cast) -> {mirror_form!r}")
    assert twin_form == "0.1", f"expected twin '0.1', got {twin_form!r}"
    assert mirror_form == "0.10000000149011612", (
        f"expected mirror '0.10000000149011612', got {mirror_form!r}"
    )
    assert twin_form != mirror_form, "expected twin and mirror to DISAGREE"

    # ----------------------------------------------------------------------
    # (B) Engine ground truth: the twin is used on BOTH join sides, so a
    #     Float32 0.1 column MATCHES a JSON-f64 entry key 0.1.
    # ----------------------------------------------------------------------
    table_no_default = {
        "name": "F32 Score",
        "factors": ["score"],
        "outputColumn": "f",
        "onMissing": "neutral",
        "entries": [{"score": 0.1, "value": 9.0}],  # JSON f64 key, as authored
    }
    frame = pl.DataFrame({"score": pl.Series("score", [0.1], dtype=pl.Float32)})
    engine_out = _apply_rating_table(frame.lazy(), table_no_default).collect()
    engine_value = engine_out["f"].to_list()[0]
    print(f"(B) engine output value for f32 0.1 vs entry 0.1 -> {engine_value!r}")
    assert engine_value == 9.0, (
        f"engine should MATCH f32 0.1 to entry 0.1; got {engine_value!r}"
    )

    # ----------------------------------------------------------------------
    # (C) Trace enrichment fed the jsonified Float32 row MISREPORTS the match.
    #     input_row/output_row are built exactly as the real trace pipeline
    #     builds them: _jsonify_row over the engine frames.
    # ----------------------------------------------------------------------
    input_row = _jsonify_row(frame.row(0, named=True))
    output_row = _jsonify_row(engine_out.row(0, named=True))
    print(f"(C) jsonified input_row = {input_row!r}")
    detail = _enrich_single_table(table_no_default, input_row, output_row)
    print(
        "(C) no-default status="
        f"{detail['status']!r} matched={detail['matched']!r} "
        f"matched_entry={detail['matched_entry']!r}"
    )

    # The engine MATCHED (value 9.0 present, equals the only entry's value).
    # A faithful waterfall must report status 'matched' with the matched
    # entry identified. Instead the mirror cannot find the entry: it reports
    # 'unmatched_value' with matched_entry None -> a wrong, self-contradictory
    # flag (claims matched yet shows no matching entry).
    assert detail["status"] != "matched", (
        "EXPECTED BUG: mirror divergence should prevent a clean 'matched' "
        f"status, but got {detail['status']!r} (claim refuted)"
    )
    assert detail["matched_entry"] is None, (
        "EXPECTED BUG: mirror could not locate the engine-matched entry, "
        f"but matched_entry={detail['matched_entry']!r} (claim refuted)"
    )
    assert detail["status"] == "unmatched_value", (
        f"expected degraded 'unmatched_value' status, got {detail['status']!r}"
    )

    # ----------------------------------------------------------------------
    # (C2) With a usable defaultValue, the divergence flips the flag to a hard
    #      wrong 'default' / matched=False, even though the engine matched a
    #      real entry whose value happens to equal the default.
    # ----------------------------------------------------------------------
    table_with_default = {
        "name": "F32 Score (default)",
        "factors": ["score"],
        "outputColumn": "f",
        "defaultValue": 9.0,  # same value as the real entry
        "entries": [{"score": 0.1, "value": 9.0}],
    }
    engine_out2 = _apply_rating_table(frame.lazy(), table_with_default).collect()
    engine_value2 = engine_out2["f"].to_list()[0]
    assert engine_value2 == 9.0, f"engine should produce 9.0; got {engine_value2!r}"
    input_row2 = _jsonify_row(frame.row(0, named=True))
    output_row2 = _jsonify_row(engine_out2.row(0, named=True))
    detail2 = _enrich_single_table(table_with_default, input_row2, output_row2)
    print(
        "(C2) with-default status="
        f"{detail2['status']!r} matched={detail2['matched']!r} "
        f"default_used={detail2['default_used']!r}"
    )
    # Engine matched a REAL entry, so the truth is 'matched'. The mirror,
    # unable to match the f32-widened key, attributes the value to the
    # default -> reports 'default'/matched=False: a concrete wrong flag.
    assert detail2["status"] == "default", (
        "EXPECTED BUG: divergence should misattribute the matched row to the "
        f"default, but status={detail2['status']!r} (claim refuted)"
    )
    assert detail2["matched"] is False, (
        f"EXPECTED BUG: matched should be wrongly False; got {detail2['matched']!r}"
    )
    assert detail2["default_used"] is True

    # ----------------------------------------------------------------------
    # Control: a Float64 column with the same 0.1 does NOT diverge — proving
    # the bug is specific to the Float32 dtype erasure, not to 0.1 itself.
    # ----------------------------------------------------------------------
    frame64 = pl.DataFrame({"score": [0.1]})  # Float64
    out64 = _apply_rating_table(frame64.lazy(), table_no_default).collect()
    in64 = _jsonify_row(frame64.row(0, named=True))
    o64 = _jsonify_row(out64.row(0, named=True))
    detail64 = _enrich_single_table(table_no_default, in64, o64)
    print(f"(control) Float64 0.1 status={detail64['status']!r}")
    assert detail64["status"] == "matched", (
        "control: Float64 path must agree (status 'matched'); "
        f"got {detail64['status']!r}"
    )

    print()
    print("BUG REPRODUCED: Float32 non-integer key -> twin/mirror divergence "
          "-> wrong trace matched/default flag.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"REPRO FAILED (claim not substantiated): {exc}", file=sys.stderr)
        raise
