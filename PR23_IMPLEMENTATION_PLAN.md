# PR #23 Implementation Plan

Scope: all confirmed findings in `PR23_REVIEW_RESULTS.md` plus confirmed/plausible additions in
`PR23_REVIEW_CONTINUATION.md`.

Guiding rules:
- Start every bug with a failing test or a minimal repro test. If the current working tree already passes because a
  fix landed during review, keep the test and mark the issue closed in the implementation log.
- For each issue or tightly coupled issue bundle, use two agents when executing: one developer agent owns the patch and
  one reviewer agent verifies the patch, test coverage, and regressions.
- Prefer fail-loud behavior over quiet fallback. Do not preserve wrong behavior for compatibility unless the plan calls
  out an explicit migration path.
- Keep fixes small enough to review. Bundles below are grouped only where fixing separately would duplicate tests or
  touch the same state machine.

## Phase 0 - Baseline And Tracking

1. Create an implementation ledger.
   - Add a temporary checklist, or extend this file, with status columns: `open`, `test-red`, `fixed`, `reviewed`,
     `verified`.
   - Map every issue below to a test file and source owner.

2. Reconcile current working tree.
   - Run the focused tests already added during review.
   - For each issue, first add or locate a regression test that fails on the buggy behavior.
   - If a test already passes, inspect the code and mark the issue as already fixed only when the test directly covers
     the reviewed failure mode.

3. Establish broad verification commands.
   - Backend unit slices: `uv run pytest` with targeted files per bundle.
   - Frontend unit slices: `npm run test -- <test-file-or-pattern>`.
   - E2E smoke listing/run as needed: `npx playwright test --list --grep "@smoke"`, then targeted smoke if frontend
     behavior changed.
   - Lint on changed files: `uv run ruff check ...` and existing frontend lint/typecheck command if configured.

## Phase 1 - Security And Write-Safety Blockers

### 1. Local session auth bypass and TrustedHost hardening

Issues:
- SEC-1: `Host: testserver` bypasses HTTP and WebSocket local session auth.
- SEC-2: IPv6 binds set trusted hosts to `*`.
- SEC-3: supported non-loopback remote UI binds get 403 on POST because Origin allowlist does not include the served
  host.
- SEC-4: non-ASCII token currently returns 500 instead of 403.
- CLN-8: `HAUTE_TRUSTED_HOSTS` literal is duplicated.

Developer plan:
- Remove production auth bypass based on `Host: testserver`.
- Make tests supply `HAUTE_LOCAL_SESSION_TOKEN` through headers/query instead of relying on magic host bypass.
- Move shared security constants into `_local_security.py` or a small security config module.
- Build trusted-host config that distinguishes loopback IPv4, loopback IPv6, wildcard binds, and explicit public hosts.
- Extend local Origin checks for configured remote bind origins without opening arbitrary origins.
- Make token comparison reject invalid/non-ASCII input as a normal auth failure.

Tests first:
- HTTP API with `Host: testserver` and no token returns 403.
- WebSocket with `Host: testserver` and no token is rejected.
- TestClient fixtures still pass by providing token.
- `::1` does not require wildcard trusted hosts.
- `--host 0.0.0.0` accepts the intended served origin and rejects unrelated origins.
- Non-ASCII token returns 403, not 500.

Reviewer checks:
- No production-only test carve-outs remain.
- Remote bind support does not create an arbitrary-Origin or arbitrary-Host opening.
- Existing local browser flow still receives a token.

### 2. Sink output path clobber

Issues:
- SNK-1: sink outputs reuse input-path "existing files win" resolution and can overwrite a project-root output from a
  nested pipeline.
- CLN-12: `execute_sink` double-applies `_resolve_sink_path`.

Developer plan:
- Add an output-specific resolver that never prefers an existing alternate location over the configured output location.
- For API graphs with `project_root`, resolve relative sink outputs against the pipeline file's directory when
  `graph.source_file` is present; otherwise use the project root.
- Keep project-root containment checks.
- Remove the double normalization once the output resolver owns extension handling.

Tests first:
- Nested pipeline `pipelines/main.py` writing `out` resolves to `pipelines/outputs/out.parquet` even when
  `outputs/out.parquet` exists.
