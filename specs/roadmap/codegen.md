# Codegen roadmap

## Scope

Generating pipeline source from the graph. Current behaviour is specified in
[the codegen specification](../codegen/high-level.md). This package comes
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CODEGEN-R01 | Planned | P3 | Config-backed node decorators are emitted directly, not spliced after the fact. |

## Planned improvements

### CODEGEN-R01 — Emit config-backed decorators directly
**Why:** For a node type with a config sidecar, the per-type builder first
emits the whole config inline in the decorator (the rating-step builder
writes `tables=` with the repr of every table), and `_node_to_code` then
replaces that decorator by slicing the generated string at
`code.index("\ndef ")`. The work of rendering the config is thrown away,
and the splice contradicts the codegen specification's rule that callers
never splice at a manually located delimiter.

**Plan:** Have each config-backed builder emit
`@pipeline.<type>(config="config/<type>/<name>.json")` itself, and delete the
splice. Keep contract injection on the LibCST boundary.

**Acceptance:** No codegen path slices generated source at a searched
delimiter; generated files are byte-identical to today's for the existing
round-trip fixtures.

**Dependencies:** None.

**Evidence:** `src/haute/codegen.py::_node_to_code`;
`src/haute/_codegen_builders.py::_gen_rating_step`;
`src/haute/_python_syntax.py`; `tests/test_codegen.py`.
