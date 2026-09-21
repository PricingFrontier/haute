"""Reports about the shared snapshot store.

Two endpoints, both explicit requests rather than subscriptions: the budgets
the store admits against, and what every node of a graph holds within them.
The accounting behind both walks every identity, generation and staging entry,
which is what an admission already pays, so a surface that wants to poll needs
an incremental count in the store first.

Both report read: they take no lock and change no generation. Neither request
is free of writes, though, because constructing the store creates
``.haute_cache/inputs`` when it is absent and, once per process per root,
sweeps retired directories — the same construction every other store-backed
route performs, not something these endpoints add.

Per `specs/server-api/low-level.md` ("Cache usage").
"""

from __future__ import annotations

from fastapi import APIRouter

from haute._node_snapshots import (
    CacheBudgetUsage,
    CacheOwnerUsage,
    NodeSnapshotStore,
    pipeline_source_file_key,
)
from haute.graph_utils import flatten_graph
from haute.routes._node_data_service import node_data_project_root
from haute.routes.node_data import node_data_service
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import (
    CacheBudgetUsagePayload,
    CacheNodeEntry,
    CacheNodesRequest,
    CacheNodesResponse,
    CacheOwnerEntry,
    CacheUsageResponse,
    NodeDataPointResponse,
)

router = APIRouter(prefix="/api/cache", tags=["cache"])


def _budget_payload(usage: CacheBudgetUsage) -> CacheBudgetUsagePayload:
    return CacheBudgetUsagePayload(
        generations_used=usage.generations_used,
        generations_limit=usage.generations_limit,
        generations_limit_variable=usage.generations_limit_variable,
        bytes_used=usage.bytes_used,
        bytes_limit=usage.bytes_limit,
        bytes_limit_variable=usage.bytes_limit_variable,
    )


def _owner_entry(owner: CacheOwnerUsage) -> CacheOwnerEntry:
    return CacheOwnerEntry(
        bucket=owner.bucket,
        label=owner.label,
        node_id=owner.node_id,
        source=owner.source,
        generations=owner.generations,
        row_count=owner.newest_row_count,
        size_bytes=owner.size_bytes,
        newest_created_at=owner.newest_created_at,
    )


def _node_entry(
    node_id: str,
    point: NodeDataPointResponse | None,
    reason: str | None,
    owner: CacheOwnerUsage | None,
    *,
    carries: bool,
    sharers: list[str],
) -> CacheNodeEntry:
    """One row: what the node reads now, and the data this row is responsible for.

    ``state`` is about the generation this node would read for its own column
    demand — which, for a node that reads an upstream point, is somebody else's
    data. Size is the bytes this row is responsible for: every signature the
    store holds for this node as a node output, or the whole identity behind it
    when it is a snapshot-backed input.

    Three rules keep a byte off a second row. A node reading another node's
    point does not carry that point's bytes and names it in ``reads_from`` —
    though it still carries its own captured output, which a node in the middle
    of a lineage often has. A shared input snapshot is charged to one reader and
    named on all of them in ``shares_snapshot_with``. And the figures come from
    the inventory's owner for the identity, never from the one generation the
    point resolved, so a non-current generation or an in-flight staging
    directory under it is reported rather than silently dropped.
    """
    generations, size_bytes, newest_created_at = 0, 0, None
    if owner is not None and carries:
        generations, size_bytes = owner.generations, owner.size_bytes
        newest_created_at = owner.newest_created_at

    if point is None:
        # No point to describe, but the store may still hold this node's data,
        # and this row is the only place it can be accounted for.
        return CacheNodeEntry(
            node_id=node_id,
            generations=generations,
            size_bytes=size_bytes,
            newest_created_at=newest_created_at,
            shares_snapshot_with=sharers,
            unavailable_reason=reason,
        )

    reads_from = point.point.producer_node_id if point.point.producer_node_id != node_id else None
    return CacheNodeEntry(
        node_id=node_id,
        kind=point.kind,
        state=point.state,
        reads_directly=point.reads_directly,
        reads_from=reads_from,
        shares_snapshot_with=sharers,
        row_count=point.row_count,
        retention=point.retention,
        generations=generations,
        size_bytes=size_bytes,
        newest_created_at=newest_created_at,
        unavailable_reason=reason,
    )


