# Assistant Evaluation

## Purpose

The evaluation judges every assistant change against the tasks analysts ask
for. This document owns the case format, the reference trajectories, the three
evaluation tiers, the scoring layers, the boundary between the assistant and
execution, and how evidence is reported. The assistant itself is specified in
[the assistant specification](high-level.md) and its harness modules in
[the low-level specification](low-level.md).

## Case format

Each case is one JSON file in `tests/assistant_eval/self_test/`, in the closed
case shape version 2: `schema_version`, `id`, `fixture_version`,
`project_fixture`, `category`, `request` and `expectations`. The project
fixture is a directory under `tests/assistant_eval/projects/` holding its
project configuration and pipeline file and no symbolic link. The category is
`semantic`, `clarification`, `prompt_injection` or `safety`. An unknown or
missing key at any level fails loading, and so does a required or forbidden
node-type name that is not a node type.

The expectations are closed and every key is required:

- `outcome`: `applied`, `clarified`, `blocked` or `unchanged`.
- `required_node_types` and `forbidden_node_types`.
- `required_edges`: source, target and target handle; a null handle matches an
  edge by its endpoints, and a named handle is an exact port assertion.
- `require_connected_graph`.
- `max_provider_round_trips`, `max_tool_calls`, `max_failed_tool_calls` and
  `max_duplicate_static_reads`.
- `forbidden_assistant_text`: canary values the assistant's text must never
  contain.
- `modified_nodes`: the pre-existing nodes the request may change.
- `node_configs`: for a node, the configuration subset it must hold after the
  turn.
- `execution`: goldens, each naming a `node`, its plain-Polars `golden` code and
  whether the comparison is `order_free`.

A case that does not expect `applied` declares no node configurations and no
goldens.

The portfolio covers specialist recipes, primitive graph edits, new Polars
logic on Transforms (including items from the Polars step corpus in the
two-input file project `polars_corpus`), mapped response outputs, join-port
semantics, graph authoring for file sources and sinks, focused clarification,
prompt injection, and blocked requests to execute pipelines or perform external
writes. Cases and their projects are held out: nothing in them is reachable
through the assistant's tools, examples, recipes or prompt.

## Reference trajectories

Every case has a checked-in reference trajectory in
`tests/assistant_eval/trajectories/`, a file named after the trajectory's id in
the closed trajectory shape version 1: `schema_version`, `id`, `case` and
`turns`. Each turn holds `rounds`; each round holds the assistant `text` it
streams and the tool `calls` it makes. A call records its `id`, `tool`,
`arguments` and `result`, where the result is `{"status": "ok"}` or
`{"status": "error", "error_code": ...}`. A round without calls ends its turn.
An argument written as `{"$result": "<call>.<key>"}` stands for that key of an
earlier call's result (a dotted key reads nested objects), so plan hashes flow
from one round into the next; a reference to a later or unknown call fails
loading. Self-test cases are single-turn, so a replayed trajectory records one
turn.

Each step-corpus case has two trajectories: the taught `[source, free_code]`
form under the case's id, and the corpus's structured translation under
`<case id>_structured`. The `smoke_polars_feature_transform` trajectory first
writes `code` to a new Transform, which the stepped-node refusal rejects as
`invalid_ops`, and then authors the free-code form: removing that refusal
changes the recorded error, and the case diverges.

A case runs with the same session-stable system prompt and turn context the
message route builds, with no selection, so a trajectory that adds or edits one
primitive node makes its first dry-run without reading the graph first: the
graph brief already names each node's inputs and columns. Recipe and
clarification trajectories keep the reads their protocol names.

`TrajectoryProvider` replays a trajectory through the real loop. Before each
round it compares the results the loop returned with the recorded statuses and
error codes, and on the first difference stops sending and ends the turn. The
loop ends a turn without another round after a successful apply, so after the
turn the harness also compares every executed call, in order, with the
recording. Any difference, a round the loop never asked for, or a round it
asked for that was never recorded raises `TrajectoryDivergedError` with
`trajectory <id> diverged at turn <t> round <r>` and the call, tool, observed
and recorded status. Divergence is never scored as an ordinary case failure: it
means the tools or contracts changed under the recording.

## Tiers

**Tier 0: offline replay, in CI.** `tests/test_assistant_replay.py` replays
every reference trajectory with `replay_self_test_case` through the real loop,
tools, dry-run, apply, parser and Git mutation gate, in a copy of the case's
project under the test's temporary directory, and each replay must pass every
scoring layer within its per-case timeout. No provider request is made. A
replay starts with an empty plan store, because copies of one project have
identical content and so identical plan hashes. The replay test also checks
that no single-node trajectory reads before its first dry-run and that the
first provider request's turn context lists the columns that dry-run reads.
Replay proves the tools, validators and contracts; it cannot show that a prompt
change helps a model.

**Tier 1: live self-test, on demand.** `scripts/run_assistant_self_test.py`
runs selected cases against the configured provider, each in its own spawned
process and its own disposable project copy, under the harness's egress
allowances rather than the invoking project's. It scores the same layers as
replay and is a fast diagnostic and regression loop for a model, not
qualification.

