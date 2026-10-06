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
from haute.routes._optimiser_artifacts import _cleanup_orphan_apply_result_artifact
from haute.routes._optimiser_input import _optimiser_solve_required_columns_by_node
from haute.routes._optimiser_service import OptimiserSolveService
from haute.schemas import (
    OptimiserEstimateRequest,
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
    for profile in ("OPTIMISER_SETUP", "OPTIMISER_SOLVE", "TRAINING"):
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
    required_columns_by_node: dict[str, frozenset[str]] | None = None,
) -> _Setup:
    """Run one optimiser setup as its callers do and read *read* while the plan is held.

    *required_columns_by_node* replaces the solve's demand, to publish a
    capture another caller would have made.
    """
    calls = _counting_builds(monkeypatch)
    pipeline = PipelineGraph.model_validate(graph)
    config = pipeline.node_map["opt"].data.config
    mode = str(config["mode"])
    body = OptimiserSolveRequest(graph=graph, node_id="opt")
    required = (
        _optimiser_solve_required_columns_by_node(pipeline, "opt", config)
        if required_columns_by_node is None
        else required_columns_by_node
    )
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


_J_FEATURES = [
    "quote_id",
    "scenario_index",
    "scenario_value",
    "expected_income",
    "volume",
    "territory",
]


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
            # Features are opt-in. Training on every column of J besides the
            # target makes its capture of J cover what the setup reads.
            ("train", "modelling", {**_MODELLING, "feature_columns": _J_FEATURES}),
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


# ---------------------------------------------------------------------------
# Auto-range runs the solve setup's pipeline stage
# ---------------------------------------------------------------------------


def _analysis_side_input_graph(project: Path) -> dict[str, Any]:
    """``src → D`` is the data input; ``attrs`` a separate analysis input."""
    return _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            (
                "D",
                "polars",
                {"code": "df = src.with_columns(pl.col('volume') * 1.0).sort('quote_id')"},
            ),
            ("attrs", "dataInput", _data_input(project, "attrs.parquet")),
            (
                "opt",
                "optimiser",
                {**_online("D"), "analysis_input": "attrs", "analysis_columns": ["territory"]},
            ),
        ],
        [("src", "D"), ("D", "opt"), ("attrs", "opt")],
    )


def _api_column(name: str, kind: str) -> dict[str, Any]:
    return {"name": name, "path": f"$[:].{name}", "type": kind, "selected": True}


def parallel_edge_graph(root: Path, quotes: pl.DataFrame) -> dict[str, Any]:
    """One API source feeds the optimiser through two edges, one per emitted table.

    ``quotes`` is the data input; ``keys`` is a second frame of the same source.
    """
    (root / "quotes.json").write_text(json.dumps(quotes.to_dicts()), encoding="utf-8")
    api = {
        "path": str(root / "quotes.json"),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [
                    _api_column("quote_id", "str"),
                    _api_column("scenario_index", "int"),
                    _api_column("scenario_value", "float"),
                    _api_column("expected_income", "float"),
                    _api_column("volume", "float"),
                ],
            },
            {
                "path": "$[:]",
                "label": "keys",
                "emit": True,
                "columns": [_api_column("quote_id", "str")],
            },
        ],
    }
    return _graph(
        root,
        [("api", "apiInput", api), ("opt", "optimiser", _online("quotes"))],
        [
            {"id": "e_quotes", "source": "api", "sourceHandle": "quotes", "target": "opt"},
            {"id": "e_keys", "source": "api", "sourceHandle": "keys", "target": "opt"},
        ],
    )


def _parallel_edge_graph(project: Path) -> dict[str, Any]:
    return parallel_edge_graph(project, pl.read_parquet(project / "quotes.parquet"))


def _plain_graph(project: Path) -> dict[str, Any]:
    """``src → opt``: the parallel-edge source's data table, read directly."""
    return _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("opt", "optimiser", _online("src")),
        ],
        [("src", "opt")],
    )


def _client() -> Any:
    from fastapi.testclient import TestClient

    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


def _completed(client: Any, route: str, start: str, graph: dict[str, Any]) -> dict[str, Any]:
    started = client.post(f"/api/optimiser/{start}", json={"graph": graph, "node_id": "opt"})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    _poll(client, route, job_id)
    status: dict[str, Any] = client.get(f"/api/optimiser/{route}/status/{job_id}").json()
    assert status["status"] == "completed", status.get("message")
    return status


def _auto_range_job(client: Any, graph: dict[str, Any]) -> dict[str, Any]:
    return _completed(client, "frontier/auto-range", "frontier/auto-range/start", graph)


def _solve_job(client: Any, graph: dict[str, Any]) -> dict[str, Any]:
    return _completed(client, "solve", "solve", graph)


def _job_captures(status: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (capture["node_id"], capture["outcome"])
        for capture in status["execution_metrics"]["shared_snapshot_captures"]
    ]


def _job_seeds(status: dict[str, Any]) -> set[str]:
    return {seed["node_id"] for seed in status["execution_metrics"]["shared_snapshot_seeds"]}


