"""Version-pinned Polars compatibility corpus.

``tests/polars_compatibility_corpus.json`` records, for a representative shape
per registered operation class and namespace, the classification the live
analysers give it today: lineage support, cardinality availability, chunk
eligibility, and the planner strategy under an admitted execution context (both
with and without a native worker memory cap), together with the pinned Polars
version.  Any difference fails this test, so a Polars upgrade or an analyser
change cannot silently turn a working shape into a rejection.

Regenerating the corpus is deliberately a manual, reviewed edit — pytest never
rewrites it, so CI cannot regenerate it silently.  From the repository root::

    .venv/Scripts/python.exe -m tests.test_polars_compatibility_corpus

then review the resulting diff of ``tests/polars_compatibility_corpus.json``
before committing it.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from haute._column_lineage import analyze_polars_cardinality, analyze_polars_lineage
from haute._execution_context import ExecutionProfile
from haute._native_memory_limit import native_memory_backend_scope
from haute._polars_operations import (
    POLARS_OPERATIONS,
    OperationClass,
    OperationReceiver,
    PolarsOperation,
)
from haute._polars_operations import (
    operation as registry_operation,
)
from haute.chunking import classify_chunk_local_polars_code
from haute.errors import GroupByExecutionUnsupportedError
from haute.execution import ProjectionRequest, plan_execution_strategy
from tests.conftest import make_edge, make_graph, make_ready_file_input_config
from tests.test_polars_backend_strategy_contract import _context

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

CORPUS_PATH = Path(__file__).with_name("polars_compatibility_corpus.json")

_SOURCE_ROWS = 20
_SOURCE_COLUMNS = ("segment", "premium", "extra", "s", "t", "l")

_GROUP_BY_PREMIUM = "df = df.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
_GROUP_BY_VALUE = "df = df.group_by('segment').agg(pl.col('value').sum().alias('total'))"


def _write_source(path: Path) -> None:
    """Write the one fixed source frame every corpus shape reads."""
    rows = _SOURCE_ROWS
    pl.DataFrame(
        {
            "segment": [f"seg-{index % 4}" for index in range(rows)],
            "premium": [None if index % 7 == 0 else float(index) for index in range(rows)],
            "extra": list(range(rows)),
            "s": [
                None if index % 5 == 0 else f"a{'x' if index % 2 else 'y'}{index}"
                for index in range(rows)
            ],
            "t": [
                None if index % 6 == 0 else date(2024, 1 + (index % 12), 1 + (index % 28))
                for index in range(rows)
            ],
            "l": [list(range(index % 3)) for index in range(rows)],
        },
        schema={
            "segment": pl.String,
            "premium": pl.Float64,
            "extra": pl.Int64,
            "s": pl.String,
            "t": pl.Date,
            "l": pl.List(pl.Int64),
        },
    ).write_parquet(str(path))


# ---------------------------------------------------------------------------
# The corpus shapes
# ---------------------------------------------------------------------------
# (id, receiver, namespace, operation, code, group_by_code, output_column)
# ``receiver`` names what the operation is called on ("frame", "expr",
# "namespace" or "polars_function").  It is the first element of the registry
# key, so a shape can only be looked up receiver-aware: the same name can carry
# a different class on a different receiver.
# ``receiver`` and ``operation`` are ``None`` only for a deliberate decoy: code
# whose comment or string literal mentions an operation the classifier must not
# react to.

_SHAPES: tuple[tuple[str, str | None, str | None, str | None, str, str, str], ...] = (
    # ------------------------------------------------------- frame row-local
    (
        "frame_select",
        "frame",
        None,
        "select",
        "df = df.select(['segment', 'premium', 'extra'])",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_with_columns",
        "frame",
        None,
        "with_columns",
        "df = df.with_columns((pl.col('premium') * 2).alias('doubled'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_filter",
        "frame",
        None,
        "filter",
        "df = df.filter(pl.col('premium') > 0)",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_rename",
        "frame",
        None,
        "rename",
        "df = df.rename({'extra': 'extra_renamed'})",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_cast",
        "frame",
        None,
        "cast",
        "df = df.cast({'extra': pl.Float64})",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_fill_null",
        "expr",
        None,
        "fill_null",
        "df = df.with_columns(pl.col('premium').fill_null(0.0))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_drop",
        "frame",
        None,
        "drop",
        "df = df.drop('extra')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_drop_nulls",
        "frame",
        None,
        "drop_nulls",
        "df = df.drop_nulls(subset=['premium'])",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # -------------------------------------------------------order-dependent
    (
        "frame_sort",
        "frame",
        None,
        "sort",
        "df = df.sort('premium')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_unique",
        "frame",
        None,
        "unique",
        "df = df.unique(subset=['segment'])",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_reverse",
        "frame",
        None,
        "reverse",
        "df = df.reverse()",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_top_k",
        "frame",
        None,
        "top_k",
        "df = df.top_k(5, by='premium')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_bottom_k",
        "frame",
        None,
        "bottom_k",
        "df = df.bottom_k(5, by='premium')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_shift",
        "frame",
        None,
        "shift",
        "df = df.shift(1)",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_head",
        "frame",
        None,
        "head",
        "df = df.head(10)",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_with_row_index",
        "frame",
        None,
        "with_row_index",
        "df = df.with_row_index('row_id')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "expr_shift",
        "expr",
        None,
        "shift",
        "df = df.with_columns(pl.col('premium').shift(1).alias('previous'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "expr_cum_sum",
        "expr",
        None,
        "cum_sum",
        "df = df.with_columns(pl.col('premium').cum_sum().alias('running'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # --------------------------------------------------------- row-expanding
    (
        "frame_explode",
        "frame",
        None,
        "explode",
        "df = df.explode('l')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_unpivot_literal",
        "frame",
        None,
        "unpivot",
        "df = df.unpivot(on=['premium', 'extra'], index=['segment'])",
        _GROUP_BY_VALUE,
        "total",
    ),
    (
        "frame_unpivot_dynamic",
        "frame",
        None,
        "unpivot",
        "df = df.unpivot(index=['segment'])",
        _GROUP_BY_VALUE,
        "total",
    ),
    (
        "list_explode",
        "namespace",
        "list",
        "explode",
        "df = df.with_columns(pl.col('l').list.explode())",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # ------------------------------------------------------- fan-in stateful
    (
        "frame_group_by",
        "frame",
        None,
        "group_by",
        "df = df.group_by('segment').agg(pl.col('premium').sum().alias('premium'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_join_self",
        "frame",
        None,
        "join",
        "df = df.join(df, on='segment', how='left')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "expr_over",
        "expr",
        None,
        "over",
        "df = df.with_columns(pl.col('premium').sum().over('segment').alias('segment_total'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_join_asof_self",
        "frame",
        None,
        "join_asof",
        "df = df.sort('t').join_asof(df.sort('t'), on='t')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_merge_sorted_self",
        "frame",
        None,
        "merge_sorted",
        "df = df.sort('t').merge_sorted(df.sort('t'), key='t')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_interpolate",
        "frame",
        None,
        "interpolate",
        "df = df.interpolate()",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_rolling",
        "frame",
        None,
        "rolling",
        (
            "df = df.rolling(index_column='t', period='30d')"
            ".agg(pl.col('premium').sum().alias('premium'))"
        ),
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_group_by_dynamic",
        "frame",
        None,
        "group_by_dynamic",
        (
            "df = df.group_by_dynamic('t', every='1mo')"
            ".agg(pl.col('premium').sum().alias('premium'))"
        ),
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "expr_sum_reduction",
        "expr",
        None,
        "sum",
        "df = df.select(pl.col('segment'), pl.col('premium').sum().alias('premium'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # ---------------------------------------------------------------- opaque
    (
        "frame_map_batches",
        "frame",
        None,
        "map_batches",
        "df = df.map_batches(lambda frame: frame)",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "expr_map_elements",
        "expr",
        None,
        "map_elements",
        "df = df.with_columns(pl.col('extra').map_elements(lambda value: value).alias('mapped'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "frame_pipe",
        "frame",
        None,
        "pipe",
        "df = df.pipe(lambda frame: frame)",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # ------------------------------------------------------- str namespace
    (
        "str_contains_literal",
        "namespace",
        "str",
        "contains",
        "df = df.filter(pl.col('s').str.contains('x'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "str_to_uppercase",
        "namespace",
        "str",
        "to_uppercase",
        "df = df.with_columns(pl.col('s').str.to_uppercase().alias('s_upper'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "str_replace",
        "namespace",
        "str",
        "replace",
        "df = df.with_columns(pl.col('s').str.replace('x', 'y').alias('s_replaced'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "str_to_date_with_format",
        "namespace",
        "str",
        "to_date",
        "df = df.with_columns(pl.col('s').str.to_date('%Y-%m-%d').alias('s_date'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "str_to_date_without_format",
        "namespace",
        "str",
        "to_date",
        "df = df.with_columns(pl.col('s').str.to_date().alias('s_date'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "str_contains_column_argument",
        "namespace",
        "str",
        "contains",
        "df = df.filter(pl.col('s').str.contains(pl.col('segment')))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # -------------------------------------------------------- dt namespace
    (
        "dt_year",
        "namespace",
        "dt",
        "year",
        "df = df.with_columns(pl.col('t').dt.year().alias('year'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "dt_truncate",
        "namespace",
        "dt",
        "truncate",
        "df = df.with_columns(pl.col('t').dt.truncate('1mo').alias('month'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "dt_offset_by",
        "namespace",
        "dt",
        "offset_by",
        "df = df.with_columns(pl.col('t').dt.offset_by('1d').alias('shifted'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # ------------------------------------------------------- pl functions
    (
        "fn_sum_horizontal",
        "polars_function",
        None,
        "sum_horizontal",
        "df = df.with_columns(pl.sum_horizontal('premium', 'extra').alias('total'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "fn_when_then_otherwise",
        "polars_function",
        None,
        "when",
        "df = df.with_columns(pl.when(pl.col('premium') > 0).then(1).otherwise(0).alias('flag'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "fn_min_horizontal",
        "polars_function",
        None,
        "min_horizontal",
        "df = df.with_columns(pl.min_horizontal('premium', 'extra').alias('lowest'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "fn_concat_str",
        "polars_function",
        None,
        "concat_str",
        "df = df.with_columns(pl.concat_str(['segment', 's']).alias('joined'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "fn_all_selector",
        "polars_function",
        None,
        "all",
        "df = df.select(pl.all())",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    # ---------------------------------------------------------------- decoys
    (
        "decoy_comment_mentions_sort",
        None,
        None,
        None,
        "# .sort(\ndf = df.filter(pl.col('premium') > 0)",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "decoy_string_literal_mentions_sort",
        None,
        None,
        None,
        "df = df.filter(pl.col('s') != '.sort(')",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
)


# ---------------------------------------------------------------------------
# Building one shape's record from the live analysers
# ---------------------------------------------------------------------------


def _shape_graph(path: Path, transform_code: str, group_by_code: str, output_column: str):
    """source -> shape -> group_by(segment) -> output."""
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                },
                {
                    "id": "shape",
                    "data": {
                        "label": "shape",
                        "nodeType": "polars",
                        "config": {"code": transform_code},
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {"code": group_by_code},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "agg",
                                    "source_column": output_column,
                                    "output_path": f"$[:].{output_column}",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "shape").model_dump(),
                make_edge("shape", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )


def _plan_record(profile: ExecutionProfile, graph) -> dict[str, object]:
    try:
        result = plan_execution_strategy(
            ProjectionRequest(graph=graph, target_node_id="out", profile=profile),
            execution_context=_context(
                profile,
                memory_limit_bytes=1 << 30,
                headroom_bytes=1 << 30,
            ),
        )
    except GroupByExecutionUnsupportedError as error:
        return {"rejected": error.reason_code}
    return {
        "strategy": result.strategy.value,
        "status": result.status.value,
        "reason_code": result.diagnostic.reason_code,
    }


def _strategy_records(graph) -> tuple[dict[str, object], dict[str, object]]:
    """Plan on every profile; the record must not depend on the profile."""
    plain = [_plan_record(profile, graph) for profile in ExecutionProfile]
    with native_memory_backend_scope("rlimit"):
        capped = [_plan_record(profile, graph) for profile in ExecutionProfile]
    assert all(record == plain[0] for record in plain)
    assert all(record == capped[0] for record in capped)
    return plain[0], capped[0]


def _registry_entry(receiver: str | None, namespace: str | None, operation: str | None):
    """Look the shape's operation up receiver-aware; ``None`` only for a decoy."""
    if receiver is None or operation is None:
        assert receiver is None and operation is None
        return None
    return registry_operation(OperationReceiver(receiver), operation, namespace)


