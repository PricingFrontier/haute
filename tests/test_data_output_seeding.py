"""Data Output runs seed from and capture into shared snapshots.

CACHE-S07. The write-output route's supervising parent prepares inputs and opens the seed
plan, the sink worker adopts it — run in-process here so the handoff is exercised without a
spawn per run — and an in-process write (``write_data_output``) opens its own.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._node_snapshots import NodeSnapshotStore
from haute._sandbox import set_project_root
from haute._types import PipelineGraph
from haute.routes import pipeline as pipeline_route
from tests.test_training_seeding import _MODELLING, _counting_builds, _train

_ROWS = 120


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    set_project_root(haute_scratch)
    for profile in ("LAZY_SINK", "TRAINING"):
        monkeypatch.setenv(f"HAUTE_{profile}_MEMORY_LIMIT_MB", "1024")
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "id": list(range(_ROWS)),
            "a": [float(value % 7) for value in range(_ROWS)],
            "b": [float(value % 5) for value in range(_ROWS)],
            "y": [float(value % 3) for value in range(_ROWS)],
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    pl.DataFrame(
        {"id": list(range(_ROWS)), "d": [value / 10 for value in range(_ROWS)]}
    ).write_parquet(haute_scratch / "claims.parquet")
    return haute_scratch


def _data_input(project: Path, name: str) -> dict[str, Any]:
    return {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(project / name)}


def _graph(project: Path, *, with_training: bool = False) -> dict[str, Any]:
    """``quotes, claims → J (join) → B → out`` (and ``B → train``)."""
    nodes: list[dict[str, Any]] = [
        {"id": node_id, "data": {"label": node_id, "nodeType": kind, "config": config}}
        for node_id, kind, config in [
            ("quotes", "dataInput", _data_input(project, "quotes.parquet")),
            ("claims", "dataInput", _data_input(project, "claims.parquet")),
            ("J", "polars", {"code": "df = quotes.join(claims, on='id', validate='1:1')"}),
            (
                "B",
                "polars",
                {"code": "df = J.with_columns((pl.col('a') + pl.col('d')).alias('e')).sort('id')"},
            ),
            (
                "out",
                "dataOutput",
                {
                    "outputType": "file",
                    "format": "parquet",
                    "path": str(project / "out" / "result.parquet"),
                },
            ),
        ]
    ]
    edges = [("quotes", "J"), ("claims", "J"), ("J", "B"), ("B", "out")]
    if with_training:
        nodes.append(
            {
                "id": "train",
                "data": {"label": "train", "nodeType": "modelling", "config": _MODELLING},
            }
        )
        edges.append(("B", "train"))
    return {
        "nodes": nodes,
        "edges": [
            {"id": f"e{index}", "source": source, "target": target}
            for index, (source, target) in enumerate(edges)
        ],
        "preamble": "import polars as pl",
        "source_file": str(project / "main.py"),
    }


@dataclass
class _Write:
    status_code: int
    body: dict[str, Any]
    calls: Counter[str]

    @property
    def metrics(self) -> dict[str, Any]:
        return self.body["execution_metrics"]

    @property
    def seeds(self) -> set[str]:
        return {seed["node_id"] for seed in self.metrics["shared_snapshot_seeds"]}

    @property
    def captures(self) -> dict[str, str]:
        return {
            capture["node_id"]: capture["outcome"]
            for capture in self.metrics["shared_snapshot_captures"]
        }


def _inline_worker(monkeypatch: pytest.MonkeyPatch, child: Any = None) -> list[Any]:
    """Run the sink worker in this process; record the plan each launch was handed."""
    handoffs: list[Any] = []

    def run(function: Any, *args: Any, config: Any = None, **_: Any) -> Any:
        handoffs.append(args[-2])
        if child is not None:
            return child(*args)
        return function(*args)

    monkeypatch.setattr(pipeline_route, "run_isolated_worker", run)
    return handoffs


def _write(monkeypatch: pytest.MonkeyPatch, graph: dict[str, Any]) -> _Write:
    from fastapi.testclient import TestClient

    from haute.server import app

    calls = _counting_builds(monkeypatch)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/pipeline/write-output",
        json={"graph": graph, "node_id": "out", "overwrite": True},
    )
    return _Write(response.status_code, response.json(), Counter(calls))


def _result(project: Path) -> pl.DataFrame:
    return pl.read_parquet(project / "out" / "result.parquet")


def _staging(project: Path) -> list[Path]:
    return [path for path in project.glob(".haute_cache/inputs/*/.staging-*") if path.is_dir()]


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


def test_second_data_output_run_seeds_producer(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(project)
    _inline_worker(monkeypatch)

    first = _write(monkeypatch, graph)
    written = _result(project)
    second = _write(monkeypatch, graph)

    assert first.status_code == second.status_code == 200, (first.body, second.body)
    # The Data Output is a pass-through: its producer B is captured, and the
    # join above it too.
    assert first.captures == {"J": "published", "B": "published"}
    assert second.seeds == {"B"}
    assert second.captures == {}
    assert sum(second.calls.values()) == 0
    assert_frame_equal(_result(project), written, check_row_order=False)


def test_data_output_seeds_training_capture(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Data Output runs on the batch source, so the same upstream is a batch
    # training run.
    graph = _graph(project, with_training=True)
    training = _train(monkeypatch, graph, source="batch")
    assert training.captures.get("B") == "published", training.job.get("message")
    _inline_worker(monkeypatch)

    write = _write(monkeypatch, graph)

    assert write.status_code == 200, write.body
    assert write.seeds == {"B"}
    assert sum(write.calls.values()) == 0


def test_in_process_write_seeds_and_captures(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.executor import write_data_output

    graph = PipelineGraph.model_validate(_graph(project))
    first = write_data_output(graph, "out", overwrite=True, project_root=project)
    calls = _counting_builds(monkeypatch)
    second = write_data_output(graph, "out", overwrite=True, project_root=project)

    assert first.execution_metrics is not None and second.execution_metrics is not None
    assert {capture.node_id for capture in first.execution_metrics.shared_snapshot_captures} == {
        "J",
        "B",
    }
    assert {seed.node_id for seed in second.execution_metrics.shared_snapshot_seeds} == {"B"}
    assert sum(calls.values()) == 0


def test_data_output_leaves_no_checkpoint_directory(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    from haute._dataframe_execution_cache import DataFrameExecutionCache

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
    _inline_worker(monkeypatch)

    write = _write(monkeypatch, _graph(project))

    assert write.status_code == 200, write.body
    assert not [name for name in created if name.startswith("haute_sink_")]
    assert stored == []


@pytest.mark.parametrize(
    ("stopped", "status_code"),
    [("cancelled", 409), ("timed_out", 504), ("memory_limited", 507)],
)
def test_terminated_data_output_worker_leaves_no_capture_staging(
    project: Path, monkeypatch: pytest.MonkeyPatch, stopped: str, status_code: int
) -> None:
    from haute._data_points import DataPointResolver
    from haute._worker_isolation import (
        IsolatedWorkerMemoryLimitExceededError,
        IsolatedWorkerStoppedError,
        IsolatedWorkerTimeoutError,
    )

    graph = _graph(project)
    staged: list[Path] = []

    def staged_then_killed(*args: Any) -> Any:
        handoff = args[-2]
        store = NodeSnapshotStore(handoff.project_root)
        resolver = DataPointResolver(
            PipelineGraph.model_validate(graph), source="batch", store=store
        )
        identity = resolver.node_output_slot("B").identity(resolver.node_output_signature("B"))
        artifact = store.stage_node_output(identity, staging_token=handoff.staging_token)
        pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
        staged.append(artifact.directory)
        if stopped == "timed_out":
            raise IsolatedWorkerTimeoutError(timeout_seconds=1.0)
        if stopped == "memory_limited":
            raise IsolatedWorkerMemoryLimitExceededError(rss_bytes=2048, rss_limit_bytes=1024)
        raise IsolatedWorkerStoppedError(terminal_reason="cancelled")

    _inline_worker(monkeypatch, child=staged_then_killed)

    write = _write(monkeypatch, graph)

    assert write.status_code == status_code, write.body
    assert len(staged) == 1
    assert not staged[0].exists()
    assert _staging(project) == []
    assert not (project / "out" / "result.parquet").exists()


def test_response_metrics_keep_parent_preparation_with_worker_evidence(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._input_preparation as input_preparation

    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    monkeypatch.setattr(
        input_preparation,
        "run_isolated_worker",
        lambda function, request, budget, **_: function(request, budget),
    )
    pl.read_parquet(project / "quotes.parquet").write_csv(project / "quotes.csv")
    graph = _graph(project)
    graph["nodes"][0]["data"]["config"] = {
        "inputType": "file",
        "format": "csv",
        "path": str(project / "quotes.csv"),
    }
    _inline_worker(monkeypatch)

    write = _write(monkeypatch, graph)

    assert write.status_code == 200, write.body
    # The parent built the CSV snapshot; the worker captured below it.
    assert [
        (record["node_id"], record["action"]) for record in write.metrics["input_preparation"]
    ] == [("quotes", "built")]
    assert write.captures == {"J": "published", "B": "published"}


@pytest.mark.parametrize("ends", ["opened", "failed"])
def test_parent_preparation_past_the_sink_timeout_is_a_timeout(
    project: Path, monkeypatch: pytest.MonkeyPatch, ends: str
) -> None:
    from haute.errors import InputPreparationError

    open_plan = pipeline_route.open_seed_plan
    deadlines: list[float | None] = []

    class _Clock:
        offset = 0.0

        def monotonic(self) -> float:
            return time.monotonic() + self.offset

    clock = _Clock()
    monkeypatch.setattr(pipeline_route, "time", clock)
    monkeypatch.setenv("HAUTE_SINK_TIMEOUT", "60")

    def slow_open(*args: Any, **kwargs: Any) -> Any:
        deadlines.append(kwargs.get("deadline"))
        clock.offset += 65.0
        if ends == "failed":
            # A build stopped by the deadline fails typed; the sink timed out.
            raise InputPreparationError(
                "Preparing this Data Input's snapshot failed.",
                node_id="quotes",
                identity_digest="0" * 64,
                build_class="bounded",
                reason_code="timed_out",
                remediation="Run it again.",
            )
        return open_plan(*args, **kwargs)

    monkeypatch.setattr(pipeline_route, "open_seed_plan", slow_open)
    handoffs = _inline_worker(monkeypatch)

    write = _write(monkeypatch, _graph(project))

    assert write.status_code == 504, write.body
    assert handoffs == []
    assert len(deadlines) == 1 and deadlines[0] is not None
    assert _staging(project) == []


def test_parent_preparation_budget_leaves_the_worker_the_remainder(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_plan = pipeline_route.open_seed_plan

    class _Clock:
        offset = 0.0

        def monotonic(self) -> float:
            return time.monotonic() + self.offset

    clock = _Clock()
    monkeypatch.setattr(pipeline_route, "time", clock)
    monkeypatch.setenv("HAUTE_SINK_TIMEOUT", "60")

    def slow_open(*args: Any, **kwargs: Any) -> Any:
        clock.offset += 45.0
        return open_plan(*args, **kwargs)

    monkeypatch.setattr(pipeline_route, "open_seed_plan", slow_open)
    configs: list[Any] = []

    def run(function: Any, *args: Any, config: Any = None, **_: Any) -> Any:
        configs.append(config)
        return function(*args)

    monkeypatch.setattr(pipeline_route, "run_isolated_worker", run)

    write = _write(monkeypatch, _graph(project))

    assert write.status_code == 200, write.body
    [config] = configs
    assert 10.0 < config.timeout_seconds <= 15.0


def test_gate_cancels_what_registers_before_or_after_the_request() -> None:
    """The gate cancels the context parent preparation checkpoints."""
    from haute._execution_context import ExecutionContext, ExecutionProfile
    from haute.routes._isolated_worker_async import WorkerCancellationGate

    context = ExecutionContext(
        operation="pipeline_write_output", profile=ExecutionProfile.LAZY_SINK
    )
    gate = WorkerCancellationGate()
    gate.on_request(context.cancellation_token.cancel)
    assert not context.cancellation_token.cancelled

    gate.request()

    assert context.cancellation_token.cancelled
    late = ExecutionContext(operation="late", profile=ExecutionProfile.LAZY_SINK)
    gate.on_request(late.cancellation_token.cancel)
    assert late.cancellation_token.cancelled


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [("contract", 422), ("corrupt", 500), ("admission", 507)],
)
def test_parent_plan_failure_starts_no_worker(
    project: Path, monkeypatch: pytest.MonkeyPatch, failure: str, status_code: int
) -> None:
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionProfile
    from haute._source_cache import SourceCacheCorruptError
    from haute.errors import InputPreparationError

    errors: dict[str, Exception] = {
        "contract": InputPreparationError(
            "This Data Input's source is unavailable and no published snapshot exists.",
            node_id="quotes",
            identity_digest="0" * 64,
            build_class="bounded",
            reason_code="build_failed",
            remediation="Restore the source.",
        ),
        "corrupt": SourceCacheCorruptError("source-cache generation is corrupt"),
        "admission": ExecutionAdmissionError(
            "pipeline_write_output",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=1024,
            rss_at_admission_bytes=None,
            reason="no headroom",
        ),
    }

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise errors[failure]

    monkeypatch.setattr(pipeline_route, "open_seed_plan", refused)
    handoffs = _inline_worker(monkeypatch)

    write = _write(monkeypatch, _graph(project))

    assert write.status_code == status_code, write.body
    assert handoffs == []
    assert not (project / "out" / "result.parquet").exists()
    assert not list(project.glob("out/.result.haute-stage-*"))


def test_cancelling_the_request_stops_parent_preparation(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from haute._execution_context import ExecutionContext, ExecutionProfile
    from haute._worker_isolation import IsolatedWorkerStoppedError
    from haute.errors import InputPreparationError
    from haute.routes._isolated_worker_async import WorkerCancellationGate

    gate = WorkerCancellationGate()
    observed: list[bool] = []

    def cancelled_while_preparing(*_args: Any, execution_context: Any, **_kwargs: Any) -> Any:
        # The client goes away while this process prepares the inputs.
        gate.request()
        observed.append(execution_context.cancellation_token.cancelled)
        raise InputPreparationError(
            "Preparing this Data Input's snapshot was cancelled.",
            node_id="quotes",
            identity_digest="0" * 64,
            build_class="bounded",
            reason_code="cancelled",
            remediation="Run it again.",
        )

    monkeypatch.setattr(pipeline_route, "open_seed_plan", cancelled_while_preparing)
    handoffs = _inline_worker(monkeypatch)
    context = ExecutionContext(
        operation="pipeline_write_output", profile=ExecutionProfile.LAZY_SINK
    )

    with pytest.raises(IsolatedWorkerStoppedError) as stopped:
        pipeline_route._output_write_transaction(
            PipelineGraph.model_validate(_graph(project)),
            "out",
            "live",
            None,
            project,
            True,
            None,
            None,
            SimpleNamespace(memory_limit_bytes=1024),  # type: ignore[arg-type]
            gate,
            display_path="out/result.parquet",
            execution_context=context,
        )

    assert stopped.value.terminal_reason == "cancelled"
    # Preparation saw its own context cancelled, and no worker started.
    assert observed == [True]
    assert handoffs == []


def test_worker_captures_under_the_parents_staging_token(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker adopts the parent's plan, so the parent can sweep a killed worker's staging."""
    tokens: list[str | None] = []
    stage = NodeSnapshotStore.stage_node_output

    def recording_stage(self: NodeSnapshotStore, *args: Any, **kwargs: Any) -> Any:
        tokens.append(kwargs.get("staging_token"))
        return stage(self, *args, **kwargs)

    monkeypatch.setattr(NodeSnapshotStore, "stage_node_output", recording_stage)
    handoffs = _inline_worker(monkeypatch)

    write = _write(monkeypatch, _graph(project))

    assert write.status_code == 200, write.body
    [handoff] = handoffs
    assert tokens and set(tokens) == {handoff.staging_token}


