# Codebase Review

**Date:** 2026-04-17
**Scope:** Full review of the Haute codebase — backend (`src/haute/`, ~34K LOC Python) and frontend (`frontend/src/`, ~25K LOC TypeScript).
**Method:** Seven parallel specialist reviewers, one per subsystem, synthesised into this document.
**Goal:** Identify correctness bugs, overengineered solutions, performance wins, and architectural gaps — to raise the codebase to the highest engineering standard.

---

## Executive summary

The core is fundamentally sound: AST-first parsing with Pydantic contracts as the single source of truth across parser/codegen/executor, React Flow + Zustand on the frontend, clean route layering, atomic file writes, and a thoughtful sandbox for user code.

The biggest systemic issue is a **stated principle violation**. `CLAUDE.md` says *"Don't add unnecessary fallbacks — code should fail loudly so we can fix errors."* Yet the codebase contains 40+ `except Exception:` handlers across the backend and scattered silent `catch {}` / optional-chain fallbacks across the frontend that mask real bugs. Closing this gap is the single highest-leverage change available.

Beyond that, there are concrete correctness risks in the ML train→score handoff (feature order, no MLflow signature), concurrency gaps around file-sync, several ~1000-line god files that should be split, and a handful of places where three overlapping abstractions should be consolidated into one.

---

## Full priority list

All findings across the seven reviewers, tiered by severity × leverage. Tiers:

- **P0 — Critical**: correctness bugs causing silent wrong results, security gaps, real data-loss or data-exposure risks.
- **P1 — High**: architectural problems with real maintenance cost, measurable performance issues, concurrency races that can fire in normal use.
- **P2 — Medium**: UX / consistency / contract drift; overengineering that isn't actively breaking things; missing test scenarios.
- **P3 — Low**: stdlib / ecosystem swaps; mechanical cleanups; polish.

### P0 — Critical (correctness, security, data integrity)

| # | Issue | Where | Why it matters |
|---|---|---|---|
| 1 | Training→scoring feature/categorical order mismatch | `_algorithms.py:283`, `_mlflow_io.py:556–590`, `_model_scorer.py:123` | Silent wrong predictions; no MLflow signature catches it |
| 2 | No MLflow model signature logged | `_mlflow_log.py` | Deployed artifact has no authoritative contract; all downstream guards are heuristic |
| 3 | Trace / enrichment swallows 15+ exceptions silently | `trace.py:1056, 1095, 1180, 1196, 1216, 1240, 1246, 1291, 1298, 1322, 1359, 1361, 1467` | Violates project principle; incomplete traces with no signal |
| 4 | `swallow_errors=True` retry on pipeline failure | `trace.py:577–607` | Regex-matches error string to decide whether to hide — real bugs disappear |
| 5 | Waterfall build silently swallows all exceptions | `trace.py:872` | Schema change or NaN silently produces `waterfall=None` |
| 6 | WebSocket `ws_clients` set mutated without lock | `server.py:183–191` | Async race: broadcast vs. disconnect vs. cleanup |
| 7 | File-watcher vs. route-writes race (2s cooldown) | `server.py:289–310`, `_helpers.py:174–191` | Saves >2s cause watcher to re-parse partial writes |
| 8 | WebSocket sync bypasses undo/redo history | `useWebSocketSync.ts:70–91` | Ctrl+Z after a file-sync corrupts the stack |
| 9 | Cache fingerprint uses `json.dumps(default=repr)` | `_cache.py:27, 46` | Non-deterministic for sets / unordered dicts → silent collision |
| 10 | mtime-based cache keys (TOCTOU) | `_io.py:96`, `_optimiser_io.py:45` | Serves stale data; writer between `getmtime()` and read |
| 11 | Error details leaked to HTTP clients | `files.py:111`, `submodel.py:45`, `git.py:67` | `str(exception)` exposes paths / git output / internal state |
| 12 | Path traversal gap in `graph_to_code_multi()` output | `_save_pipeline.py:139–152` | `is_relative_to` check happens after resolution; symlink edge cases can escape |
| 13 | Categorical type mismatch warning-only, scoring proceeds | `_model_scorer.py:123–128` | CatBoost silently casts → wrong results. Must raise `FeatureMismatchError` |
| 14 | Pruner doesn't validate static dataSource schema | `_pruner.py:82–118` | CSV with columns reordered → silent prediction corruption on deploy |
| 15 | GLM column selection drops categorical metadata | `_training_job.py:262–264` | Term trained as categorical but not marked → scorer mismatch |
| 16 | Test-quote scoring errors captured, never raise | `_validators.py:141–151` | Deploy validation passes even if all test quotes failed |
| 17 | No hash of trained feature list → no runtime match check | `_bundler.py:88`, `_scorer.py:200` | Deployed model has no guarantee it matches training contract |
| 18 | Silent config-path recovery on Windows | `_parser_helpers.py:985–999` | Scans by `func_name`, suppresses original error; `_load_error` marker inconsistently respected |
| 19 | Instance mapping silently overrides stale explicit entries | `codegen.py:884–887`, `_types.py:597–642` | User's `inputMapping` from prior graph version silently replaced; no warning |
| 20 | Submodel cross-boundary edge resolution unvalidated | `codegen.py:1180–1189`, `_parser_submodels.py:125–133` | Malformed `targetHandle` silently wires edge to wrong node |
| 21 | Graph fingerprint cache extend-path retains stale data | `executor.py:391–449` | Node deleted and re-added with same ID leaves prior outputs in cache |
| 22 | Polars codegen empty-code edge case fragile | `codegen.py:813–829`, `_wrap_user_code:357` | Works today but fragile to refactor |
| 23 | Discovery silently skips unreadable files | `discovery.py:63–66, 78–81` | Corrupted / permission-denied project silently disappears |
| 24 | Schema endpoint catches broad `Exception` and returns 500 | `files.py:114–119` | Hides real bugs (KeyError, AttributeError, Polars internals) |
| 25 | Model-score column detection swallows config errors | `_builders.py:601–602` | Users get silent passthrough nodes with no hint why |
| 26 | Seven diagnostic exception handlers silently skip | `_training_job.py:800, 816, 838, 843, 848, 857, 886` | SHAP / feature importance / GLM coefs / regularization / CV all best-effort silently |
| 27 | Artifact download delete-and-retry masks corruption | `_mlflow_io.py:514–533` | Network-flakiness corruption retried once without exponential backoff |
| 28 | `_job_store.update_job()` mutates without atomic swap | `_job_store.py:68` | Relies on GIL; breaks on PyPy or under threading tuning |
| 29 | Git routes have `_fetch_time_lock` but no lock around fetch | `git.py:37` | Two threads can both run `git fetch` concurrently |
| 30 | Sync I/O blocks async event loop | `_io.py`, `_mlflow_io.py` | Long model loads / parquet metadata reads stall FastAPI |
| 31 | Silent AbortError leaves stale preview in panel | `usePipelineAPI.ts:217–221` | Panel shows prior node's data when user clicks away |
| 32 | Eager derived-cache cleanup on node delete | `useNodeHandlers.ts:44–58` | UI flips from cached result to `null` mid-render |
| 33 | Zustand `.getState()` reads in async callbacks undeclared as deps | `useWebSocketSync.ts:86`, `usePipelineAPI.ts:160`, `useKeyboardShortcuts.ts:186` | Hidden deps; refactors silently break |
| 34 | Stale `activeSourceRef` in preview cascade | `usePipelineAPI.ts:93–96, 121` | User switches source mid-cascade → column mismatch |
| 35 | Drag-drop config JSON parse `catch { /* ignore */ }` | `useEdgeHandlers.ts:145–147` | Creates nodes with empty config silently |
| 36 | Uncontrolled `RenameDialog` input with no validation | `RenameDialog.tsx:51` | No length limits, no sanitization, `defaultValue` + `ref.current.value` on submit |
| 37 | Catch-all try/catch in WebSocket message handler | `useWebSocketSync.ts:54–100` | `getLayoutedElements` throw leaves state half-applied |
| 38 | Unchecked `.find()` + cast to `SimpleNode \| null` | `App.tsx:492–494` | Stale `lastSelectedId` after WebSocket sync points to deleted node |
| 39 | Panel state not cleared on WebSocket-driven graph changes | `useWebSocketSync.ts:86–87` | `submodelDialog` / `renameDialog` reference deleted node IDs |
| 40 | Missing toast de-duplication | `Toast.tsx`, `useToastStore.ts` | Rapid identical errors flood UI |
| 41 | Incomplete modal focus trap | `ModalShell.tsx:48–63` | Focus leaks past sentinels on empty panels or nested modals |
| 42 | `string.replace()` on pyproject.toml can corrupt file | `_init_cmd.py:42–50` | Multiple `dependencies = [` sections silently munged |
| 43 | String-grep dependency detection fragile | `_init_cmd.py:40` | Matches commented lines / docstrings (`# Use haute for...`) |
| 44 | `uv pip install` install instruction wrong | `_status.py:33` | Other commands say `uv add`; users following this fail |
| 45 | CI detection relies on deprecated env vars | `_deploy.py:40` | Misses GitHub Actions / CircleCI / Azure DevOps reliably |
| 46 | `haute serve` no port-conflict detection | `_serve.py:57–67` | uvicorn fails silently on port clash |
| 47 | Container base image unpinned (`python:3.11-slim`) | `deploy/_config.py:41` | Deployed containers silently diverge from training |
| 48 | Static dataSource paths re-resolved at deploy time | `_bundler.py:112–125` | At container runtime CWD may not exist |
| 49 | Silent `prod_exists=False` on any exception | `_impact.py:196–228` | Can't distinguish "doesn't exist" from "network error" |
| 50 | `_save_pipeline` has 5 responsibilities without transaction | `routes/_save_pipeline.py` | Late-step failure orphans earlier writes |
| 51 | Node positions silently lost on rename via sanitized IDs | `_helpers.py:325–354` | No warning; user work disappears |

