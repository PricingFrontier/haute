# Versions

Haute keeps your project's history as saves and milestones on a branch. Click the branch
name in the toolbar to open the Git panel, which shows that history.

## Saves and milestones

Every save (Ctrl+S) is a save on your working branch. **Commit a milestone** gathers the
saves since the last milestone under a message and, if you like, a version label.

The panel lists the saves since the last milestone in a highlighted box, then the
milestones. Opening a milestone shows the saves in it. Each row's commit code is a short ID
for that save or milestone.

A save is recorded exactly as you wrote it. Your project's git hooks run at **Commit a
milestone**, as `git commit` would run them, over everything the milestone records. The
pre-commit hook `haute init` sets up fixes and formats the Python with ruff, and what it
changes is recorded as one more save before the milestone. A copy of the project you clone
gets the same hook once you choose your working branch.

## When a milestone is refused

If a hook refuses, nothing is committed and your saves are untouched. When the refusal comes
from the pre-commit hook, the message lists each problem by the node whose code holds it
(or by file and line outside a node), quoting the line:

```
Node `priced`: `check = df.filter(pl.col("x") > 0)` - Local variable `check` is assigned
to but never used (F841)
```

These are problems ruff cannot fix for you, because the fix depends on what you meant.
Open the node, change the line, save, and commit again. The common ones:

| Code | What it means | What to do |
|---|---|---|
| F841 | A variable is set but never used. | Delete the line, or use the variable. |
| F821 | A name is used that nothing defines, usually a typo. | Correct the name. The pipeline would fail on it when run. |
| E711 | `== None` or `!= None`. | In a Polars expression use `pl.col("x").is_null()` or `.is_not_null()`; for a Python value use `is None`. |
| E712 | `== True` or `== False`. | In a Polars expression compare the column itself, or use `~`; for a Python value use `if x:` or `is True`. |
| E741 | A variable named `l`, `O` or `I`. | Rename it. |
| E722 | A bare `except:`. | Name the exceptions to catch, such as `except ValueError:`. |

To keep a line as it is, end it with `# noqa: ` and its code, such as `# noqa: F841`. To
change which problems ruff checks for the whole project, edit the `[tool.ruff]` table in
`pyproject.toml`. The project's CI runs the same check on every pull request, so a problem
left here also fails there.

If the message instead says ruff could not run, the hook could not start `uv run ruff`:
check that uv is installed and that `uv sync` succeeds in the project folder.

## Going back to a version

Choose **Restore this version** on any earlier save or milestone (the button on its row, or
right-click it). Your branch carries on from that version: Haute records its files as a new
save. The saves since then stay in the history, so restoring a later one undoes it. You stay
on the same branch, with nothing to reconnect.

`data/` and `outputs/` are left as they are ([Project Files](project-files.md)).

## Starting a branch from a version

To try something from an earlier version without changing your branch, choose **Start a
branch from this version**, name the branch, and it opens at that version.

Right-clicking a row also offers:

- **New branch from here, stay on this branch**, which makes the branch without opening it;
- **Move the later saves to a new branch**, on the saves since the last milestone and on the
  latest milestone, which puts the later saves on the new branch and winds this branch back
  to the version you chose.

## Viewing an earlier version

The toolbar reads **Viewing an earlier version** when the project was moved to an earlier
version outside Haute, for example with `git checkout` in a terminal. Click it to choose a
branch. Choosing an existing branch opens its latest save, so the version open now is kept
only by a new branch, which is what the chooser starts on.

## Archiving and deleting branches

The branch manager at the top of the Git panel archives and deletes branches. An archived
branch stays under **Archived** until you restore it.

A deleted branch goes to **Deleted**, which keeps the 20 most recent deletions. Restore
brings one back exactly as it was. If a branch has its name again, Restore asks for another
name. Deleting or archiving the branch you are on leaves its last milestone open, and the
branch chooser opens.

Switching, archiving and deleting a branch can also be undone from the toolbar's Undo until
the page reloads.

Earlier versions of Haute could leave a branch in two parts if the server stopped during
an archive or a delete. Haute names such a branch when it starts, and the branch
manager lists it with **Repair**. Repair finishes an archive that stopped part way. A
branch that went missing comes back at its last milestone, with its saves since then
waiting to be committed again.

## Files in use

On Windows, a version can't replace a file another program holds open, such as a CSV open
in Excel. The toolbar then reads **Files in use** and names the files. Close them and choose
**Retry**. Until then, committing a milestone and restoring a version are refused.
