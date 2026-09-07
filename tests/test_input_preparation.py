"""Automatic snapshot preparation planned before an execution runs."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import polars as pl
import polars.testing as plt
import pytest

from haute._execution_admission import (
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._execution_schemas import ExecutionMetricsPayload
from haute._input_preparation import (
    InputPreparationOutcome,
    InputPreparationRecord,
    build_input_snapshot_worker,
    prepare_input_snapshots,
)
from haute._input_providers import build_input_snapshot, resolve_data_input
from haute._native_memory_limit import native_memory_backend_scope
from haute._polars_io_registry import PolarsIoConfigError
from haute._sandbox import set_project_root
from haute._source_cache import SourceCacheStore
from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    process_memory_caps_supported,
)
from haute.errors import InputPreparationError
from tests.conftest import make_edge, make_graph, make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _node(node_id: str, node_type: str, config: dict[str, object]) -> dict[str, object]:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": node_type, "config": config},
    }


def _graph(config: dict[str, object], fields: list[str]) -> Any:
    return make_graph(
        {
            "nodes": [
                _node("input", "dataInput", config),
                _node("out", "output", make_output_config(fields)),
            ],
            "edges": [make_edge("input", "out").model_dump()],
        }
    )


def _context(profile: ExecutionProfile = ExecutionProfile.LAZY_SINK) -> ExecutionContext:
    return create_admitted_execution_context(operation="input_preparation_test", profile=profile)


def _prepare(
    config: dict[str, object],
    *,
    store: SourceCacheStore,
    base_dir: Path,
    context: ExecutionContext | None,
    schema_only: bool = False,
    spawn: Any = None,
    fields: list[str] | None = None,
) -> tuple[InputPreparationRecord, ...]:
    graph = _graph(config, fields or ["id"])
    return prepare_input_snapshots(
        ["input"],
        graph.node_map,
        profile=context.profile if context is not None else None,
        execution_context=context,
        base_dir=base_dir,
        schema_only=schema_only,
        store=store,
        spawn=spawn,
    )


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    retire_grace_seconds: float | None = None,
) -> SourceCacheStore:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    store = SourceCacheStore(tmp_path)
    if retire_grace_seconds is not None:
        # The sandbox fixture wraps the store constructor, so the retirement
        # grace these tests need is stated on the instance instead.
        store.retire_grace_seconds = retire_grace_seconds
    return store


def _csv_config(path: Path, **extra: object) -> dict[str, object]:
    return {"inputType": "file", "format": "csv", "path": str(path), **extra}


# ---------------------------------------------------------------- (1) parity


def _parity_cases(tmp_path: Path) -> list[tuple[dict[str, object], pl.DataFrame]]:
    frame = pl.DataFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]})
    declared = tmp_path / "declared.csv"
    frame.write_csv(declared)
    inferred = tmp_path / "inferred.csv"
    frame.write_csv(inferred)
    ndjson = tmp_path / "rows.ndjson"
    frame.write_ndjson(ndjson)
    plain = tmp_path / "rows.json"
    frame.write_json(plain)
    return [
        (
            _csv_config(declared, arguments={"schema": {"id": "Int64", "value": "String"}}),
            frame,
        ),
        (_csv_config(inferred), frame),
        ({"inputType": "file", "format": "ndjson", "path": str(ndjson)}, frame),
        ({"inputType": "file", "format": "json", "mode": "read", "path": str(plain)}, frame),
        (
            {
                "inputType": "inline",
                "format": "records",
                "records": frame.to_dicts(),
            },
            frame,
        ),
    ]


def test_prepared_generation_matches_a_direct_whole_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    for index, (config, expected) in enumerate(_parity_cases(tmp_path)):
        context = _context()
        try:
            with native_memory_backend_scope("rlimit"):
                records = _prepare(
                    config, store=store, base_dir=tmp_path, context=context, fields=["id", "value"]
                )
        finally:
            context.release_admission()
        assert [record.action for record in records] == ["built"], index
        prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
        plt.assert_frame_equal(prepared, expected)


# ------------------------------------------------------- (2) mixed late types


def test_late_typed_csv_rows_are_inferred_from_the_whole_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "late.csv"
    rows = [str(value) for value in range(2_000)] + ["late-text"]
    path.write_text("id\n" + "\n".join(rows) + "\n", encoding="utf-8")
    config = _csv_config(path)

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            _prepare(config, store=store, base_dir=tmp_path, context=context)
    finally:
        context.release_admission()

    prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert prepared.schema["id"] == pl.String
    assert prepared.height == 2_001
    assert prepared["id"][-1] == "late-text"


# ------------------------------------------------------ (3) malformed records


def test_a_ragged_csv_row_fails_the_build_and_keeps_the_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "ragged.csv"
    path.write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)

    path.write_text("id,amount\n1,10\n2,20\n3,30,extra\n", encoding="utf-8")
    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            with pytest.raises(InputPreparationError) as excinfo:
                _prepare(config, store=store, base_dir=tmp_path, context=context)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "build_failed"
    assert store.open_generation(store_identity(config, tmp_path)).generation_id == (
        first.generation_id
    )
    identity_dir = store.identity_path(store_identity(config, tmp_path))
    assert not list(identity_dir.glob(".staging-*"))


def test_a_source_truncated_to_zero_bytes_fails_the_refresh_and_keeps_the_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "zero.csv"
    frame = pl.DataFrame({"id": [1, 2], "amount": [10, 20]})
    frame.write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)

    path.write_bytes(b"")
    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            with pytest.raises(InputPreparationError) as excinfo:
                _prepare(config, store=store, base_dir=tmp_path, context=context)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "build_failed"
    assert "Preparing this Data Input's snapshot failed." in str(excinfo.value)
    assert "Traceback" not in str(excinfo.value)
    assert store.open_generation(store_identity(config, tmp_path)).generation_id == (
        first.generation_id
    )
    identity_dir = store.identity_path(store_identity(config, tmp_path))
    assert not list(identity_dir.glob(".staging-*"))
    prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    plt.assert_frame_equal(prepared, frame)


def test_a_header_only_source_publishes_an_empty_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "header_only.csv"
    frame = pl.DataFrame({"id": [1, 2], "amount": [10, 20]})
    frame.write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)

    path.write_text("id,amount\n", encoding="utf-8")
    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            records = _prepare(config, store=store, base_dir=tmp_path, context=context)
    finally:
        context.release_admission()

    assert len(records) == 1
    record = records[0]
    assert record.action == "refreshed"
    assert record.row_count == 0
    assert record.generation_id != first.generation_id

    current_gen = store.open_generation(store_identity(config, tmp_path))
    assert current_gen.generation_id == record.generation_id
    assert current_gen.generation_id != first.generation_id

    prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert prepared.height == 0
    assert prepared.columns == ["id", "amount"]


def store_identity(config: dict[str, object], base_dir: Path) -> Any:
    from haute._input_providers import source_cache_identity

    return source_cache_identity(config, base_dir=base_dir)


# -------------------------------------------------------- (4) source mutation


def test_reuse_refresh_and_rebuild_follow_the_source_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    def prepare() -> InputPreparationRecord:
        context = _context()
        try:
            with native_memory_backend_scope("rlimit"):
                return _prepare(config, store=store, base_dir=tmp_path, context=context)[0]
        finally:
            context.release_admission()

    assert prepare().action == "built"

    poisoned = store.build

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a fresh generation must not be rebuilt")

    monkeypatch.setattr(store, "build", refuse)
    assert prepare().action == "reused"
    monkeypatch.setattr(store, "build", poisoned)

    pl.DataFrame({"id": [1, 2, 3, 4]}).write_csv(path)
    refreshed = prepare()
    assert refreshed.action == "refreshed"
    assert resolve_data_input(config, store=store, base_dir=tmp_path).collect().height == 4

    store.clear(store_identity(config, tmp_path))
    assert prepare().action == "built"


# --------------------------------------------------- (5) concurrent execution


def test_two_concurrent_executions_share_one_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)

    builds: list[str] = []
    real_build = store.build

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        builds.append("build")
        time.sleep(0.05)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(store, "build", counting_build)

    results: list[InputPreparationRecord] = []
    errors: list[BaseException] = []

    # One admitted envelope, two executions: the single-flight is keyed by the
    # snapshot identity, not by the requesting context.
    context = _context()

    def run() -> None:
        try:
            with native_memory_backend_scope("rlimit"):
                results.append(_prepare(config, store=store, base_dir=tmp_path, context=context)[0])
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        context.release_admission()

    assert not errors
    assert len(builds) == 1
    assert len({record.generation_id for record in results}) == 1


# ------------------------------------------------ (6)/(7) cancel and timeout


def test_a_cancelled_build_is_typed_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)

    context = _context()
    context.cancellation_token.cancel()
    try:
        with native_memory_backend_scope("rlimit"):
            with pytest.raises(InputPreparationError) as excinfo:
                _prepare(config, store=store, base_dir=tmp_path, context=context)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "cancelled"
    identity_dir = store.identity_path(store_identity(config, tmp_path))
    assert not list(identity_dir.glob(".staging-*"))
    assert not (identity_dir / "current.json").exists()


def test_a_build_deadline_in_the_past_is_reported_as_timed_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)
    monkeypatch.setattr(
        "haute._input_preparation._build_deadline",
        lambda: time.monotonic() - 1.0,
    )

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            with pytest.raises(InputPreparationError) as excinfo:
                _prepare(config, store=store, base_dir=tmp_path, context=context)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "timed_out"
    identity_dir = store.identity_path(store_identity(config, tmp_path))
    assert not list(identity_dir.glob(".staging-*"))


# ----------------------------------------------- (8)/(14) worker kill windows


class _FakeSpawn:
    """Perform the store's own build steps and stop at a chosen point."""

    def __init__(
        self,
        store: SourceCacheStore,
        base_dir: Path,
        *,
        stop_after: str,
        failure: BaseException,
    ) -> None:
        self.store = store
        self.base_dir = base_dir
        self.stop_after = stop_after
        self.failure = failure
        self.budget: IsolatedExecutionBudget | None = None
        self.config: IsolatedWorkerConfig | None = None
        self.request: Any = None

    def __call__(self, function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        self.budget = budget
        self.config = config
        self.request = request
        identity = store_identity(request.config, self.base_dir)
        identity_dir = self.store.identity_path(identity)
        staging = identity_dir / f".staging-{request.staging_token}"
        if self.stop_after == "nothing":
            raise self.failure
        staging.mkdir(parents=True)
        (staging / "data.parquet").write_bytes(b"partial")
        if self.stop_after == "staging":
            raise self.failure
        generation_dir = identity_dir / "generations" / request.generation_id
        generation_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(generation_dir)
        if self.stop_after == "renamed":
            raise self.failure
        (identity_dir / "current.json").write_text(
            json.dumps(
                {"identity_digest": identity.digest, "generation_id": request.generation_id},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        raise self.failure


def _memory_failure() -> BaseException:
    return IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=1024)


def _child_store(root: Path) -> SourceCacheStore:
    """A store handle standing in for a spawned child's own process.

    Handles sharing a cache root share process-local lease counts, so a child
    process is simulated by giving this handle its own empty lease table: the
    parent's leases are invisible to it, exactly as across a process boundary.
    """
    child = SourceCacheStore(root)
    child._leases = {}
    return child


def _real_child_build(request: Any, budget: Any) -> InputPreparationOutcome:
    """Run the production child entry point against a child-local store handle.

    Only the store handle is swapped, so the child's own
    ``defer_retirement=True`` wiring is the thing under test.
    """
    import haute._input_preparation as preparation_module

    original = preparation_module.SourceCacheStore
    preparation_module.SourceCacheStore = (  # type: ignore[misc]
        lambda cache_root: _child_store(Path(cache_root))
    )
    try:
        outcome = build_input_snapshot_worker(request, budget)
    finally:
        preparation_module.SourceCacheStore = original  # type: ignore[misc]
    assert isinstance(outcome, InputPreparationOutcome)
    return outcome


class _RealRefreshSpawn(_FakeSpawn):
    """Spawn stand-in that runs the production child entry point in-process."""

    def __call__(self, function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        self.request = request
        return _real_child_build(request, budget)


def test_a_spawned_build_never_retires_a_generation_the_parent_still_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch, retire_grace_seconds=0)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    identity = store_identity(config, tmp_path)
    old_dir = store.identity_path(identity) / "generations" / first.generation_id

    spawn = _RealRefreshSpawn(
        store,
        tmp_path,
        stop_after="published",
        failure=RuntimeError("unused"),
    )
    with store.lease(identity) as leased:
        assert leased.generation_id == first.generation_id
        context = _context()
        try:
            record = _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)[
                0
            ]
        finally:
            context.release_admission()
        assert record.action == "refreshed"
        assert record.generation_id != first.generation_id
        # The child deferred retirement, so the generation this process leases
        # survives its refresh and still reads.
        assert old_dir.is_dir()
        assert pl.scan_parquet(leased.data_path).collect().height == 2

    # The final lease release retires it with the parent's own lease counts.
    store.retire_unleased(identity)
    assert not old_dir.exists()
    assert store.open_generation(identity).metadata.row_count == 3


def test_the_parent_retires_the_superseded_generation_after_a_spawned_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch, retire_grace_seconds=0)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    identity = store_identity(config, tmp_path)
    old_dir = store.identity_path(identity) / "generations" / first.generation_id

    spawn = _RealRefreshSpawn(
        store,
        tmp_path,
        stop_after="published",
        failure=RuntimeError("unused"),
    )
    context = _context()
    try:
        record = _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)[0]
    finally:
        context.release_admission()

    # Nothing leases the old generation, so the parent's own retirement pass
    # removes what the child deliberately left behind.
    assert record.action == "refreshed"
    assert not old_dir.exists()
    assert store.open_generation(identity).generation_id == record.generation_id


