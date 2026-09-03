"""Tests for multi-row deploy scoring in a hard-capped isolated worker.

Covers the child worker (`haute.deploy._batch_scoring`), the generated
container app's batch path and its error mapping, and the bundle-time
execution-policy record that both the manifest and ``/health`` expose.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import structlog.testing
from fastapi.testclient import TestClient

from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._worker_isolation import (
    IsolatedWorkerCrashedError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerTimeoutError,
    run_isolated_worker,
)
from haute.deploy import _batch_scoring, _schema
from haute.deploy._batch_scoring import (
    DEPLOY_BATCH_PROCESS_NAME,
    BatchScoreCleanupError,
    BatchScoreError,
    BatchScoreOutcome,
    accept_batch_outcome,
    deploy_batch_timeout_seconds,
    prepare_batch_scoring,
    score_batch_worker,
)
from haute.deploy._container import _generate_app_source
from haute.deploy._pruner import find_output_node, prune_for_deploy
from haute.deploy._schema import infer_deploy_execution_policy, infer_output_schema
from haute.deploy._utils import build_manifest
from haute.errors import BoundedMemoryUnsupportedError, DeployError, PreambleError
from haute.graph_utils import PipelineGraph
from haute.parser import parse_pipeline_file
from tests._deploy_helpers import make_resolved_deploy
from tests.conftest import make_edge, make_graph

FIXTURE_DIR = Path("tests/fixtures")
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"
DATA_DIR = FIXTURE_DIR / "data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingContext:
    """Delegating proxy that counts parent admission releases."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.release_calls: list[bool] = []

    def release_admission(self, *, preserve_primary_error: bool = False) -> None:
        self.release_calls.append(preserve_primary_error)
        self._inner.release_admission(preserve_primary_error=preserve_primary_error)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _fixture_graph() -> tuple[PipelineGraph, str]:
    """Return the pruned fixture deploy graph and its output node id."""
    full = parse_pipeline_file(PIPELINE_FILE)
    output_id = find_output_node(full)
    pruned, _kept, _removed = prune_for_deploy(full, output_id)
    return pruned, output_id


def _fixture_rows(count: int = 2) -> list[dict[str, Any]]:
    frame = pl.read_parquet(DATA_DIR / "policies.parquet", n_rows=count)
    return frame.to_dicts()


