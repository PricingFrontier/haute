# Backend Execution Optimisation Plan

Status: working plan
Owner: backend execution workstream
Last updated: 2026-05-10

## Goal

Make the whole backend consistently fast, memory-safe, observable, and cancellable across previews, optimiser solves, auto-range, model training, sinks, and deployed scoring.

This is not an optimiser-only tuning exercise. The target architecture is a shared execution philosophy:

- Build explicit execution plans before expensive work starts.
- Project columns as early and generally as possible.
- Prefer streaming and chunked execution over materialising expanded datasets.
- Check cancellation and memory budgets at every expensive boundary.
- Record stage timings and RSS samples so slow or memory-heavy steps are visible.
- Fail loudly when contracts are wrong instead of falling back to broad, hidden behaviour.
- Treat the engine as a small internal execution boundary: stable import surface, no private-helper imports from routes/deploy, and explicit bundle-version bumps when deployed runtime behaviour changes incompatibly.

## Review Verdict

The first draft set the right *direction* but assumed the current code already honoured it. A multi-agent code review found that several "no silent fallback" claims do not match what the code does today, that `ExecutionContext.memory_limit_bytes` is dead in production (no route sets it), that the projection logic is scattered across at least eight sites with route-side imports of private engine helpers, that training is fully uncancellable, and that the lifecycle/error taxonomy collapses superseded/timeout/memory-limit/contract-error into generic `error` on every path except auto-range.

This revision treats those gaps as first-class deliverables. Some of the wider items below are deliberately long-term hardening, not prerequisites for the next fixes. The near-term track stays focused on the changes that directly improve execution correctness, memory safety, preview latency, and maintainability.

## Pragmatic Priority Filter

### Definitely Right Direction

These are the immediate execution track:

- Keep the honest `Current Gaps` baseline and remove gaps slice by slice.
- Build Slice 0 guardrails and the shared conformance harness before deeper refactors.
- Split bounded execution from best-effort execution so `safe_sink`-style broad collects cannot hide inside memory-safe paths.
- Extract projection planning behind a shared planner facade.
- Unify job lifecycle/status semantics across auto-range, solve, training, preview, sink, and deploy.
- Redesign preview so first-click work is bounded and explainable.
- Add scale gates for 10m-row-class workloads with deterministic counters, not only wall-clock timing.

### Scope Carefully

These are useful, but v1 should be modest:

- Streaming plan inspection should live behind one helper and should not depend on brittle string matching beyond controlled tests.
- Memory admission should start with per-execution RSS-growth budgets and fail-loud behaviour before introducing queueing. Absolute process RSS caps are opt-in because the GUI server is a long-lived, cache-warmed process.
- Checkpoint policy should first cover projected parquet checkpoints, atomic writes, cleanup, and disk budget; resumability can wait.
- The I/O adaptor should first enforce bounded-profile rules for JSON/CSV/parquet and projection pushdown, then grow source coverage.
- Training cancellation should be honest about native-library boundaries; CatBoost/GLM fitting may be cancellable only between named stages.

### Deferred Hardening

These should not block the next execution slices:

- OTel export and OTel integration tests.
- Formal two-minor-version deprecation policy for every new engine type.
- `ContextVar` cancellation inside arbitrary user UDFs/preambles.
- Hypothesis graph fuzzing.
- NDJSON streaming responses for deployed batch scoring.
- Full dtype/nullability contracts on every node. Start with source schemas, join key dtype checks, and train/score parity.

## Implementation Status

### Slice 0 And Slice 1 Status

Completed in the current workstream:

- Route/deploy hygiene guardrails now prevent new private execution/planning imports from leaking into API layers.
- `ExecutionContext` is profile-aware and threaded through preview, sink, optimiser setup, auto-range, training prep, and deploy scoring.
- Public status/response models expose typed `ExecutionMetricsPayload` rather than loose debug dictionaries.
- Preview supersession and background-job cancellation now share cooperative cancellation tokens.

Evidence:

- `tests/test_execution_context.py`
- `tests/test_request_supersession.py`
- `tests/test_pipeline_route_supersession.py`
- `tests/test_routes_hygiene.py`
- schema, API, UI-contract, deploy, optimiser, sink, and server contract tests listed in the slice handoff.

### Slice 5 V1 Status

Completed in this next-stage slice:

- Added `haute._execution_admission` as the single v1 admission seam for per-profile memory budgets.
- Preview and sink routes now create admitted contexts with profile-specific memory budgets from environment settings.
- Preview responses now include `execution_metrics`, including the admitted growth budget, RSS-at-admission baseline, effective RSS ceiling, headroom, and config key.
- Admission failures and runtime memory-budget failures on preview/sink now report HTTP 507 with typed payloads instead of generic 500s.
- Default admission is baseline-aware: a cache-warmed GUI process above the preview growth budget is still admitted, and runtime checks enforce `rss_at_admission + memory_limit_bytes`. Opt-in `*_PROCESS_RSS_LIMIT_*` settings provide absolute process caps when required.
- Optimiser solve setup, synchronous/background auto-range, training prep, pyfunc deploy scoring, generated container scoring, and direct deploy `score_graph()` now create admitted contexts with their profile-specific budgets. Optimiser setup now records explicit validation/projection, factor-extraction, and grid-build stages before launching the background solver.
- Generated container scoring and MLflow pyfunc scoring now admit from request/dataframe row count before building the extra Polars materialisation (`pl.DataFrame(rows)` / `pl.from_pandas(...)`), so rejected deploy batches fail before doubling the payload in memory.
- Background auto-range now admits the new execution before registering it as latest, so a request rejected by memory admission cannot supersede an already-running auto-range job.
- Background auto-range runtime memory failures now poll as structured typed status payloads (`error_code`, `http_status_code`, `error_detail`) instead of a stringified error blob.
- Deploy scoring chooses `deploy_live` for single-row scoring and `deploy_batch` for multi-row payloads, so batch requests use the batch budget.
- Runtime preview memory-limit and cancellation signals bypass per-node error swallowing so they reach the route/job layer.
- Timed-out preview/sink requests cancel their execution contexts, and same-key timed-out previews remain active in supersession until their background thread finishes.
- Windows RSS sampling now uses explicit `psapi.dll` bindings so admission works on the primary desktop development platform.
- Preview cache pins are released on exception paths, including preview projection failures.

Evidence:

- `test_admitted_execution_context_uses_profile_specific_memory_limit`
- `test_admitted_execution_context_rejects_when_current_rss_exceeds_limit`
- `test_execution_metrics_payload_includes_admission_metadata`
- `test_preview_route_creates_admitted_preview_execution_context`
- `test_preview_route_maps_admission_failure_to_http_507`
- `test_admitted_execution_context_allows_warm_process_above_operation_budget`
- `test_preview_route_admits_when_warm_process_rss_exceeds_operation_budget`
- `test_sink_route_maps_admission_failure_to_http_507`
- `test_optimiser_start_creates_admitted_setup_context`
- `test_optimiser_start_records_setup_stage_metrics_when_memory_limited`
- `test_optimiser_start_preserves_typed_memory_http_exception_metrics`
- `test_optimiser_extract_factors_checks_memory_budget`
- `test_optimiser_build_grid_preserves_memory_limit_error`
- `test_optimiser_auto_range_entry_points_create_admitted_contexts`
- `test_auto_range_admission_failure_does_not_supersede_running_job`
- `test_auto_range_background_memory_limit_status_exposes_typed_error`
- `test_auto_range_background_preserves_typed_memory_http_exception_status`
- `test_training_start_creates_admitted_training_context`
- `test_deploy_score_graph_creates_admitted_context_when_omitted`
- `TestHauteModelPredict.test_predict_pandas_round_trip`
- `TestGenerateAppSource.test_quote_admits_before_polars_dataframe_materialisation`
- `TestHauteModelPredict.test_predict_admits_before_polars_conversion`
- `test_eager_graph_execution_does_not_swallow_memory_budget_failures`
- `test_eager_graph_execution_does_not_swallow_cancellation`
- `test_timed_out_same_key_preview_stays_active_until_worker_finishes`
- `test_preview_cache_unpins_entry_when_preview_projection_fails`
- `tests/test_pipeline_route_supersession.py` remains green after admission wiring.

Deferred from Slice 5 V1:

- Queueing/admission scheduling. V1 refuses clearly; it does not queue.
- Broader queue/admission policy remains deferred. Full lifecycle taxonomy for long-running solve/training/auto-range has since moved to Slice 7.

### Slice 10 Request Boundary Status

Completed in the current deploy hardening slice:

- Added `haute.deploy._request_limits` as the shared request-body guard for generated deploy scoring apps.
- Generated container `/quote` now resolves a fail-loud request body limit at startup (`HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES`, or `HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_MB`).
- Oversized `Content-Length` is rejected before stream consumption or JSON parsing with HTTP 413 and a typed `request_body_too_large` payload.
- Requests without `Content-Length` are read through a streaming byte counter and rejected as soon as the configured limit is crossed.
- Malformed `Content-Length` fails with a typed request-header payload instead of being ignored.
- JSON parsing now happens from bounded bytes via the shared helper rather than `request.json()`.

Evidence:

- `TestGenerateAppSource.test_quote_rejects_oversized_content_length_before_json_materialisation`
- `TestGenerateAppSource.test_limited_json_reader_rejects_oversized_content_length_before_stream`
- `TestGenerateAppSource.test_limited_json_reader_rejects_stream_without_content_length_at_limit`
- `TestGenerateAppSource.test_limited_json_reader_rejects_invalid_content_length`
- `TestGenerateAppSource.test_request_body_limit_config_bytes_wins_over_mb`
- `TestGenerateAppSource.test_request_body_limit_config_invalid_values_fail_loudly`

### Slice 10 Deploy Collect Status

Completed in the current deploy collect slice:

- Added `haute._polars_utils.streaming_collect(..., profile=..., allow_broad=False)` as the shared profiled Polars streaming collect helper.
- Deployed scoring final collect now uses `streaming_collect(output_lf, profile=execution_context.profile)` inside the existing `deploy_collect` execution stage.
- Deploy collect metrics and memory/cancellation propagation remain owned by `ExecutionContext`; no silent fallback or eager broadening was introduced.

Evidence:

- `test_streaming_collect_uses_polars_streaming_engine`
- `test_deploy_score_graph_final_collect_uses_streaming_engine`
- `test_deploy_score_graph_final_collect_preserves_execution_context_memory_error`

### Slice 10 Deploy modelScore Batch Status

Completed in the current deploy model-scoring slice:

- Bundled/remapped deploy `modelScore` nodes now choose their scoring mode from the admitted execution profile.
- Configured non-bundled deploy `modelScore` nodes now use the same deploy-profile scoring decision instead of inheriting graph-level `source="live"`.
- `deploy_live` keeps `source="live"` and therefore uses the eager live scorer for single-row requests.
- `deploy_batch` passes `source="deploy_batch"` into the shared model scorer, which routes through the existing parquet-batched scoring path instead of eager full-frame scoring.
- Lazy execution now supports a per-node builder source override, so deploy can keep graph/source-switch routing on `live` while only modelScore nodes receive `deploy_batch`.
- Deploy graph execution still enters lazy execution with `source="live"` through `execute_lazy_graph(...)`, so source-switch and API-input routing semantics are unchanged.
- Deploy modelScore hooks now receive the same `required_output_columns` projection demand as the standard builder, so `output_fields` and downstream projection can shrink batch scoring writes.
- Batch modelScore temp outputs are tracked through a scoped scorer temp-file context and cleaned at the end of each deploy request, including paths created by the standard `ModelScorer` builder. Failure paths remove both sunk input parquet and partial scored output parquet.
- Model-score temp input sinks now use a strict streaming sink rather than the best-effort `safe_sink` fallback, so deploy batch scoring does not silently broaden to an eager collect when Polars rejects a streaming sink plan.
- Remapped deploy modelScore nodes forward secondary parent frames into `_run_score_pipeline`, preserving existing multi-parent user-code semantics.
- Rating-table joins now explicitly preserve left/input row order under Polars streaming joins, and the Polars dependency floor reflects that runtime API contract.
- Generated MLflow deploy requirements now advertise the same Polars floor as the project runtime.

Evidence:

- `TestScoreGraphModelScoreRemap.test_multi_row_model_score_uses_deploy_batch_source`
- `TestScoreGraphModelScoreRemap.test_single_row_model_score_keeps_live_source`
- `TestScoreGraphModelScoreRemap.test_model_score_remap_forwards_required_output_columns`
- `TestScoreGraphModelScoreRemap.test_multi_row_unbundled_model_score_uses_deploy_batch_source`
- `TestScoreGraphModelScoreRemap.test_deploy_batch_model_score_cleans_scored_temp_after_collect`
- `TestScoreGraphModelScoreRemap.test_model_score_remap_forwards_secondary_inputs`
- `test_deploy_batch_graph_routing_stays_live_for_source_switch`
- `test_deploy_score_graph_forwards_execution_context_to_lazy_executor`
- `TestRunScorePipeline.test_non_live_routes_to_batched`
- `TestRegisterTempCleanup.test_active_temp_file_scope_tracks_batch_output`
- `TestSinkToTemp.test_streaming_sink_failure_propagates_without_collect_fallback`
- `TestSinkToTemp.test_temp_path_removed_when_streaming_sink_raises`
- `TestRunScorePipeline.test_batched_failure_removes_input_temp_file`
- `TestBatchScoreToParquet.test_predict_failure_removes_partial_output_parquet`
- `TestBatchScoreToParquet.test_unreadable_input_removes_output_temp_parquet`
- `TestRegisterTempCleanup.test_cleanup_registered_temp_files_unlinks_and_unregisters`
- `TestDeployModelScoreTempCleanup.test_cleanup_preserves_primary_error_when_unlink_fails`
- `TestApplyRatingTable.test_streaming_lookup_preserves_left_input_row_order`
- `test_polars_floor_supports_order_preserving_lazy_joins`
- `TestCondaEnvAndPipRequirements.test_pip_requirements_includes_haute_and_polars`

Remaining boundary:

- The batch model-scoring step is routed through the existing batched scorer and NDJSON responses can stream from the lazy deploy plan. Very large JSON request bodies are still intentionally bounded by request admission rather than treated as streamed source artifacts.

### Slice 2 Bounded Sink Status

Completed in the current bounded sink slice:

