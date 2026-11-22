# t-boost staged API, typed (design v3, 22 Nov 2026)

The user's decisions:
- **Replace:** the old staged shapes are replaced outright, with no backwards compatibility.
- **Order:** start with the t-boost API.
- **No interim code:** "Don't defer if it means building, and removing code. Just figure out what the best approach is overall."

## Measured on the demo (542k rows, 144 tables, saved session)

| Measurement | Result |
|---|---|
| Pruning-path evidence | 3.7 KB (27 steps); about 1.0 s to compute, with or without main effects |
| `pruning_selection` | 4 ms, so it needs no cache |
| `from_json(doc).to_json() == doc` | True, for pruned, banded and smoothed candidates |
| Applied candidate's revision equals its point's | True, for all three kinds |
| Banded candidate (7.1 MB) | decode 0.23 s (includes one `from_json`, 0.06 s); apply 0.13 s |
| Smoothed candidate (2.9 MB) | decode 0.16 s; apply 0.10 s |
| `prepare_path` | pruned 11.3 s, banded 17.3 s, smoothed 5.2 s |
| Fit-time `prune_path_*` parameters | validated, but not used in the full-fit computation (`sklearn.py:4414`) |
| A pruned stage's inference document | already carries the path settings: `_prune_sessions` sets them on the estimator, so the revision already hashes them |
| Haute stage goldens | replace revisions and path ids with placeholders |

So:
- **Installing a snapshot keeps every identity.**
- **The candidate savings are tenths of a second each.** The typed API is justified mainly by clarity and ownership.
- **The remaining path-job time is elsewhere:** family preparation, and Haute's evaluation and frame preparation (plan step 5).

Sources:
- plan: `boundary-plan-2026-11-22.md` steps 2–4;
- reviews of v1: `reviews/tboost-api-review-codex-2026-11-22.md` (REQUEST_CHANGES) and `reviews/tboost-api-review-fable-2026-11-22.md` (proceed with changes).

## Unchanged

- **Estimators:** `TBoostRegressor`/`TBoostClassifier`, their fitting, and ordinary (non-staged) fits with pruning parameters.
- **Numerical results:** bit-identical, including working and inference banks, inference documents, prediction bytes and keep-sets.
- **Revisions:** stage revisions stay identical for the same inputs.
  - A pruned stage's hashed settings stay `{fraction, tolerance, max_tables}`, because its path settings are already in its hashed inference document.
  - `strength` is still omitted when it is 1.
  - A manual smoothing path still stores the multiplied alpha.
  - No value is coerced: `1` stays `1`.
- **Exception, non-default fit-time path settings:** a caller that passed non-default fit-time `prune_path_*` values (which staged fits now refuse; see Fitting) gets a new full revision. That flows on to the evidence digest, every descendant revision and the family `path_id`s, while banks and predictions stay equal. For default fits, which include all Haute fits with the default path, identities are exact. The oracle classifies these as expected metadata differences, separately from bank and prediction equality.
- **The evidence digest** is fixed by the full snapshot and the native training evidence. Computing, caching or using pruning evidence never changes it.
- **`path_id`:** unchanged for unchanged inputs. It hashes stage, parent, parent revision, data digest and point metadata.
- **Session diagnostics:**
  - `loss_history` (a dict), which stays on the session: every acquisition in Haute holds the session, and the inference model deliberately omits per-round history;
  - `data_digest` and `evidence_digest` (strings);
  - `fork()`.

**Changed formats** (versions bumped; older files are refused with the existing rebuild message):
- **Chain checkpoint:** the session-level `path_settings`, `path` and `path_unavailable_reason` go. The pruned stage's record carries its operation, including its path settings. The chain stores the session's evidence cache: one entry per settings value computed, whether for the applied stage or for a prepared path. A restart then never recomputes evidence that was already used. There is no active session-level setting. Entries are written in canonical settings order, with duplicates refused and unavailable outcomes kept, so the same cache contents always give the same bytes.
- **Candidate archive:** the top-level `path_settings` goes. `candidate.json` carries the candidate's operation as plain data, so a pruning candidate's path settings travel with it. Members are otherwise unchanged and deterministic.

