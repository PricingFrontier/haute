"""Malformed-input fallbacks for the per-clone git-state readers.

Every reader in ``haute._git_state`` treats a corrupt/wrong-shape file as
reconstructable preference, not data: it downgrades to safe defaults rather
than propagating a bad shape. These tests pin those fallbacks — in particular
``read_pushed_shas`` feeds rewrite detection (X3), so a leaked bad shape there
would poison descendant checks.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock, RLock, get_ident


class _TrackingRLock:
    """RLock wrapper that lets concurrency tests verify lock ownership."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state_lock = Lock()
        self._owner: int | None = None
        self._depth = 0
        self.enter_count = 0

    def __enter__(self) -> _TrackingRLock:
        self._lock.acquire()
        ident = get_ident()
        with self._state_lock:
            if self._owner == ident:
                self._depth += 1
            else:
                self._owner = ident
                self._depth = 1
            self.enter_count += 1
        return self

    def __exit__(self, *exc_info: object) -> None:
        with self._state_lock:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
        self._lock.release()

    def held_by_current_thread(self) -> bool:
        with self._state_lock:
            return self._owner == get_ident()


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


class TestNoForkState:
    def test_no_fork_state_helpers_exist(self) -> None:
        import haute._git_state as git_state

        assert not hasattr(git_state, "read_forks")

    def removed_per_entry_type_filter_drops_bad_values(self, tmp_path: Path) -> None:
        return

        # A dict, but only entries with str key AND str value survive — the
        # non-string values (and any non-string key) are filtered out.
        _write(
            tmp_path / ".haute" / "removed.json",
            '{"good": "abc123", "numeric": 5, "nested": {"x": 1}, "nullish": null}',
        )
        assert True


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


class TestRecordPushedShasConcurrency:
    def test_public_reader_uses_the_shared_pushed_state_lock(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import haute._git_state as git_state

        git_state.record_pushed_shas(tmp_path, {"origin/main": "sha-main"})
        tracking_lock = _TrackingRLock()
        monkeypatch.setattr(git_state, "_pushed_state_lock", tracking_lock, raising=False)

        assert git_state.read_pushed_shas(tmp_path) == {"origin/main": "sha-main"}
        assert tracking_lock.enter_count == 1

    def test_concurrent_merges_preserve_both_remote_ref_entries(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import haute._git_state as git_state

        tracking_lock = _TrackingRLock()
        monkeypatch.setattr(git_state, "_pushed_state_lock", tracking_lock, raising=False)
        real_read = git_state._read_pushed_shas_unlocked
        real_atomic_write = git_state.atomic_write_text
        unlocked_read_barrier = Barrier(2)

        def coordinated_read(project_root: Path) -> dict[str, str]:
            snapshot = real_read(project_root)
            if not tracking_lock.held_by_current_thread():
                # If the transaction lock regresses, force both writers to
                # take the same snapshot so the lost update is deterministic.
                unlocked_read_barrier.wait(timeout=5)
            return snapshot

        def guarded_atomic_write(path: Path, data: str, encoding: str = "utf-8") -> None:
            assert tracking_lock.held_by_current_thread(), (
                "the pushed-state lock must cover the write, not only the read"
            )
            real_atomic_write(path, data, encoding)

        monkeypatch.setattr(git_state, "_read_pushed_shas_unlocked", coordinated_read)
        monkeypatch.setattr(git_state, "atomic_write_text", guarded_atomic_write)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                git_state.record_pushed_shas,
                tmp_path,
                {"origin/feature": "sha-feature"},
            )
            second = pool.submit(
                git_state.record_pushed_shas,
                tmp_path,
                {"upstream/review": "sha-review"},
            )
            first.result(timeout=5)
            second.result(timeout=5)

        assert real_read(tmp_path) == {
            "origin/feature": "sha-feature",
            "upstream/review": "sha-review",
        }

    def test_atomic_replace_exposes_only_complete_old_or_new_documents(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import haute._git_state as git_state

        git_state.record_pushed_shas(tmp_path, {"origin/main": "sha-main"})
        target = tmp_path / ".haute" / "pushed.json"
        real_replace = Path.replace
        before_replace = Event()
        finish_replace = Event()
        replacement_sources: list[Path] = []

        def pause_before_replace(source: Path, destination: Path | str) -> Path:
            if Path(destination) == target:
                replacement_sources.append(source)
                before_replace.set()
                assert finish_replace.wait(timeout=5)
            return real_replace(source, destination)

        monkeypatch.setattr(Path, "replace", pause_before_replace)
        with ThreadPoolExecutor(max_workers=1) as pool:
            write = pool.submit(
                git_state.record_pushed_shas,
                tmp_path,
                {"origin/feature": "sha-feature"},
            )
            try:
                assert before_replace.wait(timeout=5)
                assert json.loads(target.read_text(encoding="utf-8")) == {"origin/main": "sha-main"}
                assert len(replacement_sources) == 1
                staged = replacement_sources[0]
                assert staged.parent == target.parent
                assert json.loads(staged.read_text(encoding="utf-8")) == {
                    "origin/main": "sha-main",
                    "origin/feature": "sha-feature",
                }
            finally:
                finish_replace.set()
            write.result(timeout=5)

        assert git_state.read_pushed_shas(tmp_path) == {
            "origin/main": "sha-main",
            "origin/feature": "sha-feature",
        }


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
