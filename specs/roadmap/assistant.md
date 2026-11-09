# Assistant roadmap

## Scope

The in-app assistant that authors pipeline graphs from a chat panel: the
backend agent loop, tools, capability catalogue, recipes, examples and
provider adapters in `src/haute/assistant/`, its HTTP routes, the chat panel,
and its evaluation harness. Current behaviour is specified in
[the assistant specification](../assistant/high-level.md) and its
[low-level specification](../assistant/low-level.md), and the chat panel in
[the assistant UI specification](../frontend-assistant-ui/high-level.md).

The remaining authoring upgrades are gated by evaluation: blocking advisory
findings, structured steps with formula text, typed banding and rating,
item-level edits, and qualification per model. They build on the current
free-code-card authoring path, node cards, data checks, egress controls and
evaluation harness described in the component specifications. The packages
below state the evidence needed to reopen each deferred capability.

**Out of scope.** Converting code-mode nodes back into structured steps,
editing inside submodels or wiring submodel occurrences, remote table
discovery, and an OpenAI Responses API migration: none has evidence tying it
to the build journeys the evaluation targets first.

**Review packages.** `ASSIST-54` to `ASSIST-56` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md): the provider
client as the policy describes it, bounded requests with a terminal event on
every stream, and the core's hygiene; `ASSIST-57` to `ASSIST-59`, from the same
review's pass over the tools, ops, data checks, catalog, recipes and assets:
tools that never stall the server, facts the model can trust, and the tools'
hygiene; `ASSIST-60` from its pass over the chat panel.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ASSIST-44 | Deferred | P3 | Near-certain data bugs block apply unless explicitly accepted. |
| ASSIST-50 | Deferred | P3 | The model can write structured steps with formula text. |
| ASSIST-51 | Deferred | P3 | Banding and rating are authored in one closed, model-friendly form. |
| ASSIST-52 | Deferred | P3 | The model extends banding, rating, response and request nodes item by item. |
| ASSIST-53 | Deferred | P2 | Each served model has an attributable qualification record per area. |
| ASSIST-59 | Planned | P3 | A re-validated plan keeps a full TTL, the assistant imports the one canonical JSON encoder and one freeze helper, and its limits are declared once. |
| ASSIST-60 | Planned | P3 | The chat panel recognises the server's "still finishing" answer by a code, shows the server's detail for every other failure, never throws from a click, and keeps a draft the store refused to send. |
| ASSIST-62 | Planned | P2 | Session/knowledge caches write atomically without fixed `.json.tmp` collisions, and `_graph_fact` handles unparseable pipelines cleanly. |

## Planned improvements

### ASSIST-44 — Blocking findings with explicit acceptance
**Why:** Advisory findings can be ignored. The package is deferred because
empty frames, all-default bands and unmatched joins are sometimes intended,
and whether blocking helps must be measured first.

**Plan:** If evaluation shows models ignoring near-certain findings, have
apply refuse an unaccepted blocking finding and record accepted findings on
the change card, identified by finding instance.

**Acceptance:** Seeded cases are refused without acceptance and land with it;
a partial join never blocks.

**Dependencies:** Evaluation evidence that models ignore near-certain advisory findings.

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

**Dependencies:** Evaluation evidence that structured authoring improves on the current free-code path.

**Evidence:** `frontend/src/panels/editors/polarsSteps/formula.ts`;
`src/haute/_polars_steps.py::render_polars_steps`.

### ASSIST-51 — Typed banding and rating authoring
**Why:** Banding rules and rating tables are the most error-prone
configurations; the package is deferred until the existing node cards
are shown not to be enough.

**Plan:** Author banding factors with string boundaries (number, date or
open-ended) and string categorical values checked against the column's text
form, and rating tables as positional rows, lowered to canonical form; add a
banding-to-rating chain whose rating levels come from the band labels; replace
the single-node recipes, rewriting the recipes section of the specification.

**Acceptance:** The banding-to-rating replay case passes; shape errors in the
evaluation fall.

**Dependencies:** Evaluation evidence of shape errors with the existing node cards.

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

**Dependencies:** Evaluation evidence that whole-list edits remain inadequate under the current egress and read-before-write guards.

**Evidence:** `src/haute/assistant/_ops.py::_apply_update_node`. On 2026-10-01,
under internal egress, 11 of 37 failing live case-runs came from retyping
withheld lists and 6 were saved as silent corruptions; the dry-run's
`config_withheld` guard (`_ops.py::_refuse_blind_rewrite`) now refuses them, and
under a readable policy a rewrite of a node the turn has not read is `config_unread`.

### ASSIST-53 — Qualification per model
**Why:** The support matrix lists configurations but no qualification verdicts.
Deferred until the configured endpoints and holdout case portfolio support
repeatable live evaluation.

**Plan:** Run the holdout split three times for the configured Databricks
models, and for Anthropic and OpenAI models when a project provides keys,
with per-area thresholds and prices in the support matrix and infrastructure
failures excluded.

