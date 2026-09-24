"""A structured API Input's emitting tables as shared input snapshots.

Covers the identity of each table, its source freshness proof, the one-shred
build that publishes the tables, the supervised worker build and its
settlement, and automatic preparation before an admitted execution.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._cache import canonical_json
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._input_preparation import InputPreparationRecord, prepare_input_snapshots
from haute._json_shred import _records, _shred, _snapshots, _writer
from haute._json_shred._cache import load_v2_api_source
from haute._json_shred._snapshots import (
    ApiInputBuildOutcome,
    ApiInputBuildRequest,
    SourceChangedDuringCacheBuildError,
    TableBuildPlan,
    api_input_snapshot_source,
    api_input_source_signature,
    api_input_table_statuses,
    build_api_input_tables,
    build_api_input_tables_worker,
    new_table_build_plans,
    run_supervised_api_input_build,
    scratch_directory,
)
from haute._native_memory_limit import native_memory_backend_scope
from haute._sandbox import set_project_root
from haute._source_cache import SourceCacheCorruptError, SourceCacheStore
from haute.errors import InputPreparationError
from tests.conftest import make_edge, make_graph, make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_PROFILE = ExecutionProfile.LAZY_SINK


def _column(name: str, path: str, type_: str = "int", *, selected: bool = True) -> dict[str, Any]:
    return {"name": name, "path": path, "type": type_, "selected": selected}


def _config(data_path: Path, *, driver_columns: tuple[str, ...] = ("id", "age")) -> dict[str, Any]:
    driver_paths = {"id": "$[:].id", "age": "$[:].drivers[:].age"}
    return {
        "path": str(data_path),
        "tables": [
            {
                "label": "quotes",
                "path": "$[:]",
                "emit": True,
                "columns": [_column("id", "$[:].id"), _column("premium", "$[:].premium", "float")],
            },
            {
                "label": "drivers",
                "path": "$[:].drivers[:]",
                "emit": True,
                "columns": [
                    _column(name, driver_paths[name], selected=name in driver_columns)
                    for name in ("id", "age")
                ],
            },
            {
                "label": "unused",
                "path": "$[:].drivers[:]",
                "emit": False,
                "columns": [_column("age", "$[:].drivers[:].age")],
            },
        ],
    }


def _write_source(path: Path, count: int = 3) -> Path:
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": index,
                    "premium": index * 1.5,
                    "drivers": [{"age": 30 + index}, {"age": 40 + index}],
                }
            )
            for index in range(count)
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, SourceCacheStore]:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    return tmp_path, SourceCacheStore(tmp_path)


def _build_all(source: Any, store: SourceCacheStore, **kwargs: Any) -> dict[str, Any]:
    return build_api_input_tables(
        source, [table.label for table in source.tables], store=store, profile=_PROFILE, **kwargs
    )


# ----------------------------------------------------------------- identity


def test_each_emitting_table_is_one_identity_named_by_its_own_spec(tmp_path: Path) -> None:
    data = _write_source(tmp_path / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    assert [table.label for table in source.tables] == ["quotes", "drivers"]
    drivers = source.table("drivers").identity
    assert drivers.provider == "api_input"
    assert dict(drivers.descriptor) == {
        "path": str(data.resolve()),
        "table": {
            "path": "$[:].drivers[:]",
            "columns": [["id", "$[:].id", "int"], ["age", "$[:].drivers[:].age", "int"]],
        },
        "shred_version": 1,
    }
    with pytest.raises(KeyError, match="unused"):
        source.table("unused")


def test_editing_one_table_moves_only_its_identity(tmp_path: Path) -> None:
    data = _write_source(tmp_path / "quotes.jsonl")
    before = api_input_snapshot_source(_config(data), data)
    after = api_input_snapshot_source(_config(data, driver_columns=("age",)), data)
    renamed_config = _config(data)
    renamed_config["tables"][0]["label"] = "policies"
    renamed = api_input_snapshot_source(renamed_config, data)

    assert after.table("quotes").identity == before.table("quotes").identity
    assert after.table("drivers").identity != before.table("drivers").identity
    # The port label is not part of a table's identity.
    assert renamed.table("policies").identity == before.table("quotes").identity
    assert after.group_digest != before.group_digest
    assert renamed.group_digest == before.group_digest


def test_the_group_digest_is_the_path_and_the_distinct_table_digests(tmp_path: Path) -> None:
    data = _write_source(tmp_path / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    expected = hashlib.sha256(
        canonical_json(
            {
                "path": str(data.resolve()),
                "tables": sorted(table.identity.digest for table in source.tables),
            }
        ).encode("utf-8")
    ).hexdigest()
    assert source.group_digest == expected


# ---------------------------------------------------------------- freshness


def test_the_source_signature_is_the_content_sha256_and_size(tmp_path: Path) -> None:
    data = _write_source(tmp_path / "quotes.jsonl")
    payload = data.read_bytes()
    assert api_input_source_signature(data) == (
        f"sha256:{hashlib.sha256(payload).hexdigest()}:{len(payload)}"
    )
    assert api_input_source_signature(tmp_path / "absent.jsonl") == "missing"
    assert api_input_source_signature(tmp_path) == "missing"


def test_statuses_follow_the_build_and_the_source(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    def states(signature: str) -> list[tuple[str, str, str]]:
        return [
            (table.label, status.state, status.freshness)
            for table, status in api_input_table_statuses(source, store, source_signature=signature)
        ]

    assert states(api_input_source_signature(data)) == [
        ("quotes", "missing", "unknown"),
        ("drivers", "missing", "unknown"),
    ]
    _build_all(source, store)
    assert states(api_input_source_signature(data)) == [
        ("quotes", "ready", "fresh"),
        ("drivers", "ready", "fresh"),
    ]
    _write_source(data, count=4)
    assert states(api_input_source_signature(data)) == [
        ("quotes", "ready", "stale"),
        ("drivers", "ready", "stale"),
    ]
    # A missing source proves nothing about what was published.
    data.unlink()
    assert states(api_input_source_signature(data)) == [
        ("quotes", "ready", "unknown"),
        ("drivers", "ready", "unknown"),
    ]


# -------------------------------------------------------------------- build


def test_a_build_publishes_each_table_at_full_width(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    generations = _build_all(source, store)

    assert set(generations) == {table.identity.digest for table in source.tables}
    quotes = generations[source.table("quotes").identity.digest]
    drivers = generations[source.table("drivers").identity.digest]
    assert quotes.metadata.row_count == 3
    assert quotes.metadata.columns == {"id": "Int64", "premium": "Float64"}
    assert drivers.metadata.row_count == 6
    assert drivers.metadata.source_signature == api_input_source_signature(data)
    assert drivers.metadata.build_class == "bounded"
    assert drivers.lazy_frame.collect()["age"].to_list() == [30, 40, 31, 41, 32, 42]


def test_a_build_leaves_no_scratch_behind(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    _build_all(source, store, scratch_token="abcdef01")

    assert scratch_directory(store, "abcdef01") == (
        store.inputs_root / ".shred" / ".staging-abcdef01"
    )
    assert list((store.inputs_root / ".shred").iterdir()) == []


def test_tables_sharing_an_identity_are_built_once(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    config = _config(data)
    twin = json.loads(json.dumps(config["tables"][0]))
    twin["label"] = "copy"
    config["tables"].append(twin)
    source = api_input_snapshot_source(config, data)
    assert source.table("copy").identity == source.table("quotes").identity

    generations = build_api_input_tables(source, ["quotes", "copy"], store=store, profile=_PROFILE)

    assert list(generations) == [source.table("quotes").identity.digest]


def test_a_build_names_only_emitting_tables(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    assert build_api_input_tables(source, [], store=store, profile=_PROFILE) == {}
    with pytest.raises(KeyError, match="unused"):
        build_api_input_tables(source, ["quotes", "unused"], store=store, profile=_PROFILE)


def test_a_build_publishes_exactly_the_planned_generations(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    plans = new_table_build_plans(source, ["quotes", "drivers"])

    generations = _build_all(source, store, plans=plans)

    assert {digest: gen.generation_id for digest, gen in generations.items()} == {
        digest: plan.generation_id for digest, plan in plans.items()
    }
    with pytest.raises(ValueError, match="exactly the identities"):
        build_api_input_tables(
            source,
            ["quotes"],
            store=store,
            profile=_PROFILE,
            plans=plans,
        )


def test_a_build_of_a_missing_source_fails_before_writing(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = root / "absent.jsonl"
    source = api_input_snapshot_source(_config(data), data)

    with pytest.raises(FileNotFoundError, match="absent.jsonl"):
        _build_all(source, store, scratch_token="abcdef02")
    assert not scratch_directory(store, "abcdef02").exists()


def test_a_source_that_changes_during_the_build_publishes_nothing(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    signatures = iter(["sha256:before:1", "sha256:after:1"])
    monkeypatch.setattr(_snapshots, "api_input_source_signature", lambda _path: next(signatures))

    with pytest.raises(SourceChangedDuringCacheBuildError, match="changed while"):
        _build_all(source, store, scratch_token="abcdef03")

    assert not scratch_directory(store, "abcdef03").exists()
    assert all(
        status.state == "missing"
        for _table, status in api_input_table_statuses(source, store, source_signature=None)
    )


def test_skipped_records_are_reported(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = project
    data = root / "quotes.jsonl"
    data.write_text('{"id": 1, "premium": 1.0, "drivers": []}\n7\n', encoding="utf-8")
    source = api_input_snapshot_source(_config(data), data)
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        _snapshots.logger, "warning", lambda event, **fields: warnings.append((event, fields))
    )

    _build_all(source, store)

    assert [(event, fields["skipped_records"]) for event, fields in warnings] == [
        ("json_shred_records_skipped", 1)
    ]


def test_a_clean_source_reports_no_skips(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    warnings: list[str] = []
    monkeypatch.setattr(
        _snapshots.logger, "warning", lambda event, **_fields: warnings.append(event)
    )

    _build_all(source, store)

    assert warnings == []


@pytest.mark.parametrize(("range_count", "parallel"), [(0, False), (1, False), (2, True)])
def test_the_shred_runs_in_parallel_only_across_several_ranges(
    project: tuple[Path, SourceCacheStore],
    monkeypatch: pytest.MonkeyPatch,
    range_count: int,
    parallel: bool,
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    used: list[str] = []
    streaming = _writer._write_tables_streaming
    monkeypatch.setattr(_records, "_should_shred_in_parallel", lambda _path: True)
    monkeypatch.setattr(_records, "_jsonl_byte_ranges", lambda _path, _size: [(0, 1)] * range_count)

    def fake_parallel(path: Path, config: Any, specs: Any, scratch: Path, ranges: Any) -> Any:
        used.append(f"parallel:{len(ranges)}")
        return streaming(path, config, specs, scratch)

    def spy_streaming(*args: Any) -> Any:
        used.append("streaming")
        return streaming(*args)

    monkeypatch.setattr(_writer, "_write_tables_in_parallel", fake_parallel)
    monkeypatch.setattr(_writer, "_write_tables_streaming", spy_streaming)

    _build_all(source, store)

    assert used == (["parallel:2"] if parallel else ["streaming"])


class _InlinePool:
    """``ProcessPoolExecutor`` stand-in that runs each chunk in this process."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def map(self, function: Any, tasks: Any) -> list[Any]:
        return [function(task) for task in tasks]

    def shutdown(self, **_kwargs: Any) -> None:
        pass


