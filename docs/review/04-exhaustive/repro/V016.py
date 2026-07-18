"""Isolated reproduction for V016.

Claim: ``infer_v2_schema_from_data`` builds an object column path as
``f"{table_path}.{col_name}"`` with no escaping of ``col_name``. For a
top-level JSON key that itself contains a dot (e.g. ``"a.b": 42``), the
inferred column path becomes ``"$[*].a.b"``. ``parse_column_path`` then
returns the leaf ``"a.b"``, which ``_resolve_leaf`` interprets as a NESTED
dotted path (``value.get("a").get("b")``) rather than the single flat key
``"a.b"``. The real value is silently dropped to ``None`` and the row is
still emitted (no skip is counted), so the loss is invisible.

This exercises the ordinary "Infer Tables -> shred" flow end to end:
  1. write synthetic records to a tempfile JSONL,
  2. infer the v2 schema from that file,
  3. shred the SAME records through the inferred schema,
  4. assert the dotted-key value is lost while a sibling control survives.

ISOLATION: all disk I/O is via tempfile; no rating/, src/, tests/, or real
project files are read or written.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from haute._json_shred import (
    ShredSkipStats,
    infer_v2_schema_from_data,
    shred_to_buffers,
)


def main() -> None:
    records = [{"a.b": 42, "ok": 1}]

    with tempfile.TemporaryDirectory() as tmp:
        data_path = Path(tmp) / "records.jsonl"
        with data_path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        schema = infer_v2_schema_from_data(data_path)

    # --- Inspect the inferred root table / column path. -------------------
    assert schema["tables"], "expected at least the root table to be inferred"
    root = schema["tables"][0]
    cols = {c["name"]: c["path"] for c in root["columns"]}
    print(f"inferred root path        : {root['path']!r}")
    print(f"inferred column paths     : {cols}")

    # The dotted key produced an ambiguous flat-key-as-dotted-path column.
    assert cols.get("a.b") == "$[*].a.b", (
        f"expected unescaped dotted column path '$[*].a.b', got {cols.get('a.b')!r}"
    )

    # --- Shred the same records through the inferred schema. --------------
    stats = ShredSkipStats()
    buffers = shred_to_buffers(records, schema, stats=stats)

    # The root table's label is its path here (label defaults to path in infer).
    root_label = root["label"]
    rows = buffers[root_label]
    print(f"shredded rows             : {rows}")
    print(f"skip total                : {stats.total}  ({stats.as_meta()})")

    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    row = rows[0]

    # --- The control sibling survives. -----------------------------------
    assert row.get("ok") == 1, (
        f"control key 'ok' should round-trip to 1, got {row.get('ok')!r}"
    )

    # --- The BUG: the dotted-key value is silently lost. -----------------
    expected_value = 42
    actual_value = row.get("a.b")
    print(f"a.b  source value         : {expected_value}")
    print(f"a.b  shredded value       : {actual_value}")

    assert actual_value is None, (
        "EXPECTED-BUG NOT OBSERVED: the dotted key resolved to "
        f"{actual_value!r}; the value was NOT lost (bug may be fixed)."
    )
    assert actual_value != expected_value, (
        "value was preserved — no data loss; claim refuted"
    )

    # --- And the loss is silent: no skip of any kind is recorded. --------
    assert stats.total == 0, (
        f"expected the loss to be SILENT (0 skips), got {stats.total} "
        f"skips ({stats.as_meta()}) — loss would be surfaced after all"
    )

    print()
    print(
        "REPRODUCED: source value 42 under flat key 'a.b' was silently dropped "
        "to None (sibling 'ok'=1 survived; row-skip count stayed 0)."
    )


if __name__ == "__main__":
    main()
