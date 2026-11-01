# Databricks IO — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_databricks_credentials.py` | Single environment-to-credentials boundary: redaction-safe `DatabricksCredentials`, `DatabricksConfigError`, host normalisation, completeness validation, the five sign-in forms and their precedence and refusals, the CLI config file's profile reader, `databricks_sdk_config` (the SDK `Config` that performs a user sign-in, whose failure is `DatabricksSignInError`), `databricks_workspace_client`, and `credentials_rejected`, which tells a refusal of the credentials (a `DatabricksSignInError`, the SDK's Unauthenticated error, or the ValueError its OAuth exchange raises for a refused service principal) from any other failure. |
| `src/haute/_databricks_io.py` | SQL-connector adaptation, table/projection validation, canonical table identity, bounded Arrow batch iteration, retries, and integrity checks. It raises `DatabricksConfigError` from `src/haute/_databricks_credentials.py`. |
| `src/haute/_databricks_output.py` | Data Output writes to a Unity Catalog table: Spark-compatible dtype casts, the staged Parquet upload, the overwrite/append load SQL, staged-file cleanup, and `DatabricksWriteError`. |
| `src/haute/routes/databricks.py` | `/api/databricks` endpoints: the connection (which sign-in is selected), the connection test, and Unity Catalog browsing. |

`src/haute/_input_providers.py`, `src/haute/_source_cache.py`, and
`src/haute/routes/input_cache.py` are owned by IO/server components and consume
`DatabricksSnapshotBuilder`; they are interactions, not this component's module map.

## Key types and data structures

- `DatabricksCredentials` is a frozen, slotted internal dataclass containing the
  normalised workspace host, bare SQL hostname, selected auth mode (`pat`,
  `service_principal`, `cli_login`, `browser_login` or `profile`), the selected
  credential fields, the profile name (mode `profile`) and the SDK auth type
  (`DATABRICKS_AUTH_TYPE`'s value, or the one the profile declares, if any). Token,
  client id, and client secret fields are excluded from its representation.
  `signs_in_as_user` is true for the three user sign-ins; `sign_in_failure()` is
  the problem and the remedy, in haute's words, when one cannot be completed (the
  `databricks auth login` command for the CLI forms, naming the profile or
  `DATABRICKS_HOST` rather than the host; finishing the browser sign-in for the
  browser forms).
- `DatabricksConfigError(HauteError)` reports missing host/authentication/http-path
  configuration or an unavailable OAuth provider dependency. The class is owned
  by `src/haute/_databricks_credentials.py` and re-exported by `src/haute/_databricks_io.py`.
- `DatabricksSignInError(DatabricksConfigError)` is a user sign-in the SDK could not
  complete, raised by `databricks_sdk_config` from the SDK's construction-time
  `ValueError`, which it keeps as its cause for the log. It carries `problem` and
  `remedy` separately so a route can set the problem in its own sentence; its
  message is `"<problem>: <remedy>."`.
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

### Table write

A Data Output with `outputType: "databricks"` has no `format`;
`write_polars_output()` dispatches it to `write_databricks_output()` before any
format lookup.

1. Registry validation requires `http_path` and `table`, allows an optional
   `write_mode` of `overwrite` (the default) or `append`, and refuses arguments.
2. The writer re-checks `_TABLE_NAME_RE` and the write mode (`_require_write_mode`),
   and resolves credentials with
   `_resolve_connection(http_path, node_label="Data Output")`;
   one `DatabricksCredentials` authenticates both the SQL connection
   (`_sql_auth_kwargs`) and the Files API client
   (`_databricks_credentials.databricks_workspace_client`, which the browsing
   route shares).
3. `_spark_compatible()` casts `Null` and `Time` columns to strings and
   nanosecond datetimes to microseconds. The frame then streams through the
   ordinary Parquet sink into a temporary directory, and the row count comes
   from that file's metadata. A chunk-write recipe is composed with the same
   cast (`recipe.then(_spark_compatible)`), so it still describes the frame
   written and passes `check_recipe_equivalence`.
