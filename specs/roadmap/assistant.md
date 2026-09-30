# Assistant roadmap

## Scope

The in-app assistant that authors pipeline graphs from a chat panel: the
backend agent loop, tools, capability catalogue, recipes, examples and
provider adapters in `src/haute/assistant/`, its HTTP routes, the chat panel,
and its evaluation harness. Current behaviour is specified in
[the assistant specification](../assistant/high-level.md) and its
[low-level specification](../assistant/low-level.md), and the chat panel in
[the assistant UI specification](../frontend-assistant-ui/high-level.md).

The assistant was designed in early August 2026, before the Polars step
builder, the declaration-and-hook pipeline format, the banding rework, the
modelling families and most optimiser and tracing work. A review on 30
September 2026 (ten read-only audits, six design angles with scripted tool
replays, and a Codex second opinion) found that its knowledge, examples and
several write paths no longer match the product: code written to a node the
analyst created in the editor is overwritten by that node's steps while the
chat reports success, every node it creates is locked out of the step
builder, it is taught that Load File has no inputs, questions end as failed
turns, and the chat can edit a pipeline other than the one on the canvas. The
packages below correct that and then make the assistant good at building
pipelines.

**Target models.** The configured project provider is Databricks Foundation
Model serving with open-weight models only (`databricks-qwen35-122b-a10b`;
the workspace also serves gpt-oss-120b, qwen3-next-80b and llama-4-maverick).
The design works first on these mid-tier models and uses stronger Anthropic
or OpenAI models when a project configures them. On 30 September 2026 the
workspace refused every model request with HTTP 400, so live measurements
wait for access; offline replays through the real tools do not.

**Decisions taken on 30 September 2026.**

- The assistant writes new Polars logic as steps with a free-code card:
  `[source, free_code]` on a Transform and `[free_code]` on every other
  stepped surface. Every surface then follows one rule, "transform `df`;
  other inputs by name on Transform and Load File". Existing structured steps
  and code-mode nodes are preserved, and the assistant never switches a node
  to code mode; that switch stays an analyst action in the editor.
- All five phases below are planned. Phase 5 packages start only when the
  evaluation shows a gain.
- A new local file input is checked at dry-run against a schema inferred from
  the file, recorded as inferred; remote sources without a readable schema
  fail loudly.
- Data checks run under a new explicit `[assistant.egress]` permission and
  report advisory findings; blocking findings wait for evaluation evidence.

**Codex second opinion, adopted.** Refusing code on stepped nodes ships with
the free-code path that replaces it (`ASSIST-01` and `ASSIST-03` land
together). The mode-switch rule compares the original and resulting node, so
`steps: null` plus `code` cannot bypass it. A saved change that fails
verification has its own outcome. A data check is an execution capability
and needs its own authorisation and contract. Offline replay proves the tools
and contracts, not that a prompt change helps a model, and a data check never
replaces independent execution goldens.

**Phases.** Phase 1 (`ASSIST-01` to `ASSIST-19`) corrects the assistant and
makes authoring on stepped nodes work. Phase 2 (`ASSIST-20` to `ASSIST-26`)
teaches node shapes with real values, gives actionable errors, adds per-turn
context and starts measurement. Phase 3 (`ASSIST-30` to `ASSIST-39`) replaces
the lexical controller, allows several applies per turn with a change card
each, adds Undo and Compare, consolidates the tools and modernises the
providers. Phase 4 (`ASSIST-40` to `ASSIST-44`) lets the assistant see whether
the data came out right. Phase 5 (`ASSIST-50` to `ASSIST-53`) adds structured
authoring upgrades gated by evaluation. Each phase is one branch and one pull
request, with specifications updated before behaviour changes.

**Out of scope.** Converting code-mode nodes back into structured steps,
editing inside submodels or wiring submodel occurrences, remote table
discovery, and an OpenAI Responses API migration: none has evidence tying it
to the build journeys the evaluation targets first.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ASSIST-01 | Planned | P1 | A write the assistant asks for lands in the saved config or its plan fails naming the fix. |
| ASSIST-02 | Planned | P1 | Free-code steps keep their steps on reload and are checked against their surface's contract. |
| ASSIST-03 | Planned | P1 | Every node the assistant writes on a stepped surface stays in the step builder. |
| ASSIST-04 | Planned | P2 | Every change to what the model sees shows up as a reviewable diff. |
| ASSIST-05 | Planned | P1 | The prompt and guide state the product's real contracts and the effective egress policy. |
| ASSIST-06 | Planned | P1 | Node descriptors agree with the product's own registries and use palette names. |
| ASSIST-07 | Planned | P1 | Every teaching bundle saves and solves in the editor, and dry-run catches name collisions. |
| ASSIST-08 | Planned | P1 | Recipes produce configs that match rows and never advertise invalid options. |
| ASSIST-09 | Planned | P1 | Questions end as answered turns. |
| ASSIST-10 | Planned | P1 | No row value or unpermitted source reaches the provider through errors or readiness. |
| ASSIST-11 | Planned | P1 | A new local file input can be added and transformed in one plan. |
| ASSIST-12 | Planned | P1 | The assistant edits exactly the pipeline the canvas shows. |
| ASSIST-13 | Planned | P1 | The transcript is in order and each turn ends with a typed outcome. |
| ASSIST-14 | Planned | P2 | Long turns keep their context and streamed tool calls are never dropped. |
| ASSIST-15 | Planned | P2 | Git history reads the right pipeline and names the right files. |
| ASSIST-16 | Planned | P2 | Column profiles work downstream of joins and aggregations. |
| ASSIST-17 | Planned | P2 | A multi-case self-test run measures the model, not leftover harness state. |
| ASSIST-18 | Planned | P2 | Renaming a node never leaves a consumer silently broken. |
| ASSIST-19 | Planned | P2 | Invalid modelling and Load File configs fail at dry-run with the product's messages. |
| ASSIST-20 | Planned | P1 | Every assistant change is checked in CI against reference trajectories through the real tools. |
| ASSIST-21 | Planned | P2 | The model edits stepped nodes step by step and can see each node's authoring state. |
| ASSIST-22 | Planned | P1 | The model reads a realistic, executed config for every node type. |
| ASSIST-23 | Planned | P1 | Tool errors say how to fix them, and the model keeps correcting while each error is new. |
| ASSIST-24 | Planned | P1 | Each turn starts with the graph, the selection and the policy, without orientation reads. |
| ASSIST-25 | Planned | P2 | Renaming a node rewrites the references that structured configs hold. |
| ASSIST-26 | Planned | P2 | Structured cards after a free-code card keep their column help. |
| ASSIST-30 | Planned | P1 | No regex over the user's words steers a turn. |
| ASSIST-31 | Planned | P1 | After every apply the analyst sees a change card built from what was saved. |
| ASSIST-32 | Planned | P1 | A multi-stage build can finish in one turn, with a change card per stage. |
| ASSIST-33 | Planned | P2 | The analyst can undo or compare the latest assistant change in one click. |
| ASSIST-34 | Planned | P2 | Long turns never collide with analyst edits, and setup is explained. |
| ASSIST-35 | Planned | P2 | About nine task-shaped tools replace fifteen, and recipes compose with primitive operations. |
| ASSIST-36 | Planned | P1 | Realistic builds, edits and recoveries are measured per area on real models. |
| ASSIST-37 | Planned | P2 | Context stays short on mid-tier models; strong models get caching and thinking. |
| ASSIST-38 | Planned | P3 | Each provider gets the tool-schema shape it handles best. |
| ASSIST-39 | Planned | P3 | A multi-stage build shows a checklist of its stages. |
| ASSIST-40 | Planned | P1 | Data checks have a specified execution contract and their own egress permission. |
| ASSIST-41 | Planned | P1 | A dry-run reports advisory findings when changed nodes produce implausible data. |
| ASSIST-42 | Planned | P2 | One call answers why a saved node fails or why a column is null. |
| ASSIST-43 | Planned | P2 | Evaluation scores data findings as an extra layer and measures recovery from them. |
| ASSIST-44 | Deferred | P3 | Near-certain data bugs block apply unless explicitly accepted. |
| ASSIST-50 | Deferred | P3 | The model can write structured steps with formula text. |
| ASSIST-51 | Deferred | P3 | Banding and rating are authored in one closed, model-friendly form. |
| ASSIST-52 | Deferred | P3 | The model extends banding, rating, response and request nodes item by item. |
| ASSIST-53 | Deferred | P2 | Each served model has an attributable qualification record per area. |

