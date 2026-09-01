"""Read-only Git history projections and safe historical materialisation."""

from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._git_core import (
    GitDomainError,
    GitError,
    GitHistoryReadError,
    _assert_git_repo,
    _commit_parents,
    _first_parent_spine,
    _get_default_branch,
    _git_env,
    _graph_log_cached,
    _graph_log_rows,
    _is_ancestor,
    _is_full_sha,
    _list_branches_with_tips,
    _rev_parse,
    _run_git,
    _run_git_ok,
    _run_git_process,
    _validate_ref_name,
    branch_category,
    ledger_name,
)
from haute._logging import get_logger
from haute.schemas import (
    GitCommitContext,
    GitCommitRef,
    GitFileChange,
    GitGraphBranch,
    GitGraphEntry,
    GitGraphResponse,
    GitLedgerSave,
    GitLedgerSavesResponse,
    GitMilestoneEntry,
    GitMilestonesResponse,
)

logger = get_logger(component="git")

_HISTORY_ARCHIVE_MAX_MEMBERS = 10_000
_HISTORY_ARCHIVE_MAX_BYTES = 64 * 1024 * 1024
_SAVE_RECORD_SEP = "\x1e"


__all__ = [
    "working_milestones",
    "commit_context",
    "milestone_saves",
    "pending_ledger_saves",
    "graph_topology",
    "archive_commit",
]


def working_milestones(
    project_root: Path,
    limit: int | None = 20,
    cwd: Path | None = None,
    branch: str | None = None,
) -> GitMilestonesResponse:
    """Milestone history (first-parent chain, newest first, with version-label
    tags). Defaults to the clone's working branch; pass *branch* to peek at
    another branch's history without switching to it.

    Shares the graph rail's cached windowed log (keyed by the resolved tip
    SHA): one ``git log`` per (tip, limit) ever, then a warm page costs the
    tip resolve plus the per-request batched tag read only. Version labels
    are applied after retrieval — tags move independently of tips."""
    _assert_git_repo(cwd)
    from haute._git_state import read_working_branch

    if branch is not None:
        _validate_ref_name(branch)
        working: str | None = branch
    else:
        working = read_working_branch(project_root)
    if working is None:
        return GitMilestonesResponse(working_branch=working, entries=[])
    tip = _rev_parse(working, cwd=cwd)
    if tip is None:
        return GitMilestonesResponse(working_branch=working, entries=[])

    # First-parent walk = the milestone spine (skips the ledger's per-save
    # commits, which hang off each merge's second parent).
    try:
        rows = _graph_log_cached(tip, limit, str(cwd) if cwd else "")
    except GitError:
        return GitMilestonesResponse(working_branch=working, entries=[])

    # ONE batched tag read for the whole page instead of a per-row
    # ``tag --points-at`` (the N+1 that dominated this endpoint).
    labels = _version_label_map(cwd=cwd)
    entries = [
        GitMilestoneEntry(
            sha=sha,
            short_sha=short_sha,
            message=message,
            timestamp=timestamp,
            version_label=labels.get(sha),
            # Truncation-aware root tagging for free from the row's %P: only a
            # genuinely parentless commit is flagged, and a first-parent walk
            # can only ever end (not pass through) one — so this fires for the
            # oldest entry of an untruncated page and never for a windowed cut.
            is_root=not parents,
        )
        for sha, short_sha, parents, timestamp, message in rows
    ]
    return GitMilestonesResponse(working_branch=working, entries=entries)


def _version_label_for(sha: str, cwd: Path | None = None) -> str | None:
    """The version label (a ``version/<label>`` tag) pointing at *sha*, if any."""
    ok, raw = _run_git_ok("tag", "--points-at", sha, "--list", "version/*", cwd=cwd)
    if ok and raw.strip():
        first = raw.strip().splitlines()[0]
        return first[len("version/") :] if first.startswith("version/") else first
    return None


