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
    path.write_text(text, encoding="utf-8")


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


class TestReadTrashFallbacks:
    def test_unparseable_json_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash

        _write(tmp_path / ".haute" / "trash.json", "not json {")
        assert read_trash(tmp_path) == {}

    def test_non_dict_reads_as_empty(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash

        _write(tmp_path / ".haute" / "trash.json", '["branch", "tombstone"]')
        assert read_trash(tmp_path) == {}

    def test_per_entry_type_filter_keeps_only_str_dict_entries(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash

        # A tombstone is a dict; only str-keyed dict values survive — a scalar
        # value or a non-string key is dropped rather than propagated.
        _write(
            tmp_path / ".haute" / "trash.json",
            '{"good": {"branch_tip": "abc"}, "scalar": 5, "listish": ["x"]}',
        )
        assert read_trash(tmp_path) == {"good": {"branch_tip": "abc"}}


class TestRecordTrashCap:
    def test_records_evict_oldest_beyond_the_cap(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash, record_trash

        # Exceed the cap by two — the two oldest tombstones are dropped and the
        # newest survive, exercising the eviction loop.
        for i in range(22):
            record_trash(tmp_path, f"pricing/test-user/b{i:02d}", {"branch_tip": f"sha{i}"})
        names = set(read_trash(tmp_path))
        assert len(names) == 20
        assert "pricing/test-user/b00" not in names
        assert "pricing/test-user/b01" not in names
        assert "pricing/test-user/b21" in names

    def test_redelete_same_name_refreshes_recency(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash, record_trash

        record_trash(tmp_path, "pricing/test-user/a", {"branch_tip": "one"})
        record_trash(tmp_path, "pricing/test-user/a", {"branch_tip": "two"})
        assert read_trash(tmp_path) == {"pricing/test-user/a": {"branch_tip": "two"}}


class TestRemoveTrashAbsent:
    def test_remove_absent_branch_is_a_noop(self, tmp_path: Path) -> None:
        from haute._git_state import remove_trash

        # No tombstone file at all: removing a name touches nothing.
        remove_trash(tmp_path, "pricing/test-user/ghost")
        assert not (tmp_path / ".haute" / "trash.json").exists()

    def test_remove_absent_leaves_other_tombstones(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash, record_trash, remove_trash

        record_trash(tmp_path, "pricing/test-user/keep", {"branch_tip": "abc"})
        remove_trash(tmp_path, "pricing/test-user/ghost")  # not present → no write
        assert read_trash(tmp_path) == {"pricing/test-user/keep": {"branch_tip": "abc"}}
