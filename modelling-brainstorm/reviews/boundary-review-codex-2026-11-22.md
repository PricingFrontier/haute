The boundary should be **“acquire a fitted model, then evaluate it”**. Haute should keep the canonical evaluator; t-boost should supply complete stage results and reusable candidates. A retained candidate should not need to masquerade as a fit.

Several earlier optimisations are already present. The remaining problem is chiefly orchestration and object lifetime, rather than private-method access.

This was a read-only source review with small in-memory probes. I did not re-benchmark the 542k-row workload or run the full test suites.

The current boundary has three owners:

- **Haute owns evaluation and product policy:** partitions, positive-class encoding, offsets, metrics, pooled OOF diagnostics, AvE, recommendation tolerance, draft/preview/applied state, finalisation and durable storage.
- **t-boost owns model transformations:** fitting, retained OOB evidence, pruning selection, banding, smoothing, working versus repaired inference banks, stage identities and candidate validation.
- **The adapter translates between them**, but currently also orchestrates several kinds of model acquisition through `fit()`.

The crossings in the reviewed workflow are:

| Crossing | What crosses today | Ownership and source |
|---|---|---|
| Training configuration | Haute loss names → native objective/link; parameters, seed, threads, monotonicity, unknown-category policy | Haute’s supported product configuration is defined in [_descriptors.py:703](/home/ralph/suite/haute/src/haute/modelling/_descriptors.py:703); estimator construction is in [_tboost.py:617](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:617). |
| Training data | Polars feature frame; NumPy labels, weights, exposure/offset and groups | Haute fixes feature order and casts categories to strings and numerics to Float64. It converts its offset contract into t-boost fit arguments. [_tboost.py:180](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:180), [_tboost.py:456](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:456). |
| Ordinary/staged acquisition | `estimator.fit(...)`, or `fit_staged(...)` followed by `apply_recipe(...)` | t-boost fits and transforms; Haute chooses the configured route. [_tboost.py:650](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:650). |
| Fit progress and evidence | Callback dictionaries; `evals_result_`/`loss_history`, `n_trees_`, `stopping_reason_` | t-boost reports native evidence; Haute converts it into canonical progress and `FitResult`. [_tboost.py:478](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:478), [_tboost.py:689](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:689). |
| Resumed stage acquisition | `stage_session`, `prepared_stage`, `stage_operation` inside untyped adapter kwargs | Haute chooses a branch; the adapter either calls `session.model`, configures/prunes, bands or smooths. [_training_job.py:2758](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:2758), [_staged_runtime.py:662](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:662), [_tboost.py:655](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:655). |
| Pruning schedule | `configure_path`, `pruning_path`, `pruning_budgets`; shared integer budgets | t-boost already owns its default schedule. Haute raises budgets to the mandatory-table floor across retained fits. [_stage_paths.py:181](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:181), [_staged.py:527](/home/ralph/suite/t-boost/python/t_boost/_staged.py:527). |
| Path preparation | Stage, parent, settings and values → `TBoostPathFamily`; detached point dictionaries, IDs, outcomes and candidate bytes | Native transformation and complexity belong to t-boost; cross-fold organisation belongs to Haute. [_stage_paths.py:285](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:285), [_paths.py:235](/home/ralph/suite/t-boost/python/t_boost/_paths.py:235). |
| Candidate restoration | Checkpoint session → fork → `from_candidate_bytes` → `apply` | Haute restores an already prepared candidate before handing the session to its adapter. [_staged_runtime.py:339](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:339). |
| Predictions | Prepared frame → `predict_raw` or `decision_function` → float64 margin; Haute adds baseline and applies inverse link | Haute retains its response-scale contract and canonical evaluation. [_tboost.py:196](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:196), [_training_job.py:1525](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:1525). |
| Explanations | `predict_contributions`, `feature_importances_`, `table_export` | t-boost supplies mathematical outputs; Haute supplies display labels, table shapes, relativities and diagnostic presentation. [_tboost.py:214](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:214), [_tboost.py:253](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:253), [_tboost.py:718](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:718). |
| Stage provenance | `stages`, `recipe`, data digest, pruning evidence, recommendation | t-boost exposes these separately; Haute assembles them into staged results and applied configuration. [_staged_runtime.py:762](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:762), [_staged_runtime.py:791](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:791). |
| Training persistence | `save_evidence`, `save_chain`, `load`, candidate ZIP bytes | t-boost owns payload formats and validation. Haute owns placement, content addressing, revision closure and leases. [_staged_runtime.py:325](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:325), [_staged_runtime.py:373](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:373), [_checkpoint.py:215](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:215). |
| Inference persistence | Native JSON document with Haute’s contract and stage metadata in `metadata` | Haute writes its record, invokes `to_json`, and on load inspects the envelope before `from_json`. [_tboost.py:282](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:282), [_tboost.py:317](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:317). |
| Apply and finalise | Apply publishes references to the exact candidate; finalise starts a canonical final job with frozen applied settings | These are correctly Haute responsibilities. Apply does not need to reconstruct training arrays. Finalise deliberately performs fresh fitting and recipe replay. [_stage_paths.py:512](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:512), [_stage_store.py:870](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:870), [_staged_runtime.py:948](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:948). |

