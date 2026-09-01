"""Side-effect-free editor recovery for readable pipeline documents."""

from __future__ import annotations

import ast
import keyword
import re
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from haute._ast_helpers import (
    _chained_receiver_calls,
    _connect_call_edge,
    _extract_function_bodies,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
    _get_decorator_kwargs,
    _get_docstring,
    _is_pipeline_authored_decorator,
    _is_submodel_authored_decorator,
)
from haute._cache import canonical_json
from haute._config_builder import _resolve_node_config
from haute._editor_identities import api_input_source_handles, resolve_editor_identity
from haute._graph_builders import (
    PipelineNodeSkeleton,
    _edge_param_names_for_node,
    _extract_decorated_node_skeletons,
    _resolve_node_skeleton,
)
from haute._graph_utils import executable_input_name
from haute._hashing import content_hash_bytes
from haute._io import read_user_bytes_and_text, read_user_text
from haute._logging import get_logger
from haute._parser_regex import (
    RecoveredFunctionFragment,
    _parse_decorator_kwargs_regex,
    recover_pipeline_fragments,
)
from haute._parser_submodels import (
    SubmodelRegistration,
    _extract_definition_contract,
    extract_submodel_registrations,
    parse_submodel_source,
)
from haute._pipeline_revision import pipeline_recovery_revision
from haute._sidecar import (
    SidecarReadResult,
    _normalise_sidecar_sources,
    read_sidecar_state,
)
from haute._submodel_paths import resolve_submodel_reference
from haute._types import NODE_TYPE_TO_DECORATOR, GraphNode, NodeType, PipelineGraph
from haute.errors import ConfigError, HauteError, ParseError
from haute.parser import _infer_parse_base_dir, parse_pipeline_source
from haute.schemas import (
    PipelineDiagnosticScope,
    PipelineDocumentCapabilities,
    PipelineEditorDocument,
    PipelineElementAvailability,
    PipelineLoadStatus,
    PipelineRecoveryDiagnostic,
    RecoveryGraphSnapshot,
    RecoveryPipelineEdge,
    RecoveryPipelineNode,
    RecoverySourceSpan,
    RecoverySubmodelDefinition,
    RecoveryUnresolvedConnection,
)

logger = get_logger(component="pipeline.recovery")

_MAX_DIAGNOSTICS = 200


