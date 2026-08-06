# Assistant — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/assistant/__init__.py` | Public package seam; re-exports only `assistant_readiness`. The FastAPI router remains in the routes package and is not re-exported here. |
| `src/haute/assistant/_config.py` | Resolves assistant configuration: the outer `[assistant]` table is closed to provider/model/base URL/egress and the required nested `[assistant.egress]` table is closed to trust, maximum sensitivity, and the three `allow_*` booleans. It validates endpoint/trust combinations and credential-free OpenAI URLs before SDK/key probing. The first-class Databricks mode rejects `base_url`, reads DATABRICKS_HOST / DATABRICKS_TOKEN, validates a credential-free HTTPS workspace-root host, and derives `<host>/serving-endpoints`. Other credentials come from their named environment variables. It produces `AssistantConfig`/`AssistantReadiness` including safe endpoint host, trust, and sensitivity status. |
| `src/haute/assistant/_catalog.py` | The versioned capability registry and compatibility catalogue view. It derives mechanical node facts and resolved JSON Schemas from Haute's canonical types, config validation, config I/O, node registry, Polars I/O registry, and save validation; owns completeness-checked semantic metadata; declares the closed operation descriptors consumed by the tool layer; computes canonical manifest identity; and caches immutable manifests by installed version plus capability hash. |
| `src/haute/assistant/_assets.py` | Loader and verifier for the assistant's packaged knowledge assets (read via `importlib.resources`): resource enumeration, `authoring_guide()`, and `example_index()` are cached; `load_example(name)` materialises the complete example tree for validation, then returns only a self-contained model-facing attribution/narrative/rendered-graph view with no inaccessible resource inventory. `validate_example_bundles()` checks closed manifests, content digests, evidence-resource roles, graph/schema assertions, positional golden output, and the declared installed-package fast checks; `materialize_example_bundle()` copies one already-validated project into an empty destination for specialist ordinary/negative checks. The guide fails loudly if missing/empty, index summaries come from the first module-docstring line, and an unknown name is a structured error listing valid names. |
| `src/haute/assistant/_recipes.py` | Versioned immutable recipe registry and deterministic `plan_recipe` dispatcher. Each descriptor has a closed argument schema, unresolved decisions, preconditions, allowed primitive operation kinds, postconditions, linked example bundles, and stable failures. Explanation-only requests do not route to mutations, while a later explicit sequenced authoring clause retains mutation intent. Planner output is round-tripped through `_wire_ops.parse_ops`; unknown recipes and invalid arguments fail by stable code. |
| `src/haute/assistant/_project_knowledge.py` | Source-linked project-knowledge extraction, bounded query selection, and disposable content-addressed index. Derives a saved-graph fact, a value-free `haute.toml` digest fact, and allowlisted UTF-8 documentation evidence; labels unmarked document sensitivity as restricted; records source digest/extraction version/evidence class; filters by `EgressPolicy`; and atomically refreshes metadata-only cache state under `.haute/assistant/knowledge/`. An allowlisted document that is not valid UTF-8 fails the read with a typed, project-relative error instead of silently disappearing. Dataset schemas remain a separate schema-only tool result. |
| `src/haute/assistant/_evaluation.py` | Closed evaluation scenario/support-matrix loaders, semantic/safety scorer, repeated-trial attribution, percentile aggregation, and release-gate evaluator. The runner is injected so deterministic tests use fakes and the live-provider lane uses real provider adapters in isolated projects. It never imports or exposes held-out fixtures through production tools. |
| `src/haute/assistant/_self_test.py` | Developer-facing live self-test harness for the configured provider. It loads a closed prompt-case format, copies each project fixture into a disposable directory, initializes the real Git mutation gate, runs the provider-neutral loop with the real tool executor, and evaluates semantic, completion, connectivity, join-port, failed-attempt, duplicate-read, and canary-leakage expectations. Reports may retain ordered tool names plus value-free status/error code/validation path/validation reason diagnostics so failed model strategies are actionable. They otherwise record only redacted identities, outcomes, reasons, graph structure, and aggregate metrics; prompts, model prose, tool arguments/results, credentials, dataset values, canary values, and content digests are not written to reports. |
| `scripts/run_assistant_self_test.py` | Explicit credentialed CLI for listing/selecting self-test cases, loading the project's configured provider, running isolated synthetic cases, printing the redacted summary, optionally writing the redacted report, and exiting non-zero when any case fails. |
| `src/haute/assistant/assets/examples/<id>/manifest.json` | Closed executable-bundle manifest (`schema_version=1`, stable id/version, summary, source, `fast`/`ordinary`/`negative` assertion tier, required `engineering`/`pricing` review class, and a closed-role resource inventory). Review class records the required discipline rather than asserting approval; model-validation and optimisation fixtures use `pricing`, while purely mechanical fixtures use `engineering`. Every bundle includes its project configuration, source, synthetic input, graph/schema expectations, golden request/output, boundary cases, paired prompts, and semantic assertions. Assertion files have only `target`, non-empty `required_columns`, optional `row_count`, and a non-empty closed `checks` list. Golden arrays retain production row order, so order-unstable operators are followed by an explicit stable pipeline sort rather than normalized by the verifier. Every declared resource resolves inside its bundle, exists, and matches its recorded SHA-256 digest. |
| `src/haute/assistant/assets/authoring_guide.md` | Packaged, hand-authored Haute idiom: canonical pipeline shapes, naming and stage-chaining conventions, and do/don't guidance returned with source/version/digest/evidence attribution by the authoring-guide tool; it is not embedded in every system prompt. |
| `src/haute/assistant/assets/examples/branched_features.py` | Packaged exemplar with parallel feature branches joined before the output stage; its module docstring supplies narrative notes and the index summary. |
| `src/haute/assistant/assets/examples/joined_reference.py` | Packaged exemplar showing a reference-data join; parsed as data by `_assets.py`, never imported as a module. |
| `src/haute/assistant/assets/examples/linear_pricing.py` | Packaged minimal linear-pricing exemplar; parsed through the real pipeline parser and rendered in the same compact graph shape as the get-pipeline tool. |
| `src/haute/assistant/assets/examples/config/data_input/quotes.json`, `src/haute/assistant/assets/examples/config/data_input/regions.json` | Packaged parser-relative file-input sidecars used by the linear and joined exemplars; source decorators load them through the same generated-code helper as user pipelines. |
| `src/haute/assistant/assets/examples/config/quote_input/quote.json` | Packaged API-input schema for the branched exemplar, including its emitted `quote` port. |
| `src/haute/assistant/assets/examples/config/quote_response/joined_priced.json`, `src/haute/assistant/assets/examples/config/quote_response/linear_priced.json`, `src/haute/assistant/assets/examples/config/quote_response/response.json` | Packaged response-output sidecars; each carries a concrete non-empty `outputMapping`. |
| `src/haute/assistant/_wire_ops.py` | Closed provider-wire graph-edit models plus graph-independent `parse_ops` validation. It imports no assistant modules, so recipes, the capability catalogue, and the graph domain layer share one operation vocabulary without lazy imports or dependency cycles. |
| `src/haute/assistant/_ops.py` | Pure graph-edit domain layer, re-exporting the wire vocabulary for its existing public seam: ordered graph application, assistant-authoring validation (including connected new nodes and retained Polars results), canonical snapshot/revision and semantic-diff functions, typed plan models, deterministic verification policy, postcondition evaluation, and the bounded single-use `PlanStore`. It performs no writes. |
| `src/haute/assistant/_render.py` | Shared compact graph renderer for live pipelines and packaged examples. It emits bounded node/config summaries, edges and handles, preamble presence/digest, and singleton presence without executable source or row values. Edge handles are rendered under the exact field names the graph-edit operations accept, so the shape the model reads back is the shape it must write; see Edge cases. |
| `src/haute/assistant/_application.py` | `PipelineApplicationService`, the stateful inspect → dry-run → apply → verify service. It composes the public parser, the save service's no-write validation and transactional save, shared save lock, plan store and graph-update publisher; transport and model tools are adapters only. Schema validation resolves through `execute_lazy_graph(..., schema_only=True)`, and owns both the seed rule and the pre-existing-failure rule described under Plan/apply/verify. |
| `src/haute/assistant/_tools.py` | Thin adapters over the capability registry and `PipelineApplicationService`. Read tools retain their bounded renderers, including bounded recursive dataset discovery. Config redaction is policy-driven: credentials and row values are never eligible, while executable keys follow the project's own `allow_executable_source` decision rather than being redacted unconditionally. Value profiling is the one data-reading adapter and is gated on the egress policy's row-sample permission; see Control flow. A routed Parquet showcase binds an omitted listing root to the safe folder explicitly named by the user and enables recursion. Each source-bound executor seeds its evidence ledger from schema/content evidence in the exact provider history window, then adds evidence returned during the current turn. The only provider-visible mutation tools are `dry_run_graph_edits` and `apply_graph_plan`: the model must pass the exact returned plan hash and cannot resend operations at apply time. Tool code does not own revision, save, or verification policy. |
| `src/haute/assistant/_session.py` | Session store: `AssistantSession` records (id, bound pipeline `source_file`, provider-neutral user/assistant/tool/internal-controller history including required tool-result `is_error`, per-session `asyncio.Lock`, timestamps), create/lookup/resume, `list_sessions` for the chat list, the provider-request history window, and bounded retention. Controller messages are provider-visible but transcript-hidden. Durable tool arguments/results become `{"redacted": true}` plus approved revisions/evidence and value-free validation diagnostics; deterministic payload digests are forbidden because finite-domain values are enumerable. Persistence, revival, corruption handling, pruning, and non-fatal write degradation retain their existing contracts. |
| `src/haute/assistant/_providers.py` | The `AssistantProvider` protocol and its three public adapters: `AnthropicProvider` (`anthropic` SDK, Messages streaming API), `OpenAIProvider` (`openai` SDK, Chat Completions), and `DatabricksProvider`. Databricks subclasses the OpenAI-compatible implementation but retains the `databricks` provider identity for client construction, logs, and typed failures. SDKs are core dependencies but imported lazily inside the adapters (importing Haute never triggers provider-side behaviour; a broken install surfaces as a readiness reason); each adapter normalises its SDK's stream into the internal `ProviderEvent`s (see Control flow § Provider adapters for the exact call and event mappings) and maps SDK failures to `AssistantProviderError`. |
| `src/haute/assistant/_loop.py` | Provider-neutral agent loop as an async generator of typed stream events: resolves only an unbroken `NEEDS_INPUT:` clarification chain into its originating recipe route, assembles prompt/history/tool inputs, forwards text deltas, invokes the injected tool executor, feeds structured results into later provider rounds, shields only an in-flight transactional apply from cancellation, enforces tool/time limits, terminates when either the plan-correction or the malformed-call dry-run budget is exhausted, applies the bounded incomplete-mutation continuation gate, commits turn history, and closes every provider stream. It does not implement graph edits itself. |
| `src/haute/routes/assistant.py` | The FastAPI router: `GET /api/assistant/status`, `GET /api/assistant/sessions` (this pipeline's saved conversations for the panel's chat list, resolving the pipeline exactly as session creation does), `POST /api/assistant/session`, `POST /api/assistant/message` (an SSE `StreamingResponse` wrapping `_loop`'s generator). Route-level exception translation follows the product conventions (typed `HauteError`s surfaced, everything else sanitized). Swept by the existing `tests/test_routes_hygiene.py` contracts like every `routes/` module. |
| `src/haute/_column_summary.py` | Shared with [explore-eda](../explore-eda/low-level.md): the Polars dtype facts every column-summarising surface needs — `is_unhashable_dtype` for the columns that cannot be counted, the reserved count-field alias `CATEGORICAL_COUNT_FIELD`, and `json_safe_scalar`. Polars-only, so the assistant reaches it without importing the routes layer. |
| `src/haute/schemas.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); the assistant slice of the server-api-owned shared HTTP/SSE contracts: status, session request/response and transcript entries, message request, usage, and the text-delta, tool-started, tool-finished, graph-updated, completed, failed, and cancelled event union mirrored by `frontend/src/api/assistant.ts`. |
| `src/haute/server.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); includes the assistant router with the other feature routers ahead of the API/WebSocket 404 catch-alls and supplies graph-update fingerprint/wire-path helpers used by mutation publishing. |
| `src/haute/routes/_save_pipeline.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); transactional save service used by assistant mutations; its `save_graph_transactionally` wrapper explicitly forwards the parsed graph's preserved blocks into `SavePipelineRequest` and owns rollback, self-write marking, and ledger-capture warnings. |
| `pyproject.toml` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); declares `anthropic>=0.40` and `openai>=1.55` as core dependencies and omits `src/haute/assistant/assets/*` from import-coverage measurement because exemplar `.py` files are parsed package data, while ruff and parser tests still check them. |

Environment knobs: `HAUTE_ASSISTANT_TURN_TIMEOUT` (seconds, default 300) and
`HAUTE_ASSISTANT_MAX_TOOL_CALLS` (default 20) read lazily via `haute._env`, matching the
existing pattern. `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS` (the per-provider-call output budget
both adapters pass through, threaded via `AssistantConfig`) is deliberately stricter than
`haute._env`'s lenient semantics: unset → 8192; a set-but-malformed or non-positive value
is a **readiness error**, not a warn-and-default — a silently substituted cost ceiling is
precisely the wrong-fallback class the project forbids.
Retention constants in `_session.py`, not env knobs: the provider request carries the most
recent **complete turns** fitting a 40-message budget; stored history is capped at 200
messages by evicting whole oldest turns; live-session LRU cap 32 (least-recently-used
*idle* session evicted on create beyond the cap — dropping only the in-memory record, the
persisted file revives it on next lookup; a session with a running turn is never
evicted); persisted session files cap at 100 (`MAX_PERSISTED_SESSIONS`), pruning the
oldest by session-file modification time at session creation after removing abandoned
atomic-write temp files. Pruning always cuts at turn boundaries — an
assistant tool call and its result are never separated (both provider APIs reject
orphaned halves).

## Key types and data structures

- **`CapabilityManifest`** (`_catalog.py`): immutable manifest identity plus
  tuples of `NodeCapabilityDescriptor`, `OperationCapabilityDescriptor`, and
  recipe descriptors.
  `as_dict()` is the sole JSON representation and always emits
  `schema_version`, `haute_version`, `capability_hash`,
  `installed_capabilities`, `feature_flags`, `nodes`, `operations`, and
  `recipes`.
- **`NodeCapabilityDescriptor`**: a closed description of one `NodeType`.
  `config_schema` is derived from the canonical `TypedDict` annotations
  (including `Required`, `Literal`, unions, lists, mappings and discriminated
  Data Input/Output branches) and has `additionalProperties: false`.
  `required_fields`, `optional_fields`, defaults and enum values are derived
  from that resolved schema rather than maintained separately.
- **`OperationCapabilityDescriptor`**: a closed, versioned operation
  declaration. `_tools.TOOL_DEFINITIONS` is projected from these descriptors,
  so a provider-visible tool cannot exist without risk, egress, retry,
  concurrency, timeout, payload, context-budget, stable-error and recovery
  metadata. Its output schema requires attribution plus exactly one non-empty
  success or error result variant; an empty object is never a valid declared
  operation result.
- **Manifest identity/cache**: `get_capability_manifest()` refreshes installed
  format/engine facts, canonicalises all immutable material as UTF-8 JSON with
  sorted object keys and compact separators, hashes it with SHA-256, and looks
  up the frozen result by `(haute_version, capability_hash)`. Cache inspection
  and clearing are private test seams only.

- **`AssistantConfig`** (frozen dataclass, `src/haute/assistant/_config.py`): `provider: Literal["anthropic", "openai", "databricks"]`,
  `model: str`, `base_url: str | None` (authored only for OpenAI; rejected for
  Anthropic and Databricks; Databricks receives the derived
  `<DATABRICKS_HOST>/serving-endpoints` value; an authored OpenAI value is an
  absolute `http|https` URL with a hostname,
  valid port, no whitespace/control characters, and no user information),
  `api_key: str`, `max_output_tokens: int` (from `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS`,
  default 8192 when unset; a set-but-malformed or non-positive value fails readiness with a
  named reason rather than silently substituting the default),
  `egress: EgressPolicy`, and safe `endpoint_host: str`. Only ever constructed fully valid.
- **`AssistantReadiness`** (`src/haute/assistant/_config.py` → `AssistantStatusResponse`): `configured: bool`,
  `reason: str | None` (exactly one of: no `[assistant]` table, unknown provider, missing
  model, missing provider credential/host env var — named — the provider SDK missing from the installation
  (a broken install: the SDKs are core dependencies), or an invalid
  `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS` value — malformed or non-positive, named),
  `provider`/`model` echoes, safe `endpoint_host`, `trust`, and
  `max_sensitivity`, plus
  `mutations_enabled: bool` / `mutations_reason: str | None` — driven by
  `haute._git.working_branch_status(...)`, the same readiness the GUI's Save gate requires.
  The state→reason mapping is owned in `src/haute/assistant/_config.py` because not every non-ready state
  carries its own message: `"ready"` → enabled, reason `None`; `"no-repository"` → fixed
  message directing the analyst to initialise Git; `"unset"` → fixed message directing the
  analyst to create/select a working branch in the Git panel; `"detached"` → fixed message
  directing them to attach HEAD in the Git panel; `"divergent"` → fixed message directing
  them to resolve divergence in the Git panel; `"invalid"` → the response's `errors` list
  joined verbatim (the one state that carries git-layer text). `working_branch_status` is
  total for those six repository/readiness states. An unexpected git-domain `HauteError`
  raised while computing readiness likewise maps to disabled with that error's message as
  the reason — the assistant status endpoint always renders readiness; an infrastructure
  failure is a reason, never an HTTP error.
- **`ProviderEvent`** (internal union, `_providers.py`): `TextDelta(text)`,
  `ToolCallRequest(id, name, arguments)` — emitted only once a call's streamed argument
  fragments have been fully accumulated and JSON-parsed; several calls in one provider turn
  are emitted in stream order — and `TurnStop(reason: "end" | "tool_use", usage)`.
  Adapters translate SDK streams into exactly these; the loop never sees SDK types.
- **`AssistantProviderError(HauteError)`** (`_providers.py`): hand-authored message carrying
  provider name and a classified failure such as `authentication`, `rate_limit`,
  `connection`, `status`, `stream`, `dependency`, `malformed_stream`, `truncated`, or
  `filtered` — never the raw provider response body.
- **`GraphEditOp`** (discriminated union, `_ops.py`), addressing nodes by id (the function
  name shown by `get_pipeline`) or — within one batch — by `$<ref>`, the batch-local handle
  a preceding `add_node` declared. Refs are resolved server-side to the real sanitised node
  ids as each `add_node` applies, so the model never has to predict name sanitisation or
  collision outcomes:
  - `add_node {node_type, name, config?, ref?}` — `submodel`/`submodelPort` types rejected;
    `ref` (optional) names the batch-local handle later ops may use wherever a node id is
    accepted. The declaration is the bare name without a leading `$` and later uses carry
    it (`"ref": "agg"` → `"$agg"`); the asymmetry is enforced by `_wire_ops` and stated in
    the `ref` property's own schema description, because documenting `$ref` only at the
    use sites led to declarations being written in the rejected spelling.
    Positions are assigned by the deterministic rule below *after* the whole batch
    has applied, so parent-based placement sees the batch's final wiring.
    The persisted id and label are both the canonical sanitised function name,
    because source reparse cannot preserve a separate unsanitised label.
    Adding or renaming to a sanitised id already owned by a different node is
    rejected before the working copy can contain duplicate identities.
  - `update_node {node, config}` — shallow key merge into the existing config; an explicit
    JSON `null` value removes that key. Unknown keys for the node's type are rejected using
    the same `TypedDict`-derived allowlist machinery the sidecar writer uses (see Edge
    cases for why this is deliberately stricter than save's warn-and-drop).
  - `rename_node {node, new_name}` (sets both id and persisted label to the
    canonical sanitised function name) · `delete_node {node}` (drops every touching edge,
    mirroring the GUI's atomic delete) · `add_edge {source, target, source_handle?,
    target_handle?}` · `delete_edge {source, target, source_handle?, target_handle?}`
    (matched on endpoints + handles; an ambiguous match is an error, never a guess) ·
    `update_preamble {preamble}` (full replacement). Both handle properties describe when
    they apply: a source handle only for a multi-frame source such as an `apiInput` table,
    a target handle only for a node with named input roles. An ordinary `polars` node
    binds inputs by source name and has no input ports, so its edges carry neither — the
    schema says so rather than leaving the model to guess a port name.
    Every edge into an `edgeJoin` is stricter than the generic operation shape:
    `target_handle` is mandatory, exactly one incoming edge must use `"base"` and exactly
    one must use `"join"`, and those sources must equal the node's `baseInput` and
    `joinInput` respectively. Handle-less joins are invalid; there is no edge-order
    inference or compatibility path.
- **`ProjectSnapshot` / `ProjectRevision`** (`_ops.py`): immutable saved graph
  plus a canonical manifest of source/config/knowledge/artifact/capability
  digests. The revision is the SHA-256 of canonical JSON for that manifest.
- **`GraphEditPlan`**: base revision, normalized primitive operations,
  semantic diff, affected capabilities, postconditions, egress, verification
  tier, schema evidence, and plan hash.
  Affected capabilities are derived from the complete change set rather than
  the bounded presentation lists. The hash excludes timestamps and includes
  every authority-relevant field.
  A schema-tier plan carries a bounded, deterministic record for each verified
  terminal (`node`, output/port shape, column count, schema SHA-256); that
  evidence is part of the hash rather than an informational afterthought.
- **`SemanticDiff`**: closed added/removed/renamed/updated node records,
  added/removed edges, configuration changes, preamble change and sidecar
  change identities. Provider-visible identity lists are capped at 50 entries
  per category and carry closed complete-category counts, an explicit
  `truncated` flag, and a SHA-256 over the complete untruncated semantic diff.
  The digest covers every change identity, full edge port identity, and the
  exact preamble digest. Post-save exactness compares that complete digest as
  well as the visible values, so presentation bounds can never mask an
  additional or missing structural change. Configuration changes identify
  their node and key; the stored normalized
  operation remains the authority for its requested value.
- **`PlanStore`**: process-local, size- and TTL-bounded records keyed by plan
  hash. It owns validated/applying/applied/aborted state transitions under a
  lock. An applied record cannot return to validated. A failed pre-commit
  application becomes aborted and cannot be applied directly again; an
  identical fresh dry-run may replace that aborted record and reissue the same
  deterministic hash after complete revalidation.
- **`PipelineApplicationService`**: the only stateful assistant mutation
  service. `inspect`, `dry_run`, `apply`, and `verify` return closed
  Pydantic models or stable `AssistantOperationError` codes.
- **`AssistantSession`** (`_session.py`): `id` (uuid4 hex), `source_file`, `history` — a list
  of **turn records**, each grouping one user message with every assistant message, tool
  call, and tool result it produced (the atomic unit all pruning operates on) — one
  `asyncio.Lock` (the one-turn-at-a-time guard), `created_at`/`last_used`.
- **SSE wire events** (`schemas.py`): the `AssistantStreamEvent` union listed in the module
  map — field-for-field the contract documented in
  [frontend-assistant-ui](../frontend-assistant-ui/low-level.md) Key types.

## Control flow

**Capability query**: the prompt obtains
`get_capability_manifest(compact=True)`, which returns identity, dynamic
installed capabilities, and stable indexes. `get_capability_descriptors(kind,
ids)` accepts only the closed kinds `node`, `operation`, and `recipe` plus
one to twelve unique ids. It validates the complete batch before returning descriptors
in request order, so an unknown or duplicate id is one stable
`unsupported_capability` or `invalid_capability_query` result rather than a partial
response. Every descriptor is materialised into ordinary JSON containers.
`list_node_types` maps the manifest's node descriptors into its legacy response shape
and cannot drift independently.

**Recipe planning**: `plan_recipe` has a canonical flat discriminated union derived from the
closed recipe argument schemas. For a uniquely routed current request, the provider-facing
tool definition contains only the exact matching union branch. Unrouted provider catalogs
omit `plan_recipe` and `dry_run_recipe_plan`; the canonical internal registry retains the
complete union. Argument descriptions distinguish a requested graph-node name from its
output-column name. Transform, join, and rating recipes also accept optional non-empty
`output_name` and `output_columns` fields, which must be present together. The latter is a
non-empty unique array of simple JSON-field column names. When present, the deterministic
planner adds one response `output` node, a canonical JSON `outputMapping` for exactly those
columns, and an edge from the recipe node in the same canonical batch. The standalone
`response_output` recipe requires `source`, `output_name`, and `output_columns` and
creates the same canonical mapping directly after the saved source. A bare output name or
column list is a material ambiguity and fails recipe planning. A continuous-banding rule is
a closed object requiring `op1` from `<`, `<=`, `>`, `>=`, `=`, or `==`; finite
numeric `val1`; and a non-empty string `assignment`. The optional second bound is valid
only when `op2` and finite numeric `val2` are both present. A categorical-banding rule
contains exactly a non-null finite JSON scalar `value` and non-empty `assignment`. The
rating-step recipe's provider-facing table contract is closed and positional: each
table requires one to three unique ordered `factors`, an `output_column`, a finite numeric
`default_value`, and non-empty entries. Every entry has exactly `factor_values` and a
finite numeric `value`; the factor-values length must equal the table's factor count and
each factor value must be a non-null finite JSON scalar. Optional `combined_outputs` entries
have exactly `output_column`, `operation` from `multiply`, `add`, `min`, or `max`,
and finite numeric `base_value`. Planning converts these snake-case positional arguments
to canonical dynamic-key rating tables and camel-case combined outputs, runs the canonical
rating validators, then includes the result in the `recipe_plan_hash` over recipe id,
version, canonical operations, and postconditions.

The `parquet_showcase` branch requires closed `base` and `reference` objects containing
exactly a safe relative Parquet `path` and graph `name`, plus `join_name`, `join_key`,
`transform_name`, and `output_name`. It accepts neither provider-authored transform code nor
output columns. Planning emits two scanned file `dataInput` nodes, a left `edgeJoin` with
exact base/join handles, a connected Polars node whose fixed code casts `join_key` to the
derived `<join_key>_text` column and adds literal `showcase_stage`, and a connected canonical
JSON response output mapping exactly `join_key`, `<join_key>_text`, and `showcase_stage`.
Paths, names, generated code retention, mappings, primitive operations, and postconditions all
pass their ordinary validators; planning and self-test never execute the graph or output.
The source-bound executor retains that material by hash and replaces the previous pending
handle for the same recipe on correction. Its provider result is the closed opaque receipt
`recipe_id`, `version`, and `recipe_plan_hash`; canonical operations and postconditions stay
server-side. `dry_run_recipe_plan(recipe_plan_hash)` resolves only that executor's live
handle and invokes the ordinary graph dry-run with exactly the stored canonical recipe
material. It rejects every additional property. A pending recipe makes primitive
`dry_run_graph_edits` return `recipe_plan_requires_handle`; an unknown or replaced handle returns
`recipe_plan_not_found`. The live handle clears only after a successful dedicated
dry-run. Neither tool writes. A conservative current-request router returns a recipe id only
when exactly one explicit domain pattern matches: a band/banding term with a continuous,
range, breakpoint, bucket, or comparison-operator cue maps to `continuous_banding`;
categorical/discrete banding maps to `categorical_banding`; join maps to `reference_join`;
and the phrase rating step maps to `rating_step`. An explicit request to build, create,
author, or make a Parquet pipeline as a showcase of multiple node types maps to
`parquet_showcase`; its showcase cue is `showcase`, `node types`, or the closed pair
`many`/`types` even when the intervening noun is misspelled. Its current-turn
contract requires dataset listing and every schema when two to eight Parquet datasets are
discovered. It forms candidate pairs with shared `quote_id`, otherwise pairs with exactly one
shared column; ranks candidates by `quote_id` first, descending combined distinct column count
second, and the ordered project-relative path pair last; then chooses the wider member as base
with stable path order breaking equal widths. It supplies only the selected source/key/node-name
arguments while the recipe owns its schema-safe transform and mapped output, and calls the recipe
rather than asking about reversible demonstration aesthetics. It asks only when the dataset count
is outside two to eight or no candidate pair remains. If the assistant returns
`NEEDS_INPUT:`, route resolution scans backward only across consecutive turns whose final
assistant text also begins `NEEDS_INPUT:` and reuses the first directly routed user request.
Any other final response ends continuation, so an unrelated bare path cannot revive stale
mutation authority. A standalone
response-output request maps to `response_output`; a specialist recipe that also requests
a response output keeps its specialist route and owns that downstream output. The loop
appends that route id to the current turn's system contract and
the source-bound executor independently enforces it. Primitive dry-run before that recipe
returns `recipe_route_required`; a `plan_recipe` call for another id returns
`recipe_route_mismatch`. Zero or multiple matches do not force a route; the provider-visible
catalog then omits both `plan_recipe` and `dry_run_recipe_plan`, while the canonical internal
registry retains the complete discriminated union. The router never populates recipe arguments. A closed primary-name recognizer accepts
recipe-specific `named NAME` forms plus `add NAME:` and compares the explicit name to
`name` (or `output_name` for standalone response output) before planning. A mismatch
returns `recipe_name_mismatch` with the expected name and never stores a recipe plan.
A closed material-input recognizer identifies rating-factor
requests which explicitly say factor values or missing-factor policy are not supplied. Such a
turn appends a mandatory `NEEDS_INPUT:` contract, omits `plan_recipe`,
`dry_run_recipe_plan`, `dry_run_graph_edits`, and `apply_graph_plan` from provider tools,
and makes the independent source-bound executor return `material_input_required` for any
of those calls. Provider-side narrowing changes only the advertised input
branch; the source-bound executor still validates the canonical schema and route.

**Column value profiles**: `get_column_profiles(node, input?)` is the only operation that
reads project data, and it never returns a row. It requires
`[assistant.egress].allow_row_samples`; without it the result is `egress_policy_denied`
naming that flag. With `input` omitted it profiles the node's own output; with `input` set
it profiles that named input, resolved through the same code-visible input names
`get_node_schema` reports. It prepares frames through the ordinary lazy path — **not**
`schema_only`, because collecting is materialisation and the engine's admission policy
must apply exactly as it would to any other read of those rows — collects one bounded
prefix (`_MAX_PROFILE_ROWS`, reported as `rows_scanned`/`scan_bounded`), and summarises
each column: `null_count` always; `distinct_count` whenever the dtype can be counted; for
string, categorical, enum and boolean columns with at most `_MAX_PROFILE_LEVELS` distinct
values, those values with their counts; for numeric and temporal columns, `min` and `max`;
otherwise `values_withheld`. The cardinality bound reduces disclosure for high-cardinality
strings, while low-cardinality strings — including repeated names, addresses, dates of
birth, or registrations — can be emitted. The explicit `allow_row_samples` grant is the
authorization boundary; the level cap is not a personal-data guarantee. A `Y`/`N`-style
encoding is exactly what the tool is intended to emit. Individual level strings are
truncated to `_MAX_PROFILE_VALUE_CHARS`. The operation's egress class is its own value,
`restricted-value-profile`, so a policy review
can see the one data-reading capability plainly.

Every branch is selected by dtype *before* its aggregation runs, through the shared
predicates in `haute/_column_summary.py` that Explore's frame statistics also use — the
one place these Polars facts are recorded, so a second summariser cannot rediscover them
as production failures. A column whose values Polars cannot hash raises rather than
returning nothing, and one raise inside a per-column loop aborts the entire frame's
profile: such a column reports `values_withheld` and no `distinct_count`, losing only
itself. `value_counts` is given an explicit count-field name because Polars refuses it on
a column already called `count`, which is an ordinary name in an aggregated frame.

Every emitted value passes through `json_safe_scalar`. The result is JSON-encoded twice
before the model reads it — once to bound it against `_MAX_TOOL_CONTEXT_BYTES`, once by
the provider adapter — and both encoders take only JSON scalars under `allow_nan=False`.
Polars returns native `date`, `datetime`, `time`, `timedelta` and `Decimal` objects for
exactly the temporal and money columns this tool exists to describe, and a non-finite
float is a real value a numeric column can hold; each becomes its written form (ISO-8601,
exact digits, `inf`) while numbers and strings keep their JSON type, so a numeric bound
stays a number. Left raw, the failure surfaced nowhere near the column that caused it: as
an opaque `tool_failed` for the whole call. The system prompt requires the model to
profile a frame before comparing a column to a literal, and to answer `NEEDS_INPUT:`
rather than guess an encoding when values are unavailable or withheld — a guessed
comparison produces code that runs, validates at schema tier, and silently matches
nothing.

**Project-knowledge query**: `get_project_knowledge(query, limit)` builds the
current policy-filtered view, scores only eligible items against normalized
query terms, and returns at most ten items under a fixed aggregate character
budget. Returned items retain source/digest/version/sensitivity/evidence
attribution. Restricted or otherwise excluded material contributes only to an
excluded count; its path and content never cross the tool boundary.

**Plan/apply/verify**:

1. `dry_run` resolves and parses the saved source, builds the snapshot and
   revision, parses and normalizes primitive ops, applies them to a deep graph
   copy, invokes the save service's public no-write validation (including
   canonical Edge Join role, handle, topology, and key-form validation),
   derives the complete changed-node set, and resolves the schema of every
   reachable executable terminal through `flatten_graph` +
   `execute_lazy_graph(..., enforce_contracts=True, schema_only=True)` +
   `collect_schema()`.
   No frame is collected and no sink is invoked, which is what `schema_only`
   declares to the engine's group-by materialisation-admission gate. It then
   derives the semantic
   diff/postconditions/tier, binds the closed schema evidence into the plan
   hash, and records the immutable validated plan. A schema failure aborts the
   dry-run and stores no plan, except for the pre-existing collateral case below.

   **Seeds are the nodes the plan is answerable for**: nodes added, updated, or
   renamed, plus the **target** of every added or removed edge — never the source.
   An edge change alters what arrives at the target and therefore everything
   downstream of the target; the source's own output schema is unchanged and its
   other children are untouched. A changed preamble seeds the whole graph. Validation
   targets are the terminal nodes of the seeds' downstream cone, capped by
   `_MAX_SCHEMA_TARGETS`.

   **Pre-existing collateral is reported, not charged to the plan.** A target inside
   that cone which the plan did not seed, and which already fails to resolve on the
   saved graph, is excluded from schema evidence and recorded as a
   `pre_existing_schema_failure:<node>` validation warning. The plan then falls to the
   tier its evidence actually supports rather than claiming a verification it did not
   perform. The warning carries the node identity and nothing else: validation warnings
   are part of the hashed plan authority, and an engine failure message embeds estimated
   row counts and scan byte sizes, so including it would make the plan hash depend on
   data-file metadata the revision manifest does not pin and turn an ordinary apply into
   a spurious `invalid_plan`. `get_node_schema` on the named node reports the actual
   failure, and the tool log records it server-side. Seeded nodes are never excused: a node the plan
   added or updated is the plan's responsibility, and an authored-but-empty node fails
   on the saved graph by construction, so excusing seeds would silently accept exactly
   the broken code the analyst asked for. A target that resolves on the saved graph and
   fails on the planned graph was broken by the edit and still aborts the dry-run.
   `apply` recomputes seeds, evidence, and these warnings identically, so the plan hash
   is stable; post-save verification re-resolves only the targets the plan already
   proved and admits no pre-existing excuse at all.
2. `apply` acquires `save_lock`, reloads the snapshot, compares its revision,
   replays and revalidates the stored normalized operations, recomputes the
   candidate schema evidence and plan hash, checks the plan-store state, and invokes
   `SavePipelineService.save_graph_transactionally` exactly once.
3. Still under the lock, it reparses, derives the actual diff/revision,
   verifies the visible diff plus complete semantic-diff digest, postconditions,
   and schema evidence at the declared tier, marks the plan applied, and publishes
   one graph update. Errors before the save leave no files changed; post-save
   verification errors report the committed state and ledger evidence without
   retrying the mutation.

`PlanStore` is bounded for plans awaiting use, but an `applying` record is a
non-evictable lease until `complete_apply` or `abort_apply` records its
terminal result. TTL expiry and capacity pressure may remove only
non-applying records. If every slot is leased, a new distinct dry-run fails
with `plan_store_busy` rather than losing authority evidence for a save that
may already be committing.

**Status** (`GET /api/assistant/status`): `_config.assistant_readiness()` — read `haute.toml`
(malformed or unknown `[assistant]` key → `ConfigError` → 400), check
provider/model fields, validate an OpenAI `base_url` or derive and validate the
Databricks serving endpoint from `DATABRICKS_HOST`, then probe the SDK import
for the configured provider and check its credential env var. Pure inspection, no
provider network call. Config errors name `[assistant].<field>` but never echo
the field value.

**Session list** (`GET /api/assistant/sessions`): resolve the pipeline exactly as session
creation does, then return `SessionStore.list_sessions(source_file)` — one summary per
conversation bound to that source file, carrying id, title, created/last-used timestamps,
and message count, most recently used first. The title is the opening user message,
whitespace-collapsed and bounded to 80 characters. Summaries are read directly from the
persisted files rather than through `_revive`: listing must not pull every stored
conversation into the retained LRU, where it would evict live sessions. Like every other
store operation, the merge with live records runs synchronously on the asyncio event-loop
thread; moving that call to a worker would race the event-loop-owned mapping. A live
session takes precedence over its persisted copy, which can lag by one
turn; an unreadable or malformed file is a logged warning treated as absent, matching the
store's existing degradation posture. A conversation with no messages is omitted, because
`create` persists immediately and an abandoned "new chat" would otherwise occupy the list
as an untitled empty row.

**Session create** (`POST /api/assistant/session`): resolve the pipeline (explicit name via
`lookup_pipeline_by_name`, else the same first-pipeline default `GET /api/pipeline` uses);
unknown name → 404. When the request carries a prior `session_id`,
`SessionStore.resume` validates its source binding before touching an
in-memory candidate or promoting a disk-backed candidate. When it matches,
return it unchanged
with `history`: the stored turns mapped to transcript entries (`user`/`assistant` text
entries, and `tool` entries carrying the tool name, the same compact result summary the
live stream uses, and the error flag) so the panel rehydrates the conversation.
A successful mutation tool result's persisted `graph_fingerprint` additionally
derives a settled `graph_updated` activity entry immediately after that tool
entry, matching the live “Canvas updated” row without duplicating provider
history. Any other
case — no `session_id`, unknown/pruned/corrupt, or a different pipeline — creates and
returns a fresh session with empty `history`; resume is an offer, never an error.

**Message turn** (`POST /api/assistant/message` → SSE stream from `_loop.run_turn`):

1. Readiness is checked before session lookup (400 before the stream opens if
   unconfigured). This ordering means an unconfigured request reports the configuration
   problem even when its session id is also unknown.
2. Look up the session (404) and atomically acquire its lock without waiting — held →
   409. The reservation happens before provider construction or pipeline parsing; either
   pre-stream failure releases it immediately, while a started turn releases it from the
   loop/response lifecycle and appends the turn to history.
3. Resolve the provider configuration, construct the adapter, parse the session's saved
   pipeline, and build the provider request: system prompt (static role and
   authority/evidence instructions + compact capability identity, an installed-I/O
   availability summary, and node/operation/recipe ids with their canonical summaries
   + an example-ID-only index + project facts: pipeline name, source file,
   node-count/type summary) + windowed history + the new user message + `_tools` JSON
   schemas. Fresh graph detail is deliberately *not* embedded in the system prompt — the
   model fetches it via tools, so it is never stale mid-turn. The authoring
   guide and full exemplar bodies are likewise prompt-excluded: the model pulls
   them through `get_authoring_guide` and `get_example` only when relevant. The
   permanent installed-I/O summary includes only group identity, input/output
   availability, cache modes, and format names; field schemas stay out of the prompt
   and remain available through capability tools. This keeps the routing facts useful
   without paying for a redundant descriptor copy or burying the mutation protocol.
   The prompt treats explicit authoring verbs such as build, add, change, update,
   connect, remove, delete, and make as mutation intent rather than an invitation to
   inspect and stop. When an installed deterministic recipe matches the requested
   operation, the model must call `plan_recipe`, then pass its `recipe_plan_hash` to
   `dry_run_recipe_plan`; it never copies the returned operations. A unique explicit
   current-request recipe route is repeated in the per-turn system contract and enforced
   independently by the tool executor. A failed dry run
   permits at most one materially corrected retry. The loop keeps **two independent
   bounded budgets** over failed calls to either dry-run tool, because the two failure
   classes are not the same evidence. A domain rejection — the plan was built and judged
   — costs one of two plan attempts. A closed-input-schema rejection
   (`invalid_request`/`invalid_capability_query`) never reached planning: it says the
   call was spelled wrong, not that the plan is wrong, and the named-field validation
   error makes it directly correctable, so it costs one of two malformed-call attempts
   instead. Charging both to one budget spent the plan retry before the plan had ever
   been judged. Either exhausted budget ends the turn, so neither class can loop: the
   loop appends a deterministic assistant `BLOCKED:` message naming which class blocked
   it, carrying only the latest stable error code and the fact that no graph changes were
   applied, emits `completed`, and performs no further dry-run or provider round. The
   system prompt states the same distinction.
4. Stream provider events. `TextDelta` → emit `text_delta`. `ToolCallRequest` → emit
   `tool_started`; execute; append the result to the pending provider messages before
   emitting `tool_finished` (+`graph_updated` for successful mutations); on `TurnStop("tool_use")`
   re-invoke the provider with the accumulated results. Before streaming, a closed lexical
   classifier marks completion as required for explicit build/add/change/update/connect/
   remove/delete/create/rename/configure/edit/author requests unless they clearly ask only
   for explanation, and for explicit pipeline run/execute/materialise or external-write
   requests. Classification never authorises a new action; it only prevents false completion.
   An action word which the user explicitly identifies as untrusted reported content does not
   create completion authority when the request starts as read-only inspection and asks only for
   an explanation; an actual inspect-then-mutate request remains completion-required.
   The loop also marks completion required after a graph dry-run and tracks whether
   `apply_graph_plan` has succeeded. A successful apply is terminal after the current stream
   reaches its stop event: any later tool-call events in that same provider round are ignored,
   the loop records the successful apply and its result, emits the deterministic assistant
   text `Graph changes applied successfully.`, emits `completed` with usage, and does not
   invoke the provider again. Otherwise, when completion is required, `TurnStop("end")` is
   accepted only when the stripped assistant text begins `NEEDS_INPUT:` or `BLOCKED:` and
   contains non-whitespace detail after the marker. The first unqualified end appends a
   transcript-hidden `controller` message instructing the model to continue; when dry-run
   succeeded, it explicitly requires an immediate `apply_graph_plan` tool call with the
   exact returned hash rather than prose. The loop re-invokes the provider, and adapters
   encode that internal role as a user instruction. A second unqualified end emits `failed`
   with the incomplete-mutation reason. An accepted `TurnStop("end")` emits `completed` with
   usage and finishes.
   If the response closes while suspended at
   `tool_started`, the round commit filters the unmatched call; closing at either later
   event retains the already-recorded result. Thus every persisted call id has exactly one
   matching result id on every generator-close boundary.
5. Tool execution: read tools run via `asyncio.to_thread`. Reads whose answer depends on
   the saved graph (`get_pipeline`, `get_node_schema`, `get_node_config`,
   `get_column_profiles`, `get_dataset_schema`, and `get_project_knowledge`) hold the
   process-wide `save_lock` across their worker-thread operation, so a concurrent save cannot
   interleave graph parsing with schema or value resolution. `get_node_schema` parses the
   saved pipeline, then proceeds in this order:
   1. **Validate the target id against the original hierarchical graph** — a submodel
      placeholder, or an id found only inside a submodel's nested graph → structured error
      naming the v1 submodel boundary (the same classification the ops engine applies to
      submodel-internal targets); an id found nowhere → unknown-node error; only an
      original top-level executable node proceeds. Validating after flattening would be
      wrong twice over: a submodel-internal child id becomes executable once inlined (a
      boundary bypass), and an unknown id would be indistinguishable from a dissolved
      placeholder.
   2. **Reproduce the production execution callers' graph preparation** — the
      `_explore_service._materialise_and_summarise` sequence, never an assistant-local
      variant: `flat = flatten_graph(graph)` (submodels inlined, as every
      run/preview/optimise caller does first); `preamble_ns = _compile_preamble(
      graph.preamble or "", pipeline_dir=_pipeline_dir(graph))`; then
      `lazy_outputs, *_ = execute_lazy_graph(flat, _build_node_fn, target_node_id=node,
      preserve_node_ids={node} | {parents}, preamble_ns=preamble_ns or None,
      source=graph.active_source, enforce_contracts=True, schema_only=True)`
      (`_build_node_fn`/`_compile_preamble`/`_pipeline_dir` from
      `haute.executor`; `preserve_node_ids` keeps the target frame alive through the
      engine's buffer-release, and preserving the target's parents alongside it means one
      call yields both the node's own schema and its per-input schemas;
      `source=graph.active_source` — the facade's `"live"`
      default would silently pick the wrong live-switch branch for a pipeline whose saved
      active source differs; `schema_only=True` states the invariant this path already
      holds and tests, so the engine's group-by materialisation-admission gate does not
      apply — see [execution-engine](../execution-engine/low-level.md)).
   3. **Read the result** from `lazy_outputs[node]`: a single frame → `collect_schema()`
      rendered as `{name, dtype}` pairs; a multi-frame source (a
      `dict[port_name, LazyFrame]` — e.g. an `apiInput` with several emitted tables) → the
      same rendering per port, keyed by port name, never an unconditional
      `.collect_schema()` on the dict. Nothing is collected, and no result is persisted
      (the call is cheap and always reflects saved state; the engine's own in-request
      schema caches apply).
   4. **Report the inputs too.** When the node has incoming edges, the result carries
      `inputs`: the same `{name, dtype}` rendering per input, keyed by the name the
      node's own code binds — `_graph_utils.edge_input_name`, so an `apiInput` frame
      handle and an ordinary sanitised source label are each named the way the authored
      function signature sees them, and a multi-frame source is narrowed to the edge's
      own port. This is the fact authoring code against a node actually requires; the
      output schema alone describes what a node already produces, which is precisely
      what an unwritten node has not got. A source node reports no `inputs` key.
   5. **An authored-but-empty transform is a success, not a refusal.** A node with no
      code and more than one input cannot resolve its own output — the engine raises its
      canonical `INCOMPLETE_TRANSFORM_MESSAGE`. That is the ordinary editing state of a
      node the analyst is asking the assistant to write, so the tool returns the third
      declared success shape: `unresolved_reason: "node_has_no_code"` plus `inputs`, with
      neither `columns` nor `ports`. Each input is then resolved against its own source,
      one engine call per source on this path only; an input whose own source is
      unresolvable reports `{unresolved_reason, source}` in place of columns rather than
      disappearing from the result. The operation's own description states what that state
      means for authoring: such a node is already wired into the graph and awaiting its
      code, so `update_node` on it is normally the intended edit. Without that, a model
      reads "no output schema" as "unusable" and builds a parallel node beside the one the
      analyst pointed at. Any other engine raise — unfetched Databricks cache
      (`CacheNotFoundError`, whose message already tells the analyst to fetch), a missing
      trained artifact, invalid node code — remains a structured `schema_unresolvable`
      tool error. A Polars failure keeps its own text, because naming the offending
      column or plan step is what lets the model correct its authoring and is exactly
      what the dry-run schema-validation path already returns; every other unexpected
      exception is still sanitized to the internal-error detail and logged with
      `exc_info`.

   Mutation dispatch is an adapter over `PipelineApplicationService`.
   `dry_run_graph_edits` performs parse, exact evidence/revision capture, pure
   operation replay, no-write save validation, schema-only lazy-plan validation, postcondition evaluation, semantic
   diff construction, and immutable plan storage while holding the shared
   save lock against concurrent GUI saves. `apply_graph_plan` then checks branch
   readiness and, under the same lock, reloads and compares all revision sources,
   replays the stored normalized operations, recomputes the plan hash, checks
   one-use authority, invokes
   `SavePipelineService.save_graph_transactionally` once, reparses, verifies the
   actual diff, schema evidence, and postconditions, and publishes the standard graph update.
   The provider receives no operation that combines dry-run and apply.

   Every tool outcome is logged through the package's structlog logger before it
   returns: a failure at info level as `assistant_tool_error` with the operation,
   elapsed milliseconds, stable error code, analyst-facing message, and validation
   path/reason; a success at debug level as `assistant_tool_succeeded` with the
   operation, elapsed milliseconds, and result key names. This is the diagnostic
   channel, deliberately separate from durable session history: that history redacts
   arguments and messages by design (see `_session.py`), which otherwise left a failed
   turn with no server-side record of why it failed. Tool arguments and result values
   are never logged.

   Before any dispatcher indexes an argument, the executor validates the
   complete JSON value against the operation descriptor's closed input schema,
   including required/unknown fields, discriminated operation variants,
   bounds, enums, hash patterns, JSON-serialisability, and finite numbers.
   For a closed object union whose branches expose a common `const`/`enum`
   discriminator such as `op` or `kind`, validation first selects that branch.
   A selected-branch failure therefore retains its exact safe schema path and stable
   value-free reason instead of becoming a generic union mismatch. These two fields are
   included in the structured error and durable redacted result, while the rejected value
   and free-form message are not persisted. A rejection additionally names what would
   satisfy it, because a stable reason alone is not correctable: `unknown_field` carries
   `unknown_fields` (the rejected keys) and `allowed_fields` (the closed allowlist), and
   `wrong_type` carries `expected_types` and the `received_type`, adding an explicit
   "send the value itself, not a JSON-encoded string of it" when a string arrived where an
   array or object was declared — the exact shape a gateway dialect produces, and one the
   model cannot infer from a bare type complaint. None of these are persisted, because
   `_session._persisted_message` copies exactly `code`, `validation_path`, and
   `validation_reason`. A rejected key is content the model itself submitted and is
   already in provider history, so naming it back is not new egress — and it is what
   makes the rejection correctable inside the turn's single retry, where
   "contains a field that is not allowed" was not. Malformed capability-descriptor
   queries return `invalid_capability_query`; other malformed known-tool calls return
   `invalid_request`. Neither path raises a `KeyError`, echoes any other rejected
   value, or invokes the operation.
6. Limits: a wall-clock deadline (`HAUTE_ASSISTANT_TURN_TIMEOUT`) checked around provider
   streaming and before each tool dispatch, and a per-turn tool-call cap
   (`HAUTE_ASSISTANT_MAX_TOOL_CALLS`). Hitting either aborts the provider stream and emits
   `failed` naming the limit. A non-mutating tool still running at the deadline is cancelled
   rather than drained; the interrupted call receives a matched, value-free
   `tool_interrupted` error in durable history so the timeout remains a real response bound
   without creating an orphaned provider call/result pair.
7. Cancellation: the client dropping the SSE connection cancels the generator. The provider
   stream is closed immediately; no further tool is dispatched; a save/publish already in
   flight is wrapped in `asyncio.shield` so the transactional write and its broadcast always
   complete as a pair — and, critically, on cancellation the loop **awaits the shielded
   task to completion while still holding `save_lock`** before re-raising (a bare
   `await shield(...)` re-raises immediately, which would release the lock mid-save and let
   another writer interleave; the implementation therefore uses the await-then-reraise
   form). The `finally` then releases the
   session lock. A `cancelled` terminal event
   is emitted if the transport is still writable, otherwise the turn simply ends (the
   invariant "exactly one terminal event" holds for every stream the client can still read).

**Provider adapters** (the exact SDK surfaces and event mappings):

- **Anthropic** — `client.messages.stream(model=…, system=…, messages=…, tools=…,
  max_tokens=<HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS>)`. Text deltas → `TextDelta`; a `tool_use` content block's
  `input_json_delta` fragments accumulate per block and emit one `ToolCallRequest` at the
  block's stop; the message stop reason (`end_turn` vs `tool_use`) → `TurnStop`; usage
  from the message-level usage events.
- **OpenAI and Databricks** — `client.chat.completions.create(model=…, messages=…, tools=…, stream=True,
  stream_options={"include_usage": True})` plus the output budget, whose parameter name is
  target-mapped: `max_completion_tokens` against api.openai.com (required by current OpenAI
  models), but `max_tokens` whenever `base_url` is set — the parameter Databricks' Chat
  Completions contract documents. Chat Completions, not the Responses API, deliberately: it
  is the OpenAI-compatible protocol Databricks model-serving endpoints implement.
  `DatabricksProvider` uses the URL derived by `_config`, attributes errors as
  `databricks`, and otherwise reuses this exact stream path; adapter tests assert the
  emitted request for both shapes. Databricks client construction disables the OpenAI
  SDK's internal retries. `DatabricksProvider` retries only a pre-stream SDK rate-limit
  exception, at most twice, after one and three seconds, against the identical
  model/endpoint request. Each retry is logged by provider identity and ordinal without
  the raw response. A failure after a stream object exists is never retried; exhausted
  rate limits retain the sanitized `databricks`/`rate_limit` failure.
  Before either provider request, every canonical tool
  input schema is projected to a portable wire schema with a sixteen-property budget per
  tool. It retains object/array shape, property names, descriptions, common required fields,
  single scalar types, enums, and closed-object declarations; nullable scalar unions project
  to their non-null generation type. For a composition of closed object branches that fits
  the remaining budget, the projection unions branch properties, combines discriminator
  constants into one enum, recursively projects a property present in one branch or
  identically declared across branches within the remaining budget, reduces conflicting
  property schemas to a common portable type, intersects required fields, and remains closed. A
  composition that does not fit remains a generic typed container. Patterns, ranges, and
  other unsupported validation vocabulary are omitted. `_tools` independently validates
  the decoded call against the unchanged canonical operation schema, so the projection is a
  generation contract rather than an authorization or validation fallback. After the outer
  function-arguments object is parsed,
  Databricks alone performs one schema-directed compatibility pass over its top-level
  fields: when the advertised input schema declares a field as `array` or `object` but
  the provider returned a string, valid finite JSON with the declared container type is
  decoded and then proceeds through the unchanged closed tool validator. Invalid JSON or a
  decoded scalar/wrong container remains the original string; the canonical validator
  returns `invalid_request`, nothing executes, and the model can retry in the same turn.
  Declared string fields, nested values already carried inside a decoded container, unknown
  tools, OpenAI, and Anthropic are never coerced. This deliberately avoids blanket recursive
  JSON coercion or speculative repair while handling the live
  Databricks/Qwen function-calling dialect captured on 2026-07-30. An eligible field that
  is *not* decoded is logged as a warning by shape only —
  `assistant_databricks_argument_decode_failed` (declared types, encoded length, and
  whether the string opens like a JSON container) when the string is not valid JSON, and
  `assistant_databricks_argument_decoded_wrong_type` (declared types and decoded type)
  when it decodes to the wrong container. Neither logs the value. Such a field will
  certainly fail canonical validation, and durable history redacts arguments, so without
  this an operator cannot tell an undecoded gateway dialect apart from a model that
  composed the wrong argument. `delta.content` →
  `TextDelta`, accepting both the api.openai.com dialect (a plain string) and the
  OpenAI-compatible-gateway dialect for Anthropic models (a list of typed content parts,
  as Databricks Foundation Model APIs stream for Claude): `text` parts yield `TextDelta`s,
  `reasoning` parts (thinking summaries) are deliberately not surfaced — the chat has no
  thinking channel — and any other part type or shape raises a typed `malformed_stream`
  failure. Each raw chunk's structure (content kinds, tool-call counts, finish reasons,
  usage placement — never text values or tool arguments) is logged at debug level as
  `assistant_openai_chunk_shape`, so an operator can capture a gateway's wire dialect from a
  live stream with `HAUTE_LOG_LEVEL=DEBUG`. End-of-stream contract: a `finish_reason` is
  normally required, but Databricks intermittently omits it from a complete reply's final
  text chunk (captured live 2026-07-19), so a clean stream end without one is accepted as a
  natural stop **only** when no half-delivered tool call is pending, text was actually
  streamed, per-chunk usage was observed, and the output stayed under the token budget —
  logged as a `assistant_openai_stream_missing_finish` warning; a pending tool fragment or a
  missing-usage/empty stream raises `malformed_stream`, and an at-budget end raises the
  typed `truncated` failure; `delta.tool_calls[*]` argument fragments accumulate per call index/id and
  emit `ToolCallRequest`s when `finish_reason == "tool_calls"`; `finish_reason == "stop"` →
  `TurnStop`; usage from the final chunk.
- Usage is **summed across the provider round-trips within one turn**; the `completed`
  event reports the aggregate.
- SDK floors are core project dependencies, not an optional extra:
  `anthropic>=0.40` and `openai>=1.55`. The adapters still import the SDKs lazily and
  readiness reports a missing SDK as a broken installation.

## Edge cases and invariants

- **Ops apply in order against the evolving graph** — `add_node` followed by `add_edge`
  addressing the new node via `$ref` within one batch is valid and covered by tests.
- **Revision checks and the write are one critical section.** Dry-run is serialized
  with saves while it captures the exact saved sources. Apply reloads and verifies
  those sources, replays the exact plan, saves, reparses, verifies, and publishes
  under the process-wide `save_lock`. A GUI save before apply therefore produces
  `stale_revision`; a GUI save after apply is a distinct later save.
- **A batch is all-or-nothing**: op validation failures abort before the save; a mid-save
  failure rolls back every staged file (the save service's existing `_TouchedFile`
  snapshot/rollback); in both cases the pipeline on disk is exactly what it was.
- **Aborted plans require fresh validation**: a pre-commit exception moves the
  acquired plan from `applying` to `aborted`; applying that hash again returns
  `plan_aborted`. Repeating the identical dry-run replaces the aborted record
  after full validation, allowing a deliberate retry without minting a
  different hash or weakening the one-use rule for applied plans.
- **New assistant-authored nodes are connected**: after the complete ordered
  batch is replayed, every surviving node created by an `add_node` operation,
  tracked through later batch-local renames and deletes, must be incident to at
  least one final edge. The check does not reject unrelated edits merely
  because an already-saved node is disconnected.
- **Polars results must be retained**: non-empty explicit code on a `polars`
  node must parse as Python and contain a non-trivial assignment to `df` or an
  explicit non-trivial return. Bare immutable expressions and `df = df` are
  rejected because generated node code would otherwise discard their result.
  Empty code remains the canonical pass-through.
- **Assistant-authored multi-input code names the input it starts from.** Both the
  executor and the generated module bind a bare `df` to the node's *first input by
  edge order*. On a single-input node that is the idiom and stays unambiguous. On a
  node with two or more inputs it is a silent dependency on wiring: adding or
  reordering an edge changes which frame the code operates on, with no error and no
  visible change to the code. Assistant-authored code that reads `df` before assigning
  it, on a node with two or more inputs, is therefore an op error naming that node's
  actual input names. Binding first (`df = proposer_claims`) and then reusing `df` is
  explicit and accepted. Only reads that resolve to the injected binding count, so the
  check skips any nested scope holding a `df` of its own — a `def helper(df)` parameter,
  a `lambda df:`, a comprehension target — because those name that scope's variable and
  say nothing about wiring order. Bindings inside a further nested scope do not shadow
  `df` in the enclosing function. A comprehension's first iterable is evaluated before
  its target is bound, so a bare `df` read there still resolves to the injected input.
  A statement's loads are judged before its stores, since
  an assignment evaluates its value first: `df = df.head()` reads the injected frame,
  `df = left` does not. This is an authoring-time rule for assistant edits only, like
  the unknown-config-key strictness above; existing human-authored code is untouched.
- **Unknown config keys are op errors, not warn-and-drop.** The sidecar writer's
  warn-and-drop exists to tolerate stale keys already on disk; an authoring-time unknown key
  is an LLM mistake that must bounce back as a tool error so the model corrects it. Same
  allowlist source, different strictness, both deliberate.
- **The assistant configuration is closed.** The outer accepted key set is
  exactly `provider`, `model`, `base_url`, and `egress`; the nested table has
  exactly the five required ASSIST-A07 fields. Unknown or missing fields fail
  with their full TOML path. A non-string `base_url` raises `ConfigError`, any
  `base_url` on Anthropic or Databricks is a not-ready reason, and OpenAI accepts only an
  absolute credential-free HTTP(S) URL with a hostname and valid port.
  Databricks requires `DATABRICKS_HOST` to be an absolute credential-free
  HTTPS workspace-root URL with no query, fragment, or non-root path, strips
  only a trailing slash, derives `/serving-endpoints`, and reads only
  `DATABRICKS_TOKEN` for authentication.
- **Provider compatibility is schema-directed and fail-closed.** Every provider receives
  the same portable wire-schema projection while the ordinary tool validator retains the
  complete canonical schema. Only the Databricks adapter may decode a stringified top-level
  tool argument, only when that field's advertised schema exclusively declares one or more
  compatible JSON types from `object`, `array`, `boolean`, `integer`, and `number`, and only
  when the decoded finite JSON has a declared type. Python booleans do not satisfy integer
  or number declarations. String and null declarations, undeclared types, ambiguous schemas,
  non-finite numbers, and nested string values are not decoded. A value that cannot be
  decoded safely is preserved solely so canonical validation can reject it as a recoverable
  tool result; it is never passed to an operation.
- **Submodel boundaries**: ops may only target top-level nodes; `add_node` of
  `submodel`/`submodelPort` types and any op addressing a node inside a submodel graph
  return named tool errors (v1 limitation, stated in the error text).
- **Singletons, name collisions, reserved filenames** are enforced by the save service's
  existing validation — the assistant adds no duplicate checks and inherits any future ones.
- **Position rule** (deterministic, no randomness, evaluated after the whole batch has
  applied so parent-based placement sees final wiring): a new node lands one horizontal step
  right of its rightmost parent (fallback: right of the graph's rightmost node; empty graph:
  origin), vertically staggered by sibling index. Nothing else moves; analysts rearrange
  freely afterwards.
- **Mutation precondition**: `apply_graph_plan` requires `working_branch_status(...)` to
  report `"ready"` — the state in which the save service ledger-captures — so every assistant
  edit is *expected* to be captured. If capture still fails after a successful save (the
  service's documented degrade-to-warning path), the warning propagates into the tool
  result and the chat activity row — never swallowed — and, per the service's own design,
  the next successful capture sweeps the orphaned delta up from working-tree state. Read
  tools carry no precondition.
- **The `graph_updated` fingerprint is the post-save re-parse fingerprint** — the same value
  `/ws/sync` clients receive, so the frontend can correlate the chat event with the canvas
  update.
- **One `GraphUpdatePayload` contract**: the assistant publishes the identical payload shape
  the watcher publishes; no assistant-specific frame type exists on `/ws/sync`.
- **Bounded retention, turn-atomic**: the provider request carries the most recent
  complete turns within a 40-message budget plus the always-complete system prompt; stored
  history caps at 200 messages by evicting whole oldest turns. No pruning boundary ever
  separates an assistant tool call from its result (an orphaned half is an invalid provider
  conversation). Live sessions are LRU-capped at 32 with least-recently-used *idle*
  eviction — a session holding a running turn is never evicted, and eviction drops only
  the in-memory record: the persisted file revives the id transparently on next lookup.
- **Dataset discovery and schema inspection share one safety contract**: installed readable path
  extensions come from `routes.files._installed_input_extensions()` and are matched by
  case-folded filename suffix (including compound extensions). The resolved project-relative
  path must contain no hidden component; exact state/credential names in the assistant
  denylist are rejected before listing or reading. Direct calls cannot bypass the filter
  that navigation applies. `list_datasets(project_root, recursive)` defaults to a one-level
  listing; recursive mode walks only non-symlink descendants, returns deterministic
  project-relative POSIX paths, caps datasets and directories independently, and sets
  `truncated=true` when either cap or the traversal bound is reached. For a routed
  `parquet_showcase`, a safe folder explicitly named in the effective authoring request is
  advertised as the provider schema's constant `project_root`; before canonical validation,
  the source-bound executor replaces any model-supplied listing root and recursion value with
  those routed constants. The assistant schema helper is separate from the UI
  schema/preview reader and never invokes the preview collector.
- **The read shape names handles the way the write shape does.** The compact graph
  renderer emits each edge's ports as `source_handle`/`target_handle`, matching the
  `add_edge`/`delete_edge` operation fields exactly. The camel-case `sourceHandle`
  spelling remains the persisted wire detail of the graph edge model and the frontend
  payload, and is never shown to the model: echoing it invited edit operations written
  in the shape the model had just read, which the closed operation schema then rejected
  as an unknown field. Every in-repo reader of that rendering follows the same names —
  including `_self_test._read_graph`, whose scoring compares Edge Join `base`/`join`
  roles. A reader left on the persisted spelling gets `None` for every edge without
  raising, silently scoring every handle-qualified required edge as missing.
- **Egress flags are honoured, not merely recorded.** `allow_executable_source` and
  `allow_row_samples` each gate a real capability: node `code`/`preamble`/`query`/`script`
  values in `get_node_config`, and `get_column_profiles` respectively. A parsed,
  validated, reported flag that no code path consults is worse than no flag, because a
  project reads its own configuration as a grant that silently never applies.
  Credential keys and inline row-value keys stay redacted under every policy.
- **`get_node_schema` collects nothing** — the invariant is testable: the tool's plan
  construction plus `collect_schema()` must never invoke `LazyFrame.collect` (asserted by
  poisoning `collect` in tests). The two honest cost exceptions are inherited, not assistant
  behaviour: plain-`.json` sources parse eagerly inside `read_source` (the GUI's
  `/api/schema` pays the same), and a never-fetched Databricks table raises
  `CacheNotFoundError` instead of ever reaching for credentials or the network.
- **`get_node_schema` sees the graph the engine runs, not the graph the editor draws** —
  flattened, preamble-compiled, active-source-selected (the step-5 sequence). A node
  downstream of a submodel therefore resolves through the submodel's real internals, a
  live-switch resolves to the saved active source, and preamble-defined helpers are in
  scope; the submodel placeholder itself is not addressable (structured error, v1
  boundary). Multi-frame sources report per-port schemas keyed by port name.
- **Session invariants**: an id unknown to both memory and disk → 404 on message send,
  never auto-created; one turn per session via the per-session lock; committed turns
  persist to `.haute/assistant/sessions/` and survive restarts, while a truly lost id
  (pruned, corrupt file, cleaned `.haute/`) still 404s and the frontend renders that
  explicitly by starting a fresh session. A `controller` role is internal provider history:
  adapters encode it as a user instruction, it does not count as the turn's single real user
  message, and transcript projection never exposes it. Current tool-role records require an
  explicit boolean `is_error`; missing values are invalid session data rather
  than inferred from an obsolete content shape. Tool-role messages retain `is_error` through
  validation, JSON persistence, revival, history-window rendering, and both provider
  adapters. Persisted tool arguments/results carry no deterministic digest. Tool errors may
  retain only `code`, `validation_path`, and `validation_reason`; paths/reasons are
  produced by trusted schemas and never contain submitted values. Turn and response cleanup
  release their idempotent reservation in nested
  `finally` blocks even if history append or iterator close raises.
- **No `print`, structlog only** — the assistant package and router are swept by the existing
  decoupling and routes-hygiene contract tests.

## Error handling

| Failure | Where raised | Surfaced as |
|---|---|---|
| Malformed `haute.toml`, unknown assistant/egress key, missing or invalid egress policy, invalid OpenAI `base_url`, invalid/missing `DATABRICKS_HOST`, or conflicting Databricks `base_url` | `_config`, before SDK probing/client construction | `ConfigError` or not-ready reason naming the field/environment variable (never its value) → 400 |
| Not configured / provider SDK missing | `_config` via route pre-check | 400 with the readiness reason verbatim |
| Unknown session | route | 404 |
| Turn already running on session | route (lock try-acquire) | 409 |
| Working branch not `"ready"` (`working_branch_status`) | `_tools` mutation pre-check | Structured tool error carrying the mapped per-state reason; status reports `mutations_enabled: false` with the same reason |
| Ledger capture fails after a committed save | save service (degrade-to-warning path) | Warning propagated into the tool result and activity row; next successful capture sweeps the delta |
| Provider adapter construction/dependency failure | route provider factory | HTTP 502 before the stream opens |
| Provider request/stream failures (authentication, rate limit, connection, malformed/truncated/filtered output) | `_providers` | `AssistantProviderError` → terminal `failed` SSE event after the response has started |
| Op validation, save validation, missing dataset, unknown node, unknown example name, unresolvable node schema (unfetched Databricks cache, missing artifact, invalid node code) | `_tools`/`_ops`/`_assets`/engine/save service | Structured tool error returned to the model (visible as a failed activity row); never terminates the turn |
| Unexpected exception inside `dry_run_graph_edits` | `_tools` tool boundary | `operation_failed`, never `invalid_plan`. `invalid_plan` is a specific authorization verdict the domain layer raises; reusing it as the catch-all told the model its plan had been judged and rejected when nothing had judged it |
| Turn timeout / tool-call cap | `_loop` | Terminal `failed` event naming the limit |
| Any unexpected exception in the loop | `_loop` outermost handler | Logged server-side with `exc_info=True`; terminal `failed` event carrying the sanitized `_INTERNAL_ERROR_DETAIL` text only |
| Broadcast subscriber failures | event bus | Isolated and logged by `EventBus.publish` (existing behaviour); never fails the committed save |

The stream invariant: every response the client can still read ends with exactly one
terminal event (`completed`, `failed`, or `cancelled`); a save/publish pair is never
abandoned half-done (cancellation-shielded); persisted tool calls are always paired with
results; the session lock is always released even when history append or response close
raises. The
release is owned by an idempotent turn reservation with two independent paths — the
loop's `finally` and the streaming response's own lifecycle — so even a client that
disconnects before the body iterator ever starts cannot leave the session locked
(the route reserves atomically before its awaited pre-work, which is also what makes
the concurrent-send 409 a pre-stream decision).

## Testing

Flat files under `tests/` per repo convention (`asyncio_mode = "auto"`; shared `client`
fixture for route tests). The implemented coverage is:

- **`tests/test_assistant_ops.py`** — every op's happy path and rejection paths; op ordering
  within a batch (add then connect via `$ref`); ref resolution (unknown ref, duplicate ref,
  ref shadowing an existing id); all-or-nothing on mid-batch validation failure;
  shallow-merge/null-removes semantics; unknown-config-key rejection; submodel-target and
  submodel-type rejection; ambiguous edge match; deterministic positions evaluated
  post-batch (property: same batch, same graph → same positions); and the multi-input
  bare-`df` rule, parameterised over a bare read, a named input, a bind-then-reuse, and
  the nested scopes (`def`, `lambda`, comprehension) whose own `df` must not be mistaken
  for the injected one.
  ASSIST-A05 adds canonical revision/plan hashing, semantic diff boundaries,
  closed postconditions, single-use plan transitions,
  stale/altered-plan rejection before save,
  unrelated-diff detection, and truthful verification evidence.
- **`tests/test_assistant_catalog.py`** — completeness against `NodeType` (mirror of the
  registry-completeness test); folder/decorator facts agree with `_types`/`_config_io`.
  ASSIST-A04 additionally pins resolved schema agreement, closed operation
  metadata, deterministic canonical hashing, cache reuse/invalidation,
  manifest compatibility identity, and the compact/full projection boundary.
- **`tests/test_assistant_tools.py`** — real tmp-project coverage for source/downstream
  schemas, preamble-dependent transforms, per-input schemas keyed by the code-visible
  input name (absent for a source node), the authored-but-empty transform's
  `node_has_no_code` success shape with resolved inputs, a group-by node resolving
  because nothing is collected, an invalid-code failure retaining the engine's own
  column diagnosis, and the collect-poisoning invariant
  (`LazyFrame.collect` must not run). Value-profile coverage pins small-cardinality levels
  with counts, high-cardinality withholding, numeric bounds rather than values, an unknown
  input naming the available ones, and the `allow_row_samples` gate. A dtype-matrix
  fixture — dates, datetimes, decimals, a non-finite float, and a column named `count` —
  is profiled *through the source-bound executor*, because the bounding encoder is what
  rejects a value the direct call happily returns; alongside it, the rendered form of each
  bound, and an unsummarisable column withholding only itself. Executable-config
  coverage pins `code` visible or redacted strictly by `allow_executable_source`.
  Contract tests assert that the saved `active_source`
  is passed to the engine; crafted/mocked execution results cover submodel-boundary
  rejection, multi-frame per-port shaping, unknown-node errors, and propagation of an
  unfetched-Databricks `CacheNotFoundError` message as a structured tool error. Capability
  tests pin ordered one-to-twelve descriptor batches, duplicate/unknown rejection, and JSON
  materialisation. Discriminated-union failures pin precise validation paths and stable
  value-free reasons without invoking an operation; an `unknown_field` rejection pins the
  named rejected keys and closed allowlist, and a `wrong_type` rejection pins the expected
  and received JSON types for both a stringified container and a lone object sent where a
  batch was declared. The rendered graph is asserted to
  name edge handles in the operation vocabulary. Dataset
  coverage pins installed-registry extension parity and rejects direct hidden,
  state-directory, and credential-file listing/schema inspection; preview
  collection is poisoned to enforce the no-row boundary.
- **`tests/test_assistant_assets.py`** — the authoring guide loads non-empty via
  `importlib.resources`; every legacy exemplar and content-addressed bundle
  parses through `parse_pipeline_to_graph`; bundle manifests are closed,
  inventories reject unknown roles, missing/digest-mismatched/undeclared
  files, expected-schema/assertion drift, and missing required artifact
  classes; the declared fast subset executes against synthetic data.
  `load_example` returns bounded attribution, narrative, and the same graph shape as live
  inspection; resource inventory names and paths are absent from the model-facing result.
- **`tests/test_assistant_recipes.py`** — closed descriptor completeness,
  deterministic planning, unresolved-decision handling, primitive-operation
  validation, linked examples, preconditions, and stable recipe failures.
- **`tests/test_assistant_application.py`** — saved-state inspection, exact
  no-write planning, single-use authority, stale-state rejection,
  transactional apply, semantic-diff verification, postconditions, and
  committed verification-failure reporting. Schema-validation scope is pinned against a
  fixture whose saved pipeline already contains one unresolvable node: an authored
  group-by validates at schema tier, a new branch off a shared input does not drag that
  input's other branches into validation, untouched collateral that already failed
  becomes a `pre_existing_schema_failure` warning at structural tier, and a node the
  plan itself changed is never excused.
- **`tests/test_assistant_project_knowledge.py`** — source attribution,
  sensitivity filtering, cache invalidation/rebuild, bounded queries, tool
  policy, symlink containment, and metadata-only durable cache state.
- **`tests/test_assistant_evaluation.py`** — held-out/teaching separation,
  closed support matrices, semantic and zero-tolerance safety scoring,
  repeated-trial attribution, aggregation, redacted reports, and fail-closed
  qualification decisions.
- **`tests/test_assistant_self_test.py`** — closed prompt-case loading and
  selection plus a synthetic portfolio covering continuous and categorical banding,
  exact join roles, positional rating steps, Polars transforms, explicit mapped response
  outputs, file input/output graph authoring without sink execution, broad parquet showcase
  construction, material clarification for joins/rating/output mappings, prompt injection,
  and blocked pipeline execution/external writes. Loop-quality scoring requires a successful
  terminal outcome with bounded failed tool attempts and duplicate static reads, graph
  connectivity, and exact edge-join base/join port assertions. A null expected target handle
  matches an edge by source/target endpoints for ordinary single-input nodes; non-null handles
  remain exact port assertions. `_read_graph` is pinned directly against the compact
  renderer's own output, because reading a handle under a name the renderer does not emit
  yields `None` without raising and turns every port assertion into a silent pass-through
  failure. For multi-round text, scoring uses the last explicit
  `NEEDS_INPUT:` or `BLOCKED:` marker, so earlier preparatory prose cannot hide the final
  qualified outcome. Coverage also pins the redacted report shape and a
  scripted-provider integration through the real loop, tools, dry-run, apply, parser, and
  disposable Git mutation gate.
- **`tests/test_assistant_example_portfolio.py`** — live/batch parity, trace
  and structural dry-run, real model training/scoring, real online/ratebook
  optimisation, saved versioned apply, deployment preflight plus unsafe
  configuration rejection, and adversarial content/operation handling.
- **`tests/test_assistant_config.py`** — readiness matrix (absent table, unknown provider,
  missing model, missing key, missing SDK, fully configured); malformed TOML raises;
  unknown keys name their TOML path; OpenAI `base_url` accepts absolute
  credential-free HTTP(S) URLs and rejects malformed/relative/unsupported-
  scheme/userinfo/invalid-port values without exposing them; `base_url` is
  rejected for anthropic and Databricks; Databricks derives its serving URL
  from a validated `DATABRICKS_HOST`, reads `DATABRICKS_TOKEN`, and fails
  loudly/redacted for missing or malformed values; `max_output_tokens` unset-defaults-to-8192 and
  malformed/non-positive-fails-readiness behaviour (named reason, no silent default);
  `mutations_enabled`/`mutations_reason` across all six `working_branch_status` states
  (ready, no-repository, unset, detached, divergent, invalid — asserting each state's
  mapped reason, including invalid's joined `errors`).
- **`tests/test_assistant_providers.py`** — adapters normalise scripted fake SDK streams to
  `ProviderEvent`s; SDK exception classes map to `AssistantProviderError` variants; lazy
  import failure produces the readiness reason, not an ImportError at server start; the
  OpenAI content-delta dialects (plain string, and gateway content-part lists where `text`
  parts stream, `reasoning` parts stay unsurfaced, and unknown part types or non-text
  shapes raise `malformed_stream`); the Databricks adapter reuses the
  OpenAI-compatible request while preserving `databricks` failure attribution; all three
  adapters advertise the same budgeted portable wire schemas, including merged
  discriminated graph-operation fields and discriminator enums; the Databricks adapter decodes valid
  schema-declared top-level array, object, boolean, integer, and finite-number strings from
  the live dialect, leaves declared strings, nulls, nested values, and the other adapters
  untouched, carries invalid/wrong-type encodings to the canonical validator for a
  recoverable `invalid_request` result, and logs each undecoded eligible field by shape
  alone — asserting both warning events and that the rejected value never reaches the log.
- **`tests/test_assistant_loop.py`** — against a scripted fake provider: text-only turn;
  tool round-trip; tool error fed back; cap and timeout terminal events; completed/failed
  exactly-one-terminal checks; cancellation drains an in-flight tool, closes the provider
  stream, and releases the session lock; closing at each tool lifecycle yield never
  persists an unmatched call; a raising history append still releases the lock;
  turn-atomic history windowing, including a tool-heavy turn crossing both caps without
  splitting a call/result group; the two dry-run budgets are independent and each
  bounded — a malformed call does not consume a plan-correction attempt, and repeated
  malformed calls block with their own wording and no echoed detail; an end after
  unsuccessful mutation receives one internal
  controller continuation, successful apply terminates with deterministic text and no later
  provider/tool round, explicit `NEEDS_INPUT:`/`BLOCKED:` outcomes terminate normally, and a
  second unqualified end fails rather than completes.
- **`tests/test_assistant_routes.py`** — status/sessions/session/message endpoints: SSE framing,
  400/404/409 mapping, sanitized unexpected-error paths, readiness reasons on status,
  transcript rehydration, adapter construction, atomic concurrent-send reservation, and
  lock release on pre-stream failure, disconnect, and mid-stream send failure. The list
  endpoint pins this pipeline's conversations with their titles and counts, an empty
  project, and the unknown-pipeline 404.
- **`tests/test_assistant_session_persistence.py`** — atomic write-through persistence,
  restart revival, invisible LRU eviction, corrupt/invalid-file logged misses, session-id
  path hardening, oldest-first persisted-file pruning, abandoned temp-file cleanup,
  tool-error round-trip, internal-controller revival/transcript hiding, absence of
  deterministic payload digests, safe validation path/reason retention, and non-fatal
  persist failures. Listing coverage pins recency ordering and per-pipeline scoping,
  omission of empty conversations, titles bounded and whitespace-collapsed, survival of a
  restart without reviving any session into memory, and an unreadable file skipped with a
  warning. A syntactically valid file that fails the same id, timestamp, source, or history
  validation used by revival is skipped too; listing never advertises a conversation that
  cannot subsequently be opened.
- **`tests/test_assistant_integration.py`** — fake-provider end-to-end on a tmp project:
  instruction → ops → real transactional save (files on disk assert codegen/sidecars) →
  `graph.update` published with the post-save fingerprint (asserted via a test subscriber)
  → a second turn reads its own edit back; a pipeline containing preserve markers survives
  an assistant edit byte-identically outside the edited region; the mutation precondition
  (non-ready working-branch state → tool error carrying the git reason, nothing written);
  `save_lock` exclusivity — a concurrent GUI-style save cannot interleave inside an assistant
  mutation's critical section; cancellation during a slow save leaves the lock held until
  the shielded save has landed and then releases it;
  a degraded ledger capture surfaces its warning in the tool result.
- **`tests/test_save_pipeline_integrity.py`** includes a regression pinning the
  preserve-marker round-trip through
  `save_graph_transactionally` (parse a marker-bearing pipeline → transactional save →
  markers and content survive on disk), independent of which layer supplies the blocks.
No automated test calls a live Anthropic, OpenAI, or Databricks-compatible endpoint; provider
wire behaviour is exercised with scripted SDK streams. `scripts/run_assistant_self_test.py`
is the explicit credentialed developer lane: it loads the same project `.env` and
`[assistant]` configuration as the app, accepts repeated `--case` selection (plus `--list`),
runs each selected prompt once in an isolated fixture. Entering and leaving a fixture clears
the process-cached active pipeline directory so one project or the invoking repository cannot
escape into another fixture's application service. The lane exits non-zero on any failed case and
optionally writes the redacted report described above. Cases may expose only synthetic schemas,
the synthetic request, and ordinary assistant tool context to the configured endpoint. The lane
never executes the authored pipeline or materialises a configured output sink. It is a fast
diagnostic and regression loop, not release qualification; repeated support-matrix trials remain
the authority for model qualification. The package is covered by the repository's global branch
gate, while exemplar
`.py` assets are omitted from coverage because they are parsed package data rather than
importable modules (they remain parser- and lint-checked).
