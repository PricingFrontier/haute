# Caching — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_hashing.py` | Deterministic xxh64 byte/file hashing primitives. |
| `src/haute/_cache.py` | Canonical JSON, checked cache-input/config contracts, graph/preamble fingerprints, lineage-key factory, and utility-file hash memo/cache. |
| `src/haute/_lru_cache.py` | Thread-safe entry/TTL/byte bounded LRU with pinning. |
| `src/haute/_stat_gated_cache.py` | Bounded LRU, per-key single-flight cache gated by backing-file metadata. |
| `src/haute/routes/json_cache.py` | Structured API-input (JSON/JSONL/NDJSON/XML) schema inference HTTP surface. |
| `src/haute/_data_points.py` | Consumer-to-data-point mapping, point kinds and states, data versions, point identity digests, and leased point reads. |
| `src/haute/_seed_plans.py` | Seed plans for bounded executions: which node outputs a run reads from shared snapshots and which it captures, column negotiation, ancestry agreement, the plan fingerprint, and leased plans with their worker handoff. |
| `src/haute/_analysis_results.py` | Analysis documents keyed by data version under `.haute_cache/analyses`, and the in-process memo of synchronous analyses. |
| `src/haute/_source_cache.py` | Cross-component dependency owned by [io-layer](../io-layer/low-level.md); IO-layer-owned source snapshot store consumed for canonical cache identity and immutable generations. |

The shared `_source_cache.py` relationship is recorded in `specs/ownership.toml`; IO
layer is primary and caching is a consumer.

## Key types and data structures

- `CacheConsumerContract` and `CheckedCacheInputs` define exact, versioned key fields.
- `CACHE_CONFIG_FIELD_CLASSIFICATIONS` classifies every recognised node config field as
  execution input or rationale-bearing presentation exclusion; a stepped surface's
  `steps` list is classified `user_code` beside its `code` (transform, Data Input, External File, Rating Step, Model Score, Scenario Expander, Explore).
- `GraphFingerprintMemo` pins utility-file hashes consistently within one request while the
  shared content signature serves unchanged files across requests.
- `LineageCacheKeyRequest` carries graph, target node/port, upstream lineage, prepared
  runtime switch state, and utility-file evidence for `lineage_cache_key()`.
- `LRUCache` stores values in an `OrderedDict` with timestamps, optional sizes, and pins.
  `get` promotes an entry; `peek` reads without promoting, for a read that must not count as
  use. The assistant's `PlanStore` and `SessionStore` are built on it, pinning an applying
  plan and a session with a running turn.
- `StatGatedCache` stores `(freshness token, value)` in an `LRUCache`, plus participant-
  counted per-key load gates that are dropped as soon as no caller waits. The default maximum
  is 256 entries. A forked child replaces its locks and starts empty.
- `_source_proof.Freshness(token, reusable)` is one observation of a file;
  `_source_proof.FileSignature(size, mtime_ns, digest)` its complete-content proof, whose
  `source_signature` is `xxh64:<digest>:<size>`.
- `SeedPlanRequest` names one bounded execution before it builds anything: the lineage
  target, the nodes the caller reads afterwards (`consumed_node_ids`, default the target),
  source, profile, the caller's column demand, best-effort `capture_columns_by_node`,
  per-node source overrides, `refresh`, and an explicit build's node. `SeedPlanDecision` is
  its pickle-safe resolution: seeds (`SeedDecision`: identity, generation id, generation
  columns, demand, recorded dependencies), captures (`CaptureDecision`: identity,
  `CaptureKind`, negotiated and strict columns), `skipped_captures` mapping each capture
  point the bounded rule declined to its reason (`cheap_segment` or
  `slice_transparent_feeder`), the executed nodes, the selected edge of every executed
  pass-through node, the planning demands, the lineage and runtime-input fingerprints, and
  the plan fingerprint. `SeedPlan` holds the seed leases and whatever the run registers;
  `SeedPlanHandoff` carries a plan to a spawned worker.

### Checked cache-input inventory

Every maintained consumer has one closed payload shape and version. Construction
requires exactly the listed fields, preserves contract order, and rejects both
missing and unknown names before hashing:

| Consumer | Version | Complete field set |
|---|---:|---|
| `graph_structure` | 2 | `nodes`, `edges` |
| `graph_execution` | 8 | `base_fingerprint`, `preamble_fingerprint`, `source_file`, `extra_keys` |
| `preview_trace` | 3 | `preamble`, `source_file`, `nodes`, `edges`, `target_node_id`, `source`, `requested_columns`, `initial_column_limit`, `row_limit`, `port_label`, `contract_fingerprint`, `selected_live_switch_path`, `runtime_input_fingerprint`, `execution_semantics_version` |
| `runtime_graph_input` | 4 | `source`, `sources`, `preamble_fingerprint`, `extra` |
| `deploy_schema` | 1 | `graph_fingerprint`, `runtime_input_fingerprint`, `artifact_fingerprint`, `output_node_id`, `input_node_ids`, `source`, `row_limit`, `execution_policy` |
| `model_contract` | 1 | `feature_names`, `categorical_features`, `offset_column` |
| `input_snapshot` | 1 | `schema_version`, `provider`, `descriptor` |
| `node_snapshot_signature` | 1 | `lineage_fingerprint`, `runtime_input_fingerprint`, `source`, `semantics_class`, `enforce_contracts`, `preamble_supplied`, `execution_semantics_version` |