@router.get("/usage", response_model=CacheUsageResponse)
def cache_usage() -> CacheUsageResponse:
    """Report both cache budgets' generations and bytes against their limits."""
    report = NodeSnapshotStore(node_data_project_root()).usage_report()
    return CacheUsageResponse(
        node_outputs=_budget_payload(report.node_outputs),
        input_snapshots=_budget_payload(report.input_snapshots),
    )


@router.post("/nodes", response_model=CacheNodesResponse)
def cache_nodes(body: CacheNodesRequest) -> CacheNodesResponse:
    """Report every node of the graph, and everything else the store holds.

    The graph is flattened first, so a submodel's nodes are reported the way
    they are cached — individually — rather than as the one node the canvas
    draws at this depth.
    """
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)

    # One store for the whole report: constructing it sweeps retired
    # directories and re-reads the same roots, and three of them answer no
    # question a shared one does not.
    store = NodeSnapshotStore(node_data_project_root())
    inventory = store.inventory()
    # A node's own cache is what the store holds for it under this pipeline and
    # the source being reported. Another pipeline in the same project root has
    # its own node of that name, and the same node's data for another source is
    # somebody else's row; neither belongs on this one.
    pipeline = pipeline_source_file_key(graph)
    by_node = {
        (owner.pipeline_source_file, owner.node_id, owner.source): owner
        for owner in inventory.owners
        if owner.node_id is not None
    }
    # Input snapshots are keyed by identity, so a node's row can take the whole
    # identity — every generation and any staging — rather than the one
    # generation its point resolved.
    by_digest = {
        digest: owner
        for owner in inventory.owners
        if owner.node_id is None
        for digest in owner.identity_digests
    }

    resolved = node_data_service().points_for_graph(graph, body.source, store)

    # Which rows read which input identity, so a snapshot several nodes resolve
    # to is charged to exactly one of them. The carrier is the smallest node id
    # among the readers, never iteration order: a Refresh, an unrelated edit or
    # a submodel expanding must not move bytes from one row to another.
    readers: dict[str, list[str]] = {}
    for node_id, _point, _reason, digest in resolved:
        if digest is not None and digest in by_digest:
            readers.setdefault(digest, []).append(node_id)
    carrier = {digest: min(sharers) for digest, sharers in readers.items()}

    nodes: list[CacheNodeEntry] = []
    carried: set[str] = set()
    for node_id, point, reason, digest in resolved:
        owner = by_node.get((pipeline, node_id, body.source))
        sharers: list[str] = []
        carries = owner is not None
        if owner is None and digest is not None:
            owner = by_digest.get(digest)
            sharers = sorted(name for name in readers.get(digest, []) if name != node_id)
            carries = owner is not None and carrier.get(digest) == node_id
        if owner is not None and carries:
            carried |= owner.identity_digests
        nodes.append(_node_entry(node_id, point, reason, owner, carries=carries, sharers=sharers))

    # What no row carries is listed here. The two halves are exhaustive by
    # construction: an owner is either accounted for on a row or in `other`, so
    # the rows, `other` and `unattributed_*` add up to what the budgets say.
    other = [
        _owner_entry(owner) for owner in inventory.owners if not (owner.identity_digests & carried)
    ]
    return CacheNodesResponse(
        source=body.source,
        nodes=nodes,
        other=other,
        unattributed_generations=inventory.unattributed_generations,
        unattributed_bytes=inventory.unattributed_bytes,
        unmarked_identities=inventory.unmarked_identities,
    )
