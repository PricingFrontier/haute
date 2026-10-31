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
| ASSIST-54 | Planned | P2 | The provider client uses the validated endpoint, classifies SDK errors by type, builds its request outside the failure catch-all, and logs provider errors redacted. |
| ASSIST-55 | Planned | P2 | A message is bounded, the turn deadline ends every stream with a terminal event, the chat list is read off the event loop, and an oversized document is counted, not dropped. |
| ASSIST-56 | Planned | P3 | The assistant core carries no test-only methods, one one-line helper, one readiness resolution per message, and names that mean one thing. |
| ASSIST-57 | Planned | P2 | A tool never stalls the server: `inspect_node` profiles outside the save lock, the capability manifest is built once per installed set, an example is loaded once, and the egress policy is resolved once per call. |
| ASSIST-58 | Planned | P2 | What the model is told is true: a join's execution failure is kept unless the validation raised it, the brief's columns follow the data, Load File's summary names the three file types, and no rule is stated that the product does not enforce. |
| ASSIST-59 | Planned | P3 | A re-validated plan keeps a full TTL, the assistant imports the one canonical JSON encoder and one freeze helper, and its limits are declared once. |
| ASSIST-60 | Planned | P3 | The chat panel recognises the server's "still finishing" answer by a code, shows the server's detail for every other failure, never throws from a click, and keeps a draft the store refused to send. |
| ASSIST-61 | Planned | P1 | Applying a verified plan never adopts a newer source revision as permission to overwrite an intervening external edit. |
| ASSIST-62 | Planned | P2 | Assistant dry-run flattening, data checks, visibility checks and column profiling resolve workbench form tables from the configured project root, session/knowledge caches write atomically without fixed `.json.tmp` collisions, and `_graph_fact` handles unparseable pipelines cleanly. |

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

### ASSIST-54 — The provider client as the policy describes it
**Why:** The endpoint trust validation and the readiness `endpoint_host`
describe `api.anthropic.com` and `api.openai.com`, but the SDK clients are built
without an explicit `base_url`, so `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` in
the process environment, which `haute serve` fills from the project `.env`,
silently redirects the key, the prompt and project material to an endpoint the
policy never checked (the installed SDKs read the variable whenever no
`base_url` is passed); no code or test accounts for the override. Failures are
classified by substrings of the SDK exception class name, so a wrong model name
(404) or an overloaded endpoint (529) reads as a "stream failure", and the
installed-SDK test parametrises only the classes that classify as intended. The
Anthropic adapter builds its request inside the `try` whose catch-all classifies
every exception as a provider failure, so a programming error in the history
translation is shown as a provider failure, while the OpenAI adapter builds
outside it. The raw SDK error string, which embeds the HTTP response body, is
logged unredacted.

**Plan:** Pass the validated endpoint explicitly to both constructors (or
validate the environment variables like `[assistant].base_url` and report the
real host); classify by `isinstance` against the SDKs' status and connection
hierarchies with `stream` reserved for mid-stream failures, and extend the
installed-SDK test to every status class; build the request before the `try`;
log the error through `redact_sensitive_text` with the key as a known secret.

