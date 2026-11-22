"""Signed commits: when git is set to sign every commit (``commit.gpgsign``), every commit
Haute makes is signed with the key and format git is set to use, a milestone as a save is.

Saves are ``git commit``, which reads the setting itself; every other commit (a milestone, a
restore, a branch's first milestone, the copies Create & Move makes, a new repository's first
commit) is ``commit-tree``, which does not, and asks for its signature
(``src/haute/_git_core.py::_commit_tree``). SSH signing stands in for every format: it needs
only ``ssh-keygen``, which every runner has.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from haute._git import (
    commit_milestone,
    create_working_branch,
    restore_version,
    set_working_branch,
)
from haute._git_core import GitBlockedError, _blocked_by, _rev_parse
from tests import _git_engine_helpers
from tests._git_engine_helpers import LEDGER, WORKING, _git, _write_and_save

repo = _git_engine_helpers.repo
unborn_repo = _git_engine_helpers.unborn_repo


def _sign_with_a_key(root: Path, keys: Path) -> None:
    """Git in *root* set to sign every commit with a new SSH key, and to check its
    signatures against it."""
    keys.mkdir(exist_ok=True)
    key = keys / "signing"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    public = (keys / "signing.pub").read_text(encoding="utf-8").strip()
    allowed = keys / "allowed_signers"
    allowed.write_text(f"test@example.com {public}\n", encoding="utf-8")
    _git(root, "config", "gpg.format", "ssh")
    _git(root, "config", "user.signingkey", str(keys / "signing.pub"))
    _git(root, "config", "gpg.ssh.allowedSignersFile", str(allowed))
    _git(root, "config", "commit.gpgsign", "true")


def _signed(root: Path, commit: str) -> bool:
    """Whether *commit* carries a signature that checks against the key git signs with."""
    checked = subprocess.run(
        ["git", "verify-commit", commit], cwd=root, capture_output=True, text=True
    )
    return checked.returncode == 0


def _commits(root: Path, span: str) -> list[str]:
    return _git(root, "rev-list", span).split()


def test_every_commit_haute_makes_is_signed_when_git_is_set_to_sign(
    repo: Path, tmp_path: Path
) -> None:
    _sign_with_a_key(repo, tmp_path / "keys")
    set_working_branch(WORKING, repo, cwd=repo)

    first = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save 1")
    milestone = commit_milestone("M1", repo, cwd=repo).sha
    pending = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save 2")
    _write_and_save(repo, WORKING, {"rating.py": "# v4\n"}, message="save 3")
    assert first is not None and pending is not None

    # A branch forked at a pending save opens at a milestone made for it.
    create_working_branch("exp", repo, at=pending, cwd=repo)
    # Create & Move copies the saves after the milestone onto the new branch.
    create_working_branch("moved", repo, move=True, cwd=repo)
    moved = _commits(repo, "moved..moved-save")
    restored = restore_version(milestone, repo, cwd=repo).sha

    made = [first, milestone, _git(repo, "rev-parse", "exp"), *moved, restored]
    assert len(moved) == 2
    assert [commit for commit in made if not _signed(repo, commit)] == []


def test_a_new_repository_s_first_commit_is_signed(unborn_repo: Path, tmp_path: Path) -> None:
    _sign_with_a_key(unborn_repo, tmp_path / "keys")
    (unborn_repo / "main.py").write_text("x = 1\n")

    set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

    root = _git(unborn_repo, "rev-list", "--max-parents=0", "HEAD")
    assert _signed(unborn_repo, root)


def test_nothing_is_signed_when_git_is_not_set_to_sign(repo: Path) -> None:
    set_working_branch(WORKING, repo, cwd=repo)
    _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save 1")

    milestone = commit_milestone("M1", repo, cwd=repo).sha

    assert "gpgsig" not in _git(repo, "cat-file", "-p", milestone)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("user.signingkey", "missing.pub"),  # the key file is gone
        ("user.signingkey", None),  # no key set
    ],
)
def test_a_commit_that_cannot_be_signed_is_refused_with_its_cause_and_moves_nothing(
    repo: Path, tmp_path: Path, setting: str, value: str | None
) -> None:
    _sign_with_a_key(repo, tmp_path / "keys")
    set_working_branch(WORKING, repo, cwd=repo)
    _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save 1")
    if value is None:
        _git(repo, "config", "--unset", setting)
    else:
        _git(repo, "config", setting, str(tmp_path / "keys" / value))
    working, ledger = _rev_parse(WORKING, cwd=repo), _rev_parse(LEDGER, cwd=repo)

    with pytest.raises(GitBlockedError) as milestone:
        commit_milestone("M1", repo, cwd=repo)
    with pytest.raises(GitBlockedError) as save:
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save 2")

    assert milestone.value.cause == save.value.cause == "signing"
    assert "could not be signed" in str(milestone.value)
    assert (_rev_parse(WORKING, cwd=repo), _rev_parse(LEDGER, cwd=repo)) == (working, ledger)


def test_an_ssh_key_s_words_are_a_signing_failure_only_from_a_commit() -> None:
    """A push through SSH can print the words ssh-keygen prints when it cannot sign."""
    words = 'Load key "/home/a/.ssh/id": incorrect passphrase supplied to decrypt private key'

    assert _blocked_by(words, None) is None
    refused = _blocked_by(words, None, committing=True)
    assert refused is not None and refused.cause == "signing"


def test_every_commit_tree_goes_through_the_one_that_signs() -> None:
    """``commit-tree`` is named only in the git core: a commit made any other way would
    skip the signature a team set git to require."""
    source = Path(__file__).resolve().parents[1] / "src" / "haute"
    naming = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if re.search(r"[\"']commit-tree[\"']", path.read_text(encoding="utf-8"))
    }
    assert naming == {"_git_core.py"}
