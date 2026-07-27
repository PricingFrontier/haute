# JSON Shredding — High-Level Specification

## Purpose

This component turns nested, tree- or graph-shaped inputs into the flat, minimal
representations the rest of the pipeline actually consumes. Two related problems
are grouped here:

1. **JSON API-input shredding** — a JSON/JSONL document (arbitrarily nested objects
   and arrays) is "shredded" into one or more flat, typed tables (Polars frames),
   using a valid parquet cache as a fast path when present and otherwise parsing
   the source directly for that execution.
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
  the shared edge-join demand-narrowing rule that planner consumes.
- Submodel definition, boundary rewiring, and expansion into an executable graph —
  see [submodels](../submodels/high-level.md).
- The HTTP routes that drive JSON-cache build/status/delete
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
of the source data file. Every table entry also records the size and SHA-256 of its
derived parquet, so payload corruption is rejected before the footer-only schema
probe can accept it. Caching is an optional performance prewarm: runtime first
tries signed, readable, exact-schema `working/` then `committed/` parquets; when
neither can serve, it applies the same parsed table specs, shredding, type checks,
skip accounting, and conservation guards directly in memory without creating or
refreshing cache files. Schema inference can sniff a v2 config from a data file
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
the same single array-outer path grammar: `$[:]` at the root, dotted ASCII
identifier keys, and `[:]` for array traversal. Its explicit structural
validator rejects same-port duplicate or prefix-comparable destinations and a
single source frame mapped to divergent emit prefixes. The runtime assembler invokes
that validator before frame collection, so dry-run, direct runtime, generated, and
deployed execution share one acceptance boundary. Validation scales with the
mapping set rather than repeatedly reparsing every pair of paths. Incomplete
editor rows are inactive and ignored consistently.
Assembly returns a top-level list of objects:
sibling array branches are nested independently (never cross-multiplied),
same-level frames use a deterministic cut plan and bag semantics, unmatched
partials survive, and null-valued/empty-collection object fields are pruned
from the rendered document (null or empty-list elements already inside arrays
remain array elements). A relation key is checked only in frames that actually
carry that key: a missing column in another mapping frame is absence, not a null.
An actual null relation-key component raises `OutputNestingKeyError`; nullable
non-key payloads remain valid. In shredding, an ancestor `$value` column does not
turn a descendant object table into a scalar table — scalar classification requires
the sentinel at the table's own array depth.

The complete bounded output document uses full-document schema inference,
preserving a nested field whose first non-null value occurs beyond a sampling
window. Input inference and shredding use one
JSON-scalar compatibility rule: genuine scalars can render deterministically into a
declared string, while objects and arrays remain shape values and fail or count as
shape mismatches. Inference rejects source keys outside the canonical ASCII
identifier grammar (and the reserved `$value` sentinel) before returning a schema.

**Edge Join semantics.** The built-in `edgeJoin` accepts exactly the Polars
strategies `inner`, `left`, `right`, `full`, `semi`, `anti`, and `cross`.
`cross` rejects every key field. Every other strategy requires either a
non-empty `on` key (one name or a list shared by both frames) or both
non-empty `leftOn` and `rightOn` keys with equal lengths; `on` cannot coexist
with the paired form. The base and join frames are explicit roles, not inferred
from edge order, and both connected source ids must be distinct.

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
non-empty array) or counts the loss so a build's summary can report exactly how
many records/rows were dropped and why. The cache build additionally runs a
conservation assertion at the root level — emitted-plus-skipped
must equal records-read — and raises `RuntimeError` if it doesn't, treating an
unaccounted discrepancy as a shred bug, not something to serve silently.

**Cache freshness and integrity are proven by content hashes.** A build records the
data file's size, mtime, and full SHA-256, plus each emitted parquet's size and full
SHA-256 after writing it. Public validity re-hashes the source and every candidate
artifact: a readable footer does not prove its data pages are intact. Runtime goes
further to close the hash-then-reopen race: it reads each compressed parquet exactly
once, verifies size/SHA-256 over that exact payload, and gives those same bytes to
Polars as an in-memory lazy scan. A rewrite that preserves size and mtime, or a
damaged data page beneath an unchanged footer, is therefore never served.

