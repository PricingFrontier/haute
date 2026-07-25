# I/O layer roadmap

## Scope

Canonical Data Input and Data Output nodes, retained API Input/response Output,
shredding, caches, formats, and editor workflows remain correct and explicit.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IO-IO03, IO-IO04, AUD-C12, IO-IO01, IO-IO02, IO-IO07 | Complete | P0 | Correct input, cache, and output contracts. |
| IO-IO05–IO-IO10 | Complete | P1 | Hardened writing, authoring, feedback, and shared-cache work. |
| IO-IO11–IO-IO12 | Audited | P2 | Selected hygiene delivered; no speculative format added. |

## Planned improvements

### IO-IO03 — Output document schema
**Audit:** Confirmed improvement. The final Polars construction still used its
default 100-row inference window, so a late nested field could disappear.

**Delivered:** Full-document schema inference (`infer_schema_length=None`) and
direct/generated null-first nested-field parity coverage.

**Why:** Nulls in early rows can erase later output fields.

**Plan:** Infer document structure across the complete bounded output, preserving nullable fields.

**Acceptance:** Null-first and nested heterogeneous-row round trips retain every field.

**Dependencies:** IO-IO07.

**Evidence:** `src/haute/_node_apply.py`; `tests/test_codegen_execution_equivalence.py`;
`docs/specs/json-shredding/high-level.md`.

### IO-IO04 — API Input infer/build contract
**Audit:** Confirmed improvement. Mixed scalars could infer as strings and then
fail during build; nested lists could become fabricated null scalar rows; some
unaddressable keys failed only after inference.

**Delivered:** Shared scalar coercion and key predicates, explicit list-shape
skip accounting, early actionable key rejection, real cache column metadata,
and infer/build route regressions including `.ndjson`.

**Why:** Inference and cache construction can accept different API values.

**Plan:** Share validation and coercion semantics between inference and cache build without fabricating losses.

**Acceptance:** Accepted/rejected payload matrices have identical infer/build outcomes and diagnostics.

**Dependencies:** None.

**Evidence:** `src/haute/_json_shred.py`; `src/haute/routes/json_cache.py`;
`tests/test_json_cache_corrupt_and_errors.py`.

### AUD-C12 — Dataframe-cache post-write validation
**Audit:** Confirmed correctness improvement. The first consumer could route a
freshly admitted artifact through ordinary validation and destructive eviction.

**Delivered:** Exact-entry store/first-consume checks bypass the ordinary
validator while later lookups retain corruption validation and eviction.

**Why:** Reopening a just-written Parquet artifact can evict a valid artifact after a transient read failure.

**Plan:** After writing under the lock, check presence without routing through validation/eviction.

**Acceptance:** A simulated transient reopen failure never evicts a newly stored valid artifact; ordinary corruption remains detected on later reads.

**Dependencies:** None.

**Evidence:** `src/haute/_dataframe_execution_cache.py`; `tests/test_dataframe_execution_cache.py`.

### IO-IO01 — Data Input picker honesty
**Audit:** Confirmed user-facing improvement. Browse defaults were handwritten,
blocking enumeration ran on the event loop, and known decoder failures lacked a
safe, specific response.

**Delivered:** Installed registry-derived extensions, case-insensitive
thread-pooled browsing with symlink/broken-entry hygiene, safe typed format and
decoder errors, and inline editor schema diagnostics.

**Why:** Picker/read failures and unsupported formats must be intelligible on canonical Data Input.

**Plan:** Reverify capability-driven availability and present specific read errors without advertising unsupported formats.

**Acceptance:** UI and route tests cover unavailable format, picker cancel, missing file, and malformed input.

**Dependencies:** IO-IO12.

**Evidence:** `frontend/src/panels/editors`; `src/haute/routes/files.py`;
`tests/test_files_routes.py`.

### IO-IO02 — Retained input config parity
**Audit:** Confirmed correctness improvement. Generated API Input and External
File bodies embedded declarative values already present in their sidecars.

