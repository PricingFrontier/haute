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

The fixture snapshots TWO policy objects (``policy_id`` 1001 and 1002) whose
child rows DELIBERATELY REUSE ids: ``driver_id=1`` and ``vehicle_id=1`` both
recur under each policy. That reuse is the contract's teeth — it is what makes
the fixed point PROVE row distinction. The shred carries ``policy_id`` into every
child row, so a correct assembler keeps each child attached to its OWN parent via
the carried ``policy_id``; a ``policy_id``-blind / cross-multiplying assembler
would visibly merge the colliding ids (driver 1 would gather ``[UK, EU, US]``,
vehicle 1 would attach to both policies). With a single policy every child shares
one ``policy_id`` value, so an assembler that ignored ``policy_id`` entirely would
still reproduce the document — the second policy is what removes that loophole.
The two roots also differ in a non-key value (``premium`` 500 vs 750), ruling out
accidental same-shape masking and exercising a second ``quotes``-frame column.

Because the mapping is a faithful inverse of the shred, the pipeline is the
IDENTITY on this document, and the contract is its FIXED POINT: the fixture is
itself the assembler's canonical (pretty-printed) output, so
``assemble(shred(fixture))`` re-serialises BYTE-IDENTICALLY to the fixture file.
We compare serialised text, not parsed objects — a stronger, simpler check than
Python ``==`` (it pins key order and formatting, not just value-equivalence).
Any wrong-parent attachment changes the assembled bytes, so the byte-identical
assertion fails; ``test_nest_example_rows_attach_to_own_parent`` then makes that
failure self-describing. Regenerate the fixture by writing
``_canonicalize(assemble(shred(...)))`` to it.

The apiInput schema and the output mapping below are SNAPSHOTS of Nick's
witnessed configs (``rating/config/quote_input/quotes.json`` and
``rating/config/quote_response/Quote_Response_9.json``); they are pinned here so
the contract is self-contained and does not drift as the live pipeline is edited.
(The ``premium`` column is part of this self-contained snapshot; the live
``quotes.json`` need not carry it for this contract to hold.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from haute._json_shred import shred_to_buffers
from haute._output_assembler import assemble_output_from_mapping
from haute.parser import parse_pipeline_file

_FIXTURE = Path(__file__).parent / "fixtures" / "output_assembler" / "nest_example.json"
_REFERENCE_PIPELINE = Path(__file__).parent.parent / "rating" / "main.py"


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
            "columns": [
                _col("policy_id", "$[:].policy_id", "int"),
                _col("premium", "$[:].premium", "int"),
            ],
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
    _entry("quotes", "premium", "$[:].premium"),
    _entry("drivers", "driver_id", "$[:].drivers[:].driver_id"),
    _entry("drivers", "policy_id", "$[:].policy_id"),
    _entry("vehicles", "vehicle_id", "$[:].vehicles[:].vehicle_id"),
    _entry("vehicles", "policy_id", "$[:].policy_id"),
    _entry("licenses", "type", "$[:].drivers[:].licenses[:].type"),
    _entry("licenses", "policy_id", "$[:].policy_id"),
    _entry("licenses", "driver_id", "$[:].drivers[:].driver_id"),
]


def test_checked_in_reference_pipeline_parses() -> None:
    """The repository's configured default must remain a parseable Haute graph."""
    graph = parse_pipeline_file(_REFERENCE_PIPELINE)
    assert [node.id for node in graph.nodes] == ["quotes", "Quote_Response_9"]
    assert len(graph.edges) == 4
    assert {edge.sourceHandle for edge in graph.edges} == {
        "quotes",
        "drivers",
        "vehicles",
        "licenses",
    }


def _shred_to_frames(records: list[dict[str, Any]]) -> dict[str, pl.LazyFrame]:
    """Run the v2 shred and lift each frame's row buffer to a LazyFrame."""
    buffers = shred_to_buffers(records, _APIINPUT_V2_SCHEMA)
    return {label: pl.DataFrame(rows).lazy() for label, rows in buffers.items()}


