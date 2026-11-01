"""Atomic working/ledger branch-pair saves, milestones, version restores, and forks."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._git_core import (
    GitDomainError,
    GitError,
    GitFilesInUseError,
    GitGuardrailError,
    GitMilestoneForkError,
    GitTransactionError,
    _assert_eligible_working,
    _assert_git_repo,
    _assert_new_branch_name,
    _assert_no_git_op_in_progress,
    _canonical_remote,
    _commit_parents,
    _generate_commit_message,
    _get_current_branch,
    _held_files,
    _is_ancestor,
    _is_valid_full_ref_name,
    _ledger_or_branch_sha,
    _merge_base,
    _refuse_too_large_for_history,
    _remote_names,
    _rev_parse,
    _rewrite_tree,
    _run_git,
    _run_git_capture,
    _run_git_ok,
    _serialized_mutation,
    _tracked_changes,
    _tree_of,
    _validate_ref_name,
    _wipe_volatile_artefacts,
    divergence_state,
    ledger_name,
)
from haute._git_setup import (
    _crystallize_milestone,
    _folding_anchor,
    _on_first_parent,
    get_identity,
    resolve_ledger,
    set_working_branch,
)
from haute._logging import get_logger
from haute._project_layout import versioned_name
from haute.schemas import (
    GitCommitResponse,
    GitCreateWorkingBranchResponse,
    GitMilestoneFork,
    GitRestoreVersionResponse,
)

logger = get_logger(component="git")

__all__ = [
    "commit_save",
    "check_invariants",
    "merge_to_working",
    "create_working_branch",
    "commit_milestone",
    "restore_version",
    "retry_interrupted",
]

# How many of the files in a restore's way its refusal names.
_IN_THE_WAY_NAMED = 5


@_serialized_mutation
def commit_save(
    paths: list[str], working: str, cwd: Path | None = None, message: str | None = None
) -> str | None:
    """Record one save as one commit on the ledger of *working*.

    Pathspec-scoped: only *paths* enter the commit, regardless of any content
    the user may have staged in the meantime. Returns the new commit SHA, or
    None when none of *paths* changed (idempotent saves produce no empty
    commits).
    """
    if not paths:
        return None
    # A pathspec commit fails during a merge with git's own words; say why first.
    _assert_no_git_op_in_progress(cwd)

    ledger = resolve_ledger(working, cwd=cwd)

    # :(literal) so a filename holding glob characters names only itself.
    pathspecs = [_literal(path) for path in paths]
    if not _run_git("status", "--porcelain", "--", *pathspecs, cwd=cwd):
        return None

    # New files must be known to git before a pathspec'd commit can include
    # them; the explicit-path add also stages deletions of tracked paths.
    _run_git("add", "--", *pathspecs, cwd=cwd)
    # A stale index entry can make porcelain report ``MM`` even when the
    # working-tree content already matches HEAD. The add above reconciles that
    # entry; if no net path change remains, there is nothing to commit. The
    # staged name list (not porcelain status, whose fixed-width prefix can
    # begin with a space) is also what names the files in the message.
    staged = _run_git(
        "diff", "--cached", "--name-only", "--no-renames", "HEAD", "--", *pathspecs, cwd=cwd
    )
    changed = staged.splitlines()
    if not changed:
        return None
    msg = message if message is not None else _generate_commit_message(changed)
    # `git commit -- <paths>` commits the working-tree state of exactly those
    # paths, bypassing unrelated index content the user may have pre-staged.
    _run_git("commit", "-m", msg, "--", *pathspecs, cwd=cwd)

    sha = _run_git("rev-parse", "HEAD", cwd=cwd).strip()
    logger.info("save_committed", ledger=ledger, sha=sha, files=len(changed))
    return sha


def _literal(path: str) -> str:
    return f":(literal){path}"


# Never versioned from the pipeline folder, whatever the project's .gitignore says.
def _pipeline_folder_new_files(pipeline_folder: str | None, cwd: Path | None = None) -> list[str]:
    """New files under *pipeline_folder*, which is versioned as a whole: untracked ones
    ``.gitignore`` does not ignore, and ones staged but never committed (``git add`` by
    hand, or a save whose commit failed after its add).

    Only names the Files panels show are swept (``versioned_name``): dot-names, the
    deploy's build folder among them, ``__pycache__`` and bytecode stay out even where
    ``.gitignore`` lacks them.
    """
    if pipeline_folder is None:
        return []
    untracked = _run_git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        _literal(pipeline_folder),
        cwd=cwd,
    )
    staged = _run_git(
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=A",
        "-z",
        "HEAD",
        "--",
        _literal(pipeline_folder),
        cwd=cwd,
    )
    return [
        path
        for path in dict.fromkeys(untracked.split("\0") + staged.split("\0"))
        if path and versioned_name(path)
    ]


def _refuse_unversionable(paths: list[str], pipeline_folder: str, project_root: Path) -> None:
    """Refuse a sweep holding a pipeline-folder file too large for version history."""
    folder = PurePosixPath(pipeline_folder)
    # not contained_path: git's repository-relative names, compared lexically
    in_folder = [path for path in paths if PurePosixPath(path).is_relative_to(folder)]
    _refuse_too_large_for_history(in_folder, project_root, "commit again")


def _residual_tracked_changes(cwd: Path | None = None) -> list[str]:
    """Return tracked paths whose index or working content differs from HEAD.

    Milestone commits represent the whole tracked project state, not only the
    files last generated by the canvas save. Untracked/staged-new files are
    deliberately excluded so an unrelated local file cannot silently enter
    history. Both comparisons are needed: the HEAD comparison finds real net
    changes, while the index comparison also finds stale ``MM`` entries whose
    working content has returned to HEAD and merely need reconciliation.
    """

    def parse_name_status(raw: str) -> list[str]:
        tokens = raw.split("\0")
        paths: list[str] = []
        i = 0
        while i < len(tokens) and tokens[i]:
            status = tokens[i]
            i += 1
            code = status[0]
            if code in ("R", "C"):
                if i + 1 >= len(tokens):
                    raise GitError("Malformed git name-status output.")
                old_path, new_path = tokens[i], tokens[i + 1]
                i += 2
                paths.extend((old_path, new_path))
            else:
                if i >= len(tokens):
                    raise GitError("Malformed git name-status output.")
                path = tokens[i]
                i += 1
                # A means newly staged/untracked. Save & commit captures only
                # files that were already tracked by the project.
                if code in ("M", "D", "T"):
                    paths.append(path)
        return paths

    net = _run_git("diff", "--name-status", "-z", "-M", "HEAD", "--", cwd=cwd)
    staged = _run_git("diff", "--cached", "--name-status", "-z", "-M", "HEAD", "--", cwd=cwd)
    return list(dict.fromkeys(parse_name_status(net) + parse_name_status(staged)))


def _unrecorded_project_files(
    project_root: Path, pipeline_folder: str | None, cwd: Path | None = None
) -> list[str]:
    """What is on disk but in no save yet, for one sweep save before a milestone or
    a restore: tracked files changed since HEAD, and new files in the pipeline
    folder, which is versioned as a whole. A pipeline-folder file too large for
    version history refuses the sweep before anything is recorded."""
    paths = list(
        dict.fromkeys(
            _residual_tracked_changes(cwd) + _pipeline_folder_new_files(pipeline_folder, cwd)
        )
    )
    if paths and pipeline_folder is not None:
        _refuse_unversionable(paths, pipeline_folder, project_root)
    return paths


def check_invariants(working: str, cwd: Path | None = None) -> list[str]:
    """Cheap plumbing checks of the branch-pair healthy-state invariant.

    Returns a list of human-readable violations (empty == healthy). Used at
    open and before every milestone merge.
    """
    _assert_git_repo(cwd)
    violations: list[str] = []

    working_tip = _rev_parse(working, cwd=cwd)
    if working_tip is None:
        return [f"working branch '{working}' does not exist"]

    ledger = ledger_name(working)
    ledger_tip = _rev_parse(ledger, cwd=cwd)
    if ledger_tip is None:
        return []  # pre-spawn: nothing to check

    base = _merge_base(working_tip, ledger_tip, cwd=cwd)
    if base is None:
        violations.append(f"'{working}' and '{ledger}' share no history")
        return violations

    if not _is_ancestor(base, ledger_tip, cwd=cwd):
        violations.append(f"merge-base of '{working}' and '{ledger}' is not on the ledger")

    if _tree_of(working_tip, cwd=cwd) != _tree_of(base, cwd=cwd):
        violations.append(
            f"'{working}' tip tree differs from its last-merged ledger commit — "
            "the working branch was advanced outside haute"
        )

    return violations


@_serialized_mutation
def merge_to_working(
    working: str,
    message: str,
    tag_label: str | None = None,
    cwd: Path | None = None,
) -> str:
    """Milestone merge: ledger → working, always a real merge commit.

    Produced with plumbing (``commit-tree`` + ``update-ref``) — no checkout,
    no index, and by construction never a fast-forward. The user-supplied
    *message* rides the merge commit itself. Returns the milestone SHA.
    """
    _assert_git_repo(cwd)
    _validate_ref_name(working)
    _assert_eligible_working(working)

    ledger = ledger_name(working)
    working_tip = _rev_parse(working, cwd=cwd)
    ledger_tip = _rev_parse(ledger, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    if ledger_tip is None:
        raise GitDomainError(f"'{working}' has no save ledger yet — nothing to commit.")

    if not message.strip():
        raise GitDomainError("A commit message is required.")
    # Reject C0 control characters (except tab/newline/CR, which are valid in a
    # multi-line message). A stray record-separator etc. would otherwise corrupt
    # the ledger-history parser, which delimits commits with \x1e.
    if any((ord(c) < 0x20 and c not in "\t\n\r") or ord(c) == 0x7F for c in message):
        raise GitDomainError("Commit message must not contain control characters.")

    tag_ref: str | None = None
    if tag_label is not None:
        _validate_ref_name(tag_label)
        tag_ref = f"version/{tag_label}"
        if not _is_valid_full_ref_name(f"refs/tags/{tag_ref}"):
            raise GitDomainError(f"Version label {tag_label!r} is not a valid Git tag name.")
        ok, _ = _run_git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{tag_ref}", cwd=cwd)
        if ok:
            raise GitDomainError(f"Version label '{tag_label}' already exists.")

    violations = check_invariants(working, cwd=cwd)
    if violations:
        raise GitDomainError(
            "Cannot commit to the working branch: " + "; ".join(violations) + ". "
            "Use the branch manager to start a fresh branch from your current state."
        )

    base = _merge_base(working_tip, ledger_tip, cwd=cwd)
    if base == ledger_tip:
        raise GitDomainError("No new saves to commit — the working branch is up to date.")

    tree = _tree_of(ledger_tip, cwd=cwd)
    sha = _run_git(
        "commit-tree", tree, "-p", working_tip, "-p", ledger_tip, "-m", message, cwd=cwd
    ).strip()
    if tag_ref is None:
        _run_git("update-ref", f"refs/heads/{working}", sha, working_tip, cwd=cwd)
    else:
        # Build the annotated tag object without publishing its ref, then update
        # the branch CAS and create the label in one ref transaction. If either
        # precondition loses a race, Git applies neither update.
        tagger = _run_git("var", "GIT_COMMITTER_IDENT", cwd=cwd)
        tag_object = _run_git(
            "hash-object",
            "--literally",
            "-t",
            "tag",
            "-w",
            "--stdin",
            cwd=cwd,
            input_text=(
                f"object {sha}\ntype commit\ntag {tag_ref}\ntagger {tagger}\n\n{tag_label}\n"
            ),
        )
        _run_git(
            "update-ref",
            "--stdin",
            cwd=cwd,
            input_text=(
                f"update refs/heads/{working} {sha} {working_tip}\n"
                f"create refs/tags/{tag_ref} {tag_object}\n"
            ),
        )

    logger.info("milestone_merged", working=working, sha=sha, tag=tag_label or "", ledger=ledger)
    return sha


def _commits_in_range(start: str, end: str, cwd: Path | None = None) -> list[str]:
    """SHAs in ``start..end``, oldest-first (ready to replay onto a new base)."""
    ok, raw = _run_git_ok("rev-list", "--reverse", f"{start}..{end}", cwd=cwd)
    return raw.split() if ok and raw.strip() else []


def _replay_onto(base: str, commits: list[str], cwd: Path | None = None) -> str:
    """Replay each commit (its tree + message) onto *base* via plumbing, linear,
    returning the new tip. Trees apply cleanly because the commits already formed
    a linear chain whose root has *base*'s tree. Author + committer identity and
    dates are preserved (relocated saves keep their provenance + timeline, S38).
    Empty *commits* returns base."""
    tip = base
    for c in commits:
        if len(_commit_parents(c, cwd=cwd)) != 1:
            raise GitDomainError(
                "Create & Move cannot relocate history containing a merge commit. "
                "Create a parallel branch instead so the merge topology is preserved."
            )
        tree = _tree_of(c, cwd=cwd)
        msg = _run_git("log", "-1", "--format=%B", c, cwd=cwd)
        # \x1f (unit separator) can't appear in identity/date fields.
        an, ae, ad, cn, ce, cd = _run_git(
            "log", "-1", "--format=%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI", c, cwd=cwd
        ).split("\x1f")
        env = {
            "GIT_AUTHOR_NAME": an,
            "GIT_AUTHOR_EMAIL": ae,
            "GIT_AUTHOR_DATE": ad,
            "GIT_COMMITTER_NAME": cn,
            "GIT_COMMITTER_EMAIL": ce,
            "GIT_COMMITTER_DATE": cd,
        }
        tip = _run_git("commit-tree", tree, "-p", tip, "-m", msg, cwd=cwd, env=env)
    return tip


def _rollback_fork(name: str, ledger: str, ledger_tip: str, cwd: Path | None = None) -> bool:
    """Undo a partially-applied fork and report whether every step succeeded."""
    succeeded = True
    new_ledger = ledger_name(name)
    if _get_current_branch(cwd) == new_ledger:
        # The spawning ledger isn't checked out → safe to restore it, then move
        # HEAD back so the new ledger can be deleted.
        ok, _ = _run_git_ok("branch", "-f", ledger, ledger_tip, cwd=cwd)
        succeeded &= ok
        ok, _ = _run_git_ok("checkout", ledger, cwd=cwd)
        succeeded &= ok
    if _rev_parse(new_ledger, cwd=cwd) is not None:
        ok, _ = _run_git_ok("branch", "-D", new_ledger, cwd=cwd)
        succeeded &= ok
    if _rev_parse(name, cwd=cwd) is not None:
        ok, _ = _run_git_ok("branch", "-D", name, cwd=cwd)
        succeeded &= ok
    return succeeded


@_serialized_mutation
def create_working_branch(
    name: str,
    project_root: Path,
    at: str | None = None,
    move: bool = False,
    cwd: Path | None = None,
    switch: bool = False,
) -> GitCreateWorkingBranchResponse:
    """Create a new working branch as a fork of the current one (P5d/S38).

    Fork point: ``at=None`` → the current branch's latest milestone; ``at=<sha>``
    → that milestone, or a pending save or a save folded into a milestone (either
    crystallized into an anchoring milestone). ``move=False`` (default) spins off
    a parallel line and leaves the current branch and your in-progress work
    untouched — you stay put; ``switch=True`` then opens the new branch, a
    checkout of its ledger that leaves the current pair's refs as they were.
    ``move=True`` relocates the work after the fork point (unmilestoned saves +
    uncommitted edits) onto the new branch, rewinds the current branch's ledger
    to the fork point, and switches you over. Move is valid only at the latest
    milestone or a pending save, and not with ``switch``, which it implies.
    """
    from haute._git_state import read_working_branch, write_working_branch

    _assert_git_repo(cwd)
    _assert_new_branch_name(name, cwd=cwd)
    _validate_ref_name(name)
    _assert_eligible_working(name)
    if move and switch:
        raise GitDomainError("Create & Move already opens the new branch; choose one or the other.")

    current = read_working_branch(project_root)
    if current is None:
        # No working branch yet — an adopt-create off real HEAD; no fork model.
        if at is not None or move or switch:
            raise GitDomainError("No working branch to fork from yet.")
        res = set_working_branch(name, project_root, create=True, cwd=cwd)
        return GitCreateWorkingBranchResponse(
            working_branch=name,
            moved=False,
            switched=True,
            last_save_sha=res.last_save_sha,
        )

    if _rev_parse(name, cwd=cwd) is not None:
        raise GitDomainError(f"Branch '{name}' already exists.")
    if _rev_parse(ledger_name(name), cwd=cwd) is not None:
        raise GitDomainError(f"A branch named '{ledger_name(name)}' already exists.")

    working_tip = _rev_parse(current, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Working branch '{current}' does not exist.")
    ledger = ledger_name(current)
    ledger_tip = _rev_parse(ledger, cwd=cwd) or working_tip

    if at is not None:
        _validate_ref_name(at)  # same guard every other user-supplied ref gets
    point = working_tip if at is None else _rev_parse(at, cwd=cwd)
    if point is None:
        raise GitDomainError(f"Commit '{at}' does not exist.")

    is_milestone = _on_first_parent(current, point, cwd=cwd)
    in_milestones = not is_milestone and _is_ancestor(point, working_tip, cwd=cwd)
    is_pending = not is_milestone and not in_milestones and _is_ancestor(point, ledger_tip, cwd=cwd)
    if not (is_milestone or in_milestones or is_pending):
        raise GitDomainError(
            "You can only branch from a milestone or a save on the current branch."
        )
    if move and in_milestones:
        raise GitDomainError(
            "Create & Move is only available at the latest milestone or a "
            "pending save — older saves can only start a parallel line."
        )

    if switch:
        # Opening the new branch replaces the files: what is on disk must be saved.
        _assert_no_git_op_in_progress(cwd)
        ok_status, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
        if ok_status and status.strip():
            raise GitDomainError(
                "You have unsaved changes. Save or discard them before opening the new branch."
            )

    if is_milestone:
        base = point
    else:
        anchor = working_tip if is_pending else _folding_anchor(working_tip, point, cwd=cwd)
        base = _crystallize_milestone(anchor, point, name, cwd=cwd)

    if not move:
        # Two fresh refs at the base; with *switch* the new ledger is then checked
        # out, the current pair's refs untouched either way.
        try:
            _run_git("branch", name, base, cwd=cwd)
            _run_git("branch", ledger_name(name), base, cwd=cwd)
            if switch:
                _rewrite_tree("checkout", ledger_name(name), cwd=cwd)
                write_working_branch(project_root, name)
        except Exception as exc:
            refs_restored = _rollback_fork(name, ledger, ledger_tip, cwd=cwd)
            try:
                write_working_branch(project_root, current)
                state_restored = True
            except OSError:
                state_restored = False
            if not refs_restored or not state_restored:
                raise GitTransactionError(
                    "Creating the branch failed and automatic rollback was incomplete. "
                    "Inspect the repository before retrying."
                ) from exc
            raise
        logger.info("working_branch_forked", name=name, at=point[:8], moved=False, switched=switch)
        return GitCreateWorkingBranchResponse(
            working_branch=name,
            moved=False,
            switched=switch,
            last_save_sha=_ledger_or_branch_sha(name, cwd=cwd),
        )

    # Move: only at the latest milestone or a pending save.
    if is_milestone and point != working_tip:
        raise GitDomainError(
            "Create & Move is only available at the latest milestone or a "
            "pending save — older milestones can only spin off a parallel line."
        )
    # M5 safety: move-mode rewinds the spawning branch's ledger to the fork
    # point (the ``branch -f`` below). If that ledger is already published, the
    # rewind drops commits the remote still has, leaving the source pair
    # un-pushable (non-fast-forward) — and S33 forbids the force-push that would
    # fix it. Refuse and steer to a parallel fork, which rewinds nothing. Only
    # refuse when the rewind genuinely orphans published commits (the remote
    # ledger is not an ancestor of the fork point); a tip-fork stays frictionless.
    for remote in _remote_names(cwd=cwd):
        remote_ledger = _rev_parse(f"refs/remotes/{remote}/{ledger}", cwd=cwd)
        if remote_ledger is not None and not _is_ancestor(remote_ledger, point, cwd=cwd):
            raise GitGuardrailError(
                "This branch's save history is published, and moving from here "
                "would rewind it past the shared copy. Spin off a parallel line "
                "instead — it leaves this branch untouched."
            )
    # The new ledger carries the saves after the fork point. At the latest
    # milestone the pending chain already sits on the base, so reuse it; at a
    # pending save, replay the later saves onto the crystallized milestone.
    if is_milestone:
        new_ledger_tip = ledger_tip
    else:
        new_ledger_tip = _replay_onto(base, _commits_in_range(point, ledger_tip, cwd=cwd), cwd=cwd)

    # The mutations below are not individually atomic; on any failure roll the
    # whole fork back so the user isn't wedged behind the "already exists" guard
    # with work duplicated across two lineages (S38).
    try:
        _run_git("branch", name, base, cwd=cwd)
        _run_git("branch", ledger_name(name), new_ledger_tip, cwd=cwd)
        # Switch onto the new ledger. The new ledger tip shares the old HEAD's
        # tree, so uncommitted edits carry across the checkout untouched.
        _rewrite_tree("checkout", ledger_name(name), cwd=cwd)
        # Rewind the spawning branch's ledger to the fork point (its later work
        # has been relocated; the commits stay reachable via the new ledger).
        _run_git("branch", "-f", ledger, point, cwd=cwd)
        write_working_branch(project_root, name)
    except Exception as exc:
        refs_restored = _rollback_fork(name, ledger, ledger_tip, cwd=cwd)
        try:
            write_working_branch(project_root, current)
            state_restored = True
        except OSError:
            state_restored = False
        if not refs_restored or not state_restored:
            raise GitTransactionError(
                "Creating and moving to the branch failed and automatic rollback was "
                "incomplete. Inspect the repository before retrying."
            ) from exc
        raise
    logger.info("working_branch_forked", name=name, at=point[:8], moved=True)
    return GitCreateWorkingBranchResponse(
        working_branch=name,
        moved=True,
        switched=True,
        last_save_sha=_ledger_or_branch_sha(name, cwd=cwd),
    )


@_serialized_mutation
def commit_milestone(
    message: str,
    project_root: Path,
    version_label: str | None = None,
    cwd: Path | None = None,
    allow_fork: bool = False,
    pipeline_folder: str | None = None,
) -> GitCommitResponse:
    """Promote the ledger's accumulated saves to a milestone on the working
    branch (a real `--no-ff`-shaped merge commit via plumbing), with the
    user's *message* and an optional version-label tag (S7/S18).

    *pipeline_folder* (relative to *project_root*, POSIX) is versioned as a whole:
    its new files join the milestone's sweep, and one too large for version
    history refuses the milestone before anything is recorded.

    Fork-gate (U4/D4): if the working branch is behind/diverged from its canonical
    remote on locally-known refs, a milestone would branch off the shared copy —
    refuse with :class:`GitMilestoneForkError` so the UI can warn, unless
    *allow_fork* is the user's deliberate override. The check is local-only (no
    fetch): the milestone stays instant and a local-only/offline user is never
    blocked (no remote, or an untracked/unknown leg, degrades open)."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    _assert_not_interrupted(project_root)
    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this project.")
    _assert_no_git_op_in_progress(cwd)

    if not allow_fork:
        leg = divergence_state(working, cwd=cwd)
        if leg is not None and leg.status in ("behind", "diverged"):
            remote = _canonical_remote(cwd) or "the remote"
            behind = leg.behind or 0
            message_text = (
                f"Saving a milestone now will fork '{remote}' — it has {behind} newer "
                f"milestone{'' if behind == 1 else 's'} on this branch that you don't "
                "have yet, so your version would branch off the shared one instead of "
                "building on it. Your work is safe — commit anyway to create a fork, "
                "or catch up first."
            )
            raise GitMilestoneForkError(
                GitMilestoneFork(remote=remote, working=leg, message=message_text)
            )

    # The canvas save immediately preceding this action captures Haute-owned
    # generated files. A project can also have changes to already-tracked files
    # made by dependency tooling or a code editor (uv.lock is a common example),
    # and new files in the pipeline folder, which is versioned as a whole. Fold
    # those into the ledger before creating the milestone so an explicit "Save &
    # commit" leaves the project clean. A new file anywhere else stays untouched
    # and cannot ride into history implicitly.
    residual = _unrecorded_project_files(project_root, pipeline_folder, cwd)
    if residual:
        commit_save(residual, working, cwd=cwd, message="Updated project files")

    sha = merge_to_working(working, message, tag_label=version_label, cwd=cwd)
    logger.info("milestone_committed", working=working, sha=sha, tag=version_label or "")
    return GitCommitResponse(
        sha=sha,
        short_sha=sha[:8],
        working_branch=working,
        version_label=version_label,
    )


