# IO Layer — High-Level Specification

## Purpose

Haute pipelines read tabular data from disk (CSV/JSON/NDJSON/Parquet and,
through the newer format registry, a much wider polars surface) and write
results back to disk. Every one of those boundaries needs the same set of
guarantees regardless of which node type or code path triggers it: paths
must be validated against traversal/URL injection, execution profiles that
promise bounded memory must never be silently handed an unbounded read or a
full in-memory collect, and a write must never leave a reader observing a
half-written file. The io-layer is the shared foundation that gives every
data-reading and data-writing node in Haute those guarantees in one place,
instead of each node type re-implementing (and potentially getting wrong)
its own version.

It also owns the small set of cross-cutting filesystem concerns that don't
belong to any one node type: resolving a user-facing relative path against
either the project root or a pipeline's own directory, warning about
case-ambiguous paths that will silently break when a checkout moves between
case-sensitive and case-insensitive filesystems, and discovering which
`.py` files in a project are pipeline entry points.

## Scope

In scope:
- Reading a configured flat-file data source (CSV/JSON/NDJSON/Parquet) into
  a Polars `LazyFrame`, with column projection, column-existence validation,
  declared-schema validation, and bounded-memory-profile enforcement.
- The polars I/O format registry that maps a `dataInput`/`dataOutput` node
  config to one concrete polars `read_*`/`scan_*`/`write_*`/`sink_*`
  invocation, with its argument surface derived from an extracted interface
  schema rather than hand-typed.
- A struct/list/nested-capable dtype codec for schema declarations carried
  in node configs as JSON.
- Atomic file-write primitives (temp-file-then-rename) and a `Writer`
  context manager built on them.
- Streaming collect/sink helpers that give bounded-memory execution
  profiles a typed, fail-loud contract instead of a silent broad collect.
- Runtime path resolution for user-facing relative paths (project-root vs.
  pipeline-directory candidates) and a case-ambiguity audit for paths that
  will not survive a filesystem-sensitivity change.
- Pipeline file discovery for the CLI and server (finding `.py` files that
  define a `haute.Pipeline`).
- Loading external non-tabular objects (JSON, joblib, pickle, CatBoost
  models) with content-hash-keyed memoisation.

Out of scope (owned elsewhere):
- Fetching, caching, and reading back Databricks-backed tables — see
  [databricks-io](../databricks-io/high-level.md), which depends on this
  component's Parquet-metadata helper but owns its own cache lifecycle.
- Deciding *which* execution profile applies to a running node, chunk
  planning, and the graph executor that calls into this component's
  readers/writers — see execution-engine.
- The FastAPI HTTP layer, request/response schemas, and node-config
  validation endpoints that sit in front of this component — see
  server-api.
- The restricted-deserialization primitives this component calls
  (`safe_unpickle`, `safe_joblib_load`, `validate_project_path`) — see
  sandbox-security, which owns their implementation; this component only
  calls them from `load_external_object`.
- Generated pipeline body code that calls
  `read_polars_input_from_config`/`write_polars_output_from_config` — see
  codegen for what generates those call sites.

## Behaviour

- `read_source` dispatches purely on file extension
  (`.csv`/`.json`/`.jsonl`/`.parquet`); any other extension raises
  `ValueError` before any file is opened.
- Plain JSON has no polars lazy-scan entry point, so it is the one format
  that is inherently eager: reading it for any execution profile other than
  `preview_eager`/`deploy_live` raises `BoundedMemoryUnsupportedError`
  before parsing begins.
- CSV sources additionally require a full declared schema
  (`schema_overrides`/`dtypes`/`column_dtypes`/`schema` in the source
  config) for every column that will be read, for any execution profile
  outside `preview_eager`/`deploy_live` — without declared dtypes, `scan_csv`
  would infer types by reading the data itself, which those profiles forbid.
- Every file-backed `dataInput` path handed to this component is rejected up front if it looks like a URL
  (`scheme://...`) or contains a `..` path segment. `dataOutput` receives an
  already-resolved filesystem target from the executor; the registry itself
  checks only that its configured target is non-empty. The generated-code
  wrapper resolves relative output paths against the pipeline directory.
- The format registry (`FORMATS`) enumerates every polars I/O format Haute
  exposes to `dataInput`/`dataOutput` nodes — CSV, JSON, NDJSON, Parquet,
  Arrow IPC (file and stream), Avro, Excel, ODS, text lines, database (URI),
  Delta Lake, Iceberg, and inline records — recording which of read/scan
  and write/sink each supports, which third-party engine packages reading
  or writing it needs, and which config keys belong to the node's own
  fields (`path`, `table`, `uri`, `query`, `records`, …) rather than the
  free-form `arguments` object.
  > NOTE: unlike `read_source`, the registry never sniffs a file extension
  > to choose a format — dispatch is always the explicit `format` config
  > key. Extensions on `IoFormat` are advisory metadata for pickers/editors
  > only.
