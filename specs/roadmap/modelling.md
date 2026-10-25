# Modelling roadmap

## Scope

What happens when a training fit does not fit in memory. Current behaviour is
specified in [the modelling specification](../modelling/high-level.md) and
its [low-level specification](../modelling/low-level.md), with the worker and
memory machinery in
[the execution-engine specification](../execution-engine/low-level.md). The
six trained families (CatBoost, GLM through RustyStats, XGBoost, LightGBM,
EBM and t-boost) are in scope only for what each backend exposes to a caller
that must bound memory; their losses, tuning and evaluation are out of scope.
The GLM distributions that `MOD-M01` names in its acceptance are the subject
of that package, not a tuning choice. `MOD-R01` to `MOD-R06` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md) and are the
exception to that scope: they cover what the review found in the fits, the
contracts and the training lifecycle.

Today a training fit that exceeds its memory budget terminates: bounded
streaming mode is refused outright (`BoundedMemoryUnsupportedError`, shown as
"Training cannot run in bounded streaming mode"), and a budget overrun
surfaces `memory_limited` with reduce-your-data guidance. `MOD-M01` and
`MOD-M02` add a degrade-gracefully path. Both are `Decision` packages: each needs an explicit
engagement and product choice before implementation, and the design decisions
belong to that implementation cycle, not to this page. Where this page names
a tolerance, a bound or a cap, the decision sets its value and the modelling
low-level specification records it before implementation starts.

Two constraints are shared, and they are about what can be trusted as a
signal rather than about design.

**Allocation failure is not a reliable adaptation signal.** Depending on the
overcommit setting, cgroup limits, the allocator and the native binding, an
allocation that cannot be honoured may return an error, surface as a
translated exception, or succeed and kill the worker later when the pages
are touched. Backend state after any of those is not known to be reusable.
The signals a fitter can design around are the up-front estimate
(`src/haute/_ram_estimate.py`) and the measured-RSS checkpoint
(`ExecutionContext.checkpoint` raising `ExecutionMemoryLimitExceededError`
at a controlled boundary). A fitter can act on the checkpoint only at a
chunk boundary it owns, where its own state is still coherent; work that
runs inside a backend between such boundaries can be sized from the estimate
but not adapted mid-fit.

**Worker death is not a resumption point.** A dead worker cannot resume a
partial fit, and a non-zero exit or `SIGKILL` does not reliably identify a
memory failure. Because the training parquet is already on disk before
fitting, a restart from it is possible; whether an explicitly identified
memory failure permits a bounded restart, or every such death terminates the
job as it does today, is an input to the engagement decision below, not
something this page settles.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-M01 | Decision | P3 | Exact chunked GLM fitting under a memory budget. |
| MOD-M02 | Decision | P3 | A low-memory boosted-tree fitting mode under an explicit user choice. |
| MOD-R01 | Planned | P1 | No family refuses a categorical value its fit never saw: each library scores it through its own mechanism, Haute assigns it no level, and every scoring path counts and reports such values by feature. |
| MOD-R02 | Planned | P2 | A dispersion estimate registers its cancellation token before preparing, prepares off the request thread, and reports curated errors. |
| MOD-R03 | Planned | P2 | Training publication validates the evaluation plan outside the job-store lock, and the plan stores memberships compactly. |
| MOD-R04 | Planned | P2 | Fit parameters have no hidden defaults: an unset Tweedie power is refused by the job itself, CatBoost reserves its loss keys, a missing feature or dtype is an error, and `tweedie_deviance` needs its power. |
| MOD-R05 | Planned | P3 | Diagnostics are measured as the fit was: weighted loss importance, one-based GPU iterations, no GLM `best_iteration`, a dispersion search that stops on `MemoryError`, the offset dtype in the contract, params logged whole. |
| MOD-R06 | Planned | P3 | The training memory log is opt-in, the dead compatibility fields are gone, and the four native wrappers share one base. |

## Planned improvements

