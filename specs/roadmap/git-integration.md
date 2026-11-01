# Git integration roadmap

## Scope

The working-branch model on top of git: saves and milestones, restore, archive,
delete, catch-up, spin-off and push, the unborn-repo seed, the history views and
the remote handling, and the git panel that drives them. Current behaviour is
specified in [the git-integration specification](../git-integration/high-level.md)
and [the git UI](../frontend-git-ui/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GIT-R06 | Planned | P3 | Every save runs the project's git hooks, in a clone too, and the specification says so. |

## Planned improvements

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
