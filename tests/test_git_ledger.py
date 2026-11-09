"""The git engine's save ledger, milestones and commit context.

A pipeline save commits to the ledger branch only when it should, a milestone merges the ledger's
saves into the working branch, the baton, git state and identity are read from the repository,
renames are staged as renames across saves, and the ledger's folded-in saves are read back.
Assertions read the commit graph directly through plumbing.
"""

import subprocess
from pathlib import Path

import pytest

import haute._git_core as git_core
import haute._git_transactions as git_transactions
from haute._git import (
    GitDomainError,
    GitError,
    GitGuardrailError,
    check_invariants,
    commit_context,
    commit_milestone,
    commit_parent,
    commit_save,
    get_identity,
    merge_to_working,
    milestone_saves,
    pending_ledger_saves,
    resolve_ledger,
    set_identity,
    set_working_branch,
    working_milestones,
)
from haute._types import GraphNode, NodeData, PipelineGraph
from haute.graph_utils import _sanitize_func_name
from haute.schemas import SavePipelineRequest, SavePipelineResponse
from tests import _git_engine_helpers
from tests._git_engine_helpers import (
    LEDGER,
    WORKING,
    _git,
    _parents,
    _tree,
    _write_and_save,
)
from tests._git_helpers import REWRITING_PRE_COMMIT_HOOK, install_hook
from tests.conftest import current_source_revision

# Fixtures shared with the other modules split from one suite.
repo = _git_engine_helpers.repo


