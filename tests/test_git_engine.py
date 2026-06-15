"""Tests for the v1 git engine — the working/ledger branch-pair model.

Temp-repo fixtures throughout; assertions read the commit graph directly via
plumbing so the tests document the model's graph shapes, not just function
return values.
"""

import subprocess
from pathlib import Path

import pytest

from haute._git import (
    GitDomainError,
    GitGuardrailError,
    branch_category,
    check_invariants,
    commit_save,
    get_identity,
    is_eligible_working_branch,
    ledger_name,
    merge_to_working,
    resolve_ledger,
    set_identity,
    set_working_branch,
    working_branch_status,
    working_name,
)
from haute.schemas import SavePipelineRequest, SavePipelineResponse

WORKING = "pricing-dev"
LEDGER = "pricing-dev-save"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _parents(repo: Path, ref: str) -> list[str]:
    out = _git(repo, "rev-list", "--parents", "-n", "1", ref)
    return out.split()[1:]


def _tree(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", f"{ref}^{{tree}}")


def _write_and_save(
    repo: Path, working: str, files: dict[str, str], message: str | None = None
) -> str | None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return commit_save(list(files), working, cwd=repo, message=message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "pipeline_repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# pipeline\n")
    _git(root, "add", "rating.py")
    _git(root, "commit", "-m", "initial pipeline")
    _git(root, "checkout", "-b", WORKING)
    return root


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


class TestResolveLedger:
    def test_spawns_at_working_tip_and_checks_out(self, repo: Path) -> None:
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger = resolve_ledger(WORKING, cwd=repo)
        assert ledger == LEDGER
        assert _git(repo, "rev-parse", LEDGER) == working_tip
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    def test_idempotent_after_spawn(self, repo: Path) -> None:
        resolve_ledger(WORKING, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        resolve_ledger(WORKING, cwd=repo)
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip  # no respawn/reset

    def test_refuses_protected_and_ledger_names(self, repo: Path) -> None:
        with pytest.raises(GitGuardrailError):
            resolve_ledger("main", cwd=repo)
        with pytest.raises(GitGuardrailError):
            resolve_ledger(LEDGER, cwd=repo)

    def test_refuses_missing_working_branch(self, repo: Path) -> None:
        with pytest.raises(GitDomainError):
            resolve_ledger("does-not-exist", cwd=repo)


class TestCommitSave:
    def test_one_save_one_commit_on_ledger(self, repo: Path) -> None:
        sha = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert sha is not None
        assert _git(repo, "rev-parse", LEDGER) == sha
        # working branch untouched by a plain save
        assert _git(repo, "rev-parse", WORKING) == _parents(repo, sha)[0]

    def test_noop_save_produces_no_commit(self, repo: Path) -> None:
        first = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        again = commit_save(["rating.py"], WORKING, cwd=repo)
        assert first is not None and again is None
        assert _git(repo, "rev-parse", LEDGER) == first

    def test_pathspec_scoping_ignores_foreign_staged_content(self, repo: Path) -> None:
        resolve_ledger(WORKING, cwd=repo)
        (repo / "foreign.txt").write_text("user staged this themselves\n")
        _git(repo, "add", "foreign.txt")

        sha = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert sha is not None
        committed = _git(repo, "show", "--name-only", "--format=", sha).splitlines()
        assert committed == ["rating.py"]
        # the foreign file is still staged, untouched, for the user to deal with
        staged = _git(repo, "diff", "--cached", "--name-only").splitlines()
        assert "foreign.txt" in staged

    def test_empty_path_list_is_noop(self, repo: Path) -> None:
        assert commit_save([], WORKING, cwd=repo) is None


class TestMilestoneMerge:
    def test_first_milestone_is_real_merge_with_user_message(self, repo: Path) -> None:
        spawn_point = _git(repo, "rev-parse", WORKING)
        s1 = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        s2 = _write_and_save(repo, WORKING, {"config/factor/age.json": "{}\n"})
        assert s1 is not None and s2 is not None

        milestone = merge_to_working(WORKING, "First working version", cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == milestone
        assert _parents(repo, milestone) == [spawn_point, s2]  # no fast-forward, ever
        assert _git(repo, "log", "-1", "--format=%s", milestone) == "First working version"
        assert _tree(repo, milestone) == _tree(repo, s2)
        # HEAD stayed on the ledger throughout — no checkout dance
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    def test_version_label_becomes_annotated_tag(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        milestone = merge_to_working(WORKING, "Tagged version", tag_label="2.0", cwd=repo)
        assert _git(repo, "rev-parse", "version/2.0^{commit}") == milestone
        with pytest.raises(GitDomainError):
            _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
            merge_to_working(WORKING, "Dup label", tag_label="2.0", cwd=repo)

    def test_invariant_holds_after_merge_and_after_next_save(self, repo: Path) -> None:
        """The grill regression: the naive ancestor check fails exactly here."""
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        merge_to_working(WORKING, "M1", cwd=repo)
        assert check_invariants(WORKING, cwd=repo) == []

        s3 = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        assert s3 is not None
        assert check_invariants(WORKING, cwd=repo) == []
        # document WHY the naive check is wrong: working is NOT an ancestor of
        # the ledger tip once a milestone exists
        is_anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", WORKING, LEDGER],
            cwd=repo,
            capture_output=True,
        )
        assert is_anc.returncode != 0

    def test_second_milestone_chains_first(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        m1 = merge_to_working(WORKING, "M1", cwd=repo)
        s3 = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        m2 = merge_to_working(WORKING, "M2", cwd=repo)
        assert _parents(repo, m2) == [m1, s3]

    def test_refuses_when_no_new_saves(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        merge_to_working(WORKING, "M1", cwd=repo)
        with pytest.raises(GitDomainError, match="No new saves"):
            merge_to_working(WORKING, "M2", cwd=repo)

    def test_refuses_blank_message(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        with pytest.raises(GitDomainError, match="message is required"):
            merge_to_working(WORKING, "   ", cwd=repo)

    def test_refuses_when_working_advanced_externally(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        # a foreign commit lands directly on the working branch (terminal user)
        _git(repo, "checkout", WORKING)
        (repo / "foreign.py").write_text("# external\n")
        _git(repo, "add", "foreign.py")
        _git(repo, "commit", "-m", "external commit on working")
        _git(repo, "checkout", LEDGER)

        violations = check_invariants(WORKING, cwd=repo)
        assert violations, "external advance must be detected"
        with pytest.raises(GitDomainError, match="branch manager"):
            merge_to_working(WORKING, "M1", cwd=repo)


class TestBaton:
    def test_multigeneration_ledger_walk(self, repo: Path) -> None:
        """Child ledger ancestry reaches parent saves up to — and only up to —
        the branch point."""
        s1 = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        s2 = _write_and_save(repo, WORKING, {"config/factor/age.json": "{}\n"})
        m1 = merge_to_working(WORKING, "M1", cwd=repo)

        # parent ledger continues AFTER the child branches at M1
        s3 = _write_and_save(repo, WORKING, {"rating.py": "# v3 post-branch\n"})
        assert s1 and s2 and s3

        # the designed move-then-edit sequence (S13): materialise M1 first,
        # THEN edit, then save — the spawn checkout is tree-identical to M1 so
        # the freshly-written save content survives it
        child = "pricing-child"
        _git(repo, "checkout", m1)
        _git(repo, "branch", child, m1)
        c1 = _write_and_save(repo, child, {"rating.py": "# child edit\n"})
        assert c1 is not None

        reachable = set(_git(repo, "rev-list", c1).splitlines())
        assert s1 in reachable and s2 in reachable, "baton reaches pre-branch saves"
        assert m1 in reachable, "milestone is the hop point"
        assert s3 not in reachable, "post-branch parent saves are correctly excluded"

    def test_child_invariants_clean(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        m1 = merge_to_working(WORKING, "M1", cwd=repo)
        child = "pricing-child"
        _git(repo, "branch", child, m1)
        _write_and_save(repo, child, {"rating.py": "# child\n"})
        assert check_invariants(child, cwd=repo) == []
        merge_to_working(child, "child M1", cwd=repo)
        assert check_invariants(child, cwd=repo) == []


class TestGitState:
    def test_roundtrip(self, tmp_path: Path) -> None:
        from haute._git_state import read_working_branch, write_working_branch

        assert read_working_branch(tmp_path) is None
        write_working_branch(tmp_path, WORKING)
        assert read_working_branch(tmp_path) == WORKING
        assert (tmp_path / ".haute" / "state.json").is_file()

    def test_malformed_state_reads_as_unset(self, tmp_path: Path) -> None:
        from haute._git_state import read_working_branch

        state = tmp_path / ".haute" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text("not json {")
        assert read_working_branch(tmp_path) is None
        state.write_text('{"workingBranch": "   "}')
        assert read_working_branch(tmp_path) is None
        state.write_text('["wrong shape"]')
        assert read_working_branch(tmp_path) is None


class TestSaveProgressV1:
    def test_refused_when_working_branch_configured(self, repo: Path) -> None:
        from haute._git import save_progress
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        (repo / "rating.py").write_text("# changed\n")
        with pytest.raises(GitDomainError, match="use Save in the toolbar"):
            save_progress(repo)

    def test_never_pushes(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import save_progress

        remote = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(remote))
        _git(repo, "remote", "add", "origin", str(remote))

        (repo / "rating.py").write_text("# changed\n")
        result = save_progress(repo)
        assert result.commit_sha
        ls = subprocess.run(
            ["git", "ls-remote", "origin", WORKING],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert ls.stdout.strip() == "", "save_progress must not push (deliberate-push only)"


class TestLedgerCaptureOnSave:
    """Service-level integration: pipeline saves commit to the ledger when —
    and only when — the clone has a working branch configured."""

    @staticmethod
    def _save_body() -> SavePipelineRequest:
        return SavePipelineRequest(name="demo", source_file="demo.py")

    def _service_save(self, root: Path) -> SavePipelineResponse:
        from unittest.mock import patch

        from haute.routes._save_pipeline import SavePipelineService

        svc = SavePipelineService(root)
        with patch.object(svc, "_infer_flatten_schemas"):
            return svc.save(self._save_body())

    def test_no_state_no_commit(self, repo: Path) -> None:
        result = self._service_save(repo)
        assert result.git_sha is None
        ok = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", LEDGER],
            cwd=repo,
            capture_output=True,
        )
        assert ok.returncode != 0, "no ledger may exist without a configured branch"

    def test_configured_save_commits_written_files_to_ledger(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        result = self._service_save(repo)

        assert result.git_sha is not None
        assert _git(repo, "rev-parse", LEDGER) == result.git_sha
        committed = set(
            _git(repo, "show", "--name-only", "--format=", result.git_sha).splitlines()
        )
        assert "demo.py" in committed
        assert "demo.haute.json" in committed
        # state file itself must never enter the ledger
        assert not any(p.startswith(".haute/") for p in committed)

    def test_idempotent_resave_produces_no_second_commit(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        first = self._service_save(repo)
        second = self._service_save(repo)
        assert first.git_sha is not None
        assert second.git_sha is None
        assert _git(repo, "rev-parse", LEDGER) == first.git_sha

    def test_capture_failure_degrades_to_warning(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        # configure an INVALID working branch: ledger-suffixed names are
        # guardrail-refused, so capture fails while the save itself succeeds
        write_working_branch(repo, "broken-save")
        result = self._service_save(repo)
        assert result.status == "saved"
        assert result.git_sha is None
        assert any("version capture failed" in w for w in result.warnings)
        assert (repo / "demo.py").exists()


class TestIdentity:
    def test_get_identity_reads_config(self, repo: Path) -> None:
        name, email = get_identity(repo)
        assert name == "Test Actuary"
        assert email == "test@example.com"

    def test_get_identity_returns_none_or_str(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()
        _git(bare, "init", "-b", "main")
        # A fresh repo may still inherit a global identity in a dev environment,
        # so assert the shape (None or str), not a specific value.
        name, email = get_identity(bare)
        assert name is None or isinstance(name, str)
        assert email is None or isinstance(email, str)

    def test_set_identity_local(self, repo: Path) -> None:
        result = set_identity("New Name", "new@example.com", cwd=repo)
        assert result.scope == "local"
        assert _git(repo, "config", "--local", "user.name") == "New Name"
        assert _git(repo, "config", "--local", "user.email") == "new@example.com"

    def test_set_identity_rejects_blank(self, repo: Path) -> None:
        with pytest.raises(GitDomainError):
            set_identity("  ", "x@y.z", cwd=repo)

    def test_set_identity_rejects_newlines(self, repo: Path) -> None:
        with pytest.raises(GitDomainError):
            set_identity("a\nb", "x@y.z", cwd=repo)

    def test_set_identity_rejects_other_control_chars(self, repo: Path) -> None:
        # Tab and DEL would also corrupt the config file / inject content.
        with pytest.raises(GitDomainError):
            set_identity("a\tb", "x@y.z", cwd=repo)
        with pytest.raises(GitDomainError):
            set_identity("ok", "x@y.z\x7f", cwd=repo)


class TestWorkingBranchStatus:
    def test_unset(self, repo: Path) -> None:
        st = working_branch_status(repo, cwd=repo)
        assert st.state == "unset"
        assert st.working_branch is None
        assert WORKING in st.eligible_branches
        assert "main" not in st.eligible_branches  # protected + default excluded
        assert st.identity_set is True

    def test_eligible_excludes_ledger_branches(self, repo: Path) -> None:
        # Spawn a ledger, then confirm it is never offered as a working branch.
        resolve_ledger(WORKING, cwd=repo)
        st = working_branch_status(repo, cwd=repo)
        assert LEDGER not in st.eligible_branches
        assert WORKING in st.eligible_branches

    def test_ready_after_set(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        st = working_branch_status(repo, cwd=repo)
        assert st.state == "ready"
        assert st.working_branch == WORKING
        assert st.current_branch == LEDGER  # HEAD moved onto the ledger (S10)
        assert st.last_save_sha is not None

    def test_invalid_when_recorded_branch_missing(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, "ghost-branch")
        st = working_branch_status(repo, cwd=repo)
        assert st.state == "invalid"
        assert st.errors

    def test_invalid_when_recorded_branch_ineligible(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, "main")
        st = working_branch_status(repo, cwd=repo)
        assert st.state == "invalid"

    def test_divergent_when_head_moved_away(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _git(repo, "checkout", "main")  # user moves the repo outside haute
        st = working_branch_status(repo, cwd=repo)
        assert st.state == "divergent"
        assert st.current_branch == "main"
        assert st.working_branch == WORKING


class TestSetWorkingBranch:
    def test_adopt_existing(self, repo: Path) -> None:
        result = set_working_branch(WORKING, repo, cwd=repo)
        assert result.working_branch == WORKING
        assert result.state == "ready"
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        from haute._git_state import read_working_branch

        assert read_working_branch(repo) == WORKING

    def test_create_new(self, repo: Path) -> None:
        result = set_working_branch("fresh-line", repo, create=True, cwd=repo)
        assert result.working_branch == "fresh-line"
        assert _git(repo, "rev-parse", "--verify", "fresh-line")  # branch exists
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "fresh-line-save"

    def test_create_refuses_existing(self, repo: Path) -> None:
        with pytest.raises(GitDomainError, match="already exists"):
            set_working_branch(WORKING, repo, create=True, cwd=repo)

    def test_adopt_refuses_missing(self, repo: Path) -> None:
        with pytest.raises(GitDomainError, match="does not exist"):
            set_working_branch("nope", repo, create=False, cwd=repo)

    def test_refuses_protected(self, repo: Path) -> None:
        with pytest.raises(GitGuardrailError):
            set_working_branch("main", repo, cwd=repo)

    def test_refuses_ledger_name(self, repo: Path) -> None:
        with pytest.raises(GitGuardrailError):
            set_working_branch(LEDGER, repo, cwd=repo)
