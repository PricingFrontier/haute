# Databricks IO — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_databricks_credentials.py` | Single environment-to-credentials boundary: redaction-safe `DatabricksCredentials`, `DatabricksConfigError`, host normalisation, completeness validation, and PAT-over-service-principal precedence. |
| `src/haute/_databricks_io.py` | SQL-connector adaptation, table/projection validation, canonical table identity, bounded Arrow batch iteration, retries, and integrity checks. It raises `DatabricksConfigError` from `src/haute/_databricks_credentials.py`. |
| `src/haute/routes/databricks.py` | `/api/databricks` Unity Catalog browsing endpoints. |

`src/haute/_input_providers.py`, `src/haute/_source_cache.py`, and
`src/haute/routes/input_cache.py` are owned by IO/server components and consume
`DatabricksSnapshotBuilder`; they are interactions, not this component's module map.

## Key types and data structures

- `DatabricksCredentials` is a frozen, slotted internal dataclass containing the
  normalised workspace host, bare SQL hostname, selected auth mode, and the
  selected credential fields. Token, client id, and client secret fields are
  excluded from its representation.
- `DatabricksConfigError(HauteError)` reports missing host/authentication/http-path
  configuration or an unavailable OAuth provider dependency. The class is owned
  by `src/haute/_databricks_credentials.py` and re-exported by `src/haute/_databricks_io.py`.
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
   projection. It omits `batch_size` and includes symbolic environment references only.
3. `DatabricksSnapshotBuilder.__init__()` repeats safety-critical table/query validation.
4. `build()` delegates to `_iter_databricks_batches()`.
5. `_iter_databricks_batches()` resolves the host and selected authentication, builds the SQL statement, checkpoints,
   opens the connector, checkpoints again, and executes.
6. Each fetch is preceded by a checkpoint. A fetch is attempted at most three times
   (two retries) with exponential backoff of 1s then 2s.
7. After any retry, `_assert_no_rows_lost_after_retry()` compares received rows with
   `cursor.rownumber`.
8. Arrow tables flow directly into `SourceCacheStore`, which owns Parquet writing and
   atomic publication.

### Credentials

`resolve_databricks_credentials()` is the only reader of `DATABRICKS_HOST`,
`DATABRICKS_TOKEN`, `DATABRICKS_CLIENT_ID`, and `DATABRICKS_CLIENT_SECRET`.
A non-empty token wins; otherwise both service-principal fields are required.
The resolver strips surrounding whitespace and trailing slashes from the host,
preserves an optional protocol for the workspace SDK, derives a protocol-free
hostname for the SQL connector, and passes selected authentication values
byte-for-byte rather than rewriting secret material. Errors report only
environment-variable names—never their values. Callers may add named non-secret
requirements to the same aggregated configuration error.

`_connection_settings(http_path)` passes the mandatory node `http_path` as such
an additional requirement; there is no `DATABRICKS_HTTP_PATH` fallback. It
adapts the resolved credentials into either `access_token` or the SDK OAuth M2M
callback built by `_service_principal_credentials()`. The browsing route calls
the same resolver and adapts the selected form into `WorkspaceClient`. Returned
secrets and providers stay in connector-call scope.

### Browsing

`src/haute/routes/databricks.py` creates a Databricks `WorkspaceClient` and maps SDK objects into the
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
- Host and credential values are absent from configs, identity, metadata, logs, and API
  responses. Credential object representations omit every token, client id, and secret.

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

- `tests/test_databricks_credentials.py` covers the shared resolver's host
  projections, PAT precedence, incomplete-pair failures, aggregated named
  requirements, and representation/error redaction.
- `tests/test_databricks_io.py` covers SQL credential adaptation, table/projection validation,
  cancellation before execute, batch streaming, retries, empty-schema behaviour, row-loss
  detection, identity-independent batch sizing, and builder-through-store publication.
- `tests/test_databricks_endpoints.py` covers optional dependency/credential failures,
  SDK mapping, and generic error responses for browsing.
- `tests/test_databricks_routes_auth.py` proves the browsing adapter consumes
  the shared resolution result for both auth modes and preserves normalised
  workspace hosts.
- `tests/test_input_providers.py` covers redacted/canonical Databricks source identity.
- `tests/test_input_cache_route.py` covers provider-neutral job admission, logged provider
  diagnostics, cancellation, status, and publication lifecycle.
