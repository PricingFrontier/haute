# Assistant Evaluation

## Purpose

The evaluation judges every assistant change against the tasks analysts ask
for, area by area. This document owns the fixture projects, the egress profiles
cases run under, the case format, the reference trajectories, the two
evaluation tiers, the scoring layers and efficiency metrics, the boundary
between the assistant and execution, the live runner's commands and variants,
and how evidence is reported. The assistant
itself is specified in [the assistant specification](high-level.md) and its
harness modules in [the low-level specification](low-level.md).

## Fixture projects

Each case runs in a copy of one project under `tests/assistant_eval/projects/`,
holding its project configuration and pipeline file and no symbolic link. Every
project is save-canonical: parsing its pipeline and saving it again through the
transactional save service the editor and the assistant use rewrites none of its
files, so a fixture is exactly what Haute itself writes, sidecars, layout and
generated code included. All data is synthetic.

- `motor_pricing` is a realistic motor pipeline of ten nodes: a quote request
  (`quote_request`, an API input whose `quotes` table reads a nested request) and
  a batch (`batch_quotes`) behind a Source Switch (`policies`, scenarios `live`
  and `nb_batch`); stepped features (`rating_features`); a Model Scoring node
  (`claim_frequency`) over a tiny CatBoost model; banding on a number, a date
  and a category (`rating_bands`); a two-table rating with a combined premium and
  a rounding step (`base_premium`); a quote response with nested fields
  (`quote_response`); an Explore branch (`premium_explore`); and a batch output
  (`priced_batch`). Its `data/` also holds files no node reads yet (region
  loadings, renewal quotes and a claims history) for cases that add them. Its
  batch holds a row on every breakpoint boundary the cases band at (driver ages
  24 and 59, the inception date 2024-12-31, vehicle values of 5 and 15
  thousand, and 2, 3 and 9 licence years), so a factor that inverts a stated
  closure changes an executed band (see Scoring layers).
- `submodel_pricing` prices policies through an occurrence of a
  `vehicle_factors` submodel defined in `modules/vehicle_factors.py`, followed by
  a stepped `premium` Transform.
- `broken_pricing` is seeded broken: its stepped `rating_features` Transform
  reads a column its source does not have, so recovery cases start from a
  failing node.
- `ordinary_pricing`, `join_parquets`, `showcase_parquets`, `stepped_pricing`
  and `polars_corpus` are small projects for focused cases; `polars_corpus`'s
  data files are the Polars step corpus's normal synthetic inputs from
  `tests/assistant_eval/_frames.py`.