def test_nest_example_shred_frames() -> None:
    """INPUT half: the four frames + W1 ancestor-key distribution are pinned."""
    records = json.loads(_FIXTURE.read_text())
    buffers = shred_to_buffers(records, _APIINPUT_V2_SCHEMA)

    assert set(buffers) == {"quotes", "drivers", "vehicles", "licenses"}
    assert buffers["quotes"] == [
        {"policy_id": 1001, "premium": 500},
        {"policy_id": 1002, "premium": 750},
    ]
    # driver_id=1 recurs under BOTH policies; only the carried policy_id keeps
    # the two driver-1 rows distinct in the flat buffer.
    assert buffers["drivers"] == [
        {"driver_id": 1, "policy_id": 1001},
        {"driver_id": 2, "policy_id": 1001},
        {"driver_id": 1, "policy_id": 1002},
    ]
    # vehicle_id=1 recurs too — distinguished only by policy_id (no cross-multiply:
    # 3 vehicles under 1001 + 1 under 1002 == 4, a sum not a product).
    assert buffers["vehicles"] == [
        {"vehicle_id": 1, "policy_id": 1001},
        {"vehicle_id": 2, "policy_id": 1001},
        {"vehicle_id": 3, "policy_id": 1001},
        {"vehicle_id": 1, "policy_id": 1002},
    ]
    # licenses carries BOTH ancestor keys (policy_id from root, driver_id from
    # the enclosing driver) so it nests back under the right driver. The two
    # driver-1 rows (policy 1001 → [UK, EU], policy 1002 → [US]) share driver_id
    # but differ in policy_id — the (policy_id, driver_id) pair is the identity.
    assert buffers["licenses"] == [
        {"type": "UK", "policy_id": 1001, "driver_id": 1},
        {"type": "EU", "policy_id": 1001, "driver_id": 1},
        {"type": "EU", "policy_id": 1001, "driver_id": 2},
        {"type": "US", "policy_id": 1002, "driver_id": 1},
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
    cross-multiply), and each policy_id collapses to its own root object.
    Because the fixture is itself the assembler's canonical output, re-running
    ``assemble(shred(fixture))`` and re-serialising reproduces the fixture file
    BYTE-FOR-BYTE — a stronger contract than parsed-object equality.

    This is the load-bearing distinction check. The fixture's two policies reuse
    child ids (driver_id=1, vehicle_id=1 under BOTH policies), so any wrong-parent
    attachment — a policy_id-blind matcher merging driver 1's licenses into
    [UK, EU, US], or a cross-multiply attaching vehicle 1 to both policies —
    serialises to DIFFERENT bytes and fails here. No new assertion mechanism is
    needed; the reused-id witness gives the existing byte-identity its teeth.
    """
    fixture_text = _FIXTURE.read_text()
    records = json.loads(fixture_text)
    frames = _shred_to_frames(records)
    document = assemble_output_from_mapping(frames, _OUTPUT_MAPPING)
    assert _canonicalize(document) == fixture_text


def test_nest_example_rows_attach_to_own_parent() -> None:
    """Self-describing distinction check on the PARSED assembled document.

    Where the byte-identity test fails with "bytes differ", this names the
    defect. The fixture reuses child ids across both policies (driver_id=1 and
    vehicle_id=1 each appear under policy 1001 AND 1002), so a correct assembler
    must partition children by the carried policy_id; a policy_id-blind or
    cross-multiplying assembler visibly merges them.
    """
    records = json.loads(_FIXTURE.read_text())
    frames = _shred_to_frames(records)
    document = assemble_output_from_mapping(frames, _OUTPUT_MAPPING)

    # (a) two root policies, each keeping its own premium (no leakage of 750
    #     onto 1001 or vice versa).
    assert len(document) == 2
    by_policy = {root["policy_id"]: root for root in document}
    assert set(by_policy) == {1001, 1002}
    assert by_policy[1001]["premium"] == 500
    assert by_policy[1002]["premium"] == 750

    # (b) the colliding driver_id=1 is partitioned by policy_id, NOT merged:
    #     policy 1001 driver 1 → [UK, EU]; policy 1002 driver 1 → [US].
    def _driver(root: dict[str, Any], driver_id: int) -> dict[str, Any]:
        (driver,) = [d for d in root["drivers"] if d["driver_id"] == driver_id]
        return driver

    def _license_types(driver: dict[str, Any]) -> list[str]:
        return [lic["type"] for lic in driver["licenses"]]

    assert _license_types(_driver(by_policy[1001], 1)) == ["UK", "EU"]
    assert _license_types(_driver(by_policy[1002], 1)) == ["US"]
    # the single-policy driver is untouched.
    assert _license_types(_driver(by_policy[1001], 2)) == ["EU"]

    # (c) vehicle_id=1 did NOT attach to both policies and there is no
    #     cross-multiply: policy 1001 keeps [1, 2, 3], policy 1002 keeps [1].
    assert [v["vehicle_id"] for v in by_policy[1001]["vehicles"]] == [1, 2, 3]
    assert by_policy[1002]["vehicles"] == [{"vehicle_id": 1}]

    # (d) counts are SUMS, not products — guards against cross-join blow-up.
    assert sum(len(root["drivers"]) for root in document) == 3
    assert sum(len(root["vehicles"]) for root in document) == 4
