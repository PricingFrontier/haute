# I/O layer roadmap

## Scope

Canonical Data Input and Data Output nodes, retained API Input/response Output,
shredding, caches, formats, and editor workflows remain correct and explicit.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IO-IO03, IO-IO04, AUD-C12, IO-IO01, IO-IO02, IO-IO07 | Active | P0 | Correct input, cache, and output contracts. |
| IO-IO05–IO-IO10 | Active | P1 | Harden writing, authoring, feedback, and shared-cache work. |
| IO-IO11–IO-IO12 | Active | P2 | Maintain hygiene and choose additional format capabilities. |

## Planned improvements

### IO-IO03 — Output document schema
**Why:** Nulls in early rows can erase later output fields.

**Plan:** Infer document structure across the complete bounded output, preserving nullable fields.

**Acceptance:** Null-first and nested heterogeneous-row round trips retain every field.

**Dependencies:** IO-IO07.

**Evidence:** `src/haute/_io.py`; `tests/test_io.py`; `docs/specs/io-layer/high-level.md`.

### IO-IO04 — API Input infer/build contract
**Why:** Inference and cache construction can accept different API values.

**Plan:** Share validation and coercion semantics between inference and cache build without fabricating losses.

**Acceptance:** Accepted/rejected payload matrices have identical infer/build outcomes and diagnostics.

**Dependencies:** None.

**Evidence:** `src/haute/routes/json_cache.py`; `src/haute/_config_io.py`; `tests/test_json_cache.py`.

### AUD-C12 — Dataframe-cache post-write validation
**Why:** Reopening a just-written Parquet artifact can evict a valid artifact after a transient read failure.

**Plan:** After writing under the lock, check presence without routing through validation/eviction.

**Acceptance:** A simulated transient reopen failure never evicts a newly stored valid artifact; ordinary corruption remains detected on later reads.

**Dependencies:** None.

**Evidence:** `src/haute/_dataframe_execution_cache.py`; `tests/test_dataframe_execution_cache.py`.

### IO-IO01 — Data Input picker honesty
**Why:** Picker/read failures and unsupported formats must be intelligible on canonical Data Input.

**Plan:** Reverify capability-driven availability and present specific read errors without advertising unsupported formats.

**Acceptance:** UI and route tests cover unavailable format, picker cancel, missing file, and malformed input.

**Dependencies:** IO-IO12.

**Evidence:** `frontend/src/panels/editors`; `src/haute/routes`; `tests/test_io.py`.

### IO-IO02 — Retained input config parity
**Why:** API Input and External File paths still risk divergent persisted/runtime configuration.

**Plan:** Establish one validated source of truth for each retained path and migrate hand-edit behaviour to it.

**Acceptance:** Editing, reload, generated execution, and malformed-config tests agree for both paths.

**Dependencies:** IO-IO04.

**Evidence:** `src/haute/_config_io.py`; `src/haute/_io.py`; `tests/test_io.py`.

### IO-IO07 — Generated Output parity
**Why:** Generated Output behaviour can diverge from canvas and executor assembly.

**Plan:** Share output assembly semantics between generated and interactive paths.

**Acceptance:** Equivalent graphs produce matching schema, rows, and errors through both paths.

**Dependencies:** IO-IO03.

**Evidence:** `src/haute/codegen`; `src/haute/_io.py`; `tests/test_io.py`.

### IO-IO05 — Data Output write correctness
**Why:** BOM, overwrite, durability, and row-count behaviour need a defined Data Output contract.

**Plan:** Specify and implement explicit encoding, overwrite, atomic/durable write, and observable row-count policy. Treat exact CSV row counting as a benchmark-gated design: byte line counts are invalid for quoted newlines, while a second parse may be the honest cost.

**Acceptance:** Tests cover BOM/no-BOM, collision choice, failed write preservation, and accurate counts. The row-count decision records a representative workload and correctness fixture; a measured no-change decision retires the gate.

**Dependencies:** None.

**Evidence:** `src/haute/_io.py`; `tests/test_io.py`; `docs/specs/io-layer/high-level.md`.

### IO-IO08 — CSV authoring surface
**Why:** Canonical Data Input must author schema and arguments through a bounded end-to-end CSV workflow.

**Plan:** Complete the editor declaration surface and connect it to validation and execution.

**Acceptance:** UI-to-executor tests cover delimiter, schema, arguments, validation, and CSV preview/load.

**Dependencies:** IO-IO01.

**Evidence:** `frontend/src/panels/editors`; `src/haute/_io.py`; `tests/test_io.py`.

### IO-IO06 — Data Output editor UX
**Why:** Destination, progress, and lifecycle feedback must describe Data Output, not retired editor concepts.

**Plan:** Provide destination validation, clear write progress, and completed/failed output state in the canonical editor.

**Acceptance:** UI tests cover valid/invalid destinations, progress, success, and failure.

**Dependencies:** IO-IO05.

**Evidence:** `frontend/src/panels/editors`; `frontend/src/**/*.test.tsx`; `docs/specs/io-layer/high-level.md`.

### IO-IO09 — Input editor feedback
**Why:** Inference, schema, and validation feedback differs between Data Input and API Input.

**Plan:** Use consistent inline diagnostics, status, and actionable fixes across retained input editors.

**Acceptance:** UI tests cover inference failure, schema mismatch, validation error, and recovery.

**Dependencies:** IO-IO04, IO-IO08.

**Evidence:** `frontend/src/panels/editors`; `frontend/src/**/*.test.tsx`.

### IO-IO10 — Shred and load performance
**Why:** Shredding, snapshotting, and loading must reuse the shared input cache without weakening correctness.

**Plan:** Reverify cache reuse and remove duplicate work while retaining freshness, schema, and validation checks.

**Acceptance:** Tests prove cache hits reduce work and changed/invalid input remains detected.

**Dependencies:** AUD-C12.

**Evidence:** `src/haute/_json_shred.py`; `src/haute/routes/json_cache.py`; `tests/test_json_shred.py`.

### IO-IO11 — I/O hygiene
**Why:** Small maintainability defects remain outside the semantic packages.

**Plan:** Apply individually verified low-risk cleanup with focused regression coverage.

**Acceptance:** Each selected improvement is covered without broadening format semantics.

**Dependencies:** None.

**Evidence:** `src/haute/_io.py`; `src/haute/_config_io.py`; `tests/test_io.py`.

### IO-IO12 — Optional format capabilities
**Why:** The registry is complete, but future formats require deliberate capability decisions.

**Plan:** Add optional formats one registered capability at a time, including validation, UI availability, and round-trip tests.

**Acceptance:** Every added format has explicit capability metadata and end-to-end supported/unsupported coverage.

**Dependencies:** IO-IO01.

**Evidence:** `src/haute/_io.py`; `frontend/src/panels/editors`; `tests/test_io.py`.