class _SourceCaptures:
    """First-read byte/text capture for every source artifact in one load.

    The recovery revision must authenticate the exact bytes this document was
    built from, so each parent/child source is read once and every later
    consumer — including the revision manifest — reuses that capture instead
    of re-reading a file a concurrent edit may have changed.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, tuple[Path, bytes, str]] = {}

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve()).casefold()

    def read(self, path: Path) -> tuple[bytes, str]:
        key = self._key(path)
        captured = self._by_key.get(key)
        if captured is None:
            raw, text = read_user_bytes_and_text(path)
            captured = (path, raw, text)
            self._by_key[key] = captured
        return captured[1], captured[2]

    def known_bytes(self) -> dict[Path, bytes]:
        return {path: raw for path, raw, _text in self._by_key.values()}


@dataclass(slots=True)
class _RecoveredCandidate:
    authored_id: str
    recovery_id: str
    decorator_name: str
    node_type: NodeType | None
    description: str
    config: dict[str, Any] | None
    config_reference: str | None
    param_names: tuple[str, ...]
    edge_param_names: tuple[str, ...]
    span: RecoverySourceSpan
    availability: PipelineElementAvailability
    diagnostic_ids: list[str]
    endpoint_ids: tuple[str, ...] = ()
    submodel_input_ports: tuple[str, ...] = ()
    submodel_output_ports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RecoveredConnection:
    source_authored_id: str
    target_authored_id: str
    source_port: str | None = None
    target_port: str | None = None
    span: RecoverySourceSpan | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedRecoveryEdge:
    source: _RecoveredCandidate
    target: _RecoveredCandidate
    source_authored_id: str
    target_authored_id: str
    source_handle: str | None
    target_handle: str | None
    source_port: str | None
    target_port: str | None
    span: RecoverySourceSpan | None
    ordinal: int


def _wire_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _span(
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> RecoverySourceSpan:
    return RecoverySourceSpan(
        start_line=max(1, start_line),
        start_column=max(0, start_column),
        end_line=max(1, end_line),
        end_column=max(0, end_column),
    )


def _diagnostic(
    *,
    code: str,
    scope: PipelineDiagnosticScope,
    message: str,
    source_file: str,
    element_id: str | None = None,
    source_span: RecoverySourceSpan | None = None,
    remediation: str | None = None,
    incident_id: str | None = None,
) -> PipelineRecoveryDiagnostic:
    safe_message = (message.strip() or "The authored element is invalid.")[:1024]
    identity = canonical_json(
        {
            "code": code,
            "scope": scope,
            "source_file": source_file,
            "element_id": element_id,
            "source_span": source_span.model_dump() if source_span is not None else None,
            "message": safe_message,
        }
    )
    digest = content_hash_bytes(identity.encode("utf-8"))[:16]
    return PipelineRecoveryDiagnostic(
        diagnostic_id=f"{code}:{digest}",
        code=code,
        scope=scope,
        message=safe_message,
        element_id=element_id,
        source_file=source_file,
        source_span=source_span,
        remediation=remediation,
        incident_id=incident_id,
    )


def _exception_message(exc: BaseException) -> str:
    if isinstance(exc, HauteError) and exc.message:
        return exc.message
    if isinstance(exc, SyntaxError):
        return "Pipeline source contains invalid Python syntax."
    return "The authored element could not be resolved."


def _node_failure_code(exc: BaseException) -> str:
    if isinstance(exc, ConfigError):
        return "node_config_invalid"
    if isinstance(exc, ParseError):
        return "node_parse_invalid"
    return "node_contract_invalid"


def _unavailable_candidate(
    authored: PipelineNodeSkeleton | RecoveredFunctionFragment,
    *,
    recovery_id: str,
    node_type: NodeType | None,
    description: str,
    config_reference: str | None,
    span: RecoverySourceSpan,
    diagnostic: PipelineRecoveryDiagnostic,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> _RecoveredCandidate:
    """Record *diagnostic* and return its non-canonical candidate."""
    diagnostics.append(diagnostic)
    return _RecoveredCandidate(
        authored_id=authored.authored_id,
        recovery_id=recovery_id,
        decorator_name=authored.decorator_name,
        node_type=node_type,
        description=description,
        config=None,
        config_reference=config_reference,
        param_names=tuple(authored.param_names),
        edge_param_names=tuple(authored.edge_param_names),
        span=span,
        availability="unavailable",
        diagnostic_ids=[diagnostic.diagnostic_id],
    )


def _candidate_from_ast(
    skeleton: PipelineNodeSkeleton,
    *,
    recovery_id: str,
    source_file: str,
    base_dir: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> _RecoveredCandidate:
    span = _span(
        skeleton.start_line,
        skeleton.start_column,
        skeleton.end_line,
        skeleton.end_column,
    )
    config_reference: str | None = None
    try:
        decorator_kwargs = _get_decorator_kwargs(skeleton.decorator)
    except HauteError:
        decorator_kwargs = {}
    raw_reference = decorator_kwargs.get("config")
    if isinstance(raw_reference, str) and raw_reference.strip():
        config_reference = raw_reference.replace("\\", "/")

    if skeleton.explicit_node_type is None:
        return _unavailable_candidate(
            skeleton,
            recovery_id=recovery_id,
            node_type=None,
            description=skeleton.description,
            config_reference=config_reference,
            span=span,
            diagnostic=_diagnostic(
                code="node_decorator_unknown",
                scope="node",
                message=(
                    f"The authored @{skeleton.decorator_name} node type is not available "
                    "in this Haute version."
                ),
                source_file=source_file,
                element_id=recovery_id,
                source_span=span,
                remediation=(
                    "Install a compatible node implementation or update the source explicitly."
                ),
            ),
            diagnostics=diagnostics,
        )
    try:
        if skeleton.is_async:
            raise ParseError("Pipeline node bodies must be synchronous; remove the async keyword.")
        raw_node = _resolve_node_skeleton(skeleton, base_dir)
    except HauteError as exc:
        return _unavailable_candidate(
            skeleton,
            recovery_id=recovery_id,
            node_type=skeleton.explicit_node_type,
            description=skeleton.description,
            config_reference=config_reference,
            span=span,
            diagnostic=_diagnostic(
                code=_node_failure_code(exc),
                scope="node",
                message=_exception_message(exc),
                source_file=source_file,
                element_id=recovery_id,
                source_span=span,
                remediation="Open the referenced source or config and correct this node.",
            ),
            diagnostics=diagnostics,
        )
    except Exception:
        incident_id = uuid4().hex
        logger.error(
            "pipeline_recovery_node_unexpected",
            source_file=source_file,
            node_id=skeleton.authored_id,
            incident_id=incident_id,
            exc_info=True,
        )
        return _unavailable_candidate(
            skeleton,
            recovery_id=recovery_id,
            node_type=skeleton.explicit_node_type,
            description=skeleton.description,
            config_reference=config_reference,
            span=span,
            diagnostic=_diagnostic(
                code="node_recovery_internal_error",
                scope="node",
                message="This node could not be recovered because of an internal error.",
                source_file=source_file,
                element_id=recovery_id,
                source_span=span,
                remediation="Check the server logs with the incident id and report the defect.",
                incident_id=incident_id,
            ),
            diagnostics=diagnostics,
        )

    return _RecoveredCandidate(
        authored_id=skeleton.authored_id,
        recovery_id=recovery_id,
        decorator_name=skeleton.decorator_name,
        node_type=raw_node["node_type"],
        description=str(raw_node["description"]),
        config=dict(raw_node["config"]),
        config_reference=config_reference,
        param_names=tuple(str(value) for value in raw_node["param_names"]),
        edge_param_names=tuple(str(value) for value in raw_node["edge_param_names"]),
        span=span,
        availability="ready",
        diagnostic_ids=[],
    )


def _candidate_from_regex(
    fragment: RecoveredFunctionFragment,
    *,
    recovery_id: str,
    source_file: str,
    base_dir: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> _RecoveredCandidate:
    span = _span(fragment.start_line, 0, fragment.end_line, 0)
    config_reference: str | None = None
    description = ""
    try:
        decorator_kwargs = _parse_decorator_kwargs_regex(fragment.decorator_text)
        raw_reference = decorator_kwargs.get("config")
        if isinstance(raw_reference, str) and raw_reference.strip():
            config_reference = raw_reference.replace("\\", "/")

        if fragment.explicit_node_type is None:
            return _unavailable_candidate(
                fragment,
                recovery_id=recovery_id,
                node_type=None,
                description="",
                config_reference=config_reference,
                span=span,
                diagnostic=_diagnostic(
                    code="node_decorator_unknown",
                    scope="node",
                    message=(
                        f"The authored @{fragment.decorator_name} node type is not "
                        "available in this Haute version."
                    ),
                    source_file=source_file,
                    element_id=recovery_id,
                    source_span=span,
                    remediation=(
                        "Install a compatible node implementation or update the source explicitly."
                    ),
                ),
                diagnostics=diagnostics,
            )

        function_source = (
            f"{fragment.decorator_text}\n"
            f"def {fragment.authored_id}({fragment.params_text}):\n"
            f"{fragment.body_text}"
        )
        function_tree = ast.parse(function_source)
        function = next(
            (item for item in function_tree.body if isinstance(item, ast.FunctionDef)),
            None,
        )
        if function is None:
            raise ParseError("The decorated function could not be recovered.")
        description = _get_docstring(function)
        node_type, config = _resolve_node_config(
            decorator_kwargs,
            fragment.body_text,
            list(fragment.param_names),
            len(fragment.param_names),
            base_dir,
            func_name=fragment.authored_id,
            explicit_node_type=fragment.explicit_node_type,
            edge_param_names=list(fragment.edge_param_names),
        )
    except (HauteError, SyntaxError) as exc:
        return _unavailable_candidate(
            fragment,
            recovery_id=recovery_id,
            node_type=fragment.explicit_node_type,
            description=description,
            config_reference=config_reference,
            span=span,
            diagnostic=_diagnostic(
                code=(
                    "node_syntax_invalid"
                    if isinstance(exc, SyntaxError)
                    else _node_failure_code(exc)
                ),
                scope="node",
                message=_exception_message(exc),
                source_file=source_file,
                element_id=recovery_id,
                source_span=span,
                remediation="Open the referenced source or config and correct this node.",
            ),
            diagnostics=diagnostics,
        )
    except Exception:
        incident_id = uuid4().hex
        logger.error(
            "pipeline_recovery_regex_node_unexpected",
            source_file=source_file,
            node_id=fragment.authored_id,
            incident_id=incident_id,
            exc_info=True,
        )
        return _unavailable_candidate(
            fragment,
            recovery_id=recovery_id,
            node_type=fragment.explicit_node_type,
            description=description,
            config_reference=config_reference,
            span=span,
            diagnostic=_diagnostic(
                code="node_recovery_internal_error",
                scope="node",
                message="This node could not be recovered because of an internal error.",
                source_file=source_file,
                element_id=recovery_id,
                source_span=span,
                remediation="Check the server logs with the incident id and report the defect.",
                incident_id=incident_id,
            ),
            diagnostics=diagnostics,
        )

    return _RecoveredCandidate(
        authored_id=fragment.authored_id,
        recovery_id=recovery_id,
        decorator_name=fragment.decorator_name,
        node_type=node_type,
        description=description,
        config=config,
        config_reference=config_reference,
        param_names=fragment.param_names,
        edge_param_names=fragment.edge_param_names,
        span=span,
        availability="ready",
        diagnostic_ids=[],
    )


def _candidate_ids(
    identities: list[tuple[str, int]],
) -> list[str]:
    counts = Counter(identity for identity, _line in identities)
    return [
        f"{identity}@L{line}" if counts[identity] > 1 else identity for identity, line in identities
    ]


def _edge_recovery_id(
    source_id: str,
    target_id: str,
    source_handle: str | None,
    target_handle: str | None,
    ordinal: int,
) -> str:
    payload = canonical_json([source_id, target_id, source_handle, target_handle, ordinal])
    return f"edge:{content_hash_bytes(payload.encode('utf-8'))[:16]}"


def _mark_duplicate_candidates(
    candidates: list[_RecoveredCandidate],
    *,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> None:
    """Make every colliding authored identity explicit and non-canonical."""
    counts = Counter(candidate.authored_id for candidate in candidates)
    for candidate in candidates:
        if counts[candidate.authored_id] < 2:
            continue
        diagnostic = _diagnostic(
            code="node_identity_duplicate",
            scope="node",
            message=(
                f"The authored node id {candidate.authored_id!r} is duplicated; "
                f"this occurrence is represented as {candidate.recovery_id!r}."
            ),
            source_file=source_file,
            element_id=candidate.recovery_id,
            source_span=candidate.span,
            remediation="Rename one of the decorated functions so every node id is unique.",
        )
        diagnostics.append(diagnostic)
        candidate.availability = "unavailable"
        candidate.diagnostic_ids.append(diagnostic.diagnostic_id)


def _connection_diagnostic(
    *,
    code: str,
    message: str,
    recovery_id: str,
    connection: _RecoveredConnection,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> str:
    diagnostic = _diagnostic(
        code=code,
        scope="edge",
        message=message,
        source_file=source_file,
        element_id=recovery_id,
        source_span=connection.span,
        remediation="Correct the connection endpoint names, ports, or duplicate declarations.",
    )
    diagnostics.append(diagnostic)
    return diagnostic.diagnostic_id


def _unresolved_connection(
    connection: _RecoveredConnection,
    *,
    ordinal: int,
    code: str,
    message: str,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
    source_recovery_id: str | None = None,
    target_recovery_id: str | None = None,
) -> RecoveryUnresolvedConnection:
    recovery_id = _edge_recovery_id(
        connection.source_authored_id,
        connection.target_authored_id,
        connection.source_port,
        connection.target_port,
        ordinal,
    )
    diagnostic_id = _connection_diagnostic(
        code=code,
        message=message,
        recovery_id=recovery_id,
        connection=connection,
        source_file=source_file,
        diagnostics=diagnostics,
    )
    return RecoveryUnresolvedConnection(
        recovery_id=recovery_id,
        source_recovery_id=source_recovery_id,
        target_recovery_id=target_recovery_id,
        source_authored_id=connection.source_authored_id,
        target_authored_id=connection.target_authored_id,
        source_handle=connection.source_port,
        target_handle=connection.target_port,
        source_port=connection.source_port,
        target_port=connection.target_port,
        source_span=connection.span,
        diagnostic_ids=[diagnostic_id],
    )


def _connect_endpoint_expression(call: ast.Call, role: str) -> ast.expr | None:
    positional_index = 0 if role == "source" else 1
    if len(call.args) > positional_index:
        return call.args[positional_index]
    return next(
        (keyword.value for keyword in call.keywords if keyword.arg == role),
        None,
    )


def _connection_endpoint_label(call: ast.Call, role: str) -> str:
    expression = _connect_endpoint_expression(call, role)
    if expression is None:
        return f"<missing-{role}>"
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value or f"<empty-{role}>"
    try:
        rendered = ast.unparse(expression).strip()
    except Exception:  # pragma: no cover - valid AST expressions unparse
        rendered = ""
    return rendered or f"<invalid-{role}>"


def _recover_ast_connections(
    tree: ast.Module,
    *,
    receiver: str,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> tuple[list[_RecoveredConnection], list[RecoveryUnresolvedConnection]]:
    """Extract each top-level connection independently with an exact span."""
    connections: list[_RecoveredConnection] = []
    unresolved: list[RecoveryUnresolvedConnection] = []
    ordinal = 0
    for statement in tree.body:
        for link in _chained_receiver_calls(statement, receiver=receiver, method="connect"):
            span = _span(
                link.lineno,
                link.col_offset,
                link.end_lineno or link.lineno,
                link.end_col_offset or link.col_offset,
            )
            placeholder = _RecoveredConnection(
                source_authored_id=_connection_endpoint_label(link, "source"),
                target_authored_id=_connection_endpoint_label(link, "target"),
                span=span,
            )
            try:
                edge = _connect_call_edge(link, receiver)
            except HauteError as exc:
                unresolved.append(
                    _unresolved_connection(
                        placeholder,
                        ordinal=ordinal,
                        code="connection_declaration_invalid",
                        message=_exception_message(exc),
                        source_file=source_file,
                        diagnostics=diagnostics,
                    )
                )
            else:
                if edge is None:
                    unresolved.append(
                        _unresolved_connection(
                            placeholder,
                            ordinal=ordinal,
                            code="connection_declaration_invalid",
                            message=(
                                f"The authored {receiver}.connect() declaration does not "
                                "contain string source and target identities."
                            ),
                            source_file=source_file,
                            diagnostics=diagnostics,
                        )
                    )
                else:
                    connections.append(_RecoveredConnection(*edge, span=span))
            ordinal += 1
    return connections, unresolved


def _is_receiver_method_statement(
    statement: ast.stmt,
    *,
    receiver: str,
    method: str,
) -> bool:
    return bool(_chained_receiver_calls(statement, receiver=receiver, method=method))


def _recover_ast_submodel_registrations(
    tree: ast.Module,
    *,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> list[SubmodelRegistration]:
    """Recover independent valid registrations when one sibling is malformed."""
    registrations: list[SubmodelRegistration] = []
    for statement in tree.body:
        if not _is_receiver_method_statement(
            statement,
            receiver="pipeline",
            method="submodel",
        ):
            continue
        try:
            registrations.extend(
                extract_submodel_registrations(ast.Module(body=[statement], type_ignores=[]))
            )
        except HauteError as exc:
            span = _span(
                statement.lineno,
                statement.col_offset,
                statement.end_lineno or statement.lineno,
                statement.end_col_offset or statement.col_offset,
            )
            diagnostics.append(
                _diagnostic(
                    code="submodel_registration_invalid",
                    scope="submodel",
                    message=_exception_message(exc),
                    source_file=source_file,
                    source_span=span,
                    remediation="Correct the submodel registration identity and file path.",
                )
            )
    return registrations


def _build_recovery_graph(
    candidates: list[_RecoveredCandidate],
    connections: Sequence[_RecoveredConnection | tuple[str, str, str | None, str | None]],
    *,
    source_file: str,
    positions: dict[str, dict[str, float]],
    diagnostics: list[PipelineRecoveryDiagnostic],
    initial_unresolved: list[RecoveryUnresolvedConnection] | None = None,
) -> tuple[
    list[RecoveryPipelineNode],
    list[RecoveryPipelineEdge],
    list[RecoveryUnresolvedConnection],
]:
    _mark_duplicate_candidates(
        candidates,
        source_file=source_file,
        diagnostics=diagnostics,
    )

    by_authored: dict[str, list[_RecoveredCandidate]] = {}
    for candidate in candidates:
        endpoint_ids = candidate.endpoint_ids or (candidate.authored_id,)
        for endpoint_id in dict.fromkeys(endpoint_ids):
            by_authored.setdefault(endpoint_id, []).append(candidate)

    edge_specs: list[_ResolvedRecoveryEdge] = []
    unresolved: list[RecoveryUnresolvedConnection] = list(initial_unresolved or [])
    explicit_pairs: set[tuple[str, str]] = set()
    seen_declarations: set[tuple[str, str, str | None, str | None]] = set()
    normalised_connections: list[_RecoveredConnection] = [
        connection
        if isinstance(connection, _RecoveredConnection)
        else _RecoveredConnection(*connection)
        for connection in connections
    ]
    for ordinal, connection in enumerate(normalised_connections):
        source_authored_id = connection.source_authored_id
        target_authored_id = connection.target_authored_id
        identity = (
            source_authored_id,
            target_authored_id,
            connection.source_port,
            connection.target_port,
        )
        sources = by_authored.get(source_authored_id, [])
        targets = by_authored.get(target_authored_id, [])
        if identity in seen_declarations:
            unresolved.append(
                _unresolved_connection(
                    connection,
                    ordinal=ordinal,
                    code="connection_duplicate",
                    message="This authored connection duplicates an earlier declaration.",
                    source_file=source_file,
                    diagnostics=diagnostics,
                    source_recovery_id=(sources[0].recovery_id if len(sources) == 1 else None),
                    target_recovery_id=(targets[0].recovery_id if len(targets) == 1 else None),
                )
            )
            continue
        seen_declarations.add(identity)

        if len(sources) != 1 or len(targets) != 1:
            ambiguous = len(sources) > 1 or len(targets) > 1
            unresolved.append(
                _unresolved_connection(
                    connection,
                    ordinal=ordinal,
                    code=(
                        "connection_endpoint_ambiguous"
                        if ambiguous
                        else "connection_endpoint_missing"
                    ),
                    message=(
                        "The authored connection endpoint matches more than one recovered node."
                        if ambiguous
                        else "The authored connection references an endpoint that is not present."
                    ),
                    source_file=source_file,
                    diagnostics=diagnostics,
                    source_recovery_id=(sources[0].recovery_id if len(sources) == 1 else None),
                    target_recovery_id=(targets[0].recovery_id if len(targets) == 1 else None),
                )
            )
            continue

        source_candidate = sources[0]
        target_candidate = targets[0]
        source_handle = connection.source_port
        target_handle = connection.target_port
        source_port: str | None = None
        target_port: str | None = None
        if source_candidate.node_type == NodeType.SUBMODEL:
            if connection.source_port not in source_candidate.submodel_output_ports:
                unresolved.append(
                    _unresolved_connection(
                        connection,
                        ordinal=ordinal,
                        code="submodel_output_port_invalid",
                        message="The submodel connection does not name a declared output port.",
                        source_file=source_file,
                        diagnostics=diagnostics,
                        source_recovery_id=source_candidate.recovery_id,
                        target_recovery_id=target_candidate.recovery_id,
                    )
                )
                continue
            source_port = connection.source_port
            source_handle = f"out__{connection.source_port}"
        if target_candidate.node_type == NodeType.SUBMODEL:
            if connection.target_port not in target_candidate.submodel_input_ports:
                unresolved.append(
                    _unresolved_connection(
                        connection,
                        ordinal=ordinal,
                        code="submodel_input_port_invalid",
                        message="The submodel connection does not name a declared input port.",
                        source_file=source_file,
                        diagnostics=diagnostics,
                        source_recovery_id=source_candidate.recovery_id,
                        target_recovery_id=target_candidate.recovery_id,
                    )
                )
                continue
            target_port = connection.target_port
            target_handle = f"in__{connection.target_port}"

        explicit_pairs.add((source_candidate.recovery_id, target_candidate.recovery_id))
        edge_specs.append(
            _ResolvedRecoveryEdge(
                source=source_candidate,
                target=target_candidate,
                source_authored_id=source_authored_id,
                target_authored_id=target_authored_id,
                source_handle=source_handle,
                target_handle=target_handle,
                source_port=source_port,
                target_port=target_port,
                span=connection.span,
                ordinal=ordinal,
            )
        )

    seen_implicit: set[tuple[str, str]] = set()
    implicit_ordinal = len(normalised_connections)
    for target_candidate in candidates:
        raw = {
            "func_name": target_candidate.authored_id,
            "node_type": target_candidate.node_type,
            "config": target_candidate.config or {},
            "param_names": list(target_candidate.param_names),
            "edge_param_names": list(target_candidate.edge_param_names),
        }
        try:
            edge_params = _edge_param_names_for_node(raw)
        except HauteError:
            edge_params = list(target_candidate.edge_param_names)
        for source_id in edge_params:
            sources = by_authored.get(source_id, [])
            if not sources or (len(sources) == 1 and sources[0] is target_candidate):
                continue
            connection = _RecoveredConnection(
                source_authored_id=source_id,
                target_authored_id=target_candidate.authored_id,
                span=target_candidate.span,
            )
            if len(sources) != 1:
                unresolved.append(
                    _unresolved_connection(
                        connection,
                        ordinal=implicit_ordinal,
                        code="implicit_binding_ambiguous",
                        message=(
                            "This function parameter matches more than one recovered upstream node."
                        ),
                        source_file=source_file,
                        diagnostics=diagnostics,
                        target_recovery_id=target_candidate.recovery_id,
                    )
                )
                implicit_ordinal += 1
                continue
            pair = (sources[0].recovery_id, target_candidate.recovery_id)
            if pair in explicit_pairs or pair in seen_implicit:
                continue
            seen_implicit.add(pair)
            edge_specs.append(
                _ResolvedRecoveryEdge(
                    source=sources[0],
                    target=target_candidate,
                    source_authored_id=source_id,
                    target_authored_id=target_candidate.authored_id,
                    source_handle=None,
                    target_handle=None,
                    source_port=None,
                    target_port=None,
                    span=target_candidate.span,
                    ordinal=implicit_ordinal,
                )
            )
            implicit_ordinal += 1

    # Graph-shape validation is currently node-local (Explore is the only
    # topology-constrained canonical type).  Attribute the failure to that
    # node and continue validating independent siblings.
    for candidate in candidates:
        if candidate.availability != "ready" or candidate.node_type != NodeType.EXPLORE:
            continue
        incoming = [edge for edge in edge_specs if edge.target is candidate]
        outgoing = [edge for edge in edge_specs if edge.source is candidate]
        if len(incoming) == 1 and not outgoing:
            continue
        message = (
            "Explore nodes must have exactly one incoming edge."
            if len(incoming) != 1
            else "Explore nodes cannot have outgoing edges."
        )
        diagnostic = _diagnostic(
            code="node_topology_invalid",
            scope="node",
            message=message,
            source_file=source_file,
            element_id=candidate.recovery_id,
            source_span=candidate.span,
            remediation="Correct the node's incoming or outgoing connections.",
        )
        diagnostics.append(diagnostic)
        candidate.availability = "unavailable"
        candidate.diagnostic_ids.append(diagnostic.diagnostic_id)

    adjacency: dict[str, list[str]] = {}
    candidate_order = {candidate.recovery_id: index for index, candidate in enumerate(candidates)}
    candidate_by_id = {candidate.recovery_id: candidate for candidate in candidates}
    for edge in edge_specs:
        adjacency.setdefault(edge.source.recovery_id, []).append(edge.target.recovery_id)
    for downstream_ids in adjacency.values():
        downstream_ids.sort(key=lambda item: (candidate_order[item], item))

    availability: dict[str, PipelineElementAvailability] = {
        candidate.recovery_id: candidate.availability for candidate in candidates
    }
    blocking_paths: dict[str, list[str]] = {}
    queue: deque[tuple[str, list[str]]] = deque(
        (candidate.recovery_id, [candidate.recovery_id])
        for candidate in candidates
        if candidate.availability == "unavailable"
    )
    while queue:
        blocker, path = queue.popleft()
        for downstream_id in adjacency.get(blocker, []):
            if availability[downstream_id] == "unavailable" or downstream_id in blocking_paths:
                continue
            downstream_candidate = candidate_by_id[downstream_id]
            availability[downstream_id] = "blocked"
            blocking_paths[downstream_id] = [*path, downstream_candidate.recovery_id]
            queue.append((downstream_id, blocking_paths[downstream_id]))

    handles_by_source: dict[str, list[str]] = {}
    for edge in edge_specs:
        if edge.source_handle is not None:
            handles = handles_by_source.setdefault(edge.source.recovery_id, [])
            if edge.source_handle not in handles:
                handles.append(edge.source_handle)
    nodes: list[RecoveryPipelineNode] = []
    for index, candidate in enumerate(candidates):
        position = positions.get(
            candidate.authored_id,
            {"x": float(index * 300), "y": 0.0},
        )
        resolved_identity = None
        if candidate.node_type is not None:
            try:
                alias = (
                    candidate.config.get("alias")
                    if isinstance(candidate.config, dict)
                    and isinstance(candidate.config.get("alias"), str)
                    else None
                )
                source_handles = handles_by_source.get(candidate.recovery_id, [])
                if candidate.node_type == NodeType.API_INPUT and isinstance(candidate.config, dict):
                    source_handles = list(api_input_source_handles(candidate.config))
                elif candidate.node_type == NodeType.SUBMODEL:
                    source_handles = [
                        f"out__{port_id}" for port_id in candidate.submodel_output_ports
                    ]
                resolved_identity = resolve_editor_identity(
                    node_type=candidate.node_type,
                    label=candidate.authored_id,
                    source_handles=source_handles,
                    submodel_alias=alias,
                    config_reference_override=candidate.config_reference,
                )
            except (HauteError, ValueError):
                resolved_identity = None
        nodes.append(
            RecoveryPipelineNode(
                recovery_id=candidate.recovery_id,
                authored_id=candidate.authored_id,
                label=candidate.authored_id,
                decorator_name=candidate.decorator_name,
                node_type=str(candidate.node_type) if candidate.node_type is not None else None,
                description=candidate.description,
                availability=availability[candidate.recovery_id],
                display_position=position,
                config=candidate.config,
                config_reference=candidate.config_reference,
                function_name=(
                    resolved_identity.function_name if resolved_identity else candidate.authored_id
                ),
                default_input_name=(
                    resolved_identity.default_input_name if resolved_identity else None
                ),
                source_handle_input_names=(
                    resolved_identity.source_handle_input_names if resolved_identity else {}
                ),
                source_file=source_file,
                source_span=candidate.span,
                diagnostic_ids=candidate.diagnostic_ids,
                blocking_path=blocking_paths.get(candidate.recovery_id, []),
            )
        )

    edges: list[RecoveryPipelineEdge] = []
    for edge in edge_specs:
        source_availability = availability[edge.source.recovery_id]
        target_availability = availability[edge.target.recovery_id]
        edge_availability: PipelineElementAvailability = (
            "ready" if source_availability == target_availability == "ready" else "blocked"
        )
        input_name: str | None = None
        source_candidate = candidate_by_id[edge.source.recovery_id]
        if edge_availability == "ready" and source_candidate.node_type is not None:
            alias = (
                source_candidate.config.get("alias")
                if isinstance(source_candidate.config, dict)
                and isinstance(source_candidate.config.get("alias"), str)
                else None
            )
            try:
                input_name = executable_input_name(
                    node_type=source_candidate.node_type,
                    label=source_candidate.authored_id,
                    source_handle=edge.source_handle,
                    submodel_alias=alias,
                )
            except ValueError:
                input_name = None
        edges.append(
            RecoveryPipelineEdge(
                recovery_id=_edge_recovery_id(
                    edge.source.recovery_id,
                    edge.target.recovery_id,
                    edge.source_handle,
                    edge.target_handle,
                    edge.ordinal,
                ),
                source_recovery_id=edge.source.recovery_id,
                target_recovery_id=edge.target.recovery_id,
                source_authored_id=edge.source_authored_id,
                target_authored_id=edge.target_authored_id,
                source_handle=edge.source_handle,
                target_handle=edge.target_handle,
                source_port=edge.source_port,
                target_port=edge.target_port,
                input_name=input_name,
                availability=edge_availability,
                source_span=edge.span,
                blocking_path=blocking_paths.get(edge.target.recovery_id, []),
            )
        )
    return nodes, edges, unresolved


def _sidecar_diagnostic(
    result: SidecarReadResult,
    *,
    source_file: str,
) -> PipelineRecoveryDiagnostic | None:
    if result.state in {"absent", "valid"}:
        return None
    return _diagnostic(
        code="sidecar_corrupt" if result.state == "corrupt" else "sidecar_unreadable",
        scope="pipeline",
        message=(
            "The editor sidecar contains invalid JSON or invalid fields."
            if result.state == "corrupt"
            else "The editor sidecar could not be read."
        ),
        source_file=source_file,
        remediation="Repair or remove the sidecar explicitly; loading has not changed it.",
    )


def _sidecar_values(
    result: SidecarReadResult,
) -> tuple[dict[str, dict[str, float]], list[str], str | None, bool]:
    if result.state == "absent":
        return {}, ["live"], "live", True
    if result.state != "valid" or result.data is None:
        return {}, [], None, False
    sources = _normalise_sidecar_sources(result.data.sources) or ["live"]
    active = result.data.active_source
    if active not in sources:
        return result.data.positions, [], None, False
    return result.data.positions, sources, active, True


def _canonical_snapshot(
    graph: PipelineGraph,
    *,
    source_path: Path,
    project_root: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> RecoveryGraphSnapshot:
    source_file = _wire_path(source_path, project_root)
    sidecar = read_sidecar_state(source_path)
    positions, _sources, _active, _trusted = _sidecar_values(sidecar)
    sidecar_issue = _sidecar_diagnostic(sidecar, source_file=source_file)
    if sidecar_issue is not None:
        diagnostics.append(sidecar_issue)

    connected_handles_by_source: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.sourceHandle is not None:
            handles = connected_handles_by_source.setdefault(edge.source, [])
            if edge.sourceHandle not in handles:
                handles.append(edge.sourceHandle)

    def source_handles_for(node: GraphNode) -> list[str]:
        if node.data.nodeType == NodeType.API_INPUT:
            return list(api_input_source_handles(node.data.config))
        if node.data.nodeType == NodeType.SUBMODEL:
            definition_id = node.data.config.get("definitionId")
            definition = (
                (graph.submodels or {}).get(definition_id)
                if isinstance(definition_id, str)
                else None
            )
            if definition is None:
                raise ValueError(f"Submodel node {node.id!r} references a missing definition.")
            return [f"out__{port.port_id}" for port in definition.output_ports]
        if node.data.nodeType == NodeType.SUBMODEL_PORT:
            return connected_handles_by_source.get(node.id, [])
        return []

    nodes: list[RecoveryPipelineNode] = []
    for node in graph.nodes:
        alias = (
            node.data.config.get("alias")
            if isinstance(node.data.config.get("alias"), str)
            else None
        )
        identity = resolve_editor_identity(
            node_type=node.data.nodeType,
            label=node.data.label,
            source_handles=source_handles_for(node),
            submodel_alias=alias,
        )
        nodes.append(
            RecoveryPipelineNode(
                recovery_id=node.id,
                authored_id=node.id,
                label=node.data.label,
                decorator_name=NODE_TYPE_TO_DECORATOR.get(node.data.nodeType, "submodel"),
                node_type=str(node.data.nodeType),
                description=node.data.description,
                availability="ready",
                display_position=positions.get(node.id, node.position),
                config=dict(node.data.config),
                config_reference=identity.config_reference,
                function_name=identity.function_name,
                default_input_name=identity.default_input_name,
                source_handle_input_names=identity.source_handle_input_names,
                source_file=source_file,
            )
        )
    source_nodes = {node.id: node for node in graph.nodes}
    edges = [
        RecoveryPipelineEdge(
            recovery_id=edge.id,
            source_recovery_id=edge.source,
            target_recovery_id=edge.target,
            source_authored_id=edge.source,
            target_authored_id=edge.target,
            source_handle=edge.sourceHandle,
            target_handle=edge.targetHandle,
            source_port=edge.sourcePort,
            target_port=edge.targetPort,
            input_name=executable_input_name(
                node_type=source_nodes[edge.source].data.nodeType,
                label=source_nodes[edge.source].data.label,
                source_handle=edge.sourceHandle,
                submodel_alias=(
                    source_nodes[edge.source].data.config.get("alias")
                    if isinstance(source_nodes[edge.source].data.config.get("alias"), str)
                    else None
                ),
            ),
            availability="ready",
        )
        for edge in graph.edges
    ]
    submodels: dict[str, RecoverySubmodelDefinition] = {}
    for definition_id, definition in (graph.submodels or {}).items():
        child_path, _config_base = resolve_submodel_reference(
            definition.file,
            pipeline_dir=source_path.parent,
            project_root=project_root,
        )
        submodels[definition_id] = RecoverySubmodelDefinition(
            definition_id=definition_id,
            file=definition.file,
            availability="ready",
            graph=_canonical_snapshot(
                definition.graph,
                source_path=child_path,
                project_root=project_root,
                diagnostics=diagnostics,
            ),
            input_ports=[
                port.model_dump(mode="json", by_alias=True) for port in definition.input_ports
            ],
            input_port_input_names={
                port.port_id: executable_input_name(
                    node_type=NodeType.SUBMODEL_PORT, label="", source_handle=port.port_id
                )
                for port in definition.input_ports
            },
            output_ports=[
                port.model_dump(mode="json", by_alias=True) for port in definition.output_ports
            ],
        )
    return RecoveryGraphSnapshot(
        nodes=nodes,
        edges=edges,
        submodels=submodels or None,
    )


def _port_ids(ports: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(port.get("portId"))
        for port in ports
        if isinstance(port.get("portId"), str) and port.get("portId")
    )


def _recover_submodel_snapshot(
    child_path: Path,
    *,
    project_root: Path,
    config_base: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
    captures: _SourceCaptures,
) -> tuple[RecoveryGraphSnapshot, list[dict[str, Any]], list[dict[str, Any]]]:
    """Best-effort child graph recovery without making it canonical."""
    child_source_file = _wire_path(child_path, project_root)
    try:
        _raw, source = captures.read(child_path)
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return RecoveryGraphSnapshot(), [], []

    sidecar = read_sidecar_state(child_path)
    positions, _sources, _active, _trusted = _sidecar_values(sidecar)
    sidecar_issue = _sidecar_diagnostic(sidecar, source_file=child_source_file)
    if sidecar_issue is not None:
        diagnostics.append(sidecar_issue)

    bodies = _extract_function_bodies(source, tree=tree)
    skeletons = _extract_decorated_node_skeletons(
        tree,
        _is_submodel_authored_decorator,
        bodies,
        source=source,
    )
    identities = [(item.authored_id, item.start_line) for item in skeletons]
    candidates = [
        _candidate_from_ast(
            skeleton,
            recovery_id=recovery_id,
            source_file=child_source_file,
            base_dir=config_base,
            diagnostics=diagnostics,
        )
        for skeleton, recovery_id in zip(
            skeletons,
            _candidate_ids(identities),
            strict=True,
        )
    ]
    connections, initially_unresolved = _recover_ast_connections(
        tree,
        receiver="submodel",
        source_file=child_source_file,
        diagnostics=diagnostics,
    )
    nodes, edges, unresolved = _build_recovery_graph(
        candidates,
        connections,
        source_file=child_source_file,
        positions=positions,
        diagnostics=diagnostics,
        initial_unresolved=initially_unresolved,
    )
    try:
        _definition_id, input_ports, output_ports = _extract_definition_contract(tree)
    except HauteError:
        input_payload: list[dict[str, Any]] = []
        output_payload: list[dict[str, Any]] = []
    else:
        input_payload = [port.model_dump(mode="json", by_alias=True) for port in input_ports]
        output_payload = [port.model_dump(mode="json", by_alias=True) for port in output_ports]
    return (
        RecoveryGraphSnapshot(
            nodes=nodes,
            edges=edges,
            unresolved_connections=unresolved,
        ),
        input_payload,
        output_payload,
    )


def _unavailable_submodel_definition(
    registration: SubmodelRegistration,
    *,
    graph: RecoveryGraphSnapshot | None = None,
    input_ports: list[dict[str, Any]] | None = None,
    output_ports: list[dict[str, Any]] | None = None,
    diagnostic_ids: list[str] | None = None,
) -> RecoverySubmodelDefinition:
    return RecoverySubmodelDefinition(
        definition_id=registration.definition_id,
        file=registration.path,
        availability="unavailable",
        diagnostic_ids=list(diagnostic_ids or []),
        graph=graph or RecoveryGraphSnapshot(),
        input_ports=list(input_ports or []),
        input_port_input_names={
            port_id: executable_input_name(
                node_type=NodeType.SUBMODEL_PORT, label="", source_handle=port_id
            )
            for port_id in _port_ids(list(input_ports or []))
        },
        output_ports=list(output_ports or []),
    )


def _recover_unavailable_submodel_definition(
    registration: SubmodelRegistration,
    child_path: Path,
    *,
    project_root: Path,
    config_base: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
    captures: _SourceCaptures,
    code: str,
    message: str,
    remediation: str,
    incident_id: str | None = None,
) -> RecoverySubmodelDefinition:
    """Recover a child snapshot while keeping its definition non-canonical."""
    before_count = len(diagnostics)
    graph, input_ports, output_ports = _recover_submodel_snapshot(
        child_path,
        project_root=project_root,
        config_base=config_base,
        diagnostics=diagnostics,
        captures=captures,
    )
    diagnostic = _diagnostic(
        code=code,
        scope="submodel",
        message=message,
        source_file=_wire_path(child_path, project_root),
        element_id=registration.definition_id,
        remediation=remediation,
        incident_id=incident_id,
    )
    diagnostics.insert(before_count, diagnostic)
    return _unavailable_submodel_definition(
        registration,
        graph=graph,
        input_ports=input_ports,
        output_ports=output_ports,
        diagnostic_ids=[diagnostic.diagnostic_id],
    )


def _recover_registered_submodels(
    registrations: list[SubmodelRegistration],
    *,
    parent_path: Path,
    project_root: Path,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
    captures: _SourceCaptures,
) -> tuple[dict[str, RecoverySubmodelDefinition] | None, list[_RecoveredCandidate]]:
    """Resolve each definition once and retain every occurrence independently."""
    if not registrations:
        return None, []

    definitions: dict[str, RecoverySubmodelDefinition] = {}
    aliases = Counter(registration.alias for registration in registrations)
    definition_sources: dict[str, set[str]] = {}
    for registration in registrations:
        try:
            child_path, _config_base = resolve_submodel_reference(
                registration.path,
                pipeline_dir=parent_path.parent,
                project_root=project_root,
            )
        except ValueError:
            normalised_path = registration.path.replace("\\", "/").casefold()
            source_key = f"invalid:{normalised_path}"
        else:
            source_key = f"resolved:{str(child_path.resolve()).casefold()}"
        definition_sources.setdefault(registration.definition_id, set()).add(source_key)
    conflicting_definitions = {
        definition_id for definition_id, sources in definition_sources.items() if len(sources) > 1
    }
    occurrence_ids = _candidate_ids(
        [(registration.instance_id, registration.line or 1) for registration in registrations]
    )

    for registration in registrations:
        if registration.definition_id in definitions:
            continue
        span = _span(
            registration.line or 1,
            0,
            registration.line or 1,
            0,
        )
        if registration.definition_id in conflicting_definitions:
            diagnostic = _diagnostic(
                code="submodel_definition_duplicate",
                scope="submodel",
                message="One submodel definition id resolves to more than one file.",
                source_file=source_file,
                element_id=registration.definition_id,
                source_span=span,
                remediation="Give each submodel file a unique definition id.",
            )
            diagnostics.append(diagnostic)
            definitions[registration.definition_id] = _unavailable_submodel_definition(
                registration,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue
        try:
            child_path, config_base = resolve_submodel_reference(
                registration.path,
                pipeline_dir=parent_path.parent,
                project_root=project_root,
            )
        except ValueError as exc:
            diagnostic = _diagnostic(
                code="submodel_path_invalid",
                scope="submodel",
                message=str(exc),
                source_file=source_file,
                element_id=registration.definition_id,
                source_span=span,
                remediation="Use a project-contained relative submodel path.",
            )
            diagnostics.append(diagnostic)
            definitions[registration.definition_id] = _unavailable_submodel_definition(
                registration,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue

        if not child_path.is_file():
            diagnostic = _diagnostic(
                code="submodel_file_missing",
                scope="submodel",
                message=f"Referenced submodel file {registration.path!r} does not exist.",
                source_file=source_file,
                element_id=registration.definition_id,
                source_span=span,
                remediation="Restore the file or update the registration path.",
            )
            diagnostics.append(diagnostic)
            definitions[registration.definition_id] = _unavailable_submodel_definition(
                registration,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue

        try:
            _child_raw, child_source = captures.read(child_path)
            child_graph = parse_submodel_source(
                child_source,
                source_file=str(child_path),
                _base_dir=config_base,
            )
        except (HauteError, OSError, UnicodeError) as exc:
            code = (
                "submodel_syntax_invalid"
                if isinstance(exc, ParseError) and isinstance(exc.__cause__, SyntaxError)
                else "submodel_definition_invalid"
            )
            definitions[registration.definition_id] = _recover_unavailable_submodel_definition(
                registration,
                child_path,
                project_root=project_root,
                config_base=config_base,
                diagnostics=diagnostics,
                captures=captures,
                code=code,
                message=_exception_message(exc),
                remediation=("Open the submodel source and correct the diagnosed definition."),
            )
            continue
        except Exception:  # noqa: BLE001 - named submodel recovery isolation boundary
            incident_id = uuid4().hex
            logger.error(
                "pipeline_recovery_submodel_unexpected",
                source_file=_wire_path(child_path, project_root),
                definition_id=registration.definition_id,
                incident_id=incident_id,
                exc_info=True,
            )
            definitions[registration.definition_id] = _recover_unavailable_submodel_definition(
                registration,
                child_path,
                project_root=project_root,
                config_base=config_base,
                diagnostics=diagnostics,
                captures=captures,
                code="submodel_recovery_internal_error",
                message=("This submodel could not be recovered because of an internal error."),
                remediation=("Check the server logs with the incident id and report the defect."),
                incident_id=incident_id,
            )
            continue

        input_ports = [
            port.model_dump(mode="json", by_alias=True)
            for port in (child_graph._parser_input_ports or [])
        ]
        output_ports = [
            port.model_dump(mode="json", by_alias=True)
            for port in (child_graph._parser_output_ports or [])
        ]
        if child_graph._parser_definition_id != registration.definition_id:
            diagnostic = _diagnostic(
                code="submodel_definition_id_mismatch",
                scope="submodel",
                message="The registration and submodel file declare different definition ids.",
                source_file=_wire_path(child_path, project_root),
                element_id=registration.definition_id,
                remediation="Make the registered and authored definition ids match.",
            )
            diagnostics.append(diagnostic)
            definitions[registration.definition_id] = _unavailable_submodel_definition(
                registration,
                graph=_canonical_snapshot(
                    child_graph,
                    source_path=child_path,
                    project_root=project_root,
                    diagnostics=diagnostics,
                ),
                input_ports=input_ports,
                output_ports=output_ports,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue
        definitions[registration.definition_id] = RecoverySubmodelDefinition(
            definition_id=registration.definition_id,
            file=registration.path,
            availability="ready",
            graph=_canonical_snapshot(
                child_graph,
                source_path=child_path,
                project_root=project_root,
                diagnostics=diagnostics,
            ),
            input_ports=input_ports,
            input_port_input_names={
                port_id: executable_input_name(
                    node_type=NodeType.SUBMODEL_PORT, label="", source_handle=port_id
                )
                for port_id in _port_ids(input_ports)
            },
            output_ports=output_ports,
        )

    occurrences: list[_RecoveredCandidate] = []
    for registration, recovery_id in zip(registrations, occurrence_ids, strict=True):
        definition = definitions[registration.definition_id]
        span = _span(
            registration.line or 1,
            0,
            registration.line or 1,
            0,
        )
        diagnostic_ids = list(definition.diagnostic_ids)
        availability = definition.availability
        if aliases[registration.alias] > 1:
            diagnostic = _diagnostic(
                code="submodel_alias_duplicate",
                scope="submodel",
                message=f"Submodel alias {registration.alias!r} is duplicated.",
                source_file=source_file,
                element_id=recovery_id,
                source_span=span,
                remediation="Give every submodel occurrence a unique alias.",
            )
            diagnostics.append(diagnostic)
            diagnostic_ids.append(diagnostic.diagnostic_id)
            availability = "unavailable"
        occurrences.append(
            _RecoveredCandidate(
                authored_id=registration.instance_id,
                recovery_id=recovery_id,
                decorator_name="submodel",
                node_type=NodeType.SUBMODEL,
                description=registration.label or registration.alias,
                config={
                    "definitionId": registration.definition_id,
                    "alias": registration.alias,
                    **(
                        {"instanceOf": registration.instance_of}
                        if registration.instance_of is not None
                        else {}
                    ),
                },
                config_reference=registration.path,
                param_names=(),
                edge_param_names=(),
                span=span,
                availability=availability,
                diagnostic_ids=diagnostic_ids,
                endpoint_ids=(registration.instance_id, registration.alias),
                submodel_input_ports=_port_ids(definition.input_ports),
                submodel_output_ports=_port_ids(definition.output_ports),
            )
        )
    return definitions, occurrences


def _source_references(
    source: str,
    *,
    child: bool,
) -> tuple[list[str], list[SubmodelRegistration]]:
    config_refs: list[str] = []
    registrations: list[SubmodelRegistration] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        try:
            fragments = recover_pipeline_fragments(source)
        except HauteError:
            return config_refs, registrations
        for function in fragments.functions:
            try:
                kwargs = _parse_decorator_kwargs_regex(function.decorator_text)
            except HauteError:
                continue
            reference = kwargs.get("config")
            if isinstance(reference, str) and reference.strip():
                config_refs.append(reference)
        registrations.extend(fragments.submodel_registrations)
        return config_refs, registrations

    bodies = _extract_function_bodies(source, tree=tree)
    decorator_checker = (
        _is_submodel_authored_decorator if child else _is_pipeline_authored_decorator
    )
    for skeleton in _extract_decorated_node_skeletons(
        tree,
        decorator_checker,
        bodies,
        source=source,
    ):
        try:
            kwargs = _get_decorator_kwargs(skeleton.decorator)
        except HauteError:
            continue
        reference = kwargs.get("config")
        if isinstance(reference, str) and reference.strip():
            config_refs.append(reference)
    # Revision discovery must be at least as tolerant as the editor recovery
    # pass.  The strict extractor validates cross-registration uniqueness and
    # would otherwise turn a representable duplicate alias/instance into a
    # top-level source-only incident while computing the artifact manifest.
    registrations.extend(
        _recover_ast_submodel_registrations(
            tree,
            source_file="<revision-manifest>",
            diagnostics=[],
        )
    )
    return config_refs, registrations


def _recovery_artifacts(
    pipeline_path: Path,
    project_root: Path,
    *,
    parent_source: str | None = None,
    captures: _SourceCaptures | None = None,
) -> list[tuple[str, Path]]:
    root = project_root.resolve()
    parent = pipeline_path.resolve()
    artifacts: list[tuple[str, Path]] = [
        ("parent_source", parent),
        ("parent_sidecar", parent.with_suffix(".haute.json")),
    ]
    visited: set[str] = set()

    def visit(
        source_path: Path,
        *,
        child: bool,
        config_base: Path,
        source: str | None = None,
    ) -> None:
        source_key = str(source_path.resolve()).casefold()
        if source_key in visited:
            return
        visited.add(source_key)
        if source is None:
            try:
                if captures is not None:
                    _raw, source = captures.read(source_path)
                else:
                    source = read_user_text(source_path)
            except OSError:
                return
        config_refs, registrations = _source_references(source, child=child)
        for reference in config_refs:
            config_path = (config_base / reference.replace("\\", "/")).resolve()
            if config_path.is_relative_to(root):
                artifacts.append(("node_config", config_path))
        for registration in registrations:
            try:
                child_path, child_config_base = resolve_submodel_reference(
                    registration.path,
                    pipeline_dir=source_path.parent,
                    project_root=root,
                )
            except ValueError:
                continue
            artifacts.extend(
                [
                    ("child_source", child_path),
                    ("child_sidecar", child_path.with_suffix(".haute.json")),
                ]
            )
            if child_path.is_file():
                visit(child_path, child=True, config_base=child_config_base)

    visit(parent, child=False, config_base=parent.parent, source=parent_source)
    return artifacts


def _capabilities(
    status: PipelineLoadStatus,
    *,
    source_selection_trusted: bool,
) -> PipelineDocumentCapabilities:
    ready = status == "ready"
    return PipelineDocumentCapabilities(
        can_mutate=ready,
        can_save=ready,
        can_execute=ready,
        can_preview=status != "source_only" and source_selection_trusted,
        can_manage_submodels=ready,
        can_repair=status == "degraded",
        reserved_api_input_frame_labels=sorted(keyword.kwlist),
    )


def empty_pipeline_editor_document() -> PipelineEditorDocument:
    """Return the editable new-project canvas when no authored file exists."""
    return PipelineEditorDocument(
        load_status="ready",
        has_authored_content=False,
        capabilities=_capabilities("ready", source_selection_trusted=True),
    )


def _load_readable_pipeline_editor_document(
    path: Path,
    root: Path,
    source: str,
    captures: _SourceCaptures,
) -> PipelineEditorDocument:
    """Recover a source string whose path and readability are already trusted."""
    source_file = _wire_path(path, root)
    sidecar = read_sidecar_state(path)
    positions, sources, active_source, source_selection_trusted = _sidecar_values(sidecar)
    diagnostics: list[PipelineRecoveryDiagnostic] = []
    sidecar_issue = _sidecar_diagnostic(sidecar, source_file=source_file)

    pipeline_name: str | None = None
    pipeline_description: str | None = None
    preamble: str | None = None
    preserved_blocks: list[str] = []
    nodes: list[RecoveryPipelineNode] = []
    edges: list[RecoveryPipelineEdge] = []
    unresolved: list[RecoveryUnresolvedConnection] = []
    submodels: dict[str, RecoverySubmodelDefinition] | None = None
    connections: Sequence[_RecoveredConnection | tuple[str, str, str | None, str | None]] = []
    registrations: list[SubmodelRegistration] = []
    source_wide_failed = False

    strict_failure: BaseException | None = None
    strict_graph: PipelineGraph | None = None
    try:
        # Parse the exact bytes this document presents. Re-reading the file
        # here could straddle a concurrent external edit and silently disagree
        # with ``source_text`` and the recovery pass.
        strict_graph = parse_pipeline_source(
            source,
            source_file=str(path),
            _base_dir=path.parent,
            _submodel_base_dir=_infer_parse_base_dir(path),
            _read_submodel_source=lambda child_path: captures.read(child_path)[1],
        )
    except (HauteError, OSError, UnicodeError) as exc:
        strict_failure = exc
    else:
        assert strict_graph is not None
        snapshot = _canonical_snapshot(
            strict_graph,
            source_path=path,
            project_root=root,
            diagnostics=diagnostics,
        )
        pipeline_name = strict_graph.pipeline_name
        pipeline_description = strict_graph.pipeline_description
        preamble = strict_graph.preamble
        preserved_blocks = list(strict_graph.preserved_blocks)
        nodes = snapshot.nodes
        edges = snapshot.edges
        unresolved = snapshot.unresolved_connections
        submodels = snapshot.submodels

    if strict_graph is None:
        if sidecar_issue is not None:
            diagnostics.append(sidecar_issue)
        try:
            tree = ast.parse(source)
        except SyntaxError as syntax_error:
            syntax_span = _span(
                syntax_error.lineno or 1,
                max(0, (syntax_error.offset or 1) - 1),
                syntax_error.end_lineno or syntax_error.lineno or 1,
                max(0, (syntax_error.end_offset or syntax_error.offset or 1) - 1),
            )
            diagnostics.append(
                _diagnostic(
                    code="python_syntax_error",
                    scope="pipeline",
                    message="Pipeline source contains invalid Python syntax.",
                    source_file=source_file,
                    source_span=syntax_span,
                    remediation="Open the source at this location and correct the syntax.",
                )
            )
            try:
                fragments = recover_pipeline_fragments(source)
            except Exception:  # noqa: BLE001 - named source recovery isolation boundary
                source_wide_failed = True
                incident_id = uuid4().hex
                logger.error(
                    "pipeline_source_recovery_unexpected",
                    source_file=source_file,
                    incident_id=incident_id,
                    exc_info=True,
                )
                diagnostics.append(
                    _diagnostic(
                        code="pipeline_recovery_internal_error",
                        scope="pipeline",
                        message=(
                            "The pipeline source could not be reconstructed because of an "
                            "internal error."
                        ),
                        source_file=source_file,
                        remediation=(
                            "Check the server logs with the incident id and report the defect."
                        ),
                        incident_id=incident_id,
                    )
                )
                pipeline_name = path.stem
                pipeline_description = ""
                preamble = _extract_preamble(source)
                preserved_blocks = _extract_preserved_blocks(source)
            else:
                pipeline_name = fragments.pipeline_name or path.stem
                pipeline_description = fragments.pipeline_description
                preamble = fragments.preamble
                preserved_blocks = list(fragments.preserved_blocks)
                connections = list(fragments.connections)
                registrations = list(fragments.submodel_registrations)
                identities = [
                    (fragment.authored_id, fragment.start_line) for fragment in fragments.functions
                ]
                candidates = [
                    _candidate_from_regex(
                        fragment,
                        recovery_id=recovery_id,
                        source_file=source_file,
                        base_dir=path.parent,
                        diagnostics=diagnostics,
                    )
                    for fragment, recovery_id in zip(
                        fragments.functions,
                        _candidate_ids(identities),
                        strict=True,
                    )
                ]
                submodels, submodel_occurrences = _recover_registered_submodels(
                    registrations,
                    parent_path=path,
                    project_root=root,
                    source_file=source_file,
                    diagnostics=diagnostics,
                    captures=captures,
                )
                candidates.extend(submodel_occurrences)
                nodes, edges, unresolved = _build_recovery_graph(
                    candidates,
                    connections,
                    source_file=source_file,
                    positions=positions,
                    diagnostics=diagnostics,
                )
                if not candidates:
                    source_wide_failed = True
        else:
            pipeline_name, pipeline_description = _extract_pipeline_meta(tree)
            pipeline_name = pipeline_name or path.stem
            preamble = _extract_preamble(source, tree=tree)
            preserved_blocks = _extract_preserved_blocks(source)
            recovered_connections, initially_unresolved = _recover_ast_connections(
                tree,
                receiver="pipeline",
                source_file=source_file,
                diagnostics=diagnostics,
            )
            connections = recovered_connections
            registrations = _recover_ast_submodel_registrations(
                tree,
                source_file=source_file,
                diagnostics=diagnostics,
            )
            bodies = _extract_function_bodies(source, tree=tree)
            skeletons = _extract_decorated_node_skeletons(
                tree,
                _is_pipeline_authored_decorator,
                bodies,
                source=source,
            )
            identities = [(skeleton.authored_id, skeleton.start_line) for skeleton in skeletons]
            candidates = [
                _candidate_from_ast(
                    skeleton,
                    recovery_id=recovery_id,
                    source_file=source_file,
                    base_dir=path.parent,
                    diagnostics=diagnostics,
                )
                for skeleton, recovery_id in zip(
                    skeletons,
                    _candidate_ids(identities),
                    strict=True,
                )
            ]
            submodels, submodel_occurrences = _recover_registered_submodels(
                registrations,
                parent_path=path,
                project_root=root,
                source_file=source_file,
                diagnostics=diagnostics,
                captures=captures,
            )
            candidates.extend(submodel_occurrences)
            nodes, edges, unresolved = _build_recovery_graph(
                candidates,
                connections,
                source_file=source_file,
                positions=positions,
                diagnostics=diagnostics,
                initial_unresolved=initially_unresolved,
            )
            if not diagnostics:
                diagnostics.append(
                    _diagnostic(
                        code="pipeline_parse_invalid",
                        scope="pipeline",
                        message=_exception_message(
                            strict_failure or ParseError("Pipeline parsing failed.")
                        ),
                        source_file=source_file,
                        remediation="Review the pipeline-level topology or submodel diagnostic.",
                    )
                )

    has_authored_content = bool(
        nodes
        or connections
        or registrations
        or preserved_blocks
        or re.search(r"\bhaute\s*\.\s*Pipeline\b", source)
    )
    load_status: PipelineLoadStatus
    if source_wide_failed:
        load_status = "source_only"
    elif diagnostics or any(node.availability != "ready" for node in nodes):
        load_status = "degraded"
    else:
        load_status = "ready"

    artifacts = _recovery_artifacts(path, root, parent_source=source, captures=captures)
    source_revision = pipeline_recovery_revision(
        project_root=root,
        artifacts=artifacts,
        known_bytes=captures.known_bytes(),
    )
    kept_diagnostics = diagnostics[:_MAX_DIAGNOSTICS]
    return PipelineEditorDocument(
        load_status=load_status,
        pipeline_name=pipeline_name or path.stem,
        pipeline_description=pipeline_description or "",
        preamble=preamble,
        preserved_blocks=preserved_blocks,
        source_file=source_file,
        source_revision=source_revision,
        source_text=source,
        sources=sources if source_selection_trusted else [],
        active_source=active_source if source_selection_trusted else None,
        source_selection_trusted=source_selection_trusted,
        has_authored_content=has_authored_content,
        nodes=nodes,
        edges=edges,
        unresolved_connections=unresolved,
        submodels=submodels,
        diagnostics=kept_diagnostics,
        diagnostics_omitted=max(0, len(diagnostics) - len(kept_diagnostics)),
        capabilities=_capabilities(
            load_status,
            source_selection_trusted=source_selection_trusted,
        ),
    )


def load_pipeline_editor_document(
    pipeline_path: str | Path,
    *,
    project_root: Path | None = None,
) -> PipelineEditorDocument:
    """Load one readable pipeline into an honest editor recovery document.

    Permission, containment, and unreadable-file failures remain system
    failures. Once the source bytes are readable, an unexpected editor-only
    recovery defect is contained as ``source_only`` with a safe incident id;
    strict parser consumers still receive the original exception.
    """
    path = Path(pipeline_path).resolve()
    root = (project_root or path.parent).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Pipeline recovery path escapes the project root.")
    captures = _SourceCaptures()
    raw_source, source = captures.read(path)
    try:
        return _load_readable_pipeline_editor_document(path, root, source, captures)
    except Exception:  # noqa: BLE001 - named editor recovery isolation boundary
        incident_id = uuid4().hex
        source_file = _wire_path(path, root)
        logger.error(
            "pipeline_recovery_document_unexpected",
            source_file=source_file,
            incident_id=incident_id,
            exc_info=True,
        )
        try:
            source_revision = pipeline_recovery_revision(
                project_root=root,
                artifacts=[
                    ("parent_source", path),
                    ("parent_sidecar", path.with_suffix(".haute.json")),
                ],
                known_bytes={path: raw_source},
            )
        except Exception:  # noqa: BLE001 - best-effort incident metadata
            source_revision = None
        try:
            preserved_blocks = _extract_preserved_blocks(source)
        except Exception:  # noqa: BLE001 - never hide readable current source
            preserved_blocks = []
        diagnostic = _diagnostic(
            code="pipeline_recovery_internal_error",
            scope="pipeline",
            message="The pipeline source could not be reconstructed because of an internal error.",
            source_file=source_file,
            remediation="Check the server logs with the incident id and report the defect.",
            incident_id=incident_id,
        )
        return PipelineEditorDocument(
            load_status="source_only",
            pipeline_name=path.stem,
            pipeline_description="",
            preamble=None,
            preserved_blocks=preserved_blocks,
            source_file=source_file,
            source_revision=source_revision,
            source_text=source,
            sources=[],
            active_source=None,
            source_selection_trusted=False,
            has_authored_content=bool(source.strip()),
            diagnostics=[diagnostic],
            capabilities=_capabilities(
                "source_only",
                source_selection_trusted=False,
            ),
        )
