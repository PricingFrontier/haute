"""Submodel parsing and merging for the pipeline parser.

Handles:
- Extracting canonical ``pipeline.submodel(...)`` registrations from AST
- Parsing individual submodel .py files
- Merging submodel graphs into the parent pipeline graph
  (either flattened for execution or hierarchical for the GUI)
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from haute._ast_helpers import (
    _extract_connect_calls,
    _extract_function_bodies,
    _extract_global_constants_declaration,
    _extract_preamble,
    _extract_preserved_blocks,
    _extract_submodel_meta,
    _is_submodel_authored_decorator,
    _reject_reserved_global_constants_bindings,
    is_submodel_import,
)
from haute._flatten import flatten_graph
from haute._graph_builders import (
    _build_edges,
    _build_rf_nodes,
    _extract_decorated_nodes,
)
from haute._graph_shape import definition_boundary_topology, validate_graph_shape_contracts
from haute._graph_utils import _edge_id, _sanitize_func_name
from haute._parser_bindings import (
    assert_definition_parameters_bound,
    assert_request_frames_named,
)
from haute._parser_conservation import assert_parser_structure_conserved
from haute._submodel_instances import rewrite_submodel_alias_references
from haute._submodel_paths import (
    SUBMODEL_PACKAGE,
    MalformedSubmodelPathError,
    is_pipeline_dir,
    submodel_module_file,
    submodel_module_name,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelInputPort,
    SubmodelOutputPort,
    check_submodel_interface,
)
from haute.errors import ParseError

_Connect4 = tuple[str, str, str | None, str | None]
_SubmodelPortT = TypeVar("_SubmodelPortT", SubmodelInputPort, SubmodelOutputPort)


@dataclass(frozen=True)
class SubmodelRegistration:
    """One canonical occurrence registration in a parent source file."""

    path: str
    name: str
    instance_of: str | None = None
    line: int | None = None
    #: The name ``from modules import <binding>`` binds the definition's module to.
    binding: str | None = None


def _import_form(name: str, occurrence: str) -> str:
    return (
        f"`from {SUBMODEL_PACKAGE} import {name}`, then "
        f"`pipeline.submodel({name}.submodel, {occurrence!r})`"
    )


def submodel_imports(tree: ast.Module) -> dict[str, str]:
    """A pipeline file's submodel imports: each imported name, and the file it imports.

    ``from modules import a, b`` imports ``modules/a.py`` and ``modules/b.py``. Every
    other spelling of an import from ``modules`` (a relative or a dotted one, ``import
    modules...``, an alias, ``*``) and a module imported twice are refused.
    """
    imports: dict[str, str] = {}
    spans = [
        (statement.lineno, statement.end_lineno or statement.lineno) for statement in tree.body
    ]
    for statement in tree.body:
        if not is_submodel_import(statement):
            continue
        line = statement.lineno
        end = statement.end_lineno or line
        if sum(1 for first, last in spans if first <= end and line <= last) > 1:
            # Codegen owns the import's lines; code sharing them would go with it.
            raise ParseError(
                "A submodel import has its lines to itself: other code on them "
                "(after a ';') would be lost when the import is rewritten.",
                line=line,
                remediation="Put the other statement on a line of its own.",
            )
        if not (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == SUBMODEL_PACKAGE
        ):
            raise ParseError(
                f"A submodel module is imported as `from {SUBMODEL_PACKAGE} import <name>`, "
                f"from the {SUBMODEL_PACKAGE}/ folder beside the pipeline; "
                f"{ast.unparse(statement)!r} is not that form.",
                line=line,
                remediation=f"Write `from {SUBMODEL_PACKAGE} import <name>`.",
            )
        for alias in statement.names:
            if alias.name == "*" or alias.asname is not None:
                raise ParseError(
                    f"A submodel module is imported by its own name, "
                    f"`from {SUBMODEL_PACKAGE} import <name>`, without `as` or `*`.",
                    line=line,
                    remediation=f"Write `from {SUBMODEL_PACKAGE} import <name>`.",
                )
            file = submodel_module_file(alias.name)
            try:
                submodel_module_name(file)
            except MalformedSubmodelPathError as exc:
                raise ParseError(
                    str(exc),
                    name=alias.name,
                    line=line,
                    remediation=(
                        f"Rename {file} and its import to a name a node could have, "
                        "then register the renamed module."
                    ),
                ) from exc
            if alias.name in imports:
                raise ParseError("A submodel module is imported twice.", name=alias.name, line=line)
            imports[alias.name] = file
    return imports


def _registration_name(link: ast.Call) -> str:
    """The occurrence name, the registration's second argument (or ``name=``)."""
    line = getattr(link, "lineno", None)
    name_expression: ast.expr | None = None
    if len(link.args) >= 2:
        name_expression = link.args[1]
        if any(kw.arg == "name" for kw in link.keywords):
            raise ParseError(
                "pipeline.submodel() contains a duplicate keyword.", field="name", line=line
            )
    else:
        name_kws = [kw for kw in link.keywords if kw.arg == "name"]
        if len(name_kws) > 1:
            raise ParseError(
                "pipeline.submodel() contains a duplicate keyword.", field="name", line=line
            )
        if name_kws:
            name_expression = name_kws[0].value
    if name_expression is None:
        raise ParseError(
            "pipeline.submodel() requires the occurrence name as its second argument.",
            line=line,
        )
    if not isinstance(name_expression, ast.Constant) or not isinstance(name_expression.value, str):
        raise ParseError(
            "pipeline.submodel() name must be a string literal.",
            field="name",
            line=getattr(name_expression, "lineno", None),
        )
    if not name_expression.value or name_expression.value != name_expression.value.strip():
        raise ParseError(
            "pipeline.submodel() name must be non-empty and unpadded.",
            field="name",
            line=getattr(name_expression, "lineno", None),
        )
    name = name_expression.value
    sanitized_name = _sanitize_func_name(name)
    if sanitized_name != name:
        raise ParseError(
            "Submodel instance name must be a canonical identifier.",
            name=name,
            expected=sanitized_name,
            line=line,
        )
    return name