The repeated records inside those payloads are separately closed and versioned:
`graph_node` v1 is `id`, `label`, `nodeType`, `config`; `graph_edge` v1 is
`source`, `sourceHandle`, `target`, `targetHandle`; `runtime_input_entry` v1
is `node_id`, `node_type`, `config`, `files`; and
`live_switch_selection` v1 is `switch_id`, `incoming_edges`.

Each consumer also classifies all ten logical input classes — node config,
upstream lineage, edge wiring, user code, source selection, row limit,
runtime files, artifacts, request shape, and execution policy — as either
consumed by named payload fields or deliberately excluded with a non-empty
rationale. A payload field that is not assigned to a class, or an input class
that is neither consumed nor explained, makes contract construction fail.

## Control flow

### Graph and lineage identity

1. Callers classify inputs through `checked_cache_inputs()`.
2. `graph_fingerprint()` canonicalises execution-relevant graph/config/code/utility-file
   evidence and applies `ALGO_VERSION`.
3. `lineage_cache_key()` builds the shared preview/trace key from its request, including
   selected live-switch paths and upstream lineage.
4. Executor and trace use the factory; graph fingerprint alone is not their cache key.
5. `dataframe_graph_input_identity()` reads a scope's runtime inputs into a
   `RuntimeInputIdentity`; its `fingerprint(extra)` hashes them with extra entries and
   reads nothing, and `dataframe_graph_input_fingerprint()` is the two together.
   `preview_lineage_cache_key()` accepts a caller's `runtime_input_identity` — the read an
   entry must be keyed by — and a `seed_plan_fingerprint`, which joins the runtime-input
   fingerprint as `extra["seed_plan"]`, so an entry computed from one seed generation is
   never served for another; without one the key is unchanged.

Utility-file hashes use a request memo in front of the shared content signature
(`_source_proof.file_signature`), which execution's runtime-path fingerprints and the Data
Input and API Input source signatures read too, so one file is hashed once per unchanged
freshness token whatever asks.

### Source freshness

`_source_proof.observe_freshness(path)` reads the native revision (Windows: volume serial,
128-bit file id, USN, size and last-write time through `FSCTL_READ_FILE_USN_DATA`; POSIX:
device, inode, ctime, size and mtime). Without one it returns the stat tuple
`("stat", dev, ino, size, mtime_ns, ctime_ns)`, reusable only when the mtime is at least
`SETTLE_SECONDS` (2.0) old, and logs `source_revision_unavailable` once per path. A missing
file raises `FileNotFoundError`. `file_signature(path)` is a `StatGatedCache` of
`FileSignature` values keyed by the canonical path; `clear_file_signatures()` empties it
(the in-process proofs only).

### Durable source proofs

A content signature proved under a native revision is also recorded on disk, so a new
process reuses it instead of reading the whole file again. One record per resolved source
path lives at `<project root>/.haute_cache/source_proofs/<name>.json`, where `<name>` is the
SHA-256 hex of `os.path.normcase(<resolved path>)` and the project root is
`haute._sandbox._get_project_root()`, read through `_source_proof._proof_record_root()`.

A record (`schema_version` 1) is `canonical_json` bytes written through
`_file_ops.atomic_write_bytes`. It has exactly these keys at each level; a missing or extra
key, or a value of the wrong type, makes it invalid. Integers are checked with
`type(x) is int`, so a `bool` is not an integer here.

| Key | Type and rule |
|---|---|
| `schema_version` | int, `== 1` |
| `path` | str, the case-preserved resolved path; must equal the path being proved |
| `revision.kind` | `"windows_usn_v1"` or `"posix_ctime_v1"` |
| `revision.file_identity` | 2-item list: `[volume serial or st_dev (int >= 0), file id]`; the file id is a 32-char lowercase hex string of the 128-bit id, not all zero, for `windows_usn_v1`, and an int inode > 0 for `posix_ctime_v1` |
| `revision.size` | int >= 0 |
| `revision.mtime_ns` | int |
| `revision.change_token` | int > 0 (the USN on Windows, `st_ctime_ns` on POSIX) |
| `hash_algo` | str, equal to `_hashing.HASH_ALGO` (`"xxh64"`) |
| `digest` | str, 16 lowercase hex chars |
| `size` | int, `== revision.size` |
| `mtime_ns` | int, `== revision.mtime_ns` |

Records are read and written only by the `file_signature` loader, so only on an in-process
miss, inside the freshness-gated load below. The loader reads the native revision once and
then:

| Situation | Outcome | Log |
|---|---|---|
| No native revision | Hash; no record is read or written (the settled-stat rule is in-process only) | `source_revision_unavailable`, once per path |
| No record file | Hash, then write | none |
| Record unreadable (an `OSError` other than `FileNotFoundError`) | Hash, then try to write | `source_proof_record_rejected`, `reason="unreadable"` |
| Record does not parse, breaks the contract, or names another path or algorithm | Hash, then overwrite | `source_proof_record_rejected`, `reason="invalid"` |
| Valid record naming a different revision | Hash, then overwrite | none |
| Valid record naming the current revision | Return its signature without hashing | none |
| The write fails (`OSError`) | Return the fresh signature; no new record is published and any previous one remains, which its revision already refuses | `source_proof_record_write_failed` |

A record is written only when the revision read before the hash is still the file's
revision after it. Processes race by atomic replacement, and each record is valid only for
the revision it names. Records have no eviction (one small file per source path) and go
with `.haute_cache`. A rejected record is logged rather than raised: it only spares a hash,
and the content hash stays authoritative.