- Absolute paths outside project root fail before execution.
- Direct executor callers without `project_root` keep historical explicit-path behavior.

Reviewer checks:
- Output resolver is not reused for input reads.
- No path traversal or symlink escape.

### 3. Git branch remote/local consistency

Issues:
- Existing top-15: `archive_branch` mutates remote before local checkout/rename can fail.
- Phase 3: `delete_branch` deletes remote before local checkout/delete can fail.
- GIT-1: `pushed`/`push_error` soft channel is dead; offline Save commits then raises, retry says no changes.
- C1.3/B1: switch/save flows now raise on push failures in places the UI cannot represent.
- CLN-9: dead `PROTECTED_BRANCHES` alias.

Developer plan:
- For archive/delete of current branch, perform local checkout validation before mutating remote. If a dirty checkout
  would fail, fail before remote branch operations.
- Ensure backup tags are created/pushed before destructive remote operations where appropriate, but after local
  preconditions are known good.
- Decide one push-failure contract per UI action:
  - destructive branch operations fail loudly before destructive remote mutation;
  - save/submit can either fail before commit or return committed-but-unpushed state with actionable UI fields.
- Remove or deprecate dead response fields only after frontend contract is adjusted; otherwise wire them through.
- Remove dead `PROTECTED_BRANCHES` alias or make it impossible to monkeypatch accidentally.

Tests first:
- Archive current dirty branch fails with remote unchanged.
- Delete current dirty branch fails with remote unchanged.
- Remote backup tag push failure does not delete/rename branch.
- Offline save response is either no local commit, or a clear committed-but-unpushed response shown by frontend.
- `PROTECTED_BRANCHES` monkeypatch cannot silently bypass runtime protected branch config.

Reviewer checks:
- No destructive remote operation can happen before local preconditions are validated.
- UI state after push failure is not a dead end.

## Phase 2 - Projection, Parser, And Save Integrity

### 4. Projection rename and fan-in right-join correctness

Issues:
- PRJ-1: rename demand drops required source columns.
- Phase 3: right-join recovery demands left-origin columns from the right parent.

Developer plan:
- For literal `df.rename(mapping)`, keep rename source keys required whenever the rename call will execute, unless the
  analysis deliberately widens to full width.
- Distinguish column provenance from row preservation in `join_parent_demands`.
- For missing `inputs_by_parent` coverage that cannot be proven, fail loudly in strict mode or widen to full width rather
  than assigning remaining columns to a preserved parent.

Tests first:
- Rename source is demanded even when downstream does not demand the renamed output.
- No-op rename behavior remains safe.
- Right join with incomplete parent contract does not demand left-origin column from right parent.
- Existing left-join suffix demand tests still pass.

Reviewer checks:
- The fix does not reintroduce post-rename names into parent demand.
- Ambiguous fan-in contracts fail loud or widen, not silently misproject.

### 5. Parser fallback fail-loud and body extraction

Issues:
- PRX-1: fallback parser treats comment-ending backslash as continuation and drops connects.
- PRX-2/PRX-3: fallback parser aborts on `)` inside comments in multiline calls or wrapped def signatures.
- PRX-4: fallback parse + save empties code for sidecar-config nodes.
- PRX-6: `_code_extraction` strips user imports and `with open(...)` bodies.
- Phase 3: healthy AST body extraction uses `splitlines()` and can shift around form-feed style line breaks.
- EFF-2: fallback parser repeatedly rescans source from index 0 and can block the event loop on large broken files.

Developer plan:
- Use Python-token-aware scanning for comments/strings in fallback parser rather than ad hoc line/backslash logic.
- Extract function bodies using `source.split("\n")` or AST end offsets in a way consistent with AST line numbering.
- For sidecar-config node fallback, extract current code from the parsed body just like the healthy path.
- Preserve user imports and arbitrary `with open(...)` code unless generated boilerplate is positively identified.
- Move heavy fallback parsing/file-watcher parse work off the event loop.
- Add a bounded parser classification/cumulative-line index to avoid O(anchors x file_size) rescans.

