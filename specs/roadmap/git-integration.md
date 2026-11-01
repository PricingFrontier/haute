# Git integration roadmap

## Scope

The working-branch model on top of git: saves and milestones, restore, archive,
delete, catch-up, spin-off and push, the unborn-repo seed, the history views and
the remote handling, and the git panel that drives them. Current behaviour is
specified in [the git-integration specification](../git-integration/high-level.md)
and [the git UI](../frontend-git-ui/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).
`GIT-R18` comes from the [28 October functional review](codebase-review-2026-10-28.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GIT-R05 | Planned | P3 | The git modules carry no dead symbols, one free-name finder and one compensation helper. |
| GIT-R06 | Planned | P3 | Every save runs the project's git hooks, in a clone too, and the specification says so. |
| GIT-R18 | Planned | P2 | A rejected push's Catch up and Spin off actions stay bound to that rejection's remote. |
| GIT-R19 | Planned | P2 | `validate_remote_url` rejects `file://` URLs with a non-empty host and refuses `file://` bindings in hosted mode, and `set_working_branch` enforces `VERSIONED_FILE_LIMIT_BYTES` when seeding an unborn repository. |

## Planned improvements

### GIT-R05 — Dead symbols, one free-name finder, one compensation helper
**Why:** `PROTECTED_BRANCHES` is only re-exported (`DEFAULT_PROTECTED_BRANCHES`
is the real set), `fetch_pair` and `commit_parent` are called only by tests, and
the `pricing/<user>/` ownership convention (`_BRANCH_PREFIX`, `_is_own_branch`,
`_get_user_slug`, `_slugify`, the `is_yours` sort key) is produced by nothing in
the product, so `is_yours` is false for every branch Haute creates. Two
date-and-counter free-name finders exist (`_unique_archive_name`,
`_unique_aside_name`), and nine hand-rolled compensation blocks repeat
`restored &= ok` with two different exception filters.

**Plan:** Delete the dead symbols and either generate names under the
convention or drop `is_yours` and its helpers; one free-name helper taking the
prefix pattern; one `compensate(steps)` helper with one exception filter raising
`GitTransactionError` on an incomplete rollback.

**Acceptance:** The named symbols are gone or called from production; jscpd
reports no pair among the compensation blocks.

**Dependencies:** `ENGQ-R01` owns `_assert_not_protected`.

**Evidence:** `src/haute/_git_core.py`; `src/haute/_git_remote.py::fetch_pair`;
`src/haute/_git_core.py::commit_parent`;
`src/haute/_git_archive.py::_unique_archive_name`;
`src/haute/_git_remote.py::_unique_aside_name`.

### GIT-R06 — Hooks on every save
**Why:** Automatic ledger saves and the seed commit run the project's git hooks
(no `--no-verify`) while milestones bypass them (`commit-tree`), and the
specification says neither. The hook `haute init` scaffolds runs `uv run ruff
format`, `ruff check --fix` and `git add` on the staged Python, so what reaches
the remote passes the `ruff check` and `ruff format --check` jobs of the CI
`haute init` generates. Generated code is already a fixed point of `ruff
format`; hand-written Python such as a utility module is not, and it enters git
through a save (the sweep before a milestone). `haute init` installs the hook
into `.git/hooks`, which a clone does not copy, so a colleague's saves run no
hook.

**Decided (1 November 2026):** hooks run on every save: the ledger save, the
seed commit and the sweep before a milestone or restore. A milestone keeps
`commit-tree`; its tree is the ledger's, which the hooks have already seen. A
save that stages Python pays two `uv run` starts. A clone runs the same hook.

**Plan:**
- The git-integration specification states that a save runs the project's
  hooks and a milestone does not.
- When Haute opens a repository whose hooks directory has no `pre-commit` and
  the project tracks `.githooks/pre-commit`, it installs that hook as
  `haute init` does, executable. An existing hook, or a `core.hooksPath` the
  analyst set, is left alone: setting `core.hooksPath` would bypass the hooks
  the pre-commit framework installs in `.git/hooks`.
- Reproduce first: the pipeline save computes the source revision it returns
  before the ledger capture runs the hook, so a hook that rewrites the saved
  file (`ruff check --fix` removing an unused import) may leave the editor's
  next save refused as stale. If it does, the editor ends up holding the file
  as the capture left it.

**Acceptance:** The specification and `commit_save` agree. A clone of a project
`haute init` made runs the scaffolded hook on its first save. After a save whose
hook rewrites the pipeline file, the next save succeeds without a revision
conflict.

**Dependencies:** None.

**Evidence:** `src/haute/_git_transactions.py::commit_save`;
`src/haute/_git_transactions.py::commit_milestone`;
`src/haute/_scaffold.py::pre_commit_hook`;
`src/haute/cli/_init_cmd.py::handle_init`;
`src/haute/routes/_save_pipeline.py::SavePipelineService.save`.

### GIT-R18 — Recovery acts on the remote named by its dialog

**Why:** While Push to origin is pending, the remote select remains editable. Switching it to
backup before origin returns 409 produces a dialog naming origin, but its recovery callbacks
submit backup. Actual component tests reproduce both behind and diverged cases. The wrong
endpoint submission is measured; the resulting Git tree mutation is supported by the route
semantics, not executed by this UI probe.

**Plan:** Pass the rejection's authenticated remote into modal recovery actions; standalone
recovery uses its own captured target. Invalidate stale dialogs when their context is no longer
actionable and keep busy/selection rules consistent. Do not take a destructive target from a
mutable dropdown after confirmation.

**Acceptance:** Delay origin's 409, select backup, then invoke each modal action: only origin
is submitted or the stale dialog is explicitly refused. The dialog and action always name the
same remote. Existing dirty-document and branch protections still apply.

**Dependencies:** None. Catch up and Spin off ask about unsaved edits first, naming the remote
the action submits, so that question must follow whichever remote this package binds the
actions to.

**Evidence:** `frontend/src/components/RemotePushControl.tsx::RemotePushControl.doCatchUp`;
`frontend/src/components/RemotePushControl.tsx::RemotePushControl.doBranchAway`;
`frontend/src/components/RemotePushControl.tsx::RemotePushControl` (remote selector);
`frontend/src/components/RemotePushControl.tsx::RemotePushControl` (`RejectPushDialog` callback props).

### GIT-R19 — Remote `file://` host/hosted-mode validation and unborn-repository file-size guard
**Why:** Two validation gaps exist in git remote binding and unborn-repository seeding:
1. `validate_remote_url` in `_project_storage.py` accepts any `file://` URL with a
   non-empty path, including `file://host/share/repo.git` (non-empty `host`, which on
   Windows resolves to a remote SMB/UNC share) and local `file:///...` paths in
   hosted mode (`is_hosted_mode()`), allowing a hosted workspace binding to clone or
   fetch from the container's local filesystem or an SMB host.
2. When `set_working_branch` seeds an unborn repository (`_head_oid(root) is None`),
   it stages the allowlisted `seed_files` (`_SEED_PATHSPECS`) and commits the initial
   seed without checking those eligible seed files against `_refuse_unversionable`
   (`VERSIONED_FILE_LIMIT_BYTES`), whereas every subsequent `commit_save` enforces the
   50 MB per-file limit.

**Plan:** Require `parsed.hostname in (None, "", "localhost")` for `file://` URLs and
refuse `file://` remote URLs when `is_hosted_mode()` is true in
`validate_remote_url`; check the eligible `seed_files` (`_SEED_PATHSPECS`) against
`VERSIONED_FILE_LIMIT_BYTES` (`_refuse_unversionable`) before committing the initial
seed in `set_working_branch` (leaving ignored or unversioned files outside
`seed_files` unaffected).

**Acceptance:** `validate_remote_url("file://server/share/repo.git")` raises
`HauteError`; `validate_remote_url("file:///tmp/repo.git")` raises `HauteError` when
`is_hosted_mode()` is true; and `set_working_branch` on an unborn repository containing
an eligible seed candidate above `VERSIONED_FILE_LIMIT_BYTES` refuses the seed naming
the oversized file before creating a commit, while ignoring oversized files outside
`_SEED_PATHSPECS`.

**Dependencies:** None.

**Evidence:** `src/haute/_project_storage.py::validate_remote_url`;
`src/haute/_git_setup.py::set_working_branch`;
`src/haute/_git_setup.py::_refuse_implausible_seed`;
`src/haute/_git_transactions.py::_refuse_unversionable`.