def _commit_meta(sha: str, cwd: Path | None = None) -> tuple[str, str, str, str]:
    """(full sha, short sha, subject, ISO author date) for *sha*.

    Fields are NUL-delimited and the subject is last, so tabs in an authored
    commit message cannot shift metadata columns.
    Raises :class:`GitError` when git can't read the commit.
    """
    ok, raw = _run_git_ok("show", "-s", "--format=%H%x00%h%x00%aI%x00%s", sha, cwd=cwd)
    parts = raw.split("\0", 3)
    if not ok or len(parts) < 4:
        raise GitError(f"git show failed for {sha}")
    full, short_sha, timestamp, message = parts
    return full, short_sha, message, timestamp


def _milestone_fold_points(
    milestones: list[GitMilestoneEntry],
    cwd: Path | None = None,
) -> dict[str, str]:
    """Return milestone → ledger fold-point from one parent-metadata query."""
    if not milestones:
        return {}
    ok, raw = _run_git_ok(
        "rev-list",
        "--parents",
        "--no-walk=unsorted",
        *[milestone.sha for milestone in milestones],
        cwd=cwd,
    )
    if not ok:
        raise GitError("could not read milestone parent metadata")
    parents_by_sha: dict[str, list[str]] = {}
    for line in raw.splitlines():
        fields = line.split()
        if fields:
            parents_by_sha[fields[0]] = fields[1:]
    return {
        milestone.sha: (
            parents_by_sha[milestone.sha][1]
            if len(parents_by_sha.get(milestone.sha, [])) >= 2
            else milestone.sha
        )
        for milestone in milestones
        if milestone.sha in parents_by_sha
    }


def _is_root_commit(sha: str, cwd: Path | None = None) -> bool:
    """Whether *sha* is a root commit (no parents). The ``rev-list --parents``
    line is ``"<sha> <parent1> <parent2>..."`` — a root has no trailing shas."""
    ok, raw = _run_git_ok("rev-list", "--parents", "-n", "1", sha, cwd=cwd)
    return ok and len(raw.split()) <= 1


def _ledger_point(milestone_sha: str, cwd: Path | None = None) -> str:
    """A milestone's ledger fold-point — the last ledger commit it folded in, i.e.
    its SECOND parent. Milestone *merges* (working line) are never ancestors of
    the ledger's save commits, but their fold-point IS, so ancestry against the
    fold-point is what locates the latest milestone for a given save. A non-merge
    milestone (the root) has no second parent, so it is its own fold-point."""
    second = _rev_parse(f"{milestone_sha}^2", cwd=cwd)
    return second if second is not None else milestone_sha


