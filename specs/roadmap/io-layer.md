# IO layer roadmap

## Scope

Dtype vocabulary, file writes and file locks used by the IO layer and the
stores built on it. Current behaviour is specified in
[the IO-layer specification](../io-layer/high-level.md). These packages come
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IO-R01 | Planned | P2 | One dtype vocabulary; an unknown dtype name is rejected. |
| IO-R02 | Planned | P3 | One atomic-write primitive and one file-lock primitive. |

## Planned improvements

### IO-R01 — One dtype vocabulary
**Why:** Polars dtypes are mapped to and from strings in about seven places:
the canonical `_polars_dtypes` module; `_io._normalise_dtype`, with its own
alias table; the rating descriptor format; the output-document schema; deploy
scoring's canonical dtype; the MLflow signature mapping; and training's
display names. `_normalise_dtype` also falls back to `getattr(pl, name)`, so
any attribute of the Polars module is accepted as a declared source dtype.

**Plan:** Make `_polars_dtypes` the only string-to-dtype and dtype-to-string
mapping, with named views where a consumer needs a coarser vocabulary
(JSON schema, MLflow). Reject any name it does not define.

**Acceptance:** One module parses and renders dtypes; declaring a source
column as a non-dtype Polars attribute fails with a message naming it; rating
descriptors, deploy contracts and MLflow signatures round-trip through the
shared module under test.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_dtypes.py::parse_dtype`;
`src/haute/_polars_dtypes.py::dtype_to_spec`;
`src/haute/_io.py::_normalise_dtype`;
`src/haute/_rating.py::rating_dtype_descriptor`;
`src/haute/_rating.py::rating_dtype_from_descriptor`;
`src/haute/_output_assembler.py::_document_dtype`;
`src/haute/deploy/_scorer.py::_canonical_dtype`;
`src/haute/modelling/_signature.py::_map_dtype`;
`src/haute/modelling/_training_job.py::_polars_dtype_name`.

### IO-R02 — One atomic write and one file lock
**Why:** `_polars_utils.atomic_write` stages to a fixed
`dest.with_suffix(".parquet.tmp")`, so two writers to one destination
collide, a CSV gets a `.parquet.tmp` stage, and nothing is flushed with
`fsync`. `_file_ops.atomic_write_bytes` already uses unique staging names,
`fsync` and a Windows contention retry. File locks are wrapped three times,
each with its own timeout and stale-lock loop: the project mutation lock, the
source-cache store lock and the JSON-shred file lock (since `CACHE-S08`, the
runtime storage budget's lock).

**Plan:** Build `atomic_write` on the `_file_ops` primitive and delete the
fixed-suffix staging. Provide one file-lock helper with timeout and stale-owner
handling and use it for the three locks.

**Acceptance:** Two concurrent writes to one destination leave one complete
file and no stray staging file under test; one lock helper remains; the
store, JSON-shred lock and project-lock tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_utils.py::atomic_write`;
`src/haute/_file_ops.py::atomic_write_bytes`;
`src/haute/_project_mutation_lock.py::ProjectMutationLock`;
`src/haute/_source_cache.py::_StoreFileLock`;
`src/haute/_json_shred/_publication.py::_CacheBuildLock`;
`src/haute/_file_lock.py`.
