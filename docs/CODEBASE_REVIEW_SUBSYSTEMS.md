# Codebase Review — New Subsystem Design Notes

Per `docs/COMMIT_STANDARDS.md` §18, each new subsystem or public API introduced during the codebase review gets a short design note (problem / approach / alternatives / open questions). These notes are retrospective: the code has already landed and passed review. Their purpose is to document the judgment calls for future maintainers.

Cross-references:
- Overall plan: `docs/CODEBASE_REVIEW_PLAN.md`
- Findings: `docs/CODEBASE_REVIEW.md`
- Commit audit: `docs/CODEBASE_REVIEW_COMMIT_AUDIT.md`

---

## F1 — `haute.errors` (typed error hierarchy)

**Problem.** 40+ `except Exception:` handlers across the backend swallowed errors silently, defeating the stated "fail loudly" principle. Raising plain `ValueError` at those sites didn't let callers distinguish categories (config vs parse vs execution) for structured handling.

**Approach.** Single shallow hierarchy rooted at `HauteError`: `ConfigError`, `ParseError`, `ExecutionError`, `DeployError`, `FeatureMismatchError`. Each accepts `message: str = ""` plus arbitrary `**context` kwargs that render into `str(err)` as `"msg (k=v, k=v)"`. `err.args = (str(err),)` so `logging.exception` shows the rendered form.

**Alternatives considered.**
- Multiple roots per subsystem (rejected: fragments the `except haute.HauteError:` surface).
- Deep hierarchies (`ConfigError → HautePathError → ...`) — rejected: one layer is enough for the distinctions we make.
- `pydantic.ValidationError` for config — rejected: only fits Pydantic-validated cases; doesn't help parser / executor.

**Open questions.** None material. The hierarchy is unified around `haute.errors.HauteError`; module-specific subclasses share one `__mro__` root and the public package export points at that canonical class.

---

## F2 + F6 — `haute._file_ops` (atomic write primitives + Writer)

**Problem.** File-watcher race: the server's `.py` writes weren't atomic, so the watcher could re-parse a half-written file. A 2-second cooldown masked the race but broke for saves that took longer.

**Approach.** `atomic_write_bytes` / `atomic_write_text` follow the parquet temp-rename pattern (write to sibling `{name}.{pid}.{uuid}.tmp`, `os.replace` to target). `Writer` context manager buffers writes and commits atomically on clean exit; takes an optional `mark_self_write: Callable[[Path], None]` callback fired immediately before each rename so the watcher knows to skip the pending event.

**Alternatives considered.**
- Just use the existing `_polars_utils.atomic_write` — rejected because that uses static `.parquet.tmp` with no pid/uuid, which collides under concurrent writers; and it silently `mkdir`s parents.
- Transaction log + recovery on startup — overkill for a local dev tool.
- `atomicwrites` third-party library — adds a dep that does exactly what 30 lines of code do.

**Open questions.** Windows `os.replace` is not atomic under contention (documented; concurrent-writers test is platform-gated).

---

## F3 — `haute._hashing` (xxhash content hash)

**Problem.** Cache keys in `_io.py` / `_optimiser_io.py` used `os.path.getmtime` — TOCTOU-racy and misses same-second overwrites. `_cache.py` used SHA-256 which is ~6× slower than needed for non-cryptographic local cache keys.

**Approach.** `content_hash(path)` streams in 64 KiB chunks through `xxhash.xxh64(seed=0)`. `content_hash_bytes(data)` for in-memory. `HASH_ALGO = "xxh64"` exported for metadata stamping. Pinned known vectors guard against algorithm drift.

