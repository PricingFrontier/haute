"""Adversarial repro for claim `resolve-leaf-list-collapse-arbitrary-pick`.

Claim: `_resolve_leaf` (src/haute/_json_shred.py:529-536) silently collapses a
mid-walk array-of-objects to element [0] when resolving a dotted column leaf,
discarding the rest with NO skip accounting; and a scalar list AT the final leaf
flows whole into the strict typed Series build (fails loud only there, not
skipped/counted).

This script proves three concrete things, asserting on exact values:

  A. `_resolve_leaf({"profile":[{"age":41},{"age":99}]}, "profile.age") == 41`
     -> the second element (99) is silently chosen-against. The value picked is
        element-0, i.e. JSON-order-dependent.

  B. A full `shred_to_buffers` over such a record, WITH a ShredSkipStats passed,
     records the column value 41 and reports `stats.total == 0` and an empty
     `skipped_rows_by_table` -> the dropped 99 has ZERO skip accounting.

  C. A scalar list AT the dotted leaf (`profile.age == [41, 99]`) is returned
     whole by `_resolve_leaf` and, when built into the typed Series, raises
     ApiInputSchemaError from `_buffer_to_frame` -> it fails loud at frame-build
     time rather than being skipped/counted at shred time.

Isolation: no real project files touched. `_resolve_leaf` / `shred_to_buffers` /
`_buffer_to_frame` are pure in-memory functions; a tmp project root is set for
safety even though no disk I/O is needed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from haute import _sandbox
from haute._json_shred import (
    ShredSkipStats,
    _buffer_to_frame,
    _resolve_leaf,
    shred_to_buffers,
)
from haute._api_input_schema import ApiInputSchemaError


def main() -> int:
    # Sandbox a throwaway project root (defensive; nothing is written here).
    tmp = Path(tempfile.mkdtemp(prefix="haute_repro_resolve_leaf_"))
    _sandbox.set_project_root(tmp)

    failures: list[str] = []

    # ---- A. Standalone element-0 collapse, no accounting available ---------
    record_a = {"profile": [{"age": 41}, {"age": 99}]}
    got_a = _resolve_leaf(record_a, "profile.age")
    print(f"[A] _resolve_leaf(profile=[{{age:41}},{{age:99}}], 'profile.age') = {got_a!r}")
    if got_a != 41:
        failures.append(f"[A] expected silent element-0 pick (41), got {got_a!r}")
    # Prove it is order-dependent: reversing the array flips the chosen value.
    got_a_rev = _resolve_leaf({"profile": [{"age": 99}, {"age": 41}]}, "profile.age")
    print(f"[A] reversed array -> {got_a_rev!r} (order-dependent arbitrary pick)")
    if got_a_rev != 99:
        failures.append(f"[A] expected 99 after reversal, got {got_a_rev!r}")

    # ---- B. Full shred drops the rest with ZERO skip accounting ------------
    # Root table at $[*]; one dotted-leaf column profile.age (declared int).
    # The record's `profile` is an array of two objects -> 41 kept, 99 dropped.
    v2_config = {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[*].profile.age",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    stats = ShredSkipStats()
    buffers = shred_to_buffers([record_a], v2_config, stats=stats)
    root_rows = buffers["root"]
    print(f"[B] shred buffers['root'] = {root_rows!r}")
    print(f"[B] stats.total = {stats.total}  skipped_rows_by_table = {stats.skipped_rows_by_table!r}")
    if root_rows != [{"age": 41}]:
        failures.append(f"[B] expected exactly one row {{'age':41}}, got {root_rows!r}")
    if stats.total != 0 or stats.skipped_rows_by_table:
        failures.append(
            f"[B] expected ZERO skip accounting for the dropped element, "
            f"got total={stats.total} by_table={stats.skipped_rows_by_table!r}"
        )
    else:
        print("[B] CONFIRMED: 99 silently lost, no skip counted (silent data loss).")

    # ---- C. Scalar list AT the leaf flows whole -> fails loud at frame build
    record_c = {"profile": {"age": [41, 99]}}  # age itself is a scalar array
    got_c = _resolve_leaf(record_c, "profile.age")
    print(f"[C] _resolve_leaf(profile={{age:[41,99]}}, 'profile.age') = {got_c!r}")
    if got_c != [41, 99]:
        failures.append(f"[C] expected the whole list [41, 99] returned, got {got_c!r}")

    col_specs = [("age", "profile.age", "int")]
    raised_at_frame = False
    try:
        _buffer_to_frame([{"age": got_c}], col_specs)
    except ApiInputSchemaError as exc:
        raised_at_frame = True
        print(f"[C] _buffer_to_frame raised ApiInputSchemaError: {exc}")
    except Exception as exc:  # noqa: BLE001 - any other raise still proves 'fails loud here'
        raised_at_frame = True
        print(f"[C] _buffer_to_frame raised {type(exc).__name__}: {exc}")
    if not raised_at_frame:
        failures.append("[C] expected a list-at-leaf to fail the strict Series build, but it did not")
    else:
        print("[C] CONFIRMED: list at leaf reaches typed-frame build and fails loud THERE (not skipped/counted at shred).")

    print()
    if failures:
        print("RESULT: NOT-REPRODUCED / claim mispredicts behaviour:")
        for f in failures:
            print("  - " + f)
        return 1
    print("RESULT: REPRODUCED — all three predicted behaviours hold exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
