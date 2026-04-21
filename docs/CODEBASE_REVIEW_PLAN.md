# Codebase Review — Execution Plan

**Companion to:** `CODEBASE_REVIEW.md`
**Date:** 2026-04-17
**Scope:** Execution plan for all 156 findings + 12 missing-test scenarios (168 items total).
**Method:** Every item is worked by a **two-agent pair** — one developer agent, one reviewer agent — following the protocol in `CLAUDE.md`.

---

## 1. Per-item workflow

Every item in this plan follows the same protocol. No exceptions.

### Step 1 — Developer agent

1. Read the source file(s) cited by the finding.
2. **Write tests first (TDD).** Think through complicated user pipelines and edge cases. Test suite must cover:
   - The specific bug / scenario described.
   - At least three edge cases the reviewer would ask about (empty input, malformed input, concurrent access — whichever apply).
   - Regression guard for any behaviour the change is *not* supposed to alter.
3. Run the test suite and watch it fail for the right reason.
4. Implement the change. Minimal diff. No adjacent refactoring.
5. Re-run tests. All green.
6. Produce a change summary: files touched, rationale, what tests cover.

### Step 2 — Reviewer agent

The reviewer is a **gate**, not a rubber stamp. Reviews both the question of "should this change exist?" and the implementation quality.

The reviewer answers, in order:

1. **Is the finding still accurate?** Read the cited code. Is the described problem actually present? (Findings go stale; verify.)
2. **Is the change worth making?** Weigh cost (churn, review time, regression risk) vs. benefit (correctness, perf, clarity). Some findings are real but not worth fixing. Reviewer can reject.
3. **Does the change follow project principles?** Check `CLAUDE.md`:
   - Fails loudly rather than silently.
   - No unnecessary fallbacks / backwards-compat shims / dead code.
   - No comments explaining *what* code does.
   - No speculative generality.
   - UX / engineering quality over quick fixes.
4. **Are the tests actually good?** Not just green — do they cover complicated user scenarios and edge cases? Would they catch the bug if introduced again?
5. **Is the implementation minimal?** Any scope creep, drive-by refactors, or new abstractions?
6. **Is the diff safe to ship?** Regression risk on untouched code paths? Atomic or does it land in pieces?

Reviewer produces a verdict: **APPROVE**, **REQUEST CHANGES** (with specifics), or **REJECT** (with reasoning — the change should not be made at all).

### Step 3 — Integration

On APPROVE: merge to the branch for the phase. On REQUEST CHANGES: dev iterates. On REJECT: item is moved to the rejected-findings list with reasoning.

### Step 4 — Phase-level review

After each phase completes, spawn a fresh team of reviewer agents (no memory of the phase) to audit the whole phase for:
- Cohesion of the changes as a set.
- Principles adhered to.
- Tests genuinely stronger.
- No unintended behaviour drift.

Only after phase-level review passes does the next phase begin.

---

## 2. Reviewer gate — explicit rejection criteria

The reviewer should **reject** (not request changes — reject) if any of the following apply:

- The proposed change removes a loud error path in favour of a quiet one.
- The proposed change adds a new abstraction that only has one caller.
- The proposed change adds a comment that explains *what* rather than *why*.
- The proposed change adds a fallback for a scenario that cannot happen.
- The proposed change adds backwards-compatibility for code that has no external callers.
- The proposed change bundles unrelated refactoring with a bug fix.
- The tests pass but don't actually exercise the claimed edge cases.
- The finding is not reproducible against current code.

On rejection, record the reasoning in the rejected-findings log. A rejected finding can still be raised again later if circumstances change.

---

## 3. Parallelization and sequencing

### Parallel by default
- Backend and frontend work has no shared files — always safe to run in parallel.
- Different subsystems inside the backend (parser / trace / modelling / deploy / CLI / routes) are largely independent — parallel-safe unless they share a file.
- Different god-file splits touch disjoint files — parallel-safe.

### Serialize on
- Same file in the diff — serialize.
- Same Pydantic schema — serialize.
- Changes to `_types.py`, `schemas.py`, or the top-level `server.py` — serialize (these are shared).

### Sequencing rules
- **Phase 0 must complete before Phase 1.** Foundation helpers unlock every subsequent "fail loudly" fix.
- **Phase 1 must complete before Phase 2.** Fixing bugs before splitting god files keeps the split diffs mechanical.
- **Phase 3 (perf) runs after Phase 2 (architecture).** No point memoizing a fingerprint on an object that's about to be restructured.
- **Phase 4 (style sweep) is independent** — can run in parallel with any backend phase.
- **Missing tests (Phase 7) run throughout** — each test scenario attaches to whichever phase first makes it relevant.

---

## 4. Phases at a glance