def commit_context(
    project_root: Path, sha: str, cwd: Path | None = None, base: str | None = None
) -> GitCommitContext:
    """A commit's "breadcrumb context" for the version-compare UI: the LATEST
    milestone at the commit and the distance (commit count) from that milestone's
    ledger fold-point to the commit. A milestone is its own anchor (distance 0).
    The latest milestone is found by ledger fold-point ancestry — a save folded
    after milestone M but before M+1 anchors on M, and a pending save after the tip
    milestone anchors on the tip — not on the repo root. When ``base`` is given,
    also reports ``delta_from_base`` = the commit count ``base..sha`` (the
    historic↔current span). Pure read — no checkout, no HEAD change."""
    _assert_git_repo(cwd)
    _validate_ref_name(sha)
    resolved = _rev_parse(sha, cwd=cwd)
    if resolved is None:
        raise GitDomainError(f"Unknown commit: {sha}")

    full, short_sha, message, timestamp = _commit_meta(resolved, cwd=cwd)
    is_root = _is_root_commit(resolved, cwd=cwd)

    milestones = working_milestones(project_root, limit=None, cwd=cwd).entries
    milestone_shas = {m.sha for m in milestones}
    is_milestone = full in milestone_shas
    version_label = _version_label_for(full, cwd=cwd)

    nearest: GitCommitRef
    distance: int
    if is_milestone:
        entry = next(m for m in milestones if m.sha == full)
        nearest = GitCommitRef(
            sha=entry.sha,
            short_sha=entry.short_sha,
            message=entry.message,
            version_label=entry.version_label,
            is_root=is_root,
        )
        distance = 0
    elif is_root:
        nearest = GitCommitRef(
            sha=full,
            short_sha=short_sha,
            message=message,
            version_label=version_label,
            is_root=True,
        )
        distance = 0
    else:
        # Walk milestones newest-first; the latest one whose ledger fold-point is
        # an ancestor of this save is the milestone the save sits under (a save
        # folded by a later milestone fails the check — its fold-point is a
        # descendant of the save — so we land on the previous milestone, or the
        # tip for a pending save). Distance is counted from that fold-point.
        latest: GitMilestoneEntry | None = None
        anchor: str | None = None
        fold_points = _milestone_fold_points(milestones, cwd=cwd)
        ok_ancestors, ancestors_raw = _run_git_ok("rev-list", full, cwd=cwd)
        if not ok_ancestors:
            raise GitError(f"could not read ancestors for {full}")
        ancestors = set(ancestors_raw.split())
        for m in milestones:
            if m.sha == full:
                continue
            point = fold_points.get(m.sha)
            if point is not None and point != full and point in ancestors:
                latest = m
                anchor = point
                break
        if latest is not None and anchor is not None:
            nearest = GitCommitRef(
                sha=latest.sha,
                short_sha=latest.short_sha,
                message=latest.message,
                version_label=latest.version_label,
                is_root=latest.is_root,
            )
        else:
            # No milestone fold-point ancestor — anchor on the repo's root commit.
            ok_root, root_raw = _run_git_ok("rev-list", "--max-parents=0", resolved, cwd=cwd)
            if not ok_root or not root_raw.strip():
                raise GitError(f"could not find root commit for {sha}")
            root_sha = root_raw.splitlines()[0]
            r_full, r_short, r_msg, _r_ts = _commit_meta(root_sha, cwd=cwd)
            nearest = GitCommitRef(
                sha=r_full,
                short_sha=r_short,
                message=r_msg,
                version_label=_version_label_for(r_full, cwd=cwd),
                is_root=True,
            )
            anchor = r_full
        ok_count, count_raw = _run_git_ok("rev-list", "--count", f"{anchor}..{full}", cwd=cwd)
        if not ok_count:
            raise GitError(f"git rev-list --count failed for {anchor}..{full}")
        distance = int(count_raw.strip())

    # Optional historic↔current delta: commits between a caller-supplied base and
    # this commit (rev-list --count base..self). Used by the compare UI to show how
    # far the current pipeline has moved past the inspected version. Robust across
    # milestone merges (base..head counts only what head reaches that base doesn't).
    delta_from_base: int | None = None
    if base is not None:
        _validate_ref_name(base)
        base_resolved = _rev_parse(base, cwd=cwd)
        if base_resolved is None:
            raise GitDomainError(f"Unknown commit: {base}")
        ok_delta, delta_raw = _run_git_ok(
            "rev-list", "--count", f"{base_resolved}..{full}", cwd=cwd
        )
        if not ok_delta:
            raise GitError(f"git rev-list --count failed for {base_resolved}..{full}")
        delta_from_base = int(delta_raw.strip())

    return GitCommitContext(
        sha=full,
        short_sha=short_sha,
        message=message,
        timestamp=timestamp,
        is_root=is_root,
        is_milestone=is_milestone,
        version_label=version_label,
        nearest_milestone=nearest,
        distance=distance,
        delta_from_base=delta_from_base,
    )