def _recording_seed_plans(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every seed-plan request optimiser setup opens, then open it."""
    from haute.routes import _optimiser_service

    requests: list[Any] = []
    real = _optimiser_service.open_seed_plan

    def recording(request: Any, **kwargs: Any) -> Any:
        requests.append(request)
        return real(request, **kwargs)

    monkeypatch.setattr(_optimiser_service, "open_seed_plan", recording)
    return requests


_STAGE_GRAPHS = {
    "online": (_online_chain, "D"),
    # Ratebook setup also reads the banding input, so it executes the optimiser.
    "ratebook": (_ratebook_graph, "opt"),
    # A separate analysis input lies outside the data input's lineage.
    "analysis_side_input": (_analysis_side_input_graph, "opt"),
    # Demand on one frame of a multi-frame source is keyed on the optimiser.
    "parallel_edges": (_parallel_edge_graph, "opt"),
}


@pytest.mark.parametrize("shape", list(_STAGE_GRAPHS))
def test_auto_range_and_solve_setup_open_the_same_seed_plan(
    project: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    build, target = _STAGE_GRAPHS[shape]
    graph = build(project)
    requests = _recording_seed_plans(monkeypatch)
    client = _client()

    _auto_range_job(client, graph)
    _solve_job(client, graph)

    auto_range_request, solve_request = requests
    assert auto_range_request == solve_request
    assert solve_request.target_node_id == target
    assert solve_request.profile is ExecutionProfile.OPTIMISER_SOLVE


def test_a_parallel_edge_source_runs_auto_range_and_solve_on_its_data_table(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    plain_ranges = _auto_range_job(client, _plain_graph(project))["result"]["ranges"]
    plain_solve = _solve_job(client, _plain_graph(project))["result"]

    graph = _parallel_edge_graph(project)
    ranges = _auto_range_job(client, graph)["result"]["ranges"]
    solve = _solve_job(client, graph)["result"]

    assert ranges == plain_ranges
    assert solve["total_objective"] == pytest.approx(plain_solve["total_objective"])
    assert solve["constraints"] == pytest.approx(plain_solve["constraints"])


def scored_graph(
    project: Path, monkeypatch: pytest.MonkeyPatch, *, data: str = "quotes.parquet"
) -> tuple[dict[str, Any], list[int]]:
    """``src → M → opt``, where ``M`` is a Model Score node; returns its predict calls.

    *data* names the scored quotes under *project*.
    """
    from haute import _mlflow_io
    from haute.modelling._feature_contract import build_contract, save_contract

    predicted: list[int] = []

    class Uplift:
        def predict(self, features: Any) -> Any:
            import numpy as np

            predicted.append(len(features))
            return np.asarray(features["expected_income"], dtype="float64") * 1.1

    monkeypatch.setattr(
        _mlflow_io,
        "load_mlflow_model",
        lambda *_args, **_kwargs: _mlflow_io.ScoringModel(
            Uplift(), ["expected_income"], flavor="pyfunc"
        ),
    )
    contract_path = project / "feature_contract.json"
    save_contract(
        build_contract(
            features=["expected_income"],
            feature_types={"expected_income": "Float64"},
            categorical_features=[],
            target_name="target",
            target_type="Float64",
            task="regression",
        ),
        contract_path,
    )
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, data)),
            (
                "M",
                "modelScore",
                {
                    "sourceType": "run",
                    "run_id": "run-1",
                    "artifact_path": "model.pyfunc",
                    "task": "regression",
                    "output_column": "score",
                    "feature_contract_path": str(contract_path),
                },
            ),
            ("opt", "optimiser", {**_online("M"), "objective": "score"}),
        ],
        [("src", "M"), ("M", "opt")],
    )
    return graph, predicted


def test_auto_range_capture_of_a_scored_frame_serves_the_solve(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, predicted = scored_graph(project, monkeypatch)
    client = _client()

    auto_range = _auto_range_job(client, graph)
    assert predicted, "the cold auto-range scores the frame"
    assert ("M", "published") in _job_captures(auto_range)
    scored = len(predicted)
    calls = _counting_builds(monkeypatch)
    _solve_job(client, graph)

    assert len(predicted) == scored
    assert calls["src"] == calls["M"] == 0


def test_solve_capture_of_a_scored_frame_serves_auto_range(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, predicted = scored_graph(project, monkeypatch)
    client = _client()

    _solve_job(client, graph)
    assert predicted, "the cold solve scores the frame"
    scored = len(predicted)
    calls = _counting_builds(monkeypatch)
    auto_range = _auto_range_job(client, graph)

    assert len(predicted) == scored
    assert calls["src"] == calls["M"] == 0
    assert "M" in _job_seeds(auto_range)
    assert _job_captures(auto_range) == []


def test_auto_range_widens_a_narrow_generation_the_solve_then_seeds(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, predicted = scored_graph(project, monkeypatch)
    # A fresh generation of the scored frame without the solve's scenario columns,
    # as a capture of only what the ranges read would publish.
    narrow = _setup(
        monkeypatch,
        graph,
        read=("M",),
        required_columns_by_node={"M": frozenset({"quote_id", "score", "volume"})},
    )
    assert narrow.captures == [("M", "published")]
    assert "scenario_index" not in narrow.frames["M"].columns
    scored_narrow = len(predicted)
    client = _client()

    auto_range = _auto_range_job(client, graph)

    # The narrow generation cannot serve the solve's demand: auto-range rebuilds
    # the scored frame and publishes it with the solve's columns.
    assert len(predicted) > scored_narrow
    assert ("M", "published") in _job_captures(auto_range)
    widened = len(predicted)
    calls = _counting_builds(monkeypatch)
    _solve_job(client, graph)

    assert len(predicted) == widened
    assert calls["src"] == calls["M"] == 0


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
    """Solve, auto-range, and the estimate write no checkpoint directory."""
    import tempfile

    from fastapi.testclient import TestClient

    from haute.server import app

    created: list[str] = []
    mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args: Any, **kwargs: Any) -> str:
        path = mkdtemp(*args, **kwargs)
        created.append(Path(path).name)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)
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


def _poll(client: Any, route: str, job_id: str) -> str:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = client.get(f"/api/optimiser/{route}/status/{job_id}").json()
        if status["status"] != "running":
            return str(status["status"])
        time.sleep(0.05)
    raise TimeoutError(f"optimiser {route} job {job_id} did not finish")