- Added `BoundedMemoryUnsupportedError` as the typed error for a plan that cannot be written in bounded streaming mode.
- Split the sink API into `bounded_sink(...)` for strict bounded paths and `best_effort_sink(..., allow_broad=True)` for the deliberately broad compatibility path.
- Kept `safe_sink(...)` as a compatibility alias for older generated/user code, but removed it from bounded production callers.
- Routed lazy checkpoints, explicit pipeline sinks, generated data-sink code, training prep/split writes, optimiser grid staging, and model-score temp input writes through `bounded_sink`.
- Mapped bounded streaming incompatibilities to clear `422` responses in sink, training prep, and optimiser grid/setup paths instead of generic server failures.

Evidence:

- `test_bounded_sink_raises_typed_error_without_collect_fallback`
- `test_best_effort_sink_requires_explicit_broadening`
- `test_best_effort_sink_parquet_fallback_on_error`
- `test_best_effort_sink_csv_fallback_on_error`
- `test_bounded_memory_callers_do_not_use_fallback_sink`
- `test_sink_route_maps_bounded_streaming_failure_to_http_422`
- `test_execute_and_sink_maps_bounded_sink_failure_to_http_422`
- `test_build_grid_bounded_sink_failure_is_http_422`
- `TestSinkToTemp.test_streaming_sink_failure_propagates_without_collect_fallback`

Remaining boundary:

- Projection-impossible diagnostics are now covered for strict/projection-seeded bounded paths; broader planner extraction is closed in Slice 3.

### Slice 2 Profiled Collect Status

Completed in the current profiled collect slice:

- `streaming_collect(...)` now validates the execution profile, rejects broad fallback for bounded profiles before collecting, preserves non-streaming data errors, and raises `BoundedMemoryUnsupportedError` when Polars rejects a bounded streaming collect.
- Preview eager execution is the only profile allowed to opt into broad collect fallback, and that opt-in is explicit at the call site.
- Eager preview execution, deploy final collection, optimiser solve setup, optimiser estimate metrics, and online auto-range chunk aggregation now route direct `collect(engine="streaming")` work through the helper instead of bare Polars calls.
- Optimiser setup maps bounded streaming collect incompatibilities to `422` instead of a generic setup failure.
- Background auto-range bounded-collect failures now preserve pollable `http_status_code=422` and `error_detail` metadata for both lazy and streaming auto-range paths.

Evidence:

- `test_streaming_collect_uses_polars_streaming_engine`
- `test_streaming_collect_raises_typed_error_without_broad_fallback`
- `test_streaming_collect_preserves_non_streaming_data_errors`
- `test_streaming_collect_preserves_generic_unsupported_data_errors`
- `test_streaming_collect_rejects_broad_fallback_for_bounded_profile`
- `test_streaming_collect_rejects_unknown_profile`
- `test_streaming_collect_explicit_broad_fallback`
- `test_bounded_callers_route_streaming_collect_through_helper`
- `test_solve_maps_bounded_streaming_collect_failure_to_422`
- `test_estimate_maps_bounded_streaming_collect_failure_to_422`
- `test_frontier_auto_range_lazy_maps_bounded_collect_failure_to_422`
- `test_frontier_auto_range_streaming_maps_bounded_collect_failure_to_422`

Remaining boundary:

- Intentional broad compatibility paths still exist behind `safe_sink(...)` / `best_effort_sink(..., allow_broad=True)` for legacy generated/user code.
- Route/API schema materialisation boundaries now use the profiled helper. Remaining direct materialisation work is the named deploy request-body contract and any explicitly best-effort legacy compatibility path.

### Slice 2 Projection Diagnostics Status

Completed in the current projection diagnostics slice:

- Added `ProjectionImpossibleError` as a typed diagnostic for graphs that can run but cannot prove a safe bounded projection.
- Strict projection diagnostics are profile-gated to bounded/projection-seeded execution contexts (`lazy_sink`, `training_prep`, `optimiser_setup`, `auto_range`, `deploy_batch`, and `chunked_map_reduce`). Preview and profileless compatibility planning keep their current permissive broadening behaviour.
- Multi-parent `POLARS` fan-in without `inputs_by_parent` now fails loudly in strict mode instead of silently broadening every parent.
- Projection seeds that conflict with an opaque sibling consumer now fail loudly in strict mode instead of being ignored.
- Fan-in join suffix inference now reports unparseable join code when that inference is needed to route parent columns.
- Optimiser pipeline setup and estimate routes map projection-impossible failures to user-visible `422` responses instead of generic server errors.
- Auto-range streaming preflight catches only `ProjectionImpossibleError` and marks the streaming optimisation ineligible; the general `auto_range` execution path still reruns strict projection and returns the real `422` diagnostic.
- Background auto-range projection failures now surface pollable `http_status_code=422` and `error_detail` metadata, so the GUI does not have to parse a generic error string.

Evidence:

- `test_strict_projection_rejects_opaque_fan_in_without_parent_contract`
- `test_non_strict_projection_allows_opaque_fan_in_for_compatibility`
- `test_strict_projection_rejects_seed_that_conflicts_with_opaque_sibling`
- `test_strict_projection_reports_unparseable_join_inference`
- `test_strict_projection_allows_concrete_inputs_by_parent_join`
- `test_lazy_execution_required_seed_enables_strict_projection`
- `test_execute_pipeline_maps_projection_impossible_to_422`
- `test_estimate_maps_projection_impossible_to_422`
- `test_frontier_auto_range_prepare_treats_projection_plan_failure_as_ineligible`
- `test_frontier_auto_range_prepare_does_not_hide_contract_errors`
- `test_frontier_auto_range_maps_projection_impossible_after_preflight_to_422`
- `test_frontier_auto_range_start_records_projection_impossible_http_status`

Remaining boundary:

- Single-parent opaque transforms still broaden by design; rejecting them would be a larger planner-contract change and would break existing compatibility behaviour.
- Slice 2 is closed out; remaining projection architecture work is richer optional explainability, not a known memory-safety bug.

### Slice 3 Shared Planner Facade Status

Completed in the first Slice 3 sub-slice:

- Added `haute.projection` as the shared projection planning import surface.
- Added `ProjectionRequest`, `ProjectionPlan`, `ProjectionDiagnostics`, `plan(...)`, and `strict_projection_required(...)`.
- The shared facade prepares the target graph, applies profile-driven strictness, owns the reverse topological projection sweep directly, and returns immutable `frozenset` column demands.
- Auto-range streaming preflight now calls the shared projection planner instead of importing `_compute_projection_plan` from `_execute_lazy`.
- Route/deploy hygiene now forbids new private projection imports from `haute.projection` and removed the `_compute_projection_plan` route allowlist.
- `_execute_lazy` now uses the shared strictness policy so bounded profile semantics have one source.
- `_execute_lazy._compute_projection_plan(...)` is now only a compatibility wrapper around `haute.projection.compute_prepared_plan(...)`; routes and new callers do not need executor-private planning APIs.
- Optimiser/ratebook parent-demand routing now lives behind `haute.projection.parent_demands_for_node(...)` instead of executor-local optimiser helpers.
- Declared fan-in ownership, Polars join-suffix inference, passthrough-parent routing, declared-contract overlay, and projection-contract lookup now live in `haute.projection`.
- Concrete multi-parent `POLARS` fan-in routing now lives behind `haute.projection.fan_in_demands_for_node(...)`; the executor reverse sweep now orchestrates planner results instead of owning that rule body.
- Opaque-contract fan-in diagnostics now live behind `haute.projection.opaque_contract_demands_for_node(...)`, preserving strict `ProjectionImpossibleError` behaviour while removing another executor-local branch.
- ModelScore builder projection policy now lives in `haute.projection`: eager preview preserves modelScore passthrough schema, while lazy/batch builders receive shrinkable output demands only from explicit downstream planner demand. Config-level `selected_columns` stays optional and is applied after scoring, so stale UI selections do not become hard scorer input requirements.
- ModelScore generated/no-op code is stripped before contract and builder projection decisions, so harmless UI scaffolding does not unnecessarily make scorer projection opaque.
- Projection plans now carry node/edge reason metadata, and `haute.projection.explain(...)` provides a compact provenance view for filtered node/column diagnostics.
- Executor graph preparation now delegates to `haute.projection.prepare_graph(...)`, so live-switch pruning, target ancestor filtering, topological order, and node-label sanitisation have one implementation.
- Optimiser parent routing, opaque contract routing, and Polars fan-in routing are now named planner rule objects. Diagnostics record stable rule names via `ProjectionReason`, not anonymous strings.
- Projection rule coverage now lives in `haute.projection` via an immutable registry. Import-time validation fails loudly if any `NodeType` lacks coverage, and submodel boundaries are explicitly marked opaque instead of being implicitly unhandled.
- Lazy execution access for routes/deploy now goes through `haute.execution`, the internal facade for `execute_lazy_graph(...)`, source-switch pruning, and linear chain function building. The route/deploy private-helper allowlist is empty.

Evidence:

- `test_public_projection_plan_matches_private_projection_engine`
- `test_public_projection_plan_routes_fan_in_demands_by_parent`
- `test_public_projection_plan_strict_profile_rejects_unsafe_fan_in`
- `test_public_projection_plan_preview_profile_preserves_compatibility`
- `test_public_projection_plan_routes_ratebook_data_and_banding_inputs`
- `test_public_projection_plan_does_not_delegate_to_executor_private_planner`
- `test_builder_demands_keep_eager_model_score_schema_expanded`
- `test_model_score_required_output_columns_uses_explicit_downstream_demand_only`
- `test_projection_explain_reports_node_and_edge_reasons`
- `test_projection_diagnostics_records_named_rule_reasons`
- `test_projection_coverage_map_mentions_every_node_type`
- `test_projection_rule_coverage_is_immutable`
- `test_projection_rule_coverage_declares_opaque_node_types_explicitly`
- `TestNoNewPrivateEngineImports.test_no_new_private_execution_helper_imports_in_routes_or_deploy`

Remaining Slice 3 work:

- No known V1 Slice 3 implementation gaps remain. Future work is richer per-column provenance and rule metadata only if the UI needs deeper explainability.

### Slice 7 Lifecycle Status

Completed in the current lifecycle slice:

- Added `haute.routes._job_lifecycle.JobLifecycle` as the shared terminal-state writer for long-running jobs.
- Solve, training, and auto-range now expose typed terminal states (`cancelled`, `superseded`, `timed_out`, `memory_limited`, `contract_error`) instead of collapsing everything to generic `error`.
- Training and optimiser solve now have cooperative cancellation endpoints and shared cancellation reason propagation.
- Long-running job responses expose `terminal_reason`; frontend polling treats every typed terminal failure as a completed failed job.
- Optimiser solve completion persists apply results and ratebook factors as parquet artifact handles and releases the in-memory Polars frames from job state.
- Training loss history retained in job/status payloads is capped and marked when truncated.

Evidence:

- `tests/test_job_lifecycle.py`
- `tests/test_modelling_routes.py`
- `tests/test_optimiser_routes.py`
- `tests/test_execution_context.py`
- `frontend/src/__tests__/hooks/useBackgroundJobs.test.ts`

Remaining boundary:

- Some non-long-running route status writes still exist for request-local operations; Slice 13 should add a broader guardrail once all route families are migrated.

### Slice 8 V1 Source Boundary Status

Completed in this first Slice 8 sub-slice:

- Added a shared data-source adaptor boundary in `haute._io` for configured `dataSource` nodes.
- `read_source(...)` is now profile-aware, projection-aware, and schema-override-aware while preserving the old compatibility default.
- Plain `.json` sources fail before eager `pl.read_json(...)` under bounded profiles (`lazy_sink`, `training_prep`, `optimiser_setup`, `auto_range`, `deploy_batch`, `chunked_map_reduce`).
- Parquet, CSV, and NDJSON source projections now preserve source schema order and fail loudly when requested columns are missing.
- CSV/NDJSON schema overrides are parsed centrally, including string dtype declarations from config.
- CSV/NDJSON schema declarations now validate declared-column presence and surface `SchemaMismatchError` rather than silently ignoring missing declarations or leaking raw Polars schema errors.
- Parquet and plain JSON schema declarations are validated against the actual source schema without replacing the full scan schema.
- Runtime API/data source builders and chunk-runner root scans now use the shared source adaptor instead of duplicating extension dispatch.
- Source projection is deliberately not pushed into preview/default source reads or data-source user-code bodies, so preview error behaviour and user-code input columns are preserved.
- Direct `execute_sink(...)` calls now create a default `lazy_sink` execution context, so bounded source rules apply even outside the HTTP route layer.
- Direct sink post-write row counting now runs inside an execution-context stage and uses the shared streaming collect helper.

Evidence:

- `TestReadSourceProjectionAndSchema`
- `TestReadSourceJSON.test_bounded_profile_rejects_plain_json_before_eager_read`
- `TestReadSourceProjectionAndSchema.test_parquet_schema_declarations_validate_without_replacing_schema`
- `TestReadSourceProjectionAndSchema.test_json_schema_declaration_mismatch_fails_loudly`
- `TestReadSourceProjectionAndSchema.test_csv_schema_declaration_missing_column_fails_loudly`
- `TestReadSourceProjectionAndSchema.test_ndjson_schema_declaration_missing_column_fails_loudly`
- `TestDataSourceAdapterFlatFile.test_read_data_source_forwards_projection_and_schema`
- `TestBuildNodeFn.test_data_source_builder_pushes_projection_when_no_user_code`
- `TestBuildNodeFn.test_data_source_builder_does_not_pre_project_user_code_inputs`
- `TestBuildNodeFn.test_data_source_builder_does_not_pre_project_before_source_renames`
- `TestBuildNodeFn.test_data_source_builder_rejects_empty_path_in_bounded_profile`
- `TestExecuteSink.test_plain_json_source_rejected_by_default_bounded_sink_context`
- `TestExecuteSink.test_writes_csv`
- `TestBuildApiInput.test_json_cache_projection_missing_required_column_fails_loudly`
- `tests/test_chunk_plan.py`
- `tests/test_chunk_runner.py`

### Slice 8 V2 Generated And Deploy Source Boundary Status

Completed in this sub-slice:

- Generated `dataSource` code now imports and calls `read_data_source(...)` instead of duplicating file-extension and Databricks dispatch in codegen templates.
- Generated source loader boilerplate is stripped during parser/code extraction, so round-tripped user code does not accumulate adapter calls.
- Deploy static-source bundle validation now reads schemas through `read_data_source(..., profile=ExecutionProfile.DEPLOY_BATCH)`, enforcing declared schema keys and bounded plain-JSON rules at bundle time.
- Deploy scoring static-source artifact remaps now use `read_data_source(...)`, so bundled lookup/source files preserve declared dtypes and adapter validation at runtime.
- Data-source schema/deploy keys are first-class config-validation keys (`expected_columns`, `schema_overrides`, `dtypes`, `column_dtypes`, `schema`) and Databricks `catalog` is recognised where config surfaces carry it.

