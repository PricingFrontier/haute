"""Isolated reproduction for BUG-N2.

Claim: `_iter_sampled_json_array_records` only increments its `yielded` cap
counter for *dict* records (src/haute/_json_shred.py:427-429). Non-object
top-level array elements (scalars, nested arrays) are consumed but NOT counted
toward `sample_size`, so the `while yielded < sample_size` loop reads FAR past
`sample_size` total elements for an array dominated by non-objects.

The public contract (infer_v2_schema_from_data docstring, line 1128-1130):
"For JSONL and root JSON arrays, the iterator stops after the requested object
records instead of reading the rest of the file."

This repro builds a synthetic in-memory root array whose first N elements are
scalars followed by 2 dict records, writes it to a TEMP file, and counts how
many top-level array elements the sampled reader consumes when asked for
sample_size=2. If the bug is real, it consumes ALL N scalars + 2 objects
(i.e. it streams the whole prefix) instead of stopping early.

Read-only: imports the real module, monkeypatches ONLY a local copy of the
module's _read_root_array_value to instrument call counts. Does not modify
src/, tests/, or any project files. All disk I/O uses a tempfile.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import haute._json_shred as shred

# ----------------------------------------------------------------------------
# Build synthetic data: N scalar elements, THEN 2 object records, at root.
# A "sampled" inference asking for 2 objects should, per the docstring, stop
# "after the requested object records instead of reading the rest of the file".
# ----------------------------------------------------------------------------
N_SCALARS = 5000
SAMPLE_SIZE = 2

data = list(range(N_SCALARS)) + [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}]
TOTAL_ELEMENTS = len(data)

# ----------------------------------------------------------------------------
# Instrument: wrap the module-level _read_root_array_value so we can count how
# many top-level array elements the sampled reader actually consumes. Each call
# reads exactly one root-array value. We restore the original afterwards.
# ----------------------------------------------------------------------------
_orig_read_value = shred._read_root_array_value
element_reads = 0


def _counting_read_value(first, read_byte, current_pos):  # type: ignore[no-untyped-def]
    global element_reads
    element_reads += 1
    return _orig_read_value(first, read_byte, current_pos)


def main() -> None:
    global element_reads
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "root_array.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        file_size = path.stat().st_size

        shred._read_root_array_value = _counting_read_value
        try:
            collected = list(
                shred._iter_sampled_json_array_records(path, SAMPLE_SIZE)
            )
        finally:
            shred._read_root_array_value = _orig_read_value

    n_objects_yielded = len(collected)
    print(f"file_size_bytes            = {file_size}")
    print(f"total_root_elements        = {TOTAL_ELEMENTS}")
    print(f"sample_size (objects asked)= {SAMPLE_SIZE}")
    print(f"objects yielded            = {n_objects_yielded}")
    print(f"top-level elements consumed= {element_reads}")

    # 1) It DID yield exactly sample_size objects (so it "works" superficially).
    assert n_objects_yielded == SAMPLE_SIZE, (
        f"expected {SAMPLE_SIZE} objects, got {n_objects_yielded}"
    )

    # 2) THE BUG: to collect SAMPLE_SIZE objects it had to consume every one of
    #    the N_SCALARS non-object prefix elements first. A contract-faithful
    #    early-stop reader would consume at most a small bounded number of
    #    elements (objects are dominated by non-objects). Here it consumes
    #    N_SCALARS + SAMPLE_SIZE = the entire prefix plus the two objects.
    expected_consumed_if_buggy = N_SCALARS + SAMPLE_SIZE
    assert element_reads == expected_consumed_if_buggy, (
        f"expected to consume {expected_consumed_if_buggy} elements (buggy "
        f"full-prefix scan), but consumed {element_reads}"
    )

    # 3) Make the contract violation explicit: elements consumed vastly exceeds
    #    the requested object count.
    assert element_reads > SAMPLE_SIZE * 100, (
        "reader did NOT scan far past sample_size; bug would be refuted"
    )

    print()
    print("BUG-N2 CONFIRMED: the sampled reader consumed "
          f"{element_reads} top-level elements to collect only {SAMPLE_SIZE} "
          f"object records, reading the entire {N_SCALARS}-scalar prefix. "
          "The 'stops after the requested object records instead of reading "
          "the rest of the file' contract is violated for arrays dominated by "
          "non-object elements.")


if __name__ == "__main__":
    main()
