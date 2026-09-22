"""The per-node cache report: every node of the graph, and everything else.

Per `specs/server-api/low-level.md` ("Cache usage").
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest

from haute._chunked_writes import part_name
from haute._data_points import DataPointResolver
from haute._execution_context import ExecutionProfile
from haute._input_providers import source_cache_identity
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotSlot,
    NodeSnapshotStore,
)
from haute._source_cache import SourceCacheBuildContext, SourceCacheCorruptError
from tests.conftest import make_edge, make_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


class _LazyBuilder:
    def __init__(self, frame: pl.LazyFrame) -> None:
        self.frame = frame

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        return self.frame


@pytest.fixture
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_parquet(haute_scratch / "quotes.parquet")
    return haute_scratch


def _graph(project: Path, *, source_config: dict[str, Any] | None = None) -> dict[str, Any]:
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
                        "config": source_config
                        or {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": str(project / "quotes.parquet"),
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "join",
                    "data": {
                        "label": "join",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = source.with_columns((pl.col('premium') * 2).alias('d'))"
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "join").model_dump()],
        }
    )
    return graph.model_dump()


def _publish(store: NodeSnapshotStore, project: Path, node_id: str, source: str, rows: int) -> None:
    """Put a node-output generation on disk for *node_id* under *source*."""
    slot = NodeSnapshotSlot(
        pipeline_source_file=str(project / "main.py"),
        node_id=node_id,
        source=source,
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    identity = slot.identity(f"signature-{node_id}-{source}")
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"x": list(range(rows))}).write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ) as publication:
        assert publication.outcome == "published"


def _body(graph: dict[str, Any], source: str = "live") -> dict[str, Any]:
    return {"graph": graph, "source": source}


def _entry(payload: dict[str, Any], node_id: str) -> dict[str, Any]:
    matches = [node for node in payload["nodes"] if node["node_id"] == node_id]
    assert matches, f"{node_id} missing from {[n['node_id'] for n in payload['nodes']]}"
    return matches[0]


def _csv_config(path: Path, **arguments: Any) -> dict[str, Any]:
    return {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": str(path),
        "arguments": dict(arguments),
    }


def _build_input_via_route(client: TestClient, config: dict[str, Any]) -> None:
    build = client.post("/api/input-cache/build", json={"config": config})
    assert build.status_code == 202, build.text
    job_id = build.json()["job_id"]
    for _ in range(300):
        status = client.get(f"/api/input-cache/jobs/{job_id}").json()
        if status["status"] not in {"running", "queued"}:
            break
        time.sleep(0.05)
    assert status["status"] == "completed", status


def _data_input_node(node_id: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": "dataInput", "config": config},
    }


def _graph_of(project: Path, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": nodes,
            "edges": [],
        }
    ).model_dump()


def _accounted(payload: dict[str, Any], bucket: str) -> int:
    """Every byte the report attributes, for one budget.

    The invariant the whole report rests on: what the rows carry, plus what is
    listed as belonging to no row, plus what could not be attributed at all,
    is exactly what the budget says the store holds. Anything else means the
    report counted a byte twice or lost one.
    """
    kinds = {"node_output": {"node_output"}, "input": {"data_input", "api_input_table"}}[bucket]
    rows = sum(node["size_bytes"] for node in payload["nodes"] if node["kind"] in kinds)
    other = sum(entry["size_bytes"] for entry in payload["other"] if entry["bucket"] == bucket)
    return rows + other + payload["unattributed_bytes"]


def test_reports_every_node_of_the_graph(client: TestClient, project: Path) -> None:
    response = client.post("/api/cache/nodes", json=_body(_graph(project)))
    assert response.status_code == 200
    payload = response.json()

    assert [node["node_id"] for node in payload["nodes"]] == ["source", "join"]
    assert payload["source"] == "live"


def test_a_node_reports_what_the_store_holds_for_it(client: TestClient, project: Path) -> None:
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)

    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()

    join = _entry(payload, "join")
    assert join["generations"] == 1
    assert join["size_bytes"] > 0
    assert join["newest_created_at"] is not None

    # A node with nothing cached says so rather than being left out.
    source = _entry(payload, "source")
    assert source["generations"] == 0
    assert source["size_bytes"] == 0


def test_re_caching_replaces_a_nodes_dataset_rather_than_adding_to_it(
    client: TestClient, project: Path
) -> None:
    """A node holds one dataset, so its row never counts a second full copy."""
    store = NodeSnapshotStore(project)
    slot = NodeSnapshotSlot(
        pipeline_source_file=str(project / "main.py"),
        node_id="join",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    for signature in ("one", "two"):
        identity = slot.identity(signature)
        artifact = store.stage_node_output(identity)
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(artifact.part_path(0))
        with store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=False,
            profile=ExecutionProfile.NODE_SNAPSHOT,
        ) as publication:
            assert publication.outcome == "published"

    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()
    assert _entry(payload, "join")["generations"] == 1


def test_a_node_no_longer_in_the_graph_is_reported_as_other(
    client: TestClient, project: Path
) -> None:
    """The point of the report: a deleted node still eating the quota is named."""
    store = NodeSnapshotStore(project)
    _publish(store, project, "deleted_node", "live", rows=4)

    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()

    assert "deleted_node" not in [node["node_id"] for node in payload["nodes"]]
    other = [entry for entry in payload["other"] if entry["node_id"] == "deleted_node"]
    assert len(other) == 1
    assert other[0]["bucket"] == "node_output"
    assert other[0]["source"] == "live"
    assert other[0]["size_bytes"] > 0


def test_a_nodes_data_for_another_source_is_reported_as_other(
    client: TestClient, project: Path
) -> None:
    """Same node, different source: it is not this report's node, but it is on disk."""
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "backtest", rows=4)

    payload = client.post("/api/cache/nodes", json=_body(_graph(project), source="live")).json()

    assert _entry(payload, "join")["generations"] == 0
    other = [entry for entry in payload["other"] if entry["node_id"] == "join"]
    assert len(other) == 1
    assert other[0]["source"] == "backtest"


