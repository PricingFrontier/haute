"""Engine-driven git topology builders for the graph-rail fixtures.

Seeds an EXISTING haute project (the e2e harness shape: root commit on main,
working pair at the root, ``.haute/state.json`` recording it — or a bare pytest
scaffold, where the baseline pair is created first) with a deterministic
multi-branch history. Every history-constructing step goes through the engine —
``set_working_branch`` / ``commit_save`` (the same entry point the
``/api/pipeline/save`` route captures ledger saves through) /
``commit_milestone`` / ``create_working_branch`` / ``archive_working_pair`` —
so every asserted topology is one haute itself can produce. Raw git is used
only for reads and file edits give the saves content; the one deliberate
out-of-band mutation is deleting a ``forks.json`` entry (via the engine's own
``remove_fork``) to simulate a branch created in another clone.

Cases:

* ``rich`` — the composite fixture behind the graph e2e specs and most pytest
  assertions: a 7-milestone spine with version labels and pending saves, a
  crystallized fork at a pending save, forks at older milestones, two forks
  off one commit, a fork-of-fork, two branches rooted at the repo root, an
  archived pair, and a branch with no forks.json entry.
* ``deep`` — >50 milestones plus one old fork; pytest-only, for truncation.

CLI (one-shot per project reset — branch names and version labels collide on a
second run):

    uv run python scripts/e2e_git_topologies.py --seed <project-dir> --case rich

Importable with no side effects; pytest builds the same fixtures in tmp repos.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from haute._git import (
    archive_working_pair,
    commit_milestone,
    commit_save,
    create_working_branch,
    set_working_branch,
)
from haute._git_state import read_working_branch, remove_fork

# Matches scripts/run_frontend_e2e_server.py::E2E_WORKING_BRANCH — the pair the
# harness seeds; a bare scaffold gets the same name so specs stay uniform.
DEFAULT_WORKING_BRANCH = "pricing/haute-e2e/work"

# The file every fixture save edits (unique content per save keeps each one
# capturable — an unchanged tree would produce no ledger commit).
FIXTURE_FILE = "git_viz_fixture.txt"


@dataclass(frozen=True)
class SeededTopology:
    """Handles into a seeded fixture for assertions: role → name / SHA."""

    working: str
    branches: dict[str, str]
    commits: dict[str, str]


def _git_out(project_root: Path, *args: str) -> str:
    """Read-only git query. Construction goes through the engine; reads may not."""
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _branch_exists(project_root: Path, name: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _fork_name(working: str, suffix: str) -> str:
    """A sibling branch name in *working*'s family (same namespace prefix)."""
    if "/" in working:
        return f"{working.rsplit('/', 1)[0]}/{suffix}"
    return suffix


def _ensure_baseline(project_root: Path) -> str:
    """Adopt (or create) the project's working pair; HEAD lands on its ledger.

    The e2e harness already records a pair at the root commit — build on it.
    A bare scaffold (repo + root commit, nothing recorded) gets the same shape
    created through the engine's own adopt-create path.
    """
    working = read_working_branch(project_root)
    if working is None:
        working = DEFAULT_WORKING_BRANCH
        create = not _branch_exists(project_root, working)
        set_working_branch(working, project_root, create=create, cwd=project_root)
    else:
        set_working_branch(working, project_root, cwd=project_root)
    return working


def _save(project_root: Path, working: str, content: str) -> str:
    """One capturable save through the same engine entry point the
    ``/api/pipeline/save`` route uses (``_git.commit_save``)."""
    (project_root / FIXTURE_FILE).write_text(f"{content}\n", encoding="utf-8")
    sha = commit_save([FIXTURE_FILE], working, cwd=project_root, message=f"Save {content}")
    if sha is None:
        raise RuntimeError(f"fixture save {content!r} captured no change")
    return sha


def _milestone(project_root: Path, message: str, label: str | None = None) -> str:
    return commit_milestone(message, project_root, version_label=label, cwd=project_root).sha


def _adopt(project_root: Path, branch: str) -> None:
    set_working_branch(branch, project_root, cwd=project_root)