Evidence:

- `TestCodegenExecValidation.test_data_source_exec_uses_declared_schema_boundary`
- `TestDataSourceJsonCodegen.test_codegen_uses_shared_source_boundary`
- `TestExtractSourceUserCode.test_generated_read_data_source_load_is_not_user_code`
- `TestStaticDataSourceSchemaDrift.test_static_source_schema_declaration_mismatch_raises`
- `TestStaticDataSourceSchemaDrift.test_static_plain_json_source_rejected_by_bounded_deploy_validation`
- `TestScoreGraphStaticDataSourceRemap.test_static_data_source_remap_uses_declared_schema`
- `TestValidKeysRegistry.test_data_source_boundary_keys_present`
- Targeted regression runs:
  - `uv run pytest tests/test_codegen.py tests/test_codegen_builders.py tests/test_parser_helpers.py::TestExtractSourceUserCode -q`
  - `uv run pytest tests/test_deploy_contract_integrity.py::TestStaticDataSourceSchemaDrift tests/test_deploy_config_and_bundle.py::TestBundledPathsAreAbsolute tests/test_deploy_internals.py::TestScoreGraphStaticDataSourceRemap -q`
  - `uv run pytest tests/test_deploy_internals.py::TestInferInputSchema tests/test_deploy_internals.py::TestInferOutputSchema tests/test_deploy_internals.py::TestScoreGraphModelScoreRemap -q`
  - `uv run pytest tests/test_io.py tests/test_config_validation.py -q`
  - `uv run ruff check ...`
  - `uv run mypy ...`

### Slice 8 V3 Bounded CSV Dtype Status

Completed in this sub-slice:

- Bounded profiles now reject CSV reads without declared dtypes before calling `pl.scan_csv(...)`.
- Default reads, preview eager, and deploy live keep the compatibility behaviour of allowing CSV schema inference.
- Bounded CSV reads with full unprojected schema or projection-covered schema run with `infer_schema=False`, so undeclared columns are not inferred behind the scenes.
- Header-only validation now catches missing declared columns, missing projected columns, duplicate CSV headers, and empty CSV headers without a Polars schema-inference scan.
- `expected_columns` alone deliberately does not satisfy the bounded CSV dtype requirement because it pins order, not types.
- API input flat-file reads now use the shared source adapter too, giving API input CSV nodes the same `schema_overrides` / `dtypes` / `column_dtypes` / mapping `schema` migration path as `dataSource`.
- Deploy input/output schema inference now preserves source dtype config for flat API/data-source inputs by reading through the same adapter.

Evidence:

- `TestReadSourceCSV.test_bounded_profiles_require_declared_csv_schema`
- `TestReadSourceCSV.test_small_eager_profiles_can_infer_csv_schema`
- `TestReadSourceCSV.test_bounded_profile_accepts_declared_csv_schema`
- `TestReadSourceCSV.test_bounded_profile_requires_declared_dtype_for_every_unprojected_csv_column`
- `TestReadSourceCSV.test_bounded_profile_projection_requires_declared_dtype_for_projected_csv_column`
- `TestDataSourceAdapterFlatFile.test_read_data_source_accepts_all_declared_dtype_config_keys`
- `TestDataSourceAdapterFlatFile.test_read_data_source_rejects_bounded_csv_without_dtypes`
- `TestBuildApiInput.test_bounded_csv_source_requires_declared_dtypes`
- `TestBuildApiInput.test_bounded_csv_source_uses_declared_dtypes`
- `TestBuildNodeConfigProducesValidKeys.test_api_input_preserves_declared_dtype_config`
- Targeted regression runs:
  - `uv run pytest tests/test_io.py tests/test_executor_builders.py::TestBuildApiInput tests/test_config_validation.py -q`
  - `uv run pytest tests/test_chunk_runner.py tests/test_codegen_builders.py::TestCodegenExecValidation::test_data_source_exec_uses_declared_schema_boundary tests/test_deploy_contract_integrity.py::TestStaticDataSourceSchemaDrift tests/test_deploy_internals.py::TestScoreGraphStaticDataSourceRemap -q`
  - `uv run pytest tests/test_deploy_internals.py::TestInferInputSchema tests/test_deploy_internals.py::TestInferOutputSchema tests/test_deploy_internals.py::TestScoreGraphApiInputInjection tests/test_deploy_internals.py::TestScoreGraphStaticDataSourceRemap -q`
  - `uv run pytest tests/test_codegen.py tests/test_codegen_builders.py::TestGenApiInput tests/test_codegen_builders.py::TestCodegenExecValidation -q`
  - `uv run ruff check ...`
  - `uv run mypy ...`

### Slice 8 V4 Logical Source Projection And Schema Parity Status

Completed in the final Slice 8 hardening sub-slice:

- Source projection remapping now lives in the shared projection module as `source_scan_projection(...)`, rather than being a builder-local special case.
- Bounded source scans can now map post-source logical demand back to physical scan columns for unambiguous `selected_columns` plus `column_renames` shapes. Example: downstream demand for `premium` reads physical `raw_premium` when the source renames `raw_premium -> premium`.
- Stale `selected_columns` are validated against source schemas without widening the scan, so bounded reads can stay narrow while still failing loudly on stale UI/source configuration.
- Unsafe rename shapes remain broad or fail deterministic validation rather than guessing. Rename pushdown without a `selected_columns` physical boundary is intentionally conservative because a raw column may already share the logical target name.
- Declared fan-in Polars joins now check simple literal `on`, `left_on`, and `right_on` key dtype compatibility before running the join node, surfacing a typed `SchemaMismatchError` instead of a late Polars failure.
- Deploy contract-only modelScore validation now derives live feature order from the runtime frame schema, so training/score feature-order drift is caught even when the model artifact is not loaded.
- Declared categorical value-domain parity is now part of the feature contract. Sources and modelScore nodes can declare `categorical_levels`; training collects those domains through upstream graph ancestry and persists only the model-used categorical features, while deploy/model scoring treats the saved contract as self-sufficient and rejects conflicting runtime declarations. Observed values are checked before prediction, and batch scoring validates per chunk. Domains are explicit metadata, deterministically ordered as sets, and never inferred by scanning large datasets.

Evidence:

- `test_source_scan_projection_maps_logical_renames_to_physical_columns`
- `test_source_scan_projection_broadens_unsafe_rename_without_selected_columns`
- `test_source_scan_projection_rejects_demand_excluded_by_selected_columns`
- `test_source_scan_projection_rejects_malformed_projection_config`
- `TestBuildApiInput.test_source_projection_maps_renamed_output_columns_to_physical_columns`
- `TestBuildApiInput.test_source_projection_avoids_ambiguous_rename_pushdown`
- `TestBuildApiInput.test_source_projection_validates_stale_selected_columns`
- `TestBuildApiInput.test_source_projection_rejects_demand_excluded_by_selected_columns`
- `test_execute_lazy_rejects_simple_join_key_dtype_mismatch_before_running_node`
- `test_execute_lazy_accepts_matching_simple_join_key_dtypes`
- `test_execute_lazy_rejects_left_on_right_on_join_key_dtype_mismatch`
- `TestScoreGraphModelScoreRemap.test_contract_only_model_score_rejects_runtime_feature_order_drift`
- `TestBuildContract.test_build_contract_records_declared_categorical_levels`
- `TestValidateCategoricalValueDomains.test_allows_null_only_when_declared`
- `TestValidateCategoricalValueDomains.test_rejects_null_when_not_declared`
- `TestValidateCategoricalValueDomains.test_rejects_unknown_observed_level`
- `TestMergeCategoricalLevelDeclarations.test_rejects_conflicting_declarations`
- `TestContractHash.test_categorical_level_reorder_preserves_hash`
- `TestSaveArtifactsCoverage.test_save_feature_contract_includes_declared_categorical_levels`
- `TestSaveArtifactsCoverage.test_training_contract_filters_broad_source_categorical_levels`
- `TestSaveArtifactsCoverage.test_training_contract_rejects_levels_for_numeric_model_feature`
- `TestTrainingCategoricalLevelDeclarations.test_collects_source_declared_levels_through_transforms`
- `TestModelScorerScore.test_feature_contract_rejects_declared_categorical_level_drift`
- `TestModelScorerScore.test_feature_contract_rejects_observed_category_outside_domain_live`
- `TestModelScorerScore.test_feature_contract_enforces_levels_without_runtime_redeclaration`
- `TestBatchScoreToParquet.test_declared_categorical_levels_validate_before_batch_predict`
- `TestScoreGraphModelScoreRemap.test_remapped_model_score_rejects_observed_category_before_predict`
- `TestScoreGraphModelScoreRemap.test_contract_only_model_score_rejects_declared_categorical_level_drift`
- `TestScoreGraphModelScoreRemap.test_contract_only_model_score_accepts_matching_declared_categorical_levels`
- `TestScoreGraphModelScoreRemap.test_contract_only_model_score_rejects_observed_category_outside_domain`
- `TestScoreGraphModelScoreRemap.test_contract_only_model_score_uses_contract_levels_without_redeclaration`
- `TestScoreGraphModelScoreRemap.test_contract_only_model_score_collects_upstream_level_declarations`
- `TestGenApiInput.test_api_input_preserves_categorical_levels_in_shared_reader`
- Targeted regression runs:
  - `uv run pytest tests/test_projection_planner.py::test_source_scan_projection_maps_logical_renames_to_physical_columns tests/test_projection_planner.py::test_source_scan_projection_broadens_unsafe_rename_without_selected_columns tests/test_projection_planner.py::test_source_scan_projection_rejects_demand_excluded_by_selected_columns tests/test_projection_planner.py::test_source_scan_projection_rejects_malformed_projection_config tests/test_executor_builders.py::TestBuildApiInput::test_source_projection_maps_renamed_output_columns_to_physical_columns tests/test_executor_builders.py::TestBuildApiInput::test_source_projection_avoids_ambiguous_rename_pushdown tests/test_executor_builders.py::TestBuildApiInput::test_source_projection_validates_stale_selected_columns tests/test_executor_builders.py::TestBuildApiInput::test_source_projection_rejects_demand_excluded_by_selected_columns tests/test_execute_lazy_contracts.py::test_execute_lazy_rejects_simple_join_key_dtype_mismatch_before_running_node tests/test_execute_lazy_contracts.py::test_execute_lazy_accepts_matching_simple_join_key_dtypes tests/test_execute_lazy_contracts.py::test_execute_lazy_rejects_left_on_right_on_join_key_dtype_mismatch tests/test_deploy_internals.py::TestScoreGraphModelScoreRemap::test_contract_only_model_score_rejects_runtime_feature_order_drift -q`
  - `uv run pytest tests/test_io.py tests/test_projection_planner.py tests/test_executor_builders.py::TestBuildApiInput tests/test_execute_lazy_contracts.py tests/test_deploy_internals.py::TestScoreGraphModelScoreRemap -q`
  - `uv run pytest tests/test_feature_contract.py tests/test_model_scorer.py tests/test_deploy_internals.py tests/test_algorithms_coverage.py tests/test_modelling_routes.py tests/test_codegen_builders.py tests/test_executor_builders.py tests/test_execute_lazy_contracts.py tests/test_config_validation.py -q`
  - `uv run ruff check src/haute/projection.py src/haute/_builders.py src/haute/_io.py src/haute/_execute_lazy.py src/haute/deploy/_scorer.py tests/test_projection_planner.py tests/test_executor_builders.py tests/test_execute_lazy_contracts.py tests/test_deploy_internals.py`
  - `uv run ruff check src/haute/modelling/_feature_contract.py src/haute/modelling/_training_job.py src/haute/_model_scorer.py src/haute/_builders.py src/haute/_execute_lazy.py src/haute/deploy/_scorer.py src/haute/routes/_train_service.py src/haute/_codegen_builders.py src/haute/_config_builder.py src/haute/_config_validation.py src/haute/_types.py tests/test_feature_contract.py tests/test_model_scorer.py tests/test_deploy_internals.py tests/test_algorithms_coverage.py tests/test_modelling_routes.py tests/test_codegen_builders.py tests/test_executor_builders.py tests/test_execute_lazy_contracts.py tests/test_config_validation.py`
  - `uv run mypy src/haute/projection.py src/haute/_builders.py src/haute/_io.py src/haute/_execute_lazy.py src/haute/deploy/_scorer.py`
  - `uv run mypy src/haute/modelling/_feature_contract.py src/haute/modelling/_training_job.py src/haute/_model_scorer.py src/haute/_builders.py src/haute/_execute_lazy.py src/haute/deploy/_scorer.py src/haute/routes/_train_service.py src/haute/_codegen_builders.py src/haute/_config_builder.py src/haute/_config_validation.py src/haute/_types.py`

## Delivery Rules

Every slice follows the same workflow:

1. Write failing tests first for the intended behaviour and regression risks.
2. Implement the smallest general change that makes those tests pass.
3. Run targeted tests and lint for the touched area.
4. Use a developer pass and a reviewer pass for every slice. If agent tooling is unavailable, record the exception and perform the two passes manually.
5. Run the shared execution conformance suite for every slice that touches execution, projection, materialisation, chunking, route status, or metrics.
6. After the slice is functionally complete, run a review pass focused on elegance, generality, performance, robustness, and failure modes.
7. Update this document with completion status, test evidence, review sign-off, and any deferred questions.

No slice should add silent fallbacks. If a graph shape is unsupported, the code should say so clearly enough that we can fix the missing planning rule.

No route should recover from projection, planning, chunk-contract, or streaming-contract failure by broadening to a full collect unless that path is explicitly named as best-effort and surfaced to the caller. Bounded-memory paths must fail loudly when the bounded-memory contract cannot be honoured.

Enforcement comes from tests, not from convention. Each contract below names the test that holds it.

## Current Gaps (honest baseline)

Before describing the target architecture, we record what the code does *today* — these are the items the rewrite has to remove or reify.

