"""Row-scoped trace lookups probe by join key before matching every carried value."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import polars as pl
import pytest

import haute._polars_utils as polars_utils
import haute._trace_correlation as trace_correlation
from haute._trace_correlation import RowScopeResolver, TraceEdgeAlignment
from haute._types import GraphNode, NodeData, NodeType


def _resolver(
    plan: pl.LazyFrame,
    *,
    join_config: dict[str, Any] | None,
    off_lineage_join_config: dict[str, Any] | None = None,
) -> RowScopeResolver:
    """A resolver over ``rows``; ``join_config`` is an Edge Join on the traced
    lineage (it reads ``rows``), ``off_lineage_join_config`` one elsewhere."""
    node_map = {
        "rows": GraphNode(
            id="rows",
            data=NodeData(label="rows", nodeType=NodeType.POLARS, config={"code": "df = df"}),
        )
    }
    alignments: dict[Any, TraceEdgeAlignment] = {}
    if join_config is not None:
        node_map["joined"] = GraphNode(
            id="joined",
            data=NodeData(label="joined", nodeType=NodeType.EDGE_JOIN, config=join_config),
        )
        alignments[("rows", "joined", None, "base")] = TraceEdgeAlignment(False)
    if off_lineage_join_config is not None:
        node_map["elsewhere"] = GraphNode(
            id="elsewhere",
            data=NodeData(
                label="elsewhere", nodeType=NodeType.EDGE_JOIN, config=off_lineage_join_config
            ),
        )
    return RowScopeResolver(
        node_map=node_map,
        prefixes={},
        alignments=alignments,
        edge_metadata={},
        input_names={},
        child_input_names={},
        plans=lambda: {"rows": plan},
        frames={},
        head_resolved=set(),
    )


@pytest.fixture
def collected_plans(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The unoptimised plan of every query a lookup collects, in order."""
    plans: list[str] = []
    real_collect = polars_utils.streaming_collect

    def recording_collect(lf: pl.LazyFrame, **kwargs: Any) -> pl.DataFrame:
        plans.append(lf.explain(optimized=False))
        return real_collect(lf, **kwargs)

    monkeypatch.setattr(polars_utils, "streaming_collect", recording_collect)
    yield plans


_ROWS = pl.LazyFrame(
    {
        "quote_id": ["q1", "q2", "q2", "q3"],
        "premium": [1.0, 2.0, 5.0, 3.0],
        "region": ["a", "b", "b", "c"],
    }
)


def test_lookup_probes_by_join_key_then_matches_every_carried_value_in_memory(
    collected_plans: list[str],
) -> None:
    resolver = _resolver(_ROWS, join_config={"how": "left", "on": ["quote_id"]})

    found = resolver.lookup("rows", None, {"quote_id": "q2", "premium": 5.0, "region": "b"})

    assert found is not None
    assert found.to_dicts() == [{"quote_id": "q2", "premium": 5.0, "region": "b"}]
    assert len(collected_plans) == 1
    assert 'col("quote_id")' in collected_plans[0]
    assert 'col("premium")' not in collected_plans[0]
    assert 'col("region")' not in collected_plans[0]
    # A null-filled comparison defeats Parquet row-group statistics pruning.
    assert "fill_null" not in collected_plans[0]


@pytest.mark.parametrize(
    ("key", "value"),
    [pytest.param("policy", "p3", id="base-role"), pytest.param("quote_id", "q3", id="join-role")],
)
def test_lookup_probe_uses_either_role_of_a_left_on_right_on_join(
    collected_plans: list[str],
    key: str,
    value: str,
) -> None:
    rows = _ROWS.with_columns(policy=pl.Series(["p1", "p2", "p2", "p3"]))
    resolver = _resolver(
        rows,
        join_config={"how": "left", "leftOn": ["policy"], "rightOn": ["quote_id"]},
    )

    found = resolver.lookup("rows", None, {key: value, "premium": 3.0})

    assert found is not None
    assert found.to_dicts() == [{"quote_id": "q3", "premium": 3.0, "region": "c", "policy": "p3"}]
    assert len(collected_plans) == 1
    assert f'col("{key}")' in collected_plans[0]
    assert 'col("premium")' not in collected_plans[0]


def test_lookup_ignores_edge_joins_outside_the_traced_lineage(
    collected_plans: list[str],
) -> None:
    """An unfinished join elsewhere in the graph neither breaks the trace nor
    lends its keys to the probe."""
    resolver = _resolver(
        _ROWS,
        join_config=None,
        off_lineage_join_config={"how": "left"},
    )

    found = resolver.lookup("rows", None, {"quote_id": "q3", "premium": 3.0})

    assert found is not None and found.height == 1
    assert len(collected_plans) == 1
    assert 'col("premium")' in collected_plans[0]


