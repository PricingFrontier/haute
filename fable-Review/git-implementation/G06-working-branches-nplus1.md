# G06 — `working_branches` N+1 + the frontend's redundant refetch chain

**Severity: HIGH · Confidence: CONFIRMED (measured) · Class: hot-path latency (every save with the panel open)**
**Files: `src/haute/_git.py`; `frontend/src/panels/GitPanel.tsx`; `frontend/src/components/BranchManager.tsx`; `frontend/src/stores/useGitStore.ts`**
**Origin: P-2, P-3, P-7 (perf reviewer; measured).**

## P-3 — the amplification chain this package kills

Every save → `notifyHistoryChanged()` (`usePipelineAPI.ts:867`) → `historyNonce` → GitPanel
`refresh()` fires `Promise.all([getMilestones(50), getPendingSaves, getWorkingBranches])`
(`GitPanel.tsx:78-82`) **plus** RemotePushControl refetches `/remotes`
(`refreshNonce = historyNonce + commitNonce`, `GitPanel.tsx:316`, `RemotePushControl.tsx:87-89`).
Measured: ~**100 concurrent git spawns ≈ 4.5 s wall-clock per save** with the panel open
(50-milestone, 20-branch repo). After G05 + this package: ~17 spawns, <1 s.

## P-2 [HIGH] `working_branches` spawns 3 processes per branch

`working_branches` (`_git.py:2464-2483`) loops every branch calling `_has_unmerged_saves`
(`_git.py:2430-2438` = `_rev_parse` ×2 + `_merge_base`, the ~113 ms op) plus `_rev_parse(fork)`
per fork back-link. Measured **4 202 ms @ 20 branches** (formula 4 + 3·B). Fires twice on panel
open (see P-7) and on every save.

**Fix.**
1. One `git for-each-ref refs/heads/ --format='%(refname:short)%09%(objectname)'` resolves every
   tip → dict; kills both per-branch `_rev_parse` calls and the per-fork existence probes.
2. Replace `_merge_base(w, l) != ledger_tip` with `git merge-base --is-ancestor <ledger_tip>
   <working_tip>` — has-unmerged ⟺ NOT ancestor. Net 3 → 1 spawn per branch (~2× measured; the
   honest ceiling — a fully O(1) `rev-list --not` design risks mis-pairing ledgers across forks
   that share history; do not attempt it in this package).
3. `list_branches` already models the right pattern (single `for-each-ref` with
   `%(ahead-behind:…)`, `_git.py:544-550`) — reuse its plumbing style.

## P-7 [MEDIUM] `getWorkingBranches` fetched twice on open and on every save

`GitPanel.refresh` fetches it (`GitPanel.tsx:81`) on mount **and** every `historyNonce`;
`BranchManager` independently fetches it on its own mount (`BranchManager.tsx:60-74`). Both mount
together on panel open. Fork back-links change only on branch create/archive/delete/restore —
never on a plain save.

**Fix.** Single source of truth: lift the working-branches list into `useGitStore` (or pass
GitPanel's fetch down to BranchManager as a prop). Fetch once per panel open; refresh on
branch-mutation events (create/archive/delete/restore/switch complete) — **not** on
`historyNonce`. Keep `pending`/`milestones` on the save-driven nonce as today.

## TDD plan

1. Backend structural: counting wrapper — `test_working_branches_spawns_grow_at_most_one_per_branch`
   (B=2 vs B=12: Δspawns ≤ 1·ΔB); behavioural: unmerged-saves flag still correct for (a) fresh
   pair, (b) pending saves, (c) fully-milestoned, (d) pre-spawn ledger (no ledger ref).
2. Frontend (vitest): render the panel → assert exactly **one** `/working-branches` request on
   open and **zero** on a subsequent plain save (mock client, count calls); fork back-links still
   render (existing GitPanel tests stay green).
3. Integration guard for P-3: with panel mounted, count backend git spawns for one save
   before/after (structural count via monkeypatched `_run_git*` in the TestClient app);
   assert the count is independent of milestone and branch count.

## Notes

Backend leg is mechanical (batch review OK). The frontend leg touches data-flow between
GitPanel/BranchManager — small but review for effect-loop regressions (the existing
nonce architecture is sound; don't disturb selection semantics, see CLEARED).
