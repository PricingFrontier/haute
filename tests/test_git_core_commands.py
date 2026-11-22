"""Branch naming, protected branches and the git subprocess wrapper.

Branches are named and categorised, the protected branches come from the environment, every text-
mode git subprocess pins UTF-8, and a failure names the git subcommand past its ``-c`` settings.
"""

import subprocess
from pathlib import Path

import pytest

import haute._git_core as git_core
from haute._git import (
    GitError,
    GitGuardrailError,
    branch_category,
    is_eligible_working_branch,
    ledger_name,
    working_name,
)
from tests._git_engine_helpers import (
    LEDGER,
    WORKING,
)


class TestNamingAndCategories:
    def test_ledger_name_roundtrip(self) -> None:
        assert ledger_name(WORKING) == LEDGER
        assert working_name(LEDGER) == WORKING
        assert working_name(WORKING) is None
        assert working_name("-save") is None  # suffix alone is not a ledger

    @pytest.mark.parametrize(
        ("branch", "category"),
        [
            ("main", "protected"),
            ("master", "protected"),
            (WORKING, "working"),
            (LEDGER, "ledger"),
            ("anything-else", "working"),
        ],
    )
    def test_branch_category(self, branch: str, category: str) -> None:
        assert branch_category(branch) == category

    def test_eligibility(self) -> None:
        assert is_eligible_working_branch(WORKING)
        assert not is_eligible_working_branch("main")
        assert not is_eligible_working_branch(LEDGER)


# ---------------------------------------------------------------------------
# Protected-branch configurability + subprocess encoding.
#
# Re-applied from the nick-dev multi-frame branch after the p7×nick-dev merge
# audit found the merge had reverted both into functions p7 kept. The originals
# lived in the v0 tests/test_git.py, which the merge deleted, so the guards are
# re-homed here.
# ---------------------------------------------------------------------------


class TestProtectedBranchConfig:
    def test_env_configured_branch_is_protected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._git import _is_protected, _protected_branches

        monkeypatch.setenv("HAUTE_PROTECTED_BRANCHES", "release, staging ")

        assert _protected_branches() == frozenset({"release", "staging"})
        assert _is_protected("release") is True
        assert _is_protected("staging") is True

    def test_empty_env_config_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from haute._git import _protected_branches

        monkeypatch.setenv("HAUTE_PROTECTED_BRANCHES", "main,,release")

        with pytest.raises(GitGuardrailError, match="empty branch entry"):
            _protected_branches()


class TestGitSubprocessEncoding:
    def test_all_text_subprocess_run_calls_pin_utf8(self) -> None:
        # Every TEXT-mode subprocess.run in _git_core.py must pin encoding='utf-8' so
        # branch names and stderr round-trip consistently across platforms.
        # Binary calls (no text=True, e.g. `git archive`) decode nothing and are
        # exempt — the p7-adapted form of nick-dev's original all-calls guard.
        import ast

        tree = ast.parse(Path("src/haute/_git_core.py").read_text(encoding="utf-8"))
        offenders: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "run":
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "subprocess":
                continue
            is_text = any(
                kw.arg == "text" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            )
            if not is_text:
                continue
            has_utf8 = any(
                kw.arg == "encoding"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "utf-8"
                for kw in node.keywords
            )
            if not has_utf8:
                offenders.append(node.lineno)

        assert offenders == [], (
            "Text-mode git subprocess decoding must pin encoding='utf-8' so branch "
            f"names and stderr round-trip consistently across platforms. Offenders: {offenders}"
        )

    def test_byte_stdin_path_replacement_decodes_git_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        results = iter(
            [
                subprocess.CompletedProcess(
                    ["git", "hash-object"], 0, stdout=b"\xffobject\n", stderr=b""
                ),
                subprocess.CompletedProcess(
                    ["git", "check-ignore"], 128, stdout=b"", stderr=b"\xffdiagnostic\n"
                ),
            ]
        )
        monkeypatch.setattr(git_core.subprocess, "run", lambda *_args, **_kwargs: next(results))

        assert git_core._run_git("hash-object", "--stdin", input_text="payload\n") == "�object"
        with pytest.raises(GitError, match="�diagnostic"):
            git_core._run_git("check-ignore", "--stdin", input_text="path\n")


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (("commit", "-m", "x"), "commit"),
        (("-c", "core.hooksPath=/dev/null", "commit", "-m", "x"), "commit"),
        (("-c", "a=1", "-c", "b=2", "status"), "status"),
    ],
)
def test_a_failure_names_the_git_subcommand_past_its_settings(
    args: tuple[str, ...], named: str
) -> None:
    from haute._git_core import _subcommand

    assert _subcommand(args) == named


