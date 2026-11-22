# End-to-end workflow performance audit — 22 November 2026

> **Superseded in part.** The ranked opportunities below are the pre-review draft. The current
> plan is [performance-plan-2026-11-22.md](performance-plan-2026-11-22.md) (v2). See also
> "Review corrections" at the end of this file.

Full GBM → Pruning → Banding → Smoothing on the frequency node (678,012 rows, 8 bags,
4,000 trees, 20% random validation). Real browser replay with
`scripts/modelling_audit/workflow-interactions.mjs` on disposable copies of the demo.
No Haute or t-boost code was changed.

Chosen points: pruning budget **42** (the harness default of 44 does not exist under
this configuration; the available budgets were 10, 12, 15, 18, 22, 30, 42, 61, 92 and 142),
banding tolerance 0.75, smoothing strength 4. Final smoothed model: Gini 0.3659,
Poisson deviance 0.2981. No HTTP errors, console errors or warnings. Six browser long
tasks, 670 ms in total, the longest 142 ms.

## Where the time goes (unprofiled run)

| Phase | Wall s | Share | Avg cores (of 32) |
| --- | ---: | ---: | ---: |
| Full GBM fit | 66.7 | 23% | 9.4 |
| Automatic pruning path | 86.6 | 30% | 1.9 |
| Pruning: select → rendered / Apply | 2.9 / 3.8 | 2% | ~1 |
| Banding path | 65.7 | 23% | 1.9 |
| Banding: select → rendered / Apply | 4.7 / 4.4 | 3% | ~1 |
| Smoothing path | 43.8 | 15% | 1.4 |
| Smoothing: select → rendered / Apply | 4.4 / 7.9 | 4% | ~1 |
| **Total** | **≈291** | | |

GBM plus pruning path took 153.3 s, against 158.5 s in the previous session's single run.

**The three path jobs are 67% of the workflow and run at 1.4–1.9 of 32 cores.**

*Correction (Codex review):* the GBM fit is **not** using t-boost's measured default pool.
Haute always passes `n_jobs = os.cpu_count()` (32) through `training_threads()`
(`_descriptors.py:839`, `_tboost.py:622`). Explicit `n_jobs` overrides
`_default_fit_pool` (`t_boost/sklearn.py:3319`), whose width for this shape is 12 threads.