`MOD-M02`'s implementation reuses the chunk admission, batch reading and
result-metadata surfaces that `MOD-M01` builds, so its implementation follows
`MOD-M01`. Its first decision input, a measurement of XGBoost's
external-memory mode, does not need any of that and can start first.

### MOD-M01 — Adaptive GLM fitting (exact, chunked)
**Why:** GLM fitting by iteratively reweighted least squares (IRLS) is
chunkable in its data term. Each iteration's normal-equation terms, X′WX and
X′Wz, are sums over rows, so accumulating them chunk by chunk from the
training parquet evaluates the same estimating equations as the in-memory
fit without subsampling any row. What is bounded is the row-dependent memory;
the p×p accumulator and the solver workspace are fixed costs that must be
admitted against the budget separately, and a wide one-hot design can exceed
the budget at any chunk size. With the same preprocessing, encoding, starting
values, regularisation, rank handling, solver and stopping rules, the result
is numerically equivalent to the in-memory fit, not bitwise identical:
cross-chunk accumulation sums in a different order, and an ill-conditioned
design is where that difference shows.

The cost is measured in passes over the rows. One pass evaluates the deviance
at a candidate coefficient vector and, if that candidate is accepted,
accumulates the next normal equations in the same pass; so an iteration whose
first candidate is accepted costs one pass, and each rejected candidate under
step-halving costs one more. A fitter that does not fuse evaluation and
accumulation pays twice per iteration. Binomial or Poisson non-existence
(separation) and a poor start can make rejected candidates frequent, and
step-halving does not cure separation, so a finite pass budget exists only
once the iteration and halving caps are set. Under those caps this turns a
memory-limited GLM fit from a terminal failure into a slower exact fit.

**Plan:** Decision first: whether chunked fitting engages automatically from
the up-front estimate, adaptively on a checkpoint signal at a chunk
boundary, or only as an explicit user mode; how it composes with the
RustyStats backend (`GLMAlgorithm`), which currently receives a materialised
frame; and the iteration and halving caps that make the pass budget finite,
since that budget sets the I/O cost the mode is chosen against. One further
input: at a fixed Tweedie variance power the estimating equations stay
chunk-accumulable, but estimating the power is an outer search that repeats
passes and possibly whole fits, so the decision states whether the chunked
path estimates the power or takes a user-supplied one. Then implement
chunk-accumulated IRLS over streaming reads of the sunk training parquet,
reusing the existing chunk-sizing primitives (`src/haute/chunking.py`,
`src/haute/_ram_estimate.py`). The convergence statistic and the deviance
come from the candidate pass that accepted the final coefficients; the
dispersion estimate and the evaluation plan run over one further pass at
those coefficients unless that pass can be fused too. The refusal branch for
bounded streaming mode is retired for GLM only when the chunked path covers
it.

**Acceptance:** Chunked and in-memory fits agree on coefficients within the
relative tolerance the decision fixes, which is no looser than the in-memory
solver's own convergence tolerance, on representative
gaussian/poisson/gamma/tweedie/binomial jobs with weights and offset. The
representative set includes an ill-conditioned design, because summation
order is where the two paths diverge, and a job on which step-halving
rejects at least one candidate. The dispersion estimates agree to the same
tolerance: coefficients are invariant to the dispersion, so a
coefficients-only comparison would pass over a wrong dispersion while every
standard error and interval derived from it was skewed. Peak RSS stays within
the execution budget on a fit the in-memory path exceeds it on, pinned with
the existing memory-metrics machinery. A fit whose fixed p×p state or
minimum chunk still exceeds the budget terminates `memory_limited` with the
curated message. The chosen engagement mode and caps are specified in the
modelling low-level specification and visible in the training result
metadata.

**Dependencies:** The recoverable-checkpoint contract (execution-engine); the
existing chunk planning and admission budgets.

**Evidence:** `src/haute/modelling/_rustystats.py`;
`src/haute/modelling/_training_job.py`; `src/haute/_ram_estimate.py`;
`src/haute/_execution_context.py`; `src/haute/chunking.py`; the bounded
streaming refusal in `src/haute/routes/_training_preparation.py` and its
message in `src/haute/routes/_training_worker.py`.

