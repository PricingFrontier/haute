"""Adversarial repro: shred_to_buffers row inflation on a nested list inside a
scalar array, with ZERO skip accounting.

CLAIM under test
----------------
For a *scalar* child table (single ``$value`` column) whose source array
contains a nested list element, e.g.::

    {"tags": ["a", ["b", "c"], "d"]}   # 3 source elements at depth (tags,)

``shred_to_buffers`` should, per the region's conservation invariant, emit
EXACTLY ONE row per source element OR count any shape-mismatched element in
``ShredSkipStats``. The nested list ``["b", "c"]`` is not a scalar element; it
should be COUNTED as a skipped row (the way an object intruder in a scalar
array is), not flattened.

The defect: the ``_walk`` list branch recurses into each element with the SAME
path (``_walk(item, current_path)``). When the element is itself a list, the
recursion re-enters the list branch and iterates the INNER list too, emitting a
scalar row for each inner element. So 3 source elements produce 4 emitted rows
(a, b, c, d) and ``ShredSkipStats`` stays empty: the cached parquet's row count
silently exceeds the source element count, and the extra 'c' row has no
provenance.

Contrast (also asserted below): an *object* array with a scalar intruder IS
correctly counted, proving the conservation mechanism works for the case the
designers anticipated and is simply absent for the nested-list case.

Isolation
---------
Pure in-memory call into ``shred_to_buffers``. No disk I/O, no project root, no
rating/src/tests files touched. Synthetic config + records only.
"""

from __future__ import annotations

import sys

from haute._json_shred import ShredSkipStats, shred_to_buffers


def _scalar_table_config(label: str = "tags") -> dict:
    """Minimal valid v2 config: one root-emitting scalar child table at tags[*].

    A scalar child table has a single column whose path leaf is the reserved
    ``$value`` token, i.e. "the scalar element itself".
    """
    return {
        "tables": [
            {
                "path": "$[*].tags[*]",
                "label": label,
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[*].tags[*].$value",
                        "type": "str",
                        "status": "Inferred",
                        "selected": True,
                        "levels": None,
                    },
                ],
            },
        ],
    }


def _object_table_config(label: str = "drivers") -> dict:
    """Minimal valid v2 config: one root-emitting OBJECT child table at drivers[*]."""
    return {
        "tables": [
            {
                "path": "$[*].drivers[*]",
                "label": label,
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[*].drivers[*].driver_id",
                        "type": "str",
                        "status": "Inferred",
                        "selected": True,
                        "levels": None,
                    },
                ],
            },
        ],
    }


def main() -> int:
    failures: list[str] = []

    # ---- Case 1: nested list inside a SCALAR array (the alleged bug) --------
    records = [{"tags": ["a", ["b", "c"], "d"]}]
    source_element_count = 3  # 'a', ['b','c'], 'd' — three slots at depth (tags,)

    stats = ShredSkipStats()
    buffers = shred_to_buffers(records, _scalar_table_config(), stats=stats)

    emitted = buffers["tags"]
    emitted_values = [row["value"] for row in emitted]

    print(f"[case1] source array elements        = {source_element_count}")
    print(f"[case1] emitted rows                 = {len(emitted)}")
    print(f"[case1] emitted $value column        = {emitted_values}")
    print(f"[case1] skip stats total             = {stats.total}")
    print(f"[case1] skip stats as_meta           = {stats.as_meta()}")

    # The conservation invariant: emitted_rows + skipped_rows == element_count.
    conserved = len(emitted) + stats.total == source_element_count
    print(f"[case1] conservation holds?          = {conserved}")

    # Assertion A — row inflation: MORE rows than source elements.
    if len(emitted) == source_element_count:
        failures.append(
            "EXPECTED bug (row inflation) but emitted row count equalled the "
            f"source element count ({len(emitted)} == {source_element_count}); "
            "the nested list was NOT flattened. Claim would be REFUTED."
        )
    elif len(emitted) > source_element_count:
        # This is the predicted wrong behaviour — confirm the *exact* shape.
        if emitted_values != ["a", "b", "c", "d"]:
            failures.append(
                "Row inflation occurred but emitted values were "
                f"{emitted_values!r}, not the predicted ['a','b','c','d']."
            )
        else:
            print(
                "[case1] CONFIRMED: nested list ['b','c'] flattened into TWO "
                "rows -> 4 emitted from 3 source elements."
            )
    else:
        failures.append(
            f"Unexpected: fewer rows ({len(emitted)}) than source elements "
            f"({source_element_count})."
        )

    # Assertion B — zero skip accounting for the lost-provenance element.
    if stats.total != 0:
        failures.append(
            "EXPECTED zero skip accounting for the nested-list case, but "
            f"stats.total == {stats.total} (as_meta={stats.as_meta()}). If the "
            "nested list were correctly COUNTED, the claim's 'silent' framing "
            "would be weaker."
        )
    else:
        print(
            "[case1] CONFIRMED: skip stats EMPTY despite the extra 'c' row — "
            "no provenance, no accounting (silent over-emission)."
        )

    # Assertion C — conservation is actually violated.
    if conserved:
        failures.append(
            "Conservation invariant unexpectedly held — claim REFUTED."
        )
    else:
        print(
            "[case1] CONFIRMED: conservation VIOLATED "
            f"(emitted {len(emitted)} + skipped {stats.total} != "
            f"{source_element_count} source elements)."
        )

    # ---- Case 2 (contrast): scalar intruder in an OBJECT array IS counted ---
    # Proves the skip-accounting mechanism works for the anticipated mismatch,
    # so its absence in case 1 is a genuine gap, not a missing feature globally.
    obj_records = [{"drivers": [{"driver_id": "d1"}, "intruder", {"driver_id": "d2"}]}]
    obj_stats = ShredSkipStats()
    obj_buffers = shred_to_buffers(obj_records, _object_table_config(), stats=obj_stats)

    print(f"\n[case2] emitted drivers rows         = {len(obj_buffers['drivers'])}")
    print(f"[case2] skip stats total             = {obj_stats.total}")
    print(f"[case2] skip stats as_meta           = {obj_stats.as_meta()}")

    if obj_stats.total != 1:
        failures.append(
            "Contrast case broken: a scalar intruder in an object array should "
            f"be counted as exactly 1 skipped row, got {obj_stats.total}. "
            "(If this fails the contrast argument is undermined.)"
        )
    elif len(obj_buffers["drivers"]) != 2:
        failures.append(
            "Contrast case broken: object array should emit 2 rows "
            f"(d1, d2), got {len(obj_buffers['drivers'])}."
        )
    else:
        print(
            "[case2] CONFIRMED: object-array scalar intruder counted as 1 "
            "skip, 2 rows emitted — the mechanism exists and works."
        )

    # ---- Verdict -----------------------------------------------------------
    print()
    if failures:
        print("REPRO RESULT: NOT as predicted — claim NOT reproduced:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        "REPRO RESULT: BUG REPRODUCED. shred_to_buffers flattened a nested "
        "list inside a scalar array, emitting 4 rows from 3 source elements "
        "with empty ShredSkipStats (silent record inflation / lost provenance), "
        "while the analogous object-array mismatch is correctly counted."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