def _removed_path_form(path: str | None, occurrence: str, line: int | None) -> ParseError:
    """The refusal of a registration by file path, giving the import form that replaces it."""
    try:
        module = submodel_module_name(path) if path is not None else None
    except MalformedSubmodelPathError:
        module = None
    if module is not None:
        remediation = f"Write {_import_form(module, occurrence)}."
    else:
        remediation = (
            f"Move the definition into {SUBMODEL_PACKAGE}/<name>.py beside the pipeline, "
            f"then write {_import_form('<name>', occurrence)}."
        )
    return ParseError(
        f"pipeline.submodel() registers {occurrence!r} by its file path, which was removed: "
        f"a pipeline imports each submodel module and registers its definition. {remediation}",
        path=path,
        line=line,
        remediation=remediation,
    )


def _registration_definition(
    link: ast.Call, imports: Mapping[str, str], occurrence: str
) -> tuple[str, str]:
    """The definition file a registration names, and the imported name it names it by.

    The first argument is ``<module>.submodel`` for a module the file imports from
    ``modules``; a file path (positional or ``file=``) is the removed form.
    """
    line = getattr(link, "lineno", None)
    candidates = [*link.args[:1], *(kw.value for kw in link.keywords if kw.arg == "file")]
    literal = next(
        (
            expression.value
            for expression in candidates
            if isinstance(expression, ast.Constant) and isinstance(expression.value, str)
        ),
        None,
    )
    if literal is not None or any(kw.arg == "file" for kw in link.keywords):
        raise _removed_path_form(literal, occurrence, line)
    if any(kw.arg == "definition" for kw in link.keywords):
        raise ParseError(
            "pipeline.submodel() takes the imported definition as its first argument, "
            "`<module>.submodel`, not as definition=.",
            line=line,
            remediation=f"Write {_import_form('<module>', occurrence)}.",
        )
    definition = link.args[0] if link.args else None
    if not (
        isinstance(definition, ast.Attribute)
        and definition.attr == "submodel"
        and isinstance(definition.value, ast.Name)
    ):
        shown = "nothing" if definition is None else repr(ast.unparse(definition))
        raise ParseError(
            "pipeline.submodel() takes an imported submodel module's definition first, "
            f"`<module>.submodel`, then the occurrence name; it was given {shown}.",
            line=line,
            remediation=f"Write {_import_form('<module>', occurrence)}.",
        )
    module = definition.value.id
    file = imports.get(module)
    if file is None:
        raise ParseError(
            f"pipeline.submodel() registers {module}.submodel, but {module!r} is not imported "
            f"from {SUBMODEL_PACKAGE}.",
            name=module,
            line=line,
            remediation=f"Add `from {SUBMODEL_PACKAGE} import {module}` with the imports.",
        )
    return file, module


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


