# Pipeline authoring roadmap

## Scope

Owns the decorator DSL, parser/submodel structure, graph round trips, generated
code, standalone `Pipeline.run()`/`score()` semantics, registry wiring, and
persisted authored configuration. Current behaviour is specified in
[pipeline config](../pipeline-config/high-level.md),
[code generation](../codegen/high-level.md), and
[submodels](../submodels/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-C05` | Reverify | P0 | Conserve authored graph structure or fail loudly at every parser loss boundary. |
| `AUD-PIPE-01` | Reverify | P0 | Replace guessed public `run()`/`score()` semantics with explicit output, source, arity, and instance contracts. |
| `AUD-C01` | Reverify | P0 | Make saved-file standalone execution equivalent to the canonical executor for every stateful node. |

## Planned improvements

### AUD-C05 — Parser structure conservation

**Why:** Several invalid or unsupported authored shapes can disappear during
AST/regex parsing, submodel merging, or preamble extraction while the remaining
graph still looks healthy.

**Plan:**

- Recover top-level submodel references in regex fallback, or fail with a
  diagnostic that names every unrecovered reference.
- Reject duplicate decorated function names and unsupported `async def` nodes.
- Preserve implicit parameter-name edges into submodel children across both
  flattened and hierarchical graphs.
- Reject two submodels that resolve to the same pipeline name and surface the
  files involved.
- Make the one-level nested-submodel limit explicit and visible rather than
  silently ignoring deeper calls.
- Make preamble boundaries AST/alias aware.
- Add one structure-conservation assertion comparing authored node, edge,
  handle, and submodel identities before accepting a parse.

**Acceptance:**

- Each known loss shape has a failing regression before implementation and
  then either round-trips byte/structure faithfully or raises a typed,
  actionable error.
- Regex fallback never returns a healthy-looking graph missing all submodels.
- Parse → codegen → parse properties preserve node IDs, edge handles, submodel
  boundaries, user code, and preamble content.

**Dependencies:** Parser conservation precedes broad standalone differential
testing so the oracle does not compare already-truncated graphs.

**Evidence:** `src/haute/parser.py`, `src/haute/_parser_regex.py`,
`src/haute/_ast_helpers.py`, `src/haute/_graph_builders.py`,
`src/haute/_parser_submodels.py`, `tests/test_parser.py`,
`tests/test_parser_fail_loudly.py`, `tests/test_parser_roundtrip.py`, and
`tests/test_property.py`.

### AUD-PIPE-01 — Explicit public execution semantics

**Why:** Public `run()`/`score()` paths have historically guessed the output,
seeded multiple sources, and tolerated wiring that the canvas executor treats
differently.

**Plan:**

- Require an explicit or structurally unambiguous output; never return the last
  topological node as an implicit choice.
- Enforce node input arity and named-handle wiring before invoking a function.
- Define how `api_input` marks the live scoring input and reject ambiguous
  source seeding instead of feeding every root the same frame.
- Either implement `instanceOf`/`inputMapping` semantics in standalone
  execution or reject instance graphs on that surface.
- Share the non-source execution block between `run()` and `score()`.

**Acceptance:**

- Fan-out/multiple-output, multi-source, extra/missing input, API-input, and
  instance graphs have explicit success or typed-error tests.
- `run()`, `score()`, and the executor agree on output selection and wiring for
  all supported graph shapes.
- No unsupported shape degrades to a plausible partial result.

**Dependencies:** Uses [execution](execution-engine.md) as the runtime oracle
and [I/O](io-layer.md) for source identity.

**Evidence:** `src/haute/pipeline.py`, `src/haute/_types.py`,
`tests/test_pipeline.py`, `tests/test_e2e.py`, and
`tests/test_codegen_execution_equivalence.py`.

### AUD-C01 — Standalone/executor equivalence

**Why:** A saved Python pipeline must not no-op a stateful node or choose a
different branch from canvas/preview execution.

**Plan:**

- Encode passthrough versus stateful behaviour in the node registry.
- Give every stateful node one config-driven helper shared by its executor
  builder and generated body.
- Route Live Switch selection through a shared source/scenario selector.
- Make generated Optimiser Apply, Modelling, Scenario Expander, and other
  stateful bodies perform the same work as the executor.
- Generate safe literals for submodel names/files and reject unrepresentable
  configuration before emission.

**Acceptance:**

- Registry completeness fails if a stateful node lacks a shared apply helper
  or if codegen/executor registrations disagree.
- A generated-module differential suite runs representative graphs under
  live/batch sources and compares values, schemas, errors, and side effects
  with `execute_graph`.
- Structural round-trip and standalone-execution tests cover every retained
  node type.

**Dependencies:** Feature components own helper semantics; this component owns
their generated wiring and equivalence gate.

**Evidence:** `src/haute/_registry.py`, `src/haute/_builders.py`,
`src/haute/_codegen_builders.py`, `src/haute/codegen.py`,
`src/haute/graph_utils.py`, `tests/test_codegen.py`,
`tests/test_codegen_execution_equivalence.py`, and
`tests/test_codegen_roundtrip_property.py`.
