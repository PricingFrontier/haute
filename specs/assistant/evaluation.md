# Assistant Evaluation

## Purpose

The evaluation judges every assistant change against the tasks analysts ask
for, area by area. This document owns the fixture projects, the egress profiles
cases run under, the case format, the reference trajectories, the two
evaluation tiers, the scoring layers, the data findings layer and its
recovery metric, the efficiency metrics, the boundary
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
- `claims_join` joins a claims table onto ten quotes on `policy_id` with a
  left `m:1` Edge Join (`quote_claims`) and divides `total_incurred` by
  `premium` in a stepped `loss_ratio` Transform. Three quotes' policies have no
  claims row, so their `total_incurred` is null from the join on.
- `data_recovery` holds one Parquet Data Input, `quotes`: six motor quotes with
  `region` in lower case (`north`, `south`, `east`), `cover_type`, a
  `vehicle_group` code, a Boolean `has_prior_claim` and a `premium`. Its `data/`
  also holds two files no node reads yet, each a trap a natural plan falls into:
  `region_loadings` has one loading per region and cover type, so `region` alone
  repeats, and `vehicle_factors` keys its factors by `group_code` while its own
  `vehicle_group` describes the group. The seeded recovery cases (see Case
  format) run on it.
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
  node configuration, with project knowledge, executable source and aggregate
  statistics permitted and row samples not.
- `metadata_only`: `max_sensitivity = "internal"`, with project knowledge,
  executable source, row samples and aggregate statistics all withheld, so
  saved node configuration is withheld too. Two cases run under it: `motor_value_band_factor_withheld`,
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
shape version 5: `schema_version`, `id`, `fixture_version`, `project_fixture`,
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
- `data_findings`: `null` for a turn whose saved graph's data must be clean,
  or the closed `{reported, kept}` for a turn whose request invites an
  advisory finding, each a list of `{kind, node}` findings whose
  kind the data check can raise as advisory (`execution_failed`,
  `rows_emptied`, `banding_all_default`, `rating_misses`, `join_unmatched`,
  `join_validation_failed`, `join_fan_out` or `column_all_null`; informational
  findings need no action, so none is declared). `reported` names the findings
  a data check reports when the model makes the mistake the request invites,
  such as a recovery case's seeded bug, and is never empty; no check the model
  receives has to report them, because a model that avoids the mistake never
  meets them, and the seeded reference trajectories prove each one is raised.
  `kept` names the reported findings the saved graph keeps because correcting
  them would contradict a value the analyst stated, as the system prompt's rule
  on stated values requires; every other advisory finding must be gone from
  the saved graph. A finding named twice, a `kept` finding not `reported`, or a
  kept finding on a turn that saves nothing fails loading. The layer is scored
  as Scoring layers describes.

A turn that saves nothing declares no node configurations and no goldens.

