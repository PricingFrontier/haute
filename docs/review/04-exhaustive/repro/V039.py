"""Reproduction for V039.

Claim: ``compact_rating_step_config_for_sidecar`` can emit a sidecar that
``expand_rating_step_config_from_sidecar`` then REJECTS (write/read asymmetry)
in ``src/haute/_rating_step_config.py``.

Root cause: the compact-WRITE key normaliser ``_canonical_sidecar_key``
(line 44, -> ``normalise_rating_key``) treats a *string* factor value as a
verbatim label ("5." stays "5."), while the compact-READ normaliser
``_normalise_compact_sidecar_key`` (line 61) applies an int-like float-string
migration ("5." -> float 5.0 -> "5"). So two row labels that differ only in
float-string formatting ("5." vs "5") are written as two DISTINCT, colliding-
free map keys, but on load both migrate to "5" and raise a collision
ValueError. The save SUCCEEDS yet produces a JSON sidecar that can never be
loaded again.

ISOLATION: the two public functions are pure dict->dict transforms. Everything
is built in-memory from a synthetic ratingStep config. No disk I/O; no reads or
writes of rating/, src/, tests/, or any real project file. (normalise_rating_key
uses an in-memory ``pl.Series`` only for non-int-like floats; the int-like
branch we exercise never touches disk.)

We assert on the SPECIFIC behaviour and VALUES:
  (1) write path returns a compact map whose top-level keys are EXACTLY
      {"5.", "5"} (two distinct keys -> no duplicate raised on write);
  (2) feeding that exact compact map to the read path RAISES ValueError whose
      message names the post-migration collision on key "5";
  (3) the two helper contracts disagree on "5.":
      _canonical_sidecar_key("5.") == "5."  vs
      _normalise_compact_sidecar_key("5.") == "5".
"""

from __future__ import annotations

from haute._rating_step_config import (
    _canonical_sidecar_key,
    _normalise_compact_sidecar_key,
    compact_rating_step_config_for_sidecar,
    expand_rating_step_config_from_sidecar,
)

failures: list[str] = []


def expected_eq(label: str, actual: object, expected: object) -> None:
    ok = actual == expected
    print(f"[{'ok' if ok else 'MISMATCH'}] {label}: actual={actual!r} expected={expected!r}")
    if not ok:
        failures.append(f"{label}: expected {expected!r} got {actual!r}")


# A single-factor table with two band labels that differ ONLY in float-string
# formatting: "5." and "5". Both are perfectly valid runtime row labels.
config = {
    "tables": [
        {
            "factors": ["band"],
            "outputColumn": "value",
            "entries": [
                {"band": "5.", "value": 1.0},
                {"band": "5", "value": 2.0},
            ],
        }
    ]
}

# ---------------------------------------------------------------------------
# (3) Root-cause helper asymmetry on the SAME key "5.".
# ---------------------------------------------------------------------------
expected_eq("_canonical_sidecar_key('5.')  [WRITE side]", _canonical_sidecar_key("5."), "5.")
expected_eq(
    "_normalise_compact_sidecar_key('5.') [READ side]",
    _normalise_compact_sidecar_key("5."),
    "5",
)

# ---------------------------------------------------------------------------
# (1) WRITE path: compaction SUCCEEDS and yields two distinct map keys.
# ---------------------------------------------------------------------------
write_error: BaseException | None = None
compact_map: object = None
try:
    compacted = compact_rating_step_config_for_sidecar(config)
    compact_map = compacted["tables"][0]["entries"]
except BaseException as exc:  # noqa: BLE001 -- any write-side error refutes the claim
    write_error = exc

if write_error is not None:
    print(f"[MISMATCH] write path RAISED {type(write_error).__name__}: {write_error}")
    failures.append(
        f"write path raised {type(write_error).__name__}: {write_error} "
        f"(claim requires the save to SUCCEED)"
    )
else:
    print(f"[info] compact_map = {compact_map!r}")
    expected_eq(
        "compact top-level keys (two distinct, no duplicate raised)",
        sorted(compact_map.keys()) if isinstance(compact_map, dict) else compact_map,
        ["5", "5."],
    )
    expected_eq("compact_map['5.'] value", compact_map.get("5.") if isinstance(compact_map, dict) else None, 1.0)
    expected_eq("compact_map['5'] value", compact_map.get("5") if isinstance(compact_map, dict) else None, 2.0)

# ---------------------------------------------------------------------------
# (2) READ path on the SAME sidecar: expansion RAISES a collision ValueError.
#     This is the un-loadable sidecar -- save succeeded, load is impossible.
# ---------------------------------------------------------------------------
read_error: BaseException | None = None
read_result: object = None
if isinstance(compact_map, dict):
    sidecar = {
        "tables": [
            {
                "factors": ["band"],
                "outputColumn": "value",
                "entries": compact_map,
            }
        ]
    }
    try:
        read_result = expand_rating_step_config_from_sidecar(sidecar)
    except ValueError as exc:  # the predicted bug
        read_error = exc
    except BaseException as exc:  # noqa: BLE001 -- a different error is NOT the claim
        read_error = exc

    is_predicted = (
        isinstance(read_error, ValueError)
        and "collides" in str(read_error)
        and "'5'" in str(read_error)  # migrated-to key
    )
    if is_predicted:
        print(f"[ok] read path raised the predicted collision: {read_error}")
    else:
        print(
            f"[MISMATCH] read path did NOT raise the predicted collision; "
            f"error={read_error!r} result={read_result!r}"
        )
        failures.append(
            f"read path did not raise predicted collision ValueError on key '5'; "
            f"got error={read_error!r} result={read_result!r}"
        )
else:
    failures.append("compact_map was not a dict; cannot exercise the read path")

print()
if failures:
    print("REPRO RESULT: NOT fully reproduced -- discrepancies:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
else:
    print("REPRO RESULT: REPRODUCED -- compact_rating_step_config_for_sidecar writes a")
    print("sidecar with two distinct keys {'5.','5'} (save SUCCEEDS), but")
    print("expand_rating_step_config_from_sidecar then migrates both to '5' and raises a")
    print("collision ValueError. The saved sidecar is un-loadable: write/read asymmetry")
    print("confirmed (root cause: _canonical_sidecar_key('5.')='5.' vs")
    print("_normalise_compact_sidecar_key('5.')='5').")
