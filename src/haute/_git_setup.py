"""Repository setup, identity, default branches, and clone preferences."""

from __future__ import annotations

from pathlib import Path

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._git_core import (
    GitDomainError,
    GitError,
    GitTransactionError,
    _assert_eligible_working,
    _assert_git_repo,
    _clear_content_caches,
    _get_current_branch,
    _is_ancestor,
    _ledger_or_branch_sha,
    _list_branches_with_tips,
    _rev_parse,
    _rewrite_tree,
    _run_git,
    _run_git_ok,
    _serialized_mutation,
    _tree_of,
    _validate_ref_name,
    branch_category,
    is_eligible_working_branch,
    ledger_name,
)
from haute._gitignore_guard import ensure_gitignore_guards
from haute._logging import get_logger
from haute.schemas import GitPrefs, GitSetIdentityResponse, GitSetWorkingBranchResponse

logger = get_logger(component="git")

__all__ = [
    "resolve_ledger",
    "ensure_repo",
    "get_identity",
    "set_identity",
    "set_working_branch",
    "get_prefs",
    "set_prefs",
]

_SEED_PATHSPECS: tuple[str, ...] = (
    "haute.toml",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    ".env.example",
    "*.py",
    "*.haute.json",
    "config/",
    "*/config/*",
    # The workbench's form (specs/workbench), versioned beside the pipeline.
    "forms/",
    "prompts/",
    "tests/",
    ".githooks/",
    ".github/",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
)
_PREF_SKIP_SWITCH_CONFIRM = "skipSwitchConfirm"


def _on_first_parent(working: str, commit: str, cwd: Path | None = None) -> bool:
    """Whether *commit* sits on *working*'s first-parent (milestone) chain."""
    ok, raw = _run_git_ok("rev-list", "--first-parent", working, cwd=cwd)
    return ok and commit in raw.split()


def _crystallize_milestone(anchor: str, save: str, name: str, cwd: Path | None = None) -> str:
    """An anchoring milestone for a new branch forked at a *save*: a real merge
    commit (parents = the milestone the save came after + the save) carrying the
    save's tree, so the new branch opens at a clean milestone capturing that state."""
    tree = _tree_of(save, cwd=cwd)
    msg = f"Start {name} from save {save[:8]}"
    return _run_git("commit-tree", tree, "-p", anchor, "-p", save, "-m", msg, cwd=cwd)


def _folding_anchor(working_tip: str, save: str, cwd: Path | None = None) -> str:
    """The milestone a *save* folded into a milestone came after: the first parent
    of the oldest milestone on *working_tip*'s spine the save is an ancestor of.
    Anchoring there keeps the new branch's milestones in the order the work was
    done, never under milestones recorded after the save."""
    spine = _run_git("rev-list", "--first-parent", working_tip, cwd=cwd).split()
    folding: int | None = None
    for index, milestone in enumerate(spine):
        if not _is_ancestor(save, milestone, cwd=cwd):
            break
        folding = index
    if folding is None or folding + 1 >= len(spine):
        raise GitDomainError(f"Save {save[:8]} is not folded into a milestone of this branch.")
    return spine[folding + 1]


def _branch_start(branch: str, cwd: Path | None = None) -> str | None:
    """Where an adopt-created *branch* starts when HEAD is a save: an anchoring
    milestone for it, so the new branch's first-parent chain holds milestones
    only. None (start at HEAD) when HEAD is a milestone of a working branch or a
    commit no working branch's ledger holds."""
    head = _rev_parse("HEAD", cwd=cwd)
    if head is None:
        return None
    tips = _list_branches_with_tips(cwd)[1]
    pairs = sorted(
        (name, tip, tips[ledger_name(name)])
        for name, tip in tips.items()
        if is_eligible_working_branch(name) and ledger_name(name) in tips
    )
    if any(_on_first_parent(working, head, cwd=cwd) for working, _, _ in pairs):
        return None
    for _working, working_tip, ledger_tip in pairs:
        if _is_ancestor(head, working_tip, cwd=cwd):
            return _crystallize_milestone(
                _folding_anchor(working_tip, head, cwd=cwd), head, branch, cwd=cwd
            )
        if _is_ancestor(head, ledger_tip, cwd=cwd):
            return _crystallize_milestone(working_tip, head, branch, cwd=cwd)
    return None