**Measured follow-up:** a fresh GBM + pruning-path run with `HAUTE_TRAINING_THREADS=12`
(t-boost's default width for this shape) gave:
- GBM worker **106.7 s**, against 59.0 s at 32 threads;
- pruning path 81.9 s, against 82.4 s.

So Haute's all-cores allotment is right on this 32-core machine with 678k rows.
t-boost's `_default_fit_pool` heuristic, measured on other data, is 1.8× slower here.
This is a single run each, but the gap is far beyond run-to-run noise.

### Worker lifetime against handoff (unprofiled, from the per-second process trace)

| Job | Worker process alive | Avg cores | Gap before the UI sees the result |
| --- | ---: | ---: | ---: |
| GBM | 59.0 s | 9.9 | 6.8 s until the pruning worker starts |
| Pruning path | 82.4 s | 2.0 | 4.1 s |
| Banding path | 61.0 s | 2.0 | 4.0 s (worker starts 0.7 s after Apply returns) |
| Smoothing path | 36.7 s | 1.5 | 6.2 s |

About 21 s of the phase windows above is spent between worker exit and the next state.
These gaps are already inside those windows, not additional to them. That covers
the result, view and bundle commit, the next worker's spawn, and UI polling. The time is
measured, but not yet broken down; the commit of 0.2–0.5 GB bundles plus 16–18 MB result
JSON is the likely main cost. The GBM phase is therefore ~59 s of worker plus ~7 s of
handoff. That is why it averages 9.4 cores against a steady ~14 while boosting.

### Profiling caveat

Profiled windows ran at 203.7 / 77.4 / 48.1 s, against 153.3 / 65.7 / 43.8 s unprofiled,
for GBM + pruning, banding and smoothing. Profiled worker-function seconds were GBM 82.1,
pruning 88.7, banding 63.0 and smoothing 38.6. cProfile inflates call-heavy Python such as
`deepcopy`, `_assert_json_finite` and `_cat_level`, with millions of calls each. Treat
those figures as upper bounds. JSON, zlib and native-call figures are reliable.

## Profile attribution of the path jobs (seconds, profiled)

| Cost | Pruning | Banding | Smoothing | Owner |
| --- | ---: | ---: | ---: | --- |
| Python `json` decode + encode | 30.3 | 18.7 | 13.7 | mostly t-boost, some Haute |
| t-boost model rebuilt from bytes/JSON (`full_model` + `from_json`) | 10.7 + 9.0 (55 + 39 calls) | 3.7 + 6.0 | 3.8 + 3.2 | t-boost |
| t-boost `data_digest` → `_data_members` JSON re-serialisation | 5.9 (45 calls) | 3.8 (31) | 3.3 (27) | t-boost |
| zlib DEFLATE in t-boost checkpoint writes | 9.6 | 5.0 | 4.1 | t-boost |
| Native path algorithm | 17.8 (`apply_keepset_budgeted`, 10 budgets in turn, ~2–5 cores) | 16.9 (`band_family`, one call, bursty ~4 cores) | 2.3 | t-boost (Rust) |
| Per-point `TrainingJob._run_evaluation`, run one point at a time | 36.0 / 11 points | 20.5 / 7 | 14.8 / 6 | Haute |
| …of which `_save_artifacts` (*correction:* mostly `StageRuntime.remember` → t-boost `pruning_selection` + `data_digest`; the actual model save is only 1.8 / 1.0 / 0.8) | 5.3 | 3.5 | 2.6 | mostly t-boost |
| Haute result JSON (`_staged_runtime._json`) + snapshot `deepcopy` | 4.6 + 2.6 | 4.2 + 1.7 | 3.2 + 0.6 | Haute |

The JSON rows overlap the t-boost model rows: rebuilding a model is itself JSON work.

## Measured structural issue: duplicated ancestor snapshots

Every path point's `result.json` embeds full copies of all ancestor stage snapshots
(`staged.stages.{full,pruned,banded}`). Each copy carries the dense `tboost_tables`
(5–6 MB) and the same 1.16 MB GBM loss history. These ancestors are identical for every
point in a family, by construction in `StageRuntime.finalize` (`_staged_runtime.py:591`).

- Final `.hstage`: **476.6 MB in 52 members, 412 MB of which is per-point result JSON**
  (24 results, 9–27 MB each). Candidate deltas total 26 MB, checkpoints 13 MB, source 8 MB.
  Rewriting the results without ancestor snapshots gives **~265 MB**, measured.
  Each point still carries the identical 1.16 MB GBM loss history twice (top level and
  its own snapshot) plus 5–6 MB of dense tables. Referencing those instead would shrink
  the bundle further.
- Each path job and each Apply unpacks the whole bundle with SHA-256 and repacks it with
  SHA-256 and fsync (`open_stage_bundle`, `_pack`). Each point result is also JSON-encoded
  twice (`_stage_paths.py:348` and again at `:378`).
- The final `train/status` response of each path job is **15.7–17.6 MB** (0.7–0.95 s),
  because it embeds that result. The frontend caches it in `completeTrainJob`, while the
  modelling view reads compact stage views separately.
- Path read (selecting a point) runs `_assert_json_finite` over the full result before
  the compact projection: 1.8 s of 2.4–3.4 s profiled.
- Apply (7.0–7.5 s profiled) is `deepcopy` of metadata (2.0 s), finiteness check (1.7 s),
  JSON encode, CRC/SHA over the 0.2–0.5 GB bundle, and fsync. It is almost all bookkeeping.

## Opportunities, ranked by expected impact

1. **Cache t-boost model objects and digests within a job** (t-boost; measured cost,
   saving estimated). `TBoostTrainingSession.full_model` is a property that runs
   `from_bytes` on every access (`t_boost/_staged.py:133`): 55 rebuilds, 10.7 s in pruning
   alone. `from_json` ran 39 times (9.0 s) and `data_digest` 45 times (5.1 s). Caching an
   immutable instance per session or fork, plus the digest, could remove ~20 s from pruning
   and a similar share of banding and smoothing. The no-mutation guarantee would need an
   explicit copy-on-write point.
2. **Store ancestor stage snapshots once per bundle, not per point** (Haute; measured).
   Per-point results would keep only their own stage plus a revision reference to the
   ancestors. Measured: the bundle shrinks from ~477 MB to ~265 MB, and further if the
   duplicated loss history and dense tables are referenced. The saving then reaches every
   path job's pack and unpack, the ~21 s of commit and handoff gaps, every Apply (a guess:
   4–8 s down to 2–3 s), every point read, and the 16–18 MB status response. Writing each result once
   instead of encoding it twice is a small follow-on.