Residual: a Windows USN is a journal offset, so a USN journal deleted and recreated could in
principle hand a later write a recorded USN. Reuse also requires the same 128-bit file id,
size and last-write time, which makes that no practical collision.

### Freshness-gated loading

1. Observe the case-preserved resolved path's freshness.
2. When the token is reusable, return the keyed entry if its token matches.
3. Otherwise join the per-key load gate; after acquiring it, observe again and recheck, so a
   caller that waited through a change reuses the value the previous holder cached for it.
4. Load, observe again, and cache (only a reusable token) if the token held.
5. Retry one moving token; then raise `SourceChangedError` (an `OSError` and a
   `RuntimeError`).
6. Evict LRU entries above `max_entries`; drop the load gate when its last caller leaves.

The runtime consumers are the shared content signature, deploy scorer models
(`src/haute/deploy/_scorer.py`), and modelling feature contracts
(`src/haute/modelling/_feature_contract.py`).

### Structured API-input tables

Each emitting table of a structured (JSON, JSONL, NDJSON, XML) API Input is one
input snapshot in the shared store (provider `api_input`; identity, freshness and
build in the [JSON shredding](../json-shredding/low-level.md) specification).
`routes/input_cache.py` (owned by the [server API](../server-api/low-level.md)) serves them through the same endpoints as a Data Input's
snapshot, selected by `node_type: "apiInput"` on the request:

1. The config must have a structured path and a v2 `tables` list, validate, and
   emit at least one table; otherwise 400 `invalid_input_config`. The path is
   resolved with project-root containment exactly as execution resolves it (403
   outside the project).
2. `status` returns `InputCacheSnapshotStatusResponse` with `identity_digest` naming
   the node's set of tables (`group_digest`), `generation` `None`, and `tables`
   listing each table's label, identity digest, state, freshness and generation. The
   summary state is `building` while the node's build runs (tables not yet ready
   report `building` too), else `corrupt` if any table is, `ready` when every table
   is, and `missing` otherwise; freshness is `stale` when any ready table is,
   `fresh` when every table is ready and fresh, and `unknown` otherwise.
3. `build` starts or joins one job per node (single flight keyed by the group
   digest). The job runs in a hard-capped spawned worker admitted from the server's
   budget (`run_supervised_api_input_build`), writing every missing or stale table —
   every table on `refresh` — from one shred of the source; a node whose tables are
   all ready and fresh completes without a worker. The completed job carries the
   node's status; a table left unpublished fails the job. Worker failures map onto
   the job lifecycle exactly as an admitted-eager Data Input build's do.
4. `clear` removes every table of the node, and answers 409 while the node's build
   runs.

A Data Input's `build` takes no profile: `_chosen_build` asks the IO registry for the
config's build class (`input_snapshot_build_class(..., allow_admitted_eager=True)`) and
builds a `bounded` class under `LAZY_SINK` and an `admitted_eager` class under
`PREVIEW_EAGER` in the hard-capped worker. A config that cannot build a snapshot answers
400 `snapshot_build_unsupported`. Every build response, including one that joins a running
build, carries the job's `build_class`.

`api_input_table_build_running(table digest)` reports whether a running build
writes a table, for the data-point `building` probe. Schema inference stays on
`POST /api/json-cache/infer`.

### Data points

`src/haute/_data_points.py` owns the consumer-to-point mapping and leased reads.

- Every entry point first resolves instance nodes (`resolve_instance_nodes`), so an
  instance maps, classifies, executes, and signs with its original's configuration.
- `consumer_point(graph, consumer_node_id)` returns a `ConsumerPoint(consumer_node_id,
  point, demand)`. Banding reads the single incoming edge's point with the factors'
  `column` values as demand; Rating Step reads it with the tables' `factors`; a blank-code
  Explore reads it with every column; any other node, including Explore with code, reads
  `DataPoint(node_id, None)` with every column. The incoming edge's point is
  `DataPoint(edge.source, edge.sourceHandle)` when the producer is an `apiInput` with a
  handle, otherwise `DataPoint(edge.source, None)`. Zero or several incoming edges, an
  unknown node, or a port on a single-frame producer raise `NodeDataPointInvalidError`
  (`node_data_point_invalid`).
- `point_kind(graph, point)` returns `api_input_table` for an `apiInput` port, `data_input`
  for a Data Input with blank `code`, and `node_output` otherwise.
- `api_input_table_labels(node_id)` and `api_input_table_digests(node_id)` name a
  structured API Input's emitting tables and their distinct identities (empty for any
  other node, or for a schema the node builder would reject).
- `DataPointResolver(graph, source, store=NodeSnapshotStore, building=probe)` canonicalises
  the graph once. `building(kind, key)` reports a running build keyed by the input-snapshot
  identity digest (a Data Input's, or an API-input table's own) or the node-output identity
  digest.
  `resolve(point, demand)` returns a `PointResolution` (kind, state, demand, data version,
  build key, and the node-output identity/generation or input identity/generation id):
  - `data_input`, direct Parquet: `current`; the version hashes the resolved path, its size
    and `mtime_ns` (or `missing`), and the node's lineage fingerprint.
  - `data_input`, snapshot-backed: `SourceCacheStore.status(identity, source_signature)`;
    `ready` with `stale` freshness is `stale`, other `ready` is `current`, `corrupt` is
    `corrupt`, anything else `missing`; the version hashes the generation id and lineage.
  - `api_input_table`: the port must name a configured, emitting table; its identity is
    that table's input snapshot, whose status against the source signature maps to states
    exactly as a snapshot-backed Data Input's does; the version hashes the generation id,
    the port, and the lineage.
  - `node_output`: `NodeSnapshotStore.slot_status` for the slot (resolved pipeline file,
    node, source, `bounded`) and the `enforce_contracts=True` signature; a `current`
    generation whose columns do not cover the demand is `partial`; the version is the
    generation id.
  A non-current point whose build key has a running build is `building`.
