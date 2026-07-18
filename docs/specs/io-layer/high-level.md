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
- Every path handed to this component — a legacy `dataSource` `path`, or a
  `dataInput`/`dataOutput` node's `path` — is rejected up front if it looks
  like a URL (`scheme://...`) or contains a `..` path segment.
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
  (`_polars_dtypes.parse_dtype`) whose encoder (`dtype_to_spec`) is its
  exact inverse, so a config editor can round-trip what it displays.
- Every file write this component performs — `atomic_write_bytes`/`_text`,
  the `Writer` context manager, `_polars_utils.atomic_write`, and the
  streaming sinks — commits via a sibling temp file renamed onto the
  target. A reader can never observe a torn or partially-written file; it
  either sees the complete previous payload or the complete new one.
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
  search for `"haute.Pipeline"` in `.py` file contents: it first tries the
  path configured in `haute.toml`'s `[project].pipeline`, then falls back
  to root-level `*.py` files (excluding `__init__.py`/`setup.py`/
  `conftest.py`).
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
- **Atomic write, always, not "usually".** Every write path in this
  component commits by rename rather than in-place write. This closes the
  torn-write window regardless of which layer initiates the write (a
  user-facing config save, a pipeline checkpoint, a streaming sink), rather
  than relying on each call site to remember to do it correctly.
- **Fail loud over silent fallback.** Parent directories are never
  auto-created by the write primitives; unsupported extensions and dtypes
  raise immediately; a bounded collect that cannot stream raises a typed
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
  subtype; the write primitives guarantee only that the *target* file is
  never left partially written, not that the write eventually succeeds.
- None of these errors are swallowed or converted into a default value —
  every failure surfaces to the caller (executor, route handler, or CLI)
  for it to report or convert into an HTTP status as appropriate.
