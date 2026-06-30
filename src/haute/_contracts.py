"""Column-contract definitions and lookup helpers.

This module is intentionally lightweight: parser/codegen callers need the
contract dataclass and registry lookup without paying for the full executor-
side builder module import.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from haute._registry import NODE_REGISTRY, ensure_registry_ready
from haute._types import NodeType

# Column contract type: (produced_columns, referenced_columns).
# ``produced``: columns the node creates (not in input).  None = opaque.
# ``referenced``: input columns the node reads for computation.  None = opaque.
ColumnContract = tuple[set[str] | None, set[str] | None]
ColumnContractFn = Callable[[dict[str, Any]], ColumnContract]

#: Sentinel for builders that are genuinely opaque — user code, external
#: file schemas, etc.  Registering this explicitly (rather than omitting
#: a contract registration altogether) lets the system distinguish
#: "declared opaque" from "forgot to declare", which is important for
#: adoption tracking and the codegen/parser/executor contract pipeline.
OPAQUE_CONTRACT: ColumnContract = (None, None)

#: String sentinel emitted by codegen for opaque contracts.  Kept in
#: sync with ``tests.fixtures.expected_contracts.OPAQUE_SENTINEL``.
OPAQUE_CONTRACT_SENTINEL = "opaque"


@dataclass(frozen=True, slots=True)
class Contract:
    """Small dataclass mirror of the tuple-based ``ColumnContract``."""

    inputs: frozenset[str] | None
    outputs: frozenset[str] | None
    inputs_by_parent: Mapping[str, frozenset[str] | None] | None = None

    @classmethod
    def opaque(cls) -> Contract:
        """Return the canonical opaque contract (both sides unknown)."""
        return cls(inputs=None, outputs=None)

    @classmethod
    def from_tuple(cls, tup: ColumnContract) -> Contract:
        """Lift a ``(produced, referenced)`` tuple to a ``Contract``."""
        produced, referenced = tup
        inputs = _freeze(referenced)
        outputs = _freeze(produced)
        return cls(inputs=inputs, outputs=outputs)

    def to_tuple(self) -> ColumnContract:
        """Return the ``(produced, referenced)`` tuple form."""
        produced = set(self.outputs) if self.outputs is not None else None
        referenced = set(self.inputs) if self.inputs is not None else None
        return produced, referenced

    @classmethod
    def from_user_declared(cls, value: Any) -> Contract | None:
        """Normalise the many user-facing forms into a ``Contract``."""
        if value is None:
            return None
        if isinstance(value, Contract):
            return value
        if hasattr(value, "inputs") and hasattr(value, "outputs"):
            return cls(
                inputs=_freeze(value.inputs),
                outputs=_freeze(value.outputs),
                inputs_by_parent=_freeze_mapping(getattr(value, "inputs_by_parent", None)),
            )
        if isinstance(value, str):
            if value.strip().lower() == OPAQUE_CONTRACT_SENTINEL:
                return cls.opaque()
            raise ValueError(
                f"Invalid contract declaration: unknown string {value!r}. "
                f"The only accepted string form is {OPAQUE_CONTRACT_SENTINEL!r}.",
            )
        if isinstance(value, dict):
            unknown_keys = set(value) - {"inputs", "outputs", "inputs_by_parent"}
            if unknown_keys:
                raise ValueError(
                    "Invalid contract dict: unknown key(s) "
                    f"{sorted(unknown_keys)!r}; expected 'inputs', 'outputs', "
                    "and optional 'inputs_by_parent'.",
                )
            inputs_raw = value.get("inputs", ...)
            outputs_raw = value.get("outputs", ...)
            if inputs_raw is ... or outputs_raw is ...:
                raise ValueError(
                    "Invalid contract dict: expected both 'inputs' and "
                    f"'outputs' keys, got {sorted(value)}.",
                )
            return cls(
                inputs=_freeze(inputs_raw),
                outputs=_freeze(outputs_raw),
                inputs_by_parent=_freeze_mapping(value.get("inputs_by_parent")),
            )
        if isinstance(value, tuple) and len(value) == 2:
            a, b = value
            return cls(inputs=_freeze(a), outputs=_freeze(b))
        raise ValueError(
            f"Invalid contract declaration: unsupported type {type(value).__name__}; "
            "expected Contract, dict(inputs=..., outputs=...), 'opaque', or None.",
        )


def _freeze(value: Any) -> frozenset[str] | None:
    """Coerce an iterable of column names to ``frozenset[str]`` or ``None``."""
    if value is None:
        return None
    if isinstance(value, frozenset):
        return value
    if isinstance(value, (set, list, tuple)) or (
        isinstance(value, Iterable) and not isinstance(value, (str, bytes))
    ):
        out: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"Contract column names must be strings; got {type(item).__name__} ({item!r}).",
                )
            out.add(item)
        return frozenset(out)
    raise ValueError(
        f"Contract column set must be iterable; got {type(value).__name__}.",
    )


def _freeze_mapping(value: Any) -> dict[str, frozenset[str] | None] | None:
    """Coerce a parent-id -> column-set mapping used by fan-in contracts."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(
            "Contract inputs_by_parent must be a mapping of parent node ids to column sets.",
        )
    out: dict[str, frozenset[str] | None] = {}
    for parent_id, columns in value.items():
        if not isinstance(parent_id, str) or not parent_id:
            raise ValueError(
                "Contract inputs_by_parent keys must be non-empty parent node ids.",
            )
        out[parent_id] = _freeze(columns)
    return out


def get_column_contract(
    node_type: NodeType,
    config: dict[str, Any],
) -> ColumnContract:
    """Return the column contract for a node type."""
    ensure_registry_ready()
    entry = NODE_REGISTRY.get(node_type)
    if entry is None or entry.column_contract is None:
        raise KeyError(
            f"NodeType {node_type!r} has no column contract registered. "
            "Every builder in NODE_REGISTRY must also register a contract "
            "in NODE_REGISTRY (pass columns=... or opaque=True to "
            "_register).",
        )
    result: ColumnContract = entry.column_contract(config)
    return result