- `lease_frame(point, demand, execution_context=None)` (and the module-level
  `lease_point_frame(graph, point, source, columns)`) raises `CacheRequiredError`
  (`cache_required`, carrying the resolution) unless the point is `current`, then yields a
  `LeasedPointFrame(kind, data_version, columns, scan, resolution)` whose scan selects the
  demanded columns in schema order. A demanded column the data lacks raises
  `PointColumnsMissingError` (`node_data_columns_missing`, naming the columns); an empty
  demand keeps one carrier column so the frame keeps its row count. The data version is
  always the version of the data the scan reads:
  - `node_output`: leases the resolved generation with `lease_generation`; a generation
    retired in between re-resolves and raises `cache_required` with the new state.
  - `api_input_table`: leases exactly the resolved table generation with
    `lease_generation` and scans it — the table is the data, with no post-load code; a
    generation retired in between re-resolves and raises `cache_required`.
  - `data_input`: leases the resolved input-snapshot generation when there is one, then
    executes the single source node alone (`execute_lazy_graph` over a one-node graph,
    `enforce_contracts=True`, `prepare_inputs=False`). A snapshot pointer that moved past
    the resolved generation re-resolves and raises `cache_required`.
- `lease_resolved(resolution, exact=False, execution_context=None)` is the same lease over
  an already-resolved point, so a spawned worker reads exactly what its parent resolved and
  leased. With `exact`, data whose version moved between resolution and read — a rewritten
  direct file — raises
  `PointDataChangedError` (`node_data_changed`) instead of being read under a new version.
  `lease_frame` is `resolve` followed by a non-exact `lease_resolved`.
- `point_digest(point)` is the consumer-independent identity of a data point: the SHA-256 of
  the canonical `(schema version, resolved pipeline source file, producer node, port label,
  source)`. It keys analyses of the point and never contains a consumer node or a column
  demand, so every consumer of one point shares its analyses.

### Seed plans

`src/haute/_seed_plans.py` decides, before a bounded execution or an admitted preview builds
anything, which node outputs it reads from shared snapshots (**seeds**) and which full-data
materialisations it writes to them (**captures**). The lazy engine executes under the
resulting plan.

- **Eligibility.** The profile must both read and write the `bounded` class
  (`snapshot_read_classes`, `snapshot_write_class` with `preview_admitted=True`); deploy
  profiles raise `ValueError`. A `PREVIEW_EAGER` request is built only for a lineage
  `preview_lineage_admitted` accepts (below). The prepared graph is `_prepare_execution` with the request's target, source,
  demand, and profile — the one the lazy engine runs. Consumed nodes and an explicit build's
  node outside the target's lineage raise `ValueError`.
- **Effective edges.** A pass-through node (`PASS_THROUGH_NODE_TYPES` in `_builders.py`: Data
  Output, modelling, Optimiser, submodel, submodel port) *is* its selected input:
  `pass_through_selected_edge` returns the incoming edge its builder returns — the first, or for
  an Optimiser with `data_input` the edge that input names. It is an edge, not a parent id, so
  two ports of one API input stay distinct. That edge is the node's only effective one; every
  other node's effective edges are its relevant incoming edges.
- **Demand.** A node's demand is the engine's own projection — `compute_prepared_plan` then
  `with_api_input_port_projection_boundaries` over the prepared graph — of the caller's
  `required_columns_by_node`: its `needed_by_node` column set, or all columns.
- **Walk.** From the target and every consumed node along effective edges, a visited node is a
  seed when the request is not a refresh, it is not the explicit build's node or a dropped seed,
  it is a `node_output` point, and its identity's latest generation (resolver slot and signature)
  is fresh and covers its demand; an empty demand is covered by any generation. The walk stops
  at a seed and never reads the metadata of anything above it. Every other visited node is
  executed.