**Alternatives considered.**
- xxh3 (faster but less widely deployed at pin time).
- `hashlib.sha256` (kept for the graph-structure fingerprint; see 1D dev note for why mixing sha256 + xxhash isn't beneficial there).
- `hashlib.blake2b` (comparable speed to xxhash, stdlib, but changing the established xxhash choice now would be churn).

**Open questions.** Memoisation on top is a caller concern, not part of this module.

---

## F4 — `haute.modelling._signature` (MLflow signature helper)

**Problem.** No MLflow model signature was logged at training time, so deployed models had no authoritative feature/type contract; feature-order mismatches silently produced wrong predictions.

**Approach.** `build_signature(features, feature_types, categorical_features, target_name, target_type, task) → mlflow.models.ModelSignature`. Preserves feature order exactly; fails loudly on unknown polars dtypes (no `DataType.string` fallback, unlike the older `deploy/_mlflow._build_signature`).

**Alternatives considered.**
- Reuse `deploy/_mlflow._build_signature` — rejected because it has a silent fallback on unknown dtypes which violates the "no fallback" principle at training time.
- MLflow's `infer_signature(input_df, output_df)` — rejected because it requires an actual scored frame, whereas `build_signature` is called from the contract already materialised at training.
- Generate from pydantic model — tried; pydantic types don't map cleanly onto MLflow's `DataType` enum.

**Open questions.** Multiclass classification — the current output schema is binary-shaped (`pred_label` + `pred_proba`). Reviewer flagged this for follow-up when multiclass arrives.

---

## F5 — `haute.modelling._feature_contract` (feature contract artifact)

**Problem.** MLflow signatures are MLflow-native; operators wanted a first-class Haute artifact that travels with deploy bundles and supports a diff-capable `assert_contracts_match` so mismatches name the field that drifted.

**Approach.** `FeatureContract` frozen dataclass (features, feature_types, categorical_features, target_name, target_type, task, contract_hash). `build_contract` computes deterministic sha256 over canonical-JSON of all data fields. `save_contract` writes pretty JSON. `load_contract(path, *, verify_hash=True)` recomputes the hash on load and raises `FeatureMismatchError` on tamper. `assert_contracts_match(expected, actual)` raises with `field=`/`expected=`/`actual=` context.

**Alternatives considered.**
- Just MLflow signature — rejected because MLflow can't express "categorical set" cleanly and its comparison is a coercion warning, not an actionable error.
- Protobuf / Avro — rejected because diffability in git review is a requirement; JSON wins.
- Store only the hash (no full contract) — rejected because the hash alone can't name the offending field on mismatch.

**Open questions.** Bundled-contract-only path when a model file is missing: scorer raises a loud contract-mismatch error rather than returning the LazyFrame unchanged; flagged as a future hardening.

---

## F7 — `haute._project` (project root helper)

**Problem.** CLI commands sprinkled `Path.cwd()` calls with no validation that the user was in a valid project. `haute impact` could run against a sub-directory and write `impact_report.md` to the wrong location.

**Approach.** `get_project_root(start=None) → Path` walks up from `start` (or `Path.cwd()`) looking for `haute.toml` + a valid git setup. `is_haute_project(path)` exact-path predicate. `.git` as a directory OR a file (worktree) accepted. `PermissionError` propagates unwrapped; non-existent `start` raises `ConfigError` with a fix-it suffix.

**Alternatives considered.**
- `git rev-parse --show-toplevel` — rejected because it's git-specific and doesn't check for `haute.toml`.
- Make `strict=True` the default — rejected because file-watcher / server contexts must not crash on one unreadable pipeline.

**Open questions.** None — the split validation (haute.toml walk separate from .git walk) is documented at the function.

---

## 2A-1 — `haute._graph_utils` (extracted from `_types.py`)

**Problem.** `_types.py` mixed Pydantic graph models with graph utility functions, making it hard to navigate.

**Approach.** Pure move: `_sanitize_func_name`, `build_instance_mapping`, `resolve_orig_source_names`, `build_parents_of`, `_resolve_sink_path` moved to `_graph_utils.py`. No behaviour change. `_graph_utils.py` uses `TYPE_CHECKING` imports for `GraphEdge`/`GraphNode` to avoid cycles. `_types.py` re-imports `build_parents_of` for `PipelineGraph.parents_of` to keep using it.

**Alternatives considered.**
- Keep everything in `_types.py` — rejected because 669 LOC mixed concerns is the god-file pattern.
- Add a transition shim for external callers — rejected during audit because there were zero in-repo callers and no existing users.

**Open questions.** None.

---

## 2A-4 — `haute._trace_correlation` + expanded `_trace_enrichment` / `_trace_waterfall`

**Problem.** `trace.py` was 1736 LOC with several concerns tangled: execute_trace orchestration, post-hoc row correlation, schema diff, enrichment dispatch, waterfall assembly, JSON coercion.

**Approach.** New `_trace_correlation.py` gets post-hoc row correlation + schema diff + JSON coercion (335 LOC). `_trace_enrichment.py` absorbs the per-step dispatch walk (`enrich_steps` and its helpers). `_trace_waterfall.py` gets `build_waterfall_from_steps`. `trace.py` becomes 736 LOC facade: public dataclasses + `execute_trace` orchestrator + 3 private helpers + re-exports for the monkeypatch surface.

**Alternatives considered.**
- Don't split — rejected by the review.
- Split further into `_trace_preview.py` — rejected; the preview-cache reuse is ~80 lines only used by `execute_trace`, bureaucracy to extract.
- Move `enrich_steps` dispatch into trace.py — rejected; 800+ LOC of dispatch logic doesn't belong in the facade.

**Open questions.** `_trace_enrichment.py` is now 1393 LOC. If it grows further, splitting node-type enrichers from the dispatch walk is a natural next step.

---

## 2B-1 — `frontend/src/panels/optimiser/` (extracted from OptimiserPreview.tsx)

**Problem.** `OptimiserPreview.tsx` was 1081 LOC mixing frontier scatter rendering, convergence chart, summary tab content, and a detail-card with save / MLflow-log logic.

**Approach.** Extracted `FrontierChart.tsx`, `ConvergenceChart.tsx`, `SummaryTab.tsx`, `DetailCard.tsx`. OptimiserPreview remains at 557 LOC as the orchestrator — owns state, composes tabs, threads props explicitly. No lifted state. `isConstraintMet` re-exported from OptimiserPreview for the two children that share it.

**Alternatives considered.**
- Further split ExportTab/FrontierTab — rejected as out of the 4-component scope; reviewer flagged as soft follow-up.
- Introduce a context/provider — rejected because prop drilling stays at ≤2 levels.
- Use a state library for optimiser state — overkill for the scope.

**Open questions.** `isConstraintMet` and `SolveResult` ideally live alongside `api/types`; flagged as a follow-up.

---

## 2B-2 — `frontend/src/trace/` sub-components (extracted from CalculationHero.tsx)

**Problem.** `CalculationHero.tsx` was 919 LOC mixing waterfall rendering, expression-chain rows, input-source tree, and orchestration. Also: silent `return null` paths for missing calculation data (review item #85).

**Approach.** Extracted `WaterfallChart.tsx` (includes `WaterfallErrorAlert`), `ExpressionChain.tsx`, `InputSourceTree.tsx`. New `traceFormatting.ts` holds shared pure formatters used by ≥2 of the files. CalculationHero stays at 574 LOC as orchestrator. Item #85 addressed: the one true "data missing" null return converted to an explicit `DataMissing` alert; three legitimate "not applicable" null returns got WHY comments.

**Alternatives considered.**
- Inline the shared formatters in each file — rejected because 3× duplication is worse than a shared pure-function module.
- Split further into `ConditionalBranches` etc. — rejected; the remaining blocks are tightly coupled to orchestrator state.

**Open questions.** `formatResultValue` in `traceFormatting.ts` is exported but has no external consumer (reviewer nit) — candidate for demotion to module-local on next touch.
