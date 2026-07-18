# G07 — Every git error toast shows `HTTP 4xx` instead of the backend's hand-written message

**Severity: HIGH · Confidence: CONFIRMED (verified in client + all call sites) · Class: UX — the guardrail voice is muted**
**Files: `frontend/src/api/client.ts` (evidence), `frontend/src/panels/GitPanel.tsx`, `frontend/src/components/{BranchManager,WorkingBranchModal,DivergenceModal,MilestoneCommitModal,RemotePushControl}.tsx`**
**Origin: U-1 (UX reviewer). Independently verified: `client.ts:427` + `BranchManager.tsx:106-108`.**

## The defect

The engine's whole error design routes hand-written guidance to the user verbatim
(`routes/git.py:113` guardrail 403, `:116` domain 400) — messages like:

- `"'main' is a protected branch and cannot be used as a working branch."`
- `"'x-save' is a save ledger (managed by haute) and cannot be used as a working branch."` (`_git.py:671-673`)
- `"'X' has no save ledger yet — nothing to commit."` (`_git.py:814`)
- `"Version label '2.1' already exists."` (`_git.py:846`)
- `"You have unsaved changes. Save or discard them before…"`

But the client throws `new ApiError(\`HTTP ${res.status}\`, res.status, detail, body, rawDetail)`
(`client.ts:427`) — the **message** is the literal string `"HTTP 400"`; the backend text rides in
`err.detail`. Every git toast reads `err.message`:

`GitPanel.tsx:91,217,265` · `BranchManager.tsx:65,106` · `WorkingBranchModal.tsx:62` ·
`DivergenceModal.tsx:53` · `MilestoneCommitModal.tsx:71` · `RemotePushControl.tsx:115,141,169`

So a git-naive user who tries to name a branch `main` sees **"Could not create branch: HTTP 403"**
— the carefully-authored explanation never reaches them. Network failures surface as the equally
raw `"Failed to fetch"`. The two structured-409 paths (milestone fork, push rejection) are the
only ones that read the body correctly — proof the data is there and rendering it works.

The correct pattern already exists in-tree: `err.detail || err.message`
(`usePipelineAPI.ts:871`, `CacheFetchButton.tsx:124,180,208`).

## Fix design

One shared helper in `frontend/src/api/client.ts` (exported next to `ApiError`):

```ts
export function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) return err.detail || err.message
  if (err instanceof TypeError) return "Couldn't reach the server — check your connection."
  return err instanceof Error ? err.message : "unknown error"
}
```

Replace every `err instanceof Error ? err.message : "unknown error"` in the ten git call sites
with `apiErrorText(err)`. Sweep the rest of the frontend for the same anti-pattern opportunistically
(grep `err.message` near `addToast`) but the git surface is the deliverable here.

Note: for plain `GitError`s the backend detail is the sanitized constant — the toast then shows
that constant, which is the designed behaviour (and G09 makes those rarer by classifying common
failures into domain errors).

## TDD plan (vitest/RTL, one per component)

1. `BranchManager` — mock `setWorkingBranch` → reject
   `new ApiError("HTTP 403", 403, "'main' is a protected branch and cannot be used as a working branch.")`;
   assert the toast text contains **"protected branch"** and not **"HTTP 403"**.
2. Same shape for GitPanel fork-create (400 "already exists"), WorkingBranchModal,
   DivergenceModal, MilestoneCommitModal ("no save ledger yet"), RemotePushControl
   (non-409 plain 400 path).
3. `apiErrorText` unit tests: ApiError-with-detail → detail; ApiError-without-detail → message;
   TypeError → connection copy; plain Error → its message.
4. Existing 409 modal tests must stay green (they already consume `err.body`).

## Notes

Small, mechanical, huge payoff — this single package restores the entire authored error UX.
Batch review acceptable.
