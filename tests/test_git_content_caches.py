"""Tests for the SHA-keyed content caches behind the VC sidebar read paths.

Covers the perf fix in ``haute._git``: the batched milestones label map (the
old per-row ``tag --points-at`` N+1), the content-addressed lru caches
(_is_ancestor/_merge_base/_commit_parents/_first_parent_spine/_graph_log),
the fork-credit binary search, and the tips-threaded ``working_branches`` /
``graph_topology`` paths. Freshness is proven by mutation (new commits AND a
non-fast-forward rewrite), staleness-immunity by tip-keying; the subprocess
regression tests spy on the ``_run_git*`` entry points and pin hard bounds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import haute._git as git_mod
from haute._git import (
    _commit_parents,
    _first_parent_spine,
    _fork_source_and_credit,
    _graph_entries,
    _is_ancestor,
    _merge_base,
    _rev_parse,
    _tree_of,
    _version_label_for,
    check_invariants,
    commit_milestone,
    commit_save,
    create_working_branch,
    graph_topology,
    set_working_branch,
    working_branch_status,
    working_branches,
    working_milestones,
)
from tests._git_helpers import git_run as _git
from tests._git_helpers import init_repo as _init_repo

WORKING = "pricing-dev"
LEDGER = "pricing-dev-save"

_FAKE_SHA = "0" * 40  # full-SHA shaped, never a real object


def _save(repo: Path, working: str, content: str) -> str:
    (repo / "fixture.txt").write_text(f"{content}\n", encoding="utf-8")
    sha = commit_save(["fixture.txt"], working, cwd=repo, message=f"Save {content}")
    assert sha is not None
    return sha


def _milestone(repo: Path, message: str, label: str | None = None) -> str:
    return commit_milestone(message, repo, version_label=label, cwd=repo).sha


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A pipeline repo with the engine's working pair adopted (HEAD on ledger)."""
    root = tmp_path / "pipeline_repo"
    root.mkdir()
    _init_repo(root, user="Test Actuary")
    _git(root, "checkout", "-b", WORKING)
    set_working_branch(WORKING, root, cwd=root)
    return root


