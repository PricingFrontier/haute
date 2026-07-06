"""V015 repro: a literal JSON key named "$value" collides with the reserved
scalar-array sentinel ``_SCALAR_VALUE_LEAF``, causing shred_to_buffers to
silently drop EVERY record of an OBJECT table.

ISOLATION: all disk I/O is via tempfile; no project root, no rating/, src/,
tests/, or real project files are read or written. We build a tiny synthetic
JSONL in a temp dir and drive the public shred/build/load API.

The assertions pin the *wrong value*: a 2-record source yields a 0-row root
frame (and 2 silently-skipped rows), not the 2 rows that obviously should
appear.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import orjson

from haute._api_input_schema import parse_column_path
from haute._json_shred import (
    _SCALAR_VALUE_LEAF,
    ShredSkipStats,
    build_per_port_cache,
    infer_v2_schema_from_data,
    load_per_port_cache,
    shred_to_buffers,
)

# Two perfectly ordinary records whose key happens to be literally "$value".
# Nothing in the JSON spec forbids a key named "$value".
RECORDS = [
    {"$value": 10, "other": 1},
    {"$value": 20, "other": 2},
]


def _write_jsonl(dir_path: Path) -> Path:
    data_path = dir_path / "data.jsonl"
    with data_path.open("wb") as fh:
        for rec in RECORDS:
            fh.write(orjson.dumps(rec))
            fh.write(b"\n")
    return data_path


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        data_path = _write_jsonl(tmp)

        # 1) Inference emits an OBJECT-table column whose path collides with the
        #    scalar-array sentinel.
        inferred = infer_v2_schema_from_data(data_path)
        root = next(t for t in inferred["tables"] if t["path"] == "$[*]")
        col_paths = {c["name"]: c["path"] for c in root["columns"]}
        print("inferred root column paths:", col_paths)

        value_path = col_paths.get("$value")
        assert value_path == "$[*].$value", (
            f"expected the literal '$value' key to infer path '$[*].$value', "
            f"got {value_path!r}"
        )

        # The leaf of that OBJECT column equals the reserved scalar sentinel.
        leaf = parse_column_path(value_path, "$[*]")
        print("parsed leaf:", repr(leaf), "| sentinel:", repr(_SCALAR_VALUE_LEAF))
        assert leaf == _SCALAR_VALUE_LEAF, (
            "precondition: the parsed leaf must equal the sentinel for the bug "
            f"to bite; got leaf={leaf!r}"
        )

        # 2) shred_to_buffers drops EVERY dict record of this object table.
        stats = ShredSkipStats()
        buffers = shred_to_buffers(RECORDS, inferred, stats=stats)
        root_rows = buffers[root["label"]]
        print("root buffer row count:", len(root_rows))
        print("skip stats:", stats.as_meta())

        assert len(root_rows) == 0, (
            "BUG NOT REPRODUCED: expected 0 rows emitted (every record silently "
            f"dropped), got {len(root_rows)}"
        )
        # The 2 lost rows are counted as skipped for this table — confirming they
        # were dropped by the shape guard, not merely never seen.
        assert stats.skipped_rows_by_table.get(root["label"]) == 2, (
            f"expected 2 skipped rows for the root table, got "
            f"{stats.skipped_rows_by_table!r}"
        )

        # 3) End-to-end build + load: the cache is judged valid/fresh, yet the
        #    materialised root frame has height 0 from 2 source records.
        cache_dir = tmp / "cache"
        summary = build_per_port_cache(data_path, inferred, cache_dir)
        print("build skipped summary:", summary["skipped"])
        root_summary = next(t for t in summary["tables"] if t["label"] == root["label"])
        print("built root row_count:", root_summary["row_count"])
        assert root_summary["row_count"] == 0, (
            f"expected built root parquet to have 0 rows, got "
            f"{root_summary['row_count']}"
        )

        frames = load_per_port_cache(cache_dir, inferred)
        root_frame = frames[root["label"]].collect()
        print(
            "loaded root frame height:",
            root_frame.height,
            "columns:",
            root_frame.columns,
        )
        assert root_frame.height == 0, (
            f"expected loaded root frame height 0 (all {len(RECORDS)} records "
            f"silently lost), got {root_frame.height}"
        )

    print(
        "\nV015 REPRODUCED: 2 source records -> 0 emitted rows on the OBJECT "
        "root table; a literal '$value' key collides with the scalar-array "
        "sentinel and the shape guard drops every record."
    )


if __name__ == "__main__":
    main()
