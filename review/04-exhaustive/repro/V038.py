"""V038 reproduction — verbatim string factor labels that look like int-like
floats are silently corrupted on the compact->expand sidecar round trip.

Claim under test:
  * The compact-WRITE side keeps string factor labels VERBATIM:
        _canonical_sidecar_key("25.0") -> "25.0"
    because normalise_rating_key() never collapses string keys (its docstring
    promises: 'String keys are deliberately verbatim -- 25.0 is a label, not a
    number, and never collapses').
  * The expand-READ side applies a 'legacy float-string migration' that
    collapses any int-like float-string map key:
        _normalise_compact_sidecar_key("25.0") -> "25"
  * Therefore a genuine, non-numeric string label "25.0" stored in a row-array
    rating entry is silently rewritten to "25" after a single save (compact) +
    load (expand) cycle, and the rating lookup join (which keys on the exact
    label) STOPS MATCHING a frame whose string column value is "25.0".

This is an in-memory-only reproduction (no disk I/O, no project files). It
asserts on the specific WRONG VALUE (label "25.0" becomes "25"), not merely
that 'something raised'.
"""

from __future__ import annotations

import json

import polars as pl

from haute._rating import RatingTableMissError, apply_rating_step_from_config
from haute._rating_step_config import (
    _canonical_sidecar_key,
    _normalise_compact_sidecar_key,
    compact_rating_step_config_for_sidecar,
    expand_rating_step_config_from_sidecar,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 1) Helper-level asymmetry: write keeps "25.0"; read collapses to "25".
# ---------------------------------------------------------------------------
write_key = _canonical_sidecar_key("25.0")
read_key = _normalise_compact_sidecar_key("25.0")
print(f"_canonical_sidecar_key('25.0')        (WRITE) -> {write_key!r}")
print(f"_normalise_compact_sidecar_key('25.0') (READ)  -> {read_key!r}")

_assert(
    write_key == "25.0",
    f"WRITE side expected to keep verbatim '25.0', got {write_key!r}",
)
_assert(
    read_key == "25",
    f"READ side expected to collapse to '25' (bug), got {read_key!r}",
)
_assert(
    write_key != read_key,
    "asymmetry not present: write and read produced the same key",
)


# ---------------------------------------------------------------------------
# 2) Full round trip on a GENUINE single-factor string label "25.0".
#    Row-array entry -> compact (write) -> json dump/load -> expand (read).
#    The label must survive verbatim; the bug rewrites it to "25".
# ---------------------------------------------------------------------------
config_1f = {
    "tables": [
        {
            "name": "Band",
            "factors": ["band"],
            "outputColumn": "f",
            # Verbatim categorical/price-band string label that happens to
            # str-parse as an int-valued float. NOT a number entered as a float.
            "entries": [{"band": "25.0", "value": 7.0}],
        }
    ]
}

compact_1f = compact_rating_step_config_for_sidecar(config_1f)
print(f"\ncompact entries (1 factor): {compact_1f['tables'][0]['entries']!r}")
# Write side keeps the label verbatim as the JSON map key.
_assert(
    compact_1f["tables"][0]["entries"] == {"25.0": 7.0},
    f"compact write should keep '25.0' key verbatim, got "
    f"{compact_1f['tables'][0]['entries']!r}",
)

# Simulate the on-disk JSON sidecar round trip exactly (string-only object keys).
rehydrated_1f = expand_rating_step_config_from_sidecar(
    json.loads(json.dumps(compact_1f))
)
roundtrip_entries_1f = rehydrated_1f["tables"][0]["entries"]
print(f"rehydrated entries (1 factor): {roundtrip_entries_1f!r}")

roundtrip_label = roundtrip_entries_1f[0]["band"]
print(f"label after one save/load cycle: {roundtrip_label!r}  (was '25.0')")

_assert(
    roundtrip_label == "25",
    f"BUG NOT REPRODUCED: expected corrupted label '25', got {roundtrip_label!r}",
)
_assert(
    roundtrip_entries_1f != config_1f["tables"][0]["entries"],
    "round trip unexpectedly preserved the original entries",
)


