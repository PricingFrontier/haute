"""OPT-V09A: the analysis-column side table, its ownership, and the scenario grid.

Everything here drives the real input preparation and the real price-contour
solver: a pipeline whose source carries a non-solver ``region`` column, or a
separate connected frame that supplies it, solved through the routes. The
side table ``quote_analysis.parquet`` must hold one row per solved quote while
the solver's own inputs stay exactly the solver columns.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._types import PipelineGraph
from haute.routes import _optimiser_artifacts, _optimiser_input, _optimiser_service
from haute.routes._optimiser_input import (
    OptimiserSetupError,
    _setup_execution_target_node_id,
    _solve_columns_by_node,
    resolve_analysis_frame,
    resolve_analysis_plan,
)
from haute.routes._optimiser_outcomes import (
    ANALYSIS_ROW_PRESENT_COLUMN,
    AnalysisColumnNotConstantError,
    collect_quote_analysis,
    require_one_row_per_solved_quote,
    write_quote_analysis,
)
from haute.routes.optimiser import _store
from tests.conftest import make_edge, make_graph, make_ready_file_input_config
from tests.optimiser_fixtures import run_frontier_and_wait

_TERMINAL = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}
_REGIONS = ("North", "South", "Île")
_GRID = np.linspace(0.8, 1.2, 3).astype(np.float32)


def _region(quote: int) -> str:
    return _REGIONS[quote % len(_REGIONS)]


def _scored(root: Path, *, n_quotes: int = 9, region: bool = True, vary: bool = False) -> Path:
    """A long scored frame; ``region`` is a non-solver column constant per quote."""
    rng = np.random.RandomState(11)
    rows: dict[str, list[Any]] = {
        "quote_id": [],
        "scenario_index": [],
        "scenario_value": [],
        "expected_income": [],
        "volume": [],
        "region": [],
        "noise": [],
    }
    for quote in range(n_quotes):
        income = float(rng.uniform(100, 1000))
        volume = float(rng.uniform(0.5, 1.5))
        for step, value in enumerate(_GRID):
            rows["quote_id"].append(f"q{quote:03d}")
            rows["scenario_index"].append(step)
            rows["scenario_value"].append(float(value))
            rows["expected_income"].append(income * float(value))
            rows["volume"].append(volume * (2.0 - float(value)))
            varies = vary and quote == 4 and step == 2
            rows["region"].append("Elsewhere" if varies else _region(quote))
            rows["noise"].append(float(rng.uniform()))
    frame = pl.DataFrame(
        {
            **rows,
            "scenario_index": pl.Series(rows["scenario_index"], dtype=pl.Int32),
            "scenario_value": pl.Series(rows["scenario_value"], dtype=pl.Float32),
            "expected_income": pl.Series(rows["expected_income"], dtype=pl.Float32),
            "volume": pl.Series(rows["volume"], dtype=pl.Float32),
        }
    )
    if not region:
        frame = frame.drop("region")
    path = root / "scored.parquet"
    frame.write_parquet(path)
    return path


def _regions_frame(root: Path, *, quote_column: str = "quote_id", n_quotes: int = 9) -> Path:
    """A separate per-quote frame: missing q000, an unsolved extra quote, a repeated row."""
    quotes = [f"q{quote:03d}" for quote in range(1, n_quotes)]
    frame = pl.DataFrame(
        {
            quote_column: [*quotes, "q001", "unsolved"],
            "region": [*(_region(int(q[1:])) for q in quotes), _region(1), "Nowhere"],
            "channel": ["web"] * (len(quotes) + 2),
        }
    )
    path = root / "regions.parquet"
    frame.write_parquet(path)
    return path


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "mode": "online",
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "max_iter": 20,
        "tolerance": 1e-4,
    }
    config.update(overrides)
    return config


def _node(node_id: str, node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"id": node_id, "data": {"label": node_id, "nodeType": node_type, "config": config}}


def _data_graph(scored: Path, **config: Any) -> dict[str, Any]:
    return make_graph(
        {
            "nodes": [
                _node("source", "dataInput", make_ready_file_input_config(str(scored))),
                _node("opt", "optimiser", _config(**config)),
            ],
            "edges": [make_edge("source", "opt").model_dump()],
        }
    ).model_dump()


def _side_graph(scored: Path, regions: Path, **config: Any) -> dict[str, Any]:
    return make_graph(
        {
            "nodes": [
                _node("source", "dataInput", make_ready_file_input_config(str(scored))),
                _node("regions", "dataInput", make_ready_file_input_config(str(regions))),
                _node(
                    "opt",
                    "optimiser",
                    _config(
                        **{
                            "data_input": "source",
                            "analysis_input": "regions",
                            "analysis_columns": ["region"],
                            **config,
                        }
                    ),
                ),
            ],
            "edges": [
                make_edge("source", "opt").model_dump(),
                make_edge("regions", "opt").model_dump(),
            ],
        }
    ).model_dump()


def _poll(client: Any, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/optimiser/solve/status/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in _TERMINAL:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"solve {job_id} did not finish within {timeout}s")


def _start(client: Any, graph: dict[str, Any]) -> str:
    response = client.post("/api/optimiser/solve", json={"graph": graph, "node_id": "opt"})
    assert response.status_code == 200, response.text
    return str(response.json()["job_id"])


def _solved(client: Any, graph: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    job_id = _start(client, graph)
    status = _poll(client, job_id)
    assert status["status"] == "completed", status.get("message")
    return job_id, status


def _analysis_handle(job_id: str) -> dict[str, Any]:
    handle = _store.require_job(job_id)["artifact_handles"]["quote_analysis"]
    return dict(handle)


def _side_table(job_id: str) -> pl.DataFrame:
    return pl.read_parquet(_analysis_handle(job_id)["path"]).sort("quote_id")


def _removed(path: str | Path, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while Path(path).exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _expected_grid() -> list[dict[str, float | int]]:
    return [
        {"optimal_step": step, "scenario_value": float(value)} for step, value in enumerate(_GRID)
    ]


class _RecordGridColumns:
    """Record the columns each grid build decodes, then build the real grid."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        real = _optimiser_input.price_contour().build_grid_from_parquet_chunked

        def recording(path: str, constraint_cols: list[str], chunk: int, **kwargs: Any) -> Any:
            self.calls.append({"constraints": list(constraint_cols), **kwargs})
            return real(path, constraint_cols, chunk, **kwargs)

        monkeypatch.setattr(
            _optimiser_input.price_contour(), "build_grid_from_parquet_chunked", recording
        )


