"""Training preparation and its evaluation preview seed from and capture into shared snapshots.

CACHE-S07. Every run here goes through ``TrainService._prepare_and_launch_training`` —
the supervising parent prepares inputs and opens the seed plan, the preparation
child adopts it — with the child run in-process so the handoff is exercised
without a spawn per run, and the model launch stubbed out. One test runs whole
jobs through the routes to show a completed job keeps preparation's evidence.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._chunked_writes import WriteRecipe
from haute._data_points import DataPointResolver
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionProfile,
)
from haute._execution_schemas import ExecutionMetricsPayload
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._sandbox import set_project_root
from haute._source_cache import SourceCacheIdentity
from haute._types import PipelineGraph
from haute.routes import _training_lifecycle, _training_preparation
from haute.routes._job_store import JobStore
from haute.routes._train_service import TrainService
from haute.routes._training_preparation import prepare_training_data_worker
from haute.schemas import TrainRequest

ALL = NodeSnapshotColumns.all()
_ROWS = 120
_MODELLING = {
    "target": "y",
    "algorithm": "catboost",
    "task": "regression",
    "loss_function": "RMSE",
    "params": {"iterations": 2, "depth": 2, "learning_rate": 0.3},
    "evaluation": {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.2},
    },
    "metrics": ["rmse"],
}


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    set_project_root(haute_scratch)
    # Every profile a training run admits under. These are adaptive by
    # default, so an unpinned one scales to whatever memory the machine has
    # left and refuses the run on a loaded CI worker — a job that reports
    # memory_limited for reasons that have nothing to do with the test.
    for profile in ("TRAINING", "NODE_SNAPSHOT", "PREVIEW", "SINK"):
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


@pytest.fixture()
def store(project: Path) -> NodeSnapshotStore:
    return NodeSnapshotStore(project)


def _data_input(project: Path, name: str) -> dict[str, Any]:
    return {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(project / name)}


def _graph(
    project: Path,
    nodes: list[tuple[str, str, dict[str, Any]]],
    edges: list[tuple[str, str]],
    *,
    modelling_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A graph whose modelling node ``train`` reads the last listed producer."""
    train_config = dict(_MODELLING)
    if modelling_config is not None:
        train_config.update(modelling_config)
    return {
        "nodes": [
            *(
                {"id": node_id, "data": {"label": node_id, "nodeType": kind, "config": config}}
                for node_id, kind, config in nodes
            ),
            {
                "id": "train",
                "data": {"label": "train", "nodeType": "modelling", "config": train_config},
            },
        ],
        "edges": [
            {"id": f"e{index}", "source": source, "target": target}
            for index, (source, target) in enumerate(edges)
        ],
        # ``R`` is one random scalar per compiled preamble.
        "preamble": "import polars as pl\nimport random\nR = random.random()",
        "source_file": str(project / "main.py"),
    }


def _chain(project: Path, length: int = 2, *, b_code: str | None = None) -> dict[str, Any]:
    """``src → A → B (→ C) → train``."""
    names = ["A", "B", "C"][:length]
    nodes: list[tuple[str, str, dict[str, Any]]] = [
        ("src", "dataInput", _data_input(project, "quotes.parquet"))
    ]
    edges: list[tuple[str, str]] = []
    parent = "src"
    for index, name in enumerate(names):
        code = f"df = {parent}.with_columns(pl.lit({index}).alias('k{index}'))"
        if name == "B" and b_code is not None:
            code = b_code
        nodes.append((name, "polars", {"code": code}))
        edges.append((parent, name))
        parent = name
    edges.append((parent, "train"))
    return _graph(project, nodes, edges)


def _diamond(
    project: Path, a_code: str = "df = src.with_columns(pl.lit(R).alias('r'))"
) -> dict[str, Any]:
    """``A → B``, ``A → C``, ``B + C → D → train``."""
    return _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": a_code}),
            (
                "B",
                "polars",
                {"code": "df = A.select('id', 'a', 'b', 'y', pl.col('r').alias('rb'))"},
            ),
            ("C", "polars", {"code": "df = A.select('id', pl.col('r').alias('rc'))"}),
            ("D", "polars", {"code": "df = B.join(C, on='id', how='left', validate='1:1')"}),
        ],
        [("src", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "train")],
    )


@dataclass
class _Run:
    job: dict[str, Any]
    frame: pl.DataFrame
    calls: Counter[str]
    # The preparation child's worker config per launch, the parquet path the
    # parent created, and each context that released its admission.
    worker_configs: list[Any] = field(default_factory=list)
    parquet_paths: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)

    @property
    def metrics(self) -> dict[str, Any]:
        return self.job["execution_metrics"]

    @property
    def seeds(self) -> dict[str, str]:
        return {
            seed["node_id"]: seed["generation_id"] for seed in self.metrics["shared_snapshot_seeds"]
        }

    @property
    def captures(self) -> dict[str, str]:
        return {
            capture["node_id"]: capture["outcome"]
            for capture in self.metrics["shared_snapshot_captures"]
        }


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


