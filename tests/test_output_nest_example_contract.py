"""Pinned end-to-end contract: the nested witness dataset + the two-node pipeline.

Nick validated the input/output work by hand against a small nested dataset
(``nest_example.json``) wired through a TWO-NODE pipeline — a multi-frame
``apiInput`` (the ``quotes`` node) feeding an ``OUTPUT`` (the
``Quote_Response_9`` node). This test pins that witness as a regression
contract for BOTH halves:

- INPUT (apiInput shred): the nested document shreds into four frames —
  ``quotes`` (root), ``drivers`` (``drivers[]``), ``vehicles`` (``vehicles[]``),
  and ``licenses`` (``drivers[].licenses[]``) — with W1 ancestor keys
  (``policy_id`` into every child; ``driver_id`` into ``licenses``) distributed
  at walk-time.
- OUTPUT (assembler): the ``outputMapping`` reassembles those four frames into
  one nested JSON document — ``drivers`` carries ``licenses`` (nesting via the
  shared ``driver_id``), ``vehicles`` is a sibling (no cross-multiply), and the
  shared ``policy_id`` collapses to a single root object.

Because the mapping is a faithful inverse of the shred, the pipeline is the
IDENTITY on this document, and the contract is its FIXED POINT: the fixture is
itself the assembler's canonical (pretty-printed) output, so
``assemble(shred(fixture))`` re-serialises BYTE-IDENTICALLY to the fixture file.
We compare serialised text, not parsed objects — a stronger, simpler check than
Python ``==`` (it pins key order and formatting, not just value-equivalence).
Regenerate the fixture by writing ``_canonicalize(assemble(shred(...)))`` to it.

The apiInput schema and the output mapping below are SNAPSHOTS of Nick's
witnessed configs (``rating/config/quote_input/quotes.json`` and
``rating/config/quote_response/Quote_Response_9.json``); they are pinned here so
the contract is self-contained and does not drift as the live pipeline is edited.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from haute._json_shred import shred_to_buffers
from haute._output_assembler import assemble_output_from_mapping

_FIXTURE = Path(__file__).parent / "fixtures" / "output_assembler" / "nest_example.json"


def _canonicalize(document: object) -> str:
    """The fixture's on-disk form: pretty-printed JSON + trailing newline.

    Both halves of the fixed-point contract serialise through this single
    function, so "byte-identical" is well-defined and the fixture can be
    regenerated with ``_FIXTURE.write_text(_canonicalize(document))``.
    """
    return json.dumps(document, indent=2) + "\n"


# --- Snapshot of the witnessed apiInput v2 schema (quotes node) --------------
# Four emit-true frames; child frames declare the ancestor key columns at their
# shallow JSONPath so the shred distributes them into every descendant row.
def _col(name: str, path: str, type_: str) -> dict[str, Any]:
    return {"name": name, "path": path, "type": type_, "selected": True}


_APIINPUT_V2_SCHEMA: dict[str, Any] = {
    "tables": [
        {
            "path": "$[:]",
            "label": "quotes",
            "emit": True,
            "columns": [_col("policy_id", "$[:].policy_id", "int")],
        },
        {
            "path": "$[:].drivers[:]",
            "label": "drivers",
            "emit": True,
            "columns": [
                _col("driver_id", "$[:].drivers[:].driver_id", "int"),
                _col("policy_id", "$[:].policy_id", "int"),
            ],
        },
        {
            "path": "$[:].vehicles[:]",
            "label": "vehicles",
            "emit": True,
            "columns": [
                _col("vehicle_id", "$[:].vehicles[:].vehicle_id", "int"),
                _col("policy_id", "$[:].policy_id", "int"),
            ],
        },
        {
            "path": "$[:].drivers[:].licenses[:]",
            "label": "licenses",
            "emit": True,
            "columns": [
                _col("type", "$[:].drivers[:].licenses[:].type", "str"),
                _col("policy_id", "$[:].policy_id", "int"),
                _col("driver_id", "$[:].drivers[:].driver_id", "int"),
            ],
        },
    ]
}


# --- Snapshot of the witnessed OUTPUT mapping (Quote_Response_9 node) ---------
def _entry(port: str, col: str, path: str) -> dict[str, Any]:
    return {"source_port": port, "source_column": col, "output_path": path, "enabled": True}


_OUTPUT_MAPPING: list[dict[str, Any]] = [
    _entry("quotes", "policy_id", "$[:].policy_id"),
    _entry("drivers", "driver_id", "$[:].drivers[:].driver_id"),
    _entry("drivers", "policy_id", "$[:].policy_id"),
    _entry("vehicles", "vehicle_id", "$[:].vehicles[:].vehicle_id"),
    _entry("vehicles", "policy_id", "$[:].policy_id"),
    _entry("licenses", "type", "$[:].drivers[:].licenses[:].type"),
    _entry("licenses", "policy_id", "$[:].policy_id"),
    _entry("licenses", "driver_id", "$[:].drivers[:].driver_id"),
]


def _shred_to_frames(records: list[dict[str, Any]]) -> dict[str, pl.LazyFrame]:
    """Run the v2 shred and lift each frame's row buffer to a LazyFrame."""
    buffers = shred_to_buffers(records, _APIINPUT_V2_SCHEMA)
    return {label: pl.DataFrame(rows).lazy() for label, rows in buffers.items()}