The portfolio covers the areas above on the fixture projects: step edits and
new Polars logic on Transforms (including items from the Polars step corpus, in
their taught free-code form and the corpus's structured translation), banding
on numbers, dates and categories, multi-table rating, joins with exact port
roles, quote responses and batch outputs, Model Training and Model Scoring
setup, optimiser setup, adjacent edits around a submodel occurrence, a new
scenario on the Source Switch, Explore pivots, recovery of a seeded broken node,
recovery from a seeded data finding (below),
read-only questions (among them two answered from one `inspect_node` data call:
`claims_null_diagnosis`, why a column is null for some rows, and
`broken_bands_diagnosis`, why a node has no data when the node it reads fails),
delegated choices ("pick sensible bands"), multi-turn
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
`scenario_index`. A case's own values never conflict with its fixture's data by
accident: `smoke_rating_step` rates the driver ages its project holds (25 and
42), so its reference plan misses no row.

**Seeded recovery cases.** Five `recovery` cases on `data_recovery` measure
recovery from a pipeline that runs but is wrong. In each, the request leads to a
natural plan whose data check reports the seeded bug as an advisory finding,
and each declares that finding in `data_findings`. A model may also avoid the
mistake from the start (live runs banded the Boolean column on `true` and
`false`, filtered with `is_in`, and joined on `region` and `cover_type`); its
case then passes on its saved graph, and the recovery metric counts it as
avoided:

| Case | Split | Request | Seeded bug, as the check reports it | Expected recovery |
|---|---|---|---|---|
| `recovery_boolean_banding` | development | Band `has_prior_claim` into `claims_group`: claimant with a prior claim, clean without. | Categorical rules written `True` and `False` on a Boolean column, whose text is `true` and `false`: `banding_all_default` on `claims_band`. | Rules on `true` and `false`. |
| `recovery_emptied_filter` | development | A Transform keeping only the quotes in the north and south regions. | Both regions required of one row: `rows_emptied` on `north_south_quotes`. | A filter keeping either region. |
| `recovery_mistyped_join_key` | holdout | Left join the vehicle factors onto quotes by vehicle group, many-to-one. | A join on the same-named `vehicle_group`, which describes the group on the factors' side: `join_unmatched` on `vehicle_rated_quotes`. | The quotes' `vehicle_group` joined to the factors' `group_code`. |
| `recovery_rating_casing` | development | A rating table on region: North 1.2, South 0.95, East 1.05, default 1.0. | The stated keys miss the data's lower-case regions: `rating_misses` on `region_rating`. | The stated keys kept, the finding kept and shown on the change card, and the analyst told. |
| `recovery_duplicate_join_keys` | holdout | A left, many-to-one join of the region loadings, each quote taking its one loading. | A join on `region` alone, which repeats once per cover type: `join_validation_failed` on `loaded_quotes`. | A join on `region` and `cover_type`. |

The model corrects the plan within its turn and dry-runs again before it
applies, except where the correction would change a value the analyst stated:
there, as the system prompt's rule says, the value stays and the model tells
the analyst what the check found. The configuration layer then asserts the
stated rating keys, and the execution golden reproduces their misses.

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
There are two exceptions. The failing-node recovery trajectory
(`broken_vehicle_age_fix`) inspects the failing node before it plans the fix,
and then reads its config for the step code it rewrites. And because `update_node` replaces each key it writes whole, a
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

Each seeded recovery trajectory reads the descriptors of the node types it
adds, dry-runs the natural plan carrying the seeded bug, then, saying what the
check found, dry-runs the corrected plan and applies it. `recovery_rating_casing`
applies its one plan as the analyst stated it and closes by telling the
analyst that no quote's region matches the stated entries. Thread-mode replay
proves the tools accept both plans; the process-mode replay (see Tiers) proves
the check reports each bug.

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
transform's first dry-run reads. The two data-question trajectories
(`claims_null_diagnosis` and `broken_bands_diagnosis`) also replay in process
mode, with the preview workers started in the case's copy, and the test asserts
that their one `inspect_node` data call measured what their answers state: the
join where `total_incurred` first goes null and how many quotes it matches, and
one error at `rating_features`' failing step with `vehicle_bands`
`upstream_failed`. Their `efficiency` limits allow one tool call and two
provider round trips. The five seeded recovery trajectories replay in process
mode too, and each passes every layer, its data findings layer measured: its
first dry-run's check reports the seeded bug, which proves the declared
`reported` finding is what the trap raises, and the harness's check of the
saved graph finds it gone, or, for `recovery_rating_casing`, kept and shown on
the change card, so each is `recovered`. In thread mode every check reports `worker_mode_unsupported`,
so the data findings layer of every replay is not measured and fails nothing.
Replay proves the tools, validators and
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
  unless Git ignores that directory. One case's crash never ends the run: an
  exception the case raises comes back from its process as that case's crash
  result, its traceback carried as text so that it crosses the process boundary
  whatever the exception was, and a process that ends abruptly (a broken pool)
  is recorded as that case's crash too. A crashed case has no turns, fails every
  layer and keeps its transcript, and the next case runs in a fresh process.
- `compare` reads two reports of the same evidence kind and reports, per area,
  each report's case, pass, crash and not-applicable counts with its data
  findings counts and recovery metric; every case run in both
  that flipped between pass and fail, with the first failing layer on its
  failing side; every case run in both whose data findings flipped between
  passed and failed (a layer not measured in one report has not flipped); both
  runs' recovery metrics with the difference of their recovered-within-budget
  rates and of their avoided counts; the cases each report lists as not applicable, which are never
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

Correctness decides pass or fail. Each turn is scored in six correctness
layers, each failure reason is reported as `<layer>: <reason>` under its turn,
and a case passes only when every layer of every turn passes and, for a case in
the `recovery` area, every turn's data findings layer (below) does not fail.
The case's first failing layer is the first failing layer of its first
failing turn, `data_findings` when that turn fails only there.

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

### Data findings

The data findings layer is scored beside the correctness layers, never as one
of them, and never in place of the execution goldens: the goldens judge what
the saved pipeline computes, while this layer measures what the model was told
about its data and what the saved graph's data still shows. Per turn it
records, value-free:

- `received`: each data check the model received, in call order, from a
  dry-run's `data_check` and from `inspect_node`'s data part: the tool, the
  check's outcome (`checked`, `not_run` with its reason, or `omitted` when the
  result carried only the note that the check did not fit) and the kind and
  node of each advisory finding the model saw.