def _train(
    monkeypatch: pytest.MonkeyPatch,
    graph: dict[str, Any],
    *,
    source: str = "live",
    before_child: Callable[[Any], None] | None = None,
    child: Callable[[Any, Any], Any] | None = None,
    timeout: int = 600,
    streaming_chunk_size: int | None = None,
    row_limit: int | None = None,
) -> _Run:
    """Prepare one training run through the supervising parent and its child."""
    calls = _counting_builds(monkeypatch)
    run = _Run({}, pl.DataFrame(), calls)
    store = JobStore()
    service = TrainService(store)
    body_payload: dict[str, Any] = {"graph": graph, "node_id": "train", "source": source}
    if streaming_chunk_size is not None:
        body_payload["streaming_chunk_size"] = streaming_chunk_size
    body = TrainRequest.model_validate(body_payload)
    config = dict(body.graph.node_map["train"].data.config)
    if row_limit is not None:
        monkeypatch.setattr(
            TrainService,
            "_estimate_ram",
            lambda *args, **kwargs: (None, row_limit, _ROWS, 4),
        )
    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "training",
            "progress": 0.0,
            "message": "Preparing training data...",
            "config": config,
            "node_label": "train",
            "start_time": _training_lifecycle.time.monotonic(),
            "timeout": timeout,
        }
    )
    token = ExecutionCancellationToken()
    service._training_jobs.register_latest(("training", job_id), job_id, execution_token=token)
    requests: list[Any] = []
    create_parquet_path = _training_lifecycle.create_training_parquet_path

    def recording_parquet_path() -> str:
        run.parquet_paths.append(create_parquet_path())
        return run.parquet_paths[-1]

    release = ExecutionContext.release_admission

    def recording_release(self: ExecutionContext, *args: Any, **kwargs: Any) -> Any:
        run.released.append(self.operation)
        return release(self, *args, **kwargs)

    monkeypatch.setattr(_training_lifecycle, "create_training_parquet_path", recording_parquet_path)
    monkeypatch.setattr(ExecutionContext, "release_admission", recording_release)

    def run_child(function: Any, request: Any, budget: Any, *, config: Any = None, **_: Any) -> Any:
        requests.append(request)
        run.worker_configs.append(config)
        if before_child is not None:
            before_child(request)
        if child is not None:
            return child(request, budget)
        return prepare_training_data_worker(request, budget)

    monkeypatch.setattr(_training_lifecycle, "run_isolated_worker", run_child)
    monkeypatch.setattr(TrainService, "_launch_background", lambda *args, **kwargs: None)
    try:
        service._prepare_and_launch_training(job_id, body, "train", config, token)
    except Exception:  # noqa: BLE001 - the job records every failure; the test reads it
        pass
    run.job = dict(store.require_job(job_id))
    if requests and Path(requests[0].parquet_path).exists():
        run.frame = pl.read_parquet(requests[0].parquet_path)
    return run


def _resolver(
    store: NodeSnapshotStore, graph: dict[str, Any], source: str = "live"
) -> DataPointResolver:
    return DataPointResolver(PipelineGraph.model_validate(graph), source=source, store=store)


def _identity(
    store: NodeSnapshotStore, graph: dict[str, Any], node_id: str, source: str = "live"
) -> SourceCacheIdentity:
    resolver = _resolver(store, graph, source)
    return resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))


def _publish(
    store: NodeSnapshotStore,
    graph: dict[str, Any],
    node_id: str,
    frame: pl.DataFrame,
    *,
    dependencies: dict[str, str] | None = None,
    refresh: bool = False,
) -> str:
    """Publish *frame* as an explicit, full-width generation of *node_id*."""
    identity = _identity(store, graph, node_id)
    artifact = store.stage_node_output(identity)
    frame.write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=ALL,
        dependencies=dependencies or {},
        explicit=True,
        profile=ExecutionProfile.NODE_SNAPSHOT,
        refresh=refresh,
    ) as publication:
        assert publication.generation is not None
        return publication.generation.generation_id


def _quotes(project: Path) -> pl.DataFrame:
    return pl.read_parquet(project / "quotes.parquet")


def _chain_frames(project: Path) -> dict[str, pl.DataFrame]:
    quotes = _quotes(project)
    a = quotes.with_columns(pl.lit(0).alias("k0").cast(pl.Int32))
    b = a.with_columns(pl.lit(1).alias("k1").cast(pl.Int32))
    c = b.with_columns(pl.lit(2).alias("k2").cast(pl.Int32))
    return {"A": a, "B": b, "C": c}


def _staging(project: Path) -> list[Path]:
    return [path for path in project.glob(".haute_cache/inputs/*/.staging-*") if path.is_dir()]


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


def test_second_training_run_seeds_first_runs_captures(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("other", "dataInput", _data_input(project, "claims.parquet")),
            ("A", "polars", {"code": "df = src.with_columns((pl.col('a') * 2).alias('a2'))"}),
            ("J", "polars", {"code": "df = A.join(other, on='id', how='left', validate='1:1')"}),
            ("B", "polars", {"code": "df = J.filter(pl.col('a') >= 0)"}),
        ],
        [("src", "A"), ("A", "J"), ("other", "J"), ("J", "B"), ("B", "train")],
    )
    first = _train(monkeypatch, graph)
    assert first.job["status"] == "running", first.job.get("message")
    assert set(first.captures) == {"J"}
    assert first.calls["src"] == 1

    second = _train(monkeypatch, graph)

    assert set(second.seeds) == {"J"}
    assert second.captures == {}
    assert second.calls["src"] == 0
    assert second.calls["other"] == 0
    assert second.calls["B"] == 3
    assert_frame_equal(second.frame, first.frame)


