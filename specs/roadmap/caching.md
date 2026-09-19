# Caching roadmap

## Scope

Full data at any pipeline point is computed once, stored once, and reused by
every consumer: one snapshot store, one data-point resolver, one build service,
one analysis-result surface, and one execution rule under which every
full-data materialisation both reads and writes that store. Current behaviour is
specified in [caching](../caching/high-level.md), the
[IO layer](../io-layer/high-level.md), and the
[execution engine](../execution-engine/high-level.md). Explore consumers of
these packages are owned by the [Explore / EDA roadmap](explore-eda.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S08 | Deferred | P3 | Fold the API-input table cache into the shared snapshot store with per-table validity. |

## Planned improvements

`CACHE-S08` is deferred. A package must
not bypass the resolver, lease, signature, or capture contracts of an earlier
one. Every package builds on the node-output snapshot store (signature, slot
index, column widening, retention, cross-process leases, and the publication
rule) specified in the [IO layer](../io-layer/low-level.md#node-output-snapshots) and
the data-point resolver and leased reads specified in
[caching](../caching/low-level.md#data-points), the node-data build service, the data
profile job, and the request-time analysis helper specified in the
[server API](../server-api/low-level.md#node-data-builds), the analysis-result store
specified in [caching](../caching/low-level.md#analysis-results), and the shared frontend
data cache specified in
[frontend shared](../frontend-shared/low-level.md#the-shared-data-cache).

### CACHE-S08 — One store for API-input tables

**Why:** API-input tables are cached per port by the JSON-shredding cache in its
own `working/` and `committed/` layers, a second durable store beside the
shared snapshot store, and that cache is built and validated for all of a
node's emitting tables together, so editing one table's schema makes the
node's other tables unusable until the next build.

**Plan:** Evaluate moving per-port table caches into the shared snapshot store
while preserving the committed layer used by deployment, and evaluate
per-table validity so an edit to one table leaves the node's other tables
current. A shred reads the whole source whatever the number of tables, so the
evaluation measures how much of a build's cost is table materialisation rather
than the source traversal.

**Activation trigger:** The data-point resolver serves API-input tables in
production use and a measured maintenance or disk cost from running two
durable stores.

**Acceptance:** A decision record, and if implemented, JSON-cache route, deploy
bundling, and data-point resolver tests pass against one store.

**Dependencies:** The caching data-point resolver.

**Evidence:** `src/haute/_json_shred/_cache.py`; `src/haute/routes/json_cache.py`.
