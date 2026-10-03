"""Graph flattening for canonical reusable submodel instances."""

from __future__ import annotations

from haute._executable_names import executable_name_violations, format_name_violations
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


def flatten_executable_graph(graph: PipelineGraph) -> PipelineGraph:
    """Refuse a browser graph with an executable-name violation, then flatten it.

    Every route that runs a graph the browser sent prepares it here, so the
    canvas never runs a pipeline its own saved file could not run. Internal
    flattening (a staged dissolve, save's structural checks) uses
    :func:`flatten_graph`, since a partly dissolved graph legitimately repeats
    a shared definition's child names.
    """
    violations = executable_name_violations(graph)
    if violations:
        raise ParseError(format_name_violations(violations))
    return flatten_graph(graph)