**Acceptance:** With `ANTHROPIC_BASE_URL` set in the environment the constructed
client's `base_url` is the validated endpoint (or readiness reports the
variable's host after validation); a `NotFoundError` is reported as a status
failure; a translation error reaches the internal-error path; no log line
carries the response body unredacted.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_providers.py::AnthropicProvider`;
`src/haute/assistant/_providers.py::OpenAIProvider`;
`src/haute/assistant/_providers.py::_failure_category`;
`src/haute/assistant/_providers.py::_classify_sdk_error`;
`src/haute/assistant/_config.py::_endpoint`; `tests/test_assistant_providers.py`.

### ASSIST-55 — Bounded requests and a terminal event on every stream
**Why:** `AssistantMessageRequest.message` has no length bounds and the server no
body limit, so an empty message starts a turn that fails only after the stream
opens and an arbitrarily large one is sent to the provider in every round and
persisted on every append. The turn's wall clock is an `asyncio.timeout` around
an async generator that yields to the response task, so a deadline that fires
while the generator is suspended at a yield cancels the consumer, not the turn:
no `failed` event is produced and the client sees a closed connection, against
the one-terminal-event invariant (the ordinary path is covered by explicit
deadline checks). Listing a pipeline's chats reads, parses and revalidates every
persisted session file synchronously on the event-loop thread, blocking every
other request while the chat list opens; a test pins the loop-thread call. A
documentation file over 64 KB, and the 51st document, are dropped from project
knowledge silently while an undecodable one fails loudly.

**Plan:** `min_length` and `max_length` on `message` and a `max_length` on
`session_id`; replace the outer timeout with per-step `asyncio.timeout_at`
on each awaited step and convert its error to the existing turn-limit path, with
a test whose consumer sleeps past the deadline between events; read and parse
the session files on a worker thread (or keep a small summary beside each file)
and merge on the loop; count omitted documents in the view and the tool result.

**Acceptance:** An empty or over-long message is answered 422; a deadline during
a slow consumer ends the stream with a `failed` event; the chat list blocks the
loop for no file read; the project-knowledge result reports the omitted count.

**Dependencies:** None.

**Evidence:** `src/haute/schemas.py::AssistantMessageRequest`;
`src/haute/assistant/_loop.py::run_turn`; `src/haute/routes/assistant.py`;
`src/haute/assistant/_session.py::list_sessions`;
`src/haute/assistant/_project_knowledge.py`.

### ASSIST-56 — Core hygiene
**Why:** `AssistantTurn.as_dict`, `AssistantSession.as_dict` and
`current_turn_deadline` have no production caller while the specification names
them; `_provider_tools` is a no-op left from the retired lexical router and its
tests assert its signature; collapsing text to one bounded line is written five
times with three rules, and the two copies in `_loop.py` disagree on newlines;
every message send resolves `haute.toml` twice and runs a `git` subprocess whose
answer decides nothing on that path; `AssistantProvider` names both a `Literal`
of provider ids and the adapter `Protocol`; the project-knowledge "cache" is
write-only yet reports `cache_hit`.

**Plan:** Delete the test-only methods and the no-op; one `one_line(text,
limit)` helper; one config resolution per message; rename the `Literal`; use
the index or rename the field.

**Acceptance:** The named symbols are gone; one helper collapses text; a message
send runs no `git`; the field name says what the code does.

**Dependencies:** `ENGQ-R01` owns dead code outside this component.

**Evidence:** `src/haute/assistant/_session.py::as_dict`;
`src/haute/assistant/_providers.py::current_turn_deadline`;
`src/haute/assistant/_loop.py::_provider_tools`;
`src/haute/assistant/_loop.py::_bounded`; `src/haute/routes/assistant.py`;
`src/haute/assistant/_config.py`;
`src/haute/assistant/_project_knowledge.py::_write_cache`.

### ASSIST-57 — Tools that never stall the server
**Why:** `inspect_node` holds the process-wide, cross-process `save_lock` while
its profile part collects up to a million rows in the preview worker, bounded
only by the pipeline time limit (30 minutes by default), so every GUI save,
Files-panel write, submodel operation, assistant read, dry run, apply and undo,
and any other server process on the project, blocks for the profile's duration;
only the parse needs the lock, the data part already collects outside it, and a
wiring test pins the lock as held. `capability_manifest()` rebuilds every node
and operation descriptor, the operation union, the canonical JSON and the
SHA-256 on every call and only then consults a cache keyed by the digest it just
computed (probed: about 47 ms per warm call), paid at least once per tool result
on the event-loop thread, per project revision, per `read_reference` id and per
system prompt. Every `read_reference` of an example re-hashes and re-copies the
whole bundle into a fresh temporary directory. The egress policy is resolved
from `haute.toml` at eight sites, several within one call.

**Plan:** Prepare the profile under the lock and collect outside it, amending
the specification's exemption and replacing the lock-held test with one that
asserts the lock is free during collection; key the manifest cache on
`(haute_version, digest of the installed capabilities)` and build on a miss
only, with a test that a warm call constructs no descriptor; cache
`load_example` per name; resolve the policy once in `_dispatch_tool` and thread
it down.

**Acceptance:** A save during a profile waits for no collection; a warm
`capability_manifest()` call is under a millisecond; an example read twice
hashes once; one policy resolution per tool call.

**Dependencies:** `PCFG-R04` owns the current-directory root the policy is
resolved from.

**Evidence:** `src/haute/assistant/_tools.py::inspect_node`;
`src/haute/assistant/_tools.py::_prepare_column_profile`;
`src/haute/assistant/_catalog.py::capability_manifest`;
`src/haute/assistant/_assets.py::load_example`;
`src/haute/assistant/_tools.py::_dispatch_tool`; `tests/test_assistant_tools.py`.

### ASSIST-58 — Facts the model can trust
**Why:** A data check suppresses an Edge Join's `execution_failed` finding
whenever its validated side holds duplicate keys, whatever the join raised, so a
join that fails for another reason (key dtypes that differ between the sides;
Polars raises `SchemaError`, distinguishable from the `ComputeError` a
`validate` breach raises) reports only `join_validation_failed`, and the model
fixes the duplicates while the join still fails. The graph brief's resolved
columns are cached per project revision, which covers the pipeline file,
`haute.toml` and the ledger but not the data files, so a column added to a
parquet between turns leaves the turn context showing the old columns while
`inspect_node`'s schema part shows the new ones. The prompt's node index tells
the model Load File loads "a pickle, JSON, joblib or CatBoost file" while the
product accepts three types and the same node's usage note says so. The Source
Switch note states "use at most one switch per pipeline" as a rule the product
does not enforce. The low-level module map still describes a request-text router
in `_recipes.py` that the high-level specification says does not exist.

**Plan:** Replace the execution finding only when the failure is the
`ComputeError` the validation raises; key the brief cache on the revision plus
the lineage's input identity (or a short TTL); derive the Load File summary from
`EXTERNAL_FILE_TYPES` and add the summaries to the usage-note test; reword the
switch note as guidance; delete the module-map sentence.

**Acceptance:** A join with duplicates on its join side and mismatched key
dtypes reports both findings; a column added to a data file appears in the next
turn's brief; the summary and the loader agree on the file types.

**Dependencies:** None.

**Evidence:** `src/haute/assistant/_data_check.py::_replaced`;
`src/haute/assistant/_tools.py::_brief_nodes`;
`src/haute/assistant/_catalog.py::_SUMMARIES`;
`src/haute/assistant/_catalog.py::_USAGE_NOTES`; `src/haute/_io.py::EXTERNAL_FILE_TYPES`;
`specs/assistant/low-level.md`.

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

### ASSIST-61 — Keep the authenticated revision through commit
**Why:** The [28 October review](codebase-review-2026-10-28.md) reproduced an
external edit lost during assistant apply. `_prepare_apply` authenticates the
saved snapshot and derives the result graph; `_commit` then rereads
`_document_revision` and passes that newer revision to the save service. An IDE
save between those operations is therefore treated as the authorised baseline
for the older graph. In the probe, a rename succeeds and its post-commit
verification passes while an intervening change to the pipeline description
disappears. The process's `save_lock` cannot exclude an external editor.

**Plan:** Bind the editor-protocol save revision to the exact source snapshot
authenticated by preparation and carry it through commit. Establish and verify
that binding around the reads that build the snapshot; merely moving the fresh
revision read to another unguarded point is insufficient. Use the save service's
existing conflict precondition and stale-plan failure. Update the assistant
transaction contract before implementation.

**Acceptance:** Insert an external source edit after freshness verification
and before transactional save: apply refuses as stale/conflicted, preserves the
external bytes, and reports no successful application. Also cover an edit during
snapshot/revision acquisition so a mixed snapshot cannot be authorised. An
unchanged plan applies once and a second apply is still refused. Use real
temporary project files and the real save service with a deterministic barrier.

**Dependencies:** None; reuse the save service's existing revision checks.

**Evidence:** `src/haute/assistant/_application.py::PipelineApplicationService`;
`src/haute/routes/_save_pipeline.py`; `tests/test_assistant_application.py`.

### ASSIST-62 — Project-root workbench form resolution, atomic cache/session writes, and resilient `_graph_fact` parsing
**Why:** Three assistant persistence and project-context defects affect workbench form
resolution, session/knowledge cache writes, and project-knowledge indexing:
1. `_PreparedGraph.build`, `_bound`, `data_check_visibility` and
   `_prepare_column_profile` pass `project_root=Path.cwd()` to
   `flatten_with_form_tables` even when the caller or `PipelineApplicationService`
   was constructed with an explicit `project_root`. When the process working
   directory differs from `project_root` (in hosted runners, tests, or CLI invocations
   outside the repository root), workbench form tables are resolved against the wrong
   directory or fail with `FileNotFoundError`.
2. `_write_cache` in `_project_knowledge.py` and `SessionStore._persist` in
   `_session.py` write to a fixed sibling temporary file (`path.with_suffix(".json.tmp")`)
   and call `os.replace(temp_path, path)` instead of using `atomic_write_text`
   (`src/haute/_file_ops.py`). Concurrent writes to the same session or project-knowledge
   index race on the shared `.json.tmp` path (raising `FileNotFoundError` or
   `PermissionError` on Windows during `os.replace`) and leave orphaned `.json.tmp`
   files on failure.
3. `_graph_fact` in `_project_knowledge.py` calls `parse_pipeline_to_graph(source)`
   without catching `HauteError` or `SyntaxError` (unlike `_read_text_file` and
   `_read_cached_index`, which wrap errors in `ProjectKnowledgeError`), so a syntax or
   parse error in a pipeline file crashes `build_project_knowledge` with an unhandled
   parse exception instead of raising a typed `ProjectKnowledgeError`.

**Plan:** Thread the service's or caller's resolved `project_root` through
`_PreparedGraph.build`, `run_data_checks` / `_bound`, `data_check_visibility` and
`_prepare_column_profile` into `flatten_with_form_tables(graph, project_root=...)`
and `runtime_project_root(project_root)`; replace the manual `.json.tmp` writes in
`_write_cache` and `SessionStore._persist` with `atomic_write_text`; and catch
parse failures in `_graph_fact` to raise `ProjectKnowledgeError` with the pipeline
path and cause.

**Acceptance:** On a workbench pipeline whose `haute.toml` and form file live in a
project directory distinct from `Path.cwd()`, assistant dry-run verification,
data checks, visibility checks and `inspect_node` column profiling resolve the
project's form tables without reading `Path.cwd()`; concurrent `_write_cache` and
`SessionStore._persist` calls use unique temporary files via `atomic_write_text` and
leave no `.json.tmp` files behind; and `build_project_knowledge` on an unparseable
pipeline file raises `ProjectKnowledgeError`.

**Dependencies:** Coordinates with `PCFG-R04` (one project context).

**Evidence:** `src/haute/assistant/_application.py::_PreparedGraph.build`;
`src/haute/assistant/_data_check.py::_bound`;
`src/haute/assistant/_data_check.py::data_check_visibility`;
`src/haute/assistant/_tools.py::_prepare_column_profile`;
`src/haute/assistant/_project_knowledge.py::_write_cache`;
`src/haute/assistant/_project_knowledge.py::_graph_fact`;
`src/haute/assistant/_session.py::SessionStore._persist`.