3. **Improve scaling of the native path algorithms** (t-boost Rust; measured cost, saving
   not estimated). `band_family` runs for 16.9 s and is already partly parallel (bursts
   of 3–9 cores, ~4 on average). `apply_keepset_budgeted` runs 10 budgets one after
   another for 17.8 s at ~2–5 cores. Scaling was not isolated, so profile per thread
   before choosing between parallel budgets and wider inner loops.
4. **Cheaper checkpoint compression** (t-boost; measured cost). About 22 s of DEFLATE
   across the workflow, including 3.5 s in the GBM job. Intermediate `path-parent-*` and
   `*.checkpoint` files are then wrapped in Haute's uncompressed bundle anyway. Options are
   `compresslevel=1`, storing without compression, or zstd. Check the size/time trade-off.
5. **Slim or parallelise the per-point evaluation loop** (Haute; estimated). Points are
   evaluated one at a time at about one core, each running a full `TrainingJob.run`. Two
   steps: first, defer the model export until the selected point is known (5.3 / 3.5 /
   2.6 s). The selected point is only known after all points are evaluated, and
   `_reuse_evaluation` copies an equivalent point's export, so this needs a small
   restructure rather than a skip. Second, evaluate independent points in parallel, which
   is larger and needs care with the memory envelope.
6. **Leave the full result out of staged-job status** and validate finiteness once at write
   time rather than on every read (Haute; measured sizes, profiled cost is an upper bound).
   This saves about 1 s per path job, 16–18 MB of browser JSON parsing, and up to 1–2 s
   per point selection. Smoothing Apply took 7.9 s in the browser against 5.0 s on the
   server; that gap is unexplained.
7. **Vectorise t-boost categorical ingest** (t-boost; profiled 7.3 s inside the GBM job,
   an upper bound because cProfile inflates per-call Python).
   `_ingest.py:30 _cat_level` runs 3.19 million times with `pandas.isna` per value.

Items 1, 2 and 4 are mechanical changes; their measured costs add up to roughly a third
of the path phases. Item 3 is the largest remaining native cost.

## Harness note

`workflow-interactions.mjs` defaults to `--table-budget 44`, which is unavailable here.
Pass `--table-budget 42`, or make the default select from the available budgets explicitly.

## Evidence (scratch, may be cleaned)

Under `/tmp/claude-1000/-home-ralph-suite-haute-modelling-demo/56ea9cc6-8582-4085-8dc6-0ab58958adcb/scratchpad/`:

- `ev1`: unprofiled GBM + pruning path, with the per-second CPU trace `ev1/cpu.csv`.
- `ev1b`: resumed unprofiled run, pruning apply through smoothing.
- `ev2`: profiled full run, with worker and request `.prof` files. Analyse them with
  `prof_summary.py` and `callers.py` in the same folder.
- `run1`, `run2`: the disposable projects.

## Review corrections (Codex and Fable, 22 Nov)

- **Model-rebuild cost.** Fable found the root cause: `_attach_model`
  (`sklearn.py:5152`), `_restore_metadata` (`:1138`) and `_native_json_kind` (`:1324`)
  serialise the whole native bank to JSON and parse it, just to read a few fields. That
  is 18.0 / 8.8 / 6.4 s across the three path jobs.
- **Checkpoint churn (missing from the audit).**
  - Pruning loads the same checkpoint three times (8.45 s) and saves it twice (7.84 s).
    Banding and smoothing each load twice and save once.
  - Each rewrite defeats the stat-keyed session cache.
  - Most of the zlib time is about 100 MB of immutable rows being recompressed in every job.
- **Other Haute costs (missing).**
  - A second decode and re-encode pass over every point result (`_stage_paths.py:367-380`,
    ~4 s in pruning).
  - Candidates are round-tripped through bytes inside the same process.
  - Path read builds and walks the full pydantic response before compacting it (2.0 of 2.7 s).
- **Per-result read cap.** There is also a 32 MiB read cap per point result
  (`_stage_paths.py:463`); the largest is already 26.5 MB. `_pack` enforces neither cap at
  write time.
- **Deduplicated size.** With deduplicated content the bundle would be ~237–245 MB, not
  265 MB. Of the 320 MB of snapshots, only 80 MB is unique.
- **Path-job owner labels.** 14.5 s of the per-point `_run_evaluation` row is t-boost
  session and candidate restore, not Haute evaluation.
