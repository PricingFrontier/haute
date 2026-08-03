"""Graph flattening for canonical reusable submodel instances."""

from __future__ import annotations

from haute._submodel_instances import (
    expand_submodel_instances,
    resolve_submodel_instances,
)
from haute._types import PipelineGraph
from haute.errors import ParseError


def flatten_graph(
    graph: PipelineGraph,
    *,
    target_instance_id: str | None = None,
) -> PipelineGraph:
    """Dissolve every occurrence, or one occurrence selected by instance id."""
    instances = resolve_submodel_instances(graph)
    if target_instance_id is not None and target_instance_id not in instances:
        raise ParseError(
            "Submodel instance not found.",
            instance_id=target_instance_id,
            known_instance_ids=sorted(instances),
        )
    if not instances:
        return graph
    selected = None if target_instance_id is None else {target_instance_id}
    return expand_submodel_instances(graph, instance_ids=selected)
