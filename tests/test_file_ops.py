"""Tests for haute._file_ops — atomic write primitives and Writer context manager.

Covers Foundation tasks F2 (atomic_write_bytes / atomic_write_text) and
F6 (Writer context manager with self-write marking).

These tests are TDD: they fail until ``src/haute/_file_ops.py`` is implemented
by the dev agent.

Atomicity model (matches the parquet temp-rename pattern at
``_polars_utils.py:22-43``):

- Write payload to a sibling ``.tmp`` file in the same directory as the
  target (same filesystem — ``os.replace`` is atomic only on one FS).
- ``Path.replace`` the temp onto the target. If the replace raises, the
  temp cleanup is attempted and the publication exception propagates — no
  partial target. A cleanup failure is attached without replacing that error.
- Parent directory is NOT silently created (fail loudly — project principle).
- Target being a directory is a loud error, never silently replaced.

Writer contract (F6):

- Context manager. Within the ``with`` block the caller may call
  ``write_text`` or ``write_bytes`` zero-or-more times. Only the LAST call
  wins (simpler model, documented in ``test_writer_multiple_writes_last_wins``).
- On clean ``__exit__``: calls ``mark_self_write(path)`` BEFORE the atomic
  commit, then renames temp onto target.
- On ``__exit__`` with exception: cleans up any temp file. No write.
- If no ``write_*`` call happened, ``__exit__`` is a no-op — the target
  file is unchanged (or still absent).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute._file_ops import Writer, atomic_write_bytes, atomic_write_text

# ---------------------------------------------------------------------------
# F2: atomic_write_bytes
# ---------------------------------------------------------------------------


class TestAtomicWriteBytes:
    """Tests for the low-level atomic_write_bytes primitive."""

    def test_writes_bytes_exactly(self, tmp_path: Path) -> None:
        """Payload round-trips byte-for-byte via read_bytes()."""
        target = tmp_path / "out.bin"
        payload = b"\x00\x01\x02hello\xff\xfe"

        atomic_write_bytes(target, payload)

        assert target.exists()
        assert target.read_bytes() == payload

    def test_empty_bytes(self, tmp_path: Path) -> None:
        """Writing zero bytes produces an empty file, not a missing one."""
        target = tmp_path / "empty.bin"

        atomic_write_bytes(target, b"")

        assert target.exists()
        assert target.read_bytes() == b""
        assert target.stat().st_size == 0

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        """Calling atomic_write_bytes on an existing file replaces content."""
        target = tmp_path / "existing.bin"
        target.write_bytes(b"old contents here")

        atomic_write_bytes(target, b"new")

        assert target.read_bytes() == b"new"

    def test_crash_mid_write_leaves_original_intact(self, tmp_path: Path) -> None:
        """If the final rename fails, the original file is unchanged."""
        target = tmp_path / "stable.bin"
        original = b"this must survive"
        target.write_bytes(original)

        # Patch Path.replace so the rename step always blows up.
        with patch.object(Path, "replace", side_effect=OSError("simulated rename failure")):
            with pytest.raises(OSError, match="simulated rename failure"):
                atomic_write_bytes(target, b"should never land here")

        # Target file is unchanged — no partial write visible.
        assert target.exists()
        assert target.read_bytes() == original

    def test_transient_windows_sharing_violation_retries_same_atomic_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "stable.bin"
        target.write_bytes(b"old")
        original_replace = Path.replace
        attempts = 0

        class SimulatedSharingViolationError(PermissionError):
            winerror = 32

        def transient_replace(source: Path, destination: Path) -> Path:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise SimulatedSharingViolationError("transient sharing violation")
            return original_replace(source, destination)

        monkeypatch.setattr("haute._file_ops._IS_WINDOWS", True)
        with (
            patch.object(Path, "replace", transient_replace),
            patch("haute._file_ops.time.sleep") as sleep,
        ):
            atomic_write_bytes(target, b"new")

        assert attempts == 3
        assert [item.args[0] for item in sleep.call_args_list] == [0.01, 0.025]
        assert target.read_bytes() == b"new"
        assert not tuple(tmp_path.glob("*.tmp"))

    def test_exhausted_windows_sharing_violation_preserves_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "stable.bin"
        target.write_bytes(b"old")

        class SimulatedAccessDeniedError(PermissionError):
            winerror = 5

        monkeypatch.setattr("haute._file_ops._IS_WINDOWS", True)
        with (
            patch.object(
                Path,
                "replace",
                side_effect=SimulatedAccessDeniedError("persistent access denied"),
            ) as replace,
            patch("haute._file_ops.time.sleep") as sleep,
        ):
            with pytest.raises(SimulatedAccessDeniedError, match="persistent access denied"):
                atomic_write_bytes(target, b"new")

        assert replace.call_count == 5
        assert [item.args[0] for item in sleep.call_args_list] == [0.01, 0.025, 0.05, 0.1]
        assert target.read_bytes() == b"old"
        assert not tuple(tmp_path.glob("*.tmp"))

    def test_winerror_shaped_failure_is_not_retried_off_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "stable.bin"
        target.write_bytes(b"old")

        class SimulatedAccessDeniedError(PermissionError):
            winerror = 5

        monkeypatch.setattr("haute._file_ops._IS_WINDOWS", False)
        with (
            patch.object(
                Path,
                "replace",
                side_effect=SimulatedAccessDeniedError("not a Win32 failure"),
            ) as replace,
            patch("haute._file_ops.time.sleep") as sleep,
        ):
            with pytest.raises(SimulatedAccessDeniedError, match="not a Win32 failure"):
                atomic_write_bytes(target, b"new")

        assert replace.call_count == 1
        sleep.assert_not_called()
        assert target.read_bytes() == b"old"

    def test_temp_cleanup_failure_does_not_replace_publication_error(self, tmp_path: Path) -> None:
        target = tmp_path / "stable.bin"
        stage = tmp_path / "known-stage.tmp"
        target.write_bytes(b"old")
        original_unlink = Path.unlink

        def fail_stage_cleanup(path: Path, *args: object, **kwargs: object) -> None:
            if path == stage:
                raise PermissionError("stage cleanup blocked")
            original_unlink(path, *args, **kwargs)

        with (
            patch("haute._file_ops._temp_path_for", return_value=stage),
            patch.object(Path, "replace", side_effect=OSError("publication failed")),
            patch.object(Path, "unlink", fail_stage_cleanup),
            pytest.raises(OSError, match="publication failed") as raised,
        ):
            atomic_write_bytes(target, b"new")

        assert any("stage cleanup blocked" in note for note in raised.value.__notes__)
        assert target.read_bytes() == b"old"
        assert stage.read_bytes() == b"new"

    def test_parent_directory_missing_raises(self, tmp_path: Path) -> None:
        """Writing into a non-existent directory fails loudly (no silent mkdir)."""
        target = tmp_path / "does" / "not" / "exist" / "foo.bin"
        assert not target.parent.exists()

        with pytest.raises((FileNotFoundError, OSError)):
            atomic_write_bytes(target, b"payload")

        # Confirm we did NOT silently create the directory tree.
        assert not target.parent.exists()
        assert not target.exists()

    def test_target_is_directory_raises(self, tmp_path: Path) -> None:
        """Target being an existing directory is a loud error, not a replace."""
        target = tmp_path / "iamadir"
        target.mkdir()

        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            atomic_write_bytes(target, b"payload")

        # Directory still exists and is still a directory.
        assert target.exists()
        assert target.is_dir()

    def test_large_payload_10mb(self, tmp_path: Path) -> None:
        """A 10 MB payload round-trips without exploding."""
        target = tmp_path / "big.bin"
        payload = b"A" * (10 * 1024 * 1024)  # 10 MiB

        atomic_write_bytes(target, payload)

        assert target.stat().st_size == len(payload)
        assert target.read_bytes() == payload

    def test_no_stray_tmp_file_after_success(self, tmp_path: Path) -> None:
        """On success, no .tmp sibling lingers next to the target."""
        target = tmp_path / "clean.bin"

        atomic_write_bytes(target, b"payload")

        # Any sibling whose name starts with the target stem and ends in .tmp
        # is considered stray. Be permissive about exact suffix format.
        strays = [
            p
            for p in tmp_path.iterdir()
            if p != target and p.name.startswith(target.name) and p.suffix == ".tmp"
        ]
        assert strays == [], f"Unexpected leftover temp files: {strays}"


# ---------------------------------------------------------------------------
# F2: atomic_write_text
# ---------------------------------------------------------------------------


class TestAtomicWriteText:
    """Tests for the low-level atomic_write_text primitive."""

    def test_writes_text_default_utf8(self, tmp_path: Path) -> None:
        """Default encoding is utf-8; text round-trips."""
        target = tmp_path / "out.txt"
        payload = "hello world\n"

        atomic_write_text(target, payload)

        assert target.exists()
        assert target.read_text(encoding="utf-8") == payload

    def test_writes_text_custom_encoding(self, tmp_path: Path) -> None:
        """Non-default encoding (latin-1) is respected on disk and on read."""
        target = tmp_path / "latin.txt"
        payload = "café résumé"

        atomic_write_text(target, payload, encoding="latin-1")

        # Verify the on-disk bytes are latin-1-encoded (differ from utf-8).
        assert target.read_bytes() == payload.encode("latin-1")
        assert target.read_text(encoding="latin-1") == payload

    def test_overwrite_existing_text(self, tmp_path: Path) -> None:
        """atomic_write_text replaces an existing text file."""
        target = tmp_path / "existing.txt"
        target.write_text("old text", encoding="utf-8")

        atomic_write_text(target, "new text")

        assert target.read_text(encoding="utf-8") == "new text"

    def test_crash_mid_write_leaves_original_intact(self, tmp_path: Path) -> None:
        """If the rename fails, the original text file is unchanged."""
        target = tmp_path / "stable.txt"
        original = "original text content"
        target.write_text(original, encoding="utf-8")

        with patch.object(Path, "replace", side_effect=OSError("simulated rename failure")):
            with pytest.raises(OSError, match="simulated rename failure"):
                atomic_write_text(target, "would-be replacement")

        assert target.read_text(encoding="utf-8") == original

    def test_parent_directory_missing_raises(self, tmp_path: Path) -> None:
        """Writing into a non-existent directory fails loudly."""
        target = tmp_path / "missing" / "path" / "file.txt"
        assert not target.parent.exists()

        with pytest.raises((FileNotFoundError, OSError)):
            atomic_write_text(target, "payload")

        assert not target.parent.exists()
        assert not target.exists()

    def test_target_is_directory_raises(self, tmp_path: Path) -> None:
        """Target being a directory is a loud error."""
        target = tmp_path / "somedir"
        target.mkdir()

        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            atomic_write_text(target, "payload")

        assert target.exists()
        assert target.is_dir()

    def test_unicode_multibyte_roundtrip(self, tmp_path: Path) -> None:
        """Multi-byte unicode content round-trips via default utf-8."""
        target = tmp_path / "unicode.txt"
        payload = "Hello 世界 🦀 — café naïve Ω ∑ 𝕏"

        atomic_write_text(target, payload)

        assert target.read_text(encoding="utf-8") == payload


# ---------------------------------------------------------------------------
# F6: Writer context manager
# ---------------------------------------------------------------------------


class TestAtomicWriteWindowsReaderContention:
    """Win32-specific contract: behaviour of the temp-then-rename replace
    when a concurrent reader holds the target open.

    On POSIX ``rename(2)`` is atomic even under open readers, so the
    replace always succeeds. On Windows ``Path.replace`` →
    ``MoveFileExW(MOVEFILE_REPLACE_EXISTING)`` is NOT robust under reader
    contention: a reader opened with the default share mode (Python
    ``open()`` / ``read_bytes`` does NOT pass ``FILE_SHARE_DELETE``) makes
    the replace fail with ``PermissionError`` (WinError 5,
    ERROR_ACCESS_DENIED) or ERROR_SHARING_VIOLATION.

    These tests pin the REAL guarantee observed on this machine:

    * The reader never observes torn/partial bytes — it sees exactly the
      old complete payload.
    * The replace raises a clear OS sharing error after the bounded transient
      retry window is exhausted (there is no non-atomic fallback).
    * The original file on disk is left intact (no partial write lands).
    * No stray ``.tmp`` sibling lingers — the cleanup path runs.

    If Windows semantics ever change so the replace *succeeds* under an
    open reader, these assertions will break and force a docs update.
    """

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Reader-contention replace failure is Windows-specific; POSIX rename(2) succeeds.",
    )
    def test_bytes_replace_raises_under_open_reader_no_corruption(self, tmp_path: Path) -> None:
        target = tmp_path / "sidecar.bin"
        original = b"OLD COMPLETE PAYLOAD"
        target.write_bytes(original)

        # Hold the target open for read with the default Windows share mode
        # (no FILE_SHARE_DELETE), which is exactly what load_sidecar /
        # read_bytes / read_text do in production.
        with open(target, "rb") as reader:
            # The reader sees the complete OLD payload, never a partial one.
            assert reader.read() == original

            with pytest.raises(PermissionError) as excinfo:
                atomic_write_bytes(target, b"NEW PAYLOAD that must not land")

            # ERROR_ACCESS_DENIED (5) or ERROR_SHARING_VIOLATION (32).
            assert excinfo.value.winerror in (5, 32), (
                f"Expected an access/sharing OS error; got winerror={excinfo.value.winerror}"
            )

        # Original content is intact — no torn/partial write landed.
        assert target.read_bytes() == original
        # The staging temp was cleaned up despite the failure.
        strays = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert strays == [], f"Leftover temp files: {strays}"

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Reader-contention replace failure is Windows-specific; POSIX rename(2) succeeds.",
    )
    def test_text_replace_raises_under_open_reader_no_corruption(self, tmp_path: Path) -> None:
        target = tmp_path / "sidecar.json"
        original = '{"old": "complete"}'
        target.write_text(original, encoding="utf-8")

        with open(target, encoding="utf-8") as reader:
            assert reader.read() == original

            with pytest.raises(PermissionError) as excinfo:
                atomic_write_text(target, '{"new": "must not land"}')

            assert excinfo.value.winerror in (5, 32), (
                f"Expected an access/sharing OS error; got winerror={excinfo.value.winerror}"
            )

        assert target.read_text(encoding="utf-8") == original
        strays = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert strays == [], f"Leftover temp files: {strays}"


class TestWriterHappyPath:
    """Basic success-path tests for the Writer context manager."""

    def test_happy_path_write_text(self, tmp_path: Path) -> None:
        """with Writer(path) as w: w.write_text('hi') -> path.read_text() == 'hi'."""
        target = tmp_path / "pipeline.py"

        with Writer(target) as w:
            w.write_text("hi")

        assert target.read_text(encoding="utf-8") == "hi"

    def test_happy_path_write_bytes(self, tmp_path: Path) -> None:
        """Writer supports write_bytes too."""
        target = tmp_path / "pipeline.bin"

        with Writer(target) as w:
            w.write_bytes(b"\x00\x01raw bytes\xff")

        assert target.read_bytes() == b"\x00\x01raw bytes\xff"

    def test_target_not_visible_until_exit(self, tmp_path: Path) -> None:
        """Inside the with block the target does not yet have new content.

        This is the key invariant: consumers of the file only ever see
        the previous or the final state, never a half-written file.
        """
        target = tmp_path / "partial.txt"
        target.write_text("old", encoding="utf-8")

        with Writer(target) as w:
            w.write_text("new")
            # Before __exit__, the target still shows the old content.
            assert target.read_text(encoding="utf-8") == "old"

        assert target.read_text(encoding="utf-8") == "new"


class TestWriterFailure:
    """Failure-path tests for the Writer context manager."""

    def test_exception_inside_with_does_not_write(self, tmp_path: Path) -> None:
        """If the with-block raises, the target is NOT written and temp is cleaned."""
        target = tmp_path / "nope.txt"

        with pytest.raises(RuntimeError, match="boom"):
            with Writer(target) as w:
                w.write_text("partial")
                raise RuntimeError("boom")

        # Target was never created.
        assert not target.exists()

        # No stray .tmp sibling either.
        strays = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert strays == [], f"Leftover temp files: {strays}"

    def test_exception_inside_with_preserves_existing_target(self, tmp_path: Path) -> None:
        """Existing target content survives an aborted Writer."""
        target = tmp_path / "stable.txt"
        original = "must survive"
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="fail"):
            with Writer(target) as w:
                w.write_text("would replace")
                raise ValueError("fail")

        assert target.read_text(encoding="utf-8") == original

        strays = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert strays == [], f"Leftover temp files: {strays}"


class TestWriterSelfWriteCallback:
    """Tests for the mark_self_write callback wiring."""

    def test_mark_self_write_called_with_target_path(self, tmp_path: Path) -> None:
        """Callback is invoked exactly once with the target path."""
        target = tmp_path / "pipeline.py"
        mark = MagicMock()

        with Writer(target, mark_self_write=mark) as w:
            w.write_text("content")

        mark.assert_called_once_with(target)

    def test_mark_self_write_called_before_rename(self, tmp_path: Path) -> None:
        """Callback fires BEFORE the commit — the target does not yet show
        the new content when the callback runs.

        This is crucial: the file watcher must register the incoming write
        in its self-write set BEFORE the fs event is emitted by the rename.
        """
        target = tmp_path / "pipeline.py"
        # Give the target a distinct pre-existing content so we can tell
        # whether the rename has already happened when the callback fires.
        target.write_text("BEFORE", encoding="utf-8")

        observed_at_callback: dict[str, object] = {}

        def _callback(p: Path) -> None:
            # At the moment this callback runs, the rename must not yet
            # have been executed. So the target on disk still shows the
            # old content.
            observed_at_callback["path"] = p
            observed_at_callback["content_at_callback"] = p.read_text(encoding="utf-8")

        with Writer(target, mark_self_write=_callback) as w:
            w.write_text("AFTER")

        assert observed_at_callback["path"] == target
        # Ordering invariant: when the callback fired, the new content
        # was not yet visible through the target path.
        assert observed_at_callback["content_at_callback"] == "BEFORE"
        # But once we're out of the with block, the new content IS visible.
        assert target.read_text(encoding="utf-8") == "AFTER"

    def test_mark_self_write_none_no_crash(self, tmp_path: Path) -> None:
        """mark_self_write=None is the default and must be accepted."""
        target = tmp_path / "out.txt"

        # Explicit None
        with Writer(target, mark_self_write=None) as w:
            w.write_text("hello")

        assert target.read_text(encoding="utf-8") == "hello"

    def test_mark_self_write_default_is_none(self, tmp_path: Path) -> None:
        """Writer(path) without a callback works fine (default None)."""
        target = tmp_path / "default.txt"

        with Writer(target) as w:
            w.write_text("payload")

        assert target.read_text(encoding="utf-8") == "payload"

    def test_mark_self_write_not_called_on_exception(self, tmp_path: Path) -> None:
        """If the with-block raises, mark_self_write is NEVER called.

        This protects against feedback-loop suppression for events that
        never actually happen — we only mark real writes.
        """
        target = tmp_path / "failed.txt"
        mark = MagicMock()

        with pytest.raises(RuntimeError, match="nope"):
            with Writer(target, mark_self_write=mark) as w:
                w.write_text("partial")
                raise RuntimeError("nope")

        mark.assert_not_called()
        assert not target.exists()


class TestWriterEdgeCases:
    """Edge cases: unused Writer, multiple writes, concurrent writers."""

    def test_unused_writer_no_op_when_target_absent(self, tmp_path: Path) -> None:
        """Writer that never calls write_* leaves an absent target absent."""
        target = tmp_path / "never_written.txt"
        assert not target.exists()

        with Writer(target):
            pass  # no write_* call

        assert not target.exists()
        # No stray temp files either.
        strays = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert strays == []

    def test_unused_writer_no_op_when_target_exists(self, tmp_path: Path) -> None:
        """Writer that never calls write_* leaves an existing target unchanged."""
        target = tmp_path / "existing.txt"
        original = "untouched"
        target.write_text(original, encoding="utf-8")

        with Writer(target):
            pass  # no write_* call

        assert target.read_text(encoding="utf-8") == original
        strays = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert strays == []

    def test_unused_writer_does_not_call_mark_self_write(self, tmp_path: Path) -> None:
        """No write_* call means no self-write mark either."""
        target = tmp_path / "unused.txt"
        mark = MagicMock()

        with Writer(target, mark_self_write=mark):
            pass

        mark.assert_not_called()

    def test_multiple_writes_last_wins(self, tmp_path: Path) -> None:
        """Documented behaviour: repeated write_* calls within a Writer
        are last-wins, not concatenative. Picking the simpler model.

        If we ever change to concatenative, this test should break —
        forcing us to update docs and callers.
        """
        target = tmp_path / "replay.txt"

        with Writer(target) as w:
            w.write_text("first")
            w.write_text("second")
            w.write_text("third and final")

        assert target.read_text(encoding="utf-8") == "third and final"

    def test_mixed_write_text_and_write_bytes_last_wins(self, tmp_path: Path) -> None:
        """Mixing write_text and write_bytes in one Writer still follows
        last-wins. Ensures there's no accidental buffer concatenation
        across types."""
        target = tmp_path / "mixed.bin"

        with Writer(target) as w:
            w.write_text("text first")
            w.write_bytes(b"\xde\xad\xbe\xef final bytes")

        assert target.read_bytes() == b"\xde\xad\xbe\xef final bytes"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "Win32 MoveFileExW is not atomic under contention: a concurrent "
            "rename to the same target can fail with ERROR_ACCESS_DENIED / "
            "ERROR_SHARING_VIOLATION. Production impl deliberately does not "
            "retry (no silent fallback), so loser threads fail loudly on "
            "Windows. POSIX rename(2) is atomic, so the test's premise holds "
            "there."
        ),
    )
    def test_concurrent_writers_final_state_is_one_of_two(self, tmp_path: Path) -> None:
        """Two threads each running a Writer on the same path must not
        crash, and the final file content must exactly equal one of
        the two payloads (last-rename wins on one filesystem). We
        deliberately do NOT assert which one wins."""
        target = tmp_path / "raced.txt"
        payload_a = "payload from thread A" * 100
        payload_b = "payload from thread B" * 100

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def _run(payload: str) -> None:
            try:
                barrier.wait(timeout=5)
                with Writer(target) as w:
                    w.write_text(payload)
            except BaseException as exc:  # noqa: BLE001 — collect for main thread
                errors.append(exc)

        t1 = threading.Thread(target=_run, args=(payload_a,))
        t2 = threading.Thread(target=_run, args=(payload_b,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not t1.is_alive() and not t2.is_alive()
        assert not errors, f"Thread errors: {errors}"

        # Final state must be one of the two complete payloads — never a
        # torn/partial write.
        final = target.read_text(encoding="utf-8")
        assert final in (payload_a, payload_b), (
            f"Concurrent writers produced a torn file: {final!r} is neither payload"
        )

    def test_writer_parent_missing_raises(self, tmp_path: Path) -> None:
        """Writer into a non-existent parent directory fails loudly,
        same as the primitives."""
        target = tmp_path / "missing" / "deep" / "file.txt"
        assert not target.parent.exists()

        with pytest.raises((FileNotFoundError, OSError)):
            with Writer(target) as w:
                w.write_text("payload")

        assert not target.exists()
        assert not target.parent.exists()

    def test_writer_overwrites_existing_target(self, tmp_path: Path) -> None:
        """Writer replaces existing target content, like atomic_write_*."""
        target = tmp_path / "replace.txt"
        target.write_text("stale", encoding="utf-8")

        with Writer(target) as w:
            w.write_text("fresh")

        assert target.read_text(encoding="utf-8") == "fresh"