1. **Silent broaden-to-collect inside the legacy sink helper**. Slice 2 V1 removed `safe_sink` from bounded-memory callers and introduced `bounded_sink` / `best_effort_sink`; Slice 2 V2 routes audited bounded collect sites through `streaming_collect(..., profile=...)`. File/schema preview and deploy-schema sampling now also route through the profiled collect helper. Remaining broadening risk lives only in explicitly named compatibility paths and deploy response materialisation.
2. **`ExecutionContext.memory_limit_bytes` was dead in production at baseline**. Slice 5 V1 now wires admitted, budgeted contexts into preview, sink, optimiser setup, auto-range, training prep, and deploy scoring. The budget is an RSS-growth allowance from the admission baseline, not a default absolute process cap, so cache-warmed GUI processes do not self-block. Remaining follow-up: queueing/admission scheduling.
3. **Process-global Polars config mutation**. Resolved for production call sites: direct `pl.Config.set_streaming_chunk_size(...)` is forbidden by a hygiene test, and production callers use the locked `temporary_streaming_chunk_size(...)` helper. Polars still exposes the setting process-globally, so the helper serialises the scoped mutation.
4. **Projection scatter**. Resolved for V1: projection planning now lives behind `haute.projection`, route/deploy private execution-helper imports are forbidden with an empty allowlist, and node-type projection coverage is validated at import time. Remaining work is richer explain/provenance only if the UI needs it.
5. **Silent broadenings inside the planner itself**:
   - AST parse failure on `pl.join(...)` returns `[]` (`_execute_lazy.py:457-459`) → opaque parent demand.
   - `required_columns_by_node` seed is silently ignored when `len(children) > 1` (`_execute_lazy.py:687`).
   - Opaque builder + multi-parent fan-in with no `inputs_by_parent` declaration silently broadens (`_execute_lazy.py:740-742`).
   - `_effective_contract` softens to `Contract.opaque()` on `ConfigError | OSError | MlflowException` (`_execute_lazy.py:140-187`), while `_projection_contract` raises (`:190-201`). Same node, two answers.
   - Preamble compile errors are swallowed and re-injected only into `POLARS`/`LIVE_SWITCH` nodes (`executor.py:1080-1105`); a preamble that defines a helper used inside `MODEL_SCORE` config silently fails downstream.
6. **Job lifecycle taxonomy is fragmented.** Resolved for long-running solve/training/auto-range V1 through `JobLifecycle`, typed terminal states, and shared cancellation reason propagation. Non-long-running request-local writes are tracked as Slice 13 guardrail work.
7. **Reason precedence is undefined.** Resolved for long-running jobs: cancellation, timeout, memory-limit, and contract-error transitions preserve typed terminal reasons. Remaining work is broader fault-injection coverage rather than a known broken path.
8. **Training is uncancellable end-to-end.** Training now receives admitted execution contexts and cooperative cancellation boundaries. Native CatBoost/GLM internals remain cancellable only between named stages.
9. **Heavy frames retained in `JobStore`.** Improved: apply results and ratebook factors are persisted as parquet artifact handles, solve-result dataframes are cleared after persistence, and training loss history is capped. Solver/quote-grid state remains in memory for interactive frontier/apply workflows and is a deliberate local-GUI trade-off until a full artifact-backed optimiser session model exists.
10. **Eager preview collects every ancestor.** Resolved for first-click target previews: the backend materialises only the selected node and bounded initial column set, with cache keys encoding the materialisation shape.
11. **Training streaming-engine omissions.** Resolved for V1 training prep: known full-frame prep/materialisation steps route through the profiled helper and named execution stages. Native training libraries still require measured materialised train/eval frames.
12. **Ratebook factor source materialisation.** Improved but not fully chunked: factor extraction is projected, staged, budget-checked, and factors are persisted to an artifact handle after solve. The solve itself still requires an in-memory factor table for the current price-contour API.
13. **Deploy batch API boundary is bounded, with request streaming intentionally out of scope for V1.** Slice 10 now wraps deploy collect in an admitted execution context, uses the shared no-fallback `streaming_collect(...)` helper, routes deploy-batch `modelScore` nodes through the existing parquet-batched scorer, forwards deploy projection demand into model scoring, uses strict no-fallback temp sinks for scorer inputs, cleans request-scoped scorer temp files, and streams NDJSON responses when requested. Non-streaming JSON responses return a bounded envelope. Remaining boundary: request payload materialisation is guarded by admission/request-size limits rather than a streamed upload contract.
14. **`.json` source breaks bounded memory silently.** Resolved: bounded execution profiles reject plain JSON before eager parsing. NDJSON uses `scan_ndjson`; eager JSON remains allowed only for preview/deploy-live compatibility.
15. **Preview cache pin leaked on exception at baseline.** Slice 5 V1 now releases preview cache pins from a `finally` path and covers projection-failure leakage with `test_preview_cache_unpins_entry_when_preview_projection_fails`.

Each item below is removed by a named contract or slice.

## Canonical Contracts

These contracts must be designed and tested before the broad refactors depend on them. Every contract names the test fixture that enforces it.

### Execution Profiles

Every execution entry point must declare one profile:

- `preview_eager`: interactive, bounded preview sample, cache-aware, never a full-data contract.
- `lazy_sink`: batch materialisation to an output file (bounded memory).
- `training_prep`: batch materialisation for downstream model fitting (bounded memory through to the algorithm boundary).
- `optimiser_setup`: optimiser solve preparation and grid/factor setup (bounded memory; ratebook factor source preflight required).
- `auto_range`: frontier range estimation, chunk-capable when eligibility allows it.
- `deploy_live`: deployed scoring with bounded request payloads.
- `deploy_batch`: deployed batch scoring; either chunked or explicitly bounded.
- `chunked_map_reduce`: constrained chunk-local execution with bounded reducers.

Each profile must state whether it is bounded-memory, best-effort streaming, or intentionally eager. Profile is a field on `ProjectionRequest`, `ExecutionContext`, and on every metrics payload — guardrail tests fail when a route entry point omits it.

### Job Lifecycle And Error Taxonomy

Background and long-running work must share one lifecycle contract.

- Running states: `queued`, `running`, `cancelling`.
- Terminal states: `completed`, `cancelled`, `superseded`, `timed_out`, `memory_limited`, `contract_error`, `error`.
- Terminal transitions go through one helper, `JobLifecycle.transition(job_id, *, to, reason, expected_status="running")`, which is the only sanctioned writer of `status` for background jobs. Every direct `atomic_update(..., status=...)` in `routes/` is replaced.
- Reason precedence is explicit and tested: `superseded > timed_out > cancelled > memory_limited > contract_error > error`. The "first writer wins" current behaviour is forbidden.
- `BackgroundJobStoppedError` carries the *registry-derived* terminal reason, not the post-write `store.require_job(...).status`. The current code path at `_optimiser_service.py:2064-2065` is rewritten.
- `ExecutionMemoryLimitExceededError` always maps to `memory_limited`. `ContractMismatchError` always maps to `contract_error`. Generic `error` is reserved for everything else.
- Temp directories, checkpoint files, cache pins, worker registry entries, and heavy job payloads are cleaned on every terminal path. Cleanup is owned by a context-manager scope per worker, not scattered try/finally.
- Non-cooperative native work (Polars `collect`, `solver.solve`, model fit/predict) is acknowledged as cooperative-at-boundary only; cancellation latency is measured and bounded per profile (see Cancellation Latency Budgets below).
- Training, optimiser solve, and any future long worker is registered with `CancellableJobRegistry` and runs under an `ExecutionContext`. Training-as-uncancellable is removed.

Conformance suite cases (named, must exist):

- `test_terminal_idempotent_under_race`
- `test_supersession_then_timeout_reports_superseded_not_timeout`
- `test_timeout_then_supersede_reports_timeout`
- `test_contract_error_distinct_from_internal_error`
- `test_memory_limit_distinct_from_cancellation`
- `test_route_status_taxonomy_matrix` (auto-range × solve × training × preview × trace × sink × deploy, parametrised over cancellation/supersession/timeout/memory/contract/internal)
- `test_temp_parquet_removed_on_every_terminal_path` (parametrised over all six terminal states)
- `test_register_latest_under_lock_with_concurrent_supersede`

### Projection Planner API

Projection must be owned by a dedicated planner module, not by route-local helper code or by `if NodeType.X` branches inside the executor.

Required types (drawn from real fields the engine and routes already pass around — fields are examples, not exhaustive):

```python
@dataclass(frozen=True)
class ProjectionRequest:
    graph: PipelineGraph
    target_node_id: str | None
    profile: ExecutionProfile
    required_columns_by_node: Mapping[str, frozenset[str]]
    side_inputs_to_preserve: frozenset[str] = frozenset()
    source: str = "live"

@dataclass(frozen=True)
class ProjectionPlan:
    needed_by_node: Mapping[str, frozenset[str] | None]   # None == opaque
    edge_demands: Mapping[tuple[str, str], frozenset[str] | None]
    materialisation_boundaries: frozenset[str]
    opaque_boundaries: frozenset[str]
    diagnostics: ProjectionDiagnostics

class ProjectionRule(Protocol):
    node_type: NodeType
    def parent_demand(self, node, my_needed, parents, seeds) -> ProjectionContribution: ...

@dataclass(frozen=True)
class ProjectionDiagnostics:
    column_provenance: Mapping[tuple[str, str], tuple[ProvenanceReason, ...]]
    opaque_reasons: Mapping[str, OpaqueReason]
    impossible_projections: Sequence[ProjectionImpossible]
```

Public API: `haute.projection.plan(request)`, `haute.projection.explain(plan, column)`, `haute.projection.register_rule(node_type, rule)`. Everything else is `_private`.

Rules to extract and register: `OptimiserDataInputRule` (replaces `_optimiser_parent_demands`), `RatebookFactorRule` (replaces `_ratebook_factor_required_columns`), `DeclaredFanInRule`, `PolarsJoinSuffixRule` (decision: keep AST inference as a registered rule for `NodeType.POLARS` only when no `inputs_by_parent` is declared, but require a typed `ProjectionImpossible` diagnostic when AST parsing fails — never silent emptiness), `ScenarioExpanderRule`, `ModelScoreRule`.

Diagnostics are first-class. Every column kept by the planner has a provenance trail that names which seed reached it via which edges via which rule. Without provenance, "explain" tests can't lock anything.

Registry coverage gate: `validate_registry_complete` (`_registry.py:166-187`) is extended to fail at import when a `NodeType` lacks a `ProjectionRule` or an explicit opaque declaration.

Route-import lint: a hygiene test scans `src/haute/routes/**` and `src/haute/deploy/**` for private execution-helper imports. The allowlist is empty; new route/deploy execution access must go through the shared facade.

### Chunk Plan Contract

Chunked execution is not a universal streaming executor. It is a constrained physical strategy chosen only when graph semantics prove it safe.

Every chunk-capable rule must declare:

- map-only, bounded-state, repartition-by-key, or global-materialisation requirements;
- ordering guarantees;
- key partitioning requirements;
- fan-in support or explicit fan-in rejection;
- whether state crosses chunk boundaries;
- whether retries are safe;
- whether model/scorer instances can be reused safely;
- reducer bounds and merge semantics.

Unsupported shapes return explicit diagnostics, not silently fall back to broad execution inside a bounded-memory path.

The `ChunkPlan` is computed *from* the `ProjectionPlan` — chunkability is a function of the planner's output. The auto-range eligibility check at `routes/_optimiser_service.py:507-516` becomes a planner-level decision, not route-local code.

V1 chunk-safe set (decision, not deferred): `DATA_SOURCE` (parquet/CSV scan only — JSON excluded), `RATING_STEP` (when no user code), `SCENARIO_EXPANDER`, `MODEL_SCORE` (with explicit `model_reuse_lifetime=batch`), `BANDING`, `OPTIMISER_APPLY`. Explicitly unsupported: any `POLARS` node with a sort/window/global-aggregation, fan-in joins without declared `inputs_by_parent`, opaque user code in `RATING_STEP`.

### Streaming Compatibility Contract

Polars `LazyFrame` and `collect(engine="streaming")` are not themselves a bounded-memory guarantee. The engine must define:

- which node operations are stream-compatible;
- how streaming plan inspection is performed via `lf.explain(engine="streaming")` for canonical graphs (sink, training prep, optimiser project, auto-range);
- which operators force non-streamable/global execution (sorts, windows, global aggregations, unsupported joins, UDFs, broad fallbacks);
- a single `streaming_collect(lf, *, profile, allow_broad=False)` helper that all bounded-memory callers go through; the helper raises `BoundedMemoryUnsupportedError` when the plan is not streamable, and only the explicit `allow_broad=True` opt-in falls back;
- how `safe_sink` is split: `bounded_sink(lf, path, ...)` (the new default for `lazy_sink`, `training_prep`, `optimiser_setup`, `deploy_batch`) raises on streaming failure; `best_effort_sink(lf, path, ..., allow_broad=True)` is the named broad path. The current behaviour at `_polars_utils.py:147-158` is the broad path under a different name.
- how tests prove no full-frame collect happened on bounded-memory paths via an `ExecutionContext` collect counter asserted at zero.

### Polars Version Floor And Drift Policy

The plan leans on `engine="streaming"`, `sink_parquet`, and `set_streaming_chunk_size` — surfaces Polars itself flags as unstable. We pin a minimum Polars version, run CI against the lower and upper supported versions, and fail loudly when streaming behaviour drifts. The `streaming_collect` helper above is the single seam — when Polars renames or removes the streaming engine, only that helper changes.

### Checkpoint Policy

Checkpointing is a policy, not scattered calls to parquet writes.

The policy defines:

- parquet versus in-memory materialisation decisions;
- row-group size and compression defaults (`lz4` for transient checkpoints, `zstd` for retained outputs);
- projected schema before write;
- disk budget enforcement (counted writes, not post-hoc `du`);
- atomic write and cleanup rules;
- checkpoint reuse/resume stance;
- downstream scan projection tests, proving rereads do not pull discarded columns;
- collision avoidance: checkpoint filenames include the `job_id`, not only `nid`. Concurrent `execute_sink` calls on the same graph must not collide. The current `_execute_lazy.py:1327` reuses `nid`; this is fixed as part of this contract.

### Metrics And Trace Schema

Metrics are bounded, versioned, and concrete enough to drive optimisation decisions.

`ExecutionStageMetric` (`_execution_context.py:50-66`) is extended:

```python
@dataclass(frozen=True, slots=True)
class ExecutionStageMetric:
    name: str
    operation: str
    profile: ExecutionProfile
    elapsed_ms: float
    node_id: str | None
    job_id: str | None
    rss_start_bytes: int | None
    rss_end_bytes: int | None
    rows_in: int | None = None
    rows_out: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None
    columns_scanned: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0
    schema_version: int = 1
```

`ExecutionTraceSummary` carries `schema_version`, `profile`, `status`, `terminal_reason`, stage rollups, node rollups, retained stage sample, and truncation indicators.

`ExecutionMetricsRecorder` enforces:

- max stage count;
- max label cardinality;
- chunk rollups after the cap;
- no retained frames or `LazyFrame`s — a recursive `isinstance` guard runs on every recorded payload;
- TTL/eviction behaviour in job store;
- snapshot tests for payload shape, with schema-version bumps required on any breaking change.