### P1 — High (architecture, performance, concurrency)

| # | Issue | Where | Why it matters |
|---|---|---|---|
| 52 | Four user-code extractors with overlapping logic | `_parser_helpers.py:261–530` | ~300 LOC that could be ~100 via shared engine |
| 53 | Three overlapping cache systems | `_cache.py`, `_fingerprint_cache.py`, `_lru_cache.py` | Callers must know which to use; `FingerprintCache` reimplements LRU eviction |
| 54 | Regex fallback parser duplicates AST work, loses fidelity | `parser.py:122–126`, `_parser_regex.py` | Binary AST-vs-regex choice; regex loses booleans/floats in decorator kwargs |
| 55 | Parallel `_NODE_BUILDERS` / `_CODEGEN_BUILDERS` dispatch tables | `_builders.py:102–135`, `codegen.py:392–402` | Silent divergence; forgotten codegen entry falls back to transform |
| 56 | Graph flattening duplicated | `_parser_submodels.py:135–141` vs. `_flatten.py` | Two paths that can drift |
| 57 | Column contract system underutilized | `_execute_lazy.py:38–98`, `_builders.py:107–146` | Infrastructure exists, not adopted by codegen / parser / executor |
| 58 | Uniform `ScoringModel` wrapper adds abstraction cost | `_mlflow_io.py:38–83` | `__getattr__` proxy for 3 flavors; real branching still at callsite |
| 59 | Duplicate batch + eager scoring paths | `_model_scorer.py:206–209`, `_score_batched_standalone` | Paths not tested equally; context-variable branching |
| 60 | Dead GLM cross-validation path, swallowed exceptions | `_training_job.py:865–887` | Speculative, unclear usage |
| 61 | God file: `_parser_helpers.py` (1016 LOC) | | Mixes AST utils, config building, code extraction, edge building |
| 62 | God file: `_types.py` (669 LOC) | | Pydantic types + utilities mixed |
| 63 | God file: `codegen.py` (1298 LOC) | | Orchestration + builder registration |
| 64 | God file: `trace.py` (1500+ LOC) | | Preview / trace / enrichment / waterfall separable |
| 65 | God component: `OptimiserPreview.tsx` (1081 LOC) | | Chart + state + save/log + frontier + tabs + detail cards |
| 66 | God component: `CalculationHero.tsx` (893 LOC) | | Waterfall + expression chain + input sources |
| 67 | 200+ LOC duplication between `ModellingConfig` & `OptimiserConfig` | `panels/ModellingConfig.tsx`, `panels/OptimiserConfig.tsx` | Extract `useConfigEstimate` + `useJobPolling` |
| 68 | 15-level prop drilling in NodePanel | `panels/NodePanel.tsx` | `allNodes`, `edges`, `submodels`, `preamble` threaded through many levels |
| 69 | Shell trinity: `ModalShell` + `PanelShell` + `PanelHeader` | `components/` | Ceremony without preventing bugs |
| 70 | `ConfigInput`/`ConfigSelect` trivial wrappers | `panels/editors/` | 75-line wrappers for label + focus-style mutation |
| 71 | 96+ inline `style` mutations in event handlers | across components | Breaks concurrent rendering; bypasses reconciliation |
| 72 | Three-layer preview-propagation indirection | `usePipelineAPI.ts:101–140` | `propagatingRef` Set + `propagateRef` function + Promise.all |
| 73 | `isDragging` ref reimplements React Flow logic | `useUndoRedo.ts:61–88` | Inspect `NodeChange` array once per effect instead |
| 74 | Dual-layer error conversion in git routes | `routes/git.py:61–77`, `_dc_to_pydantic()` | Unnecessary dataclass → Pydantic shim |
| 75 | Manual pipeline index invalidation with two global caches | `routes/_helpers.py:198–285` | Brittle; async handler scheduled after invalidation sees stale cache |
| 76 | Error response shape drift (`detail: string` vs `detail: dict`) | `routes/utility.py:117`, `routes/pipeline.py:192` | Frontend must handle both |
| 77 | Save service has no transactional guarantees | `routes/_save_pipeline.py:139–152` | Use write-to-temp-then-rename uniformly |
| 78 | Defensive Node PATH patching | `cli/_helpers.py:95–105` | Should fail loudly if Node missing |
| 79 | Browser-open 4-fallback chain | `cli/_helpers.py:72–92` | `xdg-open` → `open` → webbrowser → webbrowser again |
| 80 | `_find_frontend_dir()` returns `None` silently | `cli/_helpers.py:123–130` | Callers treat absence as "not in dev mode" rather than error |
| 81 | `click.UsageError` inconsistent with rest of CLI style | `cli/_smoke.py:48–51` | 7 other commands use `click.echo + SystemExit(1)` |
| 82 | Three-fallback `staging_suffix` opaque to users | `cli/_impact.py:49` | `endpoint_suffix or config.ci.staging_endpoint_suffix or "_staging"` |
| 83 | `console.warn` on errors that should be toasts | `OptimiserPreview.tsx:151–155`, `OptimiserConfig.tsx:150+` | Users get no visible feedback |
| 84 | Optional-chain fallback hides missing `instanceOf` | `NodePanel.tsx:84–86` | UI shows `"undefined"` instead of surfacing bug |
| 85 | Silent `null` returns in `CalculationHero.tsx` for missing data | | Hides errors rather than showing `<ErrorUI />` |
| 86 | Graph fingerprint recomputed every preview | `executor.py:385` | Memoize on `PipelineGraph` via `@cached_property` |
| 87 | `_compute_needed_columns` O(n²) backward pass | `_execute_lazy.py:38–98` | Single forward pass is O(n) |
| 88 | Preamble cache manual `OrderedDict + popitem` eviction | `executor.py:88–222` | `functools.lru_cache` is O(1) and battle-tested |
| 89 | SHA-256 for local cache keys | `_cache.py:27, 46` | `xxhash` is 10–100× faster; collision risk irrelevant |
| 90 | `JSON.stringify(n.data)` on every render | `App.tsx:188–198` | Large config objects dominate fingerprint cost |
| 91 | Polars ↔ pandas churn with no categoricals present | `_mlflow_io.py:556–590` | Wasted allocation; keep Polars where possible |
| 92 | JSON data input always fully eager | `_io.py:57–60` | 100MB JSON file fully loaded even for 10-row preview |
| 93 | Feature validation redundant per score call | `_model_scorer.py:81–130` | Cache keyed by `(model_id, schema_hash)` |
| 94 | Redundant fingerprinting on trace miss | `trace.py:509` | Pass through from preview |
| 95 | `columnsEqual` array walk per preview completion | `usePipelineAPI.ts:50–54` | Memoize fingerprint on node data |
| 96 | `nodesWithStatus` remap on every change (8 deps) | `useTracing.ts:150–174` | Memoize each node's computed properties |
| 97 | Preview path materializes eagerly at every node | `executor.py:385–438` | Polars `cache_hint()` or output-only materialization |
| 98 | Model cache hit rate not logged | `_mlflow_io.py:26–30` | Thrashing is invisible |
| 99 | `dirty` flag as separate boolean, not derived | `stores/useUIStore.ts` | Save `.then()` after local undo corrupts `lastSavedRef` |
| 100 | State scatter across refs + state + Zustand + other refs | `App.tsx`, `useWebSocketSync.ts` | `graphRef` + `nodes/edges` + `useUIStore` + `useNodeResultsStore` + `submodelsRef` |

