"""The workbench nodes' tables, given from the project's form (specs/workbench).

A Workbench Input and a Workbench Output keep nothing of the form in their configs: wherever
the pipeline runs from its source, each is given the tables the form folder defines, and the
Workbench Input the sample quote, under the keys the nodes are built from (``tables`` and
``sample``, :data:`haute._config_validation.FORM_GIVEN_KEYS`), so the executor, the
projection and deploy read them as any setting and a node's cache key holds them. The
editor's routes give them to every graph the browser sends
(:func:`haute._flatten.flatten_executable_graph`), each request reading the folder anew;
``haute deploy`` and ``haute app build`` once, into the bundle
(:func:`haute.deploy._config.resolve_config`); the pipeline file's own ``run()`` from the
project the file is in (``haute._node_apply``). A graph without a workbench node reads no
form.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from haute._node_specs import WORKBENCH_NODE_TYPES, node_spec, node_type_table
from haute._types import GraphNode, NodeType, PipelineGraph
from haute._workbench_config import WorkbenchConfigError, read_workbench_config
from haute._workbench_tables import WorkbenchTables, workbench_tables

if TYPE_CHECKING:
    from haute._workbench_form import Cell, FormDocument, FormSpec


def read_project_form(project_root: Path, *, owner: str) -> FormDocument:
    """The form *project_root*'s form folder holds now, for a node *owner* names.

    Refused while the project's workbench is not enabled, naming *owner*, and for a folder
    that cannot be read as a form, naming why (:func:`haute._workbench_folder.read_form_document`).
    """
    # Imported here: the folder module reads the form's files, which a graph without a
    # workbench node never needs.
    from haute._workbench_folder import read_form_document

    config = read_workbench_config(project_root)
    if not config.enabled:
        raise WorkbenchConfigError(
            f"The {owner} reads its tables from the project's workbench, which haute.toml "
            "does not enable."
        )
    return read_form_document(config)


def project_form_tables(
    project_root: Path, *, owner: str, sample: Mapping[str, list[dict[str, Cell]]] | None = None
) -> WorkbenchTables:
    """The tables, sample and response tables *project_root*'s form folder defines now, the
    tables refused naming the table when the pipeline cannot take them
    (:func:`haute._workbench_tables.check_tables`).

    Given *sample*, the sample the workbench view holds, the saved form is read with it in
    place of its own sample: its schema, sheets and Shown when rules decide what is typed, so
    a table or column the saved schema does not have reads as nothing typed."""
    spec = read_project_form(project_root, owner=owner).spec
    if sample is not None:
        spec = spec.model_copy(update={"sample": dict(sample)})
    return workbench_tables(spec)


def _given_input(config: Mapping[str, Any], tables: WorkbenchTables) -> dict[str, Any]:
    return {
        **config,
        "tables": [table.model_dump(mode="json") for table in tables.tables],
        "sample": tables.sample,
    }


def _given_output(config: Mapping[str, Any], tables: WorkbenchTables) -> dict[str, Any]:
    return {
        **config,
        "tables": [table.model_dump(mode="json") for table in tables.response_tables],
    }


#: What a node of each type is given from the form, over its config: a Workbench Input
#: its tables and sample, a Workbench Output its response tables; ``None`` for a type
#: given nothing.
_GIVEN_FROM_FORM: Mapping[
    NodeType, Callable[[Mapping[str, Any], WorkbenchTables], dict[str, Any]] | None
] = node_type_table(
    "given from the form",
    {
        NodeType.API_INPUT: None,
        NodeType.WORKBENCH_INPUT: _given_input,
        NodeType.WORKBENCH_OUTPUT: _given_output,
        NodeType.DATA_INPUT: None,
        NodeType.DATA_OUTPUT: None,
        NodeType.POLARS: None,
        NodeType.EDGE_JOIN: None,
        NodeType.MODEL_SCORE: None,
        NodeType.BANDING: None,
        NodeType.RATING_STEP: None,
        NodeType.OUTPUT: None,
        NodeType.EXPLORE: None,
        NodeType.EXTERNAL_FILE: None,
        NodeType.LIVE_SWITCH: None,
        NodeType.MODELLING: None,
        NodeType.OPTIMISER: None,
        NodeType.SCENARIO_EXPANDER: None,
        NodeType.OPTIMISER_APPLY: None,
        NodeType.CONSTANT: None,
        NodeType.SUBMODEL: None,
        NodeType.SUBMODEL_PORT: None,
    },
)


def given_config(
    node_type: NodeType, config: Mapping[str, Any], tables: WorkbenchTables
) -> dict[str, Any]:
    """*config* with what a node of *node_type* is given from the form: a Workbench Input
    its tables and sample, a Workbench Output its tables, each as
    ``GET /api/workbench/tables`` answers them, over anything it held; any other node's as
    it is (:data:`_GIVEN_FROM_FORM`)."""
    give = _GIVEN_FROM_FORM[node_type]
    return dict(config) if give is None else give(config, tables)


def _workbench_nodes(graph: PipelineGraph) -> Iterator[GraphNode]:
    """Every workbench node of *graph* and of its submodels' definitions."""
    for node in graph.nodes:
        if node.data.nodeType in WORKBENCH_NODE_TYPES:
            yield node
    for definition in (graph.submodels or {}).values():
        yield from _workbench_nodes(definition.graph)