## Order of work (nothing built to be removed)

1. **Oracle, recorded from the current code as data**, not as tests on the old API (boundary plan step 1). For a matrix of cases it records, per stage and per candidate:
   - working and inference native bytes and inference documents;
   - `predict_raw` bytes and `table_export`;
   - keep-sets, reports, statuses and revisions.

   The matrix covers regression and binary models, weights, exposure, categoricals, monotonicity, every operation and path kind, identity, unchanged and rejected candidates, and `n_jobs` 1 and 4. The data lives in `modelling-brainstorm/baselines/`.
2. **t-boost: the API below, in one change.** It includes snapshot installation, results seeded with live estimators, and the new formats. The staged suites are ported, every refusal is kept, and the new cases are added. The result is compared against the oracle.
3. **Haute: migrated to it in one change** (boundary plan steps 3, 4 and 7 together):
   - the adapter acquires a `StageResult` instead of the `prepared_stage`/`stage_operation` kwargs;
   - bundles and path jobs apply candidates;
   - `applied_path`, `path_params` and Haute's own recipe and settings interpretation are removed;
   - `StagedConfig.path` stays as Haute's **draft** pruning controls. `operation("pruned")` combines it with the pruning selection into one complete `Prune`, which every pruning use takes: cross-fold budgets (`_stage_paths.py:181`), with evidence, availability and the mandatory-table floor all queried for `selection.path` on every retained fit; the `PrunePath` request; and diagnostics in `remember_session`, which takes an explicit settings context instead of defaults;
   - the applied policy lives only in the typed applied recipe. Final fitting reads it, and later draft edits cannot alter it;
   - the frontend (`stageSettings.ts`) follows: no `applied_path`, recipes as the typed plain data, and draft comparison against the applied recipe's `Prune.path`.

   Ordinary (non-staged) t-boost fits keep `prune_main_effects` in Parameters. Staged configurations already refuse every `prune_*` Parameter (`_staged_config.py:198`).

   The stored stage goldens change deliberately, for the structural changes only (no `applied_path`; the recipe schema). Numerical results and identifier relationships stay, and metrics must equal the oracle's.
4. **Then plan steps 5 and 6** (acquire before preparing frames, `PreparedInput`). Both are additive, and they are where the remaining time is.

## Types (`t_boost/_stage_types.py`: value records only)

```python
StageName = Literal["full", "pruned", "banded", "smoothed"]
PostFitStage = Literal["pruned", "banded", "smoothed"]
CandidateStatus = Literal["identity", "changed", "unchanged", "rejected"]

@dataclass(frozen=True)
class PruningPathSettings:          # structural OOB evidence settings
    steps: int = 32
    main_effects: bool = False
    min_rows: int = 500

@dataclass(frozen=True)
class Prune:
    stage: ClassVar = "pruned"; parent: ClassVar = "full"
    fraction: float = 0.995
    tolerance: float = 0.001
    max_tables: int | None = None   # only with the default fraction and tolerance
    path: PruningPathSettings = PruningPathSettings()  # the evidence this selects from

@dataclass(frozen=True)
class Band:
    stage: ClassVar = "banded"
    parent: Literal["full", "pruned"] = "pruned"
    tolerance: float = 0.75
    deviance_cap: float = 0.001

@dataclass(frozen=True)
class Smooth:
    stage: ClassVar = "smoothed"
    parent: Literal["full", "pruned", "banded"] = "banded"
    alpha: float | None = None      # manual; requires strength 1
    high_order_alpha: float = 0.0
    strength: float = 1.0           # Auto only

Operation = Prune | Band | Smooth

@dataclass(frozen=True)
class Recipe:                       # portable policy across folds and final fits
    operations: tuple[Operation, ...] = ()

@dataclass(frozen=True)
class StageInfo:
    stage: StageName
    revision: str
    parent: StageName | None
    parent_revision: str | None
    operation: Operation | None     # None for "full"
```

