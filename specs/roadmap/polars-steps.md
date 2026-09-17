# Polars step builder roadmap

## Scope

Polars transform nodes can be authored as an ordered list of low-code steps
rendered to the same generated function body that code-only nodes use. Current
behaviour is specified in [pipeline config](../pipeline-config/high-level.md),
[codegen](../codegen/high-level.md),
[execution engine](../execution-engine/high-level.md),
[server API](../server-api/high-level.md), and
[frontend node editors](../frontend-node-editors/high-level.md).

The remaining Polars code surfaces are the node panel's Polars tab on Data
Input, External File, Scenario Expander, Rating Step and Model Score (all
rendered by `frontend/src/panels/editors/shared/PolarsCodePanel.tsx` from
`NodeEditorBody` in `NodePanel.tsx`) and the Explore node's "Polars Code" pane
(`ExploreCodeEditor.tsx`). This roadmap turns the Transform step builder into
one reusable stepped-code pane and rolls it out to those surfaces.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PST-S03 | Planned | P2 | External File: steps over `obj` and the connected inputs, including input renames. |
| PST-S04 | Planned | P3 | Rating Step and Model Score post-processing as steps. |
| PST-S05 | Planned | P3 | Scenario Expander: rename its grid-size `steps` key, then steps over the expanded grid. |
| PST-S06 | Decision | P3 | Explore's Polars Code pane as steps, once its persistence home is chosen. |

Deliver in package order: the shared seams (the start mode, the surface table,
the stepped-code pane and the fail-loud guards) are in place on Data Input, and
each package is a per-type rollout over them.

## Shared design

### The difference between the surfaces

A Transform's code starts from nothing: the inputs are the function
parameters, and the first step (`source`) is the authored line
`df = <input>`. Every other surface hands the code a frame that is already
bound to `df` before the first authored line:

| Surface | What `df` is when the code starts | Other names in scope | Input names the steps may reference |
|---|---|---|---|
| Transform (`polars`) | nothing; the `source` step binds it | the edge names (or logical names through `inputMapping` on an instance) | edge / logical names |
| Data Input | the opened input snapshot | — | none |
| External File | the first connected input (`alias_first_input_as_df`) | `obj`, the loaded file; every edge name | all edge names |
| Scenario Expander | the expanded scenario grid | — | none (`_exec_user_code(code, ["df"], ...)`) |
| Rating Step | the rated frame | — | none |
| Model Score | the scored frame | — | none |
| Explore | the single input, bound by codegen as `df = <param>` | — | none |

So the reusable feature is the existing step vocabulary with a second
**start mode**: `input` (today's Transform behaviour) or `frame` (the frame is
already `df`).

**Input eligibility is a separate property from start mode.** The last column
above is what a surface's steps may reference in `join.input` and
`concat.inputs`, and it must be the same list on every path that renders the
steps (the editor's render request, load-time reconcile, codegen, the
executor and the deploy interceptors). Transform and External File pass their
edge names; every other surface passes an empty list, because its code runs
with only `df` in scope. The editor derives the list from this eligibility,
never from the input chips it displays.

### Renderer: a required `start` argument

`render_polars_steps(steps, input_names, *, start)` in
`src/haute/_polars_steps.py`, with `start` one of `"input"` or `"frame"`
(required, no default; every caller says which surface it renders for).

- `start="input"`: unchanged. The first step must be `source`; an empty list
  is an error ("Choose the input to start from.").
