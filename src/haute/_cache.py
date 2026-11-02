"""Graph fingerprinting for cache invalidation."""

from __future__ import annotations

import ast as _ast
import importlib as _importlib
import json as _json
import math as _math
import sys as _sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.machinery import PathFinder as _PathFinder
from pathlib import Path
from types import MappingProxyType
from typing import Any

from haute._global_constants import ConstantReads, graph_constant_reads
from haute._hashing import content_hash_bytes
from haute._json_shred._source_proof import file_signature
from haute._logging import get_logger
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph

logger = get_logger(component="cache")

# ---------------------------------------------------------------------------
# Algorithm versioning
# ---------------------------------------------------------------------------

# Fingerprint-algorithm version.  Embedded as a ``"v<N>:"`` prefix on
# every :func:`graph_fingerprint` output so that a future
# canonicalisation tweak (node-attribute order, edge representation,
# hash family, etc.) cannot silently collide with digests produced by
# the previous algorithm.  Bumping this constant invalidates every
# previously-cached fingerprint-keyed entry in a single step.
#
# Read dynamically inside :func:`graph_fingerprint` so tests can
# ``monkeypatch.setattr(haute._cache, "ALGO_VERSION", ...)`` to
# simulate a bump and confirm cache entries do not collide across
# versions — pinned by
# ``tests/test_routes_hygiene.py::TestBumpVersionInvalidatesCache``.
#
# v4: edge serialization became frame-aware — ``sourceHandle`` /
# ``targetHandle`` are now part of the digest material, so rewiring
# which frame feeds a consumer invalidates previews/traces/dataframes
# cached under the old wiring.
#
# v5: canonical-JSON encoder unification (W2.13).  The two divergent
# encoders (``_canonicalise`` here vs ``_normalise_execution_policy``
# in the dataframe execution cache, since removed) were replaced by the single
# :func:`canonical_json`.  Node-config digest material switched from
# spaced ``json.dumps`` separators to the canonical compact form, so
# every node with a non-empty config produces different digest bytes.
#
# v6: fingerprint-material framing became injective (W1-cache F164).
# The node line and the ``graph_fingerprint`` extra-keys/context join are
# now emitted through :func:`canonical_json` instead of raw ``|``/``\n``
# concatenation, so a node id or extra key that literally contains those
# separators can no longer collide with a logically-different graph.  The
# NaN sort order in :func:`_sort_key` also became total (F163).  The byte
# layout of every digest changed, so previously-cached entries invalidate
# in one step.
#
# v7: fingerprint factories route through exact checked input contracts.
# Structural identity now includes execution labels, excludes presentation
# edge IDs, and keys the source-file context used for relative resolution.
#
# v8: repeated graph/runtime identity records use versioned, checked shapes.
# Preview lineage fields are explicit rather than hidden beneath an opaque
# graph member, and runtime identity declares its required structural companion.
ALGO_VERSION: int = 9


class CacheInputClass(StrEnum):
    """Closed logical input classes shared by maintained cache consumers."""

    NODE_CONFIG = "node_config"
    UPSTREAM_LINEAGE = "upstream_lineage"
    EDGE_WIRING = "edge_wiring"
    USER_CODE = "user_code"
    SOURCE_SELECTION = "source_selection"
    ROW_LIMIT = "row_limit"
    RUNTIME_FILES = "runtime_files"
    ARTIFACTS = "artifacts"
    REQUEST_SHAPE = "request_shape"
    EXECUTION_POLICY = "execution_policy"
    GLOBAL_CONSTANTS = "global_constants"
    # The engine that computed a cached result: a Polars release can change what
    # the same pipeline computes, its row order or its dtypes.
    ENGINE_VERSION = "engine_version"


class CacheConsumer(StrEnum):
    """Maintained cache-key consumers with checked payload contracts."""

    GRAPH_STRUCTURE = "graph_structure"
    GRAPH_EXECUTION = "graph_execution"
    PREVIEW_TRACE = "preview_trace"
    RUNTIME_GRAPH_INPUT = "runtime_graph_input"
    DEPLOY_SCHEMA = "deploy_schema"
    MODEL_CONTRACT = "model_contract"
    INPUT_SNAPSHOT = "input_snapshot"
    NODE_SNAPSHOT_SIGNATURE = "node_snapshot_signature"


class CacheIdentityRecord(StrEnum):
    """Repeated nested records with closed, independently versioned shapes."""

    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    RUNTIME_INPUT_ENTRY = "runtime_input_entry"
    LIVE_SWITCH_SELECTION = "live_switch_selection"
    GLOBAL_CONSTANT = "global_constant"
    GLOBAL_CONSTANT_VALUE = "global_constant_value"


@dataclass(frozen=True, slots=True)
class CacheIdentityRecordContract:
    """Exact shape and version marker for nested cache identity material."""

    record: CacheIdentityRecord
    version: int
    fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("cache identity record version must be a positive integer")
        if len(set(self.fields)) != len(self.fields) or any(not field for field in self.fields):
            raise ValueError("cache identity record fields must be unique non-empty strings")


CACHE_IDENTITY_RECORD_CONTRACTS: Mapping[CacheIdentityRecord, CacheIdentityRecordContract] = (
    MappingProxyType(
        {
            CacheIdentityRecord.GRAPH_NODE: CacheIdentityRecordContract(
                record=CacheIdentityRecord.GRAPH_NODE,
                version=1,
                fields=("id", "label", "nodeType", "config"),
            ),
            CacheIdentityRecord.GRAPH_EDGE: CacheIdentityRecordContract(
                record=CacheIdentityRecord.GRAPH_EDGE,
                version=1,
                fields=("source", "sourceHandle", "target", "targetHandle"),
            ),
            CacheIdentityRecord.RUNTIME_INPUT_ENTRY: CacheIdentityRecordContract(
                record=CacheIdentityRecord.RUNTIME_INPUT_ENTRY,
                version=1,
                fields=("node_id", "node_type", "config", "files"),
            ),
            CacheIdentityRecord.LIVE_SWITCH_SELECTION: CacheIdentityRecordContract(
                record=CacheIdentityRecord.LIVE_SWITCH_SELECTION,
                version=1,
                fields=("switch_id", "incoming_edges"),
            ),
            # A read constant as declared, every source's value included; a name
            # the pipeline does not define carries ``None`` throughout.
            CacheIdentityRecord.GLOBAL_CONSTANT: CacheIdentityRecordContract(
                record=CacheIdentityRecord.GLOBAL_CONSTANT,
                version=1,
                fields=("name", "type", "value", "by_source"),
            ),
            # A read constant resolved for one source: ``state`` is ``set``,
            # ``missing`` (split without that source) or ``undefined``.
            CacheIdentityRecord.GLOBAL_CONSTANT_VALUE: CacheIdentityRecordContract(
                record=CacheIdentityRecord.GLOBAL_CONSTANT_VALUE,
                version=1,
                fields=("name", "type", "value", "state"),
            ),
        }
    )
)