- **Validation:** every constructor validates, taking over the private `_check_*` helpers.
  - Numbers must be finite, and a boolean is never accepted as a number.
  - `Recipe` coerces its operations to a tuple, and requires a continuous chain from `full`.
- **Settings:** `operation.settings()` returns the exact hashed settings dict, per Unchanged above. For `Prune` that excludes `path`, which its inference document already carries. Revisions use it and nothing else; there is no `dataclasses.asdict`.
- **Plain data:** each type has `to_plain_data()` and `from_plain_data()`.
  - Operation: `{"stage", "parent", "settings"}`, with `Prune` adding `"path": {steps, main_effects, min_rows}`.
  - `StageInfo`: complete: `{stage, revision, parent, parent_revision, operation}`. The point-metadata projection that `path_id` hashes stays a separate, fixed encoding.
  - Recipe: `{"schema_version": 1, "operations": [...]}`.
- **Strict decoding:** `from_plain_data` rejects unknown keys, unsupported versions, broken chains, non-finite numbers and booleans in numeric positions. Round trips are tested through real JSON encoding.

## Results and candidates (`_stage_results.py`, `_paths.py`)

```python
class StageResult:
    info: StageInfo
    recipe: Recipe
    data_digest: str
    evidence_digest: str
    @property
    def model(self) -> TBoostRegressor | TBoostClassifier: ...
```

**Ownership:**
- **A fresh result per call:** every `session.stage(...)`, `session.apply(...)` and `candidate.result()` call returns a fresh `StageResult`.
- **Captured contents:** each result captures its info, recipe, digests and immutable inference content when it is created. It does not look them up in the session later, and it holds no rows or session.
- **Its own model:** `model` belongs to that result. It is built on first access, or seeded with the live estimator the operation just produced, so nothing is rebuilt. Repeated access returns the same object, and no two results share one.
- **Isolation:** changing a result's model never affects the session, candidates, candidate bytes, checkpoints or other results.
- **Lifetime:** a result stays usable after its stage is invalidated or its session is released.

```python
class TBoostCandidate:
    point_id: str
    status: CandidateStatus
    identity: bool                  # property: status == "identity"
    target_stage: PostFitStage      # the stage the family prepares
    operation: Operation | None     # the attempted operation; None for identity
    outcome_id: str
    diagnostics: dict[str, Any]     # a copy on each access
    def result(self) -> StageResult: ...
    def to_bytes(self) -> bytes: ...
    @classmethod
    def from_bytes(cls, session, data, *, max_bytes=512 * 1024**2) -> TBoostCandidate: ...
    def to_plain_data(self) -> dict[str, Any]: ...  # today's point metadata
```

- **Identity candidates:** `result()` describes the **parent's** existing snapshot, with its own info and recipe. Applying one removes `target_stage` and its dependants, and returns the parent's result.
- **Distinct statuses:** an `unchanged` or `rejected` candidate keeps its attempted operation, its reports and its own revision. Refusing rejected selections remains Haute's product policy.
- **Binding:** a candidate privately binds, by value, to its parent stage and revision and to the data and evidence digests. A pruning candidate's path settings are part of its operation, so they need no separate check. It holds no session or rows, so it can be applied to any fork of the same fit.
- **Validation in `from_bytes`:** everything checked today stays, before `from_bytes` returns:
  - strict members and size limits;
  - the native working bank;
  - the inference document through the estimator's own validated loader: envelope and native consistency, categorical provenance, feature names, classes and objective;
  - `n_features_in_` against the fit, and the raw feature names against the bank;
  - for pruning, the operation's `path` equals the inference document's `prune_path_steps`, `prune_main_effects` and `prune_guard_min_rows`. The revision does not cover `path` directly, and a table budget can select the same keep-set under different step schedules, so without this check a candidate could advertise a policy its model does not record;
  - keep-sets: supports membership; for pruning, `keep == pruning_selection(op).kept == report.kept`; otherwise `keep == parent.keep`;
  - an identity revision equal to its parent's;
  - the content revision.

  The estimator built for validation becomes the first `result().model`, so a restored candidate costs one build, not three. Corruption tests recompute the revision after tampering, so a matching hash never admits invalid content.

