# Codebase review — 28 October 2026

A further functional review at `2c3151b0e2734d9e0ff8bfc0ffca76aa525ba1ac`, on
`astra-review`, before external users. The working tree had no tracked changes
when review began. This report supports the component roadmaps and owns no work.
It records the initial functional pass and the subsequent full-source pass requested
by the user. New work is assigned to component roadmaps; existing packages are
extended where they already own the defect.
It assigns 42 finding groups to 30 new packages and extensions of 9 existing
packages. It does not implement fixes or certify the repository as bug-free.

## Method and limits

The initial pass inspected selected workflows. The follow-up inventoried tracked
production/build source and assigned areas to the main session and two read-only
reviewers. The ledger marks **1,081 authored source files, 396,975 original lines**,
plus **40 runtime/repository configuration files, 3,588 lines**, as reviewed.
Its 1,121 full-coverage rows contain 414 distinct notes, often shared across a batch
of files (one note covers 34 files). Some notes retain pending candidates or checks.
Those records support an inventory-wide source pass and area-level review notes;
they do not establish that every executable statement was individually inspected
or that every candidate was resolved. The earlier stronger statement-level coverage
claim is withdrawn. Candidate dispositions are recorded below.

Python source was often shown in a semantic view omitting comments, blank lines and
true docstrings; comments/docstrings relevant to findings were checked at source.
**38 generated modules/assets, 26,954 lines**, were assessed through generators,
contracts and consumers. **1,218 test/documentation-support files, 698,341 lines**,
were inventoried, with relevant tests inspected selectively; neither the complete
test corpus nor the complete specification corpus was read or run.

The 2,377-record point-in-time ledger is kept outside the repository with the
reviewer's scratch files. It is not a maintained specification or future coverage
guarantee. Its SHA-256 values describe the reviewed Windows working-tree bytes, not
Git blob bytes; the cross-family check found 92 differences against Git blobs. They
must not be used as portable commit-coverage evidence.

Candidates were compared with the existing component roadmaps and the 25 October
report. The main session inspected every accepted finding's decisive source and
independently reran the behavioral witnesses, including those supplied by reviewers.
Source-only variants are labelled separately from executed failures. Existing
roadmap entries remain unresolved unless a separate delivery proves them fixed.