### Execution Policy And Budgets

Memory and disk budgets are more than reactive RSS checks.

The policy layer defines:

- per-route default budgets and environment-variable validation at startup;
- per-job versus process-wide accounting (process-wide is authoritative; per-job is a delta from start-of-stage);
- concurrency admission via a process-wide semaphore-with-priority: heavy jobs declare an estimated peak RSS at launch; if admitted-set-sum-of-estimates exceeds budget × 0.8, new heavy jobs are queued, not admitted;
- preflight estimates from projected schema/cardinality where possible (rows × bytes-per-row × column-count + 1.5× overhead);
- behaviour when the OS memory sampler is unavailable (treat as unbounded; emit a `memory_sampler_unavailable` warning event; bounded-memory profiles fail unless `HAUTE_ALLOW_UNSAMPLED_BUDGET=1`);
- route-specific HTTP/status mapping for memory (507), disk (507), timeout (504), cancellation (409), and contract failures (422);
- adoption gate: a guardrail test fails when any `ExecutionContext(...)` is constructed in `src/haute/routes/**` or `src/haute/deploy/**` without `memory_limit_bytes` set (or with `None`) outside an explicit allowlist.

Large jobs that cannot be safely interrupted inside native work are evaluated for isolated worker processes (decision below in Open Questions resolved).

### `ExecutionContext` Decomposition

`ExecutionContext` today fuses three responsibilities (cancellation, budget, metrics) and forces an RSS sample on every `stage()` even when no budget is configured (`_execution_context.py:211, 221`). It splits:

- `CancellationScope` — cooperative token, `throw_if_cancelled`, `current()` lookup via `ContextVar` so user code in preambles / UDFs can cooperate.
- `BudgetPolicy` — preflight estimate, admission, RSS sampling on-demand, `memory_limit_bytes`, `disk_limit_bytes`. Sampling is opt-in per stage rather than always-on.
- `MetricsRecorder` — the existing recorder, extended per the schema above.
- `ExecutionContext` composes the three; existing call sites keep working but new entry points compose explicitly.

`ContextVar`-backed propagation means new entry points cannot forget to thread the context — UDFs / preamble code reach `CancellationScope.current()` directly. The current ad-hoc kwarg threading remains valid but is no longer the only path.

### Preview Cache Contract

V1 scope note: context propagation into arbitrary user UDFs / preamble code is deferred
hardening. The near-term implementation keeps explicit context propagation through
engine-owned call paths and avoids adding surprising ambient state before the main
execution surfaces are stable.

Preview cache reuse has a documented contract:

- cache key dimensions: graph fingerprint, target node, projection set, source, row limit, preamble/utility fingerprint, path/source fingerprint, execution profile, and user/session scope where relevant;
- value shape: bounded samples, schemas, metrics, and optional checkpoint handles;
- projection subset/superset reuse rules;
- invalidation ownership after graph edits, source changes, failed materialisations, cancelled requests, and memory pressure;
- cache budget enforcement and eviction tests;
- pin lifecycle: pins are acquired before response shaping and released through a guaranteed `finally` path. The current pin-leak on exception (`executor.py:856,1027`) is fixed by this contract.

### Logging And Telemetry Schema

Structured logs are not free-form. Every execution event emits a fixed event schema:

- required fields: `request_id`, `job_id`, `node_id`, `profile`, `stage`, `elapsed_ms`, `rss_start_bytes`, `rss_end_bytes`, `event`;
- forbidden fields: raw paths over N chars, raw user code, raw config dicts, full graph payloads;
- redaction layer for source paths and user-provided strings;
- a single emit helper (`haute._logging.emit_stage_event`) that all execution code goes through;
- explicit OTel mapping: `stage()` becomes an OTel span when `HAUTE_OTEL_ENABLED=1`; `ExecutionStageMetric` fields map 1:1 to span attributes;
- log-volume budget per job (cap + truncation indicator).

### Schema And Dtype Boundary Contract

V1 scope note: OTel export is deferred. The near-term requirement is structured logs
plus bounded status JSON using the same schema names.

Dtype safety is a first-class boundary check at every source/sink/join.

- Every node carries an expected output schema (dtype + nullability) alongside its column contract;
- `read_source` declares dtypes per source kind, not per call site (the current `.csv` head-inference is a known footgun);
- joins assert dtype compatibility; mismatches raise `SchemaMismatchError` (mapped to `contract_error`);
- train/score parity test suite asserts categorical levels and feature order are bit-stable;
- the planner can use dtype information to narrow downstream demands (e.g. drop columns whose declared dtype is unused by a downstream rule).

### I/O Source Adaptor Contract

`read_source` currently dispatches on extension with no uniform schema/projection-pushdown contract. JSON is eagerly read end-to-end, silently breaking bounded-memory claims.

Replaced by a `DataSource` adaptor:

- one method per kind (`scan_parquet`, `scan_csv`, `scan_ndjson`, `scan_databricks`); JSON is explicitly *not* a bounded-memory source — `deploy_live` may use it, every other profile rejects it;
- uniform projection pushdown contract: `DataSource.scan(*, projection)` is the only path, and the planner asserts pushdown happened via `lf.explain()` inspection;
- partitioned-source projection (Hive directories) is supported in v1 for parquet only;
- instrumented sources record `bytes_read` and `columns_scanned` into the metrics schema.

### Concurrency / Threading Model

The plan names the threading-vs-asyncio boundary explicitly:

- FastAPI route handlers are async; long work runs in `loop.run_in_executor` against a dedicated thread pool, not `threading.Thread` directly;
- single-process Polars; the GIL is released inside `collect()` so multi-thread admission works, but only up to the budget semaphore above;
- per-uvicorn-worker caches; multi-worker deploys must share state via files (checkpoints), not module-level dicts;
- Direct `pl.Config.set_streaming_chunk_size` calls are moved out of production code and behind the locked `temporary_streaming_chunk_size(...)` helper. Process-global mutation is forbidden by a guardrail test.

### Determinism And Reproducibility

The plan states what is bit-identical, what is row-order-stable, and what is set-equal:

- bit-identical: parquet sink output for `lazy_sink` profile with fixed Polars version, fixed dtypes, fixed `data_page_size`, no random sampling;
- row-order-stable: `preview_eager` outputs (sorted by source order); `auto_range` chunk outputs after merge;
- set-equal-only: `chunked_map_reduce` reductions where merge order is unspecified;
- RNG seeding: every `_split` and CatBoost call routes through a single `Random(seed)` constructed in `ExecutionContext`;
- `graph_fingerprint` hash stability is locked by a snapshot test across Polars/Python upgrades.

### Disk Lifecycle And Janitor

Process-wide tracking of `haute_*` temp dirs:

- a server-startup orphan reaper deletes temp dirs whose owning process is gone;
- per-job disk quota enforced by counted writes;
- a kill-switch when the disk budget is breached (raises, does not silently truncate);
- temp-dir context-manager scoping owned by `JobLifecycle.transition`, not scattered across workers.

### Cancellation Latency Budgets

Per-profile worst-case cancel latency is named and tested:

- `preview_eager`: ≤ 200 ms (single-node native `collect` boundary);
- `lazy_sink`, `training_prep`, `optimiser_setup`: ≤ 5 s (one chunk or one solver-internal iteration);
- `auto_range`: ≤ 2 s (one streaming batch boundary);
- `deploy_live`: ≤ 100 ms;
- training algorithm interior (CatBoost iteration): documented as cooperative-at-iteration; status during native work is `cancelling`, not `cancelled`, until the boundary returns.

A fault-injection test harness fires cancellation during native `collect()`, between checkpoint stages, and inside reducers; wall-clock is asserted against the SLO.

### Internal Execution Surface Policy

The local GUI/API deployment does not need a formal semver public API for execution internals. It does need a narrow, stable internal import surface so routes, deploy scoring, and tests do not drift back to private helpers.

- routes and deploy enter lazy execution through `haute.execution` and projection planning through `haute.projection`;
- hygiene tests scan route/deploy modules for private execution-helper imports and keep the allowlist empty;
- incompatible deployed scoring runtime-surface changes bump the deploy bundle format/version;
- a formal deprecation policy remains deferred until these internals are consumed as an external package API.

## Execution Conformance Suite

V1 scope note: the stability discipline here is an internal engine boundary for
routes, deploy, and tests. A formal two-minor-version deprecation policy is deferred;
V1 requires stable public import paths and deploy bundle version bumps for
incompatible runtime-surface changes.

Create a shared conformance suite early and run it whenever execution behaviour changes.

Canonical graph fixtures:

- tall narrow source;
- wide source with many unused columns;
- scenario expansion chain;
- model scoring with and without probability output;
- banding and ratebook factor-source graph;
- fan-out graph;
- fan-in join graph with explicit `inputs_by_parent`;
- fan-in graph with **declared** AST-inferable join (locks current behaviour);
- fan-in graph with non-AST-inferable join (locks loud failure);
- opaque transform boundary;
- training graph with target, weight, offset, exclusions, GLM terms, and categorical dtypes;
- sink and deploy-output graph;
- diamond graph with shared upstream source;
- non-default `quote_id` column;
- `OPTIMISER_APPLY` with `optimised_value_column` rename;
- `DATA_SOURCE` × `.parquet` / `.csv` / `.ndjson` (the supported bounded-memory kinds; `.json` is asserted-rejected on bounded profiles).

Assertions:

- schema parity between projected and unprojected execution for requested columns;
- data parity for deterministic graph subsets;
- projection minimality, including instrumented source reads proving unused columns are not read;
- status/error taxonomy parity across routes;
- metrics schema and bounded retention;
- cancellation at known checkpoints and at native boundaries (latency-budget assertions);
- temp-file cleanup on every terminal state;
- cache invalidation;
- chunked versus non-chunked parity for eligible graphs;
- streaming-plan inspection: `lf.explain(engine="streaming")` does not contain in-memory markers on bounded profiles;
- collect-counter assertion: bounded profiles execute zero unbudgeted full-frame collects;
- preview cache pin balance: failed preview leaves no pinned entry;
- `ExecutionStageMetric.rows_in/rows_out/bytes_read/bytes_written` populated for every stage on every profile;
- `JobStore` retention guard: no `pl.DataFrame | pl.LazyFrame` lives in a job dict outside the explicit `_HEAVY_OBJECT_KEYS` window.

Chunked conformance runs across chunk sizes `1`, a prime number, exact boundary size, larger-than-input, and random partitions.

## Scale Gates

Performance work needs deterministic gates, not only wall-clock expectations.

The benchmark matrix for 10m-row-class workloads:

- row counts: small, 1m, 10m, and a CI-safe synthetic smoke size;
- column widths: narrow, medium, wide;
- scenario multipliers: none, modest, large;
- graph shapes: linear, fan-out, fan-in join, model score, scenario expand, ratebook, training prep, sink, preview, deploy batch;
- source kinds: parquet, csv, ndjson, partitioned-parquet;
- outputs: peak RSS, bytes read, bytes written, rows materialised, columns scanned, **n_collects**, **n_checkpoints**, chunk count, temp disk use, first-click preview latency, repeat-click preview latency, cancellation latency.

CI smoke-size baselines are pinned in `tests/baselines/`; regressions outside a 10% noise envelope fail CI. Wall-clock is one signal among several — `n_collects`, `bytes_read`, and `columns_scanned` are deterministic and harder to fake.

The plan is not accepted for the 10m-row problem until the large local benchmark matrix has been run and recorded.

## Current Baseline (already on the branch)

- Shared `ExecutionContext` with cancellation, timing, RSS sampling, and admitted per-profile memory-growth budgets wired through the main route/service/deploy entry points.
- Eager preview execution records per-node collect stages.
- Lazy execution accepts `ExecutionContext` and records function-build, node-build, and parquet-checkpoint stages.
- Preview supersession can cooperatively cancel active preview execution.
- Auto-range background jobs are single-flight per graph/node and cancellation-aware.
- Auto-range cancellation shares the same execution token used by executor checkpoints.
- Sink, optimiser setup, auto-range, training materialisation, and deployed scoring accept the shared context.
- Score projection eager/batch parity fix.
- Explicit non-Polars contracts are preserved through codegen.
- Projection fixes for optimiser parent demands, fan-in joins, and ratebook factor-source selection.
- Auto-range, optimiser solve, and training have structured terminal taxonomies via `JobLifecycle`; request-local status writes remain a Slice 13 guardrail target.

## Suggested Order

The slice order has been revised. Projection precedes memory budgets (budgets without projection are reactive RSS-watching). Streaming-fallback removal moves up because the silent broaden in `safe_sink` undermines every later memory claim. Lifecycle unification is a dedicated slice because the audit found training is uncancellable and reason precedence collapses on every non-auto-range route.

1. Baseline Guardrails And Conformance Harness (Slice 0).
2. Execution Metrics Surface (Slice 1).
3. Forbid Silent Fallbacks And Streaming Compatibility (Slice 2).
4. General Projection Planner (Slice 3).
5. `ExecutionContext` Decomposition And Propagation (Slice 4).
6. Memory Budgets And Abort Semantics (Slice 5).
7. General Chunked Execution Contract (Slice 6).
8. Job Lifecycle Unification And Heavy-Frame Eviction (Slice 7).
9. I/O Source Adaptor And Schema/Dtype Boundary (Slice 8).
10. Preview Performance Redesign (Slice 9).
11. Deployed Scoring And Sink Consistency (Slice 10).
12. Training Pipeline Memory Safety (Slice 11).
13. Optimiser Solve Generalisation (Slice 12).
14. Determinism, Fault Injection, And Long-Term Guardrails (Slice 13).

## Slice 0: Baseline Guardrails And Conformance Harness

Problem: guardrails cannot wait until the final slice. The riskiest APIs need tests before the architecture expands.

Implementation:

- Add the canonical execution conformance fixtures.
- Add route status/error taxonomy matrix tests.
- Add metrics schema snapshot tests.
- Add registry-wide checks that every node type has explicit projection metadata or an explicit opaque declaration.
- Add hygiene tests that forbid new direct `_execute_lazy` call sites without an explicit execution profile/context decision.
- Keep no-private-planner/import tests for routes and deploy; the reviewed execution-helper allowlist is empty.
- Add deterministic instrumentation helpers for memory sampler, cancellation hooks, collect counters, checkpoint counters, source column reads, and temp-file cleanup.
- Add a `JobStore` payload guard that recursively rejects `pl.DataFrame | pl.LazyFrame` outside the heavy-object window.
- Add a `pl.Config` mutation guard that fails when execution code mutates the process-global Polars config.

