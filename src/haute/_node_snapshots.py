"""Node-output snapshots in the shared source snapshot store.

A node's full output is stored once per *snapshot identity* — a slot
``(pipeline source file, node, source, semantics class)`` plus the node's
checked data signature — as immutable generations beside the input snapshots
of :class:`haute._source_cache.SourceCacheStore`. Both kinds share the store
root, byte quota, generation cap, and environment variables.

Node-output generations can be published, leased, evicted, and cleared by
several processes at once (the server, spawned workers, and CLI runs), so this
module adds two cross-process file locks on top of the store's process-local
state:

- a per-identity **publication lock**, held by one writer from its freshness
  re-check to its first lease;
- a store-wide **lease lock**, always taken after a publication lock, under
  which lease acquisition, publication through the publisher's first lease,
  and retirement are each atomic.

Every lease is also a marker file inside its generation naming the owning
process's token. A process holds an exclusive lock on its own token file for
its lifetime, so a marker whose token lock can be acquired belongs to a
process that has exited. Retirement renames a generation to a private name
under the lease lock and deletes its files only after the lock is released.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import polars as pl

from haute._cache import (
    CacheConsumer,
    GraphFingerprintMemo,
    canonical_json,
    checked_cache_inputs,
    graph_fingerprint,
)
from haute._chunked_writes import part_name, part_paths, scan_parts
from haute._env import int_env
from haute._execution_context import ExecutionProfile
from haute._file_ops import atomic_write_text, ensure_disk_headroom, remove_tree
from haute._logging import get_logger
from haute._source_cache import (
    _LEASE_PREFIX,
    INPUT_CACHE_MAX_BYTES_VARIABLE,
    INPUT_CACHE_MAX_GENERATIONS_VARIABLE,
    NODE_OUTPUT_PROVIDER,
    CacheBucket,
    SourceCacheCorruptError,
    SourceCacheError,
    SourceCacheGeneration,
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
    SourceCacheLegacyLayoutError,
    SourceCacheMetadata,
    SourceCacheQuotaExceededError,
    SourceCacheStore,
    _ensure_identity_marker,
    _StoreFileLock,
    _validate_generation_id,
    _validate_staging_token,
    _verification_key,
    classify_identity_marker,
    describe_parts,
    generation_bytes,
    new_staging_token,
)
from haute._types import PipelineGraph

logger = get_logger(component="node_snapshots")

NODE_SNAPSHOT_METADATA_VERSION = 2
NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION = "node-snapshot:v1"
BOUNDED_SEMANTICS_CLASS = "bounded"
DEFAULT_NODE_SNAPSHOT_MAX_BYTES = 40 * 1024 * 1024 * 1024
DEFAULT_NODE_SNAPSHOT_MAX_GENERATIONS = 512
# As with the input budget's variables in ``haute._source_cache``: the usage
# report names the variable it read the limit from, so the two cannot drift.
NODE_SNAPSHOT_MAX_BYTES_VARIABLE = "HAUTE_NODE_SNAPSHOT_MAX_BYTES"
NODE_SNAPSHOT_MAX_GENERATIONS_VARIABLE = "HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS"
_SLOT_INDEX_SCHEMA_VERSION = 1
_LAST_USED_UPDATE_INTERVAL_SECONDS = 60.0
_RETIRED_PREFIX = ".retired-"

Retention = Literal["pinned", "automatic"]
SlotState = Literal["current", "stale", "missing", "corrupt"]
PublicationOutcome = Literal["published", "superseded"]

# CACHE-S06 measured that an interactive preview carries the same rows and
# schema as a bounded execution but not the same row order, and row order is
# not part of the snapshot contract. An admitted preview therefore reads and
# writes the ``bounded`` class; this constant changes only the two mappings below.
PREVIEW_SHARES_BOUNDED_SEMANTICS = True

_BOUNDED_SNAPSHOT_PROFILES = frozenset(
    {
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
        ExecutionProfile.NODE_SNAPSHOT,
    }
)


@dataclass(frozen=True, slots=True)
class CacheBudgetUsage:
    """One budget's usage against its limits, and the variables that set them."""

    generations_used: int
    generations_limit: int
    generations_limit_variable: str
    bytes_used: int
    bytes_limit: int
    bytes_limit_variable: str


@dataclass(frozen=True, slots=True)
class CacheUsageReport:
    """Both of the store's budgets, which are independent of one another.

    Node outputs and input snapshots neither consume nor evict one another, so
    there is no combined total to report and none is offered.
    """

    node_outputs: CacheBudgetUsage
    input_snapshots: CacheBudgetUsage


@dataclass(frozen=True, slots=True)
class CacheOwnerUsage:
    """What one owner holds on disk: a node's output for a source, or an input.

    ``label`` is what to call it when the owner is no longer in anybody's
    graph — the node id for a node output, the source's path or table for an
    input snapshot.
    """

    bucket: CacheBucket
    node_id: str | None
    source: str | None
    label: str
    #: The pipeline whose node this is; several can share one project root.
    pipeline_source_file: str | None
    generations: int
    size_bytes: int
    newest_row_count: int | None
    newest_created_at: float | None
    #: How long the newest generation took to cache, when it was recorded.
    newest_build_seconds: float | None
    # The identities this owner covers, so a caller holding one — a node's
    # resolved input snapshot, say — can tell whether this owner is its own.
    identity_digests: frozenset[str]


@dataclass(frozen=True, slots=True)
class CacheInventory:
    """Every generation on disk, attributed to the owner its metadata names.

    ``unattributed_*`` covers what could not be attributed — a generation whose
    ``meta.json`` is absent or unreadable, and staging under an identity that
    holds no readable generation. It is reported rather than dropped so the
    owners' bytes plus the unattributed bytes account for what the budgets say.

    ``unmarked_identities`` counts identities that hold generations but whose
    provider marker does not classify. Those are charged to *both* budgets at
    admission, and therefore in :class:`CacheUsageReport`, while the inventory
    attributes each to the one bucket its metadata names — so this is the
    number that explains a difference between the two.
    """

    owners: tuple[CacheOwnerUsage, ...]
    unattributed_generations: int
    unattributed_bytes: int
    unmarked_identities: int


# Descriptor keys that name an input source without disclosing anything: a
# descriptor is already credential-free by construction, but naming the keys
# read here keeps an unexpected one from reaching a browser.
_INPUT_LABEL_KEYS = ("path", "table", "name")


def _input_label(provider: str, descriptor: Mapping[str, object]) -> str:
    for key in _INPUT_LABEL_KEYS:
        value = descriptor.get(key)
        if isinstance(value, str) and value:
            return value
    return provider


@dataclass(slots=True)
class _OwnerAccumulator:
    bucket: CacheBucket
    node_id: str | None
    source: str | None
    label: str
    pipeline_source_file: str | None = None
    generations: int = 0
    size_bytes: int = 0
    newest_row_count: int | None = None
    newest_created_at: float | None = None
    newest_build_seconds: float | None = None
    identity_digests: set[str] = field(default_factory=set)

    def add(
        self,
        *,
        size_bytes: int,
        row_count: int | None,
        created_at: float | None,
        build_seconds: float | None,
    ) -> None:
        self.generations += 1
        self.size_bytes += size_bytes
        if created_at is not None and (
            self.newest_created_at is None or created_at > self.newest_created_at
        ):
            self.newest_created_at = created_at
            self.newest_row_count = row_count
            self.newest_build_seconds = build_seconds

    def frozen(self) -> CacheOwnerUsage:
        return CacheOwnerUsage(
            bucket=self.bucket,
            node_id=self.node_id,
            source=self.source,
            label=self.label,
            pipeline_source_file=self.pipeline_source_file,
            generations=self.generations,
            size_bytes=self.size_bytes,
            newest_row_count=self.newest_row_count,
            newest_created_at=self.newest_created_at,
            newest_build_seconds=self.newest_build_seconds,
            identity_digests=frozenset(self.identity_digests),
        )