@_serialized_mutation
def restore_version(
    sha: str,
    project_root: Path,
    cwd: Path | None = None,
    pipeline_folder: str | None = None,
) -> GitRestoreVersionResponse:
    """Write the version at *sha* back as one new save on the current branch.

    History first, files second. Everything on disk is recorded by the
    milestone's sweep, so nothing the restore replaces is lost; the restore save
    is built from *sha*'s tree with plumbing (one parent, the ledger tip) and the
    ledger advanced by compare-and-swap; only then are the files written. A
    failure writing them leaves the restore in history. No other ref moves and
    the branch is not left, so every later save stays reachable and restoring
    the save before undoes it.

    *sha* must be in the current branch's history: a milestone or a save folded
    into one (reachable from the working tip) or a pending or earlier save
    (reachable from the ledger tip). *pipeline_folder* is as for
    :func:`commit_milestone`."""
    from haute._git_state import read_working_branch, record_interrupted

    _assert_git_repo(cwd)
    _assert_not_interrupted(project_root)
    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this project.")
    _assert_no_git_op_in_progress(cwd)
    name, email = get_identity(cwd)
    if name is None or email is None:
        raise GitDomainError(
            "Restoring a version needs a git identity. Set your name and email first."
        )
    _validate_ref_name(sha)
    ledger = resolve_ledger(working, cwd=cwd)

    target = _rev_parse(sha, cwd=cwd)
    if target is None:
        raise GitDomainError(f"No commit found for {sha!r}.")
    working_tip = _rev_parse(working, cwd=cwd)
    ledger_tip = _rev_parse(ledger, cwd=cwd)
    if working_tip is None or ledger_tip is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    if not (
        _is_ancestor(target, working_tip, cwd=cwd) or _is_ancestor(target, ledger_tip, cwd=cwd)
    ):
        raise GitDomainError("You can only restore a version from this branch's history.")

    unrecorded = _unrecorded_project_files(project_root, pipeline_folder, cwd)
    target_tree = _tree_of(target, cwd=cwd)
    if not unrecorded and target_tree == _tree_of(ledger_tip, cwd=cwd):
        raise GitDomainError("This is already the current version.")
    if unrecorded:
        commit_save(unrecorded, working, cwd=cwd, message="Updated project files")
        ledger_tip = _run_git("rev-parse", "HEAD", cwd=cwd).strip()

    # A file the target brings back must not overwrite one history does not hold.
    added = _run_git(
        "diff", "--name-only", "--no-renames", "--diff-filter=A", "-z", "HEAD", target, cwd=cwd
    )
    in_the_way = [
        path for path in added.split("\0") if path and os.path.lexists(project_root / path)
    ]
    if in_the_way:
        named = ", ".join(in_the_way[:_IN_THE_WAY_NAMED])
        more = len(in_the_way) - _IN_THE_WAY_NAMED
        raise GitDomainError(
            "Restoring this version would overwrite a file that is not in version history: "
            f"{named}{f' and {more} more' if more > 0 else ''}. Move or rename it first."
        )

    subject = _run_git("log", "-1", "--format=%s", target, cwd=cwd).strip()
    title = f"Restored {target[:8]}: {subject}" if subject else f"Restored {target[:8]}"
    message = f"{title}\n\nRestored-from: {target}"
    restored = _run_git(
        "commit-tree", target_tree, "-p", ledger_tip, "-m", message, cwd=cwd
    ).strip()
    _run_git("update-ref", f"refs/heads/{ledger}", restored, ledger_tip, cwd=cwd)

    # HEAD is the ledger, now at the restore: bring the index and the files to it.
    # Paths tracked but absent from the restore are removed; untracked and ignored
    # files, .haute/ among them, are not git's to touch. A file another program
    # holds open cannot be written; the restore stays recorded and the clone is
    # interrupted until Retry writes what is left.
    code, stderr = _run_git_capture(
        "restore", "--source=HEAD", "--staged", "--worktree", "--", ":/", cwd=cwd
    )
    left = sorted(_tracked_changes(cwd))
    if left:
        record_interrupted(project_root, "restore", left)
        raise GitFilesInUseError(_held_files(stderr) or left)
    if code != 0:
        logger.warning("git_command_failed", cmd=["git", "restore"], stderr=stderr)
        raise GitError(stderr or "git restore failed")
    _wipe_volatile_artefacts(cwd or Path.cwd())

    logger.info("version_restored", working=working, sha=restored, restored_from=target)
    return GitRestoreVersionResponse(
        sha=restored,
        short_sha=restored[:8],
        restored_from=target,
        working_branch=working,
    )