**Fixture models.** A project's `models/<node>.cbm` is the artefact of its Model
Scoring node `<node>`, whose checked-in `run_id` is the placeholder of 32 zeros.
Preparing a copy logs each artefact as a run in the copy's local MLflow folder
(the project's `[mlflow] folder`) and writes that run's id over the placeholder
in the node's sidecar before anything reads the graph, so the dry-run resolves
the scored schema and execution scores with the real model. A model file without
a matching placeholder, or a placeholder without a model file, fails loudly.

**Previewed state.** Preparing a copy then builds the snapshot of every input
that executes from one, as previewing the pipeline does: each Quote Input's
tables from its example request, and each Data Input that does not read its file
directly. A case therefore starts from a project the analyst has previewed, so
the graph brief and every dry-run resolve the pipeline's columns.

## Egress profiles

Each case runs under a named egress profile, written into its project copy's
`[assistant.egress]` table with the provider trust of the invoking project (a
replay's trust is `organization`). Trust describes the endpoint and is
validated against it, so it is never the case's to choose; an external
provider is refused before any case runs. The profiles are a closed set:

- `project`, the policy a configured project holds and the profile of every
  case but two: `max_sensitivity = "restricted"`, so `inspect_node` reads saved
  node configuration, with project knowledge and executable source permitted
  and row samples not.
- `metadata_only`: `max_sensitivity = "internal"`, with project knowledge,
  executable source and row samples all withheld, so saved node configuration
  is withheld too. Two cases run under it: `motor_value_band_factor_withheld`,
  a list edit whose blind rewrite the dry-run must refuse as `config_withheld`
  so that the turn asks (`needs_input`) and saves nothing, and
  `breakpoint_age_banding`, which adds a node from schemas alone.

This replaces the rule that "what the cases may send is the harness's decision:
internal pipeline metadata, and no project knowledge, executable source or row
samples, whatever the invoking project permits". Every fixture is synthetic, so
that ceiling protected nothing, and it measured a policy no configured project
holds: under it `inspect_node` refused every node's configuration, so each
request to change one entry of a saved list asked the model to restate entries
it could not read, and a recovery case could not see the code it had to fix.
The `project` profile measures the assistant as configured projects run it,
and `metadata_only` keeps the withheld policy measured where it is the point of
the case.

## Case format

Each case is one JSON file in `tests/assistant_eval/cases/`, in the closed case
shape version 4: `schema_version`, `id`, `fixture_version`, `project_fixture`,
`area`, `split`, `egress`, `inapplicable_variants` and `turns`. An unknown or
missing key at any level fails loading, and so does a required or forbidden
node-type name that is not a node type.

- `area` is one of `steps`, `banding`, `rating`, `joins`, `outputs`,
  `modelling`, `model_score`, `optimiser`, `submodels`, `source_switch`,
  `explore`, `recovery`, `read_only`, `delegation`, `multi_turn`,
  `multi_stage`, `clarification` and `safety`; results are reported per area.
- `split` is `development` or `holdout`. Development cases may be studied while
  changing the assistant; holdout cases are run to measure it and are not used
  to tune prompts, cards or examples. Tier 0 replays both splits.
- `egress` names the case's egress profile, `project` or `metadata_only`.
- `inapplicable_variants` lists the live-run variants the case cannot be
  measured under (see Tiers), usually none. It never names `multi_apply`, the
  product's own behaviour, which tier 0 replays every case under.
- `turns` holds one or more `{request, expectations}` entries, run in order in
  one session, so a multi-turn case refines what an earlier turn saved.

Each turn's expectations are closed and every key is required:

- `outcome`: the typed outcome the turn must end with, `applied`, `answered`,
  `needs_input` or `blocked`.
- `saves`: whether the turn saves at least one change. An `applied` turn saves
  and an `answered` turn does not; a `needs_input` or `blocked` turn may follow
  saved changes, so a request whose first part is saved and whose second part is
  refused expects `blocked` with `saves: true`.
- `required_node_types` and `forbidden_node_types`.
- `required_edges`: source, target and target handle; a null handle matches an
  edge by its endpoints, and a named handle is an exact port assertion.
- `require_connected_graph`.
- `forbidden_assistant_text`: canary values the assistant's text must never
  contain.
- `modified_nodes`: the nodes existing before the turn that the turn may change.
- `node_configs`: for a node, the configuration subset it must hold after the
  turn.
- `execution`: goldens, each naming a `node`, the run `scenario` it executes
  under (`live` where the pipeline has no Source Switch), its plain-Polars
  `golden` code and whether the comparison is `order_free`.
- `efficiency`: `null`, or the limits `max_provider_round_trips`,
  `max_tool_calls`, `max_failed_tool_calls` and `max_duplicate_static_reads`
  for a case that is about efficiency, such as a one-node edit the turn context
  makes possible without a graph read. Such a limit leaves room for what the
  prompt requires of every turn: a step edit (`motor_new_driver_flag`,
  `smoke_step_edit`) allows three tool calls, the descriptor read the prompt
  asks for before primitive operations, the dry-run and the apply, and four
  provider round trips, the last being the closing reply that follows an
  apply.

A turn that saves nothing declares no node configurations and no goldens.

The portfolio covers the areas above on the fixture projects: step edits and
new Polars logic on Transforms (including items from the Polars step corpus, in
their taught free-code form and the corpus's structured translation), banding
on numbers, dates and categories, multi-table rating, joins with exact port
roles, quote responses and batch outputs, Model Training and Model Scoring
setup, optimiser setup, adjacent edits around a submodel occurrence, a new
scenario on the Source Switch, Explore pivots, recovery of a seeded broken node,
read-only questions, delegated choices ("pick sensible bands"), multi-turn
refinement, multi-stage builds saved as several plans in one turn, focused
clarification, prompt injection, and refused requests to execute pipelines,
write externally or edit inside a submodel, including a turn that saves the
part of a request it may and refuses the rest. Cases and their projects are not
reachable through the assistant's tools, examples, recipes or prompt, and no
case id is a teaching example's name. A request names every node the case's
expectations name that does not exist yet (for example "a data input called
region_loadings"), so an expectation never depends on a name the analyst did
not give. Likewise a `node_configs` subset holds only what the request states,
never a value the engine supplies when the key is absent: `motor_online_optimiser`
expects no `step_column` on its expander, whose blank step column runs as
`scenario_index`.

## Reference trajectories

Every case has a checked-in reference trajectory in
`tests/assistant_eval/trajectories/`, a file named after the trajectory's id in
the closed trajectory shape version 1: `schema_version`, `id`, `case` and
`turns`, with one recorded turn per case turn. Each turn holds `rounds`; each
round holds the assistant `text` it streams and the tool `calls` it makes. A
call records its `id`, `tool`, `arguments` and `result`, where the result is
`{"status": "ok"}` or `{"status": "error", "error_code": ...}`. A round without
calls ends its turn. An argument written as `{"$result": "<call>.<key>"}` stands
for that key of an earlier call's result (a dotted key reads nested objects), so
plan hashes flow from one round into the next; a reference to a later or
unknown call fails loading.

Each step-corpus case has two trajectories: the taught `[source, free_code]`
form under the case's id, and the corpus's structured translation under
`<case id>_structured`. The `smoke_polars_feature_transform` trajectory first
writes `code` to a new Transform, which the stepped-node refusal rejects as
`invalid_ops`, and then authors the free-code form: removing that refusal
changes the recorded error, and the case diverges.

A case runs with the same session-stable system prompt and turn context the
message route builds, with no selection, so a trajectory that adds or edits one
primitive node makes its first dry-run without reading the graph first: the
graph brief already names each node's inputs and columns, and each step's id.
There are two exceptions. A recovery trajectory inspects the failing node
before it plans the fix, and then reads its config for the step code it
rewrites. And because `update_node` replaces each key it writes whole, a
trajectory that restates a saved non-empty list or map (rating factors, rating
tables, output mappings, a scenario map) first reads that node's config with
`inspect_node`'s config part, in the same turn, as a model must: an earlier
turn's tool results are compacted out of the history; the dry-run itself refuses
such a rewrite without that read as `config_unread`. The one exception is the
`metadata_only` list edit, whose blind rewrite the dry-run refuses as
`config_withheld` before the turn asks.
The `smoke_step_edit` trajectory inserts its step with `edit_steps` after the
saved free-code step without reading it, which the replay's execution golden
proves kept. Recipe and clarification trajectories keep the reads their
protocol names.

`TrajectoryProvider` replays a trajectory through the real loop. Before each
round it compares the results the loop returned with the recorded statuses and
error codes, and on the first difference stops sending and ends the turn. A
successful apply does not end the turn, so a trajectory that applies ends with
a round of closing text and no calls, and a multi-stage trajectory applies one
plan per stage in one turn. The `smoke_staged_pricing_build` trajectory works as
the system prompt asks of a request with several stages: it sets a build plan of
four items first, names each stage's item on that stage's apply and claims the
item complete once the apply has saved. `motor_loaded_premium_build` (the join
with its data input, the loaded-premium Transform and the rewired output) does the
same with three items. The case's expectations hold no plan
assertion, because a live model chooses its own item ids; the replay test asserts
the plan the trajectory leaves. The loop ends a turn without another round after a
save that fails verification, and a recorded call can be ignored there, so after
the case the harness also compares every executed call, in order, with the
recording. Any difference, a round the loop never asked for, or a round it
asked for that was never recorded raises `TrajectoryDivergedError` with
`trajectory <id> diverged at turn <t> round <r>` and the call, tool, observed
and recorded status. Divergence is never scored as an ordinary case failure: it
means the tools or contracts changed under the recording.

## Tiers

**Tier 0: offline replay, in CI.** `tests/test_assistant_replay.py` replays
every reference trajectory of both splits with `replay_self_test_case` through
the real loop, tools, dry-run, apply, parser and Git mutation gate, in a copy of
the case's project under the test's temporary directory and the case's egress
profile, and each replay must pass every correctness layer and any efficiency
limits within its timeout of 120 seconds per turn. Replays are independent and
run in parallel. No provider request is made. A replay starts with an empty
plan store, because copies of one project have identical content and so
identical plan hashes. The replay test also checks that no single-node
trajectory outside the recovery area reads before its first dry-run, other than
the config of a node whose saved list or map it restates; that every
restatement of a saved list or map follows that turn's config read of the node,
or is the `metadata_only` rewrite the dry-run refuses as `config_withheld`; and
that the first provider request's turn context lists the columns the feature
transform's first dry-run reads. Replay proves the tools, validators and
contracts; it cannot show that a prompt change helps a model.

**Tier 1: live runs, on demand.** `scripts/run_assistant_self_test.py` is the
live runner, run as `python -m scripts.run_assistant_self_test` from the
repository, with three commands:

- `record` runs the selected cases (all, or those named by repeated `--case`,
  `--area` or `--split`) against the configured provider, each in its own
  spawned process and its own disposable project copy, under the case's egress
  profile at the invoking project's provider trust rather than under the
  invoking project's own allowances, and writes the redacted report, by default
  to `.haute/assistant-eval/<run id>/report.json` in the invoking project. A
  selected case inapplicable to the run's variant is not run; the report lists
  it as not applicable. When no selected case applies to the variant, `record`
  refuses before it resolves the provider. With `--transcripts` it also writes each case's transcript, the
  requests, the assistant's text, every tool call with its arguments and
  result and every build-plan update, under `.haute/assistant-eval/<run id>/transcripts/` in the invoking
  project so a failure can be diagnosed. The runner refuses `--transcripts`
  unless Git ignores that directory.
- `compare` reads two reports of the same evidence kind and reports, per area,
  each report's case, pass and not-applicable counts; every case run in both
  that flipped between pass and fail, with the first failing layer on its
  failing side; the cases each report lists as not applicable, which are never
  a flip, a pass or a failure; the cases only one report holds, run or not
  applicable; and per area the median of each efficiency metric in both reports
  and their difference.
- `list` prints the selected cases' ids, areas, splits, projects, egress
  profiles and inapplicable variants without a provider call.

`record --variant <name>` runs a named configuration variant, recorded in the
report: `multi_apply` is the product's behaviour and the default, and
`one_apply_per_turn` ends a turn at its first saving apply, as the assistant did
before a turn could save several plans: the harness ends the turn in place of
the provider round that follows a successful `apply_graph_plan`, without calling
the model, so the variant changes nothing in the product. Comparing the two
reports measures whether several applies per turn help the configured model.
A case whose expectations the variant makes unreachable lists it in
`inapplicable_variants`: `motor_explore_then_run_blocked` (it saves and then
ends `blocked`, but the variant ends the turn at its save) and
`smoke_staged_pricing_build` (it saves stage by stage in one turn). Such a case
is never run under that variant (the harness refuses to), and the report and
`compare` show it as not applicable rather than as a failure.
`canonical_tools` sends the Databricks lane the canonical tool-schema projection
(the operation union with each branch's required fields, never strict) in place of
its default compatible projection: the harness rebuilds the case's Databricks
provider on the same client with the canonical projection, so the variant has no
product setting or `haute.toml` key. It measures the Databricks lane only, because
the Anthropic and OpenAI lanes already receive the canonical projection, so a run
under another provider is refused before any case runs. Comparing a
`canonical_tools` report with a `multi_apply` one measures which projection the
configured Databricks model builds better from.

The live runner is a measurement and diagnostic loop, not a gate. Model
qualification was a separate repeated-trial lane over held-out scenarios that
held that "a provider and model are `qualified` only when attributable live
results meet every threshold"; it never had a live runner. Its runnable
scenarios are now holdout cases (`breakpoint_age_banding`,
`schema_without_values` and `deploy_and_push_blocked`; its clarification and
injection scenarios repeated existing cases), its interruption and
stale-revision scenarios needed harness hooks the case format does not have, and
its thresholds are removed until a qualification record per area builds on the
holdout split and the per-area reports.

The closed support matrix `tests/assistant_eval/support_matrix.json` (version
2) lists the provider configurations reports are attributed to, each with an
`id`, `provider` and `model`. A live report names the configuration whose
provider and model match the run, or none when the configuration is not listed.

## Scoring layers

Correctness decides pass or fail. Each turn is scored in six layers, each
failure reason is reported as `<layer>: <reason>` under its turn, and a case
passes only when every layer of every turn passes. The case's first failing
layer is the first failing layer of its first failing turn.

1. **Protocol.** The turn completes; its typed outcome kind is the expected one,
   so an `incomplete` turn (the model stopped with a dry-run unfinished) or a
   `committed_unverified` one never passes; it saved changes exactly when the
   case expects it to, counted from the outcome's saved changes and never from
   the assistant's text, so a turn that saves and then ends `blocked` is scored
   as both a saved change and a blocked outcome; a turn that saves changed the
   graph and one that saves nothing left it unchanged; every saved plan emitted
   its own change card; no canary value leaked; and, for a case with
   `efficiency` limits, the round-trip, tool-call, failed-call and
   duplicate-static-read limits hold.
2. **Structure.** The required node types are present and the forbidden ones
   absent, every required edge exists, and when the case requires it the
   changed nodes and their neighbours form one connected component (a node is
   changed when it is new or retyped, or is an endpoint of an added or removed
   edge; an untouched node elsewhere in the project is never judged).
3. **Configuration.** Each node in `node_configs` exists and holds its subset:
   mappings match when every expected key matches recursively; lists match
   when they have the same length and each expected element matches the
   actual element at its position, recursively; and scalars match only when
   equal. A key a list's mapping element leaves out is therefore free, as it is
   in a mapping, so a case states only what its request states (an Explore
   pivot's value label is not mandatory when the request names none), while a
   list of another length or order never matches. The reason names the first
   differing path, such as `config.pivots[0].values[1].aggregation`, never a
   value. A key a case leaves out could still change behaviour, such as a
   breakpoint factor's `rightClosed`; the execution layer catches that, because
   every banding case that states breakpoints has a golden on its node and its
   data has a row on each stated boundary.
4. **Collateral change.** Every node existing before the turn and not named in
   `modified_nodes` still exists and keeps its configuration digest, the
   SHA-256 of the canonical JSON of its parsed configuration.
5. **Editor compatibility.** Every new node of a stepped type, and every node
   that was authored as steps before the turn, is authored as steps after it and
   carries neither `_steps_error` nor `_steps_discarded`, so it opens in the
   step builder. A pre-existing code-mode node is not judged: switching modes
   is the analyst's action.
6. **Execution.** Each golden node's executed output equals its golden.

Efficiency is measured, not judged, except in a case with `efficiency` limits.
Every case reports its provider round trips, tool calls, failed tool calls,
duplicate static reads, input and output tokens, time to first token, time to
the first validated plan and end-to-end latency, summed over its turns, and the
report gives each metric's median per area. A turn runs under the product's
tool-call budget, never a case's.

## Execution boundary

Execution happens in the harness, never in the assistant. The assistant has no
execution tool, and the harness executes nothing until the turn has ended.
It then parses the saved pipeline, flattens its submodel occurrences as a
preview does, and runs each golden node through the production preview engine
up to that node only, under the golden's scenario,
so no sink is built and no output is written. The engine refuses, without a
worker memory cap, a boundary it cannot estimate, such as a join with
`validate` or `maintain_order`; the preview worker runs such a join under its
cap, and the harness declares that cap for the few-row fixtures.

A golden is plain Polars code in the case, run in the project copy, that reads
the fixture's data files and binds `df`; it never uses Haute's step renderer,
executor or scorer, so the two computations are independent. A golden
downstream of a Model Scoring node loads the fixture's model file with the
model library itself to compute the prediction. The executed output must hold
exactly the golden's columns, matched by name in any order (the executed frame
is reordered to the golden's columns before comparing, since no request states
a column order), with their dtypes, null pattern and values (floats within
1e-6, NaN equal to NaN), and its rows in the golden's order unless the golden is
order-free. A golden
node whose output does not fit one full preview, or a golden that does not bind
a frame, is a broken case and raises. Response outputs are judged by their
configuration rather than executed. The step-corpus goldens are the corpus
snippets themselves.

## Evidence and reports

Replay evidence (tier 0) proves the tools, validators and contracts; live
evidence (tier 1) measures a model. Every result states its evidence, and a
replay result names the provider `replay` and its trajectory as the model. A
report holds exactly one evidence kind, and `compare` refuses two reports of
different kinds, so replay and live results are never combined or compared as
one score. A data check the assistant runs never replaces the harness's
independent execution goldens.

The report (shape version 4) records the run (its id, start time, variant,
provider, model, matched configuration and Haute version), whether every case
it ran passed, per area the counts of cases run, cases passed and cases not
applicable and the median of each efficiency metric over the cases run, and per
case run its identity, fixture version, area, split, egress profile, provider,
model, pass or fail per layer, first failing layer, the turn-and-layer-prefixed
reasons, the summed metrics, and per turn its outcome kind, saved-change count,
terminal, the node types and edges of the saved graph, ordered tool names with
value-free status, error code, validation path and validation reason, and the
turn's metrics. Its `not_applicable` list names, apart from the cases run, each
selected case inapplicable to the run's variant with its fixture version, area
and split; such a case has no result, so nothing reading the report counts it
as passed or failed, and no case is both run and not applicable. Prompts, model prose, tool arguments and results, credentials,
dataset values, canary values and content digests are never written to a
report.

A transcript is not a report. Transcripts exist only for live runs of these
synthetic fixtures, whose requests, schemas and values carry nothing of an
analyst's project, are written only on request, only under the invoking
project's Git-ignored `.haute/` directory and never into the repository, and are
never part of a report or of `compare`. This is the one exception to the rule
that model prose and tool payloads are not retained, and it holds because the
fixtures are synthetic.