## Planned improvements

### ASSIST-01 — Writes to stepped nodes land or fail loudly
**Why:** Seven node types carry a `steps` list, and palette-created nodes
start with `steps: []`. `NodeData` rebuilds `code` from `steps` whenever
`steps` is present, so an `update_node {code}` on such a node is overwritten:
on Data Input, Rating Step, Model Score, Scenario Expander and Explore the
saved code becomes empty, on a Transform the model is told the node has no
code, and dry-run, apply and post-save verification all pass because the
semantic diff is derived from the operations rather than the saved config.
`add_node` copies the model's config verbatim, so assistant nodes miss the
palette defaults (`steps: []`, a Data Input's `inputType`). `get_node_schema`
compares the incomplete-transform message by exact equality, so a default
Transform returns an opaque failure instead of `node_has_no_code`.

**Plan:** In the assistant's graph operations, compare each node before and
after the operation: a `code` write on a node that holds or held `steps`, or
removing `steps` from such a node, is refused with an error naming the
free-code form (`ASSIST-03`). `add_node` merges `node_defaults.json` beneath
the model's config. After `with_config`, every key the operation wrote must
hold the written value, and `_steps_error` must not appear, or the plan fails
with `op_not_applied`. Automatic postconditions add a per-node config digest
over authored fields, verified after reparse, and dry-run renders, generates
and reparses stepped nodes in memory to prove their steps survive.
`get_node_schema` matches the incomplete-transform and incomplete-steps
message prefixes. Specify the stepped-node write contract in both assistant
specifications first.

**Acceptance:** Tests on each palette-default stepped type: `update_node
{code}` and `{steps: null, code}` are refused at dry-run and the saved file
is unchanged; a comment-free `[free_code]` write applies and the saved body
holds it; a digest mismatch after save is reported as a verification failure;
`get_node_schema` on `steps: []` returns `node_has_no_code` with the input
schemas; `add_node dataInput {path}` saves with the palette's `inputType`.

**Dependencies:** Ships in the same pull request as `ASSIST-03`.

**Evidence:** `src/haute/_types.py::_materialise_steps`;
`src/haute/assistant/_ops.py::_apply_update_node`;
`src/haute/assistant/_ops.py::_apply_add_node`;
`src/haute/node_defaults.json`; `src/haute/assistant/_tools.py::get_node_schema`;
`src/haute/assistant/_application.py::build_verified_plan`.

### ASSIST-02 — Free-code steps survive a reload and are checked per surface
**Why:** On a frame-start surface (Data Input, Rating Step, Model Score,
Scenario Expander, Explore) a free-code step whose code begins with a comment
loses its steps on reparse, because body extraction starts at the first
statement while step reconciliation compares comments; the node silently
becomes code-only. Analysts hit this in the editor as well as through the
assistant. A free-code step holding a bare expression also passes the
assistant's checks, because the rendered `df = <input>` line satisfies the
retained-result visitor.

**Plan:** Keep comment and blank lines between the function header and the
first statement when extracting a body. Check each free-code step against its
surface's contract: the names it may read are `df`, `pl`, the preamble's
bindings, builtins, names bound by earlier steps or locally, the incoming
edge names on Transform and Load File, and `obj` on Load File; a discarded
bare expression in a step is refused. Reuse the product's step validation for
syntax and scope rather than a second parser, and describe the rule in the
pipeline-config and assistant specifications.

**Acceptance:** A comment-first free-code step round-trips with its steps on
every frame-start surface, through the editor's save path and the assistant's;
a bare-expression step is refused with its step index; a Rating Step step that
reads an input by name is refused with "Rating Step code sees only df"; a
step that calls a preamble helper is accepted.

**Dependencies:** None.

**Evidence:** `src/haute/_ast_helpers.py::_function_body_source`;
`src/haute/_config_builder.py::_reconcile_steps`;
`src/haute/assistant/_ops.py::_validate_polars_result_retained`;
`src/haute/_polars_steps.py::STEPPED_NODE_TYPES`.

### ASSIST-03 — Steps-first authoring with a free-code card
**Why:** Nothing the assistant is taught mentions steps: descriptors show
`steps` as an open array, the examples and recipes write code, and the prompt
states one `df` rule that is wrong on six of the seven code surfaces. Every
node it creates is code-mode, and the editor's switch from steps to code is
one way, so analysts can never open assistant work in the step builder. In
August the configured model failed repeatedly on a two-input aggregation
because it believed inputs were reached as `df['name']`.

**Plan:** Teach and accept one authoring form for new logic: a Transform is
`[{kind: source, input: <edge name>}, {kind: free_code, code}]`, and every
other stepped surface, Load File included, is `[{kind: free_code, code}]`,
where the code transforms `df`, reads other inputs by name only on Transform
and Load File, and starts with a one-line `# intent` comment that becomes the
card title. A hook that needs no post-processing keeps `steps: []`. Existing
structured steps keep their ids and order, and code-mode nodes keep code
editing. Descriptors expose the source and free-code step shapes and each
surface's start and input rule, derived from `STEPPED_NODE_TYPES`; the
structured step grammar is available on demand for strong models. Recipes
that create Polars logic emit the same form.

**Acceptance:** Replays of the August aggregation request, of filling a
palette-default Transform and Rating Step, and of a Load File using `obj` and
a second input, apply in one dry-run, edit the named node rather than a new
one, parse back with their steps and execute on fixture data. No
assistant-authored node on a stepped surface reloads with `_steps_error` or
discarded steps.

**Dependencies:** `ASSIST-01`, `ASSIST-02`, `ASSIST-05`, `ASSIST-06`.

**Evidence:** `src/haute/_polars_steps.py::render_polars_steps`;
`src/haute/assistant/_catalog.py::_USAGE_NOTES`;
`src/haute/assistant/_loop.py::build_system_prompt`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx`.

### ASSIST-04 — A golden snapshot of what the model sees
**Why:** The system prompt, capability index and the tool schemas each
provider receives are assembled from several modules, and no test shows how
a change alters them. Several stale instructions reached the always-on prompt
unnoticed.

**Plan:** Render the system prompt for a fixed project, the canonical and
provider-projected tool schemas, and their hashes into checked-in golden
files, with an explicit update command, and fail with a readable diff when
they change.

**Acceptance:** Editing a prompt sentence or a wire-operation description
fails the golden test with the changed lines; the update command rewrites the
files and the test passes.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_loop.py::build_system_prompt`;
`src/haute/assistant/_tools.py::TOOL_DEFINITIONS`;
`src/haute/assistant/_providers.py::_portable_tools`.

### ASSIST-05 — Teaching text matches the product
**Why:** The always-on prompt says `df` is never pre-bound, which is false on
the hook surfaces, and demands column profiles that the project's policy may
deny, so a simple aggregation always ends in a question when row samples are
off. The authoring guide uses `map_elements` with a lambda for plain
arithmetic, describes a `haute init` starter pipeline that no longer exists,
claims the examples avoid sidecars, and recommends `inputMapping`, which a
stepped Transform rejects. The exemplar list includes security and deployment
fixtures, and a Python class docstring leaks into the `add_node` schema while
`config`'s merge semantics are undocumented.

**Plan:** State the `df` contract per surface, state the effective egress
policy in words and only require a profile when it is permitted, replace the
guide's idioms with vectorised Polars expressions, correct the starter,
sidecar and `inputMapping` text, drop test fixtures from the exemplar index,
and document `update_node` merge and null-removal semantics in the wire
schema.

**Acceptance:** The golden diff from `ASSIST-04` shows the changes; no
always-on text contains "never pre-bound"; under `allow_row_samples = false`
the prompt does not require `get_column_profiles`; the guide contains no
`map_elements`.

**Dependencies:** `ASSIST-04`.

**Evidence:** `src/haute/assistant/assets/authoring_guide.md`;
`src/haute/assistant/_loop.py::build_system_prompt`;
`src/haute/assistant/_wire_ops.py`.

### ASSIST-06 — Catalogue facts come from the product's registries
**Why:** The node descriptors' hand-kept sets have drifted: Load File is a
zero-input source although it passes its first input through and exposes the
loaded object as `obj`; Model Training, Optimisation and Explore are shown
with an output although the editor gives them none, and two examples wire
them into the response; Rating Step is marked multi-input and Apply
Optimisation single-input; the Scenario Expander note names fields that do
not exist and hides the required `stepCount`; banding breakpoints are
described as numeric only; Data Input and Data Output enums keep only the last
branch; required fields are empty for 17 of 19 types. The node index lists
bare ids with no palette names.

**Plan:** Derive source, sink, pass-through and input cardinality from the
product's registries (`SOURCE_NODE_TYPES`, the pass-through set and the
editor's sink-only list, shared through one backend table the frontend
reads), reject outgoing edges from sink-only types in save validation,
correct the per-type notes, merge enums across every branch, publish palette
defaults, and show each node's palette name and one-line purpose in the node
index.