Tests:

- Conformance fixtures build and execute at smoke size.
- Every route maps cancellation, timeout, memory-limit, contract-error, and internal-error distinctly (parametrised matrix).
- Metrics payloads are snapshot-tested and bounded.
- Direct executor/planner bypasses fail the guardrail tests.
- Injectable memory and cancellation hooks avoid flaky RSS/timing tests.
- Direct `pl.Config.set_streaming_chunk_size` mutation outside the locked shared helper fails the test.

Acceptance criteria:

- Every later slice has a shared test harness to prove it did not make preview, optimiser, training, sink, deploy, lazy, and chunked execution drift apart.
- Guardrails are in place before projection planner extraction or chunk runner implementation.

## Slice 1: Execution Metrics Surface

Problem: stage metrics exist in memory, but users cannot yet see where time or RAM went, and stages do not carry rows/bytes counters.

V1 scope note: implement the small generic surface first. Profiles, bounded
stage samples, RSS/timing rollups, and status JSON are in scope. OTel, JSONL
trace artifacts, wall-clock attribution gates, and mandatory rows/bytes on
every stage are deferred hardening because forcing those now would either
couple metrics to optimiser details or trigger extra execution just to count.

Status: Slice 1 V1 implemented on 2026-05-09. Evidence:

- `ExecutionProfile`, per-stage profile metadata, RSS peak tracking, bounded
  `ExecutionTraceSummary`, and serialisable metrics payloads live in
  `src/haute/_execution_context.py`.
- Preview, lazy, sink, deploy, optimiser setup, auto-range, and training prep
  paths thread `ExecutionContext` through shared executors.
- Optimiser and training status payloads expose bounded `execution_metrics`.
- Tests run: `uv run pytest tests/test_execution_context.py -q`,
  `uv run pytest tests/test_routes_hygiene.py -q`,
  `uv run pytest tests/test_request_supersession.py tests/test_pipeline_route_supersession.py -q`,
  `uv run ruff check ...`, and targeted `uv run mypy ...`.

Implementation:

- Extend `ExecutionStageMetric` with the fields named in the Metrics And Trace Schema contract.
- Add `ExecutionStageSummary` and `ExecutionTraceSummary` models, schema-versioned.
- Add a serialisable metrics summary helper for `ExecutionMetricsRecorder` with hard payload caps and rollups.
- Include bounded stage summaries in optimiser auto-range job status.
- Include bounded stage summaries in optimiser solve setup status where feasible.
- Include bounded training pipeline materialisation metrics in training job metadata.
- Add structured logs for every heavy stage with the canonical event schema. Deferred beyond V1.
- Define what is public API status metadata versus internal trace/log detail.
- Add the OTel span emission path behind `HAUTE_OTEL_ENABLED`. Deferred hardening.

Tests:

- Metrics summary preserves stage order and aggregates node totals.
- Auto-range status includes execution metrics after a completed run.
- Cancelled/superseded jobs keep partial metrics.
- Training job stores pipeline materialisation metrics without retaining heavy frame objects.
- Long chunked jobs roll up metrics after the cap and expose truncation indicators.
- Metrics labels and stage counts cannot grow unbounded.
- Sum of stage `elapsed_ms` is within 10% of the wall-clock latency for the canonical preview fixture; the gap is attributed to a named `unaccounted_ms` field. Deferred hardening.
- `rows_in/rows_out/bytes_read/bytes_written` are non-null on every stage on the canonical fixtures. Deferred; these stay optional unless already known without extra execution.

Acceptance criteria:

- For each canonical preview fixture (tall, wide, fan-in, ratebook), the sum of stage `elapsed_ms` in the returned trace is within 10% of the wall-clock latency observed by the route handler. The remainder is attributed to a named `unaccounted_ms` field. A test asserts no fixture exceeds 10%.
- Metrics payloads are schema-versioned, bounded, and safe to keep in job store.

## Slice 2: Forbid Silent Fallbacks And Streaming Compatibility

Problem: the plan claims "no silent fallbacks" but `safe_sink` collects on streaming failure (`_polars_utils.py:147-158`), and several planner-internal silent broadenings remain. This must be fixed before any later memory claim is meaningful.

Implementation:

- [done] Split `safe_sink` into `bounded_sink(lf, path, ...)` (raises `BoundedMemoryUnsupportedError` on streaming failure) and `best_effort_sink(lf, path, ..., allow_broad=True)` (current fallback behaviour, named).
- [done] Audit every call site (six callers identified in the audit) and pick the correct variant; bounded-memory profiles default to `bounded_sink`.
- [done] Introduce `streaming_collect(lf, *, profile, allow_broad=False)` and route every bounded `collect(engine="streaming")` through it. The helper centralises profile validation and typed bounded-streaming failures. Planner inspection via `lf.explain(engine="streaming")` remains a separate diagnostic hardening step because Polars explain text is version-sensitive.
- [done] Replace the AST-parse-failure silent emptiness at `_execute_lazy.py:457-459` with `ProjectionImpossible` diagnostics when strict fan-in inference needs the code.
- [done] Replace the multi-child seed silent ignore at `_execute_lazy.py:687` with a typed error for strict bounded/projection-seeded profiles.
- [done] Replace the opaque-fan-in silent broadening at `_execute_lazy.py:740-742` with a typed `ProjectionImpossible` for strict bounded/projection-seeded profiles.
- Reconcile `_effective_contract` and `_projection_contract` at `_execute_lazy.py:140-201`; same node, one answer; divergences surface in `ProjectionDiagnostics`.
- Replace preamble compile-error swallowing at `executor.py:1080-1105` with a named, surfaced error that flows to all downstream node types, not only `POLARS`/`LIVE_SWITCH`.

Tests:

- `bounded_sink` raises `BoundedMemoryUnsupportedError` on a sort-before-sink fixture; `best_effort_sink` succeeds and returns a flag.
- `streaming_collect` raises on a fan-in-with-sort fixture under `lazy_sink`; same fixture under `preview_eager` succeeds.
- AST-unparseable join in a `POLARS` node under `lazy_sink` raises `ProjectionImpossible` with a diagnostic naming the column.
- Multi-child seed honoured (or rejected) — covered by a test snapshot.
- `_effective_contract` divergence is fixed: same node, identical contract under both helpers.
- Preamble compile error in a helper used by `MODEL_SCORE` produces a node-level error, not silent miswiring.

Acceptance criteria:

- No bounded-memory profile silently broadens on streaming-incompatibility.
- Every silent broadening listed in Current Gaps §1, §3, and §5 is removed or named.

## Slice 3: General Projection Planner

Problem: current projection logic is improving but still scattered across at least eight sites, with route-side imports of private engine helpers.

Implementation:

- Extract projection planning into `haute.projection` with the shared API named in the Projection Planner API contract.
- Define `ProjectionRequest`, `ProjectionPlan`, `ProjectionRule`, `ProjectionDiagnostics` with the fields in the contract.
- Attach node-specific projection rules to registry metadata; extend `validate_registry_complete` to fail at import on missing rules.
- Move `OptimiserDataInputRule`, `RatebookFactorRule`, `DeclaredFanInRule`, `PolarsJoinSuffixRule`, `ScenarioExpanderRule`, `ModelScoreRule` into the planner.
- Forbid routes from importing private projection helpers; migrated callers use `haute.projection`.
- Implement provenance-rich `ProjectionDiagnostics` with per-column trails.
- Fold `profile` into `ProjectionRequest`; rules differ by profile (eager-vs-lazy MODEL_SCORE skip, ratebook vs online).
- Decide and document the "opaque seed override" carve-out (currently `len(children) <= 1` at `_execute_lazy.py:687`); name it explicitly so it doesn't get refactored away.

Tests:

- Single-parent chains with terminal column seeds.
- Multi-parent joins with explicit `inputs_by_parent`.
- Joins with suffix-generated columns.
- Ratebook optimiser with data input plus banding source.
- Opaque transform boundaries fail loudly when the caller requests impossible projection.
- Different quote-id column names are handled through config, not assumed.
- Registry-wide projection coverage fails when a node type lacks projection metadata.
- Projected execution matches unprojected execution for requested columns.
- Instrumented sources prove unused columns are not read.
- Diagnostics snapshots explain opaque and materialisation boundaries with full provenance.
- Route-import lint fails when a route imports `from haute.projection._*`.

Acceptance criteria:

- The executor consumes a plan object rather than rediscovering projection details inline.
- Adding a new node type means adding a projection rule in one place.
- No route imports private planner or executor helpers.

## Slice 4: `ExecutionContext` Decomposition And Propagation

Problem: `ExecutionContext` fuses cancellation, budget, and metrics; RSS sampling runs even when no budget is configured; propagation is by kwarg through every layer; UDFs and preamble code cannot reach the active context.

Implementation:

- Split `ExecutionContext` into `CancellationScope`, `BudgetPolicy`, `MetricsRecorder` (the existing recorder, extended). `ExecutionContext` composes the three for backward compatibility.
- Add `CancellationScope.current()` via `ContextVar`. UDFs in preambles call it directly to cooperate.
- Make RSS sampling opt-in per stage (`stage(..., sample_rss=True)`); the always-on sample at `_execution_context.py:211, 221` is removed.
- Replace the `_eager_execute` 8-tuple return at `executor.py:1039-1115` with one frozen dataclass; the named-tuple shape `EagerResult` (`_execute_lazy.py:1486`) is the precedent.
- Move `pl.Config.set_streaming_chunk_size` off `executor.py:1217-1218` into the per-run scope; the process-global mutation is forbidden by the Slice 0 guardrail.

Tests:

- A UDF in a preamble cooperatively cancels via `CancellationScope.current()`.
- `stage()` with `sample_rss=False` performs no `current_rss_bytes()` calls.
- Concurrent sinks no longer race the global `pl.Config`; per-run scope is honoured.
- New entry points without an explicit `ExecutionContext` fail the guardrail test.

Acceptance criteria:

- `ExecutionContext` is composed, not monolithic.
- Cancellation is reachable from any execution-related Python code.
- No execution code mutates process-global Polars state.

## Slice 5: Memory Budgets And Abort Semantics

Problem: cancellation now exists, but `memory_limit_bytes` is dead — no production path sets it. Memory budget policy must be applied consistently by every route.

Implementation:

- Add an `ExecutionPolicy`/`ExecutionBudget` provider for preview, optimiser, auto-range, training, sink, and deploy jobs.
- Add environment-backed memory and disk budget config with startup validation.
- Apply budgets through `BudgetPolicy(memory_limit_bytes=...)` from Slice 4.
- Add the process-wide concurrency/admission semaphore named in the contract.
- Add preflight memory estimates from projected schema/cardinality before known collect/sink/solver/training boundaries.
- Translate `ExecutionMemoryLimitExceededError` into HTTP 507 with `error_code="memory_limit"` and the configured/observed bytes.
- Keep cancellation distinct from memory-budget failures in statuses and logs (depends on Slice 7).
- Make timeout-triggered auto-range cancellation preserve its intended timeout message (depends on Slice 7).
- Define behaviour when RSS sampling is unavailable per the contract.
- Wire the adoption gate guardrail test from the contract.

Tests:

- Each route maps memory-budget failure to the expected status/error shape.
- Setting `HAUTE_PREVIEW_MEMORY_LIMIT_MB=512` and running the wide-source preview fixture with `row_limit=10M` raises `ExecutionMemoryLimitExceededError` within one stage boundary of the breach; the route returns HTTP 507 with `error_code="memory_limit"`; `tempfile`-prefixed `haute_*` directories are removed before the response is sent.
- Timeout cancellation reports timeout, not generic cancellation.
- Superseded auto-range reports superseded, not memory failure.
- Memory checks fail before launching avoidable downstream work.
- Invalid budget environment variables fail loudly at startup.
- Concurrent heavy jobs racing the same process-wide budget are admitted or rejected deterministically.
- Tests use injectable samplers and hooks rather than real RSS timing.
- Adoption gate fails when a route constructs `ExecutionContext(...)` without a budget outside the allowlist.

Acceptance criteria:

- If RAM is about to blow past a configured budget, the backend stops within one stage boundary with a typed error and a 507 response.
- Status semantics remain precise: cancelled, superseded, timed out, memory-limited, contract-error, or internal error.
- `memory_limit_bytes` is set on every production execution path; the dead-code state is removed.

## Slice 6: General Chunked Execution Contract

Problem: auto-range has a streaming fast path, but the general backend still does not expose a reusable chunked execution contract.

### Slice 6 Status

Completed in the Slice 6 implementation:

- Added `haute.chunking` as the public chunk-planning surface.
- Added `ChunkPlanRequest`, `ChunkPlan`, `ChunkCapability`, and `ChunkCapabilityKind`.
- Added `ChunkPlanUnsupportedError` as the typed fail-loud diagnostic for graphs that cannot prove a safe chunked physical plan.
- Chunk planning is computed against the projection planner and records the projected node/edge demands that the runner honours.
- V1 capability checks now reject unsupported source kinds, unsupported node types, fan-in shapes, global/order-sensitive Polars user code, opaque `ratingStep` user code, and `modelScore` nodes without explicit `model_reuse_lifetime='batch'`.
- Chunk capability declarations now live in an immutable node-type registry with import-time coverage validation, so future node types cannot silently skip the chunk contract.
- Planning is serial-by-default with `max_in_flight_chunks=1`; no parallelism is implied until node contracts declare thread-safety.
- Added `ChunkRunnerRequest`, `ChunkBatch`, `iter_chunked_frames`, `run_chunked_reduce`, and the guarded `collect_chunked(..., allow_unbounded=True)` test/diagnostic helper.
- Added `chunk_start_node_id`, `pre_chunk_node_ids`, and `chunk_node_ids` so a bounded caller can execute a proven base pipeline once, then share the generic chunk runner for the downstream chunk-local suffix.
- Added row-expansion accounting for scenario-like nodes so the source batch size is reduced before expansion.
- Added bounded reducer enforcement: reducers must declare `bounded=True`, otherwise chunked execution fails loudly.
- Added checkpoint writing for emitted chunks and cleanup on cancellation or partial failure.
- Added `bounded_collect_batches` as the shared typed wrapper around Polars `collect_batches(engine="streaming")`.
- Checkpoint writes are atomic: a failed parquet write cleans both final and temporary paths instead of leaving corrupt chunk files behind.
- Online auto-range now uses the shared chunk runner for proven scenario suffixes and fails loudly when an eligible streaming shape cannot be proven by `ChunkPlan`, rather than falling back to a bespoke high-memory path.
- Auto-range reduction has explicit collect-batch and batch-reduction checkpoints, so memory/cancellation sampling covers both bucket-file creation and bucket combine.
- The row-local Polars guard is source-bound: frame-derived subplans are rejected inside expression arguments instead of being accepted and failing later at runtime.