The supplied friction list needs these corrections:

- **Table export:** fixed at the Haute boundary. `table_export` returns Python data. Internally, t-boost still obtains native JSON, parses it once and annotates it in place. The native binding still returns `str`. [_sklearn.py:3195](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:3195), [_t_boost.pyi:632](/home/ralph/suite/t-boost/python/t_boost/_t_boost.pyi:632).
- **Stage estimator reconstruction:** still present. `session.model(stage)` and `family.model(point)` use `from_json`; `full_model` already uses binary reconstruction. [_staged.py:176](/home/ralph/suite/t-boost/python/t_boost/_staged.py:176), [_staged.py:393](/home/ralph/suite/t-boost/python/t_boost/_staged.py:393), [_paths.py:80](/home/ralph/suite/t-boost/python/t_boost/_paths.py:80).
- **Point-result round trips:** the old normalisation encode/decode is gone. Results now go directly through diagnostic splitting and canonical storage encoding, with a real-path byte-equality regression test. This does not mean every identical subobject is encoded only once globally. [_stage_paths.py:208](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:208), [_stage_diagnostics.py:118](/home/ralph/suite/haute/src/haute/modelling/_stage_diagnostics.py:118), [test_staged_runtime.py:1368](/home/ralph/suite/haute/tests/test_staged_runtime.py:1368).
- **Private t-boost access:** I found no current Haute production use of the named `_state`, `_stages`, `_path_settings`, `_model`, `_serve_kwargs` or `_measure_kwargs` surfaces. There is test coupling, for example [test_staged_runtime.py:113](/home/ralph/suite/haute/tests/test_staged_runtime.py:113). The named production accesses are inside t-boost.
- **One full job per point:** mostly true, but exact outcome aliases already reuse canonical evaluation when fingerprints match across **all** retained fits. [_stage_paths.py:357](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:357).
- **Repeated preparation:** confirmed structurally. The precise “60 ms” serving cost was not independently reproduced. The repository records measurements involving 542k rows, but those are historical measurements, not a fresh profile of this checkout. [performance-plan:597](/home/ralph/suite/haute/modelling-brainstorm/performance-plan-2026-11-22.md:597).

I would rank the design problems as follows. Performance ranks concern avoidable boundary overhead, not total native training time.

