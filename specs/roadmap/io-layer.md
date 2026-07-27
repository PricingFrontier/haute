# I/O layer roadmap

## Scope

Owns input/output correctness, registry-backed formats, shared snapshots,
publication and overwrite semantics, cache lifecycle, authoring feedback, and
I/O performance boundaries.

The previously audited I/O packages are delivered. Their current behaviour and
evidence live in the I/O, caching, Databricks, codegen, server-api, and frontend
editor specifications and ordinary regression tests.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| `IO-JSON-01` | Queued | P2 | Enforce the declared v2 JSON-input value types at runtime. |

## Planned improvements

### IO-JSON-01 — Closed v2 JSON-input schema

**Why:** `validate_v2_schema` checks the surrounding structure but currently
consumes `emit` and `selected` by truthiness and leaves `status` unchecked,
despite the narrower public schema.

**Plan:** Validate all declared scalar fields at the schema boundary and report
the exact field path and expected type before shredding or UI consumption.

**Acceptance:** Boolean lookalikes, invalid status values, and wrong scalar
types fail deterministically; canonical schema fixtures and round trips remain
unchanged.

**Dependencies:** Frontend node editors consume the same persisted schema.

**Evidence:** `src/haute/_api_input_schema.py`,
`tests/test_v2_codec_and_shred.py`, and
`frontend/src/__tests__/editors/ApiInputEditor.test.tsx`.