- **Capture points** are executed nodes, other than pass-through types, non-`node_output`
  points, and the explicit build's node, with configs read instance-resolved so an instance
  runs its original's operations. Capture decisions evaluate the segment upstream of each
  node. The segment is the walk up effective edges from the node (inclusive) that stops at,
  and excludes, a seed, a capture point already decided in this resolution (captures are
  decided in execution order), or a source base case. A node's own facts come from
  `recompute_facts_by_node` (code) and `NODE_REGISTRY[node_type].recompute_cost` (builders);
  all of these are planning-time facts, and no builder runs while capture points are decided.
  The source base cases and their facts are: a seed or an already-decided capture point is
  cheap and slice-transparent (a generation is a Parquet leaf); a `DATA_INPUT` with blank
  post-load code is cheap and slice-transparent whether reading direct Parquet or a prepared
  snapshot, and with post-load code takes its code's recompute facts; an `API_INPUT` with a
  structured path (`is_json_api_input_path`) is cheap and slice-transparent, and with a
  flat-file path is costly; a `CONSTANT` is cheap and slice-transparent; and an
  `EXTERNAL_FILE` is not a base case, continuing the walk through its inputs. A node with
  several distinct effective inputs takes the conjunction over every input's segment.
  Pass-through nodes contribute nothing and the walk follows their selected edge.
  The bounded capture rule evaluates executed nodes in execution order under six-step precedence:
  1. batch Model Score (scenario not `live`) is captured as `model_score` (its scored parts are
     the artifact);
  2. a node whose recompute facts are costly and whose type is not a `costly` builder
     (its cost comes from its code) is captured as `materialising`;
  3. an `EDGE_JOIN` node, or a node with more than one distinct `(source, sourceHandle)`
     effective input, is captured as `structural`;
  4. if the segment is costly, the node is captured as `consumed` if it is a consumed
     producer, else `structural` if it fans out (more than one executed child) or feeds a
     join (a child that is an `EDGE_JOIN` or has more than one distinct effective input),
     else not captured; a `costly` builder that rules 1 and 3 do not capture (a Rating
     Step, a live Model Score) is decided here, because its segment includes itself;
  5. if the segment is cheap and slice-transparent, the node is not captured; when it is a
     consumed producer, fans out, or feeds a join, the skip is recorded as
     `slice_transparent_feeder` if it feeds a join, else `cheap_segment`;
  6. if the segment is cheap and not slice-transparent, the node is captured as `structural`
     only if it feeds a join; otherwise a fan-out or consumed producer is skipped with reason
     `cheap_segment`.
- **Preview capture points.** In a `PREVIEW_EAGER` request, a code node is captured when
  `recompute_facts_by_node` reports a registered call that is full-input work (costly to
  recompute and not opaque, so a row limit cannot bound it), or a node with more than one
  distinct effective input; frame and expression `sort`, `unique`, `rank`, `group_by`, `join`,
  `over`, and `pivot` are captured, while `explode`, `shift`, `map_elements`, `pipe`, and a node
  whose only costly call is unresolved are not captured. A batch Model Score (scenario not
  `live`) is captured as `model_score` when a capture below it drains its whole output: a node's
  output is drained when it is captured, or when a child whose code reads every input row has its
  output drained. A captured Model Score reads every input row (it scores its whole input before
  its post-processing code runs); other code reads every input row unless it calls a row-bounding
  method (`head`, `tail`, `limit`, `slice`, `first`, `last`, `sample`, `gather_every`) on any
  receiver, slices with a subscript (`df[:10]`), or its recompute facts leave a call unproven
  (`projection.code_bounds_rows`). The check is conservative: code that bounds rows only after
  reading them all (`sort(...).head(10)`) still counts as bounding, and its scorer keeps the
  row-local scan. A row bound below the scorer is pushed into its scan, which then scores only
  the rows kept. Drained, its row-local scan would be
  pulled whole through Polars, which applies no backpressure to a Python source, so every scored
  batch would sit in memory until the capture below wrote it. Captured, it scores every row a
  batch at a time into its own parts whatever the row limit, and the capture below reads parquet
  parts. A Model Score nothing below captures, including a previewed target, still scores
  row-locally. A preview records no skips. A preview may seed its own target. A node whose own or instance-resolved config
  selects or renames its output columns (`selected_columns`, `column_renames`) is seeded only
  from a generation that recorded its unshaped columns: a preview reports every node's columns
  before that shaping — the columns its Columns editor offers and its stale-selection warnings
  check — and a generation holds the shaped output. Every capture and explicit build of such a
  node records those columns as `node_output.unshaped_columns` (`[name, dtype]` pairs), and a
  seeded preview reports them from there (`SeedPlan.seed_unshaped_columns`). A generation
  without the record is not seeded: the node is executed, and a preview may still capture it,
  for the bounded executions that seed from it. A preview's caller demand is only which columns to
  show first, so its request is `best_effort_demand`: no capture's columns are strict, and a
  requested column the target does not produce is refused as it is without a plan (400)
  rather than as a capture that lacks it.
- **Negotiation.** Each capture's demand is the run's demand there, the best-effort capture
  columns, and the columns of its identity's latest generation, fresh or stale, so a rebuild
  never narrows a generation. All columns plan as `AllExcept()`, and a caller's unresolved
  `AllExcept` demand stays all columns. The projection is re-planned with those demands; a seed
  that no longer covers its re-planned demand is dropped and the walk runs again with the
  re-planned demand, until none drops.
- **Ancestry agreement.** Seeds that record different generations of one identity are all
  dropped, and so is every seed recording an identity whose node the run still executes: a
  current, fresh, covering generation of that node would already have been seeded where the walk
  reached it, so an executed one would feed its readers other data than the seed was built from.
  A dependency nothing else in the run reads imposes nothing, so a linear chain keeps its seed
  after an ancestor is cleared. Resolution restarts until nothing drops; a dropped seed is never
  re-added.
- **Decision.** A capture records the negotiated columns it writes and the strict columns the run
  itself needs — none for a `best_effort_demand` request. The decision carries `skipped_captures`
  mapping each skipped capture point to its reason (`cheap_segment` or `slice_transparent_feeder`),
  and `SeedPlanHandoff` carries it to a spawned worker. `runtime_input_fingerprint` is
  `dataframe_graph_input_fingerprint` over only the executed nodes (a seed's inputs cannot change
  what the run reads); `lineage_fingerprint` is the graph fingerprint of the target's upstream
  subgraph; `fingerprint` is `seed-plan:v1:` and the SHA-256 of the sorted `(identity digest,
  generation id)` pairs of the seeds only.
