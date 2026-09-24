"""Inventory and explicit clearing for the shared snapshot store.

Listing walks every identity, generation and staging entry, so it is requested
explicitly rather than polled. The report takes no lock and changes no data;
store construction may create the cache root and sweep retired directories.
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter

from haute._node_snapshots import (
    CacheOwnerUsage,
    NodeSnapshotStore,
    pipeline_source_file_key,
)
from haute.graph_utils import flatten_graph
from haute.routes._node_data_service import node_data_project_root
from haute.routes.node_data import node_data_service
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import (
    CacheClearRequest,
    CacheClearResponse,
    CacheNodeEntry,
    CacheNodesRequest,
    CacheNodesResponse,
    CacheOwnerEntry,
    NodeDataPointResponse,
)

router = APIRouter(prefix="/api/cache", tags=["cache"])


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
        build_seconds=owner.newest_build_seconds,
        identity_digests=sorted(owner.identity_digests),
    )


def _merged_owner(owners: list[CacheOwnerUsage]) -> CacheOwnerUsage:
    """One row's figures for the several input identities it carries."""
    if len(owners) == 1:
        return owners[0]
    created = [owner.newest_created_at for owner in owners if owner.newest_created_at is not None]
    newest = max(
        owners,
        key=lambda owner: owner.newest_created_at if owner.newest_created_at is not None else -1.0,
    )
    return dataclasses.replace(
        owners[0],
        generations=sum(owner.generations for owner in owners),
        size_bytes=sum(owner.size_bytes for owner in owners),
        newest_row_count=newest.newest_row_count,
        newest_created_at=max(created) if created else None,
        newest_build_seconds=newest.newest_build_seconds,
        identity_digests=frozenset().union(*(owner.identity_digests for owner in owners)),
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
    build_seconds: float | None = None
    digests: list[str] = []
    if owner is not None and carries:
        generations, size_bytes = owner.generations, owner.size_bytes
        newest_created_at = owner.newest_created_at
        build_seconds = owner.newest_build_seconds
        # Only a row that carries the bytes names the identities, because
        # clearing a row must clear exactly what that row reports.
        digests = sorted(owner.identity_digests)

    if point is None:
        # No point to describe, but the store may still hold this node's data,
        # and this row is the only place it can be accounted for.
        return CacheNodeEntry(
            node_id=node_id,
            generations=generations,
            size_bytes=size_bytes,
            newest_created_at=newest_created_at,
            build_seconds=build_seconds,
            identity_digests=digests,
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
        build_seconds=build_seconds,
        identity_digests=digests,
        unavailable_reason=reason,
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
    # A structured API Input's row names every table it emits.
    readers: dict[str, list[str]] = {}
    for node_id, _point, _reason, digests in resolved:
        for digest in digests:
            if digest in by_digest:
                readers.setdefault(digest, []).append(node_id)
    carrier = {digest: min(sharers) for digest, sharers in readers.items()}

    nodes: list[CacheNodeEntry] = []
    carried: set[str] = set()
    for node_id, point, reason, digests in resolved:
        owner = by_node.get((pipeline, node_id, body.source))
        sharers: list[str] = []
        carries = owner is not None
        if owner is None and digests:
            carried_owners = [
                by_digest[digest]
                for digest in digests
                if digest in by_digest and carrier.get(digest) == node_id
            ]
            owner = _merged_owner(carried_owners) if carried_owners else None
            sharers = sorted(
                {name for digest in digests for name in readers.get(digest, []) if name != node_id}
            )
            carries = owner is not None
        if owner is not None and carries:
            carried |= owner.identity_digests
        nodes.append(_node_entry(node_id, point, reason, owner, carries=carries, sharers=sharers))

    # What no row carries is listed here. The two halves are exhaustive by
    # construction: an owner is either accounted for on a row or in `other`, so
    # the rows, `other` and `unattributed_*` account for the stored data.
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


@router.post("/clear", response_model=CacheClearResponse)
def cache_clear(body: CacheClearRequest) -> CacheClearResponse:
    """Clear the identities a report's row named.

    The row is the unit the user acts on, and a row names exactly the
    identities it reports — so this clears what that row showed and nothing
    else. A digest the store no longer holds is reported as not cleared rather
    than failing: acting on a report a moment out of date is an ordinary race.

    Cache data is regenerable, which is why this needs no confirmation of its
    own; what it must not do is surprise a reader mid-scan, and it does not —
    a generation a reader holds retires when that reader releases it.
    """
    store = NodeSnapshotStore(node_data_project_root())
    cleared: list[str] = []
    freed = 0
    for digest in dict.fromkeys(body.digests):
        held = store.clear_identity(digest)
        if held or store.inputs_root.joinpath(digest).exists():
            cleared.append(digest)
            freed += held
    return CacheClearResponse(cleared=cleared, freed_bytes=freed)