```python
class TBoostPathFamily:
    path_id: str                    # unchanged
    candidates: tuple[TBoostCandidate, ...]
    def candidate(self, point_id: str) -> TBoostCandidate: ...
```

Path requests are typed per stage, because the swept value means something different in each:

| Request | Swept values | Each candidate's operation |
|---|---|---|
| `PrunePath(selection: Prune = Prune(), budgets: tuple[int, ...] \| None = None)` | Total-table budgets. By default, `pruning_budgets(selection)`. | `Prune(max_tables=budget, path=selection.path)`, with fraction and tolerance at their defaults |
| `BandPath(base: Band = Band(), fractions=None)` (the base gives the maxima) | Fractions m in (0, 1] | `Band(base.parent, sqrt(m)·base.tolerance, m·base.deviance_cap)` |
| `SmoothPath(base: Smooth = Smooth(), strengths=None)` | Auto: strengths; the default schedule includes `base.strength`, and an explicit schedule replaces it. Manual: alpha multipliers. | Auto: `Smooth(base.parent, strength=s, high_order_alpha=base.high_order_alpha)`. Manual: `Smooth(base.parent, alpha=base.alpha·s, strength=1, high_order_alpha=…)`. |

These keep today's defaults, the request order, repeated explicit values, the separate identity point, and the limit of 1–16 values.

## Session

| Old | New |
|---|---|
| `full_model`, `model(stage)` | `stage(name) -> StageResult` (`full` is rebuilt from native bytes, as today) |
| `stages -> dict[str, dict]` | `stages -> dict[StageName, StageInfo]` |
| `recipe(stage)` | `stage(name).recipe` |
| `prune(...)`, `band(...)`, `smooth(...)`, `apply_recipe(list)`, `family.apply(session, point)` | `apply(Operation \| Recipe \| TBoostCandidate) -> StageResult` |
| `configure_path(...)`, `pruning_path`, `path_unavailable_reason` | removed: `pruning_path(settings: PruningPathSettings = PruningPathSettings()) -> dict \| None` and `pruning_unavailable_reason(settings) -> str \| None` |
| `pruning_selection(fraction=…, …)`, `pruning_budgets(...)` | `pruning_selection(Prune)`, `pruning_budgets(Prune)` |
| `prepare_path(stage, parent=…, settings=…, values=…)` | `prepare_path(PrunePath \| BandPath \| SmoothPath) -> TBoostPathFamily` |
| `family.points`, `family.model(point)` | `family.candidates`, `candidate.result()` |
| `family.candidate_bytes(p)` / `TBoostPathFamily.from_candidate_bytes(s, b)` | `candidate.to_bytes()` / `TBoostCandidate.from_bytes(s, b)` |

How `apply` treats each argument:
- **`Operation`:** builds that stage and publishes it, invalidating dependants as today. `Smooth(strength != 1)` still goes through the one-point smoothing family; it is never solved directly with an already-selected alpha.
- **`Recipe`:** is validated in full, then replayed on a private branch against that fit's own evidence. Stages are committed together. On any failure or interruption, stages, recipes and identities stay as they were. The shared evidence cache may have been warmed, so a later checkpoint can include that entry. Missing-parent and unavailable-evidence failures are unchanged.
- **`TBoostCandidate`:** checks its binding, then installs its exact snapshot (working bank, inference document, keep-set). Nothing is rebuilt or re-serialised.