### MOD-M02 — Low-memory boosted-tree mode
**Why:** An exact chunked boosted fit is not ruled out by the mathematics.
Histogram-based gradient and hessian accumulation is a sum over rows, so with
the bin cuts fixed up front by the same quantile sketch the in-memory path
uses, and deterministic settings, chunked accumulation targets the same split
statistics; external-memory and data-parallel boosting modes are built on
that fact. It does not promise the same tree: floating-point reduction
order, sampling and tie-breaking can change a split, so equivalence for a
tree model is defined on predictions and objective history under stated
tolerances, with tree-structure equality only where a backend promises it.

What constrains the exact path here is the interface each shipped backend
exposes. CatBoost, LightGBM and EBM do not expose histogram accumulation as
something a caller can drive chunk by chunk, so an exact chunked path
through them means writing tree construction ourselves. The chunked
approaches they do reach, sequential continuation over chunks and bagged
subsample ensembles, are different models with order effects and different
variance behaviour, not the full-data fit computed differently. XGBoost 3.x
provides iterator-backed and external-memory data structures; supplying
batches through an iterator does not by itself bound resident memory or
reproduce the in-memory fit, so the decision must name the exact API, cache
configuration, tree method and device, and measure peak memory and
agreement before counting it as the exact path. An approximate fit is still
useful for analysis and proximal simulation when the data does not fit the
budget, provided its quality against the full-data fit is bounded rather
than merely recorded.

**Plan:** Decision first, and it is a product choice as much as an
architectural one. The first call, per family, is whether an exact chunked
path is reachable: for XGBoost by the measurement above, for the others only
by building tree construction. Where it is, that reshapes this package
rather than delivering it. The approximate variants are chunk-sequential
boosting continuation, a bagged partition ensemble, and guided downsampling
beyond the existing automatic downsample. Reduced-capacity fitting on the
full data (fewer trees, shallower depth, coarser bins) is not a low-memory
mode, because the resident frame and the per-row gradient buffers do not
shrink with model capacity; it belongs here only as a quality lever for a
job whose data fits but whose training overhead does not.

Engagement is split in two. Approximate fitting is enabled only by explicit
configuration, and the result, model card and MLflow metadata are stamped as
approximate; the up-front estimate and the checkpoint never turn an exact
request into an approximate model. Once an approximate mode is enabled, the
estimate sizes its chunks or partitions and the checkpoint may terminate it
at a boundary Haute owns (between chunks of a continuation, between members
of an ensemble); work inside a backend fit has no such boundary and is sized
from the estimate alone. An exact external-memory path, if one is adopted,
takes `MOD-M01`'s engagement decision rather than this one.

**Acceptance:** The mode is opt-in configuration, and a run without it never
produces an approximate model. The approximation is stamped in the training
result, model card and MLflow metadata. For each family the variant
supports, representative regression and classification fixtures meet a
non-inferiority bound the decision fixes on the family's primary holdout
metric against the full-data fit, measured on the same rows and features,
and a directional-agreement criterion where proximal simulation depends on
it; a variant that misses the bound is not shipped. The measurement is
recorded as a performance artifact rather than run in the ordinary test
lanes, so the bound is a release criterion, not a per-commit gate.
Memory-bound tests prove bounded peak RSS. Should the decision adopt an exact
path for a family, the approximation stamp falls away for that family and a
family-specific equivalence criterion replaces the non-inferiority bound:
fixed seeds and deterministic settings, identical preprocessing and cuts,
prediction and objective-history agreement under stated absolute and
relative tolerances, and tree-structure equality only if the backend promises
it.

**Dependencies:** `MOD-M01`'s chunk admission, batch reading and
result-metadata surfaces, for the implementation; nothing, for the XGBoost
external-memory measurement. The model-card and result metadata surface.

