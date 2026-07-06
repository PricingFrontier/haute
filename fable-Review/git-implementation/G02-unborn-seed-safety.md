# G02 — Unborn-repo seeding: `git add -A` sweeps state, credentials, and data into the root commit

**Severity: HIGH · Confidence: CONFIRMED (reproduced end-to-end for the `.haute/` leg) · Class: data-safety / permanent UX lock-out**
**Files: `src/haute/_git.py` (`set_working_branch` create path), `src/haute/cli/_init_cmd.py` (ignore scaffold)**
**Origin: R-4 (routes reviewer, reproduced in a scratch repo), engine reviewer's candidate-4 note, T-8 (tests reviewer). The `.gitignore` scaffold behaviour verified directly at `_init_cmd.py:653`.**

## The seeding path

When the first working branch is created in a repo with no commits (unborn HEAD),
`set_working_branch(create=True)` plants a root commit with:

```
_git.py:1030   _run_git("add", "-A", cwd=cwd)
_git.py:1036   _run_git("commit", …, "-m", "Initial commit", cwd=cwd)
```

`git add -A` stages **everything not ignored**. What is ignored depends entirely on which flow the
user took:

- `haute init` writes/extends `.gitignore` with `.env`, `.haute/`, `.haute_cache/`, `mlruns/`,
  `data/` (`_init_cmd.py:653` for existing files, `:664` for fresh ones). Fine.
- But `haute init` **never runs `git init`** — and the engine's own error message tells users to do
  it themselves: `"Not a git repository. Run 'git init' first."` (`_git.py:395`). `haute serve`
  runs in **any** git repo. A repo initialised by hand has no scaffolded `.gitignore`.

## Finding R-4 — `.haute/` gets committed → permanent "You have unsaved changes" lock-out (reproduced)

If haute was launched before the seed (writing `.haute/prefs.json` — e.g. the user toggled the
switch-confirm pref, or `.haute/forks.json` exists from a prior attempt), `git add -A` commits it:

```
tracked files in initial commit:
.haute/prefs.json      ← swept in by `git add -A`
pipeline.py
```

`state.json` itself escapes only by accident of ordering (it is written *after* the commit).
From then on, **every pref write dirties a tracked file**, and the shared dirty-gate
(`move_to_commit` `_git.py:1187`, `fast_forward_pair` `:2236`, `branch_away` `:2356`,
archive/delete `:2460`/`:2509`) refuses with **"You have unsaved changes. Save or discard them…"**
— for a file the user cannot see in the editor and never edited. The lock-out is permanent until
someone runs `git rm --cached` by hand, which is precisely the audience the panel promises never
needs the CLI.

## Finding (adjacent, same fix) — `.env` and datasets in hand-initialised repos

In the same no-`.gitignore` flow, the seed commits **`.env` (credentials) and any data files**
(`data/`, `*.parquet`, `*.csv` — pricing projects routinely carry hundreds of MB). A later
`push_working_pair` publishes them to the org remote. Secrets in git history are effectively
unremovable without a rewrite — which haute (correctly) never does (S33).

## Finding (engine reviewer) — `output/`/`outputs/` inconsistency

`_VOLATILE_ARTEFACTS` (`_git.py:1109-1119`) treats `output/`, `outputs/`, `.cache/`,
`.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` as reconstructable and wipes them on
move/ff/branch-away — but the init scaffold does **not** gitignore them (`_init_cmd.py:653`). An
`outputs/` tree present at seed time gets committed, then a later move **deletes the committed
copy from the working tree** via the volatile wipe → the dirty-gate sees deletions of tracked
files → same lock-out class as R-4.

## Fix design

Defence in depth — all three legs:

1. **Scope the seed add.** Replace `git add -A` with an exclude-pathspec add:
   `git add -A -- . ':(exclude).haute' ':(exclude).env' ':(exclude).haute_cache' ':(exclude)mlruns' ':(exclude)data' ':(exclude)output' ':(exclude)outputs'`
   (single source of truth: build the exclude list from one module-level constant shared with
   `_VOLATILE_ARTEFACTS` and the init scaffold list).
2. **Seed the ignore file when absent.** In the unborn path, if no `.gitignore` exists, write the
   same scaffold `haute init` would (reuse the `haute_entries` constant — extract it from
   `_init_cmd.py` into a shared module so the two cannot drift), *before* the add, and include it
   in the root commit. This also protects every later save (`commit_save` is pathspec-scoped, so
   it is safe today — the seed is the only `add -A`).
3. **Align the init scaffold.** Add `output/` and `outputs/` to `haute_entries` so init-created
   projects are consistent with what the engine wipes.

Fail-loud posture: do **not** silently skip `.env` while claiming a full snapshot — log
`seed_commit_scoped` with the excluded entries, and surface the created `.gitignore` in the
response message if one was written.

## TDD plan

1. `test_unborn_seed_excludes_state_dir` (regression for the reproduced trap) — unborn repo, no
   `.gitignore`, pre-existing `.haute/prefs.json`; run `set_working_branch(create=True)`; assert
   `.haute/prefs.json` **not** in `git ls-files`; toggle a pref; assert `move_to_commit` to the
   root commit is not blocked by the dirty-gate.
2. `test_unborn_seed_excludes_env_and_data` — unborn repo containing `.env` and `data/big.csv`;
   assert neither is tracked after the seed and both still exist on disk.
3. `test_unborn_seed_writes_gitignore_when_absent` — assert the seeded `.gitignore` is tracked and
   contains the haute entries.
4. `test_unborn_seed_respects_existing_gitignore` — a repo with its own `.gitignore` keeps it (no
   clobber, entries appended or left per design choice — match `haute init`'s append semantics).
5. `test_init_scaffold_covers_volatile_artefacts` — every directory in `_VOLATILE_ARTEFACTS` that
   the wipe deletes is present in the shared ignore constant.
6. Keep/extend the existing `TestSetWorkingBranchUnborn` matrix (rename-to-main, rollback,
   identity-unset) — do not regress it.

## Notes for the implementer

- The reproduced `.haute/` leg is the must-fix; the `.env`/data leg is the reason this is HIGH
  (publishing credentials via a later push).
- `commit_save` needs no change — it commits explicit pathspecs only (`_git.py:747-750`, cleared).