**Acceptance:** A catalogue test asserts that the source and sink sets equal
the product registries; a Load File with an incoming edge validates against
its descriptor; the golden node index shows palette names such as Load File
and Source Switch; an edge out of Model Training is rejected at dry-run.

**Dependencies:** `ASSIST-04`.

**Evidence:** `src/haute/assistant/_catalog.py::_node_ports`;
`src/haute/assistant/_catalog.py::_config_schema`;
`src/haute/_standalone_nodes.py::SOURCE_NODE_TYPES`;
`frontend/src/utils/nodeTypes.ts::SINK_ONLY_TYPES`.

### ASSIST-07 — Example bundles the editor accepts
**Why:** The reusable-submodel example names its occurrence like the inner
node, so any edit passes dry-run and then fails at apply on the codegen name
collision, which dry-run never checks. The ratebook example uses a Data Input
as the banding source, which the editor's Optimise refuses. Two examples wire
training and optimisation into the response. Two bundles label a vehicle's
year as its age and assert it in their goldens.

**Plan:** Rename the submodel occurrence, rebuild the ratebook example
through a Banding node, make training and optimisation terminal branches, fix
the vehicle-age goldens, move security and deployment fixtures out of the
teaching index, and run the codegen collision check inside dry-run
validation.

**Acceptance:** Every bundle parses, regenerates and accepts a no-op edit
through the application service; the collision repro fails at dry-run; the
portfolio test solves the ratebook example with a Banding source.

**Dependencies:** `ASSIST-06`.

**Evidence:** `src/haute/assistant/assets/examples/reusable_submodel/pipeline.py`;
`src/haute/assistant/assets/examples/ratebook_optimisation_apply/pipeline.py`;
`src/haute/codegen.py::_error_on_name_collisions`;
`tests/test_assistant_example_portfolio.py`.

### ASSIST-08 — Recipe corrections
**Why:** The categorical-banding recipe accepts boolean and numeric rule
values, but the runtime compares the column's text form, so a true/false rule
never matches and every row gets the default while dry-run reports success.
Rules that collide once converted to text crash apply. The reference-join
recipe advertises `cross`, which it can never produce. The `parquet_showcase`
recipe exists only to pass one evaluation prompt and writes a literal
showcase column into the response. When the dry-run budget runs out, the
controller's `BLOCKED` text omits the error that caused it.

