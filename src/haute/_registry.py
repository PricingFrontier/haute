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
    from haute._codegen_builders import NodeSource
    from haute._types import GraphNode

#: Exec-side builder signature: ``(NodeBuildContext) -> (func_name, fn, is_source)``.
#: The context type is declared in ``_builders.py`` to avoid a circular import.
ExecFn = Callable[..., tuple[str, Callable[..., Any], bool]]

#: Codegen-side builder signature: ``(node, source_names) -> NodeSource``.
CodegenFn = Callable[["GraphNode", list[str]], "NodeSource"]


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
    #: The column-contract callback, given a node's config. Only meaningful on
    #: the exec side; stored here so the unified registry holds every callable.
    column_contract: Callable[[dict[str, Any]], Any] | None = field(default=None, repr=False)


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
) -> Callable[[ExecFn], ExecFn]:
    """Decorator to register the exec builder for *node_type*.

    The optional *column_contract* declares the node's column contract —
    which columns it creates and which input columns it reads — given its
    config dict.  Used by the checkpoint projection pass to avoid writing
    unneeded columns to intermediate parquet files. The type's other facts
    (behavioural, recompute cost, slice transparency) are its
    :class:`~haute._node_specs.NodeSpec`'s.
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


def validate_registry_complete() -> None:
    """Assert every :class:`NodeType` registers its callables.

    Called once after both ``_builders`` and ``_codegen_builders`` finish
    registering: every NodeType has an exec builder, a codegen builder and a
    column contract (previously optional yet dereferenced as mandatory at
    runtime — a missing one used to pass import validation and blow up later).
    The type's facts, and their invariants (a behavioural type is never a
    standalone passthrough), are checked by :mod:`haute._node_specs` at import.

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
            "exec builder, a codegen builder and a column contract.\n"
            f"  Missing exec:    {[n.value for n in missing_exec]}\n"
            f"  Missing codegen: {[n.value for n in missing_codegen]}\n"
            f"  Missing contract: {[n.value for n in missing_contract]}"
        )


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