@pytest.fixture
def git_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count every git subprocess launched through the module's entry points.

    ``_run_git`` / ``_run_git_ok`` / ``_run_git_rc`` each run their own
    ``subprocess.run`` (no delegation between them), so wrapping all three
    counts each git launch exactly once.
    """
    counts = {"n": 0}
    real_run, real_ok, real_rc = git_mod._run_git, git_mod._run_git_ok, git_mod._run_git_rc

    def spy_run(*args, **kwargs):
        counts["n"] += 1
        return real_run(*args, **kwargs)

    def spy_ok(*args, **kwargs):
        counts["n"] += 1
        return real_ok(*args, **kwargs)

    def spy_rc(*args, **kwargs):
        counts["n"] += 1
        return real_rc(*args, **kwargs)

    monkeypatch.setattr(git_mod, "_run_git", spy_run)
    monkeypatch.setattr(git_mod, "_run_git_ok", spy_ok)
    monkeypatch.setattr(git_mod, "_run_git_rc", spy_rc)
    return counts


# ---------------------------------------------------------------------------
# (a) Milestones label correctness — the batched map must match the old
#     per-row ``tag --points-at`` behaviour exactly.
# ---------------------------------------------------------------------------


class TestMilestoneLabels:
    def test_labels_match_per_row_lookup(self, repo: Path) -> None:
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1", label="v1.0")
        _save(repo, WORKING, "m2")
        m2 = _milestone(repo, "Milestone 2")  # untagged
        _save(repo, WORKING, "m3")
        m3 = _milestone(repo, "Milestone 3", label="v2.0")
        # A second tag on m3's commit — first by refname order must win, the
        # same first-line semantics the old per-row lookup had.
        _git(repo, "tag", "version/v2.1", m3)

        entries = working_milestones(repo, cwd=repo).entries
        by_sha = {e.sha: e for e in entries}
        assert by_sha[m1].version_label == "v1.0"
        assert by_sha[m2].version_label is None
        assert by_sha[m3].version_label == "v2.0"
        # Field-for-field equivalence with the old behaviour (the per-sha
        # helper still backs commit_context) for EVERY returned row.
        for e in entries:
            assert e.version_label == _version_label_for(e.sha, cwd=repo)
        # Root entry tagging is unaffected by the batching.
        assert entries[-1].is_root is True
        assert [e.sha for e in entries[:3]] == [m3, m2, m1]

    def test_unresolvable_branch_returns_empty(self, repo: Path) -> None:
        res = working_milestones(repo, cwd=repo, branch="pricing-ghost")
        assert res.working_branch == "pricing-ghost"
        assert res.entries == []

    def test_unreadable_log_degrades_empty_and_is_not_cached(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _save(repo, WORKING, "m1")
        _milestone(repo, "Milestone 1")

        real_ok = git_mod._run_git_ok

        def failing_log(*args, **kwargs):
            if args and args[0] == "log":
                return (False, "")
            return real_ok(*args, **kwargs)

        monkeypatch.setattr(git_mod, "_run_git_ok", failing_log)
        assert working_milestones(repo, cwd=repo).entries == []
        # The failure was never memoised: with git healthy again, the same
        # (tip, limit) key serves the real page.
        monkeypatch.undo()
        assert len(working_milestones(repo, cwd=repo).entries) == 2


# ---------------------------------------------------------------------------
# (b) Cache freshness — identical repeats, then mutations (fast-forward AND
#     non-fast-forward) must be visible immediately: everything re-keys on the
#     tip SHA resolved per request, nothing stales.
# ---------------------------------------------------------------------------


class TestCacheFreshness:
    def test_repeat_calls_identical(self, repo: Path) -> None:
        _save(repo, WORKING, "m1")
        _milestone(repo, "Milestone 1", label="v1.0")
        _save(repo, WORKING, "pending")

        first = (
            graph_topology(repo, cwd=repo).model_dump(),
            working_milestones(repo, cwd=repo).model_dump(),
            working_branches(repo, cwd=repo).model_dump(),
        )
        second = (
            graph_topology(repo, cwd=repo).model_dump(),
            working_milestones(repo, cwd=repo).model_dump(),
            working_branches(repo, cwd=repo).model_dump(),
        )
        assert first == second

    def test_new_commit_visible_immediately(self, repo: Path) -> None:
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1")
        graph_topology(repo, cwd=repo)  # warm every cache
        working_milestones(repo, cwd=repo)
        assert not working_branches(repo, cwd=repo).branches[0].has_unmerged_saves

        _save(repo, WORKING, "m2")  # pending save moves the LEDGER tip
        assert working_branches(repo, cwd=repo).branches[0].has_unmerged_saves
        m2 = _milestone(repo, "Milestone 2", label="v9.9")  # moves the working tip

        graph = graph_topology(repo, cwd=repo)
        branch = next(b for b in graph.branches if b.name == WORKING)
        assert branch.tip_sha == m2
        assert branch.entries[0].sha == m2
        assert branch.entries[0].version_label == "v9.9"
        milestones = working_milestones(repo, cwd=repo).entries
        assert milestones[0].sha == m2
        assert milestones[1].sha == m1
        assert not working_branches(repo, cwd=repo).branches[0].has_unmerged_saves

    def test_non_fast_forward_rewrite_visible_immediately(self, repo: Path) -> None:
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1")
        _save(repo, WORKING, "m2")
        m2 = _milestone(repo, "Milestone 2")
        graph_topology(repo, cwd=repo)  # warm every cache at tip m2
        working_milestones(repo, cwd=repo)
        assert not working_branches(repo, cwd=repo).branches[0].has_unmerged_saves

        # Non-fast-forward: rewrite the working tip back to m1 (the ledger
        # stays at m2's fold, so it is no longer merged into the working tip).
        _git(repo, "branch", "-f", WORKING, m1)

        graph = graph_topology(repo, cwd=repo)
        branch = next(b for b in graph.branches if b.name == WORKING)
        assert branch.tip_sha == m1
        assert branch.entries[0].sha == m1
        assert m2 not in [e.sha for e in branch.entries]
        assert working_milestones(repo, cwd=repo).entries[0].sha == m1
        assert working_branches(repo, cwd=repo).branches[0].has_unmerged_saves

    def test_new_tag_visible_immediately(self, repo: Path) -> None:
        """Tags are mutable independently of tips — labels must never be served
        from a commit-keyed cache."""
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1")
        graph = graph_topology(repo, cwd=repo)  # warm the windowed-log cache
        assert working_milestones(repo, cwd=repo).entries[0].version_label is None
        branch = next(b for b in graph.branches if b.name == WORKING)
        assert branch.entries[0].version_label is None

        _git(repo, "tag", "-a", "version/v1.5", "-m", "v1.5", m1)

        assert working_milestones(repo, cwd=repo).entries[0].version_label == "v1.5"
        graph = graph_topology(repo, cwd=repo)  # same tip SHA → cached log rows
        branch = next(b for b in graph.branches if b.name == WORKING)
        assert branch.entries[0].version_label == "v1.5"


# ---------------------------------------------------------------------------
# (c) Subprocess-count regression — the N+1s must stay dead.
# ---------------------------------------------------------------------------


class TestSubprocessCounts:
    def test_milestones_constant_git_calls(self, repo: Path, git_spy: dict[str, int]) -> None:
        """O(1) git calls regardless of entry count — repo-check, tip resolve,
        one (cached) log, one batched tag read; never one per row — and a warm
        page (shared with the graph's windowed-log cache) skips the log too."""
        for i in range(21):  # > the default page size of 20
            _save(repo, WORKING, f"m{i}")
            _milestone(repo, f"Milestone {i}", label=f"v{i}.0")

        git_spy["n"] = 0
        response = working_milestones(repo, cwd=repo)
        cold_count = git_spy["n"]
        assert len(response.entries) == 20
        # Truncated page: the oldest entry still has parents — no root chip.
        assert response.entries[-1].is_root is False
        assert cold_count <= 4  # measured: 4 (repo check, rev-parse, log, tag map)

        git_spy["n"] = 0
        warm = working_milestones(repo, cwd=repo)
        assert warm.model_dump() == response.model_dump()
        assert git_spy["n"] <= 3  # measured: 3 — the windowed log is cache-served

    def test_graph_second_call_is_cheap(self, repo: Path, git_spy: dict[str, int]) -> None:
        for i in range(5):
            _save(repo, WORKING, f"m{i}")
            _milestone(repo, f"Milestone {i}")
        _save(repo, WORKING, "s-fork")
        fork_source = _save(repo, WORKING, "s-fork2")
        create_working_branch("pricing-fork", repo, at=fork_source, cwd=repo)
        _save(repo, WORKING, "s-post")
        _milestone(repo, "Milestone folds fork source")

        git_spy["n"] = 0
        first = graph_topology(repo, cwd=repo)
        first_count = git_spy["n"]

        git_spy["n"] = 0
        second = graph_topology(repo, cwd=repo)
        second_count = git_spy["n"]

        assert second.model_dump() == first.model_dump()
        # Unchanged repo: every spine, windowed log, ancestry probe and parent
        # read is served from the SHA-keyed caches; what remains is the fixed
        # per-request work (repo check, current/default branch, user slug, one
        # for-each-ref, and one tag read). Default-branch resolution is
        # deliberately live because its remote HEAD ref may move.
        assert second_count <= 9  # measured: 8 (first call: 14)
        assert second_count <= first_count - 6  # the content reads all went away

    def test_working_branches_second_call_is_cheap(
        self, repo: Path, git_spy: dict[str, int]
    ) -> None:
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1")
        for name in ("pricing-a", "pricing-b", "pricing-c"):
            create_working_branch(name, repo, at=m1, cwd=repo)

        git_spy["n"] = 0
        first = working_branches(repo, cwd=repo)
        first_count = git_spy["n"]

        git_spy["n"] = 0
        second = working_branches(repo, cwd=repo)
        second_count = git_spy["n"]

        assert second.model_dump() == first.model_dump()
        assert len(second.branches) == 4
        # Second call: zero per-branch subprocesses (tips come from the single
        # for-each-ref; every unmerged-saves merge-base is cache-served) — the
        # count is the fixed request overhead, i.e. O(1) amortized per branch.
        # The two live default-branch probes are intentionally not cached.
        assert second_count <= first_count - len(second.branches) + 2
        assert second_count <= 8  # measured: 8 (first call: 10)


class TestTreeOfCache:
    """``_tree_of`` is content-addressed: a full-SHA arg is cached per (sha,
    cwd); a ref-name arg (mutable) falls through uncached; failures raise and
    are never memoised. The invariant check reads two trees per call, so the
    cache is what keeps ``working_branch_status`` off a per-call fork pair."""

    def test_full_sha_cached_ref_name_uncached(self, repo: Path, git_spy: dict[str, int]) -> None:
        _save(repo, WORKING, "m1")
        _milestone(repo, "Milestone 1")
        tip = _rev_parse(WORKING, cwd=repo)
        assert tip is not None

        git_spy["n"] = 0
        first = _tree_of(tip, cwd=repo)
        assert git_spy["n"] == 1  # cold: one rev-parse
        git_spy["n"] = 0
        assert _tree_of(tip, cwd=repo) == first
        assert git_spy["n"] == 0  # warm: cache-served, no fork

        # A ref NAME is mutable — it must never take the cached path.
        git_spy["n"] = 0
        assert _tree_of(WORKING, cwd=repo) == first
        assert _tree_of(WORKING, cwd=repo) == first
        assert git_spy["n"] == 2  # one fork each, uncached

    def test_tree_reread_after_history_rewrite(self, repo: Path) -> None:
        """The same commit SHA always names the same tree; a rewrite that puts
        a DIFFERENT commit at the branch resolves to a different SHA (a new
        key), so no stale tree can survive a non-fast-forward move."""
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1")
        _save(repo, WORKING, "m2")
        m2 = _milestone(repo, "Milestone 2")
        tree_m1, tree_m2 = _tree_of(m1, cwd=repo), _tree_of(m2, cwd=repo)
        assert tree_m1 != tree_m2  # the two milestones touched the tree

        _git(repo, "branch", "-f", WORKING, m1)  # non-FF rewrite to the elder
        # Re-resolving the ref yields m1, whose cached tree is m1's — correct.
        assert _tree_of(_rev_parse(WORKING, cwd=repo), cwd=repo) == tree_m1

    def test_status_invariant_check_is_fork_free_when_warm(
        self, repo: Path, git_spy: dict[str, int]
    ) -> None:
        """A healthy repo's second ``check_invariants`` costs no tree fork: both
        ``_tree_of`` reads (working tip, merge-base) are on resolved SHAs and
        cache-served. ``working_branch_status`` inherits the saving."""
        _save(repo, WORKING, "m1")
        _milestone(repo, "Milestone 1")
        assert check_invariants(WORKING, cwd=repo) == []  # warm the caches

        git_spy["n"] = 0
        assert check_invariants(WORKING, cwd=repo) == []
        warm_invariants = git_spy["n"]
        # Only the two ref-name rev-parses (working, ledger) remain; merge-base,
        # is-ancestor and BOTH tree reads are cache-served.
        assert warm_invariants <= 3

        status = working_branch_status(repo, cwd=repo)
        assert status.state == "ready"


# ---------------------------------------------------------------------------
# (d) Fork-credit binary search — identical semantics to the linear scan.
# ---------------------------------------------------------------------------


def _linear_credit(source: str, parent_spine: list[str], fork_point: str, cwd: Path) -> str | None:
    """The pre-optimisation oldest-first linear scan, kept as the oracle."""
    parent_idx = parent_spine.index(fork_point)
    for candidate in reversed(parent_spine[:parent_idx]):
        if _is_ancestor(source, candidate, cwd=cwd):
            return candidate
    return None


class TestForkCredit:
    def _seed_fork_at_save(self, repo: Path) -> tuple[str, str, str]:
        """work: M1..M3, fork crystallized at pending save S, then M4..M6 on
        work (M4 folds S). Returns (fork source save, fold milestone M4, fork name)."""
        for i in range(1, 4):
            _save(repo, WORKING, f"m{i}")
            _milestone(repo, f"Milestone {i}")
        source = _save(repo, WORKING, "spawn-source")
        create_working_branch("pricing-fork", repo, at=source, cwd=repo)
        _save(repo, WORKING, "m4")
        fold = _milestone(repo, "Milestone 4")  # folds the spawn source
        for i in range(5, 7):
            _save(repo, WORKING, f"m{i}")
            _milestone(repo, f"Milestone {i}")
        return source, fold, "pricing-fork"

    def test_binary_search_matches_linear_scan(self, repo: Path) -> None:
        source, fold, fork = self._seed_fork_at_save(repo)
        graph = graph_topology(repo, cwd=repo)
        branch = next(b for b in graph.branches if b.name == fork)
        assert branch.fork_source_sha == source
        # The credit is the OLDEST parent milestone containing the source —
        # several commits below the parent tip — and must equal the linear
        # scan's answer exactly.
        parent = next(b for b in graph.branches if b.name == WORKING)
        parent_spine = _first_parent_spine(parent.tip_sha, cwd=repo)
        assert parent_spine is not None
        assert branch.fork_credit_sha == fold
        assert branch.fork_credit_sha == _linear_credit(
            source, parent_spine, branch.fork_point_sha, repo
        )

    def test_pending_source_has_no_credit(self, repo: Path) -> None:
        """Fork at a pending save the parent never milestones: the source is
        reachable only via the parent's ledger → credit None (both scans)."""
        _save(repo, WORKING, "m1")
        _milestone(repo, "Milestone 1")
        source = _save(repo, WORKING, "still-pending")
        create_working_branch("pricing-fork", repo, at=source, cwd=repo)

        graph = graph_topology(repo, cwd=repo)
        branch = next(b for b in graph.branches if b.name == "pricing-fork")
        assert branch.fork_source_sha == source
        assert branch.fork_credit_sha is None

    def test_credit_mid_spine_binary_boundary(self, tmp_path: Path) -> None:
        """Out-of-engine parent history where the crediting fold sits MID-spine
        (candidates below it don't contain the source), driving the binary
        search through its shrink-right branch; the linear oracle agrees.

        Engine-built histories always credit the oldest candidate (the first
        milestone after a spawn folds the whole ledger), so this boundary is
        only reachable in foreign repos — which the endpoint must tolerate.
        """
        repo = tmp_path / "foreign"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Test Actuary")
        _git(repo, "config", "user.email", "test@example.com")

        def commit(msg: str) -> str:
            (repo / "f.txt").write_text(f"{msg}\n", encoding="utf-8")
            _git(repo, "add", "f.txt")
            _git(repo, "commit", "-m", msg)
            return _git(repo, "rev-parse", "HEAD")

        commit("R")
        c1 = commit("C1")
        c2 = commit("C2")
        _git(repo, "checkout", "-b", "side")
        s = commit("S")  # the fork source: child of C2, so C1/C2 don't contain it
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--no-ff", "-m", "C3", "side")  # C3 contains S
        c3 = _git(repo, "rev-parse", "HEAD")
        c4 = commit("C4")
        # The fork's anchoring merge X: parents [C1, S] via plumbing.
        tree = _git(repo, "rev-parse", f"{c1}^{{tree}}")
        x = _git(repo, "commit-tree", tree, "-p", c1, "-p", s, "-m", "X")

        spine = [x, c1, _git(repo, "rev-list", "--max-parents=0", c1)]
        parent_spine = _first_parent_spine(c4, cwd=repo)
        assert parent_spine == [c4, c3, c2, c1, parent_spine[-1]]

        got = _fork_source_and_credit(spine, c1, parent_spine, None, cwd=repo)
        assert got == (s, c3)  # candidates [C4, C3, C2]: True, True, False
        assert _linear_credit(s, parent_spine, c1, repo) == c3

        # A plain (non-merge) oldest own commit means nothing was folded at
        # the spawn — no source, no credit.
        y = _git(repo, "commit-tree", tree, "-p", c1, "-m", "Y")
        assert _fork_source_and_credit([y, c1], c1, parent_spine, None, cwd=repo) == (None, None)

    def test_no_candidate_contains_source(self, repo: Path) -> None:
        """Candidates exist above the fork point but none contains the source
        (it is pending on the parent ledger) — the newest-candidate probe must
        short-circuit to None, matching the linear scan."""
        _save(repo, WORKING, "m1")
        m1 = _milestone(repo, "Milestone 1")
        source = _save(repo, WORKING, "pending-source")
        create_working_branch("pricing-fork", repo, at=source, cwd=repo)
        # A sibling line advanced past m1 WITHOUT folding the pending source.
        create_working_branch("pricing-sib", repo, at=m1, cwd=repo)
        set_working_branch("pricing-sib", repo, cwd=repo)
        _save(repo, "pricing-sib", "sib1")
        sib_tip = _milestone(repo, "Sibling 1")

        fork_tip = _git(repo, "rev-parse", "pricing-fork")
        spine = _first_parent_spine(fork_tip, cwd=repo)
        parent_spine = _first_parent_spine(sib_tip, cwd=repo)
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        assert spine is not None and parent_spine is not None
        assert m1 in parent_spine  # the shared fork point, with sib_tip above it

        got = _fork_source_and_credit(spine, m1, parent_spine, ledger_tip, cwd=repo)
        assert got == (source, None)
        assert _linear_credit(source, parent_spine, m1, repo) is None


# ---------------------------------------------------------------------------
# Branch enumeration — the single for-each-ref read must thread tips through
# and skip malformed ref lines.
# ---------------------------------------------------------------------------


class TestListBranchesEnumeration:
    def test_malformed_ref_line_is_skipped(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_ok = git_mod._run_git_ok

        def with_garbage_line(*args, **kwargs):
            ok, out = real_ok(*args, **kwargs)
            if args and args[0] == "for-each-ref" and ok:
                out = f"garbage-no-tabs\n{out}"
            return ok, out

        monkeypatch.setattr(git_mod, "_run_git_ok", with_garbage_line)
        listing, tips = git_mod._list_branches_with_tips(cwd=repo)
        assert "garbage-no-tabs" not in tips
        assert "garbage-no-tabs" not in {b.name for b in listing.branches}
        assert WORKING in tips


# ---------------------------------------------------------------------------
# Cache-helper unit coverage — guards, failure bypasses, uncached fallbacks.
# ---------------------------------------------------------------------------


class TestCacheHelpers:
    def test_non_sha_args_take_uncached_path(self, repo: Path) -> None:
        head = _git(repo, "rev-parse", "HEAD")
        assert _is_ancestor("main", WORKING, cwd=repo) is True
        assert _merge_base("main", WORKING, cwd=repo) == _git(repo, "rev-parse", "main")
        assert _commit_parents("HEAD", cwd=repo) == _commit_parents(head, cwd=repo)
        spine = _first_parent_spine(WORKING, cwd=repo)
        assert spine is not None and spine[0] == _git(repo, "rev-parse", WORKING)

    def test_failures_are_not_cached(self, repo: Path) -> None:
        head = _git(repo, "rev-parse", "HEAD")
        # Unreadable full-SHA-shaped object: old failure semantics preserved…
        assert _is_ancestor(_FAKE_SHA, head, cwd=repo) is False
        assert _merge_base(_FAKE_SHA, head, cwd=repo) is None
        assert _commit_parents(_FAKE_SHA, cwd=repo) == []
        assert _first_parent_spine(_FAKE_SHA, cwd=repo) is None
        assert _graph_entries(_FAKE_SHA, 10, {}, cwd=repo) == []
        # …and none of the failures was memoised.
        assert git_mod._is_ancestor_cached.cache_info().currsize == 0
        assert git_mod._merge_base_cached.cache_info().currsize == 0
        assert git_mod._commit_parents_cached.cache_info().currsize == 0
        assert git_mod._first_parent_spine_cached.cache_info().currsize == 0
        assert git_mod._graph_log_cached.cache_info().currsize == 0

    def test_graph_entries_uncached_ref_path(self, repo: Path) -> None:
        """A ref-name tip (not a full SHA) renders identically but bypasses the
        windowed-log cache."""
        _save(repo, WORKING, "m1")
        _milestone(repo, "Milestone 1", label="v1.0")
        labels = git_mod._version_label_map(cwd=repo)
        tip = _git(repo, "rev-parse", WORKING)
        by_ref = _graph_entries(WORKING, 10, labels, cwd=repo)
        by_sha = _graph_entries(tip, 10, labels, cwd=repo)
        assert [e.model_dump() for e in by_ref] == [e.model_dump() for e in by_sha]
        assert git_mod._graph_log_cached.cache_info().currsize == 1  # SHA call only

    def test_clear_content_caches_empties_everything(self, repo: Path) -> None:
        head = _git(repo, "rev-parse", "HEAD")
        main = _git(repo, "rev-parse", "main")
        _is_ancestor(main, head, cwd=repo)
        _merge_base(main, head, cwd=repo)
        _commit_parents(head, cwd=repo)
        _first_parent_spine(head, cwd=repo)
        _graph_entries(head, 10, {}, cwd=repo)
        assert git_mod._is_ancestor_cached.cache_info().currsize == 1
        git_mod._clear_content_caches()
        for cache in (
            git_mod._is_ancestor_cached,
            git_mod._merge_base_cached,
            git_mod._commit_parents_cached,
            git_mod._first_parent_spine_cached,
            git_mod._graph_log_cached,
        ):
            assert cache.cache_info().currsize == 0