def test_an_input_snapshot_is_reported_on_its_reader_and_nowhere_else(
    client: TestClient, project: Path
) -> None:
    """The same bytes must never appear on two rows of one report.

    A Data Input's snapshot is the cache for that node, so it belongs on its
    row — and must therefore not also be listed as data belonging to nothing.
    """
    # A CSV input is snapshot-backed; a Parquet scan is read directly and has
    # no snapshot to report at all.
    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_csv(csv_path)
    config = {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": str(csv_path),
        "arguments": {},
    }
    graph = _graph(project, source_config=config)
    build = client.post("/api/input-cache/build", json={"config": config})
    assert build.status_code == 202, build.text
    job_id = build.json()["job_id"]
    for _ in range(300):
        status = client.get(f"/api/input-cache/jobs/{job_id}").json()
        if status["status"] not in {"running", "queued"}:
            break
        time.sleep(0.05)
    assert status["status"] == "completed", status

    payload = client.post("/api/cache/nodes", json=_body(graph)).json()

    source_row = _entry(payload, "source")
    assert source_row["size_bytes"] > 0, "the Data Input's snapshot is its cache"
    assert source_row["reads_from"] is None

    inputs_listed_again = [entry for entry in payload["other"] if entry["bucket"] == "input"]
    assert inputs_listed_again == []


def test_a_node_reading_another_nodes_cache_reports_no_size(
    client: TestClient, project: Path
) -> None:
    """Otherwise one join's bytes would be counted once per consumer."""
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)
    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                *_graph(project)["nodes"],
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "banding",
                        "config": {
                            "factors": [
                                {
                                    "banding": "continuous",
                                    "column": "premium",
                                    "outputColumn": "band",
                                    "rules": [],
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "join").model_dump(),
                make_edge("join", "banding").model_dump(),
            ],
        }
    ).model_dump()

    payload = client.post("/api/cache/nodes", json=_body(graph)).json()

    banding = _entry(payload, "banding")
    assert banding["reads_from"] == "join"
    assert banding["size_bytes"] == 0
    assert _entry(payload, "join")["size_bytes"] > 0


