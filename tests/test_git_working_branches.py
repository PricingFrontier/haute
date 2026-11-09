"""The git engine's working branches and branch-pair management.

Working branches are created, switched to and reported on (including on unborn repositories and
their ``.gitignore`` seed), the branch manager archives and deletes pairs, broken pairs are
detected, a fork is resolved by branching away, and a milestone is refused while the working branch
is behind its remote.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import haute._git_core as git_core
import haute._git_remote as git_remote
import haute._git_setup as git_setup
from haute._git import (
    GitDomainError,
    GitError,
    GitGuardrailError,
    GitMilestoneForkError,
    archive_commit,
    archive_working_pair,
    branch_away,
    check_invariants,
    commit_milestone,
    commit_save,
    create_working_branch,
    delete_working_pair,
    divergence_state,
    ledger_name,
    pending_ledger_saves,
    push_working_pair,
    repair_working_pair,
    resolve_ledger,
    restore_version,
    restore_working_pair,
    set_working_branch,
    working_branch_status,
    working_branches,
    working_milestones,
)
from tests import _git_engine_helpers
from tests._git_engine_helpers import (
    LEDGER,
    WORKING,
    _git,
    _parents,
    _tree,
    _write_and_save,
)
from tests._git_helpers import install_hook

# Fixtures shared with the other modules split from one suite.
repo = _git_engine_helpers.repo
unborn_repo = _git_engine_helpers.unborn_repo


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
    @staticmethod
    def _track_project_hook(repo: Path) -> Path:
        """The scaffolded hook, tracked as `haute init` leaves it; a clone has no copy of it
        in `.git/hooks`."""
        from haute._scaffold import pre_commit_hook

        (repo / ".githooks").mkdir()
        (repo / ".githooks" / "pre-commit").write_text(pre_commit_hook(), newline="\n")
        _git(repo, "add", ".githooks/pre-commit")
        _git(repo, "commit", "-q", "-m", "project hook")
        return repo / ".githooks" / "pre-commit"

    def test_a_clone_gets_the_projects_tracked_hook(self, repo: Path) -> None:
        tracked = self._track_project_hook(repo)

        set_working_branch(WORKING, repo, cwd=repo)

        installed = repo / ".git" / "hooks" / "pre-commit"
        assert installed.read_text() == tracked.read_text()
        if sys.platform != "win32":
            assert os.access(installed, os.X_OK)

    @pytest.mark.parametrize("existing", ["a hook", "a hooks path"])
    def test_a_hook_the_analyst_set_up_is_left_alone(self, repo: Path, existing: str) -> None:
        self._track_project_hook(repo)
        if existing == "a hook":
            own = install_hook(repo, "pre-commit", "#!/bin/sh\nexit 0\n")
        else:
            _git(repo, "config", "core.hooksPath", "my-hooks")

        set_working_branch(WORKING, repo, cwd=repo)

        if existing == "a hook":
            assert own.read_text() == "#!/bin/sh\nexit 0\n"
        else:
            assert not (repo / "my-hooks").exists()
            assert not (repo / ".git" / "hooks" / "pre-commit").exists()

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

    def test_create_refuses_a_name_git_cannot_use(self, repo: Path) -> None:
        with pytest.raises(GitDomainError, match="can't be used as a branch name"):
            set_working_branch("motor pricing", repo, create=True, cwd=repo)

    @staticmethod
    def _detach_at(repo: Path, sha: str) -> None:
        """Leave the project on *sha*, unlinked, as a checkout outside Haute would."""
        from haute._git_state import clear_working_branch

        _git(repo, "checkout", "-q", "--detach", sha)
        clear_working_branch(repo)

    def test_create_from_a_detached_pending_save_starts_at_an_anchoring_milestone(
        self, repo: Path
    ) -> None:
        ids = _fork_setup(repo)
        self._detach_at(repo, ids["s2"])

        set_working_branch("fresh", repo, create=True, cwd=repo)

        assert _parents(repo, "fresh") == [ids["m1"], ids["s2"]]
        assert _tree(repo, "fresh") == _tree(repo, ids["s2"])
        ms = working_milestones(repo, cwd=repo, branch="fresh")
        assert ms.entries[0].message.startswith("Start fresh from save")
        assert [e.message for e in ms.entries[1:]] == ["M1", "initial pipeline"]
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "fresh-save"

    def test_create_from_a_detached_folded_save_anchors_on_the_milestone_before(
        self, repo: Path
    ) -> None:
        ids = _fork_setup(repo)
        commit_milestone("M2", repo, cwd=repo)  # folds save 2 and save 3
        self._detach_at(repo, ids["s2"])

        set_working_branch("fresh", repo, create=True, cwd=repo)

        assert _parents(repo, "fresh") == [ids["m1"], ids["s2"]]
        assert (repo / "rating.py").read_text() == "# v3\n"

    def test_create_from_a_detached_milestone_starts_there(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        self._detach_at(repo, ids["m1"])

        set_working_branch("fresh", repo, create=True, cwd=repo)

        assert _git(repo, "rev-parse", "fresh") == ids["m1"]


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

    @pytest.mark.parametrize(
        ("environment", "marker"),
        [
            ("venv", "pyvenv.cfg"),
            ("env", "pyvenv.cfg"),
            ("tools/py311", "pyvenv.cfg"),
            ("conda", "conda-meta/history"),
        ],
    )
    def test_the_first_commit_never_seeds_a_python_environment(
        self, unborn_repo: Path, environment: str, marker: str
    ) -> None:
        """Only .venv/ was ignored, so a venv/ or env/ beside the pipeline passed the seed's
        *.py permit and its packages entered the permanent root commit."""
        (unborn_repo / "main.py").write_text("x = 1\n")
        root = unborn_repo / environment
        (root / marker).parent.mkdir(parents=True)
        (root / marker).write_text("home = /usr/bin\n")
        package = root / "lib" / "python3.11" / "site-packages" / "polars"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("# an installed package\n")
        (root / "bin").mkdir()
        (root / "bin" / "activate_this.py").write_text("# an activation script\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert not any(f.startswith(f"{environment}/") for f in committed), committed

    def test_an_environment_whose_marker_is_ignored_is_still_left_out(
        self, unborn_repo: Path
    ) -> None:
        """An ignore rule hiding pyvenv.cfg, but not the environment's Python files, must
        not hide the environment from the seed."""
        (unborn_repo / ".gitignore").write_text("pyvenv.cfg\n")
        (unborn_repo / "main.py").write_text("x = 1\n")
        (unborn_repo / "venv").mkdir()
        (unborn_repo / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        package = unborn_repo / "venv" / "lib" / "python3.11" / "site-packages" / "polars"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("# an installed package\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert not any(f.startswith("venv/") for f in committed), committed

    def test_an_implausibly_large_first_commit_is_refused_naming_its_folder(
        self, unborn_repo: Path
    ) -> None:
        """A tree of Python files that is not the project (a copied package, an environment
        without a marker) is named and refused, never silently made permanent."""
        (unborn_repo / "main.py").write_text("x = 1\n")
        vendored = unborn_repo / "vendor" / "lib"
        vendored.mkdir(parents=True)
        for index in range(git_setup._SEED_FILE_LIMIT):
            (vendored / f"module_{index}.py").write_text("x = 1\n")

        with pytest.raises(GitDomainError) as refused:
            set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        limit = git_setup._SEED_FILE_LIMIT
        assert str(refused.value) == (
            f"The first version of this project would hold {limit + 2:,} files, {limit:,} of "
            "them in 'vendor/'. That is more than a project holds: move that folder out of "
            "the project or add it to .gitignore, then try again."
        )
        assert _git(unborn_repo, "rev-list", "--all") == ""
        assert not (unborn_repo / ".haute" / "state.json").exists()

    def test_a_first_commit_holding_a_file_too_large_to_version_is_refused(
        self, unborn_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GitHub refuses a file over the limit, and no later commit can take it out of the
        root commit's history."""
        monkeypatch.setattr(git_core, "VERSIONED_FILE_LIMIT_BYTES", 1024 * 1024)
        (unborn_repo / "main.py").write_text("x = 1\n")
        fixtures = unborn_repo / "tests" / "fixtures"
        fixtures.mkdir(parents=True)
        (fixtures / "portfolio.parquet").write_bytes(b"x" * (1024 * 1024 + 1))

        with pytest.raises(
            GitDomainError,
            match=r"tests/fixtures/portfolio\.parquet \(1 MiB\) is too large for version "
            r"history, which takes files up to 1 MiB",
        ):
            set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        assert _git(unborn_repo, "rev-list", "--all") == ""

    def test_a_large_file_the_seed_leaves_out_does_not_stop_it(
        self, unborn_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(git_core, "VERSIONED_FILE_LIMIT_BYTES", 1024 * 1024)
        (unborn_repo / "main.py").write_text("x = 1\n")
        (unborn_repo / "notes.bin").write_bytes(b"x" * (1024 * 1024 + 1))

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        committed = _git(unborn_repo, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert "notes.bin" not in committed

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
            ".python-version",
            ".env.example",
            "connections.toml",
            "rating/main.py",
            "rating/main.haute.json",
            "rating/utility/helpers.py",
            "rating/config/data_input/quotes.json",
            # The workbench's form folder, in the pipeline folder or, for a root pipeline,
            # at the root.
            "rating/workbench/form.json",
            "rating/workbench/schema/quote_details.json",
            "rating/workbench/sheets/sheet_1.json",
            "rating/workbench/components/table_1.json",
            "workbench/sample/perils.json",
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


class TestRootCommitRunsNoHook:
    def test_the_root_commit_runs_none_of_the_projects_hooks(self, unborn_repo: Path) -> None:
        """The root commit is Haute's own, as a save is: no hook refuses or runs on it."""
        (unborn_repo / "haute.toml").write_text("[project]\n")
        install_hook(unborn_repo, "pre-commit", "#!/bin/sh\nexit 1\n")
        install_hook(unborn_repo, "prepare-commit-msg", "#!/bin/sh\nexit 1\n")
        install_hook(unborn_repo, "post-commit", "#!/bin/sh\ntouch post-commit-ran\n")

        set_working_branch("initial-model", unborn_repo, create=True, cwd=unborn_repo)

        assert "haute.toml" in _git(unborn_repo, "show", "--name-only", "--format=", "main")
        assert not (unborn_repo / "post-commit-ran").exists()


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
        # active pair → HEAD detached at the working branch's last milestone, never
        # another branch's files, and the working branch forgotten
        assert self._current_head(repo) == "HEAD"
        assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", f"archive/{WORKING}")
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
        working_tip = _git(repo, "rev-parse", WORKING)
        delete_working_pair(WORKING, repo, confirm=True, cwd=repo)
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)
        assert self._current_head(repo) == "HEAD"
        assert _git(repo, "rev-parse", "HEAD") == working_tip

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

    def test_unconfirmed_delete_of_the_current_branch_keeps_a_dirty_tree(self, repo: Path) -> None:
        """Only a confirmed delete may discard edits: confirm guarded the unmerged saves
        alone, so a delete without it threw away what was on disk with the branch."""
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("m", repo, cwd=repo)  # nothing unmerged: confirm guards nothing else
        (repo / "rating.py").write_text("# uncommitted edit\n")

        with pytest.raises(GitDomainError) as refused:
            delete_working_pair(WORKING, repo, confirm=False, cwd=repo)

        assert str(refused.value) == (
            "You have unsaved changes on this branch. Save or discard them before deleting it."
        )
        assert (repo / "rating.py").read_text() == "# uncommitted edit\n"
        assert self._branch_exists(repo, WORKING) and self._branch_exists(repo, LEDGER)
        assert self._current_head(repo) == LEDGER

    def test_delete_current_force_discards_dirty_tree(self, repo: Path) -> None:
        # A confirmed delete of the current branch is destructive by intent — a
        # dirty working tree is discarded with it (S38), not a refusal.
        from haute._git_state import read_working_branch

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        commit_milestone("m", repo, cwd=repo)
        milestone = _git(repo, "rev-parse", WORKING)
        (repo / "rating.py").write_text("# uncommitted edit\n")  # dirty tree
        delete_working_pair(WORKING, repo, confirm=True, cwd=repo)
        assert not self._branch_exists(repo, WORKING)
        assert not self._branch_exists(repo, LEDGER)
        # HEAD detached at the last milestone with the edit discarded; state
        # cleared → the startup chooser
        assert _git(repo, "rev-parse", "HEAD") == milestone
        assert (repo / "rating.py").read_text() == "# v2\n"
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
        _fork_setup(repo)
        _git(repo, "checkout", "-q", "-b", "elsewhere", "main")
        (repo / "other.txt").write_text("x\n")
        _git(repo, "add", "other.txt")
        _git(repo, "commit", "-q", "-m", "elsewhere")
        foreign = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", LEDGER)
        with pytest.raises(GitDomainError, match="milestone or a save on the current branch"):
            create_working_branch("nope", repo, at=foreign, cwd=repo)

    def test_fork_at_folded_save_anchors_on_the_milestone_before(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        m2 = commit_milestone("M2", repo, cwd=repo).sha  # folds save 2 and save 3
        res = create_working_branch("exp", repo, at=ids["s2"], cwd=repo)
        assert res.switched is False
        assert _tree(repo, "exp") == _tree(repo, ids["s2"])
        # Anchored on M1, the milestone save 2 came after, never on M2 above it.
        assert _parents(repo, "exp") == [ids["m1"], ids["s2"]]
        ms = working_milestones(repo, cwd=repo, branch="exp")
        assert [e.message for e in ms.entries[1:]] == ["M1", "initial pipeline"]
        assert _git(repo, "rev-parse", WORKING) == m2

    def test_move_at_folded_save_refused(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        commit_milestone("M2", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="older saves can only start a parallel line"):
            create_working_branch("nope", repo, at=ids["s2"], move=True, cwd=repo)

    def test_switch_opens_the_new_branch_and_leaves_the_current_one(self, repo: Path) -> None:
        from haute._git_state import read_working_branch

        ids = _fork_setup(repo)
        ledger_before = _git(repo, "rev-parse", LEDGER)
        res = create_working_branch("idea", repo, at=ids["s2"], switch=True, cwd=repo)
        assert res.switched is True and res.moved is False
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "idea-save"
        assert read_working_branch(repo) == "idea"
        assert (repo / "rating.py").read_text() == "# v3\n"
        # The branch left behind keeps every save.
        assert _git(repo, "rev-parse", LEDGER) == ledger_before
        assert _git(repo, "rev-parse", WORKING) == ids["m1"]

    def test_switch_refuses_tracked_changes(self, repo: Path) -> None:
        ids = _fork_setup(repo)
        (repo / "rating.py").write_text("# not saved\n")
        with pytest.raises(GitDomainError, match="unsaved changes"):
            create_working_branch("idea", repo, at=ids["m1"], switch=True, cwd=repo)
        assert _git(repo, "branch", "--list", "idea") == ""

    def test_switch_rolls_back_a_failed_checkout(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import read_working_branch

        ids = _fork_setup(repo)
        real_capture = git_core._run_git_capture

        def refuse_checkout(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            if args[:2] == ("checkout", "idea-save"):
                return 128, "fatal: simulated checkout failure"
            return real_capture(*args, cwd=cwd)

        monkeypatch.setattr(git_core, "_run_git_capture", refuse_checkout)
        with pytest.raises(GitError):
            create_working_branch("idea", repo, at=ids["m1"], switch=True, cwd=repo)
        assert _git(repo, "branch", "--list", "idea", "idea-save") == ""
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING

    def test_move_and_switch_together_refused(self, repo: Path) -> None:
        _fork_setup(repo)
        with pytest.raises(GitDomainError, match="choose one or the other"):
            create_working_branch("x", repo, move=True, switch=True, cwd=repo)

    def test_name_collision_refused(self, repo: Path) -> None:
        _fork_setup(repo)
        create_working_branch("dup", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="already exists"):
            create_working_branch("dup", repo, cwd=repo)

    @pytest.mark.parametrize(
        ("name", "rule"),
        [
            ("motor pricing", "it contains a space"),
            ("a..b", "it contains '..'"),
            ("x.lock", "it ends with '.lock'"),
            ("pricing/x.lock/y", "a part of it ends with '.lock'"),
            (".hidden", "it starts with '.'"),
            ("pricing/.hidden", "a part of it starts with '.'"),
            ("a//b", "it contains '//'"),
            ("ends/", "it ends with '/'"),
            ("ends.", "it ends with '.'"),
            ("a@{b", "it contains '@{'"),
            ("a:b", "it contains ':'"),
            ("@", "git keeps '@' for the version open now"),
        ],
    )
    def test_a_name_git_cannot_use_is_refused_naming_the_rule(
        self, repo: Path, name: str, rule: str
    ) -> None:
        """A space typed into the name box reached git as a raw failure the server
        could only report as "Operation failed"; it is refused before git, naming the
        rule it breaks, and nothing is created."""
        _fork_setup(repo)
        before = _git(repo, "for-each-ref", "refs/heads/")
        with pytest.raises(GitDomainError) as refused:
            create_working_branch(name, repo, cwd=repo)
        assert str(refused.value) == (
            f"'{name}' can't be used as a branch name: {rule}. "
            "Use letters, numbers, dashes and slashes."
        )
        assert _git(repo, "for-each-ref", "refs/heads/") == before

    def test_git_decides_a_typed_name_the_named_rules_let_through(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``git check-ref-format --branch`` is the authority for a typed name: one the
        named rules missed is still refused in words, before any ref is made."""
        _fork_setup(repo)
        monkeypatch.setattr(git_core, "_branch_name_problem", lambda _name: None)
        before = _git(repo, "for-each-ref", "refs/heads/")
        with pytest.raises(GitDomainError, match="git does not accept it as a branch name"):
            create_working_branch("a..b", repo, cwd=repo)
        assert _git(repo, "for-each-ref", "refs/heads/") == before

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


class TestBrokenPairs:
    """GIT-R16: a pair a crash split, before delete and archive moved both refs in one
    transaction, is reported at startup and Repair makes it whole."""

    def _broken(self, repo: Path) -> list[tuple[str, str, str]]:
        status = working_branch_status(repo, cwd=repo)
        return [(pair.kind, pair.ledger, pair.working) for pair in status.broken_pairs]

    def test_a_ledger_without_its_branch_is_given_it_back_at_its_last_milestone(
        self, repo: Path
    ) -> None:
        """The working branch was started on the default branch's plain commit; it comes
        back there, and every save on the ledger is pending again."""
        set_working_branch(WORKING, repo, cwd=repo)
        start = _git(repo, "rev-parse", WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="first")
        commit_milestone("First milestone", repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="second")
        _git(repo, "update-ref", "-d", f"refs/heads/{WORKING}")  # the old delete's crash

        assert self._broken(repo) == [("orphan-ledger", LEDGER, WORKING)]

        repaired = repair_working_pair(LEDGER, repo, cwd=repo)

        assert (repaired.working, repaired.ledger) == (WORKING, LEDGER)
        assert _git(repo, "rev-parse", WORKING) == start
        assert check_invariants(WORKING, cwd=repo) == []
        pending = pending_ledger_saves(repo, cwd=repo, branch=WORKING).saves
        assert [save.message for save in pending] == ["second", "first"]
        assert self._broken(repo) == []
        assert working_branch_status(repo, cwd=repo).state == "ready"

    def test_a_half_archived_pair_is_archived_whole(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        ledger_tip = _git(repo, "rev-parse", LEDGER)
        _git(repo, "checkout", "-q", "main")
        _git(repo, "branch", "-m", WORKING, f"archive/{WORKING}")  # the old archive's crash

        assert self._broken(repo) == [("half-archived", LEDGER, f"archive/{WORKING}")]

        repaired = repair_working_pair(LEDGER, repo, cwd=repo)

        assert (repaired.working, repaired.ledger) == (f"archive/{WORKING}", f"archive/{LEDGER}")
        assert _git(repo, "rev-parse", f"archive/{LEDGER}") == ledger_tip
        assert _git(repo, "branch", "--list", LEDGER) == ""
        assert self._broken(repo) == []
        archived = {b.name: b for b in working_branches(repo, cwd=repo).branches}
        assert archived[f"archive/{WORKING}"].is_archived

    def test_an_archive_to_a_dated_name_is_finished_under_that_name(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        _git(repo, "checkout", "-q", "main")
        _git(repo, "branch", "-m", WORKING, f"archive/{WORKING}-20261020")

        repaired = repair_working_pair(LEDGER, repo, cwd=repo)

        assert repaired.ledger == f"archive/{WORKING}-20261020-save"
        assert self._broken(repo) == []

    def test_a_restore_save_is_never_taken_for_a_milestone(self, repo: Path) -> None:
        """A restore records one save with one parent: the walk passes it, and the saves
        the lost branch's milestone held are pending again rather than lost."""
        set_working_branch(WORKING, repo, cwd=repo)
        start = _git(repo, "rev-parse", WORKING)
        first = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="first")
        commit_milestone("First milestone", repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="second")
        assert first is not None
        restore_version(first, repo, cwd=repo)
        _git(repo, "update-ref", "-d", f"refs/heads/{WORKING}")

        repair_working_pair(LEDGER, repo, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == start
        pending = [s.message for s in pending_ledger_saves(repo, cwd=repo, branch=WORKING).saves]
        assert pending[0].startswith(f"Restored {first[:8]}")
        assert pending[1:] == ["second", "first"]

    def test_a_branch_started_at_a_save_comes_back_at_its_anchoring_milestone(
        self, repo: Path
    ) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        save = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert save is not None
        create_working_branch("idea", repo, at=save, switch=True, cwd=repo)
        anchor = _git(repo, "rev-parse", "idea")
        assert len(_parents(repo, anchor)) == 2
        _write_and_save(repo, "idea", {"rating.py": "# idea\n"}, message="on the idea")
        _git(repo, "update-ref", "-d", "refs/heads/idea")

        repair_working_pair("idea-save", repo, cwd=repo)

        assert _git(repo, "rev-parse", "idea") == anchor
        pending = pending_ledger_saves(repo, cwd=repo, branch="idea").saves
        assert [s.message for s in pending] == ["on the idea"]

    def test_saves_another_ledger_shares_are_not_taken_for_milestones(self, repo: Path) -> None:
        """After Spin off a copy, the set-aside ledger shares the saves before the fork: they
        lie on its first-parent chain, but they are saves all the same."""
        set_working_branch(WORKING, repo, cwd=repo)
        start = _git(repo, "rev-parse", WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"}, message="first")
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"}, message="second")
        _git(repo, "branch", "aside", WORKING)
        _git(repo, "branch", "aside-save", LEDGER)
        _git(repo, "update-ref", "-d", f"refs/heads/{WORKING}")

        repair_working_pair(LEDGER, repo, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == start
        pending = pending_ledger_saves(repo, cwd=repo, branch=WORKING).saves
        assert [save.message for save in pending] == ["second", "first"]

    def test_repair_refuses_a_ledger_that_is_not_broken(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        before = _git(repo, "for-each-ref", "refs/heads/")

        with pytest.raises(GitDomainError, match="is not part of a broken branch"):
            repair_working_pair(LEDGER, repo, cwd=repo)

        assert _git(repo, "for-each-ref", "refs/heads/") == before
        assert self._broken(repo) == []


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

        _git(repo, "fetch", "origin")  # refresh repo's tracking ref

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