def _conservative_graph() -> PipelineGraph:
    """An apiInput graph whose group-by estimate is genuinely unavailable.

    ``unpivot`` produces a dynamic shape, so the estimator cannot prove the
    materialisation boundary ahead of the group-by (the same gap
    ``tests/test_polars_backend_strategy_contract.py`` exercises).
    """
    return make_graph(
        {
            "nodes": [
                {
                    "id": "quotes",
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {"path": "quotes.json"},
                    },
                },
                {
                    "id": "shape",
                    "data": {
                        "label": "shape",
                        "nodeType": "polars",
                        "config": {"code": "df = quotes.unpivot(index=['segment'])"},
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = shape.group_by('segment').agg("
                                "pl.col('value').sum().alias('total'))"
                            )
                        },
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
                                    "source_column": "total",
                                    "output_path": "$[:].total",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("quotes", "shape", source_handle="quotes").model_dump(),
                make_edge("shape", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )


def _provable_group_by_graph() -> PipelineGraph:
    """An apiInput graph whose group-by boundary is fully estimable."""
    return make_graph(
        {
            "nodes": [
                {
                    "id": "quotes",
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {"path": "quotes.json"},
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = quotes.group_by('segment').agg("
                                "pl.col('premium').sum().alias('premium'))"
                            )
                        },
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
                                    "source_column": "premium",
                                    "output_path": "$[:].premium",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("quotes", "agg", source_handle="quotes").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )


def _write_policy_sample(tmp_path: Path, graph: PipelineGraph) -> PipelineGraph:
    """Point the graph's apiInput at a real one-row JSON sample on disk."""
    sample = tmp_path / "quotes.json"
    sample.write_text(
        json.dumps([{"segment": "a", "premium": 1.0}]),
        encoding="utf-8",
    )
    nodes = []
    for node in graph.nodes:
        if node.id == "quotes":
            data = node.data.model_copy(
                update={"config": {**node.data.config, "path": str(sample)}}
            )
            nodes.append(node.model_copy(update={"data": data}))
        else:
            nodes.append(node)
    return graph.model_copy(update={"nodes": nodes})


def _load_app(
    tmp_path: Path,
    *,
    execution_policy: dict[str, Any] | None = None,
):
    """Import the generated container app with a trivial manifest."""
    manifest = {
        "pruned_graph": PipelineGraph().model_dump(mode="json"),
        "input_node_ids": ["quotes"],
        "output_node_id": "quotes",
        "artifacts": {},
        "output_fields": None,
    }
    if execution_policy is not None:
        manifest["execution_policy"] = execution_policy
    (tmp_path / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app_path = tmp_path / "app.py"
    app_path.write_text(_generate_app_source("motor", 8080), encoding="utf-8")
    module_name = f"_haute_batch_app_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


class _BatchHarness:
    """Drive the generated app's batch path with a patched worker runner."""

    def __init__(self, module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.module = module
        self.plans: list[Any] = []
        self.contexts: list[_RecordingContext] = []
        self.worker_calls: list[dict[str, Any]] = []
        real_prepare = module.prepare_batch_scoring

        def instrumented_prepare(rows, **kwargs):
            plan = real_prepare(rows, **kwargs)
            recording = _RecordingContext(plan.execution_context)
            plan.execution_context = recording
            self.plans.append(plan)
            self.contexts.append(recording)
            return plan

        monkeypatch.setattr(module, "prepare_batch_scoring", instrumented_prepare)

    def set_worker(self, monkeypatch: pytest.MonkeyPatch, behaviour) -> None:
        async def fake_runner(function, *args, config):
            self.worker_calls.append({"function": function, "args": args, "config": config})
            return behaviour(self.plans[-1])

        monkeypatch.setattr(self.module, "run_isolated_worker_async", fake_runner)

    @property
    def plan(self) -> Any:
        return self.plans[-1]

    def assert_cleaned_up(self) -> None:
        assert self.contexts, "no batch plan was prepared"
        for context in self.contexts:
            assert len(context.release_calls) == 1
        for plan in self.plans:
            assert not Path(plan.temp_dir).exists()


def _write_result(plan: Any, frame: pl.DataFrame) -> None:
    frame.write_parquet(plan.request.result_path)


# ---------------------------------------------------------------------------
# Generated app — batch path
# ---------------------------------------------------------------------------


class TestGeneratedAppBatchPath:
    def test_single_row_request_never_launches_a_worker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)

        def fake_score_graph(**kwargs):
            return pl.DataFrame({"premium": [12.5]})

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("a one-row request must not launch a worker")

        def forbidden_prepare(*_args, **_kwargs):
            raise AssertionError("a one-row request must not admit a batch worker")

        monkeypatch.setattr(module, "score_graph", fake_score_graph)
        monkeypatch.setattr(module, "run_isolated_worker_async", forbidden)
        monkeypatch.setattr(module, "prepare_batch_scoring", forbidden_prepare)

        response = TestClient(module.app).post("/quote", json={"age": 30})

        assert response.status_code == 200
        assert response.json()["rows"] == [{"premium": 12.5}]

    def test_batch_runs_in_one_hard_capped_worker_and_keeps_the_envelope(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def behaviour(plan):
            _write_result(plan, pl.DataFrame({"premium": [1.0, 2.0, 3.0]}))
            return BatchScoreOutcome(
                row_count=3,
                execution_metrics={"operation": "deploy_quote", "profile": "deploy_batch"},
            )

        harness.set_worker(monkeypatch, behaviour)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "rows",
            "row_count",
            "returned_rows",
            "truncated",
            "limit",
            "execution_metrics",
        }
        assert body["rows"] == [{"premium": 1.0}, {"premium": 2.0}, {"premium": 3.0}]
        assert body["row_count"] == 3
        assert body["returned_rows"] == 3
        assert body["truncated"] is False
        assert body["limit"] == module._QUOTE_RESPONSE_ROW_LIMIT
        assert body["execution_metrics"] == {
            "operation": "deploy_quote",
            "profile": "deploy_batch",
        }

        assert len(harness.worker_calls) == 1
        call = harness.worker_calls[0]
        assert call["function"] is score_batch_worker
        assert call["args"] == (harness.plan.request, harness.plan.budget)
        assert call["config"].process_name == DEPLOY_BATCH_PROCESS_NAME
        assert call["config"].memory_limit_bytes == harness.plan.budget.memory_limit_bytes
        assert call["config"].timeout_seconds == deploy_batch_timeout_seconds()
        assert harness.plan.execution_context.profile is ExecutionProfile.DEPLOY_BATCH
        harness.assert_cleaned_up()

    def test_batch_ndjson_streams_every_row_in_order(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def behaviour(plan):
            _write_result(plan, pl.DataFrame({"premium": [1.5, 2.5, 3.5]}))
            return BatchScoreOutcome(row_count=3, execution_metrics={})

        harness.set_worker(monkeypatch, behaviour)

        response = TestClient(module.app).post(
            "/quote",
            json=[{"age": 30}, {"age": 31}],
            headers={"accept": "application/x-ndjson"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert [json.loads(line) for line in response.text.splitlines()] == [
            {"premium": 1.5},
            {"premium": 2.5},
            {"premium": 3.5},
        ]
        harness.assert_cleaned_up()

    @pytest.mark.parametrize(
        ("exc", "status", "error_code"),
        [
            pytest.param(
                ExecutionCancelledError("deploy_quote", job_id="job-1"),
                499,
                "execution_cancelled",
                id="cancelled",
            ),
            pytest.param(
                ExecutionMemoryLimitExceededError(
                    "deploy_quote",
                    rss_bytes=9,
                    limit_bytes=4,
                ),
                507,
                "memory_limit",
                id="memory",
            ),
            pytest.param(
                BoundedMemoryUnsupportedError("cannot stream"),
                422,
                "bounded_streaming_unsupported",
                id="bounded",
            ),
            pytest.param(
                PreambleError("bad preamble", source_line=3),
                422,
                "preamble_failed",
                id="public_contract",
            ),
        ],
    )
    def test_parent_side_typed_failures_map_like_the_live_path(
        self,
        exc: BaseException,
        status: int,
        error_code: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spooling NDJSON in the parent raises the same typed errors the live
        path maps; the batch path must not collapse them into a 500."""
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def behaviour(plan):
            _write_result(plan, pl.DataFrame({"premium": [1.5, 2.5]}))
            return BatchScoreOutcome(row_count=2, execution_metrics={})

        harness.set_worker(monkeypatch, behaviour)

        def explode(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(module, "_materialize_batch_ndjson", explode)

        response = TestClient(module.app).post(
            "/quote",
            json=[{"age": 30}, {"age": 31}],
            headers={"accept": "application/x-ndjson"},
        )

        assert response.status_code == status
        assert response.json()["error_code"] == error_code
        harness.assert_cleaned_up()

    @pytest.mark.parametrize(
        ("outcome", "status", "expected"),
        [
            (
                BatchScoreOutcome(
                    failure_kind="contract",
                    detail="bad preamble",
                    payload={"error_code": "preamble_error"},
                ),
                422,
                {"error_code": "preamble_error"},
            ),
            (
                BatchScoreOutcome(failure_kind="bounded", detail="cannot stream"),
                422,
                {
                    "error_code": "bounded_streaming_unsupported",
                    "error": "Bounded streaming unsupported",
                    "detail": "cannot stream",
                },
            ),
            (
                BatchScoreOutcome(
                    failure_kind="memory",
                    detail="out of memory",
                    payload={"error_code": "memory_limit"},
                ),
                507,
                {"error_code": "memory_limit"},
            ),
            (
                BatchScoreOutcome(failure_kind="cancelled", detail="cancelled"),
                499,
                {
                    "error_code": "execution_cancelled",
                    "operation": "deploy_quote",
                    "job_id": None,
                    "reason": "cancelled",
                },
            ),
            (
                BatchScoreOutcome(failure_kind="error", detail="boom"),
                500,
                {"error_code": "deploy_internal_error", "error": "boom"},
            ),
        ],
        ids=["contract", "bounded", "memory", "cancelled", "error"],
    )
    def test_child_failure_kinds_map_to_their_http_contract(
        self,
        outcome: BatchScoreOutcome,
        status: int,
        expected: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)
        harness.set_worker(monkeypatch, lambda _plan: outcome)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == status
        assert response.json() == expected
        harness.assert_cleaned_up()

    @pytest.mark.parametrize(
        "behaviour",
        [
            pytest.param(lambda plan: BatchScoreOutcome(row_count=2), id="missing_file"),
            pytest.param(
                lambda plan: (
                    _write_result(plan, pl.DataFrame({"premium": [1.0]})),
                    BatchScoreOutcome(row_count=5),
                )[1],
                id="mismatched_rows",
            ),
            pytest.param(lambda plan: object(), id="wrong_outcome_type"),
        ],
    )
    def test_unpublishable_success_outcomes_fail_as_500(
        self,
        behaviour,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)
        harness.set_worker(monkeypatch, behaviour)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 500
        assert response.json()["error_code"] == "deploy_internal_error"
        harness.assert_cleaned_up()

    @pytest.mark.parametrize(
        ("exc", "reason"),
        [
            (
                IsolatedWorkerMemoryLimitExceededError(rss_bytes=9, rss_limit_bytes=4),
                "worker_rss_limit_exceeded",
            ),
            (
                IsolatedWorkerMemoryLimitUnsupportedError(memory_limit_bytes=1024),
                "native_memory_cap_unavailable",
            ),
            (
                IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=1024),
                "worker_may_have_exceeded_memory_limit",
            ),
            (
                IsolatedWorkerRemoteError(
                    remote_type="MemoryError",
                    remote_message="child died",
                    remote_traceback="",
                ),
                "worker_memory_limit",
            ),
        ],
        ids=["rss_exceeded", "cap_unsupported", "crashed_memory", "remote_memory"],
    )
    def test_worker_memory_failures_map_to_507_with_the_shared_detail(
        self,
        exc: BaseException,
        reason: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def raise_worker(_plan):
            raise exc

        harness.set_worker(monkeypatch, raise_worker)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 507
        body = response.json()
        assert body["error_code"] == "memory_limit"
        assert body["operation"] == "deploy_quote"
        assert body["reason"] == reason
        assert body["memory_limit_bytes"] == harness.plan.budget.memory_limit_bytes
        harness.assert_cleaned_up()

    def test_worker_timeout_maps_to_504(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def raise_timeout(_plan):
            raise IsolatedWorkerTimeoutError(timeout_seconds=1.0)

        harness.set_worker(monkeypatch, raise_timeout)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 504
        assert response.json() == {
            "error_code": "deploy_batch_timeout",
            "operation": "deploy_quote",
            "timeout_seconds": deploy_batch_timeout_seconds(),
        }
        harness.assert_cleaned_up()

    @pytest.mark.parametrize(
        "exc",
        [
            IsolatedWorkerCrashedError(exitcode=1, memory_limit_bytes=None),
            IsolatedWorkerRemoteError(
                remote_type="ValueError",
                remote_message="child raised",
                remote_traceback="",
            ),
        ],
        ids=["crashed", "remote_error"],
    )
    def test_non_memory_worker_deaths_map_to_500(
        self,
        exc: BaseException,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def raise_worker(_plan):
            raise exc

        harness.set_worker(monkeypatch, raise_worker)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 500
        assert response.json()["error_code"] == "deploy_internal_error"
        harness.assert_cleaned_up()

    def test_unexpected_parent_failures_use_the_internal_error_envelope(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-worker failure after admission is a logged 500, and cleanup still runs."""
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def explode(_plan):
            raise RuntimeError("staging disk vanished")

        harness.set_worker(monkeypatch, explode)

        response = TestClient(module.app, raise_server_exceptions=False).post(
            "/quote", json=[{"age": 30}, {"age": 31}]
        )

        assert response.status_code == 500
        assert response.json() == {
            "error_code": "deploy_internal_error",
            "error": "staging disk vanished",
        }
        harness.assert_cleaned_up()

    def test_health_reports_batch_enforcement_and_execution_policy(
        self,
        tmp_path: Path,
    ) -> None:
        policy = {
            "schema_version": 1,
            "profile": "deploy_batch",
            "status": "projected",
            "strategy": "projected",
            "reason_code": "projection_available",
            "blocking_node_id": None,
            "blocking_operator": None,
            "remediation": "No change is needed.",
        }
        module = _load_app(tmp_path, execution_policy=policy)

        body = TestClient(module.app).get("/health").json()

        assert body["memory_enforcement"] == "admission_rss_best_effort"
        assert body["batch_memory_enforcement"] in {"required", "best_effort"}
        assert body["execution_policy"] == policy


# ---------------------------------------------------------------------------
# Child worker (in process)
# ---------------------------------------------------------------------------


def _child_plan(tmp_path: Path, rows: list[dict[str, Any]]):
    graph, output_id = _fixture_graph()
    return prepare_batch_scoring(
        rows,
        graph=graph,
        input_node_ids=["quotes"],
        output_node_id=output_id,
        artifact_paths={},
        output_fields=None,
    )


class TestScoreBatchWorkerInProcess:
    def test_worker_sinks_scored_rows_and_reports_batch_metrics(
        self,
        tmp_path: Path,
    ) -> None:
        plan = _child_plan(tmp_path, _fixture_rows(2))
        try:
            outcome = score_batch_worker(plan.request, plan.budget)

            assert outcome.failure_kind is None, outcome.detail
            assert outcome.row_count == 2
            assert Path(plan.request.result_path).is_file()
            assert pl.read_parquet(plan.request.result_path).height == 2
            assert outcome.execution_metrics is not None
            assert outcome.execution_metrics["admission"]["profile"] == "deploy_batch"
        finally:
            plan.cleanup()

    @pytest.mark.parametrize(
        ("error", "kind"),
        [
            (PreambleError("bad preamble"), "contract"),
            (
                ExecutionMemoryLimitExceededError(
                    "deploy_quote",
                    rss_bytes=2,
                    limit_bytes=1,
                ),
                "memory",
            ),
            (ExecutionCancelledError("deploy_quote"), "cancelled"),
            (RuntimeError("boom"), "error"),
        ],
        ids=["contract", "memory", "cancelled", "error"],
    )
    def test_worker_classifies_failures_and_leaves_no_result_file(
        self,
        error: BaseException,
        kind: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _child_plan(tmp_path, _fixture_rows(2))
        real_create = _batch_scoring.create_isolated_execution_context
        recording_contexts: list[_RecordingContext] = []

        def recording_create(budget):
            context = _RecordingContext(real_create(budget))
            recording_contexts.append(context)
            return context

        def failing_score_graph_lazy(**_kwargs):
            raise error

        monkeypatch.setattr(
            "haute.deploy._batch_scoring.create_isolated_execution_context",
            recording_create,
        )
        monkeypatch.setattr(
            "haute.deploy._scorer.score_graph_lazy",
            failing_score_graph_lazy,
        )
        # Prove the removal path really removes something.
        Path(plan.request.result_path).write_bytes(b"partial")
        try:
            outcome = score_batch_worker(plan.request, plan.budget)

            assert outcome.failure_kind == kind
            assert outcome.row_count is None
            assert not Path(plan.request.result_path).exists()
            assert [context.release_calls for context in recording_contexts] == [[True]]
        finally:
            plan.cleanup()

    def test_unexpected_child_failures_are_logged_before_they_collapse(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The untyped ``error`` outcome carries only a detail string, so the
        child must log the traceback before returning it."""
        plan = _child_plan(tmp_path, _fixture_rows(2))

        def failing_score_graph_lazy(**_kwargs):
            raise KeyError("premium")

        monkeypatch.setattr(
            "haute.deploy._scorer.score_graph_lazy",
            failing_score_graph_lazy,
        )
        try:
            with structlog.testing.capture_logs() as captured:
                outcome = score_batch_worker(plan.request, plan.budget)
        finally:
            plan.cleanup()

        assert outcome.failure_kind == "error"
        records = [entry for entry in captured if entry["event"] == "deploy_batch_scoring_failed"]
        assert len(records) == 1
        assert records[0]["error_type"] == "KeyError"


# ---------------------------------------------------------------------------
# Real spawn
# ---------------------------------------------------------------------------


class TestRealSpawn:
    def test_batch_scores_end_to_end_in_a_real_worker(self, tmp_path: Path) -> None:
        rows = _fixture_rows(2)
        plan = _child_plan(tmp_path, rows)
        try:
            outcome = run_isolated_worker(
                score_batch_worker,
                plan.request,
                plan.budget,
                config=plan.worker_config,
            )
            result = accept_batch_outcome(plan, outcome)

            assert result.row_count == 2
            frame = pl.read_parquet(result.result_path)
            assert frame.height == 2
            assert "premium" in frame.columns
            assert result.execution_metrics is not None
            assert result.execution_metrics["admission"]["profile"] == "deploy_batch"
        finally:
            plan.cleanup()

    def test_unprovable_group_by_runs_conservatively_under_the_worker_cap(
        self,
        tmp_path: Path,
    ) -> None:
        plan = prepare_batch_scoring(
            [{"segment": "a", "premium": 1.0}, {"segment": "b", "premium": 2.0}],
            graph=_conservative_graph(),
            input_node_ids=["quotes"],
            output_node_id="out",
            artifact_paths={},
            output_fields=None,
        )
        try:
            outcome = run_isolated_worker(
                score_batch_worker,
                plan.request,
                plan.budget,
                config=plan.worker_config,
            )
            result = accept_batch_outcome(plan, outcome)

            assert result.execution_metrics is not None
            strategy = result.execution_metrics["execution_strategy"]
            assert strategy["status"] == "warned"
            assert strategy["strategy"] == "full-width-conservative"
            assert any(item.startswith("hard_cap_backend=") for item in strategy["assumptions"])
        finally:
            plan.cleanup()


# ---------------------------------------------------------------------------
# Bundle-time execution policy
# ---------------------------------------------------------------------------


class TestInferDeployExecutionPolicy:
    def test_plain_graph_records_a_streaming_ok_policy(self) -> None:
        graph, output_id = _fixture_graph()

        policy = infer_deploy_execution_policy(
            graph, output_id, ["quotes"], batch_runtime="hard_capped_worker"
        )

        assert policy["schema_version"] == 1
        assert policy["profile"] == "deploy_batch"
        assert policy["status"] in {"projected", "boundary", "admitted_eager"}
        assert policy["strategy"] != "unsupported"
        assert policy["reason_code"]

    def test_unavailable_estimate_is_translated_to_the_runtime_warning(
        self,
        tmp_path: Path,
    ) -> None:
        graph = _write_policy_sample(tmp_path, _conservative_graph())

        policy = infer_deploy_execution_policy(
            graph, "out", ["quotes"], batch_runtime="hard_capped_worker"
        )

        assert policy["status"] == "warned"
        assert policy["strategy"] == "full-width-conservative"
        assert policy["reason_code"] == "materialisation_estimate_unavailable_conservative"
        assert policy["blocking_node_id"] == "agg"
        assert policy["blocking_operator"] == "group_by"
        assert "hard-capped" in policy["remediation"]

    def test_other_planning_rejections_fail_the_bundle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_BYTES", "1")
        graph = _write_policy_sample(tmp_path, _provable_group_by_graph())

        with pytest.raises(DeployError) as error:
            infer_deploy_execution_policy(
                graph, "out", ["quotes"], batch_runtime="hard_capped_worker"
            )

        assert "agg" in str(error.value)
        assert "materialisation_exceeds_headroom" in str(error.value)

    def test_bundle_time_context_is_released_exactly_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph, output_id = _fixture_graph()
        contexts: list[_RecordingContext] = []

        def recording_admit(*, operation: str, row_count: int, profile=None):
            context = _RecordingContext(
                create_admitted_execution_context(
                    operation=operation,
                    profile=ExecutionProfile.DEPLOY_BATCH,
                )
            )
            contexts.append(context)
            return context

        monkeypatch.setattr(
            "haute.deploy._scorer.admit_deploy_execution",
            recording_admit,
        )

        infer_deploy_execution_policy(
            graph, output_id, ["quotes"], batch_runtime="hard_capped_worker"
        )

        assert [context.release_calls for context in contexts] == [[True]]

    def test_in_process_runtime_refuses_an_unprovable_group_by(
        self,
        tmp_path: Path,
    ) -> None:
        graph = _write_policy_sample(tmp_path, _conservative_graph())

        with pytest.raises(DeployError, match="serving process") as error:
            infer_deploy_execution_policy(graph, "out", ["quotes"], batch_runtime="in_process")

        assert "agg" in str(error.value)
        assert "group_by" in str(error.value)

    def test_in_process_runtime_records_a_provable_policy(self) -> None:
        graph, output_id = _fixture_graph()

        policy = infer_deploy_execution_policy(
            graph, output_id, ["quotes"], batch_runtime="in_process"
        )

        assert policy["runtime"] == "in_process"
        assert policy["status"] != "warned"

    def test_manifest_carries_the_execution_policy(self) -> None:
        policy = {"schema_version": 1, "profile": "deploy_batch", "status": "projected"}
        resolved = make_resolved_deploy(execution_policy=policy)

        assert build_manifest(resolved)["execution_policy"] == policy


# ---------------------------------------------------------------------------
# Parent helpers
# ---------------------------------------------------------------------------


class TestPrepareAndAccept:
    def test_prepare_releases_admission_when_staging_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph, output_id = _fixture_graph()
        contexts: list[_RecordingContext] = []

        def recording_admit(*, operation: str, row_count: int, profile=None):
            context = _RecordingContext(
                create_admitted_execution_context(
                    operation=operation,
                    profile=ExecutionProfile.DEPLOY_BATCH,
                )
            )
            contexts.append(context)
            return context

        def failing_mkdtemp(**_kwargs):
            raise OSError("no temp space")

        monkeypatch.setattr("haute.deploy._scorer.admit_deploy_execution", recording_admit)
        monkeypatch.setattr("haute.deploy._batch_scoring.tempfile.mkdtemp", failing_mkdtemp)

        with pytest.raises(OSError, match="no temp space"):
            prepare_batch_scoring(
                [{"a": 1}, {"a": 2}],
                graph=graph,
                input_node_ids=["quotes"],
                output_node_id=output_id,
                artifact_paths={},
                output_fields=None,
            )

        assert [context.release_calls for context in contexts] == [[True]]

    def test_one_row_batch_still_admits_the_batch_profile(self, tmp_path: Path) -> None:
        """One fixed execution path: the served envelope and batch contract apply
        even to the bundle's one-row dry-run, never the live profile."""
        plan = _child_plan(tmp_path, _fixture_rows(1))
        try:
            assert plan.execution_context.profile is ExecutionProfile.DEPLOY_BATCH
            assert plan.budget.profile is ExecutionProfile.DEPLOY_BATCH
        finally:
            plan.cleanup()

    def test_budget_matches_the_parent_admission(self, tmp_path: Path) -> None:
        plan = _child_plan(tmp_path, _fixture_rows(2))
        try:
            assert plan.budget == isolated_execution_budget(plan.execution_context)
            assert plan.worker_config.memory_limit_bytes == plan.budget.memory_limit_bytes
            assert Path(plan.request.input_path).is_file()
        finally:
            plan.cleanup()
        assert not Path(plan.temp_dir).exists()

    def test_accept_rejects_a_failed_outcome(self, tmp_path: Path) -> None:
        plan = _child_plan(tmp_path, _fixture_rows(2))
        try:
            with pytest.raises(BatchScoreError) as error:
                accept_batch_outcome(
                    plan,
                    BatchScoreOutcome(failure_kind="memory", detail="oom", payload={"x": 1}),
                )
            assert error.value.kind == "memory"
            assert error.value.payload == {"x": 1}
        finally:
            plan.cleanup()

    def test_admission_refusal_propagates_from_prepare(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph, output_id = _fixture_graph()

        def reject(*, operation: str, row_count: int, profile=None):
            raise ExecutionAdmissionError(
                operation,
                profile=ExecutionProfile.DEPLOY_BATCH,
                memory_limit_bytes=1,
                rss_at_admission_bytes=2,
                reason="process_rss_limit_exceeded",
            )

        monkeypatch.setattr("haute.deploy._scorer.admit_deploy_execution", reject)

        with pytest.raises(ExecutionAdmissionError):
            prepare_batch_scoring(
                [{"a": 1}, {"a": 2}],
                graph=graph,
                input_node_ids=["quotes"],
                output_node_id=output_id,
                artifact_paths={},
                output_fields=None,
            )


# ---------------------------------------------------------------------------
# Output-schema inference for an unprovable group-by
# ---------------------------------------------------------------------------


def _force_schema_cache_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the on-disk output-schema cache at an empty temp location."""
    monkeypatch.setattr(
        _schema,
        "_SCHEMA_CACHE_FILE",
        str(tmp_path / "schema_cache" / "output_schema.json"),
    )


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestOutputSchemaConservativeFallback:
    def test_cache_miss_proves_the_schema_in_the_real_capped_worker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real spawn: nothing is patched across the process boundary."""
        graph = _write_policy_sample(tmp_path, _conservative_graph())
        _force_schema_cache_miss(tmp_path, monkeypatch)
        # The spawned child computes its own project root from the working
        # directory it inherits, so the sample must live inside it.
        monkeypatch.chdir(tmp_path)
        spawns: list[str] = []
        outcomes: list[Any] = []
        real_runner = _schema.run_isolated_worker

        def recording_runner(function, *args, config):
            spawns.append(config.process_name)
            outcome = real_runner(function, *args, config=config)
            outcomes.append(outcome)
            return outcome

        monkeypatch.setattr(_schema, "run_isolated_worker", recording_runner)

        schema = infer_output_schema(graph, "out", ["quotes"])

        # The OUTPUT node maps the aggregation only; the dtype comes from the
        # parquet the worker actually wrote.
        assert set(schema) == {"total"}
        assert schema["total"].startswith("Float") or schema["total"].startswith("Int")
        assert spawns == [DEPLOY_BATCH_PROCESS_NAME]
        # The one-row dry-run ran under the served batch envelope, not the live one.
        admission = outcomes[0].execution_metrics["admission"]
        assert admission["profile"] == "deploy_batch"
        assert admission["operation"] == "deploy_bundle_schema"

    def test_provable_graph_never_spawns_a_worker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph, output_id = _fixture_graph()
        _force_schema_cache_miss(tmp_path, monkeypatch)
        dry_runs: list[int] = []
        spawns: list[str] = []
        real_dry_run = _schema._dry_run_output_schema

        def counting_dry_run(*args, **kwargs):
            dry_runs.append(1)
            return real_dry_run(*args, **kwargs)

        def recording_runner(function, *args, config):
            spawns.append(config.process_name)
            raise AssertionError("a provable graph must not spawn a schema worker")

        monkeypatch.setattr(_schema, "_dry_run_output_schema", counting_dry_run)
        monkeypatch.setattr(_schema, "run_isolated_worker", recording_runner)

        schema = infer_output_schema(graph, output_id, ["quotes"])

        assert schema
        assert len(dry_runs) == 1
        assert spawns == []

    def test_a_host_without_a_usable_cap_fails_the_bundle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph = _write_policy_sample(tmp_path, _conservative_graph())
        _force_schema_cache_miss(tmp_path, monkeypatch)

        def unsupported_runner(_function, *_args, config):
            raise IsolatedWorkerMemoryLimitUnsupportedError(
                memory_limit_bytes=config.memory_limit_bytes or 1,
            )

        monkeypatch.setattr(_schema, "run_isolated_worker", unsupported_runner)

        with pytest.raises(DeployError) as error:
            infer_output_schema(graph, "out", ["quotes"])

        message = str(error.value)
        assert "hard-capped batch worker" in message
        assert "agg" in message
        assert "group_by" in message
        assert "native kernel limit" in message

    def test_a_child_failure_fails_the_bundle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The worker ran but could not produce the rows — e.g. it hit its cap."""
        graph = _write_policy_sample(tmp_path, _conservative_graph())
        _force_schema_cache_miss(tmp_path, monkeypatch)

        def failing_runner(_function, *_args, config):
            return BatchScoreOutcome(
                failure_kind="memory",
                detail="group-by exceeded the worker envelope",
                payload={"error_code": "memory_limit"},
            )

        monkeypatch.setattr(_schema, "run_isolated_worker", failing_runner)

        with pytest.raises(DeployError) as error:
            infer_output_schema(graph, "out", ["quotes"])

        message = str(error.value)
        assert "agg" in message
        assert "group-by exceeded the worker envelope" in message
        assert "every batch request" in message


# ---------------------------------------------------------------------------
# Cleanup failures are never silent
# ---------------------------------------------------------------------------


class TestBatchCleanupFailsLoud:
    @staticmethod
    def _break_rmtree(monkeypatch: pytest.MonkeyPatch) -> None:
        def failing_rmtree(path, *args, **kwargs):
            raise OSError(f"cannot remove {path}")

        monkeypatch.setattr("haute.deploy._batch_scoring.shutil.rmtree", failing_rmtree)

    def test_successful_batch_reports_a_cleanup_failure_as_500(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)

        def behaviour(plan):
            _write_result(plan, pl.DataFrame({"premium": [1.0, 2.0]}))
            return BatchScoreOutcome(row_count=2, execution_metrics={})

        harness.set_worker(monkeypatch, behaviour)
        self._break_rmtree(monkeypatch)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 500
        body = response.json()
        assert body["error_code"] == "deploy_internal_error"
        assert "temporary directory" in body["error"]
        assert len(harness.contexts[0].release_calls) == 1
        monkeypatch.undo()
        shutil.rmtree(harness.plan.temp_dir, ignore_errors=True)

    def test_cleanup_failure_wins_over_a_handled_child_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_app(tmp_path)
        harness = _BatchHarness(module, monkeypatch)
        harness.set_worker(
            monkeypatch,
            lambda _plan: BatchScoreOutcome(
                failure_kind="contract",
                detail="bad preamble",
                payload={"error_code": "preamble_error"},
            ),
        )
        self._break_rmtree(monkeypatch)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 500
        assert "temporary directory" in response.json()["error"]
        assert len(harness.contexts[0].release_calls) == 1
        monkeypatch.undo()
        shutil.rmtree(harness.plan.temp_dir, ignore_errors=True)

    def test_setup_failure_attaches_the_cleanup_note_to_the_original_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph, output_id = _fixture_graph()

        def failing_write_text(self, *_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", failing_write_text)
        self._break_rmtree(monkeypatch)

        with pytest.raises(OSError, match="disk full") as error:
            prepare_batch_scoring(
                [{"a": 1}, {"a": 2}],
                graph=graph,
                input_node_ids=["quotes"],
                output_node_id=output_id,
                artifact_paths={},
                output_fields=None,
            )

        assert any(
            "Deploy batch cleanup failed" in note for note in getattr(error.value, "__notes__", [])
        )

    def test_plan_cleanup_raises_when_no_primary_error_is_in_flight(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _child_plan(tmp_path, _fixture_rows(2))
        temp_dir = plan.temp_dir
        self._break_rmtree(monkeypatch)
        try:
            with pytest.raises(BatchScoreCleanupError, match="temporary directory"):
                plan.cleanup()
            # The admission is still released, and the guard stays idempotent.
            plan.cleanup()
        finally:
            monkeypatch.undo()
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# A cap-dependent policy requires enforced caps (fail closed at startup)
# ---------------------------------------------------------------------------

_WARNED_POLICY = {
    "schema_version": 1,
    "profile": "deploy_batch",
    "runtime": "hard_capped_worker",
    "status": "warned",
    "strategy": "full-width-conservative",
    "reason_code": "materialisation_estimate_unavailable_conservative",
    "blocking_node_id": "agg",
    "blocking_operator": "group_by",
    "remediation": "The deployed batch worker runs this group-by under its full envelope.",
}

_OK_POLICY = {
    "schema_version": 1,
    "profile": "deploy_batch",
    "runtime": "hard_capped_worker",
    "status": "projected",
    "strategy": "projected",
    "reason_code": "projection_available",
    "blocking_node_id": None,
    "blocking_operator": None,
    "remediation": "No change is needed.",
}


class TestFailClosedBatchEnforcement:
    def test_warned_policy_refuses_to_start_under_best_effort(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")

        with pytest.raises(RuntimeError) as error:
            _load_app(tmp_path, execution_policy=_WARNED_POLICY)

        message = str(error.value)
        assert "HAUTE_WORKER_MEMORY_ENFORCEMENT=required" in message
        assert "full-width-conservative" in message
        assert "agg" in message
        assert "group_by" in message

    def test_warned_policy_starts_under_required_enforcement(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")

        module = _load_app(tmp_path, execution_policy=_WARNED_POLICY)

        body = TestClient(module.app).get("/health").json()
        assert body["batch_memory_enforcement"] == "required"
        assert body["execution_policy"] == _WARNED_POLICY

    def test_warned_policy_refuses_to_start_when_the_host_cannot_install_a_cap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``required`` is a setting, not a capability: a host with no native
        cap backend cannot keep a warned policy's promise either."""
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")
        monkeypatch.setattr(
            "haute._worker_isolation.process_memory_caps_supported",
            lambda: False,
        )

        with pytest.raises(RuntimeError) as error:
            _load_app(tmp_path, execution_policy=_WARNED_POLICY)

        assert "native memory cap" in str(error.value)

    def test_warned_policy_starts_when_the_host_can_install_a_cap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")
        monkeypatch.setattr(
            "haute._worker_isolation.process_memory_caps_supported",
            lambda: True,
        )

        module = _load_app(tmp_path, execution_policy=_WARNED_POLICY)

        assert TestClient(module.app).get("/health").json()["execution_policy"] == _WARNED_POLICY

    def test_provable_policy_starts_under_best_effort(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")

        module = _load_app(tmp_path, execution_policy=_OK_POLICY)

        body = TestClient(module.app).get("/health").json()
        assert body["batch_memory_enforcement"] == "best_effort"
        assert body["execution_policy"] == _OK_POLICY

    def test_required_enforcement_without_a_usable_cap_answers_507(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A host that cannot install the cap fails the request, not silently."""
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "required")
        module = _load_app(tmp_path, execution_policy=_WARNED_POLICY)
        harness = _BatchHarness(module, monkeypatch)

        def raise_unsupported(plan):
            raise IsolatedWorkerMemoryLimitUnsupportedError(
                memory_limit_bytes=plan.budget.memory_limit_bytes,
            )

        harness.set_worker(monkeypatch, raise_unsupported)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}, {"age": 31}])

        assert response.status_code == 507
        body = response.json()
        assert body["error_code"] == "memory_limit"
        assert body["reason"] == "native_memory_cap_unavailable"
        harness.assert_cleaned_up()
