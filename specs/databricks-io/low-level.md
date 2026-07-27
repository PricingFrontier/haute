# Databricks IO — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_databricks_io.py` | Credentials, table/projection validation, canonical table identity, bounded Arrow batch iteration, retries, and integrity checks. |
| `src/haute/routes/databricks.py` | `/api/databricks` Unity Catalog browsing endpoints. |

`src/haute/_input_providers.py`, `src/haute/_source_cache.py`, and
`src/haute/routes/input_cache.py` are owned by IO/server components and consume
`DatabricksSnapshotBuilder`; they are interactions, not this component's module map.

## Key types and data structures

- `DatabricksConfigError(HauteError)` reports missing host/token/http-path configuration.
- `FetchIntegrityError(HauteError)` reports an unprovable complete/schema-bearing result.
- `DatabricksSnapshotBuilder` validates config once and exposes `build(context)`.
- `_TABLE_NAME_RE` accepts three dot-separated, optionally backtick-quoted identifier parts.
- `_DANGEROUS_SQL_RE` and `_FROM_SQL_RE` define the projection-only query boundary.
- `_FETCH_BATCH_SIZE`, `_FETCH_MAX_RETRIES`, and `_FETCH_INITIAL_BACKOFF` bound fetching.

## Control flow

### Snapshot build

1. Registry validation requires snapshot mode, table, HTTP path, optional projection, and
   optional positive `batch_size`.
2. `source_cache_identity()` uses `_canonical_table()`, the HTTP path, and validated
   projection. It omits `batch_size` and includes symbolic host/token references only.
3. `DatabricksSnapshotBuilder.__init__()` repeats safety-critical table/query validation.
4. `build()` delegates to `_iter_databricks_batches()`.
5. `_iter_databricks_batches()` resolves host/token, builds the SQL statement, checkpoints,
   opens the connector, checkpoints again, and executes.
6. Each fetch is preceded by a checkpoint. Transient exceptions retry at most three times
   with exponential backoff.
7. After any retry, `_assert_no_rows_lost_after_retry()` compares received rows with
   `cursor.rownumber`.
8. Arrow tables flow directly into `SourceCacheStore`, which owns Parquet writing and
   atomic publication.

### Credentials

`_get_credentials(http_path)` reads only `DATABRICKS_HOST` and `DATABRICKS_TOKEN` from the
environment. `http_path` is mandatory node configuration; there is no
`DATABRICKS_HTTP_PATH` fallback. Protocol prefixes are stripped from the host for the SQL
connector. Returned secrets stay in connector-call scope.

### Browsing

`routes/databricks.py` creates a Databricks `WorkspaceClient` and maps SDK objects into the
shared warehouse/catalog/schema/table response models. Browsing does not fetch table data
or publish snapshots.

## Edge cases and invariants

- Backticks and case do not create duplicate table identities.
- A projection containing any `FROM`, comment, semicolon, or dangerous keyword is rejected.
- `batch_size` must be positive but does not change identity.
- A checkpoint occurs before `cursor.execute`; cancellation never waits for a new fetch to
  become observable.
- An empty schema-bearing Arrow table is yielded once; an empty schemaless result fails.
- Retried fetches must prove cursor position equality at every later boundary.
- Host/token values are absent from configs, identity, metadata, logs, and API responses.

## Error handling

`DatabricksConfigError` and `ValueError` are admission-time configuration failures.
`FetchIntegrityError` prevents publication of incomplete results. `KeyboardInterrupt`,
`SystemExit`, and programming/type errors are never retried. Other fetch errors are retried
within the fixed budget and the final provider exception propagates with its message.

The input-cache worker logs `error_type` and a shared credential-scrubbed `error` for
provider failures; raw error text is not copied to public responses. Browsing endpoints
preserve deliberate `HTTPException` responses and log unexpected SDK messages before
returning a generic 500.

## Testing

- `tests/test_databricks_io.py` covers credentials, table/projection validation,
  cancellation before execute, batch streaming, retries, empty-schema behaviour, row-loss
  detection, identity-independent batch sizing, and builder-through-store publication.
- `tests/test_databricks_endpoints.py` covers optional dependency/credential failures,
  SDK mapping, and generic error responses for browsing.
- `tests/test_input_providers.py` covers redacted/canonical Databricks source identity.
- `tests/test_input_cache_route.py` covers provider-neutral job admission, logged provider
  diagnostics, cancellation, status, and publication lifecycle.
