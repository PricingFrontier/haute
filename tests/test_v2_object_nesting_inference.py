"""Object-nesting transparency in v2 apiInput ingestion (2026-06-17 ruling).

Relational depth is the array (``[:]``) nesting depth ONLY. Nesting inside 1-1
objects is relationally transparent: addressing within different objects does
not change the relational structure, so ``$[:].a.b.c`` and ``$[:].p.q`` are
siblings (same table). Only an array of objects descends a level. The grammar,
shred, and inference must all agree on this; ``[:]`` is the ONLY accepted array
selector (one canonical form — a legacy ``[*]`` is rejected, not normalised).
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute._api_input_schema import (
    array_depth,
    make_table_path,
    parse_column_path,
    parse_column_path_full,
    parse_table_path,
)
from haute._json_shred import infer_v2_schema_from_data, shred_to_buffers
from haute._output_assembler import assemble_output_from_mapping
from haute.errors import HauteError

# ---------------------------------------------------------------------------
# Grammar — object hops vs array hops
# ---------------------------------------------------------------------------


class TestTablePathGrammar:
    def test_root_is_empty(self) -> None:
        assert parse_table_path("$[:]") == ()
        assert parse_table_path("$") == ()

    def test_array_hop(self) -> None:
        assert parse_table_path("$[:].drivers[:]") == (("drivers", True),)

    def test_array_inside_object(self) -> None:
        # proposer is a 1-1 object that locates the claims array; depth is 1.
        segs = parse_table_path("$[:].proposer.claims[:]")
        assert segs == (("proposer", False), ("claims", True))
        assert array_depth(segs) == 1

    def test_object_terminal_table_path_rejected(self) -> None:
        # A bare object key is NOT a table — its leaves are columns of root.
        with pytest.raises(HauteError):
            parse_table_path("$[:].quote_metadata")

    def test_star_selector_rejected(self) -> None:
        # No legacy alias: [*] is not a valid array selector — one canonical form.
        with pytest.raises(HauteError):
            parse_table_path("$[*].drivers[*]")
        with pytest.raises(HauteError):
            parse_table_path("$[:].drivers[*]")

    def test_make_table_path_roundtrip_and_emits_colon(self) -> None:
        segs = (("proposer", False), ("claims", True))
        assert make_table_path(segs) == "$[:].proposer.claims[:]"
        assert make_table_path(()) == "$[:]"
        assert parse_table_path(make_table_path(segs)) == segs


class TestColumnPathGrammar:
    def test_root_scalar(self) -> None:
        assert parse_column_path_full("$[:].quote_id") == ((), "quote_id")

    def test_object_nested_scalar_is_root_level_leaf(self) -> None:
        # The whole point: an object-nested scalar lives at the ROOT table,
        # with the object hops folded into the dotted leaf.
        locating, leaf = parse_column_path_full("$[:].quote_metadata.quote_id")
        assert locating == ()
        assert leaf == "quote_metadata.quote_id"

    def test_deep_object_nesting_still_root(self) -> None:
        locating, leaf = parse_column_path_full("$[:].a.b.c.d.e.f.g")
        assert locating == ()
        assert leaf == "a.b.c.d.e.f.g"

    def test_array_then_object_leaf(self) -> None:
        locating, leaf = parse_column_path_full("$[:].proposer.claims[:].amount")
        assert locating == (("proposer", False), ("claims", True))
        assert leaf == "amount"

    def test_membership_normal_column(self) -> None:
        leaf = parse_column_path("$[:].proposer.claims[:].amount", "$[:].proposer.claims[:]")
        assert leaf == "amount"

    def test_membership_ancestor_column(self) -> None:
        # An ancestor (W1) column sourced at root is valid for a deeper table.
        assert parse_column_path("$[:].quote_id", "$[:].proposer.claims[:]") == "quote_id"

    def test_membership_object_chain_mismatch_rejected(self) -> None:
        # Same array key but a different object chain must not match.
        with pytest.raises(HauteError):
            parse_column_path("$[:].vehicle.claims[:].amount", "$[:].proposer.claims[:]")


# ---------------------------------------------------------------------------
# Grammar unification (PATH_GRAMMAR.md §6) — INPUT now routes through the
# shared lynchpin `haute._jsonpath`, adopting the unified ACCEPTANCE grammar.
# Four intended behaviour changes vs the old string-split INPUT parser:
#   1. identifier bracket-names accepted (`['k']` / `["k"]` → bare `.k`);
#   2. object-key charset tightened to identifiers (non-identifier dot keys
#      now rejected — previously any non-`[`/`]` key slipped through);
#   3. `.:` and incidental whitespace explicitly rejected;
#   4. `[*]`/index/range/filter/descendant/non-array-wildcard still rejected
#      (now via the shared core, not INPUT's own ad-hoc string checks).
# The reserved `$value` scalar-array leaf — deliberately not an identifier —
# is still accepted (it is INPUT-only and never reaches the OUTPUT mode).
# ---------------------------------------------------------------------------


class TestGrammarUnificationAccepts:
    """NEW accepts: identifier bracket-names normalise to the bare name,
    matching OUTPUT (kills the historical input/output bracket asymmetry)."""

    def test_bracket_name_single_quote_table(self) -> None:
        assert parse_table_path("$[:]['drivers'][:]") == (("drivers", True),)

    def test_bracket_name_double_quote_table(self) -> None:
        assert parse_table_path('$[:]["drivers"][:]') == (("drivers", True),)

    def test_bracket_name_equivalent_to_dotted(self) -> None:
        assert parse_table_path("$[:]['drivers'][:]") == parse_table_path("$[:].drivers[:]")

    def test_bracket_name_in_column_path(self) -> None:
        locating, leaf = parse_column_path_full("$[:]['drivers'][:]['driver_id']")
        assert locating == (("drivers", True),)
        assert leaf == "driver_id"

    def test_bracket_name_membership_against_dotted_table(self) -> None:
        # A bracket-spelled column under a dot-spelled table still matches —
        # both normalise to the same segments.
        assert parse_column_path("$[:]['drivers'][:]['id']", "$[:].drivers[:]") == "id"

    def test_reserved_value_leaf_still_accepted(self) -> None:
        # `$value` is the scalar-array element-itself sentinel: not an
        # identifier, but INPUT-only and still parses as a trailing leaf.
        locating, leaf = parse_column_path_full("$[:].coverages[:].$value")
        assert locating == (("coverages", True),)
        assert leaf == "$value"


class TestGrammarUnificationRejects:
    """NEW rejects (charset tightening + explicit `.:`/whitespace)."""

    def test_digit_leading_dot_key_rejected(self) -> None:
        with pytest.raises(HauteError):
            parse_column_path_full("$[:].2024")

    def test_hyphen_dot_key_rejected(self) -> None:
        with pytest.raises(HauteError):
            parse_column_path_full("$[:].a-b")

    def test_dot_colon_rejected(self) -> None:
        # `.:` reads as an object key named `:`; previously slipped through as
        # a literal key, now rejected outright (PATH_GRAMMAR §3).
        with pytest.raises(HauteError):
            parse_column_path_full("$[:].:")

    def test_leading_whitespace_in_key_rejected(self) -> None:
        with pytest.raises(HauteError):
            parse_column_path_full("$[:]. drivers")

    def test_trailing_whitespace_before_dot_rejected(self) -> None:
        with pytest.raises(HauteError):
            parse_column_path_full("$[:].drivers[:] .x")

    def test_object_outer_root_rejected(self) -> None:
        # `$.key` treats the outer structure as an object (a different
        # transport, §5) — out of domain for array-outer INPUT.
        with pytest.raises(HauteError):
            parse_column_path_full("$.key")

    def test_index_selector_rejected(self) -> None:
        with pytest.raises(HauteError):
            parse_column_path_full("$[:].drivers[0].x")

    def test_descendant_selector_rejected(self) -> None:
        with pytest.raises(HauteError):
            parse_column_path_full("$..drivers")


# ---------------------------------------------------------------------------
# Inference — object nesting produces no spurious tables
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "data.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records))
    return p


def _table_paths(schema: dict) -> set[str]:
    return {t["path"] for t in schema["tables"]}


def _columns_of(schema: dict, table_path: str) -> dict[str, str]:
    for t in schema["tables"]:
        if t["path"] == table_path:
            return {c["name"]: c["path"] for c in t["columns"]}
    raise AssertionError(f"no table {table_path!r} in {_table_paths(schema)}")


class TestInferenceIgnoresObjectBranching:
    def test_flat_record_one_root_table(self, tmp_path: Path) -> None:
        schema = infer_v2_schema_from_data(_write(tmp_path, [{"a": 1, "b": "x"}]))
        assert _table_paths(schema) == {"$[:]"}

    def test_object_nesting_does_not_mint_tables(self, tmp_path: Path) -> None:
        # quote_metadata + policy_details are 1-1 objects: SAME relational level.
        rec = {"quote_metadata": {"quote_id": "q1"}, "policy_details": {"policy_number": "p1"}}
        schema = infer_v2_schema_from_data(_write(tmp_path, [rec]))
        assert _table_paths(schema) == {"$[:]"}
        cols = _columns_of(schema, "$[:]")
        assert cols["quote_id"] == "$[:].quote_metadata.quote_id"
        assert cols["policy_number"] == "$[:].policy_details.policy_number"

    def test_array_of_objects_descends_a_level(self, tmp_path: Path) -> None:
        rec = {"id": 1, "drivers": [{"name": "a"}, {"name": "b"}]}
        schema = infer_v2_schema_from_data(_write(tmp_path, [rec]))
        assert _table_paths(schema) == {"$[:]", "$[:].drivers[:]"}

    def test_array_inside_object_lifts_via_object_hop(self, tmp_path: Path) -> None:
        # proposer is 1-1; its claims array is a depth-1 child located via proposer.
        rec = {"proposer": {"name": "a", "claims": [{"amount": 1}]}}
        schema = infer_v2_schema_from_data(_write(tmp_path, [rec]))
        assert _table_paths(schema) == {"$[:]", "$[:].proposer.claims[:]"}
        root_cols = _columns_of(schema, "$[:]")
        assert root_cols["name"] == "$[:].proposer.name"

    def test_collision_qualifies_name_bare_where_unique(self, tmp_path: Path) -> None:
        rec = {
            "breakdown_cover": {"selected": True, "level": "x"},
            "legal_expenses": {"selected": True, "level": "y"},
            "quote_id": "q",
        }
        schema = infer_v2_schema_from_data(_write(tmp_path, [rec]))
        cols = _columns_of(schema, "$[:]")
        # Unique leaf keeps its bare name; colliding 'selected'/'level' qualify.
        assert "quote_id" in cols
        assert "breakdown_cover_selected" in cols
        assert "legal_expenses_selected" in cols
        assert "selected" not in cols
        # The path always carries the full address regardless of the name.
        assert cols["breakdown_cover_selected"] == "$[:].breakdown_cover.selected"

    def test_inference_emits_colon_never_star(self, tmp_path: Path) -> None:
        rec = {"proposer": {"claims": [{"amount": 1}]}, "drivers": [{"x": 1}]}
        schema = infer_v2_schema_from_data(_write(tmp_path, [rec]))
        blob = json.dumps(schema)
        assert "[*]" not in blob
        assert "[:]" in blob


# ---------------------------------------------------------------------------
# Shred — array-in-object ingests; gluing unaffected by object nesting
# ---------------------------------------------------------------------------


def _entry(port: str, col: str, path: str) -> dict:
    return {"source_port": port, "source_column": col, "output_path": path, "enabled": True}


def _col(name: str, path: str, type_: str = "str") -> dict:
    return {"name": name, "path": path, "type": type_, "selected": True}


class TestOutputGluingInvariantUnderObjectNesting:
    """Task-1 output check: mapping values into key-OBJECT pairs instead of
    key-primitive pairs (NOT arrays) must not change the gluing. Object nesting
    only re-addresses; the relational level (and hence which rows join) is set
    by array (``[:]``) depth alone.
    """

    def test_shared_key_glues_the_same_nested_or_flat(self) -> None:
        frames = {
            "a": pl.DataFrame({"k": [1, 2], "va": ["a1", "a2"]}).lazy(),
            "b": pl.DataFrame({"k": [1, 2], "vb": ["b1", "b2"]}).lazy(),
        }
        flat = [
            _entry("a", "k", "$[:].k"),
            _entry("a", "va", "$[:].va"),
            _entry("b", "k", "$[:].k"),
            _entry("b", "vb", "$[:].vb"),
        ]
        # Same data, every path wrapped in a 1-1 ``meta`` object.
        nested = [
            _entry("a", "k", "$[:].meta.k"),
            _entry("a", "va", "$[:].meta.va"),
            _entry("b", "k", "$[:].meta.k"),
            _entry("b", "vb", "$[:].meta.vb"),
        ]
        doc_flat = assemble_output_from_mapping(frames, flat)
        doc_nested = assemble_output_from_mapping(frames, nested)
        # The join actually happened (k bound va to vb per row)...
        assert {(o["k"], o["va"], o["vb"]) for o in doc_flat} == {(1, "a1", "b1"), (2, "a2", "b2")}
        # ...and object nesting changed NOTHING about the gluing — same count,
        # same join, just wrapped one level deeper.
        assert doc_nested == [{"meta": obj} for obj in doc_flat]

    def test_deep_sibling_object_branches_glue_at_one_level(self) -> None:
        # Nick's claim: g at $[:].a.b.c.d.e.f.g and r at $[:].p.q.r are SIBLINGS
        # — different (even deep) object chains glue at the same root level.
        frames = {
            "a": pl.DataFrame({"k": [1], "g": ["G"]}).lazy(),
            "b": pl.DataFrame({"k": [1], "r": ["R"]}).lazy(),
        }
        mapping = [
            _entry("a", "k", "$[:].id"),
            _entry("a", "g", "$[:].a.b.c.d.e.f.g"),
            _entry("b", "k", "$[:].id"),
            _entry("b", "r", "$[:].p.q.r"),
        ]
        doc = assemble_output_from_mapping(frames, mapping)
        assert doc == [
            {
                "id": 1,
                "a": {"b": {"c": {"d": {"e": {"f": {"g": "G"}}}}}},
                "p": {"q": {"r": "R"}},
            }
        ]


_DATA_MODEL_EXAMPLE = Path("tests/fixtures/output_assembler/data_model_example.json")


def _embed_objects(records: list[dict]) -> list[dict]:
    """Return a copy of the data-model example with 1-1 OBJECTS embedded.

    Spurious object branching to prove inference ignores it:
    - root gains ``metadata`` (a 1-1 object, nested two deep);
    - each driver gains ``profile`` (1-1, with a nested 1-1 ``residence``);
    - each driver gains ``history`` — a 1-1 object that CONTAINS an array of
      objects (``claims``), which DOES descend a relational level, located
      through the ``history`` object hop.
    None of the object wrappers may mint a table; only ``history.claims`` does.
    """
    out = json.loads(json.dumps(records))
    for policy in out:
        policy["metadata"] = {"channel": "web", "geo": {"region": "EU"}}
        for driver in policy.get("drivers", []):
            driver["profile"] = {"occupation": "engineer", "residence": {"country": "GB"}}
            driver["history"] = {"claims": [{"claim_id": 1, "amount": 100.0}]}
    return out


class TestDataModelObjectEmbeddingIgnoresBranching:
    """Task-2 demonstration: embed objects in the data-model sample; inference
    ignores the spurious branching (object wrappers add zero tables; only an
    array of objects — even one nested inside a 1-1 object — descends a level).
    """

    def test_object_wrappers_add_no_tables_only_arrays_do(self, tmp_path: Path) -> None:
        original = json.loads(_DATA_MODEL_EXAMPLE.read_text())
        base_paths = _table_paths(infer_v2_schema_from_data(_write(tmp_path / "a", original)))
        assert base_paths == {
            "$[:]",
            "$[:].drivers[:]",
            "$[:].drivers[:].licenses[:]",
            "$[:].vehicles[:]",
        }

        embedded = _embed_objects(original)
        emb_path = tmp_path / "b" / "data.json"
        emb_path.parent.mkdir()
        emb_path.write_text(json.dumps(embedded))
        emb_schema = infer_v2_schema_from_data(emb_path)
        emb_paths = _table_paths(emb_schema)

        # The four 1-1 object wrappers (metadata, metadata.geo, profile,
        # profile.residence, history) mint NO tables. Only history.claims —
        # an array inside the 1-1 history object — descends a level.
        assert emb_paths == base_paths | {"$[:].drivers[:].history.claims[:]"}

        # The embedded scalars flatten into their array level via object paths.
        root_cols = _columns_of(emb_schema, "$[:]")
        assert root_cols["channel"] == "$[:].metadata.channel"
        assert root_cols["region"] == "$[:].metadata.geo.region"
        driver_cols = _columns_of(emb_schema, "$[:].drivers[:]")
        assert driver_cols["occupation"] == "$[:].drivers[:].profile.occupation"
        assert driver_cols["country"] == "$[:].drivers[:].profile.residence.country"
        # The array-in-object child table is located through the object hop.
        claims_cols = _columns_of(emb_schema, "$[:].drivers[:].history.claims[:]")
        assert set(claims_cols) == {"claim_id", "amount"}


class TestShredObjectNesting:
    def test_object_scalars_flatten_into_root_rows(self) -> None:
        config = {
            "tables": [
                {
                    "path": "$[:]",
                    "label": "root",
                    "emit": True,
                    "columns": [
                        _col("quote_id", "$[:].quote_metadata.quote_id"),
                        _col("policy_number", "$[:].policy_details.policy_number"),
                    ],
                }
            ]
        }
        records = [
            {"quote_metadata": {"quote_id": "q1"}, "policy_details": {"policy_number": "p1"}}
        ]
        buffers = shred_to_buffers(records, config)
        assert buffers["root"] == [{"quote_id": "q1", "policy_number": "p1"}]

    def test_array_inside_object_shreds_with_ancestor_key(self) -> None:
        config = {
            "tables": [
                {
                    "path": "$[:].proposer.claims[:]",
                    "label": "claims",
                    "emit": True,
                    "columns": [
                        # ancestor key sourced at root, through an object hop
                        _col("quote_id", "$[:].quote_metadata.quote_id"),
                        _col("amount", "$[:].proposer.claims[:].amount", "int"),
                    ],
                }
            ]
        }
        records = [
            {
                "quote_metadata": {"quote_id": "q1"},
                "proposer": {"claims": [{"amount": 10}, {"amount": 20}]},
            }
        ]
        buffers = shred_to_buffers(records, config)
        assert buffers["claims"] == [
            {"quote_id": "q1", "amount": 10},
            {"quote_id": "q1", "amount": 20},
        ]
