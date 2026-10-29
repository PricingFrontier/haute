"""Graph flattening for canonical reusable submodel instances."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from haute._executable_names import executable_name_violations, format_name_violations
from haute._submodel_instances import (
    expand_submodel_instances,
    resolve_submodel_instances,
)
from haute._types import PipelineGraph
from haute._workbench_nodes import with_form_tables
from haute.errors import ParseError

if TYPE_CHECKING:
    from haute._workbench_form import FormSpec


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


def flatten_executable_graph(
    graph: PipelineGraph, *, form: FormSpec | None = None
) -> PipelineGraph:
    """Refuse a browser graph with an executable-name violation, flatten it, and give its
    workbench nodes their tables.

    Every route that runs a graph the browser sent prepares it here, so the
    canvas never runs a pipeline its own saved file could not run, and a
    workbench node runs on the tables the project's form folder defines now,
    read anew for each request, or on *form*'s, the form the workbench view
    holds, for Preview's pricing (:func:`haute._workbench_nodes.with_form_tables`).
    Internal flattening (a staged dissolve, save's structural checks) uses
    :func:`flatten_graph`, since a partly dissolved graph legitimately repeats
    a shared definition's child names.
    """
    violations = executable_name_violations(graph)
    if violations:
        raise ParseError(format_name_violations(violations))
    # The project is the working directory, as it is for the rest of the server.
    return with_form_tables(flatten_graph(graph), project_root=Path.cwd(), form=form)


def flatten_with_form_tables(graph: PipelineGraph, *, project_root: Path) -> PipelineGraph:
    """*graph* flattened as a run takes it, its workbench nodes given their tables from
    *project_root*'s form folder (:func:`haute._workbench_nodes.with_form_tables`): for
    a graph read from disk or planned in process, which the assistant resolves and runs."""
    return with_form_tables(flatten_graph(graph), project_root=project_root)
