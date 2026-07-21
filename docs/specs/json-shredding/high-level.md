# JSON Shredding — High-Level Specification

## Purpose

This component turns nested, tree- or graph-shaped inputs into the flat, minimal
representations the rest of the pipeline actually consumes. Two related problems
are grouped here:

1. **JSON API-input shredding** — a JSON/JSONL document (arbitrarily nested objects
   and arrays) is "shredded" into one or more flat, typed tables (Polars frames),
   cached on disk as parquet so the pipeline never re-parses raw JSON at run time.
2. **JSON output assembly** — flat frames plus output-path mappings can be
   structurally validated and are re-nested into one deterministic array-outer
   response document without cross-multiplying independent sibling arrays.

Two smaller shared utilities back these: a single path-grammar core used to address
locations inside a JSON document from both the input and output sides, and a
JSON-safe value encoder used whenever pipeline data crosses an HTTP boundary.

## Scope

In scope:

- Shredding a JSON/JSONL API input into per-table parquet caches, schema inference
  from sample data, and the dual-layer (working/committed) cache lifecycle around it.
- The v2 apiInput schema codec: shape recognition, shared table/column path
  parsing, canonical path writing, structural validation, and filesystem-safe
  table labels.
- The v2 OUTPUT mapping contract and document assembler, including same-level
  cyclic-table cut planning, bag-natural joins, array-prefix nesting, and the
  final response-document shape.
- Validating and executing `edgeJoin` node configuration (Polars join construction).
- The shared array-outer JSON path grammar (acceptance, canonical form, parsing).
- Converting arbitrary Python/pipeline values into JSON-safe payloads for API
  responses and preview rows.

Out of scope (owned elsewhere):

- Graph-wide column projection planning (`projection.py`) and the executor's
  backward column-demand analysis belong to
  [execution-engine](../execution-engine/high-level.md). This component supplies
  the shared edge-join demand-narrowing helper that planner consumes.
- Submodel definition, boundary rewiring, and expansion into an executable graph —
  see [submodels](../submodels/high-level.md).
- The HTTP routes that drive JSON-cache build/status/delete/cancel
  (`routes/json_cache.py`) — see [caching](../caching/high-level.md).

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
naming collision-free bare leaf keys as their own column names. Inferred table
labels are readable identifiers derived from the source key names — the root
table is `root`, `$[:].proposer.claims[:]` becomes `claims`, and two levels
sharing a key name qualify symmetrically (`a_items`/`b_items`) — never raw path
strings, so an inferred schema is immediately valid under the label rule below
and its labels read as the argument names they will become.

Every array element the shred sees is accounted for: it either becomes a row in some
table, or is counted as a skip against that table (its shape didn't match — an
object where a scalar was expected, or vice versa), or — for a non-object top-level
record — is counted as a skipped record. No element silently disappears.

