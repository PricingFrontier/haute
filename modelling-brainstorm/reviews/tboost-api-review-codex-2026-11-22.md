The direction is sound, but the design needs changes before implementation. The main gaps concern ownership, validation, identity candidates, and the distinction between a replay policy and its stored representation.

1. **P1 — Preserve semantic validation when removing estimator construction.**  
   [Design:114](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:114) is too loose about validating “the schema … where possible.”

   The current [candidate decoder](/home/ralph/suite/t-boost/python/t_boost/_paths.py:183) validates both native banks, the inference estimator’s input-feature count, equality of raw feature names, keep-set semantics, and the content revision. Calling `from_json` also performs [envelope/native consistency checks](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:1144): categorical provenance, feature names, classes, objective, and fitted metadata.

   **Correction:** extract a shared validated-inference loader used by ordinary estimator restoration and candidate decoding. It may defer creating the Python estimator, but must validate the native inference document and its envelope before returning an applicable candidate. Comparing only JSON header fields is insufficient: `n_features_in_` is normally omitted, and categorical channel axes are not necessarily raw input features.

   Explicitly retain:
   - Pruning: `keep == session.pruning_selection(operation)["kept"] == inference_report["kept"]`.
   - Banding/smoothing: `keep == parent.keep`.
   - Support membership/type checks, estimator kind, feature-schema checks, revision verification, strict archive members, and compressed/expanded size limits.
   - The existing configurable `max_bytes` argument, absent from the proposed signature.

   Corruption tests should recompute the revision after changing the feature schema or keep-set. A matching hash must not make semantically invalid content acceptable. Until equivalent shared validation exists, keep eager validation.

2. **P1 — Lazy `StageResult.model` is sound only with explicit ownership rules.**  
   [Design:68](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:68) does not establish snapshot independence. This is observable today: Haute [mutates estimator metadata when saving](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:282).

   **Correction:** specify that every `session.stage(...)`, `candidate.result()`, and operation/application call returns a fresh result handle. Each handle captures immutable inference content, stage information, the complete recipe, and digests **at creation**, rather than looking them up from the session on first access.

   Repeated `.model` access on one handle may return the same caller-owned mutable estimator. Different handles must never share that estimator or mutable native state. Mutating it must not affect candidates, candidate bytes, checkpoints, another result, or later transformations. `session.apply(candidate)` must always install the candidate’s immutable prepared content.

   Document that `info` and `recipe` describe the captured snapshot; they do not change if the caller subsequently refits or modifies the returned estimator. Results should remain usable after their originating session changes or is released, without retaining its training arrays merely for lazy construction.

   Test mutation both before and after another handle’s first `.model` access, and invalidate the originating stage before materializing an old result.

3. **P1 — Identity candidates need separate target and result identities.**  
   [Design:81](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:81) calls `info` “the stage this candidate would publish,” but an identity candidate publishes no stage. The [current implementation](/home/ralph/suite/t-boost/python/t_boost/_paths.py:94) returns its parent’s model and removes the targeted stage and its descendants.

   **Correction:** distinguish `target_stage` from the effective result’s `StageInfo`. For a banding identity candidate whose parent is pruned:
   - `target_stage == "banded"`;
   - `candidate.result().info` and recipe describe the existing **pruned** snapshot;
   - `session.apply(candidate)` removes banded and its descendants, then returns that parent snapshot.

   The candidate must separately retain the application parent/revision and fit binding. Its result’s `parent_revision` describes the parent stage’s own ancestry and cannot serve as that binding.

   Also state that `identity`, `unchanged`, and `rejected` remain distinct. An unchanged or rejected nonidentity candidate retains its attempted operation, reports, and stage revision; identical predictions do not turn it into an identity candidate. Haute currently refuses rejected selections through its [product policy](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:530).

4. **P1 — Define path invalidation and transactional recipe replay together.**  
   [Design:102–109](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:102) omits important existing behavior and leaves the new `Recipe.path` operationally unspecified.

   **Correction:** retain the [current reconfiguration rules](/home/ralph/suite/t-boost/python/t_boost/_staged.py:545): equal settings are a no-op; changed settings recompute structural OOB evidence without boosting, preserve the full snapshot/revision, and invalidate pruned plus its actual descendants. A banded stage derived directly from full survives.

   Candidate path-setting equality should apply to **pruning candidates**, as [today](/home/ralph/suite/t-boost/python/t_boost/_paths.py:83). Unconditionally checking it would wrongly reject a full-parent banding or smoothing candidate after unrelated pruning-path reconfiguration.

   `apply_recipe` must validate the complete recipe, configure its pruning path on a private branch, replay against that fit’s own evidence, and commit **path settings, evidence, availability reason, and stages together**. Failure or interruption must preserve all original state. The current transaction [commits only stages](/home/ralph/suite/t-boost/python/t_boost/_staged.py:471); adding recipe-owned path settings requires expanding that transaction.

   A recipe extracted from a result must capture the applied path settings, independently of later pending controls. Across folds and final fits it carries selection policy or table budget and Auto strength—not support IDs, path indices, fitted alpha vectors, or source-fit identities. Explicitly retain missing-parent and unavailable-evidence failures.

