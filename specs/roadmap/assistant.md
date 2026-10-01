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
| ASSIST-41 | Planned | P1 | A dry-run reports advisory findings when changed nodes produce implausible data. |
| ASSIST-42 | Planned | P2 | One call answers why a saved node fails or why a column is null. |
| ASSIST-43 | Planned | P2 | Evaluation scores data findings as an extra layer and measures recovery from them. |
| ASSIST-44 | Deferred | P3 | Near-certain data bugs block apply unless explicitly accepted. |
| ASSIST-50 | Deferred | P3 | The model can write structured steps with formula text. |
| ASSIST-51 | Deferred | P3 | Banding and rating are authored in one closed, model-friendly form. |
| ASSIST-52 | Deferred | P3 | The model extends banding, rating, response and request nodes item by item. |
| ASSIST-53 | Deferred | P2 | Each served model has an attributable qualification record per area. |

## Planned improvements

### ASSIST-41 — Advisory data findings after dry-run
**Why:** A model that must choose to call a check tool often will not, and the
analyst never sees the data-level consequences of a plan.

**Plan:** When the permission allows, run the specified check automatically
after an eligible schema-tier dry-run, outside the save lock and the plan
hash, and attach advisory findings to the dry-run result and the change card:
rows in and out, null shares of new columns, per-rule banding counts, rating
misses and unused entries, join matches, and execution errors; ineligible
nodes report why they were not checked. The check implements the contract in
[the assistant specification's approved change contract](../assistant/high-level.md#approved-change-contract--data-checks)
and [its low-level counterpart](../assistant/low-level.md#approved-change-contract--data-checks);
landing it folds both into present-tense sections and removes them.

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

**Evidence:** `src/haute/assistant/_recipes.py::expand_recipe`;
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

**Evidence:** `src/haute/assistant/_ops.py::_apply_update_node`. On 2026-10-01,
under internal egress, 11 of 37 failing live case-runs came from retyping
withheld lists and 6 were saved as silent corruptions; the dry-run's
`config_withheld` guard (`_ops.py::_refuse_blind_rewrite`) now refuses them, and
under a readable policy a rewrite of a node the turn has not read is `config_unread`.

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