4. One SQL connection runs `CREATE VOLUME IF NOT EXISTS` for `haute_staging` in
   the target schema, the volume quoted part by part as a table name is
   (`_quoted_table_name`). The file is uploaded under a fresh UUID name with the
   Files API's `upload_from`, in parts of `UPLOAD_PART_BYTES` (16 MiB) on at
   most `UPLOAD_PARALLELISM` (4) threads, which bounds the upload's memory; the
   connector's `PUT` reads the whole file into memory and is not used. The load
   follows in one statement (`load_statement`), a single Delta transaction:
   `CREATE OR REPLACE TABLE ... AS SELECT` for overwrite; for append, the Tables
   API's exact-name `tables.exists` (`_table_exists`) chooses `CREATE TABLE ... AS
   SELECT` for a table that does not exist yet and `INSERT INTO ... BY NAME` for
   one that does. A failed load therefore leaves the table as it was: no table
   after a failed first append, no added rows after a failed later one. An
   existence answer that is not a plain yes reads as no, since the `CREATE TABLE`
   that follows refuses a table that exists, as it does one another writer
   created after the check: the append fails and replaces nothing.
5. The upload and the load share one `finally` that deletes the staged file
   (`_remove_staged`), so an upload whose response failed after the bytes landed
   is cleaned up too. `NotFound` (the upload never created the file) is
   expected; any other cleanup failure is logged and never masks the write's
   own outcome.
6. Every warehouse or storage message, in the `DatabricksWriteError` and in the
   cleanup log, passes `_warehouse_message`: `redact_sensitive_text` with the
   token and client secret as known secrets, then whitespace collapsed and the
   text cut to 600 characters, redaction first so a cut never exposes part of a
   secret. `redact_sensitive_text` also redacts a credential query parameter
   outside a full URI (the relative signed URL a storage error prints), wherever
   it sits in the query, which an enclosing `url: ...` assignment would
   otherwise swallow.

The write is transactional: there is no staging path on disk to publish, and
`resolve_data_output_path()` returns `(None, table)`. Duplicate-destination
detection compares `_canonical_table()` spellings.

### Credentials

`resolve_databricks_credentials()` is the only reader of `DATABRICKS_HOST`,
`DATABRICKS_TOKEN`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`,
`DATABRICKS_AUTH_TYPE`, `DATABRICKS_CONFIG_PROFILE` and `DATABRICKS_CONFIG_FILE`. It
selects, in this order:

1. A named profile (`DATABRICKS_CONFIG_PROFILE`, mode `profile`). The profile is read
   from the Databricks CLI's config file (`DATABRICKS_CONFIG_FILE`, else
   `~/.databrickscfg`) with `configparser`, as the SDK reads it, `DEFAULT` section
   inherited; its `host` is required and its `auth_type`, if declared, is recorded.
   Reading the file here, with the standard library, keeps resolution free of the SDK
   and of any sign-in side effect. A missing file, an unknown profile, a profile
   without a host, and an unparseable file are each refused naming the file and the
   profile (never a line of the file) with the `databricks auth login ... --profile`
   command that creates the profile. A profile beside any of the other five variables
   is refused naming both, because the SDK merges the environment over a profile and
   the identity haute acts as would otherwise be a quiet merge.
2. An auth type (`DATABRICKS_AUTH_TYPE`): `databricks-cli` selects mode `cli_login`
   and `external-browser` mode `browser_login`; any other value is refused naming the
   two accepted. `DATABRICKS_HOST` is required. An auth type beside a token or a
   service-principal field is refused naming both.
3. A non-empty token (mode `pat`); otherwise both service-principal fields (mode
   `service_principal`). A token beside the pair simply wins, because a Databricks
   App's injected pair must be overridable. When neither is present the missing
   message names the token, the missing pair fields, and the user sign-ins as the
   alternative.

The resolver strips surrounding whitespace and trailing slashes from the host,
preserves an optional protocol for the workspace SDK, derives a protocol-free
hostname for the SQL connector, and passes selected authentication values
byte-for-byte rather than rewriting secret material. Errors report
environment-variable names and profile names—never values read from the environment,
and never a host. Callers may add named non-secret requirements to the same
aggregated configuration error.
MLflow never uses these variables: its Databricks credentials are the dedicated
`DATABRICKS_MLFLOW_HOST`/`DATABRICKS_MLFLOW_TOKEN` pair
([modelling](../modelling/low-level.md)), so a data token and an MLflow token
can carry different scopes. MLflow itself reads `DATABRICKS_CONFIG_PROFILE` for a bare
`databricks` tracking URI, which is why the modelling component refuses that variable
beside the MLflow pair; one profile serves both through
`MLFLOW_TRACKING_URI=databricks://<profile>`.