def build_shape(directory: Path, shape) -> dict[str, object]:
    """Build one corpus record from the live analysers."""
    shape_id, receiver, namespace, operation, code, group_by_code, output_column = shape

    entry = _registry_entry(receiver, namespace, operation)
    assert entry is not None or receiver is None, shape_id

    lineage = analyze_polars_lineage(code, {"df": frozenset(_SOURCE_COLUMNS)}).reason
    cardinality = analyze_polars_cardinality(code, {"df": _SOURCE_ROWS}).reason
    decision = classify_chunk_local_polars_code(code, frame_names=["df"])
    chunk: dict[str, object] = {"eligible": decision.eligible, "reason": decision.reason}
    if not decision.eligible:
        chunk["blocking_operator"] = decision.blocking_operator

    path = directory / f"{shape_id}.parquet"
    _write_source(path)
    graph = _shape_graph(path, code, group_by_code, output_column)
    strategy, strategy_under_cap = _strategy_records(graph)

    return {
        "id": shape_id,
        "receiver": receiver,
        "namespace": namespace,
        "operation": operation,
        "class": None if entry is None else entry.operation_class.value,
        "policy": None if entry is None else entry.policy.value,
        "expansion": None if entry is None else entry.expansion,
        "code": code,
        "lineage": lineage,
        "cardinality": cardinality,
        "chunk": chunk,
        "strategy": strategy,
        "strategy_under_cap": strategy_under_cap,
    }


