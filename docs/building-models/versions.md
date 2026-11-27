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

## Sharing your work for review

**Push**, at the top of the Git panel, sends your branch and its saves to the remote you
choose, such as your team's repository on GitHub, GitLab or Azure DevOps. Nothing leaves
your computer until you press it. Then open a pull request from your branch on that site,
where your team reviews and merges it ([Before You Start](../deployment/before-you-start.md)
explains pull requests).

The line beside **Push** says how your branch stands against the remote: **synced**, or how
many milestones ahead or behind it is. If the remote has changes you don't, **Catch up**
brings them in when it can do so without a merge; when it cannot, Haute offers **Spin off a
copy**, which keeps your work on a branch beside the shared one.

## Starting your next change

Once your pull request has merged, start the next change from what the team's main branch
holds now. In the Git panel, type the new branch's name, open the arrow beside **Create**
and choose **Create from origin's default branch** (the menu names each remote you have).
Haute reads the remote, makes the branch there and opens it.

Start each change this way. A branch made with **Create** starts from the branch you are
on, which doesn't have what merged from your colleagues, and after a pull request that was
squashed it brings the old commits back, so the next pull request shows conflicts.

## Going back to a version

Choose **Restore this version** on any earlier save or milestone (the button on its row, or
right-click it). Your branch carries on from that version: Haute records its files as a new
save. The saves since then stay in the history, so restoring a later one undoes it. You stay
on the same branch, with nothing to reconnect.

The project root's `files/` is left as it is ([Project Files](project-files.md)).

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

## An action that stopped part-way

Restoring a version, switching branch, catching up and the other version actions each take
several steps: a restore records the version, then writes its files. If one stops before it
finishes, the toolbar reads **Unfinished**, and its tooltip names the action and its files:
"Restoring a version stopped part-way: rating/main.py." That happens when the server stops
in the middle of one, or on Windows when another program holds one of the files open, such
as a CSV open in Excel.

Close any of the files another program has open, then choose **Retry**. Haute finishes the
action, or puts everything back as it was before it. Until then, saving, committing a
milestone, restoring a version and switching branch are refused.

If you changed one of the files after the action stopped, Retry leaves it alone and names
it. Move your changes aside, then choose **Retry** again.

## When git refuses the repository

Sometimes git itself will not work in a project, for a reason outside Haute. The toolbar
reads **Git refused**, and its tooltip says why and what to do:

- **A folder git does not trust.** Git refuses a folder another account owns, as on a
  network share. The tooltip gives the command that trusts it, `git config --global --add
  safe.directory` and the folder. Run it, then reload the page.
- **Damaged settings.** A line of git's settings file, usually `.git/config`, cannot be
  read, or a setting that must be true or false is neither. The message names the line or
  the setting. Correct or remove it.

Saving still writes your work to disk. Its warning gives the same reason, and the files are
captured at the next save once git works again. Two more causes are named when a save or a
milestone meets them:

- **A lock file.** Another git program is using the repository, or one that stopped left its
  lock file behind. Wait for it to finish. If no git program is running, delete the file the
  message names, usually `.git/index.lock`.
- **Signing.** Git is set to sign every commit, and the signature could not be made. Check
  that your signing key is available and signs without asking for a passphrase.

## Signed commits

A signed commit carries a signature made with the key of the person who recorded it, so
anyone can check who made each save and milestone, and that no one has changed it since.
Where pricing work is audited, signing every commit makes version history a record you can
show. Git checks the signatures; a hosting service such as GitHub or GitLab shows each
commit as verified.

Haute signs when git is set to sign every commit: each save, milestone and restore, the first
milestone of a branch made from a save, and the saves Create & Move copies to a new branch.
It uses the key and the format git is set to use. Nothing is signed unless git is set to sign.

To sign with an SSH key, run in the project's folder:

```
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
git config commit.gpgsign true
```

Add `--global` to each to sign in every project. The key must sign without asking for a
passphrase while Haute runs: load it into your SSH agent, or use a key without one. A GPG key
works the same way, without the first line.

When a commit cannot be signed, nothing is recorded and Haute says so (see **Signing** above).
A mistyped `commit.gpgsign` is refused the same way, never read as "don't sign".
The saves Create & Move copies are signed by whoever moves them; the copies keep each save's
original author. Version labels are not signed; the milestone each one names is.

## Files changed outside Haute

A file you change in another program, such as a rate table saved in Excel, is on disk but not
in version history. The editor's **Save** saves what is on the canvas and in the form, so it
does not record that file. Before Haute switches branch, opens a new branch, catches up,
spins off a copy, or archives or deletes the branch you are on, it asks about such files and
names them. It also names a file you copied in where the other branch has one of that name.

- **Record** saves them as one save on the branch you are on, then goes on. The change stays
  with that branch.
- **Put back** returns them to the version history has, then goes on. A file version history
  does not have, such as one you copied in, is removed.

Recording before **Catch up** makes a save the shared branch does not have, so Catch up then
asks you to **Spin off a copy**, which keeps your save on a copy beside the shared line.

## Another tab or a terminal changes branch

When another tab switches the project's branch, or you check out a branch in a terminal, every
open tab follows: its toolbar shows the new branch without a reload. A project moved off its
working branch, as by `git checkout` in a terminal, opens the dialog that asks whether to go
back to your working branch or make the new one your working branch.

A save made from a tab before it has followed is refused, naming both branches. Nothing is
written, and your changes stay in the tab. Look at the branch the toolbar now shows, then save
again.
