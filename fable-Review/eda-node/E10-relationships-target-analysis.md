# E10 — Relationships tab: target-aware one-way analysis (G-02, P1 — the flagship)

**Kind:** feature (fills a dead tab; turns Explore into a pricing tool) · **Effort:** L (surface), M (risk — every piece has a built analog)
**Review:** dev/reviewer pair (new numbers rendered to analysts)
Files: new `src/haute/routes/_explore_analysis.py` (or extend `_explore_service.py`),
`src/haute/routes/explore.py`, `src/haute/schemas.py`, new
`frontend/src/panels/explore/ExploreRelationshipsPane.tsx`, `frontend/src/panels/ExplorePreview.tsx`,
config plumbing in `frontend/src/panels/editors/` (target selector)
Tests: new `tests/test_explore_analysis.py`, `tests/test_api_contracts.py`, frontend vitest
Depends on: E06 (re-introduces the pane WITH content). Establishes the on-demand endpoint pattern
E11's key-uniqueness and the P3 backlog reuse.

## Why (analyst case)

This IS pricing EDA. Before fitting a GLM, an analyst runs one-way analysis: for each candidate
rating factor, exposure and mean target (claim frequency / severity / burning cost) across its
levels, and a ranking of factors by signal. Today they leave Haute or fit a model to get it.
`docs/EXPLORE_NODE_SPEC.md` "Future Scope" explicitly reserves Relationships for "target
relationships once target selection exists". Highest-value addition by a wide margin.

## The architectural asset this rides on (verified)

The Explore run already materialises the post-`code` frame to parquet in
`DataFrameExecutionCache` (namespace `explore_dataset`) — and **nothing reads it after the report
is built**. `DataFrameExecutionCache.scan(key)` (`_dataframe_execution_cache.py:369`) reopens it as
a pinned lazyframe with refcount/finalizer lifetime management; the key is reconstructable from the
same inputs `_prepare_spec` already uses
(`execution.build_dataframe_execution_cache_request(...).keys_by_node[node_id]`,
`execution.py:619`). Follow-up analyses therefore run **without re-executing the pipeline**.

## Backend design — the canonical on-demand endpoint (pattern for all Tier-2 analyses)

```
POST /api/explore/analysis/target   body: {graph, node_id, source, target, weight?, feature?}
  key = build_dataframe_execution_cache_request(graph, [node_id],
            namespace="explore_dataset", source=..., ...).keys_by_node[node_id]
  lf  = default_dataframe_execution_cache().scan(key)
  if lf is None: raise HTTPException(409, "Explore cache expired — re-run Explore first.")
  ctx = create_admitted_execution_context(profile=EXPLORE_ANALYSIS, ...)
  ... bounded aggregation via streaming_collect(..., execution_context=ctx)
```

- **409 on cache miss, never silent re-materialisation** — the dataframe cache is a byte-budgeted
  LRU; an expired entry must send the analyst back to the Run button, not hide a full pipeline
  re-execution behind a tab click.
- Synchronous route (like `POST /api/optimiser/frontier`) — the aggregations below are one bounded
  group-by per feature; add the job-lifecycle wrapper only if measurements demand it.
- Aggregations (Polars, bounded):
  - Categorical feature: `lf.group_by(f).agg(exposure = pl.col(w).sum() if w else pl.len(),
    target_sum = (pl.col(target) * pl.col(w)).sum() if w else pl.col(target).sum())`, derive
    `mean_target = target_sum/exposure`, `.sort(exposure, descending=True).head(50)`.
  - Numeric feature: bin first (`.qcut(~20)` quantile bins — label bins by edge values), then the
    same group-by. Null feature values form an explicit "Missing" level (never dropped).
  - Signal score per feature: exposure-weighted variance of `mean_target` across levels
    (correlation-ratio flavoured); one scalar per feature for the ranking rail.
  - Weight/target validation: numeric dtypes only, fail 400 with a clear message otherwise
    (fail loud; no coercion).
- Cache each response in a small LRU keyed `(dataframe_cache_key, target, weight, feature)` —
  mirror the report LRU.
- Schema: `ExploreTargetAnalysisResponse{ features: [{feature, kind, signal, levels:
  [{label, exposure, mean_target}]}] }` — deliberately AvE-shaped.

## Frontend design

- Config: target column + optional exposure/weight column persisted in the Explore node's `config`
  (same mechanism as `config.overview`; these are display-parameters — extend
  `dataAffectingConfig`'s strip list (`cacheIdentity.ts:15-22`) so choosing a target does NOT
  invalidate the cached report; add that assertion to the E07 test set).
- Relationships pane: `FeatureBrowser`-style left rail ranked by signal; right side **reuses
  `AveChart` (`modelling/AveTab.tsx:101`) verbatim** — exposure bars + mean-target line, rotated
  labels. The analyst sees the identical one-way visual pre-model (Explore) and post-model
  (modelling): a deliberate consistency win.
- Column pickers should offer columns from the cached report's schema (already in the store), not
  free text.

## TDD plan (failing tests first)

1. Backend: seeded cache entry → endpoint returns per-level exposure/mean_target matching
   hand-computed values for (a) unweighted count exposure, (b) weighted, (c) numeric feature via
   qcut bins, (d) nulls as explicit "Missing" level. **All fail today** (endpoint absent).
2. Cache-miss → 409 with the re-run message; no pipeline execution occurred (monkeypatch
   `execute_lazy_graph` to assert zero calls).
3. Non-numeric target/weight → 400.
4. Ranking: a constructed strong-signal feature outranks a noise feature.
5. Contract tests: route + schema registered (`tests/test_api_contracts.py` conventions).
6. Frontend: rail renders ranked features from fixture; selecting one renders AveChart bars/line;
   409 renders the "re-run Explore" empty state; target picker writes config and does not blank the
   Overview (ties to E07 test 1).
