# t-boost / Haute boundary plan (22 Nov 2026)

Source: `reviews/boundary-review-codex-2026-11-22.md`. The user's goals are to remove the
obvious bottlenecks and to make the relationship between the two libraries elegant.

## Target

The boundary is **acquire a fitted model, then evaluate it**:
- **Haute** keeps the canonical evaluator, product policy and storage;
- **t-boost** supplies complete, typed stage results and reusable candidates.

Three seams cross the boundary:
1. **Acquisition:** fit, retained stage, or candidate → a model plus its provenance.
2. **Scoring and explanation:** a prepared input → raw scores and the table export.
3. **Persistence:** evidence and chain parts, candidates, and inference documents, as bytes.

Typed values cross; JSON text crosses only for persistence and HTTP.

## What each library owns

- **t-boost:**
  - fitting and stage transformations;
  - candidate preparation and validation;
  - recipe validity, so its recipe is authoritative and complete;
  - stage identities;
  - the exact working and inference banks;
  - serving-input preparation, as an opaque `PreparedInput`.
- **Haute:**
  - partitions and pooled out-of-fold rows;
  - metrics and AvE;
  - offsets and class semantics;
  - enabled phases and pending or applied state;
  - cross-fold budget floors and recommendation policy;
  - finalisation;
  - content-addressed storage and publication.

## Migration (each step behaviour-preserving, reviewed, measured)

| # | Step | Verification | Cost removed |
|---|---|---|---|
| 0 | Defects found by the review: candidate archives were nondeterministic, and validation frames are loaded for t-boost only to be discarded. | Byte equality across clocks; identical canonical validation scores. | Some I/O and memory; deduplication works again. |
| 1 | Contract oracle: extend the goldens (native banks, prediction bytes, recipes, stage revisions, path metadata, metrics, AvE, point members) across regression and binary models, weights, offsets, groups, categoricals, monotonicity, and skipped, identity and rejected candidates. | The goldens themselves. | None; it is the oracle. |
| 2 | Typed recipe, settings and result objects in t-boost, with recipe validation authoritative there. | Defaults and accepted or rejected settings unchanged; replay on folds and final fits reproduces banks and reports. | Mainly clarity. |
| 3 | A typed acquisition result in place of the `prepared_stage`/`stage_operation` kwargs; reuse the model candidate application already returned. | Restore counts drop; estimator metadata, prediction bytes, stage records and artifacts unchanged; no boosting on resume. | One redundant restore per candidate. |
| 4 | In-memory candidates: `TBoostCandidate.result()` without forking a session; serialise only for persistence and bounded spilling. | Live and reloaded candidates equal (banks, predictions, recipes, revisions); refusals unchanged; returned models independent. | Candidate zip decode, repeated validation and `from_json` (three per candidate today). |
| 5 | Haute acquires a retained model before preparing training frames; table reports on demand. | Ordinary and supplied-model evaluation agree on the same context; no label conversion or level scan on retained acquisition; no unused selection-fold tables. | Scans, casts, unused estimator construction and unused tables. |
| 6 | `PreparedInput`, reused through Haute's evaluation context under a memory budget. | Frame and prepared-input bytes equal (predictions, tables); column order, unseen and rare categories, dtypes, eviction. | Repeated serving preparation. |
| 7 | Remove Haute's duplicate recipe interpretation and the obsolete adapter kwargs. | The full → paths → Apply → restart → finalise goldens; stale, cancelled and failed publication paths. | Bookkeeping; completes the ownership change. |

Standards for every step:
- **Bit identity:** across thread counts, processes and seeded reruns, comparing native model bytes separately from estimator envelopes, and working banks as well as inference banks.
- **Caches:** they share an explicit memory budget.

## Progress (23 Nov)

- **Step 1, oracle:** recorded from the old API as data (`baselines/oracle_record.py`,
  `oracle-staged-api.json`).
- **Steps 2–4, t-boost:** the typed API (`tboost-api-design-2026-11-22.md`), committed as
  t-boost `72d0075`. `oracle_compare.py` reports no difference from the old API's numbers.
- **Steps 3, 4 and 7, Haute:**
  - the adapter acquires its model with one `session.apply(target)`;
  - bundles restore candidates themselves (`StageBundle.candidate`);
  - `applied_path`, `path_params` and Haute's own recipe interpretation are removed;
  - `StagedConfig` builds t-boost operations;
  - stage configuration and staged payloads are schema version 2;
  - the stage goldens are regenerated for the structural changes only. Every configuration
    and snapshot value equals the old goldens'.
- **Cost:** a restored 7 MB path candidate cost about 0.42 s (decode with one build, apply
  with another build and re-serialisation, then the adapter's third build); it now costs
  about 0.23 s (decode with one build; apply installs the snapshot).
- **Remaining:** steps 5 (acquire before preparing frames) and 6 (`PreparedInput`).

## Done before this plan (22 Nov)

- **Pruning family:** pruning budgets run together natively.
- **Parallel banding candidates:** pending review.
- **Table export:** `table_export` parses once; a direct float32 conversion was rejected because it changes values.
- **Evaluation duplication:** snapshots share diagnostics, and stage jobs reuse the bundle's plan.
- **Point results:** encoded once.

## Out of scope / kept

- **Working and inference banks stay separate,** since stage chaining needs the working bank.
- **No substitutes for Haute's evaluation:** its out-of-fold pooling and source-row checks are never replaced by t-boost's OOB scores.
- **Spec drift to resolve:** the t-boost roadmap describes coded native categoricals as delivered, while loading still builds per-row strings. Reconcile the documents; item D stays deferred.
