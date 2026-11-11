# Caching roadmap

## Scope

The on-disk store of input and node-output snapshots, as [the caching
specification](../caching/high-level.md) describes it: what a node-output
snapshot's signature covers, and so when a stored output stops being
current, how a finished snapshot is published, and what the check after it
costs. `CCH-01` comes from the
9 November 2026 codebase review. It follows from the signature's fields as
written and from the repository's history; no stale read was staged to
show it. `CCH-02` comes from the review plan's sweep of pairs of operations
and was reproduced on 11 November with a second process holding one of the
snapshot's new files open. `CCH-03` comes from the plan's sweep of sizes
on the same day, measured again by the main session with a timer around
the check.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CCH-01 | Planned | P2 | A node output stored by one version of Haute's node code is not read as current by another. |
| CCH-02 | Planned | P3 | A snapshot is published while a scanner or an indexer still has one of its new files open, instead of the build failing with no reason given. |
| CCH-03 | Planned | P3 | A preview that stores many node outputs checks the store's budget once, not with a walk of the whole store after each one. |

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

### CCH-02 — A snapshot is published through the rename that waits
**Why:** Reproduced on 11 November 2026 with nothing patched. On Windows a
folder cannot be renamed while any program has a file inside it open.
Haute's own helper for renaming a folder says so, "as an antivirus scanner
or indexer does for a while after the file is written", and retries for
about four seconds; the workbench app's release build uses it. The
publication of an input snapshot does not: it renames its staging folder
once. A second process that opens one of the snapshot's new files, with full
sharing so that it refuses nothing, and holds it for one second makes the
build end "Input snapshot build failed." with nothing more. The server log
holds the `PermissionError` on the rename. The earlier snapshot stays
current, no staging folder is left, and the same build succeeds once the
file is let go. The plan's sweep of pairs of operations met the same failure
with no stand-in, once in 18 rebuilds started straight after a cancelled
build, which is why this is rated P3 on this machine; a machine whose
scanner is slower would meet it more often.

A node-output snapshot is published by the same bare rename, and a retired
generation is moved aside by another; both were read from the source and
not run. A Data Output's Write, run the same way against its staged copy,
waited the second out and succeeded.

**Plan:** Publish and retire snapshots through the shared folder rename, so
that a scanner's moment on a new file is waited out. Where the wait runs
out, say that another program has the snapshot's files open and that the
build can be run again (`IOL-01` owns the wording of a failed build).

**Acceptance:** With one of the staged files held open for one second by
another process, an input snapshot build and a node-output publication
complete. Held for longer than the wait, the build's message says that
another program has the files open, and the next build succeeds.

**Dependencies:** None. `IOL-01` for the message of a failed build.

**Evidence:** `src/haute/_file_ops.py::rename_directory`;
`src/haute/_source_cache.py::SourceCacheStore.build`;
`src/haute/_source_cache.py::_retire_generation_locked`;
`src/haute/_node_snapshots.py::publish_node_output`;
`src/haute/routes/input_cache.py::_build_failure`;
`src/haute/_workbench_app.py`.

### CCH-03 — A stored node output does not cost a walk of the whole store
**Why:** Measured on 11 November 2026, by the review plan's sweep of sizes
and again by the main session. A preview stores the output of each join it
crosses, a capture, so that later runs can read it. Each capture ends with
a check that the automatic captures still fit their budget, and that check
walks the whole store: every identity and generation, each one's files
sized and its facts, slot index and pointer read. One walk took 47, 91 and
195 ms on stores of 25, 50 and 100 generations. So a preview below k joins
pays k walks of a store that holds k captures.

The fixture is one 1,000-row input, k branches of it and k - 1 left joins
declared many-to-one, with a preview of 100 rows of the last join. Run as
the editor runs it, that preview took 4.8, 10.8 and 29.6 seconds below 24,
49 and 99 joins, while the first branch took half a second each time. With
a timer around the check, and the previews run in the server process:

| Joins | First preview | In the check | After an edit above | In the check |
|---:|---:|---:|---:|---:|
| 24 | 7.3 s | 1.0 s | 7.0 s | 2.5 s |
| 49 | 14.0 s | 3.5 s | 15.5 s | 7.6 s |
| 99 | 32.1 s | 12.8 s | 47.4 s | 29.3 s |

The check ran once per join each time. After an edit to the first branch
every join is captured again, each against a full store, so the check is
36, 49 and 62 per cent of the preview. A third preview with nothing changed
took 0.9, 1.3 and 3.8 seconds and captured nothing. The budget is 20 GiB or
a tenth of the free disk unless the project sets one, so on small data the
check finds nothing to evict and its whole cost is the walk. What is left
after the check, about a fifth of a second per captured join, is the cost
of the capture itself and is not this package's subject.

**Plan:** Check the budget once for a run, after its captures, instead of
after each. Or keep the automatic captures' total with the store's own
state, moved on a publish, an eviction and a Clear, and walk the store only
when that total is missing or passes the budget. Either way the walk stays
the source of truth for an eviction.

**Acceptance:** A preview that captures 99 node outputs walks the store at
most once, asserted by counting the walks. After it the automatic captures
are within their budget, and the existing eviction tests pass unchanged.

**Dependencies:** None.

**Evidence:**
`src/haute/_node_snapshots.py::NodeSnapshotStore.publish_node_output`;
`src/haute/_node_snapshots.py::NodeSnapshotStore.enforce_automatic_budget`;
`src/haute/_node_snapshots.py::NodeSnapshotStore._scan_usage`.