def test_an_unwired_node_is_a_row_not_a_failed_request(client: TestClient, project: Path) -> None:
    """A Banding with no input cannot resolve a point; the rest still reports."""
    graph = make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": [
                {
                    "id": "orphan_banding",
                    "data": {
                        "label": "orphan",
                        "nodeType": "banding",
                        "config": {
                            "factors": [
                                {
                                    "banding": "continuous",
                                    "column": "premium",
                                    "outputColumn": "band",
                                    "rules": [],
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [],
        }
    ).model_dump()

    response = client.post("/api/cache/nodes", json=_body(graph))
    assert response.status_code == 200
    entry = _entry(response.json(), "orphan_banding")
    assert entry["state"] is None
    assert entry["unavailable_reason"]


def test_reports_the_unmarked_identities_that_double_count(
    client: TestClient, project: Path
) -> None:
    """An identity with no provider marker is charged to both budgets.

    The usage report therefore exceeds the sum of these entries, and this
    number is the only thing that explains the difference.
    """
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=3)
    marker = next(
        path / "provider"
        for path in (project / ".haute_cache" / "inputs").iterdir()
        if (path / "provider").is_file()
    )
    marker.unlink()

    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()
    assert payload["unmarked_identities"] == 1
    # It is still attributed to its node, because its metadata names it.
    assert _entry(payload, "join")["generations"] == 1


def test_two_nodes_reading_one_snapshot_report_its_bytes_once(
    client: TestClient, project: Path
) -> None:
    """Two Data Inputs with one configuration resolve to a single identity.

    A submodel instantiated twice flattens to exactly this, so the bytes must
    land on one row and be named by the other, not counted on both.
    """
    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_csv(csv_path)
    config = _csv_config(csv_path)
    _build_input_via_route(client, config)

    graph = _graph_of(
        project, [_data_input_node("quotes_a", config), _data_input_node("quotes_b", config)]
    )
    usage = client.get("/api/cache/usage").json()
    payload = client.post("/api/cache/nodes", json=_body(graph)).json()

    rows = {node["node_id"]: node for node in payload["nodes"]}
    # The carrier is the smallest node id, not whichever row came first, so an
    # unrelated edit never moves the bytes between rows.
    assert rows["quotes_a"]["size_bytes"] > 0
    assert rows["quotes_b"]["size_bytes"] == 0
    # Both sides name the other, so either row tells the whole story.
    assert rows["quotes_a"]["shares_snapshot_with"] == ["quotes_b"]
    assert rows["quotes_b"]["shares_snapshot_with"] == ["quotes_a"]
    assert _accounted(payload, "input") == usage["input_snapshots"]["bytes_used"]


def test_the_carrier_of_a_shared_snapshot_does_not_depend_on_node_order(
    client: TestClient, project: Path
) -> None:
    """Reordering the graph must not move bytes from one row to another."""
    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_csv(csv_path)
    config = _csv_config(csv_path)
    _build_input_via_route(client, config)

    forward = _graph_of(project, [_data_input_node("aaa", config), _data_input_node("zzz", config)])
    reversed_order = _graph_of(
        project, [_data_input_node("zzz", config), _data_input_node("aaa", config)]
    )

    for graph in (forward, reversed_order):
        payload = client.post("/api/cache/nodes", json=_body(graph)).json()
        rows = {node["node_id"]: node for node in payload["nodes"]}
        assert rows["aaa"]["size_bytes"] > 0, "the smallest id carries it either way"
        assert rows["zzz"]["size_bytes"] == 0


def test_a_second_identity_under_the_same_path_is_still_accounted_for(
    client: TestClient, project: Path
) -> None:
    """The same file read with different arguments is a different identity.

    Grouping those by their shared path label made one of them invisible: not
    on a row, not in `other`, not unattributed. The budget still counted it.
    """
    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_csv(csv_path)
    read_config = _csv_config(csv_path)
    _build_input_via_route(client, read_config)

    stale_config = _csv_config(csv_path, has_header=False)
    store = NodeSnapshotStore(project)
    identity = source_cache_identity(stale_config, base_dir=project)
    context = SourceCacheBuildContext(profile=ExecutionProfile.LAZY_SINK, build_class="bounded")
    store.build(
        identity, _LazyBuilder(pl.DataFrame({"y": list(range(500))}).lazy()), context=context
    )

    graph = _graph_of(project, [_data_input_node("quotes", read_config)])
    usage = client.get("/api/cache/usage").json()
    payload = client.post("/api/cache/nodes", json=_body(graph)).json()

    assert usage["input_snapshots"]["generations_used"] == 2
    assert [entry["bucket"] for entry in payload["other"]] == ["input"]
    assert _accounted(payload, "input") == usage["input_snapshots"]["bytes_used"]


def test_one_misconfigured_data_input_does_not_fail_the_whole_report(
    client: TestClient, project: Path
) -> None:
    """A Data Input with no path raises while resolving its own identity.

    That is a row with a reason, not a 500: a report about every node is worth
    least precisely when one node is half-configured.
    """
    pl.DataFrame({"premium": [1.0]}).write_parquet(project / "ok.parquet")
    healthy = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": str(project / "ok.parquet"),
        "arguments": {},
    }
    broken = {"inputType": "file", "format": "csv", "mode": "scan", "arguments": {}}

    graph = _graph_of(
        project, [_data_input_node("ok", healthy), _data_input_node("broken", broken)]
    )
    response = client.post("/api/cache/nodes", json=_body(graph))

    assert response.status_code == 200
    payload = response.json()
    assert _entry(payload, "ok")["unavailable_reason"] is None
    assert _entry(payload, "broken")["unavailable_reason"]


def test_every_node_output_byte_is_accounted_for_exactly_once(
    client: TestClient, project: Path
) -> None:
    """The invariant, against the shapes that broke it before.

    A node's own data, a stray generation under its identity that publication
    left behind, an in-flight staging directory, and a node no longer in the
    graph — each must land on exactly one side of the report, and together
    they must equal what the budget counts.
    """
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)
    _publish(store, project, "deleted_node", "live", rows=4)

    identity_dirs = [
        path
        for path in (project / ".haute_cache" / "inputs").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    # A second generation the store did not retire, and staging mid-write:
    # both count against the budget, so both must be reported somewhere.
    stray = project / identity_dirs[0].relative_to(project) / "generations" / "stray-generation"
    stray.mkdir(parents=True)
    (stray / "meta.json").write_text(
        (identity_dirs[0] / "generations")
        .glob("*/meta.json")
        .__next__()
        .read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (stray / part_name(0)).write_bytes(b"x" * 2048)
    staging = project / identity_dirs[1].relative_to(project) / ".staging-abcdef012345"
    staging.mkdir(parents=True)
    (staging / "part-00000.parquet").write_bytes(b"y" * 1024)

    usage = client.get("/api/cache/usage").json()
    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()

    assert _accounted(payload, "node_output") == usage["node_outputs"]["bytes_used"]


def test_another_pipelines_node_of_the_same_name_is_not_this_ones(
    client: TestClient, project: Path
) -> None:
    """One project root can hold several pipelines, and one store serves them.

    Keyed on node id and source alone, `alt.py`'s `join` would be reported as
    this pipeline's `join` — a row claiming bytes that belong to a graph the
    user is not looking at.
    """
    store = NodeSnapshotStore(project)
    (project / "alt.py").write_text("# other pipeline\n", encoding="utf-8")
    other = NodeSnapshotSlot(
        pipeline_source_file=str(project / "alt.py"),
        node_id="join",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    identity = other.identity("alt-signature")
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"x": list(range(9))}).write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.NODE_SNAPSHOT,
    ) as publication:
        assert publication.outcome == "published"

    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()

    assert _entry(payload, "join")["size_bytes"] == 0
    assert [entry["label"] for entry in payload["other"]] == ["join"]
    assert payload["other"][0]["size_bytes"] > 0


