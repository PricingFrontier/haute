"""Side-effect-free editor recovery for readable pipeline documents."""

from __future__ import annotations

import ast
import errno
import hashlib
import keyword
import os
import re
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from haute._ast_helpers import (
    _chained_receiver_calls,
    _connect_call_edge,
    _extract_function_bodies,
    _extract_global_constants_declaration,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
    _get_decorator_kwargs,
    _is_pipeline_authored_decorator,
    _is_submodel_authored_decorator,
    is_submodel_import,
    unkept_module_statements,
)
from haute._cache import canonical_json
from haute._config_io import node_configs_read_through
from haute._editor_identities import (
    recoverable_request_input_source_handles,
    resolve_editor_identity,
)
from haute._executable_names import RESERVED_NAMES, ROOT_MODULE, NameViolation
from haute._graph_builders import (
    PipelineNodeSkeleton,
    _edge_param_names_for_node,
    _extract_decorated_node_skeletons,
    _resolve_node_skeleton,
)
from haute._graph_shape import definition_boundary_topology
from haute._graph_utils import REQUEST_INPUT_KINDS, edge_input_name, executable_input_name
from haute._hashing import content_hash_bytes
from haute._io import decode_user_source, read_user_source
from haute._logging import get_logger
from haute._parser_submodels import (
    SubmodelRegistration,
    _extract_definition_contract,
    extract_submodel_registrations,
    parse_submodel_source,
    submodel_imports,
)
from haute._pipeline_revision import pipeline_recovery_revision
from haute._sidecar import (
    SidecarReadResult,
    _normalise_sidecar_sources,
    read_sidecar_state,
)
from haute._source_layout import python_lines
from haute._submodel_paths import resolve_submodel_reference, submodel_source_key
from haute._submodel_recovery import submodel_registration_evidence
from haute._types import (
    GLOBAL_CONSTANTS_FILE,
    NODE_TYPE_TO_DECORATOR,
    REQUEST_INPUT_NODE_TYPES,
    GlobalConstant,
    GraphNode,
    NodeType,
    PipelineGraph,
)
from haute.errors import ConfigError, HauteError, ParseError
from haute.parser import (
    _infer_parse_base_dir,
    load_declared_global_constants,
    parse_pipeline_source_with_name_violations,
)
from haute.schemas import (
    PipelineDiagnosticScope,
    PipelineDocumentCapabilities,
    PipelineEditorDocument,
    PipelineElementAvailability,
    PipelineLoadStatus,
    PipelineNameViolation,
    PipelineNameViolationParty,
    PipelineNodeCompleteness,
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
    """First-read byte capture for every source artifact in one load.

    The recovery revision must authenticate the exact bytes this document was
    built from, so each source, editor sidecar, node config and constants file
    is read once and every later consumer — including the revision manifest —
    reuses that capture instead of re-reading a file a concurrent edit may have
    changed; a file missing when first read stays missing. A source's text is
    decoded from its captured bytes by ``decode_user_source``, so a file that is
    not UTF-8 raises its located ``ParseError`` wherever its text is read.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        # ``None`` records a file that was missing when first read.
        self._by_key: dict[str, tuple[Path, bytes | None]] = {}

    @staticmethod
    def _key(path: Path) -> str:
        # The exact resolved path: case-folding it would let two files that differ
        # only in case on a case-sensitive filesystem share one file's bytes.
        return str(path.resolve())

    def read_bytes(self, path: Path) -> bytes:
        key = self._key(path)
        if key not in self._by_key:
            try:
                raw: bytes | None = path.read_bytes()
            except FileNotFoundError:
                raw = None
            self._by_key[key] = (path, raw)
        captured = self._by_key[key][1]
        if captured is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))
        return captured

    def read_source(self, path: Path) -> str:
        return decode_user_source(self.read_bytes(path), source_file=_wire_path(path, self._root))

    def known_bytes(self) -> dict[Path, bytes | None]:
        return dict(self._by_key.values())


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
    position_key: str | None = None


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
    authored: PipelineNodeSkeleton,
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


def _recovery_ids(identities: Iterable[tuple[str, int]]) -> Callable[[str, int], str]:
    """The recovery id of each element of one graph, minted over all of them at once.

    *identities* are every candidate's ``(authored name, line)``: decorated nodes
    and submodel occurrences, valid or not. A name the graph holds once is its
    own id; a name held more than once takes its line. Minting once, before any
    candidate is resolved, means a diagnostic created with an element's id still
    names that element in the finished document.
    """
    counts = Counter(name for name, _line in identities)

    def recovery_id(name: str, line: int) -> str:
        return f"{name}@L{line}" if counts[name] > 1 else name

    return recovery_id


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
    unavailable_candidates: list[_RecoveredCandidate] | None = None,
    include_invalid_references: bool = False,
    recovery_id: Callable[[str, int], str] | None = None,
) -> list[SubmodelRegistration]:
    """Recover independent valid registrations when one sibling is malformed.

    The submodel imports are read one statement at a time too, so a malformed import
    is its own diagnostic and the well-formed ones still name their files. An
    unavailable occurrence takes the id *recovery_id* mints for it, its name otherwise.
    """
    imports: dict[str, str] = {}
    for statement in tree.body:
        if not is_submodel_import(statement):
            continue
        try:
            imports.update(submodel_imports(ast.Module(body=[statement], type_ignores=[])))
        except HauteError as exc:
            diagnostics.append(
                _diagnostic(
                    code="submodel_import_invalid",
                    scope="submodel",
                    message=_exception_message(exc),
                    source_file=source_file,
                    source_span=_span(
                        statement.lineno,
                        statement.col_offset,
                        statement.end_lineno or statement.lineno,
                        statement.end_col_offset or statement.col_offset,
                    ),
                    remediation=exc.context.get(
                        "remediation",
                        "Import each submodel module as `from modules import <name>`.",
                    ),
                )
            )
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
                extract_submodel_registrations(
                    ast.Module(body=[statement], type_ignores=[]), imports=imports
                )
            )
        except HauteError as exc:
            span = _span(
                statement.lineno,
                statement.col_offset,
                statement.end_lineno or statement.lineno,
                statement.end_col_offset or statement.col_offset,
            )
            evidence = submodel_registration_evidence(statement, imports=imports)
            element_id = (
                None
                if evidence is None
                else recovery_id(evidence.name, statement.lineno)
                if recovery_id is not None
                else evidence.name
            )
            diagnostic = _diagnostic(
                code="submodel_registration_invalid",
                scope="submodel",
                message=_exception_message(exc),
                source_file=source_file,
                source_span=span,
                element_id=element_id,
                remediation=exc.context.get(
                    "remediation", "Correct the submodel registration identity and file path."
                ),
            )
            diagnostics.append(diagnostic)
            if evidence is not None:
                if include_invalid_references:
                    registrations.append(
                        SubmodelRegistration(
                            path=evidence.path,
                            name=evidence.name,
                            line=statement.lineno,
                        )
                    )
                if unavailable_candidates is not None:
                    assert element_id is not None
                    unavailable_candidates.append(
                        _RecoveredCandidate(
                            authored_id=evidence.name,
                            recovery_id=element_id,
                            decorator_name="submodel",
                            node_type=NodeType.SUBMODEL,
                            description=evidence.name,
                            config={
                                "definitionId": evidence.definition_id or evidence.path,
                                "alias": evidence.name,
                            },
                            config_reference=evidence.path,
                            param_names=(),
                            edge_param_names=(),
                            span=span,
                            availability="unavailable",
                            diagnostic_ids=[diagnostic.diagnostic_id],
                            position_key=evidence.instance_id,
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
    boundary_incoming: Mapping[str, Sequence[str]] | None = None,
    boundary_outgoing: Mapping[str, Sequence[str]] | None = None,
) -> tuple[
    list[RecoveryPipelineNode],
    list[RecoveryPipelineEdge],
    list[RecoveryUnresolvedConnection],
]:
    # Every candidate carries the id minted over the graph's combined set
    # (``_recovery_ids``), so the diagnostics already made with it stay valid.
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
            source_handle = f"out__{connection.source_port}"
        if str(source_candidate.node_type) in REQUEST_INPUT_KINDS and not connection.source_port:
            # A Quote or Workbench Input emits a frame per table; a connect()
            # naming none has no frame to carry.
            unresolved.append(
                _unresolved_connection(
                    connection,
                    ordinal=ordinal,
                    code="connection_frame_missing",
                    message="This connection does not name the table it takes from the input.",
                    source_file=source_file,
                    diagnostics=diagnostics,
                    source_recovery_id=source_candidate.recovery_id,
                    target_recovery_id=target_candidate.recovery_id,
                )
            )
            continue
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
            if (
                str(sources[0].node_type) in REQUEST_INPUT_KINDS
                and source_id == sources[0].authored_id
            ):
                # The parameter names the input, not one of its tables, and no
                # connect() binds it (an input may name a table after itself), so
                # it binds no frame: the consumer's own failure, which Recover
                # settings rebinds to its connected inputs.
                if target_candidate.availability == "ready":
                    diagnostic = _diagnostic(
                        code="node_input_frame_missing",
                        scope="node",
                        message=(
                            f"Parameter {source_id!r} names the input itself, not one of its "
                            "tables."
                        ),
                        source_file=source_file,
                        element_id=target_candidate.recovery_id,
                        source_span=target_candidate.span,
                        remediation="Name the parameter after the table it takes.",
                    )
                    diagnostics.append(diagnostic)
                    target_candidate.diagnostic_ids.append(diagnostic.diagnostic_id)
                    target_candidate.availability = "unavailable"
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
                    span=target_candidate.span,
                    ordinal=implicit_ordinal,
                )
            )
            implicit_ordinal += 1

    # Graph-shape validation is currently node-local (Explore is the only
    # topology-constrained canonical type).  Attribute the failure to that
    # node and continue validating independent siblings.
    # In a submodel, its public ports add edges of their own (*boundary_*).
    for candidate in candidates:
        if candidate.availability != "ready" or candidate.node_type != NodeType.EXPLORE:
            continue
        incoming_count = sum(1 for edge in edge_specs if edge.target is candidate) + len(
            (boundary_incoming or {}).get(candidate.authored_id, ())
        )
        outgoing_count = sum(1 for edge in edge_specs if edge.source is candidate) + len(
            (boundary_outgoing or {}).get(candidate.authored_id, ())
        )
        if incoming_count == 1 and not outgoing_count:
            continue
        message = (
            "Explore nodes must have exactly one incoming edge."
            if incoming_count != 1
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
            positions.get(candidate.position_key or "", {"x": float(index * 300), "y": 0.0}),
        )
        resolved_identity = None
        if candidate.node_type is not None:
            try:
                source_handles = handles_by_source.get(candidate.recovery_id, [])
                if candidate.node_type in REQUEST_INPUT_NODE_TYPES and isinstance(
                    candidate.config, dict
                ):
                    source_handles = list(
                        recoverable_request_input_source_handles(
                            candidate.node_type, candidate.config
                        )
                    )
                elif candidate.node_type == NodeType.SUBMODEL:
                    source_handles = [
                        f"out__{port_name}" for port_name in candidate.submodel_output_ports
                    ]
                resolved_identity = resolve_editor_identity(
                    node_type=candidate.node_type,
                    label=candidate.authored_id,
                    source_handles=source_handles,
                    config_reference_override=candidate.config_reference,
                    alias=(candidate.config or {}).get("alias") if candidate.config else None,
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
            try:
                input_name = executable_input_name(
                    node_type=source_candidate.node_type,
                    label=source_candidate.authored_id,
                    source_handle=edge.source_handle,
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


def _editor_sidecar_positions(
    source_path: Path,
    *,
    source_file: str,
    captures: _SourceCaptures,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> dict[str, dict[str, float]]:
    """The canvas positions in one file's editor sidecar, read through *captures*.

    A corrupt or unreadable sidecar is diagnosed and gives no positions.
    """
    sidecar = read_sidecar_state(source_path, read_bytes=captures.read_bytes)
    issue = _sidecar_diagnostic(sidecar, source_file=source_file)
    if issue is not None:
        diagnostics.append(issue)
    return _sidecar_values(sidecar)[0]


def _canonical_snapshot(
    graph: PipelineGraph,
    *,
    source_path: Path,
    project_root: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
    captures: _SourceCaptures,
) -> RecoveryGraphSnapshot:
    source_file = _wire_path(source_path, project_root)
    positions = _editor_sidecar_positions(
        source_path, source_file=source_file, captures=captures, diagnostics=diagnostics
    )

    connected_handles_by_source: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.sourceHandle is not None:
            handles = connected_handles_by_source.setdefault(edge.source, [])
            if edge.sourceHandle not in handles:
                handles.append(edge.sourceHandle)

    def source_handles_for(node: GraphNode) -> list[str]:
        if node.data.nodeType in REQUEST_INPUT_NODE_TYPES:
            return list(
                recoverable_request_input_source_handles(node.data.nodeType, node.data.config)
            )
        if node.data.nodeType == NodeType.SUBMODEL:
            definition_id = node.data.config.get("definitionId")
            definition = (
                (graph.submodels or {}).get(definition_id)
                if isinstance(definition_id, str)
                else None
            )
            if definition is None:
                raise ValueError(f"Submodel node {node.id!r} references a missing definition.")
            return [f"out__{port.name}" for port in definition.output_ports]
        if node.data.nodeType == NodeType.SUBMODEL_PORT:
            return connected_handles_by_source.get(node.id, [])
        return []

    nodes: list[RecoveryPipelineNode] = []
    for node in graph.nodes:
        source_handles = source_handles_for(node)
        identity = resolve_editor_identity(
            node_type=node.data.nodeType,
            label=node.data.label,
            source_handles=source_handles,
            alias=(
                (node.data.config or {}).get("alias")
                if node.data.nodeType == NodeType.SUBMODEL
                else None
            ),
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
            input_name=edge_input_name(
                edge,
                source_nodes[edge.source],
                submodels=graph.submodels,
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
                captures=captures,
            ),
            input_ports=[
                port.model_dump(mode="json", by_alias=True) for port in definition.input_ports
            ],
            output_ports=[
                port.model_dump(mode="json", by_alias=True) for port in definition.output_ports
            ],
        )
    return RecoveryGraphSnapshot(
        nodes=nodes,
        edges=edges,
        submodels=submodels or None,
    )


def _port_names(ports: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(port.get("name"))
        for port in ports
        if isinstance(port.get("name"), str) and port.get("name")
    )


#: Strict refusals the recovery pass reports element by element in its own
#: words: the context key that marks the refusal, and the diagnostic codes that
#: already show it on screen.
_STRICT_FAILURE_SHOWN_AS: dict[str, frozenset[str]] = {
    "decorator": frozenset({"node_decorator_unknown"}),
    "duplicate_name": frozenset({"node_identity_duplicate"}),
    "dangling_edges": frozenset({"connection_endpoint_missing", "connection_endpoint_ambiguous"}),
    "duplicate_edges": frozenset({"connection_duplicate"}),
    "missing_paths": frozenset({"submodel_file_missing"}),
    "frame_missing_edges": frozenset({"connection_frame_missing", "node_input_frame_missing"}),
}


def _strict_failure_shown(
    failure: BaseException | None, diagnostics: Sequence[PipelineRecoveryDiagnostic]
) -> bool:
    """Whether a diagnostic already shows the strict parse's refusal."""
    message = _exception_message(failure or ParseError("Pipeline parsing failed."))
    if any(diagnostic.message == message for diagnostic in diagnostics):
        return True
    if not isinstance(failure, HauteError):
        return False
    codes = {diagnostic.code for diagnostic in diagnostics}
    return any(
        key in failure.context and not codes.isdisjoint(shown)
        for key, shown in _STRICT_FAILURE_SHOWN_AS.items()
    )


def _attribute_binding_failure(
    failure: BaseException | None,
    candidates: Sequence[_RecoveredCandidate],
    *,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
    definition: bool,
) -> None:
    """Mark the node a parameter-binding failure names unavailable, with that failure.

    The binding gate checks a whole graph, so no node fails it on its own; the
    failure names its node, and a definition file's failure names that file
    too, so it is never taken for a parent node of the same name.
    """
    if not (isinstance(failure, ParseError) and "unbound_parameters" in failure.context):
        return
    if ("source_file" in failure.context) != definition:
        return
    failed_node_id = failure.context.get("node_id")
    matching = [candidate for candidate in candidates if candidate.authored_id == failed_node_id]
    if len(matching) != 1 or matching[0].availability != "ready":
        return
    failed = matching[0]
    diagnostic = _diagnostic(
        code="node_parse_invalid",
        scope="node",
        message=failure.message,
        source_file=source_file,
        element_id=failed.recovery_id,
        source_span=failed.span,
        remediation=failure.context.get("remediation", "Correct this node's source or reset it."),
    )
    diagnostics.append(diagnostic)
    failed.diagnostic_ids.append(diagnostic.diagnostic_id)
    failed.availability = "unavailable"


def _attribute_instance_failure(
    failure: BaseException | None,
    candidates: Sequence[_RecoveredCandidate],
    *,
    source_file: str,
    diagnostics: list[PipelineRecoveryDiagnostic],
) -> None:
    """Mark the submodel occurrences a copy rule names unavailable, with that rule.

    Each occurrence registers fine on its own; only together do they break a
    rule (a copy naming a missing original, a definition with no owner or two),
    so the failure names the occurrences, and each can then be removed.
    """
    if not isinstance(failure, ParseError) or "definition_id" not in failure.context:
        return
    named = failure.context.get("instance_ids") or (
        [failure.context["instance_id"]] if "instance_id" in failure.context else []
    )
    for candidate in candidates:
        if (
            candidate.node_type != NodeType.SUBMODEL
            or candidate.authored_id not in named
            or candidate.availability != "ready"
        ):
            continue
        diagnostic = _diagnostic(
            code="submodel_instance_invalid",
            scope="node",
            message=failure.message,
            source_file=source_file,
            element_id=candidate.recovery_id,
            source_span=candidate.span,
            remediation=(
                "Remove the occurrence, or give it instance_of naming the definition's "
                "one owner occurrence."
            ),
        )
        diagnostics.append(diagnostic)
        candidate.diagnostic_ids.append(diagnostic.diagnostic_id)
        candidate.availability = "unavailable"


def _recover_submodel_snapshot(
    child_path: Path,
    *,
    project_root: Path,
    config_base: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
    captures: _SourceCaptures,
    failure: BaseException | None = None,
) -> tuple[RecoveryGraphSnapshot, list[dict[str, Any]], list[dict[str, Any]]]:
    """Best-effort child graph recovery without making it canonical.

    *failure* is the strict parse's refusal of the file, attributed to the node
    it names when the binding gate raised it.
    """
    child_source_file = _wire_path(child_path, project_root)
    try:
        source = captures.read_source(child_path)
        tree = ast.parse(source)
    except (OSError, ParseError, SyntaxError):
        return RecoveryGraphSnapshot(), [], []

    positions = _editor_sidecar_positions(
        child_path, source_file=child_source_file, captures=captures, diagnostics=diagnostics
    )

    bodies = _extract_function_bodies(source, tree=tree)
    skeletons = _extract_decorated_node_skeletons(
        tree,
        _is_submodel_authored_decorator,
        bodies,
        source=source,
    )
    recovery_id = _recovery_ids((item.authored_id, item.start_line) for item in skeletons)
    candidates = [
        _candidate_from_ast(
            skeleton,
            recovery_id=recovery_id(skeleton.authored_id, skeleton.start_line),
            source_file=child_source_file,
            base_dir=config_base,
            diagnostics=diagnostics,
        )
        for skeleton in skeletons
    ]
    connections, initially_unresolved = _recover_ast_connections(
        tree,
        receiver="submodel",
        source_file=child_source_file,
        diagnostics=diagnostics,
    )
    _attribute_binding_failure(
        failure,
        candidates,
        source_file=child_source_file,
        diagnostics=diagnostics,
        definition=True,
    )
    try:
        _definition_id, input_ports, output_ports = _extract_definition_contract(tree)
    except HauteError:
        input_payload: list[dict[str, Any]] = []
        output_payload: list[dict[str, Any]] = []
        boundary_incoming: dict[str, list[str]] = {}
        boundary_outgoing: dict[str, list[str]] = {}
    else:
        input_payload = [port.model_dump(mode="json", by_alias=True) for port in input_ports]
        output_payload = [port.model_dump(mode="json", by_alias=True) for port in output_ports]
        boundary_incoming, boundary_outgoing = definition_boundary_topology(
            input_ports, output_ports
        )
    nodes, edges, unresolved = _build_recovery_graph(
        candidates,
        connections,
        source_file=child_source_file,
        positions=positions,
        diagnostics=diagnostics,
        initial_unresolved=initially_unresolved,
        boundary_incoming=boundary_incoming,
        boundary_outgoing=boundary_outgoing,
    )
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
    definition_id: str | None = None,
    graph: RecoveryGraphSnapshot | None = None,
    input_ports: list[dict[str, Any]] | None = None,
    output_ports: list[dict[str, Any]] | None = None,
    diagnostic_ids: list[str] | None = None,
) -> RecoverySubmodelDefinition:
    def_id = definition_id or registration.path
    return RecoverySubmodelDefinition(
        definition_id=def_id,
        file=registration.path,
        availability="unavailable",
        diagnostic_ids=list(diagnostic_ids or []),
        graph=graph or RecoveryGraphSnapshot(),
        input_ports=list(input_ports or []),
        output_ports=list(output_ports or []),
    )


