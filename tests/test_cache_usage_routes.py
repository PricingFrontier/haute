"""The cache-usage endpoint: both budgets against their limits.

Per `specs/server-api/low-level.md` ("Cache usage").
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

from haute._chunked_writes import part_name
from haute._execution_context import ExecutionProfile
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotSlot,
    NodeSnapshotStore,
)
from haute._source_cache import SourceCacheBuildContext, SourceCacheIdentity

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


class _LazyBuilder:
    def __init__(self, frame: pl.LazyFrame) -> None:
        self.frame = frame

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        return self.frame


def _publish_node_output(store: NodeSnapshotStore, project: Path, node_id: str) -> None:
    slot = NodeSnapshotSlot(
        pipeline_source_file=str(project / "main.py"),
        node_id=node_id,
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    identity = slot.identity("signature-1")
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


def _build_input_snapshot(store: NodeSnapshotStore, name: str) -> None:
    identity = SourceCacheIdentity(provider="file", descriptor={"path": f"{name}.parquet"})
    context = SourceCacheBuildContext(
        profile=ExecutionProfile.LAZY_SINK,
        build_class="bounded",
    )
    store.build(identity, _LazyBuilder(pl.DataFrame({"y": [1, 2]}).lazy()), context=context)


@pytest.fixture
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project whose snapshot store the endpoint reports on."""
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    return haute_scratch


@pytest.fixture
def _pinned_limits(monkeypatch: pytest.MonkeyPatch) -> Mapping[str, int]:
    """Pin all four limits so the response's numbers are the ones set here."""
    limits = {
        "HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS": 512,
        "HAUTE_NODE_SNAPSHOT_MAX_BYTES": 40 * 1024 * 1024 * 1024,
        "HAUTE_INPUT_CACHE_MAX_GENERATIONS": 64,
        "HAUTE_INPUT_CACHE_MAX_BYTES": 20 * 1024 * 1024 * 1024,
    }
    for variable, value in limits.items():
        monkeypatch.setenv(variable, str(value))
    return limits


def test_usage_reports_both_budgets_against_their_limits(
    client: TestClient,
    project: Path,
    _pinned_limits: Mapping[str, int],
) -> None:
    store = NodeSnapshotStore(project)
    _publish_node_output(store, project, "join")
    _build_input_snapshot(store, "drivers")

    response = client.get("/api/cache/usage")
    assert response.status_code == 200
    payload = response.json()

    node_outputs = payload["node_outputs"]
    assert node_outputs["generations_used"] == 1
    assert node_outputs["bytes_used"] > 0
    assert (
        node_outputs["generations_limit"] == _pinned_limits["HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS"]
    )
    assert node_outputs["bytes_limit"] == _pinned_limits["HAUTE_NODE_SNAPSHOT_MAX_BYTES"]

    input_snapshots = payload["input_snapshots"]
    assert input_snapshots["generations_used"] == 1
    assert input_snapshots["bytes_used"] > 0
    assert (
        input_snapshots["generations_limit"] == _pinned_limits["HAUTE_INPUT_CACHE_MAX_GENERATIONS"]
    )
    assert input_snapshots["bytes_limit"] == _pinned_limits["HAUTE_INPUT_CACHE_MAX_BYTES"]


def test_usage_names_the_variable_behind_each_limit(
    client: TestClient,
    project: Path,
    _pinned_limits: Mapping[str, int],
) -> None:
    """The names are the report's whole point: they are what a user changes."""
    payload = client.get("/api/cache/usage").json()

    assert payload["node_outputs"]["generations_limit_variable"] == (
        "HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS"
    )
    assert payload["node_outputs"]["bytes_limit_variable"] == "HAUTE_NODE_SNAPSHOT_MAX_BYTES"
    assert payload["input_snapshots"]["generations_limit_variable"] == (
        "HAUTE_INPUT_CACHE_MAX_GENERATIONS"
    )
    assert payload["input_snapshots"]["bytes_limit_variable"] == "HAUTE_INPUT_CACHE_MAX_BYTES"


def test_named_variables_are_the_ones_the_store_reads(
    client: TestClient,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising the variable the response names must move the limit it names.

    A hardcoded name in the UI would pass the test above while telling a user
    to change a variable the store never reads; setting each named variable and
    reading the limit back is what rules that out.
    """
    payload = client.get("/api/cache/usage").json()
    raised = {}
    for budget in ("node_outputs", "input_snapshots"):
        for limit in ("generations", "bytes"):
            variable = payload[budget][f"{limit}_limit_variable"]
            value = int(payload[budget][f"{limit}_limit"]) + 7
            monkeypatch.setenv(variable, str(value))
            raised[(budget, limit)] = value

    after = client.get("/api/cache/usage").json()
    for (budget, limit), value in raised.items():
        assert after[budget][f"{limit}_limit"] == value


def test_an_unclassified_identity_counts_against_both_budgets(
    client: TestClient,
    project: Path,
) -> None:
    """The spec says so, because admission says so — pin it at the surface.

    An identity whose provider marker is missing or unrecognised is charged to
    both budgets when a capture is admitted, so the report has to charge it to
    both too: what it shows is what would refuse the next capture, not a
    second opinion about it.
    """
    before = client.get("/api/cache/usage").json()

    identity_dir = project / ".haute_cache" / "inputs" / ("a" * 64)
    generation = identity_dir / "generations" / "0001"
    generation.mkdir(parents=True)
    (identity_dir / "provider").write_text("not_a_known_provider\n", encoding="utf-8")
    (generation / "meta.json").write_text("{}", encoding="utf-8")
    (generation / part_name(0)).write_bytes(b"x" * 4096)

    after = client.get("/api/cache/usage").json()
    for budget in ("node_outputs", "input_snapshots"):
        assert after[budget]["generations_used"] == before[budget]["generations_used"] + 1
        assert after[budget]["bytes_used"] >= before[budget]["bytes_used"] + 4096


def test_usage_counts_nothing_in_an_empty_store(
    client: TestClient,
    project: Path,
) -> None:
    payload = client.get("/api/cache/usage").json()
    for budget in ("node_outputs", "input_snapshots"):
        assert payload[budget]["generations_used"] == 0
        assert payload[budget]["bytes_used"] == 0
