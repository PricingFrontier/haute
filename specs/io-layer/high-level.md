# IO Layer — High-Level Specification

## Purpose

The IO layer turns persisted Data Input and Data Output node configuration into
validated Polars operations. It also owns provider-neutral, immutable input snapshots for
sources that are not already Parquet. A file-backed Parquet Data Input is the canonical
direct-read case and is scanned from its configured source.

The component keeps source acquisition a distinct, contained operation. Explicit
snapshot builds acquire non-Parquet provider data and publish a verified Parquet
generation; pipeline execution reads that generation. A bounded execution that finds
that generation missing or stale may schedule the same build before it starts — only
through the explicit build path, inside a hard memory cap, warned and reported — and
then reads the published generation. File-backed Parquet skips the redundant build and
is scanned directly.

## Scope

In scope:

- the format and argument registry for canonical `dataInput` and `dataOutput` nodes;
- API-input flat-file adapters and common Polars collect/sink helpers;
- provider dispatch for file, lakehouse, inline, database, and Databricks inputs;
- source-cache identity, build, publication, lease, clear, and status behaviour;
- node-output snapshots in the same store: signatures, slots, column widening, retention and
  replacement, and cross-process publication and leases;
- bounded SQLite snapshot acquisition.

The [Databricks IO component](../databricks-io/high-level.md) owns Databricks credential
resolution, query validation, Arrow fetching, and Unity Catalog browsing. The
[caching component](../caching/high-level.md) consumes the source-cache identity contract
and owns execution/dataframe cache behaviour. HTTP job admission and responses belong
to [server API](../server-api/high-level.md).

## Behaviour

**Cache storage and the automatic-capture budget.** Input snapshots and explicit
builds have no byte or entry-count limit: they stay until the user refreshes or clears
them. Automatic node-output captures, which are the node outputs a preview captures
and no explicit build pins, share one byte budget: the pipeline settings' cache size
(`cache_size_gb`), automatically the smaller of 20 GiB and a tenth of the store's free
disk, read when the budget is applied. Each automatic capture that
publishes brings the captures back within the budget, evicting the least recently
leased first. Eviction never takes a pinned generation (an explicit build, or a
capture that has since replaced one in its slot), a generation any process leases, or
an input snapshot. Evicting a current generation is clearing it, so the next reader
computes that node again. An explicit build adds nothing the budget counts and
triggers no eviction, and no publication is ever refused for size. The preview status
bar shows the store's size, and the project cache inventory lists and clears entries.
Refresh still replaces previous data, and replacement, clearing and eviction preserve
active readers until their leases end. Actual filesystem errors, disk-headroom checks
and execution-memory limits remain enforced.

`dataInput` configurations select exactly one provider. There is no stored cache-mode
field: `data_input_is_direct` derives the execution mode, so a file-backed Parquet scan
reads directly and every other file format and every database, lakehouse, Databricks, or
inline input is snapshot-backed. A config still carrying the removed `cacheMode` field is
rejected as an inactive field, never migrated. Inline records
use a bounded snapshot build whose identity contains a digest of the logical records
rather than their raw values. `dataOutput` configurations select a registered file,
lakehouse, or database sink, or a Databricks table
([Databricks IO](../databricks-io/high-level.md)). Unknown fields, unsupported arguments, unavailable engines,
ambiguous locators, and invalid mode/provider combinations fail before provider access.

A file's columns are named by its header, or by the configuration, never by Polars.
A header-less CSV, Excel or ODS input (`has_header: false`) must name every column, in
order, with `new_columns` (or, for a CSV, a full `schema`, which binds a header-less file
by position), and one that names none is refused when its configuration is validated:
Polars 2 calls unnamed columns `column_0`, `column_1`, … where Polars 1 began at
`column_1`, so a pipeline that relied on those names would silently read the next
column. A `new_columns` list that names fewer or more columns than the file has is
refused with the two counts. For a spreadsheet Haute applies `new_columns` itself,
since no spreadsheet engine Polars uses takes it. A CSV `schema` is matched to the
header by name, as Polars 2 does: declared in another order it reads each column by
name, and one naming columns the header does not have, which renamed columns by
position under Polars 1, is refused with Haute's message pointing at `new_columns` (to
rename) and `schema_overrides` (to set types), never Polars' own hint
(`extra_columns='ignore'`), which would drop the mismatched columns. These checks read
the configuration and the file's first line, decoded as the read decodes it (a prefix
of a file in another encoding than UTF-8), and cost nothing beyond the scan. Haute's
own CSV reads pass `raise_if_empty=True` and `truncate_ragged_lines=False`, so an empty
file and a row with more fields than the header fail as they did. A scan of several
files infers its schema from the first ten (`infer_schema_files`), and a
`schema_overrides` list must cover every column; Haute's configurations give
`schema_overrides` as a mapping, which may name some.