@_serialized_mutation
def resolve_ledger(working: str, cwd: Path | None = None) -> str:
    """Find-or-create the ledger for *working* and check it out (HEAD-on-ledger).

    Lazy spawn: the ledger is created at the working branch's current tip on
    first use. Returns the ledger branch name.
    """
    _assert_git_repo(cwd)
    _validate_ref_name(working)
    _assert_eligible_working(working)

    if _rev_parse(working, cwd=cwd) is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")

    ledger = ledger_name(working)
    if _rev_parse(ledger, cwd=cwd) is None:
        _run_git("branch", ledger, working, cwd=cwd)
        logger.info("ledger_spawned", working=working, ledger=ledger)

    if _get_current_branch(cwd) != ledger:
        _rewrite_tree("checkout", ledger, cwd=cwd)

    return ledger


def ensure_repo(cwd: Path | None = None) -> None:
    """Assert we're in a git repo."""
    _assert_git_repo(cwd)


def get_identity(cwd: Path | None = None) -> tuple[str | None, str | None]:
    """Return (user_name, user_email) from git config, each None when unset."""
    ok_name, name = _run_git_ok("config", "user.name", cwd=cwd)
    ok_email, email = _run_git_ok("config", "user.email", cwd=cwd)
    return (
        name.strip() if ok_name and name.strip() else None,
        email.strip() if ok_email and email.strip() else None,
    )


@_serialized_mutation
def set_identity(
    user_name: str,
    user_email: str,
    set_global: bool = False,
    cwd: Path | None = None,
) -> GitSetIdentityResponse:
    """Set git commit identity, repo-local by default (or global on request)."""
    _assert_git_repo(cwd)
    name = user_name.strip()
    email = user_email.strip()
    if not name or not email:
        raise GitDomainError("Both a name and an email are required.")

    scope_flag = "--global" if set_global else "--local"
    # These are plain config values, not refs, so _validate_ref_name does not
    # apply — but reject ALL control characters defensively (newlines and other
    # C0/DEL chars would corrupt the git config file or inject extra lines).
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name + email):
        raise GitDomainError("Name and email must not contain control characters.")

    _run_git("config", scope_flag, "user.name", name, cwd=cwd)
    _run_git("config", scope_flag, "user.email", email, cwd=cwd)
    logger.info("git_identity_set", scope="global" if set_global else "local")
    return GitSetIdentityResponse(
        user_name=name, user_email=email, scope="global" if set_global else "local"
    )