Evidence:

- `test_chunk_plan_accepts_v1_chunk_safe_chain`
- `test_chunk_capability_registry_mentions_every_node_type`
- `test_chunk_capability_registry_is_immutable`
- `test_chunk_capability_registry_declares_unsupported_types_explicitly`
- `test_chunk_capability_registry_validation_rejects_drift`
- `test_chunk_plan_rejects_json_sources_for_bounded_chunking`
- `test_chunk_plan_requires_explicit_model_score_batch_reuse`
- `test_chunk_plan_rejects_opaque_rating_step_user_code`
- `test_chunk_runner_matches_full_lazy_for_chunk_safe_chain`
- `test_chunk_runner_projects_source_columns_before_first_map_node`
- `test_chunk_runner_can_start_from_proven_intermediate_frame`
- `test_chunk_runner_ignores_nested_prefix_edge_demands_with_start_frame`
- `test_chunk_runner_supports_row_local_polars_transform`
- `test_chunk_runner_reuses_model_score_model_across_chunks`
- `test_chunk_plan_rejects_global_polars_transform`
- `test_chunk_runner_cancels_before_next_chunk_and_cleans_checkpoints`
- `test_chunk_runner_cleans_checkpoints_when_later_chunk_fails`
- `test_chunk_runner_cleans_partial_checkpoint_write_failure`
- `test_run_chunked_reduce_rejects_unbounded_reducer`
- `test_run_chunked_reduce_accepts_bounded_reducer`
- `test_bounded_collect_batches_uses_polars_streaming_batches`
- `test_bounded_collect_batches_maps_streaming_iteration_failure`
- `test_frontier_auto_range_estimator_checks_memory_during_batch_reduce`
- `test_frontier_auto_range_prepare_does_not_fallback_after_chunk_plan_rejection`
- `test_frontier_auto_range_uses_generic_chunk_runner_for_supported_chain`

Remaining Slice 6 hardening that now becomes Slice 13/conformance work:

- Add broader random-partition/fuzz parity once the graph fuzzer exists.
- Extend the generic chunk runner beyond the V1 serial map-only suffix once richer pre-chunk boundedness contracts cover additional graph shapes.

Implementation:

- Define a chunk-capable node contract: whether a node can process independent batches and what ordering/key requirements it has.
- Define a `ChunkPlan` capability model before implementation: V1 exposes only map-only and bounded-state semantics, with richer repartition/global strategies deferred until the runner actually supports them.
- Add a generic chunk runner that streams source batches, applies a chain of chunk-safe nodes, checkpoints chunk outputs, aggregates bounded summaries, and respects cancellation and memory budgets.
- Keep execution serial-by-default unless node contracts explicitly declare thread-safety.
- Add max in-flight chunk and queue/backpressure rules before any parallelism.
- Define deterministic chunk ids, retry stance, model reuse lifetime, and reducer merge semantics.
- Move auto-range streaming onto this shared runner and fail loudly when the planner cannot prove bounded execution.
- Use the V1 chunk-safe set decided in the Chunk Plan Contract.

Tests:

- Chunked runner matches full lazy output for chunk-safe chains.
- Parity holds across chunk sizes `1`, prime, exact boundary, larger-than-input, and randomised partitions.
- Nulls, dtypes, row ordering, categorical values, and boundary rows are preserved.
- Scenario expansion never materialises all scenarios at once in the chunked path.
- Model scoring reuses loaded models across chunks.
- Non-chunk-safe fan-in graphs fail with a clear `ProjectionImpossible` diagnostic.
- Cancellation during chunk N stops before chunk N+1 within the latency budget.
- Partial failures clean up chunk checkpoints.
- Bounded reducers fail loudly if asked to retain unbounded data.
- Unsupported node types under `chunked_map_reduce` fail at planning time, not at execution time.

Acceptance criteria:

- Auto-range, future optimiser passes, and other large-data workflows can share one chunk execution mechanism.
- Unsupported shapes fail early and clearly.
- Chunking is used only when the graph has a proven `ChunkPlan`; otherwise bounded-memory callers get explicit diagnostics.

## Slice 7: Job Lifecycle Unification And Heavy-Frame Eviction

Problem: only auto-range has a structured terminal taxonomy; solve and training collapse everything into `running | error | completed`. Reason precedence is undefined. Training is uncancellable. Heavy Polars frames live in the `JobStore` for the heavy-TTL window.

Implementation:

- Introduce `JobLifecycle.transition(job_id, *, to, reason, expected_status="running")` as the only sanctioned writer of `status`. Replace every direct `atomic_update(..., status=...)` in `routes/`.
- Add the missing terminal states (`cancelled`, `superseded`, `timed_out`, `memory_limited`, `contract_error`) for **solve** and **training**, not only auto-range.
- Encode reason precedence (`superseded > timed_out > cancelled > memory_limited > contract_error > error`) in `transition`; tests exercise all six pairwise races.
- Rewrite `BackgroundJobStoppedError` to carry the registry-derived terminal reason, not the post-write status read at `_optimiser_service.py:2064-2065`.
- Wire `TrainingJob.run` under `CancellableJobRegistry` and an `ExecutionContext`; surface a `cancel_training` endpoint.
- Make `_solve_background` accept an `ExecutionContext` so post-setup stages can be cooperatively cancelled.
- Bound `train_loss` history in the job dict per the metrics retention cap.
- After `_persist_apply_result_artifact`, null the in-memory `solve_result.dataframe` reference (`_optimiser_service.py:701-733`); the parquet handle is the only retained pointer.
- Replace `factors_df` in-job retention with an artifact handle; the in-memory frame is freed on solve completion.
- Cleanup is owned by a context-manager scope per worker; `JobLifecycle.transition` is the only place temp dirs / cache pins are released.
- Promote `_FRONTIER_AUTO_RANGE_TERMINAL_STATUSES` to a module-level public taxonomy used by every route.
- Fix the `register_latest` / `thread.start()` TOCTOU window at `_optimiser_service.py:1869-1898` (move the launch under the lock or accept a documented "second writer wins" with a test).

Tests:

- All conformance suite cases named in the Job Lifecycle contract pass.
- Training is cancellable end-to-end; latency is within the budget.
- Solve background work cancels at the documented native boundary.
- After solve completion, no `pl.DataFrame` lives on the job dict; only the artifact handle.
- `train_loss` history caps emit a truncation indicator.
- The `register_latest` race produces a deterministic terminal status.

Acceptance criteria:

- Lifecycle and reason precedence are defined, tested, and consistent across every route.
- No heavy frame is retained on the `JobStore` outside the documented heavy-object window.
- Training and solve are cooperatively cancellable end-to-end at the per-profile latency budget.

## Slice 8: I/O Source Adaptor And Schema/Dtype Boundary

Problem: `read_source` dispatches on extension with no uniform contract; `.json` is eager-only and silently breaks bounded-memory; CSV head-inference produces dtype divergence between preview and lazy sink; joins do not assert dtype compatibility.

Implementation:

- Replace `_io.read_source` with the `DataSource` adaptor named in the contract.
- Add explicit per-kind projection pushdown; the planner asserts pushdown via `lf.explain()` inspection.
- Bounded profiles reject `.json`; `deploy_live` is the only profile that may use it.
- Add partitioned-parquet support (Hive directories) with projection.
- Add per-kind dtype declarations; CSV dtype is asserted, not inferred per-call-site.
- Add a `SchemaMismatchError` type emitted on join dtype/level/order violations.
- Add a train/score parity test suite that asserts categorical levels and feature order.
- Use schema knowledge in the planner to narrow downstream demands where possible.

Tests:

- `lf.explain()` confirms projection pushdown for parquet/csv/ndjson.
- A `.json` source under `lazy_sink` fails with a typed error.
- CSV with mismatched dtypes between preview and lazy sink raises at the boundary.
- Train/score parity holds for every algorithm × categorical encoding pair in the test matrix.
- Partitioned-parquet projection prunes unread partitions (instrumented).

Acceptance criteria:

- Every bounded-memory profile honours bounded memory across every supported source kind.
- Dtype/categorical-level drift between train and score is a typed boundary error, not a silent prediction shift.

## Slice 9: Preview Performance Redesign

Problem: preview panes are slow when selecting nodes such as `ratebook_optimiser`; the eager core collects every ancestor (`_execute_lazy.py:1806-1809`); first-click forces a full row-count collect on large sources.

Status 2026-05-11:

- Implemented the first execution-core step: target-only preview now keeps ancestors lazy and materialises only the selected node, while still using the shared eager core for schema capture, projection, contract checks, errors, timings, and preview-cache metadata.
- Target-only preview cache entries now retain only materialised target DataFrames plus schema metadata, reducing retained preview memory for selected-node clicks.
- First-click target previews now apply the same initial-column cap the frontend uses after schema discovery: the backend returns the complete schema but materialises only the bounded initial column set when no explicit requested preview columns are supplied.
- Preview execution metrics now include explicit cache stages (`preview_cache_lookup`, plus hit/miss/extend) so first-click and repeat-click latency is explainable from the response metrics.
- Trace now treats target-only preview cache entries as partial and re-executes when it needs the full ancestor waterfall, instead of silently showing an incomplete trace.
- The preview route keeps relevant ancestor schema/status metadata in the response without materialising ancestor DataFrames, preserving editor dropdown UX while retaining the target-only execution path.
- Preview cache keys now encode target-only materialisation shape, and cache hits additionally verify materialised columns before reuse, so first-click capped caches cannot satisfy later broad previews.
- Target-only previews now propagate upstream node errors onto the target instead of returning an opaque `No output` result.
- First-click preview projection is now regression-tested against an expensive unused column beyond the initial cap, so schema discovery does not accidentally force broad materialisation.
- The Phase 9 latency budgets are documented in `docs/PERFORMANCE_CHECKS.md` and enforced by `tests/performance/test_preview_trace_perf.py` for the representative preview/trace graph. Real 10m-row checks use the same runner artifacts instead of checked-in fixtures.
- Covered with executor, preview-cache, trace, route supersession, documentation, and representative preview/trace performance tests.

Implementation:

- Use the shared projection planner for preview requests.
- Add a `lazy_until=target` execution mode where ancestors stay lazy and only the target collects; the eager core's per-node collect is replaced.
- Add preview-specific execution contexts and metrics to status/logs.
- Avoid serialising or collecting unrequested columns.
- Push projection and limit into scans wherever the source supports it.
- Avoid full row-count collection on first click; preview row counts are derived from the bounded materialised preview frame, not a separate full-data length pass.
- Cache only bounded preview samples, schemas, metrics, and explicit checkpoint handles (already enforced by `_estimate_preview_cache_entry_bytes`).
- Reuse cached upstream materialisations safely across node clicks per the Preview Cache Contract.
- Fix the cache pin leak on exception by releasing preview-cache pins in a `finally` block after response shaping.
- Add an optional preview explain endpoint that returns cache hit/miss, projected columns, and per-stage timings. Decision: do not add a separate endpoint in Phase 9 because the preview response now carries execution metrics with cache hit/miss and per-stage timings; add an explain-only endpoint later only if the UI needs it.
- Define first-click and repeat-click latency SLOs for the benchmark matrix.

Tests:

- Clicking a downstream node after an upstream node does not invalidate useful cache entries.
- Requested preview columns seed projection correctly through data sources, banding, model score, joins, and optimiser apply nodes.
- Preview serialisation respects max cell limits.
- Superseded preview requests cancel the active worker.
- Repeated clicks on known slow graph shapes stay under the agreed performance envelope in a targeted performance test.
- Cache keys change after graph edits, source changes, row-limit changes, preamble/utility changes, and projection changes.
- Failed or cancelled materialisations do not poison later previews; cache pin balance test passes.
- First-click preview does not execute a full-data `len(df)` collect on large sources.
- First-click preview does not evaluate unrequested late columns beyond the backend initial-column cap.
- 10m-row first-click and repeat-click preview SLOs are recorded through local scale-gate artifacts, not default fixtures.

Acceptance criteria:

- Preview latency is explainable from metrics (per the Slice 1 acceptance criterion).
- Node-click behaviour improves generally, not only for `ratebook_optimiser`.
- First-click and repeat-click preview budgets are documented, enforceable locally, and represented by non-flaky CI-sized performance tests.

## Slice 10: Deployed Scoring And Sink Consistency

Problem: deploy and sinks are part of the same execution surface and currently lag behind GUI paths. Deploy collects without a streaming engine declaration. Sink-time mutation of `pl.Config` is process-global.

Promoted earlier in the order because deployed scoring is the most safety-critical surface (it serves prod traffic) and currently bypasses much of the new infrastructure.

Status 2026-05-11:

- Deployed scoring now has a lazy plan surface (`score_graph_lazy`) used by the generated container when clients request `Accept: application/x-ndjson`, so batch responses can stream bounded Polars batches through the wire.
- The existing `score_graph` API now delegates to the lazy plan and performs the final collection through the shared `streaming_collect(...)` helper, keeping temp model-score artifacts alive until collection finishes.
- Generated container scoring admits requests before DataFrame materialisation, records request-materialisation checkpoints, preserves typed memory/cancellation/bounded-streaming errors, and no longer launders unexpected scoring failures into generic validation errors.
- Generated container JSON responses now include execution metrics, while NDJSON responses stream rows directly without the JSON envelope.
- Sink writes now request their reduced streaming chunk size through `bounded_sink(..., streaming_chunk_size=...)`, where the process-global Polars setting is locked and scoped, instead of mutating `pl.Config` directly in `execute_sink`.
- `bounded_sink` now preserves non-streaming Polars compute/schema errors and maps only streaming-compatibility failures to `BoundedMemoryUnsupportedError`.
- Sink routes log write-stage execution metrics on success and include execution metrics in bounded-streaming failure logs.

Implementation:

- Keep deployed scoring on the shared context and projection planner.
- Add `deploy_live` and `deploy_batch` profiles; live is bounded by request payload, batch is chunked.
- Keep the request-size guard before JSON parsing and document supported payload limits for `deploy_live` and non-streaming `deploy_batch`.
- Keep deployed final collection on the shared no-fallback `streaming_collect(...)` helper.
- For `deploy_batch` over 10m rows, prefer NDJSON streaming or source-artifact workflows; only add a request-body streaming/chunk-runner upload path if deployed clients genuinely need huge row sets over HTTP POST.
- Make sink routes expose write-stage metrics in logs/status.
- Replace `safe_sink` with `bounded_sink` on every bounded-memory caller (Slice 2 dependency).
- Verify source path resolution and artifact remapping still fail loudly.
- Surface when a sink path cannot honour bounded streaming via the typed error from Slice 2.
- Add a streaming response (NDJSON) option for `deploy_batch` so the LazyFrame stays lazy through the wire when the client supports it.

