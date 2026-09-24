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