**Evidence:** `src/haute/modelling/_algorithms.py`;
`src/haute/modelling/_xgboost.py`; `src/haute/modelling/_lightgbm.py`;
`src/haute/modelling/_ebm.py`; `src/haute/modelling/_training_job.py`;
`src/haute/modelling/_model_card.py`; `src/haute/_ram_estimate.py` (the
existing downsample decision).

### MOD-R01 — Unseen categorical values score natively and are reported
**Decision (the owner, 25 October 2026):** a categorical value the fit never saw
is never refused. Each library scores it through its own mechanism, Haute maps it
to no level of its own, and every such value is reported. This replaces the
refusal and the null policy that the review's validation recorded for this
package.

**Why:** The families disagree today. LightGBM, XGBoost and EBM go through
`encode_frame`, whose `require_known_levels` raises "values the model was not
trained on" for the whole column, so one new value fails the scoring call for
every row in it, and a deployed bundle's `_assert_runtime_contract_matches`
refuses it again against the contract's levels, which for these families are
the fitted ones. None of the three libraries refuses it on its own. t-boost
scores it in its pooled `<rare>` cell (`UNKNOWN_CATEGORY`) and logs validation
rows that hold one. A GLM categorical term and CatBoost score it silently:
RustyStats encodes an unknown label as all-zero dummies, the reference level
(probed: D, Z and a null all priced at A; the user guide says unseen levels
share the intercept), and CatBoost gives it its zero-count prior. Only t-boost
can tell that a value is unseen: the GLM contract records no fitted levels
(`_load_rustystats_model` sets `cat_feature_names` empty, and the contract's
dtype-derived `categorical_features` leaves out a `categorical` term on an
`Int64` column), and CatBoost's contract keeps only a declared domain. New
levels are routine in production scoring. A refusal turns one into an outage,
and silence hides an upstream recoding that prices a whole segment at the
fallback.

Where each library scores an unseen value (probe, 25 October 2026: three levels
of 3,000 rows and ten rare levels of 4 rows, whose true mean is 6):

| Family | Native mechanism | An unseen value scores |
|---|---|---|
| t-boost | `unknown_category="rare"`, which Haute sets | in the pooled `<rare>` cell, as the rare levels do (5.98) |
| LightGBM | its categorical split settings, `cat_smooth` and `min_data_per_group` | as the rare levels do (3.20) at the defaults, passed as a missing value as Haute's encoding passes it; with both settings lowered, rare levels split alone and the match ends |
| CatBoost | `ctr_leaf_count_limit` (CPU only) keeps statistics for the most frequent categories only | at the zero-count prior by default (6.07, the rare levels 4.43); with the limit set, rare and unseen values share one value near the book average (2.15) |
| GLM, categorical term | **Levels** fits indicators for the listed levels only | in the intercept, with the reference level and every unlisted level |
| GLM, target-encoding term | the encoding's prior | at the prior, the training mean that a level's encoding shrinks to as its rows fall (RustyStats documents it; not probed) |
| XGBoost | none | down the missing or not-chosen branch of each split |
| EBM | none: `min_cat_samples` groups thin levels while boosting, but each keeps its own score | in the unknown bin, whose score 0 is the book average (2.02) |

**Plan:**