**Acceptance:** The support matrix carries a qualified or unqualified verdict
per configuration with run identifiers.

**Dependencies:** Reachable configured endpoints and a representative holdout case portfolio.

**Evidence:** `tests/assistant_eval/support_matrix.json`.

### ASSIST-59 — Tool hygiene
**Why:** A repeated identical dry run keeps the first dry run's expiry:
`PlanStore.put` on a still-`validated` record replaces the receipt but never
refreshes `expires_at`, so a plan re-validated late in its ten minutes is
reported valid with a hash that expires seconds later and the apply answers
`plan_expired`. `_ops._canonical_json` is a second canonical JSON encoder beside
`haute._cache.canonical_json`, whose docstring forbids a second one, and three
recursive freeze helpers exist with two thaw helpers. Every operation descriptor
declares the same hand-written limits as literals duplicating the tool module's
constants, and the 30-second timeout they state is true of the data part alone.

**Plan:** Refresh `expires_at` on a re-put of a `validated` plan with a test;
import the one encoder and move one freeze/thaw pair into `_wire_ops.py`;
declare the limits once and state the timeout per operation or drop it.

**Acceptance:** A plan re-validated at `ttl - ε` stays applicable for a full
TTL; one encoder, one freeze pair; the limits have one definition.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_ops.py::PlanStore`;
`src/haute/assistant/_ops.py::_canonical_json`; `src/haute/_cache.py::canonical_json`;
`src/haute/assistant/_catalog.py::_freeze`; `src/haute/assistant/_wire_ops.py`.

### ASSIST-60 — The chat panel's failures and clicks
**Why:** The send-time 409 notice is chosen by comparing `error.detail` with the
server's English sentence ("An assistant turn is already running"), and every
other HTTP failure (500, 403, 422, 503) is rendered as "Turn interrupted" with
the server's detail discarded. Two assertions throw from click paths (a
checklist link whose card is missing; Undo on a card outside an open chat),
which React does not route to the error boundary, so the click silently does
nothing. The composer clears the draft before the store re-checks the send gate,
so a send refused at the gate loses the typed text.

**Plan:** A stable code on the server's 409 and a match on it; render other
failures as `failed` with the detail; disable the link when the card is absent
and set a notice instead of throwing; clear the draft only after a successful
send.

**Acceptance:** A 503 shows the server's detail; a reworded 409 sentence still
shows the still-finishing notice; a refused send keeps the draft.

**Dependencies:** None.

**Evidence:** `frontend/src/stores/useAssistantStore.ts`;
`src/haute/routes/assistant.py`; `frontend/src/panels/assistant/BuildChecklist.tsx`;
`frontend/src/panels/assistant/Composer.tsx`.

### ASSIST-62 — Project-root workbench form resolution, atomic cache/session writes, and resilient `_graph_fact` parsing
**Why:** Two assistant persistence defects affect session/knowledge cache writes and
project-knowledge indexing:
1. `_write_cache` in `_project_knowledge.py` and `SessionStore._persist` in
   `_session.py` write to a fixed sibling temporary file (`path.with_suffix(".json.tmp")`)
   and call `os.replace(temp_path, path)` instead of using `atomic_write_text`
   (`src/haute/_file_ops.py`). Concurrent writes to the same session or project-knowledge
   index race on the shared `.json.tmp` path (raising `FileNotFoundError` or
   `PermissionError` on Windows during `os.replace`) and leave orphaned `.json.tmp`
   files on failure.
2. `_graph_fact` in `_project_knowledge.py` calls `parse_pipeline_to_graph(source)`
   without catching `HauteError` or `SyntaxError` (unlike `_read_text_file` and
   `_read_cached_index`, which wrap errors in `ProjectKnowledgeError`), so a syntax or
   parse error in a pipeline file crashes `build_project_knowledge` with an unhandled
   parse exception instead of raising a typed `ProjectKnowledgeError`.

**Plan:** Replace the manual `.json.tmp` writes in
`_write_cache` and `SessionStore._persist` with `atomic_write_text`; and catch
parse failures in `_graph_fact` to raise `ProjectKnowledgeError` with the pipeline
path and cause.

**Acceptance:** Concurrent `_write_cache` and
`SessionStore._persist` calls use unique temporary files via `atomic_write_text` and
leave no `.json.tmp` files behind; and `build_project_knowledge` on an unparseable
pipeline file raises `ProjectKnowledgeError`.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_application.py::_PreparedGraph.build`;
`src/haute/assistant/_data_check.py::_bound`;
`src/haute/assistant/_data_check.py::data_check_visibility`;
`src/haute/assistant/_tools.py::_prepare_column_profile`;
`src/haute/assistant/_project_knowledge.py::_write_cache`;
`src/haute/assistant/_project_knowledge.py::_graph_fact`;
`src/haute/assistant/_session.py::SessionStore._persist`.