- The set of argument names a node config may put in `arguments` for a
  given polars callable is derived from `_polars_io_arguments.json`, a
  machine-extracted record of the installed polars' I/O function
  signatures, minus a few excluded classes (private/underscore args,
  remote-storage args, object-valued args, and — for `sink_*` — the
  execution-owned `lazy`/`engine`/`optimizations` args). An unknown
  argument name fails loudly, naming both the polars callable and the
  argument names that are actually allowed.
- Struct/List/Array/Decimal/Datetime/Duration/Enum/Categorical dtypes in a
  node's schema declaration decode through a dedicated JSON grammar
  (`_polars_dtypes.parse_dtype`) whose encoder (`dtype_to_spec`) produces a
  canonical spec that decodes to the same dtype (aliases need not preserve
  their original spelling), so a config editor can round-trip semantics.
- The explicitly atomic write surfaces — `atomic_write_bytes`/`_text`, the
  `Writer` context manager, and `_polars_utils.atomic_write` — stage to a
  sibling temporary file and rename it onto the target. The
  `streaming_sink`/`bounded_sink`/`best_effort_sink` family uses that staging
  path when the target's parent already exists; if it does not, the helper
  calls Polars on the target directly so the missing-parent error surfaces.
  `_file_ops` gives concurrent writers unique temp names;
  `_polars_utils.atomic_write` uses one fixed `.parquet.tmp` sibling and
  therefore assumes a single writer per destination.
  Direct registry calls (`write_polars_output`) invoke the selected Polars
  `write_*`/`sink_*`
  method on the caller-supplied path and do not add another atomic wrapper;
  generated `write_polars_output_from_config` calls also create the target's
  parent directories before that direct write.
- Bounded-memory collection (`streaming_collect`, `bounded_collect_batches`,
  `bounded_sink`) never silently widens to a full in-memory collect when
  Polars cannot honour streaming execution — it raises a typed
  `BoundedMemoryUnsupportedError` instead. Only functions whose names say
  so fall back to an eager collect: `best_effort_sink` requires its caller
  to pass `allow_broad=True` explicitly or it raises `ValueError` before
  any I/O; `safe_sink` is a fixed-`allow_broad=True` convenience wrapper
  around it with no `allow_broad` parameter of its own, so it always
  permits the fallback.
- `resolve_runtime_file_path` reconciles a user-facing relative path (as
  reported by a GUI file browser rooted at the project) against two
  candidate roots — the project root and the owning pipeline's directory —
  preferring whichever candidate exists on disk, and preferring the
  project-root candidate when both exist or neither does (unless the
  caller asks for pipeline-preference).
- `warn_if_case_ambiguous` logs — but never blocks — when a resolved path
  has a case-equivalent sibling on disk, since Haute pins no Unicode/case
  normalisation on user-supplied data paths: a config that resolves cleanly
  on one filesystem's case sensitivity can silently resolve to a different
  file (or fail to resolve at all) on another.
- `discover_pipelines` finds pipeline entry points by literal substring
  search for `"haute.Pipeline"` in `.py` file contents: it checks the path
  configured in `haute.toml`'s `[project].pipeline` first, then also scans
  root-level `*.py` files (excluding `__init__.py`/`setup.py`/`conftest.py`)
  and de-duplicates the configured match.
- `load_external_object` deserialises a model/JSON/pickle/joblib file and
  memoises the result keyed by `(path, content_hash, file_type,
  model_class)`, so repeated calls for an unchanged file skip re-parsing
  entirely while a changed file (different digest) is never served stale.

## Design rationale

- **Bounded memory is enforced before parse, not after.** Every check that
  a format/mode/schema combination is unsafe for a bounded-memory profile
  runs before the underlying polars call executes, so a misconfigured node
  fails fast with an actionable message (add dtypes, use NDJSON, cache to
  Parquet first) instead of erroring deep inside a lazy query plan or, worse,
  succeeding by quietly reading everything into memory.
- **The argument surface is generated, not hand-typed.** Hand-maintaining
  which polars keyword arguments a node config may set would drift
  silently the moment polars changes a function signature. Deriving the
  allow-list from an extracted schema and asserting that schema still
  matches the installed polars (see
  `tests/test_polars_io_interface_contracts.py`) turns a silent behaviour
  change into a CI failure instead of a user-facing surprise.
