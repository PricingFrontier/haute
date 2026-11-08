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
`MOD-R11` comes from the family memory measurement and covers the memory Haute's
own evaluation planning takes.

A GLM job whose up-front estimate would downsample trains on every row in chunks
(the modelling low-level specification, Chunked GLM fitting). Otherwise a training
fit that exceeds its memory budget terminates: bounded
streaming mode is refused outright (`BoundedMemoryUnsupportedError`, shown as
"Training cannot run in bounded streaming mode"), and a budget overrun
surfaces `memory_limited` with reduce-your-data guidance. `MOD-M02` adds a
degrade-gracefully path, decided on 2 November 2026: XGBoost's exact external memory,
engaging the way the GLM's chunked fit does.
The remaining design decisions belong to each implementation cycle, not to this page. Where this page names
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
fitting, a restart from it is possible; the 2 November 2026 decision below
keeps every such death terminal, as it is today: chunked fitting engages from
the up-front estimate, never as a restart.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-M02 | Planned | P3 | An XGBoost job too large for memory trains on every row through XGBoost's external memory, giving the same model. |
| MOD-R11 | Planned | P2 | Evaluation planning and the split hold their memberships as row labels, not as Python integers for every position. |

## Planned improvements

`MOD-M02` reuses the GLM chunked fit's engagement (`training_row_plan`), its job scratch
directory and its fit-mode evidence. `MOD-R11` bounds what either low-memory mode saves
end to end: until planning stops costing twice the frame, a job whose planning alone
exceeds the budget gains nothing from a low-memory fit.

### MOD-M02 — XGBoost trains through external memory when it will not fit
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

