# G05 — Version-label N+1 in `working_milestones` + the `commit_context` walk

**Severity: HIGH · Confidence: CONFIRMED (measured) · Class: user-visible latency on the hottest panel path**
**Files: `src/haute/_git.py`; `frontend/src/components/ComparisonView.tsx` (call-count only)**
**Origin: P-1, P-5 (perf reviewer; scratch-repo measurements on this Windows machine).**

## Measured baseline

Subprocess spawn on this machine: ~45-80 ms each (`git --version` costs the same as real work —
the spend is process spawn + AV + git startup). Measured end-to-end:

| Scenario | As implemented | Batched | Speedup |
|---|---|---|---|
| `working_milestones(limit=50)`, 50 version tags | **4 562 ms** | 197 ms | **23×** |

## P-1 [HIGH] One `git tag --points-at` per milestone

`working_milestones` calls `_version_label_for(sha)` inside the per-entry loop (`_git.py:1527`);
each call is one `git tag --points-at <sha> --list version/*` spawn (`_git.py:1538-1544`). The
panel requests `limit=50` (`GitPanel.tsx:79`), and the panel refetches on **every save**
(`historyNonce` effect, `GitPanel.tsx:119-121`) — so every save with the panel open pays ~50
spawns ≈ 4.5 s just for labels.

**Fix.** Resolve all labels in ONE spawn and dict-lookup per entry:

```
git for-each-ref --format='%(refname:short)%09%(objectname)%09%(*objectname)' refs/tags/version/
```

Build `{commit_sha: label}` keyed on `*objectname` when non-empty (peeled annotated tag), else
`objectname` (lightweight tag). The peeled column is **mandatory** — annotated `version/*` tags
have `objectname != *objectname`; this is the same care `_ls_remote_version_tags` already takes
(`_git.py:2088-2095`). `working_milestones` drops from `3 + N` to ~4 spawns; `_version_label_for`
keeps one direct caller (`commit_context`, single sha) — route it through the same map builder.

## P-5 [HIGH] `commit_context` multiplies the N+1 and adds an O(M) ancestry walk

`commit_context` (`_git.py:1600`) calls `working_milestones` internally (inheriting the tag N+1),
then for a non-milestone sha walks milestones newest-first calling `_ledger_point` (`rev-parse ^2`)
+ `_is_ancestor` (~113 ms each) per milestone (`_git.py:1634-1641`). `ComparisonView` calls
`getCommitContext` **twice** per comparison open (`ComparisonView.tsx:342`, `:356` — once for the
inspected sha, once for the live tip with `base=`). Worst case measured shape: 2 × (20 tag spawns
+ up to 40 walk spawns) ≈ 1.5-4 s per comparison open.

**Fix.**
1. P-1's map removes the tag component for free.
2. Fetch all milestones' second parents in ONE spawn — `git rev-list --first-parent --parents
   --max-count=<limit> <working>` yields `sha parent1 [parent2]` per line; `_ledger_point` becomes
   a dict lookup (second column pair), eliminating the per-milestone `rev-parse ^2`.
3. The remaining per-milestone `_is_ancestor` probe is bounded by the walk's early `break`; after
   (1)+(2) the worst case is ~M ancestry probes with no other traffic. If still hot in practice, a
   single `git merge-base --is-ancestor` replacement is NOT available batched — accept the bounded
   loop, or compute reachability once via `git rev-list <sha>..<fold-points>` set logic (design
   note only; don't gold-plate).
4. Frontend: share one milestones/context fetch across ComparisonView's two calls where trivially
   possible (they differ by `base` — at minimum memoize within one open).

## TDD plan (structural, not wall-clock)

1. Counting wrapper monkeypatching `_run_git` / `_run_git_ok` to tally argv[0..1]:
   - `test_working_milestones_spawn_count_is_constant_in_milestone_count` — equal counts at M=2
     and M=40; assert zero `tag --points-at` invocations.
   - `test_commit_context_tag_lookups_do_not_scale_with_milestones`.
2. Behavioural guards (must stay green through the rewrite):
   - annotated AND lightweight `version/*` tags both resolve to labels (lightweight is currently
     untested anywhere — closes part of T-6);
   - a tag on a *ledger save* (not a milestone) does not mislabel the milestone;
   - `is_root` tagging of the oldest entry unchanged.
3. Keep the existing `TestWorkingMilestones` / `TestCommitContext` suites green — they pin
   nearest-milestone/distance semantics that the rewrite must not disturb.

## Notes

Biggest single perf win in the subsystem, clean and mechanical. Per the calibrated-review split,
batch review is acceptable (no silent-wrongness surface beyond the peeled-tag mapping — test it).