def test_lookup_keeps_two_rows_sharing_every_carried_value(
    collected_plans: list[str],
) -> None:
    rows = pl.LazyFrame({"quote_id": ["q1", "q1", "q1"], "region": ["a", "a", "a"]})
    resolver = _resolver(rows, join_config={"how": "inner", "on": "quote_id"})

    found = resolver.lookup("rows", None, {"quote_id": "q1", "region": "a"})

    assert found is not None and found.height == 2
    assert len(collected_plans) == 1


def test_lookup_finds_no_row_when_a_carried_non_key_value_differs(
    collected_plans: list[str],
) -> None:
    resolver = _resolver(_ROWS, join_config={"how": "left", "on": ["quote_id"]})

    found = resolver.lookup("rows", None, {"quote_id": "q2", "premium": 99.0})

    assert found is not None and found.height == 0
    assert len(collected_plans) == 1


def test_lookup_falls_back_to_the_full_filter_when_the_probe_reaches_its_limit(
    collected_plans: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(trace_correlation, "_ROW_SCOPE_PROBE_LIMIT", 1)
    resolver = _resolver(_ROWS, join_config={"how": "left", "on": ["quote_id"]})

    found = resolver.lookup("rows", None, {"quote_id": "q2", "premium": 2.0})

    assert found is not None
    assert found.to_dicts() == [{"quote_id": "q2", "premium": 2.0, "region": "b"}]
    assert len(collected_plans) == 2
    assert 'col("premium")' not in collected_plans[0]
    assert 'col("premium")' in collected_plans[1]


@pytest.mark.parametrize(
    ("join_config", "values"),
    [
        pytest.param(None, {"quote_id": "q2", "premium": 2.0}, id="no-edge-join"),
        pytest.param(
            {"how": "left", "on": ["policy"]},
            {"quote_id": "q2", "premium": 2.0},
            id="key-not-carried",
        ),
        pytest.param(
            {"how": "left", "on": ["quote_id"]},
            {"quote_id": None, "premium": 2.0},
            id="null-key",
        ),
        pytest.param({"how": "cross"}, {"quote_id": "q2", "premium": 2.0}, id="cross-join"),
    ],
)
def test_lookup_without_a_probe_column_filters_on_every_carried_value(
    collected_plans: list[str],
    join_config: dict[str, Any] | None,
    values: dict[str, Any],
) -> None:
    resolver = _resolver(_ROWS, join_config=join_config)

    found = resolver.lookup("rows", None, values)

    assert found is not None
    assert found.height == (1 if values["quote_id"] == "q2" else 0)
    assert len(collected_plans) == 1
    assert 'col("premium")' in collected_plans[0]


@pytest.mark.parametrize("probe_limit", [1, 2, 1_000])
def test_lookup_returns_the_rows_the_full_filter_returns(
    monkeypatch: pytest.MonkeyPatch,
    probe_limit: int,
) -> None:
    monkeypatch.setattr(trace_correlation, "_ROW_SCOPE_PROBE_LIMIT", probe_limit)
    rows = pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", None],
            "premium": [1.0, 1.0, 2.0, 3.0, None, 4.0],
            "region": ["a", "a", "a", "b", "b", None],
        }
    )
    candidates = [
        *rows.to_dicts(),
        {"quote_id": "q1", "premium": 9.0, "region": "a"},
        {"quote_id": "q9", "premium": 1.0, "region": "a"},
        {"quote_id": "q1", "region": "a"},
    ]
    for values in candidates:
        resolver = _resolver(rows.lazy(), join_config={"how": "left", "on": ["quote_id"]})
        found = resolver.lookup("rows", None, values)
        expected = rows.filter(
            pl.all_horizontal(
                (pl.col(name).is_null() if value is None else pl.col(name) == value).fill_null(
                    False
                )
                for name, value in values.items()
            )
        ).head(2)
        assert found is not None
        assert sorted(map(str, found.to_dicts())) == sorted(map(str, expected.to_dicts())), values


def test_repeated_lookups_read_a_plan_schema_once(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _ROWS.with_columns(pl.col("premium") * 1)
    schema_reads = 0
    real_collect_schema = pl.LazyFrame.collect_schema

    def counting_collect_schema(self: pl.LazyFrame) -> pl.Schema:
        nonlocal schema_reads
        if self is plan:
            schema_reads += 1
        return real_collect_schema(self)

    monkeypatch.setattr(pl.LazyFrame, "collect_schema", counting_collect_schema)
    resolver = _resolver(plan, join_config={"how": "left", "on": ["quote_id"]})

    first = resolver.lookup("rows", None, {"quote_id": "q1"})
    second = resolver.lookup("rows", None, {"quote_id": "q3", "region": "c"})

    assert first is not None and first.height == 1
    assert second is not None and second.height == 1
    assert schema_reads == 1
