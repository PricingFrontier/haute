# Assistant — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/assistant/__init__.py` | Public package seam; re-exports only `assistant_readiness`. The FastAPI router remains in the routes package and is not re-exported here. |
| `src/haute/assistant/_config.py` | Resolves assistant configuration: the outer `[assistant]` table is closed to provider/model/base URL/egress and the required nested `[assistant.egress]` table is closed to trust, maximum sensitivity, and the three `allow_*` booleans. It validates endpoint/trust combinations and credential-free OpenAI URLs before SDK/key probing. The first-class Databricks mode rejects `base_url`, reads DATABRICKS_HOST / DATABRICKS_TOKEN, validates a credential-free HTTPS workspace-root host, and derives `<host>/serving-endpoints`. Other credentials come from their named environment variables. It produces `AssistantConfig`/`AssistantReadiness` including safe endpoint host, trust, and sensitivity status. It owns `ADAPTIVE_THINKING_MODELS`, the Claude models the Anthropic adapter runs, and `unsupported_anthropic_model`, the not-ready reason for any other Anthropic model, which readiness reports and the adapter raises. |
| `src/haute/assistant/_catalog.py` | The versioned capability registry, the assistant's one node catalogue. It derives mechanical node facts and resolved JSON Schemas from Haute's canonical types, config validation, config I/O, node registry, Polars I/O registry, and save validation; owns completeness-checked semantic metadata; declares the closed operation descriptors consumed by the tool layer; serves each node type's card from `_node_cards.py` as its descriptor's `card`, and at import refuses a card configuration whose keys fall outside that type's closed config schema; computes canonical manifest identity; and caches immutable manifests by installed version plus capability hash. It also holds the activity-row titles and the running tool call's progress reporter (`report_tool_progress`, which a tool calls to retitle its row, and `tool_progress_reporter`, which the loop sets for each call). |
| `src/haute/assistant/_node_cards.py` | Loads and validates the packaged node cards (read via `importlib.resources`, cached). Exactly one card per `NodeType` must exist, each a closed JSON object: an authorable card has `fields` (config path to meaning), `inputs` (edge, optional source and target handles, column-to-dtype map), `configs` (exactly `minimal` then `realistic`, each with `intent`, `config`, `produces` and optionally its own `inputs`) and a `fixture`; a card for a type the assistant cannot author has only `authorable: false` and a `note`. A missing, unexpected or malformed card raises `NodeCardError`. `node_card(node_type)` returns the model-facing card without its fixture; `node_card_fixture(node_type)` returns the fixture for the CI harness, or nothing for a non-authorable card. |
| `src/haute/assistant/assets/node_cards/<nodeType>.json` | One node card per node type, library content with no project data. Its closed `fixture` holds `frames` (tiny rows, with `dates` naming string columns to parse as Date, each written to `data/<name>.parquet` and added as a Data Input only when the plan reads it), `files` (project paths to JSON values or text), `upstream` and `downstream` operations placed around the card node, `model_run` (the frame and features of a tiny CatBoost model logged to the project's local MLflow folder, whose run id replaces the card's `run_id` placeholder), and `expect` (per-configuration expected rows of produced columns). |
| `src/haute/assistant/_assets.py` | Loader and verifier for the assistant's packaged knowledge assets (read via `importlib.resources`): resource enumeration, `authoring_guide()`, and `example_index()` are cached; `example_index()` lists only bundles whose manifest sets `teaching: true`, and `load_example(name)` refuses any other name as an unknown example; for a teaching bundle it materialises the complete example tree for validation, then returns only a self-contained model-facing attribution/narrative/rendered-graph view, its node configurations rendered with their values, with no inaccessible resource inventory. `validate_example_bundles()` checks closed manifests, content digests, evidence-resource roles, graph/schema assertions, positional golden output, and the declared installed-package fast checks; `materialize_example_bundle()` copies one already-validated project into an empty destination for specialist ordinary/negative checks. The guide fails loudly if missing/empty, index summaries come from the first module-docstring line, and an unknown name is a structured error listing valid names. |
| `src/haute/assistant/_recipes.py` | Versioned immutable recipe registry and the deterministic `expand_recipe_operations`, which expands each recipe operation of a dry-run batch in place through `expand_recipe`. Each descriptor has a closed argument schema, unresolved decisions, preconditions, allowed primitive operation kinds, postconditions, linked example bundles, and stable failures. Explanation-only requests do not route to mutations, while a later explicit sequenced authoring clause retains mutation intent. Each expansion is round-tripped through `_wire_ops.parse_ops`; unknown recipes and invalid arguments fail by stable code with the index of the `recipe` operation. |
| `src/haute/assistant/_project_knowledge.py` | Source-linked project-knowledge extraction, bounded query selection, and disposable content-addressed index. Derives a saved-graph fact, a value-free `haute.toml` digest fact, and allowlisted UTF-8 documentation evidence; labels unmarked document sensitivity as restricted; records source digest/extraction version/evidence class; filters by `EgressPolicy`; and atomically refreshes metadata-only cache state under `.haute/assistant/knowledge/`. An allowlisted document that is not valid UTF-8 fails the read with a typed, project-relative error instead of silently disappearing. Dataset schemas remain a separate schema-only tool result. |
| `scripts/run_assistant_self_test.py` | Developer-facing evaluation harness, outside the installed package, for both tiers of [the assistant evaluation](evaluation.md), and its live runner. It loads the closed case format v5 (`load_self_test_cases`: area, split, egress profile, inapplicable variants and one or more turns, each with its expectations, a recovery case's declared data findings among them, their kinds checked against `ADVISORY_KINDS`, which is read from the data check's closed finding shapes) and reference trajectories (`load_trajectories`), copies each case's project fixture into a caller-supplied empty work directory, logs the fixture's model artefacts into the copy's local MLflow folder and writes their run ids over the Model Scoring placeholders (`prepare_fixture_models`), writes the case's egress profile (one of `EGRESS_PROFILES`, as `egress_policy` defines it) at the invoking project's provider trust, never the invoking project's own allowances, into the copy (`self_test_config`; `evaluation_trust` refuses an external provider), binds the sandbox project root to that copy for the case and restores it afterwards, builds every snapshot-backed input's snapshot as a preview does (`prepare_fixture_snapshots`), initializes the real Git mutation gate, and runs every turn of the case in one session through the provider-neutral loop with the real tool executor, built each turn on the session's evidence ledger and build plan as the message route builds it, with the turn context carrying that plan (`run_self_test_case`); a case transcript records each build-plan update beside the text, tool calls and outcome. `TrajectoryProvider` and `replay_self_test_case` replay a trajectory with `$result` substitution and raise `TrajectoryDivergedError` when a tool result's status or error code differs from the recording. After each turn the harness executes that turn's golden nodes of the flattened saved graph under their scenarios and compares each with its golden by column name (`execute_goldens`; an exception the saved pipeline raises there is an execution reason naming its class and message) and `score_turn` scores the protocol (from the completed event's typed outcome and its saved changes), structure, configuration (subsets matched recursively, same-length lists element by element), collateral, editor and execution layers and any efficiency limits, and beside them the data findings layer (`score_data_findings`: the checks the model received, read from each tool result by `received_check`; the harness's check of the saved graph over the turn's `changed_nodes`, run through `run_node_data_check` in a preview worker under a session of its own by `final_data_check`; and the change cards' checks, `change_card`), which judges the saved graph, gates a case only in the `recovery` area and classifies each turn for the recovery metric as recovered, avoided or not recovered by what it received and its saved graph; `frames_equal` is the one frame comparison and `config_digest` the per-node configuration digest. `VARIANTS` names the configuration variants; the one-apply-per-turn variant is applied by the observed provider, which ends the turn in place of the round after a saving apply, the canonical-tools variant by rebuilding the case's Databricks provider with the canonical tool projection (`canonical_tools_provider`; any other provider is refused before the case runs, since the other lanes already receive it), and a case is never run under a variant it lists as inapplicable. The command (`python -m scripts.run_assistant_self_test`) has `record`, which runs each selected case that applies to the variant live in its own spawned process inside a directory the parent removes once that process has exited, records a case that raises or whose process ends abruptly as that case's crash (`crashed_result`) and goes on to the next, and writes the report, with the inapplicable cases listed as not applicable, and, on request, transcripts; `compare` and `list` make no provider call. A case run in process mode starts the interactive preview workers inside its project copy, as the server starts them, and shuts them down with the case (`_preview_workers`), so its dry-runs' data checks run; a transcript records each tool result whole, a dry-run's data check included. |
| `scripts/assistant_eval_report.py` | The evaluation's report and comparison, outside the installed package: `report_payload` builds the closed content-redacted report v7 (run identity, the run's recovery metric, per-area counts of cases run, passed, crashed and not applicable, with the data findings counts, the area's recovery metric (cases that received an advisory finding, recovered and avoided one, and the recovered-within-budget rate, recovered over received) and the median of each efficiency metric over the cases that did not crash, per-case egress profile, correctness layers, first failing layer, reasons, crash traceback, data findings layer and metrics, per-turn data findings by kind, node, status and reason, per-turn outcome, saved-change count, graph structure and value-free tool diagnostics, and the cases not applicable to the run's variant, listed apart) from results of one evidence kind, `write_report` writes it atomically, `compare_reports` compares two reports of one evidence kind per area (counts, data findings counts and recovery metrics, flips with their first failing layer among the cases both ran, data findings flips between passed and failed, both runs' recovery metrics with the differences of their rates and avoided counts, each report's not-applicable cases, cases only one report holds, metric medians and their differences), `write_transcript` writes one live case's local-only transcript, and `load_support_matrix` reads the closed support matrix v2 that attributes a live report to a configuration. |
| `src/haute/assistant/assets/examples/<id>/manifest.json` | Closed executable-bundle manifest (`schema_version=1`, stable id/version, summary, source, `fast`/`ordinary`/`negative` assertion tier, required `engineering`/`pricing` review class, required boolean `teaching`, and a closed-role resource inventory). `teaching: false` marks a test fixture that is validated and materialised but never offered to the model: `deployment_safety` and `invalid_adversarial` are the test fixtures, every other bundle teaches. Review class records the required discipline rather than asserting approval; model-validation and optimisation fixtures use `pricing`, while purely mechanical fixtures use `engineering`. Every bundle includes its project configuration, source, synthetic input, graph/schema expectations, golden request/output, boundary cases, paired prompts, and semantic assertions. Assertion files have only `target`, non-empty `required_columns`, optional `row_count`, and a non-empty closed `checks` list. Golden arrays retain production row order, so order-unstable operators are followed by an explicit stable pipeline sort rather than normalized by the verifier. Every declared resource resolves inside its bundle, exists, and matches its recorded SHA-256 digest. |
| `src/haute/assistant/assets/authoring_guide.md` | Packaged, hand-authored Haute idiom: canonical pipeline shapes written with vectorised Polars expressions, the per-surface `df` rule the system prompt states, what `haute init` scaffolds (a blank pipeline), naming and stage-chaining conventions, and do/don't guidance returned with source/version/digest/evidence attribution by the authoring-guide tool; it is not embedded in every system prompt. |
| `src/haute/assistant/assets/examples/<id>/pipeline.py` | Every example is a bundle; there is no other example format. The bundle source is parsed as data by `_assets.py`, never imported, and rendered in the same compact graph shape as the get-pipeline tool, each node's configuration carried with its values; its module docstring supplies the narrative and the index summary. `linear_pricing` teaches implicit wiring through a source, one Polars enrichment and an output; `branched_features` teaches parallel feature branches joined before the response with explicit connections. `model_lifecycle` ends its training branch at Model Training and feeds the response from Model Score; `online_scenario_optimisation` ends at the Optimisation node and feeds the response from the scored scenario frame; `ratebook_optimisation_apply` bands the raw rating column through a Banding node that is both the optimiser's `banding_source` and Apply Optimisation's ratebook input; `reusable_submodel` names its occurrence `enrichment`, apart from the `enriched` node inside its definition. A request for the removed `joined_reference` example is refused with `example_removed`, naming `reference_join`, which teaches the same edge join. |
| `src/haute/assistant/_wire_ops.py` | Closed provider-wire graph-edit models plus graph-independent `parse_ops` validation. It imports no assistant modules, so recipes, the capability catalogue, and the graph domain layer share one operation vocabulary without lazy imports or dependency cycles. |
| `src/haute/assistant/_ops.py` | Pure graph-edit domain layer, re-exporting the wire vocabulary for its existing public seam: ordered graph application, assistant-authoring validation (including connected new nodes and retained Polars results), canonical snapshot/revision and semantic-diff functions, typed plan models, deterministic verification policy, postcondition evaluation, the bounded single-use `PlanStore`, and `SourceEvidenceLedger`, a session's live evidence ledger (see Edge cases, **Evidence ledger**). `_evidence_manifest_entry` names the project-relative file in its missing-source and stale-evidence messages, with the tool call that refreshes it (listing datasets for a vanished dataset, reading a changed dataset's schema, querying project knowledge for a document). It performs no writes. Its plan store keeps the data check of a plan's latest dry-run beside the plan (`record_data_check`, `data_check`), outside the plan's authority. |
| `src/haute/assistant/_render.py` | Shared compact graph renderer for live pipelines and packaged examples. It emits bounded node/config summaries (a live pipeline's node configs as their key names and count; with `config_values=True`, which only the example loader passes, each config whole as JSON values), edges and handles, preamble presence/digest, and singleton presence without executable source or row values. Edge handles are rendered under the exact field names the graph-edit operations accept, so the shape the model reads back is the shape it must write; see Edge cases. It also owns the turn context: the frozen `TurnContext`/`BriefNode`/`BriefInput` data, the egress policy words with the column-value rule, and the pure bounded `render_turn_context`, which the route, the self-test harness and the golden script share, and the `ContextUpdate` data with `render_context_update`, the turn context update placed after an apply's round. The turn context lists the session's build plan while it has an open item. It renders an earlier turn's compact record too: `render_turn_record` writes the assistant half of a `TurnRecord` (the turn's final text, then Haute's record of its outcome, saved changes, end revision, the build plan as the turn left it when it changed it, and later undos) and `render_omitted_turns` the note that leads a history whose oldest records were dropped. |
| `src/haute/assistant/_application.py` | `PipelineApplicationService`, the stateful inspect → dry-run → apply → verify service. It composes the public parser, the save service's no-write validation and transactional save, shared save lock, plan store and document-update publisher; transport and model tools are adapters only. Schema validation resolves through `execute_lazy_graph(..., schema_only=True)`, and owns both the seed rule and the pre-existing-failure rule described under Plan/apply/verify. It returns a `DryRunResult` (the plan, the compact view the dry-run tool returns and the candidate graph the data check measures) and an `ApplicationResult` whose `as_dict` is the compact apply result carrying the change record; the record's parent commit comes from `_git.commit_parent`. |
| `src/haute/assistant/_change_record.py` | The value-free change builder: `graph_changes(before, after, diff)` turns a semantic diff and the graphs on either side of it into node chips (id, palette type name from the capability manifest, `added`/`changed`/`removed`/`renamed`, the earlier id of a renamed node, changed fields in words, step kinds and changed-step count) and added and removed edges, each bounded at 50; `change_record` adds the record's id (the saved plan's hash), the plan receipt, the save warnings and the commit and its parent; `touched_node_ids` lists the node ids a batch of records names, for the turn context update; `evidence_summary` reduces verification evidence to its counts and input names; `change_data_check` is the change card's view of a plan's stored data check under its visibility, each finding worded by `finding_words` and a check that did not run by `not_run_words`, which the activity rows' summaries share. The dry-run result, the apply result and the stream event all use these. |
| `src/haute/assistant/_build_plan.py` | `BuildPlan`, one session's build plan (see Edge cases, **Build plan**): it holds the current `AssistantBuildPlan` snapshot, or none, and replaces it whole on every change, so a caller detects a change by identity. `update(items, complete)` sets or revises the items and claims one complete, all or nothing; `require_item` refuses an id the plan lacks before an apply saves; `record_change` records a committed change against an item; `undo` marks a change undone and reopens a complete item left with no change that is not undone. A refusal raises `BuildPlanError`, carrying a stable code, a message, `where`, `fix` and extra fields the tool error spreads. `build_plan_view` is the compact item list the tool result shows the model. It reads nothing from the project and imports no other assistant module. |
| `src/haute/assistant/_tools.py` | Thin adapters over the capability registry and `PipelineApplicationService`. Read tools retain their bounded renderers, including bounded recursive dataset discovery; `inspect_node` composes the schema, config, profile and data parts (`node_schema`, `node_config`, `column_profiles`, and the data check's `run_node_data_check`), each behind its own egress check. `build_turn_context` gathers the turn context's facts (revision, brief nodes resolved schema-only and cached per revision, validated selection, policy-reduced preview error) on the same schema helpers as `inspect_node`'s schema part, and `build_context_update` the facts of the update after an apply (new revision and the brief entries of the nodes the saved change records name) from the same cache; `context_update` gathers them under the save lock and renders them for the loop. Config redaction is policy-driven: credentials and row values are never eligible, while executable keys follow the project's own `allow_executable_source` decision rather than being redacted unconditionally. Value profiling is the one adapter that returns values from data and is gated on the egress policy's row-sample permission; see Control flow. The data part returns value-free counts under the aggregate-statistics permission (see [Data checks](#data-checks)). Each source-bound executor writes the schema/content evidence its successful results return into the session's `SourceEvidenceLedger` (`build_tool_executor(..., evidence=session.evidence)`; an executor built without one gets a fresh ledger of its own) through `_observe_project_source_evidence`, where a successful `find_data` first drops schema evidence whose file no longer exists; building the executor starts a turn on the ledger, and before each dry-run it releases carried evidence that no longer holds (see Edge cases, **Evidence ledger**). The executor also takes the session's `BuildPlan` (`plan=session.build_plan`; one built without it keeps a plan of its own): the build-plan tool updates it, and an `apply_graph_plan` naming an `item` is refused before it saves when the plan lacks that item and records its committed change against the item afterwards (see Edge cases, **Build plan**). `apply_graph_plan` is the only tool that writes the project; the mutation path is `dry_run_graph_edits` (its `recipe` operations expanded first) followed by `apply_graph_plan` with the exact returned plan hash, and operations cannot be resent at apply time. Tool code does not own revision, save, or verification policy. `dry_run_graph_edits` takes a `DataCheckGate` (the turn's policy and session) and, when that policy permits data checks, checks the stored plan's data (`_checked_dry_run`, see [Data checks](#data-checks)). It imports the incomplete-transform message from `src/haute/_code_extraction.py` (owned by [codegen](../codegen/low-level.md)). |
| `src/haute/assistant/_data_check.py` | The data check's engine (see [Data checks](#data-checks)): `run_data_check`, which decides eligibility on the server from configuration, file metadata and published-generation pointers, admits one preview-eager execution and runs the job pre-emptibly in the interactive worker under one absolute deadline, one check per session (a newer one supersedes, a cancelled turn stops it); the worker half `measure_candidate` (input verification and freshness, the binding read before and after the measuring walk, the walk's measurement queries, node records and findings); the closed result shapes (`DATA_CHECK_VIEW`); `fit_data_check`, the model-facing reduction; and `data_check_visibility`, the freshness comparison a consumer runs. Its sibling entry point `run_node_data_check` checks one saved node's lineage for the node inspection's data part, optionally following one column's nulls, with its own view (`NODE_DATA_VIEW`) and reduction (`fit_node_data_check`). Whether a check runs at all is its caller's decision. |
| `src/haute/assistant/_session.py` | Session store: `AssistantSession` records (id, bound pipeline `source_file`, provider-neutral user/assistant/tool/internal-controller history including required tool-result `is_error`, each turn's typed outcome (`AssistantTurn.outcome`, none for a failed or cancelled turn; a persisted turn requires the `outcome` key and its detail is redacted like assistant text), per-session `asyncio.Lock`, the live `SourceEvidenceLedger` (never serialized), the session's `BuildPlan` (`build_plan`, persisted as its current snapshot under `build_plan`, and each turn's snapshot as the turn left it, `AssistantTurn.build_plan`, set only on a turn that changed the plan; item titles are redacted like assistant text, and a file without the keys, written before build plans, revives with no plan), timestamps), create/lookup/resume, `record_undo` (which also applies the undo to the build plan before it persists), `list_sessions` for the chat list, the compacted provider history (`provider_history`: earlier turns as `TurnRecord`s within `PROVIDER_HISTORY_CHARACTERS`; see Edge cases, **Compacted provider history**), and bounded retention. Controller messages are provider-visible but transcript-hidden. Durable tool arguments/results become `{"redacted": true}` plus approved revisions/evidence (`_PERSISTED_TOOL_EVIDENCE_KEYS`, which include a dry-run's operation count and an apply's applied-operation count), value-free validation diagnostics, and a successful apply's `change` record, validated against `AssistantChangeRecord` with its summary and assumptions redacted like assistant text; deterministic payload digests are forbidden because finite-domain values are enumerable. Persistence, revival, corruption handling, pruning, and non-fatal write degradation retain their existing contracts. |
| `src/haute/assistant/_providers.py` | The `AssistantProvider` protocol and its three public adapters: `AnthropicProvider` (`anthropic` SDK, Messages streaming API), `OpenAIProvider` (`openai` SDK, Chat Completions), and `DatabricksProvider`. Databricks subclasses the OpenAI-compatible implementation but retains the `databricks` provider identity for client construction, logs, and typed failures. A neutral `context` message becomes a mid-conversation `system` message for the Anthropic models in `MID_CONVERSATION_SYSTEM_MODELS` and otherwise the leading text of the preceding user message; one that follows a round's tool results (the turn context update) is otherwise a text block after the tool-result blocks on the Anthropic wire and a user message after the tool messages on the OpenAI wire. The Anthropic adapter sends the system prompt as one text block carrying the request's one prompt-cache breakpoint, enables adaptive thinking with `ANTHROPIC_EFFORT` (`medium`) for the Claude models that take it (the configuration module's set, which readiness checks too) and refuses any other model at construction, emits `ThinkingStarted` when a thinking block opens and, before the stop, `ReplayContent` with the message's content blocks in order when it holds a thinking block, and sends an assistant message that carries that content back verbatim. SDKs are core dependencies but imported lazily inside the adapters (importing Haute never triggers provider-side behaviour; a broken install surfaces as a readiness reason); each adapter normalises its SDK's stream into the internal `ProviderEvent`s (see Control flow § Provider adapters for the exact call and event mappings) and maps SDK failures to `AssistantProviderError`. |
| `src/haute/assistant/_loop.py` | Provider-neutral agent loop as an async generator of typed stream events: builds the session-stable system prompt, assembles prompt/history/turn-context/tool inputs (the history is the store's compacted provider history of earlier turns; the context message is the route's rendered turn context and is never stored), forwards text deltas and a content-free `thinking` status, carries each round's provider replay content into the next round's request only, invokes the injected tool executor, feeds structured results into later provider rounds, shields only an in-flight transactional apply from cancellation, enforces tool/time limits, terminates when the dry-run budget is spent or a failed dry-run makes no progress, continues the turn after a saving apply with the turn context update the route's refresher renders placed after that round's results (never stored), records every saved change id on the outcome, streams a build-plan-updated event after each tool call that changed the session's build plan and stores the plan with the turn when the turn changed it, sends one end-of-turn reminder when the model stops with a validated plan unapplied or a failed dry-run uncorrected and completes the turn `incomplete` on a second such stop, commits turn history, and closes every provider stream. A tool call runs beside the turn (`_RunningTool`), so each progress title it reports streams as a tool-progress event while it runs. It writes each activity row's plain-words summary (see **Tool summaries**). It does not implement graph edits itself. |
| `src/haute/routes/assistant.py` | The FastAPI router: `GET /api/assistant/status`, `GET /api/assistant/sessions` (the saved conversations bound to the requested `source_file`, for the panel's chat list), `POST /api/assistant/session`, `POST /api/assistant/message` (an SSE `StreamingResponse` wrapping `_loop`'s generator), `POST /api/assistant/changes/undo` (Undo of one change card, see Control flow). The session and undo responses carry the session's build plan, and each turn's executor and turn context are built with it. Every one of the last four carries the canvas document's `source_file`, resolved by one route helper (`contained_path` inside the project root, then membership of `discover_pipelines()`, then the POSIX project-relative spelling the editor document uses); there is no default-pipeline guess. Route-level exception translation follows the product conventions (typed `HauteError`s surfaced, everything else sanitized). Swept by the existing `tests/test_routes_hygiene.py` contracts like every `routes/` module. |
| `src/haute/_column_summary.py` | Shared with [explore-eda](../explore-eda/low-level.md): the Polars dtype facts every column-summarising surface needs — `is_unhashable_dtype` for the columns that cannot be counted, the reserved count-field alias `CATEGORICAL_COUNT_FIELD`, and `json_safe_scalar`. It imports only Polars and the stdlib-only JSON-safe encoder, so the assistant reaches it without importing the routes layer. |
| `src/haute/schemas.py` | Cross-component dependency owned by [server-api](../server-api/low-level.md); the assistant slice of the server-api-owned shared HTTP/SSE contracts: status, session request/response and transcript entries (including the `outcome` entry), message request (with its optional closed `context`: `selected_node_ids`, unique, at most 20, and an optional `preview_error_node_id`), usage, the turn outcome `AssistantTurnOutcome` (a kind of applied, answered, needs_input, blocked, committed_unverified or incomplete, a non-empty detail exactly for the last four, and the required `changes`, the ids of the changes the turn saved in order: non-empty for applied, empty for answered, either for the rest), the build plan `AssistantBuildPlan` (see Key types) that the session and undo responses carry under the required, nullable `build_plan`, and the text-delta, thinking (no fields beyond its type), tool-started, tool-progress (`AssistantToolProgressEvent`: the running call's id and its new title), tool-finished, change-applied (its record's data check, an `AssistantChangeDataCheck` of `AssistantDataFinding`s, or null), build-plan-updated (the whole plan), completed (usage and required outcome), failed, and cancelled event union mirrored by `frontend/src/api/assistant.ts`. |
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
Retention constants in `_session.py`, not env knobs: the provider request carries the
newest earlier turns' **records** fitting `PROVIDER_HISTORY_CHARACTERS` (24,000
characters; whole records only, see Edge cases, **Compacted provider history**) and the
current turn whole; stored history is capped at 200
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
  at import. The Polars `usage` ends with `_catalog.INPUT_NAMING_RULE`, the
  input-naming rule of `haute._graph_utils.executable_input_name` in words (an
  input is named after its incoming edge: the upstream node's name, except that
  an edge from a Quote Input frame, which `add_edge`'s `source_handle` selects,
  is named by that frame, and an edge from a submodel output by its port); the
  system prompt, the authoring guide's "Names and wiring" and the `input names`
  field of the Polars, Quote Response and Source Switch cards state it verbatim,
  held so by test, and no card says an input is "the upstream node's name"
  alone. `step_authoring` is `null` for a type outside
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
  result to `df`; the source step binds `df`), not the code-mode one. The Explore
  descriptor's say an analyst's pivot table is a `pivots` entry on the node, never a
  step, which the card's `steps` field and the guide's step section repeat beside the
  `pivot` step kind.
- **`OperationCapabilityDescriptor`**: a closed, versioned operation
  declaration. `_tools.TOOL_DEFINITIONS` is projected from these descriptors,
  so a provider-visible tool cannot exist without risk, egress, retry,
  concurrency, timeout, payload, context-budget, stable-error and recovery
  metadata. Its output schema requires attribution plus exactly one non-empty
  success or error result variant; an empty object is never a valid declared
  operation result. The `dry_run_graph_edits.ops` primitive branches are generated from
  `_wire_ops`' canonical Pydantic operation models, and its `recipe` branches from the
  recipe descriptors. The projection inlines local
  definitions and retains closed fields, requiredness, discriminator constants,
  descriptions, nullability, and the canonical `NodeType` enum; `_catalog` does
  not hand-copy the primitive operation vocabulary.
- **`PreparedGraphEdit` / `VerifiedPlan`** (`_ops.py` / `_application.py`): the
  prepared value contains one parsed, normalized, graph-applied edit plus its
  resolved postconditions, semantic diff, and affected capabilities. The verified
  value adds save validation, schema evidence, warnings, tier, and the one sealed
  `GraphEditPlan`. `build_verified_plan` is the sole application-service path from
  snapshot plus raw operations to that pair and is reused for dry-run and apply
  replay.
- **Manifest identity/cache**: `capability_manifest()` refreshes installed
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
  are emitted in stream order — `TurnStop(reason: "end" | "tool_use", usage)`,
  `ThinkingStarted()` — the model opened a thinking block; it carries no content — and
  `ReplayContent(blocks)` — the round's assistant content as this provider must receive it
  back within the turn, at most once per round and before its `TurnStop`. Only the
  Anthropic adapter emits the last two. Adapters translate SDK streams into exactly these;
  the loop never sees SDK types, and treats `ReplayContent` blocks as opaque JSON.
- **`SourceEvidenceLedger`** (`_ops.py`): one session's evidence ledger, the
  `ProjectSourceEvidence` its tool results returned keyed by kind and project-relative
  path, each marked as observed in the current turn or carried from an earlier one.
  `begin_turn()` marks every entry carried; `observe(key, evidence)` adds or replaces an
  entry as current; `drop_vanished_schemas()` is the `find_data` rule;
  `release_stale_carried(project_root)` drops each carried entry that
  `_evidence_manifest_entry` reports missing or changed and returns the released keys;
  `sources()` lists the entries in key order for a dry-run's snapshot. It lives on the
  `AssistantSession` and is never serialized.
- **`TurnRecord`** (`_render.py`): an earlier turn as the provider sees it after
  compaction: the analyst's `request`, the turn's final assistant text (`reply`, empty when
  its last assistant message carried tool calls or no text), its stored `outcome` (none
  for a failed or cancelled turn), the change records its applies saved (`changes`, in
  order, from its tool messages' `change` records, so a persisted turn yields the same
  record), the build plan as the turn left it (`build_plan`, none when the turn did not
  change the plan) and the ids of the changes the analyst undid after it (`undone`).
- **`AssistantBuildPlan`** (`schemas.py`): `items`, one to twelve
  `AssistantBuildPlanItem`s in order, ids unique: `id` (lower-case letters, digits and
  underscores, starting with a letter, at most 32 characters), `title` (non-empty; the
  tool schema bounds what the model writes at 80 characters, the model does not, because a
  persisted title is redacted like assistant text, which can lengthen it), `complete`, and
  `changes`, the `AssistantBuildPlanChange`s recorded against
  it in the order they were saved: `id`, the change card's id, and `undone`, whether the
  analyst undid it. All three models are closed and frozen, and the item validator refuses
  a complete item without a change that is not undone, so no transition can produce one.
- **`BuildPlan`** (`_build_plan.py`): the session's mutable holder of its current
  `AssistantBuildPlan` (`current`, none before the model sets one), live on
  `AssistantSession.build_plan` like the evidence ledger but persisted. Every change
  replaces `current` with a new snapshot, and an operation that changes nothing keeps the
  same object.
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
    no Python class docstring reaches the provider-visible schema. `name` carries one
    too: when the analyst names the node ("add X", "as X", "called X", "named X"), that
    exact name, with no suffix such as `_node` or `_input`.
    An `outputMapping` row's `source_port` that starts with `$` names a batch ref, as a
    node id may: it resolves to the input name the referenced node's edge gives (its
    sanitised label), on `add_node` and `update_node` alike, and an undeclared ref, or one
    whose node an earlier operation deleted, is the unknown-reference error; this is how the `response_output` recipe after a node the
    plan adds reads it.
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
  - `edit_steps {node, edits}` — changes one node's `steps` list step by step, so an edit
    never resends steps it does not change (a free-code step whose code the policy masks
    included). `edits` is a non-empty ordered list, each entry one of
    `{insert_after: <step id> | null, step}` (`null` inserts at the start),
    `{replace: <step id>, step}` and `{remove: <step id>}`, applied in order to the list
    as the earlier edits left it. A `step` without an `id` gets one: a replacement keeps
    the id it replaces, an insertion gets `<kind>_<n>` with the smallest `n` from 1 that
    no step of the node holds at that point, so ids are deterministic and unique within
    the node. There is no partial edit: a replacement is the whole step. An edit naming an
    id the list does not hold, or a step whose explicit id another step holds, is an op
    error whose `where.step` names that id. The operation needs a node holding a `steps`
    list on a stepped surface: a code-mode node is refused pointing at `update_node
    {code}`, and a stepped-type node with neither steps nor code is refused pointing at
    `update_node {steps}` with the surface's free-code form. An instance (`instanceOf`
    set) runs its original's configuration, so it is refused pointing at the original
    node, the one to edit.
  - All three node operations follow the stepped-node write contract in Edge cases: a write
    that would change how a stepped-type node is authored is refused, and a write that
    does not land in the materialised config fails the plan with `op_not_applied`.
  - `rename_node {node, new_name}` (sets both id and persisted label to the
    canonical sanitised function name, rewrites edge endpoints and the consumers'
    structured references to the old input name, and refuses a rename that a
    consumer's code would not follow, see Edge cases) ·
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
  deduplicated and sorted by node id. Then come `input_schema_declared` records
  (`node`, `table`, `tier: "declared"`, `column_count`), one per structured API Input
  table in a resolved terminal's lineage whose schema came from the IO layer's
  declared schema tier, deduplicated and sorted by node id and table. `table` is the
  table's label, the port name the graph's edges already carry as their
  `source_handle`; `column_count` counts the table's declared selected columns,
  whatever a consumer demands. Neither record carries a path or column name, and the
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
  their node and key (`<node>:<key>`); an `edit_steps` records one change per step id
  it inserts, replaces or removes (`<node>:steps[<id>]`) and counts its node as updated.
  The stored normalized
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
  deterministic hash after complete revalidation. An identical fresh dry-run replaces
  an applied record the same way: the hash covers the base revision, so it recurs only
  when an undo has restored that revision, and the record of the earlier apply must not
  outlive the revision it applied to. `begin_apply` on an applied record, with no fresh
  dry-run between, still fails `plan_already_applied`. Each record also holds the plan's
  `PlanReceipt` (summary and assumptions), outside the hashed authority: `put` requires
  one, an identical dry-run of a still-valid validated plan replaces it, and
  `receipt(plan_hash)` returns it to the apply that builds the change record.
- **`AssistantChangeRecord`** (`schemas.py`): `id` (the hash of the plan the change
  saved, which `change_record` takes from the apply), `summary`, `assumptions`, `changes` (an
  `AssistantGraphChanges`: `nodes`, `edges_added`, `edges_removed`, `preamble_changed`,
  `truncated`), `warnings`, `git_sha`, `parent_sha`, `revision` (the editor document
  revision the save returned, `SavePipelineResponse.source_revision`, which an undo
  compares and passes as its save's base revision). A node chip is an
  `AssistantChangeNode`: `id`, `type` (palette display name), `change`, `renamed_from`,
  `fields`, `steps` (step kinds in order, `null` for a node without a step list) and
  `steps_changed`; an edge is `{source, target}`. All models are closed (`extra="forbid"`).
  A field is a top-level config key in lower-case words (camel case and underscores
  split); `<node>:steps[<id>]` identities, from `edit_steps` and from renames rewriting a
  step's input, count toward `steps_changed` instead, and an `update_node` that writes
  `steps` adds the field `steps`. The record is value-free by construction: it is built
  only from node ids, node types, config keys, step kinds and edge endpoints, never from
  a config value, step code or an intent comment.
- **`PipelineApplicationService`**: the only stateful assistant mutation
  service. `inspect`, `dry_run`, `apply`, and `verify` return closed
  Pydantic models or stable `AssistantOperationError` codes.
- **`AssistantSession`** (`_session.py`): `id` (uuid4 hex), `source_file`, `history` — a list
  of **turn records**, each grouping one user message with every assistant message, tool
  call, and tool result it produced (the atomic unit all pruning operates on), its outcome, and
  `undone`, the records of the changes the analyst undid after it, kept whole so they outlive
  the pruning of the turn that saved them (required when persisted, with each record's
  summary and assumptions redacted like a stored change record), and `build_plan`, the
  build plan as the turn left it when the turn changed it — one
  `asyncio.Lock` (the one-turn-at-a-time guard), the session's `BuildPlan`
  (`build_plan`), `created_at`/`last_used`.
- **SSE wire events** (`schemas.py`): the `AssistantStreamEvent` union listed in the module
  map — field-for-field the contract documented in
  [frontend-assistant-ui](../frontend-assistant-ui/low-level.md) Key types.

## Control flow

**Reference reads**: the prompt carries `compact_manifest(capability_manifest())`:
identity, dynamic installed capabilities, and stable node, operation and recipe indexes.
`read_reference(ids)` accepts one to twelve unique ids from four namespaces: `guide`,
`node:<NodeType value>`, `recipe:<recipe id>` and `example:<teaching example name>`
(an example and a recipe can share a name, so ids are always namespaced). It validates
the complete batch before reading anything, so an unknown id is one stable
`unknown_reference` result naming that id with up to three `did_you_mean` close valid
ids (an unprefixed node type such as `banding` therefore suggests `node:banding`), and a
duplicate or malformed batch is `invalid_request` from the closed input schema. Results
are `{"count", "references": [{"id", "content"}]}` in request order: a node's `content`
is its complete descriptor with its card, a recipe's its descriptor with the closed
argument schema, an example's the `load_example` view, and the guide's the attributable
guide with `step_grammar`. Every item is materialised into ordinary JSON containers. The
manifest is the only node catalogue: there is no separate node-type list. A removed
tool, which a resumed session's history can still name, is refused with `tool_removed`
and a message naming its replacement: `list_node_types`, `get_capability_manifest`,
`get_capability_descriptors`, `get_example` and `get_authoring_guide` (`read_reference`),
`get_node_schema`, `get_node_config` and `get_column_profiles` (`inspect_node` with the
matching part), `list_datasets` and `get_dataset_schema` (`find_data`), and
`plan_recipe` and `dry_run_recipe_plan` (a `recipe` operation in `dry_run_graph_edits`).

**Recipe operations**: the `dry_run_graph_edits` operation union holds, beside the
primitive branches projected from `_wire_ops`, one closed branch per installed recipe,
built by `_catalog` from the recipe descriptors: `op` const `recipe`, `recipe` const
the recipe id, `arguments` that recipe's closed argument schema, and an optional `ref`
with exactly `AddNodeOp`'s `ref` schema. Canonical validation selects the branch by `op`
and then by `recipe`, so an argument error names its path, such as
`dry_run_graph_edits.ops[0].arguments.rules[1].value`. Each branch's `arguments`
description names the recipe and its argument fields, required first, with the keys of
any object items, because the compatible projection reduces the differing `arguments`
schemas to one object with their descriptions joined; the full schema is the
`recipe:<id>` reference. Every request receives the same operation union; nothing in the
request's wording changes tool availability or schema shape.

`_tools.dry_run_graph_edits` passes the batch to `_recipes.expand_recipe_operations`
before the application service sees it. Each `recipe` operation at index `i` expands in
place, in batch order, through the recipe's deterministic planner, with the node it
creates declaring the operation's `ref` (or `recipe_<i>` without one) and a response
output it adds declaring that ref plus `_output`. The recipe adds no postconditions of
its own: its nodes and edges are covered by the plan's postconditions like any other
operation's (the automatic structural summary unless the model declares its own). The
result (`ExpandedBatch`) carries the expanded primitive operations, the `positions` list
holding for every expanded operation the index of the operation it came from, and the
recipe id of each `recipe` operation's index. A planner failure (`RecipeError`) becomes a
structured error with the recipe failure's code, `where` `{"op_index": i, "recipe": <id>}`
plus `field` `arguments.<argument>` when the failure names one, and a `fix` naming the
correction and the `recipe:<id>` reference. `PipelineApplicationService.dry_run` takes
`positions` through `build_verified_plan` and `prepare_graph_edit` to `parse_ops` and the
batch application, so every `where.op_index`, and every operation index a message or
`fix` names, is the index in the batch the model sent; the tool then adds `recipe` to
`where` when that operation is a recipe. The loop's progress rule and `_attempted`
therefore read the operation the model wrote. Apply replays the stored primitive plan,
whose indices are its own. Expansion reads no project state and never writes.

Argument descriptions distinguish a requested graph-node name
from its output-column name. Transform, join, and rating recipes also accept optional non-empty
`output_name` and `output_columns` fields, which must be present together. The latter is a
non-empty unique array of simple JSON-field column names. When present, the deterministic
planner adds one response `output` node, a canonical JSON `outputMapping` for exactly those
columns, and an edge from the recipe node in the same expansion. The standalone
`response_output` recipe requires `source`, `output_name`, and `output_columns` and
creates the same canonical mapping directly after the saved source. A bare output name or
column list is a material ambiguity and fails recipe planning. A categorical-banding rule
contains exactly a non-empty string `value` and non-empty `assignment`. Execution
(`src/haute/_rating.py::_apply_banding`) casts the banded column to text and matches each row's
text exactly, so the rule's `value` description states that text form: booleans are
`"true"`/`"false"` and integers are their digits. Through the tool, the closed input
schema refuses a boolean or numeric `value` as `invalid_request` with reason `wrong_type`
at `dry_run_graph_edits.ops[N].arguments.rules[M].value`, and the message repeats that
description. A direct planner call refuses it with `recipe_argument_invalid` naming the
argument and the text form it must take (`"true"` or `"false"` for a boolean, the digits
for an integer). Planning
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
to canonical dynamic-key rating tables and camel-case combined outputs and runs the
canonical rating validators. No code reads the request's words to suggest, populate,
rewrite, or reject a recipe or primitive plan. The source-bound executor API has no
natural-language request parameter, so this separation is structural rather than a
convention inside its dispatcher.

Material choices a recipe or node needs, such as rating factor values and the value for a
missing factor, are node-card guidance: the `ratingStep` card's field meanings say they
come from the analyst, are never invented, and are asked for with `NEEDS_INPUT:` unless
the analyst delegated them. If the provider submits a complete structured call, that call
is judged only by the canonical recipe/operation schema and graph validators. The lexical-only error codes
`recipe_route_required`, `recipe_route_mismatch`, `recipe_name_mismatch`, and
`material_input_required` are not part of the operation descriptors.

**Node inspection**: `inspect_node(node, parts?, input?, column?)` answers the parts it
names, in the order `schema`, `config`, `profile`, `data`, with `["schema"]` when `parts`
is omitted (`parts` holds one to four unique part names). Each part has its own egress
requirement, checked before that part reads anything: `schema` needs `max_sensitivity`
`internal` or `restricted`, `config` needs `restricted`, `profile` needs a non-public
ceiling and `allow_row_samples`, and `data` needs a non-public ceiling and
`allow_aggregate_statistics` (`EgressPolicy.permits_data_checks`). A denied part is
listed under `withheld` as `{"part", "required_policy"}`, where `required_policy` is the
`[assistant.egress]` setting it needs (`max_sensitivity = "internal"`,
`max_sensitivity = "restricted"`, `allow_row_samples = true` or
`allow_aggregate_statistics = true`); the permitted parts answer under their own keys beside
`node`, `withheld` and `project_revision`. When every requested part is denied the call is
`egress_policy_denied` with that `withheld` list and, when the policy permits any
part, `available`, those parts, whose message sentences say what each answers (the
schema part lists each output column with its dtype, a struct dtype naming its fields,
and each input's columns); an unreadable policy is
`egress_policy_unavailable`. `input` names the frame the profile part reads and is
`invalid_request` without the `profile` part (`input_without_profile`); `column` names
the column the data part follows and is `invalid_request` without the `data` part
(`column_without_data`). Each permitted part answers or fails on its own. A part that
fails (an unknown or submodel-internal node, a submodel occurrence for any part but
`schema`, an unresolvable schema, an unavailable profile, a saved graph the data part
cannot parse) is reported under `part_errors`, an object keyed by the part, in part
order, whose value is the error envelope the call would have returned for that part
alone (its `code`, `message`, located fields such as `step`, `line` or `inputs`, and
`retryable` by the call-level rule), while the other parts answer beside it; the live
case was a schema part failing on a broken free-code step, which hid the data part that
reports that failure. Only when no permitted part answers is the call an error: the first
failing part's error, with `part` naming it. A data check that does not run is the data
part's answer, never a failure. The call takes the save lock
itself while its parts read the saved graph, and its parts must report one project
revision; the data part parses the saved graph and reads its revision under the lock
and runs its check after releasing it (**A saved node's check** under
[Data checks](#data-checks)).

**Profile part**: `column_profiles(node, input?)` is the only part that returns
values from project data, and it never returns a row. With `input` omitted it profiles the node's
own output; with `input` set it profiles that named input, resolved through the same
code-visible input names the schema part reports. It prepares frames through the ordinary lazy path — **not**
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
truncated to `_MAX_PROFILE_VALUE_CHARS`. The operation descriptor names each part's
egress class, the profile's being its own value, `restricted-value-profile`, so a policy
review can see the one value-returning capability plainly; the data part's is
`internal-aggregate-statistics`.

The operation runs where the editor's previews run. The server checks the egress gate,
parses the saved graph, validates the target and input name, and admits one
`PREVIEW_EAGER` execution context. Frame preparation and the bounded collection then run
in the interactive preview worker through
`src/haute/_interactive_workers.py::run_in_interactive_worker`, under the isolated budget
derived from that admission and the worker's native memory cap, bounded by the pipeline
settings' pipeline time limit. Under that cap a join or group-by whose materialisation cannot
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
   uses) runs inside `_input_providers.recording_schema_tiers()`, so a local file
   Data Input with no snapshot resolves at the inferred schema tier and a structured
   API Input table with no snapshot at the declared schema tier, and each is recorded;
   an input the inferred tier cannot scan fails the dry-run as `schema_unresolvable`
   whose message carries the IO layer's `input_snapshot_missing:` reason and the remedy
   "Preview this input first, which builds its snapshot." An API Input contract that
   fails `validate_v2_schema` (a column without a declared type) fails it as
   `schema_unresolvable` carrying the validator's `ApiInputSchemaError`. The dry-run
   writes no snapshot, and post-save verification re-resolves the same inputs the same
   way, so the inferred and declared records compare equal. It then
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
   a spurious `invalid_plan`. `inspect_node` on the named node reports the actual
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
   carries the identical list. A plan holds at most `MAX_PLAN_OPERATIONS` (100)
   operations. Each adds or updates at most one node, except `rename_node`, which also
   writes every code-carrying consumer whose structured references it rewrites and so
   adds one `node_config` per such consumer; the number of `node_config` postconditions
   is therefore not bounded by the operation count. A caller declares at most
   `MAX_DECLARED_POSTCONDITIONS` (100), enforced by `dry_run`; and the sealed list (the
   declared or automatic list plus every `node_config`), which apply replays through
   `build_verified_plan`, is checked in `prepare_graph_edit` against
   `MAX_SEALED_POSTCONDITIONS` (`MAX_DECLARED_POSTCONDITIONS + MAX_PLAN_OPERATIONS`,
   200). A list over its cap fails the plan loudly as `invalid_plan` before anything is
   saved; no check is dropped, so a plan whose renames rewrite too many consumers is
   refused rather than sealed with a truncated list. The automatic structural postconditions (one
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
entries, and `tool` entries carrying the tool name, its title, the finished summary the
live stream's `_result_summary` computes, and the error flag) so the panel rehydrates the
conversation. A resumed tool entry's title and summary are computed from the persisted
result, whose arguments are redacted (see **Tool titles** and **Tool summaries**). A
result persisted to disk keeps only evidence scalars, the change record and the error code,
so a session revived from disk shows a dry-run as "Plan is valid", an apply as "Saved:
<change summary>", a read with no summary and an error as `tool error`. A successful
apply's persisted `change` record additionally yields one `change` entry carrying that
record immediately after the apply's tool entry, matching the live `change_applied`
event without duplicating provider history. A turn stored with an outcome ends with one `outcome` entry carrying that
`AssistantTurnOutcome`, the same value the live `completed` event carried; a failed or
cancelled turn has none. Stored assistant text is returned as stored, so the panel
derives the same display from a resumed turn as from the live one. Any other
case — no `session_id`, unknown/pruned/corrupt, or a different pipeline — creates and
returns a fresh session with empty `history`; resume is an offer, never an error.
Each change a turn records as undone (`AssistantTurn.undone`) yields one `undo` entry,
carrying that change record, after that turn's outcome entry. The response's required
`build_plan` is the session's current build plan, `null` for a fresh session or one
whose model never set a plan.

**Undo** (`POST /api/assistant/changes/undo` with `{session_id, change_id, source_file}`;
any other field is refused with 422): resolve the source file, then reserve the session
the way a message does (unknown → 404, a running turn → 409) and release it in `finally`;
a source file other than the session's binding is a 409 naming the chat's pipeline. The
change is the latest stored `change` record with that id in the session's history (404
when none). `PipelineApplicationService.undo` then refuses a record with no
`parent_sha` (`undo_unavailable`, 409: the change was not saved to Git) and, under
`save_lock`, a current document revision other than the record's `revision`
(`undo_superseded`, 409: a later save exists, so only the Git panel can go back further).
It reads the parent graph with `commit_pipeline_graph(parent_sha, source_file)`, saves it
with `save_graph_transactionally` (base revision the record's `revision`, commit message
`Undo: ` and the headline), and publishes the document update with the undone change as
its origin. A historical read failure or a stale save precondition is a 409 too. No
mutation-readiness check runs: an undo is the analyst's own save, like a canvas save.
Finally `SessionStore.record_undo` adds the change record to the latest turn's `undone`,
applies `BuildPlan.undo(change.id)` to the session's build plan and persists the session;
the response is `{change_id, git_sha, build_plan}`: the commit the undo save made and the
session's build plan after the undo (`null` without one). The next turn's context lists every change the latest stored turn records as
undone, with its summary (see `render_turn_context`), so the model learns of it once, on
the turn that follows.

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
   type naming its id, palette name (followed by `read-only to you` for a type whose node
   card is not authorable: the submodel and its port) and one-line purpose, operation ids,
   and recipe ids with their canonical summaries and a sentence that number and date ranges
   have no recipe and are banded with breakpoints + an example-ID-only index + the source
   file. It holds
   no pipeline name, node summary, egress policy or request-dependent text. The request
   then carries the compacted history of the earlier turns (`SessionStore.provider_history`,
   see Edge cases), the new user message, a turn context message (neutral
   role `context`) and the `_tools` JSON schemas. The route builds the turn context under
   the save lock with `_tools.build_turn_context(source_file, egress, selected_node_ids,
   preview_error_node_id)` and `_render.render_turn_context`; the self-test harness builds it
   the same way. It holds:
   - the pipeline name and the base revision (`build_project_snapshot(...).revision`);
   - the effective egress policy in words, taken from the resolved configuration's
     `egress` (provider trust, highest sensitivity sent, and whether project knowledge,
     executable source and column value profiles are permitted, and, when row samples are
     not permitted, that execution errors are reported without their text; below
     `restricted`, that saved node configuration is withheld for every node, so factors,
     tables, mappings, scenario maps and code are not visible, and that `update_node`
     replaces a key's whole value, so a list or map not read is never rewritten and the
     model asks instead; the note that the config part redacts node code appears only
     when the config part is readable; and one line on aggregate data statistics,
     `permitted (value-free counts and shares, never row values)` under
     `allow_aggregate_statistics` and otherwise `not permitted; no data check runs, so a
     dry-run proves schemas, never that the data came out right`), followed by
     the column-value rule it implies: with `allow_row_samples`, call
     `inspect_node` with the `profile` part before comparing a column to a literal and answer
     `NEEDS_INPUT:` when its values are withheld; without it, ask which values to match,
     beginning `NEEDS_INPUT:`. The always-on rules defer to that section for project
     material, so they never deny access the policy grants;
   - the selected node ids, in request order;
   - the graph brief: per top-level node its id, palette name (from the capability
     manifest), label (whitespace collapsed, at most 80 characters, JSON-quoted), authoring
     state on a stepped surface (`incomplete` when its resolution raises the incomplete
     transform or incomplete steps placeholder or its config is incomplete, else
     `stepped` when `is_stepped_config`, else `code`) and the node's authoring facts
     rendered under the turn's policy as described for `get_pipeline` under Edge cases
     (one line per step, the step an incomplete list fails at, a discarded-steps
     marker), each input as its code-visible name, source node and column names, and
     its output columns (per port for a multi-frame node, each port line naming the
     `add_edge` `source_handle` that selects it and the input name
     `executable_input_name` derives for an edge from it: a Quote Input frame is
     selected and read by its label, a submodel output port `<port>` is selected by
     `out__<port>` and read as `<port>`), and for a Source Switch the
     sorted names of the scenarios its `input_scenario_map` routes (scenario names are
     pipeline metadata; which input each routes is configuration and is not listed).
     Schemas resolve schema-only in
     one `execute_lazy_graph` call preserving every node; when that call raises, each node
     resolves on its own and an input's source resolves separately, so one broken node
     marks only itself `unresolved` (never with its error text). A node counts as
     resolved only once its output's schema has been read, because a lazy frame raises a
     column its plan reads but its input lacks only then. A submodel node has no
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
   - when the session's build plan has an open item, a `### Build plan` section before the
     pipeline section (so it is present under every policy, the plan being the model's own
     words): a sentence saying it is the plan the model set and that the open items remain,
     then one line per item in order with its id, its title as one bounded JSON-quoted
     line, `complete` or `open`, and how many saved changes are recorded against it, with
     the undone ones counted apart. A finished plan, every item complete, is not listed.
   The block holds nothing derived from the analyst's words: no recipe suggestion and no
   clarification hint.
   Under `max_sensitivity = "public"` the block holds only the policy and says the graph
   is withheld; the pipeline is not read, so the ids are not checked. Under any other
   policy a selected id that is not a top-level node of the saved pipeline, or a
   preview-error id that is not a top-level executable node, raises `TurnContextError`
   and the route answers 409 before the stream opens, releasing the reservation. The loop places the context message after the user message
   in every provider round of the turn and never appends it to the stored turn. The
   Anthropic adapter sends it as a mid-conversation `system` message for the models that
   accept one (`MID_CONVERSATION_SYSTEM_MODELS`) and otherwise, like the OpenAI and
   Databricks adapters, prepends it to the preceding user message's text followed by an
   `## Analyst message` heading.
   After a round in which `apply_graph_plan` saved, the loop awaits the route's context
   refresher with that round's change records and places the rendered **turn context
   update** after the round's tool results in every later provider round, never storing
   it. The refresher (`_tools.context_update`) takes the save lock, gathers the facts on
   a thread with `_tools.build_context_update(source_file, egress, changes)` and renders
   them with `_render.render_context_update`; the self-test harness passes the same
   refresher. The facts are the new base revision; the brief entries, in graph order and
   from the same per-revision cache as the turn context, of every node the records name
   (a chip's node, a renamed node's new id, either endpoint of an added or removed edge)
   that the saved graph's top level still has; the named ids it no longer has, listed as
   removed; and whether any record's changes were truncated, which adds a pointer to
   `get_pipeline`. The brief stops at the turn context's character bound. Under
   `max_sensitivity = "public"` the update holds only the policy sentence saying the graph
   and its revision are withheld. Each adapter places an update that follows tool results
   as the same `system` message for the mid-conversation models; otherwise the Anthropic
   adapter appends it as a text block to the user message holding the round's
   `tool_result` blocks, and the OpenAI and Databricks adapters send it as a user message
   after the round's tool messages. A context message that follows neither a user text
   message nor tool results is refused. The authoring
   guide and full exemplar bodies are prompt-excluded: the model pulls
   them through `read_reference` (`guide`, `example:<name>`) only when relevant, and the
   node cards travel in the node descriptors: the mutation paragraph tells the model
   to read each descriptor's card with its ports, wiring rules, schema, enums and
   anti-patterns, and to write each config in the shape of the card's configurations.
   The `guide` reference also carries `step_grammar`, derived from
   `haute._polars_steps`: every step kind with its required and optional fields
   (`step_fields`) and the closed vocabularies structured steps use (operators,
   aggregations, join kinds, cast dtypes, fill strategies, functions, literal
   types), so a model editing structured steps reads them on demand; nested
   expression shapes are not enumerated there. The mutation paragraph states one
   authoring rule for new Polars logic, derived from `STEPPED_NODE_TYPES` and the
   palette names: steps with a free-code card, the Polars (Transform) form
   `[source, free_code]` and the `[free_code]` form for every other stepped
   surface, each spelled as `new_logic_steps` renders it with the example code
   and `<edge name>` standing for the name of the input that becomes `df`,
   followed by `INPUT_NAMING_RULE`;
   the code transforms `df` and assigns the result to `df`, reads other inputs by
   edge name only on the surfaces whose steps see their edges (Polars and Load
   File, where the loaded object is `obj`), and starts with a one-line `# intent`
   comment; a hook needing no post-processing keeps `steps: []`; existing
   structured steps keep their ids and order, a code-mode node keeps its `code`,
   and the assistant never switches a node between steps and code. The
   permanent installed-I/O summary includes only group identity, input/output
   availability, cache modes, and format names; field schemas stay out of the prompt
   and remain available through `read_reference`. This keeps the routing facts useful
   without paying for a redundant descriptor copy or burying the mutation protocol.
   The prompt treats explicit authoring verbs such as build, add, change, update,
   connect, remove, delete, and make as mutation intent rather than an invitation to
   inspect and stop. When an installed deterministic recipe matches the requested
   operation, the model writes it as a `recipe` operation in its `dry_run_graph_edits`
   batch, beside any primitive operations the request also needs, and reads
   `recipe:<id>` when it needs the argument schema; joining a file onto a flow is an
   `edgeJoin` node or the `reference_join` recipe, never Polars code. When the analyst
   delegates a choice ("pick any", "you choose"), the prompt tells the model to make a
   reasonable choice, state it, and proceed, and to ask only for choices that change the
   result materially and that the analyst has not delegated. Its outcome rules: ask on a
   line starting `NEEDS_INPUT:`, report a blocker on a line starting `BLOCKED:`, a message
   that asks or blocks applies nothing, a request saved in part with another part that
   cannot be done (a run, a file write, a deployment, training) names that part on a
   `BLOCKED:` line, and a value, name or threshold the request states is never put back to
   the analyst for confirmation. Its build-plan rule: for a request with several stages,
   set the stages with `update_build_plan` first, pass each stage's item id as `item` to
   its `apply_graph_plan`, and mark an item `complete` only once its whole stage is saved;
   a request of one change needs no plan, and the turn context lists the open items to
   continue. When it explains that it ignored instructions found in
   project or pasted text, it does not repeat their instructions or tokens. Every request receives the
   same complete mutation tool schemas. A failed dry run may be corrected while
   the corrections make progress. The loop keeps **one bounded budget of four failed
   calls** across the turn's dry-runs and both failure classes, a domain rejection (the
   plan was built and judged) and a closed-input-schema rejection
   (`invalid_request`, which never reached planning). The
   budget replaces two independent budgets of two attempts each, which existed because
   a single plan retry was spent by a spelling error before the plan had ever been
   judged; with four attempts and the progress rule below that can no longer happen,
   and one counter is the simpler bound. The budget and the identities below start
   afresh at the start of the turn and after every apply that saves: a saved plan is
   progress, and a request that failed against the earlier revision may succeed against
   the new one. The loop ends the turn before the budget is
   spent when a failed dry-run makes no progress, judged on value-free identities it
   keeps for the failed dry-runs since the turn started or the latest saving apply:
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
   were applied, or, when the turn saved changes earlier, that those changes stay saved
   and no further changes were applied. The message is exactly what the tool result
   already returned to the
   model, so the outcome adds no value the model had not seen. It then emits `completed`
   with the `blocked` outcome, whose detail is that message after its `BLOCKED:` marker
   (built, not parsed, since the error message can quote the model's own text),
   and performs no further dry-run or provider round. A further dry-run call in the same
   provider round is refused without running as `dry_run_retry_limit` (not retryable) and
   does not change the recorded blocker. The system prompt states the same rule.

   What the model sees is pinned by a checked-in golden snapshot under
   `tests/assistant_eval/golden/`: the system prompt rendered for one fixed source
   file (`motor_pricing.py`), the turn context rendered from fixed data for two turns of
   that project (a three-node brief with a selection and a withheld preview error, then
   a four-node brief under a changed policy with an unfinished build plan, each with the
   fixed policy words), the turn
   context update after an apply in the second turn (a new revision, two changed nodes and
   a removed one), the compacted history of two earlier turns rendered from fixed records
   (`turn_records.md`: the omission note, then an `applied` turn with two saved changes,
   the build plan it left and a later undo, and a `needs_input` turn with no saves, each as
   its user and assistant messages), the canonical
   `TOOL_DEFINITIONS`, the tools each provider lane sends (`tools_anthropic.json` and
   `tools_openai.json` in the canonical projection with their strict read tools, and
   `tools_databricks.json` in the compatible projection, the two OpenAI-dialect files as
   Chat Completions functions), and a sha256 of each file.
   `scripts/update_assistant_prompt_golden.py` renders them; by default it fails with
   a unified diff per changed file, and `--write` is the only update path. The Haute
   version and the capability hash, which change on every release, are replaced with
   the placeholders `<haute-version>` and `<capability-hash>` so a version bump alone
   does not change the snapshot; every other line, including the installed-I/O
   summary rendered against the locked Polars, is compared verbatim. The script renders
   the system prompt once per turn and refuses to write when the two renderings differ,
   so the snapshot shows one prefix shared by both turns.
4. Stream provider events. `TextDelta` → emit `text_delta`. `ThinkingStarted` → emit
   `thinking`, a content-free status the panel shows as "Thinking…". `ReplayContent` → kept
   for the round: when the round's assistant message joins the request for a later round
   of the same turn it carries those blocks as `provider_content`, so the adapter that
   emitted them receives them back verbatim; the stored turn never holds them, so they end
   with the turn. A second `ReplayContent` in one round is a programming error that
   raises. `ToolCallRequest` → emit
   `tool_started` with the call's started title; execute, emitting `tool_progress` with
   each title the running call reports (a dry-run reports "Checking the data" as its data
   check starts); append the result to the
   pending provider messages before emitting `tool_finished` with the finished title
   (+`change_applied` carrying the result's `change` record for a successful apply, then
   +`build_plan_updated` carrying the whole plan when the call replaced the session's
   build plan snapshot, which a successful `update_build_plan` or an apply naming an
   `item` does); on
   `TurnStop("tool_use")`
   re-invoke the provider with the accumulated results. The loop tracks one **open state**,
   read only from tool results and never from the request's wording: the latest
   `dry_run_graph_edits` result decides it, a *validated plan* when
   that dry-run succeeded and a *failed dry-run* when it failed, and only a saving apply
   clears it. A dry-run refused by the spent budget never runs and does not change it.
   The loop also keeps the ids of the changes the turn saved, in order: a successful
   `apply_graph_plan` appends its change record's `id`, clears the open state and starts
   the dry-run budget afresh, and the turn continues. A successful apply is not terminal:
   later tool calls in its provider round run, the provider is invoked again after a
   `tool_use` stop with the turn context update placed after the round's results, and the
   model may dry-run and apply further plans within the tool-call and time limits. The
   loop adds no assistant text for an apply: its change card says what was saved. Every
   `completed` event's outcome carries the saved ids as `changes`. An `apply_graph_plan`
   result whose error code is `verification_failed` means the save committed but its
   post-save verification failed; its `change` record is streamed as a change-applied
   event and appended to the saved ids like a verified one; it is terminal after the
   current stream reaches its stop
   event (later tool calls in the round are ignored and the provider is not invoked
   again): the loop records the deterministic
   assistant text `Graph changes were saved, but post-save verification failed.` and emits
   `completed` with the `committed_unverified` outcome, whose detail is the tool row's
   summary of that error. This check precedes the dry-run budget check, so the budget's
   blocker can never follow a committed save in the same round. Otherwise a
   `TurnStop("end")` whose final-round text carries `NEEDS_INPUT:` or `BLOCKED:`
   completes with the `needs_input` or `blocked` outcome, whatever the open state
   (`_prefixed_outcome`). A marker counts anywhere in a line: the uppercase word with its
   colon, case-sensitive, not preceded by a letter, digit or underscore, bare or wrapped in
   backticks or markdown emphasis around the marker or its colon (`**NEEDS_INPUT:**`,
   `**NEEDS_INPUT**:`, `_BLOCKED:_`, `` `BLOCKED:` ``, `However, BLOCKED: ...`);
   `blocked:` and `NOT_BLOCKED:` are not markers. The last marker decides, and the detail
   is all the text after it and its wrapping, stripped; a last marker with no detail is no
   outcome. The dry-run budget's blocker is built as a `blocked` outcome rather than
   parsed, since its last error can quote the model's own text. The editor's transcript
   removes the marker the same way (`useAssistantStore.withoutOutcomeText`): the reply
   ends with the marker, its wrapping and the detail, and the text before the marker
   stays. An `apply_graph_plan` call whose
   own assistant message carries a marker before it is refused without running: its
   result is the loop's `apply_in_outcome_message` error (retryable, with a fix), saying
   nothing was applied because the same message asks or blocks, and it changes neither the
   saved ids nor the open state. Every adapter streams a message's text before its tool
   calls (the OpenAI-compatible adapters emit calls at the finish reason; Anthropic's
   `tool_use` blocks follow its text blocks), so the text the loop holds at the call is
   the message's text. An unqualified end (no marker, or a marker with no detail) with no
   open state completes with `applied` when the turn saved a change and `answered`
   otherwise. The first unqualified end with an open state
   appends one transcript-hidden `controller` reminder naming it: for a validated plan,
   apply it now with `apply_graph_plan` and the exact plan hash the latest successful
   dry-run returned (dry-running again first if an apply refused it), or begin with
   `NEEDS_INPUT:` or `BLOCKED:` and say why it should not be applied; for a failed
   dry-run, correct the plan as its error says and dry-run it again, or begin with
   `BLOCKED:` and state the blocker. The loop re-invokes the provider, and adapters encode
   that internal role as a user instruction. A turn sends at most one reminder: a second
   unqualified end with an open state completes with the `incomplete` outcome, whose
   detail is the controller's fixed, value-free reason (`A dry-run validated a plan that
   was never applied.` or `The last dry-run failed and no later dry-run succeeded.`), and
   the loop adds no text of its own. The outcome of every completed turn is
   stored on its history record; failed and cancelled turns store none. Every turn,
   whatever its end, stores the session's build plan as `build_plan` when the plan's
   snapshot at the end differs from the one the turn started with, and none otherwise.
   If the response closes while suspended at
   `tool_started`, the round commit filters the unmatched call; closing at either later
   event retains the already-recorded result. Thus every persisted call id has exactly one
   matching result id on every generator-close boundary.

   **Tool titles.** `_catalog.tool_title(name, arguments, result)` writes each activity
   row's title beside the operation descriptors, in plain words: a read names what it
   reads ("Reading the pipeline", "Inspecting a node", "Finding data", "Reading
   references"), `dry_run_graph_edits` reads "Checking the plan",
   `apply_graph_plan` "Applying the plan" and `update_build_plan` "Updating the
   checklist". A started row reads the arguments, so a dry-run
   counts the operations the model sent, a recipe operation counting as one ("Checking
   3 changes"). A finished row reads only the result: a
   dry-run counts the result's `operations` and an apply its `applied_operations`
   ("Applying 3 changes"), and a failed call, whose result counts nothing, keeps the plain
   title. A resumed row, whose arguments are redacted, therefore reads the same as the
   live finished row; `operations` and `applied_operations` are persisted result evidence
   for that reason. A name no tool has is titled by its name.

   **Tool summaries.** Below its title each row carries one plain-words line for the
   analyst, never JSON: `_loop._started_summary(name, arguments)` on `tool_started` and
   `_loop._result_summary(name, payload, is_error)` on `tool_finished`. A started summary
   reads only the model's own arguments, which are not yet validated, so only their string
   and list-of-string values count and anything else contributes nothing:
   `inspect_node` "<node>: <parts>" (in part order, `schema` when omitted); `find_data`
   "Schema of <path>", "Data files in <directory>" or "Project data files" (", including
   subfolders" when recursive); `read_reference` the ids; `get_project_knowledge` the query;
   `dry_run_graph_edits` the plan's `summary`; `apply_graph_plan` "For checklist item
   <item>"; `update_build_plan` "Set <n> items" and "mark <id> done". A finished summary
   reads only the result: `get_pipeline` "<n> nodes"; `inspect_node` the parts it answered
   ("Schema, config"), then its data part's check, then "<parts> failed" for its
   `part_errors`, then "<parts> withheld by policy";
   `find_data` "Schema of <path>, <n> columns", or "<n> files", "<n> folders" and "more not
   listed" when the listing was cut; `read_reference` the ids it returned;
   `get_project_knowledge` "<n> items"; `dry_run_graph_edits` "Plan is valid" (with ",
   <n> warnings" when it has any) and then its data check; `apply_graph_plan` "Saved:
   <change summary>", or "Saved for checklist item <item>: …" when the result names an
   item; `update_build_plan` "<complete> of <items> done". A data check reads "data checked:
   no findings", its findings counted by severity ("1 advisory, 2 informational findings",
   plus "and <n> more" for findings the result omits), "data not checked: <reason>" in the
   change card's words for that reason (an inspection that could check nothing in its
   lineage says so), or "data checked, result too large to show" for an omitted check. A
   summary therefore says nothing the result had not already returned to the model, and no
   measured value: only counts of items, the names and ids the result returned, and a check's
   outcome, reason and finding severities. An error row's summary is the error's message
   (`tool error` without one). Each summary is collapsed to one line and cut to 160
   characters with an ellipsis; a tool with nothing to name, a result without the fields a
   summary reads, and a name no tool has, have an empty summary, which the panel omits.
5. Tool execution: read tools run via `asyncio.to_thread`, except `inspect_node`'s
   profile part, which prepares on a thread and collects in the interactive preview worker
   (see **Profile part**), and its data part, whose check runs in that worker. Reads whose
   answer depends on the saved graph (`get_pipeline`, `find_data`, and
   `get_project_knowledge`) hold the
   process-wide `save_lock` across their worker-thread operation, so a concurrent save cannot
   interleave graph parsing with schema or value resolution; `inspect_node` takes the lock
   itself around its parts' reads, so that its data part's check, which may wait up to its
   deadline, runs after the lock is released. The schema part
   (`node_schema`) parses the
   saved pipeline, then proceeds in this order:
   1. **Validate the target id against the original hierarchical graph** — a submodel
      port node, or an id found only inside a submodel's nested graph → structured error
      naming the v1 submodel boundary (the same classification the ops engine applies to
      submodel-internal targets); an id found nowhere → unknown-node error; an original
      top-level executable node or a top-level submodel occurrence proceeds. An
      occurrence answers `ports` (each output port's columns, resolved at the inner node
      and port its definition names, `qualified_runtime_node_id`) and `inputs` (keyed by
      the input port each parent edge binds); a failure inside it names no column. Its
      config and profile parts are refused as `submodel_boundary` with a message saying
      its configuration, code and inner nodes cannot be read or edited and a fix naming
      the schema part before the `BLOCKED:` reply. Validating after flattening would be
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
      tool error, which on `inspect_node`'s schema part still carries `inputs`, each
      input resolved against its own source as on the empty-node path, and `step`, the
      failing step's id, when the failure's line falls in one (`_failed_step`) or, for a
      lazy plan failure with no line, when every other step is a `source` step (the
      taught `[source, free_code]` form); another step list is never replayed, since that
      would run authored code again. A Source Switch's schema part also lists
      `scenarios`, the sorted scenario names its map routes. An *authored-code failure* — a Polars error, any exception raised
      from node code (it carries the user-code line `_exec_user_code` records), or a
      `PreambleError` (the preamble is authored code that can read project data at
      import) — is rendered by the one execution-failure renderer in `_tools`, which the
      dry-run and apply schema-validation path and `inspect_node`'s schema and
      profile parts share: its exception type, the line of the node code or of
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
      engine path as the schema part, plus that node's own output schema on the
      column-profile path, where the schema resolved and only the collection failed; a
      profile of one named input uses that input's frame alone, so rendering its
      failure never resolves the consumer the profile did not ask for; a
      frame that does not resolve contributes nothing and is never an error of its own;
      (b) in dry-run and apply, every string in the operations the model submitted for
      this plan (apply's are the plan's `normalized_operations`: for a primitive
      operation the parsed form of what the model wrote itself, and for a `recipe`
      operation the packaged recipe's template text filled with the model's own
      arguments, so neither
      is project data), with the identifiers, keyword names and string
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
   `verification_failed`, `tool_result_too_large`, `submodel_boundary`), and true for
   every other code, each of which is a rejection a corrected call can clear. A read or
   edit of a submodel or of a node inside one (`inspect_node`, or an operation's node,
   source or target) is `submodel_boundary`, whose message says submodels and the nodes
   inside them cannot be read or edited by the assistant and whose fix says to reply
   with a `BLOCKED:` line asking the analyst to make the change in the editor (a read of
   an occurrence's config or profile part names its readable schema part first). The plan domain locates
   its failures: `OpValidationError` and `AssistantOperationError` carry `where` (any
   of `op_index`, `node`, `field`, `step`), `fix` (one concrete correction), `graph`
   (the graph the failure was judged against) and `did_you_mean` (close names the
   failure found itself, empty unless set); `OpValidationError` requires its `fix`,
   so every `invalid_ops` failure carries one. `_apply_ops_with_refs` stamps the
   failing operation's index, and records which operation last wrote each node (added,
   updated or renamed it) and, for a node only an `add_edge` or `delete_edge` touched,
   the first such operation, so a failure found after the whole batch — the
   assistant-authored step and code checks, `op_not_applied`, `node_not_ready`,
   `invalid_config`, `scenario_unrouted`, `schema_unresolvable` — is stamped with the
   index of the operation that wrote its node and with the planned graph.

   A node setting a config parser refuses raises `ConfigSettingError` (a
   `HauteValidationError`, so still a `ValueError` and a per-node failure in an editor
   preview) from `_banding_config`, `_rating` and `_rating_step_config`, carrying
   `setting` (`factors`, `tables` or `combinedOutputs`), an optional `fix` (a breakpoint
   kind mismatch names the boundaries the column takes, "a Date column's boundaries are
   dates like 2024-12-31"; a rating row without its factor names the row shape) and
   `values`, the configured values the message quotes. Before save validation,
   `build_verified_plan` runs the banding and rating-step parsers over each node the
   plan writes, so a refusal is `invalid_config` located at that node, its `setting` as
   `field` and the operation that wrote it. One the engine raises while resolving a
   schema is the `schema_unresolvable` failure located with `field` set to its
   `setting` and the parser's fix. Either message is shown whole when the policy lets
   the model read saved configuration or every quoted value is one the model's own
   operations sent; otherwise it says only that the setting is invalid and that its
   message quotes withheld configuration. A schema failure is located at the node the
   graph walk was building or running when it was raised (`errors.mark_failing_node`,
   set by the walker's per-node boundary; `failing_node` reads it), falling back to the
   validated target for a lazy failure that surfaces only when the target's schema is
   read; its message then names both (`Schema validation of node 'priced' failed at node
   'rates'.`), and `fix` and `context.inputs` are the raising node's. A plain exception
   that is not Haute's keeps the sanitized internal detail.

   The executor's `dry_run_graph_edits` passes the plan domain a `ConfigVisibility`:
   `withheld` when the session's policy withholds saved configuration (the config
   part's requirement is unmet), and otherwise `read`, the nodes whose saved
   configuration the running turn has seen. The executor is built per turn and records
   a node when a successful `inspect_node` result carries its config part (after the
   result bound, so a result too large to return records nothing); an earlier turn's
   reads do not count, because compaction drops their results. A read follows its
   node. Within a batch `_apply_ops_with_refs` applies each `rename_node` to the
   visibility as it applies the operation (`ConfigVisibility.renamed`: the new id is
   read exactly when the old id was, whatever node held the new id before) and each
   `delete_node` (`ConfigVisibility.deleted` drops the read), so a later
   `update_node` is judged against the reads as they stand at that operation. Across
   applies, after an apply whose result carries a change record (its save
   committed), the executor reads the applied plan's complete, untruncated
   `SemanticChanges` from the plan store without side effect
   (`PlanStore.applied_changes`, `None` once the plan has left the store) and
   carries its reads through them (`_carry_config_reads`, `ConfigVisibility.saved`):
   the renames in operation order, then the removed nodes' reads dropped, then each
   added node recorded except an id a rename produced, which takes the renamed
   node's read instead. A save that committed but failed verification, or whose
   plan the store no longer holds, drops every read, since the saved graph is not
   known to be the one the turn read; an apply that saved nothing leaves them. The
   change card's node chips are not the source, because they are capped and keep
   only each final id's last rename. An `update_node` on a node the plan
   did not add, and not seen this turn under a readable policy, that replaces a key
   whose current value is a non-empty list or map is refused
   (`_refuse_blind_rewrite`) unless the new value keeps every entry: each list entry
   present unchanged, each map key present with an equal value (`None`, which removes
   the key, keeps none). The error is located at the operation, node and key, names the
   entries it would change or drop by their identity where that is metadata
   (`outputColumn` for banding factors, rating tables and combined outputs,
   `output_path` for response rows, `id` for pivots and steps, `name` for constants, the
   entry itself for `inputs` and `selected_columns`, the key for `input_scenario_map`,
   `inputMapping` and `column_renames`) and otherwise by entry position or as a count of
   a map's keys, never a rule value. Under a withholding policy it is `config_withheld`:
   its message says the model cannot read that configuration under the project's
   egress policy, and its fix says to ask the analyst on a `NEEDS_INPUT:` line instead
   of retyping it. Under a readable one it is `config_unread`: its message says the
   model has not read the node's saved configuration in this turn, and its fix says to
   read the node with `inspect_node`'s config part and resend the update keeping the
   key's existing entries. For `steps` either fix says to change them with
   `edit_steps`. Both are retryable. A `ConfigVisibility` of `None` checks no rewrite:
   an apply replays a plan that passed its dry-run, and an example's dry-run check
   composes nothing from memory. The dry-run boundary renders `where`, `fix`, and,
   when `where.node` is set, `context.inputs`: each incoming input's code-visible name
   and its column names, resolved schema-only per source by `_input_columns`, the same
   resolution that decides which Polars-named columns an execution failure may name.
   An input whose source does not resolve is left out. The executor admits the dry-run
   and apply tools only under the schema part's permission (a readable policy whose
   `max_sensitivity` is not `public`), so a public policy refuses the call as
   `egress_policy_denied` before any column is resolved. `did_you_mean` is the
   `difflib` close matches (at most three, cutoff 0.6) of each column an execution
   failure names that no input provides, among those input and column names only; for
   an unknown node reference (see Edge cases) it is the failure's own close node ids of
   the working graph, the ids `get_pipeline` already lists plus those the batch added.
   `schema_unresolvable` always carries a `fix` naming the step or node to correct,
   and the replacement name when `did_you_mean` has one. An unknown tool name carries
   the valid names and its close matches among them; `ask`, `ask_user`, `clarify` and
   `question` carry the fix that asking the analyst is a reply line starting
   `NEEDS_INPUT:`. A step that reads an input no edge connects, naming a node of the
   graph, carries the fix that adds that edge. None of these fields is
   persisted: durable history keeps only `code`, `validation_path` and
   `validation_reason`.

   Before any dispatcher indexes an argument, the executor validates the
   complete JSON value against the operation descriptor's closed input schema,
   including required/unknown fields, discriminated operation variants,
   bounds, enums, hash patterns, JSON-serialisability, and finite numbers.
   For a closed object union whose branches expose a common `const`/`enum`
   discriminator such as `op` or `kind`, validation first selects that branch. When the
   value selects several branches (every `recipe` operation has `op` `recipe`), it
   narrows them by the next discriminator whose values differ among them (`recipe`); a
   candidate whose values are the same on every remaining branch never selects.
   A selected-branch failure therefore retains its exact safe schema path and stable
   value-free reason instead of becoming a generic union mismatch. These two fields are
   included in the structured error and durable redacted result, while the rejected value
   and free-form message are not persisted. A rejection additionally names what would
   satisfy it, because a stable reason alone is not correctable: a bound rejection
   (`below_minimum`, `above_maximum`, `too_few_items`, `too_many_items`, `too_short`,
   `too_long`) states the bound; `unknown_field` carries
   `unknown_fields` (the rejected keys) and `allowed_fields` (the closed allowlist), and
   a field another operation takes is reported ahead of the fields the selected one
   misses, with a `fix` naming that operation (`update_node` sent with `edits` points at
   `edit_steps`), and
   `wrong_type` carries `expected_types` and the `received_type`, spelling a boolean as
   "JSON boolean (true or false)" so a model that sent `True` or `"true"` sees the literal
   it needs, without the rejected value being echoed, and adding an explicit
   "send the value itself, not a JSON-encoded string of it" when a string arrived where an
   array or object was declared — the exact shape a gateway dialect produces, and one the
   model cannot infer from a bare type complaint. Such a string that opens like JSON (`[`
   or `{`) but does not decode is instead `invalid_json_text`: its message names the
   decoder's message, the character position and up to thirty characters on each side
   with the position marked (`ops is JSON text with an error at character 486 (Expecting
   ',' delimiter): ...1000)}]}}, {"<<here>>op": ...`), and its `fix` says to correct that
   spot and resend, escaping quotes and newlines inside a string such as a step's code.
   A model whose provider sends containers as JSON text cannot send the value itself, so
   the live evaluation of 2026-10-01 saw Qwen resend the same broken text after the
   type-only message until the identical-request stop ended the turn, and a later run
   saw it resend a text lacking one `}` after the located message alone. So when
   brackets left open explain the error, the message adds them and the `fix` says how
   to close them. The text up to the error is scanned with string contents and their
   escapes skipped; a bracket fault is a complete value ending there followed either by
   a closer that does not close the innermost open bracket or by the end of the text.
   The message then adds, after the excerpt and a semicolon, at most the three innermost
   still-open brackets, each with its character and where it sits (`brackets still open
   at character 358, innermost first: "{" from character 59 (an item of "edits"), "["
   from character 58 (the value of "edits"), "{" from character 1 (an item of the
   top-level list).`), and the `fix` is
   `Close the object opened at character 59 with "}" before the "]" at character 358,
   then resend the call.`, or `Close them at the end of the text with "}]", then resend
   the call.` when the text ends. Any other fault (every bracket closed, an error inside
   a string, after a key, `,` or `:`, or at another character) keeps the located message
   and the escaping fix. The text is diagnosed, never repaired. The excerpt is the
   model's own argument, already in its history, like a rejected key. Each different
   text is progress for the dry-run budget, because the budget compares the whole
   arguments of a call whose error names no operation. A `wrong_type` message also repeats the
   field's own schema description when it has one, so a categorical rule `value` sent as a
   boolean or number reads the text form it must take. None of these are persisted, because
   `_session._persisted_message` copies exactly `code`, `validation_path`, and
   `validation_reason`. A rejected key is content the model itself submitted and is
   already in provider history, so naming it back is not new egress — and it is what
   makes the rejection correctable on the next attempt, where
   "contains a field that is not allowed" was not. Every malformed known-tool call
   returns `invalid_request`. That path never raises a `KeyError`, echoes any other rejected
   value, or invokes the operation.
6. Limits: a wall-clock deadline (`HAUTE_ASSISTANT_TURN_TIMEOUT`) checked around provider
   streaming and before each tool dispatch, and passed to the provider: the loop runs each
   step of a provider stream (`anext`) inside `turn_deadline(deadline)` and never across a
   `yield`, so the context variable is set and reset in one context and any stream the
   provider wraps sees it, and an adapter's pre-stream retry never waits past it; and a
   per-turn tool-call cap
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

- **Anthropic** — `client.messages.stream(model=…, system=[{"type": "text", "text": <system
  prompt>, "cache_control": {"type": "ephemeral"}}], messages=…, tools=…,
  max_tokens=<HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS>, thinking={"type": "adaptive"},
  output_config={"effort": "medium"})`. The request's one cache breakpoint is the system
  block: tools render before the system prompt, so it caches the tool definitions and the
  frozen system prompt together, and the history, the turn context and the current turn
  (a mid-conversation `system` message or text in a user message, all inside `messages`)
  follow it. No other block carries `cache_control`. Thinking and effort are sent for the
  models in `ADAPTIVE_THINKING_MODELS` — `claude-fable-5`, `claude-fable-5-1`,
  `claude-mythos-5`, `claude-mythos-5-1`, `claude-opus-5-5`, `claude-opus-5`,
  `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-5-5`,
  `claude-sonnet-5` and `claude-sonnet-4-6`, the models that take `{"type": "adaptive"}` —
  with `ANTHROPIC_EFFORT = "medium"`, a level each of them accepts (see the high-level
  Design rationale). `thinking.display` is left at the model's default; the adapter
  forwards no thinking text. Constructing `AnthropicProvider` for any other model raises
  `ConfigError` naming the model and the supported ones. Text deltas → `TextDelta`; a `tool_use` content block's
  `input_json_delta` fragments accumulate per block and emit one `ToolCallRequest` at the
  block's stop; the message stop reason (`end_turn` vs `tool_use`) → `TurnStop`; usage
  from the message-level usage events. A `thinking` block's start emits `ThinkingStarted`
  and its `thinking_delta` and `signature_delta` fragments accumulate into one
  `{"type": "thinking", "thinking", "signature"}` block, which a missing signature at the
  block's stop makes a `malformed_stream`; a `redacted_thinking` block arrives whole in its
  start event, also emits `ThinkingStarted` and is kept as `{"type":
  "redacted_thinking", "data"}`. Thinking is accumulated from the raw events whatever the
  model, since some models think without the parameter. When the message held a thinking
  block, `ReplayContent` follows its last block before the `TurnStop`, carrying every
  content block in stream order: the thinking blocks as accumulated, non-empty text
  blocks, and `tool_use` blocks with their parsed input. In the request, the consecutive tool results of
  one round travel as `tool_result` blocks in a single user message, as the Messages API
  expects for parallel tool calls, and an assistant message carrying `provider_content`
  is sent as exactly those blocks, after checking that their `tool_use` ids are the
  message's tool-call ids in order (a mismatch raises). Within a turn each round's request
  therefore extends the previous round's byte for byte, as preserved thinking requires;
  no earlier turn's thinking is sent, because the history holds turn records only.
- **OpenAI and Databricks** — `client.chat.completions.create(model=…, messages=…, tools=…, stream=True,
  stream_options={"include_usage": True})` plus the output budget, whose parameter name is
  target-mapped: `max_completion_tokens` against api.openai.com (required by current OpenAI
  models), but `max_tokens` whenever `base_url` is set — the parameter Databricks' Chat
  Completions contract documents. Chat Completions, not the Responses API, deliberately: it
  is the OpenAI-compatible protocol Databricks model-serving endpoints implement.
  `DatabricksProvider` uses the URL derived by `_config`, attributes errors as
  `databricks`, and otherwise reuses this exact stream path; adapter tests assert the
  emitted request for both shapes. Every OpenAI-compatible client is constructed with an
  explicit `openai.Timeout` (the SDK's own type: its transport package is the SDK's
  dependency, not Haute's, and openai 3 moved it to `httpx2`): a thirty-second connect timeout (the SDK default of five seconds
  failed live against a Databricks serving endpoint before any stream existed) and a read
  timeout equal to `HAUTE_ASSISTANT_TURN_TIMEOUT`. Databricks client construction disables
  the OpenAI SDK's internal retries. `DatabricksProvider` retries only a pre-stream SDK
  exception classified `rate_limit` or `connection` (connection, timeout, or network
  failures), against the identical model/endpoint request, by the per-category schedule
  `pre_stream_retry_delays`: `connection` at most twice, after one and three seconds, and
  `rate_limit` at most three times, after five, fifteen and thirty seconds, each category
  counting its own retries. A rate limit whose SDK error carries an HTTP response with a
  `Retry-After` header waits what the header asks (`_retry_after_seconds`: delta-seconds, or
  an HTTP date less the current time, never below zero) in place of that retry's scheduled
  delay, still within the schedule's count; a header that is neither form is ignored. A wait
  that would end at or after the turn's deadline is not taken: the exception is raised at
  once and classified as below, so the turn fails as `rate_limit` (or `connection`) rather
  than at its time limit. The deadline reaches the adapter through
  `turn_deadline(deadline)`, a context variable the loop sets around each `anext` of a
  provider stream (read with `current_turn_deadline()`); outside a turn there is none and
  only the schedule's count bounds the retries. Each retry is logged as
  `assistant_provider_request_retry` by provider identity, failure class, SDK exception class
  name, ordinal, delay and its source (`schedule` or `retry_after`) without the raw
  response, and a retry not taken for the deadline as
  `assistant_provider_request_retry_past_deadline`. A failure after a stream
  object exists is never retried; exhausted retries retain the sanitized `databricks`
  failure class (`rate_limit` or `connection`). The direct OpenAI client keeps the SDK's
  own two request retries, which cover the same pre-stream connection and timeout
  failures, so no provider nests two retry layers. These adapters send no cache control,
  thinking or effort; an assistant message carrying `provider_content` never reaches them
  (only the Anthropic adapter emits replay content) and raises if it does.
  Before a provider request, the adapter projects every canonical tool input schema for
  its lane (`_canonical_tools` or `_compatible_tools`), either way from the one canonical
  schema. The **canonical**
  projection, which the Anthropic and OpenAI lanes send and the Databricks lane sends
  only under the evaluation's `canonical_tools` variant, is the canonical input schema
  unchanged, except that a strict tool is sent its strict schema. A tool is strict only on
  the Anthropic and OpenAI lanes, only when it is a read operation (an operation not in
  `_catalog.MUTATING_OPERATION_IDS`), and only when its canonical schema reduces to the
  strict subset (`_strict_tool_schema`): at every level an object closed by
  `additionalProperties: false` with declared properties, no `oneOf`, `anyOf`, `allOf` or
  `$ref`, no nullable or multi-typed value, and every value typed or enumerated. The
  reduction keeps types, descriptions, enumerations, properties, required fields, items
  and closure, declares an enumeration's single JSON type, drops every other validation
  keyword (Anthropic's strict mode refuses `minimum`, `maximum`, `minLength`, `maxLength`,
  `maxItems` and `uniqueItems` with an HTTP 400) and states a number's `minimum` and
  `maximum` in its description as the compatible projection does. On the Anthropic lane
  an optional property stays optional. OpenAI's strict mode requires every property, so on
  that lane each optional property is required and nullable (`"type": ["string",
  "null"]`, with `null` added to an enumeration), and the OpenAI adapter removes a `null`
  it receives for an optional property of a tool it sent strict before validation; the
  reduction refuses a schema whose optional property is already nullable, so that `null`
  always means omitted. The Anthropic tool carries `"strict": true` beside its
  `input_schema` and the OpenAI function beside its `parameters`; a tool that is not
  strict carries no `strict` key. The strict tools are `get_pipeline`, `inspect_node`,
  `find_data`, `read_reference` and `get_project_knowledge`: `dry_run_graph_edits` reads
  but its operation union and open `config` and step objects do not reduce, and
  `apply_graph_plan` mutates. Together they stay within Anthropic's documented strict
  limits of twenty strict tools, twenty-four optional parameters and sixteen union-typed
  parameters per request. The Databricks lane is never strict: strict mode was not part of
  the probe below, and a variant changes one thing. The **compatible** projection, which the
  Databricks lane sends by default (`DatabricksProvider(tool_projection="compatible")`), is
  a flat wire schema with a forty-property budget per
  tool. It retains object/array shape, property names, descriptions, common required fields,
  single scalar types, enums, and closed-object declarations; nullable scalar unions project
  to their non-null generation type. For a composition of closed object branches that fits
  the remaining budget, the projection unions branch properties, combines discriminator
  constants into one enum, recursively projects a property present in one branch or
  identically declared across branches within the remaining budget, merges different arrays
  of closed object items by the same union/intersection rule, reduces other conflicting
  property schemas to a common portable type, intersects required fields, and remains closed.
  A property whose schema differs between branches without a shared enum keeps their
  common JSON type and joined descriptions, which is how a `recipe` operation's
  `arguments` reach the provider: an object whose description names each recipe's
  arguments. A composition that does not fit remains a generic typed
  container. Patterns and other unsupported validation vocabulary are omitted; a number's
  `minimum` and `maximum` are dropped as keywords but stated in its description ("At
  least 1, at most 10."), after any description of its own.
  **The Databricks schema probe (2026-10-01).** Non-streaming `chat.completions` requests on
  the assistant's configured Databricks credential sent `databricks-qwen35-122b-a10b` and
  `databricks-gpt-oss-120b` the canonical `dry_run_graph_edits` schema (101 property keys
  in all, at most 8 per object, with `anyOf` and `oneOf`, 18,108 characters; its
  compatible projection then had 32 keys, at most 15 per object, no composition and 4,782
  characters; every other tool has at most 3 keys) and flat objects of 16, 17 and 24 keys.
  Both models accepted every schema without an HTTP 400, so Databricks' documented limit
  of sixteen keys and no composition is not enforced on them, and the compatible
  projection is kept as the lane's default for its measured baselines, not for a limit.
  Qwen sent arrays (`ops`, `assumptions`) as JSON-encoded strings under both projections,
  which the decoding pass below resolves whichever projection was sent. gpt-oss-120b sent
  real arrays under the canonical union and, given the compatible projection, read the
  reference first; one of its samples with 24 flat keys made no tool call and leaked its
  reasoning into the content, a single sample from which nothing is concluded. Both Qwen
  samples composed a wrong banding configuration because neither read a reference: the
  probe measures schema acceptance only, not which projection yields better plans, which
  is what the `canonical_tools` variant measures live. Separately, two `ops` strings from
  the live evaluation that failed `json.loads` held brackets the model had mismatched, not
  truncated output.
  `_tools` independently validates
  the decoded call against the unchanged canonical operation schema, so a projection is a
  generation contract rather than an authorization or validation fallback. After the outer
  function-arguments object is parsed,
  Databricks alone performs one schema-directed compatibility pass over it
  (`_normalise_databricks_tool_arguments`): when the canonical input schema declares a
  value as an array, object, boolean, integer or number but the provider returned a
  string, valid finite JSON of the declared type is decoded and then proceeds through the
  unchanged closed tool validator. The pass reads the canonical schema whichever projection
  the lane sent, so it decodes the same under both: the compatible projection keeps no
  union to select a branch from, and both projections derive from the canonical schema.
  The pass walks the declared schema, not the value: an
  object's declared properties, an array's declared items, and within a closed object
  union the one branch the value selects. A branch is selected by discriminators, the
  properties every remaining branch constrains to `const`/`enum` values, compared by
  exact JSON type and value, one at a time while more than one branch remains and the
  discriminator separates them: an operation selects its branch by `op`, and a `recipe`
  operation then by `recipe`, so `ops[i].arguments` and its `rules`, `tables`,
  `combined_outputs` and `output_columns` decode by that recipe's argument schema. When no
  branch or more than one branch remains, nothing below that value is decoded.
  Two spellings decode by the declared type without JSON, each logged as a warning by
  shape only: a string where an array of strings is declared that does not open like
  JSON (`[`, `{` or `"`) becomes a one-item array
  (`assistant_databricks_argument_wrapped_in_array`, field and encoded length), which is
  how Qwen sends `assumptions` as one sentence or a bullet list; and `True` or `False`
  where a boolean is declared becomes that boolean
  (`assistant_databricks_argument_python_boolean`). Invalid JSON or a
  decoded scalar/wrong container remains the original string; the canonical validator
  returns `invalid_request`, nothing executes, and the model can retry in the same turn.
  Declared string fields, an object without declared properties (such as a node's
  `config` or a step), unknown tools, OpenAI, and Anthropic are never coerced. The pass
  follows only declared types, so it never guesses or repairs, while handling the live
  Databricks/Qwen function-calling dialect captured on 2026-07-30 and the same dialect
  inside a recipe operation's arguments, captured on 2026-10-01. An eligible field that
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
- **A response row reads an incoming frame** — for an output node the plan adds or whose
  `outputMapping` it writes, every active row's `source_port` must be the input name of
  one of its incoming edges (`_validate_output_rows_read_inputs`); otherwise the plan
  fails as `invalid_ops` located at the node and `outputMapping`, listing the incoming
  edges. When the port names an unconnected node whose plain edge (no source handle)
  gives that name, the fix adds that edge; otherwise, as when the port names a Quote
  Input or a submodel node, whose edges are named by the frame or port they select, the
  fix is the input-name fix the source step gets (`_input_name_fix`, with `row <n>` and
  `row <n>'s source_port`, "the frame the column comes from"): the edge to add when none
  reaches the node, else the names the edges give with one suggested (also
  `did_you_mean`), then `INPUT_NAMING_RULE`. The
  engine lets a one-input response read any name, so without this check a wrong name
  saved silently. Saved rows the plan does not write are not judged.
- **A categorical factor bands text** — a categorical banding factor the plan writes
  that the saved node does not already hold, on a column whose input dtype is a decimal,
  date or time, fails as `invalid_config` located at the node and `factors`, with a fix
  pointing at `banding: breakpoints` (`_prove_categorical_factors_band_text`, after the
  schemas resolve). Integers, booleans and strings are not refused, since integer codes
  such as vehicle groups are mapped value by value; the editor's banding is unchanged.
- **Every scenario a Source Switch routes is validated** — a switch the plan touches (a
  written node or an edge target) whose `input_scenario_map` routes a scenario only to
  inputs no incoming edge provides fails as `scenario_unrouted`, located at the switch and
  `input_scenario_map`, naming the scenario, the inputs it routes to and the inputs the
  switch's incoming edges do provide, saying that `add_edge`'s `target_handle` never names
  an input and stating `INPUT_NAMING_RULE`. When
  an edge into the switch carries a target handle the scenario routes to, the error names
  the input that edge provides and its fix maps that input to the scenario and lists it in
  `inputs` in place of the handle (`_unrouted_scenario`). Then, outside strict
  verification, each target that resolved under the active scenario is resolved again
  under every other scenario a switch in the flattened graph maps; a failure there is the
  `schema_unresolvable` error naming the scenario, unless the target is not the plan's own
  and fails the same way on the saved pipeline, which is the
  `pre_existing_schema_failure:<node>:<scenario>` warning. These resolutions add no
  evidence, so post-save verification replays the active scenario's evidence alone.
  `LiveSwitchScenarioError` names the switch, the scenario and the scenario names it
  maps, all pipeline metadata.
- **An unknown node reference says the fix** — when an operation's `node`, `source` or
  `target` names no node of the working graph, `_resolve_node_id` raises
  `UnknownNodeReferenceError` (an `OpValidationError` carrying the reference and its
  role), and `_apply_ops_with_refs` rewrites its message and `fix` from the whole batch;
  `where` carries the operation's index. The first matching case decides:
  1. a bare word an earlier `add_node` declared as its `ref` — write `$<ref>` or the
     node's id;
  2. a reference a later `add_node` of the batch would satisfy (its sanitised id, its
     name, or its ref with or without `$`) — move that `add_node`, named by its index,
     before this operation;
  3. an unknown `$<ref>` — the refs declared so far, each with its node id;
  4. anything else — the nodes the batch has added so far (id and ref), and that each
     dry-run is a whole plan: a node an earlier failed dry-run proposed is not in the
     graph, so the plan adds it before wiring it. `did_you_mean` carries up to three
     close node ids of the working graph (`difflib`, cutoff 0.6).

  The reference is never resolved on the model's behalf: the live run's two unknown
  references (`join_node`, `enriched_quotes`) were already id-shaped, and sanitisation
  preserves case, so neither was a display name, and accepting a bare ref would make
  `$` optional in one place only.
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
  Every node the batch adds, or whose `steps` it sets or edits, that carries a step list on a
  stepped surface (instances excepted) is rendered with the product's renderer
  (`render_polars_steps`, against the node's incoming input names in the surface's start
  mode), so an invalid step list is an op error naming its step (`where.step` holds the
  id of the step the renderer's error names), worded as the step-render rule below
  describes (a palette-default Transform left at `steps: []` gets the surface's
  free-code form, because a Transform's steps must choose their input), except that a
  step reading an upstream node that is not wired in gets the `add_edge` fix (only a node
  whose plain edge, with no source handle, gives its own id counts, so a Quote Input or a
  submodel never does: their edges are named by the frame or port they select), and that
  a Transform's source step reading an input no incoming edge gives gets the input-name
  fix (`_input_name_fix`, shared with the response-row check) that teaches the naming:
  with no incoming edge, `'<node>' has no incoming edge, so its source step reads
  nothing. Add {"op": "add_edge", "source": "<upstream node>", "target": "<node>"} to
  this plan, with "source_handle": "<frame>" when the source is a Quote Input, and set
  the source step's input to the name that edge gives.`; otherwise `The edges into
  '<node>' provide the input(s) <names>; set the source step's input to <name>.`, naming
  the only one, or the closest by `difflib` (cutoff 0.6) "if that is the frame df starts
  from", or "the one df starts from" when none is close. Either form ends with
  `INPUT_NAMING_RULE`, and the suggested name is also returned as `did_you_mean`. Each `free_code` step
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
  - in `add_node` of a stepped type: a `code` key or a `steps` value that is not a list;
  - on a node that is, or that the operation leaves, an instance (`instanceOf` set,
    whatever its type): any `steps` or `code` key, since an instance runs its original's
    configuration and its own would never be read. The refusal names the original and
    points there; writing `instanceOf: null` with the logic detaches the node first and
    the rules above then apply.
  Code-mode nodes keep `code` editing. Each refusal names the node, its surface
  (`STEPPED_SURFACE_LABELS`) and the free-code form to write instead: on a surface
  whose steps choose their input (`start == "input"`, the Transform)
  `[{"id": "start", "kind": "source", "input": "<edge name>"}, {"id": "logic", "kind":
  "free_code", "code": "..."}]`, and on every other stepped surface
  `[{"id": "logic", "kind": "free_code", "code": "..."}]`.
  `edit_steps` keeps the same transitions: it only changes a list the node already
  holds, so it never switches a node between steps and code, and it is refused on a
  code-mode node (edit its `code` instead), on a stepped-type node without a list, and on
  an instance (edit its original instead).
  After each operation's `with_config`, every key the model wrote must hold the written
  value in the materialised config (a key written as `null` must be absent; for
  `edit_steps`, `steps` must hold the edited list), and when the operation wrote `steps`
  the config must carry no `_steps_error`. Otherwise the plan fails with the stable code
  `op_not_applied` naming the node and the key, or the step error with `where.step` set
  to the id of the step it names. A palette-default node's own `_steps_error` (a
  Transform's empty step list) is not the operation's write and does not trip this
  check. Both step-render refusals (`_ops._unrendered_steps`) tell a step the surface
  cannot hold from one that is only incomplete. Only the first points at the free-code
  form: a list-level error, a step that is not an object or has no kind in
  `STEP_KINDS`, a `source` step on a `frame` surface, a `join` or `concat` on a surface
  whose code sees only `df`, and a Transform whose first step is not its `source` or
  that has a later one. Any other step error names the step by kind and id with the
  renderer's message, and the `fix` completes it where it stands (`its filter step
  'keep' cannot be rendered (Step 1: Missing field(s) ...)`, fix `Complete the filter
  step 'keep': ...`; `Correct the free_code step ...` for code that does not parse). A
  `pivot` step on an Explore instead gets the fix that an analyst's pivot table is a
  `pivots` entry in the Explore's config, not a step, since a pivot step only reshapes
  the frame: read `node:explore` with `read_reference` for the entry's shape and add it
  with `update_node`. In the 2026-10-01 evaluation, a pivot step without pivot columns
  on an Explore was redirected to free code, which took the model further from the
  `pivots` entry the analyst asked for.
- **The model sees how each stepped node is authored, without values.** `get_pipeline`
  and the turn context's graph brief carry, for every node of a stepped type (an
  instance has none: its configuration is its original's), `_render.node_authoring`'s
  facts, read from the saved config alone: its state
  (`stepped` when its `steps` list renders, `incomplete` when the list does not render
  or the node has neither steps nor code, `code` otherwise), whether the editor
  discarded its steps (`_steps_discarded`, a code-mode node), one entry per step (its
  id, its kind when it is one of `haute._polars_steps.STEP_KINDS`, otherwise none, which
  `get_pipeline` reports as `null` and the brief as `unknown`, so an incomplete list's
  saved text cannot break a line; the input names a source, join or concat step reads;
  and a free-code step's intent, the text of a first line that is a `#` comment) and, for a list that
  does not render, the id of the step its error names. Both views are `internal`
  project metadata while a step's other fields are `restricted` configuration, so the
  rendering is value-free and follows the policy: a free-code step's intent is
  executable source, shown only under `allow_executable_source`, so under a masking
  policy a free-code step shows only its id and kind; the renderer's message for an
  incomplete list can quote an authored literal, so it is shown only under a
  `restricted` ceiling, and when it names a free-code step only under
  `allow_executable_source` too. `get_pipeline` reports them as a node's `authoring`
  object (`state`, `steps` as `{id, kind, reads?, intent?}`, `steps_discarded` when
  true, `error` as `{step, message?}`); the brief as the state on the node's line
  (`code (steps discarded)`, `incomplete at step "<id>": <message>`) and one
  `step "<id>" <kind>` line per step. A packaged example's rendering carries its
  configuration whole and has no `authoring` object.
- **A rename reconciles structured references and never leaves a consumer silently
  broken.** Downstream nodes name their inputs by the edge's input name
  (`haute._graph_utils.edge_input_name`, the source's sanitised label). `rename_node`
  compares each outgoing edge's input name under the old and the new label (an API
  Input frame or a submodel port keeps its name, so it is never affected) and, for
  every edge whose name changes, rewrites in the same plan the structured fields of
  the target that name the old input, as the editor's rename does
  (`frontend/src/utils/nodeUpdatePlan.ts`): the input references of a Transform's or
  External File's steps (`input` on a source or join, `inputs` on a concat, through
  `haute._polars_steps.rename_step_inputs`), `inputMapping` values,
  `input_scenario_map` keys, `data_input`, `banding_source`, `analysis_input` and
  `ratebook_input`, `outputMapping` rows' `source_port`, and the `inputMapping` keys of
  every instance of the target. An instance's own steps or code are never rewritten.
  Each rewritten node is an updated node of the semantic diff, with one config change
  per field: `<node>:steps[<id>].input` (or `.inputs`), `<node>:inputMapping.<logical>`,
  `<node>:input_scenario_map.<name>`, `<node>:<field>` and
  `<node>:outputMapping[i].source_port`. A rewrite that would give a target two inputs
  of one name, or a mapping two entries of one key, fails the plan as an op error
  naming the target. Code is never rewritten: the operation lists `steps[i].code` when
  a free-code step reads the old name, a code-mode node's `code` when it reads the name
  (unparsable code is listed, since it cannot be shown not to) unless its
  `inputMapping` binds that name to another edge, and `instanceOf` on any node whose
  `instanceOf` names the renamed node's id. When that list is not empty the plan fails
  with the stable code `rename_has_consumers`, a message naming each consumer and
  field, and a `consumers` array of `{node, field}` records; nothing is renamed. The
  check runs against the working graph, so an earlier operation of the batch that
  rewrites the consumer lets the rename apply.
- **Unknown config keys are op errors, not warn-and-drop.** The sidecar writer's
  warn-and-drop exists to tolerate stale keys already on disk; an authoring-time unknown key
  is an LLM mistake that must bounce back as a tool error so the model corrects it. Same
  allowlist source, different strictness, both deliberate.
- **The assistant configuration is closed.** The outer accepted key set is
  exactly `provider`, `model`, `base_url`, and `egress`; the nested table has
  exactly the six required fields: `trust`, `max_sensitivity`,
  `allow_project_knowledge`, `allow_executable_source`, `allow_row_samples`,
  and `allow_aggregate_statistics`. Unknown or missing fields fail
  with their full TOML path, and each `allow_` field must be a boolean. Under
  `trust = "external"` the policy must be `public` with executable source, row
  samples and aggregate statistics all false; any of them true is a
  `ConfigError` naming `[assistant].egress`. `EgressPolicy.policy_hash` covers
  all six fields, so changing any of them changes the hash. A non-string
  `base_url` raises `ConfigError`, any
  `base_url` on Anthropic or Databricks is a not-ready reason, and OpenAI accepts only an
  absolute credential-free HTTP(S) URL with a hostname and valid port.
  Databricks requires `DATABRICKS_HOST` to be an absolute credential-free
  HTTPS workspace-root URL with no query, fragment, or non-root path, strips
  only a trailing slash, derives `/serving-endpoints`, and reads only
  `DATABRICKS_TOKEN` for authentication.
- **Provider compatibility is schema-directed and fail-closed.** Each provider lane
  receives its projection of the canonical schema (canonical for Anthropic and OpenAI,
  with strict read tools; compatible for Databricks unless the `canonical_tools` variant
  runs) while the ordinary tool validator retains the complete canonical schema. Only the
  Databricks adapter may decode a stringified tool argument, only when the canonical
  schema at that value's position exclusively declares one or more
  compatible JSON types from `object`, `array`, `boolean`, `integer`, and `number`, and only
  when the decoded finite JSON has a declared type. Python booleans do not satisfy integer
  or number declarations. String and null declarations, undeclared types, ambiguous schemas,
  and non-finite numbers are not decoded. Only the OpenAI adapter removes a `null`, and only
  for an optional property of a tool it sent strict. A value that cannot be
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
  result and the change card — never swallowed — and, per the service's own design,
  the next successful capture sweeps the orphaned delta up from working-tree state. Read
  tools carry no precondition.
- **The change record describes the reparsed save** — its chips come from the actual diff
  between the graph the plan started from and the graph reparsed after the commit, never
  from the requested operations alone, and its commit is the one the save made. The one
  exception is a committed save whose verification failed: its record is built, right
  after the commit, from the graph the plan computed and the plan's validated diff, and
  the error result carries it. The canvas updates through the `/ws/sync` document
  update, not through the chat event.
- **One `PipelineDocumentUpdatePayload` contract**: the assistant publishes the payload
  shape the watcher publishes, plus `origin` (`{kind: "assistant", session_id, change_id,
  node_ids}`, `node_ids` from `touched_node_ids` of the saved or undone change); the
  watcher and resyncs omit the key. No assistant-specific frame type exists on `/ws/sync`.
  The service's `DocumentUpdatePublisher` takes the source file and the change record
  (or `None` when a committed save has none, which publishes without an origin); the
  tool layer's publisher adds the chat's session id.
- **Assistant saves name their change** — `save_graph_transactionally` takes a
  `commit_message`, which the save passes to `commit_save`; an apply passes
  `change_headline(summary)` and an undo `Undo: ` and the undone change's headline.
  `change_headline` collapses the summary's whitespace to one line and, past
  `CHANGE_HEADLINE_LIMIT` (100) characters, cuts it at the last space that leaves room
  for a closing `…` within the limit (inside the word when there is no such space), so a
  summary as long as the receipt's 400-character bound still makes a one-line commit
  subject. `PlanReceipt` refuses a control character other than
  whitespace, because the ledger history parser delimits commits with control
  characters.
- **Undo restores only the latest change** — the comparison of the current document
  revision with the record's `revision` runs under `save_lock`, and the save's own
  `base_revision` precondition repeats it inside the transaction, so a save that lands
  between the two still refuses the undo.
- **Compacted provider history**: the stored history is append-only (a turn is never
  rewritten; only the retention cap below removes whole oldest turns), and the provider
  sees a view of it built at the turn boundary. `SessionStore.provider_history(session)`
  renders every stored turn, oldest first, as a `TurnRecord` of two neutral messages: a
  `user` message holding the turn's request and an `assistant` message holding
  `render_turn_record(record)`. That text is the turn's final assistant text, a blank line
  when there is one, then a `## Turn record` block: a sentence saying Haute wrote it when
  the turn ended and it is not part of the reply; `- Outcome:` with the outcome kind and,
  for `incomplete` and `committed_unverified`, the stored detail (a `needs_input` or
  `blocked` turn's detail is already its final text), or, for a turn with none, that it
  failed or was stopped before finishing; one `- Saved` line per saved change with its id,
  its document revision and its summary collapsed to one JSON-quoted line; `- Ended at
  revision` with the last saved change's revision when the turn saved; when the turn
  changed the build plan, one `- Build plan as this turn left it:` line saying how many
  items are complete and naming each open item's id and JSON-quoted title (or that every
  item is complete); and one `- Undone by the analyst after this turn` line per id in its
  `undone`. The text of both messages
  counts against `PROVIDER_HISTORY_CHARACTERS` (24,000): records are kept newest first
  while their running total fits, and the older ones are dropped whole, a record larger
  than the budget on its own included (none is ever truncated). When any record was
  dropped, a `controller` message rendered by `render_omitted_turns(count)` leads the
  history, saying how many of the earliest turns are left out; adapters already send a
  controller message as a user message, which a history may begin with on every wire. On
  the OpenAI and Databricks wire a user message that directly follows a controller message
  is merged into it, the note first and a blank line between, so the note leads the first
  kept record's request (or, when no record fits, the current turn's message) and two user
  messages are never consecutive, which some gateways refuse. The
  current turn is outside the budget and travels whole, every round of it, so no turn is
  ever cut in the middle and no earlier turn's tool calls, results or thinking reach the
  provider. Stored history caps at 200 messages by evicting whole oldest turns, which
  bounds memory and the session file; the records, the transcript and Undo read the same
  stored turns. No pruning boundary ever
  separates an assistant tool call from its result (an orphaned half is an invalid provider
  conversation). Live sessions are LRU-capped at 32 idle sessions with least-recently-used
  eviction — a session holding a running turn is pinned, never evicted and outside the cap,
  and eviction drops only the in-memory record: the persisted file revives the id
  transparently on next lookup.
- **Evidence ledger**: `build_tool_executor(source_file, *, session_id, evidence)` calls
  `evidence.begin_turn()`, so every entry an earlier turn of the session observed becomes
  carried. A successful `find_data` or `get_project_knowledge` result observes the
  evidence it returned as current (a `find_data` first drops schema evidence whose file is
  gone, carried or current). Before each `dry_run_graph_edits` the executor runs
  `release_stale_carried` on a worker thread: a carried entry that
  `_evidence_manifest_entry` reports `project_source_missing` or `stale_project_evidence`
  is dropped and logged (`assistant_evidence_released`, with its kind and project-relative
  path), and any other failure there fails the call as `operation_failed`. The dry-run's
  snapshot then includes `sources()`, so a current entry that is missing or changed fails
  it naming the file and the refreshing call, and a carried entry that still holds binds
  the plan. An apply checks its plan's own source manifest and never reads the ledger. The
  ledger is live state on `AssistantSession`, omitted from `as_dict` and
  `as_persisted_dict`, so a revived session starts with an empty one.
- **Build plan**: `update_build_plan {items?, complete?}` first passes its closed input
  schema (`items`: one to twelve `{id, title}` objects, `id` matching
  `^[a-z][a-z0-9_]{0,31}$` and `title` one to 80 characters; `complete`: an item id). A
  call with neither is refused as `empty_plan_update`, and `items` that repeat an id as
  `duplicate_plan_item`, whose `where.field` is `items[<index>].id`. `items` set the plan:
  while the current plan has an open item they revise it (an id it holds keeps its recorded
  changes and its completion under the new title, a new id starts open with no change, and
  an id left out is dropped); with no plan, or one whose every item is complete, they start
  a new plan whose items are all open with no change. `complete` is then checked against
  the resulting plan: an id it lacks is `unknown_plan_item` (`where.field` `complete`,
  `valid_ids` the plan's ids and `did_you_mean` the close ones; with no plan, a `fix`
  saying to set the items first), and an item without a recorded change that is not undone
  is `plan_item_unsaved` (`where` naming the field and the item, `fix` saying to apply a
  plan that names it in `item` first). A claim on an item already complete changes
  nothing. A refused call leaves the plan as it was, so `items` and `complete` in one call
  apply together or not at all, and every refusal is retryable. The success result is
  `items`, per item its `id`, `title`, `complete` and `changes`, the count of its recorded
  changes that are not undone, with `undone`, the count of undone ones, only when there
  are any. `apply_graph_plan {plan_hash, item?}` refuses an `item` the plan does not hold,
  or any `item` without a plan, as `unknown_plan_item` with `where.field` `item` before the
  apply runs, so nothing is saved. Once the apply returns a change record, on success or
  beside the `verification_failed` error of a save that committed, the executor records
  that change's id against the item, inside the shielded apply call, so a stopped turn
  still records it: appended to the item's changes or, when the item already lists that id
  (the same plan saved again after an undo), marked not undone. The result then carries
  `item`. Recording never completes an item. `BuildPlan.undo(change_id)` marks every entry
  with that id undone and reopens each complete item left without an entry that is not
  undone; an id no item lists changes nothing. `update_build_plan` reads no project
  material, so it runs under every egress policy and outside the save lock, and its closed
  schema is sent strict on the Anthropic and OpenAI lanes like the read tools.
- **Dataset discovery and schema inspection share one safety contract**: installed readable path
  extensions come from `routes.files._installed_input_extensions()` and are matched by
  case-folded filename suffix (including compound extensions). The resolved project-relative
  path must contain no hidden component; exact state/credential names in the assistant
  denylist are rejected before listing or reading. Direct calls cannot bypass the filter
  that navigation applies. `find_data(directory, recursive, path)` lists one directory
  (the project root by default) one level deep; recursive mode walks only non-symlink
  descendants, returns deterministic
  project-relative POSIX paths, caps datasets and directories independently, and sets
  `truncated=true` when either cap or the traversal bound is reached. With `path` it also
  returns that file's schema under `schema` (`path`, `columns`, `row_count`,
  `row_count_estimated`, `column_count`, `source_digest`) and the `project_revision`
  that includes its schema evidence; a failure to read that schema fails the call. The
  `find_data` schema is the same for every request wording, and the source-bound
  executor runs the directory, recursion value and path the model supplied. The assistant schema helper is separate from the UI
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
  values in `inspect_node`'s config part, and its profile part plus the text of
  authored-code failures (see Control flow step 5) respectively. A `free_code` step's `code` is
  masked by the same recursive key redaction, while the step's `id`, `kind` and the
  structured steps around it stay visible: structured steps are configuration, not
  executable source. A parsed,
  validated, reported flag that no code path consults is worse than no flag, because a
  project reads its own configuration as a grant that silently never applies.
  Credential keys and inline row-value keys stay redacted under every policy.
  `allow_aggregate_statistics` gates the [data check](#data-checks) through
  `EgressPolicy.permits_data_checks` (the flag under a ceiling above `public`),
  which the dry-run tool reads; it is also hashed and stated in the turn
  context's policy line. The policy line states only what may be sent, never
  that a check ran.
- **The schema part collects nothing** — the invariant is testable: the part's plan
  construction plus `collect_schema()` must never invoke `LazyFrame.collect` (asserted by
  poisoning `collect` in tests). The two honest cost exceptions are inherited, not assistant
  behaviour: plain-`.json` sources parse eagerly inside `read_source` (the GUI's
  `/api/schema` pays the same), and a never-fetched Databricks table raises
  `CacheNotFoundError` instead of ever reaching for credentials or the network.
- **The schema part sees the graph the engine runs, not the graph the editor draws** —
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
  validation, JSON persistence, revival, the current turn's provider rounds, and both
  provider adapters. Persisted tool arguments/results carry no deterministic digest. Tool errors may
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
| Anthropic model outside `ADAPTIVE_THINKING_MODELS` | `_config._resolve_config` (readiness reason, `unsupported_anthropic_model`), and `AnthropicProvider` construction (`ConfigError`) with the same text | Readiness reports not configured with that reason, which the panel's readiness card shows; the message route answers HTTP 400 before the stream opens, naming the model and the supported ones |
| A carried evidence check that fails other than as missing or changed | `_tools` executor, before a dry-run | `operation_failed` tool error, like an unexpected dry-run exception |
| Provider request/stream failures (authentication, rate limit, connection, malformed/truncated/filtered output) | `_providers` | `AssistantProviderError` → terminal `failed` SSE event after the response has started |
| Op validation, save validation, missing dataset, unknown node, unknown example name, unresolvable node schema (unfetched Databricks cache, missing artifact, invalid node code) | `_tools`/`_ops`/`_assets`/engine/save service | Structured tool error returned to the model (visible as a failed activity row); never terminates the turn |
| Authored-code failure (a Polars error, an exception raised from node code, or a preamble failure) while `inspect_node`'s schema or profile part, dry-run or apply resolves a schema or frame | engine, rendered by `_tools` | Structured `schema_unresolvable` (`preamble_failed` when the preamble fails during plan validation) error with the exception type, line or step, and only the named columns the egress policy already discloses (the failing node's input schemas, the model's own plan text, saved code only under `allow_executable_source`); the error's own text only when `allow_row_samples` is true; an unreadable egress policy withholds the text and names why |
| Working-branch state `"git-unavailable"` | `_config.mutations_readiness` | Status 200 with `mutations_enabled: false` and the fixed Git-unavailable reason; a mutation tool call returns `authority_denied` with it |
| A node write that would not land (a written key missing from the materialised config, a `steps` write whose rendering fails, steps the save's reparse would discard) | `_ops` operation replay, `_application` dry-run reparse proof | Structured `op_not_applied` tool error naming the node and the key or step problem; nothing is written |
| A Modelling or Load File node the plan adds or updates that is not ready (no target, an incomplete objective, a configured column its input lacks, a file that is missing or does not load as its `fileType`) | `_application._prove_nodes_ready` | Structured `node_not_ready` tool error naming the node and the product's message; nothing is written. A malformed modelling value fails earlier as save validation's 400, `operation_failed` |
| A rename whose consumers' code reads the old input name, or whose node an instance names | `_ops` operation replay | Structured `rename_has_consumers` tool error listing each consumer and field, with a `consumers` array of `{node, field}`; nothing is written |
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
  within a batch (add then connect via `$ref`); under withheld configuration an
  `update_node` that changes, drops or removes saved list or map entries is refused as
  `config_withheld` naming the node, key and entries by metadata only (map keys counted
  unless they are input names, a step list pointed at `edit_steps`), while keeping every
  saved entry and a node the plan adds pass; under a readable policy a rewrite of a node
  the turn has not read is `config_unread` naming the dropped response rows by output
  path, with a fix to read the node's config, and one the turn read passes; a read
  follows its node through a rename in the batch, while a node the batch renames onto
  the id of a read node it deleted is `config_unread`; without a
  visibility nothing is guarded; a submodel edit is a
  `submodel_boundary` error; ref resolution (unknown ref, duplicate ref,
  ref shadowing an existing id); each unknown node reference case naming its fix (a ref
  without `$`, an `add_node` later in the batch by id or ref, an undeclared `$ref`, and a
  node no operation adds with its close ids in `did_you_mean`); all-or-nothing on mid-batch validation failure;
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
  `op_not_applied` landing check; `edit_steps` inserting, replacing and removing in
  order with deterministic assigned ids, its refusals on an unknown or duplicate step
  id, a code-mode node, a node without a list and an instance (as are `add_node` and
  `update_node` writing an instance's `steps` or `code`, each pointing at the
  original), its `steps[<id>]` diff entries, and
  an edited free-code step reaching the authored-step checks with `where.step` set;
  a rename rewriting each structured consumer field in one plan and listing them in
  the diff, its collision refusals, the `rename_has_consumers` refusal for each code
  consumer and `instanceOf`, edge-only and API Input renames applying, and parity with
  the fields the editor's `nodeUpdatePlan.ts` reconciles, read from source.
  Also covers canonical revision/plan hashing, semantic diff boundaries,
  closed postconditions, single-use plan transitions (a fresh identical dry-run of an
  applied plan issues it again; a repeated apply without one is refused),
  a plan summary with a control character refused, a two-sentence summary within the
  400-character bound accepted and one past it refused, the change headline cut at a
  word boundary,
  stale/altered-plan rejection before save,
  unrelated-diff detection, and truthful verification evidence.
- **`tests/test_assistant_catalog.py`** — completeness against `NodeType` (mirror of the
  registry-completeness test); folder/decorator facts agree with `_types`/`_config_io`.
  Source, sink-only and single-input sets and palette names are asserted equal to the
  product registries and to the editor's `nodeTypes.ts` declarations, read from source.
  Also pins merged I/O enums, palette defaults, resolved schema agreement, closed operation
  metadata (the eight operations in order, `update_build_plan` last), a plain-words
  activity title for every operation (counted from a dry-run's
  arguments or result and an apply's result, the plain title for a failed call),
  deterministic canonical hashing, cache reuse/invalidation,
  manifest compatibility identity, and the compact/full projection boundary. Every
  node descriptor carries its card without the test fixture: two configurations in
  order for an authorable type, a not-authorable note otherwise, and the banding card's
  date boundary, open-ended last band and string categorical value.
  `step_authoring` is pinned against `STEPPED_NODE_TYPES`: present exactly on the
  stepped types, with the surface's `start` and `inputs`, a source step first exactly
  on an `input` start, and every step carrying a non-empty id and a kind in
  `STEP_KINDS`; the `guide` reference's `step_grammar` equals the renderer's step
  fields.
- **`tests/test_assistant_tools.py`** — through the executor: the dry-run refuses
  retyping a saved step list as a located, retryable `config_withheld` under an
  `internal` policy and `config_unread` under `restricted`; under `restricted` it allows
  the rewrite once that executor's turn read the node's config or added the node, and
  not after a read by an earlier turn's executor; a read follows its node through a
  later apply's rename and ends at an apply's deletion, so a node a later apply renames
  onto the deleted read node's id is `config_unread`, and an apply that commits but
  fails verification drops every read; a submodel occurrence's schema part
  names its `factored` output and `vehicles` input ports, its consumer's input is named
  by the port, its config part and an inner node stay `submodel_boundary`, and the turn
  context lists the occurrence's input and output port (selected by `out__factored`, read
  as `factored`) and the consumer's input from the occurrence; a parser refusal is located
  at the
  node and operation that wrote it (`invalid_config` for a rating row without its factor,
  `schema_unresolvable` with the parser's fix for number breakpoints on a Date column),
  and a schema failure at the node that raised, naming the terminal; a categorical factor
  on a number column points at breakpoints; a step reading an unconnected node carries
  the fix that names the edge; a source step, and a response row, reading an input no
  edge gives carries the add_edge-and-naming-rule fix when no edge reaches its node (also
  when it names a Quote Input node, which no plain edge names), and otherwise the names
  one edge or two edges from Quote Input frames give, suggesting the close one, with the
  rule; a response row naming a submodel occurrence is told its port's name (at the
  validator, since the assistant cannot wire a submodel); a
  `response_output` after a node the plan adds saves that node's name; a switch routing a
  scenario to a dropped input is `scenario_unrouted`; the live renewal plan's shape (a new
  Data Input wired into the switch with a `target_handle`, and that handle mapped to the new
  scenario) is `scenario_unrouted` listing the inputs the switch's edges provide and naming
  the one that edge provides, and the mapping its fix gives dry-runs; an edit that fails only under
  the batch scenario fails naming it, while the brief lists the switch's scenarios;
  `ask`-like tools, `update_node` with `edits` and bound rejections name their fix; a
  submodel boundary is final and says to block; a denied config part names the permitted
  schema part; a failing stepped node's schema part names the step and each input's
  columns without the missing one. The turn context: the brief names each node's
  inputs with their columns and its authoring state (a palette-default Transform is
  `incomplete`, a hook `stepped`, a failing node `code` with no output and no error text,
  and a node reading a column its input lacks, which Polars raises only when the lazy
  schema is read, unresolved together with what reads it while the rest resolves)
  with the selection first and the revision `get_pipeline` reports; a `public` policy
  withholds the graph without reading it; an unknown selected or preview-error node is
  refused; the brief resolves once per revision and keeps only the latest revisions; a
  submodel node has no columns and a Quote Input with two frames lists each port with the
  `source_handle` that selects it and the input name it gives; the preview error is type, line and
  column under `allow_row_samples = false` and its text when row samples are permitted,
  and a resolving node reports none; a long brief stops before its bound with a pointer to
  `get_pipeline`, lists 40 columns per frame, and a label cannot break out of its line.
  The turn context update after an apply holds the new revision and the brief entries of
  the nodes the change records name, in graph order, lists a removed node as removed,
  points to `get_pipeline` when a record was truncated, and under a `public` policy says
  only that the graph is withheld.
  The authoring facts: `get_pipeline` and the brief list each step's id and kind, a
  free-code step's intent only under `allow_executable_source`, the step an incomplete
  list fails at with its message only under a `restricted` ceiling, and a discarded-steps
  marker on a code-mode node; and a free-code step the policy masks is replaced through
  `dry_run_graph_edits` and `apply_graph_plan` with `edit_steps`, leaving the other steps
  as saved.
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
  its close match; the live join shape (an `add_edge` to a node whose `add_node` comes
  later) names that operation's index and the move as its `fix`; an edge to a node no
  operation adds carries the plan's added nodes and `did_you_mean` node ids; and three independent errors, each corrected from its error,
  converge in one scripted turn through the real tools to an applied outcome.
  Contract tests assert that the saved `active_source`
  is passed to the engine; crafted/mocked execution results cover submodel-boundary
  rejection, multi-frame per-port shaping, unknown-node errors, and propagation of an
  unfetched-Databricks `CacheNotFoundError` message as a structured tool error. Reference
  tests pin ordered one-to-twelve id batches across the four namespaces, unknown-id
  rejection with close valid ids, duplicate rejection, and JSON materialisation.
  `inspect_node` coverage pins each part's egress gate on its own: under an `internal`
  ceiling the schema answers while config and profile are withheld with their required
  settings, under `restricted` without row samples only the profile is withheld, a call
  whose every part is denied is `egress_policy_denied` with the withheld list,
  `input` without the profile part is refused, and a call whose every part fails
  (an unknown node) is the first part's error with `part` naming it. Recipe-operation coverage runs through the
  source-bound executor: a recipe operation and a primitive operation (an edge from the
  recipe's node by its `ref`, and an update beside it) apply in one plan with one change
  record; two recipes in one batch keep distinct refs; a recipe argument failure carries
  the recipe operation's index, `recipe` and `fix`; and a failure in a primitive operation
  after a recipe carries that operation's index in the batch the model sent. Discriminated-union failures pin precise validation paths and stable
  value-free reasons without invoking an operation; an `unknown_field` rejection pins the
  named rejected keys and closed allowlist, and a `wrong_type` rejection pins the expected
  and received JSON types for both a stringified container and a lone object sent where a
  batch was declared, while JSON text with an unterminated code string is
  `invalid_json_text` naming the decoder's message, the character and the marked
  excerpt, with a fix. The bracket scan is pinned on a text missing the `}` of an edit
  object around code holding its own brackets and escaped quotes (the brackets still
  open, innermost first, and where each sits), an unterminated string (its brackets are
  not counted and no value ends in it) and balanced but invalid text; the message names
  the innermost three open brackets with the closing fix for the missing brace and for a
  text ending open, and keeps the located message and escaping fix for the other two.
  The rendered graph is asserted to
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
  blocking the dry-run with `project_source_missing` naming it until `find_data` runs
  again, within the turn that inspected it. Through the session's ledger, a schema
  inspected in one turn binds the next turn's plan while it holds, with no history
  passed, and once its file changed or vanished the next turn's dry-run releases it and
  succeeds. A dry-run adding an Excel Data
  Input is refused as `schema_unresolvable` whose tool message carries the reason and
  the "Preview this input first" remedy. Build-plan coverage runs through the source-bound
  executor on the session's `BuildPlan`: a claim on an item with no saved change is a
  located, retryable `plan_item_unsaved` that leaves the plan unchanged; an apply naming
  an item the plan lacks is `unknown_plan_item` and saves nothing; an apply naming an item
  records its change against it, carries `item` and leaves it open, and the claim then
  completes it; and the turn context lists an unfinished plan's items and omits a finished
  one.
- **`tests/test_assistant_build_plan.py`** — `BuildPlan` on its own: `items` start a
  plan whose items are open with no change; a revision of an unfinished plan keeps an
  item's changes and completion under its id, starts a new id empty and drops an id left
  out, while `items` after a finished plan start a new plan, so a reused id inherits
  nothing; repeated ids, an empty call, an unknown claimed id (with the close ids and the
  plan's ids) and a claim with no change recorded are refused with their codes and leave
  the plan as it was, `items` and a failing claim in one call included; a claim on a
  complete item, an undo of a change no item lists and a refused call keep the same
  snapshot object; recording the same change again after an undo marks it live rather
  than listing it twice; an undo marks the change undone on every item listing it and
  reopens a complete item with no other live change, but not one that has another; and
  the model rejects a complete item without a live change. `build_plan_view` counts live
  and undone changes.
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
  scores a tiny CatBoost model logged to the project's local MLflow folder. Added API
  Inputs dry-run and apply with no table snapshot, and those plans (the API Input and
  Source Switch cards) carry `input_schema_declared` evidence; their snapshots are
  built only after apply, for execution, as a preview would. A non-authorable
  card's note contains the operation layer's refusal and the dry-run refuses the type.
  The walkthrough guesses (breakpoint rules written as `lower`/`upper` intervals, an
  operator-row `continuous` factor, and response paths `quote_id` and `$.quote_id`)
  replay through `dry_run_graph_edits` to their structured refusals, the card's field
  meanings state the fix, and the card's configuration on the same request passes its
  first dry-run at the schema tier. A malformed card (an unknown key, configurations out
  of order, no field meanings, a not-authorable card with configurations) raises
  `NodeCardError`.
- **`tests/test_assistant_recipes.py`** — closed descriptor completeness,
  deterministic expansion of `recipe` operations in place with per-operation refs and
  their postconditions, the index map back to the model's operations, unresolved-decision
  handling, primitive-operation validation, linked examples, preconditions, and stable
  recipe failures; a recipe's response rows read the created node by its sanitised id.
- **`tests/test_assistant_application.py`** — saved-state inspection, exact
  no-write planning, single-use authority, stale-state rejection,
  transactional apply, semantic-diff verification, postconditions, and
  committed verification-failure reporting. The change card is pinned here too: for a
  four-node batch (a Data Input, a stepped Transform, a Banding and an Output) the
  record names each chip with its palette type, step kinds and changed fields in words,
  holds no configuration value, step code or intent comment, and carries the commit and
  its parent; an `edit_steps` and a rename show as a changed-step count and a renamed
  chip; and the dry-run and apply results the provider receives for that batch each stay
  under one kilobyte with no echoed operations or repeated diffs. A committed save whose
  verification fails returns a change record of the planned change with the save's
  commit and revision. A repeated identical
  dry-run replaces the receipt of a plan not yet applied. Schema-validation scope is pinned against a
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
  A plan adding a Quote Input with two declared tables and a Transform over one of them
  applies with one `input_schema_declared` record for that table (tier `declared`, its
  declared column count), post-save verification reproduces it, and no table generation
  is written; a column without a declared type fails the dry-run as `schema_unresolvable`
  carrying the contract validator's `ApiInputSchemaError` for that column.
- **`tests/test_assistant_project_knowledge.py`** — source attribution,
  sensitivity filtering, cache invalidation/rebuild, bounded queries, tool
  policy, symlink containment, and metadata-only durable cache state.
- **`tests/test_assistant_self_test.py`** — closed case-format v5 loading and
  selection (by id, area and split) over the checked-in portfolio, which covers every area in
  both splits; no case id is a teaching example's name, and no fixture path names an assistant
  context artifact. Exactly `motor_value_band_factor_withheld` and `breakpoint_age_banding` run
  under the `metadata_only` egress profile and every other case under `project`; exactly
  `motor_explore_then_run_blocked` and `smoke_staged_pricing_build` are inapplicable to
  `one_apply_per_turn`; every node a turn's expectations name that the project does not hold yet
  is named in that turn's or an earlier turn's request, other than as a file path; and every
  breakpoint a case's expected banding states is a value of the column its golden bands. Every fixture project is save-canonical: a copy parsed and saved again
  through the transactional save service rewrites none of its checked-in files. Preparing a copy
  logs each fixture model and writes its run id over the Model Scoring placeholder, and a model
  file without a placeholder fails loudly. Case loading refuses an unknown or missing key (`egress` and
  `inapplicable_variants` included), an unknown egress profile or variant, an inapplicable
  `multi_apply`, a node-type name that is not a `NodeType` value, an `applied` turn that does not save or an `answered` turn that
  does, and node configurations or goldens on a turn that saves nothing; declared data findings
  are a closed `{reported, kept}` of `{kind, node}` findings, refused when a kind cannot be
  advisory, a finding repeats, `reported` is empty, `kept` holds a finding `reported` does not,
  a turn that saves nothing keeps one, or a turn lacks the key. Scoring reads the
  completed event's typed outcome: a turn that saved a change and then ended `blocked` scores as
  a saved, blocked turn, never as `applied`; an `incomplete` turn matches no expected outcome;
  and the count of saved changes, never the assistant's text, decides `saves`. Scoring also
  requires one change card for every saved plan, connectivity of the changed nodes and their
  neighbours (nodes added or retyped, and the endpoints of added or removed edges, together with
  every node adjacent to them, form one connected component; the report names the nodes outside
  its largest component, and an untouched node elsewhere in the fixture never fails the check),
  and exact edge-join base/join port assertions; each reason carries its turn and layer.
  Efficiency limits fail only a case that declares them; every other case reports its metrics
  and passes on correctness alone. The configuration layer matches subsets recursively, compares
  same-length lists element by element as subsets (a key an element leaves out is free; a list
  of another length, order or shape fails at its path) and names the first differing path, the collateral layer reports a changed or removed pre-existing node
  unless the turn allows it, and the editor layer reports a new stepped-type node not authored as
  steps or carrying `_steps_error`, while a pre-existing code-mode node is not judged. A null
  expected target handle matches an edge by source/target endpoints for ordinary single-input
  nodes; non-null handles remain exact port assertions. `_graph_structure` is pinned directly
  against the compact renderer's own output, because reading a handle under a name the renderer
  does not emit yields `None` without raising and turns every port assertion into a silent
  pass-through failure. The data findings layer judges the saved graph: a turn that
  declares no findings reports the advisory findings its saved graph's check shows,
  failing the case only in the `recovery` area; a declaring turn's layer fails, and in the
  `recovery` area its case with `data_findings` as the first failing layer when nothing else
  fails, when the saved graph still shows a finding it does not keep or no longer shows one
  it keeps, or when no change card showed the analyst a kept finding, while outside that
  area the same failure is reported and the case passes; a recovery turn that never received
  its declared finding and saved a clean graph passes; a turn no check ran for is
  `not_measured` and passes; the recovery metric classifies a saving turn as recovered
  (received an advisory finding, saved a clean graph), avoided (received none, saved a
  clean graph) or not recovered (a saved graph that fails the layer, or a finding received
  and the turn ended `blocked` with nothing saved), and leaves out a turn whose saved graph
  was not measured or that received nothing and saved nothing (asked a question); a case
  is not recovered when one turn is, else recovered, else avoided; and the changed nodes
  the saved graph's check covers are the new, rewritten and rewired nodes, without a node
  that has no output frame. Coverage
  also pins the redacted report v7 shape with its evidence kind,
  per-area counts (crashed and not-applicable cases included) and medians over the cases that
  did not crash, each case's egress profile, per-layer results, first failing layer and crash
  traceback, the data findings layer per turn, case and area with the recovery metric's
  received, recovered and avoided counts and its rate, recovered over received, per area and
  for the run, and the not-applicable cases listed apart from the
  results; a report refusing to mix replay and live evidence or to list a case as both run and
  not applicable; a case that raises in its own process coming back as its crash result,
  its traceback as text, every layer failed and its transcript written; through `record`'s
  per-case process runner (marked `slow`), an exception that cannot be unpickled and a
  process that ends abruptly each recorded as that case's crash while the next case runs and
  passes; `compare` reporting per-area counts, flips with the first failing layer, data
  findings flips between passed and failed, both runs' recovery metrics with the differences
  of their rates and avoided counts, each
  report's not-applicable cases (never a flip or a one-sided case), one-sided cases and metric
  differences, and refusing reports of different evidence kinds; a case refused under a variant
  it is inapplicable to, and the one-apply-per-turn variant ending a scripted multi-stage turn at
  its first saving apply without another provider request once that mark is cleared; the
  canonical-tools variant rebuilding a Databricks provider, on its own client, to send the
  canonical projection, and refusing an OpenAI configuration before the case runs; `record`
  refusing, before it resolves a provider, a variant no selected case applies to; a transcript
  written only under a Git-ignored directory and refused elsewhere; the support matrix
  attributing a run to its configuration; each egress profile's exact policy at the invoking
  trust; a scripted-provider run under the case's `project` profile, whose transcript keeps
  the dry-run's data check as the model saw it, and one under `metadata_only`, whatever the
  invoking project permits; and one scripted case through `record`'s per-case process
  runner.
- **`tests/test_assistant_replay.py`** — tier 0 of the evaluation: every checked-in reference
  trajectory (`tests/assistant_eval/trajectories/`), of both splits and every turn, replays
  through the real loop, tools, dry-run, apply, parser and disposable Git mutation gate in a copy
  under `tmp_path` and the case's egress profile, within a timeout per turn and with a fresh
  plan store, passes every scoring layer, is labelled replay evidence, and leaves the sandbox project root and working directory
  where it found them. Every case has a trajectory with one recorded turn per case turn, and each
  step-corpus case has both its free-code and its structured form; the structured trajectories
  write the corpus translations, the corpus goldens are the corpus snippets, and
  `polars_corpus`'s data files equal the corpus's normal synthetic inputs. No single-node
  trajectory outside the recovery area reads before its first dry-run, other than the config of a
  node whose saved list or map it restates; every `update_node` that restates a saved non-empty
  list or map follows that turn's config read of the node, unless it is the `metadata_only`
  rewrite the dry-run refuses as `config_withheld`; and the feature transform's first provider
  request carries a turn context listing `quotes` and its `driver_age` column. In process mode
  (spawned preview workers, marked `slow`), the two data-question trajectories replay with
  their one `inspect_node` data call measured in the worker: `claims_null_diagnosis` traces
  `total_incurred`'s nulls to the `quote_claims` join that matches 7 of 10 quotes, and
  `broken_bands_diagnosis` reports `rating_features`' failing step once, naming `vehicle_year`,
  with `vehicle_bands` `upstream_failed`. The five seeded recovery trajectories replay in
  process mode too, each passing every layer with its data findings layer measured: the first
  dry-run's check reports the seeded bug (`banding_all_default`, `rows_emptied`,
  `join_unmatched`, `rating_misses` or `join_validation_failed` on the node the request
  names), the last check the model received and the harness's check of the saved graph show
  no advisory finding but the kept stated-value miss of `recovery_rating_casing`, which its
  change card shows, and each case is classified recovered. The five seeded cases
  declare their bugs, and `smoke_corpus_high_premium_quotes` declares the `rows_emptied` its
  stated threshold keeps, the only other declaration; only it and the rating case keep a
  finding. In thread mode every check, the harness's included, reports
  `worker_mode_unsupported`, so the layer is not measured and the replay passes.
  The staged build (`smoke_staged_pricing_build`) saves a source with its features, a
  banding, a rating and a response as four plans in one turn and streams four change cards
  (the source shares the first plan, because a new node must be connected in the plan that
  adds it), and each later stage reads the columns an earlier one produced from the turn
  context update, which follows each apply's results, rather than from a read call.
  Its trajectory sets a four-item build plan first, names each stage's item on its apply
  and claims the item afterwards: the case transcript's build-plan updates show each
  item open with its one change once its apply saves and complete only after the claim.
  A variant that saves the rating stage in two applies (the table, then its combined
  output) shows the item open with the first change listed, then open with both, and
  complete only once claimed. A two-turn variant whose first turn ends after two stages
  leaves the rating and response items open; the second turn, "Continue", receives a
  turn context listing them open and a turn record of the first turn naming them, then
  saves and claims both against the first turn's item ids.
  A recorded status the tools no longer return raises a divergence naming the trajectory, turn,
  round and call; a golden the saved node does not reproduce fails only the execution layer, one
  listing the same columns in another order matches and one lacking a column does not; a turn
  that ends `blocked` with nothing saved leaves `motor_renewal_scenario`'s golden scenario
  unrouted, and the `LiveSwitchScenarioError` running it raises is an execution reason naming
  that class and message, not an exception out of the case; and a
  `$result` reference to a later call fails trajectory loading.
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
  policy reason under `trust = "external"`; an Anthropic model without adaptive thinking
  is not ready, with the adapter's reason; the egress table is closed and requires every
  key, so a table without `allow_aggregate_statistics` fails naming
  `[assistant].egress.allow_aggregate_statistics` and a non-boolean value fails naming the
  key; `trust = "external"` rejects `allow_aggregate_statistics = true` as it rejects a
  non-public ceiling, executable source and row samples; and the policy hash changes with
  the flag.
- **`tests/test_assistant_providers.py`** — adapters normalise scripted fake SDK streams to
  `ProviderEvent`s; SDK exception classes map to `AssistantProviderError` variants; lazy
  import failure produces the readiness reason, not an ImportError at server start; the
  OpenAI content-delta dialects (plain string, and gateway content-part lists where `text`
  parts stream, `reasoning` parts stay unsurfaced, and unknown part types or non-text
  shapes raise `malformed_stream`); the Databricks adapter reuses the
  OpenAI-compatible request while preserving `databricks` failure attribution; its
  pre-stream retry schedules (a connection failure twice after one and three seconds, a
  rate limit three times after five, fifteen and thirty seconds, none on the direct OpenAI
  client), a real SDK rate-limit error waiting what its `Retry-After` header asks in place of
  a schedule it could not outlast, `_retry_after_seconds` reading delta-seconds, a fraction,
  a negative value as zero, a past and a future HTTP date, and nothing from an unreadable
  header, no header or no response, and a wait past the turn's deadline, from the schedule
  or from the header, not taken: the call fails at once, after one request, with the
  sanitized `databricks provider rate_limit failure` message; each lane
  sends its projection: Anthropic and OpenAI the canonical schema itself for every
  non-strict tool (the operation union with each branch's required fields), strict
  exactly for the six closed tools that do not change the project (the five read tools
  and `update_build_plan`), in their reduced schemas (OpenAI's with every
  property required and the optional ones nullable), and Databricks by default the
  budgeted compatible schemas, including merged discriminated graph-operation fields and
  discriminator enums, with no `strict` key, or the canonical schemas, still not strict,
  when constructed with `tool_projection="canonical"`; both projections of every
  production tool derive from the canonical one (the compatible projection of the
  canonical schema is the Databricks wire schema, and the strict reduction keeps every
  property, required field and description); the strict reduction refuses a union, an open
  object and an already-nullable optional property, and the strict read tools stay
  within Anthropic's strict limits; the OpenAI adapter removes a `null` for an optional
  property of a strict tool and leaves every other value, and every other adapter's,
  untouched; the Databricks adapter decodes valid
  schema-declared array, object, boolean, integer, and finite-number strings from
  the live dialect at the top level and inside a `recipe` operation's arguments (its
  `arguments` object itself, and its `rules` and `output_columns` arrays), identically
  under either projection, leaves declared
  strings (a rule's `"3"` value), nulls, undeclared objects, an operation whose branch no
  discriminator selects, and the other adapters untouched, carries invalid/wrong-type encodings to the canonical validator for a
  recoverable `invalid_request` result, and logs each undecoded eligible field by shape
  alone — asserting both warning events and that the rejected value never reaches the log;
  it wraps a plain-text `assumptions` into a one-item list and decodes `recursive: "True"`
  and `"False"`, each logged by shape, but leaves a string opening like a list a string;
  the compatible projection and the strict reduction state `get_project_knowledge.limit`'s
  bounds in its description.
  On the OpenAI wire a compacted history's leading note leads the next user message, the
  first record's request or, with no record kept, the current message, so no two user
  messages are consecutive.
  A turn context is a mid-conversation `system` message for the Anthropic models that accept
  one and otherwise the leading text of the analyst's message, and one that follows no user
  message fails loudly. A turn context update after tool results is the same `system`
  message for those models, a text block after the `tool_result` blocks for the other
  Anthropic models, and a user message after the tool messages on the OpenAI wire.
  The Anthropic request's only `cache_control` is on its one system text block, with no
  marker on a tool or message and the turn context inside `messages`; a model in
  `ADAPTIVE_THINKING_MODELS` is sent adaptive thinking and `medium` effort and any other
  model is refused at construction with `ConfigError`. A streamed thinking block emits
  `ThinkingStarted` and, with a redacted one, returns in `ReplayContent` in stream order
  with the text and `tool_use` blocks around it; a thinking block without a signature is
  a `malformed_stream`. Driving the real loop over a fake Anthropic client for a
  three-round turn and a second turn, each round's `(system, tools, messages)` request
  serialises to a byte-identical prefix of the next round's, the signed thinking blocks
  return verbatim in their assistant message, and the second turn's request holds no
  thinking block. The OpenAI wire refuses replay content.
- **`tests/test_assistant_prompt_golden.py`** — the rendered system prompt, the two
  turn contexts, the turn context update, the compacted turn records, canonical
  tool definitions and the Anthropic, OpenAI and Databricks wire tools match the golden files; the
  release version and capability hash are normalised out; an added prompt sentence
  and an edited wire-operation field description each fail with the changed lines in
  the unified diff of every affected file and of the hashes file.
- **`tests/test_assistant_loop.py`** — against a scripted fake provider: text-only turn;
  an outcome marker anywhere in the final round, after prose or a list marker, bare or
  wrapped in backticks or markdown emphasis, decides the outcome (the last one wins, with
  the text after it and its wrapping as detail) while lowercase text, a marker inside a
  longer word and a marker without detail do not; an apply in a message that asks is
  refused unrun with `apply_in_outcome_message`; the egress policy says saved
  configuration is withheld below `restricted` and mentions the redaction of code only
  when the config part is readable, and states aggregate data statistics in one line,
  permitted, or not permitted with no data check run and schemas proved;
  tool round-trip; tool error fed back; cap and timeout terminal events; each step of a
  provider stream seeing the turn's deadline and nothing outside a turn seeing one;
  completed/failed
  exactly-one-terminal checks; cancellation drains an in-flight tool, closes the provider
  stream, and releases the session lock; closing at each tool lifecycle yield never
  persists an unmatched call; a raising history append still releases the lock;
  compacted history: a ten-turn conversation whose turns each save a change sends its
  tenth turn the nine earlier turns as records naming every change card's id, summary
  and revision, with no earlier tool call or result, while the transcript keeps all ten
  cards; under a small budget the oldest records drop whole behind the omission note and
  a record larger than the budget leaves only the note; a twenty-one-call turn is
  followed by a turn that sees its request and final text as a record; the stored
  history cap still evicts whole oldest turns; the `thinking` status is streamed when the
  provider reports thinking and replay content reaches the next round of the same turn
  but never the stored turn; the dry-run progress rule — an identical resend stops
  after two attempts, the same diagnostic for an unchanged operation stops, the same
  diagnostic after its operation changed does not, four failures that each change the
  plan spend the budget whatever their class, three independent errors converge to an
  applied turn, repeated malformed calls block with their own wording, and undecodable
  JSON texts that differ are progress while resending one stops; read-only
  questions, explanation requests after a read, and authoring wording without a dry-run end
  `answered` with no controller reminder; a validated plan left unapplied receives exactly
  one reminder naming the plan hash and then ends `incomplete` with its reason; a failed
  dry-run left uncorrected receives the failed-dry-run reminder and ends `incomplete`
  with its reason; a marker answer after the reminder is accepted; an empty marker does
  not bypass the reminder; a delegated-choice request ("pick any four features") that
  dry-runs and applies ends `applied` with no reminder and no clarification in its context
  message; and explicit `NEEDS_INPUT:`/`BLOCKED:` outcomes terminate normally.
  A successful apply streams one `change_applied` event carrying the result's record and
  no assistant text, and tool rows carry the started and finished titles. Every tool's
  started and finished summaries are pinned in plain words (each data-check outcome, an
  omitted check, withheld parts, a cut listing, a checklist item, a persisted result and
  an unknown tool among them), a malformed argument contributes nothing, no summary holds
  a JSON object or array, a long summary is one line cut to 160 characters, and the
  stream carries both summaries of a call. A successful
  apply does not end the turn: a later call in its round runs, two plans applied in one
  turn stream a card each and complete `applied` listing both ids, the round after an
  apply carries the refreshed context after its tool results (absent when the route
  passes no refresher) and the stored turn holds none of it, a turn whose second plan
  fails ends `blocked` still listing the first change, a spent dry-run budget after a
  save says the earlier changes stay saved, and the budget starts afresh after a saving
  apply. A tool call that replaces the session's build plan streams one
  `build_plan_updated` event with the whole plan after its tool row, a call that leaves it
  unchanged streams none, and the stored turn keeps the plan it left only when it changed
  the plan.
  The turn context follows the user message in every round as the route rendered it, is
  absent when the route sends none, and is never stored; the system prompt takes only the
  source file and holds no policy or pipeline facts, states the delegation rule, and the
  rendered context states the policy and the column-value rule. Every completed turn
  carries and stores its typed outcome: `answered` for a reply with no open dry-run state,
  `needs_input` for a question with or without one, `blocked` from the model or from an
  stopped dry-run budget (its detail being the streamed text after the marker),
  `applied`, `committed_unverified` for an apply whose save committed but failed
  verification, which streams that save's change card, lists it in `changes` and ends the
  turn at once with no later tool or provider round, and
  `incomplete` after the one reminder; a failed turn stores none. The system prompt states the
  steps-first rule with both `new_logic` forms, names every stepped surface by its
  palette name, and no longer teaches `df` as a code-only output variable. A running
  tool's reported progress streams as `tool_progress` events while the call runs, between
  its started and finished rows; a turn closed at a progress event cancels a read tool,
  whose matched result is `tool_interrupted`; and closing at every tool yield, the
  progress yield included, never persists an orphaned call.
- **`tests/test_assistant_routes.py`** — status/sessions/session/message endpoints: SSE framing,
  400/404/409 mapping, sanitized unexpected-error paths, readiness reasons on status,
  transcript rehydration (a stored turn outcome closes its turn as an `outcome` entry,
  a stored apply's change record follows its tool entry as a `change` entry, one saved
  before data checks with its `data_check` key present and null, and tool entries carry
  their finished titles and summaries),
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
  20 ids. Each turn's tool executor is built with the session's own evidence ledger.
  Undo pins the 404 for an unknown change, the 409 while a turn runs and for
  another pipeline, the 422 for an unknown field, the `undo` transcript entry after the
  turn that records it, and the next turn's context naming the undone change. A session
  response carries the session's build plan (`null` for a fresh one); undoing the one
  change of a complete item returns the plan with that item reopened and its change
  marked undone, and persists it; a message turn builds its executor on the session's
  build plan and its turn context lists the plan's open items.
- **`tests/test_assistant_session_persistence.py`** — atomic write-through persistence,
  restart revival, invisible LRU eviction, corrupt/invalid-file logged misses, session-id
  path hardening, oldest-first persisted-file pruning, abandoned temp-file cleanup,
  tool-error round-trip, turn-outcome revival, an outcome detail redacted like assistant
  text, a turn's `undone` records revived, an apply's change record revived as the same record with its summary and
  assumptions redacted like assistant text (a summary that redaction lengthens past the
  dry-run's `ASSISTANT_RECEIPT_TEXT_LIMIT` bound still revives, since the bound is the receipt's, not the
  record's), a revived session's provider history rendering the same turn records as the
  live session's (its change cards, outcome and final text survive persistence, and no
  redacted tool payload reaches the provider), a revived session's evidence ledger
  starting empty, its build plan and each turn's plan revived with their titles redacted
  like assistant text, a file written before build plans reviving with no plan, a turn
  record without its `outcome` key treated as invalid, internal-controller
  revival/transcript hiding, absence of
  deterministic payload digests, safe validation path/reason retention, and non-fatal
  persist failures. Listing coverage pins recency ordering and per-pipeline scoping,
  omission of empty conversations, titles bounded and whitespace-collapsed, survival of a
  restart without reviving any session into memory, and an unreadable file skipped with a
  warning. A syntactically valid file that fails the same id, timestamp, source, or history
  validation used by revival is skipped too; listing never advertises a conversation that
  cannot subsequently be opened. A change card's data check revives whole, and a card saved
  before data checks revives with none.
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
  a degraded ledger capture surfaces its warning in the tool result. In a real Git
  working branch, an apply's commit message is its headline and its document update
  carries the assistant origin; Undo of a change that added a configuration file restores
  the pipeline byte for byte, removes that file and commits `Undo: ` and the headline;
  the same change then dry-runs to the same hash and applies again; and once a later save
  exists the undo is refused as `undo_superseded` with nothing written.
- **`tests/test_save_pipeline_integrity.py`** includes a regression pinning the
  preserve-marker round-trip through
  `save_graph_transactionally` (parse a marker-bearing pipeline → transactional save →
  markers and content survive on disk), independent of which layer supplies the blocks.
- **`tests/test_assistant_data_check.py`** — the data check's engine on candidate graphs seeded under `tmp_path`. In-process, through the worker half under an admitted context: all-default banding, a 0.6 rating miss share, a filter that empties its input, an `m:1` join on duplicate keys reported as `join_validation_failed` in place of its execution failure, a partial join as informational `join_partial`, the remaining finding kinds and their exact-ratio thresholds (a 1-in-10 miss is advisory, 1-in-20 informational); a failing unchecked node read by two checked nodes reported once with both `upstream_failed` naming it while an independent branch measures; a rating miss guard that raises keeping its input and table measurements and reporting an advisory `rating_misses` in place of `execution_failed`; error records for authored code, configuration (input counts kept), validation, a preamble failure and an internal failure; a payload with no row or configuration value; a frame above 1,000,000 rows cut and marked `truncated`; each eligibility reason and its precedence, with an excluded Load File's loader never invoked and a source made stale after its snapshot was built demoted in the worker, which frees its place under the node cap; a registered or uncached run model `artifact_not_local`; a cached model that disappears failing as `ModelNotInDiskCacheError`, never a registry call; a refresh in progress making the nodes that read its input `input_not_prepared`; a preamble-only plan changing no node; no input snapshot, source cache or model cache entry gaining a file; a cached model file or an EBM's cached contract replaced during a check giving `source_changed`, and after one labelling the stored findings, as does a destination moved to another local folder with the old cached files untouched; findings hidden for another graph or scenario and labelled after an input refresh; findings labelled after a snapshot-backed CSV is edited without a refresh while its generation pointer's token is unchanged, the comparison hashing no file, and a check over a source that was gone staying current until the source returns; each worker outcome mapped to its not-run reason; admission refusal and a defect as results; and the size reduction, an oversized check cut step by step within its allocation with the stored check whole, the omission note, nothing, and a reduced `no_checkable_nodes` result that still validates against the closed `not_run` shape. In process mode (spawned workers, marked `slow`): an end-to-end check whose cold snapshot generation is verified, and its parts hashed, only in the worker; an occupied slot and a second session reporting `worker_busy` without waiting; an editor request pre-empting a check, which reports `superseded_by_preview` while the request runs on the replacement worker; a row callback that never returns stopped at a shortened deadline with its worker terminated and the slot usable, a stopped turn reporting `cancelled`, and a newer check in the session superseding an older one; and a slow binding hash (through the test-only preload module `tests/_data_check_worker_hooks.py`), a delayed dispatch and a starting worker each ending at the deadline or as `worker_busy`. The shared extensions are proved where they live: `tests/test_interactive_worker_pool.py` (a pre-emptible request refused at once while unstarted or held, pre-empted by an editor request that then runs on the replacement, refused in the handoff window while the pre-empting request has not resumed, bounded by one deadline through a stuck job and a delayed dispatch, and refused while its slot's replacement starts), `tests/test_graph_walker.py` (the measuring walk's failure provenance, its row bound and its policy's validation) and `tests/test_source_cache.py` (the published-generation probe reads metadata and never a part). Through the real dry-run and apply (in the same module): the dry-run tool's check, in process mode, leaves the plan hash, tier, evidence, warnings and changes identical to a dry-run under a policy that withholds aggregate statistics, carries `next` for its `banding_all_default` beside the check and never in it, is stored whole beside the plan, and a fresh dry-run that attempts no check clears it; in thread mode the check is attempted and reports `worker_mode_unsupported`, and no check is attempted under a policy without the flag or under a `public` ceiling; the check is sized on the fully attributed dry-run result with its `next` (the oversized check's 320 advisory `column_all_null` findings), so with room for exactly `next` and the omission note the result carries both and stays successful, with a little more a reduced check fits beside `next`, with room for `next` alone it carries `next`, and with none the result is the dry-run alone, the stored check whole in every case; `advisory_reminder` counting a check's advisory findings, naming their distinct kinds in order and returning nothing for informational findings only, no findings or a check that did not run; an apply's change record carries its plan's findings worded for the analyst and labelled `current`, `earlier_inputs` after the input changes, and no check for another graph digest, while a card hides another scenario's findings and words why nothing was checked; and, in process mode, a turn stopped while its dry-run's check runs stops the check's worker and keeps the dry-run's result, with a `cancelled` check, as the turn's record of the call. `inspect_node`'s data part (in the same module, in-process through the worker half and `_checked_result`): three nodes below a failing free-code step reported as one `failed` record with its step and one `execution_failed` finding, each of them `upstream_failed` naming it; a followed column's nulls traced along a lineage to the left join that creates them (`first_null_node`, each entry's `input_nulls`) while the join's port lists no unchanged column, and a column no frame carries giving an empty lineage; a lineage of ten nodes keeping the eight nearest the inspected node, its first two `node_cap`, with a failure beyond them reported once `at_or_upstream`; a failure inside a submodel occurrence reported against the occurrence, no runtime id in the result; the part's reduction keeping `column` whole, its omission note and nothing, and the dry-run's view refusing a `column`; and through the executor in thread mode, the part answering `not_run` with `worker_mode_unsupported` beside the project revision, `column` without the part refused as `column_without_data`, the part withheld with `allow_aggregate_statistics = true` while the schema part answers, a stopped turn stopping the part's check, which ran after the save lock was released, with the call returning its `cancelled` result, and, on a copy of the `broken_pricing` fixture with the check run in-process, the schema and data parts on `rating_features` (whose step reads a missing column) answering as a successful call: the data part reports the step's one `failed` record and `execution_failed` finding, and `part_errors` holds the schema part's located, retryable `schema_unresolvable`.
No automated test calls a live Anthropic, OpenAI, or Databricks-compatible endpoint; provider
wire behaviour is exercised with scripted SDK streams. `scripts/run_assistant_self_test.py`
is also the explicit credentialed live runner (tier 1 of [the assistant evaluation](evaluation.md)): its `record` command loads the same project `.env` and
`[assistant]` configuration as the app, selects cases by repeated `--case`, `--area` and `--split`,
runs each selected case once in an isolated fixture, each case in its own spawned process, under the
variant `--variant` names, and lists a case inapplicable to that variant as not applicable without
running it. A case that raises, or whose process ends abruptly, is recorded as that case's crash
(`crashed_result`, carrying the traceback as text) and the run continues with the next case.
Entering and leaving a fixture clears the process-cached active pipeline directory and binds,
then restores, the sandbox project root, so one project or the invoking repository cannot
escape into another fixture's application service. The invoking project supplies the provider,
model, credentials and provider trust (trust describes the endpoint); the egress allowances are
the case's egress profile, never the invoking project's: `project` (`max_sensitivity =
"restricted"` with project knowledge, executable source and aggregate statistics permitted and
row samples withheld) or `metadata_only` (`max_sensitivity = "internal"` with project knowledge,
executable source, row samples and aggregate statistics all withheld). An `external` trust is refused
before any case runs, because external trust is public-only and a public ceiling denies the
project metadata tools every case needs. `record` exits non-zero on any failed case, writes
the redacted report described above, and with `--transcripts` writes local-only transcripts under
the invoking project's Git-ignored `.haute/assistant-eval/`. Cases may expose only synthetic schemas,
the synthetic request, and ordinary assistant tool context to the configured endpoint. After
each turn the harness, not the assistant, executes only the case's golden nodes, each up to that
node, so it never materialises a configured output sink. It is a fast
measurement and regression loop per area, not a qualification gate. The package is covered by the repository's global branch
gate, while exemplar
`.py` assets are omitted from coverage because they are parsed package data rather than
importable modules (they remain parser- and lint-checked).

## Data checks

The [high-level section](high-level.md#data-checks) states the check's
behaviour, bounds, result, findings, binding and eligibility. This section
gives where it lives, what it reuses and extends, its constants and its closed
result shapes; the check keeps no second copy of a rule the product already
owns. One assistant module, `src/haute/assistant/_data_check.py`, owns the
check: eligibility, the job that crosses into the worker, the worker half, the
measurements, the findings and the result model. The dry-run tool calls it
after the plan is stored, and only when the policy permits it. Two shared
extensions, below, serve it: one to the interactive worker pool and one to the
graph walker.

**Constants.** `DATA_CHECK_VERSION = 1`; the row bound is `_MAX_PROFILE_ROWS`
(1,000,000) imported from `src/haute/assistant/_tools.py`, never a second
literal; `DATA_CHECK_DEADLINE_SECONDS = 30.0`, absolute from the check's start;
`DATA_CHECK_MAX_NODES = 8`; `DATA_CHECK_MAX_FINDINGS = 20`;
`DATA_CHECK_MAX_PORTS = 10`; `DATA_CHECK_MAX_COLUMNS = 20` per port;
`DATA_CHECK_MAX_FACTORS = 20`; `DATA_CHECK_MAX_TABLES = 20`;
`DATA_CHECK_MAX_RULE_COUNTS = 100`; `DATA_CHECK_MAX_RULE_POSITIONS = 20`;
`DATA_CHECK_DETAIL_BYTES = 32_000`; `DATA_CHECK_FINDINGS_BYTES = 16_000`;
`NODE_DATA_OMITTED_NOTE` = "The data part's result did not fit in this tool result;
ask for the data part on its own.";
`RATING_MISS_ADVISORY_SHARE = Fraction(1, 10)`; `MOSTLY_DEFAULT_SHARE = Fraction(1, 2)`;
`MOSTLY_NULL_SHARE = Fraction(1, 2)` (0.10, 0.5 and 0.5, held as fractions so no
float rounding enters a comparison). Thresholds compare exact ratios (`missed * 10 >=
rows` for 0.10, `defaulted * 2 >= rows` for 0.5) and shares are rounded to 4
decimal places only when rendered. The admission operation name is
`assistant_data_check`; the session supersession key and the worker affinity key
are both `("assistant_data_check", session_id)`.

**On the server, before admission.** The server reads only configuration, file
metadata and published-generation pointers, never an input's content and never
a snapshot's parts. In order: the worker mode
(`resolve_interactive_execution_mode` in `src/haute/_interactive_workers.py`;
`thread` ends the check as `worker_mode_unsupported`), the plan's tier, then
eligibility. The changed nodes come from the plan's `SemanticDiff` through the
same seed derivation `diff_seed_nodes` uses (one shared helper with a flag for
the preamble widening, not a copy). Eligibility applies the high-level
precedence with: `SINK_ONLY_NODE_TYPES` in `src/haute/_types.py`; a Load File's
`fileType`; a Model Scoring node's locality, decided by the same
`resolve_backend` (configuration only) and disk-cache path helpers
(`_disk_cache_root`, `_artifact_cache_path`, with the EBM contract artifact)
that the fast path of `load_mlflow_model` in `src/haute/_mlflow_io.py` uses, as
a file-existence test only, so the check counts as local exactly what a preview
would load without a tracking or registry call; an Apply Optimisation node's
`sourceType`; the lineage (`source_lineage_graph` in `src/haute/execution.py`,
under the candidate graph's `active_source`); and each snapshot-backed input's
published state from `input_snapshot_read_status`, a shared read-only helper
in `src/haute/routes/input_cache.py` that the input-cache status route
(`_status_for_config`) calls as well, for the identity `source_cache_identity` in
`src/haute/_input_providers.py` derives from configuration.

The helper reads active-build state first, before any access to the store that
could wait or hash: the editor's running input-cache build
(`input_snapshot_build_running`) and this process's preparation single-flight in
`src/haute/_input_preparation.py`, read through a read-only probe of that
single-flight. Either one active makes the input `building` without touching
the store, so `building` is reported while a previous generation is still
readable. Otherwise the helper reads the published generation in one of two
forms. The route keeps the verified form, `SourceCacheStore.status`, because it
shows the generation's metadata. The check uses a metadata-only probe instead,
`SourceCacheStore.published_generation_id` in `src/haute/_source_cache.py`: it
reads the current pointer (`current.json`), validates its generation id and
identity digest, and checks that the generation directory and its `meta.json`
exist, taking no identity lock and opening no part. It never calls
`open_generation`, whose verification compares part sizes, reads every Parquet
footer and content-hashes each part of a generation this process has not yet
verified. An absent pointer or generation is `missing`, an unreadable pointer
`corrupt`, and both make the nodes that read the input `input_not_prepared`; a
published generation passes this stage unverified, whatever its freshness. The
graph digest (`graph_fingerprint` in `src/haute/_cache.py` of
`flatten_graph(result_graph)`, which reads the preamble and the utility module
sources it imports, not data) and the checked scenario are recorded here.

**In the worker, around the walk.** Every read that can hash an input's whole
content, or a snapshot's parts, runs in the killable worker, inside the
deadline. Before the walk the worker opens and verifies each published
generation the lineage reads through `SourceCacheStore.open_generation`, as a
preview's read does, and a generation that fails verification makes the nodes
that read it `input_not_prepared` with the state `corrupt`. It computes each
snapshot-backed input's `source_signature` (in `src/haute/_input_providers.py`)
and from it the freshness the high-level table uses: a node whose lineage reads
a `stale` input becomes `input_not_prepared`, while `fresh`, `unknown` and a
`missing` signature (the original file is gone and the published generation
stays authoritative) remain readable. It then reads the start binding:

- `source_generation`, one digest over `dataframe_graph_input_identity(...).digest`
  (in `src/haute/execution.py`) for the lineage, which already signs local input
  files, snapshot generation pointers, a file-sourced Apply Optimisation artifact
  (`_local_runtime_input_path_fields` lists its `artifact_path`) and the resolved
  MLflow backend of each run-sourced node, and over the identity of each local
  Model Scoring node's cached artifact, computed by the loader's own helpers in
  `src/haute/_mlflow_io.py` (`_local_artifact_fingerprint`, or
  `_ebm_identity_fingerprint` with the cached contract), which that identity
  does not sign;
- `freshness_tokens`, the `observe_freshness` token (in
  `src/haute/_json_shred/_source_proof.py`: a native file revision or a stat,
  never content) of every file the digest signs, cached model files and EBM
  contracts included, keyed by the resolved path. For a snapshot-backed input
  that is the generation pointer and also the original local source file, whose
  content the identity signs through `source_signature`: `runtime_input_signed_paths`
  lists it through `signed_source_file` (`src/haute/_input_providers.py`, the
  path `source_signature` hashes, resolved from configuration with one existence
  check) while it exists, so editing it without refreshing the snapshot changes
  its token, and a source that is gone, which the identity signs as `missing`,
  is listed nowhere, so its return or disappearance changes the path set;
- `identity_components`, the parts of that identity that are not files and cost
  only configuration reads (and the source file's existence check above): the
  resolved path of every signed file (the key set
  of `freshness_tokens`), and for each run-sourced Model Scoring node the
  resolved MLflow backend identity `mlflow_backend_signature` returns (in
  `src/haute/execution.py`: `resolve_backend(destination).identity`, secret-free,
  or its `unresolved` marker), whose digest also selects the node's disk model
  cache directory.

After the walk it reads all three again, and a different `source_generation`
makes the check `source_changed`. A later freshness comparison, such as the
change card's, never hashes a file on the server: it re-derives
`identity_components` from configuration (with that existence check),
re-observes the stored tokens, and
labels the findings as computed from earlier inputs when any component, path set
or token differs or a file is missing. A destination whose configuration moved
to another tracking server or local folder therefore labels the findings even
though the old cached files are untouched, because the backend identity
differs.

**Worker pool extension.** `src/haute/_interactive_workers.py` serves the check
in five ways; every caller shares the first two, and only the check uses the others.

- Scheduling survives worker generations. A replacement installs a new slot,
  so a request that waited on a slot object of its own would submit to the
  retired worker's closed queues; each slot index instead has one scheduling
  lock, created with the pool and kept across replacements. `run` takes its index's scheduling lock, then reads
  that index's current slot under `_state_lock` and checks that it is open
  before submitting. A replacement happens while the job that ended the old
  worker still holds the scheduling lock, so a waiter takes it only once the
  replacement is installed, and submits to the replacement.
- The handoff is reserved. Each index counts its pending interactive requests
  under `_state_lock`: a request that will wait (every caller but a check)
  increments the count before it starts waiting for the scheduling lock and
  decrements it once it holds the lock, or when it stops waiting. A plain lock
  gives no priority, so without the count a check arriving between the
  replacement's release of the lock and the waiting request's wake-up could
  take the lock first; with it, a check's acquisition is refused while the count
  is above zero, even when the lock is momentarily free.
- A non-waiting acquisition, under `_state_lock`, refuses at once when the
  index's pending count is above zero or its scheduling lock is held, and
  otherwise takes the lock without waiting; the check maps a refusal to
  `worker_busy`. The check never starts the pool or a worker: a pool that has
  not started is also `worker_busy`.
- A pre-emptible job registers itself on its index while it runs, and the
  pending count rising above zero marks the registered job pre-empted. The job's
  stop reason reads the mark, so its result wait stops and replaces the worker
  as a timeout does (`_stop_and_replace`: terminate, join, confirm the process
  has exited, start the replacement) before the scheduling lock is released, and
  the check maps the outcome to `superseded_by_preview`. Because the pending
  request keeps the count above zero until it holds the lock, the replacement's
  first job is an interactive request.
- The check's run carries one absolute monotonic deadline rather than a
  duration. It bounds the submission of the request to the worker's queue (a
  put with the remaining time), the result wait and the release acknowledgement,
  so the run ends at the check's deadline whatever stage it is in. Termination
  and the replacement's readiness then take at most the pool's own bounds (two
  2-second joins and the 30-second start timeout): the documented cost of
  keeping the slot usable. Termination is confirmed in `_close_slot`; a worker
  that cannot be confirmed dead raises the pool's termination error, which the
  check reports as `internal_error`.

**Graph walker extension.** `src/haute/_graph_walker.py` has a measuring walk
purpose (`WalkPurpose.MEASURE`) beside `SINK`, `DISPLAY` and `CHUNK`, with its own `CollectPolicy`
constructor, rather than a parallel executor. It builds the lineage as the
display walk does, with `prepare_inputs=False`, no seed plan and no snapshot
capture. At each checked node it collects the node's measurement plan instead
of the node's frame: first one lazy aggregate over each input frame cut with
`head(row_bound)` (row counts, banding claims from `banding_rule_claim_expr` in
`src/haute/_rating.py`, rating key lookups canonicalised by that module's own
key helpers, key matches and duplicate counts with the keys
`build_edge_join_kwargs` in `src/haute/_edge_join.py` resolves), then one over
each output port cut the same way (row and null counts). It keeps no collected
frame for consumers. It records failures with the display walk's
`record_failures` semantics, extended with provenance: a failure marked by
`mark_failing_node` belongs to the marked node; a failure collecting an input
measurement belongs to the producer of that input, with `at_or_upstream`; a
failure collecting the output measurement belongs to the node. A node whose
lineage holds a recorded failure is `upstream_failed` naming it and is neither
built nor collected, and nodes with no failed ancestor continue. The measuring
walk returns one record per checked node, never a frame. Its Model Scoring
builds load a model only from the disk model cache, through a disk-only form of
`load_mlflow_model`'s fast path that raises where the full path would call
`resolve_mlflow_source` and download, so a cache file removed after eligibility
is an execution failure rather than a network call.

**Error records.** `execution_error_record`, beside `_execution_failure` in
`src/haute/assistant/_tools.py`, returns the structured error record rather than
a string, under the same egress rules: the Polars, node-code and preamble cases
it already renders become `authored_code`; `ConfigSettingError` becomes
`configuration` through the existing `_config_setting_message`; every other
`HauteValidationError` subclass becomes `validation`, its text withheld unless
`allow_row_samples` is true; other `HauteError`, `SourceCacheError` and
`PolarsIoConfigError` become `haute` with their own message, as `_error_message`
treats them; anything else becomes `internal`, logged as
`assistant_data_check_failed` with its class and message.

**Outcomes.** `ExecutionAdmissionError` maps to `admission_refused` with its
`reason` as `detail`; the busy error to `worker_busy`;
`InteractiveWorkerTimeoutError` to `deadline`; a worker terminal reason
`memory_limited` to `memory_limited`; a pre-emption to `superseded_by_preview`;
`SupersededRequestError` to `superseded`; a cancelled turn to `cancelled`;
anything else to `internal_error`. No outcome raises out of the dry-run tool.

**Result model.** The dry-run result carries the check under exactly one of
two keys, or under neither: `data_check`, whose value is one of two closed
objects discriminated by `outcome`, or `data_check_omitted`, whose value is the
fixed note "The data check's result did not fit in this tool result; it is
stored with the plan." (see **Size budget**). Beside it, a `checked` check with
at least one advisory finding adds `next`, the string
`advisory_reminder(result)` composes from the stored check: "This plan has
<n> advisory data finding(s) (<kinds>). Correct the plan and dry-run again
before applying, unless the analyst stated the values involved; then apply and
tell the analyst what the check found.", where <n> counts the stored check's
advisory findings ("finding" for one) and <kinds> lists their distinct kinds
in the check's order, comma-separated; it carries no value, node name or count
beyond <n>. The two objects have these fields and no others:

- `checked`: `version` (int), `outcome`, `scenario` (str), `row_bound` (int),
  `elapsed_ms` (int), `nodes` (list of node records, one per changed node in the
  changed nodes' order, less those dropped for size), `nodes_omitted` (int),
  `findings` (list), `findings_omitted` (int), `detail_truncated` (bool).
- `not_run`: `version`, `outcome`, `scenario`, `reason` (one of the high-level
  not-run reasons), `detail` (str, the admission's reason for
  `admission_refused`, otherwise null), `elapsed_ms`, `nodes` (the `not_checked`
  records of every changed node for `no_checkable_nodes`, otherwise empty, less
  those dropped for size), `nodes_omitted` (int).

A node record is discriminated by `status`:

- `checked`: `node`, `status`, `inputs` (list of `{input, rows, truncated}`),
  `outputs` (list of ports), `ports_omitted` (int), `banding` (list of factors,
  or null unless the node is a Banding node), `factors_omitted` (int), `rating`
  (list of tables, or null unless the node is a Rating Step), `tables_omitted`
  (int), `join` (a join record, or null unless the node is an Edge Join).
- `failed`: the `checked` fields without `outputs` and `ports_omitted`, plus
  `error` (an error record); input-side measurements that collected are kept.
- `upstream_failed`: `node`, `status`, `failed_node` (str), `at_or_upstream`
  (bool).
- `not_checked`: `node`, `status`, `reason` (one of the six eligibility reasons),
  `blocking_node` (the offending lineage node for `artifact_in_lineage`,
  `artifact_not_local` and `input_not_prepared`, otherwise null), `remedy` (str
  or null).
- A `checked` or `failed` record whose detail did not fit is reduced to `node`,
  `status` and `detail_omitted: true`.

The parts: a port is `{port (str or null), rows, truncated, columns,
columns_omitted}`; a column is `{name, kind ("new" or "changed"), nulls, share
(null when the port has 0 rows)}`; a factor is `{factor (0-based int),
output_column, status ("measured" or "skipped"), rows, rule_rows (list of int,
null when skipped or over 100 rules), claimed, defaulted, unclaimed_rules,
truncated}`, its counts null when skipped; a table is `{table (0-based int),
output_column, status, rows, missed, entries, unused_entries, truncated}`, its
counts other than `entries` null when skipped; a join is `{how, validate (str or
null), keys ({base, join}, lists of column names), base_rows, join_rows,
matched_base_rows, duplicate_key_tuples ({base, join}), truncated}`, the last
two null for a cross join; an error record is `{class ("authored_code",
"configuration", "validation", "haute" or "internal"), type, step ({id,
number} or null), line (int or null), columns (list), text (str or null),
withheld (the reason the text is withheld, or null)}`. A finding is `{kind,
severity, node, truncated}` plus exactly the fields the high-level table lists
for its kind: `port` (str or null), `factor` and `table` (0-based ints),
`output_column` and `column` (str), counts (int), `share` (float, never null on
a finding because a share finding needs a non-zero denominator), `rules` (list
of int), `rules_omitted` (int), `validate` (str), `side` (`base`, `join` or
`both`), `duplicate_key_tuples` (`{base, join}`), `error` (an error record) and
`at_or_upstream` (bool). The stored result adds `binding`: `{plan_hash,
graph_digest, source_generation, freshness_tokens, identity_components,
scenario}`.

A worked example, for a plan that joins a region table onto quotes, bands the
joined region, and reads the band into a Quote Response:

```json
{
  "version": 1,
  "outcome": "checked",
  "scenario": "live",
  "row_bound": 1000000,
  "elapsed_ms": 412,
  "nodes": [
    {
      "node": "quotes_regions",
      "status": "checked",
      "inputs": [
        {"input": "quotes", "rows": 84210, "truncated": false},
        {"input": "regions", "rows": 12, "truncated": false}
      ],
      "outputs": [
        {
          "port": null,
          "rows": 84210,
          "truncated": false,
          "columns": [{"name": "region_loading", "kind": "new", "nulls": 21052, "share": 0.25}],
          "columns_omitted": 0
        }
      ],
      "ports_omitted": 0,
      "banding": null,
      "factors_omitted": 0,
      "rating": null,
      "tables_omitted": 0,
      "join": {
        "how": "left",
        "validate": "m:1",
        "keys": {"base": ["region"], "join": ["region"]},
        "base_rows": 84210,
        "join_rows": 12,
        "matched_base_rows": 63158,
        "duplicate_key_tuples": {"base": 11, "join": 0},
        "truncated": false
      }
    },
    {
      "node": "region_band",
      "status": "checked",
      "inputs": [{"input": "quotes_regions", "rows": 84210, "truncated": false}],
      "outputs": [
        {
          "port": null,
          "rows": 84210,
          "truncated": false,
          "columns": [{"name": "region_group", "kind": "new", "nulls": 0, "share": 0.0}],
          "columns_omitted": 0
        }
      ],
      "ports_omitted": 0,
      "banding": [
        {
          "factor": 0,
          "output_column": "region_group",
          "status": "measured",
          "rows": 84210,
          "rule_rows": [0, 0, 0],
          "claimed": 0,
          "defaulted": 84210,
          "unclaimed_rules": 3,
          "truncated": false
        }
      ],
      "factors_omitted": 0,
      "rating": null,
      "tables_omitted": 0,
      "join": null
    },
    {
      "node": "premium",
      "status": "not_checked",
      "reason": "sink_only",
      "blocking_node": null,
      "remedy": null
    }
  ],
  "nodes_omitted": 0,
  "findings": [
    {
      "kind": "banding_all_default",
      "severity": "advisory",
      "node": "region_band",
      "truncated": false,
      "factor": 0,
      "output_column": "region_group",
      "rows": 84210
    },
    {
      "kind": "join_partial",
      "severity": "informational",
      "node": "quotes_regions",
      "truncated": false,
      "matched_base_rows": 63158,
      "base_rows": 84210,
      "share": 0.75
    }
  ],
  "findings_omitted": 0,
  "detail_truncated": false
}
```

**Size budget.** Sizes are of compact UTF-8 JSON, and room is measured on the
fully attributed tool result, the value `_bounded_tool_result` measures after
`_attributed_tool_result` adds its fields. The room R is
`_MAX_TOOL_CONTEXT_BYTES` (256,000) less the size of the attributed dry-run
result without the check; the allocation is the smaller of
`DATA_CHECK_DETAIL_BYTES` and R less the bytes the `data_check` key and its
separator add. Starting from the full object, the reduction applies these
steps in order, each repeated until the object fits the allocation before the
next starts; every step removes an element of a finite list, so it always ends:

1. Findings beyond `DATA_CHECK_MAX_FINDINGS`, and then beyond
   `DATA_CHECK_FINDINGS_BYTES`, are dropped from the end of their order into
   `findings_omitted`. This step applies whatever the room.
2. The last `checked` or `failed` record still holding detail is reduced to
   `{node, status, detail_omitted: true}`, and `detail_truncated` becomes true.
3. The last node record of any status, `not_checked` and reduced records
   included, is dropped into `nodes_omitted`. On a `checked` object
   `detail_truncated` also becomes true; a `not_run` object has no such field,
   and its `nodes_omitted` alone records the reduction.
4. The last finding is dropped into `findings_omitted` (only a `checked`
   object has findings).
5. When the object with no nodes and no findings still does not fit, the result
   carries `data_check_omitted` instead of `data_check`, and carries neither
   when even that note does not fit R.

`next` is added before R is measured, so R is what the dry-run result with
`next` leaves, and `next` survives every reduction step above, the omission
included; it is left out only when the attributed dry-run result with it would
exceed `_MAX_TOOL_CONTEXT_BYTES`, and then R is measured without it.

The stored check is never reduced: it is kept whole beside the plan, whatever
reaches the model. The dry-run's own fields are never reduced for the check, and
a dry-run whose own attributed result exceeds the limit is
`tool_result_too_large` exactly as it is without a check.

The schema tier, the plan hash and `build_verified_plan` are unchanged by a
check, and editor callers of the worker pool keep their waiting acquisition. A
defect in the check never fails a dry-run.

**Seams.** The engine is `src/haute/assistant/_data_check.py`:

- `run_data_check(request: DataCheckRequest, *, session_id: str, cancellation:
  ExecutionCancellationToken | None = None) -> DataCheckResult` never raises
  for an outcome: every not-run reason, a defect (`internal_error`) included, is
  a result; only a cancellation of the awaiting task itself propagates, after
  the worker has stopped. `DataCheckRequest` carries `plan_hash`,
  `candidate_graph` (the `VerifiedPlan.result_graph`), `diff`,
  `verification_tier`, `policy` and `submitted` (the operations the model
  sent). The caller decides the egress gate and calls it after the plan is
  stored, outside the save lock; it imports the module inside the dry-run
  function, because the module imports `_MAX_PROFILE_ROWS`, `_json_size` and
  `execution_error_record` from `_tools.py` (as the application service imports
  `data_check_visibility` inside `_card_data_check`).
- Cancelling `cancellation` (the turn stopped) ends the check as `cancelled`;
  one check runs per session under the key `data_check_session_key(session_id)`
  and a newer one ends the older as `superseded` (supersession outranks a
  stopped turn).
- `DataCheckResult.check` is the stored model-facing object, never reduced, and
  `DataCheckResult.binding` its `DataCheckBinding`; `as_dict()` is the stored
  form with `binding`. It belongs beside the stored plan in the plan store.
  `fit_data_check(result, room_bytes)` returns `{"data_check": ...}`,
  `{"data_check_omitted": DATA_CHECK_OMITTED_NOTE}` or `{}`, where
  `room_bytes` is `_MAX_TOOL_CONTEXT_BYTES` less the size of the fully
  attributed dry-run result without the check (with its `next`).
  `advisory_reminder(result)` returns the dry-run's `next`, or `None` when the
  check did not run or holds no advisory finding. `data_check_visibility(result,
  graph)` returns `current`, `earlier_inputs`, `other_scenario` or
  `other_graph` for the graph a consumer shows. `DATA_CHECK_VIEW` (a Pydantic
  `TypeAdapter`) validates the closed shapes. `DataCheckBinding.plan_hash` is
  `None` for an inspection of the saved graph, which no plan stores.
- Shared extensions: `InteractiveWorkerPool.run_preemptible` and
  `run_preemptible_in_interactive_worker` with `InteractiveWorkerBusyError` and
  `InteractiveWorkerPreemptedError` (`_interactive_workers.py`);
  `WalkPurpose.MEASURE`, `CollectPolicy.measuring`, `MeasurementQueries`,
  `NodeMeasurement` and `AttributedFailure` (`_graph_walker.py`);
  `disk_cache_only_model_loads`, `DiskCachedRunModel` and
  `disk_cached_run_model` (`_mlflow_io.py`), raising
  `haute.errors.ModelNotInDiskCacheError`;
  `SourceCacheStore.published_generation_id`; `input_snapshot_read_status`
  (`routes/input_cache.py`, which the status route now calls, so a running build
  or preparation reports `building` without reading the store);
  `input_preparation_running`; `runtime_input_signed_paths` and the public
  `mlflow_backend_signature` (`execution.py`); `signed_source_file`
  (`_input_providers.py`); `freshness_record`
  (`_json_shred/_source_proof.py`); `rating_table_lookup` and
  `apply_rating_table_lookup` (`_rating.py`, which `_apply_rating_table` now
  composes); `diff_seed_nodes(..., preamble_widens=...)`; and
  `execution_error_record` (`_tools.py`).
- Choices the contract left open: a measured frame is aggregated over every
  column (a row count alone lets Polars prune a column and the failure it would
  raise); the server passes every changed node no server-side reason excludes,
  and the worker applies its own `input_not_prepared` before the node cap, so a
  stale input frees a place under the cap; a structured measurement the node's
  configuration refuses keeps the input counts and is the node's failure; a
  finding that replaces an execution failure states its cause by its kind, and
  the `failed` record keeps the error; the
  measuring walk records contract, schema and public contract errors against
  their node rather than raising them as a display walk does; a preamble
  failure fails the nodes that bind the preamble, as a preview does;
  `rows_emptied` reports the largest input's rows; a cross join reports
  `matched_base_rows` and `duplicate_key_tuples` as null; a skipped factor or
  table reports `truncated` as null; the freshness tokens cover the runtime
  input files (a snapshot-backed input's original source file included while it
  exists) and the cached model files, while the preamble's utility modules
  are covered by `graph_digest`; and a worker that demotes every candidate
  returns `not_run` `no_checkable_nodes`.

**The dry-run integration.** The executor passes every dry-run a
`DataCheckGate(policy, session_id)`. `dry_run_graph_edits` runs the
application service's dry-run under the save lock, which stores the plan and
returns the `DryRunResult`, and, after the lock is released and only when
`policy.permits_data_checks`, calls `_checked_dry_run`. That reports the
progress title `DATA_CHECK_PROGRESS_TITLE` ("Checking the data"), builds the
`DataCheckRequest` from the stored plan and its candidate graph, and runs
`run_data_check` as its own task under an `ExecutionCancellationToken` it owns.
When the call is cancelled (the turn stopped), it cancels the token, awaits the
check, which stops its worker and reports `cancelled`, takes back the
cancellation (`Task.uncancel`) and returns the dry-run result with that check,
so the turn records a matched result; a second cancellation while it waits
propagates. The whole result is stored with `PlanStore.record_data_check`, which
keeps it beside a plan still `validated` and refuses (returning `False`, logged
as `assistant_data_check_not_stored`) a plan that has left the store or that
another session's apply has begun, whose apply keeps the check it read;
`PlanStore.put` of a plan already stored and validated clears its check, so each
dry-run leaves only its own. The result then carries `next` when
`advisory_reminder` gives one and the attributed result with it fits the limit,
and then `fit_data_check(result, _MAX_TOOL_CONTEXT_BYTES - _json_size(attributed))`,
where `attributed` is `_attributed_tool_result("dry_run_graph_edits", result)`,
the value `_bounded_tool_result` measures. The operation descriptor lists
`data_check`, `data_check_omitted` and `next` as optional success fields.

**The change card.** `PipelineApplicationService.apply` reads the plan's stored
check (`PlanStore.data_check`) when it begins the apply and, after
`_prepare_apply`, labels it against the apply's candidate graph, the graph the
save writes, with `_card_data_check`: `data_check_visibility`, then
`change_data_check`, on a thread, before the save, so a failure aborts the plan
like any preparation failure. Every reference trajectory's apply was checked to
fingerprint that graph and the reparsed saved graph alike. Both the verified
record and a committed-but-unverified one carry the result as
`AssistantChangeRecord.data_check`: `None` without a check or for
`other_graph`; for `other_scenario`, the outcome and scenario with no findings
and no line; otherwise the outcome, the scenario, at most
`ASSISTANT_MAX_DATA_FINDINGS` (20) findings in the stored order, each
`{severity, node, text}` worded by `finding_words` from its kind's counts,
names and 1-based rule numbers (a share in percent to one decimal place, never
rounded to 0% or 100%; a truncated finding adds "Measured on the first
1,000,000 rows."), `findings_omitted`, and `not_checked`: "Data not checked:
<reason>." for a `not_run` result (a `no_checkable_nodes` result names up to
five nodes with why each was not checked, and the count of the rest), or "Not
checked: <nodes>." for the `not_checked` records of a checked result other than
`sink_only`, or `None`. An execution failure is worded by its exception type and
step or line, never its text. `data_check` defaults to `None`, so a record
persisted before data checks revives with none; `_persisted_change` keeps the
check whole.

**A saved node's check.** `inspect_node`'s data part reuses the engine whole
through a sibling entry point, `run_node_data_check(request: NodeDataCheckRequest,
*, session_id: str, cancellation: ExecutionCancellationToken | None = None) ->
DataCheckResult`, which never raises for an outcome either. `NodeDataCheckRequest`
carries `graph` (the saved graph the call parsed under the save lock), `node`,
`column` (or `None`) and `policy`. Each entry point tests the worker mode first
(`worker_mode_unsupported`), and only the dry-run's then tests the plan's tier;
both then share one core (`_run_check`): `server_exclusions`, the `DataCheckJob`,
the session's supersession key and worker affinity
(`data_check_session_key(session_id)`, so an inspection and a dry-run's check are
one check at a time per session), `_admitted_check` and the outcome mapping. The
nodes come from
`inspection_nodes(graph, flat, node)`: the lineage `source_lineage_graph` gives
under `graph.active_source`, restricted to the ids of the saved graph's top level
(nodes inside an occurrence have runtime ids and are never checked), in the
flattened lineage's canonical topological order. `DataCheckJob` carries
`candidates` in the order records and findings rank (the changed nodes' order
for a dry-run, that topological order for an inspection), `cap_order`, the same
candidates in the order the node cap keeps them (the changed nodes' order for a
dry-run, reverse topological order for an inspection, so the 8 nearest the
inspected node are kept), and `column`. The worker demotes `input_not_prepared`
first, then keeps the first `DATA_CHECK_MAX_NODES` of `cap_order`, and judges the
kept nodes in `candidates` order. `submitted` is empty for an inspection, so an
error record echoes no submitted value, and the binding's `plan_hash` is `None`.

With a `column`, `_DataCheckQueries` adds one aggregate,
`pl.col(column).null_count()` aliased `__column_nulls__`, to the input aggregate
of every measured input frame and the output aggregate of every measured port
whose schema has the column. `_Judgement.column_lineage(column)` reads them back
for each `measured` node in `candidates` order: one entry per output port whose
aggregate has the count, `input_nulls` summing the node's input counts (or `None`
when no input aggregate has one), `truncated` when the port's rows or a counted
input's rows reached the bound, and `first_null_node` the first entry's node whose
nulls exceed `input_nulls or 0`. `WorkerOutcome.column` carries the object, `None`
without a column. A `failed` or `upstream_failed` node has no measured port and
contributes no entry.

The judgement reports every node id through `runtime_instance_id` in
`src/haute/_submodel_instances.py`, the inverse of `qualified_runtime_node_id`
(applied until the id is a top-level one), so a failure attributed to a runtime
node inside an occurrence names the occurrence in `failed_node`, a finding's
`node` and its error record, which is built against the occurrence; two failures
inside one occurrence report once. This holds for a dry-run's check too.

The view an inspection returns is the `checked` object above plus `column`, the
object `{name, first_null_node (str or null), lineage}` or null, whose lineage
entries are `{node, port (str or null), rows, nulls, share (null when rows is 0),
input_nulls (int or null), truncated}`; its `not_run` object is the dry-run's.
`NODE_DATA_VIEW` validates both, and `DATA_CHECK_VIEW` still refuses a `column`
key. `fit_node_data_check(result, room_bytes)` applies the same reduction steps
(one `_fit_view`, which never reduces `column`) and returns `{"data": view}`,
`{"data_omitted": NODE_DATA_OMITTED_NOTE}` or `{}`, where `room_bytes` is
`_MAX_TOOL_CONTEXT_BYTES` less the size of the fully attributed `inspect_node`
result without the part.

In `_tools.py`, `_part_requirement(policy, "data")` gives `max_sensitivity =
"internal"` under `public` and `allow_aggregate_statistics = true` when the flag
is false. Under the save lock `_prepare_node_data` parses the saved graph,
validates the top-level target (a submodel occurrence is refused, as for the
config and profile parts; a refusal is the data part's entry in `part_errors`) and
reads the project revision; after the lock is
released the call runs `run_node_data_check` through `_awaited_check`, which
`_checked_dry_run` uses as well: it reports `DATA_CHECK_PROGRESS_TITLE`, runs the
check as its own task under a token it owns and, when the call is cancelled,
cancels the token, awaits the check (which stops its worker and reports
`cancelled`), takes back the cancellation and returns that result. The part's
answer is then `fit_node_data_check` of the result in the room the attributed
result leaves, sized after `part_errors`, so a failed part's error always reaches the
model. The operation descriptor lists `data`, `data_omitted` and `part_errors` as
optional success fields, and `inspect_node`'s egress class adds
`internal-aggregate-statistics`.

A worked example: the evaluation's `claims_join` fixture joins a claims table
onto ten quotes on `policy_id` (`quote_claims`, a left `m:1` Edge Join) and
divides `total_incurred` by `premium` in `loss_ratio`. Three quotes' policies
have no claims row. `inspect_node` with `{"node": "loss_ratio", "parts":
["data"], "column": "total_incurred"}` answers, under `data` (two node records
shortened here):

```json
{
  "version": 1,
  "outcome": "checked",
  "scenario": "live",
  "row_bound": 1000000,
  "elapsed_ms": 233,
  "nodes": [
    {
      "node": "claims",
      "status": "checked",
      "inputs": [],
      "outputs": [
        {
          "port": null,
          "rows": 7,
          "truncated": false,
          "columns": [
            {"name": "policy_id", "kind": "new", "nulls": 0, "share": 0.0},
            {"name": "total_incurred", "kind": "new", "nulls": 0, "share": 0.0}
          ],
          "columns_omitted": 0
        }
      ],
      "ports_omitted": 0,
      "banding": null,
      "factors_omitted": 0,
      "rating": null,
      "tables_omitted": 0,
      "join": null
    },
    {"node": "quotes", "status": "checked", "...": "as claims, with 10 rows"},
    {
      "node": "quote_claims",
      "status": "checked",
      "inputs": [
        {"input": "quotes", "rows": 10, "truncated": false},
        {"input": "claims", "rows": 7, "truncated": false}
      ],
      "outputs": [
        {"port": null, "rows": 10, "truncated": false, "columns": [], "columns_omitted": 0}
      ],
      "ports_omitted": 0,
      "banding": null,
      "factors_omitted": 0,
      "rating": null,
      "tables_omitted": 0,
      "join": {
        "how": "left",
        "validate": "m:1",
        "keys": {"base": ["policy_id"], "join": ["policy_id"]},
        "base_rows": 10,
        "join_rows": 7,
        "matched_base_rows": 7,
        "duplicate_key_tuples": {"base": 0, "join": 0},
        "truncated": false
      }
    },
    {"node": "loss_ratio", "status": "checked", "...": "loss_ratio new, 3 nulls in 10 rows"}
  ],
  "nodes_omitted": 0,
  "findings": [
    {
      "kind": "join_partial",
      "severity": "informational",
      "node": "quote_claims",
      "truncated": false,
      "matched_base_rows": 7,
      "base_rows": 10,
      "share": 0.7
    }
  ],
  "findings_omitted": 0,
  "detail_truncated": false,
  "column": {
    "name": "total_incurred",
    "first_null_node": "quote_claims",
    "lineage": [
      {"node": "claims", "port": null, "rows": 7, "nulls": 0, "share": 0.0, "input_nulls": null, "truncated": false},
      {"node": "quote_claims", "port": null, "rows": 10, "nulls": 3, "share": 0.3, "input_nulls": 0, "truncated": false},
      {"node": "loss_ratio", "port": null, "rows": 10, "nulls": 3, "share": 0.3, "input_nulls": 3, "truncated": false}
    ]
  }
}
```

The count of `total_incurred` at `quote_claims` is not among its port's
`columns`, which list new and changed columns only: the column arrives unchanged
from `claims`, and only `column` follows it.

**Progress.** `_catalog.report_tool_progress(title)` reads the reporter the
running tool call's context carries (`tool_progress_reporter`, a `ContextVar`)
and does nothing outside a turn. `_loop._RunningTool` starts each tool call as a
task inside `tool_progress_reporter(queue.put_nowait)`, so the call reports
into the turn's queue. The turn waits on the call or its next title, yields each
title as `AssistantToolProgressEvent(id, title)` while the call runs, and
settles the call when it finishes. A `CancelledError` at the wait or a
`GeneratorExit` at the progress yield settles it as an interrupt: a graph apply
already executing completes, and any other call is cancelled and recorded as
`tool_interrupted`, before the turn re-raises the interrupt.

**Latency.** A scratch measurement on 2026-10-01 (Windows 11 build 26200, AMD
Ryzen family 25 model 33 with 32 logical processors, 128 GiB RAM, Python
3.11.13, process mode with one interactive worker and the required native memory
cap) timed `run_data_check` over a synthetic quotes Parquet and a six-row
region table: a stepped Polars node (source and free code), a three-band
breakpoint banding, a rating step on the band and an `m:1` left Edge Join, all
four nodes checked, three checks per size. The median check took 171 ms on
100,000 rows, 375 ms on 1,000,000 rows and 375 ms on 5,000,000 rows (each
measured frame cut at the 1,000,000-row bound, which Polars pushes down to the
scan); the first check of the run, which pays the worker's first imports, took
563 ms.
