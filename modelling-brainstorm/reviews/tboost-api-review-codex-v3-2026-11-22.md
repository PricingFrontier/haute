Moving pruning-path settings onto `Prune` is a sound choice. V3 resolves the ownership, identity-candidate, eager-validation, and transactional-replay concerns. There are still implementation-blocking gaps in policy propagation, persistence validation, and the promised identity/storage compatibility.

I verified the principal code claims:

- **Path settings are already revision-bound through inference JSON.** [`_prune_sessions`](/home/ralph/suite/t-boost/python/t_boost/_staged.py:314) sets the three structural settings plus fraction/tolerance on the estimator. Serialization includes its parameters, and [`_revision`](/home/ralph/suite/t-boost/python/t_boost/_staged.py:360) hashes the inference document.
- **They do not affect the captured full-bank computation.** The staged branch [returns before pruning selection](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:4414). “Not read” is slightly inaccurate: some are read during validation. Say “not used in the full-fit computation.”
- **Snapshot installation can preserve identities.** My small in-memory probe confirmed JSON fixed points and applied-revision equality for pruning, banding, and smoothing.
- **`path_id` can remain unchanged for unchanged inputs**, but its hash includes stage, parent, parent revision, data digest, and point metadata—not just point metadata. See [`_paths.py:56`](/home/ralph/suite/t-boost/python/t_boost/_paths.py:56).

I inspected the measurement scripts but did not repeat the 542k-row benchmark. Also, [`check_fixed.py:22`](/home/ralph/suite/haute/modelling-brainstorm/baselines/check_fixed.py:22) stops after the first nonidentity candidate of each kind; it establishes examples, not exhaustive fixed-point coverage.

1. **P1 — Explicitly validate the operation’s path against the hashed inference document.**  
   [V3:124](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:124) excludes `Prune.path` from hashed settings, while [V3:168](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:168) removes the separate settings check. This works only if decoding verifies that the serialized operation’s path equals the path settings inside its inference document.

   Otherwise, changing `operation.path.steps` can leave the content revision valid. For a manual table budget, different step schedules can select the same keep-set, so the proposed keep-set comparisons also pass. The candidate would then advertise a replay policy different from the one its model records.

   **Correction:** both candidate and chain loaders must require equality between `op.path` and the inference estimator’s `prune_path_steps`, `prune_main_effects`, and `prune_guard_min_rows`. Continue checking the operation’s selection settings through the existing revision inputs.

   For chain loading, specify this order explicitly: decode the pruned operation; validate its stored evidence against that operation’s path and fit; make that evidence available under the correct cache key; then require:

   ```python
   stage.keep == pruning_selection(op)["kept"] == model.pruning_report_["kept"]
   ```

   The current [chain validator](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:614) obtains selection from session-level evidence, so removing that state requires an explicit replacement. Do not silently compute default evidence or substitute recomputed evidence for malformed stored evidence. Add tampering tests where path settings change but the selected keep-set does not.

2. **P1 — The path-request table still loses required policy fields.**  
   [V3:191](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:191) says each pruning candidate is `Prune(max_tables=budget)`. That resets the newly important path to defaults.

   **Correction:**

   ```python
   Prune(max_tables=budget, path=request.selection.path)
   ```

   Fraction/tolerance still reset to their defaults for budget candidates, as intended.

   Haute’s [cross-fold budget calculation](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:181) must use one configured `Prune` throughout:

   ```python
   selection = config.operation("pruned")
   evidence = session.pruning_path(selection.path)  # on every retained fit
   schedule = reference_session.pruning_budgets(selection)
   request = PrunePath(selection=selection, budgets=shared_budgets)
   ```

   Availability checks must use those same settings. Preserve the maximum mandatory-table floor across fits; querying default evidence could reject a valid low-`min_rows` configuration or calculate the wrong floor when main effects are prunable.

   The [smoothing request](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:193) also promises to include “the requested strength” without having a field for it. A concrete shape is `SmoothPath(base: Smooth = Smooth(), strengths=None)`: default Auto schedules include `base.strength`, explicit schedules override it, and every generated operation preserves `base.parent` and `base.high_order_alpha`. Manual candidates store the multiplied alpha with strength 1.

