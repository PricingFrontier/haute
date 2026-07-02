"""Malformed-input fallbacks for the per-clone git-state readers.

Every reader in ``haute._git_state`` treats a corrupt/wrong-shape file as
reconstructable preference, not data: it downgrades to safe defaults rather
than propagating a bad shape. These tests pin those fallbacks — in particular
``read_pushed_shas`` feeds rewrite detection (X3), so a leaked bad shape there
would poison descendant checks.
"""

from __future__ import annotations

from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestReadPrefsFallbacks:
    def test_unparseable_json_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_prefs

        _write(tmp_path / ".haute" / "prefs.json", "not json {")
        assert read_prefs(tmp_path) == {}

    def test_truncated_json_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_prefs

        _write(tmp_path / ".haute" / "prefs.json", '{"skipSwitchConfirm": tru')
        assert read_prefs(tmp_path) == {}

    def test_wrong_shape_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_prefs

        # Valid JSON, but a list rather than the expected object.
        _write(tmp_path / ".haute" / "prefs.json", '["not", "a", "dict"]')
        assert read_prefs(tmp_path) == {}

    def test_scalar_shape_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_prefs

        _write(tmp_path / ".haute" / "prefs.json", "42")
        assert read_prefs(tmp_path) == {}


class TestReadForksFallbacks:
    def test_unparseable_json_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_forks

        _write(tmp_path / ".haute" / "forks.json", "not json {")
        assert read_forks(tmp_path) == {}

    def test_non_dict_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_forks

        _write(tmp_path / ".haute" / "forks.json", '["branch", "sha"]')
        assert read_forks(tmp_path) == {}

    def test_per_entry_type_filter_drops_bad_values(self, tmp_path: Path) -> None:
        from haute._git_state import read_forks

        # A dict, but only entries with str key AND str value survive — the
        # non-string values (and any non-string key) are filtered out.
        _write(
            tmp_path / ".haute" / "forks.json",
            '{"good": "abc123", "numeric": 5, "nested": {"x": 1}, "nullish": null}',
        )
        assert read_forks(tmp_path) == {"good": "abc123"}

    def test_all_bad_values_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_forks

        _write(tmp_path / ".haute" / "forks.json", '{"a": 1, "b": [2], "c": true}')
        assert read_forks(tmp_path) == {}


class TestReadPushedShasFallbacks:
    def test_unparseable_json_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_pushed_shas

        _write(tmp_path / ".haute" / "pushed.json", "not json {")
        assert read_pushed_shas(tmp_path) == {}

    def test_truncated_json_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_pushed_shas

        _write(tmp_path / ".haute" / "pushed.json", '{"origin/main": "abc')
        assert read_pushed_shas(tmp_path) == {}

    def test_non_dict_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_pushed_shas

        _write(tmp_path / ".haute" / "pushed.json", '["origin/main", "abc123"]')
        assert read_pushed_shas(tmp_path) == {}

    def test_per_entry_type_filter_drops_bad_values(self, tmp_path: Path) -> None:
        from haute._git_state import read_pushed_shas

        # A bad-shape value (a list of SHAs) must not leak through to rewrite
        # detection — only the well-formed str→str entry survives.
        _write(
            tmp_path / ".haute" / "pushed.json",
            '{"origin/main": "deadbeef", "origin/dev": ["a", "b"], "origin/x": 7}',
        )
        assert read_pushed_shas(tmp_path) == {"origin/main": "deadbeef"}


class TestRecordPushedShasEmpty:
    def test_empty_mapping_is_noop(self, tmp_path: Path) -> None:
        from haute._git_state import record_pushed_shas

        # An empty merge writes nothing and creates no .haute/ dir.
        record_pushed_shas(tmp_path, {})
        assert not (tmp_path / ".haute").exists()

    def test_empty_mapping_preserves_existing(self, tmp_path: Path) -> None:
        from haute._git_state import read_pushed_shas, record_pushed_shas

        record_pushed_shas(tmp_path, {"origin/main": "abc123"})
        record_pushed_shas(tmp_path, {})  # no-op, leaves prior state intact
        assert read_pushed_shas(tmp_path) == {"origin/main": "abc123"}
