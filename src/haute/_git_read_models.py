"""Immutable repository readiness and managed working-branch read models."""

from __future__ import annotations

from pathlib import Path

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._git_core import (
    _assert_git_repo,
    _get_default_branch,
    _is_git_repo,
    _ledger_or_branch_sha,
    _list_branches_with_tips,
    _merge_base,
    _rev_parse,
    _run_git_ok,
    branch_category,
    git_binary_available,
    is_eligible_working_branch,
    ledger_name,
    working_name,
)
from haute._git_setup import get_identity
from haute._git_transactions import check_invariants
from haute.schemas import GitManagedBranch, GitWorkingBranchesResponse, GitWorkingBranchResponse

__all__ = ["working_branch_status", "working_branches"]


def _eligible_working_branches(cwd: Path | None = None) -> list[str]:
    """Names choosable as a working branch: not protected, ledger, archived, or
    the repo's default branch (which is deploy-only, like the hardcoded
    protected set — PROTECTED_BRANCHES being configurable is a later item).

    Only names, archived flags and the yours-first order are consumed — the
    startup modal preselects the FIRST eligible branch, so the order must match
    the branch listing's."""
    listing, _ = _list_branches_with_tips(cwd=cwd)
    default = _get_default_branch(cwd)
    return [
        b.name
        for b in listing.branches
        if not b.is_archived and b.name != default and is_eligible_working_branch(b.name)
    ]


def working_branch_status(project_root: Path, cwd: Path | None = None) -> GitWorkingBranchResponse:
    """Compute the working-branch readiness signal for a clone.

    state is one of:
      - "git-unavailable" — no git binary on PATH (hosted containers without
                      git); distinct from "no-repository" so the UI can say
                      "git is not available here" instead of offering init
      - "no-repository" — the project has no Git repository
      - "unset"     — no working branch recorded
      - "detached"  — HEAD resolves but is not attached to a branch
      - "invalid"   — recorded branch missing / ineligible / invariants violated
      - "divergent" — recorded branch fine, but HEAD is on neither it nor its
                      ledger (user moved the repo outside haute)
      - "ready"     — recorded branch is the current lineage and healthy
    """
    from haute._git_state import read_working_branch

    if not git_binary_available():
        return GitWorkingBranchResponse(
            state="git-unavailable",
            current_branch="",
            identity_set=False,
        )

    if not _is_git_repo(cwd):
        return GitWorkingBranchResponse(
            state="no-repository",
            current_branch="",
            identity_set=False,
        )

    attached, current = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
    current = current if attached else ""
    head_sha = _rev_parse("HEAD", cwd=cwd)
    name, email = get_identity(cwd)
    identity_set = name is not None and email is not None
    eligible = _eligible_working_branches(cwd)

    working = read_working_branch(project_root)
    base = GitWorkingBranchResponse(
        working_branch=working,
        current_branch=current,
        head_sha=head_sha,
        eligible_branches=eligible,
        identity_set=identity_set,
        user_name=name,
        user_email=email,
    )

    if not attached:
        base.state = "detached"
        return base

    if working is None:
        base.state = "unset"
        return base

    base.last_save_sha = _ledger_or_branch_sha(working, cwd=cwd)

    if not is_eligible_working_branch(working):
        base.state = "invalid"
        base.errors = [f"'{working}' is no longer a valid working branch."]
        return base
    if _rev_parse(working, cwd=cwd) is None:
        base.state = "invalid"
        base.errors = [f"Working branch '{working}' no longer exists."]
        return base

    violations = check_invariants(working, cwd=cwd)
    if violations:
        base.state = "invalid"
        base.errors = violations
        return base

    if current not in (working, ledger_name(working)):
        base.state = "divergent"
        return base

    base.state = "ready"
    return base


def _has_unmerged_saves(
    working_tip: str | None, ledger_tip: str | None, cwd: Path | None = None
) -> bool:
    """Whether a pair's ledger holds saves not yet milestoned into its working
    branch (i.e. the ledger is ahead of the working branch). Takes resolved tip
    SHAs — callers already hold them (from the for-each-ref enumeration or a
    prior rev-parse), so the merge-base lands in the SHA-keyed cache and an
    unmoved pair costs no process on re-read."""
    if working_tip is None or ledger_tip is None:
        return False
    return _merge_base(working_tip, ledger_tip, cwd=cwd) != ledger_tip


def _normalize_to_working(branch: str) -> str:
    """A ledger name resolves to the working branch it serves; anything else is
    taken as the working name itself (archive/delete operate on the pair)."""
    return working_name(branch) or branch


def working_branches(project_root: Path, cwd: Path | None = None) -> GitWorkingBranchesResponse:
    """The branch manager's view: every working branch (active + archived),
    ledgers hidden, the repo's default deploy branch excluded — each with its
    current/archived flags and whether its ledger has unmerged saves."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    current = read_working_branch(project_root)
    default = _get_default_branch(cwd)
    # The working tree belongs to whatever HEAD points at (the current branch's
    # ledger); tracked, uncommitted changes block the switch-away that archive/
    # delete of the *current* pair needs. Compute once.
    ok_dirty, dirty_status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    tree_dirty = ok_dirty and bool(dirty_status.strip())

    entries: list[GitManagedBranch] = []
    # Working AND ledger tips come from the single for-each-ref enumeration
    # (ledgers are local heads too) — no per-branch rev-parse pair, and the
    # unmerged-saves merge-base is SHA-keyed-cached. The enumeration carries no
    # ahead-behind counts, but it does preserve the yours-first ORDER the
    # manager view consumes.
    listing, tips = _list_branches_with_tips(cwd=cwd)
    for b in listing.branches:
        # Working branches only — ledgers (category "ledger") and protected
        # branches are not version lines; the default branch is deploy-only.
        if branch_category(b.name) != "working" or b.name == default:
            continue
        is_current = b.name == current
        entries.append(
            GitManagedBranch(
                name=b.name,
                is_current=is_current,
                is_archived=b.is_archived,
                has_unmerged_saves=_has_unmerged_saves(
                    tips.get(b.name), tips.get(ledger_name(b.name)), cwd=cwd
                ),
                has_uncommitted_changes=is_current and tree_dirty,
            )
        )
    return GitWorkingBranchesResponse(current=current, branches=entries)