`databricks_sdk_config(credentials)` is the SDK `Config` for a user sign-in:
`Config(profile=...)` for mode `profile`, `Config(host=..., auth_type=...)` for the
other two. Building it signs in (the SDK runs `databricks auth token`, or opens the
browser), so it is called only where a connection is about to be made. The SDK's
construction-time `ValueError` becomes `DatabricksSignInError` with
`credentials.sign_in_failure()`'s words. `databricks_workspace_client` builds
`WorkspaceClient(host, token)`, `WorkspaceClient(host, client_id, client_secret)` or
`WorkspaceClient(config=databricks_sdk_config(...))` by mode.

`_resolve_connection(http_path, node_label=...)` passes the mandatory node
`http_path` as such an additional requirement; there is no `DATABRICKS_HTTP_PATH`
fallback. `_sql_auth_kwargs` adapts the resolved credentials into `access_token`, the
SDK OAuth M2M callback built by `_service_principal_credentials()`, or, for a user
sign-in, `_user_sign_in_credentials()`: a zero-argument provider that builds the SDK
config when the connector first asks and hands it the config's `authenticate` as the
header factory, so the sign-in happens at connection time and the SDK refreshes the
token during a long fetch. The Data Input's batch reader and the Data Output's writer
both connect through these. The browsing route calls the same resolver and adapts
the selected form into `WorkspaceClient` through `databricks_workspace_client`.
Returned secrets and providers stay in connector-call scope.

### Browsing