def _is_submodel_registration(statement: ast.stmt) -> bool:
    """Whether *statement* is a ``pipeline.submodel(...)`` call chain, whatever its arguments."""
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return False
    registers = False
    current: ast.expr = statement.value
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        registers = registers or current.func.attr == "submodel"
        current = current.func.value
    return registers and isinstance(current, ast.Name) and current.id == "pipeline"


def registered_module(statement: ast.stmt) -> str | None:
    """The module ``pipeline.submodel(<module>.submodel, ...)`` registers, if *statement* is one."""
    if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)):
        return None
    call = statement.value
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "submodel"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "pipeline"
        and call.args
    ):
        return None
    definition = call.args[0]
    if (
        isinstance(definition, ast.Attribute)
        and definition.attr == "submodel"
        and isinstance(definition.value, ast.Name)
    ):
        return definition.value.id
    return None


def extract_submodel_registrations(
    tree: ast.Module, *, imports: Mapping[str, str] | None = None
) -> list[SubmodelRegistration]:
    """Extract occurrence-aware registrations while preserving source order.

    A registration names its definition through the file's submodel imports:
    ``from modules import x`` then ``pipeline.submodel(x.submodel, "name")``. They are
    read from *tree*, which then also refuses an import no registration uses, unless
    *imports* gives them, for a caller extracting one statement at a time.
    """
    table = submodel_imports(tree) if imports is None else imports
    registrations: list[SubmodelRegistration] = []
    for node in tree.body:
        if not _is_submodel_registration(node):
            continue
        assert isinstance(node, ast.Expr)
        links: list[ast.Call] = []
        current: ast.expr = node.value
        while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            if current.func.attr == "submodel":
                links.append(current)
            current = current.func.value

        for link in reversed(links):
            line = getattr(link, "lineno", None)
            remediation = (
                "Write pipeline.submodel(<module>.submodel, <name>) and let the definition "
                "declare its id."
            )
            if any(keyword.arg == "label" for keyword in link.keywords):
                raise ParseError(
                    (
                        "pipeline.submodel() no longer accepts label=; "
                        "an occurrence's name is the second argument."
                    ),
                    line=line,
                    remediation=remediation,
                )
            for keyword in link.keywords:
                if keyword.arg in {"definition_id", "instance_id", "alias"}:
                    raise ParseError(
                        f"pipeline.submodel() takes the definition and the occurrence name; "
                        f"{keyword.arg}= is not accepted.",
                        keyword=keyword.arg,
                        line=line,
                        remediation=remediation,
                    )
            name = _registration_name(link)
            path, binding = _registration_definition(link, table, name)
            instance_of = _registration_keyword(link, "instance_of")
            registrations.append(
                SubmodelRegistration(
                    path=path,
                    name=name,
                    instance_of=instance_of,
                    line=line,
                    binding=binding,
                )
            )

    explicit_names: dict[str, int | None] = {}
    for registration in registrations:
        if registration.name in explicit_names:
            raise ParseError(
                "Submodel instance name is duplicated in the parent source.",
                name=registration.name,
                lines=[explicit_names[registration.name], registration.line],
            )
        explicit_names[registration.name] = registration.line
    if imports is None:
        imported_at = {
            alias.name: statement.lineno
            for statement in tree.body
            if isinstance(statement, ast.ImportFrom) and statement.module == SUBMODEL_PACKAGE
            for alias in statement.names
        }
        for registration in registrations:
            line = imported_at.get(registration.binding or "")
            if line is not None and registration.line is not None and line > registration.line:
                raise ParseError(
                    f"pipeline.submodel() registers {registration.binding}.submodel before "
                    f"the file imports {registration.binding!r}: Python runs the file in "
                    "order, so the registration would fail.",
                    name=registration.binding,
                    line=registration.line,
                    remediation="Move the import above the registrations, with the imports.",
                )
        registered = {registration.binding for registration in registrations}
        unused = [module for module in table if module not in registered]
        if unused:
            first = next(
                statement.lineno
                for statement in tree.body
                if isinstance(statement, ast.ImportFrom)
                and statement.module == SUBMODEL_PACKAGE
                and any(alias.name == unused[0] for alias in statement.names)
            )
            raise ParseError(
                f"The pipeline imports {', '.join(repr(module) for module in unused)} from "
                f"{SUBMODEL_PACKAGE} without registering it: a submodel module is imported "
                "only to register its definition.",
                names=unused,
                line=first,
                remediation=(
                    "Register it with pipeline.submodel(<module>.submodel, <name>), or "
                    "remove the import."
                ),
            )
    return registrations