- `start="frame"`: an empty list renders to empty code without error (the node
  keeps its base behaviour, exactly like an empty code box). A `source` step
  is refused at any position with a step-indexed message ("This node starts
  from `df`; remove the start step."). `join` and `concat` references are
  validated against `input_names` as today, so a surface that passes `[]`
  refuses them with the existing "known: none" message. Every other step kind,
  free code included, renders identically in both modes, and `step_lines` is
  unchanged.

A module-level table `STEPPED_NODE_TYPES: Mapping[NodeType, SteppedSurface]`
maps each stepped node type to its start mode and its input eligibility
(`polars`: `input`, edges; External File: `frame`, edges; Data Input, Rating
Step, Model Score, Scenario Expander and later Explore: `frame`, none). A
helper `step_input_names(node_type, edge_names)` returns the edge names or
`[]` from that table and is the only way callers obtain the list. Every
backend gate that today reads `nodeType == NodeType.POLARS` for step handling
switches to this table; the frontend mirrors it in
`frontend/src/utils/polarsStepInputs.ts` (`STEPPED_NODE_TYPES`,
`steppedSurfaceFor(nodeType)`, `stepInputNames(nodeType, edgeNames)`,
`isSteppedConfig`), and `tests/test_polars_steps_catalogue.py` holds the two
tables equal.

### One key: `steps`

A node is stepped when its config carries a list under `steps`, on every node
type. This keeps one predicate for the sidecar, fingerprint, undo, rename and
materialisation code paths. Scenario Expander already uses `steps` as an
integer (the grid size, in `SCENARIO_EXPANDER_CONFIG_KEYS`, the decorator
kwargs, `expand_scenarios_from_config`, chunk planning and
`ScenarioExpanderEditor.tsx`). That integer is renamed `stepCount` in PST-S05
before Scenario Expander joins `STEPPED_NODE_TYPES`; there is no compatibility
path for the old key (the library has no external users). Until PST-S05 lands,
Scenario Expander is not a stepped type and the generic `isinstance(list)`
checks leave its integer alone.

### Persistence and round trip

- **Sidecars.** The five pane types already have required sidecars
  (`has_config_folder`), so `steps` simply becomes a declared field: add
  `steps: list[dict[str, Any]]` to `DataInputFileConfig` and the other
  `DATA_INPUT_CONFIG_TYPES`, `ExternalFileConfig`, `RatingStepConfig`,
  `ModelScoreConfig`, `ScenarioExpanderConfig` in `src/haute/_types.py` (this
  feeds `VALID_KEYS`, so `_prepare_config_for_sidecar` persists it), and to
  every per-`inputType` allowed set in `_validated_data_input`
  (`src/haute/_polars_io_registry.py`), which otherwise rejects it as an
  inactive field. `node_emits_sidecar` needs no change: required-sidecar types
  always emit, and the `polars` rule stays as it is.
- **Cache field classification.** `CACHE_CONFIG_FIELD_CLASSIFICATIONS` in
  `src/haute/_cache.py` is validated at import time against `VALID_KEYS`, so
  every new TypedDict field must be classified in the same package: `steps`
  as `user_code` on each rolled-out surface (as `polars` already classifies
  it), and Scenario Expander's `stepCount` as `node_config` in PST-S05. The
  caching spec and the existing classification tests are updated with each.
- **Data Input editor allowlist.** `INPUT_COMMON_KEYS` in
  `frontend/src/panels/editors/DataInputEditor.tsx` decides which fields
  survive a provider or format change (and feeds `DATABRICKS_KEYS`); `steps`
  joins it beside `code`, so changing provider or format on a stepped node
  keeps its steps.
- **Node data invariant.** `NodeData._materialise_polars_steps` becomes
  `_materialise_steps`: for every type in `STEPPED_NODE_TYPES` whose config
  carries `steps`, `code` is the rendering in that type's start mode, or empty
  code plus `_steps_error`. Because every builder, codegen path, chunk planner,
  projection and trace consumer already reads `code`, execution needs no new
  step awareness beyond the build-time guard below.
- **Generated body.** Codegen inserts the rendered frame-mode code exactly
  where the hand-written code goes today (`_wrap_user_code` for Rating Step,
  Scenario Expander, Model Score and Explore; `_wrap_external_code` for Data
  Input and External File), after the generated `df = ...` scaffold. When the
  steps cannot be rendered, codegen emits the raising placeholder
  (`INCOMPLETE_TRANSFORM_BODY`, its message generalised to "incomplete
  steps") in place of the user code, exactly as `_gen_transform` does, so a
  generated module never runs an incomplete stepped node as its base
  behaviour. Placeholder recognition moves from `_match_polars` into the
  shared matcher pipeline in `src/haute/_code_extraction.py`, so every
  extractor kind reads the placeholder as scaffold and reload yields empty
  code with the steps kept.
- **Extraction is not a fixpoint, and the design does not rely on one.** The
  per-type finalisers normalise some renderings (a sole free-code step
  `df = (df.head(2))` loses its parentheses through `_finalise_source`'s
  chain unwrapping). Reconcile therefore compares extracted forms on both
  sides: `extract(body)` against `extract(wrap(render(steps)))`, both through
  the type's own extractor. `tests/test_polars_steps_corpus.py` gains that
  round-trip property per surface and a first-and-only free-code step with
  redundant parentheses as a named case.
- **Reconcile on load.** `_reconcile_polars_steps` in
  `src/haute/_config_builder.py` becomes `_reconcile_steps` and runs for every
  type in `STEPPED_NODE_TYPES` after `_attach_code_from_body`, rendering in the
  type's mode against `step_input_names` and comparing extracted forms as
  above. Keep, discard with `_steps_discarded`, and "unrenderable behind an
  empty body" rules are unchanged. `_discarded_sidecar` is set only for
  `polars` (the only optional sidecar); for the pane types the sidecar stays
  and the next save simply writes it without `steps`.
- **Fail loud on every execution path.** A stepped node whose steps cannot
  render has empty materialised `code`, which every code-reading consumer
  treats as "no post-processing"; execution must not. `_builders.py` factors
  the transform's incomplete-step check (`_steps_error`, or a render failure
  against `step_input_names`) into `_stepped_code_problem(config, names,
  start)` and applies it in each pane builder, returning the fail-loud
  incomplete function as the transform does. The deploy interceptors in
  `src/haute/deploy/_scorer.py` read `config["code"]` without the builder:
  the bundled Data Input snapshot, the remapped External File path, and the
  Model Score branch (which also covers configured non-bundled Model Score
  nodes). Each applies the same helper before building its function, so an
  invalid step fails before any scan, load or scoring. Save keeps the
  non-blocking warning (`_validate_transforms_are_runnable` generalised to
  every stepped node). The messages differ by path on purpose: the builder,
  the interceptors and the save warning name the failing step
  (`Step k: <message>`), while the generated module raises the constant
  placeholder message, which stays constant so reload can recognise it as
  scaffold (the failing node is the function name in the traceback). Planning
  consumers that read only `code` (projection, chunking,
  `_model_score_columns`) may see an incomplete stepped node as code-less;
  that is acceptable because execution raises before any result is produced,
  and the acceptance tests cover the builder, the generated module and the
  interceptor paths with the same invalid step.
- **Render endpoint.** `PolarsStepsRenderRequest` gains a required
  `start: Literal["input", "frame"]`; the response is unchanged.
- **Renames.** Only surfaces whose steps can name inputs need rename
  rewriting: Transform today, External File after PST-S03. Backend
  `_submodel_instances.py` and frontend `nodeUpdatePlan.ts` /
  `edgeJoinGraph.ts` widen their `polars`-only gates to "stepped type whose
  start mode allows input references". `graphSnapshot.ts` and
  `shallowNodeHash.ts` widen their `nodeType === "polars"` fingerprint gate to
  `isSteppedConfig`, so re-materialised `code` is not an edit on any surface.

### Frontend: one stepped-code pane

- **`SteppedCodePane`** (`frontend/src/panels/editors/shared/SteppedCodePane.tsx`)
  is `TransformEditor.tsx`'s mode switch made reusable: steps list present
  renders `PolarsStepsEditor`; otherwise the code box (`PolarsCodePanel`) with
  the `_steps_discarded` notice above it. Props: `config`, `onUpdate`,
  `onReplaceConfig`, `inputSources`, `onDeleteInput`, `errorLine`, `runError`,
  `upstreamColumns`, `start` (`{ kind: "input" }` or
  `{ kind: "frame", frameHint: ReactNode }`), `codeHint`, `starterCode`.
  `TransformEditor` becomes a thin wrapper passing `start: { kind: "input" }`.
- **`PolarsStepsEditor`** takes `start`. In frame mode the "Start from"
  selector is replaced by a fixed, non-editable start card that states what
  `df` is (the `frameHint`: "the opened input snapshot", "the rated data", and
  so on, reusing today's `POLARS_TAB_HINTS` wording); the seeding effect that
  writes a `source` step is skipped; `canAdd` no longer depends on a chosen
  input; `AddStepMenu` withholds only the `join` and `concat` kinds while
  `inputNames` is empty (the Combine section keeps group by, pivot and
  unpivot). Card numbering, badges, drag, undo semantics, the generated-code
  panel and the confirmed one-way switch to code are unchanged.
  `useRenderedSteps(steps, inputNames, start, onRendered)` sends `start`.
- **Node panel wiring.** `NodeEditorBody` mounts `SteppedCodePane` on the
  Polars tab for `POLARS_TAB_TYPES`, passing `onReplaceConfig`
  (`handleConfigReplace` is already in scope), `runError`, and the per-type
  frame hint. The input chips (`inputSources`) keep today's display rule, but
  the step editor's `inputNames` come from `stepInputNames(nodeType,
  edgeNames)`: the edge names for External File, `[]` for the rest, so the
  editor never offers a join the executor would refuse.
- **Defaults.** `src/haute/node_defaults.json` gives each rolled-out type
  `"steps": []` in place of `"code": ""`, so a new node starts in step mode as
  a new Transform does. An empty frame-mode list renders to empty code, so
  the node's behaviour is identical to today's empty code box until a step is
  added. Scenario Expander's default also gains the explicit `stepCount: 21`
  that PST-S05 makes required.
- **Read-only and inspection views** (`ReadOnlyNodeConfig.tsx`, the
  read-only inspector JSON) need no change: they read `code`, which the
  invariant keeps current.

### Out of scope

- A code-to-steps switch (parsing Python into steps). The switch stays one
  way on every surface.
- Any change to the step vocabulary or the Transform builder's behaviour.
- Explore's own pane tabs; PST-S06 only swaps what the "Polars Code" pane
  renders.

### Specifications to update before code

`pipeline-config` high and low level (the invariant, the `start` modes, the
reconcile rule, `STEPPED_NODE_TYPES`, the Scenario Expander key rename),
`codegen` high and low level (per-type body emission), `server-api` low level
(`PolarsStepsRenderRequest.start`), `execution-engine` low level (the build
guard), and `frontend-node-editors` high and low level (`SteppedCodePane`,
frame-mode start card, the Polars tab, node defaults). Each package updates
only the rows it changes.

### Verification ladder

1. Targeted: `uv run pytest tests/test_polars_steps.py tests/test_polars_steps_corpus.py tests/test_polars_steps_catalogue.py tests/test_sidecar_golden.py -q`
   plus the cache classification tests;
   `npm --prefix frontend test -- src/panels/editors/polarsSteps src/__tests__/editors/TransformEditor.test.tsx src/__tests__/NodePanel src/__tests__/editors/DataInputEditor`.
2. Module: parser, codegen and save-service modules for the touched node type;
   `uv run mypy src/haute/`; `uv run ruff check` and `ruff format --check` on
   touched files; `npm --prefix frontend run typecheck` and `lint`.
3. Browser: `frontend/e2e/polars-steps.spec.ts` gains one journey per
   surface (add a Limit step on the node's Polars tab, preview, save, reopen,
   confirm the sidecar carries `steps` and the body carries the rendering).
4. Full suite before each package's commit (`-n auto --basetemp=<short>`),
   then `npm run build` for the served bundle.

## Planned improvements

### PST-S03 — External File

**Why:** External File is the only frame surface whose code can reach other
frames (`obj` plus every connected input), so it exercises join/concat in frame
mode and the rename rewriting.

**Plan:** Add `EXTERNAL_FILE` to `STEPPED_NODE_TYPES` (frame, edges);
`steps` on `ExternalFileConfig` and its cache classification;
`_build_external_file` and the scorer's remapped-path interceptor apply
`_stepped_code_problem` with all edge names; `_gen_external_file` renders
into `_wrap_external_code(code, input_name=...)` or emits the placeholder;
extend the rename rewriting gates (`_submodel_instances.py`,
`nodeUpdatePlan.ts`, `edgeJoinGraph.ts`) to stepped types whose start mode
allows input references; frame hint "`obj` = loaded file, `df` = the first
input". The step forms do not model `obj`; reaching it is a Free code step,
which the start card says.

**Acceptance:** join/concat steps naming a connected input render and run;
renaming that upstream node rewrites the reference inside the steps and never
adds `inputMapping`; a Free code step reading `obj` runs in preview and in the
generated module; the External File extractor round-trips the rendering.

**Dependencies:** the shared seams already in place on Data Input.

**Evidence:** `src/haute/_builders.py` (`_build_external_file`);
`src/haute/_codegen_builders.py` (`_gen_external_file`,
`_wrap_external_code`); `src/haute/_submodel_instances.py`;
`frontend/src/utils/nodeUpdatePlan.ts` (`rewriteSteppedTransformInputs`);
`frontend/src/utils/edgeJoinGraph.ts`.

### PST-S04 — Rating Step and Model Score

**Why:** Both surfaces are post-processing over a single bound frame with no
other inputs, so they are pure rollouts of the shared seams with per-type extractors.

**Plan:** Add both types to `STEPPED_NODE_TYPES` (frame); `steps` on
`RatingStepConfig` and `ModelScoreConfig` with their cache classifications
(`normalise_rating_step_config` must pass `steps` through untouched); guards
in `_build_rating_step`, `_build_model_score` and the scorer's Model Score
interceptor (both its remapped-artifact and non-bundled branches). Because
`_score_graph_lazy` runs `_attach_bundled_model_contract_inputs` before any
interceptor, and that pass loads remapped models that lack a feature
contract, the stepped-node validation also runs at the start of
`_score_graph_lazy` over the relevant nodes, so an invalid step fails before
that preparation pass loads anything;
`_gen_rating_step` and `_gen_model_score` render into `_wrap_user_code` or
emit the placeholder; `_model_score_columns` treats a non-empty step list
like non-empty code (opaque); frame hints "the rated data" and "the scored
data"; `node_defaults.json` `steps: []` for both.

**Acceptance:** the round-trip and reconcile tests per surface; a Limit step
on each node runs after rating or scoring in preview and in the generated
module; an invalid Model Score step on a remapped model without a feature
contract fails in the deploy scorer before `_attach_bundled_model_contract_inputs`
loads the model, and before scoring on the non-bundled branch; the Model
Score projection contract is opaque while steps exist.

**Dependencies:** the shared seams already in place on Data Input.

**Evidence:** `src/haute/_builders.py` (`_build_rating_step`,
`_build_model_score`, `_model_score_columns`);
`src/haute/_codegen_builders.py` (`_gen_rating_step`, `_gen_model_score`);
`src/haute/_config_io.py` (`normalise_rating_step_config`).

### PST-S05 — Scenario Expander

**Why:** Its grid-size integer already occupies the `steps` key, so the rollout
needs a preceding rename.

**Plan:** First commit: rename the integer to `stepCount` across
`SCENARIO_EXPANDER_CONFIG_KEYS`, `_build_scenario_expander`,
`expand_scenarios_from_config`, `_scenario_row_multiplier` in `chunking.py`,
the scenario trace enrichment in `_trace_enrichment.py` (which reports the
grid size under `parameters`), the Scenario Expander cache classification
(`stepCount` as `node_config`), the `@pipeline.scenario_expander` decorator
kwargs and their parsing, `ScenarioExpanderEditor.tsx`, the reference
pipeline, fixtures and specs. The old key is not listed in
`reject_removed_config_keys` (that check is by key presence and would reject
the list-valued `steps` this package introduces); instead the canonical
validation requires an integer `stepCount`, and the stepped-type check
already refuses a non-list `steps`, so a stale sidecar carrying the integer
fails the parse loudly. Because `stepCount` is required, the absent-key
default of 21 (`_DEFAULT_SCENARIO_STEPS` in `_node_apply.py`, today's
`scenarioExpander: {}` in `node_defaults.json`) goes: `node_defaults.json`
gives a new node `stepCount: 21` explicitly, the builder, chunk planner and
`expand_scenarios_from_config` read the key without a fallback, and the
existing "empty config uses defaults" test in `tests/test_scenario_expander.py`
becomes a rejection test. Second commit: add the type to
`STEPPED_NODE_TYPES` (frame, no inputs) with the same rollout shape as
PST-S04, the frame hint "the expanded scenario grid", and `steps: []` added
to the node default.

**Acceptance:** the rename leaves every scenario test green under the new
key; a config without `stepCount` is rejected by canonical validation and by
the builder; a new default node saves, reloads and executes with a 21-row
grid; a sidecar carrying an integer `steps` fails the parse with the
"steps must be a list" message; a sidecar carrying `stepCount` plus a
list-valued `steps` loads in step mode; the trace enrichment reports the
numeric `stepCount` while structured steps are present; the rollout meets the
PST-S04 acceptance for this type.

**Dependencies:** the shared seams already in place on Data Input.

**Evidence:** `src/haute/_types.py` (`SCENARIO_EXPANDER_CONFIG_KEYS`);
`src/haute/_builders.py` (`_build_scenario_expander`);
`src/haute/chunking.py` (`_scenario_row_multiplier`);
`src/haute/_trace_enrichment.py` (scenario parameters);
`src/haute/_cache.py` (`CACHE_CONFIG_FIELD_CLASSIFICATIONS`);
`src/haute/_config_validation.py` (`reject_removed_config_keys`);
`frontend/src/panels/editors/ScenarioExpanderEditor.tsx`.

### PST-S06 — Explore

**Why:** Explore's "Polars Code" pane is the same code box, but Explore has
its own pane tabs, its own config fingerprint (`stringifyExploreConfig`), a
codegen path that binds `df = <param>` inline, and no config folder at all:
`NODE_TYPE_TO_FOLDER` has no Explore entry, its overview, pivots and charts
live in decorator arguments, and `_attach_code_from_body` has no Explore
branch (its code is extracted by the decorator-kwargs path with the
`explore` matcher kind).

**Decision:** where Explore's `steps` persist. The options are an optional
`config/explore/<name>.json` sidecar (the `polars` pattern, which needs a
folder entry, sidecar ownership in the save walks, and a decision on whether
the overview, pivot and chart settings move into it or stay in the
decorator) or a `steps=` decorator argument (no sidecar, but a large literal
in generated code). This roadmap does not choose; the package is taken only
after PST-S03 to PST-S05 have been used and that choice is made.

**Plan:** After the persistence decision, add `EXPLORE` to `STEPPED_NODE_TYPES` (frame,
no inputs); persist `steps` per the decision; `ExploreCodeEditor` renders
`SteppedCodePane`; `_gen_explore` renders steps after the `df = <param>` line
or emits the raising placeholder; `_attach_code_from_body` or the
decorator-kwargs path reconciles the steps; `stringifyExploreConfig` strips
the materialised `code` like `authoredPolarsConfig`.

**Acceptance:** as PST-S04, plus a complete round trip of overview, pivot and
chart settings through save and load of a stepped Explore, and the Explore
report still runs on the stepped frame.

**Dependencies:** the shared seams already in place on Data Input; the persistence decision above; the Explore config
fingerprint and report pipeline.

**Evidence:** `frontend/src/panels/editors/ExploreCodeEditor.tsx`;
`frontend/src/panels/NodeConfigEditor.tsx` (`EXPLORE_PANES`);
`src/haute/_codegen_builders.py` (`_gen_explore`);
`src/haute/_config_io.py` (`NODE_TYPE_TO_FOLDER`);
`src/haute/_config_builder.py` (`_attach_code_from_body`);
`frontend/src/utils/shallowNodeHash.ts` (`stringifyExploreConfig`).
