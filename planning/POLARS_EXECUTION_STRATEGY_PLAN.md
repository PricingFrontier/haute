# Polars Execution Strategy Plan

Status: implementation plan
Owner: backend execution workstream
Last updated: 2026-05-11

## Goal

Make Haute's execution engine handle realistic Polars pipelines without forcing
users into narrow contracts, while keeping the large-data guarantees that matter:

- fast column projection where the engine can prove it
- streaming and chunked execution where the operator shape supports it
- no hidden eager fallback for large data
- fail-loud diagnostics when a graph shape is genuinely unsafe
- consistent behaviour across preview, training, optimiser setup, auto-range,
  sinks, and deployment

The immediate regression this plan must catch and fix is:

```text
Pipeline cannot run in bounded streaming mode:
User-code projection requires a concrete node contract.
(node_id=competitor_join, node_type=polars,
incoming_parent_ids=['competitor_insights', 'policies'])
```

That failure appears when training `avg_top_5`, where a normal Polars join is
treated as opaque even though it should be executable safely with either
runtime-inferred projection or a bounded unprojected streaming boundary.

## Core Principle

The execution engine should not ask "can I statically prove every column from
Python source text?" and fail when the answer is no.

It should ask "which execution strategy is safe for this node and target?" and
choose the narrowest safe strategy:

1. projected streaming
2. runtime-inferred streaming
3. schema-derived all-except projection
4. bounded unprojected streaming boundary
5. chunked map-reduce
6. small eager with explicit admission
7. unsupported, with a typed reason

Failing loudly remains correct when no safe strategy exists. The important
change is that "static projection unknown" is not itself a failure.

## Why Failing Loud Still Matters

Running when the engine cannot prove safety can be worse than failing:

- it can silently materialise a 10m-row intermediate in RAM
- it can change semantics for group-by, joins, windows, sorts, or UDFs
- it can drop columns needed later if projection was guessed incorrectly
- it can hide a correctness bug behind a broad fallback

The new standard is:

- do not fail just because static projection is hard
- do fail when the chosen strategy cannot preserve semantics or bounded memory
- include the blocking node, operator class, strategy attempted, and user action

## Target Architecture

### Execution Strategy Planner

Introduce a shared planner that takes:

- graph topology
- target node and execution profile
- target demand
- known source schemas
- declared node contracts where present
- model metadata such as CatBoost feature names
- operator capabilities inferred from Polars plans or runtime samples

It returns an explicit execution strategy for every node on the target path.

The planner must be used by:

- pipeline preview
- model training preparation
- optimiser setup
- optimiser auto-range
- optimiser apply and explainability
- sinks
- deploy live scoring
- deploy batch scoring

Routes should not implement private projection rules themselves.

### Demand Model

Replace the current mostly "set of required columns" model with explicit demand
types.

`ExactColumns`

The downstream target needs exactly these columns. This is the ideal case for
model scoring, optimiser setup, previews, and sinks.

`AllExcept`

The downstream target needs every input column except a known exclusion set.
This is required for model training when users do not explicitly list feature
columns but do configure target, exclude, weight, offset, split, or group
columns.

`RuntimeInferred`

The engine cannot infer the final projection from static source text, but can
ask Polars for a lazy plan, output schema, or join/aggregate dependencies before
running the full workload.

`UnprojectedBoundary`

The node can run in streaming mode without projection through that boundary.
This is allowed only when the planner can keep it bounded and can explain why
the unprojected width is acceptable.

`ChunkedMapReduce`

The node changes row shape or requires aggregation, but can be executed as
chunk-local partials plus a bounded reduce.

`Unsupported`

The node requires semantics the engine cannot run safely in the selected
profile. This is a typed planning result, not an unhandled exception.

### Strategy Types

`projected_streaming`

Use precise column projection before the node runs. This remains the preferred
path.

`runtime_inferred_streaming`

Build a lazy Polars plan with the real parent schemas, inspect the resulting
schema and dependencies, then run bounded streaming.

This should support common user code such as:

- `select`
- `with_columns`
- `filter`
- simple expressions
- joins on explicit keys
- rename
- cast
- sort where downstream semantics permit it

`schema_all_except`