- `final`: the harness's own check of the saved graph after the turn (see
  Execution boundary) over the nodes the turn changed: each node that is new or
  retyped, whose configuration digest changed, or that is the target of an
  added or removed edge, less the nodes with no output frame (Quote Response,
  Data Output, Explore, Model Training and Optimisation) and submodel
  occurrences and ports. It records each such node's status (measured as
  `checked`, `failed` or `upstream_failed`, or not measured, with its
  `not_checked` or `not_run` reason) and the advisory findings on those nodes;
  it is null when the turn changed no such node.
- `cards`: for each change the turn saved, its change card's check outcome and
  the nodes of the advisory findings the card showed the analyst.

The layer judges the saved graph, never the trap. A turn passes it when the
saved graph's advisory findings on the nodes it changed are exactly its `kept`
findings (none when it declares nothing), so every other one is gone and no
stated value was changed to remove a kept one, and some change card showed the
analyst each kept finding; each part is judged only when a check it reads ran
(the harness's check of the saved graph, and a change card's check). What the
model received never passes or fails the layer: it only classifies the turn
for the recovery metric (below). This replaces the rule that a declaring turn
also failed when "no data check the model received reported" a `reported`
finding, which failed live runs whose model never made the seeded mistake.
A layer for which no check ran is `not_measured`, which neither passes nor
fails: thread-mode replay, where every check reports
`worker_mode_unsupported`, measures no layer, and a live check that could not
run (a busy worker, a node behind a model that is not cached locally) leaves
its part unmeasured. A case's layer is `failed` when any turn's failed,
`passed` when some turn's passed and none failed, and `not_measured` otherwise.

The layer fails a case only in the `recovery` area, where recovering is the
case's point: there a failed layer fails the turn and the case, with reasons
prefixed `data_findings:`. In every other area its result is reported and never
counted against the case. One ordinary case declares a finding:
`smoke_corpus_high_premium_quotes` keeps the corpus filter's stated threshold of
1500, which no quote in the corpus's inputs exceeds, so its `rows_emptied` is
kept rather than read as a failure to recover.

**Recovery metric.** A turn whose expectations say it saves is classified by
whether a data check it received reported an advisory finding and by its saved
graph. Its saved graph is clean when it completed with its expected outcome
kind, saved at least one change, and passes the data findings layer as above
(only the kept findings remain, each shown on a change card):

| Received an advisory finding | Turn | Class |
|---|---|---|
| yes | saved a clean graph | `recovered` |
| no | saved a clean graph | `avoided` |
| either | completed and saved, but the saved graph fails the layer | `not_recovered` |
| yes | ended with another outcome or saved nothing | `not_recovered` |
| no | ended with another outcome or saved nothing | outside the metric |
| either | saved, but the saved graph's check measured none of its changed nodes | outside the metric |

