# Versions

Haute keeps your project's history as saves and milestones on a branch. Click the branch
name in the toolbar to open the Git panel, which shows that history.

## Saves and milestones

Every save (Ctrl+S) is a save on your working branch. **Commit a milestone** gathers the
saves since the last milestone under a message and, if you like, a version label.

The panel lists the saves since the last milestone in a highlighted box, then the
milestones. Opening a milestone shows the saves in it. Each row's commit code is a short ID
for that save or milestone.

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

## Files in use

On Windows, a version can't replace a file another program holds open, such as a CSV open
in Excel. The toolbar then reads **Files in use** and names the files. Close them and choose
**Retry**. Until then, committing a milestone and restoring a version are refused.
