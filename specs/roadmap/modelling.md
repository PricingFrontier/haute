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
| MOD-M02 | Decision | P3 | An explicit low-memory approximate GBM fitting mode. |

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
boundaries it designed for, where its own state is still coherent.

### MOD-M01 — Adaptive GLM fitting (exact, chunked)
**Why:** GLM/IRLS is exactly chunkable: each iteration's normal-equation terms
(X′WX, X′Wz) are sums over rows, so accumulating them over row chunks
reproduces the full-data fit per iteration at bounded peak memory, at the cost
of one pass over the training parquet per IRLS iteration. This turns a
memory-limited GLM fit from a terminal failure into a slower exact fit.

**Plan:** Decision first: whether chunked fitting engages automatically from
the up-front estimate, adaptively on a checkpoint memory signal at a chunk
boundary, or only as an explicit user mode — and how it composes with the
rustystats backend (`GLMAlgorithm`), which currently receives a materialised
frame. Then implement chunk-accumulated IRLS over streaming reads of the sunk
training parquet, reusing the existing chunk-sizing primitives
(`chunking.py`, `_ram_estimate.py`); dispersion estimation and the evaluation
plan run over the same chunked pass. The refusal branch for bounded streaming
mode is retired for GLM only when the chunked path covers it.

**Acceptance:** Chunked and in-memory fits agree within numerical tolerance on
representative gaussian/poisson/gamma/tweedie/binomial jobs, including
weights and offset; peak RSS stays within the execution budget on a fit that
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
**Why:** Boosted trees have no exact chunked equivalent — sequential
continuation over chunks (CatBoost `init_model`) and bagged subsample
ensembles are *different models* with order effects and different variance
behaviour. But an approximate fit is still useful for analysis and proximal
simulation when the data does not fit the budget: a directionally faithful
model beats a terminal `memory_limited`.

**Plan:** Decision first, and it is a product choice: which variant
(chunk-sequential boosting continuation vs. bagged partition ensemble vs.
plain guided downsampling beyond the existing automatic downsample), and how
it is labelled — this mode must be an explicit user choice surfaced in the
config and stamped on the result/model card as approximate, never a silent
fallback from a memory signal. Then implement the chosen variant on the
chunked data path established by MOD-M01, with the same
estimate/checkpoint-driven engagement rules.

**Acceptance:** The mode is opt-in config with the approximation stamped in
the training result, model card, and MLflow metadata; predictive quality is
characterised against the full-data fit on a reference dataset (recorded as a
performance artifact, not a hard gate); memory-bound tests prove bounded peak
RSS; the mode never engages without explicit config.

**Dependencies:** MOD-M01 (chunked data path and engagement rules); the
model-card/result metadata surface.

**Evidence:** `src/haute/modelling/_algorithms.py`;
`src/haute/modelling/_training_job.py`; `src/haute/modelling/_model_card.py`;
`src/haute/_ram_estimate.py` (the existing downsample decision).