def _submodel_constructor(tree: ast.Module) -> ast.Call:
    """The module-level ``submodel = haute.Submodel(...)`` call."""
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == "submodel"
            and isinstance(node.value, ast.Call)
        ):
            return node.value
    raise ParseError("Submodel source must assign a haute.Submodel constructor.")


def _validate_pipeline_dir(tree: ast.Module, source_file: str, base_dir: Path | None) -> None:
    """Check that the constructor's ``pipeline_dir`` leads to the registering pipeline.

    A standalone run resolves the file's ``config=`` paths from there, so a
    value that disagrees with where the file sits fails here rather than
    reading another pipeline's sidecars.
    """
    keywords = [kw for kw in _submodel_constructor(tree).keywords if kw.arg == "pipeline_dir"]
    if len(keywords) > 1:
        raise ParseError("Submodel constructor contains a duplicate keyword.", field="pipeline_dir")
    value: object = "."
    if keywords:
        try:
            value = ast.literal_eval(keywords[0].value)
        except (SyntaxError, ValueError) as exc:
            raise ParseError(
                "Submodel pipeline_dir must be a literal value.",
                field="pipeline_dir",
                line=getattr(keywords[0].value, "lineno", None),
            ) from exc
    if not is_pipeline_dir(value):
        raise ParseError(
            "Submodel pipeline_dir must be '..' once per folder between this file and the "
            "pipeline that registers it (for example '..' or '../..').",
            field="pipeline_dir",
            value=repr(value),
        )
    if not source_file or base_dir is None:
        return
    source_path = Path(source_file)
    if not source_path.is_absolute():
        source_path = base_dir / source_path
    try:
        folders = source_path.resolve().parent.relative_to(base_dir.resolve()).parts
    except ValueError:
        return
    expected = "/".join([".."] * len(folders)) or "."
    if value != expected:
        raise ParseError(
            f"Submodel pipeline_dir is {value!r}, but the pipeline that registers this file "
            f"is {expected!r} from it; its config= paths would resolve in the wrong folder.",
            field="pipeline_dir",
            source_file=source_file,
            expected=expected,
        )


