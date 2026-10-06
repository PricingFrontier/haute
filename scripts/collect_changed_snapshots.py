"""Stage the screenshot baselines a render changed, for review off the runner.

`.github/workflows/e2e-snapshots.yml` re-renders Playwright baselines on Linux,
which a developer on Windows or macOS cannot do, and hands them back as an
artifact rather than committing them. This is the part that decides what goes
in the artifact: the baselines whose bytes the render actually changed, and
nothing else, so the download is the diff to commit rather than a tree to search.

It lives here rather than inline in the workflow because logic that decides what
a human reviews should be testable, and because the two ways it can quietly
produce an empty answer — a Git query that failed, and a wholly untracked
directory that Git reports as one entry — both look exactly like "nothing
changed" from inside a YAML `run:` block.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Renames and copies carry a second path (the source) in the next record.
_TWO_PATH_STATUSES = frozenset({"R", "C"})


def changed_paths(repo: Path, paths: list[str]) -> list[str]:
    """Return the repository-relative paths Git reports as changed under *paths*.

    `--untracked-files=all` is not optional: Git's default collapses a wholly
    untracked directory into a single entry naming the directory, so a spec
    whose snapshots are all new would come back as one unusable path. `-z`
    avoids the quoting Git applies to unusual names, so a path is whatever lies
    between the separators.
    """
    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            "--",
            *paths,
        ],
        cwd=repo,
        capture_output=True,
        check=True,
        text=True,
    )
    records = [record for record in completed.stdout.split("\0") if record]
    found: list[str] = []
    skip_next = False
    for record in records:
        if skip_next:
            skip_next = False
            continue
        # Two status characters and a space, then the path.
        status, path = record[:2], record[3:]
        if status[:1] in _TWO_PATH_STATUSES or status[1:2] in _TWO_PATH_STATUSES:
            skip_next = True
        if path:
            found.append(path)
    return found


def stage(repo: Path, into: Path, paths: list[str]) -> list[str]:
    """Copy each changed file into *into*, keeping its path, and return them.

    A path Git reports but that no longer exists is a deletion, and a deleted
    baseline is not something to hand back for review; it is skipped rather than
    failing the run.
    """
    staged: list[str] = []
    for relative in paths:
        source = repo / relative
        if not source.is_file():
            continue
        destination = into / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        staged.append(relative)
    return staged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--into", type=Path, required=True)
    parser.add_argument(
        "--path",
        action="append",
        required=True,
        dest="paths",
        help="Repository-relative path to inspect; repeatable.",
    )
    args = parser.parse_args(argv)

    staged = stage(args.repo, args.into, changed_paths(args.repo, args.paths))
    for relative in staged:
        print(relative)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