# ---------------------------------------------------------------------------
# A cause outside Haute that git names is said with what to do (GitBlockedError):
# never "not a repository", a missing identity, or "Git refused this".
# ---------------------------------------------------------------------------


def _blocked_project(tmp_path: Path) -> Path:
    from haute._git import set_working_branch

    root = tmp_path / "project"
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Test Actuary"),
        ("config", "user.email", "test@example.com"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / "rating.py").write_text("# pipeline\n")
    subprocess.run(["git", "add", "rating.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    set_working_branch(WORKING, root, create=True, cwd=root)
    (root / "rating.py").write_text("# changed\n")
    return root


def _block(root: Path, cause: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if cause == "lock":
        (root / ".git" / "index.lock").write_text("")
    elif cause == "untrusted-folder":
        # Git's own switch for a folder another account owns, as on a network share.
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    elif cause == "damaged-config":
        with (root / ".git" / "config").open("a", encoding="utf-8") as config:
            config.write("[core\n")
    else:
        # A signing program that cannot run: git cannot sign, as with a gpg with no key.
        subprocess.run(["git", "config", "commit.gpgsign", "true"], cwd=root, check=True)
        subprocess.run(["git", "config", "gpg.program", "no-such-gpg"], cwd=root, check=True)


_SAID = {
    "lock": "git found its lock file .git/index.lock. Wait for it to finish",
    "untrusted-folder": "To trust the folder, run: git config --global --add safe.directory",
    "damaged-config": "Git can't read its settings: line ",
    "signing": "this one could not be signed, so nothing was recorded",
}


@pytest.mark.parametrize("cause", list(_SAID))
def test_a_cause_git_names_is_said_by_the_capture_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    from haute._git import GitBlockedError, commit_milestone, working_branch_status
    from haute.routes._save_pipeline import capture_save_in_ledger

    root = _blocked_project(tmp_path)
    _block(root, cause, monkeypatch)

    warnings: list[str] = []
    captured = capture_save_in_ledger(root, ["rating.py"], warnings)
    with pytest.raises(GitBlockedError) as refused:
        commit_milestone("Milestone", root, cwd=root)
    status = working_branch_status(root, cwd=root)

    assert captured.git_sha is None and captured.uncaptured == ["rating.py"]
    assert captured.identity_required is False
    assert len(warnings) == 1 and warnings[0].startswith("Changes saved; version capture failed:")
    assert _SAID[cause] in warnings[0]
    assert refused.value.cause == cause and _SAID[cause] in str(refused.value)
    assert "git init" not in str(refused.value) + warnings[0]
    if cause in ("untrusted-folder", "damaged-config"):
        # The history is there: readiness is not "no-repository", and says why.
        assert status.state == "blocked"
        assert len(status.errors) == 1 and _SAID[cause] in status.errors[0]
    else:
        assert status.state == "ready"


def test_a_failure_git_names_no_cause_for_stays_gits_own(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)

    with pytest.raises(GitError) as failed:
        git_core._run_git("rev-parse", "--verify", "no-such-ref", cwd=root)
    assert type(failed.value) is GitError
    assert git_core._run_git_ok("rev-parse", "--verify", "no-such-ref", cwd=root)[0] is False
