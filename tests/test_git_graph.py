"""Tests for the graph-topology view (GET /api/git/graph + graph_topology).

Fixtures are seeded through the engine builders in scripts/e2e_git_topologies
(the same module the graph e2e specs invoke), so every asserted topology is
one haute itself can produce. The one raw-git construction (an orphan branch)
deliberately models an out-of-engine repo state the endpoint must tolerate.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from haute._git import (
    commit_milestone,
    commit_save,
    create_working_branch,
    graph_topology,
    milestone_saves,
    set_working_branch,
)
from scripts.e2e_git_topologies import SeededTopology, seed_deep, seed_rich
from tests._git_helpers import git_run as _git
from tests._git_helpers import init_repo as _init_repo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from haute.schemas import GitGraphBranch, GitGraphResponse


# ---------------------------------------------------------------------------
# Fixtures — the rich composite is expensive to seed, so it is built once per
# module and only ever read (graph_topology is a pure read; a test proves it).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rich_repo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, SeededTopology]:
    repo = _init_repo(tmp_path_factory.mktemp("rich-repo"))
    return repo, seed_rich(repo)


@pytest.fixture(scope="module")
def rich_graph(rich_repo: tuple[Path, SeededTopology]) -> GitGraphResponse:
    repo, _ = rich_repo
    return graph_topology(repo, cwd=repo)


def _branch(graph: GitGraphResponse, name: str) -> GitGraphBranch:
    return next(b for b in graph.branches if b.name == name)


def _expected_order(topo: SeededTopology) -> list[str]:
    # The working branch claims first; here `work` is also the deepest (8), so
    # the rest follow by spine depth DESC then name ASC: crystal(7), the
    # length-6 tie group in name order, fork-of-fork(5), the archived pair(3),
    # and the length-2 tie group in name order.
    b = topo.branches
    return [
        b["work"],
        b["crystal"],
        b["fork_old"],
        b["twin_a"],
        b["twin_b"],
        b["fork_of_fork"],
        b["archived"],
        b["indie_a"],
        b["indie_b"],
    ]


# ---------------------------------------------------------------------------
# Topology — order, spines, parents
# ---------------------------------------------------------------------------


class TestRichTopology:
    def test_order_is_spine_depth_then_name(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        assert rich_graph.order == _expected_order(topo)
        assert [b.name for b in rich_graph.branches] == rich_graph.order
        assert rich_graph.working_branch == topo.working

    def test_spine_entries_newest_first_with_parent_chain(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        c = topo.commits
        work = _branch(rich_graph, topo.branches["work"])
        assert [e.sha for e in work.entries] == [
            c["M7"],
            c["M6"],
            c["M5"],
            c["M4"],
            c["M3"],
            c["M2"],
            c["M1"],
            c["R"],
        ]
        # First parent of each entry is the next (older) spine entry.
        for upper, lower in zip(work.entries, work.entries[1:]):
            assert upper.parents[0] == lower.sha
        assert work.entries[-1].parents == []  # the root has none
        # Merge milestones carry the folded ledger tip as second parent.
        assert work.entries[0].parents == [c["M6"], c["S3"]]
        assert work.entries[1].parents == [c["M5"], c["S2"]]
        assert work.tip_sha == c["M7"]
        assert work.truncated is False

    def test_crystallized_fork_spine_contains_spawning_history(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        c = topo.commits
        crystal = _branch(rich_graph, topo.branches["crystal"])
        assert [e.sha for e in crystal.entries] == [
            c["X"],
            c["M5"],
            c["M4"],
            c["M3"],
            c["M2"],
            c["M1"],
            c["R"],
        ]
        # The anchoring milestone merges the spawning tip and the save.
        assert crystal.entries[0].parents == [c["M5"], c["S1"]]


# ---------------------------------------------------------------------------
# Fork attachments — claim-based, ancestry-derived
# ---------------------------------------------------------------------------


class TestForkAttachments:
    def test_fork_points_and_owners(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        b, c = topo.branches, topo.commits
        expected = {
            b["work"]: (None, None),
            b["crystal"]: (c["M5"], b["work"]),
            b["fork_old"]: (c["M2"], b["work"]),
            b["twin_a"]: (c["M4"], b["work"]),
            b["twin_b"]: (c["M4"], b["work"]),
            b["fork_of_fork"]: (c["FO1"], b["fork_old"]),
            b["archived"]: (c["M1"], b["work"]),
            b["indie_a"]: (c["R"], b["work"]),
            b["indie_b"]: (c["R"], b["work"]),
        }
        actual = {br.name: (br.fork_point_sha, br.fork_of) for br in rich_graph.branches}
        assert actual == expected

    def test_crystallized_fork_attaches_at_spawning_tip_not_the_save(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        # The user forked at pending save S1, but the branch topologically
        # attaches at the spawning branch's tip milestone (goal-1 §2.2).
        _, topo = rich_repo
        crystal = _branch(rich_graph, topo.branches["crystal"])
        assert crystal.fork_point_sha == topo.commits["M5"]
        assert crystal.fork_point_sha != topo.commits["S1"]

    def test_parent_advanced_past_fork(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        # `work` gained M6/M7 after every fork; attachments stay at the
        # historical fork points, not the advanced tip.
        _, topo = rich_repo
        work = _branch(rich_graph, topo.branches["work"])
        crystal = _branch(rich_graph, topo.branches["crystal"])
        assert work.tip_sha == topo.commits["M7"]
        assert crystal.fork_point_sha == topo.commits["M5"]

    def test_two_forks_off_one_commit_tie_break_by_name(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        twin_a, twin_b = topo.branches["twin_a"], topo.branches["twin_b"]
        assert _branch(rich_graph, twin_a).fork_point_sha == topo.commits["M4"]
        assert _branch(rich_graph, twin_b).fork_point_sha == topo.commits["M4"]
        assert rich_graph.order.index(twin_a) < rich_graph.order.index(twin_b)

    def test_topology_survives_missing_forks_json_entry(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        # twin-a's forks.json entry was deleted (a branch made in another
        # clone) — the ancestry-derived fork point must be unaffected.
        _, topo = rich_repo
        twin_a = _branch(rich_graph, topo.branches["twin_a"])
        assert twin_a.forked_from is None
        assert twin_a.fork_point_sha == topo.commits["M4"]

    def test_forked_from_is_passthrough_not_topology(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        c = topo.commits
        # forks.json records the commit the user forked AT (the pending save
        # for a crystallized fork) — distinct from the ancestry fork point.
        crystal = _branch(rich_graph, topo.branches["crystal"])
        assert crystal.forked_from == c["S1"]
        assert crystal.fork_point_sha == c["M5"]
        assert _branch(rich_graph, topo.branches["fork_old"]).forked_from == c["M2"]
        assert _branch(rich_graph, topo.branches["work"]).forked_from is None

    def test_fork_one_milestone_ahead_does_not_steal_the_working_spine(
        self, tmp_path: Path
    ) -> None:
        # The pathology the working-first claim order exists for: a fork made
        # at the working tip and advanced ONE milestone has the deeper spine;
        # depth-first claiming would hand it the working branch's entire
        # history and render the user's own view as a fork of it.
        repo = _init_repo(tmp_path)
        working = "pricing/haute-e2e/work"
        fork = "pricing/haute-e2e/ahead"
        set_working_branch(working, repo, create=True, cwd=repo)
        (repo / "f.txt").write_text("one\n")
        commit_save(["f.txt"], working, cwd=repo)
        tip = commit_milestone("Milestone", repo, cwd=repo).sha
        create_working_branch(fork, repo, at=tip, cwd=repo)
        set_working_branch(fork, repo, cwd=repo)
        (repo / "f.txt").write_text("two\n")
        commit_save(["f.txt"], fork, cwd=repo)
        commit_milestone("Ahead", repo, cwd=repo)
        set_working_branch(working, repo, cwd=repo)

        graph = graph_topology(repo, cwd=repo)
        assert graph.order[0] == working
        me = _branch(graph, working)
        assert me.fork_point_sha is None
        assert me.fork_of is None
        ahead = _branch(graph, fork)
        assert ahead.fork_point_sha == tip  # attaches AT the working tip
        assert ahead.fork_of == working

    def test_crystallized_fork_at_pending_save_does_not_steal_the_working_spine(
        self, tmp_path: Path
    ) -> None:
        # Same pathology without ever adopting the fork: branching at a
        # PENDING save crystallizes an anchoring milestone on the fork, so it
        # is spawning spine + 1 while the working branch hasn't moved.
        repo = _init_repo(tmp_path)
        working = "pricing/haute-e2e/work"
        fork = "pricing/haute-e2e/ahead"
        set_working_branch(working, repo, create=True, cwd=repo)
        (repo / "f.txt").write_text("one\n")
        commit_save(["f.txt"], working, cwd=repo)
        tip = commit_milestone("Milestone", repo, cwd=repo).sha
        (repo / "f.txt").write_text("two\n")
        save = commit_save(["f.txt"], working, cwd=repo)
        assert save is not None
        create_working_branch(fork, repo, at=save, cwd=repo)

        graph = graph_topology(repo, cwd=repo)
        assert graph.order[0] == working
        me = _branch(graph, working)
        assert me.fork_point_sha is None
        assert me.fork_of is None
        crystal = _branch(graph, fork)
        assert crystal.fork_point_sha == tip  # attaches AT the working tip
        assert crystal.fork_of == working

    def test_two_roots_forest(self, tmp_path: Path) -> None:
        # A branch sharing NO history (e.g. fetched from an unrelated clone)
        # roots its own tree: the fork forest is real. Constructed with raw
        # git on purpose — haute must describe states it didn't create.
        repo = _init_repo(tmp_path)
        working = "pricing/haute-e2e/work"
        set_working_branch(working, repo, create=True, cwd=repo)
        (repo / "f.txt").write_text("one\n")
        commit_save(["f.txt"], working, cwd=repo)
        commit_milestone("Milestone", repo, cwd=repo)
        _git(repo, "checkout", "--orphan", "solo-line")
        _git(repo, "commit", "-m", "Independent root")
        _git(repo, "checkout", f"{working}-save")

        graph = graph_topology(repo, cwd=repo)
        assert graph.order == [working, "solo-line"]
        assert _branch(graph, working).fork_point_sha is None
        assert _branch(graph, working).fork_of is None
        solo = _branch(graph, "solo-line")
        assert solo.fork_point_sha is None
        assert solo.fork_of is None
        assert solo.entries[-1].is_root is True


# ---------------------------------------------------------------------------
# Merge-parents ⇔ folded-saves — the invariant behind the rail's magnifier gate
# ---------------------------------------------------------------------------


class TestMergeParentsMeanSaves:
    def test_two_parents_iff_saves_everywhere(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        # The UI shows a magnifier iff ``parents.length >= 2``; that stands in
        # for "folded saves exist" because the engine never commits an empty
        # fold (merge_to_working refuses when base == ledger tip, and
        # crystallization requires a pending save). Lock the equivalence on
        # every spine commit of every branch.
        repo, _ = rich_repo
        checked = 0
        for branch in rich_graph.branches:
            for entry in branch.entries:
                has_saves = len(milestone_saves(entry.sha, cwd=repo).saves) > 0
                assert (len(entry.parents) >= 2) == has_saves, (branch.name, entry.sha)
                checked += 1
        assert checked > 0


# ---------------------------------------------------------------------------
# Version labels, root tagging, archived pairs
# ---------------------------------------------------------------------------


class TestEntryMetadata:
    def test_version_labels_from_batched_tag_lookup(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        c = topo.commits
        work = _branch(rich_graph, topo.branches["work"])
        labels = {e.sha: e.version_label for e in work.entries}
        assert labels[c["M2"]] == "v1.0"
        assert labels[c["M5"]] == "v2.0"
        assert [sha for sha, label in labels.items() if label] == [c["M5"], c["M2"]]
        # Shared spine commits carry the label on every branch listing them.
        crystal = _branch(rich_graph, topo.branches["crystal"])
        assert {e.sha: e.version_label for e in crystal.entries}[c["M5"]] == "v2.0"

    def test_root_tagged_when_window_reaches_it(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        work = _branch(rich_graph, topo.branches["work"])
        assert work.entries[-1].sha == topo.commits["R"]
        assert work.entries[-1].is_root is True
        assert all(e.is_root is False for e in work.entries[:-1])

    def test_archived_pair_included_and_flagged(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        archived = _branch(rich_graph, topo.branches["archived"])
        assert archived.is_archived is True
        assert archived.is_current is False
        assert archived.forked_from == topo.commits["M1"]  # back-link renamed with the pair
        assert [b.name for b in rich_graph.branches if b.is_archived] == [archived.name]

    def test_is_current_only_on_the_working_branch(
        self, rich_repo: tuple[Path, SeededTopology], rich_graph: GitGraphResponse
    ) -> None:
        _, topo = rich_repo
        assert [b.name for b in rich_graph.branches if b.is_current] == [topo.branches["work"]]


# ---------------------------------------------------------------------------
# Truncation — entries window after fork computation
# ---------------------------------------------------------------------------


class TestTruncation:
    def test_window_truncates_entries_not_fork_points(
        self, rich_repo: tuple[Path, SeededTopology]
    ) -> None:
        repo, topo = rich_repo
        c = topo.commits
        graph = graph_topology(repo, cwd=repo, limit=3)
        # Same forest as the unwindowed call — fork points use full spines.
        assert graph.order == _expected_order(topo)

        work = _branch(graph, topo.branches["work"])
        assert work.truncated is True
        assert [e.sha for e in work.entries] == [c["M7"], c["M6"], c["M5"]]
        # Truncation-aware root tagging: the windowed last entry isn't the root.
        assert all(e.is_root is False for e in work.entries)

        fork_old = _branch(graph, topo.branches["fork_old"])
        assert fork_old.truncated is True
        assert c["M2"] not in [e.sha for e in fork_old.entries]
        assert fork_old.fork_point_sha == c["M2"]  # reported from outside the window

        indie_a = _branch(graph, topo.branches["indie_a"])
        assert indie_a.truncated is False
        assert indie_a.entries[-1].sha == c["R"]
        assert indie_a.entries[-1].is_root is True

    def test_deep_spine_truncates_at_default_limit(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        topo = seed_deep(repo)
        graph = graph_topology(repo, cwd=repo)

        work = _branch(graph, topo.branches["work"])
        assert work.truncated is True
        assert len(work.entries) == 50
        assert work.tip_sha == topo.commits["tip"]
        assert all(e.is_root is False for e in work.entries)
        # 51 milestones + root: M1 and R fall outside the 50-entry window …
        window = {e.sha for e in work.entries}
        assert topo.commits["M1"] not in window
        assert topo.commits["R"] not in window

        # … yet the old fork still attaches there.
        child = _branch(graph, topo.branches["deep_child"])
        assert child.truncated is False
        assert child.fork_point_sha == topo.commits["M1"]
        assert child.fork_of == topo.branches["work"]
        assert [e.sha for e in child.entries] == [topo.commits["M1"], topo.commits["R"]]
        assert child.entries[-1].is_root is True

        # The magnifier-gate invariant holds on the deep fixture too.
        for entry in work.entries[:5] + child.entries:
            has_saves = len(milestone_saves(entry.sha, cwd=repo).saves) > 0
            assert (len(entry.parents) >= 2) == has_saves


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------


def _repo_state(repo: Path) -> tuple[str, str, str]:
    return (
        _git(repo, "for-each-ref"),  # every branch + tag ref with its target
        _git(repo, "symbolic-ref", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )


class TestReadOnly:
    def test_graph_topology_moves_nothing(self, rich_repo: tuple[Path, SeededTopology]) -> None:
        repo, _ = rich_repo
        before = _repo_state(repo)
        graph_topology(repo, cwd=repo)
        graph_topology(repo, cwd=repo, limit=2)
        assert _repo_state(repo) == before


# ---------------------------------------------------------------------------
# Route — GET /api/git/graph
# ---------------------------------------------------------------------------


class TestGraphRoute:
    def test_handler_is_sync(self) -> None:
        import asyncio

        from haute.routes.git import git_graph

        assert not asyncio.iscoroutinefunction(git_graph)

    def test_returns_the_seeded_forest(
        self,
        client: TestClient,
        rich_repo: tuple[Path, SeededTopology],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo, topo = rich_repo
        monkeypatch.chdir(repo)
        res = client.get("/api/git/graph")
        assert res.status_code == 200
        body = res.json()
        assert body["working_branch"] == topo.working
        assert body["order"] == _expected_order(topo)
        work = next(b for b in body["branches"] if b["name"] == topo.branches["work"])
        assert work["is_current"] is True
        assert work["tip_sha"] == topo.commits["M7"]
        assert work["entries"][0]["parents"] == [topo.commits["M6"], topo.commits["S3"]]
        assert work["entries"][1]["parents"] == [topo.commits["M5"], topo.commits["S2"]]
        assert "folded_save_count" not in work["entries"][1]  # dropped; UI derives from parents
        crystal = next(b for b in body["branches"] if b["name"] == topo.branches["crystal"])
        assert crystal["fork_point_sha"] == topo.commits["M5"]
        assert crystal["fork_of"] == topo.branches["work"]

    def test_limit_is_validated_and_applied(
        self,
        client: TestClient,
        rich_repo: tuple[Path, SeededTopology],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo, topo = rich_repo
        monkeypatch.chdir(repo)
        assert client.get("/api/git/graph?limit=0").status_code == 422
        assert client.get("/api/git/graph?limit=501").status_code == 422
        res = client.get("/api/git/graph?limit=1")
        assert res.status_code == 200
        work = next(b for b in res.json()["branches"] if b["name"] == topo.branches["work"])
        assert len(work["entries"]) == 1
        assert work["truncated"] is True

    def test_not_a_git_repo_is_a_verbatim_domain_error(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        res = client.get("/api/git/graph")
        assert res.status_code == 400
        assert res.json()["detail"] == "Not a git repository. Run 'git init' first."
