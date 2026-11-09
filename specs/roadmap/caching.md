# Caching roadmap

## Scope

The on-disk store of node outputs, as [the caching
specification](../caching/high-level.md) describes it: what a node-output
snapshot's signature covers, and so when a stored output stops being
current. The package below comes from the 9 November 2026 codebase review.
It follows from the signature's fields as written and from the repository's
history; no stale read was staged to show it.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CCH-01 | Planned | P2 | A node output stored by one version of Haute's node code is not read as current by another. |

## Planned improvements

### CCH-01 — A node-output snapshot is tied to the code that computed it
**Why:** Read from the source and the history. A node-output snapshot's
signature covers the node's lineage, its runtime inputs, the source, the
semantics class, contract enforcement, whether a preamble exists, a
hand-kept execution semantics version and the installed Polars version.
Nothing in it identifies the Haute code that computed the rows. The
specification says the Polars version is signed "so an upgrade never mixes
one release's results into another's run", which holds for a Polars upgrade
and not for a Haute one. The hand-kept version has read `node-snapshot:v1`
since the day it was introduced, 17 September 2026, while the modules that
compute node outputs have changed in 33, 18, 62, 39 and 33 commits since
(the node apply functions, rating, the builders, the model scorer and the
Polars steps). Some of those changed what a node produces: a rating table
with two entries for the same levels is now refused, a Workbench Output
fills columns it could not before. The JSON shredding cache does keep such
a version and has moved it to 3. The store lives under the project's cache
folder and outlives the server, so after a fix to a node's computation every
output captured before it is still current: preview, training preparation,
optimiser setup and Explore read the old rows until an input or the graph
changes or the analyst clears the cache.

**Plan:** Sign what computed the rows. For a released build, the Haute
version; for a source checkout, a digest of the modules a node's output
depends on, computed once per process. Keep the hand-kept version for a
deliberate break. Say in the specification that a Haute upgrade starts
node outputs cold, as a Polars upgrade does.

**Acceptance:** A snapshot published under one value of the code identity is
reported stale under another, asserted by changing the identity in a test
and reading the slot's status. Two processes of the same build share
snapshots as today. The signature's field list in the caching specification
names the new field.

**Dependencies:** None. Existing snapshots are rebuilt on first use; Haute
has no users to migrate.

**Evidence:** `src/haute/_node_snapshots.py::node_snapshot_signature`;
`src/haute/_node_snapshots.py::NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION`;
`src/haute/_cache.py::ALGO_VERSION` moves for canonicalisation changes;
`src/haute/_json_shred/_snapshots.py::SHRED_SEMANTICS_VERSION` is the
counterpart that has moved; `specs/caching/high-level.md`.
