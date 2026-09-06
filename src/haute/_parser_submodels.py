"""Submodel parsing and merging for the pipeline parser.

Handles:
- Extracting canonical ``pipeline.submodel(...)`` registrations from AST
- Parsing individual submodel .py files
- Merging submodel graphs into the parent pipeline graph
  (either flattened for execution or hierarchical for the GUI)
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from haute._ast_helpers import (
    _extract_connect_calls,
    _extract_function_bodies,
    _extract_preamble,
    _extract_preserved_blocks,
    _extract_submodel_meta,
    _is_submodel_authored_decorator,
)
from haute._flatten import flatten_graph
from haute._graph_builders import (
    _build_edges,
    _build_rf_nodes,
    _extract_decorated_nodes,
)
from haute._graph_utils import _edge_id
from haute._parser_conservation import assert_parser_structure_conserved
from haute._submodel_instances import rewrite_submodel_alias_references
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.errors import ParseError

_Connect4 = tuple[str, str, str | None, str | None]
_SubmodelPortT = TypeVar("_SubmodelPortT", SubmodelInputPort, SubmodelOutputPort)


def _submodel_path_expr(link: ast.Call) -> ast.expr | None:
    """Return the path argument node of a ``submodel(...)`` link, if any.

    Accepts the positional form ``submodel("path")`` and the keyword form
    ``submodel(file="path")``. Returns ``None`` when the call carries no
    path argument at all (e.g. ``pipeline.submodel()``).
    """
    if link.args:
        return link.args[0]
    for kw in link.keywords:
        if kw.arg == "file":
            return kw.value
    return None


@dataclass(frozen=True)
class SubmodelRegistration:
    """One canonical occurrence registration in a parent source file."""

    path: str
    definition_id: str
    instance_id: str
    alias: str
    instance_of: str | None = None
    label: str | None = None
    line: int | None = None


def _registration_keyword(link: ast.Call, name: str) -> str | None:
    matches = [keyword for keyword in link.keywords if keyword.arg == name]
    if not matches:
        return None
    if len(matches) > 1:
        raise ParseError(
            "pipeline.submodel() contains a duplicate keyword.",
            field=name,
            line=getattr(link, "lineno", None),
        )
    expression = matches[0].value
    if not isinstance(expression, ast.Constant) or not isinstance(expression.value, str):
        raise ParseError(
            f"pipeline.submodel() {name} must be a string literal.",
            field=name,
            line=getattr(expression, "lineno", None),
        )
    if not expression.value or expression.value != expression.value.strip():
        raise ParseError(
            f"pipeline.submodel() {name} must be non-empty and unpadded.",
            field=name,
            line=getattr(expression, "lineno", None),
        )
    return expression.value


def extract_submodel_registrations(tree: ast.Module) -> list[SubmodelRegistration]:
    """Extract occurrence-aware registrations while preserving source order."""
    registrations: list[SubmodelRegistration] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue

        links: list[ast.Call] = []
        current: ast.expr = node.value
        while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            if current.func.attr == "submodel":
                links.append(current)
            current = current.func.value
        if not links or not (isinstance(current, ast.Name) and current.id == "pipeline"):
            continue

        for link in reversed(links):
            path_expression = _submodel_path_expr(link)
            if path_expression is None:
                raise ParseError(
                    "pipeline.submodel() requires a file path.",
                    line=getattr(link, "lineno", None),
                )
            if not (
                isinstance(path_expression, ast.Constant) and isinstance(path_expression.value, str)
            ):
                raise ParseError(
                    "pipeline.submodel() path must be a string literal; the "
                    f"non-literal expression {ast.unparse(path_expression)!r} cannot be "
                    "resolved at parse time and its submodel would be dropped.",
                    line=getattr(path_expression, "lineno", None),
                )
            path = path_expression.value
            if not path or path != path.strip():
                raise ParseError(
                    "pipeline.submodel() path must be non-empty and unpadded.",
                    line=getattr(path_expression, "lineno", None),
                )
            definition_id = _registration_keyword(link, "definition_id")
            instance_id = _registration_keyword(link, "instance_id")
            alias = _registration_keyword(link, "alias")
            instance_of = _registration_keyword(link, "instance_of")
            label = _registration_keyword(link, "label")
            missing_fields = [
                field
                for field, value in {
                    "definition_id": definition_id,
                    "instance_id": instance_id,
                    "alias": alias,
                }.items()
                if value is None
            ]
            if missing_fields:
                raise ParseError(
                    "pipeline.submodel() requires explicit stable identity fields: "
                    "definition_id, instance_id, and alias.",
                    path=path,
                    missing_fields=missing_fields,
                    line=getattr(link, "lineno", None),
                )
            assert definition_id is not None
            assert instance_id is not None
            assert alias is not None
            registrations.append(
                SubmodelRegistration(
                    path=path,
                    definition_id=definition_id,
                    instance_id=instance_id,
                    alias=alias,
                    instance_of=instance_of,
                    label=label,
                    line=getattr(link, "lineno", None),
                )
            )

    explicit_aliases: dict[str, int | None] = {}
    explicit_instances: dict[str, int | None] = {}
    for registration in registrations:
        if registration.alias in explicit_aliases:
            raise ParseError(
                "Submodel instance alias is duplicated in the parent source.",
                alias=registration.alias,
                lines=[explicit_aliases[registration.alias], registration.line],
            )
        if registration.instance_id in explicit_instances:
            raise ParseError(
                "Submodel instance id is duplicated in the parent source.",
                instance_id=registration.instance_id,
                lines=[explicit_instances[registration.instance_id], registration.line],
            )
        explicit_aliases[registration.alias] = registration.line
        explicit_instances[registration.instance_id] = registration.line
    return registrations


def _extract_definition_contract(
    tree: ast.Module,
) -> tuple[
    str,
    list[SubmodelInputPort],
    list[SubmodelOutputPort],
]:
    """Return the required literal identity and public ports."""
    constructor: ast.Call | None = None
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == "submodel"
            and isinstance(node.value, ast.Call)
        ):
            constructor = node.value
            break
    if constructor is None:
        raise ParseError("Submodel source must assign a haute.Submodel constructor.")

    values: dict[str, object] = {}
    for keyword in constructor.keywords:
        if keyword.arg == "outputs":
            raise ParseError(
                "Submodel outputs is not supported; declare input_ports and output_ports.",
                field="outputs",
                line=getattr(keyword.value, "lineno", None),
            )
        if keyword.arg not in {"definition_id", "input_ports", "output_ports"}:
            continue
        if keyword.arg in values:
            raise ParseError(
                "Submodel constructor contains a duplicate keyword.",
                field=keyword.arg,
                line=getattr(keyword.value, "lineno", None),
            )
        try:
            values[keyword.arg] = ast.literal_eval(keyword.value)
        except (SyntaxError, ValueError) as exc:
            raise ParseError(
                f"Submodel {keyword.arg} must be a literal value.",
                field=keyword.arg,
                line=getattr(keyword.value, "lineno", None),
            ) from exc

    missing_fields = sorted({"definition_id", "input_ports", "output_ports"} - set(values))
    if missing_fields:
        raise ParseError(
            "Submodel requires definition_id, input_ports, and output_ports.",
            missing_fields=missing_fields,
        )

    raw_definition_id = values["definition_id"]
    if (
        not isinstance(raw_definition_id, str)
        or not raw_definition_id
        or raw_definition_id != raw_definition_id.strip()
    ):
        raise ParseError(
            "Submodel definition_id must be a non-empty unpadded string.",
            field="definition_id",
        )

    def parse_ports(
        field: str,
        model: type[_SubmodelPortT],
    ) -> list[_SubmodelPortT]:
        raw_ports = values[field]
        if not isinstance(raw_ports, list):
            raise ParseError(
                f"Submodel {field} must be a literal list.",
                field=field,
            )
        try:
            return [model.model_validate(port) for port in raw_ports]
        except ValueError as exc:
            raise ParseError(
                f"Submodel {field} contains an invalid public port.",
                field=field,
            ) from exc

    return (
        raw_definition_id,
        parse_ports("input_ports", SubmodelInputPort),
        parse_ports("output_ports", SubmodelOutputPort),
    )


def parse_submodel_source(
    source: str,
    source_file: str = "",
    _base_dir: Path | None = None,
) -> PipelineGraph:
    """Parse submodel source code and return a PipelineGraph.

    *_base_dir* is the project root for resolving ``config=`` references.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ParseError(
            "Submodel file has syntax errors; its graph cannot be recovered.",
            source_file=source_file,
            line=exc.lineno,
            offset=exc.offset,
        ) from exc

    submodel_name, submodel_desc = _extract_submodel_meta(tree)

    # Nested submodels are capped at one level. Returning the outer child
    # graph while dropping these authored references would corrupt the source
    # on its next save, so the producer contract is enforced as a typed error.
    nested_registrations = extract_submodel_registrations(tree)
    if nested_registrations:
        raise ParseError(
            "Nested submodels are not supported.",
            source_file=source_file,
            nested_paths=[registration.path for registration in nested_registrations],
        )

    func_bodies = _extract_function_bodies(source, tree=tree)
    raw_nodes = _extract_decorated_nodes(
        tree,
        _is_submodel_authored_decorator,
        func_bodies,
        _base_dir,
        source=source,
    )

    explicit_connects = _extract_connect_calls(tree, receiver="submodel")
    edges = _build_edges(raw_nodes, explicit_connects)
    rf_nodes = _build_rf_nodes(raw_nodes)
    assert_parser_structure_conserved(
        raw_nodes=raw_nodes,
        explicit_connects=explicit_connects,
        root_nodes=rf_nodes,
        root_edges=edges,
    )

    graph = PipelineGraph(
        nodes=rf_nodes,
        edges=edges,
        pipeline_name=submodel_name,
        pipeline_description=submodel_desc,
        preamble=_extract_preamble(
            source,
            tree=tree,
            receiver="submodel",
            constructor_name="Submodel",
        ),
        preserved_blocks=_extract_preserved_blocks(source),
        source_file=source_file,
    )
    graph._parser_parameter_names = {
        str(node["func_name"]): [str(name) for name in node.get("param_names", ())]
        for node in raw_nodes
    }

    (
        graph._parser_definition_id,
        graph._parser_input_ports,
        graph._parser_output_ports,
    ) = _extract_definition_contract(tree)
    return graph