# ---------------------------------------------------------------------------
# Data-input path: carried through the real input preparation
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestDataInputPath:
    def test_region_reaches_the_side_table_and_the_solver_inputs_are_unchanged(
        self, client, tmp_path, monkeypatch
    ):
        scored = _scored(tmp_path)
        grids = _RecordGridColumns(monkeypatch)
        plain_job, plain = _solved(client, _data_graph(scored))
        job_id, with_analysis = _solved(client, _data_graph(scored, analysis_columns=["region"]))

        table = _side_table(job_id)
        assert table.columns == ["quote_id", "region", ANALYSIS_ROW_PRESENT_COLUMN]
        assert table.height == 9
        assert table["quote_id"].to_list() == [f"q{q:03d}" for q in range(9)]
        assert table["region"].to_list() == [_region(q) for q in range(9)]
        assert table[ANALYSIS_ROW_PRESENT_COLUMN].all()
        # The solver saw exactly the solver columns, and solved exactly the same problem.
        assert grids.calls[0] == grids.calls[1]
        assert grids.calls[1]["constraints"] == ["volume"]
        for key in ("total_objective", "constraints", "lambdas", "n_quotes", "n_steps"):
            assert with_analysis["result"][key] == plain["result"][key]
        assert "quote_analysis" not in _store.require_job(plain_job)["artifact_handles"]

    def test_the_handle_records_cardinality_and_no_missing_quotes(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        handle = _analysis_handle(job_id)

        assert handle["kind"] == "optimiser_quote_analysis"
        assert handle["analysis_source"] == "data_input"
        assert handle["row_count"] == 9
        assert handle["missing_quote_count"] == 0
        assert handle["columns"] == ["region"]
        assert handle["column_stats"]["region"] == {
            "dtype": "String",
            "approx_n_unique": 3,
            # "Île" is four UTF-8 bytes; "North" and "South" are five.
            "max_string_bytes": 5,
        }

    def test_a_column_that_varies_within_a_quote_is_refused_by_name(self, client, tmp_path):
        job_id = _start(
            client, _data_graph(_scored(tmp_path, vary=True), analysis_columns=["region"])
        )
        status = _poll(client, job_id)

        assert status["status"] == "contract_error"
        assert "vary within a quote" in status["message"]
        assert "'region' (1 quote" in status["message"]
        assert "q004" in status["message"]
        assert "quote_analysis" not in (_store.require_job(job_id).get("artifact_handles") or {})

    def test_a_missing_analysis_column_is_refused_by_name(self, client, tmp_path):
        job_id = _start(
            client, _data_graph(_scored(tmp_path, region=False), analysis_columns=["region"])
        )
        status = _poll(client, job_id)

        # The source's own projection refuses the missing column by name.
        assert status["status"] != "completed"
        assert "missing=['region']" in status["message"]

    def test_without_analysis_columns_no_side_table_is_written(self, client, tmp_path):
        job_id, status = _solved(client, _data_graph(_scored(tmp_path)))

        assert "quote_analysis" not in _store.require_job(job_id)["artifact_handles"]
        assert status["result"]["scenario_grid"] == _expected_grid()


# ---------------------------------------------------------------------------
# The scenario grid
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestScenarioGrid:
    def test_every_result_carries_the_solver_inputs_grid(self, client, tmp_path):
        job_id, status = _solved(
            client,
            _data_graph(
                _scored(tmp_path),
                frontier_enabled=True,
                frontier_steps=3,
                frontier_ranges={"volume": {"min": 5.0, "max": 7.0}},
            ),
        )

        assert status["result"]["scenario_grid"] == _expected_grid()
        assert _store.require_job(job_id)["scenario_grid"] == _expected_grid()
        selected = client.post(
            "/api/optimiser/frontier/select", json={"job_id": job_id, "point_index": 0}
        )
        assert selected.status_code == 200, selected.text
        after = client.get(f"/api/optimiser/solve/status/{job_id}").json()
        assert after["result"]["selected_frontier_point"] == 0
        assert after["result"]["scenario_grid"] == _expected_grid()


# ---------------------------------------------------------------------------
# Ownership: adoption, deletion, lease, reaping
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestOwnership:
    def test_adoption_survives_completion_apply_and_frontier_recompute(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        handle = _analysis_handle(job_id)

        frontier = run_frontier_and_wait(
            client,
            {
                "job_id": job_id,
                "threshold_ranges": {"volume": [5.0, 8.0]},
                "n_points_per_dim": 3,
            },
        )
        assert frontier["status"] == "completed", frontier
        assert _analysis_handle(job_id) == handle
        assert Path(handle["path"]).is_file()

        # A point's apply, then the anchor's: each slims heavy state afterwards.
        for point_index in (0, None):
            applied = client.post(
                "/api/optimiser/apply", json={"job_id": job_id, "point_index": point_index}
            )
            assert applied.status_code == 200, applied.text
            assert _analysis_handle(job_id) == handle
            assert Path(handle["path"]).is_file()

    def test_heavy_state_expiry_keeps_the_file_for_the_jobs_lifetime(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        handle = _analysis_handle(job_id)

        _store.clear_result_data(job_id)

        assert _analysis_handle(job_id) == handle
        assert Path(handle["path"]).is_file()
        _store.delete_job(job_id)
        assert not Path(handle["directory"]).exists()

    def test_a_cancelled_solve_leaves_no_file(self, client, tmp_path, monkeypatch):
        seen: list[dict[str, Any]] = []
        entered = threading.Event()
        release = threading.Event()
        real_solve = _optimiser_service._solve_online

        def held_solve(ctx: Any, **kwargs: Any) -> None:
            seen.append(dict(kwargs["quote_analysis_handle"]))
            entered.set()
            assert release.wait(30)
            return real_solve(ctx, **kwargs)

        monkeypatch.setattr(_optimiser_service, "_solve_online", held_solve)
        job_id = _start(client, _data_graph(_scored(tmp_path), analysis_columns=["region"]))
        assert entered.wait(60)
        assert Path(seen[0]["path"]).is_file()

        cancelled = client.post(f"/api/optimiser/solve/cancel/{job_id}")
        assert cancelled.status_code == 200, cancelled.text
        release.set()

        assert _poll(client, job_id)["status"] == "cancelled"
        assert _removed(seen[0]["directory"])

    def test_the_worker_never_deletes_an_adopted_table_after_completion(
        self, client, tmp_path, monkeypatch
    ):
        completed = threading.Event()
        resume = threading.Event()
        released = threading.Event()
        real_solve = _optimiser_service._solve_online
        real_release = _optimiser_service.OptimiserSolveService._release_job_ownership

        def solve_then_wait(ctx: Any, **kwargs: Any) -> bool:
            adopted = real_solve(ctx, **kwargs)
            completed.set()
            assert resume.wait(30)
            return adopted

        def release(self: Any, *args: Any, **kwargs: Any) -> None:
            try:
                real_release(self, *args, **kwargs)
            finally:
                released.set()

        monkeypatch.setattr(_optimiser_service, "_solve_online", solve_then_wait)
        monkeypatch.setattr(
            _optimiser_service.OptimiserSolveService, "_release_job_ownership", release
        )
        job_id = _start(client, _data_graph(_scored(tmp_path), analysis_columns=["region"]))
        assert completed.wait(60)
        handle = _analysis_handle(job_id)

        with _store.lease(job_id, "quote_analysis"):
            # The job goes (evicted, say) while its solve worker is still finishing.
            _store.delete_job(job_id)
            resume.set()
            assert released.wait(30)
            assert Path(handle["path"]).is_file()
        assert not Path(handle["directory"]).exists()

    def test_a_failed_solve_leaves_no_file(self, client, tmp_path, monkeypatch):
        seen: list[dict[str, Any]] = []

        def failing_solve(ctx: Any, **kwargs: Any) -> None:
            seen.append(dict(kwargs["quote_analysis_handle"]))
            raise RuntimeError("solver blew up")

        monkeypatch.setattr(_optimiser_service, "_solve_online", failing_solve)
        job_id = _start(client, _data_graph(_scored(tmp_path), analysis_columns=["region"]))

        assert _poll(client, job_id)["status"] == "error"
        assert _removed(seen[0]["directory"])

    def test_a_setup_failure_after_extraction_leaves_no_file(self, client, tmp_path, monkeypatch):
        seen: list[dict[str, Any]] = []
        real_write = _optimiser_service.write_quote_analysis

        def recording_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
            handle = real_write(*args, **kwargs)
            seen.append(handle)
            return handle

        def failing_launch(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("launch failed")

        monkeypatch.setattr(_optimiser_service, "write_quote_analysis", recording_write)
        monkeypatch.setattr(
            _optimiser_service.OptimiserSolveService, "_launch_background", failing_launch
        )
        job_id = _start(client, _data_graph(_scored(tmp_path), analysis_columns=["region"]))

        assert _poll(client, job_id)["status"] == "error"
        assert _removed(seen[0]["directory"])

    def test_a_reader_lease_defers_deletion_until_released(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        handle = _analysis_handle(job_id)

        with _store.lease(job_id, "quote_analysis") as leased:
            assert leased == handle
            _store.delete_job(job_id)
            # The job is gone, but the reader still holds the file.
            assert Path(handle["path"]).is_file()
            assert pl.read_parquet(leased["path"]).height == 9
        assert not Path(handle["directory"]).exists()

    def test_collect_reads_inside_a_lease_and_a_gone_job_is_410(self, client, tmp_path):
        from fastapi import HTTPException

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        counts = collect_quote_analysis(
            _store,
            job_id,
            lambda frame: frame.group_by("region").len().sort("region"),
        )
        assert dict(counts.iter_rows()) == {"North": 3, "South": 3, "Île": 3}

        _store.delete_job(job_id)
        with pytest.raises(HTTPException) as caught:
            collect_quote_analysis(_store, job_id, lambda frame: frame)
        assert caught.value.status_code == 410

    def test_startup_reaping_removes_a_stale_side_table(self, tmp_path, monkeypatch):
        root = tmp_path / "analysis"
        root.mkdir()
        # Point every optimiser root here, so reaping at age 0 touches no other test's files.
        monkeypatch.setattr(_optimiser_artifacts, "_quote_analysis_artifact_root", lambda: root)
        monkeypatch.setattr(_optimiser_artifacts, "_apply_artifact_root", lambda: tmp_path / "a")
        monkeypatch.setattr(
            _optimiser_artifacts, "_ratebook_factors_artifact_root", lambda: tmp_path / "f"
        )
        directory = _optimiser_artifacts._new_quote_analysis_directory()
        assert directory.parent == root

        reports = _optimiser_artifacts.reap_stale_optimiser_artifacts(0)

        assert reports["quote_analysis"]["removed"] == 1
        assert not directory.exists()


# ---------------------------------------------------------------------------
# Side-input path: a separate connected frame supplies the analysis columns
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestSideInputPath:
    def test_a_separate_frame_supplies_region_joined_by_quote_id(self, client, tmp_path):
        scored = _scored(tmp_path, region=False)
        job_id, _status = _solved(client, _side_graph(scored, _regions_frame(tmp_path)))

        table = _side_table(job_id)
        handle = _analysis_handle(job_id)
        assert handle["analysis_source"] == "side_input"
        assert table.columns == ["quote_id", "region", ANALYSIS_ROW_PRESENT_COLUMN]
        # Exactly the solved quotes: the unsolved extra quote is ignored.
        assert table["quote_id"].to_list() == [f"q{q:03d}" for q in range(9)]
        # q000 has no row in the frame: it is marked missing and counted.
        assert table.row(0) == ("q000", None, False)
        assert table["region"].to_list()[1:] == [_region(q) for q in range(1, 9)]
        assert handle["missing_quote_count"] == 1
        assert handle["row_count"] == 9

    def test_the_same_region_gives_the_same_side_table_as_the_data_input(self, client, tmp_path):
        data_job, _ = _solved(client, _data_graph(_scored(tmp_path), analysis_columns=["region"]))
        side_root = tmp_path / "side"
        side_root.mkdir()
        regions = side_root / "all_regions.parquet"
        pl.DataFrame(
            {"quote_id": [f"q{q:03d}" for q in range(9)], "region": [_region(q) for q in range(9)]}
        ).write_parquet(regions)
        side_job, _ = _solved(client, _side_graph(_scored(side_root, region=False), regions))

        assert _side_table(side_job).equals(_side_table(data_job))

    def test_a_frame_without_quote_id_is_refused(self, client, tmp_path):
        graph = _side_graph(
            _scored(tmp_path, region=False), _regions_frame(tmp_path, quote_column="qid")
        )
        status = _poll(client, _start(client, graph))

        # Only quote_id and region are demanded from the frame, so its source
        # projection refuses the missing key by name.
        assert status["status"] != "completed"
        assert "missing=['quote_id']" in status["message"]
        assert "available=['qid', 'region', 'channel']" in status["message"]

    def test_resolving_a_computed_frame_without_quote_id_names_the_input(self, tmp_path):
        graph = PipelineGraph.model_validate(
            _side_graph(_scored(tmp_path, region=False), _regions_frame(tmp_path))
        )
        config = next(node for node in graph.nodes if node.id == "opt").data.config
        plan = resolve_analysis_plan(graph, "opt", config)
        assert plan is not None
        frame = pl.LazyFrame({"qid": ["q001"], "region": ["North"]})

        with pytest.raises(OptimiserSetupError) as caught:
            resolve_analysis_frame({"regions": frame}, config, plan)
        assert caught.value.status_code == 400
        assert "analysis_input 'regions' has no 'quote_id' column" in str(caught.value.detail)
        assert "Available: ['qid', 'region']" in str(caught.value.detail)

    def test_resolving_a_numeric_quote_id_or_a_missing_column_is_refused(self, tmp_path):
        graph = PipelineGraph.model_validate(
            _side_graph(_scored(tmp_path, region=False), _regions_frame(tmp_path))
        )
        config = next(node for node in graph.nodes if node.id == "opt").data.config
        plan = resolve_analysis_plan(graph, "opt", config)
        assert plan is not None

        with pytest.raises(OptimiserSetupError, match="must be Utf8"):
            resolve_analysis_frame(
                {"regions": pl.LazyFrame({"quote_id": [1], "region": ["North"]})}, config, plan
            )
        with pytest.raises(OptimiserSetupError, match=r"Missing analysis columns .*\['region'\]"):
            resolve_analysis_frame({"regions": pl.LazyFrame({"quote_id": ["q1"]})}, config, plan)

    def test_a_frame_whose_rows_disagree_within_a_quote_is_refused(self, client, tmp_path):
        regions = tmp_path / "disagree.parquet"
        pl.DataFrame(
            {"quote_id": ["q001", "q001", "q002"], "region": ["North", "South", "South"]}
        ).write_parquet(regions)
        status = _poll(
            client, _start(client, _side_graph(_scored(tmp_path, region=False), regions))
        )

        assert status["status"] == "contract_error"
        assert "'region' (1 quote" in status["message"]
        assert "q001" in status["message"]

    def test_online_setup_executes_the_frame_outside_the_data_inputs_lineage(self, tmp_path):
        graph = PipelineGraph.model_validate(
            _side_graph(_scored(tmp_path, region=False), _regions_frame(tmp_path))
        )
        config = next(node for node in graph.nodes if node.id == "opt").data.config

        plan = resolve_analysis_plan(graph, "opt", config)
        assert plan is not None
        assert (plan.path, plan.source_node_id, plan.columns) == (
            "side_input",
            "regions",
            ("region",),
        )
        assert _setup_execution_target_node_id(graph, "opt") == "opt"
        assert "regions" in _optimiser_input._optimiser_side_input_ids(graph, "opt")
        # Only quote_id and the analysis columns are demanded from the frame.
        demand = _solve_columns_by_node(graph, "opt", dict(config), source="batch")
        assert demand["regions"] == frozenset({"quote_id", "region"})

    def test_changing_analysis_input_changes_the_solves_identity(self, tmp_path):
        from haute._cache import graph_fingerprint

        scored = _scored(tmp_path, region=False)
        regions = _regions_frame(tmp_path)
        side = PipelineGraph.model_validate(_side_graph(scored, regions))
        data = PipelineGraph.model_validate(_side_graph(scored, regions, analysis_input="source"))
        other_columns = PipelineGraph.model_validate(
            _side_graph(scored, regions, analysis_columns=["channel"])
        )

        assert graph_fingerprint(side) != graph_fingerprint(data)
        assert graph_fingerprint(side) != graph_fingerprint(other_columns)


# ---------------------------------------------------------------------------
# Process mode: the production setup worker
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    from haute._sandbox import set_project_root

    set_project_root(tmp_path)
    return tmp_path


class TestProcessMode:
    def test_both_paths_write_the_same_side_table_as_the_thread_path(
        self, client, project, monkeypatch
    ):
        scored = _scored(project)
        regions = _regions_frame(project)
        plain_scored_root = project / "plain"
        plain_scored_root.mkdir()
        plain_scored = _scored(plain_scored_root, region=False)
        thread_data, _ = _solved(client, _data_graph(scored, analysis_columns=["region"]))
        thread_side, _ = _solved(client, _side_graph(plain_scored, regions))

        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
        process_data, _ = _solved(client, _data_graph(scored, analysis_columns=["region"]))
        process_side, _ = _solved(client, _side_graph(plain_scored, regions))

        assert _side_table(process_data).equals(_side_table(thread_data))
        assert _side_table(process_side).equals(_side_table(thread_side))
        assert _analysis_handle(process_side)["missing_quote_count"] == 1

    def test_a_worker_refusal_leaves_no_analysis_directory(self, client, project, monkeypatch):
        created: list[Path] = []
        real_new = _optimiser_artifacts._new_quote_analysis_directory

        def recording_new() -> Path:
            created.append(real_new())
            return created[-1]

        monkeypatch.setattr(_optimiser_artifacts, "_new_quote_analysis_directory", recording_new)
        monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
        graph = _data_graph(_scored(project, vary=True), analysis_columns=["region"])
        status = _poll(client, _start(client, graph))

        # The worker reduced the table under its own cap and refused it by name;
        # the parent removes the directory it created for the worker.
        assert status["status"] == "contract_error"
        assert "'region' (1 quote" in status["message"]
        assert len(created) == 1
        assert _removed(created[0])


# ---------------------------------------------------------------------------
# The extraction itself
# ---------------------------------------------------------------------------


class TestWriteQuoteAnalysis:
    def test_non_string_columns_have_no_string_length_and_counts_are_per_quote(self, tmp_path):
        source = tmp_path / "input.parquet"
        pl.DataFrame(
            {
                "quote_id": pl.Series(["a", "a", "b", "b", "c", "c"], dtype=pl.Categorical),
                "band": [1, 1, 2, 2, 2, 2],
                "segment": ["x", "x", None, None, "yy", "yy"],
            }
        ).write_parquet(source)

        handle = write_quote_analysis(
            solver_input_path=str(source),
            analysis_frame=None,
            quote_id="quote_id",
            columns=("band", "segment"),
            execution_context=None,
        )
        try:
            # Only the table is left in the directory: the per-quote reduction is removed.
            assert sorted(path.name for path in Path(handle["directory"]).iterdir()) == [
                ".haute-artifact.json",
                "quote_analysis.parquet",
            ]
            table = pl.read_parquet(handle["path"]).sort("quote_id")
            assert table.schema["quote_id"] == pl.String
            assert table.to_dicts() == [
                {"quote_id": "a", "band": 1, "segment": "x", ANALYSIS_ROW_PRESENT_COLUMN: True},
                {"quote_id": "b", "band": 2, "segment": None, ANALYSIS_ROW_PRESENT_COLUMN: True},
                {"quote_id": "c", "band": 2, "segment": "yy", ANALYSIS_ROW_PRESENT_COLUMN: True},
            ]
            assert handle["column_stats"]["band"] == {
                "dtype": "Int64",
                "approx_n_unique": 2,
                "max_string_bytes": None,
            }
            assert handle["column_stats"]["segment"]["max_string_bytes"] == 2
        finally:
            _optimiser_artifacts._cleanup_quote_analysis_artifact(handle)

    def test_a_row_count_that_disagrees_with_the_grid_fails_loudly(self) -> None:
        require_one_row_per_solved_quote({"row_count": 3}, 3)
        with pytest.raises(RuntimeError, match="2 rows for 3 solved quotes"):
            require_one_row_per_solved_quote({"row_count": 2}, 3)

    def test_a_refused_column_leaves_no_directory(self, tmp_path, monkeypatch):
        root = tmp_path / "analysis"
        monkeypatch.setattr(_optimiser_artifacts, "_quote_analysis_artifact_root", lambda: root)
        source = tmp_path / "input.parquet"
        pl.DataFrame({"quote_id": ["a", "a", "b"], "band": [1, 2, None]}).write_parquet(source)

        with pytest.raises(AnalysisColumnNotConstantError, match="'band' \\(1 quote\\)"):
            write_quote_analysis(
                solver_input_path=str(source),
                analysis_frame=None,
                quote_id="quote_id",
                columns=("band",),
                execution_context=None,
            )
        assert list(root.iterdir()) == []

    def test_a_null_beside_a_value_varies_within_the_quote(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            _optimiser_artifacts, "_quote_analysis_artifact_root", lambda: tmp_path / "analysis"
        )
        source = tmp_path / "input.parquet"
        pl.DataFrame({"quote_id": ["a", "a"], "band": [1, None]}).write_parquet(source)

        with pytest.raises(AnalysisColumnNotConstantError, match="quote 'a'"):
            write_quote_analysis(
                solver_input_path=str(source),
                analysis_frame=None,
                quote_id="quote_id",
                columns=("band",),
                execution_context=None,
            )


# ---------------------------------------------------------------------------
# OPT-V09B: bounded queries over the chosen scenarios
# ---------------------------------------------------------------------------


def _choices(job_id: str, reducer: Any, point_index: int | None = None) -> Any:
    from haute.routes._optimiser_outcomes import ChoiceTarget
    from haute.routes.optimiser import _choice_service

    return _choice_service.choice_query(job_id, ChoiceTarget(point_index), reducer)


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= 1e-9 * max(1.0, abs(expected))


def _apply_frame(job_id: str, key: str = "apply_result") -> pl.DataFrame:
    return pl.read_parquet(_store.require_job(job_id)["artifact_handles"][key]["path"])


def _rewrite_side_table(job_id: str, edit: Any) -> None:
    path = _analysis_handle(job_id)["path"]
    edit(pl.read_parquet(path)).write_parquet(path)


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestChoiceQueries:
    def test_the_histogram_reconciles_to_the_solved_totals(self, client, tmp_path):
        from haute.routes._optimiser_outcomes import ScenarioHistogram

        job_id, status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        result = _choices(job_id, ScenarioHistogram())
        rows = result.rows

        # Every grid step, from the recorded grid, including steps nobody chose.
        assert rows.select("optimal_step", "scenario_value").to_dicts() == _expected_grid()
        assert rows["quotes"].sum() == 9 == result.total
        assert rows["optimal_objective"].dtype == pl.Float64
        assert rows["optimal_volume"].dtype == pl.Float64
        assert _close(rows["optimal_objective"].sum(), status["result"]["total_objective"])
        assert _close(rows["optimal_volume"].sum(), status["result"]["constraints"]["volume"])
        chosen = _apply_frame(job_id)["optimal_step"].value_counts()
        assert dict(zip(rows["optimal_step"], rows["quotes"], strict=True)) == {
            step: int(chosen.filter(pl.col("optimal_step") == step)["count"].sum())
            for step in range(3)
        }

    def test_a_frontier_points_histogram_reconciles_to_that_points_totals(self, client, tmp_path):
        from haute.routes._optimiser_outcomes import ScenarioHistogram

        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        frontier = run_frontier_and_wait(
            client,
            {
                "job_id": job_id,
                "threshold_ranges": {"volume": [5.0, 8.0]},
                "n_points_per_dim": 3,
            },
        )
        assert frontier["status"] == "completed", frontier
        point = frontier["result"]["points"][2]

        rows = _choices(job_id, ScenarioHistogram(), point_index=2).rows

        assert _close(rows["optimal_objective"].sum(), point["total_objective"])
        assert _close(rows["optimal_volume"].sum(), point["totals"]["volume"])
        # The query materialised the point's artifact and left the selection alone.
        job = _store.require_job(job_id)
        assert "frontier_apply_result:2" in job["artifact_handles"]
        assert job.get("selected_frontier_point") is None

    def test_a_segment_breakdown_joins_the_side_table_one_to_one(self, client, tmp_path):
        from haute.routes._optimiser_outcomes import ScenarioHistogram, SegmentGroupBy

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        result = _choices(job_id, SegmentGroupBy(("region",), limit=10))
        rows = result.rows.sort("region")

        assert result.total == 3
        assert dict(zip(rows["region"], rows["quotes"], strict=True)) == {
            "North": 3,
            "South": 3,
            "Île": 3,
        }
        histogram = _choices(job_id, ScenarioHistogram()).rows
        assert _close(rows["optimal_objective"].sum(), histogram["optimal_objective"].sum())
        apply = _apply_frame(job_id)
        north = apply.filter(pl.col("quote_id").is_in(["q000", "q003", "q006"]))
        assert _close(
            rows.filter(pl.col("region") == "North")["mean_scenario_value"].item(),
            north["optimal_scenario_value"].cast(pl.Float64).mean(),
        )

    def test_the_group_limit_keeps_the_largest_groups_and_counts_them_all(self, client, tmp_path):
        from haute.routes._optimiser_outcomes import SegmentGroupBy

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path, n_quotes=10), analysis_columns=["region"])
        )
        result = _choices(job_id, SegmentGroupBy(("region",), limit=1))

        # q000..q009: North has four quotes, South and Île three each.
        assert result.total == 3
        assert result.rows.select("region", "quotes").to_dicts() == [
            {"region": "North", "quotes": 4}
        ]

    def test_a_segment_breakdown_needs_configured_analysis_columns(self, client, tmp_path):
        from fastapi import HTTPException

        from haute.routes._optimiser_outcomes import SegmentGroupBy

        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        with pytest.raises(HTTPException) as caught:
            _choices(job_id, SegmentGroupBy(("region",), limit=10))
        assert caught.value.status_code == 400
        assert "region" in str(caught.value.detail)

    @pytest.mark.parametrize("damage", ["dropped_row", "foreign_quote", "repeated_quote"])
    def test_a_side_table_that_does_not_join_one_to_one_fails_loudly(
        self, client, tmp_path, damage
    ):
        from haute.routes._optimiser_outcomes import ChoiceJoinError, ScenarioHistogram

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        edits = {
            "dropped_row": lambda frame: frame.filter(pl.col("quote_id") != "q004"),
            "foreign_quote": lambda frame: frame.with_columns(
                pl.when(pl.col("quote_id") == "q004")
                .then(pl.lit("q999"))
                .otherwise(pl.col("quote_id"))
                .alias("quote_id")
            ),
            "repeated_quote": lambda frame: frame.with_columns(
                pl.when(pl.col("quote_id") == "q004")
                .then(pl.lit("q005"))
                .otherwise(pl.col("quote_id"))
                .alias("quote_id")
            ),
        }
        _rewrite_side_table(job_id, edits[damage])

        # The histogram never reads the side table: only the 1:1 assertion can catch it.
        with pytest.raises(ChoiceJoinError):
            _choices(job_id, ScenarioHistogram())

    def test_top_k_and_row_index_return_bounded_rows_with_the_analysis_columns(
        self, client, tmp_path
    ):
        from haute.routes._optimiser_outcomes import RowIndex, TopK

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        apply = _apply_frame(job_id)

        top = _choices(job_id, TopK("optimal_objective", k=2))
        expected_top = apply.sort(["optimal_objective", "quote_id"], descending=[True, False]).head(
            2
        )
        assert top.total == 9
        assert top.rows["quote_id"].to_list() == expected_top["quote_id"].to_list()
        assert "region" in top.rows.columns

        bottom = _choices(job_id, TopK("optimal_objective", k=2, descending=False))
        assert bottom.rows["quote_id"].to_list() == (
            apply.sort(["optimal_objective", "quote_id"]).head(2)["quote_id"].to_list()
        )

        page = _choices(job_id, RowIndex(offset=3, limit=4))
        assert page.total == 9
        assert page.rows["quote_id"].to_list() == apply["quote_id"].slice(3, 4).to_list()
        assert page.rows["region"].to_list() == [_region(q) for q in range(3, 7)]

    @pytest.mark.parametrize(
        "invalid",
        [
            "k_over_cap",
            "k_by_a_non_choice_column",
            "negative_offset",
            "zero_limit",
            "limit_over_cap",
            "no_group_columns",
            "group_limit_over_cap",
        ],
    )
    def test_an_unbounded_or_invalid_reducer_is_refused(self, client, tmp_path, invalid):
        from fastapi import HTTPException

        from haute.routes._optimiser_outcomes import (
            MAX_CHOICE_ROWS,
            RowIndex,
            SegmentGroupBy,
            TopK,
        )

        reducers = {
            "k_over_cap": lambda: TopK("optimal_objective", k=MAX_CHOICE_ROWS + 1),
            "k_by_a_non_choice_column": lambda: TopK("region", k=2),
            "negative_offset": lambda: RowIndex(offset=-1, limit=2),
            "zero_limit": lambda: RowIndex(offset=0, limit=0),
            "limit_over_cap": lambda: RowIndex(offset=0, limit=MAX_CHOICE_ROWS + 1),
            "no_group_columns": lambda: SegmentGroupBy((), limit=5),
            "group_limit_over_cap": lambda: SegmentGroupBy(("region",), limit=MAX_CHOICE_ROWS + 1),
        }
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        with pytest.raises(HTTPException) as caught:
            _choices(job_id, reducers[invalid]())
        assert caught.value.status_code == 400

    def test_an_over_budget_query_is_refused_before_it_runs(self, client, tmp_path, monkeypatch):
        from fastapi import HTTPException

        from haute._polars_utils import streaming_collect
        from haute.routes import _optimiser_outcomes
        from haute.routes._optimiser_outcomes import ScenarioHistogram

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        collected: list[Any] = []

        def recording_collect(frame: Any, **kwargs: Any) -> Any:
            collected.append(frame)
            return streaming_collect(frame, **kwargs)

        monkeypatch.setattr(_optimiser_outcomes, "streaming_collect", recording_collect)
        monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_BYTES", str(32 * 1024 * 1024))
        with pytest.raises(HTTPException) as caught:
            _choices(job_id, ScenarioHistogram())

        assert caught.value.status_code == 507
        detail = caught.value.detail
        assert detail["error_code"] == "memory_limit"
        assert "choice query" in detail["reason"]
        assert "HAUTE_EXPLORE_MEMORY_LIMIT_MB" in detail["reason"]
        # Refused on its estimate: no join or reducer plan was collected.
        assert collected == []

    def test_identical_concurrent_queries_share_one_run(self, client, tmp_path, monkeypatch):
        from haute.routes._optimiser_outcomes import (
            ChoiceQueryService,
            ScenarioHistogram,
            TopK,
        )

        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        entered = threading.Event()
        release = threading.Event()
        runs: list[Any] = []
        real_run = ChoiceQueryService._run

        def held_run(self: Any, *args: Any, **kwargs: Any) -> Any:
            runs.append(args)
            entered.set()
            assert release.wait(30)
            return real_run(self, *args, **kwargs)

        monkeypatch.setattr(ChoiceQueryService, "_run", held_run)
        outcomes: list[Any] = []
        threads = [
            threading.Thread(
                target=lambda: outcomes.append(_choices(job_id, ScenarioHistogram())),
                daemon=True,
            )
            for _ in range(3)
        ]
        threads[0].start()
        assert entered.wait(30)
        for thread in threads[1:]:
            thread.start()
        time.sleep(0.2)
        other = threading.Thread(
            target=lambda: outcomes.append(_choices(job_id, TopK("optimal_objective", k=1))),
            daemon=True,
        )
        other.start()
        time.sleep(0.2)
        release.set()
        for thread in [*threads, other]:
            thread.join(30)

        assert len(outcomes) == 4
        # The three identical queries ran once; the different query ran on its own.
        assert len(runs) == 2
        histograms = [o for o in outcomes if "quotes" in o.rows.columns]
        assert len(histograms) == 3
        assert all(h.rows.equals(histograms[0].rows) for h in histograms)

    def test_an_analysis_column_named_like_a_choice_column_is_refused(self, client, tmp_path):
        from fastapi import HTTPException

        from haute.routes._optimiser_outcomes import ScenarioHistogram

        scored = _scored(tmp_path)
        pl.read_parquet(scored).with_columns(
            pl.col("region").alias("optimal_objective")
        ).write_parquet(scored)
        job_id, _status = _solved(
            client, _data_graph(scored, analysis_columns=["optimal_objective"])
        )
        with pytest.raises(HTTPException) as caught:
            _choices(job_id, ScenarioHistogram())
        assert caught.value.status_code == 400
        assert "optimal_objective" in str(caught.value.detail)