def _parse_ledger_saves(range_spec: str, cwd: Path | None = None) -> list[GitLedgerSave]:
    """Parse ``git log -M --name-status`` over *range_spec* into save records.

    Commit metadata uses NUL-delimited fields; the name-status lines below keep
    Git's own tab separators.
    """
    # core.quotepath=false: git otherwise octal-escapes + quotes non-ASCII paths
    # (e.g. a unicode config filename), which would surface as a mangled path in
    # the history view. haute-owned paths never contain spaces/tabs/newlines
    # (sanitized identifiers), which git would still quote regardless.
    ok, raw = _run_git_ok(
        "-c",
        "core.quotepath=false",
        "log",
        "-M",
        "--name-status",
        f"--format={_SAVE_RECORD_SEP}%H%x00%h%x00%aI%x00%s",
        range_spec,
        cwd=cwd,
    )
    if not ok or not raw:
        return []

    saves: list[GitLedgerSave] = []
    for block in raw.split(_SAVE_RECORD_SEP):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        header = lines[0].split("\0", 3)
        if len(header) < 4:
            continue
        sha, short_sha, timestamp, message = header

        files: list[GitFileChange] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            cols = line.split("\t")
            code = cols[0]
            letter = code[0] if code else "?"
            if letter in ("R", "C") and len(cols) >= 3:
                files.append(GitFileChange(status=letter, path=cols[2], old_path=cols[1]))
            elif len(cols) >= 2:
                files.append(GitFileChange(status=letter, path=cols[1]))
        saves.append(
            GitLedgerSave(
                sha=sha,
                short_sha=short_sha,
                message=message,
                timestamp=timestamp,
                files=files,
            )
        )
    return saves


def milestone_saves(milestone_sha: str, cwd: Path | None = None) -> GitLedgerSavesResponse:
    """The ledger saves folded into a milestone — the commits on its second
    parent that its first parent doesn't have (``M^1..M^2``), newest first.

    A non-merge commit on the spine (e.g. the pre-spawn root) folds in nothing.
    """
    _assert_git_repo(cwd)
    _validate_ref_name(milestone_sha)

    # Resolve to a single commit first. _validate_ref_name does not block "..",
    # so a range-shaped value ("a..b") would otherwise reach rev-list as a range;
    # rev-parse --verify <sha>^{commit} rejects anything that is not one commit.
    resolved = _rev_parse(milestone_sha, cwd=cwd)
    if resolved is None:
        raise GitDomainError(f"Commit '{milestone_sha}' not found.")

    ok, parents = _run_git_ok("rev-list", "--parents", "-n", "1", resolved, cwd=cwd)
    if not ok or not parents.strip():
        raise GitDomainError(f"Commit '{milestone_sha}' not found.")
    parent_shas = parents.split()[1:]
    if len(parent_shas) < 2:
        return GitLedgerSavesResponse(saves=[])

    first_parent, second_parent = parent_shas[0], parent_shas[1]
    return GitLedgerSavesResponse(
        saves=_parse_ledger_saves(f"{first_parent}..{second_parent}", cwd=cwd)
    )