def checked_cache_identity_record(
    record: CacheIdentityRecord,
    values: Mapping[str, object],
) -> dict[str, object]:
    """Return canonical nested identity material after exact-shape validation."""

    if not isinstance(record, CacheIdentityRecord):
        raise TypeError("record must be a CacheIdentityRecord")
    if not isinstance(values, Mapping):
        raise TypeError("cache identity record values must be a mapping")
    contract = CACHE_IDENTITY_RECORD_CONTRACTS[record]
    actual = set(values)
    expected = set(contract.fields)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise ValueError(
            f"{record.value} cache identity record differs (missing={missing}, unknown={unknown})"
        )
    return {
        "cache_record_schema": {
            "record": record.value,
            "version": contract.version,
        },
        **{field_name: values[field_name] for field_name in contract.fields},
    }


@dataclass(frozen=True, slots=True)
class CacheInputDisposition:
    """How one logical input class participates in a consumer key."""

    fields: tuple[str, ...] = ()
    exclusion_reason: str = ""

    def __post_init__(self) -> None:
        if bool(self.fields) == bool(self.exclusion_reason):
            raise ValueError(
                "cache input disposition must consume fields or carry an exclusion rationale"
            )
        if self.exclusion_reason and not self.exclusion_reason.strip():
            raise ValueError("cache input exclusion rationale must be non-empty")
        if len(set(self.fields)) != len(self.fields) or any(not field for field in self.fields):
            raise ValueError("cache input disposition fields must be unique non-empty strings")


@dataclass(frozen=True, slots=True)
class CacheConsumerContract:
    """Exact checked payload schema and logical completeness classification."""

    consumer: CacheConsumer
    version: int
    fields: tuple[str, ...]
    input_classes: Mapping[CacheInputClass, CacheInputDisposition]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("cache consumer contract version must be a positive integer")
        if len(set(self.fields)) != len(self.fields) or any(not field for field in self.fields):
            raise ValueError("cache consumer contract fields must be unique non-empty strings")
        if set(self.input_classes) != set(CacheInputClass):
            missing = sorted(set(CacheInputClass) - set(self.input_classes))
            extra = sorted(set(self.input_classes) - set(CacheInputClass))
            raise ValueError(
                f"{self.consumer.value} must classify every cache input class "
                f"(missing={missing}, extra={extra})"
            )
        for input_class, disposition in self.input_classes.items():
            unknown = set(disposition.fields) - set(self.fields)
            if unknown:
                raise ValueError(
                    f"{self.consumer.value} class {input_class.value} references unknown "
                    f"payload fields {sorted(unknown)}"
                )
        classified_fields = {
            field_name
            for disposition in self.input_classes.values()
            for field_name in disposition.fields
        }
        unclassified_fields = set(self.fields) - classified_fields
        if unclassified_fields:
            raise ValueError(
                f"{self.consumer.value} has unclassified payload fields "
                f"{sorted(unclassified_fields)}"
            )
        object.__setattr__(self, "input_classes", MappingProxyType(dict(self.input_classes)))


def _consumed(*fields: str) -> CacheInputDisposition:
    return CacheInputDisposition(fields=tuple(fields))


def _excluded(reason: str) -> CacheInputDisposition:
    if not reason.strip():
        raise ValueError("cache input exclusion rationale must be non-empty")
    return CacheInputDisposition(exclusion_reason=reason)


def _consumer_contract(
    consumer: CacheConsumer,
    *,
    version: int,
    fields: tuple[str, ...],
    consumed: Mapping[CacheInputClass, tuple[str, ...]],
    excluded: Mapping[CacheInputClass, str],
) -> CacheConsumerContract:
    overlap = set(consumed) & set(excluded)
    if overlap:
        raise ValueError(
            f"{consumer.value} input classes cannot be both consumed and excluded: "
            f"{sorted(item.value for item in overlap)}"
        )
    dispositions = {
        input_class: _consumed(*field_names) for input_class, field_names in consumed.items()
    }
    dispositions.update(
        {input_class: _excluded(reason) for input_class, reason in excluded.items()}
    )
    return CacheConsumerContract(
        consumer=consumer,
        version=version,
        fields=fields,
        input_classes=dispositions,
    )


_CALLER_SCOPES_LINEAGE = "The caller supplies the already-scoped graph or lineage."
_CALLER_KEYS_SOURCE = "The caller keys source selection outside this structural digest."
_FULL_FRAME_NO_ROW_LIMIT = "This cache stores a complete materialised frame, not a row slice."
_NO_RUNTIME_STATE = "Runtime state is owned by a separate input/artifact identity."
_NO_GRAPH_INPUT = "This cache is independent of pipeline graph execution."
_CONSTANT_VALUES_ARE_EXECUTION_INPUTS = (
    "Constant values are execution inputs; a structured reference to a constant is node config."
)
_ENGINE_SIGNED_BY_RESULTS = (
    "Structure does not depend on the engine; the result identities built on it sign the "
    "Polars version."
)
_REQUIRES_STRUCTURAL_IDENTITY = (
    "This external-state component must be paired with the caller's checked structural "
    "graph or lineage identity."
)

