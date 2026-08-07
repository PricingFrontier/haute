# Modelling roadmap

## Scope

Model training remains correct, memory-bounded, and honest about its failure
modes. Current behaviour is specified in
[modelling](../modelling/high-level.md) and the worker/memory machinery in
[execution-engine](../execution-engine/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-M01 | Decision | P3 | Exact chunked GLM fitting under a memory budget. |
| MOD-M02 | Decision | P3 | A low-memory GBM fitting mode under an explicit user choice. |

## Planned improvements

Today a training fit that exceeds its memory budget terminates: bounded
streaming mode is refused outright (`BoundedMemoryUnsupportedError` →
"Training cannot run in bounded streaming mode"), and a budget overrun
surfaces `memory_limited` with reduce-your-data guidance. These packages add a
degrade-gracefully path. A shared design constraint for both: the adaptation
signal is never a failed allocation. Under default Linux overcommit an
allocation failure does not surface at the request site (the OOM killer
delivers `SIGKILL` later), and a mid-fit `bad_alloc` inside a native library
aborts the process. The recoverable signals are the up-front estimate
(`_ram_estimate.py`) and the measured-RSS checkpoint
(`ExecutionContext.checkpoint` raising `ExecutionMemoryLimitExceededError` at
a controlled boundary) — an adaptive fitter catches the latter only at chunk
boundaries it designed for, where its own state is still coherent. Worker death
is not a signal either: an OOM-killed child takes its accumulated state with it,
so a parent observing a non-zero exit or `SIGKILL` has nothing coherent to adapt
from and terminates rather than retrying into lost work.

### MOD-M01 — Adaptive GLM fitting (exact, chunked)
**Why:** GLM/IRLS is exactly chunkable: each iteration's normal-equation terms
(X′WX, X′Wz) are sums over rows, so accumulating them over row chunks
reproduces the full-data fit per iteration at bounded peak memory. The cost is
one streaming pass over the training parquet per Newton step, plus a further
pass per step-halving attempt — evaluating the deviance at a candidate
coefficient vector needs a fitted value for every row — and separation or a
large initial step makes several halvings routine for exactly the binomial,
poisson and gamma fits in scope, so the budget is not one pass per iteration in
the general case. This turns a memory-limited GLM fit from a terminal failure
into a slower exact fit.

**Plan:** Decision first: whether chunked fitting engages automatically from
the up-front estimate, adaptively on a checkpoint memory signal at a chunk
boundary, or only as an explicit user mode — and how it composes with the
rustystats backend (`GLMAlgorithm`), which currently receives a materialised
frame. Two further inputs belong to that decision: the worst-case pass budget
once step-halving is counted, since it sets the I/O cost the mode is chosen
against; and whether the chunked path carries an estimated Tweedie variance
power or assumes a fixed user-supplied one, because estimating the power is an
outer optimisation over the whole fit rather than a row-sum and needs its own
pass structure. Then implement chunk-accumulated IRLS over streaming reads of
the sunk training parquet, reusing the existing chunk-sizing primitives
(`chunking.py`, `_ram_estimate.py`); the per-iteration convergence statistic,
dispersion estimation and the evaluation plan all run over that same chunked
pass rather than any separately materialised frame. The refusal branch for
bounded streaming mode is retired for GLM only when the chunked path covers it.

**Acceptance:** Chunked and in-memory fits agree within a stated relative
tolerance on representative gaussian/poisson/gamma/tweedie/binomial jobs,
including weights and offset, where the representative set names an
ill-conditioned design — cross-chunk accumulation sums in a different order from
the in-memory path, and collinearity is where that difference propagates — and a
job on which step-halving engages; the dispersion estimates agree to the same
tolerance, because the coefficients are invariant to the dispersion and a
coefficients-only comparison would pass over a wrong dispersion while every
standard error and interval derived from it was skewed; peak RSS stays within
the execution budget on a fit that
the in-memory path exceeds it on (pinned with the existing memory-metrics
machinery); a fit that still cannot run at minimum chunk size terminates
`memory_limited` with the curated message; the chosen engagement mode is
spec'd in modelling low-level and visible in the training result metadata.

**Dependencies:** The recoverable-checkpoint contract (execution-engine);
delivered chunk planning and admission budgets.

**Evidence:** `src/haute/modelling/_rustystats.py`;
`src/haute/modelling/_training_job.py`; `src/haute/_ram_estimate.py`;
`src/haute/_execution_context.py`; `src/haute/chunking.py`; the bounded
streaming refusal in `src/haute/routes/_train_service.py`.

### MOD-M02 — Low-memory approximate GBM mode
**Why:** An exact chunked boosted fit is not ruled out by the mathematics.
Histogram-based gradient and hessian accumulation is itself a sum over rows, so
with fixed bins a chunked accumulation yields the same split decisions, and the
same tree, as the in-memory histogram build — external-memory and data-parallel
modes in other boosting libraries work exactly this way. What rules it out here
is the interface: the current backend does not expose histogram accumulation as
something a caller can drive chunk by chunk, so an exact chunked path means a
different backend or building tree construction ourselves. The chunked
approaches that *are* reachable through the current backend — sequential
continuation over chunks (CatBoost `init_model`) and bagged subsample ensembles
— are *different models*, with order effects and different variance behaviour,
not the full-data fit computed differently. An approximate fit is still useful
for analysis and proximal simulation when the data does not fit the budget: a
directionally faithful model beats a terminal `memory_limited`.

**Plan:** Decision first, and it is a product choice as much as an
architectural one. The option space is wider than the approximate variants:
an exact chunked-histogram fit through a backend that exposes accumulation
would reshape this package rather than deliver it, and ruling that in or out is
the first call. The approximate variants are chunk-sequential boosting
continuation, a bagged partition ensemble, reduced-capacity same-class fitting
on the full data (fewer trees, shallower depth, coarser bins — the only variant
that keeps both the model family and every row), and plain guided downsampling
beyond the existing automatic downsample. Whichever is chosen, an approximate
mode is an explicit user choice surfaced in the config and stamped on the
result and model card as approximate, never a silent fallback from a memory
signal. Then implement the chosen variant on the chunked data path established
by MOD-M01, with the same estimate/checkpoint-driven engagement rules.

**Acceptance:** The mode is opt-in config with the approximation stamped in
the training result, model card, and MLflow metadata; predictive quality is
characterised against the full-data fit on a reference dataset (recorded as a
performance artifact, not a hard gate); memory-bound tests prove bounded peak
RSS; the mode never engages without explicit config. Should the decision land
on the exact chunked-histogram path instead, the approximation stamp falls away
and MOD-M01's agreement criterion — chunked and in-memory fits equal within
tolerance — replaces the characterisation artifact.

**Dependencies:** MOD-M01 (chunked data path and engagement rules); the
model-card/result metadata surface.

**Evidence:** `src/haute/modelling/_algorithms.py`;
`src/haute/modelling/_training_job.py`; `src/haute/modelling/_model_card.py`;
`src/haute/_ram_estimate.py` (the existing downsample decision).