3. **P1 — The full-stage identity exception propagates beyond full.**  
   [V3:41](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:41) limits the exception to the full stage, while [V3:259](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:259) says “No revision changes.” Both need correction.

   My probe compared two seeded fits with different fit-time pruning settings, normalized their timing metadata, and then applied identical pruning settings:

   | Comparison | Equal? |
   |---|---|
   | Native training bytes | Yes |
   | Full working bank | Yes |
   | Pruned working bank and inference document | Yes |
   | Pruned revision | **No** |
   | Evidence digest | **No** |
   | Pruning family `path_id` | **No** |

   Descendant revisions hash their parent revision. The [evidence digest](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:172) includes the full revision, and family IDs include parent and point revisions. Banding/smoothing directly from full can also inherit changed parameter metadata in their inference documents.

   **Correction:** explicitly permit this transitive identity migration for workflows formerly passing non-default fit-time settings. Preserve exact identities for comparable default-fit cases. Classify the oracle’s expected metadata differences separately from native-bank and prediction equality.

   Within a new fit, keep `evidence_digest` based on the immutable full snapshot and native training evidence. Computing, caching, or applying any pruning-path settings must never change it.

4. **P1 — Haute’s storage migration contradicts the unchanged-goldens requirement.**  
   [V3:64](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:64) removes `applied_path`; recipes become versioned objects containing complete operations. [V3:249](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:249) nevertheless requires unchanged goldens.

   I inspected both stored goldens: they contain `configuration.applied_path` and list-valued `applied_recipe`. Their [normalizer](/home/ralph/suite/haute/tests/_stage_goldens.py:51) replaces identifiers and selected volatile fields; it does not remove those configuration fields or normalize recipe schemas.

   **Correction:** keep `StagedConfig.path` as Haute’s **draft pruning controls**, and have `operation("pruned")` combine it with the pruning selection settings into a complete `Prune`. Store the applied policy exclusively in the typed recipe. Final fitting reads that recipe; later draft edits cannot alter it.

   Make the corresponding persistent schema changes explicit and update the goldens for those deliberate structural changes, preserving numerical results and identifier relationships. The migration must include the frontend: [`stageSettings.ts`](/home/ralph/suite/haute/frontend/src/modelling/stageSettings.ts:14) currently requires `applied_path`, assumes recipes are arrays, and uses separate path settings for draft comparisons.

   Also update [`remember_session`](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:762) to request evidence/recommendations for an explicit settings context. Its current argument-free diagnostics cannot simply become calls using defaults.

5. **P2 — `StageInfo` still lacks a lossless plain-data representation.**  
   [V3:125](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:125) promises conversion methods on every value type, but defines `StageInfo`’s output as today’s stage dictionary. That dictionary contains neither its stage name nor `Prune.path`, so it cannot reconstruct the proposed value.

   **Correction:** give `StageInfo` a complete representation containing `stage`, revision/parent fields, and the serialized operation. Keep the existing point-metadata projection specifically for the unchanged family-hash algorithm. These serve different permanent purposes.

Two smaller scope/performance points should also be settled:

- Removing `prune_main_effects` from Haute’s global parameter list affects ordinary, non-staged fits too. Its [descriptor](/home/ralph/suite/haute/src/haute/modelling/_descriptors.py:725) serves both modes. Restrict that removal to staged configuration, or explicitly approve and document the broader behavior change.
- A chain storing evidence only for its applied pruned stage cannot retain newly prepared pruning evidence when its parent is full. After restart or cache eviction, candidate validation must recompute it. Preserve the settings/evidence entries needed by prepared paths in the new chain format, without introducing an active session-level setting. Otherwise the measured approximately one-second evidence calculation can outweigh the stated decode savings.

Against my original seven points: **#1–3 are addressed**, subject to the new operation/document consistency check above; **#4 is soundly replaced by operation-owned settings and transactional stage publication**; **#5–6 remain partially addressed**; **#7 is mostly addressed**, including names, `fork`, IDs, typed selection, and retaining session-owned loss history.

I changed no files. Verification comprised source review, inspection of stored golden data, and a small in-memory numerical/identity probe.

REQUEST_CHANGES