A flat Quote Input's CSV sample, which carries no types of its own, is read with the types
its table declares, on every execution profile: the editor's preview, `haute run`, plain
Python and a deploy's input schema read one frame alike, as a bounded run needs a declared
type for every column it reads. A column the read takes that the table types nowhere is
refused before anything is read, naming the column and the file and asking for its type
in the node's table, never inferred: inferring it gave one type in a preview and a refusal
asking for settings a Quote Input cannot hold in a bounded run. A `date` column is read as
text and parsed in its declared `format` (ISO-8601 when it declares none), as the shredder
reads one. A Parquet sample carries its own types.

Raw database URIs are permitted only when credential-free. URI userinfo and recognised
secret-bearing query parameters are rejected by one shared validator before a sidecar,
cache identity, metadata document, or connector can receive the value. Named connection
references may resolve credentials from the environment, but the resolved URI never
enters cache identity or metadata. Provider diagnostics are scrubbed with the same
credential-name policy plus resolved in-process secret values before logging.

Snapshot identity includes source semantics: provider, canonical locator, validated query,
format/mode, and source arguments that can affect returned rows or schema. Databricks
`batch_size` is excluded because it changes fetch partitioning, not logical source data.
`code` is excluded because it does not change the acquired source bytes; the derived
direct-versus-snapshot execution mode is not part of the config at all.

A snapshot build writes a unique staging directory, validates the Parquet artifact and
metadata, atomically publishes an immutable generation,
and then replaces the current pointer. Cancellation, timeout, connector failure, schema
failure leaves the previous current generation readable.