def test_a_corrupt_point_is_a_row_with_a_state_not_a_reason(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Damaged data is a state the surfaces have a remedy for, not an absence.

    `consumer_point` succeeds before the resolution raises, so the kind and the
    producer are knowable and the row is worth building: the pane shows
    "Unreadable" with the node's size, where Refresh already means re-cache.
    """
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)

    real_resolve = DataPointResolver.resolve

    def corrupt_for_join(self: DataPointResolver, point: Any, demand: Any) -> Any:
        if point.producer_node_id == "join":
            raise SourceCacheCorruptError("generation is unreadable")
        return real_resolve(self, point, demand)

    monkeypatch.setattr(DataPointResolver, "resolve", corrupt_for_join)

    response = client.post("/api/cache/nodes", json=_body(_graph(project)))
    assert response.status_code == 200

    join = _entry(response.json(), "join")
    assert join["state"] == "corrupt"
    assert join["kind"] == "node_output"
    assert join["unavailable_reason"] is None
    # The budget charges a corrupt generation like any other, so the row carries it.
    assert join["size_bytes"] > 0


def test_a_generation_whose_metadata_is_unreadable_is_reported_as_unattributed(
    client: TestClient, project: Path
) -> None:
    """Its metadata names its owner, so without metadata it has none.

    The budget still counts the bytes, so the report has to say so rather than
    let them fall out of every section — which is how they went missing before.
    """
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)
    identity_dir = next(
        path
        for path in (project / ".haute_cache" / "inputs").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    generation = project / next((identity_dir / "generations").iterdir()).relative_to(project)
    (generation / "meta.json").write_text("{ this is not json", encoding="utf-8")

    usage = client.get("/api/cache/usage").json()
    payload = client.post("/api/cache/nodes", json=_body(_graph(project))).json()

    assert payload["unattributed_generations"] == 1
    assert payload["unattributed_bytes"] > 0
    assert _entry(payload, "join")["size_bytes"] == 0
    assert _accounted(payload, "node_output") == usage["node_outputs"]["bytes_used"]


def test_a_row_reports_when_it_was_cached_and_how_long_it_took(
    client: TestClient, project: Path
) -> None:
    """Both are recorded at publication, from staging the artifact to writing
    the metadata — which is the span during which the caching happened."""
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)

    join = _entry(client.post("/api/cache/nodes", json=_body(_graph(project))).json(), "join")

    assert join["newest_created_at"] is not None
    assert join["build_seconds"] is not None
    assert join["build_seconds"] >= 0


def test_a_generation_from_before_durations_were_recorded_reads_as_unknown(
    client: TestClient, project: Path
) -> None:
    """Absent is not zero: a store predating this must not claim instant builds."""
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)
    for identity_dir in (project / ".haute_cache" / "inputs").iterdir():
        if identity_dir.is_dir() and not identity_dir.name.startswith("."):
            for generation in (identity_dir / "generations").iterdir():
                meta = generation / "meta.json"
                raw = json.loads(meta.read_text(encoding="utf-8"))
                raw.pop("build_seconds", None)
                meta.write_text(json.dumps(raw), encoding="utf-8")

    join = _entry(client.post("/api/cache/nodes", json=_body(_graph(project))).json(), "join")

    assert join["build_seconds"] is None
    assert join["size_bytes"] > 0


def test_clearing_a_row_removes_exactly_what_that_row_reported(
    client: TestClient, project: Path
) -> None:
    """The row is the unit the user acts on, so it must clear its own bytes.

    Not the node's other sources, not another row's shared snapshot: exactly
    the identities the row named, which is why the row carries them.
    """
    store = NodeSnapshotStore(project)
    _publish(store, project, "join", "live", rows=5)
    _publish(store, project, "join", "backtest", rows=7)

    before = client.post("/api/cache/nodes", json=_body(_graph(project))).json()
    join = _entry(before, "join")
    assert join["identity_digests"], "a row that carries bytes names what it carries"
    other_before = [entry for entry in before["other"] if entry["source"] == "backtest"]
    assert other_before and other_before[0]["size_bytes"] > 0

    response = client.post("/api/cache/clear", json={"digests": join["identity_digests"]})
    assert response.status_code == 200
    cleared = response.json()
    assert cleared["cleared"] == join["identity_digests"]
    assert cleared["freed_bytes"] > 0

    after = client.post("/api/cache/nodes", json=_body(_graph(project))).json()
    assert _entry(after, "join")["size_bytes"] == 0
    # The same node's data for another source is a different row, untouched.
    other_after = [entry for entry in after["other"] if entry["source"] == "backtest"]
    assert other_after and other_after[0]["size_bytes"] == other_before[0]["size_bytes"]


def test_clearing_a_row_the_store_no_longer_holds_is_not_an_error(
    client: TestClient, project: Path
) -> None:
    """Acting on a report a moment out of date is a race, not a failure."""
    response = client.post("/api/cache/clear", json={"digests": ["a" * 64]})

    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "cleared": [], "freed_bytes": 0}


def test_clearing_an_orphaned_row_reclaims_it(client: TestClient, project: Path) -> None:
    """The reason the row-level clear exists: nothing else can reach these.

    A node no longer in the graph has no node-data endpoint to clear it, so
    before this its bytes could only be reclaimed by quota pressure.
    """
    store = NodeSnapshotStore(project)
    _publish(store, project, "deleted_node", "live", rows=6)

    before = client.post("/api/cache/nodes", json=_body(_graph(project))).json()
    orphan = next(entry for entry in before["other"] if entry["node_id"] == "deleted_node")
    assert orphan["size_bytes"] > 0

    client.post("/api/cache/clear", json={"digests": orphan["identity_digests"]})

    after = client.post("/api/cache/nodes", json=_body(_graph(project))).json()
    assert [entry for entry in after["other"] if entry["node_id"] == "deleted_node"] == []