**V2 schema and output document.** A v2 apiInput is recognised only by a
`tables` list. Each table has a non-empty label — unique case-insensitively,
because labels become parquet filename stems and Windows/macOS filesystems
fold case — that must be an ASCII Python identifier and not a hard keyword — the label is the frame's identity
end-to-end (canvas handle, downstream input name, and the generated
function's parameter), so it is constrained to what an argument name can be,
with zero transformation anywhere. ASCII is deliberate, not incidental:
Python NFKC-normalises source identifiers, so a non-NFKC Unicode label would
silently become a *different* parameter name when the generated file is
parsed — the exact hidden mapping this rule forbids — and ASCII lets the
frontend mirror the rule exactly instead of approximating Unicode
`str.isidentifier()`. Each table also carries a non-empty path,
unique column names, known column types, and an optional row-ID column that must name one of its own
columns. Table paths end at an array boundary; columns may live at that boundary
or at an ancestor boundary so an ancestor value can be distributed into child
rows. The OUTPUT side consumes only active, complete mapping rows and requires
the same array-outer path grammar (parseable bracket-name spellings are
accepted; the writer emits the canonical dotted form). Its explicit structural
validator rejects same-port duplicate or prefix-comparable destinations.
Assembly returns a top-level list of objects:
sibling array branches are nested independently (never cross-multiplied),
same-level frames use a deterministic cut plan and bag semantics, unmatched
partials survive, and null-valued/empty-collection object fields are pruned
from the rendered document (null or empty-list elements already inside arrays
remain array elements).

## Design rationale

**Object nesting is relationally transparent; only arrays create tables.** This is a
deliberate 2026-06-17 ruling: two fields nested inside different 1-1 objects at the
same array depth are siblings in the same table, because addressing through an
object never changes cardinality. Treating every nesting level as a new table would
produce a table explosion with mostly 1-row joins; folding 1-1 objects into dotted
columns keeps the shredded schema close to what a user actually wants to query.

**Every dropped element is counted, never silently discarded.** Earlier JSON-input
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

**Cache writes are fully staged and same-process builds are serialized per cache
directory.** A build writes everything (parquets + `meta.json`) into a unique sibling
temporary directory before publishing it. The publish helper renames the existing
directory aside and then renames the completed staging directory into place; it
retries transient Windows file-handle locks and attempts to restore the previous
directory if the second rename raises. A process-local per-cache lock prevents two
threads building the *same* cache from interleaving their write phases; builds of
different caches remain independent. The same swap primitive is used both by the
shred's own build and by promoting `working/` to `committed/` at save time.

> NOTE: Replacing an existing directory is a two-rename swap, not one atomic
> filesystem operation. Between `live -> backup` and `temp -> live`, a concurrent
> reader can observe the live path as absent. The lock covers builders and promotion
> in this process, but not readers or other processes. An abrupt process exit in that
> window (or a failed restoration) can also leave only the uniquely named backup;
> the next successful swap only cleans the legacy fixed `.build-old` name, not those
> UUID backups. Tests cover same-process builder serialization, staged-write failure,
> transient rename retry, and synchronous restoration attempts, but not concurrent
> readers, cross-process publishers, or interruption between the two renames.

**Silent numeric/date coercion is rejected even though the underlying columnar
library would allow it.** Polars will silently coerce a Python `bool` into a numeric
column (`True → 1`) and will silently reinterpret a raw JSON integer or boolean
loaded into a `Date` column as a days-since-epoch offset. Both would produce a
column that "succeeds" but is wrong. The shred detects these shapes ahead of the
strict build and raises a specific, column-named error instead.

## Interactions

- Owns the v2 apiInput type/path boundary in `_api_input_schema.py`; the shred,
  cache route, executor, and editor consume that one validation contract.
- The runtime entry point (`load_v2_api_source`) is called by both the eager
  executor's source builder and the generated/deploy code path, so both paths read
  identical cached frames — see [execution-engine](../execution-engine/high-level.md)
  and [codegen](../codegen/high-level.md).
- The execution engine's projection planner consumes `_edge_join.py`'s shared
  demand-narrowing rule so static planning and runtime join construction agree —
  see [execution-engine](../execution-engine/high-level.md).
- The JSON-cache build/status/delete HTTP routes owned by
  [caching](../caching/high-level.md) drive the build and cache-lifecycle functions
  in this component. Its compatibility cancel route does not stop a shred build.
- The shared path grammar (`_jsonpath.py`) is used by both the INPUT codec and
  this component's OUTPUT-mapping assembler, so both addressing directions stay
  single-sourced.
- JSON-safe encoding is used wherever preview rows or route payloads carry pipeline
  values (dates, non-finite floats, oversized integers) out over HTTP — see
  [server-api](../server-api/high-level.md).

## Failure model

- A malformed v2 schema config (bad table/column shape) is rejected up front by
  schema validation before any shredding happens, not discovered mid-walk.
- The OUTPUT dry-run route calls the structural validator before execution, so
  its malformed mappings raise `OutputMappingSchemaError` before data is
  assembled. The lower-level runtime assembly entry point does not call that
  validator itself; malformed path syntax is still rejected while parsing, but
  duplicate/prefix conflicts can instead reach Polars or produce ambiguous
  assembly if a caller skipped validation. A missing source port/column still
  surfaces from normal mapping/Polars lookup rather than being replaced with an
  empty document.
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
- `edgeJoin` node misconfiguration (ambiguous/missing base or join role, unsupported
  join strategy, mismatched key counts) raises `ConfigError` before any join runs.
