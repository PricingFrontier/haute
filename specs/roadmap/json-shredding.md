# JSON shredding roadmap

## Scope

OUTPUT document assembly. Current behaviour is specified in
[the JSON-shredding specification](../json-shredding/high-level.md). This
package comes from the follow-ups of the 24 September 2026 round that made
each OUTPUT array level emitted by one source frame.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| JSON-R03 | Planned | P3 | A frame that cannot be nested under its parents is rejected before assembly instead of dropped. |

## Planned improvements

### JSON-R03 — Reject a frame that lacks a scope key
**Why:** Assembly nests each level's rows under a parent object by the
*scope*: the relation keys of every ancestor level that the child's subtree
carries. A row that lacks a scope key matches no parent object, so a frame
that does not carry one of its ancestors' keys contributes nothing, silently.
For example, with `quotes` keyed by `quote_id`, a `drivers` frame carrying
`quote_id` and `driver_id`, and a `licences` frame carrying only `driver_id`,
the scope under each driver includes `quote_id` (the drivers frame carries
it), the licences rows lack it, and every licence disappears from the
document without an error.
`test_null_key_guard_skips_a_subtree_frame_that_does_not_carry_the_key` pins
that behaviour today. The engineering priorities rule out silent drops.

**Plan:** Specify the rule in the JSON-shredding specification first: a frame
emitting at a level must carry every scope key that level is nested by. The
structural validator already runs before any frame is collected, so it
computes each level's scope from the mapping and rejects a frame missing a
key with an `OutputMappingSchemaError` naming the frame, its level path and
the missing key (and saying that the key must be carried through, for example
by joining it in upstream). Replace the pinning test with the rejection.

**Acceptance:** The example above fails validation naming `licences`,
`$[:].drivers[:].licences[:]` and `$[:].quote_id`, before any frame is
collected; the same mapping with `quote_id` carried by `licences` assembles
the licences under their drivers; no assembly path drops a frame's rows for a
missing scope key.

**Dependencies:** None.

**Evidence:** `src/haute/_output_assembler.py::validate_v2_output_mapping`;
`src/haute/_output_assembler.py::_assemble_document`;
`specs/json-shredding/high-level.md`; `tests/test_output_assembler.py`.
