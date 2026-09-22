"""Optimiser setup, auto-range, and the input estimate seed from and capture into shared snapshots.

CACHE-S07. Setup runs through ``OptimiserSolveService._execute_pipeline`` under an admitted
context — exactly as the solve, auto-range, and estimate callers run it — with the run's seed
plan held on the caller's stack while the frames are read.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionProfile
from haute._sandbox import set_project_root
from haute._types import PipelineGraph
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import (
    OptimiserSolveService,
    _auto_range_required_columns_by_node,
    _cleanup_orphan_apply_result_artifact,
    _optimiser_solve_required_columns_by_node,
)
from haute.schemas import (
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserSolveRequest,
)
from tests.test_training_seeding import _MODELLING, _train

_QUOTES = 12
_SCENARIOS = 3
_SOLVER_COLUMNS = {"quote_id", "scenario_index", "scenario_value", "expected_income", "volume"}


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    set_project_root(haute_scratch)
    for profile in ("OPTIMISER_SETUP", "AUTO_RANGE", "TRAINING"):
        monkeypatch.setenv(f"HAUTE_{profile}_MEMORY_LIMIT_MB", "1024")
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    rows = [(quote, scenario) for quote in range(_QUOTES) for scenario in range(_SCENARIOS)]
    pl.DataFrame(
        {
            "quote_id": [f"q{quote}" for quote, _ in rows],
            "scenario_index": [scenario for _, scenario in rows],
            "scenario_value": [0.9 + 0.1 * scenario for _, scenario in rows],
            "expected_income": [100.0 + quote + 5.0 * scenario for quote, scenario in rows],
            "volume": [1.0 - 0.1 * scenario for _, scenario in rows],
            "y": [float((quote + scenario) % 3) for quote, scenario in rows],
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    pl.DataFrame(
        {
            "quote_id": [f"q{quote}" for quote in range(_QUOTES)],
            "territory": [("north", "south", "east")[quote % 3] for quote in range(_QUOTES)],
        }
    ).write_parquet(haute_scratch / "attrs.parquet")
    return haute_scratch


def _data_input(project: Path, name: str) -> dict[str, Any]:
    return {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(project / name)}


def _online(data_input: str) -> dict[str, Any]:
    return {
        "mode": "online",
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "data_input": data_input,
    }


def _ratebook(data_input: str, banding_source: str) -> dict[str, Any]:
    return {
        **_online(data_input),
        "mode": "ratebook",
        "factor_columns": [["territory"]],
        "banding_source": banding_source,
    }


def _graph(
    project: Path,
    nodes: list[tuple[str, str, dict[str, Any]]],
    edges: list[tuple[str, str] | dict[str, Any]],
) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": node_id, "data": {"label": node_id, "nodeType": kind, "config": config}}
            for node_id, kind, config in nodes
        ],
        "edges": [
            edge
            if isinstance(edge, dict)
            else {"id": f"e{index}", "source": edge[0], "target": edge[1]}
            for index, edge in enumerate(edges)
        ],
        "preamble": "import polars as pl",
        "source_file": str(project / "main.py"),
    }


def _online_chain(project: Path) -> dict[str, Any]:
    """``src → D → opt`` (online, reading ``D``)."""
    return _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            (
                "D",
                "polars",
                {"code": "df = src.with_columns(pl.col('volume') * 1.0).sort('quote_id')"},
            ),
            ("opt", "optimiser", _online("D")),
        ],
        [("src", "D"), ("D", "opt")],
    )


def _ratebook_graph(project: Path) -> dict[str, Any]:
    """``src → D`` is the data input; ``bands → B`` the banding side input."""
    return _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            (
                "D",
                "polars",
                {"code": "df = src.with_columns(pl.col('volume') * 1.0).sort('quote_id')"},
            ),
            ("bands", "dataInput", _data_input(project, "attrs.parquet")),
            (
                "B",
                "polars",
                {"code": "df = bands.select('quote_id', 'territory').sort('quote_id')"},
            ),
            ("opt", "optimiser", _ratebook("D", "B")),
        ],
        [("src", "D"), ("D", "opt"), ("bands", "B"), ("B", "opt")],
    )


@dataclass
class _Setup:
    frames: dict[str, pl.DataFrame]
    metrics: dict[str, Any]
    calls: Counter[str]
    factor_rows: int | None = None

    @property
    def seeds(self) -> set[str]:
        return {seed["node_id"] for seed in self.metrics["shared_snapshot_seeds"]}

    @property
    def captures(self) -> list[tuple[str, str]]:
        return [
            (capture["node_id"], capture["outcome"])
            for capture in self.metrics["shared_snapshot_captures"]
        ]


def _counting_builds(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    import haute.executor as executor

    calls: Counter[str] = Counter()
    build = executor._build_node_fn

    def counting(node: Any, **kwargs: Any) -> Any:
        name, fn, is_source = build(node, **kwargs)

        def counted(*args: Any, **fn_kwargs: Any) -> Any:
            calls[node.id] += 1
            return fn(*args, **fn_kwargs)

        return name, counted, is_source

    monkeypatch.setattr(executor, "_build_node_fn", counting)
    return calls


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    graph: dict[str, Any],
    *,
    read: tuple[str, ...],
    profile: ExecutionProfile = ExecutionProfile.OPTIMISER_SETUP,
    extract_factors: bool = False,
) -> _Setup:
    """Run one optimiser setup as its callers do and read *read* while the plan is held."""
    calls = _counting_builds(monkeypatch)
    pipeline = PipelineGraph.model_validate(graph)
    config = pipeline.node_map["opt"].data.config
    mode = str(config["mode"])
    body: OptimiserSolveRequest | OptimiserFrontierAutoRangeRequest
    if profile == ExecutionProfile.AUTO_RANGE:
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="opt")
        required = _auto_range_required_columns_by_node(pipeline, "opt", config, mode=mode)
    else:
        body = OptimiserSolveRequest(graph=graph, node_id="opt")
        required = _optimiser_solve_required_columns_by_node(pipeline, "opt", config)
    store = JobStore()
    service = OptimiserSolveService(store)
    context = create_admitted_execution_context(operation="optimiser_setup_test", profile=profile)
    factor_rows: int | None = None
    try:
        with contextlib.ExitStack() as resources:
            outputs = service._execute_pipeline(
                body,
                store.create_job({"status": "running"}),
                resources,
                required_columns_by_node=required,
                execution_context=context,
            )
            frames = {node_id: outputs[node_id].collect() for node_id in read}
            if extract_factors:
                handle = service._extract_factors(
                    outputs, pipeline, "opt", config, mode, execution_context=context
                )
                factor_rows = int(handle["row_count"])
                _cleanup_orphan_apply_result_artifact(handle, job_id="<test>", event="test")
        metrics = context.metrics_payload()
    finally:
        context.release_admission()
    # A later run wraps the builder again, so this run keeps a copy of its counts.
    return _Setup(frames, metrics, Counter(calls), factor_rows)


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


def test_optimiser_setup_seeds_training_join(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Snapshots are kept per source; the optimiser runs on the batch source, so
    # a training run on the same upstream is a batch run.
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("attrs", "dataInput", _data_input(project, "attrs.parquet")),
            ("J", "polars", {"code": "df = src.join(attrs, on='quote_id', validate='m:1')"}),
            ("train", "modelling", _MODELLING),
            ("opt", "optimiser", _online("J")),
        ],
        [("src", "J"), ("attrs", "J"), ("J", "train"), ("J", "opt")],
    )
    training = _train(monkeypatch, graph, source="batch")
    assert training.captures == {"J": "published"}, training.job.get("message")

    setup = _setup(monkeypatch, graph, read=("J",))

    assert setup.seeds == {"J"}
    assert setup.captures == []
    assert sum(setup.calls.values()) == 0
    joined = pl.read_parquet(project / "quotes.parquet").join(
        pl.read_parquet(project / "attrs.parquet"), on="quote_id"
    )
    assert_frame_equal(
        setup.frames["J"].sort("quote_id", "scenario_index"),
        joined.select(setup.frames["J"].columns).sort("quote_id", "scenario_index"),
    )


def test_ratebook_setup_captures_data_once_and_keeps_side_input(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _ratebook_graph(project)

    cold = _setup(monkeypatch, graph, read=("D", "B"))
    warm = _setup(monkeypatch, graph, read=("D", "B"))

    # Cold: the data producer is captured once and the side input the caller
    # reads is built and captured; the two-input Optimiser is not a join and
    # is neither checkpointed nor captured.
    assert sorted(cold.captures) == [("B", "published"), ("D", "published")]
    assert cold.calls == Counter({"src": 1, "D": 1, "bands": 1, "B": 1})
    # Warm: both are seeded and nothing executes.
    assert warm.seeds == {"D", "B"}
    assert warm.captures == []
    assert sum(warm.calls.values()) == 0
    for node_id in ("D", "B"):
        assert_frame_equal(warm.frames[node_id], cold.frames[node_id], check_row_order=False)


def test_ratebook_factors_from_separate_api_input(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        {"quote_id": f"q{quote}", "territory": ("north", "south", "east")[quote % 3]}
        for quote in range(_QUOTES)
    ]
    (project / "factors.json").write_text(json.dumps(records), encoding="utf-8")
    api_config = {
        "path": str(project / "factors.json"),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "rating_factors",
                "emit": True,
                "columns": [
                    {"name": "quote_id", "path": "$[:].quote_id", "type": "str", "selected": True},
                    {
                        "name": "territory",
                        "path": "$[:].territory",
                        "type": "str",
                        "selected": True,
                    },
                ],
            }
        ],
    }
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            (
                "D",
                "polars",
                {"code": "df = src.with_columns(pl.col('volume') * 1.0).sort('quote_id')"},
            ),
            ("api", "apiInput", api_config),
            ("opt", "optimiser", _ratebook("D", "rating_factors")),
        ],
        [
            ("src", "D"),
            ("D", "opt"),
            {"id": "e_api", "source": "api", "sourceHandle": "rating_factors", "target": "opt"},
        ],
    )

    cold = _setup(monkeypatch, graph, read=(), extract_factors=True)
    warm = _setup(monkeypatch, graph, read=(), extract_factors=True)

    # The API input is consumed and built on every run but never captured:
    # its tables have their own store.
    assert cold.captures == [("D", "published")]
    assert cold.calls["api"] == 1
    assert warm.seeds == {"D"}
    assert warm.captures == []
    assert warm.calls == Counter({"api": 1})
    assert cold.factor_rows == warm.factor_rows == _QUOTES


def test_auto_range_capture_serves_the_solve(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _online_chain(project)
    pipeline = PipelineGraph.model_validate(graph)
    auto_range_demand = _auto_range_required_columns_by_node(
        pipeline, "opt", pipeline.node_map["opt"].data.config, mode="online"
    )
    # Auto-range itself needs fewer columns than the solve reads.
    assert set(auto_range_demand["D"]) < _SOLVER_COLUMNS

    auto_range = _setup(monkeypatch, graph, read=("D",), profile=ExecutionProfile.AUTO_RANGE)
    solve = _setup(monkeypatch, graph, read=("D",))

    # It captures the solve's columns, so the solve seeds instead of recomputing.
    assert auto_range.captures == [("D", "published")]
    assert solve.seeds == {"D"}
    assert sum(solve.calls.values()) == 0
    assert _SOLVER_COLUMNS <= set(solve.frames["D"].columns)


def test_streaming_auto_range_capture_serves_the_solve(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Streaming auto-range captures its pre-expansion base with the solve's columns."""
    from fastapi.testclient import TestClient

    from haute.server import app

    pl.DataFrame(
        {
            "quote_id": [f"q{quote}" for quote in range(_QUOTES)],
            "volume": [1.0] * _QUOTES,
            "expected_income": [100.0 + quote for quote in range(_QUOTES)],
        }
    ).write_parquet(project / "per_quote.parquet")
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "per_quote.parquet")),
            (
                "base",
                "polars",
                {"code": "df = src.with_columns(pl.col('volume') * 1.0).sort('quote_id')"},
            ),
            (
                "scenario",
                "scenarioExpander",
                {
                    "quote_id": "quote_id",
                    "column_name": "scenario_value",
                    "min_value": 0.9,
                    "max_value": 1.1,
                    "stepCount": _SCENARIOS,
                    "step_column": "scenario_index",
                },
            ),
            ("opt", "optimiser", {**_online("scenario"), "chunk_size": 2}),
        ],
        [("src", "base"), ("base", "scenario"), ("scenario", "opt")],
    )
    client = TestClient(app, raise_server_exceptions=False)

    started = client.post(
        "/api/optimiser/frontier/auto-range/start", json={"graph": graph, "node_id": "opt"}
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    assert _poll(client, "frontier/auto-range", job_id) == "completed"
    status = client.get(f"/api/optimiser/frontier/auto-range/status/{job_id}").json()
    solve = _setup(monkeypatch, graph, read=("scenario",))

    # The streaming path ran from the base below the expander and captured it
    # with what the solve reads there, not just what auto-range reads.
    assert [
        (capture["node_id"], capture["outcome"])
        for capture in status["execution_metrics"]["shared_snapshot_captures"]
    ] == [("base", "published")]
    assert solve.seeds == {"base"}
    assert solve.calls["src"] == solve.calls["base"] == 0
    assert _SOLVER_COLUMNS <= set(solve.frames["scenario"].columns)


def test_estimate_seeds_setup_capture(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from haute.routes.optimiser import _optimiser_input_metrics

    graph = _online_chain(project)
    setup = _setup(monkeypatch, graph, read=("D",))
    assert setup.captures == [("D", "published")]
    calls = _counting_builds(monkeypatch)

    metrics = _optimiser_input_metrics(OptimiserEstimateRequest(graph=graph, node_id="opt"))

    assert sum(calls.values()) == 0
    assert metrics["quote_count"] == _QUOTES
    assert metrics["expanded_row_count"] == _QUOTES * _SCENARIOS


def test_optimiser_leaves_no_checkpoint_directory(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Solve, auto-range, and the estimate write no checkpoint directory or cache entry."""
    import tempfile

    from fastapi.testclient import TestClient

    from haute._dataframe_execution_cache import DataFrameExecutionCache
    from haute.server import app

    created: list[str] = []
    mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args: Any, **kwargs: Any) -> str:
        path = mkdtemp(*args, **kwargs)
        created.append(Path(path).name)
        return path

    stored: list[Any] = []
    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(
        DataFrameExecutionCache,
        "store_artifact",
        lambda self, *args, **kwargs: stored.append(args),
    )
    client = TestClient(app, raise_server_exceptions=False)
    graph = _online_chain(project)

    estimate = client.post("/api/optimiser/estimate", json={"graph": graph, "node_id": "opt"})
    assert estimate.status_code == 200, estimate.text
    auto_range = client.post(
        "/api/optimiser/frontier/auto-range/start", json={"graph": graph, "node_id": "opt"}
    )
    assert auto_range.status_code == 200, auto_range.text
    assert _poll(client, "frontier/auto-range", auto_range.json()["job_id"]) == "completed"
    solve = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert solve.status_code == 200, solve.text
    assert _poll(client, "solve", solve.json()["job_id"]) == "completed"

    checkpoint_prefixes = ("haute_opt_", "haute_frontier_range_", "haute_opt_estimate_")
    assert [
        name
        for name in created
        if name.startswith(checkpoint_prefixes)
        and not name.startswith("haute_frontier_range_parts_")
    ] == []
    assert stored == []


def _poll(client: Any, route: str, job_id: str) -> str:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = client.get(f"/api/optimiser/{route}/status/{job_id}").json()
        if status["status"] != "running":
            return str(status["status"])
        time.sleep(0.05)
    raise TimeoutError(f"optimiser {route} job {job_id} did not finish")
