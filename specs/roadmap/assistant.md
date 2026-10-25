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

**Review packages.** `ASSIST-54` to `ASSIST-56` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md): the provider
client as the policy describes it, bounded requests with a terminal event on
every stream, and the core's hygiene.

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
