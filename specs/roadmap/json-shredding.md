# JSON shredding roadmap

## Scope

OUTPUT document assembly. Current behaviour is specified in
[the JSON-shredding specification](../json-shredding/high-level.md). API-input
table caching is planned in the [caching roadmap](caching.md) (`CACHE-S08`).
This package comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| JSON-R02 | Decision | P3 | OUTPUT nesting is declared explicitly, and its algorithm is specified in this repository. |

## Planned improvements

### JSON-R02 — Explicit nesting for OUTPUT assembly
**Why:** The assembler treats "two tables carrying the same field" as a join
constraint, so it needs GYO α-acyclicity reduction, cyclic-core detection,
recursive cut planning and bag natural joins to build one document. Its
normative algorithm is not in this repository: the module docstring cites
`OUTPUT_ASSEMBLY_PROPERTIES.md` in a separate notes repository (axioms A2,
A4 and A5).

**Plan:** First bring the algorithm's specification into the JSON-shredding
specification, so the code has an in-repository contract. Then decide
whether mappings should declare each array table's parent table and key.
Explicit nesting makes cycles unrepresentable and reduces assembly to a tree
walk.

**Acceptance:** The JSON-shredding specification states the assembly
algorithm without external references; if explicit nesting is adopted, the
mapping schema, editor and assembler use it and the cyclic-core machinery is
removed; existing output-document tests pass or are rewritten against the new
contract.

**Dependencies:** None.

**Evidence:** `src/haute/_output_assembler.py::_gyo_residue`;
`src/haute/_output_assembler.py::_plan_cut`;
`src/haute/_output_assembler.py::validate_v2_output_mapping`;
`frontend/src/panels/editors/OutputEditor.tsx`.