def test_training_behind_batch_model_score_scores_once(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _mlflow_io
    from haute.modelling._feature_contract import build_contract, save_contract

    scored: list[int] = []

    class TenTimes:
        def predict(self, features: Any) -> Any:
            import numpy as np

            scored.append(1)
            column = features["feature"] if hasattr(features, "__getitem__") else features
            return np.asarray(column, dtype="float64") * 10.0

    monkeypatch.setattr(
        _mlflow_io,
        "load_mlflow_model",
        lambda *_args, **_kwargs: _mlflow_io.ScoringModel(TenTimes(), ["feature"], flavor="pyfunc"),
    )
    pl.DataFrame(
        {
            "feature": [float(value % 9) for value in range(_ROWS)],
            "y": [float(value % 3) for value in range(_ROWS)],
        }
    ).write_parquet(project / "scoring.parquet")
    contract_path = project / "feature_contract.json"
    save_contract(
        build_contract(
            features=["feature"],
            feature_types={"feature": "Float64"},
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
            ("scoring", "dataInput", _data_input(project, "scoring.parquet")),
            (
                "M",
                "modelScore",
                {
                    "sourceType": "run",
                    "run_id": "run-1",
                    "artifact_path": "model.pyfunc",
                    "task": "regression",
                    "output_column": "prediction",
                    "feature_contract_path": str(contract_path),
                },
            ),
        ],
        [("scoring", "M"), ("M", "train")],
    )
    first = _train(monkeypatch, graph, source="batch")
    assert first.job["status"] == "running", first.job.get("message")
    assert first.captures == {"M": "published"}
    scoring_calls = len(scored)
    assert scoring_calls > 0

    second = _train(monkeypatch, graph, source="batch")

    assert set(second.seeds) == {"M"}
    assert len(scored) == scoring_calls
    assert_frame_equal(second.frame, first.frame)


def test_training_never_seeds_stale_snapshot(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    frames = _chain_frames(project)
    a1 = _publish(store, graph, "A", frames["A"])
    _publish(store, graph, "B", frames["B"], dependencies={_identity(store, graph, "A").digest: a1})
    a2 = _publish(store, graph, "A", frames["A"], refresh=True)

    run = _train(monkeypatch, graph)

    assert run.seeds == {"A": a2}
    assert run.calls["B"] == 1


def test_training_seeds_only_b_in_a_b_chain(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    frames = _chain_frames(project)
    a1 = _publish(store, graph, "A", frames["A"])
    a_digest = _identity(store, graph, "A").digest
    b1 = _publish(store, graph, "B", frames["B"], dependencies={a_digest: a1})
    read: list[str] = []
    latest = NodeSnapshotStore.latest_generation

    def recording(self: NodeSnapshotStore, identity: SourceCacheIdentity) -> Any:
        read.append(identity.digest)
        return latest(self, identity)

    monkeypatch.setattr(NodeSnapshotStore, "latest_generation", recording)

    for _ in range(2):
        run = _train(monkeypatch, graph)
        assert run.seeds == {"B": b1}
        assert run.captures == {}
        assert not +run.calls
    assert a_digest not in read


def test_refreshed_root_is_seeded_after_chain_goes_stale(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project, length=3)
    frames = _chain_frames(project)
    a1 = _publish(store, graph, "A", frames["A"])
    a_digest = _identity(store, graph, "A").digest
    b1 = _publish(store, graph, "B", frames["B"], dependencies={a_digest: a1})
    _publish(
        store,
        graph,
        "C",
        frames["C"],
        dependencies={a_digest: a1, _identity(store, graph, "B").digest: b1},
    )
    a2 = _publish(store, graph, "A", frames["A"], refresh=True)

    run = _train(monkeypatch, graph)

    assert run.seeds == {"A": a2}
    assert run.calls["B"] == 1 and run.calls["C"] == 1


def test_disagreeing_branches_recompute_from_sources(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _diamond(project)
    quotes = _quotes(project)
    a = quotes.with_columns(pl.lit(0.5).alias("r"))
    a_digest = _identity(store, graph, "A").digest
    a1 = _publish(store, graph, "A", a)
    _publish(
        store,
        graph,
        "B",
        a.select("id", "a", "b", "y", pl.col("r").alias("rb")),
        dependencies={a_digest: a1},
    )
    a2 = _publish(store, graph, "A", a, refresh=True)
    _publish(
        store, graph, "C", a.select("id", pl.col("r").alias("rc")), dependencies={a_digest: a2}
    )
    store.clear(_identity(store, graph, "A"))

    run = _train(monkeypatch, graph)

    assert run.seeds == {}
    assert run.calls["src"] == 1
    assert run.frame["rb"].to_list() == run.frame["rc"].to_list()


def test_single_cached_branch_seeds_recorded_ancestor(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _diamond(project)
    # A's cached sample differs from anything the preamble would draw now.
    a = _quotes(project).with_columns(pl.lit(-1.0).alias("r"))
    a1 = _publish(store, graph, "A", a)
    b1 = _publish(
        store,
        graph,
        "B",
        a.select("id", "a", "b", "y", pl.col("r").alias("rb")),
        dependencies={_identity(store, graph, "A").digest: a1},
    )

    run = _train(monkeypatch, graph)

    assert run.seeds == {"A": a1, "B": b1}
    assert run.frame["rb"].to_list() == [-1.0] * _ROWS
    assert run.frame["rc"].to_list() == [-1.0] * _ROWS


def test_single_cached_branch_after_ancestor_clear_recomputes(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _diamond(project)
    a = _quotes(project).with_columns(pl.lit(-1.0).alias("r"))
    a1 = _publish(store, graph, "A", a)
    _publish(
        store,
        graph,
        "B",
        a.select("id", "a", "b", "y", pl.col("r").alias("rb")),
        dependencies={_identity(store, graph, "A").digest: a1},
    )
    store.clear(_identity(store, graph, "A"))

    run = _train(monkeypatch, graph)

    # B's recorded A is gone, and C still needs an A: both branches recompute
    # from one fresh A, so every row matches itself.
    assert run.seeds == {}
    assert run.frame["rb"].null_count() == 0
    assert run.frame["rb"].to_list() == run.frame["rc"].to_list()
    assert run.frame["rb"][0] != -1.0


def test_paused_seeded_worker_survives_refresh_and_clear(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _chain(project)
    frames = _chain_frames(project)
    b1_rows = frames["B"].with_columns(pl.lit(7.0).alias("marker"))
    _publish(store, graph, "B", b1_rows)
    b_identity = _identity(store, graph, "B")

    def refresh_then_clear(_request: Any) -> None:
        # The child has not started: B is refreshed, then its slot cleared.
        _publish(
            store, graph, "B", frames["B"].with_columns(pl.lit(8.0).alias("marker")), refresh=True
        )
        store.clear_slot(_resolver(store, graph).node_output_slot("B"))
        assert store.latest_generation(b_identity) is None

    run = _train(monkeypatch, graph, before_child=refresh_then_clear)

    assert run.job["status"] == "running", run.job.get("message")
    assert run.frame["marker"].to_list() == [7.0] * _ROWS


def test_evaluation_preview_seeds_training_capture(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.schemas import TrainEstimateRequest

    graph = _chain(project, b_code="df = A.with_columns(pl.lit(1).alias('k1')).sort('y')")
    _train(monkeypatch, graph)
    b = store.latest_generation(_identity(store, graph, "B"))
    assert b is not None and b.columns == ALL
    calls = _counting_builds(monkeypatch)

    preview = TrainService(JobStore()).evaluation_preview(
        TrainEstimateRequest.model_validate({"graph": graph, "node_id": "train"}), row_limit=None
    )

    assert preview is not None
    assert not +calls
    after = store.latest_generation(_identity(store, graph, "B"))
    assert after is not None and after.generation_id == b.generation_id


def test_training_widens_evaluation_preview_capture(
    project: Path, store: NodeSnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.schemas import TrainEstimateRequest

    graph = _chain(project, b_code="df = A.with_columns(pl.lit(1).alias('k1')).sort('y')")
    TrainService(JobStore()).evaluation_preview(
        TrainEstimateRequest.model_validate({"graph": graph, "node_id": "train"}), row_limit=None
    )
    narrow = store.latest_generation(_identity(store, graph, "B"))
    assert narrow is not None and narrow.columns == NodeSnapshotColumns.of({"y"})

    run = _train(monkeypatch, graph)

    assert run.captures == {"B": "published"}
    widened = store.latest_generation(_identity(store, graph, "B"))
    assert widened is not None and widened.columns == ALL


def test_training_leaves_no_checkpoint_directory_or_namespace_entry(
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

    run = _train(monkeypatch, _diamond(project))

    assert run.job["status"] == "running", run.job.get("message")
    assert not [name for name in created if name.startswith("haute_train_ckpt_")]
    assert stored == []


@pytest.mark.parametrize("stopped", ["cancelled", "timed_out", "memory_limited"])
def test_terminated_training_worker_leaves_no_capture_staging(
    project: Path, monkeypatch: pytest.MonkeyPatch, stopped: str
) -> None:
    from haute._worker_isolation import (
        IsolatedWorkerMemoryLimitExceededError,
        IsolatedWorkerStoppedError,
        IsolatedWorkerTimeoutError,
    )

    graph = _chain(project)
    staged: list[Path] = []

    def staged_then_killed(request: Any, _budget: Any) -> Any:
        store = NodeSnapshotStore(request.seed_plan.project_root)
        artifact = store.stage_node_output(
            _identity(store, graph, "B"), staging_token=request.seed_plan.staging_token
        )
        pl.DataFrame({"a": [1]}).write_parquet(artifact.part_path(0))
        staged.append(artifact.directory)
        if stopped == "timed_out":
            raise IsolatedWorkerTimeoutError(timeout_seconds=1.0)
        if stopped == "memory_limited":
            raise IsolatedWorkerMemoryLimitExceededError(rss_bytes=2048, rss_limit_bytes=1024)
        raise IsolatedWorkerStoppedError(terminal_reason="cancelled")

    run = _train(monkeypatch, graph, child=staged_then_killed)

    assert len(staged) == 1
    assert not staged[0].exists()
    assert _staging(project) == []
    assert run.job["status"] != "running"


# ---------------------------------------------------------------------------
# The supervising parent: job deadline, failures before the child, evidence
# ---------------------------------------------------------------------------


class _Clock:
    """The supervisor's clock, moved on by however long a test says preparation took."""

    def __init__(self) -> None:
        self.offset = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.offset


def _preparation_taking(
    monkeypatch: pytest.MonkeyPatch,
    seconds: float,
    *,
    failure: Exception | None = None,
) -> list[float | None]:
    """Make the parent's plan opening take *seconds* of job time; return its deadlines."""
    clock = _Clock()
    monkeypatch.setattr(_training_lifecycle, "time", clock)
    open_plan = _training_lifecycle.open_seed_plan
    deadlines: list[float | None] = []

    def slow_open(*args: Any, **kwargs: Any) -> Any:
        deadlines.append(kwargs.get("deadline"))
        clock.offset += seconds
        if failure is not None:
            raise failure
        return open_plan(*args, **kwargs)

    monkeypatch.setattr(_training_lifecycle, "open_seed_plan", slow_open)
    return deadlines


def _assert_stopped_before_the_child(run: _Run, project: Path) -> None:
    assert run.worker_configs == []
    assert len(run.parquet_paths) == 1
    assert not Path(run.parquet_paths[0]).exists()
    assert run.released == ["training_pipeline"]
    assert _staging(project) == []


def test_preparation_time_comes_out_of_the_childs_budget(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deadlines = _preparation_taking(monkeypatch, 45.0)

    run = _train(monkeypatch, _chain(project), timeout=60)

    assert run.job["status"] == "running", run.job.get("message")
    [deadline] = deadlines
    assert deadline is not None
    assert deadline == pytest.approx(run.job["start_time"] + 60)
    [config] = run.worker_configs
    # 60 seconds less the 45 preparation took, less real time spent here.
    assert 10.0 < config.timeout_seconds <= 15.0


def test_preparation_past_the_job_deadline_times_out_without_the_child(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _preparation_taking(monkeypatch, 65.0)

    run = _train(monkeypatch, _chain(project), timeout=60)

    assert run.job["status"] == "timed_out"
    _assert_stopped_before_the_child(run, project)


def test_preparation_failing_after_the_job_deadline_is_the_jobs_timeout(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.errors import InputPreparationError

    _preparation_taking(
        monkeypatch,
        65.0,
        failure=InputPreparationError(
            "Preparing this Data Input's snapshot failed.",
            node_id="src",
            identity_digest="0" * 64,
            build_class="bounded",
            reason_code="timed_out",
            remediation="Run it again.",
        ),
    )

    run = _train(monkeypatch, _chain(project), timeout=60)

    assert run.job["status"] == "timed_out"
    _assert_stopped_before_the_child(run, project)


def _admission_refused() -> Exception:
    from haute._execution_admission import ExecutionAdmissionError

    return ExecutionAdmissionError(
        "training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_limit_bytes=1024,
        rss_at_admission_bytes=None,
        reason="no headroom",
    )


def _preparation_refused() -> Exception:
    from haute.errors import InputPreparationError

    return InputPreparationError(
        "This Data Input's source is unavailable and no published snapshot exists.",
        node_id="src",
        identity_digest="0" * 64,
        build_class="bounded",
        reason_code="build_failed",
        remediation="Restore the source.",
    )


def _corrupt_cache() -> Exception:
    from haute._source_cache import SourceCacheCorruptError

    return SourceCacheCorruptError("source-cache generation is corrupt")


def _cancelled() -> Exception:
    from haute._execution_context import ExecutionCancelledError

    return ExecutionCancelledError("input_snapshot_preparation")


@pytest.mark.parametrize(
    ("failure", "status", "error_code"),
    [
        pytest.param(_cancelled, "cancelled", None, id="cancelled"),
        pytest.param(
            _preparation_refused, "contract_error", "input_preparation_failed", id="contract"
        ),
        pytest.param(_corrupt_cache, "error", None, id="corrupt"),
        pytest.param(_admission_refused, "memory_limited", "memory_limit", id="admission"),
    ],
)
def test_parent_plan_failure_is_classified_without_the_child(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Callable[[], Exception],
    status: str,
    error_code: str | None,
) -> None:
    _preparation_taking(monkeypatch, 0.0, failure=failure())

    run = _train(monkeypatch, _chain(project))

    assert run.job["status"] == status, run.job.get("message")
    assert run.job.get("error_code") == error_code
    _assert_stopped_before_the_child(run, project)


def _csv_chain(project: Path) -> dict[str, Any]:
    """``src (CSV, snapshot-backed) → A → B → train``."""
    pl.read_parquet(project / "quotes.parquet").write_csv(project / "quotes.csv")
    graph = _chain(project, b_code="df = A.with_columns(pl.lit(1).alias('k1')).sort('id')")
    graph["nodes"][0]["data"]["config"] = {
        "inputType": "file",
        "format": "csv",
        "path": str(project / "quotes.csv"),
    }
    return graph


def test_job_metrics_keep_parent_preparation_with_child_evidence(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._input_preparation as input_preparation

    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    monkeypatch.setattr(
        input_preparation,
        "run_isolated_worker",
        lambda function, request, budget, **_: function(request, budget),
    )
    graph = _csv_chain(project)

    first = _train(monkeypatch, graph)
    second = _train(monkeypatch, graph)
    (project / "quotes.csv").unlink()
    third = _train(monkeypatch, graph)

    assert [run.job["status"] for run in (first, second, third)] == ["running"] * 3
    # Each job's metrics carry what the parent prepared and what the child
    # read or wrote.
    assert [
        (record["node_id"], record["action"], record["warning_code"])
        for run in (first, second, third)
        for record in run.metrics["input_preparation"]
    ] == [
        ("src", "built", None),
        ("src", "reused", None),
        ("src", "reused", "source_unavailable"),
    ]
    assert first.captures == {"B": "published"}
    assert set(second.seeds) == {"B"}
    assert third.captures == {"B": "published"}


def _train_to_completion(client: Any, graph: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = client.get(f"/api/modelling/train/status/{job_id}").json()
        if status["status"] != "running":
            return dict(status)
        time.sleep(0.05)
    raise TimeoutError(f"training job {job_id} did not finish")


def test_completed_training_job_keeps_preparation_evidence(project: Path) -> None:
    from fastapi.testclient import TestClient

    from haute.server import app

    client = TestClient(app, raise_server_exceptions=False)
    graph = _chain(project, b_code="df = A.with_columns(pl.lit(1).alias('k1')).sort('id')")

    first = _train_to_completion(client, graph)
    second = _train_to_completion(client, graph)

    assert first["status"] == second["status"] == "completed", (first, second)
    # The fit worker reports last; the job keeps what preparation read and wrote.
    assert [
        (capture["node_id"], capture["outcome"])
        for capture in first["execution_metrics"]["shared_snapshot_captures"]
    ] == [("B", "published")]
    assert [seed["node_id"] for seed in second["execution_metrics"]["shared_snapshot_seeds"]] == [
        "B"
    ]


def test_concurrent_training_workers_publish_each_capture_once(
    project: Path, store: NodeSnapshotStore
) -> None:
    """Two preparation workers in their own processes, both planned before either
    publishes, publish each captured identity once; the later one is superseded
    and continues from its own data."""
    import threading

    from haute._execution_admission import (
        create_admitted_execution_context,
        isolated_execution_budget,
    )
    from haute._seed_plans import open_seed_plan
    from haute._worker_isolation import run_isolated_worker, worker_config_for_memory_policy
    from haute.routes._training_preparation import (
        TrainingPreparationOutcome,
        TrainingPreparationRequest,
        _training_required_columns_by_node,
        create_training_parquet_path,
        training_seed_plan_request,
    )

    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("other", "dataInput", _data_input(project, "claims.parquet")),
            ("J", "polars", {"code": "df = src.join(other, on='id', validate='1:1')"}),
            (
                "B",
                "polars",
                {"code": "df = J.with_columns((pl.col('a') + pl.col('d')).alias('e')).sort('id')"},
            ),
        ],
        [("src", "J"), ("other", "J"), ("J", "B"), ("B", "train")],
    )
    pipeline = PipelineGraph.model_validate(graph)
    config = dict(pipeline.node_map["train"].data.config)
    required = _training_required_columns_by_node("train", config)
    contexts = []
    plans = []
    requests = []
    try:
        # Both plans are resolved before either worker starts: neither can seed
        # the other's capture, so both execute and both capture.
        for index in range(2):
            context = create_admitted_execution_context(
                operation="training_pipeline",
                profile=ExecutionProfile.TRAINING_PREP,
                job_id=f"race-{index}",
            )
            contexts.append(context)
            plan = open_seed_plan(
                training_seed_plan_request(pipeline, "train", "live", required),
                execution_context=context,
            )
            plans.append(plan)
            requests.append(
                TrainingPreparationRequest(
                    graph=pipeline,
                    node_id="train",
                    job_id=f"race-{index}",
                    source="live",
                    parquet_path=create_training_parquet_path(),
                    config=config,
                    project_root=str(project),
                    streaming_chunk_size=None,
                    row_limit=None,
                    exclude=None,
                    keep_columns=None,
                    required_columns_by_node=required,
                    preamble_supplied=True,
                    seed_plan=plan.handoff(),
                )
            )
        assert all(set(plan.decision.captures) == {"J", "B"} for plan in plans)
        outcomes: list[object] = [None, None]

        def prepare(index: int) -> None:
            budget = isolated_execution_budget(contexts[index])
            outcomes[index] = run_isolated_worker(
                prepare_training_data_worker,
                requests[index],
                budget,
                config=worker_config_for_memory_policy(
                    memory_limit_bytes=budget.memory_limit_bytes,
                    timeout_seconds=300.0,
                    stop_reason=lambda: None,
                    process_name=f"haute-training-prep-race-{index}",
                ),
            )

        workers = [threading.Thread(target=prepare, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=300)
        assert not any(worker.is_alive() for worker in workers)
    finally:
        for plan in plans:
            plan.close()
        for context in contexts:
            context.release_admission()

    frames = []
    captures_by_node: dict[str, list[str]] = {"J": [], "B": []}
    for outcome, request in zip(outcomes, requests, strict=True):
        assert isinstance(outcome, TrainingPreparationOutcome), outcome
        assert outcome.failure is None, outcome.failure
        assert outcome.execution_metrics is not None
        for capture in outcome.execution_metrics["shared_snapshot_captures"]:
            captures_by_node[capture["node_id"]].append(capture["outcome"])
        frames.append(pl.read_parquet(request.parquet_path))
        Path(request.parquet_path).unlink()
    # Each identity is published once; the later publisher is superseded.
    for node_id in ("J", "B"):
        assert sorted(captures_by_node[node_id]) == ["published", "superseded"], node_id
        identity = _identity(store, graph, node_id)
        generations = [
            path for path in store.identity_path(identity).glob("generations/*") if path.is_dir()
        ]
        assert len(generations) == 1, node_id
    assert_frame_equal(frames[0], frames[1], check_row_order=False)
    assert _staging(project) == []


def test_no_bounded_caller_creates_a_checkpoint_directory(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Training preparation, the evaluation preview, optimiser setup, and a Data
    Output write — every bounded caller — write no checkpoint directory, and the
    process dataframe cache stays empty."""
    import tempfile

    from haute._dataframe_execution_cache import DataFrameExecutionCache
    from haute.executor import write_data_output
    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.schemas import TrainEstimateRequest
    from tests.test_optimiser_seeding import _online_chain, _setup

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
    for profile in ("TRAINING", "OPTIMISER_SETUP", "AUTO_RANGE", "LAZY_SINK"):
        monkeypatch.setenv(f"HAUTE_{profile}_MEMORY_LIMIT_MB", "1024")
    # The optimiser reads its own scored quotes, beside the training data.
    optimiser_project = project / "optimiser"
    optimiser_project.mkdir()
    (optimiser_project / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "quote_id": [f"q{quote}" for quote in range(4) for _ in range(2)],
            "scenario_index": [0, 1] * 4,
            "scenario_value": [0.9, 1.1] * 4,
            "expected_income": [float(value) for value in range(8)],
            "volume": [1.0] * 8,
        }
    ).write_parquet(optimiser_project / "quotes.parquet")

    training_graph = _chain(project)
    training = _train(monkeypatch, training_graph)
    TrainService(JobStore()).evaluation_preview(
        TrainEstimateRequest.model_validate({"graph": training_graph, "node_id": "train"}),
        row_limit=None,
    )
    optimiser_graph = _online_chain(optimiser_project)
    _setup(monkeypatch, optimiser_graph, read=("D",))
    _setup(monkeypatch, optimiser_graph, read=("D",), profile=ExecutionProfile.AUTO_RANGE)
    output_graph = PipelineGraph.model_validate(
        {
            **training_graph,
            "nodes": [
                *(node for node in training_graph["nodes"] if node["id"] != "train"),
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "dataOutput",
                        "config": {
                            "outputType": "file",
                            "format": "parquet",
                            "path": str(project / "out.parquet"),
                        },
                    },
                },
            ],
            "edges": [
                *(edge for edge in training_graph["edges"] if edge["target"] != "train"),
                {"id": "e_out", "source": "B", "target": "out"},
            ],
        }
    )
    write_data_output(output_graph, "out", overwrite=True, project_root=project)

    assert training.job["status"] == "running", training.job.get("message")
    checkpoint_prefixes = (
        "haute_train_ckpt_",
        "haute_opt_",
        "haute_frontier_range_",
        "haute_sink_",
        "haute_dfexec_cache_",
    )
    assert [
        name
        for name in created
        if name.startswith(checkpoint_prefixes)
        and not name.startswith("haute_frontier_range_parts_")
    ] == []
    assert stored == []


def test_consumed_select_below_a_rating_step_is_captured_and_seeded(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("R", "ratingStep", {}),
            ("S", "polars", {"code": "df = R.select('id', 'a', 'b', 'y')"}),
        ],
        [("src", "R"), ("R", "S"), ("S", "train")],
    )
    first = _train(monkeypatch, graph)
    assert first.job["status"] == "running", first.job.get("message")
    assert "R" not in first.captures
    assert first.captures == {"S": "published"}
    assert first.calls["src"] == 1

    second = _train(monkeypatch, graph)
    assert set(second.seeds) == {"S"}
    assert second.captures == {}
    assert second.calls["src"] == 0
    assert_frame_equal(second.frame, first.frame)


def test_modelling_node_over_chunk_local_filter_writes_input_sliced(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": "df = src.filter(pl.col('a') >= 2)"}),
        ],
        [("src", "A"), ("A", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40)
    assert run.job["status"] == "running", run.job.get("message")
    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert validated.training_write_strategy == "input_sliced"
    assert validated.training_write_input_slices == 3
    assert validated.training_write_native_reason is None
    assert validated.training_write_blocking_operator is None
    expected = pl.read_parquet(project / "quotes.parquet").filter(pl.col("a") >= 2)
    assert_frame_equal(run.frame, expected)


def test_training_write_composes_column_exclusions(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": "df = src.filter(pl.col('a') >= 2)"}),
        ],
        [("src", "A"), ("A", "train")],
        modelling_config={"exclude": ["b"]},
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40)
    assert run.job["status"] == "running", run.job.get("message")
    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert validated.training_write_strategy == "input_sliced"
    assert validated.training_write_input_slices == 3
    assert validated.training_write_native_reason is None
    assert "b" not in run.frame.columns
    expected = pl.read_parquet(project / "quotes.parquet").filter(pl.col("a") >= 2).drop("b")
    assert_frame_equal(run.frame, expected)


def test_training_write_under_row_limit_takes_native_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": "df = src.filter(pl.col('a') >= 2)"}),
        ],
        [("src", "A"), ("A", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40, row_limit=50)
    assert run.job["status"] == "running", run.job.get("message")
    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert validated.training_write_strategy == "native"
    assert validated.training_write_native_reason == "row_limit_sample"
    assert validated.training_write_input_slices is None
    assert len(run.frame) == 50


def test_mismatched_write_recipe_degrades_to_native_with_warning(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_execute = _training_preparation.execute_lazy_graph

    def execute_with_mismatched_recipe(*args: Any, **kwargs: Any) -> Any:
        res = real_execute(*args, **kwargs)
        write_recipes = kwargs.get("write_recipes")
        if write_recipes is not None and "train" in write_recipes:
            orig = write_recipes["train"]
            write_recipes["train"] = WriteRecipe(
                input=orig.input,
                fn=lambda lf: lf.filter(pl.col("id") > 0),
            )
        return res

    monkeypatch.setattr(_training_preparation, "execute_lazy_graph", execute_with_mismatched_recipe)

    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": "df = src.filter(pl.col('a') >= 2)"}),
        ],
        [("src", "A"), ("A", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40)
    assert run.job["status"] == "running", run.job.get("message")
    expected = pl.read_parquet(project / "quotes.parquet").filter(pl.col("a") >= 2)
    assert_frame_equal(run.frame, expected)

    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert validated.training_write_strategy == "native"
    assert validated.training_write_native_reason == "recipe_mismatch"
    assert validated.training_write_input_slices is None
    assert any(w.code == "recipe_mismatch" for w in validated.warnings)


def test_write_failure_that_is_not_a_mismatch_fails_the_job(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-write failure must not be swallowed and rewritten over.

    The seam catches ``RecipeEquivalenceError`` alone, because that check runs
    before a byte is written. Any other failure may have left a part-written
    file, so degrading to a native rewrite would hide it. Catching ``Exception``
    instead would turn this job green.
    """
    from haute import _chunked_writes

    real_write_file = _chunked_writes.write_file
    attempts: list[int] = []

    def explode_once(*args: Any, **kwargs: Any) -> Any:
        # Only the first attempt dies. The degrade path writes again with no
        # recipe, so a broad catch would quietly succeed here and the job would
        # come back green — which is exactly what this test must not allow.
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("the writer died half way")
        return real_write_file(*args, **kwargs)

    monkeypatch.setattr(_chunked_writes, "write_file", explode_once)

    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": "df = src.filter(pl.col('a') >= 2)"}),
        ],
        [("src", "A"), ("A", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40)
    assert run.job["status"] == "error", run.job.get("message")
    assert not Path(run.parquet_paths[0]).exists()


def test_modelling_node_directly_off_data_input_writes_sliced(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
        ],
        [("src", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40)
    assert run.job["status"] == "running", run.job.get("message")
    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert validated.training_write_strategy == "sliced"
    assert validated.training_write_native_reason is None
    assert validated.training_write_blocking_operator is None
    expected = pl.read_parquet(project / "quotes.parquet")
    assert_frame_equal(run.frame, expected)


def test_sliceable_sampled_frame_records_no_native_reason(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _training_preparation,
        "_seeded_training_sample",
        lambda lf, limit: lf.slice(0, limit),
    )
    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
        ],
        [("src", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40, row_limit=50)
    assert run.job["status"] == "running", run.job.get("message")
    validated = ExecutionMetricsPayload.model_validate(run.metrics)
    assert validated.training_write_strategy == "sliced"
    assert validated.training_write_native_reason is None
    assert len(run.frame) == 50


def test_training_write_records_discarded_reason_only_for_native_strategy() -> None:
    from haute._chunked_writes import ChunkedWrite

    context = ExecutionContext(
        operation="test",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_limit_bytes=1024 * 1024,
    )
    context.record_training_write(
        ChunkedWrite(strategy="sliced", parts=(), chunks=1, staged_inputs=0),
        native_reason="row_limit_sample",
    )
    assert context.metrics_payload()["training_write_strategy"] == "sliced"
    assert context.metrics_payload()["training_write_native_reason"] is None

    context.record_training_write(
        ChunkedWrite(strategy="input_sliced", parts=(), chunks=1, staged_inputs=0),
        native_reason="row_limit_sample",
    )
    assert context.metrics_payload()["training_write_strategy"] == "input_sliced"
    assert context.metrics_payload()["training_write_native_reason"] is None

    context.record_training_write(
        ChunkedWrite(strategy="native", parts=(), chunks=1, staged_inputs=0),
        native_reason="row_limit_sample",
    )
    assert context.metrics_payload()["training_write_strategy"] == "native"
    assert context.metrics_payload()["training_write_native_reason"] == "row_limit_sample"


def test_cancellation_mid_write_leaves_no_prepared_parquet(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig_record_chunk = ExecutionContext.record_chunk

    def cancel_during_chunk(self: ExecutionContext, *args: Any, **kwargs: Any) -> Any:
        orig_record_chunk(self, *args, **kwargs)
        self.cancel()

    monkeypatch.setattr(ExecutionContext, "record_chunk", cancel_during_chunk)

    graph = _graph(
        project,
        [
            ("src", "dataInput", _data_input(project, "quotes.parquet")),
            ("A", "polars", {"code": "df = src.filter(pl.col('a') >= 2)"}),
        ],
        [("src", "A"), ("A", "train")],
    )
    run = _train(monkeypatch, graph, streaming_chunk_size=40)
    # A cancelled run reports as cancelled, not as a pipeline failure pointing
    # the user at the server logs, and leaves nothing where the parquet would be.
    assert run.job["status"] == "cancelled", run.job.get("message")
    assert len(run.parquet_paths) >= 1
    for path in run.parquet_paths:
        assert not Path(path).exists()