The compressed byte snapshot also pins a returned LazyFrame (including derived
plans) to the generation it selected, even if the sole on-disk cache generation is
later rebuilt, mirrored, or explicitly cleared. Parquet decode and projection remain
lazy, but the full compressed source is read and copied into memory up front and is
retained while its lazy plans remain live. Disk use stays bounded to one generation;
memory use scales with the compressed artifacts referenced by active plans.

Save-time promotion first requires a well-formed v2 mode/schema fingerprint, a
recorded source signature that still matches the data file, and intact signed working
artifacts. It then validates the staged metadata and artifact bytes again before
publish. Both signed layers are verified before declaring a no-op, so invalid, stale,
or concurrently changed working state cannot replace a healthy committed cache and
damaged committed bytes are repaired from healthy working state.

**Cache writes are fully staged and same-process builds for one cache cannot
interleave.** A build writes every parquet and its manifest into a unique sibling
staging directory before replacing the live directory. Replacement attempts to
restore the prior directory if publication fails. Builds for different cache
directories remain independent, and the same staged-replacement behavior governs
both a new shred and working-to-committed promotion. Cache paths are rooted from
the process working directory selected for the project; callers must not assume
they are relative to the source file. The reader-visibility limitation of this
replacement is documented in the low-level specification.

Within one logical raw-file load or cache-build operation, the source signature
(size, mtime, and SHA-256) is computed once and shared by its consumers. A separate
operation recomputes the signature, so same-size/same-mtime rewrites remain
detectable.

**Silent numeric/date coercion is rejected even though the underlying columnar
library would allow it.** Polars will silently coerce a Python `bool` into a numeric
column (`True → 1`) and will silently reinterpret a raw JSON integer or boolean
loaded into a `Date` column as a days-since-epoch offset. Both would produce a
column that "succeeds" but is wrong. The shred detects these shapes ahead of the
strict build and raises a specific, column-named error instead.

## Interactions

- Owns the v2 apiInput type/path boundary; the shred, cache route, executor,
  and editor consume that one validation contract.
- The eager executor and generated/deploy code consume the same runtime loader,
  so both paths share identical cache-fast-path and direct-shred behaviour — see
  [execution-engine](../execution-engine/high-level.md) and
  [codegen](../codegen/high-level.md).
- The execution engine's projection planner consumes the same edge-join
  demand-narrowing rule as runtime join construction —
  see [execution-engine](../execution-engine/high-level.md).
- The JSON-cache build/status/delete HTTP routes owned by
  [caching](../caching/high-level.md) drive this component's build and cache lifecycle.
- The shared path grammar is used by both the INPUT codec and
  this component's OUTPUT-mapping assembler, so both addressing directions stay
  single-sourced.
- JSON-safe encoding is used wherever preview rows or route payloads carry pipeline
  values (dates, non-finite floats, oversized integers) out over HTTP — see
  [server-api](../server-api/high-level.md).

## Failure model

- A malformed v2 schema config (bad table/column shape) is rejected up front by
  schema validation before any shredding happens, not discovered mid-walk.
- Every OUTPUT assembly entry point calls the structural validator before frame
  collection. Malformed syntax, duplicate/prefix conflicts, divergent per-frame
  emit prefixes, and missing source ports/columns therefore fail loudly rather than
  becoming ambiguous or empty output.
- Any active parent or child row whose *present* simple/composite relation key has a
  null component raises `OutputNestingKeyError(OutputMappingSchemaError)` with
  `frame`, `output_path`, and `key`; the HTTP adapter maps it to 422. A frame that
  does not carry that relation key is not participating and is not treated as a null
  row. Null scalar payloads outside relation keys remain valid.
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
  selected columns, raises `RuntimeError` with a message telling the user to tick
  `emit` or select a column. A stale, missing, corrupt, or schema-mismatched cache
  is not a runtime error: the loader tries the next layer, then shreds the raw
  source directly. Raw-file decode, missing-file, and declared-type failures stay
  loud and specific; the direct path never replaces them with a cache prompt.
- `edgeJoin` node misconfiguration (ambiguous/missing base or join role, unsupported
  join strategy, keys on `cross`, missing/conflicting key forms, or mismatched key
  counts) raises `ConfigError` before any join runs.