Use source schema to derive feature columns by subtracting configured metadata
columns. This is essential for CatBoost training and other learners where "all
columns except target/excluded columns" is a first-class user workflow.

`unprojected_streaming_boundary`

Run a node without narrowing columns through that node when projection is not
provable, but only if:

- Polars can execute it in streaming or bounded mode
- all parents can be streamed or checkpointed safely
- row counts and width are budgeted
- the decision is visible in metrics

This is not a silent fallback. It is an explicit strategy.

`chunked_map_reduce`

Support safe shapes such as aggregations by decomposing the work into partial
chunk outputs and a bounded final reduce. This should be added only for operator
families where semantics are well understood.

`small_eager_admitted`

Allow eager execution only when a concrete admission estimate proves it is small
enough for the current profile. This is useful for previews, metadata, small
reference tables, and local debugging.

`unsupported`

Return a typed contract error with:

- node id
- node type
- execution profile
- operator shape
- target demand
- reason bounded execution could not be planned
- suggested ways to make the node plannable

## CatBoost Feature Projection

CatBoost gives the engine two important sources of truth.

### Existing Model Scoring

For scoring an existing CatBoost model, read and cache the model feature names
from the artifact metadata or model object. The projection seed should be:

```text
model feature columns
+ downstream passthrough columns required by the graph
+ configured id columns required by the target
```

This avoids asking upstream user code for columns that the model will never
read.

Acceptance criteria:

- CatBoost scoring does not request all columns from upstream sources.
- Missing model features fail before scoring starts, with a typed error.
- Feature-name reads are cached with the loaded model artifact.
- Feature order used for scoring is deterministic and tested.

### Model Training

Training is different. If users explicitly configure feature columns, use
`ExactColumns`.

If users rely on "train on all columns except target/exclude", use `AllExcept`.
The training preparation stage should derive the concrete feature set from the
input schema before collecting data.

The projection seed should include:

- target column
- explicit feature columns, if supplied
- all schema columns except excluded metadata columns, if explicit features are
  not supplied
- weight, offset, group, split, fold, and id columns where configured
- categorical columns needed by the trainer

Acceptance criteria:

- `avg_top_5` training with `competitor_join` no longer fails because the join is
  contract-free.
- Training never broadens to "all columns" silently when feature metadata is
  available.
- Training diagnostics show the final feature count and excluded columns.

## Polars User Code Support

The engine should support all common Polars code by choosing an appropriate
strategy. It should not promise static projection for arbitrary Python.

### Static Or Runtime Inference

Support dependency extraction for:

- `select`
- `with_columns`
- `filter`
- `join`
- `rename`
- `drop`
- `cast`
- simple `when/then/otherwise`
- `group_by().agg(...)` where output schema is inferable
- window expressions where dependencies are explicit

This can be implemented by a mix of declared contracts, Polars lazy schemas,
controlled AST inspection, and runtime plan probing. The implementation should
prefer Polars-native plan/schema APIs over fragile source-string parsing.

### Joins

Simple joins must be first-class:

- infer join keys from `on`, `left_on`, and `right_on`
- request join keys from both parents
- request downstream columns only from the parent that can produce them
- preserve suffix semantics
- validate duplicate output names
- fail loudly on ambiguous column ownership

The `competitor_join` case should be a regression test:

```python
df = policies.join(competitor_insights, on="quote_id", how="inner")
```

### Group-By And Aggregation

Group-by is not impossible, but it is not the same as row-preserving projection.

The planner should distinguish:

- simple aggregation that can use chunked partials plus final reduce
- aggregation that Polars can stream safely
- aggregation that requires global state and must be admitted as small eager or
  rejected

Users should not be blocked from group-by by default. The engine should either
choose a safe strategy or explain the exact unsupported aggregate shape.

### UDFs And Dynamic Python

Python UDFs, arbitrary lambdas, dynamic column generation, and side-effectful
code may be impossible to prove statically.

The planner should attempt, in order:

1. declared user contract
2. runtime-inferred schema with representative empty or schema-only inputs
3. bounded unprojected streaming boundary
4. small eager admission
5. unsupported

It must not guess dependencies from arbitrary Python in a way that can be wrong.

## Implementation Workflow

For each slice:

1. Spawn one developer agent and one reviewer agent.
2. The developer agent writes failing tests first, then implements the slice.
3. The reviewer agent critiques the implementation for correctness,
   generality, performance, and unnecessary complexity.
4. The main thread integrates, runs the relevant suite, and resolves review
   findings before moving on.

No slice is complete until tests prove:

- the intended feature works
- edge cases fail loudly
- no previous optimiser, preview, training, sink, or deploy path regresses

## Implementation Slices

### Slice 0: Regression Baseline

Goal: capture the current failures before changing planner behaviour.

Tests:

- training `avg_top_5` through a contract-free `competitor_join` reproduces the
  bounded streaming projection failure
- optimiser estimate on `ratebook_optimiser` and `online_optimiser` does not
  regress to setup-time 422 errors
- preview of optimiser nodes still succeeds before estimate starts
- no implicit eager fallback is used in the failing training path

Implementation:

- add focused fixtures for `policies`, `competitor_insights`,
  `competitor_join`, and `avg_top_5`
- assert typed error payloads rather than matching generic strings
- add counters for selected strategy when available

### Slice 1: CatBoost Training Demand

Goal: make training derive a precise demand without requiring all upstream nodes
to expose a concrete exact-column contract.

Tests:

- explicit feature list yields `ExactColumns`
- no explicit feature list yields `AllExcept`
- target, exclude, weight, group, split, fold, and id columns are retained or
  excluded correctly
- missing target fails before collecting data
- missing explicit feature fails before collecting data
- categorical features are included and ordered deterministically

Implementation:

- introduce `AllExcept` demand type
- make training preparation resolve schema before materialisation
- record derived feature columns in execution metrics
- keep CatBoost scoring feature-name projection separate from training demand

### Slice 2: Contract-Free Join Planning

Goal: support normal Polars joins without requiring users to write contracts for
every join node.

Tests:

- inner, left, semi, anti, and outer joins on `on="quote_id"`
- `left_on` and `right_on`
- suffix collisions
- duplicate non-key column names
- downstream demand from left-only, right-only, and both-parent columns
- missing join key fails loudly with parent id and column name
- ambiguous column ownership fails loudly
- `competitor_join -> avg_top_5` training succeeds in bounded mode

Implementation:

- add a fan-in join inference path to the planner
- derive per-parent demands from downstream demand plus join keys
- validate output schema and suffix behaviour
- expose strategy metrics as `runtime_inferred_streaming` or
  `projected_streaming` where exact

### Slice 3: Shared Execution Strategy Planner

Goal: remove route-specific projection decisions and centralise strategy
selection.

Tests:

- preview, training, optimiser setup, auto-range, sink, and deploy all call the
  shared planner
- route modules do not import private projection helpers
- unsupported plans return typed planner errors
- permissive profile differences are explicit, not accidental

Implementation:

- create planner facade in the execution package
- return a per-node strategy plan with reasons
- thread execution profile into all planner calls
- keep existing contracts as inputs, not as the whole planner

### Slice 4: Bounded Unprojected Streaming Boundary

Goal: let safe Polars code run even when exact projection is unavailable.

Tests:

- contract-free row-preserving Polars node can run unprojected in streaming mode
- memory metrics show no large eager collect
- route response identifies the unprojected boundary
- wide inputs are admitted or rejected using profile-aware budgets
- small eager is not used unless the admitted size is below threshold

Implementation:

- add explicit `unprojected_streaming_boundary` strategy
- verify Polars plan can stream before running
- checkpoint boundary output if downstream needs repeated reads
- record input width, estimated bytes, chunk count, and peak RSS

### Slice 5: Expression Dependency Extraction

Goal: improve exact projection for common single-parent Polars code.

Tests:

- `with_columns` expression dependencies
- `filter` predicate dependencies
- `select` and alias dependencies
- `drop` and `rename`
- `when/then/otherwise`
- casts
- window expression dependencies
- unsupported dynamic expressions fail to boundary strategy, not directly to
  unsupported

Implementation:

- use Polars lazy schema and expression metadata where possible
- add controlled AST extraction only where Polars metadata is insufficient
- never infer dependencies from arbitrary runtime values
- add unit tests for the extractor independent of routes

### Slice 6: Group-By And Chunked Map-Reduce

Goal: support aggregation shapes without pretending they are row-preserving.