@_serialized_mutation
def set_working_branch(
    branch: str,
    project_root: Path,
    create: bool = False,
    cwd: Path | None = None,
) -> GitSetWorkingBranchResponse:
    """Adopt *branch* as this clone's working branch.

    Validates eligibility, optionally creates the branch off current HEAD,
    spawns + checks out its ledger (HEAD-on-ledger, S10), and records the
    association. The startup modal's confirm and the save-gate both land here.

    When *create=True* and the repo has no commits yet (unborn HEAD), a root
    commit is seeded on the current branch first so the new branch forks off a
    real ref.  The create path is all-or-nothing: a failure after the branch is
    created restores HEAD and deletes the half-created branch.
    """
    from haute._git_state import (
        clear_working_branch,
        read_working_branch,
        write_working_branch,
    )

    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    _assert_eligible_working(branch)

    previous_working = read_working_branch(project_root)
    exists = _rev_parse(branch, cwd=cwd) is not None
    if create:
        if exists:
            raise GitDomainError(f"Branch '{branch}' already exists.")

        # Unborn-repo seeding: when HEAD has no commits yet, plant a root commit
        # on the canonical default branch so the working branch can fork off a
        # real ref.  The root commit is a permanent, legitimate state — a retry
        # after a partial failure simply takes the normal (born) code path.
        #
        # If HEAD is on an unborn working-category branch (e.g. "initial-branch"
        # left by a prior failed attempt), rename it to "main" first so the root
        # commit lands on the default, never on the branch we are about to create.
        # Protected branches (main/master/etc.) are kept as-is.
        if _rev_parse("HEAD", cwd=cwd) is None:
            current_unborn = _get_current_branch(cwd)
            if branch_category(current_unborn) != "protected":
                # A born 'main' can only coexist with an unborn non-protected HEAD
                # via `git checkout --orphan` outside haute; the rename would then
                # collide, so surface a clear domain error rather than a raw git one.
                if _rev_parse("main", cwd=cwd) is not None:
                    raise GitDomainError(
                        "Cannot seed the initial commit: HEAD is on an unborn branch "
                        "but 'main' already exists."
                    )
                _run_git("branch", "-m", "main", cwd=cwd)
                _clear_content_caches()

            # Seed staging is defence-in-depth: a file
            # enters the root commit only by matching a haute-owned pathspec
            # (_SEED_PATHSPECS, the permit gate) AND surviving the .gitignore
            # guard entries (the deny gate). `haute init` writes the guards,
            # but a foreign `git init` repo carries no such guarantee — assert
            # them here, or the seed publishes .env into history and commits
            # .haute/ (the clone-lockout class).
            toplevel = Path(_run_git("rev-parse", "--show-toplevel", cwd=cwd).strip())
            ensure_gitignore_guards(toplevel)
            # Pre-staged index content bypasses .gitignore and would ride into
            # the commit unstaged-checks notwithstanding; start from an empty
            # index so the two gates alone decide what enters the root commit.
            # --cached leaves the working tree untouched; -f covers
            # stage-vs-disk mismatches.
            _run_git("rm", "-r", "-f", "-q", "--cached", "--ignore-unmatch", "--", ".", cwd=cwd)
            # ls-files applies both gates: the pathspecs permit, and
            # --exclude-standard enforces the ignore rules.
            listed = _run_git(
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                *_SEED_PATHSPECS,
                cwd=cwd,
            )
            seed_files = [f for f in listed.split("\0") if f]
            if seed_files:
                # :(literal) so a filename containing glob characters cannot
                # re-expand into a broader pathspec at add time.
                _run_git("add", "--", *[f":(literal){f}" for f in seed_files], cwd=cwd)
            # If nothing was staged (empty working tree) use --allow-empty so we
            # still establish a root commit and the model remains consistent.
            ok_diff, _ = _run_git_ok("diff", "--cached", "--quiet", cwd=cwd)
            commit_extra = ["--allow-empty"] if ok_diff else []
            try:
                _run_git("commit", *commit_extra, "-m", "Initial commit", cwd=cwd)
            except GitError as exc:
                stderr = str(exc)
                if any(
                    kw in stderr
                    for kw in (
                        "Author identity unknown",
                        "Please tell me who you are",
                        "user.email",
                        "user.name",
                    )
                ):
                    raise GitDomainError(
                        "Cannot create the initial commit: set your git name and email first."
                    ) from exc
                raise

        # Capture pre-creation position for atomic rollback.
        ok_sym, sym = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
        if ok_sym:
            _rb_ref = sym.strip()
            _rb_detach = False
        else:
            # Detached HEAD (a checkout outside Haute): capture SHA for restore.
            _rb_ref = _run_git("rev-parse", "HEAD", cwd=cwd)
            _rb_detach = True

        # A branch made from a save opens at an anchoring milestone (same tree, so
        # the checkout changes no file and carries uncommitted edits across).
        start = _branch_start(branch, cwd=cwd)
        try:
            _rewrite_tree("checkout", "-b", branch, *([start] if start else []), cwd=cwd)
            resolve_ledger(branch, cwd=cwd)
            write_working_branch(project_root, branch)
        except Exception as exc:
            # All-or-nothing: restore HEAD and delete the half-created branch so
            # a retry is not blocked and the repo is left in a coherent state.
            # Also drop the ledger resolve_ledger may have just spawned — leaving
            # it orphaned would let a later fork off an advanced default read as
            # `invalid` via check_invariants (tree mismatch vs the stale ledger).
            restored_head, _ = (
                _run_git_ok("checkout", "--detach", _rb_ref, cwd=cwd)
                if _rb_detach
                else _run_git_ok("checkout", _rb_ref, cwd=cwd)
            )
            removed_working = True
            if _rev_parse(branch, cwd=cwd) is not None:
                removed_working, _ = _run_git_ok("branch", "-D", branch, cwd=cwd)
            removed_ledger = True
            created_ledger = ledger_name(branch)
            if _rev_parse(created_ledger, cwd=cwd) is not None:
                removed_ledger, _ = _run_git_ok("branch", "-D", created_ledger, cwd=cwd)
            try:
                if previous_working is None:
                    clear_working_branch(project_root)
                else:
                    write_working_branch(project_root, previous_working)
                restored_state = True
            except OSError:
                restored_state = False
            if not all((restored_head, removed_working, removed_ledger, restored_state)):
                raise GitTransactionError(
                    "Creating the working branch failed and automatic rollback was incomplete. "
                    "Inspect the repository before retrying."
                ) from exc
            raise
    else:
        if not exists:
            raise GitDomainError(f"Branch '{branch}' does not exist.")
        # Adopt existing branch transactionally: restore HEAD, association, and
        # any newly-spawned ledger when either checkout or state replacement fails.
        ledger = ledger_name(branch)
        ledger_existed = _rev_parse(ledger, cwd=cwd) is not None
        attached, previous_head = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
        if not attached:
            previous_head = _run_git("rev-parse", "HEAD", cwd=cwd)
        try:
            resolve_ledger(branch, cwd=cwd)
            write_working_branch(project_root, branch)
        except Exception as exc:
            restored_head, _ = (
                _run_git_ok("checkout", previous_head, cwd=cwd)
                if attached
                else _run_git_ok("checkout", "--detach", previous_head, cwd=cwd)
            )
            removed_ledger = True
            if not ledger_existed and _rev_parse(ledger, cwd=cwd) is not None:
                removed_ledger, _ = _run_git_ok("branch", "-D", ledger, cwd=cwd)
            try:
                if previous_working is None:
                    clear_working_branch(project_root)
                else:
                    write_working_branch(project_root, previous_working)
                restored_state = True
            except OSError:
                restored_state = False
            if not all((restored_head, removed_ledger, restored_state)):
                raise GitTransactionError(
                    "Selecting the working branch failed and automatic rollback was incomplete. "
                    "Inspect the repository before retrying."
                ) from exc
            raise

    logger.info("working_branch_set", branch=branch, created=create)

    return GitSetWorkingBranchResponse(
        working_branch=branch,
        state="ready",
        last_save_sha=_ledger_or_branch_sha(branch, cwd=cwd),
    )


def get_prefs(project_root: Path) -> GitPrefs:
    """This clone's local UI preferences (defaults when unset/malformed)."""
    from haute._git_state import read_prefs

    raw = read_prefs(project_root)
    return GitPrefs(skip_switch_confirm=bool(raw.get(_PREF_SKIP_SWITCH_CONFIRM, False)))


@_serialized_mutation
def set_prefs(prefs: GitPrefs, project_root: Path) -> GitPrefs:
    """Persist this clone's local UI preferences; returns the stored state."""
    from haute._git_state import write_pref

    write_pref(project_root, _PREF_SKIP_SWITCH_CONFIRM, prefs.skip_switch_confirm)
    return prefs