**Delivered:** Executor and generated paths now use shared, validated
config-driven helpers; sidecar-only edits and malformed sidecars have
executable-equivalence coverage.

**Why:** API Input and External File paths still risk divergent persisted/runtime configuration.

**Plan:** Establish one validated source of truth for each retained path and migrate hand-edit behaviour to it.

**Acceptance:** Editing, reload, generated execution, and malformed-config tests agree for both paths.

**Dependencies:** IO-IO04.

**Evidence:** `src/haute/_node_apply.py`; `src/haute/_codegen_builders.py`;
`tests/test_codegen_execution_equivalence.py`.

### IO-IO07 — Generated Output parity
**Audit:** Verified as an improvement already embodied by the shared output
assembler. A second generated-only assembler would reduce correctness.

**Delivered:** No duplicate path was added. IO-IO03 fixes the shared assembler,
and generated execution now has the late nested-field parity regression.

**Why:** Generated Output behaviour can diverge from canvas and executor assembly.

**Plan:** Share output assembly semantics between generated and interactive paths.

**Acceptance:** Equivalent graphs produce matching schema, rows, and errors through both paths.

**Dependencies:** IO-IO03.

**Evidence:** `src/haute/_node_apply.py`; `src/haute/_codegen_builders.py`;
`tests/test_codegen_execution_equivalence.py`.

### IO-IO05 — Data Output write correctness
**Audit:** Confirmed correctness and UX improvement. Overwrite intent and
collision races were ambiguous, and durability/counting policy was incomplete.

**Delivered:** Strict opt-in overwrite with 409/retry UX, collision-safe atomic
publication, artifact and directory syncing, explicit BOM opt-in, and exact
registered-scanner row counts that preserve CSV header and dialect arguments.

**Benchmark decision (2026-07-24):** On Windows with Polars 1.39.3, a
representative 500,000-row, 8,778,238-byte CSV containing 50 quoted embedded
newlines wrote in 0.0198 s and an exact streaming `scan_csv().select(pl.len())`
rescan took a 0.0070 s median over five runs. Every rescan returned 500,000;
raw newline counting returned 500,050. The exact rescan remains the policy:
the measured cost did not justify an incorrect byte-line shortcut.

**Why:** BOM, overwrite, durability, and row-count behaviour need a defined Data Output contract.

**Plan:** Specify and implement explicit encoding, overwrite, atomic/durable write, and observable row-count policy. Treat exact CSV row counting as a benchmark-gated design: byte line counts are invalid for quoted newlines, while a second parse may be the honest cost.

**Acceptance:** Tests cover BOM/no-BOM, collision choice, failed write preservation, and accurate counts. The row-count decision records a representative workload and correctness fixture; a measured no-change decision retires the gate.

**Dependencies:** None.

**Evidence:** `src/haute/executor.py`; `src/haute/routes/pipeline.py`;
`tests/test_data_io_nodes.py`; `docs/specs/io-layer/high-level.md`.

### IO-IO08 — CSV authoring surface
**Audit:** Confirmed user-facing improvement. The capability declared bounded
schema requirements, but Data Input did not expose the detected schema workflow.

**Delivered:** Capability-gated fetch/progress/preview and **Use detected
schema**, merging the ordered dtype mapping while preserving delimiter and
other arguments.

**Why:** Canonical Data Input must author schema and arguments through a bounded end-to-end CSV workflow.

**Plan:** Complete the editor declaration surface and connect it to validation and execution.

**Acceptance:** UI-to-executor tests cover delimiter, schema, arguments, validation, and CSV preview/load.

**Dependencies:** IO-IO01.

**Evidence:** `frontend/src/panels/editors/DataInputEditor.tsx`;
`frontend/src/panels/editors/__tests__/DataInputEditor.test.tsx`.

### IO-IO06 — Data Output editor UX
**Audit:** Confirmed user-facing improvement. Component-local state lost pending
and completed writes across editor remounts and offered no safe collision flow.

**Delivered:** Destination/mismatch feedback, per-node request lifecycle state,
duplicate-write prevention, structured success/failure, and explicit 409
replacement confirmation.

