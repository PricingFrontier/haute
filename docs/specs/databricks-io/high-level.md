# Databricks IO — High-Level Specification

## Purpose

Haute pipelines need to read tables that live in a Databricks Unity Catalog
warehouse, but running every pipeline execution against a live warehouse
connection would be slow, expensive, and would make offline/local runs
impossible. This component solves that by giving a data source node a
one-time "fetch" step: pull a table (or a denylist-screened `SELECT` fragment) out
of Databricks once, and persist it as a local Parquet file. Every
subsequent pipeline run — locally, in CI, or at deploy time — reads that
Parquet file directly with Polars, with no network dependency and full
predicate-pushdown/lazy-scan performance.

It also exposes the browsing surface (warehouses, catalogs, schemas,
tables) a GUI needs to let a user pick a table without knowing its
fully-qualified name up front, and a small progress/status API so a
long-running fetch can be polled from the UI instead of blocking a request
for its whole duration.

## Scope

In scope:
- Resolving Databricks connection credentials from the environment and
  node configuration.
- Validating a custom `SELECT` fragment with the implemented denylist,
  appending one validated fully-qualified table, and streaming the resulting
  query to a local cache file.
- Managing that local cache: locating it, inspecting its metadata,
  deleting it, and reading it back as a lazy frame for pipeline execution.
- Reporting in-progress fetch state so a caller can poll it.
- Unity Catalog browsing endpoints (warehouses/catalogs/schemas/tables)
  used to populate a table picker.

Out of scope (owned elsewhere):
- Actually reading/writing/caching parquet for non-Databricks sources —
  general Parquet/CSV/file IO lives in the [io-layer](../io-layer/high-level.md).
- Turning a cached table into a pipeline node's runtime data, request
  validation, and the provider-neutral cache HTTP lifecycle around other data sources — see
  the [server-api](../server-api/high-level.md) component.
- Deploying a *pipeline* to Databricks Model Serving (MLflow experiments,
  serving endpoints, `haute deploy` targets). That is a distinct feature — see
  [deploy](../deploy/high-level.md) and the user-facing setup guide at
  `docs/deployment/targets/databricks.md` — and is unrelated to this
  component beyond sharing the `DATABRICKS_HOST`/`DATABRICKS_TOKEN`
  environment variables.

## Behaviour

- A table is identified by its fully-qualified, three-part name
  (`catalog.schema.table`), optionally with each part backtick-quoted.
  Any other shape is rejected before it reaches Databricks or the
  filesystem.
- Fetching a table streams it from Databricks in batches and writes it to
  a local Parquet cache keyed by table name. The cache is a strict
  invariant boundary: either the fetch produces a complete, correctly
  typed cache file, or no cache file is produced (and any previous cache
  for that table is left untouched) — there is no partially-written or
  silently-truncated state a pipeline can read.
- Table name spelling is normalised for caching purposes: differing case
  and backtick-quoting of the same table resolve to the same cache file,
  since Databricks treats those as the same table.
- While a fetch is running, its progress (rows/batches/elapsed time) can
  be polled by table name.
  > NOTE: progress is one slot per literal table spelling, not one slot per
  > fetch. Concurrent fetches using the same spelling overwrite that slot and
  > either fetch's `finally` block can clear it while the other is still active;
  > file publication remains safe, but progress is not reliable for this case.
- Reading a table for pipeline execution only ever reads the local cache;
  it never talks to Databricks. If nothing has been fetched yet, this
  fails clearly rather than fetching implicitly.
- The cache can be inspected (row/column counts, size, columns, fetch
  time) or deleted independently of fetching.
- A custom truthy query fragment can be supplied instead of `SELECT *`. It
  must begin with `SELECT` and pass the semicolon/comment/dangerous-keyword
  checks before ` FROM <validated-table>` is appended.
  > NOTE: the validator does not prove that the fragment contains no earlier
  > `FROM`; a nested subquery can currently pass and be followed by the
  > appended table clause. This is a documented validation gap, not a promise
  > that arbitrary `SELECT` text is safely scoped to one table.
- The catalog-browsing endpoints (warehouses, catalogs, schemas, tables)
  are read-only reflections of what the configured Databricks workspace
  reports, used to drive a GUI table picker; they do not touch the local
  cache.

