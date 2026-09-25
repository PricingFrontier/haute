"""Whole-dataset banding statistics (RAT-B02).

The Banding editor used to count from preview rows with its own implementation
of the rules. These pin what the server answers instead: the distribution, the
values, and the per-rule counts of the whole data point the node reads — the
counts execution itself would produce — and what it says when the data is not
there to read.
"""

from __future__ import annotations

import math
import time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_TERMINAL = {"completed", "error", "cancelled", "superseded", "timed_out", "memory_limited"}


@pytest.fixture(autouse=True)
def _pinned_admission_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin modest budgets so these tests do not depend on the host's free RAM.

    Unpinned, a build reserves most of available RAM from a process-wide
    in-flight budget, so a second heavy operation in the same process — or less
    free RAM later in a long parallel run — turns every terminal status into
    `memory_limited`. These tests are about statistics, not memory policy.
    """
    monkeypatch.setenv("HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB", "1024")
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", "1024")


@pytest.fixture(autouse=True)
def _clean_jobs():
    from haute.routes.node_data import _node_data_service, _store

    _store.clear_all()
    yield
    for thread in list(_node_data_service._threads.values()):
        thread.join(60)
    _store.clear_all()


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    return haute_scratch


def _write_source(project: Path, frame: pl.DataFrame) -> Path:
    path = project / "quotes.parquet"
    frame.write_parquet(path)
    return path


def _graph(project: Path, factors: list[dict[str, Any]]) -> dict[str, Any]:
    """A Data Input feeding a Banding node, so the point is the input itself."""
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "Banding",
                        "nodeType": "banding",
                        "config": {"factors": factors},
                    },
                },
            ],
            "edges": [make_edge("source", "banding").model_dump()],
        }
    ).model_dump()


def _graph_with_transform(
    project: Path, factors: list[dict[str, Any]], *, code: str = "df = source"
) -> dict[str, Any]:
    """Banding behind a transform: its point is that node's built output."""
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "shaped",
                    "data": {
                        "label": "shaped",
                        "nodeType": "polars",
                        "config": {"code": code},
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "Banding",
                        "nodeType": "banding",
                        "config": {"factors": factors},
                    },
                },
            ],
            "edges": [
                make_edge("source", "shaped").model_dump(),
                make_edge("shaped", "banding").model_dump(),
            ],
        }
    ).model_dump()


def _build(client: TestClient, graph: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    """Cache the data the Banding node reads, and return its point."""
    body = {"graph": graph, "node_id": "banding", "source": "live", "refresh": refresh}
    run = client.post("/api/node-data/run", json=body)
    assert run.status_code == 200, run.text
    payload = run.json()
    if payload["status"] in {"started", "joined"}:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/node-data/status/{payload['job_id']}").json()
            if status["status"] in _TERMINAL:
                assert status["status"] == "completed", status
                break
            time.sleep(0.02)
        else:
            raise TimeoutError(payload["job_id"])
    point = client.post(
        "/api/node-data/point", json={"graph": graph, "node_id": "banding", "source": "live"}
    ).json()
    assert point["state"] == "current", point
    return point


def _factor(**updates: Any) -> dict[str, Any]:
    factor: dict[str, Any] = {
        "banding": "breakpoints",
        "column": "premium",
        "outputColumn": "band",
        "rules": [],
        "rightClosed": True,
    }
    factor.update(updates)
    return factor


def _stats(
    client: TestClient,
    graph: dict[str, Any],
    factor: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        "/api/banding/stats",
        json={
            "graph": graph,
            "node_id": "banding",
            "source": "live",
            "factor": factor,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stats_response(
    client: TestClient, graph: dict[str, Any], factor: dict[str, Any], **extra: Any
):
    return client.post(
        "/api/banding/stats",
        json={
            "graph": graph,
            "node_id": "banding",
            "source": "live",
            "factor": factor,
            **extra,
        },
    )


# --------------------------------------------------------------- numeric


def test_numeric_bins_are_equal_width_with_the_last_one_closed(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [0.0, 1.0, 2.0, 3.0, 4.0]}))
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor(), histogram_bins=2)

    assert stats["status"] == "ok"
    assert stats["total_rows"] == 5
    assert (stats["minimum"], stats["maximum"]) == (0.0, 4.0)
    # [0, 2) holds 0 and 1; [2, 4] is closed, so it holds 2, 3 and 4.
    assert [(b["lower"], b["upper"], b["count"]) for b in stats["bins"]] == [
        (0.0, 2.0, 2),
        (2.0, 4.0, 3),
    ]
    assert stats["null_count"] == 0
    assert stats["non_finite_count"] == 0