Tests:

- Deployed scoring forwards projection seeds and execution context.
- Output field selection happens after required upstream projection.
- Sink selected columns seed projection and avoid broad materialisation.
- Memory budget and cancellation errors do not get wrapped into misleading generic failures.
- `bounded_sink` callers fail with a typed error on streaming-incompatibility instead of silent broadening.
- `deploy_batch` over 10m-row fixture stays under the configured RSS budget.
- Deploy bundle format version is bumped when the deployed engine surface changes incompatibly.

Acceptance criteria:

- CLI/sink/deploy paths follow the same backend rules as GUI workflows.
- Deploy serves prod traffic with bounded memory across every supported scale.

## Slice 11: Training Pipeline Memory Safety

Problem: model training should inherit the same projection, chunking, metrics, and memory safety principles. Today `_training_job.py:480` runs a full-frame `null_count().collect()` with no streaming engine; partition reads at `:665, 676` likewise.

Status 2026-05-11:

- `TrainingJob.run` now accepts the shared `ExecutionContext` and routes training prep, split, partition materialisation, algorithm fit/pool construction, artifact save, and MLflow logging through named stages/checkpoints.
- Training schema, null-target counts, split-key pre-scans, train/eval partition reads, diagnostics partition reads, and non-parquet/LazyFrame input materialisation now use `streaming_collect(..., profile=training_prep)`.
- Temp parquet writes in `_prepare_data` and `_split_data` are instrumented and still use `bounded_sink`; the performance tests now pin that bounded path rather than the old `safe_sink` hook.
- Background training worker memory-budget errors now become typed `memory_limited` jobs with HTTP-507-shaped `error_detail` and bounded `execution_metrics`, instead of generic worker errors.
- Training memory-estimate failures now fail loudly before pipeline execution with a typed contract error, so a broken estimator cannot silently launch an unbounded run.
- CatBoost GPU VRAM insufficiency now refuses before launch with a typed `gpu_vram_limit` response instead of silently mutating the run to CPU.
- MLflow logging receives the cooperative cancellation checkpoint and checks it before/inside the run context and around model/registration work, so cancellation exceptions leave via MLflow's context manager.
- Honest boundary retained: CatBoost/GLM libraries still require materialised train/eval/diagnostics frames. Those materialisations are now measured and memory-budgeted named stages; they are not claimed to be end-to-end chunk-safe.

Implementation:

- Route training source preparation through the projection planner.
- Keep target, weight, offset, and algorithm-required term columns as explicit required columns.
- Replace `_training_job.py:480, 567, 665, 676, 842, 1111, 1119` collects with `streaming_collect(..., profile=training_prep)`.
- Replace `safe_sink` calls in `_train_service._execute_and_sink`, `_training_job._prepare_data`, `_training_job._split_data` with `bounded_sink`.
- Make training temp parquet writes fully instrumented and memory-budgeted.
- For each algorithm path, either stream/chunk safely or fail early with a clear unsupported/memory-budget status.
- Document the honest boundary where model libraries require materialising train/eval partitions; record the materialisation as a named stage so memory cost is visible.
- Probe CatBoost GPU vRAM via the existing `_VramCheck` and refuse before launching when insufficient.
- Track MLflow run lifecycle on cancellation; cancelled training closes the MLflow run cleanly.
- Remove or consolidate one-off memory logging once shared metrics cover it.

Tests:

- CatBoost, GLM, weights, offsets, exclusions, and terms all preserve required columns.
- Categorical dtypes and feature order are preserved (overlap with Slice 8).
- Wide training datasets project away unused columns before temp parquet write.
- Memory-budget failure during training preparation reports clearly with HTTP 507.
- Metrics do not retain Polars frames or large payloads in job store.
- Algorithm paths that cannot fit safely under budget fail before loading full partitions.
- MLflow run is closed on cancellation.
- vRAM-insufficient CatBoost runs refuse before launching.

Acceptance criteria:

- Training behaves like every other large-data path: projected, cancellable, measured, and memory-budgeted.
- Training does not claim end-to-end chunk safety unless the algorithm implementation supports it.

## Slice 12: Optimiser Solve Generalisation

Problem: solve setup and auto-range now share some infrastructure, but optimiser-specific code still owns too much execution detail. The original ratebook factor-source full collect has been replaced by a projected, staged, budget-checked path; the remaining boundary is the price-contour solver API's in-memory factor table requirement.

Status 2026-05-11:

- Optimiser solve data requirements now use the shared projection planner, including the shared `ratebook_factor_required_columns(...)` helper used by both planning and route setup.
- The lazy executor no longer owns a bare `NodeType.OPTIMISER` projection branch; optimiser projection behaviour is guarded by planner-focused tests.
- Ratebook factor extraction now projects only the configured quote id and factor columns, validates missing configured columns before materialisation, writes the projected factor source through the bounded sink path, reads parquet metadata for row-count/size preflight, and rejects over-budget factor sources with a typed memory error before final collection.
- Factor extraction, validation/projection, grid build, and solver execution are named stages on the shared optimiser execution context, and stage-entry memory-limit refusals now still leave a bounded metric so failures are explainable.
- Solve setup creates a cooperative cancellation token before heavy setup begins, registers the job immediately, and prevents a cancelled/superseded setup from launching the solver worker.
- The solver worker receives the admitted setup context rather than creating an opaque context, so setup and worker metrics are retained in one bounded execution payload.
- Ratebook solve now fails loudly when configured factor columns are missing from the prepared banding source instead of silently dropping factor groups.
- Covered with optimiser execution-context tests, projection-planner tests, ratebook solve route tests, and optimiser contract tests; focused lint and type checks pass for the optimiser/projection/context surface.

Implementation:

- Express optimiser data requirements through the projection planner (Slice 3 dependency).
- Share chunk runner support where solve setup can avoid expanded materialisation.
- Instrument factor extraction, grid build, and solver setup as explicit stages.
- Ensure ratebook and online modes use the same cancellation/memory semantics.
- Replace the full-frame `factors_df` collect at `_optimiser_service.py:2858` with: row-count preflight via `scan_parquet` metadata + memory-budget check + typed error if the factor source exceeds the budget.
- Move the `NodeType.OPTIMISER` projection branch out of `_execute_lazy.py:698-719` into the planner as `OptimiserDataInputRule`.

Tests:

- Online solve and ratebook solve both pass minimal column demands.
- Factor extraction only reads configured factor columns and configured quote id.
- Grid build stage records timings and honours cancellation before solver launch.
- Repeated solve/auto-range starts cannot run duplicate heavy setup for the same graph/node.
- Ratebook factor source collection is bounded, explicitly budgeted, or rejected with a clear error.
- Chunked and non-chunked optimiser setup paths are equivalent on deterministic eligible graphs.
- The `NodeType.OPTIMISER` projection rule lives in the planner; the engine has no `if node.data.nodeType == NodeType.OPTIMISER` branch.

Acceptance criteria:

- Optimiser behaviour is governed by shared execution planning, not route-local special cases.

## Slice 13: Determinism, Fault Injection, And Long-Term Guardrails

Problem: regressions in cancellation, projection, dtype safety, and memory must be hard to introduce after the architecture lands.

Status 2026-05-11:

- Added static execution-surface guardrails: every production `_execute_lazy(...)` call outside the implementation must make an explicit `execution_context=` decision; route/deploy private execution imports have an empty allowlist; route status responses may not hide corrupt state behind `status="unknown"`.
- Added process-global Polars guardrails: streaming chunk-size mutation is now exposed through `temporary_streaming_chunk_size(...)` in `_polars_utils`, optimiser setup uses that shared locked scope, and production code is statically forbidden from calling `pl.Config.set_streaming_chunk_size(...)` elsewhere.
- Strengthened bounded materialisation guardrails: production direct `.collect(engine="streaming")` calls now need an explicit allowlist, and production `safe_sink(...)` calls are forbidden outside the compatibility helper.
- Snapshot coverage now includes the nested `ExecutionMetricsPayload` contract, not just models that reference it.
- Projection guardrails now include a per-`NodeType` coverage map and a deterministic smoke test showing projection plans are stable when graph node/edge order changes.
- OpenAPI contract snapshots now include training/optimiser cancel endpoints; the frontend API client has typed cancel wrappers and parsers accept the full backend job-status taxonomy.
- Fault-injection coverage now confirms `streaming_collect(...)` preserves typed execution cancellation and memory-limit exceptions without wrapping them as streaming incompatibility errors.
- Deliberately deferred as overkill for this local engine slice: OTel export integration, a full Polars version CI matrix, broad Hypothesis graph fuzzing, and a server-startup temp-dir reaper.

Implementation:

- Add tests that forbid new direct `_execute_lazy` call sites without an explicit context decision (Slice 0 expanded).
- Add projection planner tests for every registered node contract.
- Add route hygiene tests for cancellation/status mapping.
- Add lightweight performance smoke tests with synthetic wide and tall data.
- Document how to add a new node type with projection and chunking support.
- Add static checks for private planner imports and route-local projection bypasses.
- Add benchmark report templates for the scale gates with baseline-pinned counters.
- Add the determinism contract test suite (bit-identical, row-stable, set-equal categories).
- Add a fault-injection harness that fires cancellation during native `collect()`, between checkpoint stages, and inside reducers; assert wall-clock against the cancellation latency budgets.
- Add a Hypothesis-based graph fuzzer that generates valid pipeline graphs and asserts the planner / chunk runner do not panic.
- Add a Polars version matrix CI step (lower and upper supported versions).
- Add an OTel export integration test (spans emitted, attribute names match the schema).
- Add a server-startup orphan reaper for `haute_*` temp dirs.

Tests:

- Registry-wide contract coverage.
- New node type missing projection metadata fails a clear test.
- Metrics payload schema snapshot.
- Performance smoke tests for preview, auto-range, training prep, and sink.
- Benchmark smoke tests assert deterministic gates: columns read, rows materialised, collect count, checkpoint count, metrics payload size, temp disk budget — all baseline-pinned with a noise envelope.
- Cancellation latency budgets pass on every profile in the fault-injection harness.
- Graph fuzzer surfaces no planner panics over N runs.
- Polars version matrix passes on both endpoints.
- OTel spans round-trip the named attributes.
- Orphan reaper removes a synthetic stale `haute_*` temp dir on startup.

Acceptance criteria:

- Future changes cannot silently bypass projection, cancellation, metrics, or memory budgets.
- Cancellation latency budgets are enforced in CI, not aspirational.
- The benchmark matrix produces a reproducible report; PR review includes a counter diff.

## Open Questions (decisions made)

The previous draft deferred several decisions that this revision resolves.

- **GUI metrics display.** Decision: status JSON now (Slice 1), GUI deferred to a separate workstream tracked outside this plan. Don't bloat the engine plan with frontend scope.
- **Persisted execution trace artifact for long jobs.** Decision: useful later, but deferred. Slice 1 V1 exposes bounded in-memory/status metrics only; JSONL trace artifacts should be designed with checkpoint retention in a later hardening slice.
- **Chunk-safe node types in v1.** Decided in the Chunk Plan Contract above.
- **Polars version drift policy.** Decided in the Polars Version Floor And Drift Policy contract above.
- **OTel export.** Decided in the Logging And Telemetry Schema contract — emit when `HAUTE_OTEL_ENABLED=1`.
- **Preview explain endpoint.** Decision: deferred. Phase 9 exposes cache hit/miss and per-stage timings on the preview response itself, so a separate explain-only endpoint would duplicate the engine surface until the UI needs a no-materialisation explain interaction.
- **Process isolation for non-cooperative native work.** Decision: deferred. Listed once in the Execution Policy And Budgets contract as a future evaluation, not in two slices. Re-evaluate after Slice 11 ships and we have measured cancellation-latency-budget compliance for training.

## Open Questions (truly open)

- What numeric scale-gate budgets should we pin for RSS, disk, first-click latency, repeat-click latency, bytes read/written, and collect count on the local 10m-row benchmark machine vs the CI smoke matrix? Pin the local numbers in Slice 13 once the matrix has been run.
- Multi-tenant / multi-user behaviour: is this a one-process one-user system or not? Affects the admission-control model and the cache scope. Default assumed: one-process, multi-pipeline, one-user; revisit if multi-user lands.
- Threading model under uvicorn `workers > 1`: per-process caches and locks are the only supported configuration today. If multi-worker is needed, the cache layer becomes a file-backed store; tracked as future work.

## Suggested Order

The full list below is the north-star track. The immediate implementation track is:
Slice 0 guardrails, Slice 1 bounded metrics, Slice 2 bounded sink/collect behaviour,
Slice 3 projection planner extraction, Slice 9 preview first-click redesign, Slice 5
memory/status semantics, and Slice 6 chunk runner. The deferred-hardening items
remain useful, but they should not block the concrete execution fixes.

1. Baseline Guardrails And Conformance Harness (Slice 0).
2. Execution Metrics Surface (Slice 1).
3. Forbid Silent Fallbacks And Streaming Compatibility (Slice 2).
4. General Projection Planner (Slice 3).
5. `ExecutionContext` Decomposition And Propagation (Slice 4).
6. Memory Budgets And Abort Semantics (Slice 5).
7. General Chunked Execution Contract (Slice 6).
8. Job Lifecycle Unification And Heavy-Frame Eviction (Slice 7).
9. I/O Source Adaptor And Schema/Dtype Boundary (Slice 8).
10. Preview Performance Redesign (Slice 9).
11. Deployed Scoring And Sink Consistency (Slice 10).
12. Training Pipeline Memory Safety (Slice 11).
13. Optimiser Solve Generalisation (Slice 12).
14. Determinism, Fault Injection, And Long-Term Guardrails (Slice 13).

This order makes regressions visible before deeper refactors begin (Slice 0 + 1), removes the silent fallbacks that would invalidate every later memory claim (Slice 2), establishes the projection planner as the substrate every later slice consumes (Slice 3), refactors the context primitives so they're composable (Slice 4), then turns memory budgets and chunked execution from aspirations into enforced contracts (Slices 5–6). Lifecycle unification and the I/O contract follow because they cut across every remaining slice. Preview, deploy, training, and optimiser slices then ride on top of the now-stable substrate. The final slice locks the architecture against regressions.