def seed_rich(project_root: Path) -> SeededTopology:
    """The composite fixture. Final first-parent spines (R = repo root):

    * work          [M7 M6 M5 M4 M3 M2 M1 R] + pending saves P1 P2 (current)
    * crystal       [X M5 .. R]   X crystallized from pending save S1 at tip M5
    * fork-old      [FO3 FO2 FO1 M2 M1 R]
    * twin-a/-b     [T?1 M4 M3 M2 M1 R]     two forks off one commit
    * fork-of-fork  [FF1 FO1 M2 M1 R]       forked from fork-old
    * old-idea      [A1 M1 R]               archived after its milestone
    * indie-a/-b    [I? R]                  rooted at the repo root itself

    Version labels: M2 → v1.0, M5 → v2.0. twin-a's forks.json entry is removed
    (a branch made in another clone); topology must not depend on it.
    """
    work = _ensure_baseline(project_root)
    crystal = _fork_name(work, "crystal")
    fork_old = _fork_name(work, "fork-old")
    fork_of_fork = _fork_name(work, "fork-of-fork")
    twin_a = _fork_name(work, "twin-a")
    twin_b = _fork_name(work, "twin-b")
    indie_a = _fork_name(work, "indie-a")
    indie_b = _fork_name(work, "indie-b")
    old_idea = _fork_name(work, "old-idea")

    commits: dict[str, str] = {}
    commits["R"] = _git_out(project_root, "rev-list", "--max-parents=0", "--first-parent", work)

    # Linear spine M1..M5 with version labels; M3 folds two saves.
    _save(project_root, work, "m1")
    commits["M1"] = _milestone(project_root, "Milestone 1")
    _save(project_root, work, "m2")
    commits["M2"] = _milestone(project_root, "Milestone 2", label="v1.0")
    _save(project_root, work, "m3a")
    _save(project_root, work, "m3b")
    commits["M3"] = _milestone(project_root, "Milestone 3")
    _save(project_root, work, "m4")
    commits["M4"] = _milestone(project_root, "Milestone 4")
    _save(project_root, work, "m5")
    commits["M5"] = _milestone(project_root, "Milestone 5", label="v2.0")

    # Parallel forks off the spine (current branch stays `work`).
    create_working_branch(old_idea, project_root, at=commits["M1"], cwd=project_root)
    create_working_branch(fork_old, project_root, at=commits["M2"], cwd=project_root)
    create_working_branch(twin_a, project_root, at=commits["M4"], cwd=project_root)
    create_working_branch(twin_b, project_root, at=commits["M4"], cwd=project_root)
    create_working_branch(indie_a, project_root, at=commits["R"], cwd=project_root)
    create_working_branch(indie_b, project_root, at=commits["R"], cwd=project_root)

    # Crystallized fork: branch at a PENDING save → an anchoring milestone X
    # whose parents are the spawning tip (M5) and the save (S1).
    commits["S1"] = _save(project_root, work, "s1")
    create_working_branch(crystal, project_root, at=commits["S1"], cwd=project_root)
    commits["X"] = _git_out(project_root, "rev-parse", crystal)

    # Advance `work` past every fork point (parent-advanced-past-fork).
    commits["S2"] = _save(project_root, work, "s2")
    commits["M6"] = _milestone(project_root, "Milestone 6")  # folds S1 + S2
    commits["S3"] = _save(project_root, work, "s3")
    commits["M7"] = _milestone(project_root, "Milestone 7")  # folds S3

    _adopt(project_root, fork_old)
    _save(project_root, fork_old, "fo1")
    commits["FO1"] = _milestone(project_root, "Fork-old 1")
    _save(project_root, fork_old, "fo2")
    commits["FO2"] = _milestone(project_root, "Fork-old 2")
    _save(project_root, fork_old, "fo3")
    commits["FO3"] = _milestone(project_root, "Fork-old 3")
    create_working_branch(fork_of_fork, project_root, at=commits["FO1"], cwd=project_root)

    _adopt(project_root, fork_of_fork)
    _save(project_root, fork_of_fork, "ff1")
    commits["FF1"] = _milestone(project_root, "Fork-of-fork 1")

    _adopt(project_root, old_idea)
    _save(project_root, old_idea, "a1")
    commits["A1"] = _milestone(project_root, "Old idea 1")

    _adopt(project_root, twin_a)
    _save(project_root, twin_a, "ta1")
    commits["TA1"] = _milestone(project_root, "Twin A 1")
    _adopt(project_root, twin_b)
    _save(project_root, twin_b, "tb1")
    commits["TB1"] = _milestone(project_root, "Twin B 1")

    _adopt(project_root, indie_a)
    _save(project_root, indie_a, "i1")
    commits["I1"] = _milestone(project_root, "Indie A 1")
    _adopt(project_root, indie_b)
    _save(project_root, indie_b, "i2")
    commits["I2"] = _milestone(project_root, "Indie B 1")

    # Back on the main line: archive the dead pair (not current → rename only),
    # drop twin-a's forks.json back-link (as if forked in another clone), and
    # leave two pending saves on the current ledger.
    _adopt(project_root, work)
    archived = archive_working_pair(old_idea, project_root, cwd=project_root).archived_as
    remove_fork(project_root, twin_a)
    commits["P1"] = _save(project_root, work, "p1")
    commits["P2"] = _save(project_root, work, "p2")

    return SeededTopology(
        working=work,
        branches={
            "work": work,
            "crystal": crystal,
            "fork_old": fork_old,
            "fork_of_fork": fork_of_fork,
            "twin_a": twin_a,
            "twin_b": twin_b,
            "indie_a": indie_a,
            "indie_b": indie_b,
            "archived": archived,
        },
        commits=commits,
    )


def seed_deep(project_root: Path, milestones: int = 51) -> SeededTopology:
    """A >50-milestone spine plus one fork at the first milestone, so the
    default endpoint window truncates the spine while the fork point stays
    reported from outside it. Pytest-only (too slow for the browser loop)."""
    work = _ensure_baseline(project_root)
    deep_child = _fork_name(work, "deep-child")

    commits: dict[str, str] = {}
    commits["R"] = _git_out(project_root, "rev-list", "--max-parents=0", "--first-parent", work)
    _save(project_root, work, "d1")
    commits["M1"] = _milestone(project_root, "Deep 1")
    create_working_branch(deep_child, project_root, at=commits["M1"], cwd=project_root)
    for i in range(2, milestones + 1):
        _save(project_root, work, f"d{i}")
        commits["tip"] = _milestone(project_root, f"Deep {i}")

    return SeededTopology(
        working=work,
        branches={"work": work, "deep_child": deep_child},
        commits=commits,
    )


CASES = {"rich": seed_rich, "deep": seed_deep}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a haute project with an engine-built git topology fixture."
    )
    parser.add_argument(
        "--seed",
        required=True,
        type=Path,
        metavar="PROJECT_DIR",
        help="haute project root (a git repo; the e2e harness's .tmp-e2e-project)",
    )
    parser.add_argument("--case", required=True, choices=sorted(CASES))
    args = parser.parse_args(argv)

    project_root = args.seed.resolve()
    if not (project_root / ".git").exists():
        parser.error(f"{project_root} is not a git repository")

    topology = CASES[args.case](project_root)
    print(
        json.dumps(
            {
                "case": args.case,
                "working": topology.working,
                "branches": topology.branches,
                "commits": topology.commits,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