**Plan:** Accept only string rule values in the recipe and state their text
form, reject colliding rules at dry-run, remove `cross` and `parquet_showcase`
with its self-test case and routing, and end a budget-exhausted turn with the
last sanitised validator message. Canonical matching for non-string values in
the product's own banding editor is a separate decision and is not changed
here.

**Acceptance:** A true/false recipe is refused at dry-run with the expected
text form, and `"true"`/`"false"` rules match every row when executed;
colliding rules fail at dry-run; the showcase recipe is absent from the
catalogue, routing and evaluations; a budget-exhausted turn names the last
error.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_recipes.py::_validate_categorical_rules`;
`src/haute/_rating.py::banding_rule_claim_expr`;
`src/haute/assistant/_recipes.py::plan_recipe`.

### ASSIST-09 — Completion is required only after a mutation attempt
**Why:** The loop requires a completed edit whenever the request contains an
authoring verb or matches a recipe route, so "Can you explain the rating
step?" or "Why does the join produce nulls?" end as failed turns, contrary to
the specification's statement that routing is advisory.

**Plan:** Derive the completion requirement only from a dry-run the model
actually attempted; the verb and route checks no longer set it.

**Acceptance:** The four read-only prompts from the review end completed; a
dry-run followed by an unqualified end still receives one continuation and
then fails as incomplete.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_loop.py::run_turn`;
`src/haute/assistant/_loop.py::_request_requires_completion`;
`src/haute/assistant/_recipes.py::route_recipe_request`.

### ASSIST-10 — Egress corrections
**Why:** Dry-run executes the model's code while resolving schemas, and an
exception's text is returned verbatim, so row values reach the provider under
`allow_row_samples = false`; ordinary Polars cast errors quote cell values the
same way. A plan that executed code over data still declares no egress. A
configuration with `trust = "external"` reports ready although every read and
mutation is then denied, and a host without Git makes the status endpoint
fail with a server error. Free-code step text is already masked by the
recursive redaction when executable source is not allowed; that stays.

**Plan:** Reduce execution errors during dry-run and schema reads to the
exception type, step or line, and column names unless row samples are
allowed; make plan egress truthful; report reads and mutations as denied in
readiness under external trust; return a structured readiness reason when Git
is unavailable; and pin the free-code masking with an end-to-end payload test.

**Acceptance:** The review's error-text probe returns no data values under
`allow_row_samples = false`; a payload test on a `[source, free_code]` node
shows the step structure with its code masked; readiness under external trust
and without Git returns named reasons.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_tools.py::_redact_config_value`;
`src/haute/assistant/_application.py::build_verified_plan`;
`src/haute/assistant/_config.py::mutations_readiness`.

### ASSIST-11 — New local file inputs pass dry-run on an inferred schema
**Why:** A Data Input the plan adds has no snapshot yet, so schema-only
dry-run fails with `input_snapshot_missing`, and splitting the plan leaves the
new node disconnected: no tool sequence can add a CSV input and transform it.
Separately, a dataset the model inspected and later renamed blocks every
dry-run for the rest of the history window, and a row-count-only refresh
produces a stale-evidence error that names no file.

**Plan:** In schema-only execution, a missing snapshot for a supported local
format resolves the schema from the Data Input's own configuration (format,
delimiter, sheet, schema overrides) and records the tier as inferred; a remote
or unsupported source fails loudly with a named remedy; the snapshot is still
built at the first preview. Evidence for a dataset that no longer exists is
dropped when the model re-inspects the project, and stale-evidence errors name
the file. Specify the inferred tier in the execution-engine and assistant
specifications first.

**Acceptance:** A plan adding a CSV Data Input, a Transform and their edge
applies at the inferred tier; a remote table without a readable schema returns
a named error; renaming an inspected dataset no longer blocks later dry-runs.

**Dependencies:** None.

**Evidence:** `src/haute/_input_providers.py`;
`src/haute/assistant/_tools.py::get_dataset_schema`;
`src/haute/assistant/_application.py::build_verified_plan`.

### ASSIST-12 — Every request is bound to the canvas pipeline
**Why:** The panel always sends `pipeline: null`, and the server's default
pipeline rule differs from the editor's, so in a project whose first pipeline
is new or empty the assistant commits edits to another file while the canvas
never changes.

**Plan:** Session create, list and message carry a required `source_file`
taken from the loaded document; the session response echoes it; the server
refuses a mismatch with a 409 naming the chat's pipeline; and the panel
refuses to send when its document differs. Backend and frontend change
together.

**Acceptance:** In the two-pipeline repro the assistant edits the pipeline on
the canvas; chat lists are filtered by source file; a store test refuses a
mismatched source.

**Dependencies:** None.

**Evidence:** `frontend/src/api/assistant.ts::createAssistantSession`;
`src/haute/routes/assistant.py::_find_default_pipeline`;
`src/haute/schemas.py::AssistantSessionResponse`.

### ASSIST-13 — Transcript order and typed turn outcomes
**Why:** Every text delta of a turn is appended to the first assistant bubble,
so later prose appears above the tool rows it followed. `NEEDS_INPUT:` and
`BLOCKED:` replies render as raw prefixes under a green "Turn completed", and
readiness reasons are hidden on the opening screen behind a Retry that cannot
succeed.

**Plan:** Keep text and tool rows in stream order. The completed event carries
a required outcome: `applied`, `answered`, `needs_input` with the question,
`blocked` with the sanitised reason, or `committed_unverified` when a save
committed but verification failed, rebuilt on resume. The panel renders a
question card with a one-click "you choose and tell me" reply, a blocked card
that states whether anything was saved, and readiness reasons with their fix.

**Acceptance:** Component tests render text, tool and text in order and each
outcome card; a resumed chat shows the same outcome; a verification failure
after commit never says nothing changed.

**Dependencies:** `ASSIST-09`.

**Evidence:** `frontend/src/stores/useAssistantStore.ts`;
`frontend/src/panels/assistant/TranscriptEntryView.tsx`;
`src/haute/assistant/_loop.py::run_turn`.

### ASSIST-14 — Loop and provider corrections
**Why:** The history window stops at the first turn that exceeds forty
messages, so after one long turn the next turn has no history at all. The
OpenAI-compatible stream drops accumulated tool calls when the finish reason
is `stop`. The Anthropic adapter sends each parallel tool result as its own
user message. A Databricks model that sends Python-style booleans is told only
`wrong_type`. The support matrix names 2025 model snapshots and not the
configured model.

**Plan:** Always keep the newest turn, trimming its oldest completed
tool-call rounds as whole pairs; keep streamed tool calls whatever the finish
reason; group a round's tool results; name the expected JSON type in
validation errors; refresh the support matrix for the configured models.

**Acceptance:** A turn with twenty-one tool calls is followed by a turn that
still sees the original request; the simulated stop-with-calls stream yields
its calls; the matrix lists `databricks-qwen35-122b-a10b`.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_session.py::history_window`;
`src/haute/assistant/_providers.py::OpenAIProvider`;
`src/haute/assistant/_providers.py::_anthropic_messages`;
`tests/assistant_eval/support_matrix.json`.