@pytest.mark.parametrize("labels", [("drivers",), ("quotes", "drivers")])
def test_a_parallel_build_shreds_only_the_tables_it_writes(
    project: tuple[Path, SourceCacheStore],
    monkeypatch: pytest.MonkeyPatch,
    labels: tuple[str, ...],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl", count=6)
    source = api_input_snapshot_source(_config(data), data)
    monkeypatch.setattr(_records, "_should_shred_in_parallel", lambda _path: True)
    monkeypatch.setattr(_records, "_PARALLEL_CHUNK_BYTES", 150)
    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", _InlinePool)
    shredded: list[list[str]] = []
    shred_chunk = _writer._shred_chunk

    def spy(task: Any) -> Any:
        shredded.append([spec.label for spec in _shred._emitting_table_specs(task[4])])
        return shred_chunk(task)

    monkeypatch.setattr(_writer, "_shred_chunk", spy)

    published = build_api_input_tables(source, list(labels), store=store, profile=_PROFILE)

    assert len(shredded) > 1
    assert shredded == [list(labels)] * len(shredded)
    assert set(published) == {source.table(label).identity.digest for label in labels}
    drivers = published[source.table("drivers").identity.digest]
    assert pl.read_parquet(list(drivers.data_paths))["age"].to_list() == [
        age for index in range(6) for age in (30 + index, 40 + index)
    ]


def test_a_serial_source_never_asks_for_byte_ranges(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    monkeypatch.setattr(_records, "_should_shred_in_parallel", lambda _path: False)
    monkeypatch.setattr(
        _records,
        "_jsonl_byte_ranges",
        lambda *_args: pytest.fail("a serial shred must not tile the source"),
    )

    assert len(_build_all(source, store)) == 2


class _StageRecorder:
    """The slice of an execution context the build and the store call."""

    def __init__(self) -> None:
        self.stages: list[str] = []
        self.checkpoints = 0

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        self.stages.append(name)
        yield

    def checkpoint(self, *, label: str) -> None:
        del label
        self.checkpoints += 1


def test_the_shred_runs_as_an_execution_stage(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    context = _StageRecorder()

    _build_all(source, store, execution_context=context)

    assert context.stages[0] == "api_input_shred"
    assert context.stages.count("input_snapshot_write") == 2
    assert context.checkpoints > 0


def test_a_cancelled_build_publishes_nothing(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    with pytest.raises(Exception, match="cancelled"):
        _build_all(source, store, cancellation=lambda: True)

    assert list((store.inputs_root / ".shred").iterdir()) == []
    assert all(
        status.state == "missing"
        for _table, status in api_input_table_statuses(source, store, source_signature=None)
    )


# ------------------------------------------------------------- loader reads


def test_a_built_table_reads_without_its_source(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    config = _config(data)
    _build_all(api_input_snapshot_source(config, data), store)
    data.unlink()

    frames = load_v2_api_source(
        str(data), config, read_snapshots=True, port_columns={"drivers": {"age"}}
    )

    assert list(frames) == ["drivers"]
    assert frames["drivers"].collect()["age"].to_list() == [30, 40, 31, 41, 32, 42]


# ------------------------------------------------------- supervised worker


class _ContextStub:
    def __init__(self) -> None:
        self.released = False
        self.recorder = _StageRecorder()

    def checkpoint(self, *, label: str) -> None:
        self.recorder.checkpoint(label=label)

    def stage(self, name: str) -> Any:
        return self.recorder.stage(name)

    def release_admission(self, *, preserve_primary_error: bool) -> None:
        assert preserve_primary_error is True
        self.released = True


@pytest.fixture()
def worker_context(monkeypatch: pytest.MonkeyPatch) -> list[_ContextStub]:
    import haute._execution_admission as admission

    created: list[_ContextStub] = []

    def create(_budget: Any) -> _ContextStub:
        created.append(_ContextStub())
        return created[-1]

    monkeypatch.setattr(admission, "create_isolated_execution_context", create)
    return created


def _request(source: Any, store: SourceCacheStore, root: Path) -> ApiInputBuildRequest:
    labels = [table.label for table in source.tables]
    return ApiInputBuildRequest(
        config=dict(source.config),
        data_path=str(source.data_path),
        labels=tuple(labels),
        cache_root=str(store.root),
        project_root=str(root),
        profile=_PROFILE,
        plans=new_table_build_plans(source, labels),
        scratch_token="abcdef04",
    )


def test_the_worker_builds_the_planned_tables_and_defers_retirement(
    project: tuple[Path, SourceCacheStore],
    worker_context: list[_ContextStub],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    request = _request(source, store, root)

    outcome = build_api_input_tables_worker(request, budget=None)

    assert outcome == ApiInputBuildOutcome(
        generation_ids={digest: plan.generation_id for digest, plan in request.plans.items()}
    )
    assert worker_context[0].released
    assert "api_input_shred" in worker_context[0].recorder.stages


def test_the_worker_releases_its_admission_when_the_build_fails(
    project: tuple[Path, SourceCacheStore],
    worker_context: list[_ContextStub],
) -> None:
    root, store = project
    data = root / "absent.jsonl"
    source = api_input_snapshot_source(_config(data), data)

    with pytest.raises(FileNotFoundError):
        build_api_input_tables_worker(_request(source, store, root), budget=None)
    assert worker_context[0].released


class _Spawn:
    """Runs the worker in-process, optionally failing before or after it."""

    def __init__(self, *, run: bool, failure: BaseException | None) -> None:
        self.run = run
        self.failure = failure
        self.request: ApiInputBuildRequest | None = None
        self.config: Any = None

    def __call__(self, function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        self.request = request
        self.config = config
        outcome = function(request, budget) if self.run else None
        if self.failure is not None:
            raise self.failure
        return outcome


def test_a_supervised_build_returns_the_published_tables(
    project: tuple[Path, SourceCacheStore], worker_context: list[_ContextStub]
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    spawn = _Spawn(run=True, failure=None)

    generations = run_supervised_api_input_build(
        source,
        ["quotes", "drivers"],
        store=store,
        profile=_PROFILE,
        budget="budget",
        worker_config="config",
        spawn=spawn,
    )

    assert spawn.request is not None and spawn.config == "config"
    assert spawn.request.labels == ("quotes", "drivers")
    assert spawn.request.project_root == str(root)
    assert {digest: gen.generation_id for digest, gen in generations.items()} == {
        digest: plan.generation_id for digest, plan in spawn.request.plans.items()
    }
    assert not scratch_directory(store, spawn.request.scratch_token).exists()


def test_a_worker_that_dies_after_publishing_everything_still_succeeds(
    project: tuple[Path, SourceCacheStore], worker_context: list[_ContextStub]
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    spawn = _Spawn(run=True, failure=RuntimeError("died after publishing"))

    generations = run_supervised_api_input_build(
        source,
        ["quotes", "drivers"],
        store=store,
        profile=_PROFILE,
        budget=None,
        worker_config=None,
        spawn=spawn,
    )

    assert len(generations) == 2


def test_a_worker_that_dies_before_publishing_is_settled_and_raised(
    project: tuple[Path, SourceCacheStore], worker_context: list[_ContextStub]
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)
    spawn = _Spawn(run=False, failure=RuntimeError("died early"))

    def leave_staging(function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        # The child had created its scratch and one staging directory.
        scratch_directory(store, request.scratch_token).mkdir(parents=True)
        digest, plan = next(iter(request.plans.items()))
        (store.inputs_root / digest / f".staging-{plan.staging_token}").mkdir(parents=True)
        return spawn(function, request, budget, config=config)

    with pytest.raises(RuntimeError, match="died early"):
        run_supervised_api_input_build(
            source,
            ["quotes", "drivers"],
            store=store,
            profile=_PROFILE,
            budget=None,
            worker_config=None,
            spawn=leave_staging,
        )

    assert spawn.request is not None
    assert not scratch_directory(store, spawn.request.scratch_token).exists()
    digest, plan = next(iter(spawn.request.plans.items()))
    assert not (store.inputs_root / digest / f".staging-{plan.staging_token}").exists()


def test_an_interrupt_is_never_turned_into_success(
    project: tuple[Path, SourceCacheStore], worker_context: list[_ContextStub]
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    with pytest.raises(KeyboardInterrupt):
        run_supervised_api_input_build(
            source,
            ["quotes"],
            store=store,
            profile=_PROFILE,
            budget=None,
            worker_config=None,
            spawn=_Spawn(run=True, failure=KeyboardInterrupt()),
        )


def test_new_plans_name_each_distinct_identity_once(tmp_path: Path) -> None:
    data = _write_source(tmp_path / "quotes.jsonl")
    source = api_input_snapshot_source(_config(data), data)

    plans = new_table_build_plans(source, ["quotes", "drivers", "quotes"])

    assert set(plans) == {table.identity.digest for table in source.tables}
    assert all(isinstance(plan, TableBuildPlan) for plan in plans.values())
    assert len({plan.staging_token for plan in plans.values()}) == 2
    assert all(len(plan.staging_token) == 8 for plan in plans.values())


# ------------------------------------------------------ automatic preparation


def _graph(config: dict[str, Any]) -> Any:
    return make_graph(
        {
            "nodes": [
                {"id": "api", "data": {"label": "api", "nodeType": "apiInput", "config": config}},
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["id"]),
                    },
                },
            ],
            "edges": [make_edge("api", "out").model_dump()],
        }
    )


def _prepare(
    config: dict[str, Any], store: SourceCacheStore, root: Path, **kwargs: Any
) -> tuple[InputPreparationRecord, ...]:
    context: ExecutionContext = create_admitted_execution_context(
        operation="api_input_preparation_test", profile=_PROFILE
    )
    graph = _graph(config)
    try:
        with native_memory_backend_scope(kwargs.pop("backend", "rlimit")):
            return prepare_input_snapshots(
                ["api", "out"],
                graph.node_map,
                profile=_PROFILE,
                execution_context=context,
                base_dir=root,
                schema_only=False,
                store=store,
                **kwargs,
            )
    finally:
        context.release_admission()


def _actions(records: tuple[InputPreparationRecord, ...]) -> list[tuple[str, str]]:
    return [(record.identity_digest[:8], record.action) for record in records]


def test_preparation_builds_every_table_then_reuses_them(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    config = _config(data)
    source = api_input_snapshot_source(config, data)

    first = _prepare(config, store, root)
    assert [record.action for record in first] == ["built", "built"]
    assert [record.identity_digest for record in first] == [
        table.identity.digest for table in source.tables
    ]
    assert [record.row_count for record in first] == [3, 6]
    assert {record.node_id for record in first} == {"api"}

    monkeypatch.setattr(
        _snapshots, "_shred_into", lambda *_args: pytest.fail("a reuse must not shred")
    )
    second = _prepare(config, store, root)
    assert [record.action for record in second] == ["reused", "reused"]
    assert [record.generation_id for record in second] == [record.generation_id for record in first]


def test_editing_one_table_rebuilds_only_that_table(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    first = _prepare(_config(data), store, root)

    edited = _prepare(_config(data, driver_columns=("age",)), store, root)

    assert [record.action for record in edited] == ["reused", "built"]
    assert edited[0].generation_id == first[0].generation_id


def test_touching_the_source_refreshes_every_table(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    first = _prepare(_config(data), store, root)
    _write_source(data, count=5)

    refreshed = _prepare(_config(data), store, root)

    assert [record.action for record in refreshed] == ["refreshed", "refreshed"]
    assert [record.row_count for record in refreshed] == [5, 10]
    assert all(
        new.generation_id != old.generation_id for new, old in zip(refreshed, first, strict=True)
    )


def test_a_missing_source_reuses_published_tables(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    _prepare(_config(data), store, root)
    data.unlink()

    records = _prepare(_config(data), store, root)

    assert [(record.action, record.warning_code) for record in records] == [
        ("reused", "source_unavailable"),
        ("reused", "source_unavailable"),
    ]


def test_a_missing_source_without_published_tables_is_refused(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = root / "absent.jsonl"

    with pytest.raises(InputPreparationError, match="unavailable") as raised:
        _prepare(_config(data), store, root)

    assert raised.value.reason_code == "build_failed"
    assert "API Input" in raised.value.remediation


def test_a_corrupt_table_is_reported_not_rebuilt(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    config = _config(data)
    _prepare(config, store, root)
    source = api_input_snapshot_source(config, data)
    generation = store.open_generation(source.table("quotes").identity)
    generation.data_paths[0].write_bytes(b"not parquet")

    with pytest.raises(SourceCacheCorruptError, match="API Input"):
        _prepare(config, store, root)


def test_an_api_input_that_emits_nothing_is_left_to_the_node_builder(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    config = _config(data)
    for table in config["tables"]:
        table["emit"] = False

    assert _prepare(config, store, root) == ()


def test_a_flat_file_api_input_is_not_prepared(project: tuple[Path, SourceCacheStore]) -> None:
    root, store = project
    path = root / "rows.csv"
    pl.DataFrame({"id": [1]}).write_csv(path)

    assert _prepare({"path": str(path)}, store, root) == ()


def test_preparation_fails_typed_when_the_build_fails(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")

    def fail(*_args: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(_snapshots, "_shred_into", fail)

    with pytest.raises(InputPreparationError, match="tables failed") as raised:
        _prepare(_config(data), store, root)
    assert raised.value.reason_code == "build_failed"


class _PreparationSpawn:
    """Stands in for the capped worker: runs the child in-process, then fails or not."""

    def __init__(self, *, run: bool, failure: BaseException | None) -> None:
        self.inner = _Spawn(run=run, failure=failure)

    def __call__(self, function: Any, request: Any, budget: Any, *, config: Any) -> Any:
        assert config.require_memory_limit is True
        return self.inner(function, request, budget, config=config)


def test_preparation_without_an_installed_cap_builds_in_a_capped_worker(
    project: tuple[Path, SourceCacheStore],
    monkeypatch: pytest.MonkeyPatch,
    worker_context: list[_ContextStub],
) -> None:
    import haute._input_preparation as preparation

    root, store = project
    data = _write_source(root / "quotes.jsonl")
    monkeypatch.setattr(preparation, "process_memory_caps_supported", lambda: True)
    spawn = _PreparationSpawn(run=True, failure=None)

    records = _prepare(_config(data), store, root, backend=None, spawn=spawn)

    assert [(record.action, record.execution) for record in records] == [
        ("built", "worker"),
        ("built", "worker"),
    ]
    assert all(record.memory_limit_bytes is not None for record in records)


def test_a_failed_worker_whose_tables_were_published_reuses_them(
    project: tuple[Path, SourceCacheStore],
    monkeypatch: pytest.MonkeyPatch,
    worker_context: list[_ContextStub],
) -> None:
    import haute._input_preparation as preparation
    import haute._json_shred._snapshots as snapshots_module

    root, store = project
    data = _write_source(root / "quotes.jsonl")
    config = _config(data)
    monkeypatch.setattr(preparation, "process_memory_caps_supported", lambda: True)

    def publish_then_fail(*_args: Any, **_kwargs: Any) -> Any:
        build_api_input_tables(
            api_input_snapshot_source(config, data),
            ["quotes", "drivers"],
            store=store,
            profile=_PROFILE,
        )
        raise RuntimeError("worker lost")

    monkeypatch.setattr(snapshots_module, "run_supervised_api_input_build", publish_then_fail)

    records = _prepare(config, store, root, backend=None, spawn=object())

    assert [record.action for record in records] == ["reused", "reused"]


def test_a_failed_worker_is_a_typed_preparation_failure(
    project: tuple[Path, SourceCacheStore],
    monkeypatch: pytest.MonkeyPatch,
    worker_context: list[_ContextStub],
) -> None:
    import haute._input_preparation as preparation

    root, store = project
    data = _write_source(root / "quotes.jsonl")
    monkeypatch.setattr(preparation, "process_memory_caps_supported", lambda: True)
    spawn = _PreparationSpawn(run=False, failure=RuntimeError("worker lost"))

    with pytest.raises(InputPreparationError, match="tables failed") as raised:
        _prepare(_config(data), store, root, backend=None, spawn=spawn)
    assert raised.value.reason_code == "build_failed"


def test_without_a_cap_stale_tables_are_reused_and_missing_ones_refused(
    project: tuple[Path, SourceCacheStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute._input_preparation as preparation

    root, store = project
    data = _write_source(root / "quotes.jsonl")
    monkeypatch.setattr(preparation, "process_memory_caps_supported", lambda: False)

    with pytest.raises(InputPreparationError) as raised:
        _prepare(_config(data), store, root, backend=None, spawn=object())
    assert raised.value.reason_code == "cap_unavailable"

    _prepare(_config(data), store, root)
    _write_source(data, count=4)
    records = _prepare(_config(data), store, root, backend=None, spawn=object())
    assert [(record.action, record.warning_code) for record in records] == [
        ("reused", "cap_unavailable_stale_reused"),
        ("reused", "cap_unavailable_stale_reused"),
    ]


def test_preparation_is_skipped_without_an_admitted_execution(
    project: tuple[Path, SourceCacheStore],
) -> None:
    root, store = project
    data = _write_source(root / "quotes.jsonl")
    graph = _graph(_config(data))

    assert (
        prepare_input_snapshots(
            ["api"],
            graph.node_map,
            profile=_PROFILE,
            execution_context=None,
            base_dir=root,
            schema_only=False,
            store=store,
        )
        == ()
    )
    assert not Path(store.inputs_root / ".shred").exists()