| Problem | Clarity rank | Performance rank | Evidence and consequence |
|---|---:|---:|---|
| `fit()` means both fitting and retrieving a retained model | 1 | 1 | `_train_model` materialises training data before resolving the resumed acquisition. The adapter learns categorical levels, constructs an estimator and prepares labels/weights/features before discovering that it only needs `session.model`. [_training_job.py:2601](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:2601), [_tboost.py:606](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:606), [_tboost.py:644](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:644). |
| Serialized candidates are the normal in-process handoff | 3 | 2 | Haute serializes every candidate and drops the family, then decodes candidates during evaluation. Loading validates an estimator, applying reconstructs it again, and the adapter reconstructs it again. Apply also serializes the estimator during `_publish`. [_stage_paths.py:311](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:311), [_paths.py:187](/home/ralph/suite/t-boost/python/t_boost/_paths.py:187), [_paths.py:94](/home/ralph/suite/t-boost/python/t_boost/_paths.py:94), [_staged.py:369](/home/ralph/suite/t-boost/python/t_boost/_staged.py:369). |
| Stage policy has two interpreters | 2 | 5 | Haute duplicates defaults, validation and recipe-chain rules, then separately preserves applied path settings. t-boost validates the same mathematical operations. Enabled phases and pending/applied choices belong in Haute; mathematical recipe validity does not. [_staged_config.py:14](/home/ralph/suite/haute/src/haute/modelling/_staged_config.py:14), [_staged_config.py:91](/home/ralph/suite/haute/src/haute/modelling/_staged_config.py:91), [_staged.py:490](/home/ralph/suite/t-boost/python/t_boost/_staged.py:490). |
| There is no reusable public serving input | 4 | 3 | Every prediction/table request repeats feature selection, numeric conversion, categorical splitting and unknown routing. The existing split cache stores partition Parquet, not prepared model inputs. [_sklearn.py:3227](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:3227), [_staged_runtime.py:636](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:636). |
| Table-report production is attached to acquisition | 5 | 4 | Every adapter fit computes `table_report(train_df)`, including selection fits whose canonical evaluation only needs held-out predictions. Required reference-model tables must remain, but unused fold tables need not be generated. [_tboost.py:693](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:693), [_training_job.py:1462](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:1462). |

The in-memory probe observed **three `TBoostRegressor.from_json` calls for one nonidentity candidate’s decode → apply → `session.model` sequence**. The resulting predictions were byte-equal. That establishes redundant work, although it does not establish its share of the large-workload runtime.

There are also important things to retain:

- Working banks and repaired inference banks are intentionally separate. Downstream transformations must consume the former to preserve numerical order. [staged-training.md:177](/home/ralph/suite/t-boost/specs/roadmap/staged-training.md:177).
- The canonical plan, partition cache, bounded session cache and exact-outcome reuse already exist.
- Distinct candidates still require distinct predictions unless exact inference identity proves equivalence.
- Haute’s OOF pooling and source-row checks are essential; replacing them with t-boost OOB scores or averages of fold charts would change the meaning of the numbers. [_staged_runtime.py:690](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:690).

For the target boundary, I would keep three seams: **model acquisition, scoring/explanation, and persistence**.

t-boost should expose complete typed results and opaque candidates. A concrete proposed shape is:

```python
StageName = Literal["full", "pruned", "banded", "smoothed"]

@dataclass(frozen=True)
class Recipe:
    pruning_path: PruningPathSettings | None
    operations: tuple[Prune | Band | Smooth, ...]

@dataclass(frozen=True)
class StageInfo:
    stage: StageName
    revision: str
    parent_revision: str | None
    data_digest: str
    evidence_digest: str
    outcome_id: str

@dataclass(frozen=True)
class StageResult:
    model: TBoostRegressor | TBoostClassifier  # independent, caller-owned
    info: StageInfo
    recipe: Recipe
    fit_summary: FitSummary

class TBoostTrainingSession:
    def stage(self, name: StageName) -> StageResult: ...
    def apply_recipe(self, recipe: Recipe) -> StageResult: ...
    def prepare_path(self, request: PathRequest) -> TBoostPathFamily: ...
    def apply(self, candidate: TBoostCandidate) -> StageResult: ...

class TBoostPathFamily:
    def candidate(self, point_id: str) -> TBoostCandidate: ...

class TBoostCandidate:
    info: CandidateInfo
    def result(self) -> StageResult: ...  # inspection without publication
    def to_bytes(self) -> bytes: ...
```

These are proposed names, not existing APIs.

The important distinctions are:

- **Evaluating a candidate does not require applying it to a fork.** `candidate.result()` supplies its exact inference model, provenance and replay policy without changing a session.
- **A recipe is complete.** It includes the pruning-path configuration needed to reproduce selection. Haute should not reconstruct this from a separate `applied_path` plus historical snapshot fallbacks.
- **A candidate is bound to a fit; a recipe is portable across fits.** Candidates retain evidence and parent identities. Recipes contain policies, never support IDs, fitted alpha vectors or another fold’s bank.
- **Application installs the exact prepared snapshot.** It should not reconstruct an estimator merely to serialize it back into the stage record.
- Metadata is immutable or defensively copied; the estimator is independently owned. A frozen dataclass alone does not make a mutable estimator safe.