- **Chunkability and remote storage are opt-in exclusions, not
  afterthoughts.** Nothing in the format registry registers with the
  chunking machinery by default — a new format is not chunkable until
  something explicitly says so. Remote-IO arguments
  (`storage_options`, `credential_provider`, `retries`, `file_cache_ttl`)
  are always excluded from what a node config can set, keeping the
  local-path-only security posture uniform across every format rather than
  something each format has to remember to enforce.
- **Atomicity is an explicit primitive, not an implicit registry promise.**
  File/config saves use a temp-then-rename boundary, as do profiled streaming
  sinks when their target parent exists. The generic registry stays a faithful
  adapter to Polars and writes to the exact resolved target it is given, so
  callers that require atomic publication must supply a staged target or use
  an atomic helper. Same-target concurrent Polars sinks additionally require
  caller-side serialisation because their shared temp filename is not a
  concurrent-writer protocol.
- **Fail loud over silent fallback.** `_file_ops` writes never create parent
  directories, and the profiled sink family deliberately takes its direct,
  normally failing path when a parent is absent. `_polars_utils.atomic_write`
  itself and generated registry-output wrappers do create parents as part of
  their explicit contracts. Unsupported extensions and dtypes raise
  immediately; a bounded collect that cannot stream raises a typed
  error instead of transparently materialising a potentially huge frame.
  Of the two functions that *do* offer a broadening fallback,
  `best_effort_sink` requires the caller to pass `allow_broad=True`
  explicitly (so the expensive path is never reached by accident), while
  `safe_sink` is a thin wrapper that always calls it with
  `allow_broad=True` — a caller of `safe_sink` has already opted into the
  broadening fallback by name.
- **No path normalisation, only warning.** Case-folding or Unicode-
  normalising a user-supplied path would change which file a config
  resolves to — a correctness risk larger than the portability problem it
  would solve. Instead the case-ambiguity audit surfaces the risk as a
  warning at access time and leaves the resolution behaviour untouched.

## Interactions

- Depended on by [databricks-io](../databricks-io/high-level.md), which
  reuses this component's `read_parquet_metadata` helper to build its
  cache-info responses.
- Depended on by execution-engine, which calls `read_source`/
  `read_data_source`/`read_polars_input`/`write_polars_output` (and the
  streaming collect/sink helpers) as the actual data-access boundary during
  pipeline execution, and by the chunk-planning machinery via the format
  registry's `bounded_read`/`needs_schema_when_bounded` flags.
- Depended on by codegen: generated pipeline bodies call
  `read_polars_input_from_config`/`write_polars_output_from_config`
  against a sidecar JSON file the codegen step writes alongside the
  generated pipeline module.
- Depended on by server-api for config-time validation (`format_for_config`,
  `validate_arguments`, `registry_capabilities`) and for resolving
  file-browser-supplied paths (`resolve_runtime_file_path`).
- Calls into sandbox-security's `validate_project_path`, `safe_unpickle`,
  and `safe_joblib_load` from `load_external_object`, and into
  `haute._mlflow_io._load_catboost_model` for CatBoost model loading — both
  imported lazily to avoid hard dependencies.
- Depended on by caching for the underlying atomic-write and streaming-sink
  primitives that back cache-file writes.

## Failure model

- Unsupported file extensions, malformed source paths (URL-shaped or
  containing `..`), and malformed projection-column arguments raise
  `ValueError` immediately, before any file is opened.
- Declared-schema mismatches (missing columns, dtype mismatch, unsupported
  dtype name, malformed dtype spec) raise `SchemaMismatchError`, always
  naming the source path and the specific column(s)/mismatch involved.
- Any attempt to read/write in a way that a bounded-memory execution
  profile cannot support (eager JSON, CSV without full declared dtypes, an
  eager-only format in `scan` mode, a streaming sink Polars cannot honour)
  raises `BoundedMemoryUnsupportedError`, always naming the format and
  profile.
- Format-registry config errors (unknown format, unsupported mode, unknown
  argument name, missing required source/target field, a missing engine
  package) raise `PolarsIoConfigError` (a `ValueError` subclass) with a
  message naming the offending config key and, where relevant, the set of
  valid alternatives.
- File-write failures (permission errors, disk full, a Windows rename
  blocked by a concurrent reader) propagate as the underlying `OSError`
  subtype. `_file_ops`'s unique-temp primitives protect the target from torn
  writes even with concurrent writers; `_polars_utils.atomic_write` protects
  ordinary single-writer publication but does not make concurrent writes to
  the same destination safe.
- None of these errors are swallowed or converted into a default value —
  every failure surfaces to the caller (executor, route handler, or CLI)
  for it to report or convert into an HTTP status as appropriate.