def _merge_registered_submodels(
    parent_graph: PipelineGraph,
    submodel_graphs: dict[str, PipelineGraph],
    submodel_files: dict[str, str],
    parent_edges: list[_Connect4],
    registrations: list[SubmodelRegistration],
    *,
    flatten: bool,
) -> PipelineGraph:
    """Build one shared definition entry and one placeholder per registration."""
    definitions: dict[str, SubmodelDefinition] = {}
    aliases: dict[str, tuple[str, SubmodelDefinition]] = {}
    parent_nodes = list(parent_graph.nodes)
    root_node_ids = {node.id for node in parent_graph.nodes}

    for definition_id, child_graph in submodel_graphs.items():
        if child_graph._parser_definition_id != definition_id:
            raise ParseError(
                "Parent and child submodel definition ids do not match.",
                definition_id=definition_id,
                child_definition_id=child_graph._parser_definition_id,
                file=submodel_files.get(definition_id, ""),
            )
        if child_graph._parser_input_ports is None or child_graph._parser_output_ports is None:
            raise ParseError(
                "Reusable submodel definitions must declare input_ports and output_ports.",
                definition_id=definition_id,
                file=submodel_files.get(definition_id, ""),
            )
        definitions[definition_id] = SubmodelDefinition(
            definitionId=definition_id,
            file=submodel_files.get(definition_id, ""),
            graph=child_graph,
            inputPorts=child_graph._parser_input_ports,
            outputPorts=child_graph._parser_output_ports,
        )

    for registration in registrations:
        definition = definitions.get(registration.definition_id)
        if definition is None:
            raise ParseError(
                "Submodel registration references an unresolved definition.",
                path=registration.path,
                definition_id=registration.definition_id,
                known_definitions=sorted(definitions),
            )
        if registration.alias in root_node_ids:
            raise ParseError(
                "Submodel instance alias collides with a parent node id.",
                alias=registration.alias,
            )
        if registration.instance_id in root_node_ids:
            raise ParseError(
                "Submodel instance id collides with a parent node id.",
                instance_id=registration.instance_id,
            )
        if registration.alias in aliases:
            raise ParseError(
                "Submodel instance alias is duplicated in the parent source.",
                alias=registration.alias,
            )
        aliases[registration.alias] = (registration.instance_id, definition)
        parent_nodes.append(
            GraphNode(
                id=registration.instance_id,
                type="submodel",
                data=NodeData(
                    label=registration.label or registration.alias,
                    description=definition.graph.pipeline_description or "",
                    nodeType=NodeType.SUBMODEL,
                    config={
                        "definitionId": registration.definition_id,
                        "alias": registration.alias,
                        **(
                            {"instanceOf": registration.instance_of}
                            if registration.instance_of is not None
                            else {}
                        ),
                    },
                ),
            )
        )

    parent_nodes = rewrite_submodel_alias_references(
        parent_nodes,
        {alias: instance_id for alias, (instance_id, _definition) in aliases.items()},
    )
    merged_edges = list(parent_graph.edges)
    existing = {
        (
            edge.source,
            edge.target,
            edge.sourceHandle,
            edge.targetHandle,
            edge.sourcePort,
            edge.targetPort,
        )
        for edge in merged_edges
    }
    for source, target, source_port, target_port in parent_edges:
        source_registration = aliases.get(source)
        target_registration = aliases.get(target)
        if source_registration is None and target_registration is None:
            # Root-to-root connections already live in the parent graph; any
            # other endpoint is neither a parent node nor an occurrence alias
            # and is rejected as dangling by the conservation gate.
            continue

        actual_source = source
        actual_target = target
        source_handle = source_port
        target_handle = target_port
        hidden_source_port: str | None = None
        hidden_target_port: str | None = None

        if source_registration is not None:
            instance_id, definition = source_registration
            known_outputs = {port.port_id for port in definition.output_ports}
            if source_port not in known_outputs:
                raise ParseError(
                    "Connection from a submodel instance must name a declared output port.",
                    alias=source,
                    instance_id=instance_id,
                    port_id=source_port,
                    known_ports=sorted(known_outputs),
                )
            actual_source = instance_id
            source_handle = f"out__{source_port}"
        elif source not in root_node_ids:
            raise ParseError(
                "Connection source is neither a parent node nor a submodel alias.",
                source=source,
            )

        if target_registration is not None:
            instance_id, definition = target_registration
            known_inputs = {port.port_id for port in definition.input_ports}
            if target_port not in known_inputs:
                raise ParseError(
                    "Connection to a submodel instance must name a declared input port.",
                    alias=target,
                    instance_id=instance_id,
                    port_id=target_port,
                    known_ports=sorted(known_inputs),
                )
            actual_target = instance_id
            target_handle = f"in__{target_port}"
        elif target not in root_node_ids:
            raise ParseError(
                "Connection target is neither a parent node nor a submodel alias.",
                target=target,
            )

        identity = (
            actual_source,
            actual_target,
            source_handle,
            target_handle,
            hidden_source_port,
            hidden_target_port,
        )
        if identity in existing:
            continue
        existing.add(identity)
        merged_edges.append(
            GraphEdge(
                id=_edge_id(
                    actual_source,
                    actual_target,
                    source_handle,
                    target_handle,
                    hidden_source_port=hidden_source_port,
                    hidden_target_port=hidden_target_port,
                ),
                source=actual_source,
                target=actual_target,
                sourceHandle=source_handle,
                targetHandle=target_handle,
                sourcePort=hidden_source_port,
                targetPort=hidden_target_port,
            )
        )

    hierarchical_payload = parent_graph.model_dump(mode="python", by_alias=True)
    hierarchical_payload.update(
        {
            "nodes": parent_nodes,
            "edges": merged_edges,
            "submodels": definitions,
        }
    )
    hierarchical = PipelineGraph.model_validate(hierarchical_payload)
    return flatten_graph(hierarchical) if flatten else hierarchical


def merge_submodels(
    parent_graph: PipelineGraph,
    submodel_graphs: dict[str, PipelineGraph],
    submodel_files: dict[str, str],
    parent_edges: list[_Connect4],
    *,
    registrations: list[SubmodelRegistration],
    flatten: bool = False,
) -> PipelineGraph:
    """Merge canonical definition metadata and occurrence registrations."""
    return _merge_registered_submodels(
        parent_graph,
        submodel_graphs,
        submodel_files,
        parent_edges,
        registrations,
        flatten=flatten,
    )
