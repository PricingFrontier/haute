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
    _slugify,
    archive_commit,
    archive_working_pair,
    branch_category,
    check_invariants,
    commit_context,
    commit_milestone,
    commit_save,
    create_working_branch,
    delete_working_pair,
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


def _data_source_config(label: str) -> str:
    """Relative config path the save flow writes for a dataSource node *label*."""
    return f"config/data_source/{_sanitize_func_name(label)}.json"

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

    OLD = "config/data_source/alpha.json"
    NEW = "config/data_source/beta.json"
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
        out = _git(
            repo, "show", "--name-status", "--format=", "--find-renames=100%", rename_sha
        )
        assert "R100" in out, out
        assert self.OLD in out and self.NEW in out

    def test_rename_with_minor_content_edit_still_follows(self, repo: Path) -> None:
        # A rename can carry a small content edit and still be followed — git's
        # similarity heuristic only needs the file to stay mostly the same. (The
        # converse, a rename bundled with a *large* rewrite of a tiny config, can
        # fall below the threshold and sever history; that is git's limit, not a
        # staging bug, and is the "content-minimal where possible" caveat in §3.5.)
        old = "config/data_source/gamma.json"
        new = "config/data_source/delta.json"
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
                        nodeType="dataSource",
                        config={"path": "data.parquet"},
                    ),
                )
            ],
            edges=[],
        )
        body = SavePipelineRequest(name="demo", source_file="demo.py", graph=graph)
        svc = SavePipelineService(root)
        with patch.object(svc, "_infer_flatten_schemas"):
            return svc.save(body)

    def test_node_rename_is_rename_preserving_in_ledger(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        old_rel = _data_source_config("Alpha")
        new_rel = _data_source_config("Beta")

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
        history = _git(
            repo, "log", "--follow", "--format=%H", "--", new_rel
        ).splitlines()
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
        assert _data_source_config("Alpha") in rename_lines[0]
        assert _data_source_config("Beta") in rename_lines[0]

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
                            nodeType="dataSource",
                            config={"path": "data.parquet"},
                        ),
                    )
                ],
                edges=[],
            )
            body = SavePipelineRequest(
                name="main", source_file="rating/main.py", graph=graph
            )
            svc = SavePipelineService(project_root=repo, pipeline_root=pipeline_root)
            with patch.object(svc, "_infer_flatten_schemas"):
                return svc.save(body)

        first = save("Alpha")
        second = save("Beta")
        assert first.git_sha is not None and second.git_sha is not None

        old_rel = f"rating/{_data_source_config('Alpha')}"
        new_rel = f"rating/{_data_source_config('Beta')}"
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

        assert _data_source_config("Alpha") in captured["paths"], captured
        assert _data_source_config("Beta") in captured["paths"], captured


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

    def test_milestone_saves_returns_folded_saves_and_clears_pending(
        self, repo: Path
    ) -> None:
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
        old = "config/data_source/alpha.json"
        new = "config/data_source/beta.json"
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
        unicode_path = "config/data_source/café.json"
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
        assert [
            s.message for s in pending_ledger_saves(repo, cwd=repo, branch=WORKING).saves
        ] == ["save 2"]

    def test_move_at_older_milestone_refused(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        commit_milestone("M2", repo, cwd=repo)  # M1 is now an older milestone
        with pytest.raises(GitDomainError, match="latest milestone or a pending save"):
            create_working_branch("nope", repo, at=ids["m1"], move=True, cwd=repo)

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

    def test_records_fork_point_and_removes_on_delete(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        create_working_branch("feature", repo, cwd=repo)  # forks at latest milestone

        def by_name() -> dict[str, object]:
            return {b.name: b for b in working_branches(repo, cwd=repo).branches}

        assert by_name()["feature"].forked_from == ids["m1"]  # type: ignore[attr-defined]
        delete_working_pair("feature", repo, confirm=True, cwd=repo)
        assert "feature" not in by_name()  # fork entry gone with the branch

    def test_fork_point_follows_archive_and_restore(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        create_working_branch("feature", repo, cwd=repo)
        archive_working_pair("feature", repo, cwd=repo)
        archived = {b.name: b for b in working_branches(repo, cwd=repo).branches}
        assert archived["archive/feature"].forked_from == ids["m1"]
        restore_working_pair("archive/feature", repo, cwd=repo)
        live = {b.name: b for b in working_branches(repo, cwd=repo).branches}
        assert live["feature"].forked_from == ids["m1"]

    def test_stale_fork_point_dropped(self, repo: Path) -> None:
        from haute._git_state import set_fork

        _fork_setup(repo)
        create_working_branch("feature", repo, cwd=repo)
        set_fork(repo, "feature", "0" * 40)  # point at a non-existent commit
        by_name = {b.name: b for b in working_branches(repo, cwd=repo).branches}
        assert by_name["feature"].forked_from is None

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
        assert r.ahead is None and r.behind is None

    def test_push_sends_both_working_and_ledger(self, repo: Path, tmp_path: Path) -> None:
        self._setup_pair(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})  # ledger advances
        self._add_bare_remote(repo, tmp_path)
        res = push_working_pair("origin", repo, cwd=repo)
        assert set(res.pushed_refs) == {WORKING, LEDGER}
        remote_refs = _git(repo, "ls-remote", "origin")
        assert f"refs/heads/{WORKING}" in remote_refs
        assert f"refs/heads/{LEDGER}" in remote_refs

    def test_ahead_after_a_local_milestone_following_a_push(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._setup_pair(repo)
        self._add_bare_remote(repo, tmp_path)
        push_working_pair("origin", repo, cwd=repo)
        _git(repo, "fetch", "origin")  # establish remote-tracking refs locally
        # Just pushed: the working branch is level with the remote.
        synced = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert synced.ahead == 0 and synced.behind == 0
        # A local milestone advances the working branch beyond the remote. It is a
        # merge commit, so raw commit-count ahead is 2 (the folded ledger save +
        # the merge commit itself) — ahead/behind are honest git commit counts.
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("local milestone", repo, cwd=repo)
        r = next(x for x in list_remotes(repo, cwd=repo).remotes if x.name == "origin")
        assert r.ahead == 2 and r.behind == 0

    def test_remote_url_credentials_are_redacted(self, repo: Path, tmp_path: Path) -> None:
        # A token embedded in an https remote URL must never cross the API
        # boundary (threat model: remote URLs/credentials stay server-side).
        self._setup_pair(repo)
        _git(repo, "remote", "add", "origin", "https://x-access-token:ghp_SECRET123@github.com/org/repo.git")
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

    def test_push_without_a_working_branch_is_refused(
        self, repo: Path, tmp_path: Path
    ) -> None:
        self._add_bare_remote(repo, tmp_path)  # no working-branch state recorded
        with pytest.raises(GitDomainError, match="No working branch"):
            push_working_pair("origin", repo, cwd=repo)

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