Probes used the installed Windows environment (Python 3.11.13 and Polars 2.0.0)
and the real Python functions, React hooks/components and stores. Deferred
promises and deterministic call barriers controlled races. Test projects and
probe files stayed outside the repository. No full backend/frontend suite,
browser journey, hosted deployment, external model registry, Linux worker/cgroup
run or production performance benchmark was performed. The foreign-Host probe
used TestClient; browser DNS-rebinding exploitability is an inference, not an
executed browser attack. The core-only CI finding, since delivered, was also measured in hosted
[main run 40754355254, optional-deps-smoke](https://github.com/PricingFrontier/haute/actions/runs/40754355254/job/123049170185):
the `uv run` step uninstalled 5 packages, installed 42, and passed with
`3 skipped in 0.18s`. The cross-family reviewer supplied this evidence and the
follow-up checked the job log.

## New findings and ownership

Priorities follow the roadmap convention: P1 threatens saved work, calculated
results or security; P2 materially affects correctness, UX or assurance; P3 covers
the limited-impact or rare-input items. The review recorded 42 groups: 13 P1, 25 P2 and
4 P3. Groups whose packages were delivered have since been removed, so the tables hold
the open ones. The first table keeps what is open of the ten initial finding groups.
The following table adds the full-source findings, including additions to existing
owners. A group is not an individual failing assertion or a release-wide blocker count.

| Priority | Trigger and observed consequence | Owning package | Evidence strength |
|---|---|---|---|
| P1 | An IDE save after assistant plan verification is overwritten by apply; the rename and post-save verification still succeed. | [ASSIST-61](assistant.md#assist-61--keep-the-authenticated-revision-through-commit) | Real temporary pipeline and transactional save; deterministic edit before commit. |
| P2 | Two independent API Input edits before identity resolution completes retain only the second edit. | [FSH-R10](frontend-shared.md#fsh-r10--retain-edits-during-input-identity-resolution) | Actual controller: expected `[false, false]`, received `[true, false]`; `onUpdateNode` surfaces the superseded edit as an error toast. |
| P1 | Cancellation of A finishes after B starts; A's reply retires B or publishes A's result under B's calculation identity. | [FSH-R11](frontend-shared.md#fsh-r11--cancellation-replies-belong-to-one-job) | Two pivot hook/store probes. Training has the same source pattern but was not separately executed. |
| P1 | Inference for `first.json` returns after the user selected `second.json` and added a table; old path/tables are written back. | [FNED-R06](frontend-node-editors.md#fned-r06--inference-stays-bound-to-its-source-and-draft) | Actual API Input editor with delayed inference response. |
| P1 | Auto-layout finishes after a code edit and restores the old code from its node snapshot. | [FGC-R05](frontend-graph-canvas.md#fgc-r05--auto-layout-cannot-overwrite-edits) | Actual node-handler hook with delayed layout. Node addition/deletion and document-switch variants are source-supported. |
| P2 | Stratified test allocation consumes class members needed by later validation, refusing feasible plans. | [MOD-R07](modelling.md#mod-r07--stratified-allocation-reserves-every-later-partition) | Real evaluation planner; holdout and five-fold examples below. |

### Additional full-source findings

| Priority | Trigger and observed consequence | Owning package | Evidence and practical limit |
|---|---|---|---|
| P2 | Invalid connect endpoints/keywords/ports are dropped or repaired; parameter inference can hide the discarded call. | [PCFG-R20](pipeline-config.md#pcfg-r20--malformed-connect-calls-are-parse-errors) | Actual strict parse/codegen versus executing original Python; four malformed call shapes. Existing helper tests preserve some wrong behavior. |
| P1 | A keyword-only factor default disappears on Save; a preamble name takes over and changes 120 to 90. | [PCFG-R21](pipeline-config.md#pcfg-r21--node-signatures-retain-their-configuration-bindings) | Actual original and regenerated functions, strict file parse. |
| P1 | Parser endpoint concatenation and multi-frame paste mint duplicate edge ids; healthy recovery can be refused and deleting one duplicate deletes both. | [NAME-10](name-collisions.md#name-10--every-distinct-connection-has-a-distinct-identity) | Real parser/codegen and actual keyboard/store paste/delete. Healthy recovery rejection follows its duplicate-id validator; malformed documents were not assumed to load normally. |
| P3 | Delete with a selected node and unrelated selected edge leaves the edge. | [FGC-R07](frontend-graph-canvas.md#fgc-r07--delete-removes-the-complete-eligible-selection) | Actual keyboard/store witness; the remaining edge stays visible and can be deleted again. Undo behavior is an acceptance requirement, not an executed assertion after the failure. |
| P1 | Pending submodel drill-in replaces a newly loaded root with the old child and mismatched navigation context. | [FGC-R06](frontend-graph-canvas.md#fgc-r06--pending-submodel-navigation-belongs-to-its-document) | Actual navigation hook and stores, delayed layout. A subsequent destructive Save was not executed. |
| P1 | Failed solve B restamps retained success A with B's identity, making old numbers appear current under a new objective. | [FSH-R12](frontend-shared.md#fsh-r12--failed-solves-cannot-relabel-retained-successful-results) | Actual store/hash/staleness helpers; preview label source checked. Publication already refuses the error state. |
| P2 | Integer regression target yields a declared long MLflow output although the native pyfunc predicts floats. | [MOD-R09](modelling.md#mod-r09--output-signatures-describe-predictions) | Real small CatBoost fit and generated signature; no remote registry enforcement failure claimed. |
| P2 | Haute classifier pyfunc's label/probability table is flattened to 40 values for 20 input rows. | [MSC-11](model-scoring.md#msc-11--pyfunc-classification-outputs-have-an-explicit-scoring-shape) | Real saved/loaded CatBoost pyfunc, feature contract and scoring adapter. Explicit pyfunc selection is affected; automatic native selection may avoid it. |
| P3 | Model card loses its name to a metric, importance method labels disagree with families, and integer PDP labels do not match scored values. | [MOD-R05](modelling.md#mod-r05--diagnostics-measured-as-the-fit-was), extended | Real card generation and model diagnostic calculations; separate from the signature's P2 contract impact. |
| P3 | Relationships target `__w=[10,20]` is overwritten by internal weight alias: means become `[1,1]`, strength 0. | [EDA-R01](explore-eda.md#eda-r01--pivot-requests-answered-right), extended | Actual Parquet, data-point resolver and relationship service; ordinary-name equivalent requires means `[10,20]`, strength 1. |
| P3 | Copied Explore node B receives cached result.node_id A for the same producer/card. | [EDA-R01](explore-eda.md#eda-r01--pivot-requests-answered-right), extended | Actual pivot service/cache. Numbers are correct; browser stores them under B, so no numerical cross-node corruption claim. |
| P2 | Training status discards eight recorded tuning progress fields. | [MOD-R08](modelling.md#mod-r08--training-status-carries-the-progress-the-worker-records) | Real JobStore and async status route. Cancel response has the same omission by source inspection. |
| P2 | Shared-flight operation TimeoutError is mistaken for a polling timeout; completed work spins until cancellation. | [ROAD-WORKER-09](background-jobs-api.md#road-worker-09--completed-operation-timeouts-are-terminal-failures) | Real SharedFlights and LatestWinsQueue with controlled failure/cancellation. |
| P1 | Project utility junction points outside: GET reads and PUT changes the outside Python file. | [API-R07](server-api.md#api-r07--utility-files-written-like-project-files), extended | Actual Windows junction and route operations in temporary directories; only project discovery injected. |
| P2 | Push rejection names origin; after dropdown changes, its Catch up/Spin off submits backup. | [GIT-R18](git-integration.md#git-r18--recovery-acts-on-the-remote-named-by-its-dialog) | Actual component/ApiError/response parser, both actions, with the remote changed mid-push. Spin off sets local work aside on a branch; a real wrong-remote mutation was not executed. |
| P2 | Returning from a failed directory to a cached parent leaves the error hiding the files. | [FNED-R02](frontend-node-editors.md#fned-r02--drafts-in-step-with-the-document-errors-with-their-detail), extended | Actual FileBrowser and settings cache, list transport substituted. |
| P2 | Use detected schema applies A's completed schema into B while B loads. | [FNED-R06](frontend-node-editors.md#fned-r06--inference-stays-bound-to-its-source-and-draft), extended | Actual DataInputEditor/hooks, API transport substituted. |
| P2 | Deployment proves a literal-only select preserves request order, then fails a three-quote request with one row. | [EXEC-R09](execution-engine.md#exec-r09--column-free-selects-are-not-row-local), extended | Actual row proof and score_graph. Existing chunk row-count corruption stays P1; this added deployment failure is loud. |
| P2 | Late GLM dispersion estimate for A overwrites manually chosen theta 7 in newer target B with 3. | [FMOU-R06](frontend-modelling-optimiser-ui.md#fmou-r06--dispersion-estimates-belong-to-the-initiating-training-draft) | Actual GLMTargetConfig, only the estimate promise substituted. |
| P2 | JSON omits an all-null nested pricing object while NDJSON returns pricing:null for the same quote. | [DEP-R17](deploy.md#dep-r17--json-and-ndjson-preserve-the-same-output-document) | Real generated FastAPI app, request shredder, scorer, assembler and materializer; no scorer mock. Batch variant is source-supported. |
| P2 | From project root, rating/modules edits are skipped even when rating/main.py is the configured importer. | [API-R11](server-api.md#api-r11--module-watching-follows-the-configured-pipeline-directory) | Real discovery/parser/dependency index and watcher event handling; event transport/publish capture substituted. No subsequent save-overwrite claim. |
| P2 | A utility file load replaces a newer selection and can discard the old file's draft typed after its initial flush when autosave later fails. | [FGC-R08](frontend-graph-canvas.md#fgc-r08--utility-navigation-preserves-drafts-across-pending-reads) | Actual UtilityPanel/debounce, textarea editor and transport seams; two controlled interleavings independently reproduced. |
| P2 | A real degraded document's blocked downstream node throws before its recovery inspector because its unavailable parent has no executable input identity. | [FGC-R09](frontend-graph-canvas.md#fgc-r09--recovery-inspection-does-not-require-executable-input-identities) | Real backend recovery DTO, browser parser/adapter, GraphProvider and NodePanel; no frontend mocks. |

The first implementation pass should address the P1 packages before opportunistic
cleanup. Each package includes the expected behaviour, scope, regression scenarios
and dependencies. These additions are not assigned delivery rounds; adding them
does not silently reorder the existing delivery plan.

## Reproduction details

### Assistant apply and local app

The assistant witness makes a rename plan against a real temporary pipeline,
then changes its description from another writer after `_prepare_apply` returns
and immediately before the real `_commit`. The editor revision changes from
`c49c785d49d705f1` to `83e3425ee57f4052`. Apply returns one applied operation,
the rename is present, and the external description is absent. The probe changes
only call timing; it uses the normal preparation, save and verification code.

The app witness builds a temporary release using the ordinary workbench test
fixture and creates it with a local principal. Requests to
`http://attacker.example:8001` read `/api/app/release` and POST `/api/app/price`
with the same Origin and same-origin fetch metadata. Both succeed. No real
user data, network listener or hosted service was accessed.

Decisive sites: `PipelineApplicationService._prepare_apply`, `_commit` and
`apply` in `src/haute/assistant/_application.py`; `create_app` and `_cross_site`
in `src/haute/_workbench_app.py`; `handle_app_serve` in `src/haute/cli/_app.py`.
The save service already has a conflict precondition, and
`LocalTrustedHostMiddleware` already implements the local Host boundary.

### Evaluation

The evaluation witnesses use canonical row order `0..99` and seed 42:

| Source classes | Requested plan | Actual refusal | Feasible allocation |
|---|---|---|---|
| 97 negative / 3 positive | Test 50%; single validation 10% | Development has only one positive; minimum two required. | Test: 49 negative + 1 positive; validation: 9 negative + 1 positive; training keeps the last positive. |
| 94 negative / 6 positive | Test 50%; five-fold validation | Development has three positives; minimum five required. | Test: 49 negative + 1 positive; development: 45 negative + 5 positive, enough for every fold. |

These assert allocation behaviour without fitting a model. The defect is in the
reserve calculation and initial allocation, not an absence of enough source rows.

### Frontend races

Five focused Vitest assertions deliberately fail on the reviewed source:

- Request-input controller: two independent column deselections lose the first.
- Pivot cancellation: late cancelled A removes B.
- Pivot cancellation: late completed A is stored under B's job/calculation identity.
- API Input editor: late inference writes the old source and tables.
- Auto-layout: late geometry result restores old node code.

The tests control the async seam and exercise the real local state transitions.
They are review witnesses kept outside the repository, not skipped or permanently
failing additions to the normal suite. Implementation must convert their
observable assertions into ordinary regression tests in the owning modules.

## Functional coverage

The scratch inventory and batch review notes cover node implementations and editors; parser, codegen, recovery and
save; submodels and identities; execution, projection, lineage, chunking, memory,
caches and workers; trace and explanation; Explore/profile/pivot/chart; all model
families, fitting, diagnostics, tuning, MLflow and scoring; optimiser inputs,
solves, frontier, apply and artifacts; workbench forms, pricing and quote lifecycle;
assistant providers, tools, orchestration and UI; Git, project files and hosted
storage; server routes/schemas; CLI, deploy/build/release/CI and engineering scripts.

No-new-finding areas are represented in the scratch ledger. Batch coverage labels
do not close unresolved hypotheses or prove every state correct.
No production code was changed while recording these findings.

## Candidates deliberately not promoted

The ledger and scratch notes were swept for candidate hypotheses, pending checks,
duplicates and negative probes. The rows below dispose of each distinct hypothesis;
repeated notes attached to a batch of files are consolidated. **Unverified** means
no defect is claimed: the row states what observation would confirm it. A narrow
negative probe does not rule out every other input or caller. None of these rows
adds a finding group or changes the 42-group count.

### Unverified and source-screened candidates

| Candidate and source | Disposition and confirming observation |
|---|---|
| `frontend/src/panels/editors/_DatabricksSelector.tsx::CatalogTablePicker` / `TargetTablePicker` refresh functions | **Unverified.** Defer catalog A's schema/table response, switch to B, complete B then A, and observe an A option displayed or committed under B. A source observation of missing fencing is not itself the UI witness. |
| `src/haute/routes/_optimiser_frontier.py::select_point` | **Unverified.** Pause selection after it captures generation N, complete a same-job recompute to N+1, then resume selection. Confirmation requires the old point to overwrite the new generation's selected result/artifacts or a response claiming N+1 while containing N. FMOU-R05 owns browser request lifecycle, not this server interleaving. OPT-R04 owns the recompute product decision, not a proved fix for it. |
| `src/haute/_git_remote.py::fast_forward_pair_from_tracking` and `_git_core.py::_rewrite_tree` | **Unverified.** Use a real clean ledger branch with a held/locked tracked file whose remote content changes; fast-forward the same symbolic branch and compare ref tip, working-file bytes and compensation outcome. Show stale held bytes restored over a successfully advanced commit, or another concrete inconsistency. No accepted Git package currently proves this closed. |
| `src/haute/cli/_train.py::handle_train` | **Unverified.** This is a copied three-file note also attached to `_app.py` and `_gpu_setup.py`; the import is in `_train.py`. Execute a valid training script requiring its own module registration, e.g. a dataclass with postponed annotations or introspection/pickling of a script-defined class, through `handle_train`; compare ordinary Python execution. Confirmation requires a registration-specific failure before/while the otherwise valid job runs. No new app/GPU defect is implied. |
| `src/haute/deploy/_request_rows.py::check_lookups` and join proof | **Unverified.** First establish a strict-parser-accepted join with null-equal matching. Give its lookup two null keys and a request a null key; verify deployment's uniqueness proof accepts it while actual scoring duplicates a quote. If the parser rejects that join option, this particular trigger is unreachable; that check was not recorded as completed. |
| `src/haute/_workbench_form.py::FormSpec._brings_quote_details_up_to_date` | **Unverified.** Feed an actual form-loading or save route a legacy quote-details column whose `id` is a list/object, reaching `set(ids)` before conversion. Confirm an uncaught TypeError/500 instead of the promised validation/document diagnostic, and identify the supported entry point. A direct helper exception alone would not establish user reachability. |
| `src/haute/_config_builder.py::_resolve_node_config` / `_build_node_config` | **Partly duplicate / otherwise unverified.** The sidecar-backed branch dropping extra decorator keywords is exactly **PCFG-R11**. If this note intended another inline/non-sidecar form, first name that decorator/key and compare strict parse, regenerated source and live registration; confirmation requires a silently dropped/refashioned accepted argument. Do not imply PCFG-R11 covers every unstated variant. |
| `frontend/src/app/quoteValues.ts::readDraft` / `isPendingWrite` | **Unverified.** Produce a browser draft with null/array `values` or `places` through a supported previous-version/current persistence path, then open/restore the quote and observe a crash or incorrect write. Manually corrupting localStorage establishes parser permissiveness only; a realistic producing path remains missing. |
| `frontend/src/panels/OptimiserPreview.tsx` same-job `xConstraintIdx` | **Not reachable through the current browser recompute workflow as documented.** OPT-R04 explicitly says recompute has no browser caller and browser users get only inline generation 0; a new job changes the reset dependency. This does not disprove external API recompute while a browser is open. To reopen the UI candidate, demonstrate a supported same-job generation with fewer axes reaching the mounted preview and then an out-of-range axis/error. |
| `frontend/src/panels/optimiser/useOptimiserScenarioData.ts` / `OptimiserScenarioPanes.tsx` | **Unverified.** Supply a valid configured scenario preview with a missing/null objective or constraint cell through the real preview parser and pane; show a zero-valued curve/summary where its input contract requires missing/error. Compare `optimiserScenarioStats` handling. First distinguish allowed missing input from data the upstream contract already refuses. |
| `src/haute/_polars_call_shapes.py::is_pl_dtype_reference` / `is_typed_literal_list` | **Unverified; no exact duplicate established.** These shape recognizers require the spelling `pl`. Compare a parser-accepted equivalent Polars alias/dtype/literal-series expression with canonical spelling through the actual chunk/row-proof consumer; confirm a material unsupported/fallback result. PCFG-R19's `match`/type-alias support-code inventory is not automatically an owner for module-alias recognition. No wrong numerical result was established. |
| `frontend/src/utils/graphHelpers.ts::liveHandles` / `filterIncomingEdges`, consumed by `useWebSocketSync` | **Unverified warning; edge-loss variant not established.** This is a copied 13-file note attached to `buildGraph.ts`, which does not classify handles. The inspected sync caller uses `rejectedEdges` diagnostically and adopts the original `newEdges`. A confirming UI test loads a valid Workbench Output `table__…` target handle through actual sync and observes a false warning while asserting the edge remains. Do not describe dropped wiring. |
| `frontend/src/api/assistant.ts::streamAssistantMessage` | **Unverified.** Confirm an actual supported server/proxy emits CRLF SSE, then split the CR and LF across stream chunks and assert complete events/terminal outcomes. The client normalizes each decoded chunk before buffering; no actual server-framing failure was recorded. LF-only production output does not exercise this trigger. |
| `frontend/src/api/client.ts::request` / retry policy | **Unverified, hypothesis underspecified.** Name a real retry-enabled PUT/DELETE endpoint with a non-idempotent effect, let its mutation commit while losing the response, and observe a duplicate effect or incorrect retry result. POST is not retried by default; project-file/workbench writes have explicit zero-retry paths. Do not label every mutation unsafe. |
| `frontend/src/api/projectFiles.ts` XHR upload / `FilesPanel` | **Unverified.** Stall an actual upload transport indefinitely, check whether the panel remains stuck and whether its cancel/close action retires transport and UI ownership. Lack of an automatic timeout alone is not a confirmed failure if intentional long uploads remain cancellable. |
| `frontend/src/app/useQuoteListStore.ts::more` / `load` | **Unverified.** Hold old-list `more()`, load another list, and try its pagination before/after the old request settles. Confirm new pagination remains blocked or old completion clears a newer loading state. This is distinct from the archive/restore query adoption delivered on 29 October. |
| `ExplorePivotsConfig` / `PivotFormulaSection` confirmation continuations | **Unverified; previous ownership statement lacked an exact package.** Open a destructive pivot/formula confirmation, apply an independent edit or document switch while it is open, then confirm and inspect current config. Confirmation must show old captured arrays replacing newer state or deleting a no-longer-current target. FSH-R07's identity-resolved creation fences and FNED-R02's committed drafts do not by themselves dispose of this separate continuation. |
| `frontend/src/panels/modelling/TermTableView.tsx` state, `EBMTermsTab` / `TBoostTablesTab` keys | **Unverified.** Keep the same mounted term key, replace its result with different axis count/levels, and observe an invalid fixed index/axis or wrong slice. First establish the real parent lifecycle preserves that component across results; an isolated rerender is insufficient if production remounts it. |
| `frontend/src/stores/useSettingsStore.ts::loadPipelineSettings` | **Unverified.** Complete newer load B before older A so `_confirmedPipelineSettings` could regress, then fail a settings save and inspect rollback. Confirmation requires the UI to restore A despite B being the latest accepted settings. The existing save-completion fence may rule out some interleavings; no completed witness is recorded. |
| `scripts/benchmarks/benchmark_optimiser_pipeline_timing.py::_process_memory_mb` | **Unverified manual-tool measurement issue, not a product claim.** Run or unit-check the helper on Linux/macOS with known `ru_maxrss` units and compare the returned MB to an independent RSS value. Record the affected benchmark output. The fact it is auxiliary does not prove its numbers correct. |
| `scripts/benchmarks/pr-227-consumer-io-benchmark.py` / `pr-227-implementation-benchmark.py` path setup | **Unverified runnable-tool impact.** Invoke the historical script from a clean checkout with its documented minimum inputs and show the doubled `scripts/scripts` import or another concrete file-resolution failure. No workload ran. This is a manual benchmark caveat, not a runtime pipeline finding. |
| `_analysis_results`, `_artifact_*`, `_hashing`, `_input_providers`, `_model_source`, `_stat_gated_cache` batch | **Unverified and underspecified.** The note names no cache key, omitted identity or wrong outcome. Minimum next step: identify one actual producer/consumer cache identity, change one supported input/provider/artifact attribute while keeping that identity, and observe stale data returned. Do not manufacture a precise bug or duplicate from this batch reminder. |
| `src/haute/_builders.py` | **Unverified and underspecified.** No operation, arithmetic boundary or expected answer was recorded. A confirmation must first name a valid online/ratebook config and concrete scalar/vector inputs, then compare the actual builder/runtime result with a hand-computed expected answer. No completed reproduction or rebuttal was recorded. |
| `src/haute/_json_shred/_inference_filter.py` native filter and inference walker | **Unverified.** Feed identical bounded nested/mixed arrays to native filtering and the Python inference path and compare complete inferred tables/types. Confirm a valid field/table lost or an invalid one invented, not merely different internal ordering. Accepted JSON-R07 concerns malformed ancestor skip accounting, not automatically this inference parity concern. |
| `src/haute/_pipeline_revision.py` / `_sidecar.py` | **Unverified and underspecified.** Identify the exact serialized state/path case involved; edit it through a supported writer and show a stale revision accepted, or resolve two case spellings and demonstrate incorrect graph/artifact adoption. PCFG-R19 already owns capture/revision re-reads, but the note does not prove these unspecified variants duplicate it. |
| `src/haute/_submodel_instances.py::rewrite_boundary_input_names` / Workbench Output bindings | **Source-screened for Workbench Output; otherwise unverified.** The output variant's target-table handles are not source executable input names, so a missing source-name rewrite is not shown to break that contract. A distinct Workbench Input variant requires a valid shared submodel/port rename whose real flattened runtime selects the wrong/missing frame. SUB-R04, since delivered, owned Load File's second input, not every hypothesized workbench selector. |
| `_submodel_instances.py` merged support code / `_support_code_names.py` | **Ordinary strict-parse conflict is guarded; broader variant unverified.** A confirming case must pass strict parsing with two valid occurrence scopes and then show support-name rebinding after flatten/execution. Existing ordinary preamble rejection is not evidence that dynamic imports/all reuse scopes are safe; the note contains no such witness. |
| `src/haute/modelling/_ebm.py` grouped categorical axis | **Unverified.** Fit/load a real EBM with multiple category labels sharing one bin; compare exported term labels/scores with native prediction for every grouped level. No completed reproduction or rebuttal was recorded. No GPU issue follows from this shared note. |
| `src/haute/_rating.py` null-factor/miss handling | **Unverified contract question separate from accepted `value` collision.** Run a valid table containing a null level through ordinary lookup with each declared miss policy, compare with the specified null/missing behavior, and show an incorrect match/default/refusal. The note already says nulls are deliberately treated as misses; it does not prove that policy violates the contract. |
| `src/haute/_api_input_schema.py` typed codec | **Partly duplicate / otherwise underspecified.** Persisted editor-only `origin`/`key` belongs to FNED-R01/PCFG-R08. Any other claim must first identify an exact unknown key or valid level/path shape, then demonstrate accepted data lost on codec round trip. The general note gives no other concrete case. |
| `.github/workflows/performance.yml` execution-engine-platform job | **Unverified.** Inspect or execute the exact macOS locked install and named certification tests, showing a required engine fails import/is skipped unexpectedly. No macOS execution or native dependency failure was recorded. Existing pin/coverage packages do not prove this platform-specific case. |
| `hatch_build.py` frontend freshness input closure | **Unverified residual suspicion.** Identify a real imported frontend build dependency omitted from the hook's closure, modify it, then demonstrate a build incorrectly reuses stale output. The source note supplies no omitted file. The hook's machine-specific Node fallback, a separate defect, has been removed. |
| Frontend scratch report; `SchemaTableCard.tsx` search state | **Unverified.** Apply a search while the schema exceeds its search-control threshold, refresh to fewer columns without remounting, and observe hidden search state continuing to filter with no visible way to clear it. The full report explicitly left this without a demonstrated transition. |
| Frontend scratch report; `frontend/src/panels/modelling/glmTerms.ts` override maps | **Unverified.** Use a valid feature named `__proto__` through actual term creation/rename/remove and serialize config; show loss or wrong lookup caused by assigning to a plain object. First establish that the feature name is permitted end to end. No special-name witness was executed. |
| private legacy `SplitConfig` Boolean grouping | **Not reachable through the canonical public evaluation path:** its group-key mechanism differs. This is a narrow public-path disposition, not a claim that every private caller is safe. |
| Existing report; requested trace columns / full-materialisation cache key | **Unverified:** demonstrate two valid trace requests that legitimately share the cache identity yet require different materialised columns, then show the second trace wrong/missing.  |
| Existing report; source projection transparent-chain helper | **Unverified beyond the tested case:** the recorded schema-dependent `head` witness did not fail. A different accepted argument-dependent chain must show wrong actual executor rows before promotion. Do not generalize one passing probe into universal unreachability. |
| SQLite duplicate result-column names / `_source_cache.py` publication | **Not reachable as a successful corrupted cached result in the measured path:** duplicate schema is refused before publication. This does not claim every direct helper caller rejects; a supported consumer bypassing that guard would be the confirming counterexample.  |
| `_json_shred/_runtime_storage.py` source proof/freshness | **Unverified beyond that negative probe:** ordinary same-size modification was detected. Confirmation requires a concrete supported mutation with unchanged accepted proof that returns stale rows. No general claim that all same-size/open-writer changes are impossible. |
| `frontend/src/panels/optimiser/solveActions.ts` solve request omits the active source | **Not a defect:** `solveActions` and the backend provenance path intentionally use the batch scenario; absence of an active-source field is not lost source identity. This is separate from FSH-R12's retained-success identity bug. |
| Persisted trained-handle freshness after source/model changes | **Unverified and underspecified.** The batch note named no exact package or transition. Confirm through an actual restore/load that a stale persisted model handle survives a supported source/model change and is reused incorrectly. |
| Raw pricing-error redaction in `src/haute/_workbench_app.py` | **Unverified and underspecified.** DEP-R15 owns generated-container error envelopes, but its scope does not establish ownership of this workbench hypothesis. Identify a supported pricing failure and show an externally returned sensitive/raw detail that the error contract forbids; no concrete payload was recorded. |

### Candidates already owned elsewhere

| Candidate scope | Disposition |
|---|---|
| uncontrolled Rating/pivot drafts | Duplicate **FNED-R02**. |
| percentages and auto-range | Duplicate **FMOU-R04**. |
| save flush, coarse graph creation fence, scoped edited marker | Duplicate **FSH-R07**. |
| saved-document sync | Duplicate **FSH-R05**, with **API-R08** for server saved-document echo/fingerprint. |
| utility error wording | Duplicate **FGC-R04**; distinct accepted read/draft race remains FGC-R08. |
| Scenario Steps coercion / Optimiser Apply alias mode | Duplicate **FNED-R01**. |
| rating permissive numeric parsing / grids | Duplicate **FNED-R04**; grid/helper duplication belongs **FNED-R03/FNED-R05**. |
| assistant undo/state fencing | Duplicate **ASSIST-60**. |
| typed allowed-values onto older form | Duplicate **WB-38**. |
| Unicode line splitting | Duplicate **PCFG-R18**. |
| recurring Git failures/compensation | Duplicate compensation mechanisms are **GIT-R05**. These broad notes do not establish any additional specific exception path. |
| non-SQLite form insertion | Duplicate **WB-10** for non-SQLite form insertion (the scratch spelling WB-R10 is not another package). The note gives no additional concrete database defect. |
| sandbox project globals | Duplicate **PCFG-R04**. |
| match/type-alias support binding | Duplicate **PCFG-R19**. This does not settle the separate Polars module-alias shape candidate. |
| Assistant bounds, blocking tools, factual context, tool hygiene and provider handling | Duplicates **ASSIST-55**, **ASSIST-57**, **ASSIST-58**, **ASSIST-59**, **ASSIST-54** respectively. The recipe-consolidation note names no additional behavior. |
| CLI/init/deploy existing gaps | Duplicates **DEP-R11** for impact alignment, **DEP-R12A** for build ownership, **DEP-R09** for dependency closure, and **CLI-R01**, **CLI-R04**, **CLI-R06** for init overwrite, TOML mutation and hook behavior. The scratch shorthand DEP-R12 predates its split; generic CLI errors/eager reads name no additional concrete case. |
| inactive source-switch branch | Duplicate **EXEC-R14**. |
| Literal-only chunked selects | Duplicate **EXEC-R09**; the deployed request-order witness is included in its accepted extension above. |
| Model diagnostic weights | Duplicate **MOD-R05**. |
| Hosted sign-in headers | Duplicate **WB-25**. |
| Asynchronous assistant tool blocking | Duplicate **ASSIST-57**. |

### Pending notes resolved by accepted findings

These are not rejected hypotheses. The finding tables above already give their
measured evidence and owners; this mapping closes stale candidate wording in the
scratch notes. Package splits use the new owners without adding finding groups.

| Earlier candidate note | Accepted owner |
|---|---|
| dispersion caller ownership / estimate queued | **FMOU-R06** for same-mounted draft identity; separate helper error-contract wording is **FSH-R06**. |
| rejection remote “candidate pending” | **GIT-R18**. |
| cancellation caller / result consumer identity | **FSH-R11**. Native polling's captured-job guard is not itself a new defect. |
| request-input edits / explicit Auto Layout | **FSH-R10**, **FGC-R05**; mixed Delete **FGC-R07**. |
| delayed drill-in | **FGC-R06**. |
| utility async read/save/switch | **FGC-R08**. |
| blocked recovery inspector | **FGC-R09**. |
| API Input / Data Input schema identity | **FNED-R06**. |
| cached file browser error | **FNED-R02**. |
| cached pivot response consumer node identity | **EDA-R01**; numbers remain correct as recorded in the accepted report. |
| tuning status fields | **MOD-R08**. |
| edge ID collisions | **NAME-10**. |
| failed new solve identity | **FSH-R12**. |
| malformed connect extraction | **PCFG-R20**. |
| keyword-only defaults | **PCFG-R21**. |
| pyfunc classifier output shape | **MSC-11**. |
| literal select row proof | **EXEC-R09**. |
| predicted output signature dtype | **MOD-R09** after requested split; formerly bundled MOD-R05. |
| “PDP integer grids … pending contract test” | **MOD-R05**. The copied note belongs to `modelling/_metrics.py::compute_pdp`, not `_gpu.py` or `_shap.py`; it is not a separate unresolved GPU contract concern. |
| completed TimeoutError retry loop | **ROAD-WORKER-09** after requested split; formerly bundled ROAD-WORKER-07. |

The training-import note was attached to a batch including `cli/_app.py`, but its
actual source is `cli/_train.py::handle_train`. The table-handle note attached to
`utils/buildGraph.ts` belongs to `utils/graphHelpers.ts` and its sync caller. The
“pending contract test” note attached to `modelling/_gpu.py` refers to the accepted
integer-PDP item in `modelling/_metrics.py::compute_pdp`; it identifies no separate
GPU defect. The cross-family reviewer did not check that PDP item.

Read-next reminders and old file reservations are review bookkeeping, not additional
bug hypotheses. Later coverage records supersede the reserved frontend/CSS spans,
but they do not establish that every related test was inspected. The scratch sweep
records the disposition of all 424 distinct note texts, including shared batch notes.

## Validation of these documentation changes

- `uv run pytest tests/test_docs_accuracy.py -q --no-cov`: **60 passed**, two
  dependency deprecation warnings, **14.73 seconds** after the cross-family follow-up.
- Before this follow-up, after the final recovery-inspector package and same-family corrections,
  the active-roadmap completeness test passed again: **1 passed**, the same two
  dependency warnings, 5.57 seconds.
- `uv run ruff check tests/test_docs_accuracy.py`: passed.
- `uv run ruff format --check tests/test_docs_accuracy.py`: passed.
- `git diff --check`: passed.
- Inventory reconciliation: every assigned source/config row has a coverage label.
  Batch-level notes and unresolved candidates limit that claim; generated-contract
  assessment and targeted test evidence are recorded separately. Hashes use local
  Windows working-tree bytes, not Git blobs.
- Targeted generated IO contracts: 34 tests passed, two dependency warnings.
- Real behavioral witnesses are described above; expected-failing scratch tests
  demonstrate still-open defects. No full backend/frontend test suite was run.

Before this follow-up, the documentation delta received **APPROVED** from a second
read-only Codex session of the **same model family** (`gpt-6-astra`, `xhigh`; thread
`01a18333-1e39-7b51-ac2c-9fcf2f76843b`). It requested separate classifier label and
probability assertions and a concrete P2 preview fix independent of reuse migration.
The latter, since delivered, was split from SUB-R01, which retains its original P3 scope
and must pass that regression. This was same-family review, not cross-family validation.

The subsequent **cross-family review by Claude** checked the decisive code for all
42 finding groups and reported no false positives; it did not check MOD-R05's integer
PDP-label item. It reproduced MOD-R07, PCFG-R21, the parser half of NAME-10 and two
findings since delivered, and supplied the hosted evidence for the core-only smoke finding, since delivered. This documentation
follow-up incorporates its coverage qualifications, candidate dispositions, package
splits, priorities, ownership, ordering and link corrections. Neither review approval
claims that the recorded production defects have been fixed.

Production code is unchanged. The only test-file edit registers this supporting
report in the roadmap inventory. Failing scratch probes are witnesses of unfixed
production defects, not newly failing tests added to the repository. Implementation
packages specify the corresponding permanent regression tests.
