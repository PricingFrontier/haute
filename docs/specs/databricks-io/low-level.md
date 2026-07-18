# Databricks IO — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_databricks_io.py` | Credential resolution, SQL/table-name validation, cache path management, streaming fetch-and-cache, cached-table read-back, in-memory fetch-progress tracking. |
| `src/haute/routes/databricks.py` | FastAPI router (`/api/databricks/*`) exposing Unity Catalog browsing (warehouses/catalogs/schemas/tables) and wrapping the above module's fetch/cache/progress functions as HTTP endpoints. |

Related but external to this component: `src/haute/routes/files.py` defines
`/api/schema/databricks`, which calls `haute._databricks_io.cache_info`
directly to preview a cached table's schema — see
[server-api](../server-api/high-level.md).

## Key types and data structures

- `DatabricksConfigError(HauteError)` (`_databricks_io.py`) — missing
  connection credentials.
- `CacheNotFoundError(HauteError)` — `read_cached_table` called for a table
  with no cache file.
- `FetchIntegrityError(HauteError)` — a fetch could not prove the cache it
  was about to write is complete/correctly typed; always means "no cache
  was written, safe to retry."
- `CacheInfoDict` (`TypedDict`) — `path`, `table`, `row_count`,
  `column_count`, `columns: dict[str, str]` (Arrow type strings by column
  name), `size_bytes`, `fetched_at` (mtime as a float). Returned by
  `cache_info()`.
- `FetchResultDict(CacheInfoDict)` — adds `fetch_seconds`. Returned by
  `fetch_and_cache()`.
- Module-level fetch-progress state: `_fetch_progress: dict[str,
  dict[str, object]]` guarded by `_fetch_lock: threading.Lock`, keyed by
  the *raw* (non-canonicalised) table string passed to `fetch_and_cache`/
  `fetch_progress`. Values have shape `{"rows": int, "batches": int,
  "elapsed": float}`.
- Pydantic response/request models used by the routes live in
  `src/haute/schemas.py`: `WarehouseItem`/`WarehouseListResponse`,
  `CatalogItem`/`CatalogListResponse`, `SchemaItem`/`SchemaListResponse`,
  `TableItem`/`TableListResponse`, `FetchTableRequest`
  (`table`, `http_path: str | None`, `query: str | None`),
  `FetchTableResponse` (mirrors `FetchResultDict`), `FetchProgressResponse`
  (`active: bool`, `rows`/`batches`/`elapsed` defaulting to `0`/`0`/`0.0`),
  `CacheStatusResponse` (`cached: bool`, plus the `CacheInfoDict` fields
  each defaulted so an "uncached" response can omit them).

## Control flow

### Credential resolution — `_get_credentials(http_path=None)`

1. Read `DATABRICKS_HOST` / `DATABRICKS_TOKEN` from the environment;
   resolve `http_path` from the argument, falling back to
   `DATABRICKS_HTTP_PATH`.
2. Collect every missing piece into one list and raise
   `DatabricksConfigError` naming all of them at once if any are missing.
3. Normalise `host`: strip a trailing slash, then strip a leading
   `https://` or `http://` (the SQL connector wants a bare hostname).
4. Return `(host, token, resolved_http_path)`.

### Table-name and query validation

- `_TABLE_NAME_RE` requires exactly three dot-separated parts, each
  `[\w-]+`, each optionally wrapped in a single pair of backticks (backtick
  quoting is not required to match on either side of each part
  independently).
- `_validate_select_clause(query)` (only called when a custom `query` is
  supplied) enforces, in order: starts with `SELECT` (case-insensitive) →
  no `;` → no `--` → no `/*` → no dangerous keyword from
  `_DANGEROUS_SQL_RE` (`DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|EXEC|
  EXECUTE|CREATE|GRANT|REVOKE|UNION|LATERAL`).

### Cache path resolution — `_canonical_table` / `_cache_path_for`

1. `_canonical_table(table)` strips all backticks and casefolds — this is
   the identity used for the cache path only, not for what's sent to
   Databricks.
