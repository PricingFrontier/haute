# Git integration roadmap

## Scope

Version control inside Haute, as [the git integration
specification](../git-integration/high-level.md) describes it: the working
branch and its save ledger, catching up from a remote, and what a failed git
command leaves behind. The packages below come from the review plan's runs
of 11 November 2026.
`VCS-09` comes from the plan's browser sweep of what happens around an
open editor, also on 11 November, run again by the main session with two
tabs and with a terminal.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| VCS-09 | Planned | P2 | An open tab learns when the project changes branch, and a save made from a tab that shows another branch is refused naming both, instead of landing on the other branch, moving the checkout back or leaving its files uncommitted with "see server log". |

## Planned improvements

### VCS-09 — An open editor learns that the project changed branch
**Why:** Reproduced in a browser on 11 November 2026, by the review plan's
sweep of what happens around an open editor and again by the main session.
The page reads the project's branch when it loads and after its own git
actions. Nothing tells an open tab that the branch has changed, and a save
does not ask. Three ordinary cases were run.

- Two tabs on one project. One holds an unsaved rename. In the other the
  analyst creates the branch `other` and switches to it. The first tab's
  toolbar still reads `pricing/haute-e2e/work` 45 seconds later, and after
  the tab is brought to the front, and it shows no notice. Its Save answers
  "Saved → rating/main.py". The rename is recorded on `other`; the branch
  the toolbar names has nothing. A reload shows `other`.
- A checkout in a terminal. With the editor open, the analyst runs
  `git checkout colleague` to look at a colleague's branch, whose pipeline
  differs. The tab takes the file, "Pipeline updated from file", and its
  toolbar keeps the old branch. The analyst renames a node and saves:
  "Saved → rating/main.py", then "Changes saved; version capture failed
  (git error — see server log)." The save's thirteen files are left as
  uncommitted changes on the colleague's branch, and the analyst's own
  branch has nothing. The log has git's reason: the save tried to check
  out the analyst's branch, and git would not overwrite the file the save
  had just written.
- A new branch in a terminal. The analyst runs `git switch -c mine`, which
  changes no file. The tab shows nothing. Its next Save answers "Saved →
  rating/main.py" and checks the project out on the working branch Haute
  recorded: the terminal is no longer on `mine`, and nothing says so.

After the second case a reload does tell the analyst: the toolbar reads
"Branch changed externally" and a dialog says "You've moved off your working
branch". Its first choice, "Go back to pricing/haute-e2e/work", then answers
"Could not switch working branch: Operation failed. Check the server logs
for details." That was a switch refused over the files Haute's own save
wrote. Its second choice, "Make colleague my working
branch", succeeds and leaves the save's files uncommitted.

One step causes all three. A save's capture resolves the ledger of the
working branch Haute recorded and checks it out whenever the checkout is
anywhere else. That is right the first time a branch is saved to, and wrong
once the analyst has moved the project. The readiness the page loads has a
state for it, `divergent`, and the dialog above. The commands' own captures
follow the other rule: theirs "is made only while the checkout is on the
chosen working branch's ledger; on any other branch, or a detached `HEAD`,
it is skipped, saying where the checkout is, so no other branch's files are
replaced".

**Plan:** Tell open tabs when the branch changes. The live-sync connection
sends the readiness when the checked-out branch or the recorded working
branch changes, whoever changed it, and the page reads it as it does after
its own git actions, so the toolbar and the dialog follow without a reload.
Give a save the rule the commands' captures have. A save names the working
branch its tab shows. When that is not the recorded one, or the checkout is
on neither that branch nor its ledger, the save is refused before anything
is written, naming both branches, and the unsaved work stays in the tab. A
save never checks out a branch the analyst left.

**Acceptance:** In a browser test with two tabs, a switch in one changes
the other's toolbar without a reload, and a Save sent from a tab before it
has learned of the switch is refused naming both branches. After
`git checkout` of another branch in a terminal, the open tab shows the
dialog; a Save made before a choice is refused naming where the project is,
writes nothing and leaves the checkout alone. After `git switch -c` in a
terminal, a Save does not move the checkout.

**Dependencies:** None. `FEG-05` covers a tab with unsaved work whose file
changes under it.

**Evidence:** `src/haute/_git_setup.py::resolve_ledger`;
`src/haute/_git_transactions.py::commit_save`;
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`src/haute/_git_read_models.py::working_branch_status`;
`src/haute/cli/_init_cmd.py::capture_new_files`;
`frontend/src/components/BranchIndicator.tsx`;
`frontend/src/components/DivergenceModal.tsx`;
`frontend/src/stores/useGitStore.ts`;
`frontend/src/hooks/useWebSocketSync.ts`;
`specs/git-integration/low-level.md`.