def _extract_definition_contract(
    tree: ast.Module,
) -> tuple[
    str,
    list[SubmodelInputPort],
    list[SubmodelOutputPort],
]:
    """Return the required literal identity and public ports."""
    constructor = _submodel_constructor(tree)
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
        for raw_port in raw_ports:
            if isinstance(raw_port, Mapping):
                for key in raw_port:
                    if key in {"portId", "label"}:
                        raise ParseError(
                            f"Submodel {field} port declares {key!r}; a public port has one name: "
                            "replace 'portId' and 'label' with 'name'.",
                            field=field,
                            remediation=(
                                "Declare each public port as "
                                "{'name': ..., 'targets': [...]} or {'name': ..., 'source': {...}}."
                            ),
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

    *_base_dir* is the directory of the pipeline that registers the file: its
    ``config=`` references resolve there, and the constructor's ``pipeline_dir``
    must lead there from the file.
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
    # A submodel reads its pipeline's constants: it declares none and binds the
    # reserved name only as the generated ``submodel.global_constants``.
    _extract_global_constants_declaration(tree, receiver="submodel")
    _reject_reserved_global_constants_bindings(tree, receiver="submodel")

    # Nested submodels are capped at one level. Returning the outer child
    # graph while dropping these authored references would corrupt the source
    # on its next save, so the producer contract is enforced as a typed error.
    nested_lines = [
        statement.lineno for statement in tree.body if _is_submodel_registration(statement)
    ]
    if nested_lines:
        raise ParseError(
            "Nested submodels are not supported.",
            source_file=source_file,
            nested_lines=nested_lines,
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
        str(node["func_name"]): [str(name) for name in node["param_names"]] for node in raw_nodes
    }
    graph._parser_edge_parameter_names = {
        str(node["func_name"]): [str(name) for name in node["edge_param_names"]]
        for node in raw_nodes
    }

    (
        graph._parser_definition_id,
        graph._parser_input_ports,
        graph._parser_output_ports,
    ) = _extract_definition_contract(tree)
    assert_request_frames_named(graph, source_file=source_file)
    if graph._parser_input_ports is not None and graph._parser_output_ports is not None:
        try:
            check_submodel_interface(graph, graph._parser_input_ports, graph._parser_output_ports)
        except ValueError as exc:
            # Refused here, the failure is this submodel's own; refused when the
            # definition is built, it would fail the whole document.
            raise ParseError(
                str(exc),
                source_file=source_file,
                remediation=(
                    "Point each port of the submodel at one of its nodes, with no port "
                    "name used twice, or remove the submodel."
                ),
            ) from exc
        assert_definition_parameters_bound(
            graph, graph._parser_input_ports, source_file=source_file
        )
        incoming, outgoing = definition_boundary_topology(
            graph._parser_input_ports, graph._parser_output_ports
        )
        try:
            validate_graph_shape_contracts(
                graph,
                graph_label=f"definition:{source_file}",
                extra_incoming_by_node=incoming,
                extra_outgoing_by_node=outgoing,
            )
        except ParseError as exc:
            # Located, the failure is this submodel's own, which the editor
            # attributes to the node inside it.
            raise ParseError(exc.message, source_file=source_file, **exc.context) from exc
    _validate_pipeline_dir(tree, source_file, _base_dir)
    return graph


def _merge_registered_submodels(
    parent_graph: PipelineGraph,
    submodel_graphs: dict[str, PipelineGraph],
    submodel_files: dict[str, str],
    parent_edges: list[_Connect4],
    registrations: list[SubmodelRegistration],
    *,
    flatten: bool,
    registration_definitions: dict[str, str],
) -> PipelineGraph:
    """Build one shared definition entry and one placeholder per registration."""
    definitions: dict[str, SubmodelDefinition] = {}
    occurrences: dict[str, SubmodelDefinition] = {}
    parent_nodes = list(parent_graph.nodes)
    root_node_ids = {node.id for node in parent_graph.nodes}

    for definition_id, child_graph in submodel_graphs.items():
        if child_graph._parser_input_ports is None or child_graph._parser_output_ports is None:
            raise ParseError(
                "Reusable submodel definitions must declare input_ports and output_ports.",
                definition_id=definition_id,
                file=submodel_files.get(definition_id, ""),
            )
        resolved_def_id = child_graph._parser_definition_id or definition_id
        definition = SubmodelDefinition(
            definitionId=resolved_def_id,
            file=submodel_files.get(definition_id, ""),
            graph=child_graph,
            inputPorts=child_graph._parser_input_ports,
            outputPorts=child_graph._parser_output_ports,
        )
        definitions[resolved_def_id] = definition

    for registration in registrations:
        def_id = registration_definitions.get(registration.path)
        matched_definition = definitions.get(def_id) if def_id is not None else None
        if matched_definition is None:
            raise ParseError(
                "Submodel registration references an unresolved definition.",
                path=registration.path,
                known_definitions=sorted(definitions),
            )
        definition = matched_definition
        if registration.name in root_node_ids:
            raise ParseError(
                "Submodel instance name collides with a parent node id.",
                name=registration.name,
            )
        if registration.name in occurrences:
            raise ParseError(
                "Submodel instance name is duplicated in the parent source.",
                name=registration.name,
            )
        occurrences[registration.name] = definition
        parent_nodes.append(
            GraphNode(
                id=registration.name,
                type="submodel",
                data=NodeData(
                    label=registration.name,
                    description=definition.graph.pipeline_description or "",
                    nodeType=NodeType.SUBMODEL,
                    config={
                        "definitionId": definition.definition_id,
                        "alias": registration.name,
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
        {name: name for name in occurrences},
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
        source_definition = occurrences.get(source)
        target_definition = occurrences.get(target)
        if source_definition is None and target_definition is None:
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

        if source_definition is not None:
            definition = source_definition
            known_outputs = {port.name for port in definition.output_ports}
            if source_port not in known_outputs:
                raise ParseError(
                    "Connection from a submodel instance must name a declared output port.",
                    alias=source,
                    instance_id=source,
                    port_name=source_port,
                    known_ports=sorted(known_outputs),
                )
            actual_source = source
            source_handle = f"out__{source_port}"
        elif source not in root_node_ids:
            raise ParseError(
                "Connection source is neither a parent node nor a submodel alias.",
                source=source,
            )

        if target_definition is not None:
            definition = target_definition
            known_inputs = {port.name for port in definition.input_ports}
            if target_port not in known_inputs:
                raise ParseError(
                    "Connection to a submodel instance must name a declared input port.",
                    alias=target,
                    instance_id=target,
                    port_name=target_port,
                    known_ports=sorted(known_inputs),
                )
            actual_target = target
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
    registration_definitions: dict[str, str],
) -> PipelineGraph:
    """Merge canonical definition metadata and occurrence registrations."""
    return _merge_registered_submodels(
        parent_graph,
        submodel_graphs,
        submodel_files,
        parent_edges,
        registrations,
        flatten=flatten,
        registration_definitions=registration_definitions,
    )