- **Leasing.** `open_resolved_seed_plan` resolves, leases every seed with `lease_generation`, and
  confirms each is still its identity's latest generation. A seed retired or replaced in between
  re-resolves, at most three times, then raises `SourceCacheGenerationMissingError`.
  `open_seed_plan` first runs automatic input preparation for the nodes reachable along effective
  edges — signatures sign prepared generations, and a branch reached only through an unselected
  pass-through input is never prepared. Preparation reads instance-resolved configs, so a Data
  Input instance prepares its original's snapshot.
- **Preview preparation.** A node's signature already signs each snapshot-backed input's
  generation pointer and current source signature, so a stale, missing, or cleared input fails
  every seed below it before anything is prepared. For a `PREVIEW_EAGER` request
  `open_seed_plan` therefore opens the plan first, prepares the snapshot-backed Data Inputs among
  `decision.executed_node_ids` it has not prepared yet, and opens it again — preparation may move
  a pointer and with it every signature below — until a plan executes no unprepared input. After
  three rounds (`_PREVIEW_PREPARATION_ROUNDS`) it prepares every readable input, so the next plan
  terminates the loop. Structured API Inputs are snapshot-backed inputs here too: their
  tables are prepared the same way.
- **Admission.** `preview_lineage_admitted(graph, target, source=)` decides per source of the
  target's lineage (instance nodes resolved), before any preparation: a Data Input is admitted,
  since it executes from a Parquet scan or its prepared snapshot; so is a structured (JSON,
  NDJSON, XML) API Input, which reads its prepared table snapshots exactly as a bounded run
  does; a flat-file API Input is admitted when `resolve_api_input_from_config` under
  `LAZY_SINK` followed by `collect_schema()` succeeds — for a CSV, a header read that needs
  declared dtypes. `BoundedMemoryUnsupportedError` means not admitted. Any other failure of that
  read (a missing file, a bad path) is logged and also means not admitted: it is the preview's
  own read's error to report at the node, and a preview of an unadmitted lineage runs as before.
- **Preview inputs.** `preview_input_node_ids(graph, target, source=, required_columns_by_node=)`
  returns, in execution order, the snapshot-backed Data Inputs and structured API Inputs a preview
  would read: for an admitted lineage those its first resolution executes, read without preparing
  or leasing; otherwise every one the target can read. It is advisory — a preview prepares what
  its own plan reads.
- **Listed plans.** `open_listed_seed_plan(request, listed)` is the plan of a trace, built from
  the `ListedSeed(node_id, identity_digest, generation_id)` entries its preview returned. Every
  entry is checked and leased first, in order: a node no longer in the target's lineage or not a
  `node_output` point, an identity digest the request's graph no longer produces there, or a
  generation `lease_generation` cannot find raises `SeedPlanExpiredError`; corruption and other
  storage errors propagate. `_ListedResolver` then resolves with the base walk, negotiation, and
  ancestry agreement, except that a listed node's candidate is its listed generation (current or
  not) when it covers the demand, nothing else is a candidate, and nothing is captured — so a
  listed generation that does not cover the demand is recomputed, and so is every listed seed
  recording it. The plan holds every listed lease until it closes; a duplicated node raises
  `ValueError`.
- **Ownership.** A `SeedPlan` owns the publications and request-owned artifacts the run registers
  and records the dependency closure behind each node's frame (`record_closure`,
  `dependencies_for`); `seed_frame` is a seed's leased generation projected to its demand,
  and `estimation_graph` replaces each seed with a direct Parquet input of that generation for
  materialisation estimates. `close()` closes them, releases the seed leases, and, in the process that
  opened the plan, removes any staging directory left under its staging token; a supervising
  parent closes only after its worker has exited. `handoff()` returns a `SeedPlanHandoff`
  (decision, project root, staging token), and `SeedPlan.adopt` leases the same generations in
  the worker without the currency check and never removes the parent's staging.

### Analysis results

`src/haute/_analysis_results.py` keeps analyses of a data point keyed by the exact data they
were computed from, so a refreshed, widened, or rewritten point never serves an analysis of
its previous data.

- `AnalysisKey(point_digest, data_version, kind, version)` validates its fields: the digest is
  a lowercase SHA-256 hex digest, the data version is non-empty, the kind is an identifier, and
  the analysis version is a positive integer. The analysis version is the computation's own
  contract version, raised when its result would change for unchanged data.
- `AnalysisResultStore(project_root)` holds one atomic JSON document per key under
  `<project>/.haute_cache/analyses/<point digest prefix>/<kind>-v<version>-<data version
  digest>.json`. Directory and file names are truncated digests so a deeply nested project
  stays inside the Windows path limit; every document carries the full key and is validated
  against it on read.
- `read(key, model)` returns the model only for exactly that key: it first removes every
  document of the same point, kind, and analysis version under any other data version,
  because that data can never be current again, and a document that is unparsable, keyed
  differently, of an unknown schema version, or whose result fails validation is deleted and
  reported as absent, so the analysis is recomputed rather than trusted.
- `write(key, result)` writes the canonical-JSON document atomically. `clear_point(digest)`
  removes every analysis of one point and tolerates only an absent directory: a deletion that
  fails for any other reason is raised, because a clear that reports success must leave nothing
  behind to serve.
