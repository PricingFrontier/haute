"""Tests for haute._gitignore_guard — the shared .gitignore guard-entry list.

One list, two writers: ``haute init`` and the unborn-repo seed in
``haute._git.set_working_branch``.  These tests pin the helper's contract so
the two sites cannot drift.
"""

from pathlib import Path

from haute._gitignore_guard import GITIGNORE_GUARD_ENTRIES, ensure_gitignore_guards


class TestGuardEntryList:
    def test_covers_the_secret_and_per_clone_paths(self) -> None:
        """The entries haute must never let into git history."""
        assert ".env" in GITIGNORE_GUARD_ENTRIES
        assert ".haute/" in GITIGNORE_GUARD_ENTRIES  # per-clone state — lockout class
        assert ".haute_cache/" in GITIGNORE_GUARD_ENTRIES
        assert "data/" in GITIGNORE_GUARD_ENTRIES
        assert "mlruns/" in GITIGNORE_GUARD_ENTRIES
        assert "impact_report.md" in GITIGNORE_GUARD_ENTRIES
        assert ".venv/" in GITIGNORE_GUARD_ENTRIES  # matches the *.py seed pathspec otherwise

    def test_stable_layer_json_is_not_ignored(self) -> None:
        """<pipeline>.haute.json is stable-layer (tracked) — must NOT be ignored."""
        assert not any("haute.json" in entry for entry in GITIGNORE_GUARD_ENTRIES)


class TestEnsureGitignoreGuards:
    def test_creates_file_when_absent(self, tmp_path: Path) -> None:
        added = ensure_gitignore_guards(tmp_path)

        assert added == list(GITIGNORE_GUARD_ENTRIES)
        content = (tmp_path / ".gitignore").read_text()
        for entry in GITIGNORE_GUARD_ENTRIES:
            assert entry in content.splitlines()

    def test_appends_only_missing_entries(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("__pycache__/\n.env\n")

        added = ensure_gitignore_guards(tmp_path)

        assert ".env" not in added
        assert ".haute/" in added
        lines = (tmp_path / ".gitignore").read_text().splitlines()
        assert lines.count(".env") == 1  # no duplicate
        assert "__pycache__/" in lines  # user content preserved
        for entry in GITIGNORE_GUARD_ENTRIES:
            assert entry in lines

    def test_noop_when_all_entries_present(self, tmp_path: Path) -> None:
        content = "\n".join(GITIGNORE_GUARD_ENTRIES) + "\n"
        (tmp_path / ".gitignore").write_text(content)

        added = ensure_gitignore_guards(tmp_path)

        assert added == []
        assert (tmp_path / ".gitignore").read_text() == content

    def test_tolerates_non_utf8_bytes(self, tmp_path: Path) -> None:
        """User-authored files may carry Windows-1252 bytes; must not raise."""
        (tmp_path / ".gitignore").write_bytes(b"caf\xe9/\n.env\n")

        added = ensure_gitignore_guards(tmp_path)

        assert ".env" not in added
        lines = (tmp_path / ".gitignore").read_text(errors="replace").splitlines()
        assert lines.count(".env") == 1