### ASSIST-15 — Git history for the right pipeline and files
**Why:** Reading a historical graph ignores the source file, so Compare (and
the planned Undo) parse the wrong pipeline in a project with several. Save
commit messages drop a character ("Updated ipeline") because the status
output is stripped before its fixed-width prefix is sliced.

**Plan:** Pass the source file when reading a pipeline at a commit, and slice
each status line before stripping.

**Acceptance:** The two-pipeline probe reads the edited pipeline; a save
commit message names `pipeline` and the changed sidecar correctly.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_helpers.py::commit_pipeline_graph`;
`src/haute/_git_transactions.py::commit_save`.

### ASSIST-16 — Column profiles run in the preview worker
**Why:** Profiles execute in the assistant's tool thread, where the engine
refuses any unvalidated join or unestimable aggregation, so they fail on
realistic pipelines.

**Plan:** Run profiles in the interactive preview worker with its memory
budget, admission and cancellation, keyed per session.

**Acceptance:** Profiles downstream of a join and of an aggregation return
levels; a stopped turn cancels its profile.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_tools.py::get_column_profiles`;
`src/haute/routes/pipeline.py`.

### ASSIST-17 — Self-test harness corrections
**Why:** The sandbox's project root leaks between cases, so a multi-case run
passes only its first file-input case; the connectivity check covers the whole
graph; two cases demand code the validator now rejects; one case forbids node
types that do not exist; and the run inherits the invoking project's egress
policy.

**Plan:** Bind and restore the sandbox root per case, check connectivity of
changed nodes and their neighbours, fix the stale cases, validate node-type
names against `NodeType`, pin the self-test's egress policy, and run each case
in its own process.

**Acceptance:** Six reference trajectories pass in one run; the stale cases
express current contracts; an unknown node-type name fails case loading.

**Dependencies:** `ASSIST-08`.

**Evidence:** `scripts/run_assistant_self_test.py::run_self_test_case`;
`src/haute/_sandbox.py`; `tests/assistant_eval/self_test/smoke_showcase_parquets.json`.

### ASSIST-18 — Renaming fails loudly on consumers it cannot reconcile
**Why:** `rename_node` rewrites edge endpoints only; a consumer whose code,
steps, input mapping or scenario map names the old node fails schema
validation with a bare name error.

**Plan:** Before applying a rename, list every consumer whose configuration
or code names the node and refuse with each consumer and field, until
`ASSIST-25` reconciles the structured ones.

**Acceptance:** Renaming a node with a coded consumer fails at dry-run naming
the consumer and field; a rename with only edge consumers applies.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_ops.py::_apply_rename_node`.

### ASSIST-19 — Product config validators run at dry-run
**Why:** Six invalid modelling configs (unknown family, a loss the family
rejects, missing target, target among the features) passed dry-run at the
schema tier, and a Load File pointing at a missing file validated. The
product's training validators run only when training starts.

**Plan:** Run the pure configuration checks that training and loading already
apply (family, loss, parameters, target, features, offset and weight columns,
Load File path and type) inside save validation, one path for the editor and
the assistant.

**Acceptance:** Each of the six invalid configs fails at dry-run with the
product's message; the valid config applies.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_train_config.py::validate_glm_params`;
`src/haute/modelling/_train_config.py::TrainingConfigError`.

### ASSIST-20 — Evaluation specification and offline replay
**Why:** Only one of twelve self-test cases is driven through the real tools
in CI, scoring checks node types and edges but never configuration or
execution, and the qualification runner does not exist, so no change can be
judged against the tasks analysts ask for.

**Plan:** Specify the evaluation (case format, tiers, scoring layers, and the
boundary that execution happens in the harness, never in the assistant) in a
supplemental assistant evaluation document. Replay reference trajectories
through the real tools in CI with a divergence check, and score build cases by
protocol, structure, configuration subset, collateral change, editor
compatibility (stepped nodes stay stepped) and execution against goldens
computed independently in plain Polars. Replay evidence and live-model
evidence are reported separately.

**Acceptance:** The replay suite runs in CI; deliberately regressing
`ASSIST-01` makes a named case diverge; the step-corpus cases stay stepped and
match their goldens.

**Dependencies:** `ASSIST-04`, `ASSIST-17`.

**Evidence:** `scripts/run_assistant_self_test.py::run_self_test_case`;
`tests/test_assistant_self_test.py`; `tests/test_polars_steps_corpus.py`.

### ASSIST-21 — Step-level edits and readable step summaries
**Why:** Editing one step means resending a whole steps list, including
free-code text the model may not be allowed to read, and the graph summary
shows config key names only, so the model cannot see which nodes are stepped,
code-mode or incomplete.

**Plan:** Add an `edit_steps` operation (insert after, replace, or remove by
step id, with ids assigned to new steps) whose validator errors name the step
id, and show each node's authoring state and one line per step in the graph
summary, with free-code text masked by policy. A free-code step the model
cannot read may be removed or replaced as a whole, never edited in place.

**Acceptance:** An in-place edit replays through `edit_steps` under a policy
that masks free code; errors name the step id; the summary shows authoring
states.

**Dependencies:** `ASSIST-03`.

**Evidence:** `src/haute/assistant/_wire_ops.py`;
`src/haute/assistant/_render.py::render_pipeline_graph`.

### ASSIST-22 — Validated node cards replace key-only examples
**Why:** `get_example` returns each node's config key names and a count, so
no example can teach a banding rule, an output path or a modelling family, and
the walkthroughs failed on exactly those values. Five of nineteen node types
link an example.

**Plan:** For every node type, keep a minimal and a realistic configuration
with its field semantics (breakpoints on numbers and dates with an open-ended
last band, string categorical values, `$[:].col` output paths, Load File with
`obj`, a GLM offset under a log link, an optimiser ratebook with a Banding
source), served through the descriptors in place of the key-only example
view. CI dry-runs and executes every card.

