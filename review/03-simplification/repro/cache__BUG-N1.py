"""Isolated reproduction for BUG-N1.

Claim: infer_v2_schema_from_data over-widens a pure-int scalar-array column to
'str' when an EARLIER record had the same key as an empty array [].

Mechanism (src/haute/_json_shred.py:1160-1181):
  - record {"tags": []}      -> empty-array branch: scalar_tables.setdefault(('tags',),'str')
                               sets ('tags',) -> 'str'.
  - record {"tags": [1,2,3]} -> scalar branch: elem_type='int', then
    scalar_tables[('tags',)] = _widen_type('str','int') == 'str'  (int+str disagree).

Expected (correct) behaviour: every NON-empty element is an int, so the inferred
scalar child 'value' column should be typed 'int'. The empty [] carries no type
information and must not pollute the inferred element type.

NOTE ON THE CLAIM'S OVER-REACH: BUG-N1 asserts the fault is "consistently wrong
whenever any empty array co-occurs", incl. when the empty array appears AFTER a
populated one. That is FALSE. The empty branch uses setdefault(), not assignment,
so when a populated scalar array is seen FIRST (('tags',) already == 'int'), the
later empty [] is a no-op and the type correctly stays 'int'. The bug is therefore
ORDER-DEPENDENT: it only manifests when the first observation of the key is [].
Case B below proves the post-populated empty does NOT corrupt the type.

This repro uses only a tempfile (JSONL) + the real public inference function.
It touches nothing under rating/ src/ tests/ or real project files.

Run:  uv run python review/03-simplification/repro/cache__BUG-N1.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from haute._json_shred import infer_v2_schema_from_data


def _value_col_type(schema: dict, table_path: str) -> str:
    """Return the type token of the scalar 'value' column for *table_path*."""
    for table in schema["tables"]:
        if table["path"] == table_path:
            for col in table["columns"]:
                if col["name"] == "value":
                    return col["type"]
            raise AssertionError(f"no 'value' column in table {table_path!r}: {table['columns']}")
    raise AssertionError(f"no table {table_path!r} in inferred schema; tables={[t['path'] for t in schema['tables']]}")


def _infer_from_records(records: list[dict]) -> dict:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "data.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        return infer_v2_schema_from_data(p)


def main() -> int:
    tags_table = "$[*].tags[*]"

    # --- Case A: empty array FIRST, then a populated pure-int array. ----------
    schema_a = _infer_from_records([{"tags": []}, {"tags": [1, 2, 3]}])
    type_a = _value_col_type(schema_a, tags_table)
    print(f"[case A] []  then [1,2,3]      -> value column type = {type_a!r}  (correct = 'int')")

    # --- Case B: populated FIRST, then empty. Disproves the claim's "consistently
    #     wrong ... if the empty array appears AFTER a populated one" assertion:
    #     setdefault() makes the trailing [] a no-op, so this stays 'int'. --------
    schema_b = _infer_from_records([{"tags": [1, 2, 3]}, {"tags": []}])
    type_b = _value_col_type(schema_b, tags_table)
    print(f"[case B] [1,2,3] then []       -> value column type = {type_b!r}  (correct = 'int')")

    # --- Control: NO empty array present -> must infer 'int' (sanity). --------
    schema_c = _infer_from_records([{"tags": [1, 2, 3]}, {"tags": [4, 5]}])
    type_c = _value_col_type(schema_c, tags_table)
    print(f"[control] [1,2,3] then [4,5]   -> value column type = {type_c!r}  (correct = 'int')")

    print()

    # Prove the control is correct (no empty array => correct 'int' inference).
    assert type_c == "int", (
        f"CONTROL FAILED (setup error, not the bug): pure-int arrays without any "
        f"empty [] inferred {type_c!r}, expected 'int'."
    )
    print("control OK: pure-int scalar arrays infer 'int' when no empty [] is present.")

    # The REAL bug (Case A): empty [] seen FIRST poisons the type to 'str'.
    # The ONLY difference vs the control is a leading empty [] at the same key.
    assert type_a == "str", (
        f"Case A did not reproduce: expected over-widened 'str', got {type_a!r}. "
        f"Bug may be fixed."
    )

    # The claim's over-reach (Case B) is DISPROVEN: a trailing empty [] does NOT
    # corrupt an already-populated 'int' type, because the empty branch uses
    # setdefault() rather than assignment.
    assert type_b == "int", (
        f"Case B unexpectedly corrupted: claim said this should be 'str', but the "
        f"correct/actual value is 'int'. Got {type_b!r}."
    )

    print()
    print("BUG REPRODUCED (order-dependent): when an empty [] is the FIRST observation of a")
    print("key, a later pure-int scalar array is typed 'str'. The built parquet 'value' column")
    print("would be String, so numeric rating/banding on '1','2','3' (strings) is silently wrong.")
    print("CLAIM CORRECTION: it is NOT 'consistently wrong whenever any empty array co-occurs' —")
    print("a populated-then-empty ordering (Case B) correctly yields 'int' (setdefault no-op).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