- `SynchronousAnalysisCache` memoises short, request-time analyses in process, keyed by
  `(point digest, data version, request digest)`, where the request digest is the SHA-256 of
  the canonical analysis request. It is an `LRUCache` of 64 entries by default and is never
  durable: only the profile is stored on disk.

### Source snapshots

`SourceCacheIdentity` uses `checked_cache_inputs(CacheConsumer.INPUT_SNAPSHOT, ...)`.
`node_snapshot_signature()` in `src/haute/_node_snapshots.py` builds the
`node_snapshot_signature` consumer over the node's **source lineage**:
`execution.source_lineage_graph(graph, node_id, source=...)`, the node and everything
upstream of it after the executor's own live-switch pruning (`prepare_graph` →
`prune_live_switch_edges`) for the signature's source. The lineage fingerprint is
`graph_fingerprint` of that graph, the runtime-input fingerprint is
`dataframe_graph_input_fingerprint` of the same graph targeted at the node, and the
execution semantics version is `node-snapshot:v1`. A branch into a live switch that the
source does not read is therefore not signed: editing its code or rewriting its input
files leaves the signature, and every capture keyed by it, unchanged, and its files are
never hashed. The switch node itself stays in the lineage with its config, so remapping
its `input_scenario_map` invalidates. A graph without a live switch, or a source no switch
maps, prunes nothing, and its signature is the upstream-subgraph one. The preview/trace
key (`lineage_runtime_input_identity`) reads its runtime inputs from the same graph. It never contains generations or column sets, and
request shape (column demand) and row limits are excluded with rationales.
Generation layout, integrity, publication, lease, and concurrency rules are owned
and tested by the [IO layer](../io-layer/low-level.md).

## Edge cases and invariants

- `canonical_json()` is the sole encoder for JSON-shaped transient digest and
  cache-key material. The one deliberate exception is the persisted modelling
  feature-contract hash: its historical compact sort-keyed JSON plus SHA-256
  encoding remains byte-stable so previously published `contract_hash` values
  continue to verify.
- Every logical cache input is present exactly once; unknown fields fail.
- LRU oversized rejection retains a previous same-key entry.
- Freshness-gated caches never exceed `max_entries` after a completed insertion.
- A proof or loaded value is never reused across a moved freshness token, and a token without
  a native revision is reused only for a settled file.
- Loader failure never stores a value or strands an idle load gate.
- An API Input's status, build and clear validate the same v2 schema as execution, and
  its status is ready only when every emitting table's snapshot is.
- A seed plan never seeds a stale generation, never reads metadata above a seed, never seeds
  an explicit build's own node, and seeds nothing on a refresh.
- A pass-through node is never captured; the producer its selected edge names is.
- A cheap, slice-transparent segment is never captured, whatever its fan-out, join feeding,
  or consumers.
- Capture points and skips are decided from graph, registry, and store facts alone, identical
  for `resolve_seed_plan`, `open_resolved_seed_plan`, and the handoff.

## Error handling

Contract/key errors are `ValueError`/`TypeError` at construction. `StatGatedCache` propagates
observation and loader exceptions and raises `SourceChangedError` after two moving tokens.

Seed-plan resolution raises `ValueError` for a profile outside the `bounded` class or a node
outside the target's lineage, propagates `SourceCacheCorruptError` from any generation it
reads, and raises `SourceCacheGenerationMissingError` once seeds have moved under it three
times.

Structured-input cache routes preserve structured schema/parse/path errors, return 409
when the source changed during build or the worker stopped, return 507 on
memory-limit exhaustion, unsupported caps, or admission rejection, return 504 on
response timeout, and log unexpected errors before a generic 500.

### Boundary failure ordering

1. Checked input construction validates the consumer enum, mapping shape,
   exact field set, nested-record shape, and logical-class completeness before
   canonical JSON or hashing. A caller cannot produce a best-effort key with an
   omitted or extra dimension.
2. Freshness-gated loading observes before lookup, joins the per-key single-flight gate,
   observes again and rechecks after acquiring it, loads, and observes again before
   insertion. A loader failure caches nothing; one moving token retries and a second
   raises. Eviction happens only after a stable insertion.
3. Structured-input cache routes perform path containment, then select/validate schema,
   then check the data file, then start blocking shred work. Consequently
   missing schema is 422 even when the data path is absent; file absence is
   404 only after schema succeeds; response timeout is 504 without being
   presented as cooperative cancellation.