| Phase | Theme | Items | Can run parallel with |
|---|---|---|---|
| 0 | Foundation — shared helpers | 7 scaffolding tasks | — |
| 1 | P0 critical correctness | 51 items (#1–#51) | Phase 4 |
| 2 | P1 architecture | 15 items from #52–#85 | Phase 4 |
| 3 | P1 performance | 15 items from #86–#100 | Phase 4 |
| 4 | Frontend style migration | Item #71 (96+ sites) | All backend phases |
| 5 | P2 consistency / UX | 32 items (#101–#132) | — |
| 6 | P3 stdlib / polish | 12 items (#133–#144) | — |
| 7 | Missing test scenarios | 12 items (#145–#156) | Woven through phases 1–6 |
| 8 | Deferred polish (Phases 2, 3, 4) | 7 items (#157–#163) | Phases 4, 5 |

---

## 5. Phase 0 — Foundation

Shared helpers and scaffolding. Every subsequent phase depends on these. One dev + reviewer per foundation task. All seven can run in parallel.

| # | Foundation task | Dev task | Reviewer gate |
|---|---|---|---|
| F1 | Typed error hierarchy | Add `haute.errors` module with `HauteError`, `ConfigError`, `ParseError`, `ExecutionError`, `DeployError`, `FeatureMismatchError`. Convert a representative `except Exception` to use the new hierarchy as proof. | Does every error type add real signal vs. using stdlib? Are hierarchies shallow (≤2 levels)? Is one usage actually wired? |
| F2 | Atomic-write helper | Extract the parquet temp-rename pattern from `_polars_utils.py` into a generic `atomic_write_bytes(path, bytes)` and `atomic_write_text(path, str)`. | Does this already exist in stdlib/`pathlib`? Is the API minimal? |
| F3 | Content-hash helper | Add `haute._hashing.content_hash(path)` using `xxhash`. Include unit test against `_io.py` expected behaviour. | Is xxhash actually faster in our benchmark? Is the helper overgeneralized? |
| F4 | MLflow signature helper | Add `haute.modelling._signature.build_signature(features, cats, target)` producing `mlflow.types.ModelSignature`. | Does the signature include everything the scorer needs? Is it used at both log and load sites? |
| F5 | Feature-contract artifact format | Define JSON schema for `feature_contract.json` (feature names, types, categorical flags, order). Add serializer + loader. | Is this redundant with MLflow signature? Why two? If both kept, what's the role of each? |
| F6 | Async file-write coordination primitive | Build `file_ops.Writer` context manager that performs write-to-temp + rename + marks self-write in one operation, usable by `_save_pipeline` and file watcher. | Does the abstraction earn its keep, or is it a thin wrapper? |
| F7 | Project-root helper | Add `haute._project.get_project_root()` with validation (has `haute.toml`, is git repo). Replace one CLI caller as proof. | Does this conflict with any existing abstraction? Does it do too much? |

**Phase 0 gate:** no item in Phase 1+ may be started until all seven foundation tasks are APPROVED.

---

## 6. Phase 1 — P0 critical correctness

The 51 P0 findings. Organized by subsystem so that packages serialize on shared files and the "fail loudly" sweep per subsystem happens coherently.

### Package 1A — Parser / codegen fail-loudly sweep

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 18 | Silent config-path recovery on Windows — `_parser_helpers.py:985` | Raise `ConfigError` with original path + recovered path; drop silent scan | Is Windows path recovery still needed at all? If so, should it be opt-in? |
| 19 | Instance mapping overrides stale explicit entries — `codegen.py:884`, `_types.py:597` | Detect stale keys, raise `ConfigError` with remediation hint | Does "stale" have a precise definition? Are false positives possible? |
| 20 | Submodel cross-boundary edge unvalidated — `codegen.py:1180` | Validate handle format; raise `ParseError` on mismatch | Is `removeprefix("in__")` the contract, or is there a proper id field to use instead? |
| 21 | Graph fingerprint cache extend-path keeps stale — `executor.py:391` | Invalidate prior outputs when node ID re-added | Is detection by "ID exists but structure differs" correct, or do we need stronger keying? |
| 22 | Polars codegen empty-code fragile — `codegen.py:813` | Explicit branch + test; no shared `_wrap_user_code` between empty/non-empty | Does the refactor make the code clearer or just different? |

### Package 1B — Trace fail-loudly sweep

Serializes on `trace.py`. Single dev + reviewer handling the whole sweep is cleaner than per-site pairs.

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 3 | 15+ silent excepts in enrichment — `trace.py:1056, 1095, 1180, 1196, 1216, 1240, 1246, 1291, 1298, 1322, 1359, 1361, 1467` | Per-site triage: delete / narrow to specific exception / escalate with log + user-visible signal | Has every site been classified correctly? No site left as `except Exception:`? |
| 4 | `swallow_errors=True` retry based on regex — `trace.py:577` | Replace with upfront graph validation; raise original error on failure | Is the graph validation complete? What used to succeed under retry that would now fail correctly? |
| 5 | Waterfall swallows all exceptions — `trace.py:872` | Log at warning + surface `waterfall=null` with explicit reason to frontend | Does the frontend handle the reason-string? Is the user ever shown the message? |

### Package 1C — Routes / server correctness

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 6 | `ws_clients` mutation without lock — `server.py:183` | Wrap set in `asyncio.Lock`; guard all mutations | Is `asyncio.Lock` correct for the broadcast path? Any sync contexts? |
| 7 | File-watcher 2s cooldown vs. long saves — `server.py:289`, `_helpers.py:174` | Use F6 atomic-write primitive; remove cooldown window | Does cooldown disappear entirely, or is there a residual race? |
| 11 | Error details leaked to HTTP clients — `files.py:111`, `submodel.py:45`, `git.py:67` | Funnel all HTTPException details through `_INTERNAL_ERROR_DETAIL` sanitizer; log full detail server-side | Any callers that legitimately need detail (tests)? Is logging context preserved? |
| 12 | Path traversal in `graph_to_code_multi` — `_save_pipeline.py:139` | Explicit path-prefix allowlist (`modules/`, pipeline dir); reject everything else | Does the allowlist cover legitimate codegen output? Any edge case files? |
| 24 | `/schema` swallows broad `Exception` — `files.py:114` | Let unexpected exceptions propagate to FastAPI's default handler; add structured log | Will 500s now leak detail? (Handled by #11) |
| 28 | `_job_store.update_job()` not atomic — `_job_store.py:68` | Route all mutations through `atomic_update` | Why was `update_job` separate? What's lost by removing it? |
| 29 | `git fetch` not locked — `git.py:37` | Single `threading.Lock` around fetch command | Does this serialize all git ops unnecessarily? Fetch-only lock enough? |
| 30 | Sync I/O in async handlers — `_io.py`, `_mlflow_io.py` | Wrap blocking reads in `run_in_threadpool` at route boundaries | Are there spots where blocking is intentional? Any perf regression from thread pool? |
| 50 | Save service has no transaction — `routes/_save_pipeline.py` | Use F6 writer; checkpoint each write; roll back on failure | Is partial rollback ever worse than partial write? User-visible difference? |
| 51 | Node positions lost on rename — `_helpers.py:325` | Warn user in response payload when positions can't be restored | Should this block the rename, or proceed with warning? |

### Package 1D — Caching correctness

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 9 | Fingerprint uses `json.dumps(default=repr)` — `_cache.py:27, 46` | Replace with deterministic serializer; reject non-serializable values at parse time | Does parse-time validation catch every non-JSON type users produce? |
| 10 | mtime-based cache keys (TOCTOU) — `_io.py:96`, `_optimiser_io.py:45` | Use F3 content hash; cache keyed on hash | Perf regression on hash compute for large files? Is sampling acceptable? |

### Package 1E — Modelling correctness

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 1 | Feature/categorical order mismatch | Pass ordered `cols_to_select` through `_build_pool` → `_prepare_predict_frame`; assert equality at load | Does any caller legitimately reorder features? |
| 2 | No MLflow signature logged — `_mlflow_log.py` | Use F4 helper at log time; validate at load | Does it cover all model flavors (CatBoost, pyfunc, Rustystats, GLM)? |
| 13 | Categorical type mismatch warning-only — `_model_scorer.py:123` | Raise `FeatureMismatchError`; no silent cast | Does training always produce the type users can feed? Any legitimate cast? |
| 14 | Pruner doesn't validate static source schema — `_pruner.py:82` | Add `validate_static_source_schema` in `_validators.py`; call from pruner | What's the failure mode during deploy if schema drift is detected? |
| 15 | GLM column selection drops categorical metadata — `_training_job.py:262` | Rebuild `cat_features` by filtering original list | Any other metadata dropped by the filter? |
| 16 | Test-quote errors captured, never raise — `_validators.py:141` | `validate_deploy` raises on any `validation_errors` | Was lenient validation ever intentional? CI vs. interactive path? |
| 17 | No feature contract hash at deploy — `_bundler.py:88`, `_scorer.py:200` | Use F5 contract artifact; assert match at scorer init | Is the hash stable across training runs with identical feature set? |
| 25 | Model-score column detection swallows config errors — `_builders.py:601` | Raise `ConfigError`; remove the bare `except` | Any legitimate "optional MLflow" path that needs graceful handling? |
| 26 | 7 diagnostic excepts in training — `_training_job.py:800, 816, 838, 843, 848, 857, 886` | Split into mandatory (feat importance, metrics — fail) and optional (SHAP, PDP — skip with visible flag) | Which diagnostics are mandatory vs. optional? User-agreed taxonomy? |
| 27 | Artifact download delete-and-retry — `_mlflow_io.py:514` | Exponential backoff with jitter; no silent delete | When is delete actually correct (true corruption)? |

### Package 1F — CLI correctness

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 42 | `string.replace()` on pyproject.toml — `_init_cmd.py:42` | Use `tomli_w` / `tomlkit` for proper TOML edit | Does the TOML lib preserve comments/formatting? |
| 43 | String-grep dependency detection — `_init_cmd.py:40` | Parse with tomllib; check `dependencies` array | Any edge case (dynamic deps)? |
| 44 | `uv pip install` wrong instruction — `_status.py:33` | Replace with `uv add` | Any context where `uv pip install` is correct? |
| 45 | CI env vars deprecated — `_deploy.py:40` | Replace with current canonical set (`CI=true` from GitHub Actions, `GITLAB_CI`, `CIRCLECI`, `TF_BUILD`, `BUILDKITE`) | Is there a library that already normalizes these? |
| 46 | `haute serve` no port-conflict check — `_serve.py:57` | Probe port before uvicorn; offer fallback port | What if user has multiple instances — pick vs. prompt? |
| 49 | Silent `prod_exists=False` — `_impact.py:196` | Distinguish 404 from network error; retry network, fail fast on 404 | Retry budget? |

### Package 1G — Deploy correctness

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 47 | Container base image unpinned — `deploy/_config.py:41` | Require explicit patch-version pin; reject unpinned at config load | Break existing `haute.toml` files? Migration path? |
| 48 | Static dataSource paths re-resolved at deploy — `_bundler.py:112` | Resolve to absolute path at bundle time; store in manifest | Any scenario where the path must be re-resolved at runtime? |

### Package 1H — Frontend correctness

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 8 | WebSocket sync bypasses undo history — `useWebSocketSync.ts:70` | Integrate `graphRefreshingRef` into `useUndoRedo`; block undo while sync in flight | Is blocking undo surprising? Flash a toast? |
| 31 | Silent AbortError leaves stale preview — `usePipelineAPI.ts:217` | Clear `previewData` on abort before returning | Does clearing flicker the UI? |
| 32 | Eager derived-cache cleanup — `useNodeHandlers.ts:44` | Defer cache clear by one render cycle | Use `useEffect` teardown or queue? |
| 33 | Zustand `.getState()` in async callbacks — `useWebSocketSync.ts:86`, `usePipelineAPI.ts:160`, `useKeyboardShortcuts.ts:186` | Replace with ref pattern declared in deps | Any callers that legitimately want current state? |
| 34 | Stale `activeSourceRef` in cascade — `usePipelineAPI.ts:93, 121` | Capture at cascade start | Does cascade now use wrong source if user switches? Acceptable? |
| 35 | Drag-drop JSON parse silent catch — `useEdgeHandlers.ts:145` | Throw + user-facing toast | Ever a legitimate failure users should see? |
| 36 | Uncontrolled RenameDialog input — `RenameDialog.tsx:51` | Make controlled; add maxLength, pattern, sanitize | What's the name sanitization contract? |
| 37 | Catch-all in WebSocket handler — `useWebSocketSync.ts:54` | Per-operation try/catch; partial-state rollback | Is rollback testable? |
| 38 | Unchecked `.find()` cast — `App.tsx:492` | Explicit guard + clear `lastSelectedId` on sync | User-visible effect of clearing? |
| 39 | Panel state not cleared on WebSocket sync — `useWebSocketSync.ts:86` | Clear dialogs whose referenced nodes disappear | Edge: user mid-edit when file changes? |
| 40 | Missing toast dedup — `Toast.tsx`, `useToastStore.ts` | Dedup by `(type, text)` within 2s window | Is 2s right? Any rapid-fire intentional? |
| 41 | Incomplete modal focus trap — `ModalShell.tsx:48` | Use focus-lock library or sentinel pair | Third-party dep justified for one use? |

### Package 1I — Parser-discovery fail-loudly tail

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 23 | Discovery silently skips unreadable files — `discovery.py:63, 78` | Log at warning with path + error; optionally include in result with error marker | Should unreadable files block or just warn? |

**Phase 1 gate:** fresh reviewer team audits the full diff before Phase 2.

---

## 7. Phase 2 — P1 architecture

God file splits, abstraction consolidation. Each split is its own dev + reviewer pair. Serializes on the target file being split.

### Package 2A — God file splits (backend)

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 61 | Split `_parser_helpers.py` (1016 LOC) | Extract into `_ast_helpers.py`, `_config_builder.py`, `_code_extraction.py`, `_graph_builders.py`; mechanical move + test pass | Does any split file have <3 callers (probably unnecessary)? Do the boundaries match how the code is actually used? |
| 62 | Split `_types.py` (669 LOC) | Keep Pydantic types; move `_sanitize_func_name`, `build_instance_mapping`, `resolve_orig_source_names` to `_graph_utils.py` | Are there other util-type items that should move? Any cyclical imports after split? |
| 63 | Split `codegen.py` (1298 LOC) | Keep orchestration; move builder registry to `_codegen_builders.py` | Does the split create two files that always change together? If yes, they shouldn't be split. |
| 64 | Split `trace.py` (1500+ LOC) | Separate `_trace_preview.py`, `_trace_enrichment.py` (already exists — merge), `_trace_waterfall.py`, leave `trace.py` as facade | Is the facade real, or would callers cross-import anyway? |

### Package 2B — God file splits (frontend)

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 65 | Split `OptimiserPreview.tsx` (1081 LOC) | `FrontierChart`, `ConvergenceChart`, `SummaryTab`, `DetailCard` | Is each split component ever used outside OptimiserPreview? If not, they're co-located concerns and splitting may just move code. |
| 66 | Split `CalculationHero.tsx` (893 LOC) | `WaterfallChart`, `ExpressionChain`, `InputSourceTree` | Same gate: are sub-components reused? If not, justify the split on testability or readability concretely. |

### Package 2C — Consolidate overlapping abstractions

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 52 | 4 user-code extractors → 1 — `_parser_helpers.py:261–530` | Shared engine with pluggable boilerplate matchers | Does the shared engine handle the fourth pattern cleanly, or does it need a special-case branch (which would defeat the purpose)? |
| 53 | 3 cache layers → 1 | Merge `FingerprintCache` into `LRUCache` with pinning as optional kwarg; retire `_fingerprint_cache.py` | Does unified cache lose any capability (e.g., trace-specific pinning)? |
| 55 | Parallel builder dispatch tables — `_builders.py:102`, `codegen.py:392` | Single registry with `(exec, codegen)` tuples | Does unification complicate the module boundary? Any cycle risk? |
| 56 | Graph flattening duplicated — `_parser_submodels.py:135`, `_flatten.py` | Route parser through `_flatten.py` | Does the parser have submodel-specific needs that flatten doesn't cover? |
| 57 | Column contract system underutilized — `_execute_lazy.py:38`, `_builders.py:107` | **Decision: adopt everywhere.** Extend contract declarations to every builder in `_builders.py`; wire codegen to emit contract metadata; wire parser to validate user-declared contracts; wire executor to assert contract match at node boundaries. | Does every builder now declare its contract? Does the executor actually assert (fail loudly on mismatch)? Does contract metadata round-trip through parse/codegen? Benchmark: contract overhead acceptable? |
| 58 | `ScoringModel` wrapper — `_mlflow_io.py:38` | **Decision: drop.** Replace with explicit flavor dispatch at each scoring site. Migrate callers one by one. | Does every flavor now handle its own feature ordering + categorical indices explicitly? Any callsite still relying on `__getattr__` proxy? |
| 59 | Duplicate batch + eager scoring paths — `_model_scorer.py:206` | Unify on DataFrame-size-based selection | Do the paths actually behave identically, or is there a real semantic difference? |
| 60 | Dead GLM CV path — `_training_job.py:865` | **Decision: delete.** Dev must first confirm zero callers via grep across the codebase + `starter_*` scaffolds. If any caller found, escalate. | Resolved by `9bf4221` + `4fbb8e2`: `cv_folds` was surfaced in the frontend GLM config UI but never reached the backend (the Python-side regularization CV was hard-coded at 5); both the backend field and the frontend UI removed. Zero callers remain. |
| 54 | Regex fallback parser — `_parser_regex.py` | **Decision: partial fix — keep fallback, fix lossy path only.** Replace the hand-rolled decorator-kwarg regex (`_parser_regex.py:136–150`) with `ast.parse(f"f({kwargs_str})")` to recover booleans/floats/nested structures. Leave the rest of the regex fallback in place. | Does the AST mini-parse cover every kwarg shape the current regex tries to handle? Add a parametrized test with 10+ kwarg variants. |

### Package 2D — Frontend architecture

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 67 | ModellingConfig/OptimiserConfig duplication | Extract `useConfigEstimate`, `useJobPolling` hooks | Do extracted hooks have 2+ callers now? If only 1 per, don't extract. |
| 68 | NodePanel prop drilling | `GraphContext` or Zustand selector | Does context cause unnecessary re-renders? Zustand probably better. |
| 69 | Shell trinity — `ModalShell + PanelShell + PanelHeader` | Merge `PanelShell + PanelHeader`; keep `ModalShell` only if it has distinct behaviour | Are the shells actually different? If yes, keep. If no, merge all three. |
| 70 | `ConfigInput/ConfigSelect` trivial wrappers | Replace with plain `<input>` + className helpers | Any accessibility feature the wrapper adds that plain HTML loses? |
| 72 | Preview propagation 3-layer indirection — `usePipelineAPI.ts:101` | Inline call in `.then()` | Does inlining reintroduce the ordering bug the indirection was fixing? Confirm by reading the history. |
| 73 | `isDragging` ref in undo/redo — `useUndoRedo.ts:61` | Inspect `NodeChange[]` once per effect | Does React Flow guarantee the change shape? |

### Package 2E — Remaining P1 architecture

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 74 | Dual-layer error conversion in git routes — `git.py:61` | Make `_git.py` return Pydantic directly | Breaking change to `_git.py` callers? |
| 75 | Manual pipeline index invalidation — `routes/_helpers.py:198` | Rebuild at startup + on file-watcher events only | Perf on large projects after removing cache? |
| 76 | Error response shape drift | Standardize on `detail: string`; structured info to logs | Frontend callers depending on dict shape? |
| 77 | Save service no transactional guarantees — `_save_pipeline.py:139` | (Handled in Phase 1 #50 via F6.) | — |
| 78 | Defensive Node PATH patching — `cli/_helpers.py:95` | Delete; fail with clear install instruction | Windows users actually affected? |
| 79 | Browser-open 4-fallback — `cli/_helpers.py:72` | Single `webbrowser.open`; print URL on failure | Edge: headless server? Print URL covers it. |
| 80 | Silent `_find_frontend_dir()` None — `cli/_helpers.py:123` | Raise; caller decides dev-mode | Any caller that actually wants None? |
| 81 | `click.UsageError` inconsistency — `_smoke.py:48` | Match project-wide `click.echo + SystemExit(1)` | Is UsageError semantically different (wrong args vs. runtime)? Pick one, document. |
| 82 | Three-fallback staging_suffix — `_impact.py:49` | Single source: config.ci.staging_endpoint_suffix; CLI flag overrides | Does removing a fallback break existing setups? |
| 83 | `console.warn` on errors that should be toasts — `OptimiserPreview.tsx:151`, `OptimiserConfig.tsx:150+` | Route through `useToastStore` | Which errors are user-actionable vs. genuinely debug? |
| 84 | Optional-chain hides missing `instanceOf` — `NodePanel.tsx:84` | Throw with clear message | User-facing: how is the error surfaced? |
| 85 | Silent `null` returns in CalculationHero — | Replace with explicit `<ErrorUI />` | Is there a missing-data state that's legitimately empty? |

**Phase 2 gate:** fresh reviewer team audits cohesion — any file split that didn't improve clarity is reverted.

---

## 8. Phase 3 — P1 performance

Each item gets its own pair. Requires a benchmark before-and-after.

| # | Item | Dev (must include benchmark) | Reviewer gate |
|---|---|---|---|
| 86 | Graph fingerprint recomputed — `executor.py:385` | `@cached_property` on `PipelineGraph`; benchmark on 100-node graph | Benchmark shows measurable win? |
| 87 | `_compute_needed_columns` O(n²) — `_execute_lazy.py:38` | Forward-pass O(n) | Same outputs on current test graphs? |
| 88 | Preamble cache manual eviction — `executor.py:88` | `functools.lru_cache` | Loses thread-safety guarantees anywhere? |
| 89 | SHA-256 cache keys — `_cache.py:27` | xxhash via F3 | Any code path that relies on SHA specifically? |
| 90 | `JSON.stringify(n.data)` per render — `App.tsx:188` | Shallow hash of minimal keys | Shallow enough to catch real changes? |
| 91 | Polars↔pandas churn — `_mlflow_io.py:556` | Stay in Polars when no categoricals | pyfunc requires pandas sometimes — preserve that path |
| 92 | JSON reads fully eager — `_io.py:57` | Document limitation; suggest parquet path | Or fix: Polars `scan_ndjson` works for some cases |
| 93 | Feature validation per score call — `_model_scorer.py:81` | Cache by `(model_id, schema_hash)` | Cache invalidation when model reloaded? |
| 94 | Redundant fingerprint on trace miss — `trace.py:509` | Pass from preview | Coupling regression? |
| 95 | `columnsEqual` array walk — `usePipelineAPI.ts:50` | Memoize fingerprint on node | Correctness of fingerprint vs. element-wise? |
| 96 | `nodesWithStatus` remap — `useTracing.ts:150` | Memoize computed properties | React Flow still re-renders? |
| 97 | Preview materializes eagerly — `executor.py:385` | Polars `cache_hint` | Memory profile change? |
| 98 | Model cache hit rate unlogged — `_mlflow_io.py:26` | Log hit/miss + expose metric | Noise in logs? Sampling? |
| 99 | `dirty` as separate boolean — `stores/useUIStore.ts` | Derive from `lastSavedRef` comparison | All callers compatible? |
| 100 | State scatter | Consolidate into `useGraphStore` | Does consolidation cause re-renders that scatter avoided? |

**Phase 3 gate:** published benchmark report showing wins / no-change / regressions.

---

## 9. Phase 4 — Frontend inline style migration

Single item spanning 96+ sites. Runs in parallel with all backend phases.

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 71 | 96+ inline `style` mutations in event handlers | Mechanical replacement with Tailwind state classes or CSS variables. Batched by component (5–10 components per sub-PR) for review sanity. | Every site visually identical? Storybook / screenshot diffs per component. No handler logic changed, only styling mechanism. |

Sub-packaging: split into ~10 sub-PRs by component family (form inputs, buttons, chrome, panels, etc.). Each sub-PR gets its own dev + reviewer pair.

---

## 10. Phase 5 — P2 consistency / UX

Each item gets a pair. Parallelizable except where noted.

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 101 | Conditional imports in handlers — `pipeline.py:68, 165, 212` | Hoist to module top | Any cycle that forced the conditional? |
| 102 | Sidecar manual JSON — `routes/_helpers.py:308` | Pydantic `model_dump_json()` | Format change — existing sidecars parse? |
| 103 | Fingerprint algo lacks versioning | Embed version in cache key | Cache-busting acceptable on upgrade? |
| 104 | Trace → `executor._preview_cache` coupling | Event-bus or explicit dependency injection | Overkill for current use? |
| 105 | `--endpoint-suffix` help drift | Shared help string constant | — |
| 106 | `model_name` required-vs-optional | Pick optional everywhere; derive from haute.toml | Break `_status.py` callers? |
| 107 | Dict-detail inconsistency — `utility.py:117` | Standardize on string | (Covered by #76, confirm no regressions.) |
| 108 | `haute init` no `--force` | Add flag + suggestion | Destructive-by-default? |
| 109 | `_train.py` progress bar no flush | Explicit flush | — |
| 110 | `--version-only` returns 0 on missing | Exit code != 0 on missing | Script callers relying on current behaviour? |
| 111 | `haute init` scaffold bloat | **Decision: keep scaffold; target user is actuaries.** No trim. Item resolved — no dev work. | n/a |
| 112 | Trivial starter test | Replace with test that actually imports + runs the starter pipeline end-to-end with sample data | Starter pipeline must pass zero-config on a fresh `haute init` |
| 113 | `starter_utility_features.py` clutter | **Keep.** Actuaries rely on example utility functions as a learning aid and starting point. Item resolved — no dev work. | n/a |
| 114 | Pre-commit chmod on Windows | Skip chmod on Windows; document manual run | — |
| 115 | No retry in API client | Exponential backoff for idempotent ops | User-visible impact of retries (latency)? |
| 116 | `as Record<string, unknown>` casts — `usePipelineAPI.ts:119, 208` | Type guards | Compile-time benefit vs. runtime check cost? |
| 117 | Overly permissive `as Node` — `useSubmodelNavigation.ts:80, 95, 117` | Validation function | Legitimate malformed-node scenarios? |
| 118 | No auth / authz | **Decision: bind to 127.0.0.1 by default.** Require an explicit `--host 0.0.0.0` (or equivalent in `haute.toml`) to expose beyond localhost. No token auth — that's overkill for a dev tool. Add a loud warning when non-localhost binding is enabled. | Is there any test / CI scenario that legitimately needs non-localhost by default? Does the warning actually surface on server start? |
| 119 | CSS magic colour strings | Hoist to `index.css` variables | Theme support implications? |
| 120 | Over-mocked `App.test.tsx` | Integration-style with mocked API only | Test runtime increase acceptable? |
| 121 | No behavioural tests for forms / focus / toast dedup | Add tests | (Enumerated in Phase 7.) |
| 122 | Preserved-block docstring round-trip fragile — `codegen.py:100` | Round-trip test with pathological docstrings | Do any new guard rails break valid user docstrings? |
| 123 | `_sanitize_func_name` strips non-ASCII | Preserve via mapping / escape scheme | Generated Python must still be valid; needs test. |
| 124 | Duplicate sanitized names undetected | **Decision: fix with migration window.** Phase A: detect collisions and log a `warning` with both original names. Phase B (one release later): escalate to a raised `ParseError`. Document the deprecation in release notes. | Does Phase A's warning actually reach users (server logs + UI toast)? Is the "one release later" trigger tracked in a follow-up ticket? |
| 125 | `_extract_function_bodies` tree-optional | Remove optional; require tree | Any caller that can't pass tree? |
| 126 | Per-route `JobStore()` singletons | Central store with prefixes | Breaking handler signatures? |
| 127 | File-watcher broadcast hardcoded | Event bus for sync types | Overkill if only one consumer? |
| 128 | Submodel navigation parent/child refs | View stack in `useUIStore` | Re-render cost? |
| 129 | Pipeline-file resolution inconsistent | Single `resolve_pipeline_file()` helper, called everywhere | Any command that has a legitimate reason to skip? |
| 130 | CLI business logic + plumbing tangled | Extract `handle_*(config)` pure functions | Test burden reduction real? |
| 131 | `_load_deploy_config()` multi-param magic | `DeployConfig.from_toml()` / `from_cli_args()` | Two constructors better than one? |
| 132 | Logging style inconsistent | Single convention | Which — `click.echo` or `structlog`? Decide first. |

---

## 11. Phase 6 — P3 stdlib / polish

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 133 | `graphlib.TopologicalSorter` | Replace `_topo.py` Kahn | Error messages still user-friendly? |
| 134 | `functools.lru_cache` | Replace `_lru_cache.py` non-persistent use | Persistence-needed sites remain on custom impl |
| 135 | `diskcache` for preview | **Decision: reject.** In-memory cache is sufficient for a local dev tool; restart-persistence isn't a reported pain point. Do not add the dependency. Item closed. | n/a |
| 136 | Regex kwarg extraction via mini-AST — `_parser_regex.py:136` | `ast.parse(f"f({kwargs})")` | Covers every current regex case? |
| 137 | User-code AST walk for return boundaries | Replace line heuristics | Fidelity on nested functions? |
| 138 | Typer / Pydantic CLI | **Decision: reject.** Click is working; migration churn isn't justified. Item closed. | n/a |
| 139 | `Path.cwd()` scattered | (Use F7 `get_project_root()`.) | — |
| 140 | MLflow signatures unused | (Covered by F4 + #2.) | — |
| 141 | `uv` container base | `FROM ghcr.io/astral-sh/uv:latest` + `uv sync --frozen` | Image size change? Startup time? |
| 142 | Pruner custom reachability | **Decision: conditional reject.** Do NOT rebuild unless dev first writes a comprehensive test suite covering every current pruner case (liveSwitch branches, sanitized-label edge matching, submodel ports). Only if tests pass on the current impl AND a BFS rebuild makes the code meaningfully clearer, proceed. Default: leave untouched. | Is the test suite comprehensive enough to catch a regression? Does the BFS version pass identical tests? |
| 143 | `ScoringModel` proxy | (Covered by #58.) | — |
| 144 | `run_in_threadpool` for sync I/O | (Covered by #30.) | — |

---

## 12. Phase 7 — Missing test scenarios

Each test scenario gets a pair. Attaches to whichever phase first makes the scenario coverable.

| # | Scenario | Dev | Reviewer gate | Attaches to |
|---|---|---|---|---|
| 145 | Empty pipeline (no nodes) | Parser + codegen + executor round-trip test | Does the system have a defined empty-pipeline behaviour? If not, define first. | Phase 1A |
| 146 | Single-node pipeline | Source-only, sink-only variants | Any asymmetry between sources and sinks? | Phase 1A |
| 147 | Cycle error message quality | Human-readable cycle description | Does the message name the nodes in the cycle? | Phase 6 (#133 replaces Kahn) |
| 148 | Duplicate node names | Collision detection test | (Part of #124.) | Phase 5 |
| 149 | Unicode in descriptions/labels | Round-trip test | (Part of #123.) | Phase 5 |
| 150 | Very long descriptions | Line-limit handling | Fail loud on overlength? | Phase 1A |
| 151 | 10-cycle parse→codegen round-trip | Drift detection | Exact-equal or normalized-equal? | Phase 2A |
| 152 | Pathological docstrings | `"""` / `\` at end, nested triple-quote | (Part of #122.) | Phase 5 |
| 153 | Decorator kwarg ordering stability | Multi-Python-version test | CI matrix exists? | Phase 6 |
| 154 | ConfigInput/ConfigSelect behavioural | Submission, invalid input | (Part of #70.) | Phase 2D |
| 155 | Modal focus-trap | Tab, Shift+Tab, ESC, nested | (Part of #41.) | Phase 1H |
| 156 | Toast dedup | Rapid identical errors | (Part of #40.) | Phase 1H |

---

## 12b. Phase 8 — Deferred Phase 2 polish

Residual non-blocking items from the Phase 2, 3, 4, and 5 audits that
were judged worth closing out but would have churned their respective
phase's completion record.  Added here (rather than retroactively into
the host phase) so each phase's completion stays auditable and these
items get their own scoped review.  #157–#160 came from Phase 2; #161
came from Phase 3 Wave 7E; #162–#163 came from Phase 4 and Phase 3
fail-loud audits; #164–#167 came from Phase 5 Wave 9 audits.  Each is
independent and can run in parallel; the parser-shim item (#157) needs
the largest test migration (~30 files), and the `graphVersion` item
(#161) is the most invasive store refactor.

| # | Item | Dev | Reviewer gate |
|---|---|---|---|
| 157 | `_parser_helpers.py` shim trampolines — `_config_builder._warn_unrecognized_config_keys` / `_load_node_config` are late-bound through the shim solely so ~30 test files' `patch("haute._parser_helpers.X")` sites keep working.  Production code is load-bearing for tests. | Migrate the ~30 `patch("haute._parser_helpers.<name>")` sites to patch at the real home (`haute._config_builder`, `haute._config_validation`, etc.).  Delete the late-binding trampolines in `_config_builder.py`.  Shrink the `_parser_helpers.py` shim to only the genuinely re-exported names.  | Did the patch-site count drop to zero?  Do all migrated tests still pin what they claim to pin (same assertions, different patch target)?  Is the shim smaller, or did something else grow in its place? |
| 158 | `_builders.py` → `haute.executor._exec_user_code` layering inversion — `_builders.py:413, 501, 674, 899` use lazy in-function `from haute.executor import _exec_user_code` to paper over an import cycle.  The dependency violates the declared layering (`_builders` is lower-level than `executor`). | Move `_exec_user_code` to a dedicated `src/haute/_user_exec.py` module.  Both `_builders.py` and `executor.py` import it at top level.  Delete the four lazy-import bodies. | Does `_user_exec.py` own a cohesive concern (user-code execution + error enrichment), or is it just a parking spot?  Any new cycle introduced?  Does the traceback for a user-code error still name `_exec_user_code` clearly? |
| 159 | `useConfigEstimate` + `useConfigStaleness` merge — both hooks are always called together in `ModellingConfig.tsx` and `OptimiserConfig.tsx`, and consumers must manually pass the same `configHash` to both.  The coupling is implicit today. | Merge into a single `useStaleConfigEstimate(config, cachedResult, endpoint, ...)` hook that owns both the hash and the fetch.  Delete the two old hooks + their tests; write focused tests on the merged hook. | Does the merged hook expose the same control surface (loading / error / estimate) as the pair?  Is any consumer forced to call the old split-hook pattern?  Any regression in re-render count? |
| 160 | Split `WaterfallErrorAlert` out of `WaterfallChart.tsx` — `WaterfallChart.tsx` houses both the chart (150 LoC) and the independent `WaterfallErrorAlert` component (31 LoC).  The alert is reusable; keeping it co-located blocks sharing with `CalculationHero.tsx`'s inline alert (which styles identically but lives independently). | Extract `WaterfallErrorAlert` into `frontend/src/trace/WaterfallErrorAlert.tsx`.  Import from `WaterfallChart.tsx` and `CalculationHero.tsx` (replacing its inline alert for DRY). | Is the alert component genuinely generic (no waterfall-specific assumptions)?  Does the DRY'd `CalculationHero` path still render the same a11y role + message copy?  Was a wider "generic error alert" helper considered? |
| 161 | `graphVersion` cross-store effect (Phase 3 Wave 7E audit finding) — `App.tsx:192-204` hashes `(nodes, edges)` on every render and imperatively calls `useNodeResultsStore.bumpGraphVersion()`.  Exactly the effect-coupling smell Wave 7E was supposed to remove.  Deferred from Wave 7E because the simple fixes (`graphVersion = undoStack.length`; fire `bumpGraphVersion` from `pushSnapshot`) both break preview caching — drag-start pushes a snapshot even when nothing structural changed, so every drag would invalidate every cached preview. | Add a `structuralVersion: number` field to `useGraphStore`.  Bump it only inside `setNodes`/`setEdges`/`setNodesRaw`/`setEdgesRaw` when the structural fingerprint (ids + edge pairs; NOT positions or selection) actually changes.  Then derive `graphVersion` from `structuralVersion`, or delete `useNodeResultsStore.graphVersion` entirely and have consumers subscribe to the store directly.  Delete the App.tsx effect. | Is the structural-fingerprint definition exactly right (does it catch joins + rewires but ignore drag/hover/selection)?  Does the preview cache still invalidate at the right moments?  Benchmark: re-render count on a 200-node graph during drag — must not spike. |
| 162 | Hard-coded color literals in `.tsx` files (Phase 4 audit follow-up) — ~15 `.tsx` files inline `style={{ color: "#ef4444" }}` / `color: "#22c55e"` / similar for error, success, neutral state pins instead of referencing CSS custom properties.  Phase 4 only tokenised the new `.hover-*` classes in `index.css`; the inline `.tsx` color literals predate Phase 4 and were left untouched.  Files affected include `components/CacheFetchButton.tsx`, `components/Toast.tsx`, `components/Toolbar.tsx`, `panels/DataPreview.tsx`, `panels/editors/banding/*.tsx`, `panels/editors/BandingEditor.tsx`, `panels/editors/GroupedColumnsTab.tsx`, `nodes/PipelineNode.tsx`.  Two new tokens from Phase 4 audit (`--danger`, `--signif-high`, etc.) already cover the colors. | Grep all hard-coded `#ef4444`, `#22c55e`, `#eab308`, `#60a5fa`, `#dc2626`, `#b91c1c` literals in `.tsx` / `.ts` files.  Replace each with the matching design token (`var(--danger)`, `var(--signif-high)`, `var(--signif-marginal)`, `var(--text-accent)`, `var(--danger-solid)`, `var(--danger-hover)`).  For Tailwind arbitrary values like `hover:text-[#ef4444]`, migrate to `hover:text-[var(--danger)]`. | Does the rendered visual match byte-for-byte after the swap? (Token values are the exact same hex.) Any site where the inline color was intentionally different from the token? (If yes: either add a new token or keep the hex with a comment naming the reason.) |
| 163 | Preamble `_DANGEROUS_MODULES` check uses `.__name__` string comparison (Phase 3 fail-loud audit NON-BLOCKING #12) — `executor.py` filters dangerous modules out of the compiled preamble namespace by checking `v.__name__ in _DANGEROUS_MODULES`.  An indirect reference like `my_helper = os.path.join` has `__name__ == "join"` so it passes the filter.  The upstream AST validator catches most of these but the module-level filter is currently trust-by-name. | Check by module identity (`v is not os`, `v is not subprocess`) or by membership in a frozenset of actual module objects resolved at import time.  Keep the AST validator as the primary defence and tighten this as secondary defence-in-depth. | Does the new check still permit legitimate wrapped helpers (e.g. `safe_join = os.path.join` should flag as dangerous OR be allowed — product decision)?  Any false positives on unrelated modules sharing `__name__`? |
| 164 | ~~Phase 5 #124 Phase B — escalate duplicate-sanitized-name warning to `ParseError`~~ **RESOLVED** — shipped directly as Phase B (raise) in the Phase 5 audit pass, since Haute has no deployed user base that needed a migration window.  `_error_on_name_collisions` in `src/haute/codegen.py` now raises `ParseError` naming every colliding bucket; the test class in `tests/test_parser_sanitize_phase5.py` was renamed from `…Warning` to `…Raise` and its assertion switched to `pytest.raises(ParseError)`. | — | — |
| 165 | ~~`EventBus` payload typing upgrade (Phase 5 Wave 9E audit) — `src/haute/_event_bus.py` uses `PayloadType = dict[str, Any]` for V1.~~ **RESOLVED** — shipped `TypedDict` per event (`GraphUpdatePayload`, `ParseErrorPayload`) + `Literal`-keyed `@overload`s on `publish` / `subscribe` so every call site is type-checked against the event it names.  The wide `dict[str, Any]` fallback remains for ad-hoc test events. | — | — |
| 166 | ~~`default_bus` test-reset fixture + unbounded `get_job_store` cache (Phase 5 Wave 9E + 9C audits).~~ **RESOLVED** — `tests/conftest.py` now has a session-scoped `_default_bus_baseline` (imports `haute.server` first so WS subscribers register before the snapshot) + per-test `_default_bus_test_isolation` autouse fixture that restores the baseline after every test.  `src/haute/routes/_job_store.py::get_job_store` closed to a `_KNOWN_PREFIXES = frozenset({"training", "optimiser"})` allow-list; unknown prefix raises `ValueError`. | — | — |
| 167 | `pipeline_dir()` in `src/haute/routes/_helpers.py:106-108` still uses function-local imports (`tomllib`, `haute.errors.ConfigError`) — missed by Wave 9C's #101 sweep because that item was scoped to `routes/pipeline.py`.  Same class of smell. | Hoist `tomllib` and `ConfigError` to module top in `routes/_helpers.py`.  Verify no cycle is introduced (unlikely — `errors.py` is a leaf). | Any circular import triggered?  Does the `@lru_cache(maxsize=1)` on `pipeline_dir` still work identically once imports are hoisted? |
| 168 | ~~`useUIStore.viewStack` has zero production consumers (Phase 5 Wave 10C / Phase 5 holistic audit) — the store slice + selectors (`pushView`, `popView`, `clearViews`, `currentView`) ship and are pinned by tests, but `App.tsx` / `BreadcrumbBar` / everything else still consume `useSubmodelNavigation`'s local ``useState<ViewLevel[]>``.  Two navigation models coexist.  Shipping a store abstraction with zero consumers is exactly the CLAUDE.md "premature abstraction" anti-pattern.~~ | **Decision: direction (b) — delete the store-side slice.** `ViewStackEntry` (`kind: "root" \| "submodel"`, optional `name`, `returnTo`) and the existing `ViewLevel` shape (`type`, `name`, `file`, `_savedNodes`, `_savedEdges`) differ materially — migration would require leaking graph state (`_savedNodes`/`_savedEdges` used for back-nav restoration) into `useUIStore`, which violates the store boundary documented in the file header ("Graph-shaped state... live in useGraphStore"). The existing `useSubmodelNavigation` local state is tight and correct. | Resolved: deleted `viewStack`, `pushView`, `popView`, `clearViews`, `currentView`, and `ViewStackEntry` from `frontend/src/stores/useUIStore.ts`. Removed the `#128` describe block (20 tests) from `frontend/src/hooks/__tests__/typeSafety.phase5.test.ts` and the now-unused `useUIStore` / `renderHook` / `cleanup` / `act` / `beforeEach` / `afterEach` imports. `ViewLevel` stays in `BreadcrumbBar.tsx` as the single source of truth for submodel navigation. |

**Phase 8 gate:** a single reviewer audits that none of the seven items
introduced new seams where they were supposed to remove them (e.g. #157's
trampoline-free shim must not reappear as a different abstraction; #161's
`structuralVersion` derivation must not accidentally re-introduce the
cross-store effect with a different variable name).

---

## 13. Items to debate before starting (reject candidates)

These items are legitimately real but the reviewer may conclude the right answer is "don't do this." Discuss with the user before dev agents start:

All 10 reject-candidate items are now resolved. No items remain open for user decision.

| # | Item | Decision |
|---|---|---|
| 54 | Regex fallback parser | **Partial fix** — keep fallback, replace lossy kwarg regex with `ast.parse(f"f({kwargs})")` only |
| 57 | Column contract system | **Adopt everywhere** — wire through codegen, parser, executor |
| 58 | `ScoringModel` wrapper | **Drop** — explicit flavor dispatch at callsites |
| 60 | Dead GLM CV path | **Delete** — dev first confirms zero callers |
| 111 | `haute init` scaffold | **Keep** — target user is actuaries; no trim |
| 113 | `starter_utility_features.py` | **Keep** — learning aid for actuaries |
| 118 | No auth / authz | **Bind 127.0.0.1 by default**, require explicit flag to expose; no token auth |
| 124 | Duplicate-name detection | **Fix with migration window** — warn first, raise one release later |
| 135 | `diskcache` for preview | **Reject** — in-memory cache sufficient |
| 138 | Typer CLI migration | **Reject** — Click is fine |
| 142 | Pruner simplification | **Conditional reject** — only proceed if comprehensive test suite written first AND BFS rebuild is meaningfully clearer; default leave untouched |

Phase 2 is cleared to start once Phase 0 + Phase 1 complete.

---

## 14. Progress tracking

Use a single checklist file `docs/CODEBASE_REVIEW_PROGRESS.md` (not created yet) with 156 checkboxes + 12 test scenarios + 7 foundation tasks. Each item has fields: `status` (pending / in-progress / review / done / rejected), `dev` (agent name), `reviewer` (agent name), `verdict` (approve / changes / reject), `notes`. Mark items as they move through.

Phase gates are tracked as separate checklist items — cannot be ticked until every item in that phase is done or explicitly rejected.

---

## 15. Summary

- **180 items** (156 findings + 12 tests + 12 Phase-8 deferred), **7 foundation tasks**, **two-agent pair per item**, **phase-level review** after each phase.
- Reviewer is a **gate**: rejects changes that don't improve the codebase, not just ones that have bugs.
- **Foundation first** (shared helpers), then **fix P0**, then **architecture P1**, then **perf P1**, then **frontend style sweep** (parallel), then **consistency P2**, then **polish P3**. Tests weave through.
- **All 10 reject-candidate items resolved** — no items blocking phase start.

Estimated effort, ballpark:

| Phase | Items | Effort |
|---|---|---|
| 0 | 7 | 3–5 days |
| 1 | 51 | 10–15 days |
| 2 | 15 | 10–14 days |
| 3 | 15 | 6–8 days |
| 4 | 1 (96+ sites) | 3–4 days |
| 5 | 32 | 8–10 days |
| 6 | 12 | 4–6 days |
| 7 | 12 | Woven (effort counted in host phase) |
| 8 | 7 | 3–5 days |
| **Total** | **175** | **~45–60 days** of agent work, with significant parallelism available |

With 4–6 agent pairs working in parallel, real calendar time is materially shorter.