def test_a_constant_column_is_one_bin_rather_than_an_empty_range(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [7.0, 7.0, 7.0]}))
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor(), histogram_bins=8)

    assert [(b["lower"], b["upper"], b["count"]) for b in stats["bins"]] == [(7.0, 7.0, 3)]
    assert (stats["minimum"], stats["maximum"]) == (7.0, 7.0)


def test_values_no_bin_can_hold_are_counted_but_never_widen_the_extent(
    client: TestClient, project: Path
) -> None:
    _write_source(
        project,
        pl.DataFrame({"premium": [1.0, 2.0, None, math.nan, math.inf, -math.inf]}),
    )
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor(), histogram_bins=2)

    assert stats["total_rows"] == 6
    assert stats["null_count"] == 1
    assert stats["non_finite_count"] == 3
    assert (stats["minimum"], stats["maximum"]) == (1.0, 2.0)
    assert sum(b["count"] for b in stats["bins"]) == 2


def test_a_column_with_no_finite_value_has_no_bins_and_no_extent(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": pl.Series([None, math.nan], dtype=pl.Float64)}))
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor())

    assert stats["bins"] == []
    assert stats["minimum"] is None and stats["maximum"] is None
    assert (stats["null_count"], stats["non_finite_count"]) == (1, 1)


# ----------------------------------------------------------- categorical


def test_categorical_values_are_the_text_execution_matches_on(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0, 1.0, 2.0, None]}))
    factor = _factor(banding="categorical")
    graph = _graph(project, [factor])

    stats = _stats(client, graph, factor)

    # A Float64 1.0 is "1.0" — the value a categorical rule must be written
    # against — and never "1".
    assert [(v["value"], v["count"]) for v in stats["values"]] == [("1.0", 2), ("2.0", 1)]
    assert stats["distinct_count"] == 2
    assert stats["null_count"] == 1
    assert stats["other_count"] == 0


def test_categorical_values_are_ordered_by_count_then_value_and_capped(
    client: TestClient, project: Path
) -> None:
    _write_source(
        project,
        pl.DataFrame({"premium": ["b", "b", "a", "a", "c", "d"]}),
    )
    factor = _factor(banding="categorical")
    graph = _graph(project, [factor])

    stats = _stats(client, graph, factor, value_limit=2)

    # Ties go to the lower value, so "a" precedes "b" at the same count.
    assert [(v["value"], v["count"]) for v in stats["values"]] == [("a", 2), ("b", 2)]
    assert stats["distinct_count"] == 4
    # The rows outside the returned values are still accounted for.
    assert stats["other_count"] == 2


def test_nan_and_infinity_are_ordinary_categorical_values(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [math.nan, math.inf, 1.0, None]}))
    factor = _factor(banding="categorical")
    graph = _graph(project, [factor])

    stats = _stats(client, graph, factor)

    assert {v["value"] for v in stats["values"]} == {"NaN", "inf", "1.0"}
    assert stats["null_count"] == 1
    assert stats["distinct_count"] == 3


# ------------------------------------------------------------ rule counts


def test_rule_counts_follow_the_users_rules_and_agree_with_execution(
    client: TestClient, project: Path
) -> None:
    _write_source(
        project,
        pl.DataFrame({"premium": [1.0, 5.0, 10.0, 10.5, 20.0, None, math.nan, math.inf]}),
    )
    factor = _factor(
        banding="breakpoints",
        rules=[
            {"boundary": "10", "label": "low"},
            {"boundary": "", "label": "high"},
            {"boundary": "5", "label": "low"},
        ],
    )
    graph = _graph(project, [factor])

    stats = _stats(client, graph, factor)

    # The same vector RAT-B01 pins directly, now through the route: counts are
    # aligned to the user's rules, not to the sorted intervals.
    assert stats["rule_counts"] == [1, 2, 2]
    assert stats["unmatched_count"] == 3


def test_a_factor_with_no_rules_reports_no_counts_rather_than_zeroes(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0, 2.0]}))
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor())

    assert stats["rule_counts"] == []
    assert stats["unmatched_count"] is None