**Acceptance:** Every node type has a card that CI executes; a card drifting
from its validator fails CI; replaying the walkthrough's failed banding and
output attempts against the cards produces valid first attempts.

**Dependencies:** `ASSIST-06`, `ASSIST-07`.

**Evidence:** `src/haute/assistant/_assets.py::load_example`;
`src/haute/assistant/_render.py::render_pipeline_graph`.

### ASSIST-23 — Actionable errors and a progress-based retry budget
**Why:** Validator errors rarely say which inputs and columns were available
or what would fix them, and one corrected dry-run retry is allowed before the
turn ends `BLOCKED`, so independent, fixable errors end turns early on
mid-tier models.

**Plan:** Every tool error carries where it happened, the inputs and columns
available, one concrete fix and close-match suggestions. A turn allows up to
four failed dry-runs and stops early only when an identical plan is resent or
the same diagnostic repeats for the same attempted operations; its final text
carries the last sanitised error. Amend the retry rule in the specification
first.

**Acceptance:** Replaying the August two-input failure yields an error naming
both inputs' columns and the by-name form; three independent errors converge
in one turn; an identical resend stops after two attempts.

**Dependencies:** `ASSIST-08`, `ASSIST-20`.

**Evidence:** `src/haute/assistant/_ops.py::_validate_polars_named_inputs`;
`src/haute/assistant/_loop.py::run_turn`.

### ASSIST-24 — Per-turn context and a stable system prefix
**Why:** Every walkthrough spent three to eight reads orienting before its
first dry-run, and in August an edit meant for a named node landed on a new
one. The system prompt is rebuilt each turn with the node count and routing
text, which also prevents prompt caching.

**Plan:** Freeze the system prefix for a session and add a per-turn context
block after the user message: a bounded graph brief (id, palette name, label,
inputs with columns, output columns, authoring state), the base revision, the
effective egress policy in words, the analyst's selected nodes, and an opt-in
preview error reduced by policy. The request accepts a typed context object.

**Acceptance:** The golden prefix is identical across turns; replayed
single-node edits need no read before their first dry-run; the preview error
is reduced under `allow_row_samples = false`.

**Dependencies:** `ASSIST-06`, `ASSIST-10`, `ASSIST-12`.

**Evidence:** `src/haute/routes/assistant.py`;
`src/haute/assistant/_loop.py::build_system_prompt`;
`src/haute/schemas.py::AssistantMessageRequest`.

### ASSIST-25 — Rename reconciles structured references
**Why:** The editor's rename rewrites step inputs, input mappings, scenario
maps and optimiser input names, but only in the frontend; the assistant's
rename cannot.

**Plan:** Move that reconciliation into the backend operation, using the step
renamer for stepped consumers, and keep the fail-loud list from `ASSIST-18`
for free-code and code-mode consumers.

**Acceptance:** Renaming a node with stepped and mapped consumers applies in
one dry-run; a free-code consumer is listed in the error.

**Dependencies:** `ASSIST-18`, `ASSIST-03`.

**Evidence:** `src/haute/_polars_steps.py::rename_step_inputs`;
`frontend/src/utils/nodeUpdatePlan.ts`.

### ASSIST-26 — Column help after free-code cards
**Why:** The step editor loses its known columns after a free-code card, so an
analyst who adds a structured card after the assistant's free-code card gets
no column suggestions.

**Plan:** The render endpoint returns the lazily resolved schema at each
free-code boundary and the editor's derived-column logic uses it. This polishes
existing editor behaviour and adds no step capability.

**Acceptance:** A `[free_code, with_column]` node offers the columns the free
code creates; checked in a browser pass on a scratch project.

**Dependencies:** `ASSIST-03`.

**Evidence:** `frontend/src/panels/editors/polarsSteps/derivedColumns.ts`.

### ASSIST-30 — A structural controller
**Why:** Completion, routing and clarification are driven by regular
expressions over the user's words, which misroute ordinary phrasing and turn
delegated choices ("pick any four features") into questions.

**Plan:** Remove the lexical completion check, the recipe and
material-clarification hints and the continuation text. Keep the
`NEEDS_INPUT:` and `BLOCKED:` prefixes, mapped to the typed outcome. One
end-of-turn nudge fires only on tracked state (a validated plan never applied,
or a failed dry-run with no later success). The prompt tells the model to make
delegated choices and state them. Remove the lexical sections from the
specification.

**Acceptance:** "Pick any four features" proceeds and states its choices;
explanation questions end answered; a validated but unapplied plan triggers
one nudge.

**Dependencies:** `ASSIST-09`, `ASSIST-13`, `ASSIST-23`.

**Evidence:** `src/haute/assistant/_loop.py::effective_authoring_request`;
`src/haute/assistant/_loop.py::_request_routed_system_prompt`;
`src/haute/assistant/_recipes.py::route_recipe_request`.

### ASSIST-31 — A change card built from what was saved
**Why:** A turn that edits the graph ends with a fixed sentence and a row of
hashes; the analyst cannot see what changed.

**Plan:** After every apply, build a value-free change record from the actual
diff and the before and after graphs, using palette names: node chips for
added, changed, removed and renamed nodes, configuration changes in plain
words, step kinds and counts (never free-code text), edges and warnings, with
the Git commit. Dry-run takes a required short summary and a list of
assumptions stored with the plan. The record is persisted, rebuilt on resume,
streamed as a typed event, and replaces the fixed sentence; dry-run and apply
results become compact.

**Acceptance:** Component tests render change cards; a payload test finds no
configuration values or free-code text in the record; dry-run and apply
payloads for a four-node batch stay under one kilobyte.

**Dependencies:** `ASSIST-06`, `ASSIST-10`, `ASSIST-13`, `ASSIST-15`.

**Evidence:** `src/haute/assistant/_application.py::ApplicationResult`;
`src/haute/assistant/_loop.py::run_turn`.

### ASSIST-32 — Several applies per turn
**Why:** A successful apply ends the turn, so the rest of a multi-part
request is silently dropped and a pipeline has to be built one user message
per stage.

**Plan:** Allow further dry-run and apply rounds after an apply, within the
turn's tool-call and time budgets, with the affected nodes' brief refreshed
after each apply and a change card per apply. Committed changes are recorded
separately from any claim that the request is complete. Amend the
terminal-apply rule in the specification with its recorded rationale.

**Acceptance:** A replayed source, features, banding, rating and response
build finishes in one turn with a change card per stage; the one-apply and
multi-apply variants are compared on the configured model once it answers.