### P2 — Medium (consistency, UX, contract drift, tooling)

| # | Issue | Where | Why it matters |
|---|---|---|---|
| 101 | Conditional imports inside route handlers | `routes/pipeline.py:68, 165, 212` | Scatter import graph; move to top-of-module |
| 102 | Sidecar manual JSON serialization | `routes/_helpers.py:308–322` | Pydantic `model_dump_json()` is simpler |
| 103 | Fingerprint algorithm lacks versioning | `_cache.py` | Algorithm change won't invalidate stale entries |
| 104 | Trace depends on `executor._preview_cache` | `trace.py:33`, `_train_service.py:478–481` | Hidden coupling; manual invalidation |
| 105 | `--endpoint-suffix` help text drift | `cli/_smoke.py:14`, `cli/_impact.py:12` | Should be identical |
| 106 | `model_name` required vs. optional drift | `cli/_status.py` vs. `cli/_deploy.py` | Pick one policy |
| 107 | `HTTPException` dict-detail inconsistent with string-detail elsewhere | `routes/utility.py:117–121` | `schemas.py` contract doesn't reflect it |
| 108 | `haute init` fails on existing `haute.toml` without `--force` suggestion | `cli/_init_cmd.py:125` | User must manually delete |
| 109 | Progress bar `nl=False` without flush | `cli/_train.py:62–64` | May not update until buffer fills |
| 110 | `--version-only` returns 0 on missing model | `cli/_status.py:39` | Deploying `version=0` silently wrong |
| 111 | `haute init` scaffold creates 20+ files | `cli/_init_cmd.py`, `_scaffold.py` | Trim to ~5; gate extras behind flags |
| 112 | Trivial starter `tests/test_pipeline.py` gives false security | `_scaffold.py` | Only checks file is valid Python + contains `"haute.Pipeline"` |
| 113 | `starter_utility_features.py` clutters every new project | `_scaffold.py` | 100+ lines of example code |
| 114 | Pre-commit hook `chmod 0o755` fails silently on Windows | `cli/_init_cmd.py:228` | Hook doesn't auto-run |
| 115 | No retry logic in API client | `frontend/src/api/client.ts` | Transient 5xx / timeouts fail immediately |
| 116 | `as Record<string, unknown>` casts hide shape errors | `usePipelineAPI.ts:119, 208` | Non-null assertion after optional chaining |
| 117 | Overly permissive `as Node` casts | `useSubmodelNavigation.ts:80, 95, 117` | Malformed submodel response → undefined port IDs |
| 118 | No auth / authz on any endpoint | `server.py`, `routes/` | Local-only by convention; port-forward or tunnel exposes all |
| 119 | CSS magic colour strings scattered in components | across `panels/`, `components/` | Hoist to CSS variables in `index.css` |
| 120 | `App.test.tsx` mocks every hook and component | `frontend/src/__tests__/App.test.tsx` | Brittle to refactor; prefer integration tests |
| 121 | No behavioural tests for form components / modal focus / toast dedup | `__tests__/` | Critical interactions untested |
| 122 | Preserved-block `# haute:preserve-start/end` docstring round-trip fragile | `codegen.py:100–138` | Needs pathological-docstring round-trip tests |
| 123 | `_sanitize_func_name` strips non-ASCII | `_types.py` | Round-trip loses unicode labels silently |
| 124 | Duplicate sanitized node names with different originals not detected | `_types.py` | No collision detection |
| 125 | `_extract_function_bodies` tree-optional parameter invites future double-parse | `parser.py:132`, `_parser_helpers.py:549–553` | Always pass tree from caller |
| 126 | Central `JobStore()` singletons created per route | `routes/modelling.py`, `routes/optimiser.py` | Namespace-by-UUID implicit; central store with explicit prefixes clearer |
| 127 | File-watcher broadcast hardcoded for graph updates | `server.py:166–178` | New sync types require scattered `broadcast()` calls |
| 128 | Submodel navigation parent/child refs mirror view stack | `useSubmodelNavigation.ts` | Store full view stack in `useUIStore` |
| 129 | Pipeline-file resolution not uniformly called | `cli/_run.py`, `cli/_lint.py`, `cli/_deploy.py` | `_deploy.py` accepts optional arg that's never resolved if absent |
| 130 | Business logic + CLI plumbing + config loading tangled | `cli/_deploy.py:36–50`, all `cli/_*.py` | Hard to unit-test; extract handler functions |
| 131 | `_load_deploy_config()` multi-param magic | `cli/_helpers.py` | Replace with `DeployConfig.from_toml()` / `from_cli_args()` |
| 132 | Logging style inconsistent (`click.echo` vs `get_logger`) | across CLI | Pick one; be consistent for scripting |