def test_a_spawned_refresh_cannot_exceed_the_quota_while_the_parent_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child treats the parent's leased generations as retained, not reclaimable."""
    monkeypatch.setenv("HAUTE_INPUT_CACHE_MAX_GENERATIONS", "1")
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    identity = store_identity(config, tmp_path)
    generations = store.identity_path(identity) / "generations"
    spawn = _RealRefreshSpawn(store, tmp_path, stop_after="published", failure=RuntimeError("x"))

    with store.lease(identity) as leased:
        assert leased.generation_id == first.generation_id
        context = _context()
        try:
            with pytest.raises(InputPreparationError) as excinfo:
                _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
        finally:
            context.release_admission()
        assert excinfo.value.reason_code == "quota_exceeded"
        # Nothing was published: the leased generation is still current and readable.
        assert [child.name for child in generations.iterdir()] == [first.generation_id]
        assert store.open_generation(identity).generation_id == first.generation_id
        assert pl.scan_parquet(leased.data_path).collect().height == 2


def test_a_spawned_refresh_publishes_at_the_quota_without_a_parent_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a parent lease the same refresh publishes and the parent retires."""
    monkeypatch.setenv("HAUTE_INPUT_CACHE_MAX_GENERATIONS", "1")
    store = _project(tmp_path, monkeypatch, retire_grace_seconds=0)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    identity = store_identity(config, tmp_path)
    generations = store.identity_path(identity) / "generations"
    spawn = _RealRefreshSpawn(store, tmp_path, stop_after="published", failure=RuntimeError("x"))
    context = _context()
    try:
        record = _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)[0]
    finally:
        context.release_admission()

    assert record.action == "refreshed"
    assert record.generation_id != first.generation_id
    assert [child.name for child in generations.iterdir()] == [record.generation_id]
    assert store.open_generation(identity).metadata.row_count == 3


def test_a_memory_limited_worker_reconciles_its_staging_and_keeps_the_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    spawn = _FakeSpawn(store, tmp_path, stop_after="staging", failure=_memory_failure())
    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "memory_limited"
    identity = store_identity(config, tmp_path)
    assert spawn.request is not None
    staging = store.identity_path(identity) / f".staging-{spawn.request.staging_token}"
    assert not staging.exists()
    assert store.open_generation(identity).generation_id == first.generation_id


def test_a_worker_that_dies_after_the_rename_leaves_no_unreferenced_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    spawn = _FakeSpawn(
        store,
        tmp_path,
        stop_after="renamed",
        failure=IsolatedWorkerCrashedError(exitcode=1, memory_limit_bytes=None),
    )
    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "build_failed"
    identity = store_identity(config, tmp_path)
    generations = store.identity_path(identity) / "generations"
    assert spawn.request is not None
    assert not (generations / spawn.request.generation_id).exists()
    assert store.open_generation(identity).generation_id == first.generation_id


def test_a_worker_that_dies_after_publication_is_reconciled_as_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    class _PublishedSpawn(_FakeSpawn):
        def __call__(self, function: Any, request: Any, budget: Any, *, config: Any) -> Any:
            self.request = request
            # The child really publishes the parent-chosen generation, then dies
            # before its outcome can reach the parent.
            _real_child_build(request, budget)
            raise self.failure

    # The pointer names the parent-chosen generation, so reconciliation reports
    # ``published`` even though the worker died before returning its outcome.
    spawn = _PublishedSpawn(store, tmp_path, stop_after="published", failure=_memory_failure())
    context = _context()
    try:
        record = _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)[0]
    finally:
        context.release_admission()

    identity = store_identity(config, tmp_path)
    assert spawn.request is not None
    assert record.action == "refreshed"
    assert record.generation_id == spawn.request.generation_id
    assert record.generation_id != first.generation_id
    assert record.row_count == 3
    assert store.open_generation(identity).generation_id == record.generation_id
    generation_dir = store.identity_path(identity) / "generations" / record.generation_id
    assert (generation_dir / "meta.json").is_file()
    assert (generation_dir / "data.parquet").is_file()


def test_a_successor_published_by_another_process_is_reused_not_reported_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)

    other = SourceCacheStore(tmp_path)

    class _SupersededSpawn(_FakeSpawn):
        def __call__(self, function: Any, request: Any, budget: Any, *, config: Any) -> Any:
            self.request = request
            # Another process publishes a fresh generation before the parent
            # reconciles this dead worker's own identifiers.
            build_input_snapshot(
                request.config,
                store=other,
                base_dir=self.base_dir,
                refresh=True,
            )
            raise self.failure

    spawn = _SupersededSpawn(store, tmp_path, stop_after="nothing", failure=_memory_failure())
    context = _context()
    try:
        record = _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)[0]
    finally:
        context.release_admission()

    assert record.action == "reused"
    assert resolve_data_input(config, store=store, base_dir=tmp_path).collect().height == 3


# ----------------------------------------------------------- (9)/(11) skipped


def test_schema_only_executions_never_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    monkeypatch.setattr(
        store,
        "build",
        lambda *args, **kwargs: pytest.fail("schema-only preparation must not build"),
    )

    context = _context()
    try:
        assert (
            _prepare(config, store=store, base_dir=tmp_path, context=context, schema_only=True)
            == ()
        )
    finally:
        context.release_admission()

    with pytest.raises(PolarsIoConfigError, match="^input_snapshot_missing:"):
        resolve_data_input(config, store=store, base_dir=tmp_path)


def test_a_call_without_an_admitted_context_does_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    assert _prepare(config, store=store, base_dir=tmp_path, context=None) == ()
    with pytest.raises(PolarsIoConfigError, match="^input_snapshot_missing:"):
        resolve_data_input(config, store=store, base_dir=tmp_path)


# ---------------------------------------------------------------- (10) placement


def test_the_worker_path_receives_the_budget_and_a_required_memory_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")

    captured: dict[str, Any] = {}

    def spawn(function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        captured["function"] = function
        captured["budget"] = budget
        captured["config"] = config
        return build_input_snapshot_worker(request, budget)

    context = _context()
    try:
        expected = isolated_execution_budget(context)
        record = _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)[0]
    finally:
        context.release_admission()

    assert captured["function"] is build_input_snapshot_worker
    assert captured["budget"].memory_limit_bytes == expected.memory_limit_bytes
    assert captured["config"].require_memory_limit is True
    assert record.execution == "worker"
    assert record.action == "built"


def test_the_in_process_path_is_taken_under_a_declared_native_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("an in-process build must not spawn")

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            record = _prepare(
                config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn
            )[0]
        stages = context.metrics_payload()["stage_elapsed_ms"]
    finally:
        context.release_admission()

    assert record.execution == "in_process"
    assert "input_snapshot_read" in stages
    assert "input_snapshot_write" in stages


# ------------------------------------------------------------- (12)/(13) diagnostics


def test_terminal_diagnostics_carry_digests_and_counts_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)

    emitted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "haute._input_preparation.logger",
        type(
            "_Recorder",
            (),
            {"warning": staticmethod(lambda event, **fields: emitted.append((event, fields)))},
        )(),
    )

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            _prepare(config, store=store, base_dir=tmp_path, context=context)
        payload = context.metrics_payload()
    finally:
        context.release_admission()

    records = payload["input_preparation"]
    assert isinstance(records, list) and len(records) == 1
    entry = records[0]
    assert entry["node_id"] == "input"
    assert entry["action"] == "built"
    assert entry["build_class"] == "bounded"
    assert entry["execution"] == "in_process"
    assert entry["row_count"] == 3
    assert entry["size_bytes"] > 0
    assert str(path) not in json.dumps(records)

    validated = ExecutionMetricsPayload.model_validate(payload)
    assert validated.input_preparation[0].identity_digest == entry["identity_digest"]

    auto_build = [fields for event, fields in emitted if event == "input_snapshot_auto_build"]
    assert len(auto_build) == 1
    assert auto_build[0]["identity_digest"] == entry["identity_digest"]
    assert auto_build[0]["build_class"] == "bounded"


# -------------------------------------------------------------- (15) cap gate


def test_a_host_without_a_native_cap_refuses_before_any_provider_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    monkeypatch.setattr(
        "haute._input_preparation.process_memory_caps_supported",
        lambda: False,
    )

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("preparation must refuse before spawning")

    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "cap_unavailable"
    assert not (store.identity_path(store_identity(config, tmp_path)) / "current.json").exists()


# ------------------------------------------------------- (16) cache invalidation


def test_a_rewritten_source_changes_the_runtime_input_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.execution import dataframe_graph_input_fingerprint

    _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    graph = _graph(config, ["id"])

    before = dataframe_graph_input_fingerprint(graph, target_node_id="out", source="live")
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    after = dataframe_graph_input_fingerprint(graph, target_node_id="out", source="live")

    assert before != after


def test_a_warm_preview_returns_the_new_rows_after_the_source_is_rewritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.executor import execute_graph

    _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    graph = _graph(config, ["id"])

    with native_memory_backend_scope("rlimit"):
        first = execute_graph(graph, target_node_id="input")
        assert first["input"].row_count == 2
        pl.DataFrame({"id": [1, 2, 3, 4]}).write_csv(path)
        second = execute_graph(graph, target_node_id="input")

    assert second["input"].row_count == 4


def test_a_stopped_worker_is_classified_as_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    spawn = _FakeSpawn(
        store,
        tmp_path,
        stop_after="nothing",
        failure=IsolatedWorkerStoppedError(terminal_reason="cancelled"),
    )
    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "cancelled"


# --------------------------------------------- (4) cancellable spawn and waiters


def test_a_spawned_build_is_stopped_when_the_execution_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    entered = threading.Event()

    def spawn(function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        # Stand in for the supervisor loop: block until the configured stop
        # reason reports the parent's cancellation, then stop the child.
        entered.set()
        while True:
            reason = config.stop_reason()
            if reason is not None:
                raise IsolatedWorkerStoppedError(terminal_reason=reason)
            time.sleep(0.01)

    context = _context()

    def cancel_when_running() -> None:
        entered.wait(30)
        context.cancellation_token.cancel()

    canceller = threading.Thread(target=cancel_when_running)
    canceller.start()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        canceller.join(30)
        context.release_admission()

    assert excinfo.value.reason_code == "cancelled"


def test_a_single_flight_waiter_is_cancelled_instead_of_blocking_forever(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._input_preparation import _acquire_single_flight, _release_single_flight

    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    digest = store_identity(config, tmp_path).digest

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("a waiter must never start its own build")

    # Another execution in this process owns the in-flight slot and never finishes.
    assert _acquire_single_flight(digest) is None
    context = _context()
    canceller = threading.Timer(0.2, lambda: context.cancellation_token.cancel())
    canceller.start()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn)
    finally:
        canceller.cancel()
        _release_single_flight(digest)
        context.release_admission()

    assert excinfo.value.reason_code == "cancelled"


# ------------------------------------------- (5) remote worker failure classification


def _remote_error(remote_type: str) -> IsolatedWorkerRemoteError:
    return IsolatedWorkerRemoteError(
        remote_type=remote_type,
        remote_message=f"{remote_type} raised in the child",
        remote_traceback="Traceback (most recent call last):\n",
    )


@pytest.mark.parametrize(
    ("remote_type", "expected"),
    [
        ("SourceCacheQuotaExceededError", "quota_exceeded"),
        ("NativeMemoryLimitUnsupportedError", "cap_unavailable"),
        ("NativeMemoryLimitCleanupError", "cap_unavailable"),
        ("MemoryError", "memory_limited"),
        ("ValueError", "build_failed"),
    ],
)
def test_a_remote_worker_failure_is_classified_by_the_childs_own_exception_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_type: str,
    expected: str,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    spawn = _FakeSpawn(
        store,
        tmp_path,
        stop_after="nothing",
        failure=_remote_error(remote_type),
    )
    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == expected


def test_each_preparation_reason_code_has_its_own_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    remediations: dict[str, str] = {}
    for remote_type, reason in (
        ("SourceCacheQuotaExceededError", "quota_exceeded"),
        ("NativeMemoryLimitUnsupportedError", "cap_unavailable"),
        ("MemoryError", "memory_limited"),
        ("ValueError", "build_failed"),
    ):
        spawn = _FakeSpawn(
            store,
            tmp_path,
            stop_after="nothing",
            failure=_remote_error(remote_type),
        )
        context = _context()
        try:
            with pytest.raises(InputPreparationError) as excinfo:
                _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
        finally:
            context.release_admission()
        assert excinfo.value.reason_code == reason
        remediations[reason] = excinfo.value.remediation

    assert len(set(remediations.values())) == len(remediations)
    assert "quota" in remediations["quota_exceeded"]
    assert "native memory cap" in remediations["cap_unavailable"]
    assert "memory" in remediations["memory_limited"]


# ------------------------------------- (8) lazy engine and dataframe cache scenarios


def _sorted_graph(config: dict[str, object]) -> Any:
    return make_graph(
        {
            "nodes": [
                _node("input", "dataInput", config),
                _node("sorted", "polars", {"code": "df = input.sort('id', descending=True)"}),
                _node("out", "output", make_output_config(["id"])),
            ],
            "edges": [
                make_edge("input", "sorted").model_dump(),
                make_edge("sorted", "out").model_dump(),
            ],
        }
    )


def _collect(frame: Any) -> pl.DataFrame:
    return frame.collect() if isinstance(frame, pl.LazyFrame) else frame


def test_a_lazy_run_builds_a_missing_generation_before_the_strategy_is_estimated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._builders import _build_node_fn
    from haute.execution import execute_lazy_graph

    _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [3, 1, 2]}).write_csv(path)
    graph = _sorted_graph(_csv_config(path))

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            outputs, *_ = execute_lazy_graph(
                graph,
                _build_node_fn,
                target_node_id="sorted",
                execution_context=context,
            )
            collected = _collect(outputs["sorted"])
        payload = context.metrics_payload()
        strategy = context.projection_plan
    finally:
        context.release_admission()

    assert collected["id"].to_list() == [3, 2, 1]
    assert [entry["action"] for entry in payload["input_preparation"]] == ["built"]
    # The build ran before estimation, so the strategy could size a real snapshot.
    assert strategy.diagnostic.estimated_peak_bytes is not None


def test_a_schema_only_lazy_run_never_builds_and_reports_the_missing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._builders import _build_node_fn
    from haute.execution import execute_lazy_graph

    _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [3, 1, 2]}).write_csv(path)
    graph = _sorted_graph(_csv_config(path))

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            with pytest.raises(PolarsIoConfigError, match="^input_snapshot_missing:"):
                execute_lazy_graph(
                    graph,
                    _build_node_fn,
                    target_node_id="sorted",
                    execution_context=context,
                    schema_only=True,
                )
        payload = context.metrics_payload()
    finally:
        context.release_admission()

    assert payload.get("input_preparation") in (None, [])


def test_a_warmed_dataframe_cache_returns_the_new_rows_after_a_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import execution as execution_facade
    from haute._builders import _build_node_fn

    _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    graph = _sorted_graph(_csv_config(path))
    cache = execution_facade.DataFrameExecutionCache(
        root=tmp_path / "df-cache",
        max_entries=8,
        max_bytes=10_000_000,
    )
    actions: list[list[str]] = []

    def run() -> pl.DataFrame:
        context = _context()
        try:
            with native_memory_backend_scope("rlimit"):
                request = execution_facade.build_dataframe_execution_cache_request(
                    graph,
                    node_ids={"sorted"},
                    namespace="input-preparation-test",
                    source="live",
                    target_node_id="sorted",
                    profile=ExecutionProfile.LAZY_SINK,
                    input_fingerprint=execution_facade.dataframe_graph_input_fingerprint(
                        graph, target_node_id="sorted", source="live"
                    ),
                    cache=cache,
                )
                outputs, *_ = execution_facade.execute_lazy_graph(
                    graph,
                    _build_node_fn,
                    target_node_id="sorted",
                    execution_context=context,
                    dataframe_cache_request=request,
                )
                collected = _collect(outputs["sorted"])
            actions.append(
                [entry["action"] for entry in context.metrics_payload()["input_preparation"]]
            )
            return collected
        finally:
            context.release_admission()

    first = run()
    assert first["id"].to_list() == [2, 1]
    pl.DataFrame({"id": [1, 2, 3, 4]}).write_csv(path)
    second = run()

    assert second["id"].to_list() == [4, 3, 2, 1]
    assert actions == [["built"], ["refreshed"]]


# ------------------------------- (2) preview never prepares an inactive branch


def _live_switch_graph(live_config: dict[str, object], batch_config: dict[str, object]) -> Any:
    return make_graph(
        {
            "nodes": [
                _node("live_input", "dataInput", live_config),
                _node("batch_input", "dataInput", batch_config),
                _node(
                    "sw",
                    "liveSwitch",
                    {
                        "inputs": ["live_input", "batch_input"],
                        "input_scenario_map": {"live_input": "live", "batch_input": "batch"},
                    },
                ),
            ],
            "edges": [
                make_edge("live_input", "sw").model_dump(),
                make_edge("batch_input", "sw").model_dump(),
            ],
        }
    )


def test_a_preview_never_prepares_an_inactive_live_switch_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.executor import execute_graph

    store = _project(tmp_path, monkeypatch)
    live_path = tmp_path / "live.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(live_path)
    batch_path = tmp_path / "batch.csv"
    pl.DataFrame({"id": [7, 8, 9]}).write_csv(batch_path)
    live_config = _csv_config(live_path)
    batch_config = _csv_config(batch_path)
    graph = _live_switch_graph(live_config, batch_config)

    built: list[str] = []
    original_build = SourceCacheStore.build

    def record_build(self: Any, identity: Any, builder: Any, **kwargs: Any) -> Any:
        built.append(identity.digest)
        return original_build(self, identity, builder, **kwargs)

    monkeypatch.setattr(SourceCacheStore, "build", record_build)

    context = _context(ExecutionProfile.PREVIEW_EAGER)
    try:
        with native_memory_backend_scope("rlimit"):
            results = execute_graph(
                graph,
                target_node_id="sw",
                source="live",
                execution_context=context,
            )
        records = context.metrics_payload()["input_preparation"]
    finally:
        context.release_admission()

    assert results["sw"].row_count == 2
    assert [entry["node_id"] for entry in records] == ["live_input"]
    assert built == [store_identity(live_config, tmp_path).digest]
    batch_identity = store_identity(batch_config, tmp_path)
    assert not (store.identity_path(batch_identity) / "current.json").exists()


# ------------------------------------------------- (3) a cold trace prepares


def test_a_cold_trace_builds_a_missing_generation_and_traces_the_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.trace import execute_trace

    _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    graph = _graph(_csv_config(path), ["id"])

    with native_memory_backend_scope("rlimit"):
        result = execute_trace(graph, row_index=2, target_node_id="input")

    steps = {step.node_id: step for step in result.steps}
    assert steps["input"].output_values["id"] == 3


def test_a_trace_over_a_stale_generation_shows_the_refreshed_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.trace import execute_trace

    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    graph = _graph(config, ["id"])

    pl.DataFrame({"id": [7, 8, 9, 10]}).write_csv(path)
    with native_memory_backend_scope("rlimit"):
        result = execute_trace(graph, row_index=3, target_node_id="input")

    steps = {step.node_id: step for step in result.steps}
    assert steps["input"].output_values["id"] == 10
    identity = store_identity(config, tmp_path)
    assert store.open_generation(identity).generation_id != first.generation_id


# ------------------------------------------- (A1) an absent source never breaks a snapshot


def test_an_absent_source_reuses_the_published_generation_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    frame = pl.DataFrame({"id": [1, 2, 3]})
    frame.write_csv(path)
    config = _csv_config(path)
    published = build_input_snapshot(config, store=store, base_dir=tmp_path)
    path.unlink()

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("an absent source must never start a build")

    context = _context()
    try:
        with native_memory_backend_scope("rlimit"):
            record = _prepare(
                config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn
            )[0]
    finally:
        context.release_admission()

    assert record.action == "reused"
    assert record.warning_code == "source_unavailable"
    assert record.generation_id == published.generation_id
    prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    plt.assert_frame_equal(prepared, frame)


def test_an_absent_source_without_a_generation_is_refused_before_any_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    config = _csv_config(tmp_path / "never-written.csv")
    spawned: list[object] = []

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        spawned.append(args)
        raise AssertionError("preparation must refuse before spawning")

    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "build_failed"
    assert "source is unavailable" in str(excinfo.value)
    assert spawned == []


# --------------------------------- (A2) scanner preference respects value domains


def test_a_latin_1_csv_round_trips_through_an_admitted_eager_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "latin.csv"
    path.write_bytes("name\nCaf\u00e9\n".encode("latin-1"))
    config = _csv_config(path, mode="read", arguments={"encoding": "latin-1"})

    build_input_snapshot(config, store=store, base_dir=tmp_path, allow_admitted_eager=True)

    prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert prepared["name"].to_list() == ["Caf\u00e9"]


# ------------------------------- (A3) a host without a cap reuses a stale generation


def _without_native_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "haute._input_preparation.process_memory_caps_supported",
        lambda: False,
    )
    monkeypatch.setattr(
        "haute._input_preparation.current_native_memory_backend",
        lambda: None,
    )


def _record_preparation_logs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    emitted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "haute._input_preparation.logger",
        type(
            "_Recorder",
            (),
            {"warning": staticmethod(lambda event, **fields: emitted.append((event, fields)))},
        )(),
    )
    return emitted


def test_a_host_without_a_native_cap_reuses_a_stale_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    first = build_input_snapshot(config, store=store, base_dir=tmp_path)
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    _without_native_caps(monkeypatch)

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("a host without a cap must never spawn")

    emitted = _record_preparation_logs(monkeypatch)
    context = _context()
    try:
        record = _prepare(
            config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn
        )[0]
    finally:
        context.release_admission()

    assert record.action == "reused"
    assert record.warning_code == "cap_unavailable_stale_reused"
    assert record.generation_id == first.generation_id
    events = [event for event, _fields in emitted]
    assert "input_snapshot_cap_unavailable_stale_reused" in events
    # (A8) no build started, so no automatic-build warning was announced.
    assert "input_snapshot_auto_build" not in events


def test_a_host_without_a_native_cap_still_refuses_a_missing_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    _without_native_caps(monkeypatch)
    emitted = _record_preparation_logs(monkeypatch)

    def refuse_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("a host without a cap must never spawn")

    context = _context()
    try:
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=refuse_spawn)
    finally:
        context.release_admission()

    assert excinfo.value.reason_code == "cap_unavailable"
    assert "input_snapshot_auto_build" not in [event for event, _fields in emitted]


# ------------------------------------------- (A5) the whole-file signature is memoised


def test_the_source_signature_hashes_an_unchanged_file_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._input_providers as providers

    path = tmp_path / "rows.csv"
    path.write_text("id\n1\n", encoding="utf-8")
    config = _csv_config(path)
    calls: list[Path] = []
    original = providers.content_hash

    def counting_hash(target: Any) -> str:
        calls.append(Path(target))
        return original(target)

    monkeypatch.setattr(providers, "content_hash", counting_hash)

    def settle(target: Path) -> None:
        # A file written moments ago is hashed on every call, because a
        # same-size rewrite inside the filesystem's timestamp granularity would
        # keep its (size, mtime) key; ageing the mtime past the settle window
        # is what lets the memo serve it.
        stat = target.stat()
        aged = stat.st_mtime_ns - 10 * 1_000_000_000
        os.utime(target, ns=(stat.st_atime_ns, aged))

    young = providers.source_signature(config, base_dir=tmp_path)
    assert providers.source_signature(config, base_dir=tmp_path) == young
    assert len(calls) == 2

    settle(path)
    first = providers.source_signature(config, base_dir=tmp_path)
    assert first == young
    assert providers.source_signature(config, base_dir=tmp_path) == first
    assert len(calls) == 3

    path.write_text("id\n1\n2\n", encoding="utf-8")
    settle(path)
    changed_size = providers.source_signature(config, base_dir=tmp_path)
    assert changed_size != first
    assert len(calls) == 4

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert providers.source_signature(config, base_dir=tmp_path) == changed_size
    assert len(calls) == 5


# ----------------------------- (A7) a base exception is never converted into success


def test_a_base_exception_during_a_spawned_build_is_never_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    spawn = _FakeSpawn(store, tmp_path, stop_after="published", failure=KeyboardInterrupt())
    context = _context()
    try:
        with pytest.raises(KeyboardInterrupt):
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        context.release_admission()

    identity = store_identity(config, tmp_path)
    pointer = json.loads(
        (store.identity_path(identity) / "current.json").read_text(encoding="utf-8")
    )
    assert spawn.request is not None
    assert pointer["generation_id"] == spawn.request.generation_id


# --------------------------------------------- (A9) one deadline per preparation


class _ScriptedClock:
    """Monotonic clock reporting time already spent once preparation is under way."""

    def __init__(self, start: float, spent: float, settle_after: int) -> None:
        self._start = start
        self._spent = spent
        self._settle_after = settle_after
        self.calls = 0

    def monotonic(self) -> float:
        self.calls += 1
        if self.calls <= self._settle_after:
            return self._start
        return self._start + self._spent


def test_the_spawned_worker_inherits_the_preparation_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)
    monkeypatch.setenv("HAUTE_INPUT_PREPARATION_TIMEOUT_SECONDS", "30")
    # ``started_at`` and the deadline are read first; every later reading
    # reports the five seconds this preparation has already spent.
    clock = _ScriptedClock(1_000.0, 5.0, settle_after=2)
    monkeypatch.setattr("haute._input_preparation.time", clock)

    spawn = _FakeSpawn(store, tmp_path, stop_after="nothing", failure=RuntimeError("stop"))
    context = _context()
    try:
        with pytest.raises(InputPreparationError):
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=spawn)
    finally:
        context.release_admission()

    assert spawn.config is not None
    assert spawn.config.timeout_seconds == 25.0


# ------------------------------- (A11) a waiter inherits the owner's typed failure


def test_a_single_flight_waiter_inherits_the_owners_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._input_preparation as preparation_module

    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2]}).write_csv(path)
    config = _csv_config(path)

    spawns: list[object] = []
    waiter_waiting = threading.Event()
    original_wait = preparation_module._wait_for_single_flight

    def announce_wait(*args: Any, **kwargs: Any) -> None:
        waiter_waiting.set()
        original_wait(*args, **kwargs)

    monkeypatch.setattr(preparation_module, "_wait_for_single_flight", announce_wait)

    owner_building = threading.Event()

    def owner_spawn(*args: object, **kwargs: object) -> None:
        spawns.append(args)
        owner_building.set()
        assert waiter_waiting.wait(timeout=10)
        raise _remote_error("SourceCacheQuotaExceededError")

    owner_failure: list[BaseException] = []

    # Both executions share one admitted context: the admission budget admits a
    # single in-flight execution, and only the single-flight slot is under test.
    context = _context()

    def run_owner() -> None:
        try:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=owner_spawn)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion below
            owner_failure.append(exc)

    owner = threading.Thread(target=run_owner)
    owner.start()
    try:
        # The other thread owns the slot before this execution asks for it.
        assert owner_building.wait(timeout=10)
        with pytest.raises(InputPreparationError) as excinfo:
            _prepare(config, store=store, base_dir=tmp_path, context=context, spawn=owner_spawn)
    finally:
        owner.join(timeout=10)
        context.release_admission()

    assert isinstance(owner_failure[0], InputPreparationError)
    assert owner_failure[0].reason_code == "quota_exceeded"
    assert excinfo.value.reason_code == "quota_exceeded"
    assert "Another execution's snapshot build" in str(excinfo.value)
    assert len(spawns) == 1


# ------------------------------------- (A12) a real hard-capped spawn prepares


@pytest.mark.skipif(
    not process_memory_caps_supported(),
    reason="this host cannot install the native memory cap a spawned build requires",
)
def test_automatic_preparation_runs_a_real_hard_capped_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _project(tmp_path, monkeypatch)
    path = tmp_path / "rows.csv"
    pl.DataFrame({"id": [1, 2, 3]}).write_csv(path)
    config = _csv_config(path)

    context = _context()
    try:
        budget = isolated_execution_budget(context)
        record = _prepare(config, store=store, base_dir=tmp_path, context=context)[0]
    finally:
        context.release_admission()

    assert record.execution == "worker"
    assert record.memory_limit_bytes == budget.memory_limit_bytes
    identity = store_identity(config, tmp_path)
    assert store.open_generation(identity).generation_id == record.generation_id
    prepared = resolve_data_input(config, store=store, base_dir=tmp_path).collect()
    assert prepared["id"].to_list() == [1, 2, 3]