**Dependencies:** `ASSIST-14`, `ASSIST-23`, `ASSIST-30`, `ASSIST-31`.

**Evidence:** `src/haute/assistant/_loop.py::run_turn`.

### ASSIST-33 — Undo, Compare and canvas focus
**Why:** Every assistant apply clears the canvas undo stack and re-fits the
whole graph, and the only way back is the Git panel.

**Plan:** "Undo this change" saves the graph at the change's parent commit
through the save service, only while the current revision is the change's
result, and notes it in the chat; "Compare" opens the existing comparison
view. Assistant updates carry their origin, ring and centre the changed nodes
instead of re-fitting, and assistant saves use the change headline as the Git
message.

**Acceptance:** Undo restores the file byte for byte, removes an added
sidecar, and refuses once a later save exists; a browser pass shows the
changed nodes ringed.

**Dependencies:** `ASSIST-15`, `ASSIST-31`.

**Evidence:** `src/haute/routes/_helpers.py::commit_pipeline_graph`;
`frontend/src/stores/useGraphStore.ts`.

### ASSIST-34 — Panel experience
**Why:** A long turn can collide with the analyst's own edits, a closed panel
gives no sign that edits are still landing, the analyst cannot point the
assistant at a node or its error, and nothing explains how to configure the
assistant.

**Plan:** Make the canvas read-only while a turn streams, with a Stop control;
show progress and unseen outcomes on the toolbar button; add a context chip
for the selected node and an "Ask the assistant to fix" action beside a node's
run error; show the model name and an empty state per pipeline; and add a
documentation page for the `[assistant]` table and its egress policy,
describing the editor rather than the command line.

**Acceptance:** Component tests for the chip, the progress states and the
read-only canvas; the documentation tests pass.

**Dependencies:** `ASSIST-13`, `ASSIST-24`.

**Evidence:** `frontend/src/panels/assistant/AssistantPanel.tsx`;
`frontend/src/components/Toolbar.tsx`.

### ASSIST-35 — Task-shaped tools, with recipes as an operation
**Why:** Fifteen tools split reads by storage layer, the recipe path needs
three calls and blocks primitive edits while a recipe is pending, and its
schema uses most of the portable projection's budget.

**Plan:** Consolidate to about nine tools: `get_pipeline`, one
`inspect_node` for schema, configuration and profile with a separate egress
check per part, `find_data`, `read_reference` for descriptors, cards and the
guide, project knowledge, `dry_run_graph_edits` with recipes as an operation
kind, and `apply_graph_plan`. Migrate cases, trajectories and panel labels in
the same change, and rewrite the tool-surface section of the specification.

**Acceptance:** The golden shows the smaller surface; the replay suite passes
after migration; a recipe and a primitive operation apply in one plan.

**Dependencies:** `ASSIST-20`, `ASSIST-30`.

**Evidence:** `src/haute/assistant/_tools.py::TOOL_DEFINITIONS`;
`src/haute/assistant/_recipes.py::plan_recipe`.

### ASSIST-36 — Evaluation on realistic pipelines and live models
**Why:** Evaluation fixtures are two three-row tables copied under many
names, every case is one turn on an empty or one-node project, and nothing
covers building from data, modelling, the optimiser, editing an existing
pipeline or recovering from errors.

**Plan:** Build save-canonical fixtures (a motor-pricing pipeline with a
Source Switch, stepped features, banding on numbers, dates and categories,
multi-table rating, model scoring, a nested API response and Explore, and a
submodel project) with goldens computed in plain Polars; write about forty
cases across authoring areas, read-only questions, delegation, multi-turn
work, recovery and safety, split into development and holdout; add a live
runner with record, compare and variant commands. Fold the qualification
script and held-out format into this one harness.

**Acceptance:** Every case replays in CI; compare reports per-area results and
flips; the first live baseline on the configured model is recorded once the
endpoint answers.

**Dependencies:** `ASSIST-20`, `ASSIST-35`.

**Evidence:** `scripts/run_assistant_evaluation.py`;
`tests/assistant_eval/support_matrix.json`.

### ASSIST-37 — History compaction, prompt caching and thinking
**Why:** A message-count window discards context without regard to tokens,
the evidence ledger is tied to that window, and the Anthropic adapter uses no
prompt caching, thinking or effort and drops thinking blocks.

**Plan:** Keep an append-only history compacted at turn boundaries into turn
records (request, final text, change cards, revisions, open questions), carry
the evidence ledger in session state, and for Anthropic add a cache
breakpoint on the stable prefix, adaptive thinking with progress updates,
explicit effort, and thinking replay within a turn.

**Acceptance:** A ten-turn replay keeps its change cards through compaction;
a follow-up dry-run does not fail on stale evidence; adapter tests assert the
cache breakpoint and a stable thinking prefix.

**Dependencies:** `ASSIST-14`, `ASSIST-24`, `ASSIST-31`.

**Evidence:** `src/haute/assistant/_session.py::history_window`;
`src/haute/assistant/_providers.py::AnthropicProvider`.

### ASSIST-38 — Tool-schema projections per provider
**Why:** Every provider receives one conservative projection that strips
per-operation required fields, while Databricks documents a limit of sixteen
keys and no composition that the projection exceeds.

**Plan:** Derive two projections from the wire operation models: a flat
compatible projection within Databricks' limits, and the canonical
discriminated union with per-operation requirements for Anthropic and OpenAI,
strict only on closed read tools. Probe the configured Databricks models
against the documented limit first.

**Acceptance:** Tests show both projections derive from the wire models, the
compatible one within sixteen keys; the probe result is recorded here.

**Dependencies:** `ASSIST-35`.

**Evidence:** `src/haute/assistant/_providers.py::_portable_tools`;
`src/haute/assistant/_wire_ops.py`.

### ASSIST-39 — A checklist for multi-stage builds
**Why:** A long build gives the analyst no view of what remains, and a model
that forgets its plan cannot be nudged on it.

**Plan:** Once several applies per turn are measured, add a build-plan tool
whose items the controller ticks from committed changes, shown as a checklist
in the panel, with open items resumable by "continue".

**Acceptance:** A replayed multi-stage build shows its checklist ticking per
change; an interrupted build resumes its open items.

**Dependencies:** `ASSIST-32`, `ASSIST-36`.

**Evidence:** `src/haute/assistant/_loop.py::run_turn`.

### ASSIST-40 — The data-check execution contract and permission
**Why:** Dry-run proves schemas only, so pipelines that run but are wrong pass
(every row in the default band, an emptied filter, a join that matches
nothing, rating-table misses). Checking data executes the pipeline, which the
specification's "no execution tools" boundary and its rule that execution
defines its own authorisation both govern, and project Python runs with
process privileges.