`src/haute/routes/databricks.py` creates a Databricks `WorkspaceClient` and maps SDK objects into the
shared warehouse/catalog/schema/table response models. Browsing does not fetch table data
or publish snapshots. Each of the four routes lists inside `_browsing(subject, within=...)`,
which builds `_get_databricks_client()`'s client inside its guarded block (a user sign-in
fails as the client is built, and must answer like any other refused credential, not as a
server error) and turns a `DatabricksError` or refused credential raised there or while
the listing is consumed (the SDK's listings are lazy) into `_browsing_failure`'s answer.

### Connection and its test

`GET /api/databricks/connection` (`DatabricksConnectionResponse`) answers from
`resolve_databricks_credentials()` alone: `configured`, the `auth_mode`, the `profile`
name and the `sdk_auth_type` when there is one, or `configured: false` with the
resolver's message as `detail`. No SDK is imported and no network call is made, so a
page load can never open a browser; only the test, a listing or a fetch signs in. The
host is not answered.

`POST /api/databricks/test-connection` (`DatabricksTestConnectionResponse`) signs in
through `databricks_workspace_client` and calls `current_user.me()`; it answers
`ok: true` with the `identity` (the user name, else the display name, which a service
principal has) and the SDK config's `auth_type`. A missing SDK, a configuration
failure, a sign-in failure, refused credentials, a missing grant and any other SDK
failure answer `ok: false` with `detail` worded as the browsing answers are (below),
never a 5xx, and are logged as `databricks_connection_test_failed`. For a browser
sign-in the test is the call that opens the browser.

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
  A profile name, and the SDK's name for a sign-in, are not credentials and may appear.
- Resolution never signs in: reading the environment and the CLI config file has no
  side effect, so the connection endpoint and cache-identity derivation can never open
  a browser or run the CLI.

## Error handling

`DatabricksConfigError` and `ValueError` are admission-time configuration failures.
`FetchIntegrityError` prevents publication of incomplete results. `KeyboardInterrupt`,
`SystemExit`, and programming/type errors are never retried. Other fetch errors are retried
within the fixed budget and the final provider exception propagates with its message.

A table write wraps any connector failure in `DatabricksWriteError`, whose message
is the whitespace-collapsed warehouse error (truncated at 600 characters). The
write-output route maps it to 502 and `DatabricksConfigError` to 400.

The input-cache worker logs `error_type` and a shared credential-scrubbed `error` for
provider failures; raw error text is not copied to public responses. Browsing endpoints
preserve deliberate `HTTPException` responses (the 503s for a missing SDK or missing
credentials) and answer an SDK failure with a message written for it, by its class:
refused credentials 401, naming `DATABRICKS_TOKEN`, or `DATABRICKS_CLIENT_ID` and
`DATABRICKS_CLIENT_SECRET`, by the credential form `resolve_databricks_credentials`
selects, or, for a user sign-in, the `DatabricksSignInError`'s own problem and remedy
("Databricks sign-in for profile 'work' failed or has expired, so the catalogs could not
be listed: run `databricks auth login --profile work` and try again."); a user sign-in
whose token the workspace refused after the SDK produced it gets the same remedy.
`credentials_rejected` (`_databricks_credentials.py`, shared with the UC
transport) recognises them: a `DatabricksSignInError`; a refused token, the SDK's
`Unauthenticated`; while a
refused service principal never reaches the API, because the SDK's OAuth token
exchange raises a plain `ValueError("<code>: <description>")` from the token
endpoint's answer, which counts only for the codes that refuse the client or its grant
(`invalid_client`, `invalid_grant`, `unauthorized_client`); then `PermissionDenied` 403, naming what was listed; `NotFound` from a listing inside
a catalog or schema 404, naming that catalog or schema; any other `DatabricksError` 502.
Each is logged as `databricks_browsing_failed` with the error type and the SDK's text
(a sign-in failure's SDK text is its cause, logged with it),
the token and client secret scrubbed (`redact_sensitive_text`).

## Testing

- `tests/test_databricks_credentials.py` covers the shared resolver's host
  projections, PAT precedence, incomplete-pair failures, aggregated named
  requirements, representation/error redaction, and which failures count as a refusal
  of the credentials (no other `ValueError` does); the user sign-ins: a profile read
  from a config file named by `DATABRICKS_CONFIG_FILE` (host, declared auth type,
  `DEFAULT` inheritance, no declared type), a missing file, an unknown profile, a
  profile without a host and an unparseable file each refused by name without a line
  of the file, a profile beside each other selector refused by name, each accepted
  auth type selected, an auth type without a host and an unknown auth type refused,
  an auth type beside a token or pair field refused, the missing message naming every
  form; and the SDK handoff through a fake `databricks` executable
  (`DATABRICKS_CLI_PATH`): the header is the token the CLI prints, byte for byte, a
  profile is signed in with the profile's host, an exiting CLI with the "refresh
  token is invalid" text becomes `DatabricksSignInError` with the login command, and
  the auth-type form's failure names `DATABRICKS_HOST` rather than the host. The
  handoff tests record the SDK version they were observed under.
- `tests/test_databricks_io.py` covers SQL credential adaptation (token, service
  principal, and a user sign-in whose provider builds the SDK config only when the
  connector asks and hands it the config's `authenticate`; a profile connects to the
  profile's host), table/projection validation,
  cancellation before execute, batch streaming, retries, empty-schema behaviour, row-loss
  detection, identity-independent batch sizing, and builder-through-store publication.
- `tests/test_databricks_output.py` covers output config validation, the load SQL,
  the append's one statement over a fake warehouse that keeps its tables (no table after a
  failed first append, the table unchanged after a failed later one, a table created after
  the existence check refused, not replaced), dtype casts, the staged write over a fake connector and Files API (the bounded
  part size and parallelism, cleanup after a failed load and after an upload that
  failed once its bytes may have landed, a missing staged file not logged as a
  cleanup failure), redaction of a signed-URL query and the token in errors and in
  the cleanup log, a chunk-write recipe carrying the casts, a pipeline write,
  destination identity, and worker error kinds.
- `tests/test_databricks_endpoints.py` covers optional dependency/credential failures,
  SDK mapping, and the browsing error answers: a missing catalog and a missing schema
  404 naming them, a refused token 401 naming its variable, a service principal that a
  stub token endpoint refuses through the real SDK 401 naming its pair, and listing
  again once the secret is corrected, an expired CLI sign-in 401 naming the profile
  and the login command, a missing grant 403, and any other SDK failure 502 with its text logged,
  the token scrubbed; the connection endpoint (the token form without the host, a
  profile with its declared sign-in, what is missing, and that a browser sign-in never
  builds a client); and the connection test (the identity and sign-in kind, the
  display-name fallback, an expired sign-in, a refused token, any other SDK failure
  with its text logged and the token scrubbed, missing configuration and a missing SDK,
  each `ok: false` and never a 5xx).
- `tests/test_databricks_routes_auth.py` proves the browsing adapter consumes
  the shared resolution result for both auth modes and preserves normalised
  workspace hosts.
- `tests/test_input_providers.py` covers redacted/canonical Databricks source identity.
- `tests/test_input_cache_route.py` covers provider-neutral job admission, logged provider
  diagnostics, cancellation, status, and publication lifecycle.