def pending_ledger_saves(
    project_root: Path, cwd: Path | None = None, branch: str | None = None
) -> GitLedgerSavesResponse:
    """The saves on a branch's ledger ahead of its tip (``branch..branch-save``):
    what the next save & commit would fold into a milestone. Defaults to the
    clone's working branch; pass *branch* to peek at another. Empty when no
    branch resolves, the ledger is unspawned, or nothing is pending."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    if branch is not None:
        _validate_ref_name(branch)
        working: str | None = branch
    else:
        working = read_working_branch(project_root)
    if working is None:
        return GitLedgerSavesResponse(saves=[])
    ledger = ledger_name(working)
    if _rev_parse(working, cwd=cwd) is None or _rev_parse(ledger, cwd=cwd) is None:
        return GitLedgerSavesResponse(saves=[])
    return GitLedgerSavesResponse(saves=_parse_ledger_saves(f"{working}..{ledger}", cwd=cwd))


def _version_label_map(cwd: Path | None = None) -> dict[str, str]:
    """sha → version label for every ``version/<label>`` tag, in ONE batched
    ``for-each-ref`` call (vs. the per-commit ``tag --points-at`` the milestones
    view issues). Reads the peeled ``%(*objectname)`` — version tags are
    annotated — falling back to ``%(objectname)`` for a lightweight tag. First
    label per commit wins (refname order), matching ``_version_label_for``'s
    first-line semantics."""
    ok, raw = _run_git_ok(
        "for-each-ref",
        "refs/tags/version/",
        "--format=%(*objectname) %(objectname) %(refname:short)",
        cwd=cwd,
    )
    labels: dict[str, str] = {}
    if not ok or not raw.strip():
        return labels
    for line in raw.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        peeled, plain, refname = parts
        sha = peeled or plain
        label = refname[len("version/") :] if refname.startswith("version/") else refname
        if sha and sha not in labels:
            labels[sha] = label
    return labels


def _fork_source_and_credit(
    spine: list[str],
    fork_point_sha: str,
    parent_spine: list[str],
    parent_ledger_tip: str | None,
    cwd: Path | None = None,
) -> tuple[str | None, str | None]:
    """The spawn-source save and its crediting milestone for one forked branch.

    A fork created AT A SAVE gets an auto "anchoring" merge as its oldest own
    commit X, whose parents are ``[spawning spine tip, the save]`` — that
    second parent is the commit the user actually forked from. But an
    ORDINARY milestone-level fork's oldest own commit is just its first
    milestone, whose second parent is the fork's OWN ledger save. Ancestry
    tells the two apart: a crystallized fork's source save lives in the
    PARENT pair's history (folded into a later parent milestone, or still
    pending on the parent's ledger), while a fork's own fold never does.

    Returns ``(fork_source, fork_credit)``:

    * ``fork_source`` — X's second parent, when X is a merge AND that commit
      is reachable from the parent's working tip or its ledger tip (the
      ledger may not exist — treated as not-ancestor); else None.
    * ``fork_credit`` — computed only when ``fork_source`` is set: the OLDEST
      parent-spine commit ABOVE the fork point that contains the source save,
      i.e. the milestone whose fold swallowed it — the row that should
      visually take credit for the spawn while its fold is collapsed. Found
      by binary search (containment along a first-parent spine is monotone).
      None when the save is still pending (reachable only via the parent's
      ledger, folded into no parent milestone yet).

    Both spines are the full first-parent chains graph_topology already holds
    in memory, newest first; only the is-ancestor checks (and one ``%P`` read
    for X) hit git — both SHA-keyed-cached, so a repeat is free.
    """
    idx = spine.index(fork_point_sha)
    if idx == 0:
        return (None, None)  # no own commits — the branch sits AT the fork point
    anchor = spine[idx - 1]  # X: the fork's oldest own spine commit
    parents = _commit_parents(anchor, cwd=cwd)
    if len(parents) < 2:
        return (None, None)  # plain commit — nothing was folded at the spawn
    source = parents[1]
    in_parent_history = _is_ancestor(source, parent_spine[0], cwd=cwd) or (
        parent_ledger_tip is not None and _is_ancestor(source, parent_ledger_tip, cwd=cwd)
    )
    if not in_parent_history:
        return (None, None)  # the anchoring second parent is the fork's own save
    # The parent spine is newest-first, so everything above the fork point is
    # the prefix; the EARLIEST (oldest) fold containing the save is the credit.
    # Containment along a first-parent spine is monotone — each newer spine
    # commit's ancestor set is a superset of its elder's — so contains(source)
    # is True on a newest-first prefix of the candidates and the boundary is
    # binary-searchable: O(log n) is-ancestor probes instead of the old
    # oldest-first linear scan's O(n). The newest-candidate probe repeats the
    # in_parent_history check above, so it costs nothing (SHA-keyed cache).
    parent_idx = parent_spine.index(fork_point_sha)
    candidates = parent_spine[:parent_idx]
    if not candidates or not _is_ancestor(source, candidates[0], cwd=cwd):
        return (source, None)  # still pending on the parent's ledger
    lo, hi = 0, len(candidates) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _is_ancestor(source, candidates[mid], cwd=cwd):
            lo = mid
        else:
            hi = mid - 1
    return (source, candidates[lo])


def _graph_entries(
    tip: str, limit: int, labels: dict[str, str], cwd: Path | None = None
) -> list[GitGraphEntry]:
    """The newest *limit* spine entries below *tip* (a resolved tip SHA on the
    graph path — cached per (tip, limit, cwd)), with parents and version labels
    (from the batched per-request *labels* map, applied AFTER cache retrieval —
    tags move independently of tips). Root tagging is read off %P — empty
    parents IS the root commit, window-truncated or not — so no per-branch
    rev-list probe."""
    try:
        if _is_full_sha(tip):
            rows = _graph_log_cached(tip, limit, str(cwd) if cwd else "")
        else:
            rows = _graph_log_rows(tip, limit, cwd=cwd)
    except GitError:
        return []
    return [
        GitGraphEntry(
            sha=sha,
            short_sha=short_sha,
            message=message,
            timestamp=timestamp,
            version_label=labels.get(sha),
            parents=list(parents),
            is_root=not parents,
        )
        for sha, short_sha, parents, timestamp, message in rows
    ]


def graph_topology(
    project_root: Path, cwd: Path | None = None, limit: int = 50
) -> GitGraphResponse:
    """The working-branch forest for the graph rail.

    Per pair: the newest-first first-parent spine (windowed to *limit*) and its
    fork attachment, computed from FULL spines by deterministic claiming — the
    CURRENT working branch claims first (its own spine must never be claimed by
    a deeper fork), then the rest deepest-spine-first (then name); each branch's
    ``fork_point_sha`` is its newest spine commit already claimed by an
    earlier-processed branch, ``fork_of`` that branch's name, and it claims
    everything above. Forked branches additionally carry ``fork_source_sha``
    / ``fork_credit_sha`` — the save the branch was actually spawned from
    (when it differs from the fork-point milestone) and the parent milestone
    whose fold contains that save (see _fork_source_and_credit). A branch
    sharing no claimed commit roots its own tree
    (both null) — the fork FOREST is real, since the root commit lives on the
    default branch, which is not a working pair. Archived pairs are included
    Pure read — no checkout, no HEAD movement, no ref or state writes."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    working = read_working_branch(project_root)
    default = _get_default_branch(cwd)

    # Same enumeration as the branch manager (working pairs only, ledgers
    # implicit, the deploy branch excluded) — but archived pairs stay in.
    # Refs resolve to tip SHAs once, in the for-each-ref itself; each spine is
    # then a content-addressed read below its tip (cached, so an unmoved
    # branch costs no rev-list on refresh). The enumeration carries only names,
    # tips and commit times — no per-branch ahead-behind walk.
    listing, tips = _list_branches_with_tips(cwd=cwd)
    spines: dict[str, list[str]] = {}
    archived: dict[str, bool] = {}
    for b in listing.branches:
        if branch_category(b.name) != "working" or b.name == default:
            continue
        tip = tips.get(b.name)
        spine = _first_parent_spine(tip, cwd=cwd) if tip is not None else None
        if spine is None:
            continue  # unreadable ref — nothing to draw for it
        spines[b.name] = spine
        archived[b.name] = b.is_archived

    # Deterministic processing order: the CURRENT working branch first — a
    # crystallized fork sits at spawning spine + 1 until the branch advances,
    # and depth-first claiming would hand it the user's own spine — then
    # deepest spine, then name. The first-processed branch of each component
    # roots its fork tree; two forks off one commit tie-break by name.
    # (working may be None or not a listed pair — the key degrades cleanly.)
    order = sorted(spines, key=lambda name: (name != working, -len(spines[name]), name))

    claimed: dict[str, str] = {}
    attachments: dict[str, tuple[str | None, str | None]] = {}
    for name in order:
        spine = spines[name]
        cut = len(spine)
        attachment: tuple[str | None, str | None] = (None, None)
        for i, sha in enumerate(spine):
            owner = claimed.get(sha)
            if owner is not None:
                cut = i
                attachment = (sha, owner)
                break
        for sha in spine[:cut]:
            claimed[sha] = name
        attachments[name] = attachment

    labels = _version_label_map(cwd=cwd)
    branches: list[GitGraphBranch] = []
    for name in order:
        spine = spines[name]
        fork_point_sha, fork_of = attachments[name]
        fork_source_sha: str | None = None
        fork_credit_sha: str | None = None
        if fork_point_sha is not None and fork_of is not None:
            # Parent ledger tips come from the same for-each-ref enumeration
            # (ledgers are local heads too) — no per-fork rev-parse.
            fork_source_sha, fork_credit_sha = _fork_source_and_credit(
                spine, fork_point_sha, spines[fork_of], tips.get(ledger_name(fork_of)), cwd=cwd
            )
        branches.append(
            GitGraphBranch(
                name=name,
                is_archived=archived[name],
                is_current=name == working,
                tip_sha=spine[0],
                fork_point_sha=fork_point_sha,
                fork_of=fork_of,
                fork_source_sha=fork_source_sha,
                fork_credit_sha=fork_credit_sha,
                truncated=len(spine) > limit,
                entries=_graph_entries(spine[0], limit, labels, cwd=cwd),
            )
        )
    return GitGraphResponse(working_branch=working, order=order, branches=branches)


