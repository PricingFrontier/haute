# Caching — High-Level Specification

## Purpose

Caching avoids repeated graph hashing, artifact loading, upstream execution, and JSON
shredding while preserving correctness when configuration or backing files change.
Cache identities are explicit, versioned contracts rather than ad-hoc object hashes.

## Scope

In scope are canonical JSON and checked cache-input contracts, graph and lineage keys,
bounded in-process LRU and freshness-gated caches and the one source-freshness proof they
share, the shared node-output snapshot layer and its seed plans,
the explicit input-snapshot routes (a Data Input's snapshot, or a structured API
Input's tables), and data points: the mapping from a consumer node to the data it reads
and leased reads of that data.

The [IO layer](../io-layer/high-level.md) primarily owns shared source-cache storage;
caching consumes its identity/generation contract. Execution owns runtime-path
fingerprint call sites. JSON shredding
owns the per-port transformation and each API-input table's identity and build.

## Behaviour

Graph fingerprints and preview/trace lineage keys include every execution-relevant graph,
utility-file, runtime switch, upstream lineage, output port, and algorithm-version input
defined by their checked consumer contract. Presentation-only fields are explicitly
classified and excluded. `lineage_cache_key()` is the common preview/trace factory; callers
do not key directly from `graph_fingerprint()` alone.

Eight maintained consumers — graph structure, graph execution, preview/trace,
runtime graph input, deploy schema, model contract, input snapshot, and
node-snapshot signature — each declare one complete versioned field set.
The node-snapshot signature identifies the data at a pipeline point independently of
any consumer: upstream lineage, runtime inputs, source, execution semantics class,
contract enforcement, preamble presence, and execution semantics version. Every logical
input class is either mapped to named fields or excluded with a rationale;
missing, unknown, or unclassified fields fail before a key is produced. The
low-level inventory is the reviewable source for those exact sets and their
nested record shapes.

A data point is the data at one pipeline location — a producer node and, for an
`apiInput`, a port — independent of the consumer asking for it. A Banding or Rating
Step node reads its single incoming connection's point with a demand of its banded or
raw factor columns; a blank-code Explore node reads its input's point with every
column; any other node reads its own output. A consumer without exactly one incoming
connection is `node_data_point_invalid`. Each point has one kind: a Data Input with
blank post-load code is `data_input`, an `apiInput` port is `api_input_table`, and every
other producer — including a Data Input with post-load code, whose code may sample or
depend on time — is `node_output`. A direct-Parquet Data Input is always `current`; a
snapshot-backed one follows its input snapshot (fresh or unknown freshness is
`current`); an `apiInput` port follows its own table's input snapshot the same way; a
node output follows its node-output slot, and a
fresh generation that lacks demanded columns is `partial`. A point with a running build
that is not current is `building`. Each current point has a data version: the source
file or snapshot generation (and port, for a table), or the node-output generation,
together with the producer's lineage fingerprint for source kinds.
A consumer reads a point only when it is `current` for its demand; otherwise it gets
`cache_required` with the state. An API-input table is read straight from its leased
generation; a Data Input is read by lazily executing that single source node with input
preparation disabled, so selections and renames apply exactly as in a run. A read never
builds a snapshot or shreds JSON. The read holds its lease for the caller's whole operation, including
final collection, and a spawned child reads exactly the generation its parent leased.

`LRUCache` bounds entries, optionally bounds bytes/TTL, and supports pins. Rejecting an
oversized value leaves an existing same-key entry intact. `StatGatedCache` is built on
`LRUCache`: it is bounded by an entry count, gates each entry on its file's freshness token,
provides per-key single flight, and evicts least recently used entries.

**Source freshness is one proof.** Whether a local file changed is answered in one place,
`src/haute/_json_shred/_source_proof.py`, for every consumer: Data Input and API Input
snapshot freshness, preview/trace runtime-input identity, preamble utility hashes, JSON
schema inference, and every `StatGatedCache`. The guarantee: a proof or loaded value is
reused only while the file's freshness token is unchanged. The token is the file's native
revision (Windows volume, file id and USN; POSIX device, inode and ctime, with size and
mtime), which every write moves, including a same-size rewrite that restores the mtime.
Where the platform has no native revision the token is the file's stat, trusted only for a
file last modified at least two seconds before it was observed; a younger file is proved again
on every use. A file's content signature (`xxh64:<digest>:<size>`) is hashed once per
unchanged token and shared by every consumer in the process, so one edit costs one hash. The
proof lives in process memory: a fresh process proves each file once.

A structured (JSON, JSONL, NDJSON, XML) API Input's emitting tables are input
snapshots in the same store as Data Input snapshots, one per table: automatic
preparation builds missing or stale tables before an admitted execution, and the
input-cache routes report, build and clear a node's tables together (a build is a
cancellable job in a hard-capped worker). Schema inference is its own route
(`json_cache`), which validates the path before reading and answers a structured 422
for an unexpressible source.