Tests:

- simple `group_by().agg(sum, mean, count, min, max)` uses chunked partials or
  Polars streaming
- non-associative or order-sensitive aggregates are rejected unless small eager
  is admitted
- downstream demand maps to aggregate output names
- group keys are projected correctly
- null and dtype semantics match eager Polars on test data

Implementation:

- add `chunked_map_reduce` strategy
- define supported aggregate catalogue
- implement partial aggregation and final reduce only for proven-safe operators
- record aggregate strategy in metrics

### Slice 7: Diagnostics And No Silent Broadening

Goal: make every broadening or failure explainable.

Tests:

- source rename ambiguity does not silently select all columns
- selected-columns paths do not bypass strict planner checks
- unsupported errors name blocking operator and profile
- diagnostics include strategy alternatives tried
- UI contract accepts all new typed statuses

Implementation:

- extend planner error model
- include demand, strategy, and blocking node in API payloads
- add frontend parsing coverage for new statuses
- make broadening decisions visible in execution traces

### Slice 8: Apply Across Engine Surfaces

Goal: ensure the new philosophy applies beyond the current training bug.

Tests:

- preview uses planner strategies
- optimiser estimate uses planner strategies
- auto-range uses planner strategies
- optimiser solve setup uses planner strategies
- training prep uses planner strategies
- sink execution uses planner strategies
- deploy live and batch scoring use planner strategies

Implementation:

- wire planner outputs into every execution entry point
- remove duplicate projection code from routes
- keep deployment conservative but not artificially weaker
- add conformance tests for every execution profile

### Slice 9: Performance And Scale Gates

Goal: prove the changes help large data, not just small fixtures.

Tests:

- 1m-row and 10m-row synthetic join-training pipeline
- peak RSS bounded by strategy expectations
- no accidental full-width collect in training, preview, optimiser setup, or
  auto-range
- feature projection reduces source scan width for CatBoost scoring
- unprojected streaming boundary remains linear in row count

Implementation:

- add opt-in large-data tests
- add deterministic timing and memory counters around each stage
- keep CI-friendly smaller tests always-on
- document how to run local scale gates

### Slice 10: Cleanup And Public Documentation

Goal: leave the codebase simpler than before.

Tests:

- hygiene tests prevent new route-side private planner imports
- old narrow projection exceptions are removed or mapped to planner errors
- docs examples match tested behaviour

Implementation:

- delete obsolete special cases
- update user docs for contracts, joins, group-by, CatBoost features, and
  failure diagnostics
- document when contracts are still useful
- document how to interpret execution strategy metrics

## Test Matrix

The test suite should include these categories before the plan is considered
complete:

- single-parent Polars expression dependencies
- fan-in joins with multiple parent schemas
- joins with suffix collisions and ambiguous ownership
- CatBoost scoring from model feature names
- CatBoost training with explicit features
- CatBoost training with all-except schema demand
- group-by aggregation with safe and unsafe aggregate shapes
- UDF and dynamic code boundaries
- preview strategy conformance
- training strategy conformance
- optimiser setup and auto-range strategy conformance
- sink and deploy strategy conformance
- large-data memory gates
- typed planner diagnostics
- UI status parsing for all terminal and non-terminal states

## Acceptance Criteria

The plan is complete when:

- `avg_top_5` training through `competitor_join` succeeds without adding a manual
  contract to the join.
- Optimiser previews and estimates do not regress.
- CatBoost scoring uses model feature names for upstream projection.
- CatBoost training uses explicit features or schema-derived `AllExcept`
  projection.
- Common Polars joins are supported through the shared planner.
- Unknown static projection no longer directly implies failure.
- Unprojected execution is allowed only as an explicit bounded strategy.
- Unsupported shapes fail with typed, actionable diagnostics.
- All execution profiles use the same planner facade.
- Large-data tests show bounded memory behaviour for supported shapes.

## Non-Goals

This plan does not require:

- static analysis of arbitrary Python
- pretending every Polars operation is streamable
- broad eager fallback for large data
- user-written contracts for every normal join
- immediate support for every possible aggregation or UDF shape

Contracts remain useful for highly dynamic code, but they should not be a tax on
ordinary Polars joins, feature engineering, or model training workflows.

