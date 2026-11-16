# Root cause: late answers

## Scope

An answer from the server that arrives after what asked for it has
changed, and is applied all the same.

This file is one pull request. It holds one package, `FES-04`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `FEG-10` | [frontend-graph-canvas](frontend-graph-canvas.md) | A late answer to a repair leaves the canvas on the current document | Resolves |
| `VCS-10` | [git-integration](git-integration.md) | An edit made while Catch up runs is kept, or cannot be made | Resolves |
| `FEP-04` | [frontend-preview-explore](frontend-preview-explore.md) | A utility file edited while another is being deleted keeps its edit | Resolves |
| `FEN-01` | [frontend-node-editors](frontend-node-editors.md) | A late answer does not replace the chosen catalog's schemas | Resolves |
| `FEN-10` | [frontend-node-editors](frontend-node-editors.md) | Imported values that arrive after their factor was removed are dropped | Resolves |
| `WB-61` | [workbench](workbench.md) | Text being typed when a test quote opens is kept | Resolves |
| `MLR-10` | [mlflow-model-registry](mlflow-model-registry.md) | Retry after a changed experiment does not show the old run as the new one | Resolves |
| `FEG-04` | [frontend-graph-canvas](frontend-graph-canvas.md) | A lost answer to a save does not lock the editor against itself | In part |
| `FEG-05` | [frontend-graph-canvas](frontend-graph-canvas.md) | A tab locked by a change on disk unlocks when the change is gone | In part |
| `AST-06` | [assistant](assistant.md) | An apply that lands after the turn's time limit is shown as applied | In part |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-04 | Planned | P2 | Every answer from the server is applied through one helper that drops it when what asked for it has changed. |

## Planned improvements

### FES-04 — An answer is applied only to what asked for it
**Why:** Read on 16 November 2026. The editor has a fence for answers that
arrive late: a caller captures the document's state before a request and
checks it after. It was an earlier roadmap's work, and where it is used no
finding of this review falls. It is used by choice. Of 105 source files
that wait for an answer, 12 use the fence, 8 use the optimiser's
latest-request hook, and 22 keep a counter or a cancelled flag of their
own. The files behind this review's late-answer findings use none of them:
`App.tsx`, the utility panel, the repair dialog, the catalog selector, the
MLflow export section and the test-quotes store (`FEG-10`, `FEP-04`,
`FEN-01`, `MLR-10`, `WB-61`). The fence was also built for execution
answers; an answer that writes to the document or to a panel, such as a
repair, a catch up, a delete or imported values (`VCS-10`, `FEN-10`), has
no shared equivalent. A new panel is correct only if its author remembers.

**Plan:** One request helper that every call goes through when its answer
will be written to state. It records what the answer belongs to: the
document, the node, the panel and the edit it was asked under. A late
answer is dropped, or handed back marked stale for the caller to settle.
Replace the 22 hand-kept guards with it. Add a test that lists the files
which write state after waiting without the helper, and only lets the list
shrink, to none.

**Acceptance:** One test drives every surface that calls the server: it
holds the answer, changes what it belongs to (another document, the node
deleted, an Undo, text typed), lets the answer go, and requires the page to
show the current state. The list of files outside the helper is empty. The
journeys of `FEG-10`, `VCS-10`, `FEP-04`, `FEN-01`, `FEN-10`, `WB-61` and
`MLR-10` pass.

**Dependencies:** None. Deliver before the packages its acceptance names.

**Evidence:** `frontend/src/stores/useDocumentStatusStore.ts`;
`frontend/src/panels/optimiser/useLatestRequest.ts`; `frontend/src/App.tsx`;
`frontend/src/panels/UtilityPanel.tsx`.
