# Databricks IO — High-Level Specification

## Purpose

Databricks IO lets a canonical Data Input node browse Unity Catalog and build a bounded,
shared snapshot from a Databricks SQL Warehouse. Pipeline execution reads the published
snapshot through the provider-neutral IO layer and never connects to Databricks.

## Scope

In scope are workspace browsing, host/token resolution, warehouse HTTP-path use,
fully-qualified table and projection validation, bounded Arrow fetching, retry-integrity
checks, and the builder consumed by the source-cache store.

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

A retry whose consumed-row position disagrees with locally received rows raises
`FetchIntegrityError`. Zero rows with no schema also raise `FetchIntegrityError`. No failure
publishes or replaces the previous source-cache generation.