def _historical_artifact_paths(sha: str, cwd: Path | None = None) -> list[str]:
    """Paths needed to discover and parse a historical pipeline."""
    raw = _run_git("ls-tree", "-r", "-z", "--name-only", sha, cwd=cwd)
    selected: list[str] = []
    for name in raw.split("\0"):
        if not name:
            continue
        path = Path(name)
        parts = path.parts
        if (
            name == "haute.toml"
            or path.suffix == ".py"
            or name.endswith(".haute.json")
            or "config" in parts
            or (parts and parts[0] == "prompts")
        ):
            selected.append(name)
    return selected


def _extract_history_tar(payload: bytes, dest: Path) -> None:
    """Portably extract regular files/directories after containment checks."""
    root = dest.resolve()
    try:
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            validated: list[tuple[tarfile.TarInfo, Path]] = []
            extracted_bytes = 0
            for member_number, member in enumerate(tar, start=1):
                if member_number > _HISTORY_ARCHIVE_MAX_MEMBERS:
                    raise GitHistoryReadError(
                        "The selected version contains too many archived files."
                    )
                target = (dest / member.name).resolve()
                if target != root and root not in target.parents:
                    raise GitHistoryReadError(
                        "The selected version contains an unsafe archive path."
                    )
                if member.isdir():
                    validated.append((member, target))
                    continue
                if not member.isfile():
                    raise GitHistoryReadError(
                        "The selected version contains an unsupported linked or special file."
                    )
                if member.size < 0:
                    raise GitHistoryReadError(
                        "The selected version contains an invalid archived file size."
                    )
                extracted_bytes += member.size
                if extracted_bytes > _HISTORY_ARCHIVE_MAX_BYTES:
                    raise GitHistoryReadError(
                        "The selected version's pipeline files are too large to extract safely."
                    )
                validated.append((member, target))

            for member, target in validated:
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                source = tar.extractfile(member)
                if source is None:
                    raise GitHistoryReadError(
                        "The selected version contains an unreadable archived file."
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except (tarfile.TarError, OSError) as exc:
        raise GitHistoryReadError(
            "The selected version's pipeline files could not be extracted."
        ) from exc


def archive_commit(sha: str, dest: Path, cwd: Path | None = None) -> None:
    """Extract only pipeline artifacts from *sha* without changing HEAD."""
    _assert_git_repo(cwd)
    _validate_ref_name(sha)
    if _rev_parse(sha, cwd=cwd) is None:
        raise GitDomainError(f"No commit found for '{sha}'.")
    paths = _historical_artifact_paths(sha, cwd=cwd)
    if not paths:
        return
    proc = _run_git_process(
        "archive",
        "--format=tar",
        sha,
        "--",
        *[f":(literal){path}" for path in paths],
        binary=True,
        cwd=cwd,
        env=_git_env(),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        logger.warning("git_archive_failed", sha=sha, stderr=stderr)
        raise GitError(stderr or "git archive failed")
    _extract_history_tar(proc.stdout, dest)
