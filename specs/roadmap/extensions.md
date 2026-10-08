# Extensions roadmap

## Scope

Installed packages that add a view to `haute serve` beside the pipeline editor, such
as Obverse. Current behaviour is specified in
[the extensions specification](../extensions/high-level.md), and the save ledger and
milestones in [the git integration specification](../git-integration/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXT-01 | Deferred | P2 | Files an extension writes are saved and committed like the pipeline's. |

## Planned improvements

### EXT-01 — Files an extension writes are versioned like the pipeline's
**Why:** In an extension's view the toolbar's Save and Commit act on the pipeline
only. Save commits exactly the files the pipeline save wrote, and Commit's sweep
takes only files Git already tracks, so a file an extension writes, such as
Obverse's form (`forms/form.json` in the project), never enters history. Agreed on
8 October 2026, and deferred while Obverse's builder is rebuilt.

**Plan:** The extension declares the project folders it writes, and Haute commits
changes in them, new files included, as it commits the pipeline's files. Save and
Commit first have the extension write its unsaved edits, through a function its
browser module exports, as Commit already saves the pipeline first; Ctrl+S in the
extension's view goes through the same Save. The extension never saves over a file
that changed on disk since it read it, whether a branch switch or a developer's
direct edit changed it, as the pipeline editor's save refuses a stale revision.
Branch switches happen in place, without a page reload, so the extension rereads
its files after one.

**Acceptance:** In an extension's view, Save records the extension's files on the
save ledger and Commit includes them in the milestone, new files included. A
developer's direct edit to one of them is included at Commit. After a branch switch
the extension shows that branch's files, and its save never overwrites a change
made outside it.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_save_pipeline.py::_capture_save_in_ledger`;
`src/haute/_git_transactions.py::_residual_tracked_changes`;
`frontend/src/extensions/loadExtensionModule.ts::ExtensionModule`.
