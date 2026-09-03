"""EXEC-P08 — the schema-only declaration is honoured for OUTPUT documents.

``execute_lazy_graph(schema_only=True)`` declares that the caller reads
``collect_schema()`` and never collects. The OUTPUT node used to assemble its
whole document at build time regardless, so a schema-only caller collected in
its own process with the group-by admission gate relaxed *because* of the
declaration.

The declaration now reaches node builders through ``NodeBuildContext.schema_only``
and ``haute._output_assembler.output_document_schema`` is the single schema
authority for both paths: the schema-only build returns an empty frame under the
derived schema, and the collected build declares that same schema over the
assembled document instead of letting Python inference pick dtypes.

The obligations proved here:

* **tripwire** — a schema-only execution over every corpus shape never calls
  ``LazyFrame.collect`` and never calls ``_assemble_document``, and its schema is
  exactly ``output_document_schema(...)``;
* **derivation fidelity** — on an inference-exact corpus with complete rows the
  derived schema equals the schema Python inference produced from the assembled
  document (structure, leaf mapping, and field order);
* **dtype fidelity** — over a wide dtype matrix (scalar *and* ``List``/``Struct``/
  ``Array`` container leaves), declaring the derived schema is rendering-neutral
  and every leaf keeps its source dtype (all-null columns and an empty source
  frame included); container leaves group by a canonical hashable identity;
* **equality by construction** — the collected OUTPUT frame's schema equals the
  schema-only frame's schema for every corpus shape;
* **typed rejections** — a missing port, a missing column, and conflicting
  dtypes on one output path are ``OutputMappingSchemaError``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import polars as pl
import pytest

import haute._output_assembler as assembler_module
from haute._output_assembler import (
    OutputMappingSchemaError,
    _prune,
    assemble_output_from_mapping,
    output_document_schema,
    render_output_document,
)
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import execute_lazy_graph
from haute.executor import _build_node_fn

# ---------------------------------------------------------------------------
# Corpus — one entry per OUTPUT document shape the assembler can produce
# ---------------------------------------------------------------------------


def _entry(port: str, column: str, path: str) -> dict[str, Any]:
    return {
        "source_port": port,
        "source_column": column,
        "output_path": path,
        "enabled": True,
    }


Corpus = tuple[dict[str, pl.LazyFrame], list[dict[str, Any]]]


def _flat_corpus() -> Corpus:
    """One port, only top-level scalar leaves."""
    return (
        {"main": pl.LazyFrame({"id": [1, 2], "premium": [10.5, 20.5]})},
        [_entry("main", "id", "$[:].id"), _entry("main", "premium", "$[:].premium")],
    )


def _nested_object_corpus() -> Corpus:
    """One port, object nesting two levels deep alongside a top-level leaf."""
    return (
        {"main": pl.LazyFrame({"id": [1, 2], "prem": [10.5, 20.5], "nm": ["a", "b"]})},
        [
            _entry("main", "id", "$[:].policy.id"),
            _entry("main", "prem", "$[:].policy.detail.premium"),
            _entry("main", "nm", "$[:].name"),
        ],
    )


def _one_array_corpus() -> Corpus:
    """Two ports: a parent level plus one child array sharing an ancestor key."""
    return (
        {
            "policies": pl.LazyFrame({"pid": [1, 2], "flag": [True, False]}),
            "drivers": pl.LazyFrame({"pid": [1, 1, 2], "did": [10, 11, 20], "nm": ["a", "b", "c"]}),
        },
        [
            _entry("policies", "pid", "$[:].policy_id"),
            _entry("policies", "flag", "$[:].flag"),
            _entry("drivers", "pid", "$[:].policy_id"),
            _entry("drivers", "did", "$[:].drivers[:].driver_id"),
            _entry("drivers", "nm", "$[:].drivers[:].name"),
        ],
    )


def _two_arrays_corpus() -> Corpus:
    """Three ports nesting two array levels deep (drivers → licenses)."""
    return (
        {
            "policies": pl.LazyFrame({"pid": [1, 2]}),
            "drivers": pl.LazyFrame({"pid": [1, 1, 2], "did": [10, 11, 20]}),
            "licenses": pl.LazyFrame(
                {"pid": [1, 1, 2], "did": [10, 11, 20], "code": ["x", "y", "z"]}
            ),
        },
        [
            _entry("policies", "pid", "$[:].policy_id"),
            _entry("drivers", "pid", "$[:].policy_id"),
            _entry("drivers", "did", "$[:].drivers[:].driver_id"),
            _entry("licenses", "pid", "$[:].policy_id"),
            _entry("licenses", "did", "$[:].drivers[:].driver_id"),
            _entry("licenses", "code", "$[:].drivers[:].licenses[:].code"),
        ],
    )


def _siblings_corpus() -> Corpus:
    """A multi-port parent with two sibling child arrays sharing its ancestor key."""
    return (
        {
            "policies": pl.LazyFrame({"pid": [1, 2], "d": [date(2020, 1, 1), date(2021, 2, 3)]}),
            "drivers": pl.LazyFrame({"pid": [1, 1, 2], "did": [10, 11, 20]}),
            "vehicles": pl.LazyFrame({"pid": [1, 2], "vrn": ["AA", "BB"]}),
        },
        [
            _entry("policies", "pid", "$[:].policy_id"),
            _entry("policies", "d", "$[:].start_date"),
            _entry("drivers", "pid", "$[:].policy_id"),
            _entry("drivers", "did", "$[:].drivers[:].driver_id"),
            _entry("vehicles", "pid", "$[:].policy_id"),
            _entry("vehicles", "vrn", "$[:].vehicles[:].vrn"),
        ],
    )


CORPUS: dict[str, Callable[[], Corpus]] = {
    "flat": _flat_corpus,
    "nested_object": _nested_object_corpus,
    "one_array": _one_array_corpus,
    "two_arrays": _two_arrays_corpus,
    "siblings": _siblings_corpus,
}


def _source_schemas(frames: dict[str, pl.LazyFrame]) -> dict[str, pl.Schema]:
    return {port: frame.collect_schema() for port, frame in frames.items()}


# ---------------------------------------------------------------------------
# Graph plumbing — a stubbed multi-port source feeding the real OUTPUT builder
# ---------------------------------------------------------------------------


def _output_graph(frames: dict[str, pl.LazyFrame], mapping: list[dict[str, Any]]):
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(label="src", nodeType=NodeType.API_INPUT, config={}),
            ),
            GraphNode(
                id="out",
                data=NodeData(
                    label="out",
                    nodeType=NodeType.OUTPUT,
                    config={"outputMapping": mapping, "outputFormat": "json"},
                ),
            ),
        ],
        edges=[
            GraphEdge(id=f"src_out_{port}", source="src", target="out", sourceHandle=port)
            for port in frames
        ],
    )

    def build_node_fn(node: GraphNode, **kwargs: Any):
        if node.id == "src":
            return node.id, (lambda: dict(frames)), True
        return _build_node_fn(node, **kwargs)

    return graph, build_node_fn


def _output_frame(
    frames: dict[str, pl.LazyFrame],
    mapping: list[dict[str, Any]],
    *,
    schema_only: bool,
) -> pl.LazyFrame:
    graph, build_node_fn = _output_graph(frames, mapping)
    outputs, *_ = execute_lazy_graph(
        graph,
        build_node_fn,
        target_node_id="out",
        schema_only=schema_only,
    )
    frame = outputs["out"]
    assert isinstance(frame, pl.LazyFrame)
    return frame


# ---------------------------------------------------------------------------
# Tripwire — a schema-only execution neither collects nor assembles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_schema_only_output_never_collects_or_assembles(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    frames, mapping = CORPUS[shape]()
    expected = output_document_schema(_source_schemas(frames), mapping)

    assemble_calls: list[object] = []

    def counting_assemble(field_frames):  # noqa: ANN001, ANN202
        assemble_calls.append(field_frames)
        raise AssertionError("a schema-only OUTPUT must never assemble its document")

    def poisoned_collect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("a schema-only execution must never collect")

    monkeypatch.setattr(assembler_module, "_assemble_document", counting_assemble)
    monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)

    frame = _output_frame(frames, mapping, schema_only=True)
    schema = frame.collect_schema()

    assert assemble_calls == []
    assert schema == expected


# ---------------------------------------------------------------------------
# Derivation fidelity — the derived schema is what inference used to produce
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_derived_schema_equals_inferred_schema_on_complete_rows(shape: str) -> None:
    """On inference-exact dtypes with complete rows the derivation is the schema
    Python inference produced from the assembled document — same structure, same
    leaf mapping, same field order."""
    frames, mapping = CORPUS[shape]()
    document = assemble_output_from_mapping(frames, mapping)
    inferred = pl.LazyFrame(document, infer_schema_length=None).collect_schema()
    derived = output_document_schema(_source_schemas(frames), mapping)

    assert derived == inferred
    assert list(derived.names()) == list(inferred.names())


# ---------------------------------------------------------------------------
# Equality by construction — collected schema == schema-only schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_collected_output_schema_equals_schema_only_schema(shape: str) -> None:
    frames, mapping = CORPUS[shape]()
    collected = _output_frame(frames, mapping, schema_only=False)
    declared = _output_frame(frames, mapping, schema_only=True)

    assert collected.collect_schema() == declared.collect_schema()
    assert collected.collect_schema() == output_document_schema(_source_schemas(frames), mapping)


@pytest.mark.parametrize("shape", sorted(CORPUS))
def test_empty_document_keeps_the_typed_schema(shape: str) -> None:
    """An empty document used to infer *no columns*; it now keeps its dtypes."""
    frames, mapping = CORPUS[shape]()
    empty = {port: frame.head(0) for port, frame in frames.items()}
    expected = output_document_schema(_source_schemas(frames), mapping)

    collected = _output_frame(empty, mapping, schema_only=False)
    assert collected.collect_schema() == expected
    assert collected.collect().height == 0


# ---------------------------------------------------------------------------
# Dtype fidelity — a wide dtype matrix
# ---------------------------------------------------------------------------

#: Scalar dtypes the Python assembler can carry. ``_group_rows`` keys an object's
#: identity on its own values through ``_identity``, which canonicalises container
#: values into hashable tuples — so the container leaves of ``CONTAINER_DTYPES``
#: are ordinary OUTPUT leaves and join this matrix below.
WIDE_DTYPES: dict[str, pl.Series] = {
    "i8": pl.Series([1, -2], dtype=pl.Int8),
    "i16": pl.Series([1, -2], dtype=pl.Int16),
    "i32": pl.Series([1, -2], dtype=pl.Int32),
    "i64": pl.Series([1, -2], dtype=pl.Int64),
    "u8": pl.Series([1, 2], dtype=pl.UInt8),
    "u16": pl.Series([1, 2], dtype=pl.UInt16),
    "u32": pl.Series([1, 2], dtype=pl.UInt32),
    "u64": pl.Series([1, 2], dtype=pl.UInt64),
    "f32": pl.Series([1.5, 2.5], dtype=pl.Float32),
    "f64": pl.Series([1.5, 2.5], dtype=pl.Float64),
    "boolean": pl.Series([True, False], dtype=pl.Boolean),
    "string": pl.Series(["a", "b"], dtype=pl.String),
    "categorical": pl.Series(["a", "b"], dtype=pl.Categorical),
    "enum": pl.Series(["a", "b"], dtype=pl.Enum(["a", "b"])),
    "date": pl.Series([date(2020, 1, 1), date(2021, 2, 3)], dtype=pl.Date),
    "datetime_ms": pl.Series([datetime(2020, 1, 1), datetime(2021, 1, 1)], dtype=pl.Datetime("ms")),
    "datetime_us": pl.Series([datetime(2020, 1, 1), datetime(2021, 1, 1)], dtype=pl.Datetime("us")),
    "datetime_ns": pl.Series([datetime(2020, 1, 1), datetime(2021, 1, 1)], dtype=pl.Datetime("ns")),
    "datetime_tz": pl.Series(
        [datetime(2020, 1, 1), datetime(2021, 1, 1)], dtype=pl.Datetime("us", "UTC")
    ),
    "duration": pl.Series([timedelta(days=1), timedelta(days=2)], dtype=pl.Duration("us")),
    "time": pl.Series([time(1, 2), time(3, 4)], dtype=pl.Time),
    "decimal": pl.Series([Decimal("1.50"), Decimal("2.25")], dtype=pl.Decimal(10, 2)),
    "binary": pl.Series([b"x", b"y"], dtype=pl.Binary),
    "null": pl.Series([None, None], dtype=pl.Null),
    "all_null_int32": pl.Series([None, None], dtype=pl.Int32),
}

#: Container leaves. ``_identity`` canonicalises their Python values (``list`` /
#: ``dict``) into hashable tuples, so they assemble like any other leaf.
CONTAINER_DTYPES: dict[str, pl.Series] = {
    "list": pl.Series([[1, 2], [3]], dtype=pl.List(pl.Int64)),
    "struct": pl.Series([{"a": 1}, {"a": 2}], dtype=pl.Struct({"a": pl.Int64})),
    "array": pl.Series([[1, 2], [3, 4]], dtype=pl.Array(pl.Int64, 2)),
}

#: The full rendering-neutrality matrix: scalar leaves plus container leaves.
MATRIX_DTYPES: dict[str, pl.Series] = {**WIDE_DTYPES, **CONTAINER_DTYPES}


def _wide_corpus(frame: pl.LazyFrame, names: list[str]) -> Corpus:
    mapping = [_entry("main", "i64", "$[:].top")] if "i64" in names else []
    mapping += [_entry("main", name, f"$[:].wide.{name}") for name in names]
    return {"main": frame}, mapping


def test_wide_dtype_matrix_declares_source_dtypes_and_renders_unchanged() -> None:
    frame = pl.DataFrame(MATRIX_DTYPES).lazy()
    frames, mapping = _wide_corpus(frame, list(MATRIX_DTYPES))
    derived = output_document_schema(_source_schemas(frames), mapping)

    wide = derived["wide"].to_schema()
    for name, series in MATRIX_DTYPES.items():
        assert wide[name] == series.dtype, name
    assert derived["top"] == pl.Int64

    document = assemble_output_from_mapping(frames, mapping)
    declared = pl.LazyFrame(document, schema=derived).collect()
    assert declared.schema == derived
    # Declaring the derived schema is rendering-neutral: the null padding polars
    # adds under a uniform schema is pruned back out at render time.
    assert render_output_document(declared) == _prune(document)


def test_wide_dtype_matrix_empty_source_keeps_the_typed_schema() -> None:
    frame = pl.DataFrame(MATRIX_DTYPES).lazy()
    frames, mapping = _wide_corpus(frame, list(MATRIX_DTYPES))
    derived = output_document_schema(_source_schemas(frames), mapping)

    empty = {"main": frame.head(0)}
    assert output_document_schema(_source_schemas(empty), mapping) == derived
    document = assemble_output_from_mapping(empty, mapping)
    assert document == []
    assert pl.LazyFrame(document, schema=derived).collect_schema() == derived
    assert pl.LazyFrame(schema=derived).collect_schema() == derived


def test_container_leaf_values_group_by_canonical_identity() -> None:
    """A ``List`` leaf is a valid object identity: equal list values co-locate
    their children under one parent object, distinct list values stay distinct.

    Shaped like :func:`_one_array_corpus`, but the parent's own leaf is a
    ``List(Int64)`` column rather than a scalar.
    """
    frames = {
        "policies": pl.LazyFrame(
            {
                "pid": [1, 2],
                "tags": pl.Series([[1, 2], [1, 2]], dtype=pl.List(pl.Int64)),
            }
        ),
        "drivers": pl.LazyFrame({"pid": [1, 1, 2], "did": [10, 11, 20]}),
    }
    mapping = [
        _entry("policies", "tags", "$[:].tags"),
        _entry("drivers", "pid", "$[:].policy_id"),
        _entry("policies", "pid", "$[:].policy_id"),
        _entry("drivers", "did", "$[:].drivers[:].driver_id"),
    ]
    # Equal ``tags`` values are not enough to merge: identity is the tuple of ALL
    # the level's own leaves, and ``policy_id`` still separates the two objects.
    document = assemble_output_from_mapping(frames, mapping)
    assert document == [
        {"policy_id": 1, "tags": [1, 2], "drivers": [{"driver_id": 10}, {"driver_id": 11}]},
        {"policy_id": 2, "tags": [1, 2], "drivers": [{"driver_id": 20}]},
    ]

    # With the list leaf as the *only* identity, equal lists group into one object
    # and a distinct list stays its own object.
    frames = {
        "policies": pl.LazyFrame(
            {"tags": pl.Series([[1, 2], [1, 2], [3]], dtype=pl.List(pl.Int64))}
        )
    }
    mapping = [_entry("policies", "tags", "$[:].tags")]
    assert assemble_output_from_mapping(frames, mapping) == [{"tags": [1, 2]}, {"tags": [3]}]


@pytest.mark.parametrize(
    ("dtype", "parent_keys", "child_keys"),
    [
        (pl.List(pl.Int64), [[1, 2], [3]], [[1, 2], [1, 2], [3], [9]]),
        (
            pl.Struct({"a": pl.Int64, "b": pl.String}),
            [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
            [{"a": 1, "b": "x"}, {"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 9, "b": "z"}],
        ),
        (pl.Array(pl.Int64, 2), [[1, 2], [3, 4]], [[1, 2], [1, 2], [3, 4], [9, 9]]),
    ],
    ids=["list", "struct", "array"],
)
def test_container_relation_keys_nest_children_under_their_parent(
    dtype: pl.DataType, parent_keys: list[Any], child_keys: list[Any]
) -> None:
    """A container-valued *relation* key (carried by both the parent and the
    child frame) nests children under the parent with the equal value.

    This exercises the ancestor index and the scoped lookup, not only the
    parent-level grouping: the child rows are indexed by the canonical identity
    of the shared key and looked up by the parent's own value, so a child whose
    key matches no parent is dropped and never mis-filed.
    """
    frames = {
        "policies": pl.LazyFrame(
            {
                "key": pl.Series(parent_keys, dtype=dtype),
                "flag": [True, False],
            }
        ),
        "drivers": pl.LazyFrame(
            {
                "key": pl.Series(child_keys, dtype=dtype),
                "did": [10, 11, 20, 99],
            }
        ),
    }
    mapping = [
        _entry("policies", "key", "$[:].key"),
        _entry("policies", "flag", "$[:].flag"),
        _entry("drivers", "key", "$[:].key"),
        _entry("drivers", "did", "$[:].drivers[:].driver_id"),
    ]
    document = assemble_output_from_mapping(frames, mapping)
    rendered_keys = [parent_keys[0], parent_keys[1]]
    assert document == [
        {"key": rendered_keys[0], "flag": True, "drivers": [{"driver_id": 10}, {"driver_id": 11}]},
        {"key": rendered_keys[1], "flag": False, "drivers": [{"driver_id": 20}]},
    ]

    # The declared document schema carries the container key and nests the
    # children; declaring it stays rendering-neutral for container-keyed documents.
    derived = output_document_schema(_source_schemas(frames), mapping)
    assert derived["key"] == dtype
    assert derived["drivers"] == pl.List(pl.Struct({"driver_id": pl.Int64}))
    declared = pl.LazyFrame(document, schema=derived).collect()
    assert render_output_document(declared) == _prune(document)


# ---------------------------------------------------------------------------
# Typed rejections
# ---------------------------------------------------------------------------


def test_missing_source_port_is_a_typed_rejection() -> None:
    frames, mapping = _flat_corpus()
    mapping = [*mapping, _entry("other", "id", "$[:].other_id")]
    with pytest.raises(OutputMappingSchemaError, match="'other'"):
        output_document_schema(_source_schemas(frames), mapping)


def test_missing_source_column_is_a_typed_rejection() -> None:
    frames, mapping = _flat_corpus()
    mapping = [*mapping, _entry("main", "no_such_column", "$[:].ghost")]
    with pytest.raises(OutputMappingSchemaError, match="no_such_column"):
        output_document_schema(_source_schemas(frames), mapping)


def test_conflicting_dtypes_on_one_output_path_is_a_typed_rejection() -> None:
    frames = {
        "a": pl.LazyFrame({"k": pl.Series([1, 2], dtype=pl.Int64)}),
        "b": pl.LazyFrame({"k": pl.Series(["1", "2"], dtype=pl.String)}),
    }
    mapping = [_entry("a", "k", "$[:].k"), _entry("b", "k", "$[:].k")]
    with pytest.raises(OutputMappingSchemaError, match="different types"):
        output_document_schema(_source_schemas(frames), mapping)


def test_output_schema_ignores_duplicate_identical_mapping_entry_per_port() -> None:
    frames, mapping = _flat_corpus()
    duplicate = mapping[0].copy()
    assert output_document_schema(
        _source_schemas(frames), [*mapping, duplicate]
    ) == output_document_schema(_source_schemas(frames), mapping)