5. **P1 — `prepare_path(operation, values)` needs stage-specific semantics.**  
   [Design:103](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:103) incorrectly suggests that an operation uniformly supplies fixed settings. The [three existing paths](/home/ralph/suite/t-boost/python/t_boost/_paths.py:238) interpret values differently:

   | Path | Swept value | Effective candidate operation |
   |---|---|---|
   | Pruning | Integer total-table budget | `Prune(max_tables=budget)` with default fraction/tolerance |
   | Banding | Budget fraction `m`, `0 < m <= 1` | Tolerance `sqrt(m) * maximum_tolerance`; cap `m * maximum_cap` |
   | Auto smoothing | Positive strength | `Smooth(alpha=None, strength=value)` |
   | Manual smoothing | Positive multiplier | `Smooth(alpha=base_alpha * value, strength=1)` |

   For pruning, fraction/tolerance influence the **default schedule**, rather than remaining fixed candidate settings. For smoothing, the operation’s existing `strength` raises an ambiguity: does it multiply supplied values, get replaced, or only enter the default schedule?

   **Correction:** prefer a discriminated request union such as `PrunePath(selection, budgets)`, `BandPath(parent, maximum_tolerance, maximum_deviance_cap, fractions)`, and `SmoothPath(parent, alpha, high_order_alpha, strengths)`. An overloaded `prepare_path` can still be the entry point. If retaining the proposed signature, specify equivalent rules explicitly.

   Preserve default schedules, the inclusion of the requested Auto strength in the default smoothing schedule, request order, repeated explicit values, the separate identity point, and the 1–16 positive/nonnegative-value limits. Haute needs explicit integer budgets to apply its cross-fold mandatory-table floor.

   Crucially, retain [direct smoothing’s dispatch](/home/ralph/suite/t-boost/python/t_boost/_staged.py:423): Auto `strength != 1` goes through the prepared-path implementation. It must not become a direct solve using an already selected alpha.

6. **P1 — Specify the recipe storage schema separately from revision/checkpoint encoding.**  
   [Design:56](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:56) names conversion methods without defining their durable contract. Haute needs a versioned, discriminated representation, for example:

   ```json
   {
     "schema_version": 1,
     "pruning_path": {
       "steps": 32,
       "main_effects": false,
       "min_rows": 500
     },
     "operations": [
       {
         "stage": "pruned",
         "parent": "full",
         "settings": {
           "fraction": 0.995,
           "tolerance": 0.001,
           "max_tables": 12
         }
       }
     ]
   }
   ```

   **Correction:** define exact keys, operation discriminators, default handling, and `null` path settings for recipes without pruning. Return freshly owned JSON-compatible data. Reject unsupported versions, unknown fields, malformed chains, nonfinite numbers, and booleans in numeric positions. Require round-trip equality through actual JSON encoding.

   Do **not** use generic `dataclasses.asdict()` as the checkpoint or revision codec. [Stage revisions](/home/ralph/suite/t-boost/python/t_boost/_staged.py:360) hash exact settings and inference text. Current smoothing omits `strength` when it equals 1, and manual paths store the multiplied alpha. Adding default fields, including parent/stage inside settings, or changing `1` to `1.0` can change revisions without changing predictions.

   Keep an explicit existing-format encoder for checkpoint stages, candidate metadata, and revision inputs. This is required by the design’s unchanged checkpoint/revision promise, despite replacing the Python API outright. The [checkpoint writer](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:248) currently consumes `session.stages` dictionaries directly and will need that separation.

7. **P2 — Complete the public surface and tighten the types.**  
   At [Design:22](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:22), I would make these corrections explicit:

   - Export `StageName`, `PostFitStageName`, and `CandidateStatus` aliases. Type `StageInfo.parent` appropriately rather than as arbitrary `str`. Define actual literal-valued `.stage` properties/class variables and `Prune.parent`; the comment on `Operation` does not provide them.
   - Prefer `PruningPathSettings` and `Recipe.pruning_path`; generic “path” also describes banding and smoothing families.
   - Keep frozen value records in `_stage_types.py`. Candidate persistence/validation belongs naturally in `_paths.py`, with result materialization kept separate from mathematical settings. Preserve package lazy exports.
   - Make candidate/result metadata read-only, and ensure recipes own a tuple of validated operations. Frozen dataclasses alone do not reject a supplied mutable list.
   - Explicitly retain `fork()`, `family.path_id`, stable point ordering/IDs, and the outcome fingerprint. Haute uses [forks for cached sessions](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:357) and [family IDs/outcomes for persistence and evaluation reuse](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:306). Forks must preserve independent stages and path settings while sharing immutable fit evidence.
   - Correct the “diagnostics … dicts” list: `path_unavailable_reason` is `str | None`, `pruning_budgets` is `list[int]`, and the digests are strings. Selection/budget methods should accept `Prune` or delegate to its validation rather than retain a second mathematical validator.

   Also resolve original fit-history ownership for session-free acquisition. Haute [uses `session.loss_history`](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:689), while inference serialization [deliberately omits per-round history](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:1064). Supply it through a lightweight immutable fit summary or an explicitly retained acquisition context. `StageResult.model` alone cannot supply everything needed for the existing `FitResult`.

The verification section should add the behavioral cases above, beyond porting existing assertions. For bit identity, compare old/new inference documents and revisions from identical retained evidence and metadata; compare native banks and prediction bytes across processes and thread counts. Whole estimator envelopes contain `n_jobs` and [fit timestamps/durations](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:1054), so independent fits do not generally have identical envelopes or revisions.

The earlier ZIP timestamp defect is already [fixed in this checkout](/home/ralph/suite/t-boost/python/t_boost/_paths.py:120); retain its deterministic serialization test. This was a read-only source/specification review. I changed no files and ran no tests.

REQUEST_CHANGES