def test_a_rebuilt_snapshot_is_measured_again_rather_than_answered_from_the_memo(
    client: TestClient, project: Path
) -> None:
    """A refreshed generation is new data under an *unchanged* signature.

    The transform samples its rows, so a forced rebuild produces different data
    from identical inputs: the node, its code, its inputs and therefore its
    signature are all the same across the refresh, and only the generation
    differs. A memo keyed by anything but the generation would answer the
    second question with the first answer.
    """
    _write_source(project, pl.DataFrame({"premium": [float(value) for value in range(100)]}))
    graph = _graph_with_transform(
        project, [_factor()], code="df = source.collect().sample(fraction=0.5).lazy()"
    )
    first_point = _build(client, graph)
    first_signature = _signature(project, graph)

    first = _stats(client, graph, _factor())
    assert first["data_version"] == first_point["data_version"]

    second_point = _build(client, graph, refresh=True)
    # Same question, a second answer to it.
    assert _signature(project, graph) == first_signature
    assert second_point["data_version"] != first_point["data_version"]

    second = _stats(client, graph, _factor())

    assert second["data_version"] == second_point["data_version"]
    # The sample is a different half of the rows, so the statistics describe
    # the new generation rather than repeating the memoised ones.
    assert (second["minimum"], second["maximum"]) != (None, None)
    assert second["total_rows"] == 50


def _signature(project: Path, graph: dict[str, Any]) -> str:
    """The snapshot signature of the node the Banding factor reads."""
    from haute._data_points import DataPointResolver
    from haute._node_snapshots import NodeSnapshotStore
    from haute._types import PipelineGraph

    resolver = DataPointResolver(
        PipelineGraph.model_validate(graph), source="live", store=NodeSnapshotStore(project)
    )
    return str(resolver.node_output_signature("shaped"))


def test_a_point_that_has_moved_on_is_not_measured_at_all(
    client: TestClient, project: Path
) -> None:
    """Statistics are never computed from a generation the node has left."""
    _write_source(project, pl.DataFrame({"premium": [1.0, 2.0]}))
    graph = _graph_with_transform(project, [_factor()])
    _build(client, graph)
    assert _stats(client, graph, _factor())["status"] == "ok"

    # Editing the transform changes what the node reads, so its built
    # generation describes data this graph no longer produces.
    edited = _graph_with_transform(project, [_factor()])
    for node in edited["nodes"]:
        if node["id"] == "shaped":
            node["data"]["config"]["code"] = "df = source.head(1)"

    stats = _stats(client, edited, _factor())

    assert stats["status"] == "cache_required"
    assert stats["point"]["state"] == "stale"
    assert stats["total_rows"] == 0