Tests first:
- Broken file with `# path C:\foo\` before `pipeline.connect(...)` keeps the edge or fails loudly.
- `)` inside comments in multiline connect/decorator does not abort fallback parse.
- Black/ruff-wrapped decorated def recovers in fallback parse.
- Syntax error elsewhere plus dataSource/scenario/rating/modelScore sidecar node preserves user code in GUI/save.
- External-file body with user `with open(...)` and imports round-trips.
- Healthy AST parse with form-feed before decorated function extracts the correct body.
- Large broken generated file parse does not run synchronously in async watcher.

Reviewer checks:
- No silent graph or code deletion paths remain.
- Fallback is still conservative and fails loudly when unsupported.

### 6. Recursive submodel sidecar ownership

Issues:
- Phase 3: recursive submodel config sidecars with same sanitized label/type overwrite each other.
- B1.5: dissolve submodel delete path narrowed too far for hand-authored valid submodel paths.
- CLN-5: duplicated save/delete validators.

Developer plan:
- Define a unique sidecar namespace for embedded submodels, or enforce global uniqueness across parent plus all nested
  submodel config-backed nodes before writing.
- Prefer fail-loud collision detection before writing any files.
- Share path validation for generated module writes and module deletes, with parameters for allowed shapes.
- Decide whether hand-authored nested module paths are supported. If they parse, dissolve should not block the graph
  rewrite solely because delete is skipped.

Tests first:
- Two nested submodels with same label/type fail before write, or write distinct sidecar paths by design.
- Collision rollback leaves all existing config files unchanged.
- Dissolve of supported hand-authored submodel paths completes or fails with a clear preflight error before mutation.
- Delete path validator and write path validator share traversal/symlink/root behavior.

Reviewer checks:
- No `dict.update(...)` overwrite of sidecar configs without collision detection.
- Save transaction remains atomic.

## Phase 3 - Rating, Modelling, MLflow, And Deploy Correctness

### 7. Rating key migration and miss behavior

Issues:
- RAT-1: legacy float-string rating keys no longer match canonicalized frame values; with defaultValue the whole book
  silently rates at default.
- RAT-2 residual: Datetime factors fail loudly but with confusing unsupported behavior.
- EFF-1: `_apply_ratebook` calls `collect_schema()` inside the per-table loop.

Developer plan:
- Add load-time migration or compatibility normalization for persisted string keys that represent floats.
- Ensure defaultValue never suppresses a whole-table miss silently; report missing levels/factors clearly.
- Improve Datetime ratebook error messages or explicitly reject unsupported Datetime factors at config/save time.
- Hoist schema collection in `_apply_ratebook` and track added output columns in memory.

Tests first:
- Legacy `"25.0"` key matches Float64 value `25.0`.
- With `defaultValue`, missing all factor keys raises or surfaces a blocking diagnostic, not silent all-default pricing.
- Partial misses still report exact missing keys.
- Datetime factor config errors are clear.
- Multi-table ratebook application avoids repeated schema collection.

Reviewer checks:
- Migration is deterministic and does not create ambiguous key collisions silently.
- Error path names the offending table/factor/levels.

### 8. Metrics and training diagnostics

Issues:
- MET-1: UInt32 target wraps in Gini sort and can flip sign.
- MET-1 companion: Boolean targets raise TypeError post-fit.
- Training diagnostics: non-finite prediction crashes histogram after successful fit; NaN weights produce NaN diagnostics.

Developer plan:
- Cast metric sort keys to a signed/float representation before negation; handle bool target explicitly.
- Apply a single finite-row mask to metrics, residual histogram, double-lift, AvE, Lorenz, and sample weights.
- If all rows are invalid, fail with a clear diagnostics error before artifact save assumptions break.

Tests first:
- UInt32 target gives same normalized Gini as float target.
- Boolean target either works explicitly or fails before fit with clear validation.
- One inf prediction does not crash after fit; diagnostics are skipped/marked with clear warning if intended.
- NaN/inf sample weights are filtered or rejected consistently.

Reviewer checks:
- No metric silently returns NaN.
- Masking rules are identical across all diagnostics.

### 9. GLM Tweedie export parity

Issues:
- MOD-2: exported GLM Tweedie script computes deviance with default variance power instead of configured `var_power`.
- MOD-1: modelling export route 500s on missing target.

Developer plan:
- Use the same training config builder for live and exported script parameter rendering.
- Emit GLM `var_power` into exported script when family/loss is Tweedie, independent of CatBoost `loss_function`.
- Map missing target/export validation errors to 4xx.

Tests first:
- Exported GLM Tweedie script uses configured `var_power` and reproduces live deviance.
- CatBoost Tweedie behavior remains unchanged.
- Missing target route returns 400/404 with sanitized detail, not 500.

Reviewer checks:
- No duplicate parameter-routing logic remains.
- Export script and live path share one source of truth.

### 10. MLflow artifact identity and eviction

Issues:
- MLF-1: disk cache collides same-basename artifacts within a run.
- MLF-2: disk eviction under one artifact lock can delete dirs other threads are loading.
- MLF-3: low/unreachable today, keep as regression guard if affected by the refactor.
- CLN-15: repeated keyed-lock registries.

Developer plan:
- Key disk cache by full artifact identity, not basename. Include run id plus normalized artifact path.
- Make in-memory model/artifact cache keys include full artifact identity/version.
- Make eviction acquire a run/global cache lock or per-run lock that covers directory removal.
- Consider a shared `KeyedLocks` helper only if it reduces duplication without widening the patch too much.

Tests first:
- Same run with `freq/model.cbm` and `sev/model.cbm` loads distinct bytes.
- Cache survives process restart without serving poisoned artifact.
- Concurrent load and eviction cannot remove files in use.
- Existing pyfunc/catboost load tests still pass.

Reviewer checks:
- Cache path encoding cannot collide or escape cache root.
- Eviction does not hold overly broad locks during network download.

### 11. Deploy scorer and expected-input validation

Issues:
- DSC-1: deploy batch scoring can lose cache pin mid-collect when selecting output fields.
- Phase 3: deploy test quote parser unwraps legitimate single field named `input`.
- CLN-11: live scoring path duplicates collect/validate logic already in `_score_eager_unified`.
- P2: deploy artifact path fingerprints full-hash every request.

Developer plan:
- Keep the pinned scan LazyFrame alive for the lifetime of any derived `select` plan.
- Change golden quote detection so `{"input": {...}}` is wrapper format only when explicitly in a golden-test context or
  when expected/tolerance keys are present. If ambiguity remains, fail loudly with a migration hint.
- Reuse `_score_eager_unified` categorical-level validation for live path.
- Switch artifact fingerprinting to the stat-gated runtime fingerprint path.

Tests first:
- Batch scoring with output fields cannot evict pinned parquet before collect.
- Test quote `{"input": {"raw": 1}}` for schema field `input` stays wrapped as user input or fails with clear
  ambiguity, never silently unwraps.
- Golden quote with `expected` still unwraps correctly.
- Artifact path fingerprint hits memo on unchanged file.

Reviewer checks:
- Pin lifetime is explicit, not relying on accidental Python references.
- Golden quote compatibility story is documented.

### 12. Optimiser auto-range value contracts

Issues:
- Phase 3 plausible: auto-range skips full solve non-finite/null value contract.
- CLN-10: quote-id validation duplicated across optimiser code.

Developer plan:
- Extract shared quote-id and value-contract validation helpers used by full solve and auto-range.
- Apply relevant finite/null checks before auto-range aggregation.
- Keep streaming behavior; validation should not require eager materialization of full scored data.

Tests first:
- Auto-range rejects null quote_id with same message class as full solve.
- Auto-range rejects NaN/inf constraint values before deriving ranges.
- Valid large frame still streams and produces same ranges.

Reviewer checks:
- Shared helper does not accidentally require objective/scenario columns for auto-range.
- Error messages remain user-actionable.

## Phase 4 - Trace, JSON Cache, Runtime Cache, And Concurrency

### 13. Trace waterfall branch awareness

Issues:
- WF-1: waterfall chains same-named columns across unrelated joined branches and fabricates factors.
- WF-2: big-int waterfall silently absent.
- CLN-14: `trace_result_to_dict` encodes individual fields instead of payload boundary.

Developer plan:
- Carry lineage/branch identity into waterfall step selection.
- Only compare consecutive observed values on the same lineage path, or mark waterfall unavailable with a clear reason.
- Encode the final trace payload with one `to_json_safe(payload)` call.
- Decide big-int behavior: support finite Decimal/int conversion for safe ranges or fail with reason instead of silent
  absence.

Tests first:
- Joined branches with same column name do not fabricate an implied factor.
- Opposite parent order does not produce a false reconciliation error.
- Big integer trace either renders correctly or returns a reason.
- New numeric field in trace payload is covered by recursive JSON-safe encoding.

Reviewer checks:
- Reconciliation still catches true arithmetic mismatch.
- UI receives a clear unavailable reason when waterfall cannot be built.

### 14. Trace correlation exponential relaxed matching

Issues:
- TRC-1: relaxed row matching is O(2^n), can pin a worker for days.
- TRC-2: low self-healing edge, keep as regression guard if algorithm changes.

Developer plan:
- Replace all-subset enumeration with bounded/polynomial matching.
- Precompute per-column equality masks and use a cap on dropped columns.
- Add ambiguity reporting rather than exhaustive search past budget.

Tests first:
- Wide no-match frame returns within a small time/operation budget.
- Common partial-match cases still find the same row as before.
- Ambiguous matches fail with a clear reason.

Reviewer checks:
- No abandoned worker threads after route timeout.
- Complexity is bounded by columns * rows or another documented polynomial budget.

### 15. JSON cache hashing, progress, swaps, and loads

Issues:
- JS-3/SRV-1: status endpoints hash multi-GB JSON on event loop forever after mtime drift.
- JS-1: uuid `.build-tmp-*` / `.build-old-*` dirs leak on swap failure.
- JS-2: multi-port load TOCTOU gives misleading error.
- CFB-2 partner: progress endpoint reports inactive during active v2 builds.
- CLN-4: dir swap dance duplicated; mirror path lacks Windows retry hardening.
- EFF-3: byte-at-a-time JSON array sampling is slow for API callers.

Developer plan:
- Move full-file hash checks off the event loop.
- Memoize data-file hash by stat gate or update cache metadata after successful content arbitration.
- Track active v2 builds so progress endpoint reflects real active state.
- Extract shared atomic directory replacement helper with Windows retry and cleanup of uuid temp/old dirs.
- Make multi-port load fail loudly when a promised parquet is missing after validity check.
- Improve sampled array reader with buffered reads if still relevant after correctness fixes.

Tests first:
- Mtime-only drift causes one hash then fast subsequent status.
- Async status route does not block event loop in a controlled test.
- Simulated rename/rmtree failure cleans temp dirs.
- Progress is active while build request is running.
- Missing multi-port parquet raises cache error naming the missing port/path.
- Sampling test covers large nested array without byte-at-a-time performance cliff.

Reviewer checks:
- Metadata healing cannot bless wrong content.
- Atomic swap helper is used by both shred and mirror paths.

### 16. DataFrame execution cache lock scope

Issues:
- DFC-1: per-key lock is acquired while holding global guard, serializing unrelated materializations.
- TST-1: existing materialization-lock test is vacuous.

Developer plan:
- Acquire or create the per-key lock under the guard, release guard, then acquire per-key lock.
- Keep lock lifetime strong enough that WeakValueDictionary or cleanup cannot drop it mid-acquire.
- Strengthen tests to assert unrelated keys materialize concurrently and same key serializes.

Tests first:
- Same-key workers run body once or serialize writes as intended.
- Different-key workers can enter materialization concurrently while one same-key waiter is blocked.
- `clear()` is not blocked by unrelated waiter longer than necessary, or behavior is documented.

Reviewer checks:
- No deadlock between clear, eviction, and materialization.

### 17. Runtime input fingerprint cleanup

Issues:
- CLN-1: `_stat_gated_runtime_path_fingerprint` duplicates `StatGatedCache`.
- CLN-2: `dataframe_graph_input_fingerprint` has canonical-json straggler.
- CLN-3 plausible: `_runtime_file_signature_paths` has per-node carve-outs.
- P3: target preview/trace invalidates on unrelated runtime files.

Developer plan:
- Replace bespoke stat-gated memo with `StatGatedCache` or extract shared primitive.
- Use `canonical_json` for all digest material.
- Remove redundant API input branch if it shadows table-driven logic.
- Investigate target-upstream-only runtime signatures for preview/trace. Implement only if it can be proven without
  missing hidden dependencies.

Tests first:
- Concurrent runtime fingerprint calls single-flight a large unchanged file.
- Canonical JSON digest remains byte-compatible where required or has an intentional cache-version bump.
- Runtime input changes on unrelated branch do not invalidate target preview if upstream-only signatures are implemented.

Reviewer checks:
- Cache invalidation stays conservative; no stale preview risk.

## Phase 5 - Frontend State, Live Sync, And UX Correctness

### 18. WebSocket dirty sync conflict handling

Issues:
- WS dirty banner recommends Save, which overwrites external edits.
- Resync-on-reconnect sends false changed banners and redundant reapply.
- `graphUpdateSeq` increments before foreign-file filter.
- Resync handler parses/walks filesystem on event loop.
- WSF-4: lowercased source-file matching applies wrong case-twin file on Linux.
- Phase 3: drilled submodel source file not reflected in `sourceFileRef`.

Developer plan:
- Add disk-freshness/conflict detection to `savePipeline`, or change the dirty banner to offer explicit reload/discard
  without recommending Save.
- Add a reload/apply external change action that re-requests current source.
- Make resync skip sending graph updates when fingerprint unchanged.
- Move `graphUpdateSeq` increment after source-file filtering.
- Compare canonical server-provided project-relative source ids strictly; stop lowercasing.
- Track active source file when drilling into submodels and restore parent source on breadcrumb back.
- Move server resync parse/discovery into threadpool.

Tests first:
- Dirty canvas plus external edit cannot be overwritten by following banner advice.
- Reconnect with unchanged disk does not show dirty banner/toast/fitView jump.
- Foreign frame does not cancel current frame layout.
- Case-twin paths on case-sensitive filesystem do not match.
- Drilled submodel applies only `modules/<name>.py` updates and ignores parent frames.

Reviewer checks:
- UX offers a safe way out of conflict.
- Source identity is one contract shared by backend and frontend.

### 19. CacheFetchButton async state

Issues:
- CFB-1: stale status responses can paint wrong resource status/error.
- CFB-2: progress endpoint stub makes button leave building early.

Developer plan:
- Add effect generation or AbortController so only the latest `resourceKey` status response can update state.
- Clear status only for the active key.
- Treat progress `active:false` carefully while `startFetch` is still pending; use real active build state from backend.
- Prevent double-build while start request is in flight.

Tests first:
- Slow A status resolving after fast B status does not affect B.
- Slow A rejection after B success does not show B error.
- `onCacheReady` fires only for active key.
- Build button remains building until start fetch completes and backend progress says complete.

Reviewer checks:
- No stale closure around `onCacheReady`.
- Error UI remains accessible and scoped.

### 20. ScenarioExpander numeric drafts

Issues:
- SCE-1: typing `-0.5` commits `+0.5`.
- SCE-2/SCE-3: cleared/invalid drafts persist old values on save and drafts survive node switches.
- CLN-13 plausible: min/max state mirrored and may deserve a shared hook.

Developer plan:
- Preserve in-progress negative-zero text until a valid non-ambiguous number is committed.
- Add node identity or external reset reconciliation so drafts cannot leak between selected nodes.
- Decide product behavior for empty invalid value: either commit clear, block save, or show explicit validation.
- Extract shared hook only after behavior is pinned in tests.

Tests first:
- Incremental typing `-`, `-0`, `-0.5` results in `-0.5`.
- Switching node clears invalid draft from previous node.
- Empty min/max save behavior is explicit and tested.
- Parent rerender preserves valid formatted draft only for the active node.

Reviewer checks:
- No broad editor abstraction until behavior is stable.

### 21. API guards and frontend contract gaps

Issues:
- API-2: frontend strips backend `skipped_records` / `skipped_rows`.
- API-1: token rotates per process; no 403 recovery path.
- DSP-1: SchemaPreview rounds values without full precision tooltip.
- KEY-1: Ctrl+K cannot close node-search after input autofocus.
- P1: preview fan-out cannot be aborted and refresh can stampede upstream previews.
- P4: rating grid copy can override native input copy in edge cases.

Developer plan:
- Update guards/types to preserve skipped-record metadata and show warning state.
- On 403 from local-session token mismatch, show a reload-required state or auto reload after confirmation.
- Add full precision tooltips where schema/preview rounds values.
- Let Ctrl+K close the palette even when palette input is focused while preventing browser default.
- Thread abort signals through downstream/upstream preview fan-out and add a concurrency cap for refresh.
- Clear grid range selection on input focus or prioritize native text selection over grid selection.

Tests first:
- JSON cache status with skipped rows renders warning, not green-only cached state.
- API 403 token mismatch produces reload affordance.
- Ctrl+K opens and closes node search from focused input.
- Refresh preview aborts all child/upstream requests on supersession.
- Selected input text copy wins over stale grid selection.

Reviewer checks:
- UI text is user-actionable without exposing internals.
- Accessibility roles/alerts are preserved.

## Phase 6 - Cleanup And Test Quality

### 22. Confirmed cleanup debt

Issues:
- CLN-4: shared atomic directory replacement.
- CLN-5: shared save/delete validators.
- CLN-6: shared non-finite float token helper.
- CLN-7: modelling empty-state component reuse.
- CLN-10: quote-id validation helper.
- CLN-11: scorer validation reuse.
- CLN-14: trace payload-wide JSON-safe encoding.
- CLN-15: keyed locks helper.

Developer plan:
- Do cleanup only when adjacent correctness fixes are already touching the same code.
- Keep each cleanup with behavior-preserving tests or snapshot/contract tests.
- Avoid broad refactors that delay critical fixes.

Tests first:
- For each helper extraction, pin existing behavior before moving code.

Reviewer checks:
- Diff reduces duplication without changing semantics unintentionally.

### 23. Confirmed test debt

Issues:
- TST-1: materialization lock test is vacuous.
- TST-2: ws_clients concurrency test does not exercise production wrappers.
- TST-5: trusted-host env leaks across tests.
- TSF-1/TSF-2: WebSocket reconnect tests miss stopped/connected assertions.
- TSF-4: PdpTab single-point NaN geometry unguarded.
- Additional frontend test gaps listed as TSF-3/5/6/7 and backend tautology tests.

Developer plan:
- Fix test debt alongside the product area it covers, not as a standalone noisy sweep unless it blocks confidence.
- Add assertions that would fail if the production behavior regressed.
- Use monkeypatch fixtures for environment mutation and reset.

Tests first:
- Mutation or deletion of the protected behavior makes the test fail locally.
- Env mutations are isolated per test.

Reviewer checks:
- Tests fail for the intended reason, not by tautology or implementation mirroring.

## Execution Order

Recommended order:
1. Security/auth/host/origin.
2. Write-safety: sink paths, git archive/delete, submodel config collisions.
3. Projection/parser/save fail-loud issues.
4. Rating/modelling/MLflow/deploy correctness.
5. Live sync/frontend state issues.
6. Trace/JSON cache/concurrency performance issues.
7. Cleanup and test debt.

Within each phase:
1. Developer agent writes failing tests.
2. Reviewer agent confirms the tests fail for the reviewed bug.
3. Developer agent implements the smallest production fix.
4. Developer agent runs focused tests.
5. Reviewer agent audits source, edge cases, and regression coverage.
6. Main agent integrates, runs broader verification, and updates the ledger.

## Verification Gate Before Merge

Required before marking the whole review fixed:
- All new regression tests pass.
- Existing focused suites for touched subsystems pass.
- `uv run ruff check` on changed Python files passes.
- Frontend unit tests for touched components/hooks pass.
- Playwright smoke projects are still listed and any impacted smoke is run.
- Manual review confirms no confirmed issue remains without either a fix, a test-backed "already fixed" note, or an
  explicit product decision to defer.