### P3 — Low (stdlib / ecosystem swaps, polish)

| # | Issue | Where | Why it matters |
|---|---|---|---|
| 133 | Hand-rolled Kahn instead of `graphlib.TopologicalSorter` | `_topo.py:23–56` | Stdlib since 3.9 |
| 134 | `_lru_cache.py` instead of `functools.lru_cache` | `_lru_cache.py` | Reinvents stdlib for non-persistent use |
| 135 | No persistent cache across server restarts | — | Consider `diskcache` for preview cache |
| 136 | `ast.literal_eval` used in AST path, regex in fallback | `_parser_regex.py:136–150` | Delegate regex kwarg parsing via `ast.parse(f"f({kwargs_str})")` |
| 137 | User-code extraction uses line heuristics | `_parser_helpers.py:261–317` | AST walk for return boundaries more robust |
| 138 | Click for 9-command CLI with nested options | `cli/__init__.py` | Typer / Pydantic CLI reduce boilerplate + type safety |
| 139 | `Path.cwd()` scattered | `cli/_init_cmd.py:122`, others | Single `get_project_root()` helper |
| 140 | MLflow signatures unused for feature-order enforcement | `_mlflow_log.py`, `_mlflow_io.py` | `mlflow.types.ModelSignature` auto-validates at load |
| 141 | `uv` not used for container base | `deploy/_config.py:41` | `FROM ghcr.io/astral-sh/uv:latest` + `uv sync --frozen` for reproducibility |
| 142 | Pruner has custom reachability + liveSwitch logic | `deploy/_pruner.py` | Standard DAG BFS from output would be simpler |
| 143 | `ScoringModel.__getattr__` proxy instead of explicit dispatch | `_mlflow_io.py:38–83` | Drop wrapper; dispatch at callsite |
| 144 | `run_in_threadpool` not used for sync I/O in async routes | `routes/files.py`, `routes/pipeline.py` | Offload blocking I/O |

### Missing test scenarios

