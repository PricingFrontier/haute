"""Tests for the v1 git engine — the working/ledger branch-pair model.

Temp-repo fixtures throughout; assertions read the commit graph directly via
plumbing so the tests document the model's graph shapes, not just function
return values.
"""

import subprocess
import threading
from pathlib import Path

import pytest

import haute._git_core as git_core
import haute._git_remote as git_remote
import haute._git_setup as git_setup
import haute._git_transactions as git_transactions
from haute._git import (
    GitDomainError,
    GitError,
    GitGuardrailError,
    GitMilestoneForkError,
    GitPushRejectedError,
    _slugify,
    archive_commit,
    archive_working_pair,
    branch_away,
    branch_category,
    check_invariants,
    commit_context,
    commit_milestone,
    commit_save,
    create_working_branch,
    delete_working_pair,
    divergence_state,
    fast_forward_pair,
    fetch_pair,
    get_identity,
    is_eligible_working_branch,
    ledger_name,
    list_remotes,
    merge_to_working,
    milestone_saves,
    move_to_commit,
    pending_ledger_saves,
    push_working_pair,
    resolve_ledger,
    restore_working_pair,
    set_identity,
    set_working_branch,
    working_branch_status,
    working_branches,
    working_milestones,
    working_name,
)
from haute._types import GraphNode, NodeData, PipelineGraph
from haute.graph_utils import _sanitize_func_name
from haute.schemas import SavePipelineRequest, SavePipelineResponse


def _data_input_config(label: str) -> str:
    """Relative config path the save flow writes for a dataInput node *label*."""
    return f"config/data_input/{_sanitize_func_name(label)}.json"


WORKING = "pricing-dev"
LEDGER = "pricing-dev-save"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
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


