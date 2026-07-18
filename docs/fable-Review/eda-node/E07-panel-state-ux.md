# E07 — Panel state UX: stale display, failure surfacing, superseded, per-source slots, first-run defaults

**Severity:** MEDIUM (UX correctness cluster) · **Effort:** M · **Review:** dev/reviewer pair (state machine)
Files: `frontend/src/panels/ExplorePreview.tsx`, `frontend/src/panels/explore/ExploreOverviewPane.tsx`,
`frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/hooks/usePipelineAPI.ts` (or wherever
`FAILED_JOB_STATUSES` lives), `frontend/src/panels/explore/overviewCardDefinitions.ts`,
`frontend/src/panels/editors/ExploreOverviewConfig.tsx`
Tests: `ExplorePreview.test.tsx`, `ExploreOverviewPane.test.tsx` (new cases),
`useBackgroundJobs.test.ts`, `ExploreOverviewConfig.test.tsx`

Five related defects in how the panel presents job/report state. One package because they share the
same state machine and test files; the reviewer should hold the whole picture.

## EF-14 [MEDIUM] — any upstream config change hard-hides the cached report

**Evidence:** the rendered `report` requires an exact `configHash` + `source` match
(`ExplorePreview.tsx:87-96`); any mismatch → `report = null` → "No cached data yet"
(`ExploreOverviewPane.tsx:81-93`). The identity includes each upstream node's `label`
(`cacheIdentity.ts:55-61`) and the entire `submodels` + `preamble` (`:77-78`). So renaming an
upstream node, editing an unrelated submodel, or touching the preamble blanks a populated Overview.
The store *documents* the opposite pattern for solve/train: "A config change doesn't delete the old
result — it's kept with a staleness flag" (`useNodeResultsStore.ts:9-12`). Explore is the outlier.

**Backend fact (verified):** labels are NOT part of the backend cache fingerprint — the base
fingerprint hashes `[id, nodeType, config]` (`_cache.py:226-237`). But a label rename does flow
into generated function names on file round-trip, so surgically dropping `label` from
`cacheIdentity` is NOT the recommended fix; display-staleness is.

**Fix:** stale-while-revalidate display, mirroring solve/train. When a cached result exists for the
node but its hash/source differs, keep rendering the last report with a prominent staleness strip
("Config changed since this snapshot — Re-cache", `--warning` styling) instead of nulling. The
Run button label already distinguishes ("Re-cache full data"). Cache-identity contents stay
untouched (conservative invalidation is correct; the *display* was the bug).

## EF-15 [MEDIUM] — failures are toast-only; the panel then shows a misleading empty state

**Evidence:** startup failures store `error` + `terminal_reason` on the entry
(`ExplorePreview.tsx:138-148`; `useNodeResultsStore.ts:1063-1071`) but the body never reads them
(`:246-255`); the subtitle collapses everything to "Error" (`:46`); Overview shows "No cached data
yet" — misreading a failure as not-run-yet. A `memory_limited` message (which can carry a suggested
chunk size, `useBackgroundJobs.ts:167`) vanishes with the toast.

**Fix:** when the current entry's terminal state is a failure, render an in-body error card
(message + `terminal_reason` + Retry button; reuse the Data Quality danger styling,
`ExploreSummaryCards.tsx:120-138`). `memory_limited` gets its remediation text surfaced.

## EF-16 [LOW/MEDIUM] — a benign `superseded` terminal shows as a red error toast

**Evidence:** two requests sharing `family_key` supersede the older job
(`_explore_service.py:598-605`); the poller classifies terminals via `FAILED_JOB_STATUSES`, which
**includes `"superseded"`** (`usePipelineAPI.ts:29-36`) → `onFail` → error toast. Reachable from
two tabs/clients on the same node+source. Backend semantics are correct; presentation is wrong.

**Fix:** treat `superseded` as a silent/informational terminal in the explore poller (drop the
job without an error toast; optional muted toast "Superseded by a newer run"). Check the same
mapping for optimiser/train polling while there — fix only explore in this package, note others.

## EF-17 [LOW] — one result slot per node across sources

**Evidence:** `exploreResults` is keyed by `nodeId` only (`useNodeResultsStore.ts:557`);
`completeExploreJob` overwrites (`:1026-1054`). Running under source B evicts source A's report
from the UI; flipping `activeSource` back shows "No cached data yet" though the backend report
cache likely still holds it.

**Fix:** either key the slot by `(nodeId, source)` (bounded: sources are few) or accept eviction
and rely on EF-14's staleness display + fast backend re-hit. Recommend the compound key — it's
small and honest. Keep `MAX_CACHED_EXPLORE_RESULTS` eviction across the compound keys.

## EF-18 [MEDIUM] — a node named "Automatic analysis" starts with every card off

**Evidence:** fresh Explore nodes get `defaultConfig: {}` (`nodeTypes.ts:62`, description
"Automatic analysis of an upstream dataset"); `isOverviewCardEnabled` defaults absent keys to
`false` (`overviewCardDefinitions.ts:37-42`); the pane shows "No cards enabled" with a pointer at
the right-panel toggles (`ExploreOverviewPane.tsx:67-79`). First-run experience: drop node → run →
empty pane → hunt through config.

**Fix (design carefully — round-trip semantics):** default ON for `dataset_snapshot`, `schema`,
`data_quality` **only when the `overview` key is entirely absent** from config (fresh node). An
explicit `overview` map — even partial — stays authoritative with absent-means-false (current
behaviour), so existing files don't change meaning. Implementation:
- `readOverview` returns a discriminated result (`absent` vs map) or the pane applies
  `DEFAULT_ENABLED_CARDS` when `config.overview === undefined`.
- `ExploreOverviewConfig.toggleKey` currently drops disabled keys and writes only enabled ones
  (`ExploreOverviewConfig.tsx:99-108`): once the user touches ANY toggle, write **all five keys
  explicitly** so the file pins the user's exact choice and the absent-key default never
  reinterprets it later. Backend `validate_explore_overview` already accepts explicit `false`;
  codegen already drops only empty `{}` — an all-false map round-trips fine (verify in
  `tests/test_explore_round_trip.py`).
- Update the config-panel copy so defaults are discoverable ("Snapshot, Schema and Data Quality are
  on by default").

## TDD plan (failing tests first)

1. `ExplorePreview.test.tsx` — seed cached report; rerender with an upstream label-only change:
   snapshot card still visible + staleness strip present. **Fails today** (the `:523-630` block
   pins the old hide-behaviour for a data change — keep that for the *strip* variant, change the
   assertion from "hidden" to "stale-marked").
2. `ExplorePreview.test.tsx` — mock `runExplore` rejection and a `memory_limited` poll terminal:
   body shows the message text + Retry; subtitle shows the terminal reason. **Fails today.**
3. `useBackgroundJobs.test.ts` — a poll returning `status: "superseded"`: no error toast, job
   removed. **Fails today.**
4. Store test — complete job under source "live" then "batch": both reports retrievable (compound
   key). **Fails today.**
5. `ExploreOverviewPane.test.tsx` — `config` without `overview`: Snapshot/Schema/Quality cards
   render given a report. **Fails today.** With `overview: { schema: true }`: only Schema renders
   (authoritative-map semantics pinned).
6. `ExploreOverviewConfig.test.tsx` + `tests/test_explore_round_trip.py` — toggling one card off
   from the default state writes all five keys explicitly; backend round-trips the all-false map;
   generated .py for an untouched node stays bare (no `overview=`).