| # | Scenario | Why |
|---|---|---|
| 145 | Empty pipeline (no nodes) | Parser handles it; does codegen? does executor? |
| 146 | Single-node pipeline (source-only, sink-only) | |
| 147 | Cycle error message quality | `_topo.py:52–54` raises, but message user-friendly? |
| 148 | Duplicate node names with different sanitized forms | No collision detection today |
| 149 | Unicode in descriptions / labels | `_sanitize_func_name` strips non-ASCII silently |
| 150 | Very long descriptions exceeding Python line limits | No validation |
| 151 | 10-cycle parse → codegen round-trip for drift detection | Would reveal `df = source_node` alias accumulation |
| 152 | Pathological docstrings (`"""` at end, `\` at end, `\"""`) | `_sanitize_description` fragile |
| 153 | Decorator kwarg ordering stability across Python versions | `repr()` output varies |
| 154 | Behavioural tests for `ConfigInput` / `ConfigSelect` validation | Form submission, invalid input |
| 155 | Modal focus-trap behaviour tests | Tab, Shift+Tab, ESC, nested modals |
| 156 | Toast de-duplication tests | Rapid identical errors |

---

## Cross-cutting themes

### Theme A — "Fail loudly" is not being honored

The project principle is explicit: silent fallbacks hide bugs and incorrect fallbacks are worse than no fallback. Today, every subsystem has violations. Keep the **sandbox** (`_sandbox.py`) and **atomic writes** (`_polars_utils.py:22–43`) — those are correct defensiveness. Remove or narrow the rest.

**Parser / codegen:**
- Silent config-path recovery on Windows: `_parser_helpers.py:985–999`. When config load fails, scans by `func_name` without surfacing the original error. `_load_error` marker is set but not consistently respected downstream.
- Instance-mapping drops stale explicit entries silently: `_types.py:597–642`. If an explicit `inputMapping` contains keys from a prior graph version, the positional fallback overrides them with no warning.
- Submodel cross-boundary edge resolution: `codegen.py:1180–1189`, `_parser_submodels.py:125–133`. `edge.targetHandle.removeprefix("in__")` silently produces wrong IDs on malformed handles — no validation.
- Model-score column detection swallows config errors: `_builders.py:601–602`.
- Discovery skips unreadable files silently: `discovery.py:63–66, 78–81`.

**Caching / trace:**
- 15+ `except Exception:` in trace enrichment: `trace.py:1056, 1095, 1180, 1196, 1216, 1240, 1246, 1291, 1298, 1322, 1359, 1361, 1467`. None log above `debug`; users get incomplete enrichment with no visibility into why.
- Waterfall build swallows all exceptions: `trace.py:872`.
- `swallow_errors=True` retry on pipeline failure: `trace.py:577–607`. A typo in a column name produces the same exception string as a real ordering issue; swallowing silently produces wrong traces.

**Server / routes:**
- Schema endpoint swallows broad exceptions: `files.py:114–119`.
- Error details leaked to HTTP clients: `files.py:111`, `submodel.py:45`, `git.py:67` — `str(exception)` exposes paths, git output, and internal state.
- TOML read failure returns `None` silently: `parser.py:122–126`.

**Modelling / deploy:**
- Seven diagnostic exception handlers silently skip: `_training_job.py:800, 816, 838, 843, 848, 857, 886` (SHAP, feature importance, GLM coefs, regularization, CV).
- `_validators.py:141–151`: test-quote scoring failures captured into a dict, `validate_deploy()` never raises — deploy can pass with all quotes failed.
- Feature validation on model load logs warning instead of raising: `_model_scorer.py:123–128`.
- Artifact download delete-and-retry masks real corruption: `_mlflow_io.py:514–533`.

**CLI:**
- Defensive Node PATH patching: `_helpers.py:95–105`. If Node is missing, user should install it — not have the tool inject paths.
- Browser-open 4-fallback chain: `_helpers.py:72–92`.
- Silent `prod_exists=False` on any exception: `_impact.py:196–228`. Can't distinguish "doesn't exist" from "network error".
- `_find_frontend_dir()` returns `None` silently: `_helpers.py:123–130`.

**Frontend:**
- Silent AbortError path leaves stale preview: `usePipelineAPI.ts:217–221`. Panel shows prior node's data when user clicks away.
- Drag-drop config JSON parse `catch { /* ignore */ }`: `useEdgeHandlers.ts:145–147`. Creates broken nodes silently.
- `?.data.label || String(id)` hides missing `instanceOf`: `NodePanel.tsx:84–86`. UI shows `"undefined"` instead of surfacing the bug.
- `ModellingConfig.tsx:79–84` treats AbortError as a user-facing failure.
- Broad try/catch in WebSocket message handler: `useWebSocketSync.ts:54–100`.
- `OptimiserPreview.tsx:151–155` and `OptimiserConfig.tsx:150+` `console.warn` errors that should be user-facing toasts.

---

### Theme B — Overengineering / abstractions without pull

**Backend:**

- **Four user-code extraction functions** with overlapping logic (~300 LOC that could be ~100): `_extract_user_code`, `_extract_source_user_code`, `_extract_model_score_user_code`, `_extract_external_user_code` in `_parser_helpers.py:261–530`. They differ only in what constitutes boilerplate; a shared engine with pluggable matchers would halve the code.

- **Three overlapping cache systems**: `_cache.py` (graph fingerprint), `_fingerprint_cache.py` (preview/trace), `_lru_cache.py` (generic LRU). `FingerprintCache` re-implements LRU eviction that `LRUCache` already provides. Consolidate.

- **Regex fallback parser** (`_parser_regex.py`) duplicates AST work and loses fidelity on decorator kwargs and nested functions. Prefer targeted AST recovery for specific `SyntaxError` categories over wholesale regex fallback.

- **Uniform `ScoringModel` wrapper** `_mlflow_io.py:38–83` — `__getattr__`-proxies 3 flavors but the real branching still happens per-flavor at the callsite. Drop the wrapper and dispatch directly.

- **Parallel `_NODE_BUILDERS` / `_CODEGEN_BUILDERS` dispatch tables** risk drift: `_builders.py:102–135`, `codegen.py:392–402`. A single registry with `(execution_builder, codegen_builder)` tuples would prevent silent divergence.

- **Graph flattening duplicated**: `_parser_submodels.py:135–141` flattens inline, `_flatten.py` provides a utility. Two paths that can drift.

- **Dead GLM cross-validation path**: `_training_job.py:865–887`. Speculative, swallowed exceptions, unclear if used. Remove or make explicit.

**Frontend:**

- **Shell trinity**: `ModalShell` + `PanelShell` + `PanelHeader` — merge `PanelShell+PanelHeader` into one `ResizablePanel`; `ModalShell` becomes a conditional in the parent.

- **`ConfigInput` / `ConfigSelect`** 75-line wrappers that add only label + focus-style mutation — replace with plain `<input>` + className helpers.

- **96+ inline `style` mutations** in `onFocus`/`onBlur`/`onMouseEnter` handlers — breaks concurrent-rendering guarantees and bypasses reconciliation. Replace with Tailwind state classes (`:focus`, `:hover`) or CSS variables.

- **Three-layer indirection for preview propagation**: `usePipelineAPI.ts:101–140` uses a `propagatingRef` Set, a `propagateRef` function reference, and a Promise.all chain. Call `propagateDownstream` directly inside `.then()`.

- **Reimplemented drag-aware snapshot logic** in `useUndoRedo.ts:61–88` — inspect the `NodeChange` array once per effect instead.

---

### Theme C — Concurrency correctness

- **`_job_store.update_job()` mutates without `atomic_update`**: `_job_store.py:68`. Relies on GIL for atomicity; breaks on PyPy or with threading tuning.
- **Git routes have `_fetch_time_lock` but no lock around actual `git fetch`**: `git.py:37`. Two threads can both run fetch concurrently.
- **Sync I/O blocks async event loop**: `_io.py`, `_mlflow_io.py`. Use `run_in_threadpool`.
- **WebSocket `ws_clients` set mutated without lock**: `server.py:183–191`. Broadcast vs. disconnect vs. cleanup race.
- **File-watcher 2s cooldown is too tight**: `server.py:289–310`, `_helpers.py:174–191`. Saves taking >2s cause watcher to re-parse partial writes. Fix with atomic temp-rename (already done for parquet — extend to `.py`).
- **Zustand `.getState()` reads inside async callbacks aren't declared deps**: `useWebSocketSync.ts:86`, `usePipelineAPI.ts:160`, `useKeyboardShortcuts.ts:186`.
- **`activeSourceRef` captured inside promise chain** instead of before: `usePipelineAPI.ts:93, 121`. Cascade uses old source if user switches mid-preview.
- **WebSocket sync bypasses undo history**: `useWebSocketSync.ts:70–91`. `graphRefreshingRef` only masks the symptom (150ms guard window).

---

### Theme D — God files / monolithic modules

| File | LOC | Suggested split |
|---|---|---|
| `_parser_helpers.py` | 1016 | `_ast_helpers.py`, `_config_builder.py`, `_code_extraction.py`, `_graph_builders.py` |
| `_types.py` | 669 | Pydantic types stay; graph utilities (`_sanitize_func_name`, `build_instance_mapping`, `resolve_orig_source_names`) move to `_graph_utils.py` |
| `codegen.py` | 1298 | Orchestration stays; builder registration moves to `_codegen_builders.py` |
| `trace.py` | 1500+ | Preview / trace / enrichment / waterfall are separable modules |
| `OptimiserPreview.tsx` | 1081 | `FrontierChart`, `ConvergenceChart`, `SummaryTab`, `DetailCard` |
| `CalculationHero.tsx` | 893 | `WaterfallChart`, `ExpressionChain`, `InputSourceTree` |

**Related:** duplication between `ModellingConfig.tsx` and `OptimiserConfig.tsx` — 200+ LOC of identical config-hash staleness, RAM estimate, store-backed job polling patterns. Extract `useConfigEstimate()` and `useJobPolling()` hooks.

**Related:** prop drilling through 15+ levels in `NodePanel.tsx` — graph context (`allNodes`, `edges`, `submodels`, `preamble`) should be a Zustand selector or React context.

---

### Theme E — Performance wins

- **Graph fingerprint recomputed every preview** — memoize on `PipelineGraph` via `@cached_property`. `executor.py:385`.
- **`_compute_needed_columns` is O(n²) backward pass** — single forward pass (each node contributes to its children's needs) is O(n). `_execute_lazy.py:38–98`.
- **Preamble cache uses `OrderedDict + popitem`** — swap to `functools.lru_cache`. `executor.py:88–222`.
- **SHA-256 on cache keys** — `xxhash` is 10–100× faster and collision risk is irrelevant for local cache keys. `_cache.py:27, 46`.
- **`JSON.stringify(n.data)` on every render** for graph fingerprinting — extract minimal keys or shallow hash. `App.tsx:188–198`.
- **Polars ↔ pandas churn** in `_prepare_predict_frame` even when no categoricals present: `_mlflow_io.py:556–590`.
- **JSON data input always fully eager** — document or pre-flatten via parquet. `_io.py:57–60`.
- **Feature validation on every score call** — cache keyed by `(model_id, schema_hash)`. `_model_scorer.py:81–130`.
- **Redundant fingerprinting on trace miss** — preview path already computed it; pass through. `trace.py:509`.
- **`columnsEqual` array walk on every preview completion** — hash columns once per node. `usePipelineAPI.ts:50–54`.
- **`nodesWithStatus` remapped on every change** causing React Flow to re-render every node — memoize each node's computed properties. `useTracing.ts:150–174`.

---

### Theme F — Stdlib / ecosystem replacements

- **`graphlib.TopologicalSorter`** instead of hand-rolled Kahn: `_topo.py:23–56`.
- **`functools.lru_cache`** instead of `_lru_cache.py` for non-persistent use.
- **`diskcache`** for preview cache that should survive server restarts.
- **`mlflow.pyfunc.log_model(..., signature=...)`** rather than reconstructing feature contracts.
- **`mlflow.types.ModelSignature`** instead of manual feature-order preservation.
- **`ast.parse(f"f({kwargs_str})")`** for decorator kwarg extraction in regex path: `_parser_regex.py:136–150`.
- **Proper TOML parsing** in `haute init` instead of `string.replace()` on `pyproject.toml`: `_init_cmd.py:42–50`.
- **Pydantic `model_dump_json()`** for sidecar instead of hand-rolled serialization: `_helpers.py:308–322`.
- **`run_in_threadpool`** for sync I/O in async routes.
- **Typer or Pydantic CLI** over bare Click for 9-command CLI with nested options.
- **`uv` base image** or pinned `python:3.11.X-slim` for containers: `deploy/_config.py:41`.

---

### Theme G — Deploy safety gaps

- **No hash of trained feature list logged** → no runtime check that deployed model matches training: `_bundler.py:88–98`, `_scorer.py:200`.
- **Static dataSource paths are relative, re-resolved at deploy time** using "CWD first, then pipeline dir": `_bundler.py:112–125`. At runtime in a container, CWD may not exist. Store absolute resolved paths in the manifest.
- **Container image tag `python:3.11-slim` unpinned**: `deploy/_config.py:41`. Latest tag means deployed containers silently diverge from training.
- **Pruner doesn't validate static source schema against downstream expectations**: `_pruner.py:82–118`.
- **GLM column selection drops categorical metadata**: `_training_job.py:262–264`. Filtered `_PreparedData` doesn't mark term columns as categorical.

**Recommendation:** add an explicit "model contract" artifact (JSON) logged at training time with feature names, categorical flags, and ordering. Validate it during pruning and loading.

---

### Theme H — API / UX contract drift

- **Error response shapes inconsistent**: `detail: string` (`pipeline.py:192, 194`) vs. `detail: dict` (`utility.py:117`). Frontend must handle both.
- **`model_name` required in `_status.py` but optional in `_deploy.py`** — pick one.
- **`_smoke.py:48` uses `click.UsageError`** while 7 other commands use `click.echo(..., err=True) + SystemExit(1)`.
- **`haute init` fails on pre-existing `haute.toml`** without suggesting `--force`: `_init_cmd.py:125`.
- **`haute serve` has no port-conflict detection**: `_serve.py:57–67`. uvicorn fails silently on port clash.
- **`--endpoint-suffix` help text differs** between `_smoke.py:14` and `_impact.py:12` — standardize.
- **`haute init` scaffold creates 20+ files** — trim to 5 essential, gate extras behind `--with-testing` / `--with-ci`.
- **Starter `tests/test_pipeline.py` checks only that the file is valid Python** — gives false security.
- **Pre-commit hook `chmod 0o755` fails silently on Windows**: `_init_cmd.py:228`.
- **Panel state doesn't survive WebSocket-driven graph changes** — `submodelDialog`, `renameDialog` not cleared when referenced nodes disappear from disk: `useWebSocketSync.ts:86–87`.

---

## Subsystem detail

### 1. Parser / Codegen / Execution

**File scope:** `parser.py`, `codegen.py`, `_parser_helpers.py`, `_parser_regex.py`, `_parser_submodels.py`, `_expression_parser.py`, `pipeline.py`, `executor.py`, `_execute_lazy.py`, `discovery.py`, `graph_utils.py`, `_topo.py`, `_submodel_graph.py`, `_node_builder.py`, `_builders.py`, `_flatten.py`, `_rating.py`, `_types.py`.

**Good:** Pydantic models as single source of truth across parser/executor/codegen. AST-based parsing for the happy path. Preserved-block system (`# haute:preserve-start/end`) for user code outside nodes. Clean separation of parser, codegen, executor modules.

**Critical / correctness:**
- Polars codegen empty-code edge case fragile: `codegen.py:813–829`, `_wrap_user_code` at `:357`.
- Instance mapping silently overrides stale explicit entries: `codegen.py:884–887`, `_types.py:597–642`.
- Submodel cross-boundary edge resolution unvalidated: `codegen.py:1180–1189`.
- Graph fingerprint cache extend-path can retain stale data when nodes are deleted and re-added with same ID: `executor.py:391–449`.

**Overengineering:**
- Four user-code extractors with overlapping logic: `_parser_helpers.py:261–530`.
- Regex fallback parser redundant and lossy: `parser.py:122–126`, `_parser_regex.py`.
- Parallel builder dispatch tables: `_builders.py:102–135`, `codegen.py:392–402`.
- Column contract system underutilized: `_execute_lazy.py:38–98`, `_builders.py:107–146`.

**Performance:**
- `ast.parse` passed optional to `_extract_function_bodies` invites future double-parse: `parser.py:132`, `_parser_helpers.py:549–553`.
- Preamble cache manual eviction (swap to `lru_cache`): `executor.py:88–222`.
- Graph fingerprinting recomputed per execute: `executor.py:385`.
- `_compute_needed_columns` O(n²) — forward pass would be O(n): `_execute_lazy.py:38–98`.

**Better alternatives:**
- `ast.literal_eval` consistently across regex and AST paths for decorator kwargs.
- AST walk for return-boundary detection instead of line heuristics in user-code extraction.
- `graphlib.TopologicalSorter` for `_topo.py`.

**Missing test scenarios:**
- Empty pipeline (no nodes).
- Single-node pipelines (source or sink).
- Cycle error message quality (`_topo.py:52–54`).
- Duplicate node names with different sanitized forms.
- Unicode in descriptions / labels.
- Very long descriptions exceeding line limits.
- 10-cycle parse-codegen round-trip for drift detection.

---

### 2. Caching / I/O / Trace

**File scope:** `_cache.py`, `_fingerprint_cache.py`, `_lru_cache.py`, `_io.py`, `_json_flatten.py`, `_config_io.py`, `_config_validation.py`, `_databricks_io.py`, `_mlflow_io.py`, `_mlflow_utils.py`, `_optimiser_io.py`, `_polars_utils.py`, `_ram_estimate.py`, `_sandbox.py`, `trace.py`, `_trace_enrichment.py`, `_trace_export.py`, `_trace_waterfall.py`.

**Good:** `_sandbox.py` two-layer defence (AST + restricted builtins) is well-designed. Atomic writes in `_polars_utils.py:22–43` prevent partial-write corruption.

**Critical:**
- mtime TOCTOU in cache keys: `_io.py:96`, `_optimiser_io.py:45`.
- `swallow_errors=True` retry masks real bugs: `trace.py:577–607`.
- Waterfall silent exception swallow: `trace.py:872`.
- 15+ silent enrichment exception handlers in `trace.py`.
- Graph fingerprint collision risk from `json.dumps(default=repr)`: `_cache.py:27, 46`.

**Overengineering:**
- Three overlapping cache layers (`_cache.py`, `_fingerprint_cache.py`, `_lru_cache.py`).
- Model-score column detection swallows config errors silently: `_builders.py:601–602`.
- Discovery silently skips unreadable files: `discovery.py:63–66, 78–81`.

**Performance:**
- Preview path materializes eagerly at every node — Polars `cache_hint()` or output-only materialization could save 2–3× memory/time on multi-source workflows.
- JSON reads always eager: `_io.py:57–60`.
- Sync I/O blocks FastAPI event loop.
- Trace recomputes graph fingerprint even when preview already did.

**Better alternatives:**
- `functools.lru_cache` or `diskcache` to replace `_lru_cache.py`.
- `xxhash` instead of SHA-256 for fingerprints.
- Polars `LazyFrame.cache_hint()` to replace dual `_preview_cache` + `_trace_cache`.
- Content-hash cache keys instead of mtime.

**Architecture:**
- Trace depends on `executor._preview_cache` (line 33) — hidden coupling. Invalidation coordinated manually in `_train_service.py:478–481`. Move to callback/event bus.
- Fingerprint algorithm lacks versioning — algorithm change won't invalidate stale entries.

---

### 3. Server / Routes / API

**File scope:** `server.py`, `schemas.py`, `_types.py`, `_git.py`, and everything in `routes/`.

**Good:** `validate_safe_path()` consistently used (`routes/_helpers.py:24–37`). Structured logging via `structlog` with context. Clean service-layer split (`_save_pipeline.py`, `_train_service.py`).

**Critical / security:**
- Error details leaked: `files.py:111`, `submodel.py:45`, `git.py:67`.
- `ws_clients` set mutated without lock: `server.py:183–191`.
- Path traversal gap in `graph_to_code_multi()` output: `_save_pipeline.py:139–152` — relies on trusted codegen; add explicit whitelist.
- Broad exception catch in `/schema` endpoint: `files.py:114–119`.

**Overengineering / design:**
- HTTPException dict-detail on syntax errors inconsistent with string-detail elsewhere: `utility.py:117–121`.
- Dual-layer error conversion in git routes: `git.py:61–77`, `_dc_to_pydantic()`.
- Manual pipeline index invalidation with two caches: `_helpers.py:198–285`.
- Error response shape drift: `detail: string` vs. `detail: dict`.
- `_save_pipeline.py` handles 5 responsibilities (validate, codegen, infer schemas, write configs, manage sidecars) without a transaction/checkpoint — failures orphan prior writes.
- Node positions silently lost on rename via sanitized IDs: `_helpers.py:325–354`.

**Concurrency:**
- Git routes: `_fetch_time_lock` but no lock around actual fetch: `git.py:37`.
- File-watcher 2s cooldown races with long saves: `server.py:289–310`.
- `_job_store.update_job()` mutates dict directly without atomic swap: `_job_store.py:68`.

**Better alternatives:**
- Lift conditional imports out of route handlers (`pipeline.py:68, 165, 212`).
- Pydantic `model_dump_json()` for sidecar: `_helpers.py:308–322`.
- Single event bus for sync broadcasts instead of scattered `broadcast()` calls.

**Auth note:** API is local-only by design but has no auth check. If exposed (port-forward, tunnel), every endpoint is open. Consider IP allowlist or `require_git_repo` guard.

---

### 4. Modelling / Deploy

**File scope:** `modelling/` (all), `_model_scorer.py`, `deploy/` (all).

**Critical:**
- Feature/categorical index mismatch between training and scoring: `_algorithms.py:283`, `_mlflow_io.py:578–579`. Training computes cat_indices from filtered `cols_to_select`; scoring reconstructs independently.
- No MLflow signature logged: `_mlflow_log.py`. Deployed model has no authoritative feature contract.
- Categorical type mismatch warning-only in `_model_scorer.py:123–128` — should raise `FeatureMismatchError`.
- Pruner doesn't validate static source schema: `_pruner.py:82–118`.
- GLM column selection drops categorical metadata: `_training_job.py:262–264`.

**Overengineering:**
- `ScoringModel` uniform wrapper via `__getattr__` proxy: `_mlflow_io.py:38–83`. Obscures flavor differences that still branch at callsite.
- Duplicate batch + eager scoring paths: `_model_scorer.py:206–209`, `_score_batched_standalone()`.
- Dead GLM cross-validation path with swallowed exceptions: `_training_job.py:865–887`.

**Defensive-code smells:**
- 7 diagnostic exception handlers silently skip: `_training_job.py:800, 816, 838, 843, 848, 857, 886`.
- Artifact download delete-and-retry: `_mlflow_io.py:514–533`.
- Test-quote scoring errors captured without raising: `_validators.py:141–151`.

**Deploy safety:**
- No hash of trained feature list → no runtime match check: `_bundler.py:88`, `_scorer.py:200`.
- Static dataSource paths re-resolved at deploy time: `_bundler.py:112–125`.
- Container base image unpinned: `deploy/_config.py:41`.

**Performance:**
- Model cache hit rate not logged: `_mlflow_io.py:26–30`.
- Feature validation redundant per score call: `_model_scorer.py:81–130`.
- Polars ↔ pandas churn even with no categoricals: `_mlflow_io.py:556–590`.

**Better alternatives:**
- `mlflow.types.ModelSignature` for feature-order enforcement.
- Simpler DAG-reachability pruner.
- `uv`-based container build for reproducibility.
- Explicit model-contract JSON artifact logged at training.

---

### 5. CLI

**File scope:** `cli/` (all), `_scaffold.py`, `_logging.py`, `pyproject.toml`, `hatch_build.py`, `haute.toml`.

**Critical:**
- Install instruction inconsistent across commands: `_status.py:33` says `uv pip install` while others say `uv add`.
- String-grep dependency detection fragile: `_init_cmd.py:40`. Matches comments/docstrings.
- `string.replace()` on arbitrary TOML can corrupt `pyproject.toml`: `_init_cmd.py:42–50`.
- `haute serve` no port-conflict detection: `_serve.py:57–67`.
- CI detection relies on undocumented/deprecated env vars: `_deploy.py:40`.

**UX / friction:**
- Three-fallback `staging_suffix` opaque to users: `_impact.py:49`.
- `haute init` no `--force` suggestion: `_init_cmd.py:125`.
- `click.UsageError` inconsistent with rest of CLI's error style: `_smoke.py:48–51`.
- Progress-bar `nl=False` without flush: `_train.py:62–64`.
- `--version-only` returns 0 on missing model: `_status.py:39`.

**Defensive code:**
- Node PATH patching: `_helpers.py:95–105`.
- Browser-open 4-fallback: `_helpers.py:72–92`.
- Silent `None` from `_find_frontend_dir()`: `_helpers.py:123–130`.
- Silent `prod_exists=False` on any exception: `_impact.py:196–228`.

**Inconsistencies:**
- `--endpoint-suffix` help text drift: `_smoke.py:14` vs. `_impact.py:12`.
- `model_name` required-vs-optional drift: `_status.py` vs. `_deploy.py`.
- Error-handling style drift (`click.UsageError` vs. `echo+SystemExit`).
- Pipeline-file resolution not uniformly called.

**Scaffolding:**
- 20+ files on `haute init` — trim to 5 essential.
- Trivial starter test gives false security.
- `starter_utility_features.py` 100+ lines of example code clutter every new project.
- Pre-commit hook `chmod` fails silently on Windows: `_init_cmd.py:228`.

**Better alternatives:**
- Typer or Pydantic CLI instead of Click.
- Pydantic `DeployConfig.from_toml()` / `from_cli_args()` instead of multi-param `_load_deploy_config()`.
- Consistent structured logging or `click.echo` throughout.
- Single `get_project_root()` helper with validation.

**Architecture:** Each `_X.py` bundles Click decorators + config loading + business logic. Extract business logic into separate modules that take `(config, logger)`.

---

### 6. Frontend: Canvas / State / Hooks

**File scope:** `App.tsx`, `main.tsx`, `nodes/`, `stores/`, `hooks/`, `types/`, `utils/`, `trace/`.

**Critical:**
- WebSocket sync bypasses undo history: `useWebSocketSync.ts:70–91`. `graphRefreshingRef` 150ms guard only masks symptom.
- Silent AbortError leaves stale preview: `usePipelineAPI.ts:217–221`.
- Eager derived-cache cleanup on node delete: `useNodeHandlers.ts:44–58`. Can flip UI from cached result to `null` mid-render.
- Zustand `.getState()` reads in async callbacks undeclared as deps: `useWebSocketSync.ts:86`, `usePipelineAPI.ts:160`, `useKeyboardShortcuts.ts:186`.
- Stale `activeSourceRef` in cascade: `usePipelineAPI.ts:93–96, 121`.

**State architecture:**
- Graph truth split across `graphRef`, `nodes/edges` state, Zustand stores, `submodelsRef`.
- `dirty` flag as separate store boolean rather than derived from `lastSavedRef` comparison.
- Panel state not cleared on WebSocket-driven graph changes — orphan dialogs pointing to deleted IDs.

**Overengineering:**
- Three-layer preview-propagation indirection: `usePipelineAPI.ts:101–140`.
- `isDragging` ref reimplements React Flow logic: `useUndoRedo.ts:61–88`.

**Defensive-code smells:**
- Drag-drop JSON parse `catch { /* ignore */ }`: `useEdgeHandlers.ts:145–147`.
- Unchecked `.find()` + cast: `App.tsx:492–494`.
- Catch-all in WebSocket message handler: `useWebSocketSync.ts:54–100`.

**Performance:**
- `columnsEqual` array walk per preview: `usePipelineAPI.ts:50–54`.
- `nodesWithStatus` remap on every change: `useTracing.ts:150–174`.
- `JSON.stringify(n.data)` per render: `App.tsx:188–198`.

**TypeScript hygiene:**
- Implicit `as Record<string, unknown>` casts hide shape errors: `usePipelineAPI.ts:119, 208`.
- Overly permissive `as Node` casts: `useSubmodelNavigation.ts:80, 95, 117`.

---

### 7. Frontend: Panels / Components / API

**File scope:** `components/`, `panels/` (incl. `editors/`, `modelling/`, `trace/`), `api/`, `index.css`, `__tests__/`, `setupTests.ts`, `test-utils/`.

**Good:** `api/client.ts` is exemplary — centralized fetch, typed request/response, AbortController + timeout, consistent `ApiError` wrapping. `schemas.py` ↔ `api/types.ts` alignment. `index.css` is lean (204 LOC).

**Critical:**
- Inline `style` mutations in `onFocus`/`onBlur`/`onMouseEnter`/`onMouseLeave`: `ConfigInput.tsx:49–56`, `ConfigSelect.tsx:57–63`, and 96+ instances total. Breaks concurrent rendering; bypasses reconciliation.
- Uncontrolled input with no validation in `RenameDialog.tsx:51`.
- Silent AbortError swallow treated as failure: `ModellingConfig.tsx:79–84`.
- Missing toast de-duplication.
- Incomplete modal focus trap: `ModalShell.tsx:48–63`.

**Component architecture:**
- God components: `OptimiserPreview.tsx` (1081 LOC), `CalculationHero.tsx` (893 LOC).
- 200+ LOC duplication between `ModellingConfig.tsx` and `OptimiserConfig.tsx` — extract `useConfigEstimate()` / `useJobPolling()`.
- 15-level prop drilling in `NodePanel.tsx`.

**Overengineering:**
- Shell trinity `ModalShell` + `PanelShell` + `PanelHeader`.
- `ConfigInput` / `ConfigSelect` trivial wrappers.

**Defensive-code smells:**
- `console.warn` errors that should be toasts: `OptimiserPreview.tsx:151–155`, `OptimiserConfig.tsx:150+`.
- Optional-chain fallback hiding missing `instanceOf`: `NodePanel.tsx:84–86`.
- Silent `null` returns in `CalculationHero.tsx` for missing data.

**API client:**
- No retry logic on transient failures — consider `useRetry()` with exponential backoff for idempotent operations.

**Test quality:**
- Gap tests (`usePipelineAPI.gaps.test.ts`, `useBackgroundJobs.gaps.test.ts`) are high-value.
- `App.test.tsx` mocks every hook/component — brittle to refactor. Prefer integration tests for critical flows.
- No behavioral tests for form components, modal focus, toast dedup.

**CSS:**
- Magic colour strings scattered (`#22c55e`, `rgba(59,130,246,.3)`) — hoist to CSS vars.

---

## Recommended attack order

1. **Fail-loudly audit** (~1 day, highest leverage). `rg "except Exception" src/` and `rg "catch\s*\{\s*\}" frontend/`. Triage each: delete, narrow to a specific exception, or escalate to a typed error with logging. This single pass improves observability across every subsystem.

2. **Log MLflow signatures + feature-contract hash** (~0.5 day). Closes the biggest correctness gap in train→deploy.

3. **Atomic file writes + file-watcher coordination** (~0.5 day). Extend the parquet temp-rename pattern to `.py` writes; eliminates the 2s-cooldown race in `server.py`.

4. **Lock `ws_clients` and `git fetch`** (~2 hrs). Small, removes real races.

5. **Split god files** (~1–2 days). `_parser_helpers.py`, `codegen.py`, `trace.py`, `OptimiserPreview.tsx`, `CalculationHero.tsx`. No behaviour change, big maintainability win.

6. **Consolidate 3 cache layers and 4 user-code extractors** (~1 day).

7. **Replace inline `style` mutations with Tailwind classes** (~0.5 day). 96+ mechanical replacements.

8. **Swap hand-rolled pieces for stdlib** (`graphlib`, `functools.lru_cache`, `xxhash`) (~0.5 day).

9. **Standardise CLI error handling and trim `haute init` scaffold** (~0.5 day).

10. **Split `ModellingConfig` / `OptimiserConfig` duplication into shared hooks** (~0.5 day).

Per `CLAUDE.md`, each item should be worked by a developer agent + reviewer agent pair, with test suites authored ahead of the implementation.

---

## What's intentionally not in this review

- **Line-by-line review of test files.** Test architecture and gaps are covered; individual assertions are not.
- **Generated/build artefacts** (`dist/`, `site/`, `catboost_info/`, caches).
- **Third-party vendored code.**
- **Documentation in `docs/`** other than this file.

This review captures correctness, efficiency, and engineering-standard concerns as of 2026-04-17. Once items are addressed, re-run a targeted review on the touched subsystems rather than repeating the full sweep.