def _recover_unavailable_submodel_definition(
    registration: SubmodelRegistration,
    child_path: Path,
    *,
    definition_id: str | None = None,
    project_root: Path,
    config_base: Path,
    diagnostics: list[PipelineRecoveryDiagnostic],
    captures: _SourceCaptures,
    code: str,
    message: str,
    remediation: str,
    incident_id: str | None = None,
    failure: BaseException | None = None,
) -> RecoverySubmodelDefinition:
    """Recover a child snapshot while keeping its definition non-canonical."""
    def_id = definition_id or registration.path
    before_count = len(diagnostics)
    graph, input_ports, output_ports = _recover_submodel_snapshot(
        child_path,
        project_root=project_root,
        config_base=config_base,
        diagnostics=diagnostics,
        captures=captures,
        failure=failure,
    )
    diagnostic = _diagnostic(
        code=code,
        scope="submodel",
        message=message,
        source_file=_wire_path(child_path, project_root),
        element_id=def_id,
        remediation=remediation,
        incident_id=incident_id,
    )
    diagnostics.insert(before_count, diagnostic)
    return _unavailable_submodel_definition(
        registration,
        definition_id=def_id,
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
    recovery_id: Callable[[str, int], str],
) -> tuple[dict[str, RecoverySubmodelDefinition] | None, list[_RecoveredCandidate]]:
    """Resolve each definition once and retain every occurrence independently.

    Each occurrence takes the id *recovery_id* mints for it over the whole graph.
    """
    if not registrations:
        return None, []

    definitions: dict[str, RecoverySubmodelDefinition] = {}
    names = Counter(registration.name for registration in registrations)
    occurrence_ids = [
        recovery_id(registration.name, registration.line or 1) for registration in registrations
    ]

    path_to_def_id: dict[str, str] = {}
    definition_sources: dict[str, str] = {}
    # Registrations that resolve to one file share its definition, named by the
    # first of them, as the parser groups them; also while it fails to load.
    source_definitions: dict[str, str] = {}

    for registration in registrations:
        if registration.path in path_to_def_id:
            continue
        span = _span(
            registration.line or 1,
            0,
            registration.line or 1,
            0,
        )
        try:
            child_path, config_base = resolve_submodel_reference(
                registration.path,
                pipeline_dir=parent_path.parent,
                project_root=project_root,
            )
        except ValueError as exc:
            def_id = registration.path
            path_to_def_id[registration.path] = def_id
            diagnostic = _diagnostic(
                code="submodel_path_invalid",
                scope="submodel",
                message=str(exc),
                source_file=source_file,
                element_id=def_id,
                source_span=span,
                remediation="Use a project-contained relative submodel path.",
            )
            diagnostics.append(diagnostic)
            definitions[def_id] = _unavailable_submodel_definition(
                registration,
                definition_id=def_id,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue

        if not child_path.is_file():
            def_id = registration.path
            path_to_def_id[registration.path] = def_id
            diagnostic = _diagnostic(
                code="submodel_file_missing",
                scope="submodel",
                message=f"Referenced submodel file {registration.path!r} does not exist.",
                source_file=source_file,
                element_id=def_id,
                source_span=span,
                remediation="Restore the file or update the registration path.",
            )
            diagnostics.append(diagnostic)
            definitions[def_id] = _unavailable_submodel_definition(
                registration,
                definition_id=def_id,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue

        source_key = submodel_source_key(child_path)
        if source_key in source_definitions:
            path_to_def_id[registration.path] = source_definitions[source_key]
            continue

        try:
            child_source = captures.read_source(child_path)
            child_graph = parse_submodel_source(
                child_source,
                source_file=str(child_path),
                _base_dir=config_base,
            )
        except (HauteError, OSError) as exc:
            def_id = registration.path
            path_to_def_id[registration.path] = def_id
            source_definitions[source_key] = def_id
            code = (
                "submodel_syntax_invalid"
                if isinstance(exc, ParseError) and isinstance(exc.__cause__, SyntaxError)
                else "submodel_definition_invalid"
            )
            definitions[def_id] = _recover_unavailable_submodel_definition(
                registration,
                child_path,
                definition_id=def_id,
                project_root=project_root,
                config_base=config_base,
                diagnostics=diagnostics,
                captures=captures,
                code=code,
                message=_exception_message(exc),
                remediation=(
                    exc.context.get(
                        "remediation",
                        "Open the submodel source and correct the diagnosed definition.",
                    )
                    if isinstance(exc, HauteError)
                    else "Open the submodel source and correct the diagnosed definition."
                ),
                failure=exc,
            )
            continue
        except Exception:  # noqa: BLE001 - named submodel recovery isolation boundary
            def_id = registration.path
            path_to_def_id[registration.path] = def_id
            source_definitions[source_key] = def_id
            incident_id = uuid4().hex
            logger.error(
                "pipeline_recovery_submodel_unexpected",
                source_file=_wire_path(child_path, project_root),
                definition_id=def_id,
                incident_id=incident_id,
                exc_info=True,
            )
            definitions[def_id] = _recover_unavailable_submodel_definition(
                registration,
                child_path,
                definition_id=def_id,
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

        def_id = child_graph._parser_definition_id or registration.path
        path_to_def_id[registration.path] = def_id
        source_definitions[source_key] = def_id

        if def_id in definition_sources and definition_sources[def_id] != source_key:
            diagnostic = _diagnostic(
                code="submodel_definition_duplicate",
                scope="submodel",
                message="One submodel definition id resolves to more than one file.",
                source_file=source_file,
                element_id=def_id,
                source_span=span,
                remediation="Give each submodel file a unique definition id.",
            )
            diagnostics.append(diagnostic)
            definitions[def_id] = _unavailable_submodel_definition(
                registration,
                definition_id=def_id,
                diagnostic_ids=[diagnostic.diagnostic_id],
            )
            continue

        definition_sources[def_id] = source_key
        input_ports = [
            port.model_dump(mode="json", by_alias=True)
            for port in (child_graph._parser_input_ports or [])
        ]
        output_ports = [
            port.model_dump(mode="json", by_alias=True)
            for port in (child_graph._parser_output_ports or [])
        ]
        definitions[def_id] = RecoverySubmodelDefinition(
            definition_id=def_id,
            file=registration.path,
            availability="ready",
            graph=_canonical_snapshot(
                child_graph,
                source_path=child_path,
                project_root=project_root,
                diagnostics=diagnostics,
                captures=captures,
            ),
            input_ports=input_ports,
            output_ports=output_ports,
        )

    occurrences: list[_RecoveredCandidate] = []
    for registration, occurrence_id in zip(registrations, occurrence_ids, strict=True):
        def_id = path_to_def_id.get(registration.path, registration.path)
        definition = definitions.get(def_id)
        if definition is None:
            definition = _unavailable_submodel_definition(registration, definition_id=def_id)
            definitions[def_id] = definition
        span = _span(
            registration.line or 1,
            0,
            registration.line or 1,
            0,
        )
        diagnostic_ids = list(definition.diagnostic_ids)
        availability = definition.availability
        if names[registration.name] > 1:
            diagnostic = _diagnostic(
                code="submodel_alias_duplicate",
                scope="submodel",
                message=f"Submodel name {registration.name!r} is duplicated.",
                source_file=source_file,
                element_id=occurrence_id,
                source_span=span,
                remediation="Give every submodel occurrence a unique name.",
            )
            diagnostics.append(diagnostic)
            diagnostic_ids.append(diagnostic.diagnostic_id)
            availability = "unavailable"
        occurrences.append(
            _RecoveredCandidate(
                authored_id=registration.name,
                recovery_id=occurrence_id,
                decorator_name="submodel",
                node_type=NodeType.SUBMODEL,
                description=registration.name,
                config={
                    "definitionId": def_id,
                    "alias": registration.name,
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
                endpoint_ids=(registration.name,),
                submodel_input_ports=_port_names(definition.input_ports),
                submodel_output_ports=_port_names(definition.output_ports),
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
        # A syntax-invalid file is a source-only document: it references no
        # config or submodel artifact the revision could depend on.
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
    # The global constants file the pipeline constructor names is a config
    # artifact too: the revision must authenticate the constants a document
    # carries, as it does node configs.
    if not child:
        try:
            declared = _extract_global_constants_declaration(tree, receiver="pipeline")
        except ParseError:
            # A keyword the parser refuses names no file; the document reports
            # the refusal itself, so the manifest has nothing to hash.
            declared = False
        if declared:
            config_refs.append(GLOBAL_CONSTANTS_FILE)
    # Revision discovery must be at least as tolerant as the editor recovery
    # pass.  The strict extractor validates cross-registration uniqueness and
    # would otherwise turn a representable duplicate alias/instance into a
    # top-level source-only incident while computing the artifact manifest.
    registrations.extend(
        _recover_ast_submodel_registrations(
            tree,
            source_file="<revision-manifest>",
            diagnostics=[],
            include_invalid_references=True,
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
                    source = captures.read_source(source_path)
                else:
                    _raw, source = read_user_source(source_path)
            except (OSError, ParseError):
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


def name_violations_payload(violations: Sequence[NameViolation]) -> list[PipelineNameViolation]:
    """The wire form of executable-name violations, each party with its submodel."""
    return [
        PipelineNameViolation(
            kind=violation.kind,
            name=violation.name,
            message=violation.message(),
            parties=[
                PipelineNameViolationParty(
                    node_id=party.node_id,
                    label=party.label,
                    submodel=None if party.module == ROOT_MODULE else party.module,
                )
                for party in violation.parties
            ],
        )
        for violation in violations
    ]


def _unkept_statement_diagnostics(
    source: str,
    *,
    source_file: str,
    receiver: Literal["pipeline", "submodel"] = "pipeline",
) -> list[PipelineRecoveryDiagnostic]:
    """One diagnostic per module statement that a save would drop.

    *source* is the pipeline file's, or a registered submodel file's with
    *receiver* ``"submodel"``: a save regenerates both the same way. Each
    degrades the document, so nothing regenerates the file without the
    statement until it moves into the preamble or a preserved block.
    """
    constructor = "Pipeline" if receiver == "pipeline" else "Submodel"
    lines = python_lines(source)
    diagnostics = []
    for start_line, end_line in unkept_module_statements(
        source, receiver=receiver, constructor_name=constructor
    ):
        first = lines[start_line - 1].strip()
        excerpt = first if len(first) <= 60 else f"{first[:57]}..."
        diagnostics.append(
            _diagnostic(
                code="unkept_module_statement",
                scope=receiver,
                message=(
                    f"`{excerpt}` is outside the preamble and preserved blocks, where a save "
                    "would drop it."
                ),
                source_file=source_file,
                source_span=RecoverySourceSpan(
                    start_line=start_line,
                    start_column=0,
                    end_line=end_line,
                    end_column=len(lines[end_line - 1]),
                ),
                remediation=(
                    f"Move it between the `import haute` line and "
                    f"`{receiver} = haute.{constructor}(...)`, where it becomes part of the "
                    "preamble, or wrap it in `# haute:preserve-start` and "
                    "`# haute:preserve-end` lines."
                ),
            )
        )
    return diagnostics


def _capabilities(
    status: PipelineLoadStatus,
    *,
    source_selection_trusted: bool,
    names_valid: bool = True,
) -> PipelineDocumentCapabilities:
    """The admission fence; a file with name violations is editable but not runnable."""
    ready = status == "ready"
    return PipelineDocumentCapabilities(
        can_mutate=ready,
        can_save=ready and names_valid,
        can_execute=ready and names_valid,
        can_preview=status != "source_only" and source_selection_trusted and names_valid,
        can_manage_submodels=ready,
        can_repair=status == "degraded",
        reserved_api_input_frame_labels=sorted({*keyword.kwlist, *RESERVED_NAMES}),
    )


_SCOPED_EDITABLE_TYPES = {
    kind.value for kind in NodeType if kind not in {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
}


def _mark_scoped_editable(
    nodes: list[RecoveryPipelineNode],
    submodels: dict[str, RecoverySubmodelDefinition] | None,
) -> None:
    """Advisory per-node isolation-save eligibility; the endpoint re-derives it.

    Eligible: a known ordinary type that loads (ready or blocked), with a
    trustworthy source span, not a shared instance, and no config reference
    shared with another node. Document-wide fences stay separate.
    """
    all_nodes: list[RecoveryPipelineNode] = []

    def collect(
        graph_nodes: list[RecoveryPipelineNode],
        graph_submodels: dict[str, RecoverySubmodelDefinition] | None,
    ) -> None:
        all_nodes.extend(graph_nodes)
        for definition in (graph_submodels or {}).values():
            collect(definition.graph.nodes, definition.graph.submodels)

    collect(nodes, submodels)
    reference_counts = Counter(node.config_reference for node in all_nodes if node.config_reference)
    for node in all_nodes:
        node.scoped_editable = (
            node.node_type in _SCOPED_EDITABLE_TYPES
            and node.availability != "unavailable"
            and node.source_file is not None
            and node.source_span is not None
            and not (node.config or {}).get("instanceOf")
            and (node.config_reference is None or reference_counts[node.config_reference] == 1)
        )


def _node_completeness(
    nodes: list[RecoveryPipelineNode],
    submodels: dict[str, RecoverySubmodelDefinition] | None,
) -> list[PipelineNodeCompleteness]:
    """Field-level required-value gaps for loadable Data Input/Output nodes.

    Completeness is recomputed from the io-layer validators' tolerant mode;
    unavailable nodes are excluded because their problems belong to
    ``diagnostics``. A ready node's config already passed the same structural
    validation at parse, so the reporters cannot raise here.
    """
    from haute._polars_io_registry import data_input_completeness, data_output_completeness

    entries: list[PipelineNodeCompleteness] = []

    def visit(
        graph_nodes: list[RecoveryPipelineNode],
        graph_submodels: dict[str, RecoverySubmodelDefinition] | None,
    ) -> None:
        for node in graph_nodes:
            if node.availability == "unavailable" or node.config is None:
                continue
            if node.node_type == NodeType.DATA_INPUT.value:
                gaps = data_input_completeness(node.config)
            elif node.node_type == NodeType.DATA_OUTPUT.value:
                gaps = data_output_completeness(node.config)
            else:
                continue
            entries.extend(
                PipelineNodeCompleteness(
                    element_id=node.recovery_id,
                    path=gap.path,
                    code=gap.code,
                    message=gap.message,
                )
                for gap in gaps
            )
        for definition in (graph_submodels or {}).values():
            visit(definition.graph.nodes, definition.graph.submodels)

    visit(nodes, submodels)
    return entries


def empty_pipeline_editor_document() -> PipelineEditorDocument:
    """Return the editable new-project canvas when no authored file exists."""
    return PipelineEditorDocument(
        load_status="ready",
        has_authored_content=False,
        capabilities=_capabilities("ready", source_selection_trusted=True),
    )


def _recover_global_constants(
    tree: ast.Module,
    path: Path,
    captures: _SourceCaptures,
) -> tuple[list[GlobalConstant], str | None]:
    """The global constants a recovered document carries, read through *captures*.

    A constructor keyword the strict parser would refuse leaves no constants
    and its reason, so the pane shows why instead of an empty list.
    """
    try:
        declared = _extract_global_constants_declaration(tree, receiver="pipeline")
    except ParseError as exc:
        return [], str(exc)
    if not declared:
        return [], None
    return load_declared_global_constants(
        path.parent,
        read_bytes=captures.read_bytes,
    )


def _load_readable_pipeline_editor_document(
    path: Path,
    root: Path,
    source: str,
    captures: _SourceCaptures,
) -> PipelineEditorDocument:
    """Recover a source string whose path and readability are already trusted."""
    source_file = _wire_path(path, root)
    sidecar = read_sidecar_state(path, read_bytes=captures.read_bytes)
    positions, sources, active_source, source_selection_trusted = _sidecar_values(sidecar)
    diagnostics: list[PipelineRecoveryDiagnostic] = []
    sidecar_issue = _sidecar_diagnostic(sidecar, source_file=source_file)

    pipeline_name: str | None = None
    pipeline_description: str | None = None
    preamble: str | None = None
    preserved_blocks: list[str] = []
    global_constants: list[GlobalConstant] = []
    global_constants_error: str | None = None
    nodes: list[RecoveryPipelineNode] = []
    edges: list[RecoveryPipelineEdge] = []
    unresolved: list[RecoveryUnresolvedConnection] = []
    submodels: dict[str, RecoverySubmodelDefinition] | None = None
    connections: Sequence[_RecoveredConnection | tuple[str, str, str | None, str | None]] = []
    registrations: list[SubmodelRegistration] = []
    source_wide_failed = False

    strict_failure: BaseException | None = None
    strict_graph: PipelineGraph | None = None
    name_violations: list[NameViolation] = []
    # Each registered submodel file the strict parse reads, by wire path: a save
    # regenerates it too, so its unkept statements are checked like the pipeline's.
    submodel_sources: dict[str, str] = {}

    def read_submodel_source(child_path: Path) -> str:
        child_source = captures.read_source(child_path)
        submodel_sources[_wire_path(child_path, root)] = child_source
        return child_source

    try:
        # Parse the exact bytes this document presents. Re-reading the file
        # here could straddle a concurrent external edit and silently disagree
        # with ``source_text`` and the recovery pass.
        strict_graph, name_violations = parse_pipeline_source_with_name_violations(
            source,
            source_file=str(path),
            _base_dir=path.parent,
            _submodel_base_dir=_infer_parse_base_dir(path),
            _read_submodel_source=read_submodel_source,
            _read_global_constants_bytes=captures.read_bytes,
        )
    except (HauteError, OSError) as exc:
        strict_failure = exc
    else:
        assert strict_graph is not None
        snapshot = _canonical_snapshot(
            strict_graph,
            source_path=path,
            project_root=root,
            diagnostics=diagnostics,
            captures=captures,
        )
        pipeline_name = strict_graph.pipeline_name
        pipeline_description = strict_graph.pipeline_description
        preamble = strict_graph.preamble
        preserved_blocks = list(strict_graph.preserved_blocks)
        global_constants = list(strict_graph.global_constants)
        global_constants_error = strict_graph.global_constants_error
        nodes = snapshot.nodes
        edges = snapshot.edges
        unresolved = snapshot.unresolved_connections
        submodels = snapshot.submodels
        diagnostics.extend(_unkept_statement_diagnostics(source, source_file=source_file))
        for child_file, child_source in submodel_sources.items():
            diagnostics.extend(
                _unkept_statement_diagnostics(
                    child_source, source_file=child_file, receiver="submodel"
                )
            )

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
                    remediation=(
                        "Open the source at this location in your editor and correct the syntax."
                    ),
                )
            )
            # There is no textual recovery of syntax-invalid source: the
            # document is source-only and the canvas shows this parse error.
            source_wide_failed = True
            pipeline_name = path.stem
            pipeline_description = ""
            preserved_blocks = _extract_preserved_blocks(source)
        else:
            try:
                pipeline_name, pipeline_description = _extract_pipeline_meta(tree)
            except ParseError as exc:
                # A name or description that is not a literal belongs to the
                # constructor, not to any node; the rest of the file recovers.
                diagnostics.append(
                    _diagnostic(
                        code="pipeline_constructor_invalid",
                        scope="pipeline",
                        message=_exception_message(exc),
                        source_file=source_file,
                        remediation=(
                            "Give haute.Pipeline(...) its name and description as plain "
                            'strings, such as haute.Pipeline("motor").'
                        ),
                    )
                )
                pipeline_name, pipeline_description = None, ""
            pipeline_name = pipeline_name or path.stem
            preamble = _extract_preamble(source, tree=tree)
            preserved_blocks = _extract_preserved_blocks(source)
            global_constants, global_constants_error = _recover_global_constants(
                tree, path, captures
            )
            recovered_connections, initially_unresolved = _recover_ast_connections(
                tree,
                receiver="pipeline",
                source_file=source_file,
                diagnostics=diagnostics,
            )
            connections = recovered_connections
            bodies = _extract_function_bodies(source, tree=tree)
            skeletons = _extract_decorated_node_skeletons(
                tree,
                _is_pipeline_authored_decorator,
                bodies,
                source=source,
            )
            # Nodes and occurrences, valid or not, share one id space: mint every
            # id over all of them before anything records one in a diagnostic.
            recovery_id = _recovery_ids(
                [
                    *((skeleton.authored_id, skeleton.start_line) for skeleton in skeletons),
                    *(
                        (registration.name, registration.line or 1)
                        for registration in _recover_ast_submodel_registrations(
                            tree,
                            source_file=source_file,
                            diagnostics=[],
                            include_invalid_references=True,
                        )
                    ),
                ]
            )
            unavailable_registrations: list[_RecoveredCandidate] = []
            registrations = _recover_ast_submodel_registrations(
                tree,
                source_file=source_file,
                diagnostics=diagnostics,
                unavailable_candidates=unavailable_registrations,
                recovery_id=recovery_id,
            )
            candidates = [
                _candidate_from_ast(
                    skeleton,
                    recovery_id=recovery_id(skeleton.authored_id, skeleton.start_line),
                    source_file=source_file,
                    base_dir=path.parent,
                    diagnostics=diagnostics,
                )
                for skeleton in skeletons
            ]
            submodels, submodel_occurrences = _recover_registered_submodels(
                registrations,
                parent_path=path,
                project_root=root,
                source_file=source_file,
                diagnostics=diagnostics,
                captures=captures,
                recovery_id=recovery_id,
            )
            candidates.extend(submodel_occurrences)
            for unavailable in unavailable_registrations:
                # These handles are authored connection evidence only. The unavailable
                # card remains non-executable and its downstream path stays blocked.
                unavailable.submodel_output_ports = tuple(
                    dict.fromkeys(
                        connection.source_port
                        for connection in recovered_connections
                        if connection.source_authored_id == unavailable.authored_id
                        and connection.source_port is not None
                    )
                )
                unavailable.submodel_input_ports = tuple(
                    dict.fromkeys(
                        connection.target_port
                        for connection in recovered_connections
                        if connection.target_authored_id == unavailable.authored_id
                        and connection.target_port is not None
                    )
                )
            candidates.extend(unavailable_registrations)
            _attribute_binding_failure(
                strict_failure,
                candidates,
                source_file=source_file,
                diagnostics=diagnostics,
                definition=False,
            )
            _attribute_instance_failure(
                strict_failure, candidates, source_file=source_file, diagnostics=diagnostics
            )
            nodes, edges, unresolved = _build_recovery_graph(
                candidates,
                connections,
                source_file=source_file,
                positions=positions,
                diagnostics=diagnostics,
                initial_unresolved=initially_unresolved,
            )
            # The recovery pass re-checks each element, but not every refusal
            # belongs to one: unless a diagnostic already shows it, the parse's
            # own refusal is shown, so no other problem on screen can hide it.
            if not _strict_failure_shown(strict_failure, diagnostics):
                failure = strict_failure or ParseError("Pipeline parsing failed.")
                context = failure.context if isinstance(failure, HauteError) else {}
                line = context.get("line")
                lines = python_lines(source)
                diagnostics.append(
                    _diagnostic(
                        code="pipeline_parse_invalid",
                        scope="pipeline",
                        message=_exception_message(failure),
                        source_file=source_file,
                        # The parse's own line and fix, where its refusal names them.
                        source_span=(
                            _span(line, 0, line, len(lines[line - 1]))
                            if isinstance(line, int) and 0 < line <= len(lines)
                            else None
                        ),
                        remediation=str(
                            context.get("remediation")
                            or "Review the pipeline-level topology or submodel diagnostic."
                        ),
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
    _mark_scoped_editable(nodes, submodels)
    completeness = _node_completeness(nodes, submodels)
    kept_completeness = completeness[:_MAX_DIAGNOSTICS]
    return PipelineEditorDocument(
        load_status=load_status,
        pipeline_name=pipeline_name or path.stem,
        pipeline_description=pipeline_description or "",
        preamble=preamble,
        preserved_blocks=preserved_blocks,
        global_constants=global_constants,
        global_constants_error=global_constants_error,
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
        completeness=kept_completeness,
        completeness_omitted=max(0, len(completeness) - len(kept_completeness)),
        name_violations=name_violations_payload(name_violations),
        capabilities=_capabilities(
            load_status,
            source_selection_trusted=source_selection_trusted,
            names_valid=not name_violations,
        ),
    )


def pipeline_document_fingerprint(document_payload: Mapping[str, Any]) -> str:
    """Fingerprint a complete editor document's JSON-mode, by-alias wire payload.

    Load responses, live-sync frames, and resync comparisons all use this one digest, so a
    document loaded over HTTP compares equal to the same document recovered for a resync.
    """
    return hashlib.sha256(canonical_json(document_payload).encode("utf-8")).hexdigest()


def _undecodable_pipeline_editor_document(
    path: Path,
    root: Path,
    raw_source: bytes,
    failure: ParseError,
) -> PipelineEditorDocument:
    """The source-only document of a pipeline file that is not UTF-8.

    Python refuses to compile such a file, so like a syntax error it has no
    canvas. The text shown replaces the bytes it cannot decode and is only read:
    a source-only document can be neither saved nor repaired.
    """
    source_file = _wire_path(path, root)
    line, column = failure.context["line"], failure.context["column"]
    text = raw_source.removeprefix(b"\xef\xbb\xbf").decode("utf-8", errors="replace")
    return PipelineEditorDocument(
        load_status="source_only",
        pipeline_name=path.stem,
        pipeline_description="",
        source_file=source_file,
        source_revision=pipeline_recovery_revision(
            project_root=root,
            artifacts=[
                ("parent_source", path),
                ("parent_sidecar", path.with_suffix(".haute.json")),
            ],
            known_bytes={path: raw_source},
        ),
        source_text=text.replace("\r\n", "\n").replace("\r", "\n"),
        sources=[],
        active_source=None,
        source_selection_trusted=False,
        has_authored_content=bool(text.strip()),
        diagnostics=[
            _diagnostic(
                code="source_encoding_invalid",
                scope="pipeline",
                message=failure.message,
                source_file=source_file,
                source_span=_span(line, column - 1, line, column),
                remediation="Save the file as UTF-8 in your editor, then reload it.",
            )
        ],
        capabilities=_capabilities("source_only", source_selection_trusted=False),
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
    captures = _SourceCaptures(root)
    raw_source = captures.read_bytes(path)
    try:
        source = captures.read_source(path)
    except ParseError as exc:
        return _undecodable_pipeline_editor_document(path, root, raw_source, exc)
    try:
        with node_configs_read_through(captures.read_bytes):
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