def _data_input_config(label: str) -> str:
    """Relative config path the save flow writes for a dataInput node *label*."""
    return f"config/data_input/{_sanitize_func_name(label)}.json"


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

    def test_a_name_with_glob_characters_commits_only_itself(self, repo: Path) -> None:
        # As a glob, "[ab].csv" would also name a.csv; as a literal pathspec it names itself.
        (repo / "a.csv").write_text("a\n")
        sha = _write_and_save(repo, WORKING, {"[ab].csv": "literal\n"})
        assert sha is not None
        assert _git(repo, "show", "--name-only", "--format=", sha).splitlines() == ["[ab].csv"]
        assert _git(repo, "status", "--porcelain", "--", "a.csv") == "?? a.csv"

    def test_commit_parent_names_a_save_parent_and_none_for_a_root(self, repo: Path) -> None:
        sha = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert sha is not None
        assert commit_parent(sha, cwd=repo) == _parents(repo, sha)[0]
        root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
        assert commit_parent(root, cwd=repo) is None
        with pytest.raises(GitDomainError):
            commit_parent("HEAD~1", cwd=repo)

    def test_default_message_names_the_first_changed_path_in_full(self, repo: Path) -> None:
        # Porcelain reports a modified tracked file as " M rating.py"; the
        # leading space is part of the fixed-width status prefix.
        sha = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        assert sha is not None
        assert _git(repo, "log", "-1", "--format=%s", sha) == "Updated rating"

    def test_a_form_folder_save_names_the_tables_sheets_and_components_it_changed(
        self, repo: Path
    ) -> None:
        sha = _write_and_save(
            repo,
            WORKING,
            {
                "rating/workbench/schema/perils.json": "{}\n",
                "rating/workbench/sheets/br_coverages.json": "{}\n",
                "rating/workbench/components/layers.json": "{}\n",
            },
        )
        assert sha is not None
        # Each file by its path in the folder, a component's under components/.
        assert _git(repo, "log", "-1", "--format=%s", sha) == (
            "Updated workbench/components/layers, workbench/schema/perils, "
            "workbench/sheets/br_coverages"
        )

    def test_a_save_during_a_merge_is_refused_with_the_reason(self, repo: Path) -> None:
        """A merge left in progress outside Haute made every canvas save fail with git's
        "cannot do a partial commit during a merge"; the save now says what is wrong."""
        resolve_ledger(WORKING, cwd=repo)
        before = _git(repo, "rev-parse", LEDGER)
        (repo / ".git" / "MERGE_HEAD").write_text(before + "\n")
        (repo / "rating.py").write_text("# v2\n")
        with pytest.raises(GitDomainError, match="A git operation is in progress"):
            commit_save(["rating.py"], WORKING, cwd=repo)
        assert _git(repo, "rev-parse", LEDGER) == before

    def test_a_failed_status_check_is_an_error_not_an_empty_save(self, repo: Path) -> None:
        """A status check git could not answer read as "nothing changed", so the save
        went unrecorded without a word."""
        resolve_ledger(WORKING, cwd=repo)
        before = _git(repo, "rev-parse", LEDGER)
        (repo / "rating.py").write_text("# v2\n")
        (repo / ".git" / "index").write_bytes(b"not an index")
        with pytest.raises(GitError):
            commit_save(["rating.py"], WORKING, cwd=repo)
        assert _git(repo, "rev-parse", LEDGER) == before


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

    def test_milestone_versions_new_files_in_the_pipeline_folder(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        versioned = {
            "rating/files/data/postcodes.csv": "postcode,region\n",
            "rating/files/models/freq.cbm": "model\n",
            "rating/utility/helpers.py": "x = 1\n",
        }
        left_out = {
            "rating/__pycache__/main.cpython-314.pyc": "bytecode",
            "rating/.haute_build/deploy_manifest.json": "{}",
            "rating/stale.pyc": "bytecode",
            # Names the Files panels hide: never recorded, nor published, by a milestone.
            "rating/.env.local": "SECRET=1\n",
            "rating/.ipynb_checkpoints/model-checkpoint.ipynb": "{}",
            "rating/files/data/.DS_Store": "finder",
            "rating/files/data/.~lock.rates.csv#": "lock",
            "rating/files/data/scratch.tmp": "ignored\n",
            "notes.txt": "outside the pipeline folder\n",
        }
        for rel, content in {**versioned, **left_out}.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        (repo / ".gitignore").write_text("*.tmp\n")

        milestone = commit_milestone(
            "With the pipeline's files", repo, cwd=repo, pipeline_folder="rating"
        ).sha

        committed = set(_git(repo, "ls-tree", "-r", "--name-only", milestone).splitlines())
        assert set(versioned) <= committed
        assert not committed & set(left_out)
        assert not committed & {".gitignore"}

    def test_milestone_versions_a_pipeline_file_left_staged_by_a_failed_save(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        real_run = git_core._run_git

        def fail_commit(*args: str, **kwargs: object) -> str:
            if git_core._subcommand(args) == "commit":
                raise GitError("injected commit failure")
            return real_run(*args, **kwargs)  # type: ignore[arg-type]

        # The save's add lands, its commit does not: the file is staged, not untracked.
        monkeypatch.setattr(git_transactions, "_run_git", fail_commit)
        with pytest.raises(GitError):
            _write_and_save(repo, WORKING, {"rating/files/data/rates.csv": "a,b\n"})
        monkeypatch.undo()
        staged = _git(repo, "status", "--porcelain", "--", "rating/files/data/rates.csv")
        assert staged.startswith("A")

        milestone = commit_milestone(
            "With the staged file", repo, cwd=repo, pipeline_folder="rating"
        ).sha

        assert (
            "rating/files/data/rates.csv"
            in _git(repo, "ls-tree", "-r", "--name-only", milestone).splitlines()
        )

    def test_milestone_without_a_pipeline_folder_leaves_new_files_alone(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        (repo / "rating").mkdir()
        (repo / "rating" / "new.csv").write_text("a\n")

        milestone = commit_milestone("No pipeline folder", repo, cwd=repo).sha

        assert "rating/new.csv" not in _git(repo, "ls-tree", "-r", "--name-only", milestone)

    def test_a_milestone_runs_the_pre_commit_hook_on_its_changes(self, repo: Path) -> None:
        """As a `git commit` of the milestone would: the hook sees the milestone's changes
        staged on the working branch, and what it rewrites is recorded before the milestone."""
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        (repo / "untouched.py").write_text("# as committed\n")
        _git(repo, "add", "untouched.py")
        _git(repo, "commit", "-q", "-m", "another module")
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        install_hook(repo, "pre-commit", REWRITING_PRE_COMMIT_HOOK)

        response = commit_milestone("Rated", repo, cwd=repo)

        assert _git(repo, "show", f"{response.sha}:rating.py") == "# v2\n# formatted"
        assert _git(repo, "show", f"{response.sha}:untouched.py") == "# as committed"
        assert (repo / "rating.py").read_text() == "# v2\n# formatted\n"
        assert _git(repo, "log", "-1", "--format=%s", LEDGER) == (
            "Changes made by the project's pre-commit hook"
        )
        assert _tree(repo, response.sha) == _tree(repo, LEDGER)
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_a_pre_commit_hook_that_refuses_refuses_the_milestone(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        install_hook(repo, "pre-commit", "#!/bin/sh\necho 'rating.py: line too long' >&2\nexit 1\n")
        working_tip = _git(repo, "rev-parse", WORKING)

        with pytest.raises(
            GitDomainError,
            match="The project's pre-commit hook refused the milestone: rating.py: line too long",
        ):
            commit_milestone("Rated", repo, cwd=repo)

        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    def test_a_ruff_refusal_names_the_node_the_line_and_what_to_do(self, repo: Path) -> None:
        """The scaffolded hook's report (``<file>:<line>:<col>: <code> <message>``) is said
        in the editor's terms: the node, the line quoted, and how to fix or keep it."""
        from haute._git_state import write_working_branch
        from haute._lint_refusal import REFUSAL_GUIDE_URL

        write_working_branch(repo, WORKING)
        pipeline = (
            "import haute\n"
            "import polars as pl\n\n"
            'pipeline = haute.Pipeline("rated")\n\n\n'
            "@pipeline.polars\n"
            "def priced(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            '    check = df.filter(pl.col("x") > 0)\n'
            "    return df\n"
        )
        _write_and_save(repo, WORKING, {"main.py": pipeline})
        install_hook(
            repo,
            "pre-commit",
            "#!/bin/sh\n"
            "echo 'main.py:9:5: F841 Local variable `check` is assigned to but never used' >&2\n"
            "exit 1\n",
        )

        with pytest.raises(GitDomainError) as refused:
            commit_milestone("Rated", repo, cwd=repo)

        assert str(refused.value).splitlines() == [
            "the project's pre-commit hook found 1 problem to fix first:",
            '- Node `priced`: `check = df.filter(pl.col("x") > 0)` - Local variable `check` '
            "is assigned to but never used (F841)",
            "Change each line, save, and commit again. To keep a line as it is, end it with "
            f"`# noqa: F841` (its code). More: {REFUSAL_GUIDE_URL}",
        ]

    @pytest.mark.parametrize("failure", ["an interrupt", "a cleanup failure"])
    def test_head_returns_to_the_ledger_whatever_ends_the_hooks(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        install_hook(repo, "pre-commit", "#!/bin/sh\nexit 0\n")
        if failure == "an interrupt":

            def interrupted(*_args: object, **_kwargs: object) -> None:
                raise KeyboardInterrupt

            monkeypatch.setattr(git_transactions, "_run_hook", interrupted)
            expected: type[BaseException] = KeyboardInterrupt
        else:
            unlink = Path.unlink

            def unlink_refused(path: Path, missing_ok: bool = False) -> None:
                if path.name == "haute-milestone-index":
                    raise PermissionError(path)
                unlink(path, missing_ok=missing_ok)

            monkeypatch.setattr(Path, "unlink", unlink_refused)
            expected = PermissionError

        with pytest.raises(expected):
            commit_milestone("Rated", repo, cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER

    def test_a_commit_msg_hook_may_rewrite_the_milestone_message(self, repo: Path) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        install_hook(repo, "commit-msg", '#!/bin/sh\nprintf "\\nRefs: PRICE-42\\n" >> "$1"\n')

        response = commit_milestone("Rated", repo, cwd=repo)

        assert _git(repo, "log", "-1", "--format=%B", response.sha) == "Rated\n\nRefs: PRICE-42"

    def test_milestone_refuses_a_pipeline_file_too_large_to_version(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_state import write_working_branch

        write_working_branch(repo, WORKING)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        monkeypatch.setattr(git_core, "VERSIONED_FILE_LIMIT_BYTES", 1024 * 1024)
        data = repo / "rating" / "files" / "data"
        data.mkdir(parents=True)
        (data / "portfolio.parquet").write_bytes(b"x" * (1024 * 1024 + 1))
        (data / "lookup.csv").write_text("a,b\n")
        working_tip = _git(repo, "rev-parse", WORKING)
        ledger_tip = _git(repo, "rev-parse", LEDGER)

        with pytest.raises(
            GitDomainError,
            match=r"rating/files/data/portfolio\.parquet \(1 MiB\) is too large for version "
            r"history, which takes files up to 1 MiB",
        ):
            commit_milestone("Too large", repo, cwd=repo, pipeline_folder="rating")

        # Nothing was recorded: not the small file beside it, not the milestone.
        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _git(repo, "rev-parse", LEDGER) == ledger_tip

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
    def _save_body(root: Path | None = None) -> SavePipelineRequest:
        base_revision = (
            current_source_revision(root / "demo.py", root) if root is not None else None
        )
        return SavePipelineRequest(name="demo", source_file="demo.py", base_revision=base_revision)

    def _service_save(self, root: Path) -> SavePipelineResponse:
        from unittest.mock import patch

        from haute.routes._save_pipeline import SavePipelineService

        svc = SavePipelineService(root)
        with patch.object(svc, "_validate_api_inputs_have_schemas"):
            return svc.save(self._save_body(root))

    def test_no_working_branch_saves_and_names_what_it_left_off(self, repo: Path) -> None:
        result = self._service_save(repo)
        assert result.git_sha is None
        assert "demo.py" in result.uncaptured
        assert any("no working branch is chosen" in w for w in result.warnings)
        ok = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", LEDGER],
            cwd=repo,
            capture_output=True,
        )
        assert ok.returncode != 0, "no ledger may exist without a configured branch"

    def test_no_repository_owes_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "no_git"
        root.mkdir()
        result = self._service_save(root)
        assert result.git_sha is None
        assert result.uncaptured == []
        assert not any("working branch" in w for w in result.warnings)

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
        # What the skipped capture would have taken, for the editor to capture later.
        assert "demo.py" in result.uncaptured
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
        assert result.uncaptured == []

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

    def test_commit_during_a_rebase_is_refused_with_the_reason(self, repo: Path) -> None:
        self._setup(repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        working = _git(repo, "rev-parse", WORKING)
        (repo / ".git" / "rebase-merge").mkdir()
        with pytest.raises(GitDomainError, match="A git operation is in progress"):
            commit_milestone("During a rebase", repo, cwd=repo)
        assert _git(repo, "rev-parse", WORKING) == working

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
                            "input_type": "file",
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
        body = SavePipelineRequest(
            name="demo",
            source_file="demo.py",
            graph=graph,
            base_revision=current_source_revision(root / "demo.py", root),
        )
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
                                "input_type": "file",
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
            body = SavePipelineRequest(
                name="main",
                source_file="rating/main.py",
                graph=graph,
                base_revision=current_source_revision(repo / "rating/main.py", repo),
            )
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
