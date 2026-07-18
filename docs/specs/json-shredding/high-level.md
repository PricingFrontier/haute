# JSON Shredding — High-Level Specification

## Purpose

This component turns nested, tree- or graph-shaped inputs into the flat, minimal
representations the rest of the pipeline actually consumes. Three unrelated-looking
problems share the same shape and are grouped here:

1. **JSON API-input shredding** — a JSON/JSONL document (arbitrarily nested objects
   and arrays) is "shredded" into one or more flat, typed tables (Polars frames),
   cached on disk as parquet so the pipeline never re-parses raw JSON at run time.
2. **Graph flattening** — a pipeline graph that contains submodel nodes (a saved
   sub-pipeline referenced as a single opaque node) is dissolved ("flattened") into
   one concrete graph the executor can run, with submodel boundary edges rewired to
   the real child nodes.
3. **Column-need flattening (projection)** — the minimal set of columns each node in
   a graph actually needs is computed by sweeping demand backward from terminal
   outputs to sources, so scans and intermediate frames can be narrowed instead of
   carrying every column through every node.

Two smaller shared utilities back these: a single path-grammar core used to address
locations inside a JSON document from both the input and output sides, and a
JSON-safe value encoder used whenever pipeline data crosses an HTTP boundary.

## Scope

In scope:

- Shredding a JSON/JSONL API input into per-table parquet caches, schema inference
  from sample data, and the dual-layer (working/committed) cache lifecycle around it.
- Dissolving submodel nodes into a flat, executable graph.
- Computing a graph-wide column projection plan (which columns each node and each
  edge needs) and the node-type-specific rules that drive it, including the
  mechanical column-routing rule for join nodes.
- Validating and executing `edgeJoin` node configuration (Polars join construction).
- The shared array-outer JSON path grammar (acceptance, canonical form, parsing).
- Converting arbitrary Python/pipeline values into JSON-safe payloads for API
  responses and preview rows.

Out of scope (owned elsewhere):

- The v2 apiInput schema's own type system, path-segment parsing, and validation
  rules (`_api_input_schema.py`) — this component consumes that schema but does not
  define it; see [io-layer](../io-layer/high-level.md).
- The v2 OUTPUT mapping schema and assembler that uses the OUTPUT side of the shared
  path grammar (`_output_assembler.py`) — see [io-layer](../io-layer/high-level.md).