**Pruning evidence is per settings, not session state.**
- **Computed on demand:** it is computed the first time a `Prune`, a `PrunePath`, `pruning_path(settings)` or `pruning_budgets` needs it.
- **Cached on the shared fit state,** so all forks share it. It takes about 1 s and a few KB per settings value, so the cache needs no bound.
- **Unavailability is per settings:** with too few OOB rows for `settings.min_rows`, `pruning_unavailable_reason(settings)` gives the reason, and a `Prune` with those settings fails with it.

No mutator or invalidation rule remains: a `Prune` with different settings is simply a different operation. Publishing it replaces `pruned` and invalidates its dependants, like any operation. A band built directly on `full` is never affected. `stage("pruned").recipe` always carries the settings the stage was built with.

**Chain load, in order:**
1. Decode each stored evidence entry and validate it against its settings and this fit.
2. Install the entries in the evidence cache under their settings.
3. Decode the pruned stage's operation. Require its `path` to equal the inference document's settings, and require an entry for it.
4. Require `keep == pruning_selection(op).kept == model.pruning_report_["kept"]`.

Malformed or missing stored evidence is refused. It is never replaced by recomputed or default evidence. Tampering tests change the path settings while keeping the selected keep-set, for both chains and candidates.

## Fitting

`fit_staged` refuses non-default `prune_path_steps`, `prune_main_effects`, `prune_guard_min_rows`, `prune_path_fraction` and `prune_path_tolerance`, as it already refuses `prune_box_budget`. In staged training these belong to each `Prune`. The full fit never reads them (`sklearn.py:4414`), so the full stage's document and the evidence digest no longer depend on settings the GBM ignores, and Haute stops passing them.

## Verification

- **Bit identity, against the recorded oracle:**
  - native banks (working and inference), inference documents, prediction bytes, keep-sets and revisions;
  - every operation, recipe replay and path kind;
  - regression and binary models, with weights, exposure, categoricals and monotonicity;
  - `n_jobs` 1 and 4, and separate processes.

  Whole estimator envelopes are compared only within one fit, since envelopes carry `n_jobs` and timings.
- **Encodings:** new candidate bytes survive `from_bytes` → `to_bytes` unchanged and are equal across clocks. A chain survives save → load → save unchanged. Older candidates and chains are refused.
- **Settings:** an integer-valued tolerance, `strength` 1 against 2, and manual against Auto smoothing paths all keep their oracle revisions.
- **Pruning evidence:**
  - two settings values on one fit, and forks sharing the cache;
  - unavailability for one `min_rows` but not another;
  - a full-parent band surviving a re-prune with different settings;
  - fit-time `prune_path_*` refused.
- **Refusals:** every existing candidate and checkpoint refusal fails at `from_bytes` or `apply`, never later at `.model`.
- **New behaviour:**
  - result independence: mutation before and after another result's first `.model`, and use after its stage is invalidated;
  - identity semantics;
  - a transactional recipe that fails at each step;
  - applying to a fork.
- **Haute:** the stage goldens change only structurally (`applied_path`, the recipe schema). Revisions and ids are normalised to placeholders, so their relationships still have to match. Metrics equal the oracle's.

## Decided

- **Pruning-path settings belong to `Prune`** (the user: work out the best approach overall). This removes:
  - `configure_path` and its invalidation rule;
  - the pruning-only candidate check;
  - `Recipe`'s cross-field rule;
  - Haute's `applied_path`, `path_params` and the three-source fallback in `finalize`.

  For default fits, no identity changes. Non-default fit-time path settings migrate transitively (see Unchanged).
- **Dropped from v2:**
  - the interim performance step on the old API, which would have been built and then removed;
  - the `pruning_selection` cache, which is not needed at 4 ms.
- **Not done here:** canonicalising the hashed settings (always-present `strength`, float coercion). It would change every revision and cost the revision oracle, for a cosmetic gain.