**Plan:** Specify the check before building it: what it executes (the changed
nodes' lineage in the interactive preview worker, under admission, a deadline
and a node cap, never sinks, training, optimisation or deployment), what it
returns (value-free counts and shares, with truncation and sampling recorded),
what it is bound to (the candidate graph, source generation and check
version), and add a required `allow_aggregate_statistics` flag to
`[assistant.egress]`.

**Acceptance:** A Codex plan review of the specification is resolved; the
configuration test requires the new flag and rejects it under external trust.

**Dependencies:** `ASSIST-10`, `ASSIST-16`.

**Evidence:** `src/haute/assistant/_config.py::resolve_egress_policy`;
`src/haute/_execution_admission.py`.

### ASSIST-41 — Advisory data findings after dry-run
**Why:** A model that must choose to call a check tool often will not, and the
analyst never sees the data-level consequences of a plan.

**Plan:** When the permission allows, run the specified check automatically
after an eligible schema-tier dry-run, outside the save lock and the plan
hash, and attach advisory findings to the dry-run result and the change card:
rows in and out, null shares of new columns, per-rule banding counts, rating
misses and unused entries, join matches, and execution errors; ineligible
nodes report why they were not checked.

**Acceptance:** Seeded scenarios report all-default banding, a 60% rating
miss share, an emptied filter and a failed many-to-one join validation; a
partial join is informational; a payload test finds no data or configuration
values; latency is recorded on 100k, 1M and 5M rows.

**Dependencies:** `ASSIST-40`.

**Evidence:** `src/haute/_rating.py::banding_rule_claim_expr`;
`src/haute/assistant/_application.py::build_verified_plan`.

### ASSIST-42 — Inspect a saved node's data
**Why:** "Why is `total_incurred` null for some quotes?" can only be answered
by guessing from code.

**Plan:** Expose the same check for the saved graph: per-node status with the
recorded error and step line, upstream failures collapsed to the node that
caused them, a column's null share along its lineage, and join matches on the
path.

**Acceptance:** A replayed null-diagnosis request is answered from one call;
repeated downstream errors collapse to one attributed error.

**Dependencies:** `ASSIST-41`, `ASSIST-35`.

**Evidence:** `src/haute/_graph_walker.py`; `src/haute/trace.py`.

### ASSIST-43 — Data findings in evaluation
**Why:** Recovery from pipelines that run but are wrong is the loop analysts
need most, and nothing measures it.

**Plan:** Add data findings as an extra scoring layer beside the independent
execution goldens, and seed recovery cases (a boolean banding rule, an emptied
filter, a mistyped join key, rating casing drift, a many-to-one join on
duplicate keys).

**Acceptance:** Each seeded bug is reported in replay; the live baseline
reports the recovered-within-budget rate per model.

**Dependencies:** `ASSIST-36`, `ASSIST-41`.

**Evidence:** `scripts/run_assistant_self_test.py::run_self_test_case`.

### ASSIST-44 — Blocking findings with explicit acceptance
**Why:** Advisory findings can be ignored. The package is deferred because
empty frames, all-default bands and unmatched joins are sometimes intended,
and whether blocking helps must be measured first.

**Plan:** If evaluation shows models ignoring near-certain findings, have
apply refuse an unaccepted blocking finding and record accepted findings on
the change card, identified by finding instance.

**Acceptance:** Seeded cases are refused without acceptance and land with it;
a partial join never blocks.

**Dependencies:** `ASSIST-41`, `ASSIST-43`.

**Evidence:** `src/haute/assistant/_application.py::build_verified_plan`.

### ASSIST-50 — Structured steps with formula text
**Why:** Free-code cards are editable but opaque to the step builder's
structured forms. The raw step grammar is about 8.8k characters of schema and
its operand and expression shapes caused errors even when written by hand.
The package is deferred until evaluation measures how much free code is
written.

**Plan:** Accept `with_column`, `filter`, `group_by` and `join` steps with
formula text and bare literals, lowered by a Python port of the editor's
formula parser to the canonical steps the editor reads, with shared parity
fixtures in both test suites. This adds a backend capability.

**Acceptance:** Lowered steps render identically to their raw-grammar forms;
parity fixtures pass in both suites; the evaluation shows a gain in structured
steps without a loss in pass rate on the configured model.

**Dependencies:** `ASSIST-21`, `ASSIST-36`.

**Evidence:** `frontend/src/panels/editors/polarsSteps/formula.ts`;
`src/haute/_polars_steps.py::render_polars_steps`.

### ASSIST-51 — Typed banding and rating authoring
**Why:** Banding rules and rating tables are the most error-prone
configurations; the package is deferred until the node cards from `ASSIST-22`
are shown not to be enough.

**Plan:** Author banding factors with string boundaries (number, date or
open-ended) and string categorical values checked against the column's text
form, and rating tables as positional rows, lowered to canonical form; add a
banding-to-rating chain whose rating levels come from the band labels; replace
the single-node recipes, rewriting the recipes section of the specification.

**Acceptance:** The banding-to-rating replay case passes; shape errors in the
evaluation fall.

**Dependencies:** `ASSIST-22`, `ASSIST-36`.

**Evidence:** `src/haute/assistant/_recipes.py::plan_recipe`;
`src/haute/_banding_config.py`.

### ASSIST-52 — Item-level edits for specialist nodes
**Why:** Adding a factor, a rating table, a response field or a request column
means replacing a whole list the model may not be allowed to read. Deferred
until the evaluation shows the whole-list edits failing.

**Plan:** Add upsert and remove operations for a banding factor, a rating
table, an output mapping row and an API input column, keyed by their output
names.

**Acceptance:** Replay cases add a factor and a response field under a
restricted egress policy.

**Dependencies:** `ASSIST-35`.

**Evidence:** `src/haute/assistant/_ops.py::_apply_update_node`.

### ASSIST-53 — Qualification per model
**Why:** Nothing records which models the assistant works with. Deferred until
the endpoints answer and the case portfolio exists.

**Plan:** Run the holdout split three times for the configured Databricks
models, and for Anthropic and OpenAI models when a project provides keys,
with per-area thresholds and prices in the support matrix and infrastructure
failures excluded.

**Acceptance:** The support matrix carries a qualified or unqualified verdict
per configuration with run identifiers.

**Dependencies:** `ASSIST-36`, `ASSIST-43`.

**Evidence:** `tests/assistant_eval/support_matrix.json`.