# ---------------------------------------------------------------------------
# 3) Engine consequence: the corrupted table SILENTLY STOPS MATCHING a frame
#    whose string column value is the original verbatim label "25.0".
#    Before round trip: matches -> value 7.0.
#    After round trip:  table key is "25", frame value "25.0" -> MISS.
# ---------------------------------------------------------------------------
frame = pl.DataFrame({"band": ["25.0"]}).lazy()  # genuine string column

# Pre-round-trip config matches the string label exactly.
out_before = apply_rating_step_from_config(frame, config_1f).collect()
print(f"\npre-round-trip lookup f = {out_before['f'].to_list()!r}")
_assert(
    out_before["f"].to_list() == [7.0],
    f"pre-round-trip should match label '25.0' -> 7.0, got "
    f"{out_before['f'].to_list()!r}",
)

# Post-round-trip config no longer matches; default error policy => loud miss.
# (If it had a neutral default, this would instead be a silent wrong value.)
missed = False
try:
    apply_rating_step_from_config(frame, rehydrated_1f).collect()
except RatingTableMissError as exc:
    missed = True
    print(f"post-round-trip lookup raised RatingTableMissError: {exc}")
_assert(
    missed,
    "post-round-trip table unexpectedly still matched the string label '25.0'",
)


# ---------------------------------------------------------------------------
# 3b) Same defect surfaces as a SILENT WRONG VALUE (no error) when the table
#     carries a neutral default. The label "25.0" row is lost; the matching
#     frame row falls through to the default instead of 7.0.
# ---------------------------------------------------------------------------
config_default = {
    "tables": [
        {
            "name": "Band",
            "factors": ["band"],
            "outputColumn": "f",
            "defaultValue": -1.0,
            "onMissing": "neutral",
            "entries": [{"band": "25.0", "value": 7.0}],
        }
    ]
}
rehydrated_default = expand_rating_step_config_from_sidecar(
    json.loads(json.dumps(compact_rating_step_config_for_sidecar(config_default)))
)
out_default = apply_rating_step_from_config(frame, rehydrated_default).collect()
print(f"post-round-trip (neutral default) f = {out_default['f'].to_list()!r}")
_assert(
    out_default["f"].to_list() == [-1.0],
    f"expected silent fall-through to default -1.0 (lost 7.0 row), got "
    f"{out_default['f'].to_list()!r}",
)


# ---------------------------------------------------------------------------
# 4) Multi-factor variant: a 3-factor entry with string label "1.0" on the
#    first factor is likewise corrupted to "1" after the round trip.
# ---------------------------------------------------------------------------
config_3f = {
    "tables": [
        {
            "name": "ThreeWay",
            "factors": ["a", "b", "c"],
            "outputColumn": "f",
            "entries": [{"a": "1.0", "b": "North", "c": "online", "value": 4.0}],
        }
    ]
}
rehydrated_3f = expand_rating_step_config_from_sidecar(
    json.loads(json.dumps(compact_rating_step_config_for_sidecar(config_3f)))
)
entry_3f = rehydrated_3f["tables"][0]["entries"][0]
print(f"\n3-factor entry after round trip: {entry_3f!r}")
_assert(
    entry_3f["a"] == "1",
    f"3-factor: expected corrupted 'a' == '1', got {entry_3f['a']!r}",
)
# Non-numeric-looking labels on the same row are untouched (asymmetry is
# scoped to int-like float-strings), confirming the corruption is specific.
_assert(entry_3f["b"] == "North", f"'b' unexpectedly changed: {entry_3f['b']!r}")
_assert(entry_3f["c"] == "online", f"'c' unexpectedly changed: {entry_3f['c']!r}")


print("\nV038 REPRODUCED: verbatim string label '25.0' silently corrupted to "
      "'25' on compact->expand round trip; rating lookup stops matching.")