def test_request_cancelled_while_preparation_succeeds_starts_no_worker(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile
    from haute._worker_isolation import IsolatedWorkerStoppedError
    from haute.routes._isolated_worker_async import WorkerCancellationGate

    gate = WorkerCancellationGate()
    open_plan = pipeline_route.open_seed_plan

    def cancelled_but_prepared(*args: Any, **kwargs: Any) -> Any:
        # The request goes away, yet preparation still returns a plan.
        gate.request()
        return open_plan(*args, **kwargs)

    monkeypatch.setattr(pipeline_route, "open_seed_plan", cancelled_but_prepared)
    handoffs = _inline_worker(monkeypatch)
    context = create_admitted_execution_context(
        operation="pipeline_write_output", profile=ExecutionProfile.LAZY_SINK
    )
    try:
        with pytest.raises(IsolatedWorkerStoppedError):
            pipeline_route._output_write_transaction(
                PipelineGraph.model_validate(_graph(project)),
                "out",
                "live",
                None,
                project,
                True,
                None,
                None,
                SimpleNamespace(memory_limit_bytes=1024),  # type: ignore[arg-type]
                gate,
                display_path="out/result.parquet",
                execution_context=context,
            )
    finally:
        context.release_admission()

    assert handoffs == []
    assert _staging(project) == []


def test_quota_rejected_captures_serve_the_write(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture the quota refuses is read from the run's own staging through the write."""
    import haute._seed_plans as seed_plans
    from haute._execution_context import ExecutionProfile
    from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotSlot
    from haute.executor import write_data_output

    full = NodeSnapshotStore(project, node_output_max_generations=1)
    # One pinned generation of an unrelated slot fills the quota.
    filler = NodeSnapshotSlot(str(project / "other.py"), "filler", "batch", "bounded").identity(
        "filler-signature"
    )
    artifact = full.stage_node_output(filler)
    pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
    full.publish_node_output(
        filler,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ).close()
    monkeypatch.setattr(seed_plans, "NodeSnapshotStore", lambda _root: full)

    response = write_data_output(
        PipelineGraph.model_validate(_graph(project)),
        "out",
        overwrite=True,
        project_root=project,
    )

    assert response.execution_metrics is not None
    assert {
        capture.node_id: capture.outcome
        for capture in response.execution_metrics.shared_snapshot_captures
    } == {"J": "quota", "B": "quota"}
    expected = (
        pl.read_parquet(project / "quotes.parquet")
        .join(pl.read_parquet(project / "claims.parquet"), on="id")
        .with_columns(e=pl.col("a") + pl.col("d"))
    )
    assert_frame_equal(_result(project), expected, check_row_order=False)
    # The run's staged artifacts went with its plan, after the write.
    assert _staging(project) == []