def _assert_not_interrupted(project_root: Path) -> None:
    """Refuse while files are out of step with HEAD: a milestone's sweep or a
    restore would record or overwrite them as if they were the analyst's."""
    from haute._git_state import read_interrupted

    record = read_interrupted(project_root)
    if record is None:
        return
    files = record["files"]
    assert isinstance(files, list)
    named = ", ".join(files[:5]) + (f" and {len(files) - 5} more" if len(files) > 5 else "")
    raise GitDomainError(
        f"These files are out of step with version history: {named or 'unknown'}. "
        "Close them and choose Retry first."
    )


@_serialized_mutation
def retry_interrupted(project_root: Path, cwd: Path | None = None) -> None:
    """Write HEAD's version of the files an interrupted git action left out of step,
    and clear the interruption once none is left. Files still held open refuse with
    ``GitFilesInUseError`` and the interruption stays."""
    from haute._git_state import clear_interrupted, read_interrupted

    _assert_git_repo(cwd)
    record = read_interrupted(project_root)
    if record is None:
        return
    files = record["files"]
    assert isinstance(files, list)
    stderr = ""
    if files:
        _assert_no_git_op_in_progress(cwd)
        _code, stderr = _run_git_capture(
            "restore",
            "--source=HEAD",
            "--staged",
            "--worktree",
            "--",
            *[f":(literal){path}" for path in files],
            cwd=cwd,
        )
    left = sorted(_tracked_changes(cwd) & set(files))
    if left:
        raise GitFilesInUseError(_held_files(stderr) or left)
    clear_interrupted(project_root)
    _wipe_volatile_artefacts(cwd or Path.cwd())
    logger.info("interrupted_tree_retried", files=files)