def form_owner(graph: PipelineGraph) -> str | None:
    """The node a form is read for in *graph*, as a refusal names it: its Workbench Input,
    else its Workbench Output; None when it has neither, and needs no form."""
    specs = [node_spec(kind) for kind in {node.data.nodeType for node in _workbench_nodes(graph)}]
    owners = [spec for spec in specs if spec.request_input] or specs
    return owners[0].display_name if owners else None


def with_tables(graph: PipelineGraph, tables: WorkbenchTables) -> PipelineGraph:
    """*graph* with each workbench node, its submodels' too, given *tables*
    (:func:`given_config`). The graph is copied, never changed: a browser's graph, a parsed
    one and a bundle's stay as they were."""
    nodes = [
        node.model_copy(
            update={
                "data": node.data.model_copy(
                    update={"config": given_config(node.data.nodeType, node.data.config, tables)}
                )
            }
        )
        if node.data.nodeType in WORKBENCH_NODE_TYPES
        else node
        for node in graph.nodes
    ]
    submodels = graph.submodels and {
        name: definition.model_copy(update={"graph": with_tables(definition.graph, tables)})
        for name, definition in graph.submodels.items()
    }
    return graph.model_copy(update={"nodes": nodes, "submodels": submodels})


def with_form_tables(
    graph: PipelineGraph,
    *,
    project_root: Path,
    form: FormSpec | None = None,
    sample: Mapping[str, list[dict[str, Cell]]] | None = None,
) -> PipelineGraph:
    """*graph* with its workbench nodes given their tables from the form folder of
    *project_root*, its sample replaced by *sample* when given (the pipeline editor's reads
    of the sample the view holds), or from *form* when given (Preview's pricing of the form
    the view holds); *graph* itself, nothing read, when it has none."""
    if form is not None and sample is not None:
        raise TypeError("with_form_tables takes a form or a sample, not both.")
    owner = form_owner(graph)
    if owner is None:
        return graph
    if form is None:
        return with_tables(graph, project_form_tables(project_root, owner=owner, sample=sample))
    return with_tables(graph, workbench_tables(form))


def connection_errors(graph: PipelineGraph, form: str) -> list[str]:
    """Each connection of *graph*, its workbench nodes given their tables, out of a Workbench
    Input or into a Workbench Output on a port *form*, the form folder's path, does not
    have: a pipeline file written by hand, or saved before the form dropped the table, can
    hold one."""
    ports: dict[str, set[str]] = {}
    for node in graph.nodes:
        if node.data.nodeType in WORKBENCH_NODE_TYPES:
            tables = node.data.config["tables"]
            ports[node.id] = {table["port"] for table in tables if table["columns"]}
    errors: list[str] = []
    for edge in graph.edges:
        if edge.source in ports and edge.sourceHandle not in ports[edge.source]:
            errors.append(
                f"{edge.target!r} takes table {edge.sourceHandle!r} of Workbench Input "
                f"{edge.source!r}, which {form} does not have."
            )
        if edge.target in ports and edge.targetHandle not in ports[edge.target]:
            errors.append(
                f"{edge.source!r} fills table {edge.targetHandle!r} of Workbench Output "
                f"{edge.target!r}, which {form} does not have."
            )
    return errors