A turn whose four failed dry-runs per plan, or whose tool-call budget, are
spent ends `blocked` or failed, so reaching the expected outcome is what
"within budget" means; a turn that asked or blocked without meeting a finding
says nothing about recovery and fails its protocol layer instead. A case is
`not_recovered` when one of its turns is, else `recovered` when one is, else
`avoided` when one is, and outside the metric otherwise; it counts as having
received a finding when one of its turns in the metric did. Over a report's
cases (one run, so one model) and over each area's, the report gives the
number of cases that received a finding (`received`), that recovered and that
avoided one, and the recovered-within-budget rate, recovered over received,
null when no case received one. An avoided case never enters the rate. A kept
finding counts as told because the change card shows the analyst every
advisory finding of the plan it saved; the model's own words are not scored.

## Execution boundary

Independent execution-golden evaluation happens in the harness, never in the
assistant. The assistant has no model-callable execution tool, and the harness
executes nothing until the turn has ended. Under the `project` profile's
`allow_aggregate_statistics`, the [data check](high-level.md#data-checks) a
dry-run may run executes inside the assistant under test, never as the
harness's evidence: its findings are part of what the model sees, and they
never stand in for a golden; the same holds for the check `inspect_node`'s data
part runs over a saved node's lineage. A live case starts the interactive
preview workers inside its project copy, as the server does, so its checks run;
a transcript records each dry-run's `data_check` and each data part with the
rest of the tool result. Replay runs in thread mode, where a check reports
`worker_mode_unsupported`, which changes no recorded status; the two
data-question trajectories and the five seeded recovery trajectories are also
replayed in process mode (see Tiers).

After each turn the harness also checks the saved graph itself, for the data
findings layer: each node the turn changed is checked as `inspect_node`'s data
part checks one node, through the product's check (`run_node_data_check`) in a
preview worker, under a session of the harness's own so that it supersedes no
check of the turn. That check is the harness's evidence, beside the goldens and
never in their place, and its result never reaches the model. It runs under
every egress profile, `metadata_only` included, because no part of it is sent
to the provider: the permission that gates the product's check governs what
the model may receive, and the report holds only finding kinds, node names,
statuses and reasons.

To execute goldens, the harness then parses the saved pipeline, flattens its submodel occurrences as a
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
order-free. An exception raised while the harness reads or runs the saved
pipeline is what the turn saved, so it is that golden's execution failure, its
reason naming the exception's class and message, and never a crash: a turn that
ends `blocked` having saved no new scenario leaves a Source Switch that maps no
input to its golden's scenario, and running the golden raises
`LiveSwitchScenarioError`. A golden
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

The report (shape version 7) records the run (its id, start time, variant,
provider, model, matched configuration and Haute version), whether every case
it ran passed, the recovery metric for the run's model (`received`,
`recovered`, `avoided` and `rate`), per area the counts of cases run, cases
passed, cases crashed and cases not applicable, the counts of cases whose data
findings layer passed, failed and was not measured, the area's
recovery metric and the median of each efficiency metric over the
cases run that did not crash, and per
case run its identity, fixture version, area, split, egress profile, provider,
model, pass or fail per correctness layer, first failing layer, the turn-and-layer-prefixed
reasons, its crash (the traceback, or null), its data findings layer (status,
whether it gates the case, its class in the recovery metric and whether it
received an advisory finding there), the summed metrics, and per turn its outcome kind, saved-change count,
terminal, the node types and edges of the saved graph, ordered tool names with
value-free status, error code, validation path and validation reason, the
turn's metrics, and its data findings layer: status, whether it gates, its
reasons, the checks the model received, the saved graph's check, the change
cards' checks and its class in the recovery metric, each by finding kind, node
name, status and reason. Its `not_applicable` list names, apart from the cases run, each
selected case inapplicable to the run's variant with its fixture version, area
and split; such a case has no result, so nothing reading the report counts it
as passed or failed, and no case is both run and not applicable. Prompts, model prose, tool arguments and results, credentials,
dataset values, canary values and content digests are never written to a
report. A crash's traceback names the harness and Haute code the exception
passed through and the exception's own message, as an execution reason names
an exception's class and message.

A transcript is not a report. Transcripts exist only for live runs of these
synthetic fixtures, whose requests, schemas and values carry nothing of an
analyst's project, are written only on request, only under the invoking
project's Git-ignored `.haute/` directory and never into the repository, and are
never part of a report or of `compare`. This is the one exception to the rule
that model prose and tool payloads are not retained, and it holds because the
fixtures are synthetic.
