# Submodels roadmap

## Scope

Submodel definitions and occurrences, and the other mechanisms for reusing
logic in a pipeline. Current behaviour is specified in
[the submodels specification](../submodels/high-level.md). This package
comes from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-R01 | Decision | P3 | One mechanism reuses a node or group of nodes. |

## Planned improvements

### SUB-R01 — One reuse mechanism
**Why:** Four mechanisms overlap. Node-level instances (`@pipeline.instance`,
`instanceOf` with `inputMapping`) reuse one node's logic. Submodel
definitions with instance occurrences reuse a group of nodes, with read-only
copies and port minting that suffixes `_2` and `_3` on collision. Plain
Polars transforms carry `inputMapping` to keep logical input names across
rewires. Project `utility` modules reuse functions. Together they account
for 258 `instanceOf`/`instance_of` references across the backend and
frontend. Files named after submodels hold 6,300 source lines and 11,700 test
lines, and 25 of the 48 submodel commits since May 2026 were fixes or
hardening of identity and aliasing.

**Plan:** Decide which mechanisms the product keeps. A node instance is a
one-node submodel occurrence, so one option is to keep submodel definitions
and utility modules and remove node-level instances. Specify the kept
mechanisms and the removed ones' canonical replacement, then remove the
removed ones' parser, codegen, executor and editor paths.

**Acceptance:** The submodels specification names the supported reuse
mechanisms; the removed mechanism has no parser, codegen, executor or editor
path; the documentation describes only supported mechanisms.

**Dependencies:** None. The canonical-input rule that applies to removed forms
is in the [specification README](../README.md#canonical-only-format-policy).

**Evidence:** `src/haute/pipeline.py::instance`;
`src/haute/_submodel_instances.py`; `src/haute/_flatten.py`;
`docs/building-models/nodes/instances.md`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts`.