## Polars backend contracts (0.6.0)

Remaining I/O improvement work is tracked in the
[I/O layer roadmap](../../roadmap/io-layer.md).

- Streaming incompatibility is classified only by a committed, versioned table keyed by
  the supported Polars version (`1.39.3`), concrete exception class, and a verified anchored
  full-message signature. Initial entries must be harvested from actual Polars 1.39.3
  failures and committed to this spec in a spec-only evidence step before classifier code
  starts; an empty verified table is valid, while invented substring rules are forbidden.
  Only a listed tuple may be converted or permit the existing caller's documented fallback policy.
  An unknown Polars version, exception type, or full-message signature propagates unchanged
  and never triggers a broad/eager fallback.
- Where Polars exposes supported byte and column counters, execution reports their measured values. A counter unavailable for a given operation is represented explicitly as unavailable, never as a guessed zero or estimate.
- The repository audit for this plan confirms that `safe_sink` and `best_effort_sink`
  are private underscored-module symbols with no production caller, package export, or
  supported public-documentation contract. Remove them; if contrary evidence appears before
  the batch, implementation stops and this contract is revised first. The 0.6 pre-1.0 release notes name both
  symbols; there is no deprecation shim because the fallback behaviour is unsafe and the
  API is pre-1.0. Bounded execution retains its fail-loud guarantee.
- CSV recount changes are deferred behind an explicit benchmark and semantic-equivalence gate; no recount optimisation is part of this approved change.

Non-goals: changing registry format coverage, introducing implicit eager fallbacks, or inventing counter values for operations that cannot report them. Required tests cover every committed classifier tuple; unknown version/type/signature propagation; full-match negatives containing `downstream`, `upstream`, or `stream_id`; each counter's present/unavailable state; and, subject to the audit gate, the absence of the retired sink APIs. Any future CSV recount proposal must first add representative correctness and performance benchmarks.

## Approved change contract — 0.7.0 data I/O convergence

Remaining data-I/O improvement work is tracked in the
[I/O layer roadmap](../../roadmap/io-layer.md).
This section specifies approved future behaviour; the present-tense sections above continue to
describe the shipped implementation until the 0.7.0 release reconciles them.

### Canonical node surface

- `dataInput` becomes the only authored tabular-source node type and `dataOutput` the only
  authored persistence node type. A graph may contain any number of either node. `apiInput`
  remains the live request boundary and `output` remains JSON response assembly; neither is
  merged with tabular data I/O.
- `dataSource` and `dataSink` are removed outright from the node enum, decorator API, registry,
  parser, code generator, sidecar-folder map, frontend palette/editor registry, assistant
  catalogue, examples, and tests. This is a deliberate pre-1.0 hard cutover: there are no
  aliases, deprecation shims, compatibility parsing, or migration utilities. Repository-owned
  pipelines which contain either removed node are reset to a blank graph rather than converted.

### Input providers, formats, and code

- A `dataInput` first selects an input group: **File**, **Database**, **Lakehouse**,
  **Databricks**, or **Inline**. Registry-backed groups then select a concrete format and
  supported `scan`/`read` mode. Group membership, ordering, labels, modes, arguments, optional
  engines, direct-batching support, and cache-build support all come from one backend capability
  registry; the frontend never owns a parallel format list.
- File formats cover the Polars-backed file surface. Database inputs configure a connection
  reference plus query. Lakehouse inputs cover Delta Lake and Iceberg. Databricks retains its
  dedicated warehouse/catalog/schema/table browser, optional validated query fragment, and
  explicit fetch controls; it is an input provider, not a pretend Polars file format. Inline
  records remain config-bounded and have no disk-cache lifecycle.
- Every `dataInput` exposes the optional Polars editor retained from `dataSource`. Its code runs
  after the direct source or Parquet snapshot is opened and before the frame is handed
  downstream. Source snapshots contain source data, not the result of that user code, so editing
  code never triggers a remote refetch.
- A persisted config is a strict discriminated shape. `inputType` selects the active branch;
  `format`/`mode`, locator/query fields, `arguments`, `cacheMode`, and `code` must be valid for
  that branch. Switching groups is one atomic config replacement which removes inactive branch
  keys; undo restores the previous config.

### Direct, cached, and chunked reads

- Chunkability is capability-driven, never inferred merely from a file extension or from a
  callable returning `LazyFrame`. Each registry entry declares independently whether it supports
  a direct bounded scan, a bounded snapshot build, an admitted-eager snapshot build, and reading
  an existing snapshot. A missing declaration means unsupported.