CACHE_CONSUMER_CONTRACTS: Mapping[CacheConsumer, CacheConsumerContract] = MappingProxyType(
    {
        CacheConsumer.GRAPH_STRUCTURE: _consumer_contract(
            CacheConsumer.GRAPH_STRUCTURE,
            version=2,
            fields=("nodes", "edges"),
            consumed={
                CacheInputClass.NODE_CONFIG: ("nodes",),
                CacheInputClass.UPSTREAM_LINEAGE: ("nodes", "edges"),
                CacheInputClass.EDGE_WIRING: ("edges",),
                CacheInputClass.USER_CODE: ("nodes",),
                CacheInputClass.REQUEST_SHAPE: ("nodes", "edges"),
            },
            excluded={
                CacheInputClass.SOURCE_SELECTION: _CALLER_KEYS_SOURCE,
                CacheInputClass.ROW_LIMIT: "Graph structure is independent of collection limits.",
                CacheInputClass.RUNTIME_FILES: _NO_RUNTIME_STATE,
                CacheInputClass.ARTIFACTS: _NO_RUNTIME_STATE,
                CacheInputClass.EXECUTION_POLICY: "Execution policy is supplied by each consumer.",
                CacheInputClass.GLOBAL_CONSTANTS: _CONSTANT_VALUES_ARE_EXECUTION_INPUTS,
                CacheInputClass.ENGINE_VERSION: _ENGINE_SIGNED_BY_RESULTS,
            },
        ),
        CacheConsumer.GRAPH_EXECUTION: _consumer_contract(
            CacheConsumer.GRAPH_EXECUTION,
            version=ALGO_VERSION,
            fields=(
                "base_fingerprint",
                "preamble_fingerprint",
                "global_constants",
                "source_file",
                "extra_keys",
            ),
            consumed={
                CacheInputClass.NODE_CONFIG: ("base_fingerprint",),
                CacheInputClass.UPSTREAM_LINEAGE: ("base_fingerprint",),
                CacheInputClass.EDGE_WIRING: ("base_fingerprint",),
                CacheInputClass.USER_CODE: (
                    "base_fingerprint",
                    "preamble_fingerprint",
                    "source_file",
                ),
                CacheInputClass.REQUEST_SHAPE: ("extra_keys",),
                CacheInputClass.GLOBAL_CONSTANTS: ("global_constants",),
            },
            excluded={
                CacheInputClass.SOURCE_SELECTION: _CALLER_KEYS_SOURCE,
                CacheInputClass.ROW_LIMIT: "A caller may key limits explicitly through extra_keys.",
                CacheInputClass.RUNTIME_FILES: _NO_RUNTIME_STATE,
                CacheInputClass.ARTIFACTS: _NO_RUNTIME_STATE,
                CacheInputClass.EXECUTION_POLICY: "A caller keys policy in its own contract.",
                CacheInputClass.ENGINE_VERSION: _ENGINE_SIGNED_BY_RESULTS,
            },
        ),
        CacheConsumer.PREVIEW_TRACE: _consumer_contract(
            CacheConsumer.PREVIEW_TRACE,
            version=5,
            fields=(
                "preamble",
                "global_constants",
                "source_file",
                "nodes",
                "edges",
                "target_node_id",
                "source",
                "requested_columns",
                "initial_column_limit",
                "row_limit",
                "port_label",
                "contract_fingerprint",
                "selected_live_switch_path",
                "runtime_input_fingerprint",
                "execution_semantics_version",
                "polars_version",
            ),
            consumed={
                CacheInputClass.NODE_CONFIG: ("nodes",),
                CacheInputClass.UPSTREAM_LINEAGE: ("nodes", "edges", "target_node_id"),
                CacheInputClass.EDGE_WIRING: ("edges", "selected_live_switch_path"),
                CacheInputClass.USER_CODE: (
                    "preamble",
                    "source_file",
                    "nodes",
                    "runtime_input_fingerprint",
                ),
                CacheInputClass.SOURCE_SELECTION: ("source", "selected_live_switch_path"),
                CacheInputClass.ROW_LIMIT: ("initial_column_limit", "row_limit"),
                CacheInputClass.RUNTIME_FILES: ("runtime_input_fingerprint",),
                CacheInputClass.ARTIFACTS: ("runtime_input_fingerprint",),
                CacheInputClass.REQUEST_SHAPE: (
                    "target_node_id",
                    "requested_columns",
                    "port_label",
                ),
                CacheInputClass.EXECUTION_POLICY: (
                    "contract_fingerprint",
                    "execution_semantics_version",
                ),
                CacheInputClass.GLOBAL_CONSTANTS: ("global_constants",),
                CacheInputClass.ENGINE_VERSION: ("polars_version",),
            },
            excluded={},
        ),
        CacheConsumer.RUNTIME_GRAPH_INPUT: _consumer_contract(
            CacheConsumer.RUNTIME_GRAPH_INPUT,
            version=4,
            fields=(
                "source",
                "sources",
                "preamble_fingerprint",
                "extra",
            ),
            consumed={
                CacheInputClass.USER_CODE: ("sources", "preamble_fingerprint"),
                CacheInputClass.SOURCE_SELECTION: ("source",),
                CacheInputClass.RUNTIME_FILES: ("sources",),
                CacheInputClass.ARTIFACTS: ("sources", "extra"),
                CacheInputClass.REQUEST_SHAPE: ("extra",),
            },
            excluded={
                CacheInputClass.NODE_CONFIG: _REQUIRES_STRUCTURAL_IDENTITY,
                CacheInputClass.UPSTREAM_LINEAGE: _REQUIRES_STRUCTURAL_IDENTITY,
                CacheInputClass.EDGE_WIRING: _REQUIRES_STRUCTURAL_IDENTITY,
                CacheInputClass.ROW_LIMIT: "External input identity is independent of row limits.",
                CacheInputClass.EXECUTION_POLICY: (
                    "Execution policy is keyed by the frame consumer."
                ),
                CacheInputClass.GLOBAL_CONSTANTS: _REQUIRES_STRUCTURAL_IDENTITY,
                CacheInputClass.ENGINE_VERSION: (
                    "External input identity names source bytes, not what Polars computes "
                    "from them."
                ),
            },
        ),
        CacheConsumer.DEPLOY_SCHEMA: _consumer_contract(
            CacheConsumer.DEPLOY_SCHEMA,
            version=3,
            fields=(
                "graph_fingerprint",
                "runtime_input_fingerprint",
                "artifact_fingerprint",
                "output_node_id",
                "input_node_ids",
                "source",
                "row_limit",
                "execution_policy",
                "polars_version",
            ),
            consumed={
                CacheInputClass.NODE_CONFIG: ("graph_fingerprint",),
                CacheInputClass.UPSTREAM_LINEAGE: ("graph_fingerprint",),
                CacheInputClass.EDGE_WIRING: ("graph_fingerprint",),
                CacheInputClass.USER_CODE: ("graph_fingerprint", "runtime_input_fingerprint"),
                CacheInputClass.SOURCE_SELECTION: ("source",),
                CacheInputClass.ROW_LIMIT: ("row_limit",),
                CacheInputClass.RUNTIME_FILES: ("runtime_input_fingerprint",),
                CacheInputClass.ARTIFACTS: (
                    "runtime_input_fingerprint",
                    "artifact_fingerprint",
                ),
                CacheInputClass.REQUEST_SHAPE: ("output_node_id", "input_node_ids"),
                CacheInputClass.EXECUTION_POLICY: ("execution_policy",),
                CacheInputClass.GLOBAL_CONSTANTS: ("graph_fingerprint",),
                CacheInputClass.ENGINE_VERSION: ("polars_version",),
            },
            excluded={},
        ),
        CacheConsumer.MODEL_CONTRACT: _consumer_contract(
            CacheConsumer.MODEL_CONTRACT,
            version=1,
            fields=("feature_names", "categorical_features", "offset_column"),
            consumed={
                CacheInputClass.ARTIFACTS: (
                    "feature_names",
                    "categorical_features",
                    "offset_column",
                ),
                CacheInputClass.REQUEST_SHAPE: (
                    "feature_names",
                    "categorical_features",
                    "offset_column",
                ),
            },
            excluded={
                CacheInputClass.NODE_CONFIG: _NO_GRAPH_INPUT,
                CacheInputClass.UPSTREAM_LINEAGE: _NO_GRAPH_INPUT,
                CacheInputClass.EDGE_WIRING: _NO_GRAPH_INPUT,
                CacheInputClass.USER_CODE: _NO_GRAPH_INPUT,
                CacheInputClass.SOURCE_SELECTION: _NO_GRAPH_INPUT,
                CacheInputClass.ROW_LIMIT: "Schema validation is independent of row count.",
                CacheInputClass.RUNTIME_FILES: (
                    "The loaded model cache separately owns artifact-file freshness."
                ),
                CacheInputClass.EXECUTION_POLICY: (
                    "Feature/schema compatibility is policy-independent."
                ),
                CacheInputClass.GLOBAL_CONSTANTS: _NO_GRAPH_INPUT,
                CacheInputClass.ENGINE_VERSION: (
                    "A model's feature contract is fixed when it is trained."
                ),
            },
        ),
        CacheConsumer.INPUT_SNAPSHOT: _consumer_contract(
            CacheConsumer.INPUT_SNAPSHOT,
            version=1,
            fields=("schema_version", "provider", "descriptor"),
            consumed={
                CacheInputClass.NODE_CONFIG: ("provider", "descriptor"),
                CacheInputClass.SOURCE_SELECTION: ("provider", "descriptor"),
                CacheInputClass.REQUEST_SHAPE: ("schema_version",),
            },
            excluded={
                CacheInputClass.UPSTREAM_LINEAGE: _NO_GRAPH_INPUT,
                CacheInputClass.EDGE_WIRING: _NO_GRAPH_INPUT,
                CacheInputClass.USER_CODE: (
                    "Post-read code changes execution, not external source bytes."
                ),
                CacheInputClass.ROW_LIMIT: "Snapshots contain the complete provider result.",
                CacheInputClass.RUNTIME_FILES: (
                    "Source freshness/signatures are generation metadata, not source identity."
                ),
                CacheInputClass.ARTIFACTS: (
                    "Published generations are selected after source identity resolution."
                ),
                CacheInputClass.EXECUTION_POLICY: (
                    "Build boundedness controls admission, not source identity."
                ),
                CacheInputClass.GLOBAL_CONSTANTS: (
                    "Post-read code changes execution, not external source bytes."
                ),
                CacheInputClass.ENGINE_VERSION: (
                    "A snapshot copies its source, whose bytes its part digests prove; its "
                    "metadata records the Polars version that described it, and another "
                    "version re-describes it rather than fetching the source again."
                ),
            },
        ),
        CacheConsumer.NODE_SNAPSHOT_SIGNATURE: _consumer_contract(
            CacheConsumer.NODE_SNAPSHOT_SIGNATURE,
            version=3,
            fields=(
                "lineage_fingerprint",
                "runtime_input_fingerprint",
                "source",
                "semantics_class",
                "enforce_contracts",
                "preamble_supplied",
                "execution_semantics_version",
                "polars_version",
            ),
            consumed={
                CacheInputClass.NODE_CONFIG: ("lineage_fingerprint",),
                CacheInputClass.UPSTREAM_LINEAGE: ("lineage_fingerprint",),
                CacheInputClass.EDGE_WIRING: ("lineage_fingerprint",),
                CacheInputClass.USER_CODE: (
                    "lineage_fingerprint",
                    "runtime_input_fingerprint",
                    "preamble_supplied",
                ),
                CacheInputClass.SOURCE_SELECTION: ("source",),
                CacheInputClass.RUNTIME_FILES: ("runtime_input_fingerprint",),
                CacheInputClass.ARTIFACTS: ("runtime_input_fingerprint",),
                CacheInputClass.EXECUTION_POLICY: (
                    "semantics_class",
                    "enforce_contracts",
                    "execution_semantics_version",
                ),
                CacheInputClass.GLOBAL_CONSTANTS: ("lineage_fingerprint",),
                CacheInputClass.ENGINE_VERSION: ("polars_version",),
            },
            excluded={
                CacheInputClass.ROW_LIMIT: _FULL_FRAME_NO_ROW_LIMIT,
                CacheInputClass.REQUEST_SHAPE: (
                    "A column demand widens a generation's column set instead of changing "
                    "the snapshot identity."
                ),
            },
        ),
    }
)