def test_nest_example_shred_frames() -> None:
    """INPUT half: the four frames + W1 ancestor-key distribution are pinned."""
    records = json.loads(_FIXTURE.read_text())
    buffers = shred_to_buffers(records, _APIINPUT_V2_SCHEMA)

    assert set(buffers) == {"quotes", "drivers", "vehicles", "licenses"}
    assert buffers["quotes"] == [{"policy_id": 1001}]
    assert buffers["drivers"] == [
        {"driver_id": 1, "policy_id": 1001},
        {"driver_id": 2, "policy_id": 1001},
    ]
    assert buffers["vehicles"] == [
        {"vehicle_id": 1, "policy_id": 1001},
        {"vehicle_id": 2, "policy_id": 1001},
        {"vehicle_id": 3, "policy_id": 1001},
    ]
    # licenses carries BOTH ancestor keys (policy_id from root, driver_id from
    # the enclosing driver) so it nests back under the right driver.
    assert buffers["licenses"] == [
        {"type": "UK", "policy_id": 1001, "driver_id": 1},
        {"type": "EU", "policy_id": 1001, "driver_id": 1},
        {"type": "EU", "policy_id": 1001, "driver_id": 2},
    ]


def test_fixture_is_canonical() -> None:
    """The fixture on disk IS the assembler's canonical pretty-print.

    Guards the fixed-point precondition: if someone hand-edits the fixture's
    formatting (re-inlining objects, dropping the trailing newline), this fails
    loudly rather than letting the round-trip assertion below mask it.
    """
    fixture_text = _FIXTURE.read_text()
    assert _canonicalize(json.loads(fixture_text)) == fixture_text


def test_nest_example_roundtrip_byte_identical() -> None:
    """OUTPUT half + the end-to-end contract: the pipeline is a FIXED POINT.

    The two-node pipeline reproduces the nested document — drivers nest their
    licenses (via the shared driver_id), vehicles stay a sibling array (no
    2x3 cross-multiply), and the shared policy_id collapses to one root object.
    Because the fixture is itself the assembler's canonical output, re-running
    ``assemble(shred(fixture))`` and re-serialising reproduces the fixture file
    BYTE-FOR-BYTE — a stronger contract than parsed-object equality.
    """
    fixture_text = _FIXTURE.read_text()
    records = json.loads(fixture_text)
    frames = _shred_to_frames(records)
    document = assemble_output_from_mapping(frames, _OUTPUT_MAPPING)
    assert _canonicalize(document) == fixture_text
