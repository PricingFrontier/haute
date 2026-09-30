# Assistant — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/assistant/__init__.py` | Public package seam; re-exports only `assistant_readiness`. The FastAPI router remains in the routes package and is not re-exported here. |
| `src/haute/assistant/_config.py` | Resolves assistant configuration: the outer `[assistant]` table is closed to provider/model/base URL/egress and the required nested `[assistant.egress]` table is closed to trust, maximum sensitivity, and the three `allow_*` booleans. It validates endpoint/trust combinations and credential-free OpenAI URLs before SDK/key probing. The first-class Databricks mode rejects `base_url`, reads DATABRICKS_HOST / DATABRICKS_TOKEN, validates a credential-free HTTPS workspace-root host, and derives `<host>/serving-endpoints`. Other credentials come from their named environment variables. It produces `AssistantConfig`/`AssistantReadiness` including safe endpoint host, trust, and sensitivity status. |
| `src/haute/assistant/_catalog.py` | The versioned capability registry, the assistant's one node catalogue. It derives mechanical node facts and resolved JSON Schemas from Haute's canonical types, config validation, config I/O, node registry, Polars I/O registry, and save validation; owns completeness-checked semantic metadata; declares the closed operation descriptors consumed by the tool layer; serves each node type's card from `_node_cards.py` as its descriptor's `card`, and at import refuses a card configuration whose keys fall outside that type's closed config schema; computes canonical manifest identity; and caches immutable manifests by installed version plus capability hash. |
| `src/haute/assistant/_node_cards.py` | Loads and validates the packaged node cards (read via `importlib.resources`, cached). Exactly one card per `NodeType` must exist, each a closed JSON object: an authorable card has `fields` (config path to meaning), `inputs` (edge, optional source and target handles, column-to-dtype map), `configs` (exactly `minimal` then `realistic`, each with `intent`, `config`, `produces` and optionally its own `inputs`) and a `fixture`; a card for a type the assistant cannot author has only `authorable: false` and a `note`. A missing, unexpected or malformed card raises `NodeCardError`. `node_card(node_type)` returns the model-facing card without its fixture; `node_card_fixture(node_type)` returns the fixture for the CI harness, or nothing for a non-authorable card. |
| `src/haute/assistant/assets/node_cards/<nodeType>.json` | One node card per node type, library content with no project data. Its closed `fixture` holds `frames` (tiny rows, with `dates` naming string columns to parse as Date, each written to `data/<name>.parquet` and added as a Data Input only when the plan reads it), `files` (project paths to JSON values or text), `upstream` and `downstream` operations placed around the card node, `model_run` (the frame and features of a tiny CatBoost model logged to the project's local MLflow folder, whose run id replaces the card's `run_id` placeholder), and `expect` (per-configuration expected rows of produced columns). |
| `src/haute/assistant/_assets.py` | Loader and verifier for the assistant's packaged knowledge assets (read via `importlib.resources`): resource enumeration, `authoring_guide()`, and `example_index()` are cached; `example_index()` lists only bundles whose manifest sets `teaching: true`, and `load_example(name)` refuses any other name as an unknown example; for a teaching bundle it materialises the complete example tree for validation, then returns only a self-contained model-facing attribution/narrative/rendered-graph view, its node configurations rendered with their values, with no inaccessible resource inventory. `validate_example_bundles()` checks closed manifests, content digests, evidence-resource roles, graph/schema assertions, positional golden output, and the declared installed-package fast checks; `materialize_example_bundle()` copies one already-validated project into an empty destination for specialist ordinary/negative checks. The guide fails loudly if missing/empty, index summaries come from the first module-docstring line, and an unknown name is a structured error listing valid names. |
| `src/haute/assistant/_recipes.py` | Versioned immutable recipe registry and deterministic `plan_recipe` dispatcher. Each descriptor has a closed argument schema, unresolved decisions, preconditions, allowed primitive operation kinds, postconditions, linked example bundles, and stable failures. Explanation-only requests do not route to mutations, while a later explicit sequenced authoring clause retains mutation intent. Planner output is round-tripped through `_wire_ops.parse_ops`; unknown recipes and invalid arguments fail by stable code. |
| `src/haute/assistant/_project_knowledge.py` | Source-linked project-knowledge extraction, bounded query selection, and disposable content-addressed index. Derives a saved-graph fact, a value-free `haute.toml` digest fact, and allowlisted UTF-8 documentation evidence; labels unmarked document sensitivity as restricted; records source digest/extraction version/evidence class; filters by `EgressPolicy`; and atomically refreshes metadata-only cache state under `.haute/assistant/knowledge/`. An allowlisted document that is not valid UTF-8 fails the read with a typed, project-relative error instead of silently disappearing. Dataset schemas remain a separate schema-only tool result. |
| `scripts/run_assistant_evaluation.py` | The development-only qualification harness and its command (it is not part of the installed package): closed evaluation scenario/support-matrix loaders, semantic/safety scorer, repeated-trial attribution, percentile aggregation, and release-gate evaluator. The runner is injected so deterministic tests use fakes and the live-provider lane uses real provider adapters in isolated projects. It never imports or exposes held-out fixtures through production tools. |
| `scripts/run_assistant_self_test.py` | Developer-facing evaluation harness, outside the installed package, for tiers 0 and 1 of [the assistant evaluation](evaluation.md), and its explicit credentialed command. It loads the closed case format (`load_self_test_cases`) and reference trajectories (`load_trajectories`), copies each case's project fixture into a caller-supplied empty work directory, binds the sandbox project root to that copy for the case and restores it afterwards, writes the harness's pinned egress allowances (not the invoking project's) into the copy, initializes the real Git mutation gate, and runs the provider-neutral loop with the real tool executor (`run_self_test_case`). `TrajectoryProvider` and `replay_self_test_case` replay a trajectory with `$result` substitution and raise `TrajectoryDivergedError` when a tool result's status or error code differs from the recording. After the turn the harness executes the case's golden nodes (`execute_goldens`) and `score_self_test` scores the protocol, structure, configuration, collateral, editor and execution layers; `frames_equal` is the one frame comparison and `config_digest` the per-node configuration digest. The command runs each selected case live in its own spawned process. Reports hold one evidence kind and may retain ordered tool names plus value-free status/error code/validation path/validation reason diagnostics so failed model strategies are actionable. They otherwise record only redacted identities, outcomes, per-layer results, reasons, graph structure, and aggregate metrics; prompts, model prose, tool arguments/results, credentials, dataset values, canary values, and content digests are not written to reports. |
| `src/haute/assistant/assets/examples/<id>/manifest.json` | Closed executable-bundle manifest (`schema_version=1`, stable id/version, summary, source, `fast`/`ordinary`/`negative` assertion tier, required `engineering`/`pricing` review class, required boolean `teaching`, and a closed-role resource inventory). `teaching: false` marks a test fixture that is validated and materialised but never offered to the model: `deployment_safety` and `invalid_adversarial` are the test fixtures, every other bundle teaches. Review class records the required discipline rather than asserting approval; model-validation and optimisation fixtures use `pricing`, while purely mechanical fixtures use `engineering`. Every bundle includes its project configuration, source, synthetic input, graph/schema expectations, golden request/output, boundary cases, paired prompts, and semantic assertions. Assertion files have only `target`, non-empty `required_columns`, optional `row_count`, and a non-empty closed `checks` list. Golden arrays retain production row order, so order-unstable operators are followed by an explicit stable pipeline sort rather than normalized by the verifier. Every declared resource resolves inside its bundle, exists, and matches its recorded SHA-256 digest. |
| `src/haute/assistant/assets/authoring_guide.md` | Packaged, hand-authored Haute idiom: canonical pipeline shapes written with vectorised Polars expressions, the per-surface `df` rule the system prompt states, what `haute init` scaffolds (a blank pipeline), naming and stage-chaining conventions, and do/don't guidance returned with source/version/digest/evidence attribution by the authoring-guide tool; it is not embedded in every system prompt. |
| `src/haute/assistant/assets/examples/<id>/pipeline.py` | Every example is a bundle; there is no other example format. The bundle source is parsed as data by `_assets.py`, never imported, and rendered in the same compact graph shape as the get-pipeline tool, each node's configuration carried with its values; its module docstring supplies the narrative and the index summary. `linear_pricing` teaches implicit wiring through a source, one Polars enrichment and an output; `branched_features` teaches parallel feature branches joined before the response with explicit connections. `model_lifecycle` ends its training branch at Model Training and feeds the response from Model Score; `online_scenario_optimisation` ends at the Optimisation node and feeds the response from the scored scenario frame; `ratebook_optimisation_apply` bands the raw rating column through a Banding node that is both the optimiser's `banding_source` and Apply Optimisation's ratebook input; `reusable_submodel` names its occurrence `enrichment`, apart from the `enriched` node inside its definition. A request for the removed `joined_reference` example is refused with `example_removed`, naming `reference_join`, which teaches the same edge join. |
| `src/haute/assistant/_wire_ops.py` | Closed provider-wire graph-edit models plus graph-independent `parse_ops` validation. It imports no assistant modules, so recipes, the capability catalogue, and the graph domain layer share one operation vocabulary without lazy imports or dependency cycles. |
| `src/haute/assistant/_ops.py` | Pure graph-edit domain layer, re-exporting the wire vocabulary for its existing public seam: ordered graph application, assistant-authoring validation (including connected new nodes and retained Polars results), canonical snapshot/revision and semantic-diff functions, typed plan models, deterministic verification policy, postcondition evaluation, and the bounded single-use `PlanStore`. `_evidence_manifest_entry` names the project-relative file in its missing-source and stale-evidence messages, with the tool call that refreshes it (listing datasets for a vanished dataset, reading a changed dataset's schema, querying project knowledge for a document). It performs no writes. |
| `src/haute/assistant/_render.py` | Shared compact graph renderer for live pipelines and packaged examples. It emits bounded node/config summaries (a live pipeline's node configs as their key names and count; with `config_values=True`, which only the example loader passes, each config whole as JSON values), edges and handles, preamble presence/digest, and singleton presence without executable source or row values. Edge handles are rendered under the exact field names the graph-edit operations accept, so the shape the model reads back is the shape it must write; see Edge cases. It also owns the turn context: the frozen `TurnContext`/`BriefNode`/`BriefInput` data, the egress policy words with the column-value rule, and the pure bounded `render_turn_context`, which the route, the self-test harness and the golden script share. |
| `src/haute/assistant/_application.py` | `PipelineApplicationService`, the stateful inspect → dry-run → apply → verify service. It composes the public parser, the save service's no-write validation and transactional save, shared save lock, plan store and document-update publisher; transport and model tools are adapters only. Schema validation resolves through `execute_lazy_graph(..., schema_only=True)`, and owns both the seed rule and the pre-existing-failure rule described under Plan/apply/verify. |
| `src/haute/assistant/_tools.py` | Thin adapters over the capability registry and `PipelineApplicationService`. Read tools retain their bounded renderers, including bounded recursive dataset discovery. `build_turn_context` gathers the turn context's facts (revision, brief nodes resolved schema-only and cached per revision, validated selection, policy-reduced preview error) on the same schema helpers as `get_node_schema`. Config redaction is policy-driven: credentials and row values are never eligible, while executable keys follow the project's own `allow_executable_source` decision rather than being redacted unconditionally. Value profiling is the one data-reading adapter and is gated on the egress policy's row-sample permission; see Control flow. Each source-bound executor seeds its evidence ledger from schema/content evidence in the exact provider history window, then adds evidence returned during the current turn; both pass through `_observe_project_source_evidence`, where a successful `list_datasets` or `get_dataset_schema` first drops schema evidence whose file no longer exists. `apply_graph_plan` is the only tool that writes; the mutation path is `plan_recipe`/dry_run_recipe_plan or `dry_run_graph_edits` followed by `apply_graph_plan` with the exact returned plan hash, and operations cannot be resent at apply time. Tool code does not own revision, save, or verification policy. It imports the incomplete-transform message from `src/haute/_code_extraction.py` (owned by [codegen](../codegen/low-level.md)). |
| `src/haute/assistant/_session.py` | Session store: `AssistantSession` records (id, bound pipeline `source_file`, provider-neutral user/assistant/tool/internal-controller history including required tool-result `is_error`, each turn's typed outcome (`AssistantTurn.outcome`, none for a failed or cancelled turn; a persisted turn requires the `outcome` key and its detail is redacted like assistant text), per-session `asyncio.Lock`, timestamps), create/lookup/resume, `list_sessions` for the chat list, the provider-request history window, and bounded retention. Controller messages are provider-visible but transcript-hidden. Durable tool arguments/results become `{"redacted": true}` plus approved revisions/evidence and value-free validation diagnostics; deterministic payload digests are forbidden because finite-domain values are enumerable. Persistence, revival, corruption handling, pruning, and non-fatal write degradation retain their existing contracts. |
| `src/haute/assistant/_providers.py` | The `AssistantProvider` protocol and its three public adapters: `AnthropicProvider` (`anthropic` SDK, Messages streaming API), `OpenAIProvider` (`openai` SDK, Chat Completions), and `DatabricksProvider`. Databricks subclasses the OpenAI-compatible implementation but retains the `databricks` provider identity for client construction, logs, and typed failures. A neutral `context` message becomes a mid-conversation `system` message for the Anthropic models in `MID_CONVERSATION_SYSTEM_MODELS` and otherwise the leading text of the preceding user message. SDKs are core dependencies but imported lazily inside the adapters (importing Haute never triggers provider-side behaviour; a broken install surfaces as a readiness reason); each adapter normalises its SDK's stream into the internal `ProviderEvent`s (see Control flow § Provider adapters for the exact call and event mappings) and maps SDK failures to `AssistantProviderError`. |
| `src/haute/assistant/_loop.py` | Provider-neutral agent loop as an async generator of typed stream events: resolves only an unbroken `NEEDS_INPUT:` clarification chain into its originating recipe guidance, builds the session-stable system prompt, assembles prompt/history/turn-context/tool inputs (the context message joins the route's rendered context with the routed guidance and is never stored), forwards text deltas, invokes the injected tool executor, feeds structured results into later provider rounds, shields only an in-flight transactional apply from cancellation, enforces tool/time limits, terminates when the dry-run budget is spent or a failed dry-run makes no progress, applies the bounded incomplete-mutation continuation gate, commits turn history, and closes every provider stream. It does not implement graph edits itself. |
| `src/haute/routes/assistant.py` | The FastAPI router: `GET /api/assistant/status`, `GET /api/assistant/sessions` (the saved conversations bound to the requested `source_file`, for the panel's chat list), `POST /api/assistant/session`, `POST /api/assistant/message` (an SSE `StreamingResponse` wrapping `_loop`'s generator). Every one of the last three carries the canvas document's `source_file`, resolved by one route helper (`contained_path` inside the project root, then membership of `discover_pipelines()`, then the POSIX project-relative spelling the editor document uses); there is no default-pipeline guess. Route-level exception translation follows the product conventions (typed `HauteError`s surfaced, everything else sanitized). Swept by the existing `tests/test_routes_hygiene.py` contracts like every `routes/` module. |
| `src/haute/_column_summary.py` | Shared with [explore-eda](../explore-eda/low-level.md): the Polars dtype facts every column-summarising surface needs — `is_unhashable_dtype` for the columns that cannot be counted, the reserved count-field alias `CATEGORICAL_COUNT_FIELD`, and `json_safe_scalar`. It imports only Polars and the stdlib-only JSON-safe encoder, so the assistant reaches it without importing the routes layer. |
| `src/haute/schemas.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); the assistant slice of the server-api-owned shared HTTP/SSE contracts: status, session request/response and transcript entries (including the `outcome` entry), message request (with its optional closed `context`: `selected_node_ids`, unique, at most 20, and an optional `preview_error_node_id`), usage, the turn outcome `AssistantTurnOutcome` (a kind of applied, answered, needs_input, blocked or committed_unverified, and a non-empty detail exactly for the last three), and the text-delta, tool-started, tool-finished, graph-updated, completed (usage and required outcome), failed, and cancelled event union mirrored by `frontend/src/api/assistant.ts`. |
| `src/haute/server.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); includes the assistant router with the other feature routers ahead of the API/WebSocket 404 catch-alls and supplies document-update fingerprint/wire-path helpers used by mutation publishing. |
| `src/haute/routes/_save_pipeline.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); transactional save service used by assistant mutations; its `save_graph_transactionally` wrapper explicitly forwards the parsed graph's preserved blocks into `SavePipelineRequest` and owns rollback, self-write marking, and ledger-capture warnings. |
| `pyproject.toml` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); declares `anthropic>=0.40` and `openai>=1.55` as core dependencies and omits `src/haute/assistant/assets/*` from import-coverage measurement because exemplar `.py` files are parsed package data, while ruff and parser tests still check them. |

Environment knobs: `HAUTE_ASSISTANT_TURN_TIMEOUT` (seconds, default 600) and
`HAUTE_ASSISTANT_MAX_TOOL_CALLS` (default 40) read lazily via `haute._env`, matching the
existing pattern. `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS` (the per-provider-call output budget
both adapters pass through, threaded via `AssistantConfig`) is deliberately stricter than
`haute._env`'s lenient semantics: unset → 8192; a set-but-malformed or non-positive value
is a **readiness error**, not a warn-and-default — a silently substituted cost ceiling is
precisely the wrong-fallback class the project forbids.
Retention constants in `_session.py`, not env knobs: the provider request carries the most
recent **complete turns** fitting a 40-message budget, and always the newest turn (trimmed
of its oldest tool-call rounds when it alone exceeds the budget); stored history is capped at 200
messages by evicting whole oldest turns; live-session LRU cap 32 idle sessions, held in
the shared `LRUCache` (a session with a running turn is pinned from `reserve_turn` until
its reservation is released, so it is never evicted and does not count against the cap;
when a create, a revival or the end of a turn leaves more idle sessions than the cap, the
least-recently-used idle session is evicted — dropping only the in-memory record, the
persisted file revives it on next lookup; a read that must not count as use, such as a
listing or a resume refused for another pipeline, does not promote it); persisted session files cap at 100 (`MAX_PERSISTED_SESSIONS`), pruning the
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
  `required_fields`, `optional_fields` and enum values are derived from that
  resolved schema rather than maintained separately. For Data Input and Data
  Output the top-level properties merge every branch: a key every declaring
  branch enumerates carries the union of their values, `enum_values` lists only
  such closed keys (Data Input `format` stays open because the file branch
  accepts any installed format), and `required_fields` are the keys every
  branch requires. `defaults` is the palette's config for the type
  (`haute._config_io.palette_default_config`, read from `node_defaults.json`).
  Source-ness comes from `haute._standalone_nodes.SOURCE_NODE_TYPES`, types
  with no output from `haute._types.SINK_ONLY_NODE_TYPES` (held
  equal to the editor's `SINK_ONLY_TYPES` by test), first-input pass-through from
  `haute._standalone_nodes.STANDALONE_PASSTHROUGH_TYPES`, and the single-input types
  are held equal to the palette's `maxInputs: 1` entries by test; every
  `NodeType` has an explicit input cardinality, and one without it fails at
  import. So Load File takes its first input as `df` plus further inputs by
  edge name and exposes the loaded object as `obj`; Model Training,
  Optimisation, Explore, Data Output and Quote Response have no outputs;
  Rating Step rates its one input; Apply Optimisation takes several inputs and
  picks a ratebook artifact's frame by `ratebook_input`. `display_name` is the
  palette name (held equal to `NODE_TYPE_META` in
  `frontend/src/utils/nodeTypes.ts` by test), `summary` is a one-line purpose,
  and `usage` is the longer authoring note; all three are completeness-checked
  at import. `step_authoring` is `null` for a type outside
  `haute._polars_steps.STEPPED_NODE_TYPES` and, for a stepped type, is derived
  from its `SteppedSurface`: `start` and `inputs` as the table holds them, a
  `rule` sentence (an `input` start begins with a source step whose `input`
  names the incoming edge that becomes `df`; a `frame` start has no source step
  and binds `df` to the first input on an `edges` surface or to the frame the
  node produced on a `none` surface; an `edges` surface reads further inputs by
  edge name; the code starts with a one-line `# intent` comment; a `frame`
  surface needing no post-processing keeps `steps: []`), and `new_logic`, the
  step list that writes new logic: `[{"id": "start", "kind": "source", "input":
  "<edge name>"}, {"id": "logic", "kind": "free_code", "code": ...}]` on an
  `input` start and `[{"id": "logic", "kind": "free_code", "code": ...}]` on a
  `frame` start, with a column-agnostic example code
  (`_catalog.NEW_LOGIC_EXAMPLE_CODE`). `_catalog.new_logic_steps` builds that
  list for the descriptor, the system prompt and every stepped-write refusal, so
  all three spell the same shape. Manifest validation fails at import if a
  stepped type lacks `step_authoring` or another type carries it. The Polars
  descriptor's anti-patterns state the free-code card's contract (assign the
  result to `df`; the source step binds `df`), not the code-mode one.
- **`OperationCapabilityDescriptor`**: a closed, versioned operation
  declaration. `_tools.TOOL_DEFINITIONS` is projected from these descriptors,
  so a provider-visible tool cannot exist without risk, egress, retry,
  concurrency, timeout, payload, context-budget, stable-error and recovery
  metadata. Its output schema requires attribution plus exactly one non-empty
  success or error result variant; an empty object is never a valid declared
  operation result. The `dry_run_graph_edits.ops` branches are generated from
  `_wire_ops`' canonical Pydantic operation models. The projection inlines local
  definitions and retains closed fields, requiredness, discriminator constants,
  descriptions, nullability, and the canonical `NodeType` enum; `_catalog` does
  not hand-copy the primitive operation vocabulary.
- **`PreparedGraphEdit` / `VerifiedPlan`** (`_ops.py` / `_application.py`): the
  prepared value contains one parsed, normalized, graph-applied edit plus its
  resolved postconditions, semantic diff, and affected capabilities. The verified
  value adds save validation, schema evidence, warnings, tier, and the one sealed
  `GraphEditPlan`. `build_verified_plan` is the sole application-service path from
  snapshot plus raw operations to that pair and is reused for dry-run, recipe
  dry-run, and apply replay.
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
  joined verbatim (the one state that carries git-layer text); `"git-unavailable"` → fixed
  message that Git is not available on this host and assistant edits need it to record
  each change. `working_branch_status` is total for those seven repository/readiness
  states, and a state outside them is a programming error that raises. An unexpected
  git-domain `HauteError`
  raised while computing readiness likewise maps to disabled with that error's message as
  the reason — the assistant status endpoint always renders readiness; an infrastructure
  failure is a reason, never an HTTP error. A resolved policy whose `max_sensitivity` is
  `public` (which `trust = "external"` requires) denies every project read and therefore
  every mutation at the tool boundary, so readiness reports `mutations_enabled: false`
  with a fixed reason naming `[assistant.egress].max_sensitivity` and stating that it
  denies project reads and edits; the Git state is not consulted for that reason.
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
    `node_type` carries its own description (an id from the prompt's node index): the
    schema builder overlays a field's description onto an inlined local definition, so
    no Python class docstring reaches the provider-visible schema.
    Positions are assigned by the deterministic rule below *after* the whole batch
    has applied, so parent-based placement sees the batch's final wiring.
    The persisted id and label are both the canonical sanitised function name,
    because source reparse cannot preserve a separate unsanitised label.
    Adding or renaming to a sanitised id already owned by a different node is
    rejected before the working copy can contain duplicate identities.
    The node starts from the palette's config for its type
    (`haute._config_io.palette_default_config`, read from `node_defaults.json`, the file
    the editor palette uses) with the model's `config` merged over it key by key, so an
    assistant-created node matches a palette-created one: a Data Input carries
    `inputType`, and every stepped type carries `steps: []` and opens in the step
    builder. There is therefore no assistant path to a new code-mode node. Two cases
    take less than the whole palette config. A config that selects a branch other than
    the palette's (a Data Input or Data Output provider, a Model Score or Apply
    Optimisation source, an Optimiser mode or a training algorithm, the discriminants
    config recovery also uses) keeps only the palette's `steps`, as the palette belongs
    to one branch and must not fill another's fields. A config naming `instanceOf` takes
    no palette config, because an instance's configuration is its original's.
  - `update_node {node, config}` — shallow key merge into the existing config; an explicit
    JSON `null` value removes that key. The operation schema's `config` description
    states both rules to the model: a written top-level key replaces its whole value, so
    a nested object or list is sent complete. Unknown keys for the node's type are rejected using
    the same `TypedDict`-derived allowlist machinery the sidecar writer uses (see Edge
    cases for why this is deliberately stricter than save's warn-and-drop).
  - Both node operations follow the stepped-node write contract in Edge cases: a write
    that would change how a stepped-type node is authored is refused, and a write that
    does not land in the materialised config fails the plan with `op_not_applied`.
  - `rename_node {node, new_name}` (sets both id and persisted label to the
    canonical sanitised function name, and rewrites edge endpoints; it refuses a
    rename that a consumer's configuration or code would not follow, see Edge cases) ·
    `delete_node {node}` (drops every touching edge,
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
    one must use `"join"`. These edge handles are the only role representation.
    Handle-less joins and the removed `baseInput`/`joinInput` config form are invalid;
    there is no edge-order inference or compatibility path.
- **`ProjectSnapshot` / `ProjectRevision`** (`_ops.py`): immutable saved graph
  plus a canonical manifest of source/config/knowledge/artifact/capability
  digests. The revision is the SHA-256 of canonical JSON for that manifest.
- **`GraphEditPlan`**: base revision, normalized primitive operations,
  semantic diff, affected capabilities, postconditions, egress, verification
  tier, schema evidence, and plan hash.
  `egress` states whether building the plan ran node code over project data:
  `"none"` when no schema target was resolved, so nothing executed and the plan
  carries only the model's own operations, the diff and graph counts; and
  `"schema-resolution"` when at least one target's lazy schema was resolved, which
  runs the node code of that target's lineage over the project's inputs (node code
  may collect rows) and returns node ids, column counts and schema digests, never
  column names or row values. A plan whose every target was excused as a
  pre-existing failure is `structural` yet still `"schema-resolution"`, because the
  code ran.
  Affected capabilities are derived from the complete change set rather than
  the bounded presentation lists. The hash excludes timestamps and includes
  every authority-relevant field.
  A schema-tier plan carries a bounded, deterministic record for each verified
  terminal (`node`, output/port shape, column count, schema SHA-256); that
  evidence is part of the hash rather than an informational afterthought. After
  the terminal records come `input_schema_inferred` records (`node`, `tier:
  "inferred"`, `format`, `inference_rows`), one per Data Input in a resolved
  terminal's lineage whose schema came from the IO layer's inferred schema tier,
  deduplicated and sorted by node id. They carry no path or column name, and the
  plan's tier stays `schema`.
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
  operation remains the authority for its requested value. The cap bounds
  presentation only: the diff also holds the complete untruncated identities
  (`SemanticDiff.complete`, a `SemanticChanges` that `as_dict` never emits but
  diff equality compares), and every verification reads those — assistant-authored
  code and step checks, schema seeds, the steps-survive and readiness proofs, and
  the `node_config` postconditions — so a change beyond the fiftieth in its
  category is verified like the first.
- **`PlanStore`**: process-local, size- and TTL-bounded records keyed by plan
  hash, held in the shared `LRUCache`. It owns validated/applying/applied/aborted state transitions under a
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
The manifest is the only node catalogue: there is no separate node-type list. A call to
the removed `list_node_types` tool, which a resumed session's history can still name, is
refused with `tool_removed` and a message naming `get_capability_manifest` and
`get_capability_descriptors` as its replacements.

**Recipe planning**: `plan_recipe` has a canonical flat discriminated union derived from the
closed recipe argument schemas. Every request receives the same complete provider-facing
union together with `dry_run_recipe_plan`; lexical recipe recognition changes neither tool
availability nor schema shape. Argument descriptions distinguish a requested graph-node name
from its output-column name. Transform, join, and rating recipes also accept optional non-empty
`output_name` and `output_columns` fields, which must be present together. The latter is a
non-empty unique array of simple JSON-field column names. When present, the deterministic
planner adds one response `output` node, a canonical JSON `outputMapping` for exactly those
columns, and an edge from the recipe node in the same canonical batch. The standalone
`response_output` recipe requires `source`, `output_name`, and `output_columns` and
creates the same canonical mapping directly after the saved source. A bare output name or
column list is a material ambiguity and fails recipe planning. A categorical-banding rule
contains exactly a non-empty string `value` and non-empty `assignment`. Execution
(`src/haute/_rating.py::_apply_banding`) casts the banded column to text and matches each row's
text exactly, so the rule's `value` description states that text form: booleans are
`"true"`/`"false"` and integers are their digits. Through the tool, the closed input
schema refuses a boolean or numeric `value` as `invalid_request` with reason `wrong_type`
at `plan_recipe.rules[N].value`, and the message repeats that description. A direct
planner call refuses it with `recipe_argument_invalid` naming the argument and the text form
it must take (`"true"` or `"false"` for a boolean, the digits for an integer). Planning
refuses a `value` equal to an earlier rule's, because the saved banding sidecar keys rules by their text and
refuses a repeated key when the plan is applied. The reference-join `how` enum is `inner`,
`left`, `right`, `full`, `semi`, or `anti`; the recipe always emits `leftOn`/`rightOn`, so
`cross` is neither advertised nor accepted. The
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

The source-bound executor retains planned recipe material by hash and replaces the previous pending
handle for the same recipe on correction. Its provider result is the closed opaque receipt
`recipe_id`, `version`, and `recipe_plan_hash`; canonical operations and postconditions stay
server-side. `dry_run_recipe_plan(recipe_plan_hash)` resolves only that executor's live
handle and invokes the ordinary graph dry-run with exactly the stored canonical recipe
material. It rejects every additional property. A pending recipe makes primitive
`dry_run_graph_edits` return `recipe_plan_requires_handle`; an unknown or replaced handle returns
`recipe_plan_not_found`. The live handle clears only after a successful dedicated
dry-run. Neither tool writes. A conservative current-request recognizer suggests a recipe id only
when exactly one explicit domain pattern matches: categorical/discrete banding maps to
`categorical_banding`; join maps to `reference_join`;
and the phrase rating step maps to `rating_step`. If the assistant returns
`NEEDS_INPUT:`, route resolution scans backward only across consecutive turns whose final
assistant text also begins `NEEDS_INPUT:` and reuses the first directly routed user request.
Any other final response ends continuation, so an unrelated bare path cannot revive stale
guidance. A standalone response-output request suggests `response_output`; a specialist recipe
that also requests a response output keeps its specialist suggestion and owns that downstream
output. The loop may append that suggestion to the current turn's system contract, but the
suggestion is never executor authority. Every request receives the same full `plan_recipe`
discriminated union, `dry_run_recipe_plan`, `dry_run_graph_edits`, and `apply_graph_plan`
descriptors. Zero, one, or multiple lexical matches therefore cannot remove a valid structured
path. The recognizer never populates recipe arguments, changes an input schema, rewrites a tool
call, or rejects a primitive plan or another valid recipe id. The source-bound executor API has
no natural-language request parameter, so this separation is structural rather than a
convention inside its dispatcher.

A material-input recognizer may add focused prompt guidance when rating choices appear to be
withheld, but it does not omit tools or create an executor verdict. The provider must not invent
missing choices; if it submits a complete structured call, that call is judged only by the
canonical recipe/operation schema and graph validators. The lexical-only error codes
`recipe_route_required`, `recipe_route_mismatch`, `recipe_name_mismatch`, and
`material_input_required` are not part of the operation descriptors. A pending canonical
recipe receipt remains different: `recipe_plan_requires_handle` prevents a provider from
replacing already-generated server-side recipe material with primitive operations.

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

The operation runs where the editor's previews run. The server checks the egress gate,
parses the saved graph, validates the target and input name, and admits one
`PREVIEW_EAGER` execution context. Frame preparation and the bounded collection then run
in the interactive preview worker through
`src/haute/_interactive_workers.py::run_in_interactive_worker`, under the isolated budget
derived from that admission and the worker's native memory cap, with the preview timeout
(`HAUTE_PREVIEW_TIMEOUT`). Under that cap a join or group-by whose materialisation cannot
be estimated runs conservatively inside its reserved envelope instead of being refused, so
frames downstream of joins and aggregations profile like any other. The worker renders an
execution failure itself, with the same row-value rules as every execution error, because
the exception's traceback does not cross the process boundary; a worker memory outcome is
reported as the preview memory budget being exceeded, and a timeout by its data-free
limit message. Calls are keyed per assistant session: they share one warm worker
affinity, and a newer profile in the same session supersedes and stops an older one still
running. When the turn stops, the cancelled call stops its worker before the call ends;
with `HAUTE_INTERACTIVE_EXECUTION_MODE=thread` the collection runs on a server thread,
the call cancels the context's token so the engine stops at its next checkpoint, and the
admission is released when that thread finishes. The server releases the admission in
`finally` otherwise.

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
float is a real value a numeric column can hold. Temporals and decimals become their
written form (ISO-8601, exact digits), and a non-finite float becomes the shared tagged
sentinel `{"__haute_type__": "non_finite_float", "value": "inf"}` built by
`_json_safe.non_finite_float_sentinel` — the one non-finite encoding every Haute payload
uses, which also keeps an infinity distinct from a string column holding the text `inf`.
Numbers and strings keep their JSON type, so a finite numeric bound stays a number. Left raw, the failure surfaced nowhere near the column that caused it: as
an opaque `tool_failed` for the whole call. When the policy's `allow_row_samples` is
true, the turn context requires the model to profile a frame before comparing a column
to a literal, and to answer `NEEDS_INPUT:` rather than guess an encoding when a column's
values are withheld. When it is false, the turn context says profiles are not permitted and
tells the model to ask the analyst which values to match, beginning `NEEDS_INPUT:`, so a
turn never ends on a refused profile call. Either way a guessed comparison is ruled out:
it produces code that runs, validates at schema tier, and silently matches nothing.

**Project-knowledge query**: `get_project_knowledge(query, limit)` builds the
current policy-filtered view, scores only eligible items against normalized
query terms, and returns at most ten items under a fixed aggregate character
budget. Returned items retain source/digest/version/sensitivity/evidence
attribution. Restricted or otherwise excluded material contributes only to an
excluded count; its path and content never cross the tool boundary.

**Plan/apply/verify**:

1. `dry_run` resolves and parses the saved source, builds the snapshot and
   revision, and calls the single `build_verified_plan` pipeline. Its prepared-edit
   phase parses and normalizes primitive ops once, applies them once to a deep graph
   copy, validates assistant-authored invariants, resolves postcondition refs, and
   derives the complete semantic diff and affected capabilities. Its verification
   phase invokes the save service's public no-write validation (including
   canonical Edge Join role, handle, topology, and key-form validation),
   proves that the steps of every stepped node the plan adds or updates survive a
   save (below), derives the complete changed-node set from the diff's untruncated
   identities (never the 50-entry presentation lists), and resolves the schema of every
   reachable executable terminal through `flatten_graph` +
   `execute_lazy_graph(..., enforce_contracts=True, schema_only=True)` +
   `collect_schema()`.
   No frame is collected and no sink is invoked, which is what `schema_only`
   declares to the engine's group-by materialisation-admission gate. Every such
   resolution (`_application._resolve_lazy_output`, which `_prove_nodes_ready` also
   uses) runs inside `_input_providers.recording_inferred_inputs()`, so a local file
   Data Input with no snapshot resolves at the inferred schema tier and is recorded;
   an input the tier cannot scan fails the dry-run as `schema_unresolvable` whose
   message carries the IO layer's `input_snapshot_missing:` reason and the remedy
   "Preview this input first, which builds its snapshot." The dry-run writes no
   snapshot, and post-save verification re-resolves the same inputs the same way, so
   the inferred records compare equal. It then
   binds the normalized operations, semantic diff, postconditions, tier, warnings,
   and closed schema evidence into the plan
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
   `apply` calls the same `build_verified_plan` function, so it recomputes seeds,
   evidence, normalization, and these warnings identically and the plan hash
   is stable; post-save verification re-resolves only the targets the plan already
   proved and admits no pre-existing excuse at all.

   **Steps are proved to survive a save before apply.** For each stepped node the plan
   adds or updates (the complete set, however many; instances excepted), the verification phase generates the planned
   pipeline source in memory with the save path's codegen (`graph_to_code_multi`),
   takes the node's sidecar exactly as `collect_node_configs` would write it, and
   resolves the node's generated function through the parser's own node resolution
   (`_resolve_node_skeleton`, against a temporary directory holding only those
   sidecars). The resolved config must hold the planned `steps` unchanged and no
   `_steps_discarded` reason; otherwise the plan fails with `op_not_applied` naming the
   node and the parser's reason. This is the failure class where a rendered step list
   and its extracted body disagree and reparse silently turns the node code-only.

   **Written Modelling and Load File nodes are proved ready.** Save validation already
   refuses malformed modelling values (`validate_modelling_config_values`). After schema
   evidence resolves, `_application._prove_nodes_ready` checks each Modelling and Load File
   node the plan adds or updates (the complete set, however many; instances excepted); a
   failure raises
   `AssistantOperationError("node_not_ready", "Node '<id>' is not ready: <message>")` and
   stores no plan. A Modelling node needs a `target`, no `training_objective_issue` and an
   `evaluation` object that `parse_evaluation_config` accepts, as training requires; its
   output (a pass-through of its input) is resolved through the same engine path as the
   schema evidence, and `_training_preparation.build_training_feature_selection` checks the
   configured columns against it, the check training runs on the materialised schema. A Load
   File's object is loaded through `_builders.load_external_file_object`, the loader its
   builder calls: a missing file reports that the file does not exist, a Haute refusal keeps
   its text, and any other loader failure reports only its exception type, because a
   deserialiser's message can quote the file's content. An input that does not resolve fails
   earlier as `schema_unresolvable`.

   **Authored config is verified after reparse.** Besides the structural
   postconditions, every plan carries a `node_config {node, sha256}` postcondition for
   each node it adds or updates whose type carries code (the seven stepped types;
   instances excepted) — every such node, never a truncated subset. It is appended to
   the automatic or supplied postconditions, without duplicates, so a replayed plan
   carries the identical list. The caps agree by construction: a plan holds at most
   `MAX_PLAN_OPERATIONS` (100) operations, each adding or updating at most one node, so
   it seals at most 100 `node_config` postconditions; a caller declares at most
   `MAX_DECLARED_POSTCONDITIONS` (100), enforced by `dry_run`; and the sealed list,
   which apply replays through `build_verified_plan`, holds at most
   `MAX_SEALED_POSTCONDITIONS` (their sum, 200). A list over its cap fails loudly as
   `invalid_plan`; no check is dropped. The automatic structural postconditions (one
   `node_exists`/`edge_exists`/`node_absent`/`edge_absent` per change, then
   `preamble_digest` and `graph_shape`) are a bounded summary of at most 50, whose
   identity entries give way before the two whole-graph checks; they may omit identities
   beyond that, because post-save exactness already compares the complete semantic-diff
   digest, which covers every node and edge identity. Authored config has no such
   backstop, which is why `node_config` is complete. The digest is
   over the node's authored-config projection, which is narrow on purpose because the
   save path legitimately normalises other fields (a Constant's values become strings,
   a Rating Step's combined outputs gain their default operation, a Source Switch's
   `inputs` are derived from its edges): a node holding a `steps` list is projected to
   `{"steps": steps}`, and any other node of those types to
   `{"code": normalise_user_code(code, kind)}` with its type's extraction kind, which is
   exactly what reparse returns for code-mode code. Derived and editor-only fields
   (`code` rendered from steps, `_steps_error`, `_steps_discarded`,
   `_discarded_sidecar`, positions) are outside the projection. A save whose parser
   discarded the steps reparses without `steps`, so its projection becomes the code
   form and the postcondition fails. The dry-run check trivially holds on the planned
   graph; the post-save check on the reparsed graph reports a mismatch as a committed
   verification failure (`postcondition_failed`).
2. `apply` acquires `save_lock`, reloads the snapshot, compares its revision,
   passes the stored normalized operations through `build_verified_plan`, requires
   the complete rebuilt `GraphEditPlan` and plan hash to equal the stored authority,
   checks the plan-store state, and invokes
   `SavePipelineService.save_graph_transactionally` exactly once.
3. Still under the lock, it reparses, derives the actual diff/revision,
   verifies the visible diff plus complete semantic-diff digest, postconditions,
   and schema evidence at the declared tier, marks the plan applied, and publishes
   one pipeline document update. Errors before the save leave no files changed; post-save
   verification errors report the committed state and ledger evidence without
   retrying the mutation.

`PlanStore` is bounded for plans awaiting use, but an `applying` record is a
pinned, non-evictable lease until `complete_apply` or `abort_apply` records its
terminal result; a lease does not count against the bound, and when it ends the
least-recently-used plan beyond the bound is dropped. TTL expiry and capacity
pressure may remove only non-applying records. If as many applies are in flight
as the bound, a new distinct dry-run fails with `plan_store_busy` rather than
losing authority evidence for a save that may already be committing.

**Status** (`GET /api/assistant/status`): `_config.assistant_readiness()` — read `haute.toml`
(malformed or unknown `[assistant]` key → `ConfigError` → 400), check
provider/model fields, validate an OpenAI `base_url` or derive and validate the
Databricks serving endpoint from `DATABRICKS_HOST`, then probe the SDK import
for the configured provider and check its credential env var. Pure inspection, no
provider network call. Config errors name `[assistant].<field>` but never echo
the field value.

**Source file binding.** Session list, session create and message each carry a required,
non-empty `source_file`: the project-relative path of the pipeline document the canvas
shows. The route resolves it with `contained_path` against the project root (an escaping
path → 403, a NUL byte → 400, through the shared path-error handlers), requires the
resolved file to be one of `discover_pipelines()` (otherwise 404 naming the file), and
uses its POSIX project-relative spelling, the same spelling the editor document's
`source_file` carries, as the session binding. The server never picks a pipeline on the
client's behalf.

**Session list** (`GET /api/assistant/sessions?source_file=...`): resolve the source file,
then return it with `SessionStore.list_sessions(source_file)` — one summary per
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

**Session create** (`POST /api/assistant/session` with `{source_file, session_id?}`; any
other field is refused with 422): resolve the source file; the response echoes the
resolved `source_file` beside `session_id` and `history`. When the request carries a
prior `session_id`,
`SessionStore.resume` validates its source binding before touching an
in-memory candidate or promoting a disk-backed candidate. When it matches,
return it unchanged
with `history`: the stored turns mapped to transcript entries (`user`/`assistant` text
entries, and `tool` entries carrying the tool name, the same compact result summary the
live stream uses, and the error flag) so the panel rehydrates the conversation.
A successful mutation tool result's persisted `graph_fingerprint` additionally
derives a settled `graph_updated` activity entry immediately after that tool
entry, matching the live “Canvas updated” row without duplicating provider
history. A turn stored with an outcome ends with one `outcome` entry carrying that
`AssistantTurnOutcome`, the same value the live `completed` event carried; a failed or
cancelled turn has none. Stored assistant text is returned as stored, so the panel
derives the same display from a resumed turn as from the live one. Any other
case — no `session_id`, unknown/pruned/corrupt, or a different pipeline — creates and
returns a fresh session with empty `history`; resume is an offer, never an error.

**Message turn** (`POST /api/assistant/message` → SSE stream from `_loop.run_turn`):

1. Readiness is checked before session lookup (400 before the stream opens if
   unconfigured). This ordering means an unconfigured request reports the configuration
   problem even when its session id is also unknown. The request's `source_file` is then
   resolved (see Source file binding); it needs no session, so it happens before the
   reservation.
2. Look up the session (404) and atomically acquire its lock without waiting — held →
   409. The reservation happens before provider construction or pipeline parsing; either
   pre-stream failure releases it immediately, while a started turn releases it from the
   loop/response lifecycle and appends the turn to history. A resolved `source_file` that
   differs from the session's binding releases the reservation and is refused with 409
   naming the chat's pipeline: the canvas shows another pipeline, and the turn would
   otherwise edit a file the analyst is not looking at.
3. Resolve the provider configuration and construct the adapter, then build the provider
   request. The system prompt (`build_system_prompt(source_file=...)`) takes only the
   session's source file and the installed capabilities, so it is byte-identical on every
   turn of a session: static role and authority/evidence instructions + compact capability
   identity, an installed-I/O availability summary, a node index with one line per node
   type naming its id, palette name and one-line purpose, operation ids, and recipe ids
   with their canonical summaries + an example-ID-only index + the source file. It holds
   no pipeline name, node summary, egress policy or request-dependent text. The request
   then carries the windowed history, the new user message, a turn context message (neutral
   role `context`) and the `_tools` JSON schemas. The route builds the turn context under
   the save lock with `_tools.build_turn_context(source_file, egress, selected_node_ids,
   preview_error_node_id)` and `_render.render_turn_context`; the self-test harness builds it
   the same way. It holds:
   - the pipeline name and the base revision (`build_project_snapshot(...).revision`);
   - the effective egress policy in words, taken from the resolved configuration's
     `egress` (provider trust, highest sensitivity sent, and whether project knowledge,
     executable source and column value profiles are permitted, and, when row samples are
     not permitted, that execution errors are reported without their text), followed by
     the column-value rule it implies: with `allow_row_samples`, call
     `get_column_profiles` before comparing a column to a literal and answer
     `NEEDS_INPUT:` when its values are withheld; without it, ask which values to match,
     beginning `NEEDS_INPUT:`. The always-on rules defer to that section for project
     material, so they never deny access the policy grants;
   - the selected node ids, in request order;
   - the graph brief: per top-level node its id, palette name (from the capability
     manifest), label (whitespace collapsed, at most 80 characters, JSON-quoted), authoring
     state on a stepped surface (`incomplete` when its resolution raises the incomplete
     transform or incomplete steps placeholder, else `stepped` when `is_stepped_config`,
     else `code`), each input as its code-visible name, source node and column names, and
     its output columns (per port for a multi-frame node). Schemas resolve schema-only in
     one `execute_lazy_graph` call preserving every node; when that call raises, each node
     resolves on its own and an input's source resolves separately, so one broken node
     marks only itself `unresolved` (never with its error text). A submodel node has no
     columns. A node lists at most 40 columns per frame, then how many more. The resolved
     facts are cached per project revision (a small LRU); the revision covers the pipeline
     file, `haute.toml` and the capability hash, not data files, so a data file whose
     header changes under an unchanged pipeline keeps its cached columns until the
     revision changes, and a dry-run always resolves afresh. Selected nodes come first,
     then graph order; the rendered brief stops before 8,000 characters and ends with the
     count of nodes left out and a pointer to `get_pipeline`;
   - when the request names `preview_error_node_id`, that node's schema-only resolution:
     a failure is rendered by `_execution_error_message` at a `_FailureSite` for the node
     (text only under `allow_row_samples`; otherwise type, step or line and disclosable
     column names), and a clean resolution says the schema resolves and that a failure
     seen only while rows are collected is not reproduced;
   - the routed recipe suggestion or material-clarification hint for the effective
     request (see below).
   Under `max_sensitivity = "public"` the block holds only the policy and says the graph
   is withheld; the pipeline is not read, so the ids are not checked. Under any other
   policy a selected id that is not a top-level node of the saved pipeline, or a
   preview-error id that is not a top-level executable node, raises `TurnContextError`
   and the route answers 409 before the stream opens, releasing the reservation. The loop places the context message after the user message
   in every provider round of the turn and never appends it to the stored turn. The
   Anthropic adapter sends it as a mid-conversation `system` message for the models that
   accept one (`MID_CONVERSATION_SYSTEM_MODELS`) and otherwise, like the OpenAI and
   Databricks adapters, prepends it to the preceding user message's text followed by an
   `## Analyst message` heading. The authoring
   guide and full exemplar bodies are prompt-excluded: the model pulls
   them through `get_authoring_guide` and `get_example` only when relevant, and the
   node cards travel in the node descriptors: the mutation paragraph tells the model
   to read each descriptor's card with its ports, wiring rules, schema, enums and
   anti-patterns, and to write each config in the shape of the card's configurations.
   `get_authoring_guide` also returns `step_grammar`, derived from
   `haute._polars_steps`: every step kind with its required and optional fields
   (`step_fields`) and the closed vocabularies structured steps use (operators,
   aggregations, join kinds, cast dtypes, fill strategies, functions, literal
   types), so a model editing structured steps reads them on demand; nested
   expression shapes are not enumerated there. The mutation paragraph states one
   authoring rule for new Polars logic, derived from `STEPPED_NODE_TYPES` and the
   palette names: steps with a free-code card, the Polars (Transform) form
   `[source, free_code]` and the `[free_code]` form for every other stepped
   surface, each spelled as `new_logic_steps` renders it with the example code;
   the code transforms `df` and assigns the result to `df`, reads other inputs by
   edge name only on the surfaces whose steps see their edges (Polars and Load
   File, where the loaded object is `obj`), and starts with a one-line `# intent`
   comment; a hook needing no post-processing keeps `steps: []`; existing
   structured steps keep their ids and order, a code-mode node keeps its `code`,
   and the assistant never switches a node between steps and code. The
   permanent installed-I/O summary includes only group identity, input/output
   availability, cache modes, and format names; field schemas stay out of the prompt
   and remain available through capability tools. This keeps the routing facts useful
   without paying for a redundant descriptor copy or burying the mutation protocol.
   The prompt treats explicit authoring verbs such as build, add, change, update,
   connect, remove, delete, and make as mutation intent rather than an invitation to
   inspect and stop. When an installed deterministic recipe matches the requested
   operation, the model should call `plan_recipe`, then pass its `recipe_plan_hash` to
   `dry_run_recipe_plan`; it never copies the returned operations. A unique explicit
   current-request recipe suggestion may be repeated in the turn context, but
   every request receives the same complete mutation tool schemas and the executor never
   treats lexical classification as authority. A failed dry run may be corrected while
   the corrections make progress. The loop keeps **one bounded budget of four failed
   calls** across both dry-run tools and both failure classes, a domain rejection (the
   plan was built and judged) and a closed-input-schema rejection
   (`invalid_request`/`invalid_capability_query`, which never reached planning). The
   budget replaces two independent budgets of two attempts each, which existed because
   a single plan retry was spent by a spelling error before the plan had ever been
   judged; with four attempts and the progress rule below that can no longer happen,
   and one counter is the simpler bound. The loop ends the turn before the budget is
   spent when a failed dry-run makes no progress, judged on value-free identities it
   keeps for the failed dry-runs of the turn:
   - **identical request** — the tool name and the canonical JSON of the arguments of a
     dry-run that already failed, so an identical resend stops on its second attempt;
   - **repeated diagnostic** — the error's `code`, canonical `where` and `fix`, together
     with the canonical JSON of the operation `where.op_index` points at (the whole
     arguments when the error names no operation), so a model that edits other
     operations while the failing one stays unchanged stops as soon as the same
     diagnostic comes back.

   Either stop, and a spent budget, append a deterministic assistant `BLOCKED:` message
   naming whether the latest failure was graph validation or an input-schema rejection
   and why the loop stopped (four failed dry-runs, the same request sent again, or the
   same error for an unchanged operation), carrying the latest stable error code, that
   failed dry-run's error message in the same bounded one-line form the chat's tool row
   shows (`_result_summary`, at most 160 characters), and the fact that no graph changes
   were applied. The message is exactly what the tool result already returned to the
   model, so the outcome adds no value the model had not seen. It then emits `completed`
   with the `blocked` outcome, whose detail is that message after its `BLOCKED:` marker,
   and performs no further dry-run or provider round. A further dry-run call in the same
   provider round is refused without running as `dry_run_retry_limit` (not retryable) and
   does not change the recorded blocker. The system prompt states the same rule.

   What the model sees is pinned by a checked-in golden snapshot under
   `tests/assistant_eval/golden/`: the system prompt rendered for one fixed source
   file (`motor_pricing.py`), the turn context rendered from fixed data for two turns of
   that project (a three-node brief with a selection and a withheld preview error, then
   a four-node brief under a changed policy, each with the fixed policy words), the canonical
   `TOOL_DEFINITIONS`, the portable projection the Anthropic adapter sends
   (`_portable_tools`), the OpenAI Chat Completions function projection of it (which
   the Databricks adapter reuses unchanged), and a sha256 of each file.
   `scripts/update_assistant_prompt_golden.py` renders them; by default it fails with
   a unified diff per changed file, and `--write` is the only update path. The Haute
   version and the capability hash, which change on every release, are replaced with
   the placeholders `<haute-version>` and `<capability-hash>` so a version bump alone
   does not change the snapshot; every other line, including the installed-I/O
   summary rendered against the locked Polars, is compared verbatim. The script renders
   the system prompt once per turn and refuses to write when the two renderings differ,
   so the snapshot shows one prefix shared by both turns.
4. Stream provider events. `TextDelta` → emit `text_delta`. `ToolCallRequest` → emit
   `tool_started`; execute; append the result to the pending provider messages before
   emitting `tool_finished` (+`graph_updated` for successful mutations); on `TurnStop("tool_use")`
   re-invoke the provider with the accumulated results. Completion becomes required only
   when the model calls `dry_run_graph_edits`, `dry_run_recipe_plan`, or `apply_graph_plan`
   in the turn; neither the request's wording nor a recipe route sets it, so a read-only
   question that ends without such a call completes normally. The loop also tracks whether
   `apply_graph_plan` has succeeded. A successful apply is terminal after the current stream
   reaches its stop event: any later tool-call events in that same provider round are ignored,
   the loop records the successful apply and its result, emits the deterministic assistant
   text `Graph changes applied successfully.`, emits `completed` with usage and the
   `applied` outcome, and does not invoke the provider again. An `apply_graph_plan` result
   whose error code is `verification_failed` means the save committed but its post-save
   verification failed; it is terminal in the same way (later tool calls in the round are
   ignored and the provider is not invoked again): the loop records the deterministic
   assistant text `Graph changes were saved, but post-save verification failed.` and emits
   `completed` with the `committed_unverified` outcome, whose detail is the tool row's
   summary of that error. This check precedes the dry-run budget check, so the budget's
   "no graph changes were applied" blocker can never follow a committed save. Otherwise,
   when completion is required, `TurnStop("end")` is
   accepted only when the stripped assistant text begins `NEEDS_INPUT:` or `BLOCKED:` and
   contains non-whitespace detail after the marker. The first unqualified end appends a
   transcript-hidden `controller` message instructing the model to continue; when dry-run
   succeeded, it explicitly requires an immediate `apply_graph_plan` tool call with the
   exact returned hash rather than prose. The loop re-invokes the provider, and adapters
   encode that internal role as a user instruction. A second unqualified end emits `failed`
   with the incomplete-mutation reason. An accepted `TurnStop("end")` emits `completed` with
   usage and finishes. Its outcome comes from the final round's text: `needs_input` or
   `blocked` when the stripped text begins `NEEDS_INPUT:` or `BLOCKED:` with detail after
   the marker (whether or not a mutation was attempted), the detail being that text after
   the marker, stripped; otherwise `answered`. The outcome of every completed turn is
   stored on its history record; failed and cancelled turns store none.
   If the response closes while suspended at
   `tool_started`, the round commit filters the unmatched call; closing at either later
   event retains the already-recorded result. Thus every persisted call id has exactly one
   matching result id on every generator-close boundary.
5. Tool execution: read tools run via `asyncio.to_thread`, except `get_column_profiles`,
   which prepares on a thread and collects in the interactive preview worker (see
   **Column value profiles**). Reads whose answer depends on
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
      `_node_data_service._run_node_snapshot_worker` sequence, never an assistant-local
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
   5. **An authored-but-empty transform is a success, not a refusal.** A polars node
      with no code has no implicit passthrough and cannot resolve its own output — the engine raises its
      canonical `INCOMPLETE_TRANSFORM_MESSAGE`, and a node whose step list is incomplete
      raises that message or `INCOMPLETE_STEPS_MESSAGE` followed by the step problem (a
      palette-default Transform's `steps: []` has not chosen its input). The tool
      recognises both by prefix. That is the ordinary editing state of a
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
      tool error. An *authored-code failure* — a Polars error, any exception raised
      from node code (it carries the user-code line `_exec_user_code` records), or a
      `PreambleError` (the preamble is authored code that can read project data at
      import) — is rendered by the one execution-failure renderer in `_tools`, which the
      dry-run and apply schema-validation path, `get_node_schema` and
      `get_column_profiles` share: its exception type, the line of the node code or of
      the preamble that raised it, and the column names it names. In dry-run and apply,
      when exactly one node the plan adds, updates or rewires runs authored code and that
      node is stepped, the line is also named as that node's step number and id, since
      the model authored steps rather than the rendered program. Column-name candidates
      come only from a Polars error's `column '<name>'` / `"<name>" not found` phrases
      in its first paragraph, and such a phrase can sit inside a quoted cell value or an
      authored exception's collected values, so a candidate is named only when the
      effective egress policy already discloses that name through another route; being
      written somewhere in the project is not enough, because saved code the model may
      not read can hold a literal equal to a cell. The disclosable names are: (a) the
      column names of the failing node's input frames — the failing step's node, or
      else the node the tool resolved — each resolved schema-only through the same
      engine path as `get_node_schema`, plus that node's own output schema on the
      column-profile path, where the schema resolved and only the collection failed; a
      profile of one named input uses that input's frame alone, so rendering its
      failure never resolves the consumer the profile did not ask for; a
      frame that does not resolve contributes nothing and is never an error of its own;
      (b) in dry-run and apply, every string in the operations the model submitted for
      this plan (apply's are the plan's `normalized_operations`, which the dry-run
      result returned to the model), with the identifiers, keyword names and string
      literals of each string that parses as Python; and (c) only when
      `[assistant.egress].allow_executable_source` is true, the identifiers, keyword
      names and string literals of the preamble and every node's code in the graph that
      ran (a stepped node's code is its rendered steps). An unreadable policy counts as
      `allow_executable_source` false. The allowlist is built only when a candidate
      exists, so a failure without a column phrase runs nothing more. Any other
      candidate is dropped and the message says only that the error names a column,
      with no name. A preamble failure names no column. A preamble that fails while a
      plan is being validated is the structured `preamble_failed` error; every other
      tool path that meets a `PreambleError`, the generic handlers included, renders it
      the same way. When `[assistant.egress].allow_row_samples` is true the error's own
      text follows, because a Polars cast or compute error quotes cell values and node
      or preamble code can put values it read in its exception; when it is false that
      text is withheld and the message says so. A policy that cannot be read withholds
      the text too and names why. Haute's own errors keep their text — a `HauteError`
      other than `PreambleError`, a source-cache error, an I/O-configuration error and
      the incomplete-transform messages name nodes, columns, configuration and
      remedies, never rows — and every other unexpected exception is still sanitized to
      the internal-error detail and logged with `exc_info`. The full text of a withheld
      error is logged server-side.

   Mutation dispatch is an adapter over `PipelineApplicationService`.
   `dry_run_graph_edits` captures the exact evidence/revision, calls
   `build_verified_plan` for single-pass parsing, normalization, pure operation
   replay, no-write save validation, schema-only lazy-plan validation, postcondition evaluation, semantic
   diff construction, and immutable plan storage while holding the shared
   save lock against concurrent GUI saves. `apply_graph_plan` then checks branch
   readiness and, under the same lock, reloads and compares all revision sources,
   calls the same `build_verified_plan` path with the stored normalized operations,
   requires complete plan equality and the same hash, checks
   one-use authority, invokes
   `SavePipelineService.save_graph_transactionally` once, reparses, verifies the
   actual diff, schema evidence, and postconditions, and publishes the standard pipeline
   document update.
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

   Every error result is finalised by the executor before it is logged or returned:
   it carries `retryable`, false exactly for the codes in `_NON_RETRYABLE_CODES`
   (`tool_failed`, `operation_failed`, `mutation_failed`, `egress_policy_denied`,
   `egress_policy_unavailable`, `tool_interrupted`, `dry_run_retry_limit`,
   `verification_failed`, `tool_result_too_large`), and true for every other code,
   each of which is a rejection a corrected call can clear. The plan domain locates
   its failures: `OpValidationError` and `AssistantOperationError` carry `where` (any
   of `op_index`, `node`, `field`, `step`), `fix` (one concrete correction) and `graph`
   (the graph the failure was judged against); `OpValidationError` requires its `fix`,
   so every `invalid_ops` failure carries one. `_apply_ops_with_refs` stamps the
   failing operation's index, and records which operation last wrote each node (added,
   updated or renamed it), so a failure found after the whole batch — the
   assistant-authored step and code checks, `op_not_applied`, `node_not_ready`,
   `schema_unresolvable` — is stamped with the index of the operation that wrote its
   node and with the planned graph. The dry-run boundary renders `where`, `fix`, and,
   when `where.node` is set, `context.inputs`: each incoming input's code-visible name
   and its column names, resolved schema-only per source by `_input_columns`, the same
   resolution that decides which Polars-named columns an execution failure may name.
   An input whose source does not resolve is left out. The executor admits the dry-run
   and apply tools only under `get_node_schema`'s permission (a readable policy whose
   `max_sensitivity` is not `public`), so a public policy refuses the call as
   `egress_policy_denied` before any column is resolved. `did_you_mean` is the
   `difflib` close matches (at most three, cutoff 0.6) of each column an execution
   failure names that no input provides, among those input and column names only.
   `schema_unresolvable` always carries a `fix` naming the step or node to correct,
   and the replacement name when `did_you_mean` has one. An unknown tool name carries
   the valid names and its close matches among them. None of these fields is
   persisted: durable history keeps only `code`, `validation_path` and
   `validation_reason`.

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
   `wrong_type` carries `expected_types` and the `received_type`, spelling a boolean as
   "JSON boolean (true or false)" so a model that sent `True` or `"true"` sees the literal
   it needs, without the rejected value being echoed, and adding an explicit
   "send the value itself, not a JSON-encoded string of it" when a string arrived where an
   array or object was declared — the exact shape a gateway dialect produces, and one the
   model cannot infer from a bare type complaint. A `wrong_type` message also repeats the
   field's own schema description when it has one, so a categorical rule `value` sent as a
   boolean or number reads the text form it must take. None of these are persisted, because
   `_session._persisted_message` copies exactly `code`, `validation_path`, and
   `validation_reason`. A rejected key is content the model itself submitted and is
   already in provider history, so naming it back is not new egress — and it is what
   makes the rejection correctable on the next attempt, where
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
  from the message-level usage events. In the request, the consecutive tool results of
  one round travel as `tool_result` blocks in a single user message, as the Messages API
  expects for parallel tool calls.
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
  input schema is projected to a portable wire schema with a forty-property budget per
  tool. It retains object/array shape, property names, descriptions, common required fields,
  single scalar types, enums, and closed-object declarations; nullable scalar unions project
  to their non-null generation type. For a composition of closed object branches that fits
  the remaining budget, the projection unions branch properties, combines discriminator
  constants into one enum, recursively projects a property present in one branch or
  identically declared across branches within the remaining budget, merges different arrays
  of closed object items by the same union/intersection rule, reduces other conflicting
  property schemas to a common portable type, intersects required fields, and remains closed.
  The bound preserves the complete merged recipe selection contract without using lexical
  request-dependent narrowing. A composition that does not fit remains a generic typed
  container. Patterns, ranges, and other unsupported validation vocabulary are omitted.
  `_tools` independently validates
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
  emit `ToolCallRequest`s when `finish_reason == "tool_calls"`, and equally when
  `finish_reason == "stop"` arrives with accumulated calls (some OpenAI-compatible gateways
  finish a tool-calling reply with `stop`); a round that emitted calls ends with a
  `tool_use` `TurnStop`, and `finish_reason == "stop"` without calls → an `end`
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
  Empty code still saves (with a warning), but the node raises if the pipeline
  is run — a polars node has no implicit passthrough.
- **Assistant-authored polars code names the input it starts from.** Neither the
  executor nor the generated module binds `df` to an input: a polars node's inputs are
  its named parameters (one per incoming edge) and `df` is only the output variable,
  unbound until the code assigns it. Reading `df` before assigning it is therefore a
  guaranteed `NameError` at run time, so assistant-authored code that does so is an op
  error naming that node's actual input names, on ANY input count, with `where`
  (`node`, `field: "code"`) and a `fix` that binds the first input by name. Binding first
  (`df = proposer_claims`) and then reusing `df` is explicit and accepted. Only reads
  that resolve to the module-level `df` count, so the
  check skips any nested scope holding a `df` of its own — a `def helper(df)` parameter,
  a `lambda df:`, a comprehension target — because those name that scope's variable.
  Bindings inside a further nested scope do not shadow
  `df` in the enclosing function. A comprehension's first iterable is evaluated before
  its target is bound, so a bare `df` read there still reads the unbound module `df`.
  A binding must dominate a later read: for an `if`, every fall-through branch must bind
  `df`; a loop body's binding does not establish `df` afterwards because the loop can run
  zero times. Falling off the end also reads the generated output, so a conditional-only
  assignment is rejected even when the submitted snippet has no later explicit read.
  Complex exception/context-manager/match flow is conservative rather than assuming a
  hidden store definitely ran. A statement's loads are judged before its stores, since
  an assignment evaluates its value first: `df = df.head()` reads the unbound name,
  `df = left` does not, and augmented assignment reads its target before storing it.
  The derived input name `df` is itself rejected as a reserved output-name collision.
  This is an authoring-time rule for assistant edits only, like
  the unknown-config-key strictness above; existing human-authored code is untouched
  (it fails loudly at execution instead).
- **Assistant-authored free-code steps keep their result and their surface's scope.**
  Every node the batch adds, or whose `steps` it sets, that carries a step list on a
  stepped surface (instances excepted) is rendered with the product's renderer
  (`render_polars_steps`, against the node's incoming input names in the surface's start
  mode), so an invalid step list is an op error naming its step and the surface's
  free-code form (a palette-default Transform left at `steps: []` is refused this way,
  because a Transform's steps must choose their input). Each `free_code` step
  is then checked on its own code. A top-level bare expression that calls a method on
  `df` or on an incoming input (`df.filter(...)` alone) is refused naming the node, the
  step's number and its id, because its result is discarded; the rendered program's
  earlier `df = <input>` line does not make such a step retain anything. On a surface
  whose code sees only `df` (`inputs == "none"`: Data Input, Rating Step, Model Score,
  Scenario Expander, Explore), a step that reads an incoming input name or the id of a
  node upstream is refused with `<Surface> code sees only df; <name> is not in scope`,
  naming the step, unless that name is bound in the rendered steps up to and including
  the step, in the pipeline preamble, or is `df`, `pl` or a builtin. On every stepped
  surface, a step that indexes `df` by an incoming input's edge name
  (`df['proposer_claims']`) is refused naming the step: `df` is one frame, not a
  mapping of inputs, and the refusal's `fix` says which frame `df` already is and how
  that surface reads its other inputs (by edge name on a Transform or Load File, the
  `[source, free_code]` form on a Transform). The checks are
  deliberately narrow: they never refuse a name they cannot tie to an input, so local
  helpers, preamble helpers, a column genuinely named like an input on a collected
  frame read through another name, and any other valid program pass. Each refusal
  carries `where` (`node`, `field: "steps"`, `step`) and a `fix`.
- **Writes to stepped nodes land or fail loudly.** `NodeData` rebuilds `code` from
  `steps` whenever a stepped type holds a `steps` list, so a `code` write to such a node
  would be overwritten while the plan reported success. Each `add_node` and
  `update_node` therefore compares the node as it stands before the operation (in the
  working graph, so an earlier operation of the batch counts) with what the operation
  writes, and refuses with an op error:
  - on a node whose config holds a `steps` list: any `code` key (a value or `null`),
    and a `steps` value that is not a list (`null` included), since removing steps
    would switch the node to code mode, which stays an analyst action in the editor;
  - on a stepped-type node in code mode (no `steps`, non-empty `code`): any `steps`
    key, since steps would replace the analyst's code and converting code into steps
    is out of scope. A stepped-type node with no `steps` and no code has nothing to
    lose and accepts a step list;
  - in `add_node` of a stepped type: a `code` key or a `steps` value that is not a list.
  Code-mode nodes keep `code` editing. Each refusal names the node, its surface
  (`STEPPED_SURFACE_LABELS`) and the free-code form to write instead: on a surface
  whose steps choose their input (`start == "input"`, the Transform)
  `[{"id": "start", "kind": "source", "input": "<edge name>"}, {"id": "logic", "kind":
  "free_code", "code": "..."}]`, and on every other stepped surface
  `[{"id": "logic", "kind": "free_code", "code": "..."}]`.
  After each operation's `with_config`, every key the model wrote must hold the written
  value in the materialised config (a key written as `null` must be absent), and when
  the operation wrote `steps` the config must carry no `_steps_error`. Otherwise the
  plan fails with the stable code `op_not_applied` naming the node and the key, or the
  step error. A palette-default node's own `_steps_error` (a Transform's empty step
  list) is not the operation's write and does not trip this check.
- **A rename never leaves a consumer silently broken.** `rename_node` rewrites edge
  endpoints only, while downstream nodes name their inputs by the edge's input name
  (`haute._graph_utils.edge_input_name`, the source's sanitised label). Before renaming,
  the operation compares each outgoing edge's input name under the old and the new
  label (an API Input frame or a submodel port keeps its name, so it is never
  affected) and, for every edge whose name changes, lists the fields of the target that
  still name the old input: a Transform's or External File's steps (`steps[i].input`
  on a source or join, `steps[i].inputs` on a concat, and `steps[i].code` when a
  free-code step reads the name); a code-mode node's `code` when it reads the name
  (unparsable code is listed, since it cannot be shown not to), unless its
  `inputMapping` binds that name to another edge; `inputMapping.<logical>` values;
  `input_scenario_map.<name>` keys; `data_input`, `banding_source`, `analysis_input`
  and `ratebook_input`; `outputMapping[i].source_port`; and the `inputMapping.<name>`
  keys of every instance of the target. Any node whose `instanceOf` names the renamed
  node's id is listed with `instanceOf`. When the list is not empty the plan fails with
  the stable code `rename_has_consumers`, a message naming each consumer and field, and
  a `consumers` array of `{node, field}` records; nothing is renamed. The check runs
  against the working graph, so an earlier operation of the batch that rewrites the
  consumer lets the rename apply. A node whose consumers reference it only through
  edges renames.
- **Unknown config keys are op errors, not warn-and-drop.** The sidecar writer's
  warn-and-drop exists to tolerate stale keys already on disk; an authoring-time unknown key
  is an LLM mistake that must bounce back as a tool error so the model corrects it. Same
  allowlist source, different strictness, both deliberate.
- **The assistant configuration is closed.** The outer accepted key set is
  exactly `provider`, `model`, `base_url`, and `egress`; the nested table has
  exactly the five required fields: `trust`, `max_sensitivity`,
  `allow_project_knowledge`, `allow_executable_source`, and
  `allow_row_samples`. Unknown or missing fields fail
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
- **One `PipelineDocumentUpdatePayload` contract**: the assistant publishes the identical
  payload shape the watcher publishes; no assistant-specific frame type exists on `/ws/sync`.
- **Bounded retention, turn-atomic**: the provider request carries the most recent
  complete turns within a 40-message budget plus the always-complete system prompt; stored
  history caps at 200 messages by evicting whole oldest turns. The window always holds the
  newest turn: when that turn alone exceeds the budget, its oldest completed tool-call
  rounds (an assistant tool-call message with all of its results) are dropped whole until
  it fits or no round remains, its user message and its other messages are always kept,
  and no older turn is added. A long turn therefore never leaves the next turn without
  its original request. No pruning boundary ever
  separates an assistant tool call from its result (an orphaned half is an invalid provider
  conversation). Live sessions are LRU-capped at 32 idle sessions with least-recently-used
  eviction — a session holding a running turn is pinned, never evicted and outside the cap,
  and eviction drops only the in-memory record: the persisted file revives the id
  transparently on next lookup.
- **Dataset discovery and schema inspection share one safety contract**: installed readable path
  extensions come from `routes.files._installed_input_extensions()` and are matched by
  case-folded filename suffix (including compound extensions). The resolved project-relative
  path must contain no hidden component; exact state/credential names in the assistant
  denylist are rejected before listing or reading. Direct calls cannot bypass the filter
  that navigation applies. `list_datasets(project_root, recursive)` defaults to a one-level
  listing; recursive mode walks only non-symlink descendants, returns deterministic
  project-relative POSIX paths, caps datasets and directories independently, and sets
  `truncated=true` when either cap or the traversal bound is reached. The `list_datasets`
  schema is the same for every request wording, and the source-bound executor runs the
  listing root and recursion value the model supplied. The assistant schema helper is separate from the UI
  schema/preview reader and never invokes the preview collector.
- **The read shape names handles the way the write shape does.** The compact graph
  renderer emits each edge's ports as `source_handle`/`target_handle`, matching the
  `add_edge`/`delete_edge` operation fields exactly. The camel-case `sourceHandle`
  spelling remains the persisted wire detail of the graph edge model and the frontend
  payload, and is never shown to the model: echoing it invited edit operations written
  in the shape the model had just read, which the closed operation schema then rejected
  as an unknown field. Every in-repo reader of that rendering follows the same names —
  including the self-test harness's `_graph_structure` (`scripts/run_assistant_self_test.py`),
  whose scoring compares Edge Join `base`/`join`
  roles. A reader left on the persisted spelling gets `None` for every edge without
  raising, silently scoring every handle-qualified required edge as missing.
- **Egress flags are honoured, not merely recorded.** `allow_executable_source` and
  `allow_row_samples` each gate a real capability: node `code`/`preamble`/`query`/`script`
  values in `get_node_config`, and `get_column_profiles` plus the text of authored-code
  failures (see `get_node_schema` step 5) respectively. A `free_code` step's `code` is
  masked by the same recursive key redaction, while the step's `id`, `kind` and the
  structured steps around it stay visible: structured steps are configuration, not
  executable source. A parsed,
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
| `source_file` outside the project root / holding a NUL byte | route (`contained_path`) | 403 / 400 |
| `source_file` that is not a discovered pipeline | route | 404 naming the file |
| Message `source_file` differs from the session's binding | route (after reservation, which it releases) | 409 naming the chat's pipeline |
| Turn already running on session | route (lock try-acquire) | 409 |
| Working branch not `"ready"` (`working_branch_status`) | `_tools` mutation pre-check | Structured tool error carrying the mapped per-state reason; status reports `mutations_enabled: false` with the same reason |
| Ledger capture fails after a committed save | save service (degrade-to-warning path) | Warning propagated into the tool result and activity row; next successful capture sweeps the delta |
| Provider adapter construction/dependency failure | route provider factory | HTTP 502 before the stream opens |
| Provider request/stream failures (authentication, rate limit, connection, malformed/truncated/filtered output) | `_providers` | `AssistantProviderError` → terminal `failed` SSE event after the response has started |
| Op validation, save validation, missing dataset, unknown node, unknown example name, unresolvable node schema (unfetched Databricks cache, missing artifact, invalid node code) | `_tools`/`_ops`/`_assets`/engine/save service | Structured tool error returned to the model (visible as a failed activity row); never terminates the turn |
| Authored-code failure (a Polars error, an exception raised from node code, or a preamble failure) while `get_node_schema`, a column profile, dry-run or apply resolves a schema or frame | engine, rendered by `_tools` | Structured `schema_unresolvable` (`preamble_failed` when the preamble fails during plan validation) error with the exception type, line or step, and only the named columns the egress policy already discloses (the failing node's input schemas, the model's own plan text, saved code only under `allow_executable_source`); the error's own text only when `allow_row_samples` is true; an unreadable egress policy withholds the text and names why |
| Working-branch state `"git-unavailable"` | `_config.mutations_readiness` | Status 200 with `mutations_enabled: false` and the fixed Git-unavailable reason; a mutation tool call returns `authority_denied` with it |
| A node write that would not land (a written key missing from the materialised config, a `steps` write whose rendering fails, steps the save's reparse would discard) | `_ops` operation replay, `_application` dry-run reparse proof | Structured `op_not_applied` tool error naming the node and the key or step problem; nothing is written |
| A Modelling or Load File node the plan adds or updates that is not ready (no target, an incomplete objective, a configured column its input lacks, a file that is missing or does not load as its `fileType`) | `_application._prove_nodes_ready` | Structured `node_not_ready` tool error naming the node and the product's message; nothing is written. A malformed modelling value fails earlier as save validation's 400, `operation_failed` |
| A rename whose consumers name the old input in configuration or code | `_ops` operation replay | Structured `rename_has_consumers` tool error listing each consumer and field, with a `consumers` array of `{node, field}`; nothing is written |
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
  post-batch (property: same batch, same graph → same positions); and the
  bare-`df` rule, parameterised over a bare read, a named input, a bind-then-reuse, and
  the nested scopes (`def`, `lambda`, comprehension) whose own `df` must not be mistaken
  for the module-level output variable; the free-code step rules (a discarded bare
  expression refused with its step, a Rating Step step reading its input by name refused
  with "Rating Step code sees only df", and a step calling a preamble or local helper
  accepted); the palette defaults `add_node` merges (and their omission on another
  branch or an instance); the stepped-node transition refusals and
  `op_not_applied` landing check; and the `rename_has_consumers` refusal for each
  consumer field, with edge-only and API Input renames applying.
  Also covers canonical revision/plan hashing, semantic diff boundaries,
  closed postconditions, single-use plan transitions,
  stale/altered-plan rejection before save,
  unrelated-diff detection, and truthful verification evidence.
- **`tests/test_assistant_catalog.py`** — completeness against `NodeType` (mirror of the
  registry-completeness test); folder/decorator facts agree with `_types`/`_config_io`.
  Source, sink-only and single-input sets and palette names are asserted equal to the
  product registries and to the editor's `nodeTypes.ts` declarations, read from source.
  Also pins merged I/O enums, palette defaults, resolved schema agreement, closed operation
  metadata, deterministic canonical hashing, cache reuse/invalidation,
  manifest compatibility identity, and the compact/full projection boundary. Every
  node descriptor carries its card without the test fixture: two configurations in
  order for an authorable type, a not-authorable note otherwise, and the banding card's
  date boundary, open-ended last band and string categorical value.
  `step_authoring` is pinned against `STEPPED_NODE_TYPES`: present exactly on the
  stepped types, with the surface's `start` and `inputs`, a source step first exactly
  on an `input` start, and every step carrying a non-empty id and a kind in
  `STEP_KINDS`; `get_authoring_guide`'s `step_grammar` equals the renderer's step
  fields.
- **`tests/test_assistant_tools.py`** — the turn context: the brief names each node's
  inputs with their columns and its authoring state (a palette-default Transform is
  `incomplete`, a hook `stepped`, a failing node `code` with no output and no error text)
  with the selection first and the revision `get_pipeline` reports; a `public` policy
  withholds the graph without reading it; an unknown selected or preview-error node is
  refused; the brief resolves once per revision and keeps only the latest revisions; a
  submodel node has no columns and a multi-frame node lists each port; the preview error is type, line and
  column under `allow_row_samples = false` and its text when row samples are permitted,
  and a resolving node reports none; a long brief stops before its bound with a pointer to
  `get_pipeline`, lists 40 columns per frame, and a label cannot break out of its line.
  Real tmp-project coverage for source/downstream
  schemas, preamble-dependent transforms, per-input schemas keyed by the code-visible
  input name (absent for a source node), the authored-but-empty transform's
  `node_has_no_code` success shape with resolved inputs (for code-less code and for a
  palette-default `steps: []` Transform alike), a frame-start node at `steps: []`
  resolving its ordinary schema, a group-by node resolving
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
  Actionable-error coverage runs through the source-bound executor: the August
  two-input failure (a Transform fed by two claim sources indexing
  `df['proposer_claims']`) is `invalid_ops` with its `where`, both inputs' columns in
  `context.inputs`, the `[source, free_code]` form and the by-name `fix`; a misspelt
  column is `schema_unresolvable` with `did_you_mean` and the replacement `fix`; a
  public policy refuses the dry-run before any column is named; an unknown tool names
  its close match; and three independent errors, each corrected from its error,
  converge in one scripted turn through the real tools to an applied outcome.
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
  collection is poisoned to enforce the no-row boundary. Steps-first authoring is
  replayed through the real `dry_run_graph_edits` and `apply_graph_plan` on a project
  saved with palette-default nodes: a two-input August claims aggregation filled as
  `[source, free_code]` on the named Transform, a palette-default Rating Step filled
  with `[free_code]`, and a Load File whose free-code step uses `obj` and a second
  input by name each apply in one dry-run, reparse with their steps and no
  `_steps_error` or `_steps_discarded`, and execute on fixture data. Project evidence
  coverage pins a dataset changed after retrieval failing the dry-run with
  `stale_project_evidence` naming the file, and a dataset renamed after inspection
  blocking the dry-run with `project_source_missing` naming it until `list_datasets` runs
  again, in the live turn and when replayed from history. A dry-run adding an Excel Data
  Input is refused as `schema_unresolvable` whose tool message carries the reason and
  the "Preview this input first" remedy.
- **`tests/test_assistant_assets.py`** — the authoring guide loads non-empty via
  `importlib.resources`; every example is a content-addressed bundle (no
  single-file example remains) and parses through `parse_pipeline_to_graph`; the
  removed `joined_reference` is refused with its replacement named; bundle manifests are closed,
  inventories reject unknown roles, missing/digest-mismatched/undeclared
  files, expected-schema/assertion drift, and missing required artifact
  classes; the declared fast subset executes against synthetic data. Every
  bundle parses, regenerates through `graph_to_code_multi`, and accepts a no-op
  edit through `PipelineApplicationService.dry_run`; the example index and
  `load_example` exclude `teaching: false` test fixtures.
  `load_example` returns bounded attribution, narrative, and the same graph shape as live
  inspection, with each node's configuration and its values (the discrete-banding
  example's categorical rules and `$[:]` output path are readable); resource inventory
  names and paths are absent from the model-facing result.
- **`tests/test_assistant_node_cards.py`** — every authorable card configuration is
  written into a fresh short-path project with its fixture, dry-run and applied through
  `PipelineApplicationService`, reparsed with its config keys intact, and executed by
  `execute_graph`; each declared input edge's executed columns and dtypes equal the
  card's, the node produces the card's columns, and declared expected rows match. A
  Model Training card also trains through `/api/modelling/train` and an Optimisation
  card solves through `/api/optimiser/solve`, both to `completed`; a Model Scoring card
  scores a tiny CatBoost model logged to the project's local MLflow folder; added API
  Inputs have their table snapshots built first, as a preview would. A non-authorable
  card's note contains the operation layer's refusal and the dry-run refuses the type.
  The walkthrough guesses (breakpoint rules written as `lower`/`upper` intervals, an
  operator-row `continuous` factor, and response paths `quote_id` and `$.quote_id`)
  replay through `dry_run_graph_edits` to their structured refusals, the card's field
  meanings state the fix, and the card's configuration on the same request passes its
  first dry-run at the schema tier. A malformed card (an unknown key, configurations out
  of order, no field meanings, a not-authorable card with configurations) raises
  `NodeCardError`.
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
  plan itself changed is never excused. The stepped-node write contract is pinned on
  every palette-default stepped type, each with a valid base (a readable source, a
  scorable model, a rating table): `update_node {code}` and `{steps: null, code}` are
  refused at dry-run with the file unchanged; a Transform's `[source, free_code]` and a
  frame-start surface's `[free_code]` write apply and the saved body holds them; a
  Data Input added with only a path saves with the palette's `inputType`; the
  `node_config` projection survives a real save and reparse on every code-carrying
  palette type; a saved config that no longer matches its `node_config` digest is a
  committed verification failure; steps whose generated body would not reparse
  fail the dry-run with `op_not_applied`; and the `new_logic` list each stepped
  descriptor advertises, which the system prompt spells the same way, applies
  verbatim on its surface, with only the `<edge name>` placeholder replaced.
  Dry-run runs the save path's codegen name-collision check, so a submodel
  occurrence named like a node inside its definition fails at dry-run, and an
  edge out of Model Training is rejected at dry-run. Malformed and unready nodes fail at
  dry-run with the product's message: `algorithm: "GLM"` with `family: "Poisson"`,
  `family: "poison"`, `algorithm: "gbm"` and a target listed in `feature_columns` as save
  validation's 400; an `offset` the input lacks, a GLM with no family and a Load File whose
  path does not exist as `node_not_ready`. A valid GLM (`glm`, `poisson`, log link,
  `exposure` offset) applies, and an edit beside a saved empty modelling node applies.
  A plan adding a CSV Data Input with a `;` separator and a schema override, a Transform
  and their edge applies with an `input_schema_inferred` record (tier `inferred`, format
  `csv`, the bounded row count), the Transform's resolved schema reflects both settings,
  post-save verification reproduces that evidence, and no snapshot generation is written.
- **`tests/test_assistant_project_knowledge.py`** — source attribution,
  sensitivity filtering, cache invalidation/rebuild, bounded queries, tool
  policy, symlink containment, and metadata-only durable cache state.
- **`tests/test_assistant_evaluation.py`** — held-out/teaching separation,
  closed support matrices, semantic and zero-tolerance safety scoring,
  repeated-trial attribution, aggregation, redacted reports, and fail-closed
  qualification decisions.
- **`tests/test_assistant_self_test.py`** — closed case-format v2 loading and
  selection over the checked-in portfolio (categorical banding, exact join roles, positional
  rating steps, Polars transforms, explicit mapped response outputs, file input/output graph
  authoring, material clarification for joins/rating/output mappings, prompt injection,
  and blocked pipeline execution/external writes). Scoring requires a successful terminal
  outcome with bounded failed tool attempts and duplicate static reads, connectivity of the
  changed nodes and their neighbours (nodes added or retyped, and the endpoints of added or
  removed edges, together with every node adjacent to them, form one connected component; the
  report names the nodes outside its largest component, and an untouched node elsewhere in the
  fixture never fails the check), and exact edge-join base/join port assertions; each reason
  carries its layer. The configuration layer matches subsets recursively and names the first
  differing path, the collateral layer reports a changed or removed pre-existing node unless the
  case allows it, and the editor layer reports a new stepped-type node not authored as steps or
  carrying `_steps_error`, while a pre-existing code-mode node is not judged. Case loading
  refuses a required or forbidden node-type name that is not a `NodeType` value. A null
  expected target handle matches an edge by source/target endpoints for ordinary single-input
  nodes; non-null handles remain exact port assertions. `_graph_structure` is pinned directly
  against the compact renderer's own output, because reading a handle under a name the renderer
  does not emit yields `None` without raising and turns every port assertion into a silent
  pass-through failure. For multi-round text, scoring uses the last explicit `NEEDS_INPUT:` or
  `BLOCKED:` marker, so earlier preparatory prose cannot hide the final qualified outcome.
  Coverage also pins the redacted report shape with its evidence kind and per-layer results, a
  report refusing to mix replay and live evidence, a scripted-provider run under the harness's
  own egress allowances, and one scripted case through the command's per-case process runner.
- **`tests/test_assistant_replay.py`** — tier 0 of the evaluation: every checked-in reference
  trajectory (`tests/assistant_eval/trajectories/`) replays through the real loop, tools,
  dry-run, apply, parser and disposable Git mutation gate in a copy under `tmp_path`, within a
  per-case timeout and with a fresh plan store, passes every scoring layer, is labelled replay
  evidence, and leaves the sandbox project root and working directory where it found them. Every
  case has a trajectory and each step-corpus case has both its free-code and its structured
  form; the structured trajectories write the corpus translations, the corpus goldens are the
  corpus snippets, and `polars_corpus`'s data files equal the corpus's normal synthetic inputs.
  No single-node trajectory reads before its first dry-run, and the feature transform's first
  provider request carries a turn context listing `quotes` and its `driver_age` column.
  A recorded status the tools no longer return raises a divergence naming the trajectory, turn,
  round and call; a golden the saved node does not reproduce fails only the execution layer; and
  a `$result` reference to a later call fails trajectory loading.
- **`tests/test_assistant_example_portfolio.py`** — live/batch parity, vehicle
  age derived as 2026 minus the vehicle year in both bundles that teach it, trace
  and structural dry-run, real model training/scoring, real online/ratebook
  optimisation (the ratebook solve takes its rating factors from a Banding
  node), saved versioned apply of the Banding node's output, deployment preflight plus unsafe
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
  `mutations_enabled`/`mutations_reason` across all seven `working_branch_status` states
  (ready, no-repository, unset, detached, divergent, invalid, git-unavailable — asserting
  each state's mapped reason, including invalid's joined `errors`), and the named
  policy reason under `trust = "external"`.
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
  A turn context is a mid-conversation `system` message for the Anthropic models that accept
  one and otherwise the leading text of the analyst's message, and one that follows no user
  message fails loudly.
- **`tests/test_assistant_prompt_golden.py`** — the rendered system prompt, the two
  turn contexts, canonical
  tool definitions and both provider wire projections match the golden files; the
  release version and capability hash are normalised out; an added prompt sentence
  and an edited wire-operation field description each fail with the changed lines in
  the unified diff of every affected file and of the hashes file.
- **`tests/test_assistant_loop.py`** — against a scripted fake provider: text-only turn;
  tool round-trip; tool error fed back; cap and timeout terminal events; completed/failed
  exactly-one-terminal checks; cancellation drains an in-flight tool, closes the provider
  stream, and releases the session lock; closing at each tool lifecycle yield never
  persists an unmatched call; a raising history append still releases the lock;
  turn-atomic history windowing, including a tool-heavy turn crossing both caps without
  splitting a call/result group, and a twenty-one-call turn followed by a turn that still
  sees its original request; the dry-run progress rule — an identical resend stops
  after two attempts, the same diagnostic for an unchanged operation stops, the same
  diagnostic after its operation changed does not, four failures that each change the
  plan spend the budget whatever their class, three independent errors converge to an
  applied turn, and repeated malformed calls block with their own wording; read-only
  questions and authoring wording without a dry-run attempt end completed with no
  controller continuation; an end after an attempted but unapplied mutation receives one
  internal controller continuation, successful apply terminates with deterministic text and no later
  provider/tool round, explicit `NEEDS_INPUT:`/`BLOCKED:` outcomes terminate normally, and a
  second unqualified end fails rather than completes. The turn context follows the user
  message in every round, joins the routed guidance, is absent when there is neither, and
  is never stored; the system prompt takes only the source file and holds no policy or
  pipeline facts, and the rendered context states the policy and the column-value rule.
  Every completed turn carries and
  stores its typed outcome: `answered` for a reply without a mutation attempt,
  `needs_input` for a question with or without one, `blocked` from the model or from an
  stopped dry-run budget (its detail being the streamed text after the marker),
  `applied`, and `committed_unverified` for an apply whose save committed but failed
  verification, which ends the turn at once with no later tool or provider round; a
  failed turn stores none. The system prompt states the
  steps-first rule with both `new_logic` forms, names every stepped surface by its
  palette name, and no longer teaches `df` as a code-only output variable.
- **`tests/test_assistant_routes.py`** — status/sessions/session/message endpoints: SSE framing,
  400/404/409 mapping, sanitized unexpected-error paths, readiness reasons on status,
  transcript rehydration (a stored turn outcome closes its turn as an `outcome` entry),
  adapter construction, atomic concurrent-send reservation, and
  lock release on pre-stream failure, disconnect, and mid-stream send failure. The list
  endpoint pins the requested pipeline's conversations with their titles and counts, an
  empty project, and the unknown-source 404. Source binding pins that in a two-pipeline
  project a chat created for the second pipeline runs its tools on that file, that a
  message for another pipeline is a 409 naming the chat's pipeline which frees the
  session, that an escaping source file is a 403, and that a request without
  `source_file` or with an unknown field such as `pipeline` is a 422. Two turns of one
  session send the same system prompt while the pipeline, the policy and the selection
  change, each turn's context carries its own graph, policy and selection after the user
  message, and the first turn's context is not replayed; a selection the saved pipeline
  lacks is a 409 that frees the session; the message context is closed, unique and at most
  20 ids.
- **`tests/test_assistant_session_persistence.py`** — atomic write-through persistence,
  restart revival, invisible LRU eviction, corrupt/invalid-file logged misses, session-id
  path hardening, oldest-first persisted-file pruning, abandoned temp-file cleanup,
  tool-error round-trip, turn-outcome revival, an outcome detail redacted like assistant
  text, a turn record without its `outcome` key treated as invalid, internal-controller
  revival/transcript hiding, absence of
  deterministic payload digests, safe validation path/reason retention, and non-fatal
  persist failures. Listing coverage pins recency ordering and per-pipeline scoping,
  omission of empty conversations, titles bounded and whitespace-collapsed, survival of a
  restart without reviving any session into memory, and an unreadable file skipped with a
  warning. A syntactically valid file that fails the same id, timestamp, source, or history
  validation used by revival is skipped too; listing never advertises a conversation that
  cannot subsequently be opened.
- **`tests/test_assistant_integration.py`** — fake-provider end-to-end on a tmp project:
  instruction → ops → real transactional save (files on disk assert codegen/sidecars) →
  `pipeline.document.update` published with the post-save fingerprint (asserted via a test
  subscriber)
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
is also the explicit credentialed developer lane (tier 1 of [the assistant evaluation](evaluation.md)): it loads the same project `.env` and
`[assistant]` configuration as the app, accepts repeated `--case` selection (plus `--list`),
runs each selected prompt once in an isolated fixture, each case in its own spawned process.
Entering and leaving a fixture clears the process-cached active pipeline directory and binds,
then restores, the sandbox project root, so one project or the invoking repository cannot
escape into another fixture's application service. The invoking project supplies the provider,
model, credentials and provider trust (trust describes the endpoint); the egress allowances are
the harness's own, never the invoking project's: `max_sensitivity = "internal"` with project
knowledge, executable source and row samples all withheld. An `external` trust is refused
before any case runs, because external trust is public-only and a public ceiling denies the
project metadata tools every case needs. The lane exits non-zero on any failed case and
optionally writes the redacted report described above. Cases may expose only synthetic schemas,
the synthetic request, and ordinary assistant tool context to the configured endpoint. After
each turn the harness, not the assistant, executes only the case's golden nodes, each up to that
node, so it never materialises a configured output sink. It is a fast
diagnostic and regression loop, not release qualification; repeated support-matrix trials remain
the authority for model qualification. The package is covered by the repository's global branch
gate, while exemplar
`.py` assets are omitted from coverage because they are parsed package data rather than
importable modules (they remain parser- and lint-checked).