def polars_version() -> str:
    """The installed Polars version, which every identity of Polars-computed contents signs.

    The full version, since a release can change what the same pipeline
    computes; read on each call.
    """
    import polars

    return polars.__version__


@dataclass(frozen=True, slots=True)
class CheckedCacheInputs:
    """One exact, validated cache-consumer payload."""

    consumer: CacheConsumer
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        contract = CACHE_CONSUMER_CONTRACTS[self.consumer]
        actual = set(self.values)
        expected = set(contract.fields)
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {', '.join(missing)}")
            if unknown:
                details.append(f"unknown fields: {', '.join(unknown)}")
            raise ValueError(f"{self.consumer.value} fingerprint inputs " + "; ".join(details))
        ordered = {field: self.values[field] for field in contract.fields}
        object.__setattr__(self, "values", MappingProxyType(ordered))

    @property
    def contract(self) -> CacheConsumerContract:
        return CACHE_CONSUMER_CONTRACTS[self.consumer]

    @property
    def ordered_values(self) -> tuple[object, ...]:
        return tuple(self.values[field] for field in self.contract.fields)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "consumer": self.consumer.value,
            "schema_version": self.contract.version,
            "inputs": dict(self.values),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json(self.payload).encode("utf-8")


def checked_cache_inputs(
    consumer: CacheConsumer,
    values: Mapping[str, object],
) -> CheckedCacheInputs:
    """Validate and order one consumer's complete logical input payload."""

    if not isinstance(consumer, CacheConsumer):
        raise TypeError("consumer must be a CacheConsumer")
    if not isinstance(values, Mapping):
        raise TypeError("cache fingerprint values must be a mapping")
    return CheckedCacheInputs(consumer=consumer, values=values)


def checked_cache_input_values(
    consumer: CacheConsumer,
    values: Mapping[str, object],
) -> tuple[object, ...]:
    """Validate one consumer payload and return its contract-ordered values.

    Process-local hot keys do not need the canonical payload wrapper. Preserve
    the same closed-field contract without allocating that wrapper on every
    hit; mappings already written in contract order take the lightweight path,
    while reordered or invalid mappings use the full builder.
    """

    if not isinstance(consumer, CacheConsumer):
        raise TypeError("consumer must be a CacheConsumer")
    if not isinstance(values, Mapping):
        raise TypeError("cache fingerprint values must be a mapping")
    contract = CACHE_CONSUMER_CONTRACTS[consumer]
    if tuple(values) == contract.fields:
        return tuple(values[field] for field in contract.fields)
    return checked_cache_inputs(consumer, values).ordered_values


_MODEL_PICKER_BROWSE_STATE = frozenset({"experiment_id", "experiment_name", "run_name"})

# The persisted config fields no execution reads. An execution identity signs
# a node's whole config but these, so a new setting is covered the moment it
# is declared; the specification says why each one is left out.
PRESENTATION_CONFIG_FIELDS: Mapping[NodeType, frozenset[str]] = MappingProxyType(
    {
        # The model picker writes the experiment it is browsing without changing
        # the selected model, which execution finds by its stable ids.
        NodeType.MODEL_SCORE: _MODEL_PICKER_BROWSE_STATE,
        NodeType.OPTIMISER_APPLY: _MODEL_PICKER_BROWSE_STATE,
        # Overview, Pivot and Chart cards present the explored frame; choosing
        # one never changes it.
        NodeType.EXPLORE: frozenset({"overview", "pivot_formulas", "pivots", "charts"}),
    }
)


@dataclass(frozen=True)
class _UtilityFileStatKey:
    """Metadata that identifies unchanged utility file bytes inside one memo."""

    path: Path
    mtime_ns: int
    size: int