Automatic preparation is that same build, scheduled by execution. Before an execution
plans its strategy it checks every snapshot-backed Data Input in the executed lineage
against the store with the current source signature: a current, fresh generation (or one
whose freshness is unknown because either side carries no signature) is reused; a missing
generation is built; a stale one is refreshed. An absent local source with a published
generation is reused with warning code `source_unavailable` and never refreshed; without a
generation the preparation is refused as `build_failed` before any worker starts. The build
runs under a hard cap or not at
all: in the current process when that process already runs inside an isolated worker
under a native cap, otherwise in a spawned hard-capped worker admitted from the
execution's own budget. That cap is native and mandatory for preparation whatever the
process-memory enforcement policy says, and is never replaced by RSS sampling: a host that
cannot install the cap reuses a ready-but-stale generation with warning code
`cap_unavailable_stale_reused`; only a missing generation is refused typed
(`cap_unavailable`). A caller that reads a snapshot-backed input outside an
execution (a standalone `Pipeline.run()` or `score()`, and deploy's resolution, the request
input's sample included) prepares it the same way first, under an admission of its own held
for the preparation alone (`prepare_snapshots_before_reading`), so a clean checkout runs
every way without a preview or `haute run` before it. A schema-only execution never
builds; resolution then reports the typed `input_snapshot_missing` rejection as before. The
exception is the inferred schema tier, open only to a schema-only caller that records
it (the assistant's plan verification): a local file input whose format has a lazy
scanner, and whose configuration that scanner accepts by argument name and value,
resolves its schema through the scanner with the node's own settings. CSV and NDJSON
infer types from a bounded number of rows (never the whole file), Parquet and Arrow IPC
read their file metadata, nothing is collected, and no snapshot is written; the first
preview still builds the snapshot. Eager-only formats, database, Databricks, lakehouse and
inline inputs, and configurations only the eager reader accepts keep the
`input_snapshot_missing` rejection, which then names the reason and the remedy "preview
this input first". The same caller resolves a structured Quote Input (API Input) table
with no snapshot at the declared schema tier: the table's schema is the node's own
request contract, the declared type of each selected column, so the request file is not
read, nothing is collected, no snapshot is written, and each such table is recorded with
the tier `declared`. A contract that does not validate (a selected column without a
declared type, for example) is refused by the contract validator, as a preview would
refuse it. Before a
build the engine
emits a structured warning naming the input, its identity digest, the build class, and the
reserved limit; afterwards every execution's terminal diagnostics list each input's
preparation record — reused, built, or refreshed, where it ran, its reserved limit,
elapsed time, rows, bytes, and generation — using digests and counts, never locators or
secrets. Within one process, concurrent executions needing the same identity share one
build. A build that fails for any reason publishes nothing and leaves the previous
generation readable. A spawned build carries a parent-chosen generation identifier and a
short parent-chosen staging token, so after the worker dies the parent reconciles exactly
those: a pointer already naming the generation means the build succeeded and is recorded
as such, an unreferenced renamed generation under that identifier or the staging directory
under that token is removed, nothing else is touched, and a fresh generation published
meanwhile by another process is reused rather than reported as a failure. Execution
and preview caches key a snapshot-backed input by its
generation pointer and its current source signature, so a mutated source misses every
cache and reaches preparation, and a refreshed generation changes the pointer and
therefore every later key. A source that is a folder or a glob pattern is signed by every
file it names, snapshot-backed or read directly, so adding, removing or rewriting one of
them is a mutation of the source.

A snapshot build inspects its source completely. A format that needs a declared schema in
bounded profiles (CSV) is built with a declaration when one is configured and otherwise
by whole-file schema inference before the streaming sink; sample-only inference never
decides a snapshot schema, so a late row cannot be mis-typed. When a format has a scanner
but eager `read` mode was configured, the build scans instead — with a recorded warning —
whenever every configured argument is one the scanner accepts and, where the scanner
narrows an argument's value domain (CSV `encoding`), its configured value, and runs the
eager reader
inside the hard-capped worker only when an argument is reader-only. Every supported
format therefore runs in a bounded profile once a build envelope can be reserved; a source
is refused only when its build cannot be isolated or admitted, or its parsing semantics
are invalid.

An explicit build (the preview frame's Import) is admitted like any execution and runs in
the same hard-capped worker as automatic preparation, never on a server thread, whatever its
build class. The worker shares the rows it has written with the server, so an Import counts
them as it goes wherever the build writes in steps: each batch of a database or Databricks
read, each slice of a Parquet or IPC file. A CSV is written in one streaming pass, which
reports nothing until it ends. A host with no native
process-memory cap (macOS) runs the same workers, automatic or explicit, supervised rather
than capped: the parent stops a worker whose memory passes its limit. Only an operator who
sets `HAUTE_WORKER_MEMORY_ENFORCEMENT=required` there is refused, as `cap_unavailable`.

Snapshot readers acquire an explicit generation lease. Within an execution request the
lease lasts until execution cleanup. Outside an execution request the returned scan owns a
lease token that is retained by every derived LazyFrame and released only after the scan
plan is no longer reachable. Refresh and clear never delete a live leased generation,
including one held by another process. A superseded input generation is normally retired after
`HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS` (default 1800) have elapsed since the current
generation was published. Explicit clear bypasses this grace period while preserving live leases.
Retiring a generation, an input snapshot's or a node output's, renames it out of selection under
the store-wide lease lock and deletes its files after the lock is released, so a reader's open
file never leaves a half-deleted generation behind: on Windows such a file refuses the rename,
and the generation stays whole and unpointed until a later retirement (the next release,
refresh, clear or publication) renames it. A renamed generation whose files could not be
deleted is swept the next time a process opens the store.

Node outputs live in the same store as a `node_output` provider. A
node-output identity is a slot — pipeline source file, node, source, and execution
semantics class — plus the node's
checked data signature. Each slot keeps one current dataset: publishing a new signature
replaces the previous one, so reverting an edit requires recomputation. A per-slot index lists a slot's identities:
for a requested signature the slot is `current` when that identity has a fresh generation,
`stale` when its own generation is stale or only other identities have generations, and
`missing` otherwise. Each generation records the column set it holds (`all` or an explicit
list) and the transitive closure of snapshot generations its rows derive from. A
generation is fresh when no recorded dependency identity now has a different current
generation; a cleared dependency does not make it stale. A writer always
continues from its own completed artifact. Under the identity's publication lock it
publishes only when every dependency it recorded is still current or cleared, its columns
contain the latest generation's columns, and there is no fresh latest generation, it widens
that generation, or it is an explicit refresh; otherwise it keeps its artifact as a
request-owned file and the outcome is `superseded`. A published generation therefore never
narrows its identity's columns, and replacing or widening a generation makes every
descendant recorded against the previous one stale.

A node-output generation is `pinned` when its slot's pin names its identity and `automatic`
otherwise; an explicit build pins, and a pin passes to the slot's newest publication. Its
last-used time is its metadata file's modification time, refreshed on lease at most once a
minute. Explicit datasets remain until clear or replacement; automatic captures
also leave under the automatic-capture budget, which a publication applies to other
slots' unpinned, unleased captures after it commits. Node-output publication, clear, and leases are coordinated across
processes: a per-identity publication lock is held from a writer's re-check to its first
lease, and a store-wide lease lock, always taken after it, makes lease acquisition,
publication through the publisher's first lease, and retirement atomic. Every lease is also
a marker file in its generation naming the owning process's token, whose liveness is an
exclusive lock that process holds on its token file, so retirement never removes a
generation another live process reads, and a dead owner's marker is removed. A process's
first store sweeps the token files of processes that have exited and the publication lock
files of identities that no longer exist, under a protocol that never leaves two processes
holding different files for one lock. A capture is not claimed while it is computed: two
consumers that resolve the same cold capture point at once (a preview while a training run
reads the same lineage, or an explicit build and an automatic capture of one node) both
compute it, and each publication applies the publication rule under the identity's lock, so
the later one either widens what the first published (an explicit build's every column over
a narrower capture) or is superseded (`node_snapshot_capture_superseded`). A later reader
seeds from a published generation that covers its demand. Retirement
renames a generation out of selection under the lock and deletes its files afterwards, as it
does for an input snapshot. Clear removes a slot's identities, pointers, and pin but never a
generation another operation still leases; that generation retires when released. Until it
does, as when an open Windows handle refuses its rename, its identity stays in the slot's
index, unpointed, as publication keeps a signature it superseded, so the slot's next
publication or Clear retires it. A reader may lease a named
generation, current or not, so a spawned worker reads exactly the generation its parent
leased.

Store startup never deletes a published generation. It may reclaim a staging directory only when the
newest filesystem activity beneath that directory is older than the configured stale-build
threshold; recent or unreadable staging state is preserved. Retained staging bytes are
included in the inventory, and physical disk-headroom checks remain enforced.

Registry input capabilities advertise `scan` whenever a format has a scanner;
reader-only formats advertise `read`, and declare the derived execution mode
(`cache_mode`) for that provider/format so the editor knows whether to render the cache
control. Stored `read` configurations remain valid and executable even when the
current capability payload advertises only `scan`.

SQLite snapshots open an existing database in read-only URI mode and start a read
transaction. One aggregate query determines every output column's observed SQLite storage
classes before the data cursor emits a batch; declared table types are hints only for empty
columns. Integer/real observations widen to a float, incompatible storage-class mixtures
fail before artifact output, and one Arrow schema is applied to every data batch. Missing
database files are rejected rather than created.

Outputs overwrite existing targets when the registered Polars sink has overwrite
semantics. Authoring-time publication of a new output sidecar is conflict-safe: an
unexpected existing sidecar is reported as a conflict rather than silently replaced.

File Data Output destinations are relative to the project root, independent of the
pipeline's directory and process working directory. A bare filename goes in the root's
`outputs/` folder: `output` or `output.parquet` with Parquet selected resolves to
`outputs/output.parquet`. An explicit relative path such as `exports/output.parquet`
is project-relative; an absolute path must remain inside the project, and a save and
`haute lint` refuse one, naming the path from the project root to write, as they refuse
every node path another checkout would read otherwise (a Windows drive letter, another
letter case than the file on disk; see "A path every checkout reads alike" in the
[low-level specification](low-level.md)). Missing extensions
come from the selected format; explicit extensions are preserved. Destination preview
and writing use the same resolution, and writing creates missing parent directories.
Database and lakehouse locators retain their provider-specific resolution.

**Reading an input's bytes.** One set of rules reads the bytes of an input's own file,
whichever node reads it (`haute._input_files`), so one file reads alike as a request, as
data and as a loaded object, and a file a feed writes is not refused for its form alone:

- A leading UTF-8 byte-order mark is read through: by the request shredder, Load File,
  every CSV read and a deployment's served request. Windows PowerShell 5.1 and some
  exports write one, and feeds are not the analyst's to control. Polars reads a Data
  Input's JSON and CSV through it too; its JSON Lines reader cannot, and a scan cannot
  skip the mark without copying the file, so such a file is refused naming it and the
  mark rather than in Polars' words.
- A JSON object that repeats a key keeps its last value in every reader Haute parses with
  (the request shredder, a served request, deploy's and the Workbench's test quotes, Load
  File), as orjson, Python and JavaScript read it, and nothing is refused for it. A Data
  Input's JSON is read by Polars, by its own rules: a top-level key repeated with one type
  keeps its first value and one repeated with two types becomes text, a key repeated inside
  a nested object is refused (`DuplicateError`), and in JSON Lines a nested object repeated
  with other fields keeps the later object's fields, empty. Making a Data Input agree would
  take a pass over every file before Polars reads it.
- A `.json` request file or Load File holding nothing is refused as empty, naming the
  file; a request file of no quotes is `[]`, and an empty JSON Lines file holds none.
  Where Polars reads a `.json` file (a Data Input, and deploy reading a request file's
  schema) and cannot, a file holding nothing, cut short or not UTF-8 is refused in those
  words, with its line and column, as the request shredder refuses it; JSON that Polars
  cannot read as a table keeps Polars' words.
- A CSV file saved as UTF-16 or UTF-32 (a byte-order mark of either, or null bytes in its
  first line, judged in the text a gzip, zlib or zstd file decompresses to, as Polars
  decompresses it) is refused naming the file and the encoding found and saying to save it as
  UTF-8, by both of a Data Input's CSV readers and by a Quote Input, unless a Data Input's
  read names its `encoding`: read as UTF-8 it gave columns named with null characters, a
  row the file does not hold and no word. A Latin-1 file was already refused, as invalid
  UTF-8.
- Load File reads a JSON file's bytes as UTF-8 strictly: a byte that is not UTF-8 is
  refused naming the file and the byte's line and column, never replaced, so no loaded
  value (or a key a lookup then never matches) changes without a word. Its JSON is
  Python's, as before, so `NaN`, integers of any size and a repeated key's last value
  load as they did.
- A file whose bytes cannot be read is an `InputFileError` naming the file from the
  project root and, for a fault in it, its line and column: the words every edge says,
  automatic preparation's `source_invalid` among them. So is a `PolarsIoConfigError`, so
  its message names a configured path only as `display_file` names it and never holds a
  caught exception's text: an engine that fails to import is named by its module, the
  import's own message logged (`tests/test_polars_io_registry.py` holds every message to
  this).

These rules are for an input's own file. Haute's own files (a node's settings, the
pipeline settings) keep their own readers' rules.

**Column kinds.** Every place that reads a column by its kind (the pivot, the frame
profile, the Banding statistics, a Rating lookup, the optimiser's analysis, the impact
report, the editor's column lists) asks one module what kind the column's type is:
integer, float, decimal, boolean, text, category, date, datetime, duration, time,
binary, null, nested, object or extension. The answer comes from Polars' own
classification, so a float of any width (`Float16`, `Float32`, `Float64`) is a float
everywhere and a type a later Polars adds takes its kind from Polars. The editor reads
the same answer from the generated contract. A reader handles a column as its kind or
refuses it naming its type; no reader keeps its own list of type names.

## Design rationale

The registry is the single capability source for validation, editor metadata, and Polars
dispatch, preventing code generation from inventing a second format matrix. Snapshot
generation directories and a tiny atomic pointer make refresh safe for concurrent readers.
Strict identity validation prevents durable cache metadata becoming a credential leak.

Column kinds come from one module because every list of type names kept by hand went
stale the same way: each named `Float32` and `Float64` as the floats, none named
`Float16`, and each was found as its own defect in its own area. A test finds code that
decides a kind from a type's name, on the server and in the browser, so the next list
fails a test instead of a reader.

A datetime, time or duration value reaches Haute's own Python code only as its exact
text, through one module, because Python's `datetime`, `time` and `timedelta` hold
microseconds while a `Datetime("ns")` (the unit pandas writes to Parquet), a
`Duration("ns")` and every `Time` hold nanoseconds. Read into Python, two values 100 ns
apart become one and a value loses its last three digits, and every site that read
them so lost them the same way: an OUTPUT document merging two events, a preview
showing six digits, a trace failing to find its own row, a ratebook counting two
factor levels as one. Text comes back to a column through the same module, as a
literal of the column's own type, refused when it is finer than the column holds. A
test finds every module that reads a frame's values into Python and requires each to
be sent such values or to say why it never reads one.

Parquet is the shared snapshot boundary because it is lazy-scannable, schema-bearing, and
can be written in bounded batches. Publication computes SHA-256 and seeds a process-local
verification memo. The first open of a generation not already in that memo rechecks SHA-256;
later opens reuse the verification while `(mtime_ns, size, recorded digest)` is unchanged.
Footer, schema, row count, and metadata checks still run on every open.

## Interactions

- [Databricks IO](../databricks-io/high-level.md) supplies bounded Arrow batches.
- [Caching](../caching/high-level.md) defines checked identity inputs and consumes snapshot
  generations alongside execution caches.
- [Execution engine](../execution-engine/high-level.md) supplies profiles, cancellation,
  stages, and lifecycle cleanup.
- [Server API](../server-api/high-level.md) owns explicit snapshot build/status/cancel/clear
  routes and conflict responses.
- [Sandbox security](../sandbox-security/high-level.md) owns project-root path containment.

## Failure model

Configuration and credential-safety errors are loud `ValueError` subclasses before I/O.
Unsupported bounded-memory operations fail rather than falling back to eager collection.
Connector, cancellation, deadline, and schema failures abort staging and preserve the
previous pointer.

Automatic preparation surfaces its outcome as `InputPreparationError` with a stable reason
code (`cap_unavailable`, `build_failed`, `source_invalid`, `memory_limited`, `cancelled`,
`timed_out`, `path_too_long`) and the node's identity digest. `path_too_long` is a cache path Windows refused
for its length: a project in a folder with a long path, while Windows' long paths are off. A
host that cannot install the required
native cap is refused typed rather than built without one. `source_invalid` is a build that
failed for a reason Haute names, said after the fixed sentence in the words every edge gives
it, so `haute run`, a preview and the input's panel say one thing of one file: a value its
column's type does not hold (the shredder's schema error, with its remedies), a file Haute
cannot read as its format (`InputFileError`, naming the file from the project root and the
line and column of the fault), a setting or a reader package Haute refuses
(`PolarsIoConfigError`), or a file the operating system refused or that is not there, named.
A failure nothing names keeps the fixed sentence alone, as `build_failed`, since a provider's
message can hold a connection string. It is a public contract error:
synchronous routes answer HTTP 422 with its payload, and background jobs record the
`memory_limited` terminal state for `memory_limited` and the contract-error fields
otherwise. A corrupt generation is never rebuilt automatically.

When `overwrite=false`, an existing data-output destination raises
`DataOutputDestinationExistsError`; the server maps that explicit refusal to HTTP 409 rather
than treating it as an I/O failure or replacing the destination.

Two Data Output nodes cannot target one destination. A destination's identity
(`_executable_names.data_output_destination`) is, for a file or lakehouse target, the path the
writer resolves (`resolve_data_output_path`: a bare file name under `outputs/`, the format's
default extension added, a relative path anchored as the writer anchors it, so a relative and
an absolute spelling of one file are one destination), compared ignoring case on Windows and
macOS; for a database target, its connection (or
URI) and its table as written, so one table name in two schemas is two destinations. A
destination missing a required part (a new Data Output starts with an empty path) has no
identity: it is not compared, stays an editable, saveable draft, and its completeness is
required only when it runs. A Data Output inside a submodel definition writes once per
occurrence. The check is part of the codegen naming rule's graph validation, so save, the
editor load, `haute run`, deploy and canvas execution all refuse two nodes writing one
destination before anything is written.

Malformed pointers, digest mismatches, metadata mismatches, invalid generation identifiers,
linked, hard-linked, reparse-point, or escaping generation artifacts, or invalid Parquet
footer/schema evidence raise `SourceCacheCorruptError`, and callers do not silently rebuild or
fall back. A generation that is gone is missing, not corrupt: a current pointer naming a
generation whose directory no longer exists (evicted by another process, deleted by hand)
reads as absent, as a generation in a retired layout does, so its status and the cache
inventory report `missing`, a current lease finds no snapshot, and preparation, an explicit
build or the next capture builds it again. Leasing a named generation that does not exist or
was retired raises the `SourceCacheGenerationMissingError` subclass, which its caller answers
as data that moved on. Transient operating-system access errors propagate as operating-system
errors so operators can retry and are not told durable data is corrupt.