def build_corpus(directory: Path) -> dict[str, object]:
    return {
        "polars_version": pl.__version__,
        "shapes": [build_shape(directory, shape) for shape in _SHAPES],
    }


def _load_corpus() -> dict[str, object]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _recorded_entry(record: dict[str, object]) -> PolarsOperation | None:
    return _registry_entry(
        record["receiver"],  # type: ignore[arg-type]
        record["namespace"],  # type: ignore[arg-type]
        record["operation"],  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_corpus_pins_the_installed_polars_version() -> None:
    assert _load_corpus()["polars_version"] == pl.__version__


def test_corpus_shape_ids_match_the_module_shapes() -> None:
    corpus = _load_corpus()
    assert [record["id"] for record in corpus["shapes"]] == [shape[0] for shape in _SHAPES]


@pytest.mark.parametrize("shape", _SHAPES, ids=[shape[0] for shape in _SHAPES])
def test_every_shape_is_classified_exactly_as_the_corpus_records(
    tmp_path: Path,
    shape,
) -> None:
    recorded = {record["id"]: record for record in _load_corpus()["shapes"]}
    assert build_shape(tmp_path, shape) == recorded[shape[0]]


def test_every_corpus_operation_is_registered_for_its_receiver_or_an_explicit_decoy() -> None:
    for record in _load_corpus()["shapes"]:
        if record["operation"] is None:
            # A decoy carries no receiver or operation: its comment or string
            # literal must not name one.
            assert record["id"].startswith("decoy_")
            assert record["receiver"] is None
            continue
        entry = _recorded_entry(record)
        assert entry is not None, record["id"]
        assert record["class"] == entry.operation_class.value, record["id"]
        assert record["policy"] == entry.policy.value, record["id"]
        assert record["expansion"] == entry.expansion, record["id"]


def test_the_corpus_covers_every_operation_class_and_registered_namespace() -> None:
    covered_classes: set[OperationClass] = set()
    covered_namespaces: set[str | None] = set()
    for record in _load_corpus()["shapes"]:
        if record["operation"] is None:
            continue
        entry = _recorded_entry(record)
        assert entry is not None, record["id"]
        covered_classes.add(entry.operation_class)
        covered_namespaces.add(record["namespace"])

    assert covered_classes == set(OperationClass)
    assert covered_namespaces == {entry.namespace for entry in POLARS_OPERATIONS.values()}


def main() -> None:
    """Regenerate the corpus file; never invoked by pytest."""
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
        corpus = build_corpus(Path(directory))
    payload = json.dumps(corpus, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    CORPUS_PATH.write_bytes(payload.encode("utf-8"))


if __name__ == "__main__":
    main()