- Direct chunk execution uses the common bounded batch iterator only for formats whose committed
  Polars-version contract and format-specific integration tests prove ordered bounded iteration.
  CSV and Parquet retain their existing support; every other scanner-backed format (including
  NDJSON, IPC, text lines, Delta Lake, and Iceberg) is enabled only when that same evidence exists.
- Snapshot mode materialises one source generation as Parquet and thereafter gives every
  provider the same projection-capable, chunk-readable runtime boundary. It does not make an
  eager cache builder bounded: JSON, Excel, Avro, ODS, database drivers, or any other importer
  must separately prove incremental bounded publication or be classified
  `admitted_eager`/`unsupported`. An unsupported bounded request fails before parsing or network
  access; it never broad-collects and then calls the result “chunked”.
- Databricks and database inputs execute from an explicitly built snapshot and never contact the
  remote system during ordinary preview, batch, CI, or deploy execution. Lakehouse and local-file
  inputs use direct scans by default and may opt into a snapshot when the provider declares it.
  Inline input always executes directly.
- The optional Polars body participates in the same row-local proof used for ordinary `polars`
  nodes. Proven row-local code may run per chunk. Global operations such as joins, sorting,
  grouping, windows, whole-frame aggregation, or collection are never silently evaluated once
  per chunk; they use a separately admitted lazy/materialised strategy or cause chunk planning to
  fail with a typed explanation.

### Source snapshots

- Snapshot build, refresh, status, progress, clear, publication, and metadata are provided by the
  shared caching component. A build writes a unique staged generation and atomically publishes it
  only after the Parquet artifact and signed metadata are complete. Cancellation, timeout,
  connector failure, schema failure, or an unprovable retry leaves the previous generation
  readable and unchanged.
- Cache identity includes the provider, normalised safe locator, table or path, complete query,
  format, source-affecting arguments, schema declarations, and connection-reference identity.
  It excludes secrets and post-input Polars code. Two queries against one table cannot collide,
  fixing the current Databricks table-only identity.
- Status distinguishes snapshot readiness from external freshness. It reports the identity,
  generation, rows, columns/schema, bytes, build time, and provider revision/freshness token when
  one is available. Absence of such a token is `unknown`, never guessed fresh from a timestamp.
  Execution may use an explicitly selected ready snapshot whose freshness is unknown; it may not
  use a missing, corrupt, identity-mismatched, or stale local-file snapshot.
- Connection credentials and storage credentials are resolved from named environment/secret
  references. Raw secret-bearing URIs, tokens, and credential objects are rejected from node
  sidecars, capability payloads, cache identities, metadata, logs, and error responses.

### Unified outputs

- `dataOutput` uses the same backend-defined group labels and format catalogue as `dataInput`,
  filtered by write capability. UI symmetry does not invent backend symmetry: input-only formats
  such as inline records, or a format with no writer, are not offered as working outputs.
  Databricks is absent from the output groups until a real writer with a specified publication
  contract exists.
- The output editor contains destination group, format, supported `sink`/`write` mode,
  destination path/table fields, format-specific arguments, optional-engine diagnostics, and the
  explicit **Write** action/status retained from `dataSink`. It has no Polars code editor.
  Preview, trace, graph save, and ordinary node execution never write.
- Native sink formats consume the lazy plan with bounded Polars sinks. Writer-only formats use
  admitted materialisation and say so in capabilities and execution diagnostics. Local
  single-file outputs always write to a unique sibling staging path and atomically replace the
  destination. Transactional lakehouse/database writers use their commit boundary; a
  non-transactional destination must declare that limitation and is never presented as atomic.
  Overwrite, append, replace, and provider-supported upsert semantics are explicit rather than
  inferred.

### Failure, non-goals, and acceptance

- Unknown groups/formats/modes, group/format mismatches, inactive-branch keys, missing cache
  generations, cache identity mismatch, unsafe credentials, unsupported bounded reads/builds,
  non-row-local chunk code, missing engines, and unsupported publication modes fail with typed,
  actionable errors before side effects wherever possible.
- This change does not add a Databricks output writer, remote object-store credentials, implicit
  cache refresh, automatic network access during execution, a per-chunk interpretation of global
  Polars code, or fake parity for formats Polars cannot write.
- Acceptance requires registry-contract tests for every provider/format leg; direct versus
  cached execution equivalence; boundedness tests for direct scan and cache build independently;
  cache identity/query separation, atomic refresh and concurrent-reader tests; Polars-code
  ordering and row-local rejection tests; output atomicity and explicit-write tests; and
  end-to-end parse/save/reload tests containing only `dataInput`/`dataOutput`.
