# t-boost staged API design: Fable review (22 Nov 2026)

Reviewed `tboost-api-design-2026-11-22.md`, read-only, independently of the Codex review. Verdict: **proceed with specific changes.**

## Shape

- **Keep:**
  - `TBoostCandidate` as its own type;
  - `session.apply(candidate) -> StageResult`, which collapses Haute's two `selected = parent if identity else operation` branches;
  - self-validating frozen operations;
  - `Recipe` carrying its pruning-path settings, which removes Haute's `applied_path` fallback chain.
- **One verb:** `session.apply(x)` accepts an `Operation`, a `Recipe` or a `TBoostCandidate`, so `prune(Prune())` and the like stop stuttering. The adapter's three-way branch (`_tboost.py:681-686`) becomes one call.
- **Typed selection:** `pruning_selection` and `pruning_budgets` take a `Prune`.
- **Names:** `PruningPathSettings` and `configure_pruning_path`, since "path" also means candidate families.
- **`prepare_path`:** `values` has three meanings, and the design should state them per type:
  - pruning discards fraction and tolerance when budgets are passed;
  - banding scales both settings;
  - smoothing multiplies a manual alpha, and a base strength only adds a point to the default schedule.
- **Ownership:** each `stage()`, `apply()` or `result()` returns a new `StageResult` owning its own model, because Haute mutates estimators on save (`_tboost.py:284-288`).
- **Candidates:**
  - `identity` becomes a property derived from the status;
  - add an explicit target stage;
  - add `outcome_id` as a plain attribute;
  - keep `max_bytes`.
- **Plain projections:** `to_plain_data()` on `Operation`, `StageInfo` and `TBoostCandidate`, returning today's dicts exactly. Haute persists them, and `write_chain` reads `stages` (`_checkpoint.py:251`).
- **`family.path_id`:** stays.

## Gaps against Haute

- **Cross-fold budgets:** fine, apart from the kwargs inconsistency.
- **Finalise replay:**
  - `apply_recipe` must configure the recipe's path settings atomically: evidence, reason, settings and stages together.
  - Haute must keep passing the path parameters at fit. They are in the full inference document via `get_params()`, so its revision and the evidence digest depend on them.
- **Identity candidates:** need the target stage. Loading keeps the check that the point's revision equals the parent's.
- **`strength`:** omitted from hashed settings when it is 1. `strength != 1` dispatches through the family code.
- **`fork()`:**
  - candidates bind by value, not by session identity;
  - the path-settings check stays pruning-only;
  - candidates hold no session or rows.
- **Candidate restore:**
  - `to_bytes()` must equal today's `candidate_bytes` byte for byte.
  - Either list exactly which checks move off the built estimator, or keep them all and hand the built estimator to the first `result().model`.
  - Haute still goes through bytes, because `run_path_job` prepares every fit's family before evaluating.
- **Missed bottleneck:** `pruning_selection` rebuilds the full estimator and deep-copies the evidence on every call. That happens in candidate validation, in chain load, and once per fit per point in Haute. Cache it.

## Bit-identity risks

1. **Snapshot install versus re-serialisation:** this holds only if `from_json(doc).to_json() == doc`. No test asserts that an applied stage's revision equals its point's. Write one first.
2. **Live versus rebuilt estimators:** stage operations return live estimators today, and `full_model` uses binary reconstruction. Compare `predict_raw`, `table_export`, `to_json` and `to_bytes`. Seed the `StageResult` with the live estimator when one exists.
3. **Hashed settings:** the raw dict stays the stored truth, with no coercion. `Band(tolerance=1)` and `Band(tolerance=1.0)` hash differently.
4. **Byte-equal encodings:** the `prune_path_*` spelling in `chain.json` and `candidate.json`, and `path_id` over the point metadata.
5. **Refusals:** each still fails at `from_bytes` or `apply`, never later at `.model`.

## Simpler

- **Performance first:** the performance goal needs none of the API replacement. Install the snapshot in `apply`, seed the returned model, and cache `pruning_selection`, as a small diff. The typed API is justified by ownership and clarity, so land it separately, after the fixed-point test.
- **Optional:** put the pruning-path settings on `Prune` itself, with evidence cached per settings. That removes `configure_path`, its invalidation rule, the candidate path-settings check and `Recipe`'s cross-field rule. It touches checkpoint semantics.
