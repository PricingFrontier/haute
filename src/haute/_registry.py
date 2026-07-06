"""Single-source-of-truth node registry.

Both the graph executor (``_builders.py``) and the codegen (``_codegen_builders.py``)
dispatch on :class:`NodeType`.  Historically each maintained its own private
table, which silently drifted — e.g. ``SUBMODEL`` and ``SUBMODEL_PORT`` were
registered on the exec side but absent from codegen, silently falling through
to ``_gen_transform``.

This module centralises dispatch so execution and codegen cannot disagree.
Each :class:`NodeType` maps to a single :class:`NodeRegistryEntry` that bundles
the exec builder and the codegen builder.  Registrations happen at import
time from both ``_builders`` (exec side) and ``_codegen_builders`` (codegen
side).  :func:`validate_registry_complete` asserts every ``NodeType`` has both
sides populated — this is called on package import and fails loudly on any
gap.

The registrations are wired in two phases:

1. ``_builders`` imports this module and calls :func:`register_exec` for each
   of its ``_build_*`` functions.
2. ``_codegen_builders`` imports this module and calls :func:`register_codegen`
   for each of its ``_gen_*`` functions.

Both files are imported from ``haute.codegen`` at module-init time, so the
registry is complete before any caller dispatches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from haute._types import NodeType

if TYPE_CHECKING:
    from haute._types import GraphNode

#: Exec-side builder signature: ``(NodeBuildContext) -> (func_name, fn, is_source)``.
#: The context type is declared in ``_builders.py`` to avoid a circular import.
ExecFn = Callable[..., tuple[str, Callable[..., Any], bool]]

#: Codegen-side builder signature: ``(node, source_names) -> generated-source-str``.
CodegenFn = Callable[["GraphNode", list[str]], str]


@dataclass(slots=True)
class NodeRegistryEntry:
    """Unified dispatch entry for a single :class:`NodeType`.

    Exec and codegen sides are populated independently by their respective
    modules (``_builders`` and ``_codegen_builders``) but live in one object
    so drift is impossible.

    Fields default to ``None`` to allow incremental registration; the
    invariant "both fields populated for every ``NodeType``" is enforced by
    :func:`validate_registry_complete` at import time.
    """

    exec: ExecFn | None = None
    codegen: CodegenFn | None = None
    #: Optional column-contract function.  Only meaningful on the exec side;
    #: stored here so the unified registry is the single source of truth for
    #: per-type metadata.
    column_contract: Callable[[dict[str, Any]], Any] | None = field(default=None, repr=False)
    #: Whether this NodeType applies a genuine, stateful transform (banding,
    #: rating, modelScore, liveSwitch, scenarioExpander, optimiserApply) as
    #: opposed to a pure passthrough (modelling, optimiser preview, output,
    #: dataSink, submodel).  Set on the EXEC side at registration.
    #:
    #: The invariant :func:`validate_registry_complete` enforces at import:
    #: a behavioural type's codegen body must NOT be a bare
    #: ``return {first_param}`` passthrough.  This makes a saved standalone
    #: ``.py`` that silently no-ops for a stateful node UNREPRESENTABLE — the
    #: codegen twin must route through a shared ``apply_*_from_config`` helper
    #: (the same one the executor calls), so canvas and file cannot diverge.
    is_behavioural: bool = False


#: The canonical registry: one entry per :class:`NodeType`.
NODE_REGISTRY: dict[NodeType, NodeRegistryEntry] = {}
_REGISTRY_READY = False


def _entry(node_type: NodeType) -> NodeRegistryEntry:
    """Fetch or create the entry for *node_type*."""
    entry = NODE_REGISTRY.get(node_type)
    if entry is None:
        entry = NodeRegistryEntry()
        NODE_REGISTRY[node_type] = entry
    return entry


def register_exec(
    node_type: NodeType,
    *,
    column_contract: Callable[[dict[str, Any]], Any] | None = None,
    is_behavioural: bool = False,
) -> Callable[[ExecFn], ExecFn]:
    """Decorator to register the exec builder for *node_type*.

    The optional *column_contract* declares the node's column contract —
    which columns it creates and which input columns it reads — given its
    config dict.  Used by the checkpoint projection pass to avoid writing
    unneeded columns to intermediate parquet files.

    *is_behavioural* marks a stateful-apply node whose codegen body must not
    be a bare passthrough — enforced by :func:`validate_registry_complete`.
    """

    def decorator(fn: ExecFn) -> ExecFn:
        entry = _entry(node_type)
        if entry.exec is not None:
            raise RuntimeError(
                f"duplicate exec registration for {node_type!r}: existing={entry.exec!r} new={fn!r}"
            )
        entry.exec = fn
        if column_contract is not None:
            entry.column_contract = column_contract
        if is_behavioural:
            entry.is_behavioural = True
        return fn

    return decorator


def register_codegen(node_type: NodeType) -> Callable[[CodegenFn], CodegenFn]:
    """Decorator to register the codegen builder for *node_type*."""

    def decorator(fn: CodegenFn) -> CodegenFn:
        entry = _entry(node_type)
        if entry.codegen is not None:
            raise RuntimeError(
                f"duplicate codegen registration for {node_type!r}: "
                f"existing={entry.codegen!r} new={fn!r}"
            )
        entry.codegen = fn
        return fn

    return decorator


def set_codegen(node_type: NodeType, fn: CodegenFn) -> None:
    """Programmatic (non-decorator) registration of a codegen builder.

    Used by ``_codegen_builders`` for builders produced by factories like
    ``_make_passthrough_builder`` where the decorator form would require an
    extra wrapper.
    """
    entry = _entry(node_type)
    if entry.codegen is not None:
        raise RuntimeError(
            f"duplicate codegen registration for {node_type!r}: "
            f"existing={entry.codegen!r} new={fn!r}"
        )
    entry.codegen = fn


def get_exec(node_type: NodeType) -> ExecFn:
    """Return the exec builder for *node_type* or raise ``KeyError``.

    Fails loudly — missing entries indicate a registration bug, not a
    transient condition worth falling back from.
    """
    entry = NODE_REGISTRY.get(node_type)
    if entry is None or entry.exec is None:
        raise KeyError(f"no exec builder registered for {node_type!r}")
    return entry.exec


def get_codegen(node_type: NodeType) -> CodegenFn:
    """Return the codegen builder for *node_type* or raise ``KeyError``.

    Fails loudly — the previous silent fallback to ``_gen_transform``
    hid misregistered NodeTypes; now a missing entry crashes with a clear
    message pointing at the exact NodeType that needs wiring.
    """
    entry = NODE_REGISTRY.get(node_type)
    if entry is None or entry.codegen is None:
        raise KeyError(f"no codegen builder registered for {node_type!r}")
    return entry.codegen


def validate_registry_complete() -> None:
    """Assert every :class:`NodeType` is fully and consistently registered.

    Called once after both ``_builders`` and ``_codegen_builders`` finish
    registering.  Enforces three import-time invariants:

    1. Every NodeType has an exec builder AND a codegen builder.
    2. Every NodeType has a column contract (previously optional yet
       dereferenced as mandatory at runtime — a missing one used to pass
       import validation and blow up later).
    3. Every *behavioural* NodeType's codegen body is not a bare
       ``return {first_param}`` passthrough — so a saved standalone ``.py``
       cannot silently no-op for a stateful node.  This is what forces the
       codegen twin through a shared ``apply_*_from_config`` helper.

    Raises ``RuntimeError`` naming the offending NodeType(s).
    """
    missing_exec: list[NodeType] = []
    missing_codegen: list[NodeType] = []
    missing_contract: list[NodeType] = []
    for nt in NodeType:
        entry = NODE_REGISTRY.get(nt)
        if entry is None or entry.exec is None:
            missing_exec.append(nt)
        if entry is None or entry.codegen is None:
            missing_codegen.append(nt)
        if entry is None or entry.column_contract is None:
            missing_contract.append(nt)
    if missing_exec or missing_codegen or missing_contract:
        raise RuntimeError(
            "NODE_REGISTRY is incomplete — every NodeType must register an "
            "exec builder, a codegen builder, AND a column contract.\n"
            f"  Missing exec:    {[n.value for n in missing_exec]}\n"
            f"  Missing codegen: {[n.value for n in missing_codegen]}\n"
            f"  Missing contract: {[n.value for n in missing_contract]}"
        )

    _validate_behavioural_bodies_not_passthrough()


def _validate_behavioural_bodies_not_passthrough() -> None:
    """Assert no behavioural NodeType emits a bare passthrough codegen body.

    A behavioural (stateful-apply) node whose generated function body is
    just ``return <param>`` would silently no-op under a standalone
    ``pipeline.run()`` while the canvas executor applies the real transform.
    We probe each behavioural type's codegen builder on a minimal node and
    fail loudly at import if any return statement is a bare parameter — the
    structural guarantee that stateful codegen routes through a shared
    ``apply_*_from_config`` helper (returning a ``Call`` or a local, never a
    raw input frame).
    """
    import ast

    from haute._types import GraphNode, NodeData

    probe_source = "_probe_src"
    offenders: list[str] = []
    for nt in NodeType:
        entry = NODE_REGISTRY.get(nt)
        if entry is None or not entry.is_behavioural or entry.codegen is None:
            continue
        node = GraphNode(id=f"_probe_{nt.value}", data=NodeData(label="Probe", nodeType=nt))
        code = entry.codegen(node, [probe_source])
        if _codegen_body_is_bare_passthrough(ast.parse(code)):
            offenders.append(nt.value)
    if offenders:
        raise RuntimeError(
            "Behavioural NodeType(s) emit a bare `return {first}` passthrough "
            "codegen body, which would silently no-op in a standalone "
            "pipeline.run() while the executor applies the real transform. "
            "Route the codegen body through the shared apply_*_from_config "
            f"helper (the executor's twin):\n  {offenders}"
        )


def _codegen_body_is_bare_passthrough(tree: Any) -> bool:
    """Return True if the sole generated function returns a bare parameter."""
    import ast

    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        params = {arg.arg for arg in fn.args.args}
        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                if stmt.value.id in params:
                    return True
    return False


def ensure_registry_ready() -> None:
    """Import both builder modules so the registry is fully populated.

    Idempotent — importing the modules is a no-op after the first call
    thanks to ``sys.modules`` caching.  Callers that need a guaranteed-
    populated registry (such as ``haute.codegen`` at orchestration time)
    should invoke this before dispatching.
    """
    global _REGISTRY_READY
    if _REGISTRY_READY:
        return
    # Import ordering: exec side first (it declares ``NodeBuildContext``
    # that some codegen-side utilities reference via string annotations),
    # then codegen side.  Both modules perform their registrations as
    # side-effects of import.
    import haute._builders  # noqa: F401
    import haute._codegen_builders  # noqa: F401

    validate_registry_complete()
    _REGISTRY_READY = True