@dataclass(frozen=True, slots=True)
class _GenerationFacts:
    """The little of a generation's metadata the inventory reads."""

    provider: str
    descriptor: Mapping[str, object]
    #: The identity's own schema version, so it can be reconstructed exactly —
    #: a digest is a hash of the whole payload, version included.
    schema_version: int
    row_count: int | None
    created_at: float | None
    build_seconds: float | None


def _read_generation_facts(generation_dir: Path) -> _GenerationFacts | None:
    """Read one generation's owner facts, or ``None`` when it cannot be read.

    Deliberately forgiving: the inventory reports what it cannot attribute
    rather than failing, so a half-written or hand-damaged ``meta.json`` costs
    a row in the unattributed total and nothing else.
    """
    try:
        raw = json.loads((generation_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    identity = raw.get("identity")
    if not isinstance(identity, dict):
        return None
    provider = identity.get("provider")
    descriptor = identity.get("descriptor")
    if not isinstance(provider, str) or not provider or not isinstance(descriptor, dict):
        return None
    row_count = raw.get("row_count")
    created_at = raw.get("created_at")
    build_seconds = raw.get("build_seconds")
    schema_version = identity.get("schema_version")
    return _GenerationFacts(
        provider=provider,
        descriptor=descriptor,
        schema_version=(
            schema_version
            if isinstance(schema_version, int) and not isinstance(schema_version, bool)
            else 1
        ),
        row_count=row_count
        if isinstance(row_count, int) and not isinstance(row_count, bool)
        else None,
        created_at=float(created_at) if isinstance(created_at, (int, float)) else None,
        # Absent on a generation published before this was recorded, which
        # reads as unknown rather than as an instant build.
        build_seconds=float(build_seconds)
        if isinstance(build_seconds, (int, float)) and not isinstance(build_seconds, bool)
        else None,
    )


def _owner_for(
    facts: _GenerationFacts, identity_digest: str
) -> tuple[tuple[str, str, str], _OwnerAccumulator]:
    """The owner a generation belongs to, keyed so its generations group.

    A node output groups by node and source, which is what that node costs the
    budget across however many signatures it still has. An input snapshot groups
    by **identity**, never by its descriptor's label: the same file read with
    different arguments is a different identity with the same path, and merging
    the two would report one set of bytes for both — and then lose the rest
    entirely once a reader claimed the merged owner.
    """
    if facts.provider == NODE_OUTPUT_PROVIDER:
        node_id = facts.descriptor.get("node_id")
        source = facts.descriptor.get("source")
        pipeline = facts.descriptor.get("pipeline_source_file")
        if (
            isinstance(node_id, str)
            and node_id
            and isinstance(source, str)
            and source
            and isinstance(pipeline, str)
            and pipeline
        ):
            # Several pipelines can share one project root, and therefore one
            # store. Without the pipeline in the key, `alt.py`'s `join` would be
            # reported on `main.py`'s `join` row.
            return (
                (pipeline, node_id, source),
                _OwnerAccumulator(
                    bucket="node_output",
                    node_id=node_id,
                    source=source,
                    label=node_id,
                    pipeline_source_file=pipeline,
                ),
            )
    label = _input_label(facts.provider, facts.descriptor)
    return (
        ("input", facts.provider, identity_digest),
        _OwnerAccumulator(bucket="input", node_id=None, source=None, label=label),
    )


def _staging_bytes(identity_dir: Path) -> int:
    """Bytes held by an identity's in-flight staging directories."""
    total = 0
    try:
        entries = tuple(identity_dir.iterdir())
    except FileNotFoundError:
        return 0
    for staging in entries:
        if not staging.name.startswith(".staging-") or not staging.is_dir() or staging.is_symlink():
            continue
        for root, directories, files in os.walk(staging, followlinks=False):
            root_path = Path(root)
            directories[:] = [name for name in directories if not (root_path / name).is_symlink()]
            for name in files:
                file_path = root_path / name
                # The budget's own walk skips symlinked files; counting them
                # here would make the report and the bar disagree.
                if file_path.is_symlink():
                    continue
                try:
                    total += file_path.stat().st_size
                except (FileNotFoundError, OSError):
                    continue
    return total


def _fault_point(name: str) -> None:
    """Deterministic pause point for cross-process interleaving tests."""
    del name


class NodeSnapshotMultiFrameUnsupportedError(SourceCacheError):
    """A multi-frame output reached a node-output snapshot write."""

    error_code = "node_snapshot_multi_frame_unsupported"


class NodeSnapshotQuotaRejectedError(SourceCacheQuotaExceededError):
    """Quota still rejects a node-output publication after automatic eviction.

    The completed staged artifact is not deleted: :attr:`artifact` now belongs
    to the caller, which reads it for the rest of its request and closes it.
    """

    def __init__(self, message: str, artifact: NodeSnapshotArtifact) -> None:
        super().__init__(message)
        self.artifact = artifact


def snapshot_write_class(
    profile: ExecutionProfile | str,
    *,
    preview_admitted: bool = False,
) -> str | None:
    """Return the semantics class a profile writes, or ``None`` when it never writes."""
    checked = ExecutionProfile(profile)
    if checked in _BOUNDED_SNAPSHOT_PROFILES:
        return BOUNDED_SEMANTICS_CLASS
    if (
        checked == ExecutionProfile.PREVIEW_EAGER
        and preview_admitted
        and PREVIEW_SHARES_BOUNDED_SEMANTICS
    ):
        return BOUNDED_SEMANTICS_CLASS
    return None


def snapshot_read_classes(profile: ExecutionProfile | str) -> frozenset[str]:
    """Return the semantics classes a profile may read."""
    checked = ExecutionProfile(profile)
    if checked in _BOUNDED_SNAPSHOT_PROFILES:
        return frozenset({BOUNDED_SEMANTICS_CLASS})
    if checked == ExecutionProfile.PREVIEW_EAGER and PREVIEW_SHARES_BOUNDED_SEMANTICS:
        return frozenset({BOUNDED_SEMANTICS_CLASS})
    return frozenset()


@dataclass(frozen=True, slots=True)
class NodeSnapshotColumns:
    """The column set a generation holds or a reader demands (``None`` means all)."""

    names: frozenset[str] | None

    @classmethod
    def all(cls) -> NodeSnapshotColumns:
        return cls(None)

    @classmethod
    def of(cls, names: object) -> NodeSnapshotColumns:
        if isinstance(names, str):
            raise TypeError("column names must be an iterable of strings, not a string")
        checked = frozenset(names)  # type: ignore[call-overload]
        if any(not isinstance(name, str) or not name for name in checked):
            raise ValueError("column names must be non-empty strings")
        return cls(checked)

    @property
    def is_all(self) -> bool:
        return self.names is None

    def covers(self, demand: NodeSnapshotColumns) -> bool:
        if self.names is None:
            return True
        if demand.names is None:
            return False
        return demand.names <= self.names

    def strictly_contains(self, other: NodeSnapshotColumns) -> bool:
        return self.covers(other) and not other.covers(self)

    def union(self, other: NodeSnapshotColumns) -> NodeSnapshotColumns:
        if self.names is None or other.names is None:
            return NodeSnapshotColumns.all()
        return NodeSnapshotColumns(self.names | other.names)

    def to_json(self) -> str | list[str]:
        return "all" if self.names is None else sorted(self.names)

    @classmethod
    def from_json(cls, value: object) -> NodeSnapshotColumns:
        if value == "all":
            return cls.all()
        if not isinstance(value, list):
            raise ValueError("node-output column set must be 'all' or a list")
        if len(set(value)) != len(value):
            raise ValueError("node-output column set must not repeat columns")
        return cls.of(value)


@dataclass(frozen=True, slots=True)
class NodeSnapshotSlot:
    """Consumer-independent location of one node's output data."""

    pipeline_source_file: str
    node_id: str
    source: str
    semantics_class: str

    def __post_init__(self) -> None:
        for name in ("pipeline_source_file", "node_id", "source", "semantics_class"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"node snapshot slot {name} must be a non-empty string")

    @property
    def descriptor(self) -> dict[str, object]:
        return {
            "pipeline_source_file": self.pipeline_source_file,
            "node_id": self.node_id,
            "source": self.source,
            "semantics_class": self.semantics_class,
        }

    @property
    def digest(self) -> str:
        payload = {"schema_version": _SLOT_INDEX_SCHEMA_VERSION, **self.descriptor}
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def identity(self, signature: str) -> SourceCacheIdentity:
        if not isinstance(signature, str) or not signature:
            raise ValueError("node snapshot signature must be a non-empty string")
        return SourceCacheIdentity(
            provider=NODE_OUTPUT_PROVIDER,
            # The descriptor key avoids "signature", which identity redaction
            # treats as credential material.
            descriptor={**self.descriptor, "data_fingerprint": signature},
        )

    @classmethod
    def from_identity(cls, identity: SourceCacheIdentity) -> tuple[NodeSnapshotSlot, str]:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            raise ValueError("identity is not a node-output snapshot identity")
        descriptor = dict(identity.descriptor)
        signature = descriptor.pop("data_fingerprint", None)
        if set(descriptor) != {"pipeline_source_file", "node_id", "source", "semantics_class"}:
            raise ValueError("node-output identity descriptor has an unexpected shape")
        if not isinstance(signature, str) or not signature:
            raise ValueError("node-output identity has no signature")
        slot = cls(**{key: str(value) for key, value in descriptor.items()})
        return slot, signature


def node_snapshot_signature(
    graph: PipelineGraph,
    node_id: str,
    *,
    source: str,
    semantics_class: str,
    enforce_contracts: bool,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Return the checked data signature of *node_id*'s output.

    It never contains snapshot generations or column sets: generations are
    recorded as dependencies and column sets widen a generation.
    """
    from haute._builders import resolve_instance_nodes
    from haute._dataframe_execution_cache import _upstream_subgraph
    from haute.execution import (
        canonical_dataframe_execution_graph,
        dataframe_graph_input_fingerprint,
    )

    if type(enforce_contracts) is not bool:
        raise TypeError("enforce_contracts must be a bool")
    # An instance node executes its original's config, so its signature does too.
    canonical = canonical_dataframe_execution_graph(resolve_instance_nodes(graph))
    lineage = _upstream_subgraph(canonical, node_id)
    inputs = checked_cache_inputs(
        CacheConsumer.NODE_SNAPSHOT_SIGNATURE,
        {
            "lineage_fingerprint": graph_fingerprint(lineage, memo=memo),
            "runtime_input_fingerprint": dataframe_graph_input_fingerprint(
                canonical,
                target_node_id=node_id,
                source=source,
                memo=memo,
            ),
            "source": source,
            "semantics_class": semantics_class,
            "enforce_contracts": enforce_contracts,
            "preamble_supplied": bool(canonical.preamble),
            "execution_semantics_version": NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION,
        },
    )
    digest = hashlib.sha256(inputs.canonical_bytes).hexdigest()
    return f"node-snapshot:v{inputs.contract.version}:{digest}"


def pipeline_source_file_key(graph: PipelineGraph) -> str:
    """Return the resolved pipeline file that scopes a graph's snapshot slots."""
    from haute._path_resolution import _infer_project_root

    project_root = _infer_project_root(project_root=None, source_file=graph.source_file)
    source_file = Path(graph.source_file or "")
    if not source_file.is_absolute():
        source_file = project_root / source_file
    return str(source_file.resolve())


def parse_unshaped_columns(raw: object) -> tuple[tuple[str, str], ...] | None:
    """A generation's recorded pre-shaping columns, or None when it recorded none."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("unshaped columns must be a list")
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(part, str) for part in item)
        ):
            raise ValueError("unshaped columns must be [name, dtype] pairs")
        pairs.append((item[0], item[1]))
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class NodeSnapshotGeneration:
    """One validated node-output generation and its freshness facts."""

    identity: SourceCacheIdentity
    generation: SourceCacheGeneration
    columns: NodeSnapshotColumns
    dependencies: Mapping[str, str]
    fresh: bool
    retention: Retention
    last_used: float
    # The node's columns before its own selection and renames, as
    # ``(name, dtype)`` — recorded for a node that shapes its output, so a
    # preview seeded from this generation can still report them.
    unshaped_columns: tuple[tuple[str, str], ...] | None = None

    @property
    def generation_id(self) -> str:
        return self.generation.generation_id

    @property
    def lazy_frame(self) -> pl.LazyFrame:
        return self.generation.lazy_frame


@dataclass(frozen=True, slots=True)
class NodeSnapshotSlotStatus:
    state: SlotState
    identity: SourceCacheIdentity
    generation: NodeSnapshotGeneration | None = None


class NodeSnapshotArtifact:
    """A completed node-output artifact owned by the request that produced it."""

    def __init__(self, identity: SourceCacheIdentity, directory: Path) -> None:
        self.identity = identity
        self.directory = directory
        self._released = False
        self._digests: dict[str, str] = {}
        # Staging is allocated immediately before the write, so this to
        # publication is how long caching this node actually took.
        self.started_at = time.monotonic()

    def record_digests(self, digests: Mapping[str, str]) -> None:
        self._digests = dict(digests)

    @property
    def digests(self) -> Mapping[str, str]:
        return MappingProxyType(self._digests)

    def part_path(self, index: int) -> Path:
        """Where the artifact's ``index``-th part file is written."""
        return self.directory / part_name(index)

    def parts(self) -> list[Path]:
        """The part files written into the artifact, in row order."""
        return part_paths(self.directory)

    def lazy_frame(self) -> pl.LazyFrame:
        if self._released:
            raise RuntimeError("node-output artifact is no longer owned by this request")
        return scan_parts(self.parts())

    def _mark_published(self) -> None:
        self._released = True

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        shutil.rmtree(self.directory, ignore_errors=True)
        if self.directory.exists():
            logger.warning(
                "node_snapshot_artifact_removal_failed",
                path=str(self.directory),
                identity_digest=self.identity.digest,
            )

    def __enter__(self) -> NodeSnapshotArtifact:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NodeSnapshotPublication:
    """The owned outcome of one publication attempt.

    ``published``: the writer holds a lease on the generation it published.
    ``superseded``: the writer keeps its own artifact as a request-owned file.
    Either way the writer continues from its own data and closes this handle
    when its request ends.
    """

    def __init__(
        self,
        outcome: PublicationOutcome,
        *,
        store: NodeSnapshotStore,
        identity: SourceCacheIdentity,
        generation: NodeSnapshotGeneration | None = None,
        artifact: NodeSnapshotArtifact | None = None,
    ) -> None:
        if (outcome == "published") != (generation is not None):
            raise ValueError("a published outcome carries exactly its generation")
        if (outcome == "superseded") != (artifact is not None):
            raise ValueError("a superseded outcome carries exactly its artifact")
        self.outcome = outcome
        self.identity = identity
        self.generation = generation
        self.artifact = artifact
        self._store = store
        self._closed = False

    @property
    def lazy_frame(self) -> pl.LazyFrame:
        if self.generation is not None:
            return self.generation.lazy_frame
        assert self.artifact is not None
        return self.artifact.lazy_frame()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.generation is not None:
            self._store._release_node_lease(self.identity, self.generation.generation_id)
        elif self.artifact is not None:
            self.artifact.close()

    def __enter__(self) -> NodeSnapshotPublication:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _is_identity_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class NodeSnapshotStore(SourceCacheStore):
    """The shared snapshot store, including node-output snapshots."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int | None = None,
        max_generations: int | None = None,
        retire_grace_seconds: float | None = None,
        node_output_max_bytes: int | None = None,
        node_output_max_generations: int | None = None,
    ) -> None:
        super().__init__(
            root,
            max_bytes=max_bytes,
            max_generations=max_generations,
            retire_grace_seconds=retire_grace_seconds,
        )
        if node_output_max_bytes is None:
            node_output_max_bytes = int_env(
                NODE_SNAPSHOT_MAX_BYTES_VARIABLE,
                DEFAULT_NODE_SNAPSHOT_MAX_BYTES,
            )
        if (
            isinstance(node_output_max_bytes, bool)
            or not isinstance(node_output_max_bytes, int)
            or node_output_max_bytes <= 0
        ):
            raise ValueError("source-cache node_output_max_bytes must be a positive integer")
        self.node_output_max_bytes = node_output_max_bytes

        if node_output_max_generations is None:
            node_output_max_generations = int_env(
                NODE_SNAPSHOT_MAX_GENERATIONS_VARIABLE,
                DEFAULT_NODE_SNAPSHOT_MAX_GENERATIONS,
            )
        if (
            isinstance(node_output_max_generations, bool)
            or not isinstance(node_output_max_generations, int)
            or node_output_max_generations <= 0
        ):
            raise ValueError("source-cache node_output_max_generations must be a positive integer")
        self.node_output_max_generations = node_output_max_generations
        self._slots_dir = self.inputs_root / ".node-slots"
        with self._coordination.guard:
            already_cleaned = self._coordination.retired_cleaned
            self._coordination.retired_cleaned = True
        if not already_cleaned:
            self._cleanup_retired()

    # ------------------------------------------------------------------ usage

    def usage_report(self) -> CacheUsageReport:
        """Report both budgets' usage against their limits.

        One walk per budget over the identity directories — the same walk, and
        the same cost, an admission pays. It is deliberately not cheap enough
        to poll: a caller asks for it when a user asks to see it. An identity
        whose marker does not classify counts against both budgets here exactly
        as it does at admission, so what this reports is what would refuse the
        next capture, not a second opinion about it.
        """
        node_generations, node_bytes = self._bucket_usage(NODE_OUTPUT_PROVIDER)
        input_generations, input_bytes = self._bucket_usage("input")
        return CacheUsageReport(
            node_outputs=CacheBudgetUsage(
                generations_used=node_generations,
                generations_limit=self.node_output_max_generations,
                generations_limit_variable=NODE_SNAPSHOT_MAX_GENERATIONS_VARIABLE,
                bytes_used=node_bytes,
                bytes_limit=self.node_output_max_bytes,
                bytes_limit_variable=NODE_SNAPSHOT_MAX_BYTES_VARIABLE,
            ),
            input_snapshots=CacheBudgetUsage(
                generations_used=input_generations,
                generations_limit=self.max_generations,
                generations_limit_variable=INPUT_CACHE_MAX_GENERATIONS_VARIABLE,
                bytes_used=input_bytes,
                bytes_limit=self.max_bytes,
                bytes_limit_variable=INPUT_CACHE_MAX_BYTES_VARIABLE,
            ),
        )

    def clear_identity(self, identity_digest: str) -> int:
        """Clear one identity by its digest, returning the bytes it held.

        The report names a row's identities, and this is what acting on that
        row means. The identity itself is reconstructed from a generation's own
        metadata, because a digest is a hash and cannot be inverted — so an
        identity holding no readable generation cannot be cleared this way and
        reports zero rather than guessing at what it was.

        Like every clear, a generation a reader still holds retires when that
        reader releases it rather than being deleted underneath the scan.
        """
        identity_dir = self.inputs_root / identity_digest
        if not _is_identity_digest(identity_digest) or not identity_dir.is_dir():
            return 0
        held = 0
        identity: SourceCacheIdentity | None = None
        try:
            generation_dirs = tuple((identity_dir / "generations").iterdir())
        except FileNotFoundError:
            generation_dirs = ()
        for generation_dir in generation_dirs:
            if not generation_dir.is_dir() or generation_dir.is_symlink():
                continue
            held += generation_bytes(generation_dir)
            if identity is None:
                facts = _read_generation_facts(generation_dir)
                if facts is not None:
                    identity = SourceCacheIdentity(
                        provider=facts.provider,
                        descriptor=dict(facts.descriptor),
                        schema_version=facts.schema_version,
                    )
        if identity is None or identity.digest != identity_digest:
            return 0
        held += _staging_bytes(identity_dir)
        self.clear(identity)
        return held

    def inventory(self) -> CacheInventory:
        """Attribute every generation on disk to the owner its metadata names.

        A generation's ``meta.json`` records the whole identity payload, so a
        node output names its node and source and an input snapshot names its
        provider and descriptor — no graph is needed to say whose data this is,
        which is what lets the report name a node that no longer exists.

        Like :meth:`usage_report` this takes no lock and mutates nothing, and
        it is one pass over the identity directories plus one small read per
        generation. It is for an explicit request, not a poll.
        """
        owners: dict[tuple[str, str, str], _OwnerAccumulator] = {}
        unattributed_generations = 0
        unattributed_bytes = 0
        unmarked_identities = 0
        try:
            entries = tuple(self.inputs_root.iterdir())
        except FileNotFoundError:
            return CacheInventory((), 0, 0, 0)

        for identity_dir in entries:
            if (
                not identity_dir.is_dir()
                or identity_dir.is_symlink()
                # `.locks`, `.processes` and `.node-slots` are the store's own
                # bookkeeping, not identities, and hold no generations.
                or identity_dir.name.startswith(".")
            ):
                continue

            identity_owner: _OwnerAccumulator | None = None
            identity_generations = 0
            try:
                generation_dirs = tuple((identity_dir / "generations").iterdir())
            except FileNotFoundError:
                generation_dirs = ()
            for generation_dir in generation_dirs:
                if not generation_dir.is_dir() or generation_dir.is_symlink():
                    continue
                facts = _read_generation_facts(generation_dir)
                if facts is None:
                    # A generation with no readable metadata still occupies the
                    # disk the budget counts, so it is reported, not skipped.
                    if (size := generation_bytes(generation_dir)) or (
                        generation_dir / "meta.json"
                    ).exists():
                        unattributed_generations += 1
                        unattributed_bytes += size
                    continue
                identity_generations += 1
                key, accumulator = _owner_for(facts, identity_dir.name)
                owner = owners.setdefault(key, accumulator)
                owner.identity_digests.add(identity_dir.name)
                owner.add(
                    size_bytes=generation_bytes(generation_dir),
                    row_count=facts.row_count,
                    created_at=facts.created_at,
                    build_seconds=facts.build_seconds,
                )
                identity_owner = owner

            if identity_generations and classify_identity_marker(identity_dir) == "unknown":
                unmarked_identities += 1

            staging_bytes = _staging_bytes(identity_dir)
            if staging_bytes:
                # Staging counts against the budget while it is being written.
                if identity_owner is not None:
                    identity_owner.size_bytes += staging_bytes
                else:
                    unattributed_bytes += staging_bytes

        ordered = sorted(
            (owner.frozen() for owner in owners.values()),
            key=lambda owner: (-owner.size_bytes, owner.label),
        )
        return CacheInventory(
            owners=tuple(ordered),
            unattributed_generations=unattributed_generations,
            unattributed_bytes=unattributed_bytes,
            unmarked_identities=unmarked_identities,
        )

    # ------------------------------------------------------------------ paths

    def _publication_lock(self, identity: SourceCacheIdentity) -> _StoreFileLock:
        coordination = self._coordination
        with coordination.guard:
            lock = coordination.publication_locks.get(identity.digest)
            if lock is None:
                lock = _StoreFileLock(self._locks_dir / f"publication-{identity.digest}.lock")
                coordination.publication_locks[identity.digest] = lock
            return lock

    def _generation_dir(self, identity_digest: str, generation_id: str) -> Path:
        return self.inputs_root / identity_digest / "generations" / generation_id

    def _slot_index_path(self, slot: NodeSnapshotSlot) -> Path:
        return self._slots_dir / f"{slot.digest}.json"

    @staticmethod
    def _require_node_output(identity: SourceCacheIdentity) -> None:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            raise ValueError("identity is not a node-output snapshot identity")

    def _cleanup_retired(self) -> None:
        for retired in self.inputs_root.glob(f"*/{_RETIRED_PREFIX}*"):
            if retired.is_dir() and not retired.is_symlink():
                shutil.rmtree(retired, ignore_errors=True)

    # ------------------------------------------------------------- pointers

    def _current_generation_id(self, identity_digest: str) -> str | None:
        path = self.inputs_root / identity_digest / "current.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            pointer = json.loads(raw)
            generation_id = _validate_generation_id(pointer["generation_id"])
            if pointer.get("identity_digest") != identity_digest:
                raise ValueError("invalid current pointer")
            return generation_id
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceCacheCorruptError("source-cache current pointer is corrupt") from exc

    def _write_pointer_locked(self, identity: SourceCacheIdentity, generation_id: str) -> None:
        atomic_write_text(
            self._pointer_path(identity),
            canonical_json({"identity_digest": identity.digest, "generation_id": generation_id}),
        )

    # ----------------------------------------------------------- slot index

    def _read_slot_index(self, slot: NodeSnapshotSlot) -> dict[str, Any]:
        path = self._slot_index_path(slot)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"identities": {}, "pinned_identity": None}
        except ValueError as exc:
            raise SourceCacheCorruptError("node snapshot slot index is corrupt") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != _SLOT_INDEX_SCHEMA_VERSION
            or raw.get("slot_digest") != slot.digest
            or raw.get("slot") != slot.descriptor
            or not isinstance(raw.get("identities"), dict)
            or not (raw.get("pinned_identity") is None or isinstance(raw["pinned_identity"], str))
        ):
            raise SourceCacheCorruptError("node snapshot slot index is corrupt")
        return {"identities": dict(raw["identities"]), "pinned_identity": raw["pinned_identity"]}

    def _write_slot_index_locked(self, slot: NodeSnapshotSlot, index: Mapping[str, Any]) -> None:
        path = self._slot_index_path(slot)
        identities = dict(index["identities"])
        if not identities:
            path.unlink(missing_ok=True)
            return
        pinned = index["pinned_identity"]
        if pinned not in identities:
            pinned = None
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            canonical_json(
                {
                    "schema_version": _SLOT_INDEX_SCHEMA_VERSION,
                    "slot_digest": slot.digest,
                    "slot": slot.descriptor,
                    "identities": identities,
                    "pinned_identity": pinned,
                }
            ),
        )

    def _prune_identity_locked(self, identity: SourceCacheIdentity) -> None:
        """Drop *identity* from its slot index once it no longer has a current generation."""
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        index = self._read_slot_index(slot)
        if identity.digest in index["identities"]:
            del index["identities"][identity.digest]
            self._write_slot_index_locked(slot, index)

    # ------------------------------------------------------------ metadata

    def _node_output_facts(
        self, identity: SourceCacheIdentity, generation: SourceCacheGeneration
    ) -> tuple[NodeSnapshotColumns, dict[str, str], tuple[tuple[str, str], ...] | None]:
        facts = generation.metadata.node_output
        slot, signature = NodeSnapshotSlot.from_identity(identity)
        try:
            if (
                not isinstance(facts, Mapping)
                or facts.get("metadata_version") != NODE_SNAPSHOT_METADATA_VERSION
                or facts.get("slot") != slot.descriptor
                or facts.get("slot_digest") != slot.digest
                or facts.get("signature") != signature
            ):
                raise ValueError("node-output metadata does not match its identity")
            columns = NodeSnapshotColumns.from_json(facts.get("column_set"))
            if columns.names is not None and not columns.names <= set(generation.metadata.columns):
                raise ValueError("node-output column set names columns the data does not hold")
            raw_dependencies = facts.get("dependencies")
            if not isinstance(raw_dependencies, Mapping):
                raise ValueError("node-output dependencies must be a mapping")
            dependencies: dict[str, str] = {}
            for digest, generation_id in raw_dependencies.items():
                if not isinstance(digest, str) or not _is_identity_digest(digest):
                    raise ValueError("node-output dependency identity is invalid")
                dependencies[digest] = _validate_generation_id(generation_id)
            if identity.digest in dependencies:
                raise ValueError("node-output generation depends on its own identity")
            unshaped = parse_unshaped_columns(facts.get("unshaped_columns"))
        except (TypeError, ValueError) as exc:
            raise SourceCacheCorruptError("node-output generation metadata is corrupt") from exc
        return columns, dependencies, unshaped

    def _dependencies_current_or_cleared(self, dependencies: Mapping[str, str]) -> bool:
        for identity_digest, generation_id in dependencies.items():
            current = self._current_generation_id(identity_digest)
            if current is not None and current != generation_id:
                return False
        return True

    @staticmethod
    def _last_used(generation_dir: Path, fallback: float) -> float:
        """A generation's last-used time is its metadata file's modification time."""
        try:
            return (generation_dir / "meta.json").stat().st_mtime
        except OSError:
            return fallback

    def _touch_last_used(self, generation: SourceCacheGeneration) -> None:
        now = time.time()
        previous = self._last_used(generation.directory, 0.0)
        if now - previous < _LAST_USED_UPDATE_INTERVAL_SECONDS:
            return
        try:
            # An atomic filesystem-metadata write: no content change, no temporary file.
            os.utime(generation.metadata_path, (now, now))
        except OSError as exc:
            logger.warning(
                "node_snapshot_last_used_update_failed",
                generation_id=generation.generation_id,
                error=str(exc),
            )

    def _describe(
        self,
        identity: SourceCacheIdentity,
        generation: SourceCacheGeneration,
        *,
        pinned_identity: str | None,
    ) -> NodeSnapshotGeneration:
        columns, dependencies, unshaped = self._node_output_facts(identity, generation)
        current = self._current_generation_id(identity.digest)
        retention: Retention = (
            "pinned"
            if pinned_identity is not None and current == generation.generation_id
            else "automatic"
        )
        return NodeSnapshotGeneration(
            identity=identity,
            generation=generation,
            columns=columns,
            dependencies=dependencies,
            fresh=self._dependencies_current_or_cleared(dependencies),
            retention=retention,
            last_used=self._last_used(generation.directory, generation.metadata.created_at),
            unshaped_columns=unshaped,
        )

    # ------------------------------------------------------------ reading

    def latest_generation(self, identity: SourceCacheIdentity) -> NodeSnapshotGeneration | None:
        """Return the identity's latest generation, fresh or stale, without leasing it."""
        self._require_node_output(identity)
        generation_id = self._current_generation_id(identity.digest)
        if generation_id is None:
            return None
        try:
            generation = self._metadata_from_path(identity, generation_id)
        except SourceCacheLegacyLayoutError:
            # The retired single-file layout reads as absent, so it is rebuilt.
            return None
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        pinned = self._read_slot_index(slot)["pinned_identity"]
        return self._describe(identity, generation, pinned_identity=pinned)

    def describe_generation(
        self, identity: SourceCacheIdentity, generation: SourceCacheGeneration
    ) -> NodeSnapshotGeneration:
        self._require_node_output(identity)
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        pinned = self._read_slot_index(slot)["pinned_identity"]
        return self._describe(identity, generation, pinned_identity=pinned)

    def slot_status(self, slot: NodeSnapshotSlot, signature: str) -> NodeSnapshotSlotStatus:
        """Report ``current``, ``stale``, ``missing``, or ``corrupt`` for one signature."""
        identity = slot.identity(signature)
        try:
            latest = self.latest_generation(identity)
        except SourceCacheCorruptError:
            return NodeSnapshotSlotStatus("corrupt", identity)
        if latest is not None:
            return NodeSnapshotSlotStatus("current" if latest.fresh else "stale", identity, latest)
        index = self._read_slot_index(slot)
        for digest in index["identities"]:
            if digest != identity.digest and self._current_generation_id(digest) is not None:
                return NodeSnapshotSlotStatus("stale", identity)
        return NodeSnapshotSlotStatus("missing", identity)

    # ------------------------------------------------------------ leasing

    def _acquire_node_lease(
        self,
        identity: SourceCacheIdentity,
        generation_id: str,
        *,
        require_current: bool,
    ) -> bool:
        """Record one lease; return False when *require_current* and the pointer moved.

        The marker exists before the generation is validated, so a concurrent
        retirement can never remove files from under that validation.
        """
        _validate_generation_id(generation_id)
        with self._coordination.lease_lock:
            _fault_point("lease_before_marker")
            generation_dir = self._generation_dir(identity.digest, generation_id)
            if not generation_dir.is_dir():
                self._forget_verified(identity.digest, generation_id)
                raise SourceCacheGenerationMissingError("source-cache generation does not exist")
            if require_current and self._current_generation_id(identity.digest) != generation_id:
                return False
            key = (identity.digest, generation_id)
            with self._lock:
                previous = self._leases.get(key, 0)
                self._leases[key] = previous + 1
            if previous == 0:
                try:
                    (generation_dir / f"{_LEASE_PREFIX}{self._own_token()}").touch()
                except BaseException:
                    with self._lock:
                        self._leases[key] -= 1
                        if self._leases[key] == 0:
                            del self._leases[key]
                    raise
            return True

    def _release_node_lease(self, identity: SourceCacheIdentity, generation_id: str) -> None:
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                key = (identity.digest, generation_id)
                with self._lock:
                    remaining = self._leases[key] - 1
                    if remaining:
                        self._leases[key] = remaining
                    else:
                        del self._leases[key]
                if remaining:
                    return
                generation_dir = self._generation_dir(identity.digest, generation_id)
                token = self._coordination.token
                if token is not None:
                    (generation_dir / f"{_LEASE_PREFIX}{token}").unlink(missing_ok=True)
                if (
                    generation_dir.is_dir()
                    and self._current_generation_id(identity.digest) != generation_id
                    and not self._has_live_holders_locked(generation_dir)
                ):
                    retired_path = self._retire_generation_locked(identity.digest, generation_id)
                    if retired_path is not None:
                        retired.append(retired_path)
                    if not self._generations_remaining_locked(identity.digest):
                        # This was the last generation of a signature a
                        # publication superseded and left indexed while we held
                        # it. Nothing will ever select it again, so drop it from
                        # the slot index now that it holds nothing.
                        self._prune_identity_locked(identity)
        finally:
            self._delete_retired(retired)

    @contextlib.contextmanager
    def lease(self, identity: SourceCacheIdentity) -> Iterator[SourceCacheGeneration]:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            with super().lease(identity) as generation:
                yield generation
            return
        while True:
            generation_id = self._current_generation_id(identity.digest)
            if generation_id is None:
                raise FileNotFoundError(self._pointer_path(identity))
            try:
                if self._acquire_node_lease(identity, generation_id, require_current=True):
                    break
            except SourceCacheGenerationMissingError:
                # Superseded and retired between reading the pointer and
                # leasing: select again, unless the pointer itself is dangling.
                if self._current_generation_id(identity.digest) == generation_id:
                    raise
        try:
            try:
                generation = self._metadata_from_path(identity, generation_id)
            except SourceCacheLegacyLayoutError as exc:
                raise FileNotFoundError(self._pointer_path(identity)) from exc
            self._touch_last_used(generation)
            yield generation
        finally:
            self._release_node_lease(identity, generation_id)

    @contextlib.contextmanager
    def lease_generation(
        self, identity: SourceCacheIdentity, generation_id: str
    ) -> Iterator[SourceCacheGeneration]:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            with super().lease_generation(identity, generation_id) as generation:
                yield generation
            return
        self._acquire_node_lease(identity, generation_id, require_current=False)
        try:
            generation = self._metadata_from_path(identity, generation_id)
            self._touch_last_used(generation)
            yield generation
        finally:
            self._release_node_lease(identity, generation_id)

    # --------------------------------------------------------- retirement

    def _retire_generation_locked(self, identity_digest: str, generation_id: str) -> Path | None:
        """Rename one generation out of selection. Caller holds the lease lock."""
        generation_dir = self._generation_dir(identity_digest, generation_id)
        retired = generation_dir.parent.parent / f"{_RETIRED_PREFIX}{uuid.uuid4().hex}"
        try:
            generation_dir.rename(retired)
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            # A Windows handle still open inside the generation blocks the
            # rename; the generation stays selectable-by-id and is retried later.
            logger.warning(
                "node_snapshot_retirement_deferred",
                identity_digest=identity_digest,
                generation_id=generation_id,
                error=str(exc),
            )
            return None
        self._forget_verified(identity_digest, generation_id)
        return retired

    @staticmethod
    def _delete_retired(paths: list[Path]) -> None:
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                logger.warning("node_snapshot_retired_delete_failed", path=str(path))

    def retire_unleased(self, identity: SourceCacheIdentity) -> None:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            super().retire_unleased(identity)
            return
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                retired.extend(self._retire_non_current_locked(identity.digest))
        finally:
            self._delete_retired(retired)

    def _retire_non_current_locked(self, identity_digest: str) -> list[Path]:
        generations_dir = self.inputs_root / identity_digest / "generations"
        try:
            candidates = tuple(generations_dir.iterdir())
        except FileNotFoundError:
            return []
        current = self._current_generation_id(identity_digest)
        retired: list[Path] = []
        for candidate in candidates:
            if not candidate.is_dir() or candidate.name == current:
                continue
            if self._has_live_holders_locked(candidate):
                continue
            path = self._retire_generation_locked(identity_digest, candidate.name)
            if path is not None:
                retired.append(path)
        return retired

    def clear(self, identity: SourceCacheIdentity) -> None:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            super().clear(identity)
            return
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                self._pointer_path(identity).unlink(missing_ok=True)
                self._prune_identity_locked(identity)
                retired.extend(self._retire_non_current_locked(identity.digest))
        finally:
            self._delete_retired(retired)

    def _generations_remaining_locked(self, identity_digest: str) -> bool:
        """Whether *identity_digest* still holds a generation. Caller holds the lease lock."""
        try:
            return any(
                child.is_dir() and not child.is_symlink()
                for child in (self.inputs_root / identity_digest / "generations").iterdir()
            )
        except FileNotFoundError:
            return False

    def _retire_superseded_signatures_locked(
        self, index: dict[str, Any], identity: SourceCacheIdentity
    ) -> list[Path]:
        """Retire every other signature of the slot *identity* was just published to.

        A node holds one dataset per slot: publishing replaces what was there
        rather than adding to it, so a node whose inputs or configuration change
        never costs a second full copy of its output. Mutates *index*, which the
        caller writes. Caller holds the lease lock.

        The mechanics are `clear_slot`'s: drop the pointer so nothing selects the
        signature again, then retire its generations. A generation another reader
        still holds is left alone and retires when that reader releases it —
        `_release_node_lease` retires a generation whose pointer has gone — so a
        scan in flight never has its files deleted underneath it. A scan that
        outlives its lease was always a contract violation; under this policy it
        costs the scan its files at the next re-cache rather than at the next
        eviction, so the `with` discipline at every call site matters more.

        A signature whose generation survived stays *indexed and unpointed*.
        Dropping it from the index would strand that generation where nothing
        can find it: `clear_slot` walks the index, the retired sweep only knows
        `.retired-*`, and the holder may die without ever releasing — leaving a
        node showing two datasets and a Clear that cannot remove one, which is
        the very promise this policy exists to keep. Indexed and unpointed, the
        next publish and any `clear_slot` retry it.
        """
        retired: list[Path] = []
        for digest in [d for d in index["identities"] if d != identity.digest]:
            (self.inputs_root / digest / "current.json").unlink(missing_ok=True)
            retired.extend(self._retire_non_current_locked(digest))
            if self._generations_remaining_locked(digest):
                continue
            del index["identities"][digest]
        return retired

    def clear_slot(self, slot: NodeSnapshotSlot) -> None:
        """Remove every identity of *slot* and its pin; leased generations retire on release."""
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                index = self._read_slot_index(slot)
                for identity_digest in index["identities"]:
                    (self.inputs_root / identity_digest / "current.json").unlink(missing_ok=True)
                    retired.extend(self._retire_non_current_locked(identity_digest))
                self._slot_index_path(slot).unlink(missing_ok=True)
        finally:
            self._delete_retired(retired)

    def pin(self, identity: SourceCacheIdentity) -> None:
        """Pin the slot to *identity*, which must hold a current generation."""
        self._require_node_output(identity)
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        with self._coordination.lease_lock:
            if self._current_generation_id(identity.digest) is None:
                raise SourceCacheGenerationMissingError(
                    "cannot pin an identity without a generation"
                )
            index = self._read_slot_index(slot)
            index["identities"][identity.digest] = dict(identity.descriptor)
            index["pinned_identity"] = identity.digest
            self._write_slot_index_locked(slot, index)

    # --------------------------------------------------------- publication

    def stage_node_output(
        self, identity: SourceCacheIdentity, *, staging_token: str | None = None
    ) -> NodeSnapshotArtifact:
        """Allocate a request-owned staging directory to sink a node output into.

        A supervising parent passes its own ``staging_token`` so it can discard
        exactly this staging directory if the worker is killed before cleanup.
        """
        self._require_node_output(identity)
        token = (
            _validate_staging_token(staging_token)
            if staging_token is not None
            else new_staging_token()
        )
        identity_dir = self.identity_path(identity)
        identity_dir.mkdir(parents=True, exist_ok=True)
        _ensure_identity_marker(identity_dir, identity.provider)
        staging = identity_dir / f".staging-{token}"
        ensure_disk_headroom(identity_dir)
        staging.mkdir()
        return NodeSnapshotArtifact(identity, staging)

    def discard_node_output_staging(self, staging_token: str) -> None:
        """Remove a terminated worker's staging directory named by its parent's token."""
        token = _validate_staging_token(staging_token)
        for staging in self.inputs_root.glob(f"*/.staging-{token}"):
            if staging.is_dir() and not staging.is_symlink() and not remove_tree(staging):
                logger.warning("node_snapshot_staging_discard_failed", path=str(staging))

    def _staged_metadata(
        self,
        identity: SourceCacheIdentity,
        artifact: NodeSnapshotArtifact,
        *,
        columns: NodeSnapshotColumns,
        dependencies: Mapping[str, str],
        profile: ExecutionProfile | str,
        unshaped_columns: Sequence[tuple[str, str]] | None = None,
    ) -> SourceCacheMetadata:
        slot, signature = NodeSnapshotSlot.from_identity(identity)
        parts = describe_parts(artifact.directory, digests=artifact.digests)
        schema = scan_parts(artifact.parts()).collect_schema()
        schema_columns = {name: str(dtype) for name, dtype in schema.items()}
        if columns.names is not None and not columns.names <= set(schema_columns):
            missing = sorted(columns.names - set(schema_columns))
            raise ValueError(f"node-output artifact lacks declared columns {missing}")
        checked_dependencies = {
            str(digest): _validate_generation_id(generation_id)
            for digest, generation_id in dependencies.items()
        }
        if any(not _is_identity_digest(digest) for digest in checked_dependencies):
            raise ValueError("node-output dependencies must be keyed by identity digest")
        if identity.digest in checked_dependencies:
            raise ValueError("a node-output generation cannot depend on its own identity")
        return SourceCacheMetadata(
            identity_digest=identity.digest,
            identity=identity.payload,
            schema_version=identity.schema_version,
            generation_id=str(uuid.uuid4()),
            source_signature=None,
            parts=parts,
            size_bytes=sum(part.size_bytes for part in parts),
            row_count=sum(part.row_count for part in parts),
            column_count=len(schema_columns),
            columns=schema_columns,
            created_at=time.time(),
            profile=ExecutionProfile(profile).value,
            build_class="bounded",
            build_seconds=time.monotonic() - artifact.started_at,
            node_output={
                "metadata_version": NODE_SNAPSHOT_METADATA_VERSION,
                "slot": slot.descriptor,
                "slot_digest": slot.digest,
                "signature": signature,
                "column_set": columns.to_json(),
                "dependencies": dict(sorted(checked_dependencies.items())),
                **(
                    {"unshaped_columns": [[name, dtype] for name, dtype in unshaped_columns]}
                    if unshaped_columns is not None
                    else {}
                ),
            },
        )

    def _should_publish_locked(
        self,
        identity: SourceCacheIdentity,
        *,
        columns: NodeSnapshotColumns,
        dependencies: Mapping[str, str],
        explicit: bool,
        refresh: bool,
    ) -> bool:
        """Apply the publication rule. Caller holds the identity's publication lock."""
        if not self._dependencies_current_or_cleared(dependencies):
            return False
        try:
            latest = self.latest_generation(identity)
        except SourceCacheCorruptError:
            # Only an explicit build replaces a corrupt generation; an automatic
            # capture surfaces the corruption instead of silently repairing it.
            if explicit:
                return True
            raise
        if latest is None:
            return True
        if not columns.covers(latest.columns):
            return False
        return not latest.fresh or columns.strictly_contains(latest.columns) or refresh

    def publish_node_output(
        self,
        identity: SourceCacheIdentity,
        artifact: NodeSnapshotArtifact,
        *,
        columns: NodeSnapshotColumns,
        dependencies: Mapping[str, str],
        explicit: bool,
        profile: ExecutionProfile | str,
        refresh: bool = False,
        unshaped_columns: Sequence[tuple[str, str]] | None = None,
    ) -> NodeSnapshotPublication:
        """Publish *artifact* under the publication rule and lease what was published.

        ``explicit`` marks an explicit cache build: it pins the slot and may
        replace a corrupt generation; an automatic capture does neither.
        The writer always continues from its own data: on ``superseded`` the
        returned handle owns the artifact; a quota rejection after automatic
        eviction raises :class:`NodeSnapshotQuotaRejectedError` carrying it.
        """
        if refresh and not explicit:
            raise ValueError("only an explicit build can refresh a node-output snapshot")
        self._require_node_output(identity)
        if artifact.identity.digest != identity.digest or artifact.directory.parent != (
            self.identity_path(identity)
        ):
            raise ValueError("node-output artifact was not staged for this identity")
        metadata = self._staged_metadata(
            identity,
            artifact,
            columns=columns,
            dependencies=dependencies,
            profile=profile,
            unshaped_columns=unshaped_columns,
        )
        atomic_write_text(artifact.directory / "meta.json", canonical_json(metadata.to_dict()))
        generation_id = metadata.generation_id

        with self._publication_lock(identity):
            _fault_point("publish_before_recheck")
            if not self._should_publish_locked(
                identity,
                columns=columns,
                dependencies=dependencies,
                explicit=explicit,
                refresh=refresh,
            ):
                logger.info(
                    "node_snapshot_capture_superseded",
                    identity_digest=identity.digest,
                )
                return NodeSnapshotPublication(
                    "superseded", store=self, identity=identity, artifact=artifact
                )
            retired: list[Path] = []
            try:
                with self._coordination.lease_lock:
                    _fault_point("publish_before_admission")
                    superseded_id = self._current_generation_id(identity.digest)
                    retired.extend(
                        self._admit_node_output_locked(
                            identity,
                            artifact,
                            new_size_bytes=metadata.size_bytes,
                            superseded_id=superseded_id,
                        )
                    )
                    final_dir = self._generation_dir(identity.digest, generation_id)
                    slot, _signature = NodeSnapshotSlot.from_identity(identity)
                    index = self._read_slot_index(slot)
                    previous_pin = index["pinned_identity"]
                    index["identities"][identity.digest] = dict(identity.descriptor)
                    if explicit or previous_pin is not None:
                        index["pinned_identity"] = identity.digest
                    # Register the candidate and its retention before exposing
                    # any generation. A crash may leave it indexed/unpointed;
                    # Clear can still find it, and the slot's pin protects the
                    # previous current data until the pointer commits.
                    self._write_slot_index_locked(slot, index)
                    key = (identity.digest, generation_id)
                    try:
                        final_dir.parent.mkdir(parents=True, exist_ok=True)
                        artifact.directory.replace(final_dir)
                        artifact._mark_published()
                        published_stats = tuple(
                            (final_dir / part.name).stat() for part in metadata.parts
                        )
                        with self._lock:
                            self._verified_generations.add(
                                _verification_key(
                                    identity.digest,
                                    generation_id,
                                    metadata.parts,
                                    published_stats,
                                )
                            )
                        # The publisher's lease exists before the pointer names
                        # the generation, so it is never selectable unleased.
                        (final_dir / f"{_LEASE_PREFIX}{self._own_token()}").touch()
                        with self._lock:
                            self._leases[key] = self._leases.get(key, 0) + 1
                        self._write_pointer_locked(identity, generation_id)
                    except BaseException as exc:
                        with self._lock:
                            if self._leases.get(key):
                                del self._leases[key]
                        self._forget_verified(identity.digest, generation_id)
                        shutil.rmtree(final_dir, ignore_errors=True)
                        index["pinned_identity"] = previous_pin
                        try:
                            self._write_slot_index_locked(slot, index)
                        except OSError as rollback_error:
                            # Keep the candidate indexed even if restoring the
                            # pin fails. Retaining too much is safe; stranding
                            # files or weakening an existing pin is not.
                            exc.add_note(f"slot retention rollback failed: {rollback_error}")
                        raise
                    try:
                        retired.extend(self._retire_superseded_signatures_locked(index, identity))
                        self._write_slot_index_locked(slot, index)
                        if superseded_id is not None and superseded_id != generation_id:
                            superseded_dir = self._generation_dir(identity.digest, superseded_id)
                            if superseded_dir.is_dir() and not self._has_live_holders_locked(
                                superseded_dir
                            ):
                                path = self._retire_generation_locked(
                                    identity.digest, superseded_id
                                )
                                if path is not None:
                                    retired.append(path)
                    except BaseException:
                        # Ownership has not reached the caller: release the
                        # publisher's lease before surfacing the failure.
                        self._release_node_lease(identity, generation_id)
                        raise
            finally:
                self._delete_retired(retired)

        try:
            generation = self._metadata_from_path(identity, generation_id)
            described = self.describe_generation(identity, generation)
        except BaseException:
            self._release_node_lease(identity, generation_id)
            raise
        logger.info(
            "node_snapshot_published",
            identity_digest=identity.digest,
            generation_id=generation_id,
            size_bytes=metadata.size_bytes,
            retention=described.retention,
        )
        return NodeSnapshotPublication(
            "published", store=self, identity=identity, generation=described
        )

    def _admit_node_output_locked(
        self,
        identity: SourceCacheIdentity,
        artifact: NodeSnapshotArtifact,
        *,
        new_size_bytes: int,
        superseded_id: str | None,
    ) -> list[Path]:
        """Admit a publication, retiring unleased automatic generations LRU-first.

        Caller holds the lease lock. Returns the retired paths to delete after
        the lock is released; raises :class:`NodeSnapshotQuotaRejectedError`
        without evicting anything when even full eviction cannot make room.
        """
        count, size = self._bucket_usage("node_output", exclude=artifact.directory)
        replacements: set[tuple[str, str]] = set()
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        digests = set(self._read_slot_index(slot)["identities"]) | {identity.digest}
        for digest in digests:
            identity_dir = self.inputs_root / digest
            if classify_identity_marker(identity_dir) not in {"node_output", "unknown"}:
                continue
            try:
                generations = tuple((identity_dir / "generations").iterdir())
            except FileNotFoundError:
                continue
            for generation_dir in generations:
                if digest == identity.digest and generation_dir.name != superseded_id:
                    continue
                if (
                    generation_dir.is_symlink()
                    or not generation_dir.is_dir()
                    or self._has_live_holders_locked(generation_dir)
                ):
                    continue
                # These generations retire only after the new pointer commits.
                # Never evict them early or count their bytes twice.
                replacements.add((digest, generation_dir.name))
                size -= generation_bytes(generation_dir)
                count -= 1
        projected_size = size + new_size_bytes
        projected_count = count + 1
        if (
            projected_size <= self.node_output_max_bytes
            and projected_count <= self.node_output_max_generations
        ):
            return []

        candidates = [
            candidate
            for candidate in self._eviction_candidates_locked(
                exclude=(identity.digest, superseded_id)
            )
            if (candidate[0], candidate[1]) not in replacements
        ]
        reclaimable_size = sum(candidate[2] for candidate in candidates)
        if (
            projected_size - reclaimable_size > self.node_output_max_bytes
            or projected_count - len(candidates) > self.node_output_max_generations
        ):
            raise NodeSnapshotQuotaRejectedError(
                "source-cache quota exceeded: pinned and in-use snapshots are kept until "
                "explicitly refreshed or cleared. Clear an unused snapshot or raise the "
                "cache quota.",
                artifact,
            )
        retired: list[Path] = []
        for identity_digest, generation_id, generation_size, _order in candidates:
            if (
                projected_size <= self.node_output_max_bytes
                and projected_count <= self.node_output_max_generations
            ):
                break
            _fault_point("evict_before_marker_check")
            generation_dir = self._generation_dir(identity_digest, generation_id)
            if self._has_live_holders_locked(generation_dir):
                continue
            was_current = self._current_generation_id(identity_digest) == generation_id
            path = self._retire_generation_locked(identity_digest, generation_id)
            if path is None:
                continue
            if was_current:
                (self.inputs_root / identity_digest / "current.json").unlink(missing_ok=True)
                evicted_identity = self._identity_from_generation_path(path)
                if evicted_identity is not None:
                    self._prune_identity_locked(evicted_identity)
            retired.append(path)
            projected_size -= generation_size
            projected_count -= 1
            logger.info(
                "node_snapshot_evicted",
                identity_digest=identity_digest,
                generation_id=generation_id,
                size_bytes=generation_size,
                was_current=was_current,
            )
        if (
            projected_size > self.node_output_max_bytes
            or projected_count > self.node_output_max_generations
        ):
            # A candidate gained a live holder between selection and retirement.
            self._delete_retired(retired)
            raise NodeSnapshotQuotaRejectedError(
                "source-cache quota exceeded: pinned and in-use snapshots are kept until "
                "explicitly refreshed or cleared. Clear an unused snapshot or raise the "
                "cache quota.",
                artifact,
            )
        return retired

    @staticmethod
    def _identity_from_generation_path(generation_dir: Path) -> SourceCacheIdentity | None:
        try:
            raw = json.loads((generation_dir / "meta.json").read_text(encoding="utf-8"))
            payload = raw["identity"]
            return SourceCacheIdentity(
                provider=payload["provider"],
                descriptor=payload["descriptor"],
                schema_version=payload["schema_version"],
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _eviction_candidates_locked(
        self, *, exclude: tuple[str, str | None]
    ) -> list[tuple[str, str, int, tuple[int, float]]]:
        """Unleased automatic node-output generations, superseded first, then LRU."""
        candidates: list[tuple[str, str, int, tuple[int, float]]] = []
        pinned_by_slot: dict[str, str | None] = {}
        for identity_dir in self.inputs_root.iterdir():
            if not _is_identity_digest(identity_dir.name) or not identity_dir.is_dir():
                continue
            generations_dir = identity_dir / "generations"
            try:
                generation_dirs = tuple(generations_dir.iterdir())
            except FileNotFoundError:
                continue
            current = self._current_generation_id(identity_dir.name)
            for generation_dir in generation_dirs:
                if (identity_dir.name, generation_dir.name) == exclude:
                    continue
                try:
                    raw = json.loads((generation_dir / "meta.json").read_text(encoding="utf-8"))
                    if raw["identity"]["provider"] != NODE_OUTPUT_PROVIDER:
                        break
                    size = generation_bytes(generation_dir)
                    created_at = float(raw["created_at"])
                    slot_digest = raw["node_output"]["slot_digest"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                is_current = generation_dir.name == current
                if is_current:
                    if slot_digest not in pinned_by_slot:
                        pinned_by_slot[slot_digest] = self._pinned_identity_by_slot_digest(
                            slot_digest
                        )
                    if pinned_by_slot[slot_digest] is not None:
                        continue
                if self._in_process_lease_count(identity_dir.name, generation_dir.name):
                    continue
                if self._has_live_holders_locked(generation_dir):
                    continue
                order = (1 if is_current else 0, self._last_used(generation_dir, created_at))
                candidates.append((identity_dir.name, generation_dir.name, size, order))
        candidates.sort(key=lambda candidate: candidate[3])
        return candidates

    def _pinned_identity_by_slot_digest(self, slot_digest: str) -> str | None:
        try:
            raw = json.loads((self._slots_dir / f"{slot_digest}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise SourceCacheCorruptError("node snapshot slot index is corrupt") from exc
        pinned = raw.get("pinned_identity") if isinstance(raw, dict) else None
        return pinned if isinstance(pinned, str) else None