2. `_cache_path_for` replaces `.`, `/`, `\` in the canonical string with
   `_` to get a single safe path component, resolves `project_root /
   CACHE_DIR` **once**, and asserts the resulting file's parent equals
   that resolved cache dir (defence against any path-traversal the
   character replacement might have missed) and that the safe name is
   non-empty; otherwise raises `ValueError`.
3. `cached_path`, `clear_cache`, `cache_info` all build on
   `_cache_path_for`; `cached_path` returns `None` unless the file exists,
   `clear_cache` unlinks it and returns whether it existed, `cache_info`
   delegates to `haute._polars_utils.read_parquet_metadata` for row/column
   counts, per-column Arrow types, size, and mtime.

### Fetch — `fetch_and_cache(table, http_path=None, query=None, project_root=None, batch_size=100_000)`

1. Import `pyarrow`, `pyarrow.parquet`, and `databricks.sql` lazily
   (`ImportError` here is what the route layer turns into a `400`).
2. Resolve credentials, validate `table` against `_TABLE_NAME_RE`.
3. Build `sql_query`: `f"{select_clause} FROM {table}"` where
   `select_clause` is either the validated custom query (with a trailing
   `;` stripped) or `"SELECT *"`.
4. Compute the cache path, create its parent directory, and create a
   unique per-fetch temp file with `tempfile.mkstemp(dir=parent,
   prefix=f"{stem}.", suffix=".parquet.tmp")` (exclusive creation — two
   concurrent fetches of the same table never share one temp file).
5. Register `{"rows": 0, "batches": 0, "elapsed": 0.0}` in
   `_fetch_progress[table]` before opening the connection.
6. Open `dbsql.connect(...)` and a cursor as nested context managers,
   `cursor.execute(sql_query)`, then loop:
   - Call `cursor.fetchmany_arrow(batch_size)` inside a retry loop of up
     to `_FETCH_MAX_RETRIES` (3) attempts with exponential backoff
     (`_FETCH_INITIAL_BACKOFF * 2**attempt`, sleeping via `time.sleep`).
     `KeyboardInterrupt`/`SystemExit` and `TypeError`/`KeyError`/
     `AttributeError` are re-raised immediately (never retried — the
     latter three indicate a programming/mocking error, not a transient
     one); any other exception is retried until the last attempt, which
     re-raises.
   - If any retry happened on this batch, call
     `_assert_no_rows_lost_after_retry(table=table, rows_received=row_count
     + batch.num_rows, rows_consumed=cursor.rownumber)` — see Edge cases.
   - An empty batch (`num_rows == 0`) ends the loop and is kept as
     `empty_terminator` for the zero-row-schema check below.
   - Otherwise, lazily construct a `pq.ParquetWriter` from the first
     batch's schema (zstd compression), write the batch, accumulate
     `row_count`, and update `_fetch_progress[table]` with the running
     `rows`/`batches`/`elapsed`.
7. After the loop: if a writer was opened, close it. If not (zero rows
   were ever written), fall back to writing `empty_terminator` directly
   via `pq.write_table` — but only after confirming it actually carries
   column schema (see Edge cases / Error handling).
8. `tmp_path.replace(out_path)` — atomic on both POSIX and Windows (unlike
   `Path.rename`, which raises on Windows if the destination exists).
9. On any `BaseException` during steps 6–8: close the writer if still
   open, `tmp_path.unlink(missing_ok=True)`, re-raise.
10. `finally`: pop the table's entry out of `_fetch_progress` unconditionally
    (success or failure).
11. Read the written file back through `read_parquet_metadata` to build
    the returned `FetchResultDict` (this re-derives `column_count`,
    `columns`, `size_bytes`, `fetched_at` from the actual file on disk
    rather than trusting in-memory state).

### Read — `read_cached_table(table, project_root=None)`

`cached_path(table, project_root)`; if `None`, raise
`CacheNotFoundError`; otherwise `pl.scan_parquet(p)` (lazy — no data is
read until the caller collects/executes it).

### Route layer (`routes/databricks.py`)

- `_get_databricks_client()` lazily imports `databricks.sdk.WorkspaceClient`
  (`ImportError` → `503`), reads `DATABRICKS_HOST`/`DATABRICKS_TOKEN` from
  the environment directly (not via `_get_credentials` — no `http_path` is
  needed for control-plane calls), and raises `503` if either is missing.
- `list_databricks_warehouses` / `_catalogs` / `_schemas` (needs
  `catalog` query param) / `_tables` (needs `catalog` + `schema`) each
  call the corresponding `WorkspaceClient` list method, filter out items
  with no `name` (and, for warehouses, no `id`), and map SDK objects to
  the response schemas. `TableItem.full_name` falls back to
  `f"{catalog}.{schema}.{t.name}"` when the SDK returns `None`. Each is
  wrapped in `try/except HTTPException: raise` then
  `except Exception: log + raise 500` with a shared, non-leaking detail
  message (`_INTERNAL_ERROR_DETAIL`).
- `fetch_databricks_table` (`POST /fetch`) runs `fetch_and_cache` via
  `run_blocking_with_response_timeout` (see
  [server-api](../server-api/high-level.md)) with a timeout from
  `_fetch_timeout()` (`HAUTE_FETCH_TIMEOUT` env var, default 600s,
  re-read per request so env overrides after import still apply). Maps
  `TimeoutError` → `504`, `ImportError` → `400`, re-raises `HTTPException`
  as-is, anything else → `500`.
- `get_fetch_progress` / `get_databricks_cache_status` /
  `delete_databricks_cache` all call `_validate_table_param` (same regex
  as `fetch_and_cache`, applied before touching the filesystem) then
  delegate straight to `fetch_progress` / `cache_info` / `clear_cache`.

## Edge cases and invariants

- **Concurrent fetches of the same table never corrupt the cache.** Each
  fetch stages to its own `mkstemp`-generated temp file and only the final
  `Path.replace()` touches the shared destination; whichever fetch
  finishes last wins with one complete file, never an interleaved one.
  On Windows, two concurrent replaces of the same destination may cause
  one fetch to fail with an `OSError` (loud, not silent); on POSIX both
  succeed and the file is simply overwritten.
- **`Path.resolve()` is called exactly once per `_cache_path_for` call**
  for the cache *directory*, not the child file — resolving the child
  again would be unsafe under concurrency, because on Windows a path's
  resolution can change the instant a concurrent fetch creates the parent
  directory (existing paths resolve through the filesystem; missing ones
  fall back to syntactic normalisation), which previously produced a
  spurious "Invalid table name" failure under concurrent fetches.
- **Retry row-loss detection.** `databricks-sql-connector`'s
  `fetchmany_arrow` can advance `cursor.rownumber` (rows consumed from the
  result set) *before* a network failure inside it raises. A naive retry
  then resumes from the advanced position, silently dropping the rows the
  failed attempt already consumed — anywhere from a partial batch to the
  entire remaining table if the failure happened at the very end of the
  stream. `_assert_no_rows_lost_after_retry` closes this gap: once *any*
  retry has occurred, every subsequent batch boundary asserts
  `rows_consumed == rows_received`; a mismatch raises
  `FetchIntegrityError` naming both counts.
- **Zero-row fetch schema fidelity.** The connector materialises every
  `fetchmany_arrow` result — including the empty terminating batch —
  against the query's real result-set schema. That means the terminator's
  Arrow schema is authoritative for typing an empty result, unlike a
  hand-rebuilt all-`pa.string()` schema from `cursor.description` would
  be. If the terminator itself carries zero columns (which no legal
  `SELECT` should ever produce), that means the transport is broken and
  the code refuses to cache anything rather than fabricate a shape.
- **Cache identity vs. query identity.** `_canonical_table` (backtick
  strip + casefold) governs *only* the cache filename; the exact string
  passed to `fetch_and_cache`/`fetch_progress` is used verbatim in the SQL
  query and as the `_fetch_progress` dict key. Two spellings of the same
  table therefore share one cache file but track fetch progress
  independently by their literal spelling.
- **`_TABLE_NAME_RE` uses `\w`, which matches Unicode word characters**, so
  non-ASCII letters in table names pass validation and are preserved
  verbatim in the cache filename (only `.`/`/`/`\` are replaced). Confusable
  Unicode characters (e.g. Cyrillic `а` vs Latin `a`) are treated as
  genuinely different tables, since they are different Unicode code
  points — no normalisation collapses them together.
- **Empty query string vs. `None`.** `if query:` treats both `None` and
  `""` (and any other falsy value) as "no custom query," falling back to
  `SELECT *`.

## Error handling

| Situation | Exception | Where it surfaces |
|---|---|---|
| Missing host/token/http_path | `DatabricksConfigError` | Raised from `_get_credentials`; route layer's `_get_databricks_client` does its own separate host/token check and raises `HTTPException(503)` directly rather than catching this type. `fetch_databricks_table` (`POST /fetch`) does *not* have an equivalent special case — a `DatabricksConfigError` raised inside `fetch_and_cache` falls through its `except` chain (`TimeoutError`/`ImportError`/`HTTPException` only) to the generic `except Exception` branch and surfaces as `500`, not `503`. See the `> NOTE:` below. |
| Malformed table name | `ValueError` | `fetch_and_cache` (before querying); the progress/cache-status/delete-cache routes call `_validate_table_param`, which raises `HTTPException(400)` directly instead of relying on this. `fetch_databricks_table` does not call `_validate_table_param`, so a malformed table name reaching it via `fetch_and_cache`'s own `ValueError` likewise falls through to the generic `500`, not `400`. See the `> NOTE:` below. |
| Malformed custom query | `ValueError` | `_validate_select_clause`, called from `fetch_and_cache` before query execution. |
| No cache for table | `CacheNotFoundError` | `read_cached_table`; propagates to the pipeline executor. |
| Retry lost rows | `FetchIntegrityError` | `_assert_no_rows_lost_after_retry` inside `fetch_and_cache`'s batch loop; cache not written, temp file cleaned up. |
| Zero-row fetch with no schema | `FetchIntegrityError` | End of `fetch_and_cache`'s batch loop; cache not written. |
| Connector/transport error, retries exhausted | Whatever the connector raised (not wrapped) | Propagates out of `fetch_and_cache`; temp file cleaned up, progress cleared via `finally`. |
| Missing `databricks-sql-connector` / `databricks-sdk` | `ImportError` | Route layer: `fetch_databricks_table` → `400`; `_get_databricks_client` → `503`. |
| Any other exception in a browsing route | Generic `Exception` | Logged with `logger.error(...)`, re-raised as `HTTPException(500, _INTERNAL_ERROR_DETAIL)` — the real message is never sent to the client. |
| Fetch exceeds `HAUTE_FETCH_TIMEOUT` | `TimeoutError` (via `BlockingWorkTimeoutError`) | `fetch_databricks_table` → `HTTPException(504)`; the underlying fetch thread keeps running and its eventual result/exception is drained and logged by `run_blocking_with_response_timeout`, not surfaced to the (already-responded) client. |
| `resolved_http_path` is `None` post-validation | `RuntimeError` | `_get_credentials` — internal invariant check, not reachable from valid inputs (see high-level spec NOTE). |

All `HauteError` subclasses defined here carry structured `context` kwargs
in addition to a message (via the shared `HauteError.__init__`), rendered
into the exception's string form.

> NOTE: `fetch_databricks_table` (`POST /fetch`)'s `except` chain only special-cases
> `TimeoutError`, `ImportError`, and `HTTPException`; it never catches
> `DatabricksConfigError` or the `ValueError` `fetch_and_cache` raises for a
> malformed table name. Both fall through to the generic `except Exception` branch
> and surface as `500` (with the underlying message not leaked to the client),
> even though the sibling progress/cache-status/delete-cache/browsing routes map the
> same underlying problems to `503`/`400` via `_get_databricks_client`/
> `_validate_table_param`. This looks like an inconsistency rather than an
> intentional design choice — confirmed empirically (a live `TestClient` request with
> an invalid table name, and separately with all Databricks env vars unset, both
> return `500` from `/fetch`) — but no test in `test_databricks_endpoints.py`
> exercises this real (non-mocked) path, so the discrepancy is currently undetected
> by the suite.

## Testing

Tests live in `tests/test_databricks_io.py`, `tests/test_databricks_cache.py`,
and `tests/test_databricks_endpoints.py`. Strategy is unit + integration
with the Databricks connector and SDK fully mocked (via `sys.modules`
injection for the lazily-imported `databricks.sql`, and `unittest.mock`
patches of `_get_databricks_client` for the SDK) — no real Databricks
connection is exercised.

- `test_databricks_io.py` — `_get_credentials` (env resolution, arg
  override, protocol/slash stripping, missing-field errors, listing all
  missing fields at once); `fetch_and_cache` happy path (metadata, file on
  disk), invalid/single-part table name rejection, progress
  set-then-cleared on success and on error, no temp file leaked on error,
  custom query forwarding, multi-batch row accumulation; thread-safety of
  the progress dict under concurrent writer/reader threads;
  `_validate_select_clause` exhaustively for every keyword in
  `_DANGEROUS_SQL_RE` plus semicolon/comment rejection and a
  window-function positive case; `_cache_path_for` path-traversal and
  separator-replacement behaviour; case/backtick cache-identity
  canonicalisation; corrupt/truncated/zero-byte cached parquet raising on
  read; concurrent same-table fetch producing one coherent file (barrier-
  synchronised to force the tmp-file race deterministically, with a
  platform-conditional assertion for Windows' looser same-destination
  replace semantics); zero-row fetch schema fidelity (typed columns
  preserved, and the schemaless-terminator case failing loud); and the
  retry-row-loss integrity check for both a mid-stream and an end-of-stream
  lost chunk.
- `test_databricks_cache.py` — narrower unit coverage of `cached_path`,
  `cache_info`, `clear_cache`, `fetch_progress`, `read_cached_table`, and
  additional `_cache_path_for` edge cases (backtick-quoted, hyphenated
  names, distinct-vs-same-table path identity).
- `test_databricks_endpoints.py` — full FastAPI `TestClient` coverage of
  every route: parametrized cross-resource tests for generic-exception →
  `500` (message not leaked), empty-list → `200`, and `None`-name
  filtering across warehouses/catalogs/schemas/tables; per-endpoint
  happy-path and edge-case tests (warehouse `state=None` → `"UNKNOWN"`,
  missing `id`/`name` filtered, `full_name` fallback construction,
  `comment=None` → `""`); missing required query params → `422`;
  fetch endpoint success, missing-connector `400`, timeout `504` (via a
  patched never-resolving `asyncio.to_thread`), generic-exception `500`,
  custom-query forwarding, `HTTPException` passthrough from the fetch
  layer, and missing `http_path` treated as `None`; cache status/delete
  round-trips; fetch-progress active/inactive; table-name validation
  rejecting two-part and four-part names on route params; and
  `_get_databricks_client`'s three failure modes (SDK not installed, host
  missing, token missing) each surfacing as `503`. Also covers the
  neighbouring `/api/schema/databricks` endpoint's not-cached (`404`) and
  cached-with-preview (`200`) paths, even though that route itself lives
  in `routes/files.py`.

Known coverage gaps: `test_databricks_io.py` has several "gap probe" tests
written in a `try: <call>; pytest.xfail(...) except ValueError: pass`
shape, intended to flag SQL-validation bypasses that don't raise. Of these,
only `TestSubqueryBypass.test_select_from_subquery_not_blocked` is a
currently-open gap; `TestExecuteImmediateBypass`,
`TestAdvancedSQLConstructsBypass.test_lateral_view_not_blocked`, and the
first two cases in `TestSQLCommentInjection` all in fact hit the
`except ValueError: pass` branch today (the regex/comment checks already
cover them) — see the `> NOTE:` callout below. `TestAtomicRenameOnWindows`
similarly xfails only on `win32` if the underlying `Path.replace()`
behaviour regresses to something `FileExistsError`-prone; on other
platforms an unexpected `FileExistsError` fails the test outright.

> NOTE: `_DANGEROUS_SQL_RE` lists `EXEC|EXECUTE` and `LATERAL` as separate,
> explicit alternatives (not just `EXEC` alone), so — despite their
> docstrings — `TestExecuteImmediateBypass.test_execute_immediate_not_blocked`
> and `TestAdvancedSQLConstructsBypass.test_lateral_view_not_blocked` are
> *not* live gaps: `EXECUTE IMMEDIATE ...` and `LATERAL VIEW EXPLODE(...)`
> are both already rejected today (confirmed by running the regex directly
> against each payload). The same is true of
> `TestSQLCommentInjection.test_line_comment_neutralizes_from` and
> `test_block_comment_neutralizes_from` — the module's `--`/`/*` checks in
> `_validate_select_clause` already reject both line and block comments.
> All four tests are written as `try: <call>; pytest.xfail(...) except
> ValueError: pass` probes, and in all four cases the call raises
> `ValueError`, so the test silently takes the "already fixed" `pass`
> branch and never reaches its own `pytest.xfail` — the test passes, but
> its docstring's framing of an open gap is stale. The one genuine,
> currently-open gap of this shape is
> `TestSubqueryBypass.test_select_from_subquery_not_blocked`: `FROM` is
> not in `_DANGEROUS_SQL_RE`, so a subquery embedded in the `SELECT`
> clause (e.g. `SELECT * FROM (SELECT ... FROM other_table)`) passes
> validation unmodified, and that test's `pytest.xfail` branch is the one
> actually reached.