@dataclass
class GraphFingerprintMemo:
    """Request-scoped memo for repeated graph fingerprint calculations.

    File metadata is not a complete correctness boundary: an editor or copy
    tool can preserve both size and mtime while changing bytes. Keep this memo
    scoped to one immutable request/operation and use fresh content hashes for
    independent calls.

    Signing a lineage's nodes reads the same graph and inputs once per node, so
    a memo also keeps, for the graphs and nodes it has seen, the canonical
    execution graph and each node's runtime-input entry. Those read the
    filesystem: a memo reads each once, and a caller that must see an input
    change (after preparing inputs, or to prove one did not move) uses a fresh
    memo.
    """

    utility_file_hashes: dict[_UtilityFileStatKey, str] = field(default_factory=dict)
    # id(graph) -> (graph, its canonical execution graph); the graph is held so
    # its id cannot be reused while the memo lives.
    canonical_graphs: dict[int, tuple[PipelineGraph, PipelineGraph]] = field(default_factory=dict)
    # node id -> (node, source file, runtime-input entry) for the node object it read.
    runtime_input_entries: dict[str, tuple[GraphNode, str | None, Mapping[str, object]]] = field(
        default_factory=dict
    )
    # The canonical graph_node and graph_edge records of the node and edge
    # objects it has seen; an edge's also keeps its canonical JSON, its sort key.
    graph_node_records: dict[str, tuple[GraphNode, Any]] = field(default_factory=dict)
    graph_edge_records: dict[str, tuple[GraphEdge, Any, str]] = field(default_factory=dict)


def canonical_json(value: Any) -> str:
    """THE canonical-JSON encoding for digest material — the only one.

    Every byte of fingerprint/cache-key material in this codebase that is
    JSON-shaped must be produced by this function (graph node configs,
    edge wiring, preamble context, dataframe-execution payloads and
    policies).  Two encoders with subtly different rules is how silent
    cache collisions and phantom invalidations are born — do not add a
    second one; import this.

    The persisted feature-contract ``_hash_payload`` encoder is a deliberate
    compatibility exception: its historic byte representation must not change.

    Canonical rules:

      * Mappings (any :class:`collections.abc.Mapping`) require string
        keys (``TypeError`` otherwise — the empty string is a valid key)
        and serialize with keys sorted by code point.
      * ``list``/``tuple`` serialize as JSON arrays in element order.
        Other iterables (generators, ranges, NumPy arrays, ...) raise
        ``TypeError``: silently consuming arbitrary iterables would let
        non-JSON values masquerade as digest material.
      * ``set``/``frozenset`` members are ordered by ``(type-tag, value)``
        — ``None`` < ``bool`` < numbers (numeric order) < strings (code
        point order) < arrays < objects — see :func:`_sort_key`.
      * Scalars (``None``/``bool``/``int``/``float``/``str``) use
        ``json.dumps`` text forms; non-finite floats serialize as the
        deterministic ``Infinity``/``-Infinity``/``NaN`` tokens (this is
        digest material, not interchange JSON).
      * Output is compact (``(",", ":")`` separators) and ASCII-escaped.
      * Anything else raises ``TypeError`` — fail loud, never ``repr()``.
    """
    return _canonical_dumps(_canonicalise(value))


