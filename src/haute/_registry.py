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


#: The canonical registry: one entry per :class:`NodeType`.
NODE_REGISTRY: dict[NodeType, NodeRegistryEntry] = {}


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
    unneeded columns to intermediate parquet files.
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
    """Assert every :class:`NodeType` has both exec and codegen builders.

    Called once after both ``_builders`` and ``_codegen_builders`` finish
    registering.  Raises ``RuntimeError`` with the missing set if any side
    is unregistered.
    """
    missing_exec: list[NodeType] = []
    missing_codegen: list[NodeType] = []
    for nt in NodeType:
        entry = NODE_REGISTRY.get(nt)
        if entry is None or entry.exec is None:
            missing_exec.append(nt)
        if entry is None or entry.codegen is None:
            missing_codegen.append(nt)
    if missing_exec or missing_codegen:
        raise RuntimeError(
            "NODE_REGISTRY is incomplete — every NodeType must register an "
            "exec builder AND a codegen builder.\n"
            f"  Missing exec:    {[n.value for n in missing_exec]}\n"
            f"  Missing codegen: {[n.value for n in missing_codegen]}"
        )


def ensure_registry_ready() -> None:
    """Import both builder modules so the registry is fully populated.

    Idempotent — importing the modules is a no-op after the first call
    thanks to ``sys.modules`` caching.  Callers that need a guaranteed-
    populated registry (such as ``haute.codegen`` at orchestration time)
    should invoke this before dispatching.
    """
    # Import ordering: exec side first (it declares ``NodeBuildContext``
    # that some codegen-side utilities reference via string annotations),
    # then codegen side.  Both modules perform their registrations as
    # side-effects of import.
    import haute._builders  # noqa: F401
    import haute._codegen_builders  # noqa: F401

    validate_registry_complete()


# Backward-compat views ─────────────────────────────────────────────────────
# These dicts reflect the exec / codegen sides of ``NODE_REGISTRY`` for code
# that pre-dated the unification.  They are derived *lazily*: each attribute
# read rebuilds the view so the legacy dicts cannot silently drift from the
# authoritative registry.  Callers that need to mutate (tests that monkey-
# patch a builder) should prefer mutating ``NODE_REGISTRY`` directly, but
# the monkey-patch pattern of "pop then restore" still works because a
# fresh view captures the current state.


def exec_view() -> dict[NodeType, ExecFn]:
    """Snapshot of the exec-side builders keyed by ``NodeType``."""
    return {nt: entry.exec for nt, entry in NODE_REGISTRY.items() if entry.exec is not None}


def codegen_view() -> dict[NodeType, CodegenFn]:
    """Snapshot of the codegen-side builders keyed by ``NodeType``."""
    return {nt: entry.codegen for nt, entry in NODE_REGISTRY.items() if entry.codegen is not None}
