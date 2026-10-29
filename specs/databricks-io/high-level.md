# Databricks IO — High-Level Specification

## Purpose

Databricks IO lets a canonical Data Input node browse Unity Catalog and build a bounded,
shared snapshot from a Databricks SQL Warehouse, and lets a Data Output node write its
result to a Unity Catalog table. Pipeline execution reads the published snapshot through
the provider-neutral IO layer and never connects to Databricks for an input; writing a
Databricks Data Output is the one step that does.

## Scope

In scope are workspace browsing, host/token resolution, warehouse HTTP-path use,
fully-qualified table and projection validation, bounded Arrow fetching, retry-integrity
checks, the builder consumed by the source-cache store, and the Data Output table write
(staging through a Unity Catalog volume, then one bulk load).

Provider-neutral generation publication, leases, quotas, status, and background build jobs
belong to the [IO layer](../io-layer/high-level.md) and
[server API](../server-api/high-level.md). Databricks model serving and deployment are out
of scope.

## Behaviour

The browsing endpoints list warehouses, catalogs, schemas, and tables with the Databricks
SDK. Browsing and data acquisition require `DATABRICKS_HOST` plus either a
`DATABRICKS_TOKEN` personal access token or the complete
`DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` service-principal pair. A token takes
precedence when both forms are present. Data acquisition additionally requires a non-empty
node `http_path` and a fully-qualified `catalog.schema.table`; service-principal SQL
connections use the SDK's OAuth M2M credentials provider.

An optional query is a projection clause beginning with `SELECT`. Semicolons, comments,
`FROM`, and mutating/dangerous SQL keywords are rejected before connecting. The component
appends `FROM <validated table>`.

The snapshot builder checkpoints before connection, before query execution, and before
each fetch. It streams Arrow tables in configured batch sizes. An empty result is
publishable only when the connector supplies a schema. Transient fetches retry with bounded
exponential backoff; after any retry, connector row position must prove no rows were lost.

Cache identity canonicalises case/backtick spelling of the table and includes HTTP path
and projection, but excludes `batch_size` because it is a transport/performance knob.
Resolved host and credential values never enter identity or metadata.

A Databricks Data Output names a warehouse HTTP path, a fully-qualified table, and a write
mode: `overwrite` replaces the table, `append` creates it when missing and inserts by
column name. The result streams to a local Parquet file, is uploaded to a `haute_staging`
volume in the target schema in bounded parts (so a large output never sits whole in
memory), and is loaded by the warehouse in one statement; the staged file is deleted
whether or not the upload or load succeeds. Column types Databricks cannot read from
Parquet (null-typed, `Time`, nanosecond datetimes) are cast first. Two outputs naming one
table, however spelled, are refused as duplicate destinations. In the editor an invalid
table name clears the destination, so Write is never left targeting a previous table.

## Design rationale

Explicit snapshots isolate pipeline execution from remote availability and make results
repeatable. Projection-only SQL and strict table validation keep the generated query
auditable. Arrow batches preserve schema and bound Python memory. Retry integrity is
fail-closed because a partial cache is worse than a failed refresh.

## Interactions

- [IO layer](../io-layer/high-level.md) validates the node, constructs redacted identity,
  publishes batches, and leases the generation.
- [Server API](../server-api/high-level.md) admits background builds and exposes their state.
- [Frontend node editors](../frontend-node-editors/high-level.md) obtain catalog choices and
  persist the selected HTTP path/table/projection.
- [Caching](../caching/high-level.md) defines canonical checked identity material.

## Failure model

Missing optional packages or credentials fail clearly. Invalid table/query configuration
fails before provider access. Cancellation/deadline checkpoints abort before execute or
between batches. Connector failures retain their original exception as the cause and are
logged with exception type and a credential-scrubbed message; public build state remains a
stable `build_failed` response.

A browsing failure the SDK raises is answered with its cause: refused credentials 401,
naming the `.env` variables of the form in use; a missing grant 403, naming what was
listed; a catalog or schema that does not exist 404, naming it; any other failure 502.
The SDK's own text is logged, credentials scrubbed, never returned.

A retry whose consumed-row position disagrees with locally received rows raises
`FetchIntegrityError`. Zero rows with no schema also raise `FetchIntegrityError`. No failure
publishes or replaces the previous source-cache generation.

A table write refuses an invalid table name or write mode before connecting
(`DatabricksConfigError`). A warehouse or storage failure raises `DatabricksWriteError`
carrying the warehouse's message with credentials redacted (a failed upload's error can
hold a signed storage URL); a failed staging cleanup is logged, redacted, and never masks
the write's outcome. A failed `overwrite` load leaves the previous table in place.