**Measurement (2 November 2026):** `tests/performance/test_training_family_memory_perf.py`
ran a canonical Haute job per family (30 numeric and 5 categorical features, a Poisson
frequency target with an exposure weight, a 20% holdout, 200 rounds of depth 6 or the
family's equivalent, 8 threads, a fresh process per fit, peak read from `VmHWM`). At
1.6M rows (a 436 MiB frame) the peaks were CatBoost 4,299 MiB, GLM 6,299, t-boost 3,050,
LightGBM 2,351, XGBoost 2,301 and EBM 1,959, against 1,355 MiB the training RAM estimate
predicts, so the estimate under-admits every family (`PL2-17` holds the per-family
figures). Before any fit, Haute's own evaluation planning and split raised the peak by
about 1.0 GB at 1.6M rows, about 2.2 times the frame, in every family.

XGBoost 3.2's external memory is an exact path. `ExtMemQuantileDMatrix` fed by a
`DataIter` that reads one 200,000-row parquet row group per batch (`cache_prefix` on
disk, `release_data=True`, `max_bin=256`, `enable_categorical=True`), trained with
`tree_method="hist"` on the CPU, gave bit-identical predictions and an identical
200-round objective history to the in-memory `QuantileDMatrix` at 1.6M and 6.4M rows.
At 6.4M rows (a 1,746 MiB frame) the peak was 1,017 MiB against 3,927 in memory; an
iterator-fed `QuantileDMatrix` alone reached 1,495, so most of the saving comes from
never holding the whole frame. Resident memory still grew by about 40 bytes per row, the
on-disk cache took about 83 bytes per row, `on_host=True` behaved as `on_host=False` on
the CPU, and the fit took 1.16 to 1.42 times as long. External memory therefore lowers
XGBoost's peak about fourfold without changing the model, but does not bound it.

**Decision (2 November 2026):** XGBoost's external memory is the low-memory path, and
the only one this package delivers: it is exact, so a model trained through it is the
model the in-memory fit would have produced. It takes the GLM chunked fit's engagement: an XGBoost
job whose up-front estimate says the in-memory fit will not fit trains on every row
through `ExtMemQuantileDMatrix` instead of being downsampled, the result, model card and
MLflow run record that it did, and a checkpoint overrun or a dead worker still ends
`memory_limited` with no restart. The estimate is used as it stands; its per-family
calibration stays with `PL2-17`, which will move the point where this engages. The
approximate variants (chunk-sequential continuation, bagged partition ensembles, guided
downsampling beyond the automatic downsample) for CatBoost, LightGBM, EBM and t-boost are
not pursued.

**Plan:** Build the batch reading this needs: a `DataIter` over the sunk training parquet's
row groups for the training partition (and the validation partition when early stopping runs), with the
same encoding, categorical levels, weights and offset margin the in-memory `matrix`
applies, and the cache under the job's artifact directory, removed with it. Settle in the
low-level specification the batch size, `max_bin` (the in-memory default), the cache
location and lifetime, and how the choice reaches the worker (the training request
records it; the fit never decides it on its own). Stamp the engagement in `TrainResponse`,
the model card and the candidate run. The stages outside the fit that still hold the
frame bound what the mode saves: evaluation planning and the split (`MOD-R11`) and the
diagnostics' partition read; measure the end-to-end peak with the family memory probe
before and after, and state in the specification what the mode does and does not bound.

**Acceptance:** On fixtures with a fixed seed, an external-memory fit and an in-memory fit
of the same job produce identical predictions and objective history (the measurement
found them bit-identical; a looser tolerance needs a stated reason), for regression and
classification, with weights and an offset. A job whose estimate does not fit trains on
every row through external memory and its result, model card and MLflow run say so; a
job that fits trains in memory as before. The end-to-end peak at a size the in-memory
path exceeds is recorded by the family memory probe as a performance artifact. The cache
is gone after success, failure and cancellation.

**Dependencies:** `MOD-R11` for the end-to-end saving (planning and the split hold the
frame-sized memberships today); `PL2-17` for where the estimate engages it. Nothing for
the fit itself.

**Evidence:** `src/haute/modelling/_xgboost.py`; `src/haute/modelling/_training_job.py`;
`src/haute/routes/_training_lifecycle.py`; `src/haute/modelling/_model_card.py`;
`src/haute/_ram_estimate.py` (the existing downsample decision);
`tests/performance/test_training_family_memory_perf.py`.

### MOD-R11 — Evaluation planning and the split hold memberships compactly
**Why:** Before any fit, Haute's own evaluation planning and split raised the peak by
about 1.0 GB at 1.6M rows, about 2.2 times the 436 MiB frame, in every family the family
memory probe measured. The in-memory `EvaluationPlan` holds the development and final-test
positions and every validation fit's train and validation positions as tuples of Python
integers, about 36 bytes per row per membership, and `generate_evaluation_plan` builds,
shuffles and filters Python lists of every position; the plan file is compact since
`MOD-R03`, but the plan in memory is not. A low-memory fit (the GLM's chunked fit,
`MOD-M02`) saves nothing a job can use while planning alone costs twice the frame.

**Plan:** First measure how much of the rise is the plan and how much the split's row
order and partition masks, from the probe's per-stage peaks. Then hold the plan in memory
as its per-row group labels (the schema-2 encoding) and each fit's groups, build
`final_mask`, `selection_mask` and `saved_selection_mask` from the labels with NumPy, give
row counts without materialising positions, and generate plans with NumPy arrays rather
than Python lists, keeping every plan the current code generates identical (the canonical
order, the seeded streams and the golden vector).

**Acceptance:** Every existing evaluation-plan test passes unchanged in what it asserts,
including reproducibility under row permutation and the golden fold vector; a plan
generated, saved and reloaded equals the original; at 1.6M rows with five folds,
planning and the split raise the probe's peak by under a quarter of what they do now,
recorded as a performance artifact.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_evaluation.py::EvaluationPlan`;
`src/haute/modelling/_evaluation.py::generate_evaluation_plan`;
`src/haute/modelling/_training_job.py::TrainingJob._build_evaluation_plan`;
`src/haute/modelling/_training_job.py::TrainingJob._split_data`;
`tests/performance/test_training_family_memory_perf.py`.
