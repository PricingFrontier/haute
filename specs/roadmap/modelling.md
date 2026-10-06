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
of that package, not a tuning choice.

Today a training fit that exceeds its memory budget terminates: bounded
streaming mode is refused outright (`BoundedMemoryUnsupportedError`, shown as
"Training cannot run in bounded streaming mode"), and a budget overrun
surfaces `memory_limited` with reduce-your-data guidance. These packages add a
degrade-gracefully path. Both are `Decision` packages: each needs an explicit
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