def _canonical_dumps(canonical_value: Any) -> str:
    """Serialize an already-canonicalised value with the one true format."""
    return _json.dumps(
        canonical_value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _canonicalise(value: Any) -> Any:
    """Recursively convert *value* to a JSON-safe, order-independent form.

    The resulting structure is fed to :func:`_canonical_dumps` to produce
    a digest that is:

      * deterministic across runs (no ``repr()``-based fallbacks that
        depend on hash-seed or insertion order);
      * equal for sets / frozensets whose elements are the same regardless
        of the order they were inserted (unordered containers are sorted);
      * equal for mappings regardless of key insertion order.

    Unsupported types raise ``TypeError`` loudly rather than silently
    reducing to ``repr()``.  This ensures a drift in config shape is
    caught at fingerprint time instead of producing quietly-wrong digests.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        # ``bool`` is a subclass of ``int`` but that's fine for our use —
        # both survive ``json.dumps`` losslessly.  We intentionally reject
        # ``bytes`` and ``complex`` below because neither has a canonical
        # JSON text form.
        return value
    if isinstance(value, (list, tuple)):
        return [_canonicalise(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Canonicalise members first so mixed-type sets raise loudly on
        # unsupported members rather than hitting the ``sorted`` TypeError
        # with a confusing message.
        members = [_canonicalise(v) for v in value]
        try:
            return sorted(members, key=_sort_key)
        except TypeError as exc:  # heterogeneous unsortable set
            raise TypeError(
                f"Cannot fingerprint set with unsortable members: {exc}",
            ) from exc
    if isinstance(value, Mapping):
        canon: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Cannot fingerprint mapping with non-string key of type {type(k).__name__!r}",
                )
            canon[k] = _canonicalise(v)
        return canon
    raise TypeError(
        f"Cannot fingerprint value of type {type(value).__name__!r} — "
        f"no deterministic canonical form is defined",
    )


def _sort_key(value: Any) -> tuple[str, Any]:
    """Key function for sorting canonicalised set members.

    Produces a tuple of (type-tag, value) so mixed-type canonical values
    (all of which are JSON-safe by construction) can be ordered stably
    without relying on cross-type ``<`` support.  Strings order by raw
    code point — never by their ASCII-escaped JSON text, which would
    flip the order of non-ASCII members.
    """
    if value is None:
        return ("0_none", 0)
    if isinstance(value, bool):
        return ("1_bool", value)
    if isinstance(value, (int, float)):
        # ``NaN`` compares False against everything (including itself), which
        # makes ``sorted`` order-dependent — a set containing NaN would then
        # canonicalise differently per insertion order, breaking the
        # unordered-container determinism contract.  Segregate NaN into a
        # fixed terminal bucket with a constant secondary value so every NaN
        # is byte-identical and can never displace a finite member.  Finite
        # values (and +/-inf, which order correctly) keep their natural
        # numeric order via bucket ``0``.
        if isinstance(value, float) and _math.isnan(value):
            return ("2_num", (1, 0.0))
        return ("2_num", (0, value))
    if isinstance(value, str):
        return ("3_str", value)
    if isinstance(value, list):
        # Nested structures: sort by their canonical JSON encoding (the
        # members are already canonicalised, so this is deterministic).
        return ("4_list", _canonical_dumps(value))
    if isinstance(value, dict):
        return ("5_dict", _canonical_dumps(value))
    raise TypeError(
        f"Cannot produce sort key for canonicalised value of type {type(value).__name__!r}",
    )


def _graph_base_fingerprint(
    graph: PipelineGraph,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Compute the base structural digest of a graph (node configs + edges).

    The result is memoised once per :class:`PipelineGraph` instance via the
    :attr:`PipelineGraph._haute_base_fingerprint` cached_property — this
    function is the raw computation behind that cache, not something recomputed
    on every call.  Freshness across edits is guaranteed by the immutable
    ``model_copy(update=...)`` idiom: ``model_copy`` produces a new instance
    and clears the memo (see ``PipelineGraph._HAUTE_CACHED_PROPERTY_NAMES``),
    so a structurally-different graph never serves a stale digest.

    With *memo*, each node's and edge's record is canonicalised once for every
    graph the memo fingerprints, as signing each node of a lineage needs.
    """
    nodes = [_canonical_node_record(n, memo) for n in sorted(graph.nodes, key=lambda n: n.id)]
    # Handles select which frame/role reaches a consumer. Edge IDs are canvas
    # identity only, so endpoint/handle tuples define execution wiring.
    edges = [
        record
        for record, _text in sorted(
            (_canonical_edge_record(edge, memo) for edge in graph.edges),
            key=lambda item: item[1],
        )
    ]
    inputs = checked_cache_inputs(
        CacheConsumer.GRAPH_STRUCTURE,
        {
            "nodes": nodes,
            "edges": edges,
        },
    )
    # Every record is already canonical, so the payload is not walked again.
    return content_hash_bytes(_canonical_dumps(inputs.payload).encode("utf-8"))


def _canonical_node_record(node: GraphNode, memo: GraphFingerprintMemo | None) -> Any:
    if memo is not None:
        known = memo.graph_node_records.get(node.id)
        if known is not None and known[0] is node:
            return known[1]
    record = _canonicalise(
        checked_cache_identity_record(
            CacheIdentityRecord.GRAPH_NODE,
            {
                "id": node.id,
                "label": node.data.label,
                "nodeType": str(node.data.nodeType),
                "config": _node_config_for_execution_fingerprint(node),
            },
        )
    )
    if memo is not None:
        memo.graph_node_records[node.id] = (node, record)
    return record


def _canonical_edge_record(edge: GraphEdge, memo: GraphFingerprintMemo | None) -> tuple[Any, str]:
    if memo is not None:
        known = memo.graph_edge_records.get(edge.id)
        if known is not None and known[0] is edge:
            return known[1], known[2]
    record = _canonicalise(
        checked_cache_identity_record(
            CacheIdentityRecord.GRAPH_EDGE,
            {
                "source": edge.source,
                "sourceHandle": edge.sourceHandle,
                "target": edge.target,
                "targetHandle": edge.targetHandle,
            },
        )
    )
    text = _canonical_dumps(record)
    if memo is not None:
        memo.graph_edge_records[edge.id] = (edge, record, text)
    return record, text


def _node_config_for_execution_fingerprint(node: GraphNode) -> dict[str, Any]:
    """Return the node's config without the fields only its editor presents."""

    presentation = PRESENTATION_CONFIG_FIELDS.get(node.data.nodeType, frozenset())
    return {key: value for key, value in node.data.config.items() if key not in presentation}


# Preview lineage keys deliberately do not use ``graph_fingerprint``: a preview
# must be invalidated by precisely the portion of the graph that can execute
# for its selected target/source, not by unrelated canvas state.
LINEAGE_CACHE_KEY_VERSION = CACHE_CONSUMER_CONTRACTS[CacheConsumer.PREVIEW_TRACE].version


@dataclass(frozen=True)
class LineageCacheKeyRequest:
    """All dimensions which identify a lineage-scoped preview result.

    ``prepared`` is structural on purpose.  Importing ``PreparedGraph`` here
    would create a cache/projection import cycle, so its small public shape is
    checked by :func:`lineage_cache_key` instead.
    """

    graph: PipelineGraph
    prepared: Any
    target_node_id: str | None
    source: str
    requested_columns: Iterable[str] | None
    initial_column_limit: int | None
    row_limit: int | None
    port_label: str | None
    contract_fingerprint: str
    selected_live_switch_path: tuple[dict[str, object], ...]
    runtime_input_fingerprint: str
    execution_semantics_version: str


def _lineage_node_identity(node: GraphNode) -> dict[str, Any]:
    return checked_cache_identity_record(
        CacheIdentityRecord.GRAPH_NODE,
        {
            "id": node.id,
            "label": node.data.label,
            "nodeType": str(node.data.nodeType),
            "config": _node_config_for_execution_fingerprint(node),
        },
    )


def _lineage_edge_identity(edge: GraphEdge) -> dict[str, Any]:
    return checked_cache_identity_record(
        CacheIdentityRecord.GRAPH_EDGE,
        {
            "source": edge.source,
            "sourceHandle": edge.sourceHandle,
            "target": edge.target,
            "targetHandle": edge.targetHandle,
        },
    )


def _normalise_requested_columns(columns: Iterable[str] | None) -> tuple[str, ...] | None:
    if columns is None:
        return None
    if isinstance(columns, (str, bytes)):
        raise TypeError("requested_columns must be an iterable of non-empty strings")

    normalised: list[str] = []
    seen: set[str] = set()
    for column in columns:
        if not isinstance(column, str) or not column:
            raise ValueError("requested_columns must contain only non-empty strings")
        if column not in seen:
            seen.add(column)
            normalised.append(column)
    return tuple(normalised)


def _validate_lineage_request(
    request: LineageCacheKeyRequest,
) -> tuple[dict[str, GraphNode], list[GraphEdge]]:
    if not isinstance(request.graph, PipelineGraph):
        raise TypeError("graph must be a PipelineGraph")
    for name in (
        "source",
        "contract_fingerprint",
        "runtime_input_fingerprint",
        "execution_semantics_version",
    ):
        if not isinstance(getattr(request, name), str):
            raise TypeError(f"{name} must be a string")
    if request.target_node_id is not None and not isinstance(request.target_node_id, str):
        raise TypeError("target_node_id must be a string or None")
    if request.port_label is not None and not isinstance(request.port_label, str):
        raise TypeError("port_label must be a string or None")
    for name in ("initial_column_limit", "row_limit"):
        value = getattr(request, name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer or None")

    prepared = request.prepared
    if not all(hasattr(prepared, name) for name in ("node_map", "order", "relevant_edges")):
        raise ValueError("prepared graph does not match: missing lineage fields")
    if not isinstance(prepared.node_map, Mapping):
        raise ValueError("prepared graph does not match: node_map is invalid")

    graph_nodes = request.graph.node_map
    relevant_ids = list(prepared.order)
    if len(set(relevant_ids)) != len(relevant_ids):
        raise ValueError("prepared graph does not match: duplicate node id")
    relevant_nodes: dict[str, GraphNode] = {}
    for node_id in relevant_ids:
        prepared_node = prepared.node_map.get(node_id)
        graph_node = graph_nodes.get(node_id)
        if prepared_node is None or graph_node is None:
            raise ValueError("prepared graph does not match: missing relevant node")
        if canonical_json(_lineage_node_identity(prepared_node)) != canonical_json(
            _lineage_node_identity(graph_node),
        ):
            raise ValueError("prepared graph does not match: relevant node differs")
        relevant_nodes[node_id] = graph_node

    graph_edges = {canonical_json(_lineage_edge_identity(edge)) for edge in request.graph.edges}
    relevant_edges = list(prepared.relevant_edges)
    for edge in relevant_edges:
        if canonical_json(_lineage_edge_identity(edge)) not in graph_edges:
            raise ValueError("prepared graph does not match: relevant edge differs")
        if edge.source not in relevant_nodes or edge.target not in relevant_nodes:
            raise ValueError("prepared graph does not match: edge leaves lineage")
    if request.target_node_id is not None and request.target_node_id not in relevant_nodes:
        raise ValueError("prepared graph does not match: target is not relevant")
    return relevant_nodes, relevant_edges


def selected_live_switch_path(prepared: Any) -> tuple[dict[str, object], ...]:
    """Return the source-selected incoming wiring of relevant live switches."""
    relevant_ids = set(prepared.order)
    switches: list[dict[str, object]] = []
    for switch_id in sorted(relevant_ids):
        node = prepared.node_map.get(switch_id)
        if node is None or node.data.nodeType != NodeType.LIVE_SWITCH:
            continue
        incoming = [
            _lineage_edge_identity(edge)
            for edge in prepared.relevant_edges
            if edge.target == switch_id and edge.source in relevant_ids
        ]
        switches.append(
            {
                "switch_id": switch_id,
                "incoming_edges": tuple(sorted(incoming, key=canonical_json)),
            },
        )
    return tuple(switches)


def lineage_cache_key(request: LineageCacheKeyRequest) -> str:
    """Build a deterministic, lineage-scoped preview cache key."""
    if not isinstance(request, LineageCacheKeyRequest):
        raise TypeError("request must be a LineageCacheKeyRequest")
    relevant_nodes, relevant_edges = _validate_lineage_request(request)
    requested_columns = _normalise_requested_columns(request.requested_columns)

    payload = {
        "preamble": request.graph.preamble,
        "global_constants": _global_constant_values_identity(
            request.graph,
            graph_constant_reads(
                [relevant_nodes[node_id] for node_id in sorted(relevant_nodes)],
                request.graph.node_map,
            ),
            source=request.source,
        ),
        "source_file": request.graph.source_file,
        "nodes": [
            _lineage_node_identity(relevant_nodes[node_id]) for node_id in sorted(relevant_nodes)
        ],
        "edges": sorted(
            (_lineage_edge_identity(edge) for edge in relevant_edges),
            key=canonical_json,
        ),
        "target_node_id": request.target_node_id,
        "source": request.source,
        "requested_columns": requested_columns,
        "initial_column_limit": request.initial_column_limit,
        "row_limit": request.row_limit,
        "port_label": request.port_label,
        "contract_fingerprint": request.contract_fingerprint,
        "selected_live_switch_path": tuple(
            checked_cache_identity_record(CacheIdentityRecord.LIVE_SWITCH_SELECTION, selection)
            for selection in request.selected_live_switch_path
        ),
        "runtime_input_fingerprint": request.runtime_input_fingerprint,
        "execution_semantics_version": request.execution_semantics_version,
        "polars_version": polars_version(),
    }
    inputs = checked_cache_inputs(CacheConsumer.PREVIEW_TRACE, payload)
    return f"lineage-preview:v{LINEAGE_CACHE_KEY_VERSION}:" + content_hash_bytes(
        inputs.canonical_bytes,
    )


def _is_utility_module_name(value: str) -> bool:
    return value == "utility" or value.startswith("utility.")


def _string_contains_utility_import(value: str) -> bool:
    stripped = value.strip()
    return (
        _is_utility_module_name(stripped)
        or "import utility" in stripped
        or "from utility" in stripped
    )


def _call_imports_utility(node: _ast.Call) -> bool:
    if not node.args:
        return False
    first_arg = node.args[0]
    if not isinstance(first_arg, _ast.Constant) or not isinstance(first_arg.value, str):
        return False

    func = node.func
    if isinstance(func, _ast.Name) and func.id == "__import__":
        return _is_utility_module_name(first_arg.value)
    if (
        isinstance(func, _ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, _ast.Name)
        and func.value.id == "importlib"
    ):
        return _is_utility_module_name(first_arg.value)
    if isinstance(func, _ast.Name) and func.id == "exec":
        return _string_contains_utility_import(first_arg.value)
    return False


def preamble_imports_utility(preamble: str) -> bool:
    """Return whether *preamble* imports the project ``utility`` package."""
    if not preamble.strip():
        return False
    try:
        tree = _ast.parse(preamble)
    except SyntaxError:
        # The executor will surface the syntax error later.  For cache-key
        # purposes, keep invalid preambles that mention utility sensitive
        # to utility edits instead of serving stale error/output entries.
        return "utility" in preamble

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if _is_utility_module_name(alias.name):
                    return True
        elif isinstance(node, _ast.ImportFrom):
            module = node.module or ""
            if _is_utility_module_name(module):
                return True
        elif isinstance(node, _ast.Call):
            if _call_imports_utility(node):
                return True
    return False


def _pipeline_dir(graph: PipelineGraph) -> Path | None:
    source_file = graph.source_file
    if not source_file:
        return None
    path = Path(source_file)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve().parent


def _utility_search_path(pipeline_dir: str | Path | None) -> list[str]:
    """Return the search path the ``utility`` import resolves through.

    The project's ``utility`` finder searches the project's directories first
    (:func:`haute._project_imports.utility_search_dirs`), and a project without a
    ``utility`` leaves the name to ``sys.path``.  Hashing whatever *this* path
    resolves keeps the fingerprint aligned with the module that actually executes —
    a two-dir scan silently misses a ``utility`` resolved elsewhere on ``sys.path``.
    """
    from haute._project_imports import utility_search_dirs

    prioritised = utility_search_dirs(pipeline_dir)

    seen = set(prioritised)
    ordered = list(prioritised)
    for entry in _sys.path:
        # Skip the empty string (means "cwd", already covered) and duplicates
        # so the resolution order matches the executor exactly.
        if not entry or entry in seen:
            continue
        seen.add(entry)
        ordered.append(entry)
    return ordered


def _resolve_utility_locations(pipeline_dir: str | Path | None) -> list[Path] | None:
    """Resolve the top-level ``utility`` module/package the preamble will import.

    Uses :class:`importlib.machinery.PathFinder` against the same search order the
    ``utility`` import resolves through, so the bytes we hash are the bytes that will
    run.  ``PathFinder.find_spec`` performs pure resolution — it
    neither imports the module nor mutates ``sys.modules`` — and we invalidate
    the finder caches first so a freshly created/edited ``utility`` is seen.

    Returns the filesystem location(s) to hash (a single file for a module, one
    or more directories for a package / namespace package), or ``None`` when
    ``utility`` is not importable from the current path (recorded as
    ``"missing"`` so a later creation still invalidates the digest).
    """
    _importlib.invalidate_caches()
    spec = _PathFinder.find_spec("utility", _utility_search_path(pipeline_dir))
    if spec is None:
        return None
    search_locations = spec.submodule_search_locations
    if search_locations:
        return [Path(loc).resolve() for loc in search_locations]
    origin = spec.origin
    if origin and origin not in ("built-in", "frozen", "namespace"):
        return [Path(origin).resolve()]
    return None


def _stat_key_for_utility_file(path: Path) -> _UtilityFileStatKey:
    stat = path.stat()
    return _UtilityFileStatKey(path=path.resolve(), mtime_ns=stat.st_mtime_ns, size=stat.st_size)


def _utility_file_hash(path: Path, memo: GraphFingerprintMemo | None) -> str:
    """Return a content hash for *path* through the shared source proof.

    Every preamble fingerprint caller (supersession keys, execute_trace,
    preview keys) reaches :func:`haute._json_shred._source_proof.file_signature`,
    so a utility file is hashed once per unchanged freshness token.

    The optional request-scoped *memo* additionally pins the FIRST digest
    observed for a given ``(path, mtime_ns, size)`` within one request, so a
    file changing mid-request cannot make one fingerprint call disagree with
    an earlier one inside the same operation.
    """
    key = _stat_key_for_utility_file(path)
    cached = memo.utility_file_hashes.get(key) if memo is not None else None
    if cached is not None:
        return cached

    digest = file_signature(key.path).digest
    if memo is not None:
        # Re-stat for the memo slot: if the file moved during the proof it was
        # retried against the settled state, so the digest belongs to the
        # CURRENT stat, not the pre-proof one.
        memo.utility_file_hashes[_stat_key_for_utility_file(path)] = digest
    return digest


def _hash_utility_candidate(
    path: Path,
    memo: GraphFingerprintMemo | None,
) -> dict[str, Any]:
    if not path.exists():
        return {"kind": "missing"}
    if path.is_file():
        return {"kind": "file", "hash": _utility_file_hash(path, memo)}
    if not path.is_dir():
        raise TypeError(f"Cannot fingerprint utility module root at {path!s}")

    files: list[dict[str, str]] = []
    for file_path in sorted(path.rglob("*.py")):
        if file_path.is_file():
            files.append(
                {
                    "path": file_path.relative_to(path).as_posix(),
                    "hash": _utility_file_hash(file_path, memo),
                }
            )
    return {"kind": "package", "files": files}


def preamble_execution_fingerprint(
    preamble: str | None,
    *,
    pipeline_dir: str | Path | None = None,
    memo: GraphFingerprintMemo | None = None,
) -> str | None:
    """Return a digest of preamble inputs that can affect execution.

    Empty preambles return ``None`` so callers can stay on their cheapest
    cache paths. Non-empty preambles always include the preamble text and,
    when they import the project ``utility`` package, the current contents
    of nearby ``utility.py`` / ``utility/**/*.py`` files.
    """
    preamble = preamble or ""
    if not preamble.strip():
        return None

    parts: list[dict[str, Any]] = [
        {"kind": "preamble", "hash": content_hash_bytes(preamble.encode())}
    ]
    if preamble_imports_utility(preamble):
        locations = _resolve_utility_locations(pipeline_dir)
        if locations is None:
            utility_entries: list[dict[str, Any]] = [{"kind": "missing"}]
        else:
            utility_entries = [_hash_utility_candidate(location, memo) for location in locations]
        parts.append({"kind": "utility", "entries": utility_entries})
    return content_hash_bytes(canonical_json(parts).encode())


def _read_constant_names(graph: PipelineGraph, reads: ConstantReads) -> list[str]:
    if isinstance(reads, frozenset):
        return sorted(reads)
    return sorted(constant.name for constant in graph.global_constants)


def _global_constants_identity(graph: PipelineGraph, reads: ConstantReads) -> object:
    """The declared definitions of the constants *reads* names, every source's value included.

    ``None`` when nothing is read, so a graph whose nodes read no constant
    signs no constant state at all.
    """
    if isinstance(reads, frozenset) and not reads:
        return None
    by_name = {constant.name: constant for constant in graph.global_constants}
    records = []
    for name in _read_constant_names(graph, reads):
        constant = by_name.get(name)
        records.append(
            checked_cache_identity_record(
                CacheIdentityRecord.GLOBAL_CONSTANT,
                {
                    "name": name,
                    "type": None if constant is None else constant.type,
                    "value": None if constant is None else constant.value,
                    "by_source": None if constant is None else constant.by_source,
                },
            )
        )
    return {"load_error": graph.global_constants_error, "constants": records}


def _global_constant_values_identity(
    graph: PipelineGraph,
    reads: ConstantReads,
    *,
    source: str,
) -> object:
    """What the constants *reads* names hold for *source*; ``None`` when nothing is read."""
    if isinstance(reads, frozenset) and not reads:
        return None
    by_name = {constant.name: constant for constant in graph.global_constants}
    records = []
    for name in _read_constant_names(graph, reads):
        constant = by_name.get(name)
        if constant is None:
            constant_type, value, state = None, None, "undefined"
        elif constant.by_source is None:
            constant_type, value, state = constant.type, constant.value, "set"
        elif source in constant.by_source:
            constant_type, value, state = constant.type, constant.by_source[source], "set"
        else:
            constant_type, value, state = constant.type, None, "missing"
        records.append(
            checked_cache_identity_record(
                CacheIdentityRecord.GLOBAL_CONSTANT_VALUE,
                {"name": name, "type": constant_type, "value": value, "state": state},
            )
        )
    return {"load_error": graph.global_constants_error, "constants": records}


def graph_fingerprint(
    graph: PipelineGraph,
    *extra_keys: str,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Deterministic hash of graph execution inputs for cache invalidation.

    *extra_keys* let specialised callers add request dimensions to this
    structural identity. Preview and trace use their narrower lineage contract.

    The graph's structural base fingerprint (node configs + edge topology)
    is computed once per ``PipelineGraph`` instance and cached via
    :attr:`PipelineGraph._haute_base_fingerprint`. Preamble text and imported
    project ``utility`` module content are mixed in dynamically so GUI edits
    cannot reuse stale execution artifacts.

    The returned value is prefixed with ``"v<ALGO_VERSION>:"`` so a
    future canonicalisation change (which bumps
    :data:`ALGO_VERSION`) cannot collide with stale cache entries.
    The constant is read **dynamically** on every call so tests (and
    emergency cache-busts) can monkeypatch it without re-importing.
    """
    base = graph._haute_base_fingerprint if memo is None else _graph_base_fingerprint(graph, memo)
    context_fingerprint = preamble_execution_fingerprint(
        graph.preamble,
        pipeline_dir=_pipeline_dir(graph),
        memo=memo,
    )
    inputs = checked_cache_inputs(
        CacheConsumer.GRAPH_EXECUTION,
        {
            "base_fingerprint": base,
            "preamble_fingerprint": context_fingerprint,
            "global_constants": _global_constants_identity(
                graph, graph._haute_global_constant_reads
            ),
            "source_file": graph.source_file,
            "extra_keys": tuple(extra_keys),
        },
    )
    digest = content_hash_bytes(inputs.canonical_bytes)
    fp = f"v{ALGO_VERSION}:{digest}"
    logger.debug("graph_fingerprint_computed", fingerprint=fp[:8], extra_keys=extra_keys)
    return fp