- The executor's own backward column-demand analysis inside lazy execution
  (`_execute_lazy.py`'s `_compute_needed_columns` / `_compute_projection_plan`),
  which is a related but separately-tested implementation used during actual graph
  execution — see [execution-engine](../execution-engine/high-level.md).
- Parsing pipeline source files into a graph, including the submodel-merging code
  that calls into this component's flattener (`_parser_submodels.py`) — see
  [pipeline-config](../pipeline-config/high-level.md).
- The HTTP routes that drive JSON-cache build/status/delete
  (`routes/json_cache.py`) — see [server-api](../server-api/high-level.md).

## Behaviour

**JSON shredding.** Given a v2 schema config describing zero or more output
"tables" (each a JSON path plus a set of selected columns with declared types), the
shred walks every top-level record of a JSON or JSONL data file once and produces
one row buffer per table whose `emit` flag is on and which has at least one selected
column. Relational depth is defined purely by array (`[:]`) nesting — a 1-1 nested
object never starts a new table, its scalar leaves fold into the enclosing table as
dotted columns; only an array (of objects, or of scalars) starts a child table. A
scalar array produces a one-column child table (`value`) with one row per element.
Building the cache writes one parquet per emitting table plus a `meta.json`
manifest; the manifest carries a content fingerprint of the schema and a signature
of the source data file, so a later request can tell — without re-reading the JSON —
whether the cache is still valid for the schema currently configured and the data
file currently on disk. Schema inference can sniff a v2 config from a data file
directly, sampling optionally, widening column types across every record seen and
naming collision-free bare leaf keys as their own column names.

Every array element the shred sees is accounted for: it either becomes a row in some
table, or is counted as a skip against that table (its shape didn't match — an
object where a scalar was expected, or vice versa), or — for a non-object top-level
record — is counted as a skipped record. No element silently disappears.

**Graph flattening.** A pipeline graph that references submodels carries submodel
placeholder nodes plus the submodels' own internal node/edge lists. Flattening
removes the placeholder(s) for the targeted submodel(s) (or all of them), inlines
their internal nodes and edges into the parent graph, and rewires any edge that
crossed the submodel boundary to point at the real internal node the boundary handle
represents — including restoring the base/join role on an edge-join node that sat at
a submodel boundary.

**Column projection.** Given a graph and (optionally) an execution profile and a set
of caller-seeded required output columns, the planner computes, for every node, the
column set it needs to produce (or `None` meaning "all columns, opaque") and, for
every edge, the column set the child actually demands from that specific parent.
Demand starts at terminal/output nodes and is swept backward in reverse topological
order; most node types combine their own declared or inferred column contract with
their children's demand, but nodes whose contract can't be proven (opaque contracts,
unprovable user code, ambiguous multi-parent fan-in) either force their parents
opaque too or are routed through a node-type-specific rule (join key columns, a
ratebook banding source, single-parent Polars expression backward-analysis, …).
Some execution profiles require the plan to be provably narrow; for those, an
unprovable node raises rather than silently falling back to a full-width scan.

## Design rationale

**Object nesting is relationally transparent; only arrays create tables.** This is a
deliberate 2026-06-17 ruling: two fields nested inside different 1-1 objects at the
same array depth are siblings in the same table, because addressing through an
object never changes cardinality. Treating every nesting level as a new table would
produce a table explosion with mostly 1-row joins; folding 1-1 objects into dotted
columns keeps the shredded schema close to what a user actually wants to query.

**Every dropped element is counted, never silently discarded.** Earlier flattening
code resolved a shape mismatch (an array where an object was expected, mid-walk)
by silently taking the first element and dropping the rest — a conservation
violation that lost data with no trace. The shred now either resolves the shape
cleanly or fails loud (a genuine structural mismatch, e.g. a dotted leaf crossing a
non-empty array) or counts the loss (`ShredSkipStats`) so a build's summary can
report exactly how many records/rows were dropped and why. `build_per_port_cache`
additionally runs a conservation assertion at the root level — emitted-plus-skipped
must equal records-read — and raises `RuntimeError` if it doesn't, treating an
unaccounted discrepancy as a shred bug, not something to serve silently.

**Cache freshness is proven by content hash, not by timestamp alone.** A build
records the data file's size, mtime, and a full SHA-256; validity checks always
re-verify the hash rather than trusting a matching mtime, because a rewrite that
happens to preserve size and mtime (a deliberate `os.utime` restore, or a same-length
edit on a coarse-resolution filesystem) must not be served as fresh. The one-time
cost of hashing on every validity check was judged cheaper than a class of stale-data
bugs that would be silent and hard to reproduce.

**Cache writes are atomic and serialized per cache directory.** A build stages
everything (parquets + `meta.json`) in a sibling temporary directory and swaps it
into place with a rename dance that survives transient Windows file-handle locks
(retried with backoff) and restores the previous directory if the second rename
fails. A per-cache-directory lock prevents two concurrent builds of the *same* cache
from interleaving their write phases; builds of different caches remain independent.
This is the same swap primitive used both by the shred's own build and by promoting
`working/` to `committed/` at save time, so there is one atomic-publish
implementation, not two.

**Silent numeric/date coercion is rejected even though the underlying columnar
library would allow it.** Polars will silently coerce a Python `bool` into a numeric
column (`True → 1`) and will silently reinterpret a raw JSON integer or boolean
loaded into a `Date` column as a days-since-epoch offset. Both would produce a
column that "succeeds" but is wrong. The shred detects these shapes ahead of the
strict build and raises a specific, column-named error instead.

**Projection prefers a full-width fallback over a wrong narrow one.** Wherever the
planner cannot mechanically prove which parent produces a demanded column — an
opaque multi-parent fan-in, a join whose strategy or suffix rules out a mechanical
mapping, user code with unparseable or unprovable shape — it keeps the boundary
full-width (`None`, "read everything") rather than guess. Some execution profiles
escalate this to a hard failure instead of silently falling back, because a silent
full-width read defeats the point of running a bounded/streaming profile at all.

## Interactions

- Consumes the v2 apiInput schema type system and path-segment parsing from
  `_api_input_schema.py` — see [io-layer](../io-layer/high-level.md).
- The runtime entry point (`load_v2_api_source`) is called by both the eager
  executor's source builder and the generated/deploy code path, so both paths read
  identical cached frames — see [execution-engine](../execution-engine/high-level.md)
  and [codegen](../codegen/high-level.md).
- The projection planner's output (`ProjectionPlan`) is consumed by the lazy
  execution engine to decide scan/select column sets and by route/deploy callers
  that need to know a graph's column strategy ahead of running it — see
  [execution-engine](../execution-engine/high-level.md).
- Graph flattening is invoked by the pipeline parser's submodel-merge path — see
  [pipeline-config](../pipeline-config/high-level.md).
- The JSON-cache build/status/delete HTTP routes drive the build and cache-lifecycle
  functions in this component — see [server-api](../server-api/high-level.md).
- The shared path grammar (`_jsonpath.py`) is also used by the OUTPUT-mapping
  assembler on the io-layer side, so both INPUT and OUTPUT addressing stay
  single-sourced — see [io-layer](../io-layer/high-level.md).
- JSON-safe encoding is used wherever preview rows or route payloads carry pipeline
  values (dates, non-finite floats, oversized integers) out over HTTP — see
  [server-api](../server-api/high-level.md).

## Failure model

- A malformed v2 schema config (bad table/column shape) is rejected up front by
  schema validation before any shredding happens, not discovered mid-walk.
- A source JSON key that would collide with the reserved scalar-array sentinel, or
  that contains the object-nesting separator character, is rejected loudly at
  inference time — there is no way to address it unambiguously as a column, so
  inference refuses to manufacture a column path that would silently read the wrong
  value.
- A hand-edited config that mixes the scalar-array sentinel column with a real
  sibling column on the same table is rejected at shred time, not allowed to build a
  cache whose rows would all silently vanish as shape mismatches.
- A value that doesn't fit its declared column type — including the silent-coercion
  shapes Polars would otherwise accept — fails the build with an error naming the
  offending column and declared type.
- A root-level conservation violation (rows lost or duplicated without accounting)
  aborts the build with `RuntimeError` rather than writing a cache with unaccounted
  data loss.
- At runtime, a v2 apiInput with no emit-true tables, or emit-true tables with no
  selected columns, or a stale/missing cache in both the working and committed
  layers, raises `RuntimeError` with a message telling the user which UI action
  (ticking `emit`/a column, or clicking "Cache as Parquet") will fix it.
- Projection rules that cannot prove ownership of a demanded column either widen the
  boundary to full-width (non-strict profiles) or raise `ContractMismatchError` /
  `ProjectionImpossibleError` (strict profiles) — never guess a possibly-wrong
  narrow projection.
- `edgeJoin` node misconfiguration (ambiguous/missing base or join role, unsupported
  join strategy, mismatched key counts) raises `ConfigError` before any join runs.
