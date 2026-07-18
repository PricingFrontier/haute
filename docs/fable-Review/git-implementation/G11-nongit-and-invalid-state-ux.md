# G11 — State UX: non-git projects vanish silently; `invalid`/`divergent` mislabelled; detached-HEAD copy wrong

**Severity: MEDIUM · Confidence: CONFIRMED · Class: persona-A onboarding + persona-B interop honesty**
**Files: `frontend/src/components/BranchIndicator.tsx`, `frontend/src/components/WorkingBranchModal.tsx`, `frontend/src/components/DivergenceModal.tsx`, `frontend/src/stores/useGitStore.ts`; backend copy in `src/haute/_git.py` (`check_invariants`)**
**Origin: U-6, U-7, U-8 (UX reviewer). Independently verified: `BranchIndicator.tsx:50` and `:54-68`.**

## U-6 [MEDIUM] Non-git project (or a status error) makes the whole feature invisible

`useGitStore.loadStatus` catches everything (`useGitStore.ts:127-133`) → `status` stays `null` →
`BranchIndicator` renders nothing (`BranchIndicator.tsx:50` `if (status === null) return null`) →
saves proceed ungated (`App.tsx:317-320`, by design). Net: for a git-naive user in a non-git
folder, the README's headline "Version control without the learning curve" simply does not exist —
no indicator, no explanation, no CTA — and a *transient* status failure is indistinguishable from
"not set up".

**Fix.** Distinguish the three null-ish cases and render a muted affordance instead of nothing:
- Backend: the 400 for a non-repo is already the distinct hand-written
  `"Not a git repository. Run 'git init' first."` — have `loadStatus` keep
  `{kind: "no-repo" | "error"}` alongside `status: null` (ApiError with that detail → `no-repo`;
  anything else → `error`).
- Indicator, `no-repo`: muted chip **"Version control off"** → click opens a small explainer with
  **"Turn on version control"** that calls a new, deliberate `POST /api/git/init` (plain
  `git init -b main` + the G02 ignore seeding; engine fn `init_repo()` with the same guardrail
  error taxonomy) — this also closes the loop with the engine's own "Run 'git init' first" advice
  without sending persona A to a terminal.
- Indicator, `error`: muted **"Version control unavailable — retry"** (click = `loadStatus()`).
Proposed explainer copy: *"This project isn't tracking versions yet. Haute can set that up —
you'll get saves, history, and safe branching. Nothing leaves your machine."*

## U-7 [MEDIUM] `invalid` (and `divergent`) render as red "Set branch" with a wrong tooltip and no remediation

Every non-ready state hits the same branch (`BranchIndicator.tsx:54-68`): label **"Set branch"**,
tooltip *"No working branch set — click to choose one"* — wrong for `invalid` (a branch IS set;
its invariants broke, e.g. after a CLI commit on the working branch) and for `divergent` (user
moved HEAD outside haute). The modal then shows the raw invariant string
(`WorkingBranchModal.tsx:69,94-101`), e.g. *"'X' tip tree differs from its last-merged ledger
commit — the working branch was advanced outside haute"* — accurate for persona B, opaque for
persona A, and neither gets a next step.

**Fix.**
- Indicator: per-state label/tooltip — `unset` → "Set branch" (as today); `invalid` → **"Version
  control needs attention"**; `divergent` → **"You've moved off your branch"** (click opens the
  respective modal, as today).
- Modal: translate the two common invariants into remediation copy, keeping the raw text as a
  collapsible "details" line for persona B:
  - tip-tree-differs → *"Changes were committed to `X` outside Haute. Start a fresh branch from
    the current state to keep everything — your history stays intact."* (button routes to the
    branch manager's create flow, which G04's invariant gate makes the one safe path).
  - no-shared-history / merge-base-off-ledger → same shape, "start a fresh branch" CTA.
- `divergent` modal already offers return-to-branch — verify copy names the branch, not "HEAD"
  (see U-8).

## U-8 [LOW] DivergenceModal on detached HEAD says "on `HEAD`" and gives a false disabled-reason

`_get_current_branch` returns literal `"HEAD"` when detached (`_git.py:284-287`);
`DivergenceModal.tsx:102-103` renders *"the repo is currently on `HEAD`"*, and the hard-coded
disabled-reason (`:117-120`) *"This branch can't be a working branch (it's protected or a save
ledger)."* is untrue for detached HEAD.

**Fix.** Detect `current_branch === "HEAD"` → copy *"the repo is on a specific version (not a
branch)"*; hide-or-reword the stay-here option accordingly: *"You're not on a branch — pick a
branch to continue, or go back to your working branch."*

## TDD plan (vitest/RTL)

1. `loadStatus` rejects with ApiError(detail="Not a git repository…") → indicator renders the
   "Version control off" affordance (not `null`); rejects with network error → "unavailable —
   retry"; retry click refetches.
2. Status `state:"invalid"` + tip-tree-differs error → indicator label is "Version control needs
   attention" (asserting NOT "Set branch"), modal shows the remediation copy AND the raw string
   under details.
3. Status `state:"divergent"`, `current_branch:"HEAD"` → detached-specific copy; a named branch →
   branch-named copy.
4. Backend (if `POST /api/git/init` is built): route test — non-repo → creates repo with `main`,
   seeds ignore per G02, idempotent-refuses on an existing repo with a domain error.

## Notes

The `git init` affordance is the one genuinely new surface proposed by this review — it is the
missing first rung of the README's no-learning-curve ladder. Keep it deliberate (a button that
says what it does), never automatic.