## Design rationale

- **Fetch-once, run-many.** Pipeline execution (including deployed/scoring
  paths) must be fast and must not depend on warehouse availability or
  cost; a Databricks SQL Warehouse round-trip per pipeline run would be
  both. Splitting "fetch" (interactive, explicit, potentially slow) from
  "read" (fast, always-local) matches how the GUI is used: a user connects
  a data source node, clicks Fetch once, then iterates on the pipeline
  against the cached data indefinitely.
- **Streaming batches, not one big table.** Data is pulled via
  `cursor.fetchmany_arrow` in bounded batches and written incrementally
  with a Parquet writer, so memory use is independent of table size —
  fetching a multi-billion-row table does not require holding it all in
  RAM.
- **Atomic replace, unique temp file per fetch.** The fetch writes to a
  per-fetch temporary file and only replaces the real cache file once the
  whole fetch has succeeded. This means a fetch that fails, is killed, or
  races another fetch of the same table can never leave a corrupt or
  half-written file where a pipeline expects a valid cache — worst case,
  the cache is unchanged or the query has to be re-run.
- **Fail loud on unprovable completeness, never guess.** Two situations
  where the code could plausibly "fill in" a value are instead treated as
  hard failures: a retried batch fetch where the underlying connector may
  have already consumed (and thus lost) rows before raising, and a
  zero-row result whose terminating batch carries no column schema. In
  both cases the cache is not written and the caller is told to re-run the
  fetch, rather than caching a table that is quietly wrong (missing rows,
  or a fabricated all-string schema).
- **Defence in depth against SQL injection**, given the query text can
  originate from a GUI-editable node config rather than a trusted script:
  the table name is validated by a strict grammar, the optional custom
  query must start with `SELECT`, must not contain statement terminators
  or SQL comments, and is checked against a keyword denylist, before ever
  being concatenated into a SQL string.
- **Case/quoting-insensitive cache identity.** Because Databricks resolves
  unquoted identifiers case-insensitively, and backticks are just quoting
  rather than a different table, the cache key canonicalises both away.
  Without this, the same table could double-cache under two spellings, or
  a user could "clear cache" under one spelling and still have a stale
  file served under another.

## Interactions

- Depends on the [io-layer](../io-layer/high-level.md) for the underlying
  Parquet metadata reading helper used to build cache-info responses.
- Depends on `haute._env` for reading numeric environment configuration
  (the fetch response timeout) and `haute._logging` for structured
  logging.
- Is consumed by the [server-api](../server-api/high-level.md) layer: the
  browsing routes in this component are mounted under `/api/databricks/*`, while the shared
  `/api/input-cache/*` lifecycle publishes and reports Databricks snapshots.
- Is consumed by the pipeline execution engine at run time via
  `read_cached_table`, which is how a Databricks-backed data source node
  actually gets its data during a pipeline run — see the
  [execution-engine](../execution-engine/high-level.md) component.
- Is consumed by the Databricks picker/fetch controls in
  [frontend-node-editors](../frontend-node-editors/high-level.md). The UI
  cascades warehouse/catalog/schema/table selection, polls the literal table
  name used for progress, and treats cache-status/fetch response shapes as
  runtime-validated API contracts.
- Depends on the third-party `databricks-sql-connector` package for the
  data-plane fetch (imported lazily inside `fetch_and_cache`) and the
  `databricks-sdk` package for the control-plane browsing endpoints
  (imported lazily inside `_get_databricks_client`); both are optional
  extras, not hard dependencies of the package.

## Failure model

- Missing or incomplete Databricks credentials (`DATABRICKS_HOST`,
  `DATABRICKS_TOKEN`, and an HTTP path from either the node config or
  `DATABRICKS_HTTP_PATH`) raise a `DatabricksConfigError` that lists every
  missing item, rather than failing on the first one found.
- An invalid table name (wrong shape, injection attempt) raises `ValueError`
  before any SQL is executed or any file path is computed.
- A custom query that is not a plain `SELECT`, contains a semicolon or SQL
  comment marker, or contains a denylisted keyword raises `ValueError`
  before being combined with the table name.