**Why:** Destination, progress, and lifecycle feedback must describe Data Output, not retired editor concepts.

**Plan:** Provide destination validation, clear write progress, and completed/failed output state in the canonical editor.

**Acceptance:** UI tests cover valid/invalid destinations, progress, success, and failure.

**Dependencies:** IO-IO05.

**Evidence:** `frontend/src/panels/editors/DataOutputEditor.tsx`;
`frontend/src/stores/useOutputWriteStore.ts`;
`frontend/src/panels/editors/__tests__/DataOutputEditor.test.tsx`.

### IO-IO09 — Input editor feedback
**Audit:** Confirmed consistency improvement. API Input discarded schema errors,
while bounded Data Input did not expose inference state or recovery.

**Delivered:** Shared actionable `ApiError.detail` handling, visible
loading/error/recovery, and schema-mismatch guidance across retained editors.

**Why:** Inference, schema, and validation feedback differs between Data Input and API Input.

**Plan:** Use consistent inline diagnostics, status, and actionable fixes across retained input editors.

**Acceptance:** UI tests cover inference failure, schema mismatch, validation error, and recovery.

**Dependencies:** IO-IO04, IO-IO08.

**Evidence:** `frontend/src/hooks/useSchemaFetch.ts`;
`frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/hooks/__tests__/useSchemaFetch.test.ts`.

### IO-IO10 — Shred and load performance
**Audit:** The proposed correctness-preserving reuse was already present:
one logical load/build shares a content signature, while independent operations
rehash and detect same-size/same-mtime rewrites. A cross-operation stat-only
memo would weaken freshness and was rejected.

**Delivered:** Existing cache-hit/signature behavior was retained and
reverified. Completed build locks now use weak retention so unique historical
cache identities do not grow a process-lifetime lock map.

**Why:** Shredding, snapshotting, and loading must reuse the shared input cache without weakening correctness.

**Plan:** Reverify cache reuse and remove duplicate work while retaining freshness, schema, and validation checks.

**Acceptance:** Tests prove cache hits reduce work and changed/invalid input remains detected.

**Dependencies:** AUD-C12.

**Evidence:** `src/haute/_json_shred.py`;
`tests/test_json_shred_mut_lifecycle.py`; `tests/test_json_cache_integrity.py`.

### IO-IO11 — I/O hygiene
**Audit:** Confirmed as a set of independently useful, low-risk fixes after
excluding changes that would alter hot-path parsing or format semantics.

**Delivered:** Identifier-only bracket paths, `.ndjson` parity, honest compound
suffix diagnostics, real cached column metadata with legacy fallback,
weakly-retained build locks, browse hygiene, and case-preserving runtime path
selection even on case-insensitive filesystems. Config JSON continues to reject
duplicate keys; raw source JSON/NDJSON deliberately keeps decoder-native
duplicate-key handling so inference/build do not add a second parse.

**Why:** Small maintainability defects remain outside the semantic packages.

**Plan:** Apply individually verified low-risk cleanup with focused regression coverage.

**Acceptance:** Each selected improvement is covered without broadening format semantics.

**Dependencies:** None.

**Evidence:** `src/haute/_io.py`; `src/haute/_path_resolution.py`;
`src/haute/_jsonpath.py`; `tests/test_io.py`; `tests/test_path_resolution.py`.

### IO-IO12 — Optional format capabilities
**Audit:** Not approved for implementation as written. The registry and UI are
already capability-driven, and the roadmap names no user need, format, engine,
or boundedness contract. Adding one would be speculative.

**Decision:** No format was added. The acceptance bar remains in force for a
future concrete capability proposal.

**Why:** The registry is complete, but future formats require deliberate capability decisions.

**Plan:** Add optional formats one registered capability at a time, including validation, UI availability, and round-trip tests.

**Acceptance:** Every added format has explicit capability metadata and end-to-end supported/unsupported coverage.

**Dependencies:** IO-IO01.

**Evidence:** `src/haute/_io.py`; `frontend/src/panels/editors`; `tests/test_io.py`.