**Tier 2: qualification.** Model qualification is a versioned, repeatable
lane, never part of deterministic unit tests, run by
`scripts/run_assistant_evaluation.py` over the held-out scenarios in
`tests/assistant_eval/held_out/`. Held-out scenarios contain ordinary project
artifacts, requests, semantic assertions and adversarial perturbations; their
requests and expected operations are not discoverable through assistant tools,
examples, recipes or the permanent prompt.

Each trial records the Haute version, capability hash, system-prompt hash,
provider, pinned model and version, provider parameters, fixture version, run
ID, cold or warm state, semantic and safety outcomes, provider and tool round
trips, input and output tokens, estimated cost, time to first token, time to a
validated plan, and end-to-end latency. Scoring compares graph semantics,
postconditions, unrelated diffs, clarification and recovery decisions,
authority and leakage outcomes; it does not require exact prose or tool order.

The closed support matrix `tests/assistant_eval/support_matrix.json` defines
repeated-trial counts plus per-task semantic, tool-call, token and cost, and
cold and warm p50 and p95 limits. Unauthorized mutation and sensitive or secret
leakage are zero tolerance and are never averaged into an overall score. A
provider and model are `qualified` only when attributable live results meet
every threshold; absent credentials or candidate-only evidence leave them
unqualified rather than silently skipping the gate.

## Scoring layers

Tiers 0 and 1 score every case in six layers. Each failure reason is reported
as `<layer>: <reason>`, and a case passes only when every layer passes.

1. **Protocol.** The turn completes; its outcome is the expected one, where the
   last explicit `NEEDS_INPUT:` or `BLOCKED:` marker in the accumulated
   assistant text decides a non-mutation outcome; an applied outcome applied a
   plan, emitted a graph update and changed the graph, and any other outcome
   changed nothing; no canary value leaked; and the round-trip, tool-call,
   failed-call and duplicate-static-read limits hold.
2. **Structure.** The required node types are present and the forbidden ones
   absent, every required edge exists, and when the case requires it the
   changed nodes and their neighbours form one connected component (a node is
   changed when it is new or retyped, or is an endpoint of an added or removed
   edge; an untouched node elsewhere in the project is never judged).
3. **Configuration.** Each node in `node_configs` exists and holds its subset:
   mappings match when every expected key matches recursively, and lists and
   scalars match only when equal. The reason names the first differing path,
   never a value.
4. **Collateral change.** Every pre-existing node not named in
   `modified_nodes` still exists and keeps its configuration digest, the
   SHA-256 of the canonical JSON of its parsed configuration.
5. **Editor compatibility.** Every new node of a stepped type, and every node
   that was authored as steps before the turn, is authored as steps after it and
   carries neither `_steps_error` nor `_steps_discarded`, so it opens in the
   step builder. A pre-existing code-mode node is not judged: switching modes
   is the analyst's action.
6. **Execution.** Each golden node's executed output equals its golden.

## Execution boundary

Execution happens in the harness, never in the assistant. The assistant has no
execution tool, and the harness executes nothing until the turn has ended.
It then parses the saved pipeline and runs each golden node through the
production preview engine up to that node only, so no sink is built and no
output is written. The engine refuses, without a worker memory cap, a boundary
it cannot estimate, such as a join with `validate` or `maintain_order`; the
preview worker runs such a join under its cap, and the harness declares that
cap for the few-row fixtures.

A golden is plain Polars code in the case, run in the project copy, that reads
the fixture's data files and binds `df`; it never uses Haute's step renderer
or executor, so the two computations are independent. The executed output
must match the golden's column names and dtypes, null pattern and values
(floats within 1e-6, NaN equal to NaN), and row order unless the golden is
order-free. A golden node whose output does not fit one full preview, or a
golden that does not bind a frame, is a broken case and raises. Response
outputs are judged by their configuration rather than executed. The step-corpus
goldens are the corpus snippets themselves, and `polars_corpus`'s data files
are the corpus's normal synthetic inputs from
`tests/assistant_eval/_frames.py`.

## Evidence and reports

Replay evidence (tier 0) proves the tools, validators and contracts; live
evidence (tiers 1 and 2) measures a model. Every self-test result states its
evidence, and a replay result names the provider `replay` and its trajectory as
the model. A self-test report holds exactly one evidence kind, so replay and
live results are never combined or compared as one score. A data check the
assistant runs never replaces the harness's independent execution goldens.

The self-test report (shape version 2) records, per case, its identity,
fixture version, category, outcome, terminal, pass or fail per layer, the
layer-prefixed reasons, the node types and edges of the saved graph, ordered
tool names with value-free status, error code, validation path and validation
reason, and aggregate metrics. Prompts, model prose, tool arguments and
results, credentials, dataset values, canary values and content digests are
never written to a report. The qualification lane writes its own trial records
and aggregate report under the same redaction.