4. Input-snapshot identity is checked here before storage selection; pointer,
   lease, integrity, staging, refresh, and clear failure order remains
   owned by [io-layer](../io-layer/low-level.md#boundary-failure-ordering).

### Depth-review questions

The operational review checks that this specification answers: What is the
complete field set and schema version for every maintained consumer and nested
record? How is every logical input class consumed or deliberately excluded?
When are cache values admitted, pinned, detached, and unlinked? In what order do
contract, stat/load, artifact, JSON-route, and source-snapshot failures surface?
The checked-input inventory, control-flow narratives, invariants, and boundary
ordering above are the answers and must be updated together when a consumer or
cache lifecycle changes.

## Testing

- `tests/test_seed_plans.py` covers the capture rule (join feeders, joins, fan-outs,
  materialising `group_by` and `sort`, batch but not live Model Score, consumed producers through
  pass-throughs, nothing for a blank Data Input), pass-through selection agreeing with the built
  function for modelling, Data Output, and Optimisers with and without `data_input`, a two-input
  modelling node that is neither a join nor builds its unselected branch, an Optimiser's second
  input and an API input's second port, the walk stopping at the first fresh covering generation
  without reading anything above it, stale and partial generations, class-less profiles, explicit
  builds seeding strictly upstream, refresh, disjoint-demand negotiation, a narrow upstream seed
  dropped and widened, best-effort capture columns, an unresolved `AllExcept` capturing all
  columns, a linear chain keeping its seed after an ancestor clear, a recomputed branch seeding
  the recorded ancestor or dropping the seed when it is cleared or does not cover, conflicting
  recorded generations, drops across rounds, consumed side inputs, leases through refresh and
  clear, re-resolution and giving up, handoff round trip, the plan fingerprint, ownership of
  registered publications and artifacts, token staging removal, and input preparation of only
  readable inputs before resolution. For previews it covers the capture rule (joins and
  materialisations only, including a join or group-by target and a join feeding a join, never a
  plain fan-out, feeder, target, or Model Score), seeding the target, preparing nothing above a
  seed, a rewritten source dropping the seed below it before preparation, resolving again after
  preparing, a real input build moving the capture to the prepared signature, exhausted rounds
  preparing the rest of the lineage once, instance nodes read through their originals (a
  group-by instance captured, a Data Input instance listed and prepared), and listed plans leasing
  exactly their generations through a refresh, an empty list, expiry on a retired generation, an
  edited lineage, or a point outside it, corruption propagating, a non-covering generation being
  recomputed yet still leased through a clear, and a listed seed built from a recomputed point
  being dropped. Cost-gated capture points cover cheap segment skips for fan-out and consumed nodes, slice-transparent feeders to edge joins, filter feeders captured while filter fan-outs are skipped, costly segments retaining structural and consumed captures across rating steps and unresolvable sources, segments stopping at fresh captures and seeds, multi-port API inputs joined as structural captures, costly code nodes versus cheap boundaries, preview captures restricted to registered full-input work, and immutability of settled captures and skip reasons across handoffs.
- `tests/test_training_seeding.py` covers a consumed select below a Rating Step being captured
  and seeded on a second run, while the unconsumed Rating Step is not captured.
- `tests/test_preview_admission.py` covers an API Input over an undeclared-dtype CSV not being
  admitted and over a declared one being admitted, a Data Input over the same CSV and a
  structured API Input being admitted, a failing probe leaving the lineage unadmitted without
  raising, an API Input outside the lineage not deciding it, and the preview inputs of a seeded,
  an unseeded, and an unadmitted lineage.

- `tests/test_runtime_input_cache_invalidation.py` — preview/trace cache keys invalidate on runtime file/artifact edits or disappearance, preserve stat-gate semantics, and share file signatures across preview/trace.

- `tests/test_data_point_resolver.py` covers consumer points for Banding, Rating Step,
  and Explore with and without code, invalid wiring, a narrow captured generation being
  `current` for Banding and `partial` for Explore, node-output `missing`, `building`,
  `stale`, and `corrupt`, direct-Parquet versions, a Data Input frame equal to a run's
  with selections and renames, a post-load-code Data Input read as one shared generation,
  a missing snapshot-backed input starting no build, a built one going stale, two
  `apiInput` ports scanning only their tables, an uncached port raising
  `cache_required` without shredding, a spawned child reading a parent-leased
  generation through refresh and clear, subset and empty demands over a wider generation
  and absent demanded columns, instance consumers and sources resolved through their
  originals, a table cache rebuilt between resolution and load versioned as the data read,
  and a status probe returning `building` promptly while a build holds the cache locks.
- `tests/test_node_snapshot_signature.py` covers the `node_snapshot_signature` field set
  and its invalidation matrix, including an instance node following its original.
- `tests/test_analysis_results.py` covers a document served only for its own data version and
  removed once the point moves on, other kinds and analysis versions of the same point being
  kept, unparsable, mis-keyed, unknown-schema, and invalid-result documents being discarded
  and recomputed, an otherwise valid document keyed to another point, data version, kind, or
  analysis version being discarded, `clear_point` removing one point's analyses only and
  raising a deletion failure instead of reporting success, invalid keys and digests being
  rejected, and the synchronous memo keying by point, data version, and canonical request.
- `tests/test_cache_identity_contract.py`, `tests/test_cache_fingerprint_injectivity.py`,
  `tests/test_caching_correctness.py`, `tests/test_cache_unification.py`,
  `tests/test_graph_fingerprint_cached.py`, and `tests/test_hashing.py` cover canonical
  encoding, injectivity, field completeness, versioning, live switches, utility files,
  memoisation, and shared primitive behaviour.
- `tests/test_lru_cache.py` covers entry/byte/TTL eviction, pins, oversized retention,
  non-promoting reads, and concurrency.
- `tests/test_stat_gated_cache.py` covers hit/reload, LRU bounds, single flight, moving
  gates, exceptions, clear, and load-gate reclamation.
- `tests/test_input_cache_route.py` covers an API Input's status before and after a build,
  the capped worker build and its refresh, stale tables after a source change, clearing
  (and its refusal while building), the per-table building probe, worker failure, and
  config validation; `tests/test_json_cache_corrupt_and_errors.py`,
  `tests/test_json_cache_coverage_uplift.py`, and `tests/test_json_cache_mut_witnesses.py`
  cover the inference route's errors and path confinement.
- `tests/performance/test_cache_identity_perf.py` records bounded LRU/stat-gate and lineage
  key performance evidence.