- Reading a table that has never been fetched raises `CacheNotFoundError`
  with a message that tells the user to fetch it first — there is no
  silent auto-fetch.
- A retried batch fetch that cannot prove every row the cursor consumed
  was actually received locally raises `FetchIntegrityError`; no cache is
  written.
- A zero-row fetch whose terminating batch carries no column schema
  (which no legal `SELECT` should produce) raises `FetchIntegrityError`
  rather than caching a schemaless or fabricated-schema table.
- All other errors during a fetch (connector errors, transport failures
  after retries are exhausted) propagate as-is; the temp file is cleaned
  up and fetch-progress state is cleared before the exception surfaces.
- At the HTTP layer, table-name validation failures surface as `400` and
  missing SDK/credentials as `503` on the routes that validate up front
  (fetch-progress, cache-status, delete-cache, and the Unity Catalog
  browsing routes, via `_validate_table_param`/`_get_databricks_client`);
  `ImportError` for the missing SQL connector surfaces as `400` and a fetch
  exceeding the configured response timeout as `504` (with the underlying
  fetch left running in the background — see
  [server-api](../server-api/high-level.md) for the shared timeout helper)
  on the fetch route specifically; and any other unexpected exception —
  including a malformed table name or missing credentials raised from
  *inside* the fetch route's own `fetch_and_cache` call, which is not
  specially caught there — surfaces as a generic `500` that does not leak
  the underlying error message to the client (it is logged server-side
  instead). See the low-level spec's `> NOTE:` on this fetch-route
  inconsistency.

> NOTE: `_get_credentials` raises `RuntimeError` if `resolved_http_path`
> is somehow `None` after the missing-credentials check has already
> passed — this is an internal consistency assertion, not a
> user-reachable error path (the preceding check guarantees the http path
> is truthy or the function has already raised).

## Approved change contract — 0.7.0 unified Databricks input

Remaining Databricks I/O improvement work is tracked in the
[I/O layer roadmap](../../roadmap/io-layer.md)
and the shared [data I/O](../io-layer/high-level.md#approved-change-contract-070-data-io-convergence)
and [source-snapshot](../caching/high-level.md#approved-change-contract-070-shared-input-snapshots)
contracts.

- Databricks becomes the dedicated `inputType="databricks"` branch of `dataInput`; there is no
  `dataSource` node. The editor retains warehouse/catalog/schema/table browsing, optional
  validated query, fetch/refresh, progress, status, and clear.
- Databricks supplies a bounded Arrow-batch builder to the shared source-cache store and stops
  owning cache naming, publication, metadata, progress storage, and cache routes. Normal pipeline
  execution obtains only the validated shared Parquet generation and never connects to a
  warehouse.
- Cache identity includes the canonical fully-qualified table, complete validated select
  fragment, warehouse/connection-reference identity, and source-affecting options. It is no
  longer keyed by table spelling alone. Two nodes selecting different rows/columns from one table
  have independent snapshots; equivalent case/backtick spellings still canonicalise together
  when every other identity field matches.
- The builder must either obtain a provider snapshot/version token or report external freshness
  `unknown`. A long fetch must use a consistent Databricks result snapshot; if retries or
  connector behaviour make completeness/consistency unprovable, publication fails and the
  previous generation remains current.
- The optional `dataInput` Polars body runs after the shared Parquet snapshot is scanned and is
  excluded from snapshot identity. Cached Databricks inputs therefore receive the same projection
  and chunked-batch behaviour as every other valid source snapshot.
- Existing query validation remains fail-loud and is strengthened so the configured value is a
  complete, unambiguous read-only selection contract; the known nested-`FROM` gap cannot be
  carried into the shared cache identity. Credentials continue to resolve only from named
  environment/secret references and never enter node or cache metadata.

There is no Databricks `dataOutput` in this release. The output UI may add that group only after a
real writer, boundedness model, and transactional publication contract are separately approved.
Acceptance covers picker continuity, query-distinct identities, canonical table equivalence,
bounded batch publication, snapshot consistency/retry failure, old-generation preservation,
credential redaction, shared route payloads, and offline/CI/deploy reads with zero Databricks
calls.