For scoring, add one opaque public type:

```python
prepared = model.prepare_input(feature_frame)  # PreparedInput
raw = model.predict_raw(prepared)
tables = model.table_export(prepared)          # TableExport, typed Python data
```

Binary classifiers should expose the same raw-margin operation through this surface. Existing frame-taking methods should use the same implementation.

`PreparedInput` should own validated numeric arrays and categorical codes/labels. It must carry a schema/preprocessing identity. Reuse is allowed only across compatible models.

Two restrictions matter:

1. Do not cache candidate-specific table-cell indices across banding: grids change.
2. Do not assume unknown-category routing is independent of the fitted model. Either retain unrouted canonical categories or validate an explicit routing identity. Current routing consults model-specific known and rare levels. [_sklearn.py:3277](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:3277).

Haute should retain its sorted categorical contract and class/offset semantics. Those are not interchangeable with t-boost’s first-appearance checkpoint coding.

Inside Haute, introduce a small typed acquisition seam before materialisation:

```python
ModelSource.acquire(fit_context) -> AcquiredModel
```

An ordinary source fits. A retained source supplies a `StageResult` wrapped as Haute’s `TBoostModel`, with its existing feature contract and original fit evidence.

Both then enter **the same canonical evaluation code**. A reusable `EvaluationContext` holds the verified plan, row identities, partition inputs, class encoding and diagnostic inputs. Constructing a new `TrainingJob` per point is not inherently a problem; repeating its physical preparation is.

Haute continues to own:

- Validation/test separation, fold orchestration and pooled OOF order.
- Metrics, AvE, weights/exposure semantics and diagnostic failure behaviour.
- Cross-fold budget floors, recommendation tolerance and tie-breaking.
- Enabled phases, pending settings, selected/applied distinction and finalisation.
- Content-addressed storage, leases, cancellation and compare-and-swap publication.

Across the library boundary, pass typed settings, candidates, stage results, prepared inputs, arrays and table data. Use bytes or binary streams for persistence. Keep HTTP/storage JSON where appropriate.

I would **retain the current single native table JSON parse initially**. Replacing it with direct float32-to-Python conversion is not automatically behaviour-preserving: the existing contract deliberately exposes the numbers obtained from JSON decimal rendering. A previous direct-conversion attempt was rejected for precisely this reason. [performance-plan:628](/home/ralph/suite/haute/modelling-brainstorm/performance-plan-2026-11-22.md:628).

The migration can be small and sequential:

| Step | Change | Required verification | Removes measurable cost? |
|---|---|---|---|
| 1 | Capture the current contract and update both specs to describe the intended acquisition seam. | Goldens for native banks, prediction bytes, recipes, stage revisions, path metadata, metrics, AvE and stored point members. Cover regression/binary, weights, offsets, groups, categories, monotonicity, skipped stages and identity/rejected candidates. | No. Establishes the oracle. |
| 2 | Add typed recipe/settings/result objects in t-boost around the existing algorithms. Make recipe validation authoritative there; retain Haute’s product restrictions. | Existing defaults and accepted/rejected settings remain equal. Replay on independent folds and final fits reproduces today’s banks and reports. Pending controls cannot alter the applied recipe. | Primarily clarity. |
| 3 | Preserve and use the model already returned by candidate application. Return a typed acquisition result instead of `prepared_stage`/`stage_operation` kwargs. | Count restores: remove the adapter’s extra `session.model` call. Compare complete estimator metadata, raw/response prediction bytes, stage records and exported artifacts. Assert no boosting during resume. | **Yes:** one redundant restore per candidate-fit acquisition. |
| 4 | Add an in-memory candidate/result path; make application install immutable prepared stage content. Retain serialization for persistence and bounded spilling. | Live versus serialized/reloaded candidates have equal banks, predictions, recipes and revisions. Wrong evidence, changed parent and changed pruning settings still fail. Mutation of one returned model cannot affect another. | **Yes:** avoid candidate ZIP decode, repeated validation/reconstruction and publication reserialization while resident. |
| 5 | Move retained acquisition ahead of training-frame preparation in Haute. Retain the per-fit feature contract once. Make table reports demand-driven. | Compare ordinary and supplied-model evaluation on the same canonical context. Preserve fit count/history, metrics, OOF rows, AvE, reference tables and final-test exclusion. Assert no training-label conversion or categorical-level scan on retained acquisition; no unused selection-fold table export. | **Yes:** removes scans, casts, unused estimator construction and unused tables. |
| 6 | Add `PreparedInput`, first delegating to current conversion and native scoring kernels. Reuse it through Haute’s evaluation context under a bounded memory budget. | Frame versus prepared-input prediction bytes and table exports match exactly. Test reordered/extra/missing columns, null/unseen/rare/sentinel categories, dtype variants, incompatible folds, input mutation and cache eviction. Preserve row order and current offset arithmetic. | **Yes:** removes repeated serving preparation. Measure conversions, allocations, peak RSS and wall time. |
| 7 | Remove duplicated Haute mathematical recipe interpretation and obsolete adapter kwargs. Keep durable formats explicit; reject superseded formats if changed. | Full → paths → inspect → Apply → restart → next stage → finalise goldens. Stale Apply, cancellation and failed publication leave the applied revision unchanged. Current storage closure and point-result equality tests remain valid. | Some bookkeeping savings; mainly completes the ownership change. |