class TestSlugify:
    """`_slugify` makes user-supplied text safe for use as a git ref component.

    It still backs the user-slug derivation in production, so its guarantees are
    pinned here as direct unit tests (these were the only `_slugify` tests; they
    moved here when the v0 git surface and its test file were removed).
    """

    def test_basic(self) -> None:
        assert _slugify("Update area factors") == "update-area-factors"

    def test_special_chars(self) -> None:
        assert _slugify("Fix postcode (v2)") == "fix-postcode-v2"

    def test_leading_trailing_dashes(self) -> None:
        assert _slugify("---hello---") == "hello"

    def test_empty_returns_user(self) -> None:
        assert _slugify("") == "user"

    def test_numbers(self) -> None:
        assert _slugify("Add NCD step 3") == "add-ncd-step-3"

    def test_strips_emoji(self) -> None:
        # The real protection against odd input: emoji and other non-ASCII are
        # collapsed away, leaving only the git-safe ASCII slug.
        slug = _slugify("Rocket launch \U0001f680")
        assert "\U0001f680" not in slug
        assert slug == "rocket-launch"

    def test_handles_long_description(self) -> None:
        # A long ASCII description slugifies to itself (lowercased, dash-joined)
        # — `_slugify` imposes no length cap of its own.
        long_desc = "a" * 250
        assert _slugify(long_desc) == long_desc


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
        # The path-scoped commit must also reconcile its temporary staging.
        # Leaving the saved path in the index makes a completed save appear as
        # both staged and unstaged (MM) in the branch manager.
        assert _git(repo, "status", "--porcelain", "--", "rating.py") == ""

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

    def test_milestone_captures_residual_tracked_project_changes(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"uv.lock": "locked-v1\n"})
        # A tracked project file can change outside the canvas after the last
        # Haute save (dependency locking is the common setup-time example).
        (repo / "uv.lock").write_text("locked-v2\n")
        # Also reproduce an index/worktree cancellation: the staged version is
        # stale, while the working file is already identical to HEAD.
        (repo / "rating.py").write_text("# stale staged version\n")
        _git(repo, "add", "rating.py")
        (repo / "rating.py").write_text("# pipeline\n")
        assert _git(repo, "status", "--porcelain", "--", "rating.py").startswith("MM")

        milestone = commit_milestone("Include project state", repo, cwd=repo).sha

        assert _git(repo, "show", f"{milestone}:uv.lock") == "locked-v2"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_version_label_becomes_annotated_tag(self, repo: Path) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        milestone = merge_to_working(WORKING, "Tagged version", tag_label="2.0", cwd=repo)
        assert _git(repo, "cat-file", "-t", "refs/tags/version/2.0") == "tag"
        assert _git(repo, "rev-parse", "version/2.0^{commit}") == milestone
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        working_tip = _git(repo, "rev-parse", WORKING)
        with pytest.raises(GitDomainError, match="already exists"):
            merge_to_working(WORKING, "Dup label", tag_label="2.0", cwd=repo)
        # A rejected label cannot advance the milestone branch and strand an
        # unlabelled version.
        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", "version/2.0^{commit}") == milestone

    @pytest.mark.parametrize(
        "label",
        ["release candidate", "release..candidate", "release.lock", "release@{candidate"],
    )
    def test_invalid_version_label_refuses_before_branch_mutation(
        self, repo: Path, label: str
    ) -> None:
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        working_tip = _git(repo, "rev-parse", WORKING)

        with pytest.raises(GitDomainError, match="not a valid Git tag name"):
            merge_to_working(WORKING, "Tagged version", tag_label=label, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "tag", "--list", "version/*") == ""

    def test_label_transaction_failure_leaves_branch_and_tag_unchanged(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        working_tip = _git(repo, "rev-parse", WORKING)
        real_run = git_core._run_git

        def fail_ref_transaction(*args: str, **kwargs: object) -> str:
            if args[:2] == ("update-ref", "--stdin"):
                raise GitError("injected ref transaction failure")
            return real_run(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(git_transactions, "_run_git", fail_ref_transaction)

        with pytest.raises(GitError, match="injected ref transaction failure"):
            merge_to_working(WORKING, "Tagged version", tag_label="2.0", cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "tag", "--list", "version/2.0") == ""

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

    def test_prefs_roundtrip_and_isolation(self, tmp_path: Path) -> None:
        # Prefs live in their own file, default to empty, and don't disturb the
        # working-branch state (and vice versa).
        from haute._git_state import (
            read_prefs,
            read_working_branch,
            write_pref,
            write_working_branch,
        )

        assert read_prefs(tmp_path) == {}
        write_working_branch(tmp_path, WORKING)
        write_pref(tmp_path, "skipSwitchConfirm", True)
        assert read_prefs(tmp_path) == {"skipSwitchConfirm": True}
        assert read_working_branch(tmp_path) == WORKING  # untouched by the pref
        write_pref(tmp_path, "other", "x")  # preserves existing keys
        assert read_prefs(tmp_path) == {"skipSwitchConfirm": True, "other": "x"}

    def test_get_set_prefs_engine_wrappers(self, tmp_path: Path) -> None:
        from haute._git import GitPrefs, get_prefs, set_prefs

        assert get_prefs(tmp_path).skip_switch_confirm is False
        set_prefs(GitPrefs(skip_switch_confirm=True), tmp_path)
        assert get_prefs(tmp_path).skip_switch_confirm is True


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
        with patch.object(svc, "_validate_api_inputs_have_schemas"):
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
        committed = set(_git(repo, "show", "--name-only", "--format=", result.git_sha).splitlines())
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

    @staticmethod
    def _strip_identity(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make `repo` genuinely identity-less, isolated from the dev's config.

        A restored hosted container looks exactly like this: a real repo with
        no user.name/user.email anywhere. HOME/GIT_CONFIG_GLOBAL are redirected
        so the developer's own global identity cannot leak into the assertion.
        """
        empty = tmp_path / "git-home"
        empty.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(empty))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty / "gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty / "gitconfig-system"))
        monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
        monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
        monkeypatch.delenv("GIT_COMMITTER_NAME", raising=False)
        monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)
        _git(repo, "config", "--unset", "user.name")
        _git(repo, "config", "--unset", "user.email")

    def test_missing_identity_skips_capture_and_flags_the_response(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        ledger_before = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", LEDGER],
            cwd=repo,
            capture_output=True,
        )
        self._strip_identity(repo, tmp_path, monkeypatch)

        result = self._service_save(repo)

        assert result.status == "saved"
        assert result.identity_required is True
        assert result.git_sha is None
        assert any("needs a git identity" in w for w in result.warnings)
        assert (repo / "demo.py").exists()
        ledger_after = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", LEDGER],
            cwd=repo,
            capture_output=True,
        )
        assert ledger_after.stdout == ledger_before.stdout, "no ledger commit may be created"

    def test_identity_present_captures_and_leaves_flag_false(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        result = self._service_save(repo)
        assert result.identity_required is False
        assert result.git_sha is not None

    def test_setting_identity_lets_the_next_save_capture(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        self._strip_identity(repo, tmp_path, monkeypatch)
        first = self._service_save(repo)
        assert first.identity_required is True

        set_identity("Restored User", "restored@example.com", cwd=repo)
        second = self._service_save(repo)
        assert second.identity_required is False
        assert second.git_sha is not None
        assert _git(repo, "rev-parse", LEDGER) == second.git_sha


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
    def test_git_binary_missing_reports_git_unavailable(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Hosted containers may lack git entirely; that must surface as its
        # own state (the UI says "git unavailable"), not "no-repository"
        # (which offers init) and not a 500 from FileNotFoundError.
        monkeypatch.setattr(git_core.shutil, "which", lambda _name: None)
        st = working_branch_status(repo, cwd=repo)
        assert st.state == "git-unavailable"
        assert st.identity_set is False

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


@pytest.fixture
def unborn_repo(tmp_path: Path) -> Path:
    """A fresh git repo with no commits (unborn HEAD on main) but identity configured."""
    root = tmp_path / "unborn_repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    return root


class TestSetWorkingBranchUnborn:
    """set_working_branch(create=True) on a commitless (unborn-HEAD) repo.

    A fresh ``haute init`` scaffolds files but never commits, leaving HEAD on
    an unborn branch.  The startup WorkingBranchModal calls
    ``set_working_branch(..., create=True)`` which must seed a root commit and
    then fork the working branch off it — all atomically.
    """

    def test_core_repro_succeeds_on_unborn_repo(self, unborn_repo: Path) -> None:
        """Repro: set_working_branch creates initial-model on a fresh repo.

        main gets a single root commit; initial-model forks off it; ledger exists;
        association written; HEAD lands on the ledger.
        """
        from haute._git_state import read_working_branch

        (unborn_repo / "main.py").write_text("x = 1\n")

        result = set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        assert result.state == "ready"
        assert result.working_branch == "initial-model"
        # main now has exactly one root commit (no parents).
        assert _parents(unborn_repo, "main") == []
        # initial-model resolves to a commit (forked off main's root).
        im_sha = _git(unborn_repo, "rev-parse", "--verify", "initial-model")
        main_sha = _git(unborn_repo, "rev-parse", "main")
        assert im_sha == main_sha  # branch points at the root commit
        # Ledger for initial-model exists.
        assert _git(unborn_repo, "rev-parse", "--verify", "initial-model-save")
        # Working-branch association recorded.
        assert read_working_branch(unborn_repo) == "initial-model"
        # HEAD is on the ledger (normal operating posture S10).
        head = _git(unborn_repo, "symbolic-ref", "--short", "HEAD")
        assert head == "initial-model-save"

    def test_scaffold_captured_gitignore_honored(self, unborn_repo: Path) -> None:
        """Tracked scaffold files enter the root commit; .gitignore-matched files do not."""
        (unborn_repo / "main.py").write_text("x = 1\n")
        (unborn_repo / ".gitignore").write_text("*.pyc\ncache/\n")
        cache_dir = unborn_repo / "cache"
        cache_dir.mkdir()
        (cache_dir / "data.bin").write_text("ignored")
        (unborn_repo / "model.pyc").write_text("bytecode")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert ".gitignore" in committed
        assert not any("cache" in f for f in committed), f"cache/ leaked into commit: {committed}"
        assert not any(f.endswith(".pyc") for f in committed), (
            f".pyc leaked into commit: {committed}"
        )

    def test_empty_tree_still_plants_root_commit(self, unborn_repo: Path) -> None:
        """An unborn repo with no files succeeds via --allow-empty root commit."""
        result = set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        assert result.state == "ready"
        root_sha = _git(unborn_repo, "rev-parse", "main")
        assert root_sha
        assert _parents(unborn_repo, "main") == []

    def test_atomicity_rollback_on_post_create_failure(
        self, unborn_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If resolve_ledger raises after checkout -b, HEAD is restored and branch deleted."""
        from haute._git_state import read_working_branch

        (unborn_repo / "main.py").write_text("x = 1\n")

        def _failing_resolve(working: str, cwd: Path | None = None) -> str:
            raise GitDomainError("injected resolve failure")

        monkeypatch.setattr(git_setup, "resolve_ledger", _failing_resolve)

        with pytest.raises(GitDomainError, match="injected resolve failure"):
            set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        # HEAD must not be left on the broken/unborn branch.
        head = _git(unborn_repo, "symbolic-ref", "--short", "HEAD")
        assert head != "initial-model", f"HEAD left on broken branch: {head}"
        # No association written.
        assert read_working_branch(unborn_repo) is None
        # The half-created branch was deleted.
        assert _git(unborn_repo, "branch", "--list", "initial-model") == ""

    def test_atomicity_rollback_also_deletes_orphan_ledger(
        self, unborn_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If write_working_branch fails after resolve_ledger spawned the ledger,
        the rollback deletes BOTH the working branch and its orphan ledger so a
        later fork off an advanced default cannot read as invalid."""
        import haute._git_state as git_state
        from haute._git import ledger_name
        from haute._git_state import read_working_branch

        (unborn_repo / "main.py").write_text("x = 1\n")

        def _failing_write(project_root: Path, branch: str) -> None:
            raise OSError("injected association-write failure")

        monkeypatch.setattr(git_state, "write_working_branch", _failing_write)

        with pytest.raises(OSError, match="injected association-write failure"):
            set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        # HEAD restored, no association, and NEITHER the working branch nor its
        # ledger is left behind — the ledger was spawned by resolve_ledger before
        # the failure, so it must be cleaned up too.
        assert _git(unborn_repo, "symbolic-ref", "--short", "HEAD") != "initial-model"
        assert read_working_branch(unborn_repo) is None
        assert _git(unborn_repo, "branch", "--list", "initial-model") == ""
        assert _git(unborn_repo, "branch", "--list", ledger_name("initial-model")) == ""

    def test_identity_unset_raises_clear_domain_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When git identity is not configured, a user-friendly GitDomainError is raised."""
        root = tmp_path / "no_identity"
        root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
        (root / "main.py").write_text("x = 1\n")

        # Force the seed commit to fail deterministically for missing identity.
        # An *empty* global config is not enough: with GIT_CONFIG_NOSYSTEM set and
        # the author/committer env vars cleared, git falls back to auto-detecting an
        # identity from username+hostname. That succeeds on dev machines (e.g.
        # "user@host.local") and fails only on CI runners whose hostname yields a
        # bogus ".(none)" domain — so an empty config makes this test pass in CI but
        # spuriously fail locally. user.useConfigOnly=true refuses any auto-detected
        # identity, exercising the raise-path on every machine.
        blank_cfg = tmp_path / "blank.gitconfig"
        blank_cfg.write_text("[user]\n\tuseConfigOnly = true\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(blank_cfg))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for var in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(GitDomainError, match="git name and email"):
            set_working_branch("initial-model", root, create=True, cwd=root)

    # --- Regression tests: born-repo behaviour must be unchanged -----------

    def test_regression_born_repo_create_no_extra_root_commit(self, repo: Path) -> None:
        """Repo WITH commits: create=True forks off HEAD; main gets no extra commit."""
        main_count_before = int(_git(repo, "rev-list", "--count", "main"))

        set_working_branch("fresh-line", repo, create=True, cwd=repo)

        main_count_after = int(_git(repo, "rev-list", "--count", "main"))
        assert main_count_after == main_count_before

    def test_regression_create_branch_already_exists_raises(self, repo: Path) -> None:
        """create=True when branch already exists still raises 'already exists'."""
        with pytest.raises(GitDomainError, match="already exists"):
            set_working_branch(WORKING, repo, create=True, cwd=repo)

    def test_regression_adopt_existing_branch_works(self, repo: Path) -> None:
        """create=False (adopt) of an existing eligible branch still works."""
        result = set_working_branch(WORKING, repo, cwd=repo)
        assert result.state == "ready"

    def test_regression_adopt_missing_branch_raises(self, repo: Path) -> None:
        """create=False of a non-existent branch still raises 'does not exist'."""
        with pytest.raises(GitDomainError, match="does not exist"):
            set_working_branch("ghost", repo, create=False, cwd=repo)


class TestSeedGitignoreGuards:
    """The unborn-repo seed must not trust an ambient .gitignore.

    ``haute init`` writes the guard entries (``.env``, ``.haute/``, ``data/``,
    …), so the haute-scaffolded flow is protected.  But a FOREIGN unborn repo —
    the user ran bare ``git init`` themselves in a directory holding a ``.env``
    or datasets — has no such guarantee, and the seed's ``git add -A`` would
    publish credentials into git history and commit the per-clone ``.haute/``
    state (the clone-lockout class).  The seed therefore asserts the shared
    guard entries into ``.gitignore`` before staging anything, and stages from
    an empty index so pre-staged secrets cannot ride through either.
    """

    def test_foreign_unborn_repo_secrets_not_committed(self, unborn_repo: Path) -> None:
        """PRIMARY repro: planted .env / .haute/ / data/ and NO .gitignore —
        none of them may enter the root commit."""
        (unborn_repo / "main.py").write_text("x = 1\n")
        (unborn_repo / ".env").write_text("DATABRICKS_TOKEN=hunter2\n")
        (unborn_repo / "sub").mkdir()
        (unborn_repo / "sub" / ".env").write_text("NESTED_SECRET=1\n")
        (unborn_repo / ".haute").mkdir()
        (unborn_repo / ".haute" / "state.json").write_text("{}")
        (unborn_repo / "data").mkdir()
        (unborn_repo / "data" / "quotes.csv").write_text("a,b\n1,2\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert not any(".env" in f for f in committed), f".env leaked into history: {committed}"
        assert not any(f.startswith(".haute/") for f in committed), (
            f"per-clone .haute/ state committed: {committed}"
        )
        assert not any(f.startswith("data/") for f in committed), (
            f"data/ leaked into history: {committed}"
        )
        # The asserted guards are themselves captured, so clones inherit them.
        assert ".gitignore" in committed

    def test_seed_appends_guards_to_partial_gitignore(self, unborn_repo: Path) -> None:
        """An existing .gitignore missing the guard entries gets them appended;
        the user's own entries keep working."""
        (unborn_repo / ".gitignore").write_text("*.pyc\n")
        (unborn_repo / "model.pyc").write_text("bytecode")
        (unborn_repo / ".env").write_text("SECRET=1\n")
        (unborn_repo / "main.py").write_text("x = 1\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert ".env" not in committed, f".env leaked into history: {committed}"
        assert not any(f.endswith(".pyc") for f in committed), f".pyc leaked: {committed}"
        gitignore = (unborn_repo / ".gitignore").read_text()
        assert "*.pyc" in gitignore
        assert ".env" in gitignore.splitlines()

    def test_prestaged_secret_not_committed(self, unborn_repo: Path) -> None:
        """Staged index content ignores .gitignore entirely — a .env the user
        pre-staged must still be kept out of the root commit (and left on disk)."""
        (unborn_repo / "main.py").write_text("x = 1\n")
        (unborn_repo / ".env").write_text("SECRET=1\n")
        _git(unborn_repo, "add", "main.py", ".env")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert ".env" not in committed, f"pre-staged .env leaked into history: {committed}"
        assert (unborn_repo / ".env").exists()  # working tree untouched

    def test_seed_leaves_complete_gitignore_untouched(self, unborn_repo: Path) -> None:
        """A .gitignore already carrying the full guard set is not rewritten."""
        from haute._gitignore_guard import GITIGNORE_GUARD_ENTRIES

        content = "\n".join(GITIGNORE_GUARD_ENTRIES) + "\n"
        (unborn_repo / ".gitignore").write_text(content)
        (unborn_repo / "main.py").write_text("x = 1\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        assert (unborn_repo / ".gitignore").read_text() == content

    # --- Allowlist gate (defence-in-depth): the seed stages only haute-owned
    # pathspecs, so an unintended file must fail BOTH the allowlist and the
    # .gitignore guards to reach history. -------------------------------------

    def test_unallowed_file_not_committed_even_when_not_ignored(self, unborn_repo: Path) -> None:
        """The allowlist gate alone: files matching no haute-owned pathspec
        stay out of the root commit even though nothing gitignores them."""
        (unborn_repo / "main.py").write_text("x = 1\n")
        (unborn_repo / "notes.txt").write_text("meeting notes\n")
        (unborn_repo / "backup.tar").write_text("tarball bytes")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert "notes.txt" not in committed, f"unallowed file leaked: {committed}"
        assert "backup.tar" not in committed, f"unallowed file leaked: {committed}"
        # Not committed but also untouched: still on disk for the user.
        assert (unborn_repo / "notes.txt").exists()

    def test_venv_fails_both_gates(self, unborn_repo: Path) -> None:
        """.venv/ contents match the *.py allowlist pathspec, so only the
        gitignore guard (which must include .venv/) keeps them out."""
        venv_pkg = unborn_repo / ".venv" / "lib" / "site-packages"
        venv_pkg.mkdir(parents=True)
        (venv_pkg / "dep.py").write_text("VERSION = '1.0'\n")
        (unborn_repo / "main.py").write_text("x = 1\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert not any(f.startswith(".venv/") for f in committed), (
            f".venv/ leaked into history: {committed}"
        )
        assert ".venv/" in (unborn_repo / ".gitignore").read_text().splitlines()

    # --- Over-exclusion guard: unintended EXCLUSION is a loud failure mode —
    # pin that legitimate project files ARE still captured by the seed. -------

    def test_haute_scaffold_shape_is_fully_committed(self, unborn_repo: Path) -> None:
        """Every file shape `haute init` scaffolds (plus the nested-pipeline
        layout) enters the root commit — the allowlist must not over-exclude."""
        legitimate = [
            "haute.toml",
            "pyproject.toml",
            "uv.lock",
            ".env.example",
            "rating/main.py",
            "rating/main.haute.json",
            "rating/utility/helpers.py",
            "rating/config/data_input/quotes.json",
            "prompts/starter.md",
            "tests/test_pipeline.py",
            ".githooks/pre-commit",
            ".github/workflows/ci.yml",
        ]
        for rel in legitimate:
            target = unborn_repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {rel}\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        missing = [rel for rel in legitimate if rel not in committed]
        assert not missing, f"legitimate scaffold files over-excluded: {missing}"
        assert ".gitignore" in committed


class TestSetWorkingBranchUnbornNonDefault:
    """set_working_branch(create=True) when HEAD is on an unborn NON-default branch.

    Reproduces the bug where a prior failed attempt left HEAD on an unborn
    ``initial-branch`` instead of ``main``.  The fix must:

    1. Rename the unborn branch to ``main`` before seeding so the root commit
       lands on the canonical default, not on the branch-to-be-created.
    2. Keep ``checkout -b <branch>`` inside the atomicity guard so a failure
       at any step after the rename+seed is fully rolled back.
    """

    @pytest.fixture
    def unborn_non_default_repo(self, tmp_path: Path) -> Path:
        """Unborn repo where HEAD is on ``initial-branch`` (not ``main``) —
        simulates state left by a prior failed haute-init attempt."""
        root = tmp_path / "unborn_nondefault"
        root.mkdir()
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "Test Actuary")
        _git(root, "config", "user.email", "test@example.com")
        # Simulate prior damage: HEAD on unborn non-default branch, main gone.
        _git(root, "checkout", "-b", "initial-branch")
        return root

    def test_primary_repro_seeds_on_main_not_initial_branch(
        self, unborn_non_default_repo: Path
    ) -> None:
        """PRIMARY repro: HEAD on unborn initial-branch → succeeds; root commit on main.

        Before the fix this raised ``GitError: branch 'initial-branch' already
        exists`` because the seed commit landed on ``initial-branch`` and then
        ``git checkout -b initial-branch`` clashed with the freshly-born branch,
        aborting outside the rollback block and leaving a malformed repo.
        """
        from haute._git_state import read_working_branch

        root = unborn_non_default_repo
        (root / "main.py").write_text("x = 1\n")

        result = set_working_branch("initial-branch", root, create=True, cwd=root)

        assert result.state == "ready"
        assert result.working_branch == "initial-branch"
        # main must exist with exactly one root commit (no parents).
        assert _parents(root, "main") == []
        # initial-branch resolves to a commit (forked off main's root).
        ib_sha = _git(root, "rev-parse", "--verify", "initial-branch")
        main_sha = _git(root, "rev-parse", "main")
        assert ib_sha == main_sha  # both point at the root commit
        # Ledger for initial-branch exists.
        assert _git(root, "rev-parse", "--verify", "initial-branch-save")
        # Working-branch association recorded.
        assert read_working_branch(root) == "initial-branch"
        # HEAD is on the ledger (normal operating posture S10).
        head = _git(root, "symbolic-ref", "--short", "HEAD")
        assert head == "initial-branch-save"
        # No orphaned/malformed state: main exists independently.
        all_branches_raw = _git(root, "branch", "--list").splitlines()
        branch_names = {b.strip().lstrip("* ") for b in all_branches_raw}
        assert "main" in branch_names
        assert "initial-branch" in branch_names

    def test_regression_clean_unborn_main_unchanged(self, unborn_repo: Path) -> None:
        """Regression: happy-path (HEAD on unborn main) is unchanged by the fix."""
        from haute._git_state import read_working_branch

        (unborn_repo / "main.py").write_text("x = 1\n")

        result = set_working_branch("wb", unborn_repo, create=True, cwd=unborn_repo)

        assert result.state == "ready"
        assert _parents(unborn_repo, "main") == []
        assert read_working_branch(unborn_repo) == "wb"
        head = _git(unborn_repo, "symbolic-ref", "--short", "HEAD")
        assert head == "wb-save"

    def test_unborn_master_default_not_renamed_to_main(self, tmp_path: Path) -> None:
        """HEAD on unborn master (protected): seed lands on master; no new main branch."""
        from haute._git_state import read_working_branch

        root = tmp_path / "master_repo"
        root.mkdir()
        _git(root, "init", "-b", "master")
        _git(root, "config", "user.name", "Test Actuary")
        _git(root, "config", "user.email", "test@example.com")
        (root / "main.py").write_text("x = 1\n")

        result = set_working_branch("feature-x", root, create=True, cwd=root)

        assert result.state == "ready"
        # Seed landed on master, not renamed to main.
        assert _parents(root, "master") == []
        assert _git(root, "branch", "--list", "main") == ""
        assert read_working_branch(root) == "feature-x"
        head = _git(root, "symbolic-ref", "--short", "HEAD")
        assert head == "feature-x-save"

    def test_atomicity_unborn_non_default_rollback_on_resolve_failure(
        self, unborn_non_default_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If resolve_ledger raises after checkout -b (non-default unborn repo),
        rollback leaves HEAD valid, no association, no working branch or ledger.
        main-with-seed-commit may remain — it is a legitimate permanent state."""
        from haute._git_state import read_working_branch

        root = unborn_non_default_repo
        (root / "main.py").write_text("x = 1\n")

        def _failing_resolve(working: str, cwd: Path | None = None) -> str:
            raise GitDomainError("injected resolve failure")

        monkeypatch.setattr(git_setup, "resolve_ledger", _failing_resolve)

        with pytest.raises(GitDomainError, match="injected resolve failure"):
            set_working_branch("initial-branch", root, create=True, cwd=root)

        # HEAD must be valid (on main), not on the broken working branch.
        head = _git(root, "symbolic-ref", "--short", "HEAD")
        assert head != "initial-branch", f"HEAD left on broken branch: {head}"
        # No association written.
        assert read_working_branch(root) is None
        # The half-created working branch was deleted.
        assert _git(root, "branch", "--list", "initial-branch") == ""
        # Its ledger was not created (resolve_ledger was the failure point).
        assert _git(root, "branch", "--list", "initial-branch-save") == ""

    def test_rename_then_commit_failure_leaves_unborn_main(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rename-to-main happens before the seed commit: if the commit fails
        (identity unset) on an unborn non-default branch, the repo is left on an
        unborn *main* (rename persisted, no commit) with a clear GitDomainError."""
        root = tmp_path / "rename_then_fail"
        root.mkdir()
        _git(root, "init", "-b", "main")
        _git(root, "checkout", "-b", "initial-branch")  # unborn non-default HEAD
        (root / "main.py").write_text("x = 1\n")

        # Force the commit to fail for missing identity on every machine. An empty
        # global config is not enough — with the author/committer env vars cleared
        # git auto-detects an identity from username+hostname (succeeds locally,
        # fails only on CI's ".(none)" hostname). user.useConfigOnly=true refuses
        # any auto-detected identity, so the raise-path is exercised everywhere.
        blank_cfg = tmp_path / "blank.gitconfig"
        blank_cfg.write_text("[user]\n\tuseConfigOnly = true\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(blank_cfg))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for var in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(GitDomainError, match="set your git name and email first"):
            set_working_branch("initial-branch", root, create=True, cwd=root)

        # The unborn branch was renamed to main before the failing commit, and no
        # commit exists — a coherent state a retry (with identity) resumes from.
        assert _git(root, "symbolic-ref", "--short", "HEAD") == "main"
        # Still unborn: no born branch refs exist, and the old name is gone.
        assert _git(root, "branch", "--format=%(refname:short)") == ""
        assert _git(root, "branch", "--list", "initial-branch") == ""

    def test_born_main_with_unborn_orphan_head_raises_clear_error(self, tmp_path: Path) -> None:
        """If a born 'main' coexists with an unborn non-protected HEAD (only
        reachable via `git checkout --orphan` outside haute), the rename would
        collide — surface a clear GitDomainError and mutate nothing."""
        root = tmp_path / "orphan_head"
        root.mkdir()
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "Test Actuary")
        _git(root, "config", "user.email", "test@example.com")
        (root / "main.py").write_text("x = 1\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", "root")  # main is now born
        main_sha = _git(root, "rev-parse", "main")
        _git(root, "checkout", "--orphan", "feature")  # unborn HEAD, main born

        with pytest.raises(GitDomainError, match="'main' already exists"):
            set_working_branch("feature-wb", root, create=True, cwd=root)

        # Nothing mutated: main untouched, no working branch created, HEAD unmoved.
        assert _git(root, "rev-parse", "main") == main_sha
        assert _git(root, "branch", "--list", "feature-wb") == ""
        assert _git(root, "symbolic-ref", "--short", "HEAD") == "feature"


class TestMoveToCommit:
    """P6 move-through-history: a detached checkout that materialises a
    historical commit's tree and clears the working branch (§3.4 / §3.9)."""

    def _two_saves(self, repo: Path) -> tuple[str, str]:
        """Adopt WORKING, land two ledger saves; return (first_sha, second_sha)."""
        set_working_branch(WORKING, repo, cwd=repo)
        sha1 = _write_and_save(repo, WORKING, {"rating.py": "# v1\n"})
        sha2 = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert sha1 and sha2
        return sha1, sha2

    def test_detaches_head_and_materialises_tree(self, repo: Path) -> None:
        sha1, sha2 = self._two_saves(repo)

        resp = move_to_commit(sha1, repo, cwd=repo)

        assert resp.sha == sha1
        assert resp.short_sha == sha1[:8]
        assert resp.is_detached is True
        # HEAD really is detached (not a symbolic ref to a branch)...
        assert not (repo / ".git" / "HEAD").read_text().startswith("ref:")
        assert _git(repo, "rev-parse", "HEAD") == sha1
        # ...and the working tree is the old version's content.
        assert (repo / "rating.py").read_text() == "# v1\n"
        assert _tree(repo, "HEAD") == _tree(repo, sha1)

    def test_clears_working_branch(self, repo: Path) -> None:
        from haute._git_state import read_working_branch

        sha1, _ = self._two_saves(repo)
        assert read_working_branch(repo) == WORKING

        move_to_commit(sha1, repo, cwd=repo)

        assert read_working_branch(repo) is None

    def test_prior_branch_stays_reachable(self, repo: Path) -> None:
        """The move detaches HEAD; it must not move or orphan any ref."""
        _, sha2 = self._two_saves(repo)
        ledger_tip_before = _git(repo, "rev-parse", LEDGER)
        working_tip_before = _git(repo, "rev-parse", WORKING)

        resp = move_to_commit(sha2, repo, cwd=repo)

        assert resp.prior_branch == LEDGER  # HEAD was on the ledger before the move
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip_before
        assert _git(repo, "rev-parse", WORKING) == working_tip_before

    def test_refuses_dirty_tree(self, repo: Path) -> None:
        """Row A / S21: uncommitted tracked edits block the move."""
        sha1, _ = self._two_saves(repo)
        (repo / "rating.py").write_text("# uncommitted external edit\n")

        with pytest.raises(GitDomainError, match="unsaved changes"):
            move_to_commit(sha1, repo, cwd=repo)

    def test_refuses_when_git_op_in_progress(self, repo: Path) -> None:
        """Row H: a half-finished merge/rebase/cherry-pick blocks the move."""
        sha1, _ = self._two_saves(repo)
        (repo / ".git" / "MERGE_HEAD").write_text(sha1 + "\n")

        with pytest.raises(GitDomainError, match="in progress"):
            move_to_commit(sha1, repo, cwd=repo)

    def test_refuses_unknown_sha(self, repo: Path) -> None:
        self._two_saves(repo)
        with pytest.raises(GitDomainError, match="No commit found"):
            move_to_commit("0" * 40, repo, cwd=repo)

    def test_rejects_flag_like_sha(self, repo: Path) -> None:
        self._two_saves(repo)
        with pytest.raises(GitDomainError):
            move_to_commit("--hard", repo, cwd=repo)

    def test_wipes_volatile_artefacts(self, repo: Path) -> None:
        sha1, _ = self._two_saves(repo)
        cache = repo / ".haute_cache"
        cache.mkdir()
        (cache / "stale.parquet").write_text("junk")
        out = repo / "output"
        out.mkdir()
        (out / "result.parquet").write_text("junk")

        move_to_commit(sha1, repo, cwd=repo)

        assert not cache.exists()
        assert not out.exists()

    def test_first_save_after_move_spawns_fresh_pair(self, repo: Path) -> None:
        """S13: after a move, set_working_branch(create=True) spawns a new
        working+ledger pair off the detached commit and a save lands on it."""
        from haute._git_state import read_working_branch

        sha1, _ = self._two_saves(repo)
        move_to_commit(sha1, repo, cwd=repo)

        result = set_working_branch("fresh-line", repo, create=True, cwd=repo)

        assert result.working_branch == "fresh-line"
        # The new branch is rooted at the moved-to commit, not the old tip.
        assert _git(repo, "rev-parse", "fresh-line") == sha1
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "fresh-line-save"
        assert read_working_branch(repo) == "fresh-line"

        new_save = _write_and_save(repo, "fresh-line", {"rating.py": "# branched\n"})
        assert new_save is not None
        # The save sits atop the moved-to commit on the fresh ledger.
        assert sha1 in _parents(repo, "fresh-line-save")


class TestCommitMilestone:
    def _setup(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)

    def test_commit_merges_ledger_to_working(self, repo: Path) -> None:
        self._setup(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        result = commit_milestone("First milestone", repo, cwd=repo)
        assert result.working_branch == WORKING
        assert result.version_label is None
        # working tip is the milestone merge (two parents)
        assert _git(repo, "rev-parse", WORKING) == result.sha
        assert len(_parents(repo, result.sha)) == 2
        assert _git(repo, "log", "-1", "--format=%s", WORKING) == "First milestone"

    def test_commit_with_version_label_tags(self, repo: Path) -> None:
        self._setup(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        result = commit_milestone("Tagged", repo, version_label="1.0", cwd=repo)
        assert result.version_label == "1.0"
        assert _git(repo, "rev-parse", "version/1.0^{commit}") == result.sha

    def test_commit_without_working_branch_refused(self, repo: Path) -> None:
        # No set_working_branch call → no state recorded.
        with pytest.raises(GitDomainError, match="No working branch"):
            commit_milestone("nope", repo, cwd=repo)

    def test_commit_with_no_new_saves_refused(self, repo: Path) -> None:
        self._setup(repo)
        with pytest.raises(GitDomainError, match="No new saves"):
            commit_milestone("nothing to commit", repo, cwd=repo)

    def test_blank_message_refused(self, repo: Path) -> None:
        self._setup(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        with pytest.raises(GitDomainError, match="message is required"):
            commit_milestone("   ", repo, cwd=repo)

    def test_control_chars_in_message_refused(self, repo: Path) -> None:
        # A record-separator (or other C0) in the subject would corrupt the
        # ledger-history parser; reject it at the boundary. Tab/newline stay legal.
        self._setup(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        with pytest.raises(GitDomainError, match="control characters"):
            commit_milestone("bad\x1emessage", repo, cwd=repo)


class TestWorkingMilestones:
    def test_empty_when_no_working_branch(self, repo: Path) -> None:
        ms = working_milestones(repo, cwd=repo)
        assert ms.working_branch is None
        assert ms.entries == []

    def test_lists_milestones_newest_first_with_labels(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("M1", repo, version_label="1.0", cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        commit_milestone("M2", repo, cwd=repo)

        ms = working_milestones(repo, cwd=repo)
        assert ms.working_branch == WORKING
        messages = [e.message for e in ms.entries]
        # newest first; the working branch's pre-spawn root commit is also on
        # the first-parent chain, so assert ordering of our milestones.
        assert messages.index("M2") < messages.index("M1")
        m1 = next(e for e in ms.entries if e.message == "M1")
        assert m1.version_label == "1.0"
        m2 = next(e for e in ms.entries if e.message == "M2")
        assert m2.version_label is None

    def test_peek_another_branch_without_switching(self, repo: Path) -> None:
        # branch= peeks at a non-current branch's history; the recorded working
        # branch is unaffected.
        from haute._git_state import read_working_branch

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("on dev", repo, cwd=repo)
        # a second working branch with its own milestone
        set_working_branch("other-line", repo, create=True, cwd=repo)
        _write_and_save(repo, "other-line", {"rating.py": "# other\n"})
        commit_milestone("on other", repo, cwd=repo)

        peek = working_milestones(repo, cwd=repo, branch=WORKING)
        assert peek.working_branch == WORKING
        assert any(e.message == "on dev" for e in peek.entries)
        assert not any(e.message == "on other" for e in peek.entries)
        # the recorded working branch is still 'other-line' — peeking didn't switch
        assert read_working_branch(repo) == "other-line"

    def test_per_save_ledger_commits_excluded_from_milestones(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="ledger save A")
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="ledger save B")
        commit_milestone("the milestone", repo, cwd=repo)
        ms = working_milestones(repo, cwd=repo)
        messages = [e.message for e in ms.entries]
        # The two per-save ledger commits hang off the merge's second parent —
        # the first-parent milestone spine must not include them.
        assert "ledger save A" not in messages
        assert "ledger save B" not in messages
        assert "the milestone" in messages


class TestCommitContext:
    def test_milestone_is_its_own_nearest(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        m1 = merge_to_working(WORKING, "M1", cwd=repo)

        ctx = commit_context(repo, m1, cwd=repo)
        assert ctx.is_milestone is True
        assert ctx.distance == 0
        assert ctx.nearest_milestone.sha == m1
        assert ctx.sha == m1
        # No base requested → no historic↔current delta.
        assert ctx.delta_from_base is None

    def test_save_after_a_milestone_anchors_on_the_latest_milestone(self, repo: Path) -> None:
        # A pending save committed after a real milestone anchors on THAT
        # milestone (the latest), not the repo root, with the distance counted
        # from the milestone's ledger fold-point (its second parent).
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        m1 = merge_to_working(WORKING, "M1", cwd=repo)
        save = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        assert save is not None

        ctx = commit_context(repo, save, cwd=repo)
        assert ctx.is_milestone is False
        assert ctx.nearest_milestone.sha == m1
        assert ctx.nearest_milestone.is_root is False
        # Distance counts from the milestone's fold-point, not the merge commit.
        assert ctx.distance == int(_git(repo, "rev-list", "--count", f"{m1}^2..{save}"))
        assert ctx.distance >= 1

    def test_save_older_than_default_window_keeps_its_milestone_anchor(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# milestone 1\n"}, message="save 1")
        oldest = merge_to_working(WORKING, "M1", cwd=repo)
        old_save = _write_and_save(
            repo,
            WORKING,
            {"rating.py": "# save after milestone 1\n"},
            message="old save",
        )
        assert old_save is not None
        for index in range(2, 22):
            _write_and_save(
                repo,
                WORKING,
                {"rating.py": f"# milestone {index}\n"},
                message=f"save {index}",
            )
            merge_to_working(WORKING, f"M{index}", cwd=repo)
        # The public history page stays limited to 20, but commit-context
        # classification must inspect the complete milestone spine. The old
        # save was folded by M2, so its nearest prior milestone remains M1.
        assert oldest not in {entry.sha for entry in working_milestones(repo, cwd=repo).entries}

        ctx = commit_context(repo, old_save, cwd=repo)

        assert ctx.is_milestone is False
        assert ctx.distance >= 1
        assert ctx.nearest_milestone.sha == oldest

    def test_delta_from_base_counts_commits_between_two_versions(self, repo: Path) -> None:
        # The historic↔current span for the compare UI: rev-list --count base..sha,
        # robust across milestone merges (base..head counts only what head reaches
        # that base does not). Reported only when ``base`` is supplied.
        set_working_branch(WORKING, repo, cwd=repo)
        a = _write_and_save(repo, WORKING, {"rating.py": "# a\n"})
        m1 = merge_to_working(WORKING, "M1", cwd=repo)
        c = _write_and_save(repo, WORKING, {"rating.py": "# c\n"})
        assert a is not None and c is not None

        # Save-to-save span (same ledger chain).
        ctx = commit_context(repo, c, cwd=repo, base=a)
        assert ctx.delta_from_base == int(_git(repo, "rev-list", "--count", f"{a}..{c}"))
        assert ctx.delta_from_base is not None and ctx.delta_from_base >= 1
        # Milestone-merge base: the merge is not an ancestor of the ledger save, yet
        # base..head still yields the saves made since that milestone (not all of
        # history) — the count matches git's own and is bounded by the chain length.
        ctx_m = commit_context(repo, c, cwd=repo, base=m1)
        assert ctx_m.delta_from_base == int(_git(repo, "rev-list", "--count", f"{m1}..{c}"))
        assert ctx_m.delta_from_base is not None and ctx_m.delta_from_base >= 1
        # No base → None.
        assert commit_context(repo, c, cwd=repo).delta_from_base is None

    def test_root_commit_is_its_own_root_anchor(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        root = _git(repo, "rev-list", "--max-parents=0", WORKING)

        ctx = commit_context(repo, root, cwd=repo)
        assert ctx.is_root is True
        assert ctx.distance == 0
        assert ctx.nearest_milestone.is_root is True
        assert ctx.nearest_milestone.sha == root

    def test_distance_to_nearest_milestone_with_real_count(self, repo: Path) -> None:
        # Two saves before ANY real milestone: the latest milestone is the root
        # itself (the only first-parent-chain commit), so the save anchors on the
        # root with the exact commit count from it, N(>=2).
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        save = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        assert save is not None
        root = _git(repo, "rev-list", "--max-parents=0", WORKING)

        n = int(_git(repo, "rev-list", "--count", f"{root}..{save}"))
        assert n >= 2
        ctx = commit_context(repo, save, cwd=repo)
        assert ctx.is_milestone is False
        assert ctx.nearest_milestone.sha == root
        assert ctx.nearest_milestone.is_root is True
        assert ctx.distance == n

    def test_non_milestone_commit_anchors_on_root_when_no_ancestor_milestone(
        self, repo: Path
    ) -> None:
        # A plain ledger save before any milestone exists has no ancestor
        # milestone on the working spine — it anchors on the repo root.
        set_working_branch(WORKING, repo, cwd=repo)
        save = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert save is not None
        root = _git(repo, "rev-list", "--max-parents=0", WORKING)

        ctx = commit_context(repo, save, cwd=repo)
        assert ctx.is_milestone is False
        assert ctx.is_root is False
        assert ctx.nearest_milestone.is_root is True
        assert ctx.nearest_milestone.sha == root
        assert ctx.distance == int(_git(repo, "rev-list", "--count", f"{root}..{save}"))

    def test_unknown_sha_raises_domain_error(self, repo: Path) -> None:
        with pytest.raises(GitDomainError, match="Unknown commit"):
            commit_context(repo, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", cwd=repo)

    def test_working_milestones_tags_the_root_entry(self, repo: Path) -> None:
        # The oldest first-parent entry is the repo root → is_root=True so the UI
        # can show an "init" version tag; newer milestones are not flagged.
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        merge_to_working(WORKING, "M1", cwd=repo)

        entries = working_milestones(repo, cwd=repo).entries
        assert entries[-1].is_root is True
        assert all(not e.is_root for e in entries[:-1])


class TestRenamePreservingStaging:
    """S8 / §3.5 — a canvas rename stages the old path's removal and the new
    path's addition in the SAME ledger commit, so git's rename heuristics
    (`git log --follow`, `-M`) trace a node's history across the rename.

    These engine-level tests drive ``commit_save`` directly with the path set
    a rename produces (old + new together); the service-level counterpart is
    in :class:`TestRenamePreservingSaveIntegration`.
    """

    OLD = "config/data_input/alpha.json"
    NEW = "config/data_input/beta.json"
    BODY = '{\n  "path": "data.parquet"\n}\n'

    def _commit_creating_old(self, repo: Path) -> str:
        sha = _write_and_save(repo, WORKING, {self.OLD: self.BODY})
        assert sha is not None
        return sha

    def _commit_renaming(self, repo: Path) -> str:
        # The rename as the save flow stages it: old path gone, new path
        # present with identical content, BOTH passed to one commit_save call
        # (the service supplies touched-new + removed-old, §3.5).
        (repo / self.OLD).unlink()
        new_path = repo / self.NEW
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(self.BODY)
        sha = commit_save([self.OLD, self.NEW], WORKING, cwd=repo)
        assert sha is not None
        return sha

    def test_rename_rides_a_single_commit(self, repo: Path) -> None:
        self._commit_creating_old(repo)
        rename_sha = self._commit_renaming(repo)
        # git pairs the removal and addition into one rename record, not a
        # separate delete + add.
        out = _git(repo, "show", "--name-status", "--format=", "-M", rename_sha)
        rename_lines = [ln for ln in out.splitlines() if ln.startswith("R")]
        assert len(rename_lines) == 1, out
        assert self.OLD in rename_lines[0] and self.NEW in rename_lines[0]

    def test_follow_traces_history_across_rename(self, repo: Path) -> None:
        create_sha = self._commit_creating_old(repo)
        rename_sha = self._commit_renaming(repo)
        # `--follow` on the NEW path reaches back to the commit that created
        # the OLD path — the node's pre-rename history is not severed. Note the
        # ORDER-INDEPENDENT guarantee against a broken split-into-two-commits
        # implementation comes from the single-rename-record assertions
        # (test_rename_rides_a_single_commit / *_is_a_pure_move), not from
        # --follow alone — git's copy detection can still trace some split
        # orderings, so --follow is necessary but not sufficient as a guard.
        history = _git(repo, "log", "--follow", "--format=%H", "--", self.NEW).splitlines()
        assert rename_sha in history
        assert create_sha in history, "history severed at the rename"

    def test_identical_content_rename_is_a_pure_move(self, repo: Path) -> None:
        # Content-minimal (§3.5): when only the name changes the config bytes
        # are unchanged, so the move is a 100%-similarity rename — the most
        # robust case for the heuristics.
        self._commit_creating_old(repo)
        rename_sha = self._commit_renaming(repo)
        out = _git(repo, "show", "--name-status", "--format=", "--find-renames=100%", rename_sha)
        assert "R100" in out, out
        assert self.OLD in out and self.NEW in out

    def test_rename_with_minor_content_edit_still_follows(self, repo: Path) -> None:
        # A rename can carry a small content edit and still be followed — git's
        # similarity heuristic only needs the file to stay mostly the same. (The
        # converse, a rename bundled with a *large* rewrite of a tiny config, can
        # fall below the threshold and sever history; that is git's limit, not a
        # staging bug, and is the "content-minimal where possible" caveat in §3.5.)
        old = "config/data_input/gamma.json"
        new = "config/data_input/delta.json"
        body = (
            '{\n  "path": "data.parquet",\n  "format": "parquet",\n'
            '  "limit": 1000,\n  "cache": true\n}\n'
        )
        edited = body.replace('"limit": 1000', '"limit": 2000')

        (repo / old).parent.mkdir(parents=True, exist_ok=True)
        (repo / old).write_text(body)
        create_sha = commit_save([old], WORKING, cwd=repo)
        assert create_sha is not None

        (repo / old).unlink()
        (repo / new).write_text(edited)
        rename_sha = commit_save([old, new], WORKING, cwd=repo)
        assert rename_sha is not None

        history = _git(repo, "log", "--follow", "--format=%H", "--", new).splitlines()
        assert create_sha in history, "minor edit dropped below the rename threshold"


class TestRenamePreservingSaveIntegration:
    """End-to-end: renaming a node between two real pipeline saves produces a
    rename-preserving ledger commit, so `git log --follow` on the node's new
    config file reaches its pre-rename history (S8 / §3.5)."""

    @staticmethod
    def _save_graph(root: Path, label: str) -> SavePipelineResponse:
        from unittest.mock import patch

        from haute.routes._save_pipeline import SavePipelineService

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label=label,
                        nodeType="dataInput",
                        config={
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "path": "data.parquet",
                            "arguments": {},
                        },
                    ),
                )
            ],
            edges=[],
        )
        body = SavePipelineRequest(name="demo", source_file="demo.py", graph=graph)
        svc = SavePipelineService(root)
        with patch.object(svc, "_validate_api_inputs_have_schemas"):
            return svc.save(body)

    def test_node_rename_is_rename_preserving_in_ledger(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        old_rel = _data_input_config("Alpha")
        new_rel = _data_input_config("Beta")

        write_working_branch(repo, WORKING)
        first = self._save_graph(repo, "Alpha")  # → old_rel
        assert first.git_sha is not None
        second = self._save_graph(repo, "Beta")  # → new_rel, old_rel removed
        assert second.git_sha is not None
        assert first.git_sha != second.git_sha

        # The second ledger commit carries the config rename as a single
        # rename record — old config removed + new config added together.
        out = _git(repo, "show", "--name-status", "--format=", "-M", second.git_sha)
        rename_lines = [ln for ln in out.splitlines() if ln.startswith("R")]
        assert len(rename_lines) == 1, out
        assert old_rel in rename_lines[0]
        assert new_rel in rename_lines[0]

        # Content-minimality on REAL save output (not a hand-fed fixture): a
        # name-only node rename leaves the config body byte-identical (the label
        # drives only the filename), so the production commit is a 100% move.
        pure = _git(
            repo, "show", "--name-status", "--format=", "--find-renames=100%", second.git_sha
        )
        assert "R100" in pure, pure

        # `--follow` on the renamed config reaches the commit that first
        # created it under the old name.
        history = _git(repo, "log", "--follow", "--format=%H", "--", new_rel).splitlines()
        assert first.git_sha in history, "node history severed at the rename"

    def test_config_is_the_only_rename_in_the_commit(self, repo: Path) -> None:
        # Content-minimality lives in WHAT changes: only the config file moves;
        # the .py and sidecar keep their paths (ordinary modifications). Exactly
        # one rename pair means nothing was spuriously re-paired.
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        self._save_graph(repo, "Alpha")
        second = self._save_graph(repo, "Beta")
        assert second.git_sha is not None
        out = _git(repo, "show", "--name-status", "--format=", "-M", second.git_sha)
        rename_lines = [ln for ln in out.splitlines() if ln.startswith("R")]
        assert len(rename_lines) == 1, out
        # Exact endpoints (not just the folder): a regression in the sanitized
        # filename would still satisfy a folder-prefix check but is caught here.
        assert _data_input_config("Alpha") in rename_lines[0]
        assert _data_input_config("Beta") in rename_lines[0]

    def test_rename_preserving_under_divergent_pipeline_root(self, repo: Path) -> None:
        # Production runs pipeline_root *nested under* project_root (haute.toml
        # pipeline = "rating/main.py" → pipeline_root = <cwd>/rating), unlike the
        # single-arg service the other tests use. Configs are written/removed
        # under pipeline_root while the ledger captures paths relative to
        # project_root — assert rename-preservation still holds for the shipped
        # layout, not only when the two roots coincide.
        from unittest.mock import patch

        from haute._git_state import write_working_branch
        from haute.routes._save_pipeline import SavePipelineService

        write_working_branch(repo, WORKING)
        pipeline_root = repo / "rating"
        pipeline_root.mkdir()

        def save(label: str) -> SavePipelineResponse:
            graph = PipelineGraph(
                nodes=[
                    GraphNode(
                        id="src",
                        data=NodeData(
                            label=label,
                            nodeType="dataInput",
                            config={
                                "inputType": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "data.parquet",
                                "arguments": {},
                            },
                        ),
                    )
                ],
                edges=[],
            )
            body = SavePipelineRequest(name="main", source_file="rating/main.py", graph=graph)
            svc = SavePipelineService(project_root=repo, pipeline_root=pipeline_root)
            with patch.object(svc, "_validate_api_inputs_have_schemas"):
                return svc.save(body)

        first = save("Alpha")
        second = save("Beta")
        assert first.git_sha is not None and second.git_sha is not None

        old_rel = f"rating/{_data_input_config('Alpha')}"
        new_rel = f"rating/{_data_input_config('Beta')}"
        out = _git(repo, "show", "--name-status", "--format=", "-M", second.git_sha)
        rename_lines = [ln for ln in out.splitlines() if ln.startswith("R")]
        assert len(rename_lines) == 1, out
        assert old_rel in rename_lines[0] and new_rel in rename_lines[0]
        history = _git(repo, "log", "--follow", "--format=%H", "--", new_rel).splitlines()
        assert first.git_sha in history, "history severed under divergent roots"

    def test_old_config_and_new_config_reach_one_commit_save_call(self, repo: Path) -> None:
        # Pins the load-bearing service-side assembly directly (not only via the
        # end-to-end git assertions): the orphaned old config (from `removed`)
        # and the new config (from `touched`) are handed to a SINGLE commit_save
        # call — the property the single-commit rename rests on.
        from unittest.mock import patch

        from haute import _git as _git_mod
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        self._save_graph(repo, "Alpha")  # real save creates the old config

        real = _git_mod.commit_save
        captured: dict[str, list[str]] = {}

        def spy(paths: list[str], working: str, **kw: object) -> str | None:
            captured["paths"] = list(paths)
            return real(paths, working, **kw)  # type: ignore[arg-type]

        with patch.object(_git_mod, "commit_save", side_effect=spy):
            self._save_graph(repo, "Beta")

        assert _data_input_config("Alpha") in captured["paths"], captured
        assert _data_input_config("Beta") in captured["paths"], captured


class TestLedgerExpansion:
    """P5 ledger-expansion read paths: the saves a milestone folded in
    (its second-parent run), and the pending saves on the ledger ahead of the
    working tip. Rename-aware (`-M`), closing the P4 read-path deferral."""

    def test_pending_lists_unmilestoned_saves_newest_first(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save one")
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save two")
        pending = pending_ledger_saves(repo, cwd=repo)
        assert [s.message for s in pending.saves] == ["save two", "save one"]
        assert all(s.files for s in pending.saves)  # each carries its file changes

    def test_pending_empty_without_working_branch(self, repo: Path) -> None:
        assert pending_ledger_saves(repo, cwd=repo).saves == []

    def test_milestone_saves_returns_folded_saves_and_clears_pending(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save A")
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save B")
        result = commit_milestone("the milestone", repo, cwd=repo)
        folded = milestone_saves(result.sha, cwd=repo)
        assert [s.message for s in folded.saves] == ["save B", "save A"]
        # everything is folded in now — nothing pending
        assert pending_ledger_saves(repo, cwd=repo).saves == []

    def test_milestone_saves_is_rename_aware(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        old = "config/data_input/alpha.json"
        new = "config/data_input/beta.json"
        body = '{\n  "path": "data.parquet"\n}\n'
        _write_and_save(repo, WORKING, {old: body}, message="add config")
        (repo / old).unlink()
        (repo / new).write_text(body)
        commit_save([old, new], WORKING, cwd=repo, message="rename config")
        result = commit_milestone("ms", repo, cwd=repo)

        folded = milestone_saves(result.sha, cwd=repo)
        rename_save = next(s for s in folded.saves if s.message == "rename config")
        renames = [f for f in rename_save.files if f.status == "R"]
        assert len(renames) == 1, rename_save.files
        assert renames[0].old_path == old
        assert renames[0].path == new

    def test_second_milestone_saves_exclude_the_first(self, repo: Path) -> None:
        # The load-bearing property: M^1..M^2 isolates only the NEW saves, never
        # re-counting an earlier milestone's — because M2's first parent (M1) is
        # a merge whose own second parent already reaches M1's folded saves.
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save A")
        m1 = commit_milestone("M1", repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save B")
        m2 = commit_milestone("M2", repo, cwd=repo)

        assert [s.message for s in milestone_saves(m1.sha, cwd=repo).saves] == ["save A"]
        assert [s.message for s in milestone_saves(m2.sha, cwd=repo).saves] == ["save B"]

    def test_milestone_saves_empty_for_non_merge_commit(self, repo: Path) -> None:
        # the pre-spawn root on the working branch has no second parent
        set_working_branch(WORKING, repo, cwd=repo)
        root = _git(repo, "rev-parse", WORKING)
        assert milestone_saves(root, cwd=repo).saves == []

    def test_milestone_saves_rejects_unknown_sha(self, repo: Path) -> None:
        with pytest.raises(GitDomainError):
            milestone_saves("0" * 40, cwd=repo)

    def test_milestone_saves_rejects_range_shaped_sha(self, repo: Path) -> None:
        # "a..b" passes _validate_ref_name (no forbidden chars) but must not reach
        # rev-list as a range — the single-commit resolve guard rejects it.
        set_working_branch(WORKING, repo, cwd=repo)
        with pytest.raises(GitDomainError):
            milestone_saves(f"main..{WORKING}", cwd=repo)

    def test_unicode_path_is_not_quote_escaped(self, repo: Path) -> None:
        # core.quotepath=false: a non-ASCII config filename must render as itself,
        # not git's octal-escaped, double-quoted form.
        set_working_branch(WORKING, repo, cwd=repo)
        unicode_path = "config/data_input/café.json"
        _write_and_save(repo, WORKING, {unicode_path: '{"x": 1}\n'}, message="add café")
        paths = [f.path for s in pending_ledger_saves(repo, cwd=repo).saves for f in s.files]
        assert unicode_path in paths, paths


class TestBranchManager:
    """P5b branch manager + §8 guards: working branches as version lines,
    archive-the-pair (S32), delete-the-pair refusing on unmerged saves."""

    def _current_head(self, repo: Path) -> str:
        return _git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    def _branch_exists(self, repo: Path, name: str) -> bool:
        return (
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", name],
                cwd=repo,
                capture_output=True,
            ).returncode
            == 0
        )

    # -- listing -----------------------------------------------------------

    def test_working_branches_lists_with_flags(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        wb = working_branches(repo, cwd=repo)
        assert wb.current == WORKING
        names = {b.name: b for b in wb.branches}
        assert WORKING in names
        assert names[WORKING].is_current and not names[WORKING].is_archived
        assert not names[WORKING].has_unmerged_saves
        # the default branch and the ledger are not version lines
        assert "main" not in names
        assert LEDGER not in names

    def test_has_unmerged_saves_tracks_ledger(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        entry = next(b for b in working_branches(repo, cwd=repo).branches if b.name == WORKING)
        assert entry.has_unmerged_saves
        commit_milestone("m", repo, cwd=repo)
        entry = next(b for b in working_branches(repo, cwd=repo).branches if b.name == WORKING)
        assert not entry.has_unmerged_saves

    # -- archive (S32) -----------------------------------------------------

    def test_archive_pair_renames_both_and_switches_away(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)  # HEAD now on the ledger (active)
        result = archive_working_pair(WORKING, repo, cwd=repo)
        assert result.archived_as == f"archive/{WORKING}"
        # both refs moved under archive/, originals gone
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)
        assert self._branch_exists(repo, f"archive/{WORKING}")
        assert self._branch_exists(repo, f"archive/{LEDGER}")  # ledger_name(archive/W)
        # active pair → switched away to default + working branch forgotten
        assert self._current_head(repo) == "main"
        from haute._git_state import read_working_branch

        assert read_working_branch(repo) is None

    def test_archive_via_ledger_name_archives_the_pair(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        archive_working_pair(LEDGER, repo, cwd=repo)  # passing the ledger name
        assert not self._branch_exists(repo, WORKING)
        assert self._branch_exists(repo, f"archive/{WORKING}")

    def test_archive_does_not_refuse_unmerged_saves(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})  # unmerged
        result = archive_working_pair(WORKING, repo, cwd=repo)  # no refusal (S32)
        assert result.archived_as == f"archive/{WORKING}"

    def test_archive_refuses_protected(self, repo: Path) -> None:
        with pytest.raises(GitGuardrailError):
            archive_working_pair("main", repo, cwd=repo)

    # -- delete (§8) -------------------------------------------------------

    def test_delete_refuses_unmerged_then_confirms(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})  # unmerged save
        with pytest.raises(GitGuardrailError, match="loses them"):
            delete_working_pair(WORKING, repo, confirm=False, cwd=repo)
        # branch still there after the refusal
        assert self._branch_exists(repo, WORKING)
        delete_working_pair(WORKING, repo, confirm=True, cwd=repo)
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)
        assert self._current_head(repo) == "main"

    def test_delete_clean_pair_without_confirm(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("m", repo, cwd=repo)  # nothing unmerged now
        delete_working_pair(WORKING, repo, confirm=False, cwd=repo)
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)

    def test_delete_refuses_protected(self, repo: Path) -> None:
        with pytest.raises(GitGuardrailError):
            delete_working_pair("main", repo, cwd=repo)

    def test_delete_current_force_discards_dirty_tree(self, repo: Path) -> None:
        # A confirmed delete of the current branch is destructive by intent — a
        # dirty working tree is discarded with it (S38), not a refusal.
        from haute._git_state import read_working_branch

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("m", repo, cwd=repo)
        (repo / "rating.py").write_text("# uncommitted edit\n")  # dirty tree
        delete_working_pair(WORKING, repo, confirm=True, cwd=repo)
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)
        # HEAD moved to the default branch; state cleared → startup chooser
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
        assert read_working_branch(repo) is None

    # -- review fixes ------------------------------------------------------

    def test_archive_refuses_dirty_working_tree(self, repo: Path) -> None:
        # Switching away can't checkout over uncommitted edits — refuse with an
        # actionable message rather than a sanitized git error.
        set_working_branch(WORKING, repo, cwd=repo)
        (repo / "rating.py").write_text("# uncommitted edit\n")
        with pytest.raises(GitDomainError, match="unsaved changes"):
            archive_working_pair(WORKING, repo, cwd=repo)

    def test_archive_avoids_ledger_name_collision(self, repo: Path) -> None:
        # An unrelated branch occupying the would-be archived LEDGER name must
        # not corrupt the pair — the archive name disambiguates on both refs.
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        _git(repo, "branch", f"archive/{LEDGER}")  # squat the archive ledger name
        result = archive_working_pair(WORKING, repo, cwd=repo)
        assert result.archived_as != f"archive/{WORKING}"
        assert self._branch_exists(repo, result.archived_as)
        assert self._branch_exists(repo, ledger_name(result.archived_as))
        # originals gone, no orphaned ledger
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)

    def test_has_uncommitted_changes_flags_dirty_current(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        (repo / "rating.py").write_text("# uncommitted\n")  # tracked dirty
        entry = next(b for b in working_branches(repo, cwd=repo).branches if b.name == WORKING)
        assert entry.has_uncommitted_changes

    def test_restore_unarchives_the_pair(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        archived = archive_working_pair(WORKING, repo, cwd=repo).archived_as
        result = restore_working_pair(archived, repo, cwd=repo)
        assert result.restored_as == WORKING
        assert self._branch_exists(repo, WORKING)
        assert self._branch_exists(repo, LEDGER)
        assert not self._branch_exists(repo, archived)
        assert not self._branch_exists(repo, ledger_name(archived))

    def test_restore_refuses_when_live_name_taken(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        archived = archive_working_pair(WORKING, repo, cwd=repo).archived_as
        # recreate a live branch with the would-be restored name
        _git(repo, "branch", WORKING, "main")
        with pytest.raises(GitDomainError, match="already exists"):
            restore_working_pair(archived, repo, cwd=repo)

    def test_restore_refuses_non_archived(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        with pytest.raises(GitDomainError, match="not an archived branch"):
            restore_working_pair(WORKING, repo, cwd=repo)

    def test_default_branch_falls_back_to_current_not_main(self, tmp_path: Path) -> None:
        # With a custom default and no remote/main/master, the fallback must be a
        # branch that EXISTS (the current one), not an invented "main".
        from haute._git import _get_default_branch

        repo = tmp_path / "custom"
        repo.mkdir()
        _git(repo, "init", "-b", "trunk")
        _git(repo, "config", "user.name", "T")
        _git(repo, "config", "user.email", "t@t")
        (repo / "f.txt").write_text("x\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "init")
        assert _get_default_branch(cwd=repo) == "trunk"


def _fork_setup(repo: Path) -> dict[str, str]:
    """pricing-dev with one milestone M1 then two pending saves; HEAD on ledger.

    Returns the milestone and pending-save shas so fork tests can target them.
    """
    set_working_branch(WORKING, repo, cwd=repo)
    _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="save 1")
    m1 = commit_milestone("M1", repo, cwd=repo).sha
    s2 = _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="save 2")
    s3 = _write_and_save(repo, WORKING, {"rating.py": "# v4\n"}, message="save 3")
    assert s2 is not None and s3 is not None
    return {"m1": m1, "s2": s2, "s3": s3}


class TestFetchThrottleAndHardening:
    """F7 (per-(cwd, remote, kind) fetch throttle) and F1 (prompt-proof,
    time-bounded fetch that degrades to local refs)."""

    def test_should_fetch_is_keyed_per_cwd(self) -> None:

        git_core._fetch_cooldowns.clear()
        a, b = Path("/tmp/haute-wt-a"), Path("/tmp/haute-wt-b")
        assert git_core._should_fetch("origin", cwd=a) is True
        # Second call for the same key is throttled within the window…
        assert git_core._should_fetch("origin", cwd=a) is False
        # …but a different worktree is NOT starved by the first (the F7 fix)…
        assert git_core._should_fetch("origin", cwd=b) is True
        # …nor is a different fetch family for the same worktree.
        assert git_core._should_fetch("origin", cwd=a, kind="pair") is True

    def test_fetch_refs_degrades_on_bad_remote(self, repo: Path) -> None:

        # A remote pointing nowhere must fail fast and return False — never raise
        # or prompt (F1: a background fetch must not hang the UI).
        _git(repo, "remote", "add", "origin", str(repo / "nonexistent.git"))
        assert git_core._fetch_refs("origin", "main", cwd=repo) is False

    def test_fetch_refs_times_out_to_false_with_prompt_proof_env(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F1: a hung fetch is killed by the timeout and degrades to False — never
        # raises, never blocks the UI — and the invocation is prompt-proof:
        # GIT_TERMINAL_PROMPT=0 + SSH BatchMode, so it can't sit on a credential or
        # host-key prompt to begin with.

        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))  # type: ignore[arg-type]

        monkeypatch.setattr(git_core.subprocess, "run", fake_run)
        assert git_core._fetch_refs("origin", "main", cwd=repo) is False
        env = captured["env"]
        assert isinstance(env, dict)
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
        cmd = captured["cmd"]
        assert isinstance(cmd, list) and cmd[:2] == ["git", "fetch"]

    def test_best_effort_remote_readers_degrade_on_unicode_output(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(UnicodeError("bad output")),
        )

        assert git_core._fetch_refs("origin", "main", cwd=repo) is False
        assert git_mod._ls_remote_version_tags("origin", cwd=repo) == {}


class TestCanonicalRemote:
    """X5: the read-side divergence baseline resolves a single canonical remote
    (origin → sole remote → none) so a non-origin clone isn't silently reported
    'in sync' against a non-existent ``origin/<default>``."""

    def test_none_when_no_remote(self, repo: Path) -> None:
        from haute._git import _canonical_remote

        assert _canonical_remote(cwd=repo) is None

    def test_prefers_origin_over_others(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _canonical_remote

        _git(repo, "remote", "add", "upstream", str(tmp_path / "u.git"))
        _git(repo, "remote", "add", "origin", str(tmp_path / "o.git"))
        assert _canonical_remote(cwd=repo) == "origin"

    def test_sole_non_origin_remote_is_canonical(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _canonical_remote

        _git(repo, "remote", "add", "upstream", str(tmp_path / "u.git"))
        assert _canonical_remote(cwd=repo) == "upstream"

    def test_multiple_non_origin_remotes_are_ambiguous(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _canonical_remote

        _git(repo, "remote", "add", "upstream", str(tmp_path / "u.git"))
        _git(repo, "remote", "add", "fork", str(tmp_path / "f.git"))
        assert _canonical_remote(cwd=repo) is None

    def test_default_branch_resolved_via_non_origin_remote(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git import _get_default_branch

        bare = tmp_path / "upstream.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "upstream", str(bare))
        _git(repo, "push", "upstream", "main")
        _git(repo, "remote", "set-head", "upstream", "main")
        assert _get_default_branch(cwd=repo) == "main"

    def test_default_branch_ref_is_read_live(self, repo: Path, tmp_path: Path) -> None:
        from haute._git import _get_default_branch

        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "branch", "trunk", "main")
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "push", "origin", "main", "trunk")
        _git(repo, "remote", "set-head", "origin", "main")
        assert _get_default_branch(cwd=repo) == "main"

        _git(repo, "remote", "set-head", "origin", "trunk")

        assert _get_default_branch(cwd=repo) == "trunk"


class TestCreateWorkingBranch:
    """The P5d fork model (S38): create-at-milestone (default), crystallize at a
    pending save, and Create & Move's work relocation + spawning-branch rewind.
    """

    def test_default_forks_at_latest_milestone_not_ledger(self, repo: Path) -> None:
        # The bug: forking used to branch off HEAD (the ledger), so the raw saves
        # rendered as milestones. The default fork must land at the latest
        # milestone, never on the ledger's per-save chain.
        ids = _fork_setup(repo)
        res = create_working_branch("feature", repo, cwd=repo)
        assert res.moved is False and res.switched is False
        assert _git(repo, "rev-parse", "feature") == ids["m1"]
        assert _git(repo, "rev-parse", "feature-save") == ids["m1"]
        ms = working_milestones(repo, cwd=repo, branch="feature")
        assert [e.message for e in ms.entries] == ["M1", "initial pipeline"]
        # current branch + HEAD + pending work all untouched
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert _git(repo, "rev-parse", WORKING) == ids["m1"]
        assert [s.message for s in pending_ledger_saves(repo, cwd=repo).saves] == [
            "save 3",
            "save 2",
        ]

    def test_fork_at_pending_save_crystallizes_milestone(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        res = create_working_branch("exp", repo, at=ids["s2"], cwd=repo)
        assert res.switched is False
        # New tip carries the save's tree, shaped as a merge of (M1, save).
        assert _tree(repo, "exp") == _tree(repo, ids["s2"])
        assert _parents(repo, "exp") == [ids["m1"], ids["s2"]]
        ms = working_milestones(repo, cwd=repo, branch="exp")
        assert ms.entries[0].message.startswith("Start exp from save")
        assert [e.message for e in ms.entries[1:]] == ["M1", "initial pipeline"]
        # current untouched
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    def test_move_at_latest_milestone_relocates_work(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        (repo / "rating.py").write_text("# dirty\n")  # uncommitted edit
        res = create_working_branch("moved", repo, move=True, cwd=repo)
        assert res.moved is True and res.switched is True
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "moved-save"
        assert (repo / "rating.py").read_text() == "# dirty\n"  # carried across
        assert _git(repo, "rev-parse", "moved") == ids["m1"]
        assert [s.message for s in pending_ledger_saves(repo, cwd=repo).saves] == [
            "save 3",
            "save 2",
        ]
        # spawning branch rewound to the fork point — nothing pending left
        assert _git(repo, "rev-parse", LEDGER) == ids["m1"]
        assert _git(repo, "rev-parse", WORKING) == ids["m1"]

    def test_move_at_pending_save_splits_work(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        (repo / "rating.py").write_text("# dirty\n")
        res = create_working_branch("split", repo, at=ids["s2"], move=True, cwd=repo)
        assert res.moved is True and res.switched is True
        assert _tree(repo, "split") == _tree(repo, ids["s2"])
        assert (repo / "rating.py").read_text() == "# dirty\n"
        # save 3 (after the fork point) moved over; save 2 stayed behind
        assert [s.message for s in pending_ledger_saves(repo, cwd=repo).saves] == ["save 3"]
        assert _git(repo, "rev-parse", LEDGER) == ids["s2"]
        assert [s.message for s in pending_ledger_saves(repo, cwd=repo, branch=WORKING).saves] == [
            "save 2"
        ]

    def test_move_at_older_milestone_refused(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        commit_milestone("M2", repo, cwd=repo)  # M1 is now an older milestone
        with pytest.raises(GitDomainError, match="latest milestone or a pending save"):
            create_working_branch("nope", repo, at=ids["m1"], move=True, cwd=repo)

    def test_move_refused_when_published_ledger_would_rewind(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # M5: move-mode rewinds the spawning ledger to the fork point. If that
        # ledger is published and the rewind would orphan commits the remote
        # still has, the source pair becomes un-pushable (and S33 forbids the
        # force-push that would fix it). Refuse and steer to a parallel fork.
        ids = _fork_setup(repo)  # working=M1, ledger tip = pending save 3
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        push_working_pair("origin", repo, cwd=repo)  # publish the ledger tip
        _git(repo, "fetch", "origin")  # establish refs/remotes/origin/<ledger>
        with pytest.raises(GitDomainError, match="published"):
            create_working_branch("moved", repo, move=True, cwd=repo)
        # Refusal is clean — nothing created, the ledger was not rewound.
        assert _git(repo, "branch", "--list", "moved") == ""
        assert _git(repo, "rev-parse", LEDGER) != ids["m1"]

    def test_move_allowed_when_ledger_unpublished(self, repo: Path, tmp_path: Path) -> None:
        # A configured remote that was never pushed to has no remote-tracking
        # ledger ref, so the rewind can orphan nothing — move stays frictionless.
        _fork_setup(repo)
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        res = create_working_branch("moved", repo, move=True, cwd=repo)
        assert res.moved is True

    def test_move_allowed_when_published_ledger_is_ancestor_of_fork_point(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # M5 allow-path (published but SAFE): the spawning ledger IS published, but
        # the published tip is an ANCESTOR of the fork point, so rewinding the
        # ledger to that point orphans nothing on the remote → the move is allowed.
        # Complements the refuse-path (published + would-rewind) and the
        # never-pushed allow-path, which are the only M5 cases otherwise covered.
        _fork_setup(repo)  # working=M1; pending save 2, save 3 on the ledger
        commit_milestone("M2", repo, cwd=repo)  # fold the pending saves; working=M2
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        push_working_pair("origin", repo, cwd=repo)  # publish the ledger at the M2 line
        _git(repo, "fetch", "origin")  # establish refs/remotes/origin/<ledger>
        # Default fork point is the latest milestone M2; the published ledger tip is
        # an ancestor of it, so the rewind is safe and the move proceeds.
        res = create_working_branch("moved", repo, move=True, cwd=repo)
        assert res.moved is True
        assert _git(repo, "branch", "--list", "moved") != ""

    def test_fork_from_foreign_commit_refused(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        folded = milestone_saves(ids["m1"], cwd=repo).saves[0].sha  # a save inside M1
        with pytest.raises(GitDomainError, match="milestone or a pending save"):
            create_working_branch("nope", repo, at=folded, cwd=repo)

    def test_name_collision_refused(self, repo: Path) -> None:
        _fork_setup(repo)
        create_working_branch("dup", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="already exists"):
            create_working_branch("dup", repo, cwd=repo)

    def test_rejects_flag_shaped_at(self, repo: Path) -> None:
        # `at` is user input — it must go through the same ref-name guard as
        # every other ref (a leading '-' would otherwise be an option token).
        _fork_setup(repo)
        with pytest.raises(GitDomainError, match="must not start with"):
            create_working_branch("x", repo, at="-x", cwd=repo)

    def test_move_at_pending_save_preserves_timestamps(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        orig_ts = _git(repo, "show", "-s", "--format=%aI", ids["s3"])
        create_working_branch("split", repo, at=ids["s2"], move=True, cwd=repo)
        pend = pending_ledger_saves(repo, cwd=repo).saves
        assert pend[0].message == "save 3"
        assert pend[0].timestamp == orig_ts  # replay preserved the author date

    def test_adopt_create_when_unset_switches(self, tmp_path: Path) -> None:
        repo = tmp_path / "fresh"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "T")
        _git(repo, "config", "user.email", "t@t")
        (repo / "rating.py").write_text("# p\n")
        _git(repo, "add", "rating.py")
        _git(repo, "commit", "-m", "init")
        res = create_working_branch("first-line", repo, cwd=repo)
        assert res.switched is True
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "first-line-save"


class TestArchiveCommit:
    """archive_commit materialises a commit's tree read-only (S11) — view ≠ move:
    no checkout, no HEAD change, no working-tree mutation."""

    def test_materialises_file_at_a_past_commit(self, repo: Path, tmp_path: Path) -> None:
        (repo / "rating.py").write_text("# v1\n")
        _git(repo, "add", "rating.py")
        _git(repo, "commit", "-m", "v1")
        sha1 = _git(repo, "rev-parse", "HEAD")
        (repo / "rating.py").write_text("# v2\n")
        _git(repo, "add", "rating.py")
        _git(repo, "commit", "-m", "v2")

        dest = tmp_path / "out"
        dest.mkdir()
        archive_commit(sha1, dest, cwd=repo)
        assert (dest / "rating.py").read_text() == "# v1\n"  # the PAST content

    def test_does_not_touch_head_or_working_tree(self, repo: Path, tmp_path: Path) -> None:
        (repo / "rating.py").write_text("# current\n")
        _git(repo, "add", "rating.py")
        _git(repo, "commit", "-m", "current")
        head_before = _git(repo, "rev-parse", "HEAD")
        branch_before = _git(repo, "symbolic-ref", "--short", "HEAD")

        dest = tmp_path / "out"
        dest.mkdir()
        archive_commit("HEAD", dest, cwd=repo)

        assert _git(repo, "rev-parse", "HEAD") == head_before
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == branch_before
        assert (repo / "rating.py").read_text() == "# current\n"

    def test_refuses_unknown_commit(self, repo: Path, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(GitDomainError):
            archive_commit("0" * 40, dest, cwd=repo)


class TestRemotesAndPush:
    """Deliberate push of the working/ledger pair to an existing remote (S16),
    never force (S33); ahead/behind read from local remote refs only (no fetch)."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)  # spawn the ledger; HEAD → ledger
        write_working_branch(repo, WORKING)

    def _add_bare_remote(self, repo: Path, tmp_path: Path, name: str = "origin") -> Path:
        bare = tmp_path / f"{name}.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", name, str(bare))
        return bare

    def test_no_remotes_returns_empty(self, repo: Path) -> None:
        self._setup_pair(repo)
        res = list_remotes(repo, cwd=repo)
        assert res.remotes == []
        assert res.working_branch == WORKING

    def test_lists_remote_url_and_null_ahead_before_any_push(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        res = list_remotes(repo, cwd=repo)
        assert [r.name for r in res.remotes] == ["origin"]
        r = res.remotes[0]
        assert r.url == str(bare)
        # No remote-tracking ref exists yet, so divergence is unknown (not 0).
        assert r.working is not None and r.working.status == "untracked"

    def test_push_sends_both_working_and_ledger(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})  # ledger advances
        self._add_bare_remote(repo, tmp_path)
        res = push_working_pair("origin", repo, cwd=repo)
        assert set(res.pushed_refs) == {"main", WORKING, LEDGER}
        remote_refs = _git(repo, "ls-remote", "origin")
        assert f"refs/heads/{WORKING}" in remote_refs
        assert f"refs/heads/{LEDGER}" in remote_refs

    @pytest.mark.parametrize(
        ("failure", "message"),
        [
            (subprocess.TimeoutExpired(["git", "push"], 1), "git push timed out"),
            (OSError("injected launch failure"), "git push failed"),
        ],
    )
    def test_push_transport_is_bounded_and_errors_release_the_repository(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
        message: str,
    ) -> None:
        from haute._git_lock import repository_mutation

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        real_run = git_core.subprocess.run
        captured_timeout: list[object] = []

        def fail_push(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[:2] == ["git", "push"]:
                captured_timeout.append(kwargs.get("timeout"))
                raise failure
            return real_run(cmd, **kwargs)  # type: ignore[return-value]

        monkeypatch.setattr(git_core.subprocess, "run", fail_push)

        with pytest.raises(GitError, match=message):
            push_working_pair("origin", repo, cwd=repo)

        assert captured_timeout == [git_core._PUSH_TIMEOUT_SECONDS]
        # The decorator's finally path must release the repository lock for
        # another request thread, not merely permit a same-thread RLock re-entry.
        reacquired = threading.Event()

        def acquire_after_failure() -> None:
            with repository_mutation(repo):
                reacquired.set()

        thread = threading.Thread(target=acquire_after_failure, daemon=True)
        thread.start()
        assert reacquired.wait(timeout=2)
        thread.join(timeout=2)

    def test_first_use_unborn_repo_publishes_default_working_and_ledger(
        self, unborn_repo: Path, tmp_path: Path
    ) -> None:
        """The first branch chosen in a fresh project has a publishable shared ancestry."""
        set_working_branch(WORKING, unborn_repo, create=True, cwd=unborn_repo)
        self._add_bare_remote(unborn_repo, tmp_path)

        result = push_working_pair("origin", unborn_repo, cwd=unborn_repo)

        assert result.pushed_refs == ["main", WORKING, LEDGER]
        assert _git(unborn_repo, "merge-base", "--is-ancestor", "main", WORKING) == ""
        assert _git(unborn_repo, "merge-base", "--is-ancestor", "main", LEDGER) == ""
        remote_refs = _git(unborn_repo, "ls-remote", "origin")
        assert {f"refs/heads/{name}" for name in ("main", WORKING, LEDGER)} <= set(
            line.split()[1] for line in remote_refs.splitlines()
        )

    def test_empty_bootstrap_without_ledger_publishes_only_default_and_working(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Pre-save branches retain their ledger name without inventing its ref."""
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.ledger_branch == LEDGER
        assert result.pushed_refs == ["main", WORKING]
        assert f"refs/heads/{LEDGER}" not in _git(repo, "ls-remote", "origin")

    def test_empty_bootstrap_resolves_main_when_a_tag_has_the_same_name(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Branch enumeration stays unambiguous when a tag is also named ``main``."""
        self._setup_pair(repo)
        _git(repo, "tag", "main", "refs/heads/main")
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]
        assert "refs/heads/main" in _git(repo, "ls-remote", "origin")

    def test_ahead_after_a_local_milestone_following_a_push(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")  # establish remote-tracking refs locally
        # Just pushed: the working branch is level with the remote.
        synced = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert synced.working is not None
        assert synced.working.ahead == 0 and synced.working.behind == 0
        # A local milestone advances the working branch beyond the remote. It is a
        # merge commit, so raw commit-count ahead is 2 (the folded ledger save +
        # the merge commit itself) — ahead/behind are honest git commit counts.
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("local milestone", repo, cwd=repo)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None
        assert r.working.ahead == 2 and r.working.behind == 0

    def test_legs_untracked_before_any_push(self, repo: Path, tmp_path: Path) -> None:
        # F2 honesty: never pushed ⇒ both legs "untracked", NOT "synced" — the
        # user must not read "can't tell" as "in sync".
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None and r.working.status == "untracked"
        assert r.ledger is not None and r.ledger.status == "untracked"

    def test_legs_synced_after_push(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None and r.working.status == "synced"
        assert r.ledger is not None and r.ledger.status == "synced"

    def test_ledger_behind_is_visible_independently_of_working(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # F6: the two-machine save accident — the remote ledger advances while the
        # working leg stays level. It must surface on the LEDGER leg rather than be
        # structurally invisible (the pre-P7 engine only computed the working leg).

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Another clone adds a save (a ledger commit) and pushes only the ledger.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", LEDGER)
        (other / "x.txt").write_text("remote save\n")
        _git(other, "add", "x.txt")
        _git(other, "commit", "-m", "remote save")
        _git(other, "push", "origin", LEDGER)
        # Refresh is an explicit action; listing remotes reads local refs only.
        fetch_pair("origin", WORKING, cwd=repo)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.working is not None and r.working.status == "synced"  # working level
        assert r.ledger is not None and r.ledger.status == "behind"  # ledger VISIBLE
        assert r.ledger.behind == 1

    def test_remote_url_credentials_are_redacted(self, repo: Path, tmp_path: Path) -> None:
        # A token embedded in an https remote URL must never cross the API
        # boundary (threat model: remote URLs/credentials stay server-side).
        self._setup_pair(repo)
        _git(
            repo,
            "remote",
            "add",
            "origin",
            "https://x-access-token:ghp_SECRET123@github.com/org/repo.git",
        )
        res = list_remotes(repo, cwd=repo)
        url = res.remotes[0].url or ""
        assert "ghp_SECRET123" not in url
        assert "x-access-token" not in url
        assert "github.com/org/repo.git" in url  # the host/path survives

    def test_push_is_atomic_working_not_sent_when_ledger_rejected(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # --atomic: if either ref is rejected, NEITHER lands. Here the working
        # ref would fast-forward cleanly, but the ledger diverged on the remote —
        # so the whole push must fail and the working ref must stay put.
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # remote now has working + ledger
        before = _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}").split()[0]

        # A teammate advances ONLY the ledger on the remote → our ledger push is non-ff.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", LEDGER)
        (other / "x.txt").write_text("remote ledger edit\n")
        _git(other, "add", "x.txt")
        _git(other, "commit", "-m", "remote ledger change")
        _git(other, "push", "origin", LEDGER)

        # Locally make a milestone: the working branch advances (would fast-forward
        # the remote) while the ledger advances on a different line (non-ff).
        _write_and_save(repo, WORKING, {"rating.py": "# local\n"})
        commit_milestone("local milestone", repo, cwd=repo)
        assert _git(repo, "rev-parse", WORKING) != before  # local working moved on

        with pytest.raises(GitDomainError, match="never force-pushes"):
            push_working_pair("origin", repo, cwd=repo)
        # The fast-forwardable working ref was NOT pushed — the atomic push as a
        # whole was rejected because of the ledger.
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}").split()[0] == before

    def test_push_to_unknown_remote_is_refused(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        with pytest.raises(GitDomainError, match="No remote named"):
            push_working_pair("does-not-exist", repo, cwd=repo)

    def test_first_push_bootstraps_default_and_reports_it(self, repo: Path, tmp_path: Path) -> None:
        """An advertised-empty remote receives the local merge target atomically.

        This is the analyst's first-push journey: the working pair alone is not a
        useful shared history unless its local default branch is published too.
        """
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]
        advertised = _git(repo, "ls-remote", "origin")
        assert "refs/heads/main" in advertised
        assert "refs/heads/" + WORKING in advertised
        # The default is a one-time bootstrap ref, not rewrite-detection state.
        assert "origin/main" not in read_pushed_shas(repo)

    def test_second_push_does_not_resubmit_established_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is False
        assert result.pushed_refs == [WORKING, LEDGER]

    def test_selected_remote_does_not_use_another_remotes_tracking_evidence(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """No-HEAD default resolution is isolated to the selected remote."""
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path, "origin")
        self._add_bare_remote(repo, tmp_path, "upstream")
        base = _git(repo, "rev-parse", "main")
        _git(repo, "branch", "trunk", base)
        _git(repo, "push", "origin", "trunk")
        _git(repo, "branch", "-D", "trunk")
        _git(repo, "branch", "-D", "main")
        _git(repo, "update-ref", "-d", "refs/remotes/origin/trunk")
        _git(repo, "update-ref", "refs/remotes/upstream/trunk", base)

        with pytest.raises(GitDomainError, match="determine the remote default"):
            push_working_pair("origin", repo, cwd=repo)

    def test_related_remote_main_is_never_advanced_when_publishing_pair(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "push", "origin", "main")
        _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        main_before = _git(repo, "ls-remote", "origin", "refs/heads/main")

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.bootstrapped_default is False
        assert result.pushed_refs == [WORKING, LEDGER]
        assert _git(repo, "ls-remote", "origin", "refs/heads/main") == main_before

    def test_established_push_pins_validated_tips_and_records_that_snapshot(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ref move at the push boundary cannot change what gets published or recorded."""
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)

        base = _git(repo, "rev-parse", "main")
        tree = _git(repo, "rev-parse", f"{base}^{{tree}}")
        working_tip = _git(repo, "commit-tree", tree, "-p", base, "-m", "validated working")
        ledger_tip = _git(repo, "commit-tree", tree, "-p", base, "-m", "validated ledger")
        moved_working = _git(repo, "commit-tree", tree, "-p", working_tip, "-m", "raced working")
        moved_ledger = _git(repo, "commit-tree", tree, "-p", ledger_tip, "-m", "raced ledger")
        _git(repo, "update-ref", f"refs/heads/{WORKING}", working_tip)
        _git(repo, "update-ref", f"refs/heads/{LEDGER}", ledger_tip)

        real_run = subprocess.run
        push_command: list[str] = []

        def move_refs_at_push(
            args: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["git", "push"]:
                assert kwargs.get("errors") == "replace"
                push_command.extend(args)
                real_run(
                    ["git", "update-ref", f"refs/heads/{WORKING}", moved_working],
                    cwd=repo,
                    check=True,
                )
                real_run(
                    ["git", "update-ref", f"refs/heads/{LEDGER}", moved_ledger],
                    cwd=repo,
                    check=True,
                )
            return real_run(args, **kwargs)

        monkeypatch.setattr(git_core.subprocess, "run", move_refs_at_push)

        result = push_working_pair("origin", repo, cwd=repo)

        assert f"{working_tip}:refs/heads/{WORKING}" in push_command
        assert f"{ledger_tip}:refs/heads/{LEDGER}" in push_command
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}").split()[0] == working_tip
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{LEDGER}").split()[0] == ledger_tip
        assert _git(repo, "rev-parse", WORKING) == moved_working
        assert _git(repo, "rev-parse", LEDGER) == moved_ledger
        recorded = read_pushed_shas(repo)
        assert recorded[f"origin/{WORKING}"] == working_tip
        assert recorded[f"origin/{LEDGER}"] == ledger_tip
        assert result.pushed_refs == [WORKING, LEDGER]

    def test_empty_bootstrap_pins_default_tip_to_validated_snapshot(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bootstrap's create-only default push uses the preflight commit, not a live ref."""

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        default_tip = _git(repo, "rev-parse", "main")
        tree = _git(repo, "rev-parse", f"{default_tip}^{{tree}}")
        moved_default = _git(repo, "commit-tree", tree, "-p", default_tip, "-m", "raced default")

        real_run = subprocess.run
        push_command: list[str] = []

        def move_default_at_push(
            args: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["git", "push"]:
                push_command.extend(args)
                real_run(
                    ["git", "update-ref", "refs/heads/main", moved_default],
                    cwd=repo,
                    check=True,
                )
            return real_run(args, **kwargs)

        monkeypatch.setattr(git_core.subprocess, "run", move_default_at_push)

        result = push_working_pair("origin", repo, cwd=repo)

        assert f"{default_tip}:refs/heads/main" in push_command
        assert _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == default_tip
        assert _git(repo, "rev-parse", "main") == moved_default
        assert result.pushed_refs == ["main", WORKING, LEDGER]

    def test_default_validation_does_not_import_remote_tags(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """The authoritative default fetch cannot mutate local tag state."""
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        other = tmp_path / "tagged-unrelated"
        _git(tmp_path, "init", "-b", "main", str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        (other / "other.txt").write_text("unrelated\n")
        _git(other, "add", "other.txt")
        _git(other, "commit", "-m", "unrelated tagged default")
        _git(other, "tag", "remote-only-validation-tag")
        _git(other, "remote", "add", "origin", str(bare))
        _git(other, "push", "origin", "main", "refs/tags/remote-only-validation-tag")
        _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")

        with pytest.raises(GitDomainError, match="unrelated"):
            push_working_pair("origin", repo, cwd=repo)

        assert _git(repo, "tag", "--list", "remote-only-validation-tag") == ""

    def test_established_remote_missing_local_main_refuses_before_pair_publish(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "branch", "trunk", "main")
        _git(repo, "push", "origin", "trunk")

        with pytest.raises(GitDomainError, match="expected local default branch 'main' is missing"):
            push_working_pair("origin", repo, cwd=repo)

        advertised = _git(repo, "ls-remote", str(bare))
        assert f"refs/heads/{WORKING}" not in advertised
        assert f"refs/heads/{LEDGER}" not in advertised

    @pytest.mark.parametrize(
        ("returncode", "stdout", "stderr", "expected"),
        [
            (
                0,
                "ref: refs/heads/main\tHEAD\n"
                + "a" * 40
                + "\tHEAD\n"
                + "a" * 40
                + "\trefs/heads/main\n",
                "",
                None,
            ),
            (0, "ref: refs/heads/main\tHEAD\nnot-a-sha\trefs/heads/main\n", "", "malformed"),
            (2, "", "network unavailable", "network unavailable"),
        ],
    )
    def test_inspect_remote_strictly_parses_advertisement(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        returncode: int,
        stdout: str,
        stderr: str,
        expected: str | None,
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], returncode, stdout, stderr
            ),
        )

        if expected is None:
            assert git_mod._inspect_remote("origin", cwd=repo) == ({"main"}, "main", True)
        else:
            with pytest.raises(git_mod.GitError, match=expected):
                git_mod._inspect_remote("origin", cwd=repo)

    @pytest.mark.parametrize(
        "failure",
        [subprocess.TimeoutExpired(["git"], 1), OSError("no git"), UnicodeError("bad output")],
    )
    def test_inspect_remote_translates_process_failures(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
    ) -> None:
        import haute._git as git_mod

        def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise failure

        monkeypatch.setattr(git_core.subprocess, "run", failing_run)
        with pytest.raises(git_mod.GitError):
            git_mod._inspect_remote("origin", cwd=repo)

    @pytest.mark.parametrize(
        "failure",
        [subprocess.TimeoutExpired(["git"], 1), OSError("no git"), UnicodeError("bad output")],
    )
    def test_fetch_expected_default_translates_process_failures(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
    ) -> None:
        import haute._git as git_mod

        def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise failure

        monkeypatch.setattr(git_core.subprocess, "run", failing_run)
        with pytest.raises(git_mod.GitError):
            git_mod._fetch_expected_default("origin", "main", cwd=repo)

    @pytest.mark.parametrize(
        "advertised_ref",
        [
            "refs/heads/foo..bar",
            "refs/heads/x.lock",
            "refs/tags/foo..bar",
            "refs/",
            "refs/heads/main^{}",
        ],
    )
    def test_inspect_remote_rejects_git_invalid_object_refs(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        advertised_ref: str,
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, f"{'a' * 40}\t{advertised_ref}\n", ""
            ),
        )

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_accepts_valid_peeled_tag_advertisement(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        sha = "a" * 40
        peeled = "b" * 40
        stdout = (
            "ref: refs/heads/main\tHEAD\n"
            f"{sha}\tHEAD\n"
            f"{sha}\trefs/heads/main\n"
            f"{sha}\trefs/heads/feature]x\n"
            f"{sha}\trefs/tags/version/1.0\n"
            f"{peeled}\trefs/tags/version/1.0^{{}}\n"
        )
        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
        )

        assert git_mod._inspect_remote("origin", cwd=repo) == (
            {"main", "feature]x"},
            "main",
            True,
        )

    def test_inspect_remote_treats_unborn_symbolic_head_as_empty(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, "ref: refs/heads/main\tHEAD\n", ""
            ),
        )

        assert git_mod._inspect_remote("origin", cwd=repo) == (set(), "main", False)

    @pytest.mark.parametrize("zero_oid", ["0" * 40, "0" * 64])
    def test_inspect_remote_rejects_zero_object_ids(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, zero_oid: str
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, f"{zero_oid}\trefs/heads/main\n", ""
            ),
        )

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_rejects_mixed_object_id_widths(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        stdout = f"{'a' * 40}\trefs/heads/main\n{'b' * 64}\trefs/tags/version/1.0\n"
        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
        )

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_rejects_contradictory_duplicate_object_ref(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        stdout = f"{'a' * 40}\trefs/heads/main\n{'b' * 40}\trefs/heads/main\n"
        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
        )

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_inspect_remote_rejects_head_object_mismatching_its_target(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        stdout = f"ref: refs/heads/main\tHEAD\n{'a' * 40}\tHEAD\n{'b' * 40}\trefs/heads/main\n"
        monkeypatch.setattr(
            git_core.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
        )

        with pytest.raises(git_mod.GitError, match="malformed"):
            git_mod._inspect_remote("origin", cwd=repo)

    def test_bootstrap_race_fails_create_only_lease_without_publishing_pair(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The create-only lease rejects even a concurrent fast-forwardable main."""
        import haute._git as git_mod

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        remote_tip = _git(repo, "rev-parse", "main")
        _git(repo, "push", "origin", "main")
        tree = _git(repo, "rev-parse", f"{remote_tip}^{{tree}}")
        local_default = _git(
            repo, "commit-tree", tree, "-p", remote_tip, "-m", "local default advance"
        )
        _git(repo, "update-ref", "refs/heads/main", local_default)
        monkeypatch.setattr(
            git_remote, "_inspect_remote", lambda remote, cwd=None: (set(), None, False)
        )

        with pytest.raises(git_mod.GitError) as exc:
            push_working_pair("origin", repo, cwd=repo)

        assert not isinstance(exc.value, GitPushRejectedError)
        advertised = _git(repo, "ls-remote", str(bare))
        assert _git(repo, "ls-remote", str(bare), "refs/heads/main").split()[0] == remote_tip
        assert f"refs/heads/{WORKING}" not in advertised
        assert f"refs/heads/{LEDGER}" not in advertised

    def test_bootstrap_race_with_same_default_tip_publishes_pair(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An idempotent concurrent creation at the snapshot SHA remains safe."""
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        default_tip = _git(repo, "rev-parse", "main")
        _git(repo, "push", "origin", "main")
        monkeypatch.setattr(
            git_remote, "_inspect_remote", lambda remote, cwd=None: (set(), None, False)
        )

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "main"
        assert result.bootstrapped_default is True
        assert result.pushed_refs == ["main", WORKING, LEDGER]
        assert _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == default_tip
        advertised = _git(repo, "ls-remote", "origin")
        assert f"refs/heads/{WORKING}" in advertised
        assert f"refs/heads/{LEDGER}" in advertised

    def test_tags_only_remote_is_not_mistaken_for_empty(self, repo: Path, tmp_path: Path) -> None:
        """A tag is an object ref, so it cannot authorize default bootstrap."""
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _git(repo, "tag", "outside")
        _git(repo, "push", "origin", "refs/tags/outside")

        with pytest.raises(GitDomainError, match="default branch"):
            push_working_pair("origin", repo, cwd=repo)

        assert "refs/heads/" + WORKING not in _git(repo, "ls-remote", str(bare))

    def test_unrelated_established_default_refuses_before_pair_publish(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        other = tmp_path / "unrelated"
        _git(tmp_path, "init", str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        (other / "other.txt").write_text("unrelated\n")
        _git(other, "add", "other.txt")
        _git(other, "commit", "-m", "unrelated")
        _git(other, "branch", "-M", "main")
        _git(other, "remote", "add", "origin", str(bare))
        _git(other, "push", "origin", "main")

        with pytest.raises(GitDomainError, match="unrelated"):
            push_working_pair("origin", repo, cwd=repo)

        assert "refs/heads/" + WORKING not in _git(repo, "ls-remote", str(bare))

    def test_empty_remote_refuses_unrelated_local_default_and_pair(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Bootstrap cannot publish an orphan working history beside the merge target."""
        from haute._git_state import write_working_branch

        orphan = "orphan-work"
        _git(repo, "checkout", "--orphan", orphan)
        _git(repo, "rm", "-rf", ".")
        (repo / "orphan.txt").write_text("unrelated\n")
        _git(repo, "add", "orphan.txt")
        _git(repo, "commit", "-m", "orphan root")
        write_working_branch(repo, orphan)
        bare = self._add_bare_remote(repo, tmp_path)

        with pytest.raises(GitDomainError, match="unrelated"):
            push_working_pair("origin", repo, cwd=repo)

        assert _git(repo, "ls-remote", str(bare)) == ""

    def test_empty_remote_bootstraps_a_unique_custom_default(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # The managed pair must not make custom ``trunk`` resolution ambiguous.
        self._setup_pair(repo)
        _git(repo, "branch", "trunk", "main")
        _git(repo, "branch", "-D", "main")
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.default_branch == "trunk"
        assert result.pushed_refs == ["trunk", WORKING, LEDGER]

    def test_push_without_a_working_branch_is_refused(self, repo: Path, tmp_path: Path) -> None:
        self._add_bare_remote(repo, tmp_path)  # no working-branch state recorded
        with pytest.raises(GitDomainError, match="No working branch"):
            push_working_pair("origin", repo, cwd=repo)

    @pytest.mark.parametrize("corrupted_working", ["HEAD", "@"])
    def test_push_rejects_pseudoref_as_corrupted_working_branch_state(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        corrupted_working: str,
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, corrupted_working)
        self._add_bare_remote(repo, tmp_path)
        monkeypatch.setattr(
            git_remote,
            "_inspect_remote",
            lambda *args, **kwargs: pytest.fail("invalid clone state reached remote inspection"),
        )

        with pytest.raises(GitDomainError, match="Invalid working branch"):
            push_working_pair("origin", repo, cwd=repo)

    def test_option_like_working_state_is_rejected_before_git_subprocess(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git as git_mod

        monkeypatch.setattr(
            git_core,
            "_run_git_ok",
            lambda *args, **kwargs: pytest.fail("invalid state reached a Git subprocess"),
        )

        with pytest.raises(GitDomainError, match="Invalid working branch"):
            git_mod._validate_managed_working_branch("--upload-pack=attacker", cwd=repo)

    def test_tag_named_like_working_branch_does_not_satisfy_branch_state(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        working = "tag-only-working"
        _git(repo, "tag", working, "main")
        write_working_branch(repo, working)
        self._add_bare_remote(repo, tmp_path)
        monkeypatch.setattr(
            git_remote,
            "_inspect_remote",
            lambda *args, **kwargs: pytest.fail("missing branch reached remote inspection"),
        )

        with pytest.raises(GitDomainError, match="does not exist"):
            push_working_pair("origin", repo, cwd=repo)

    def test_tag_named_like_missing_ledger_is_not_published_as_a_branch(
        self, repo: Path, tmp_path: Path
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _git(repo, "tag", LEDGER, "main")
        self._add_bare_remote(repo, tmp_path)

        result = push_working_pair("origin", repo, cwd=repo)

        assert result.pushed_refs == ["main", WORKING]
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{LEDGER}") == ""

    def test_push_never_force_overwrites_a_diverged_remote(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # The critical S33 guarantee: a non-fast-forward push is refused and the
        # remote ref is left untouched — never force-overwritten.
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # initial sync

        # A teammate advances the remote's working branch out from under us.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        (other / "rating.py").write_text("# remote edit\n")
        _git(other, "commit", "-am", "remote change")
        _git(other, "push", "origin", WORKING)
        remote_tip = _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}")

        # We advance our own working branch on a different line → divergence.
        _write_and_save(repo, WORKING, {"rating.py": "# local edit\n"})
        commit_milestone("local milestone", repo, cwd=repo)

        with pytest.raises(GitDomainError, match="never force-pushes"):
            push_working_pair("origin", repo, cwd=repo)
        # The remote ref is exactly as the teammate left it — no force overwrite.
        assert _git(repo, "ls-remote", "origin", f"refs/heads/{WORKING}") == remote_tip

    def test_rejection_is_data_bearing_with_per_leg_divergence(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # M7/M6: a non-FF rejection raises GitPushRejectedError carrying the freshly
        # recomputed per-leg divergence, and the message names the BLOCKING leg.
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # initial sync

        # A teammate diverges the WORKING branch on the remote (working only).
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        (other / "rating.py").write_text("# remote edit\n")
        _git(other, "commit", "-am", "remote change")
        _git(other, "push", "origin", WORKING)

        # We advance our own working branch on a different line via a milestone.
        _write_and_save(repo, WORKING, {"rating.py": "# local edit\n"})
        commit_milestone("local milestone", repo, cwd=repo)

        with pytest.raises(GitPushRejectedError) as exc:
            push_working_pair("origin", repo, cwd=repo)
        rej = exc.value.rejection
        assert rej.status == "rejected_diverged"
        assert rej.remote == "origin"
        assert rej.working.status == "diverged"
        # Our local save left the ledger ahead-only (teammate never pushed it), so
        # it isn't a blocking leg and the message names the working branch only.
        assert rej.ledger is not None and rej.ledger.status == "ahead"
        assert "working branch" in rej.message
        assert "save history" not in rej.message
        assert "never force-pushes" in rej.message

    def test_push_sends_version_label_tags(self, repo: Path, tmp_path: Path) -> None:
        # X4: annotated version/<label> tags travel with the push (--follow-tags).
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        _write_and_save(repo, WORKING, {"rating.py": "# v1\n"})
        commit_milestone("milestone 1", repo, version_label="1.0", cwd=repo)
        push_working_pair("origin", repo, cwd=repo)
        assert "refs/tags/version/1.0" in _git(repo, "ls-remote", "--tags", "origin")

    def test_push_refuses_a_reused_version_label(self, repo: Path, tmp_path: Path) -> None:
        # X4 / decision A: a label already on the remote at a different object is a
        # reused canonical name → refuse before the push.
        from haute._git_state import write_working_branch

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)  # sync, no tags yet

        # Clone before any label exists, so 'other' won't have it locally and the
        # local create-time dup-check can't pre-empt the push-time collision check.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        (other / "rating.py").write_text("# other v\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("other milestone", other, version_label="1.0", cwd=other)

        # Meanwhile the canonical 1.0 is published on a DIFFERENT milestone.
        _write_and_save(repo, WORKING, {"rating.py": "# repo v\n"})
        commit_milestone("repo milestone", repo, version_label="1.0", cwd=repo)
        push_working_pair("origin", repo, cwd=repo)

        with pytest.raises(GitDomainError, match="already exist"):
            push_working_pair("origin", other, cwd=other)

    def test_push_is_idempotent_for_a_published_version_label(
        self, repo: Path, tmp_path: Path
    ) -> None:
        # X4 regression (the blocker): version/<label> tags are ANNOTATED, so the
        # tag object's sha != the commit it points to. The collision pre-check
        # compares the underlying release COMMIT (peeled both sides), so a label
        # already on the remote at the SAME commit is a clean idempotent re-push —
        # NOT a reused-name refusal. Comparing tag objects false-positived here.
        import haute._git as git_mod

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        _write_and_save(repo, WORKING, {"rating.py": "# v1\n"})
        commit_milestone("milestone 1", repo, version_label="1.0", cwd=repo)
        push_working_pair("origin", repo, cwd=repo)  # publishes version/1.0
        assert "refs/tags/version/1.0" in _git(repo, "ls-remote", "--tags", "origin")
        # The published label points at THIS release commit → not a collision.
        assert git_mod._tag_collisions("origin", WORKING, cwd=repo) == []
        # …and the whole pair re-pushes cleanly (pre-fix this raised "already exist").
        res = push_working_pair("origin", repo, cwd=repo)
        assert res.pushed_refs == [WORKING, LEDGER]

    def test_leg_state_unknown_when_count_is_unreadable(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F2 tri-state: both refs resolve, but the rev-list ahead/behind count comes
        # back unreadable → status "unknown", never silently "synced". The UI must
        # not render "can't tell" as "in sync".
        import haute._git as git_mod

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")  # both branch and remote-tracking ref resolve
        real_ok = git_core._run_git_ok

        def malformed_count(*args: str, **kwargs: object) -> tuple[bool, str]:
            # Only intercept the rev-list count; let the _rev_parse probes through.
            if args and args[0] == "rev-list":
                return True, "not-a-number garbage"  # parses to a non-int → unknown
            return real_ok(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(git_core, "_run_git_ok", malformed_count)
        leg = git_mod._leg_state(WORKING, "origin", cwd=repo)
        assert leg.status == "unknown"
        assert leg.ahead is None and leg.behind is None

        def failed_revlist(*args: str, **kwargs: object) -> tuple[bool, str]:
            if args and args[0] == "rev-list":
                return False, ""  # the rev-list itself fails → same tri-state
            return real_ok(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(git_core, "_run_git_ok", failed_revlist)
        assert git_mod._leg_state(WORKING, "origin", cwd=repo).status == "unknown"

    def test_push_records_last_pushed_shas(self, repo: Path, tmp_path: Path) -> None:
        # §6.8: a successful push records the published tips (keyed <remote>/<ref>)
        # so X3 rewrite detection survives a pruned reflog.
        from haute._git_state import read_pushed_shas

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        recorded = read_pushed_shas(repo)
        assert recorded[f"origin/{WORKING}"] == _git(repo, "rev-parse", WORKING)
        assert recorded[f"origin/{LEDGER}"] == _git(repo, "rev-parse", LEDGER)

    def test_rejection_flags_a_remote_rewrite(self, repo: Path, tmp_path: Path) -> None:
        # X3: when the remote dropped a commit we published (a force-push upstream),
        # the rejection is flagged as a rewrite with a distinct message.

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        _write_and_save(repo, WORKING, {"rating.py": "# m1\n"})
        commit_milestone("m1", repo, cwd=repo)
        push_working_pair("origin", repo, cwd=repo)  # records origin/<W> = m1

        # A teammate force-pushes a DIFFERENT line over the working branch.
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        root = _git(other, "rev-list", "--max-parents=0", WORKING)
        _git(other, "reset", "--hard", root)  # back before the published milestone
        (other / "rating.py").write_text("# rewritten\n")
        _git(other, "add", "rating.py")
        _git(other, "commit", "-m", "rewritten line")
        _git(other, "push", "--force", "origin", WORKING)
        git_core._fetch_cooldowns.clear()

        with pytest.raises(GitPushRejectedError) as exc:
            push_working_pair("origin", repo, cwd=repo)
        assert exc.value.rejection.is_rewrite is True
        assert "rewritten" in exc.value.rejection.message


class TestFastForwardPair:
    """D1/D2: conflict-free catch-up to the remote by fast-forward only. Refuses
    anything that isn't a clean ff (local work present → branch-away)."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)
        write_working_branch(repo, WORKING)

    def _add_bare_remote(self, repo: Path, tmp_path: Path) -> Path:
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        return bare

    def _clone_with_pair(self, repo: Path, bare: Path, tmp_path: Path) -> Path:
        from haute._git_state import write_working_branch

        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        return other

    def test_d1_fast_forwards_both_legs_when_behind_clean(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Teammate milestones and pushes the whole pair → both legs move ahead.
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# teammate\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("teammate milestone", other, cwd=other)
        push_working_pair("origin", other, cwd=other)

        res = fast_forward_pair("origin", repo, cwd=repo)
        assert set(res.fast_forwarded) == {WORKING, LEDGER}
        assert _git(repo, "rev-parse", WORKING) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{WORKING}"
        )
        assert _git(repo, "rev-parse", LEDGER) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{LEDGER}"
        )
        assert check_invariants(WORKING, cwd=repo) == []  # healthy after the catch-up

    def test_required_fetch_failure_refuses_before_any_ref_mutation(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        monkeypatch.setattr(git_remote, "_fetch_refs", lambda *_args, **_kwargs: False)

        with pytest.raises(GitDomainError, match="Could not refresh 'origin'"):
            fast_forward_pair("origin", repo, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    @pytest.mark.parametrize(
        ("deleted_branch", "kind"),
        [(WORKING, "working branch"), (LEDGER, "save ledger")],
    )
    def test_deleted_remote_leg_has_a_distinct_refusal_after_prune(
        self, repo: Path, tmp_path: Path, deleted_branch: str, kind: str
    ) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        tracking = f"origin/{deleted_branch}"
        assert _git(repo, "branch", "--remotes", "--list", tracking) != ""

        # Delete directly in the bare remote so this clone's tracking ref stays
        # stale until Catch up performs its required authoritative refresh.
        _git(bare, "update-ref", "-d", f"refs/heads/{deleted_branch}")

        with pytest.raises(
            GitDomainError,
            match=rf"{kind} '{deleted_branch}' is missing",
        ):
            fast_forward_pair("origin", repo, cwd=repo)

        assert _git(repo, "branch", "--remotes", "--list", tracking) == ""
        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip

    def test_d2_fast_forwards_ledger_only(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Teammate saves (a ledger commit) and pushes only the ledger.
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# teammate save\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        _git(other, "push", "origin", LEDGER)

        res = fast_forward_pair("origin", repo, cwd=repo)
        assert res.fast_forwarded == [LEDGER]
        assert _git(repo, "rev-parse", LEDGER) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{LEDGER}"
        )

    def test_refuses_when_local_has_unpushed_work(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# teammate\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        _git(other, "push", "origin", LEDGER)
        # Local also saved on a different line → the ledger diverges, not a clean ff.
        _write_and_save(repo, WORKING, {"local.py": "# local\n"})
        with pytest.raises(GitDomainError, match="Spin off a copy"):
            fast_forward_pair("origin", repo, cwd=repo)

    def test_refuses_when_already_synced(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="Already up to date"):
            fast_forward_pair("origin", repo, cwd=repo)


class TestBranchAway:
    """M3: resolve a fork by setting the local pair aside under a dated name and
    repointing the canonical name to the remote tips (never the move-mode rewind)."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)
        write_working_branch(repo, WORKING)

    def _add_bare_remote(self, repo: Path, tmp_path: Path) -> Path:
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        return bare

    def _clone_with_pair(self, repo: Path, bare: Path, tmp_path: Path) -> Path:
        from haute._git_state import write_working_branch

        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        return other

    def test_sets_local_aside_and_adopts_remote(self, repo: Path, tmp_path: Path) -> None:
        from haute._git_state import read_working_branch

        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        # Remote advances on one line; we advance locally on another → both diverge.
        other = self._clone_with_pair(repo, bare, tmp_path)
        (other / "rating.py").write_text("# remote line\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("remote milestone", other, cwd=other)
        push_working_pair("origin", other, cwd=other)
        _write_and_save(repo, WORKING, {"local.py": "# local line\n"})
        commit_milestone("local milestone", repo, cwd=repo, allow_fork=True)
        old_w = _git(repo, "rev-parse", WORKING)
        old_l = _git(repo, "rev-parse", LEDGER)

        res = branch_away("origin", repo, cwd=repo)
        aside = res.set_aside_as
        assert res.working_branch == WORKING
        assert aside.startswith(f"{WORKING}-local-")
        # Canonical name now tracks the shared line…
        assert _git(repo, "rev-parse", WORKING) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{WORKING}"
        )
        assert _git(repo, "rev-parse", LEDGER) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{LEDGER}"
        )
        # …and the local divergent work is preserved under the dated name.
        assert _git(repo, "rev-parse", aside) == old_w
        assert _git(repo, "rev-parse", ledger_name(aside)) == old_l
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING
        assert check_invariants(WORKING, cwd=repo) == []  # adopted state is healthy

    def test_required_fetch_failure_refuses_before_setting_pair_aside(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import read_working_branch

        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        monkeypatch.setattr(git_remote, "_fetch_refs", lambda *_args, **_kwargs: False)

        with pytest.raises(GitDomainError, match="Could not refresh 'origin'"):
            branch_away("origin", repo, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip
        assert _git(repo, "branch", "--list", f"{WORKING}-local-*") == ""
        assert read_working_branch(repo) == WORKING

    def test_x2_respawns_ledger_when_remote_ledger_absent(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        bare = self._add_bare_remote(repo, tmp_path)
        # Push ONLY the working branch — origin never gets the ledger (X2).
        _git(repo, "push", "origin", WORKING)
        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        (other / "rating.py").write_text("# remote direct\n")
        _git(other, "add", "rating.py")
        _git(other, "commit", "-m", "remote direct")
        _git(other, "push", "origin", WORKING)
        # Local diverge.
        _write_and_save(repo, WORKING, {"local.py": "# local\n"})
        commit_milestone("local m", repo, cwd=repo, allow_fork=True)

        branch_away("origin", repo, cwd=repo)
        assert _git(repo, "rev-parse", WORKING) == _git(
            repo, "rev-parse", f"refs/remotes/origin/{WORKING}"
        )
        # No remote ledger → respawned at the adopted working tip.
        assert _git(repo, "rev-parse", LEDGER) == _git(repo, "rev-parse", WORKING)
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    def test_refuses_when_already_synced(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="Already in sync"):
            branch_away("origin", repo, cwd=repo)

    def test_refuses_when_remote_has_no_working_branch(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)  # nothing pushed
        with pytest.raises(GitDomainError, match="to adopt"):
            branch_away("origin", repo, cwd=repo)


class TestMilestoneForkGate:
    """U4/D4: save&commit refuses (with data) when the working branch is behind
    its canonical remote — a milestone there would fork the shared copy — unless
    the user overrides with allow_fork. The check is local-only and degrades open."""

    def _setup_pair(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        resolve_ledger(WORKING, cwd=repo)
        write_working_branch(repo, WORKING)

    def _remote_ahead_on_working(self, repo: Path, tmp_path: Path) -> None:
        """Publish a milestone on WORKING from another clone so the local working
        branch ends up one milestone behind its remote (no local advance)."""
        from haute._git_state import write_working_branch

        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        push_working_pair("origin", repo, cwd=repo)  # sync W + L

        other = tmp_path / "other"
        _git(repo, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", WORKING)
        write_working_branch(other, WORKING)
        resolve_ledger(WORKING, cwd=other)
        (other / "rating.py").write_text("# teammate edit\n")
        commit_save(["rating.py"], WORKING, cwd=other)
        commit_milestone("teammate milestone", other, cwd=other)
        _git(other, "push", "origin", WORKING)

        git_core._fetch_cooldowns.clear()
        fetch_pair("origin", WORKING, cwd=repo)  # refresh repo's tracking ref

    def test_divergence_state_none_without_remote(self, repo: Path) -> None:
        self._setup_pair(repo)
        assert divergence_state(WORKING, cwd=repo) is None

    def test_milestone_refused_when_behind_remote(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._remote_ahead_on_working(repo, tmp_path)
        # We have local saves to milestone, but the remote moved ahead first.
        _write_and_save(repo, WORKING, {"local.py": "# local work\n"})

        with pytest.raises(GitMilestoneForkError) as exc:
            commit_milestone("my milestone", repo, cwd=repo)
        fork = exc.value.fork
        assert fork.status == "would_fork"
        assert fork.remote == "origin"
        assert fork.working.status in ("behind", "diverged")
        assert "fork" in fork.message

    def test_allow_fork_override_commits_anyway(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        self._remote_ahead_on_working(repo, tmp_path)
        _write_and_save(repo, WORKING, {"local.py": "# local work\n"})
        # The deliberate override lands the milestone (creating the fork).
        res = commit_milestone("my milestone", repo, cwd=repo, allow_fork=True)
        assert res.short_sha
        assert working_milestones(repo, cwd=repo).entries[0].message == "my milestone"

    def test_gate_degrades_open_when_untracked(self, repo: Path, tmp_path: Path) -> None:
        # A configured-but-never-pushed remote has no tracking ref, so the leg is
        # "untracked" — the gate must NOT block (offline / local-first safety).
        self._setup_pair(repo)
        _git(repo, "init", "--bare", str(tmp_path / "origin.git"))
        _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
        _write_and_save(repo, WORKING, {"local.py": "# local work\n"})
        res = commit_milestone("offline milestone", repo, cwd=repo)
        assert res.short_sha  # committed without a fork prompt


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
                    ["git", "update-ref"], 1, stdout=b"", stderr=b"\xffdiagnostic\n"
                ),
            ]
        )
        monkeypatch.setattr(git_core.subprocess, "run", lambda *_args, **_kwargs: next(results))

        assert git_core._run_git("hash-object", "--stdin", input_text="payload\n") == "�object"
        with pytest.raises(GitError, match="�diagnostic"):
            git_core._run_git("update-ref", "--stdin", input_text="create refs/test x\n")