- `encode_frame` stops raising. A value outside the fitted levels reaches the
  library as an unseen category, so the library's own handling scores it, kept
  apart from its missing-value handling wherever the library distinguishes the
  two (EBM's unknown bin is not its missing bin).
- Every family records the levels it was fitted on, so an unseen value can be
  detected: the GLM contract stores each categorical term's labels by term
  type, an `Int64` categorical term included, and CatBoost records its training
  levels. The contract keeps these fitted levels apart from a domain the
  analyst declares upstream: today one `categorical_levels` field holds
  whichever applies (`fit_categorical_levels` lets a declared domain win), and
  a deployed bundle checks values against it. Only the declared domain is
  enforced, on every path, and a value outside it is a declared-domain error,
  not an unseen value. The fitted levels drive detection and the report only.
- Every scoring path (preview, standalone run, Model Scoring, a deployed
  bundle) counts, per categorical feature, the distinct unseen values, their
  rows, their exposure when the model has an offset, and examples, and logs one
  structured warning per feature. Model Scoring and the preview show the
  counts, and a quote's trace flags the feature. A training run reports the same
  for its validation and final-test rows in the training result, for every
  family. A null in a feature whose training rows held none is counted beside
  them; a null is never an unseen value and keeps its native missing treatment.
- The model-training user guide states, per family, where an unseen value
  scores and which native setting controls it (the table above).
- At delivery the modelling specifications stop saying that an unseen category
  fails: the feature contract's "any drift raises" keeps raising for missing
  features and dtype drift, but not for an unseen categorical value.

**Acceptance:** For each of the six families, a model trained on the probe's
data scores an unseen value without raising, in the preview, a standalone run
and a deployed bundle after saving and loading, exactly as the library scores it
directly: t-boost and LightGBM at their rare levels' value, EBM in its unknown
bin and not its missing bin. A training run whose validation rows hold a level
the training rows lack completes in every family and reports it. The report
counts the probe's unseen values and rows exactly on every path; a null in a
feature whose training rows held none is counted separately; a value outside a
declared domain is still refused. A GLM with string and `Int64` categorical
terms records both terms' levels, and its A, B and C prices are unchanged. The
tests that pin a refusal are replaced by tests of native scoring.

**Dependencies:** `DEP-R07` binds the run's contract on every deployed path, so
an MLflow-sourced model reports unseen values once it lands. `TBOOST-09` keeps
t-boost's out-of-support and stability reporting; the unseen-value report is
this package's, for every family.

**Evidence:** `src/haute/modelling/_native_encoding.py::encode_frame`;
`src/haute/modelling/_native_encoding.py::require_known_levels`;
`src/haute/modelling/_native_encoding.py::fit_categorical_levels`;
`src/haute/deploy/_scorer.py::_assert_runtime_contract_matches`;
`src/haute/modelling/_tboost.py::UNKNOWN_CATEGORY`;
`src/haute/modelling/_tboost.py::_log_unseen_validation_values`;
`src/haute/modelling/_rustystats.py::GLMAlgorithm`;
`src/haute/modelling/_rustystats.py::prepare_glm_design`;
`src/haute/modelling/_training_job.py::_save_artifacts`;
`src/haute/_mlflow_io.py::_load_rustystats_model`;
`src/haute/_model_scorer.py::_validate_features_uncached`;
`src/haute/modelling/_feature_contract.py::validate_categorical_value_domains`;
`src/haute/_builders.py::_ratebook_lookup_table` (the optimiser's rating tables
already price an unseen level neutrally and count every miss);
`tests/test_lightgbm_family.py::test_reordered_categories_score_identically_and_unseen_ones_fail`;
`tests/test_xgboost_family.py::test_reordered_categories_score_identically_and_unseen_ones_fail`;
`tests/test_ebm_family.py::test_unseen_categories_fail_and_nulls_score_through_the_missing_bin`;
`docs/building-models/nodes/model-training.md`.

### MOD-R02 — The dispersion estimate's lifecycle
**Why:** `start_dispersion_estimate` creates its admitted context without a
cancellation token and registers the job's token only after
`_execute_and_sink` returns, so cancelling during preparation finds no token
(`cancel` returns `False`), the preparation child keeps running the upstream
pipeline under the job's admitted budget, and the job is nevertheless
transitioned to `cancelled`, freeing the single running slot while the work
continues; training registers its token before preparation. The RAM estimate,
the upstream materialisation and the worker launch all run inside the HTTP
request, which returns the job id only afterwards, so on a large pipeline the
request blocks for minutes and the client has no id to poll or cancel;
training moved exactly this work to an owned preparation thread. A start
failure outside the HTTP and memory classes records `str(exc)` verbatim where
training curates it.

**Plan:** Register the token under the start lock as `start()` does and pass
it into the admitted context before preparing; run the dispersion preparation
through the same owned thread as training, parametrised on the launch
function, returning the job id at once; route failures through
`_friendly_error` and the HTTP-failure parts.

**Acceptance:** Cancelling during preparation stops the preparation worker and
releases admission before the job reads `cancelled`; the start route answers
within the admission wait on a large pipeline; a start failure carries a
curated message.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_training_lifecycle.py::start_dispersion_estimate`;
`src/haute/routes/_training_lifecycle.py::cancel_dispersion`;
`src/haute/routes/_training_lifecycle.py::_launch_dispersion_protocol`;
`src/haute/routes/_training_lifecycle.py::_prepare_and_launch_training`;
`src/haute/routes/_background_jobs.py::CancellableJobRegistry`;
`src/haute/routes/modelling.py::estimate_dispersion`.

### MOD-R03 — Publication validates outside the lock
**Why:** The evaluation plan stores every source position of the development
set and of every validation fit's train and validation sets as JSON integers,
and the parent re-parses and re-validates that file, with the results and the
report, inside the job-store critical section at publication; at 2,000,000
rows and five folds the plan is 71 MB and the reload takes 13.3 s with a
0.65 GB peak, during which every status poll stalls.

**Plan:** Store memberships compactly (a per-row partition label array, or
sorted position ranges) and validate the plan outside the lock, entering it
only to publish the already-validated fields.

**Acceptance:** A 2M-row, five-fold plan is under 10 MB; publication holds the
lock for no file read (a test with a slow reader); status polls answer during
publication.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_evaluation.py`;
`src/haute/routes/_training_artifacts.py::_validate_evaluation_artifact_contents`;
`src/haute/routes/_job_store.py::compare_and_publish_completion`;
`src/haute/routes/_training_lifecycle.py`.

### MOD-R04 — Fit parameters without hidden defaults
**Why:** `TrainingJob` does not gate the Tweedie variance power: a direct
`TrainingJob(loss_function="Tweedie")` without `variance_power` trains at 1.5
for CatBoost (`resolve_loss_function` fills it in), XGBoost and LightGBM (a
`None` parameter is dropped; probed), the library default the objective gate
exists to refuse; only the config builder and the route gate it. CatBoost's
`reserved_params` names only `thread_count` and `class_names`, so a
`loss_function`, `objective`, `eval_metric` or `random_seed` typed into the
Parameters JSON is accepted and silently overwritten by the node's loss, while
every other family refuses `objective`; its early-stopping block pops a key the
guarding `if` has shown absent. The contract writer guesses `Float64` for a
feature missing from the dtype snapshot and for an empty target dtype, the
fallback the specification forbids. `_build_pool` silently drops any requested
feature absent from the frame and recomputes the categorical indices over the
survivors, so the one function every CatBoost fit and diagnostic goes through
can build a pool of the wrong width. `tweedie_deviance` requested on a model
without a variance power is computed at a hard-coded 1.5 without saying so.

**Plan:** Raise `HauteValidationError` in `TrainingJob.__init__` (or in each
adapter) when the loss is Tweedie and the power is `None`, and drop the 1.5
default; add the loss, objective, metric, seed and file-writing keys to
CatBoost's `reserved_params`; raise for a feature missing from the snapshot or
the frame; require a variance power whenever the metric is computed.

**Acceptance:** A `TrainingJob` without a Tweedie power is refused for every
family; a CatBoost `loss_function` in the Parameters JSON is refused by name;
`_build_pool` raises naming a missing feature; `tweedie_deviance` without a
power raises.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_algorithms.py::resolve_loss_function`;
`src/haute/modelling/_algorithms.py::_build_pool`;
`src/haute/modelling/_descriptors.py`;
`src/haute/modelling/_training_job.py::_feature_dtypes_for_contract`;
`src/haute/modelling/_metrics.py::_tweedie_deviance`.

### MOD-R05 — Diagnostics measured as the fit was
**Why:** The CatBoost `LossFunctionChange` importance is computed on a pool
built without the weight column, so for a weight- or exposure-trained model
the importance shown and logged is evaluated under an unweighted loss. A GPU
CatBoost fit's reconstructed `loss_history` numbers its rows from 0 while the
CPU callback and every other family number from 1, so the row kept as the best
iteration and the live readout are off by one on GPU. A GLM reports its IRLS
iteration count as `best_iteration`, persisted and logged where every other
family's value means the early-stopping round. The dispersion
profile-likelihood search absorbs `MemoryError` as a failed candidate, tries
the remaining candidates and ends `contract_error` instead of
`memory_limited`. The job captures the offset column's dtype into
`_contract_offset_dtype` but nothing reads it, and the MLflow signature
declares every offset `Float64`. Candidate-run params are truncated at 500
characters as Python reprs although MLflow allows 6,000, so a GLM's
`param_terms` reaches MLflow cut off.

**Plan:** Pass `weight=self.weight` to the diagnostic pool; number the GPU
history from one; return `best_iteration=None` for the GLM; re-raise
`MemoryError` in the dispersion search; record the offset dtype in the
contract and sign it from there; serialise structured params as compact JSON
truncated at MLflow's limit, logging an over-long value as an artifact.

**Acceptance:** Weighted and unweighted importances differ on a weighted
fixture; a GPU-style `evals_result_` gives the callback's numbering; a GLM
candidate logs no `best_iteration`; a dispersion `MemoryError` ends
`memory_limited`; an integer exposure is signed as such; a 2,000-character
`param_terms` round-trips from MLflow.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_training_job.py::_compute_metrics`;
`src/haute/modelling/_algorithms.py`;
`src/haute/modelling/_rustystats.py::estimate_glm_dispersion`;
`src/haute/modelling/_candidate_run.py`;
`src/haute/modelling/_mlflow_log.py::log_experiment`.

### MOD-R06 — Hygiene: the memory log, dead fields, one wrapper base
**Why:** Every fit writes a debug memory log unconditionally: each checkpoint
opens, appends to and `fsync`s `.haute_cache/training_mem.log` resolved
against the current directory of whichever process runs the fit, and the
preparation child truncates it at every run, so a failed run's evidence is
overwritten and a scratch project gets a `.haute_cache` created beside its
data. Five "compatibility" fields on `ModelDiagnostics` and
`ModelCardMetadata` are set by no constructor in `src/`, so the model card's
fallbacks to them can never fire, and `DEFAULT_SPLIT_DICT` has no reference.
The inverse link is written in three adapters (and as a scalar in the
explainer), the wrapper surface (feature names, served-label `predict`,
`predict_proba`, the metadata record) in all four native wrappers, and the
`ModelDiagnostics` bundle and final-parameter projection in both the scripted
and the canvas log paths.

**Plan:** Make the log opt-in (`HAUTE_MEM_LOG` set) and otherwise a no-op
without the per-line `fsync`; delete the five fields, their fallbacks and
`DEFAULT_SPLIT_DICT`; a shared base wrapper in `_algorithm_base.py` and one
`ModelDiagnostics.from_result(...)`.

**Acceptance:** A fit without `HAUTE_MEM_LOG` writes no file; the fields are
gone; jscpd reports no clone among the four wrappers.

**Dependencies:** `ENGQ-R01` owns dead code outside this component.

**Evidence:** `src/haute/modelling/_algorithms.py::_mem_checkpoint`;
`src/haute/modelling/_result_types.py::ModelDiagnostics`;
`src/haute/modelling/_result_types.py::ModelCardMetadata`;
`src/haute/modelling/_split.py::DEFAULT_SPLIT_DICT`;
`src/haute/modelling/_algorithm_base.py`; `src/haute/modelling/_xgboost.py`;
`src/haute/modelling/_lightgbm.py`; `src/haute/modelling/_ebm.py`;
`src/haute/modelling/_tboost.py`.