Candidate/session/input caches must share an explicit memory budget. Keeping every fold’s complete family indefinitely would exchange CPU savings for unacceptable memory growth. Start with bounded reuse; change traversal order only if measurements justify it, still using the same canonical evaluator.

For every numerically relevant step, use t-boost’s actual reproducibility standard: **bit identity across thread counts, processes and repeated seeded runs**, not `allclose`. Compare native model bytes separately from estimator envelopes, since envelopes include parameters such as thread settings. Preserve working banks as well as inference banks, because an apparently equal current predictor can still produce a different downstream stage. [CONTRIBUTING.md:150](/home/ralph/suite/t-boost/CONTRIBUTING.md:150).

Retain the two-process `xtask bit-repro` gate and extend Python stage coverage across thread counts. Existing family tests provide a useful independent oracle by comparing against the single native keep-set binding rather than another wrapper around the family implementation. [CONTRIBUTING.md:78](/home/ralph/suite/t-boost/CONTRIBUTING.md:78), [test_staged_postfit.py:430](/home/ralph/suite/t-boost/python/tests/test_staged_postfit.py:430).

I found two concrete defects/wastes worth separating from the redesign:

1. **Candidate serialization is nondeterministic.**  
   `candidate_bytes()` uses `ZipFile.writestr` with filename strings, so member timestamps come from the current clock. Checkpoint writing explicitly fixes timestamps. In the in-memory reproduction, changing the clock by four seconds produced different candidate archive bytes with identical member names and contents. This undermines reproducible candidate bytes and content-addressed deduplication. [_paths.py:120](/home/ralph/suite/t-boost/python/t_boost/_paths.py:120), [_checkpoint.py:187](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:187).

   Fix it separately with fixed ZIP metadata. Verify byte equality across different clocks/processes, unchanged member bodies, unchanged stage revisions and unchanged predictions. This deliberately changes archive bytes; it must not be hidden inside a model-arithmetic golden update.

2. **Haute materialises validation data for t-boost’s fit and immediately discards it.**  
   `_train_model` loads `eval_df` for every non-GLM frame-based algorithm; `TBoostAlgorithm.fit` explicitly deletes it because t-boost takes no validation set. This affects ordinary fitting as well as resumed paths. It is wasted I/O and memory, not evidence that validation rows enter native fitting. [_training_job.py:2625](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:2625), [_tboost.py:641](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:641).

   Express fit-time validation demand explicitly and verify that canonical validation scoring still occurs with identical results.

There is also specification drift: the t-boost roadmap describes coded native fit/post-fit inputs as delivered, while checkpoint loading currently reconstructs per-row strings; Haute’s performance notes explicitly defer that work. Reconcile the documents before treating it as a migration prerequisite. [_checkpoint.py:513](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:513), [staged-training.md:121](/home/ralph/suite/t-boost/specs/roadmap/staged-training.md:121).

I did not reproduce a wrong numerical result. The strongest immediate change is to make retained-model acquisition explicit, then eliminate serialization and preparation around it while leaving the canonical evaluator and native arithmetic intact.