def test_a_factor_over_its_memory_budget_is_reported_as_one(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission failures answer 507 with the execution payload, not a 500."""
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionProfile
    from haute.routes import _synchronous_analysis as analysis_mod

    _write_source(project, pl.DataFrame({"premium": [1.0]}))
    graph = _graph(project, [_factor()])

    def refuse_admission(**_kwargs: Any) -> Any:
        raise ExecutionAdmissionError(
            "banding_stats",
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            memory_limit_bytes=1,
            rss_at_admission_bytes=None,
            reason="in_flight_limit_reached",
        )

    monkeypatch.setattr(analysis_mod, "create_admitted_execution_context", refuse_admission)

    response = _stats_response(client, graph, _factor())

    assert response.status_code == 507
    assert response.json()["detail"]["error_code"] == "memory_limit"


def test_a_decimal_column_is_measured_rather_than_crashing(
    client: TestClient, project: Path
) -> None:
    """Decimal is a numeric dtype with no `is_finite` of its own."""
    _write_source(
        project,
        pl.DataFrame(
            {"premium": pl.Series([Decimal("1.50"), Decimal("2.50")], dtype=pl.Decimal(10, 2))}
        ),
    )
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor(), histogram_bins=2)

    assert stats["status"] == "ok"
    assert (stats["minimum"], stats["maximum"]) == (1.5, 2.5)
    assert [(b["lower"], b["upper"], b["count"]) for b in stats["bins"]] == [
        (1.5, 2.0, 1),
        (2.0, 2.5, 1),
    ]


def test_an_extent_too_narrow_to_split_is_binned_rather_than_refused(
    client: TestClient, project: Path
) -> None:
    """Ordinary arithmetic produces values one representable step apart.

    `100.0 * 1.1` is not `110.0`, but 40 equal-width edges between them are not
    40 distinct numbers — and a set of breaks with a repeat is one Polars
    refuses outright, which would fail the request rather than draw the data.
    """
    _write_source(project, pl.DataFrame({"premium": [110.0, 100.0 * 1.1]}))
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor(), histogram_bins=40)

    assert stats["status"] == "ok"
    assert 0 < len(stats["bins"]) < 40
    assert sum(b["count"] for b in stats["bins"]) == 2
    lowers = [b["lower"] for b in stats["bins"]]
    assert lowers == sorted(set(lowers))


def test_a_value_is_counted_in_the_bin_whose_edges_contain_it(
    client: TestClient, project: Path
) -> None:
    """Counts are measured against the edges the response publishes.

    Deriving a bin index by arithmetic is a second calculation that can round
    differently from the edge it should agree with: over 40 bins of [0, 1] it
    put 0.3 in the bin whose published lower edge is 0.30000000000000004.
    """
    _write_source(project, pl.DataFrame({"premium": [0.0, 0.3, 1.0]}))
    graph = _graph(project, [_factor()])

    stats = _stats(client, graph, _factor(), histogram_bins=40)

    for bin_ in stats["bins"]:
        if bin_["count"]:
            assert bin_["lower"] <= bin_["upper"]
    holding = [b for b in stats["bins"] if b["count"]]
    assert len(holding) == 3
    for value, bin_ in zip([0.0, 0.3, 1.0], holding, strict=True):
        assert bin_["lower"] <= value <= bin_["upper"], (value, bin_)


# ---------------------------------------------------------------- failures


def test_statistics_are_never_computed_from_data_that_is_not_there(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0]}))
    # A Banding node behind a transform reads that node's output, which has to
    # be built before there is anything to measure.
    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "shaped",
                    "data": {
                        "label": "shaped",
                        "nodeType": "polars",
                        "config": {"code": "df = source"},
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "Banding",
                        "nodeType": "banding",
                        "config": {"factors": [_factor()]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "shaped").model_dump(),
                make_edge("shaped", "banding").model_dump(),
            ],
        }
    ).model_dump()

    stats = _stats(client, graph, _factor())

    assert stats["status"] == "cache_required"
    assert stats["point"]["state"] == "missing"
    assert stats["total_rows"] == 0


def test_a_column_the_data_does_not_have_is_unprocessable(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0]}))
    graph = _graph(project, [_factor()])

    response = _stats_response(client, graph, _factor(column="missing"))

    assert response.status_code == 422
    assert "not in the data" in response.json()["detail"]


def test_a_numeric_mode_on_a_column_it_cannot_compare_is_unprocessable(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": ["a", "b"]}))
    graph = _graph(project, [_factor()])

    response = _stats_response(client, graph, _factor())

    assert response.status_code == 422
    assert "cannot compare" in response.json()["detail"]


def test_a_date_column_is_measured_in_days_since_1970(client: TestClient, project: Path) -> None:
    from datetime import date

    _write_source(
        project,
        pl.DataFrame({"premium": [date(2024, 1, 1), date(2024, 1, 6), date(2024, 1, 11), None]}),
    )
    factor = _factor(
        rules=[{"boundary": "2024-01-06", "label": "early"}, {"boundary": "", "label": "late"}]
    )
    graph = _graph(project, [factor])

    stats = _stats(client, graph, factor, histogram_bins=2)

    # 2024-01-01 is day 19723 since 1970-01-01.
    assert (stats["minimum"], stats["maximum"]) == (19723.0, 19733.0)
    assert [(b["lower"], b["upper"], b["count"]) for b in stats["bins"]] == [
        (19723.0, 19728.0, 1),
        (19728.0, 19733.0, 2),
    ]
    # The counts compare dates, and "up to 2024-01-06" includes that day.
    assert stats["rule_counts"] == [2, 1]
    assert stats["unmatched_count"] == 1


def test_rules_execution_would_refuse_are_refused_with_its_message(
    client: TestClient, project: Path
) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0]}))
    factor = _factor(rules=[{"boundary": "x", "label": "A"}])
    graph = _graph(project, [factor])

    response = _stats_response(client, graph, factor)

    assert response.status_code == 422
    assert "unreadable boundary" in response.json()["detail"]


@pytest.mark.parametrize(
    "factor",
    [
        _factor(banding="continuous", rules=[{"op1": "<=", "val1": "1", "assignment": "A"}]),
        {key: value for key, value in _factor().items() if key != "banding"},
    ],
    ids=["continuous", "missing"],
)
def test_a_factor_without_a_supported_banding_type_is_unprocessable(
    client: TestClient, project: Path, factor: dict[str, Any]
) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0]}))
    graph = _graph(project, [_factor()])

    response = _stats_response(client, graph, factor)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        f"Banding has unsupported banding type {factor.get('banding', '')!r}; "
        "expected one of: breakpoints, categorical"
    )


def test_invalid_consumer_wiring_is_a_400(client: TestClient, project: Path) -> None:
    _write_source(project, pl.DataFrame({"premium": [1.0]}))
    graph = _graph(project, [_factor()])
    second = make_edge("source", "banding").model_dump()
    second["id"] = "e_source_banding_second"
    graph["edges"].append(second)

    response = _stats_response(client, graph, _factor())

    assert response.status_code == 400
    assert "exactly one incoming connection" in response.json()["detail"]