Source snapshot identities use the same canonical checked-input discipline. Their storage,
lease, and publication behaviour is specified by the IO layer. A published
current generation is durable until the user refreshes or clears that Data
Input (or API Input), or until an execution's automatic preparation refreshes a stale one
(warned and recorded, never silently); an absent source never retires it, and a host that
cannot install the cap reuses a ready-but-stale generation with warning code
`cap_unavailable_stale_reused` — only a missing generation is refused typed
(`cap_unavailable`). A superseded generation is itself retired only after
`HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS` (default 1800) have elapsed since the current
generation was published; explicit clear bypasses this grace while preserving live
cross-process leases. Input snapshots and node outputs have no byte/count storage limits
or automatic eviction. Users inspect and clear stored datasets through the cache inventory.

Studio also prepares structured Quote Inputs (JSON/JSONL/NDJSON/XML) before
preview. It checks the node's tables against the current in-memory schema through
the input-cache status route, builds missing or stale tables through the
input-cache build job, and awaits it before execution. The preview panel shows the
preparation. Build or status failures stop preview with an actionable error;
cancellation cancels the job and prevents late execution. Ready, fresh tables are
reused without rebuilding.

Before Studio sends a preview, it asks the backend which inputs the preview
reads — none above a shared snapshot it seeds from, none outside its lineage — and
checks each of those snapshot-backed Data Inputs through the existing status endpoint. A missing,
corrupt, failed, or already-building snapshot starts or joins the existing
visible job, waits for completion, and only then sends the preview. The
server chooses how each snapshot is built, and the orchestrator makes one call that starts
or joins that build: a format the IO registry reads in bounded slices streams through a
lazy sink, and one that needs an eager read is admitted eagerly inside the hard-capped
worker. The build response names the class it chose (`build_class`); the browser never
chooses a profile or reads error text to find one. A ready but stale snapshot is refreshed by the execution's
automatic preparation before it runs, warned beforehand and recorded in the terminal
diagnostics. A missing, corrupt, failed, or building snapshot is built (or its running
build joined) before the run, announced by an info toast rather than a prompt, and the
explicit refresh action remains available.
File-backed Parquet inputs do not participate because they scan their Parquet
source directly and expose no cache action. Snapshot execution contacts the
provider only through automatic preparation under an admitted execution
context; schema-only or unadmitted callers receive the IO layer's explicit
`input_snapshot_missing:` error when a required snapshot is missing. Execution
caches key a snapshot-backed input by its generation pointer and its current
source signature, so a rewritten source misses every cache.

Full data at a pipeline point is materialised once into one shared layer and reused: every
bounded execution (training preparation and its evaluation preview, optimiser setup and
auto-range, Data Output runs), every explicit node-data build, and every admitted preview reads
the node-output snapshots that already cover what it needs and writes the full-data
materialisations it performs — for a preview, its joins and materialising operations — instead
of recomputing upstream work or writing temporary checkpoints. A preview therefore starts from what a run or build materialised, and a run from what
a preview did. A trace reads exactly the generations the preview it explains read and writes
none. A preview whose lineage is not admitted — an API Input in it reads a flat file that a
schema-only bounded read refuses — neither reads nor writes the layer. The preview/trace
runtime input fingerprint carries the generations a preview or trace seeds from; the preview
response cache's field set and stat-gated caches are otherwise independent of the layer. The execution engine specifies the seed plan
that governs this.
Analysis results are stored by point identity and data version, so a refreshed or widened
generation never serves a previous generation's results.

## Design rationale

Exact input contracts make omissions reviewable and fail loudly on drift. Versioned keys
allow intentional invalidation. LRU and byte bounds prevent process caches becoming
unbounded. One freshness proof, rather than one per source kind, means one guarantee to
reason about: native revisions see rewrites a size/mtime gate cannot, and the settle rule
keeps the stat fallback honest on filesystems without them.

## Interactions

- [Execution engine](../execution-engine/high-level.md), tracing, and executor construct
  lineage requests.
- [IO layer](../io-layer/high-level.md) consumes canonical identity helpers and owns
  `_source_cache.py`.
- Deploy scoring and modelling feature contracts instantiate `StatGatedCache`; utility-file
  hashes (`src/haute/_cache.py`), runtime-path fingerprints (`src/haute/execution.py`), and
  snapshot source signatures read the shared content signature.
- [JSON shredding](../json-shredding/high-level.md) owns cache content generation.

## Failure model

Unknown/missing checked inputs and unclassified config fields fail key construction.
Stat/loader errors propagate and failed values are not cached; a gate that moves twice
during load raises.

JSON schema and parse failures return structured 4xx responses;
source modification during build or stopped workers return 409; memory-limit exhaustion,
unsupported caps, and admission rejections return 507; timeouts return 504; unexpected
failures are logged and return a generic 500.
