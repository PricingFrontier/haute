"""Tests for haute._hashing — content-hash helper using xxhash64.

Covers Foundation task F3: a small utility module that produces content-based
hashes for files and in-memory bytes. Used to replace the existing mtime-based
cache keys (TOCTOU-racy) with a deterministic content fingerprint.

All tests below are expected to fail until:

1. ``xxhash>=3.0.0`` is added to project dependencies.
2. ``src/haute/_hashing.py`` is implemented with the public API described in
   the module spec:

       content_hash(path: Path) -> str        # stream-reads file
       content_hash_bytes(data: bytes) -> str # in-memory bytes
       HASH_ALGO = "xxh64"                    # metadata constant
"""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path

import pytest

# ``resource`` is POSIX-only; on Windows it's absent and the RSS test is
# skipped anyway. Import it lazily so module *collection* works cross-platform.
try:
    import resource  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Windows branch
    resource = None  # type: ignore[assignment]

from haute._hashing import HASH_ALGO, content_hash, content_hash_bytes

# ---------------------------------------------------------------------------
# Known test vectors (canonical xxh64 digests with default seed=0)
# ---------------------------------------------------------------------------
#
# These pin the algorithm: any change to the hash function (algorithm, seed,
# endianness, default options, etc.) will make these break and force a
# conscious review. They come from the canonical xxHash reference vectors
# (https://github.com/Cyan4973/xxHash) and are produced by every
# conforming implementation of xxh64 with seed=0.
#
EMPTY_XXH64 = "ef46db3751d8e999"
HELLO_XXH64 = "26c7827d889f6da3"


# ---------------------------------------------------------------------------
# HASH_ALGO constant
# ---------------------------------------------------------------------------


class TestHashAlgoConstant:
    def test_hash_algo_value(self) -> None:
        """The module exposes HASH_ALGO = 'xxh64' for metadata stamping."""
        assert HASH_ALGO == "xxh64"

    def test_hash_algo_is_string(self) -> None:
        assert isinstance(HASH_ALGO, str)


# ---------------------------------------------------------------------------
# Known vectors — pin the algorithm
# ---------------------------------------------------------------------------


class TestKnownVectors:
    """Lock down specific digest values so the algorithm can't silently drift."""

    def test_empty_bytes_matches_canonical_xxh64(self) -> None:
        assert content_hash_bytes(b"") == EMPTY_XXH64

    def test_hello_bytes_matches_canonical_xxh64(self) -> None:
        assert content_hash_bytes(b"hello") == HELLO_XXH64

    def test_empty_file_matches_canonical_xxh64(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        assert content_hash(p) == EMPTY_XXH64

    def test_hello_file_matches_canonical_xxh64(self, tmp_path: Path) -> None:
        p = tmp_path / "hello.bin"
        p.write_bytes(b"hello")
        assert content_hash(p) == HELLO_XXH64


# ---------------------------------------------------------------------------
# Output shape: 16-char lowercase hex string
# ---------------------------------------------------------------------------


class TestDigestShape:
    """xxh64 produces a 64-bit digest → 16 hex characters."""

    _HEX_CHARS = set(string.hexdigits.lower())  # 0-9 a-f (explicit lowercase)

    def _assert_well_formed(self, digest: str) -> None:
        assert isinstance(digest, str), f"digest must be str, got {type(digest)!r}"
        assert len(digest) == 16, f"digest length must be 16, got {len(digest)}"
        # Lowercase hex only — no uppercase, no 0x prefix, no whitespace.
        assert set(digest).issubset(self._HEX_CHARS), (
            f"digest contains non-lowercase-hex chars: {digest!r}"
        )

    def test_bytes_digest_is_16_char_lowercase_hex(self) -> None:
        self._assert_well_formed(content_hash_bytes(b"some arbitrary content"))

    def test_empty_bytes_digest_is_16_char_lowercase_hex(self) -> None:
        self._assert_well_formed(content_hash_bytes(b""))

    def test_file_digest_is_16_char_lowercase_hex(self, tmp_path: Path) -> None:
        p = tmp_path / "payload.bin"
        p.write_bytes(b"hello world")
        self._assert_well_formed(content_hash(p))

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"\x00",
            b"\xff" * 64,
            b"The quick brown fox jumps over the lazy dog",
            bytes(range(256)),  # one of every byte value
        ],
        ids=["empty", "single-null", "all-ones-64", "ascii", "all-byte-values"],
    )
    def test_digest_shape_for_various_payloads(self, payload: bytes) -> None:
        self._assert_well_formed(content_hash_bytes(payload))


# ---------------------------------------------------------------------------
# File/bytes round-trip equivalence
# ---------------------------------------------------------------------------


class TestFileBytesRoundTrip:
    """content_hash(path) must equal content_hash_bytes(path.read_bytes())."""

    @pytest.mark.parametrize(
        "size",
        [
            0,  # empty file
            1,  # single byte
            1024,  # 1 KB
            1024 * 1024,  # 1 MB
        ],
        ids=["empty", "1-byte", "1kb", "1mb"],
    )
    def test_round_trip_for_size(self, tmp_path: Path, size: int) -> None:
        # Deterministic pseudo-random bytes so the test is reproducible.
        # os.urandom would work too but would yield different data each run;
        # a fixed pattern keeps failures debuggable.
        payload = bytes((i * 37 + 11) & 0xFF for i in range(size))
        p = tmp_path / f"payload_{size}.bin"
        p.write_bytes(payload)

        from_file = content_hash(p)
        from_bytes = content_hash_bytes(payload)

        assert from_file == from_bytes, (
            f"size={size}: file digest {from_file!r} != bytes digest {from_bytes!r}"
        )

    def test_round_trip_for_text_file(self, tmp_path: Path) -> None:
        """Text content (with newlines) must round-trip byte-for-byte."""
        text = "line 1\nline 2\nline 3\n"
        p = tmp_path / "text.txt"
        p.write_text(text, encoding="utf-8")

        assert content_hash(p) == content_hash_bytes(text.encode("utf-8"))

    def test_round_trip_for_binary_with_nulls(self, tmp_path: Path) -> None:
        """Binary data with embedded nulls must not be truncated."""
        payload = b"before\x00middle\x00after"
        p = tmp_path / "nulls.bin"
        p.write_bytes(payload)

        assert content_hash(p) == content_hash_bytes(payload)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_bytes_same_input_same_digest(self) -> None:
        payload = b"determinism check"
        assert content_hash_bytes(payload) == content_hash_bytes(payload)

    def test_file_same_content_same_digest(self, tmp_path: Path) -> None:
        p = tmp_path / "stable.bin"
        p.write_bytes(b"stable contents")
        first = content_hash(p)
        second = content_hash(p)
        assert first == second

    def test_two_files_with_identical_content_share_digest(self, tmp_path: Path) -> None:
        payload = b"identical content"
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(payload)
        b.write_bytes(payload)
        assert content_hash(a) == content_hash(b)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


class TestChangeDetection:
    def test_content_change_changes_digest(self, tmp_path: Path) -> None:
        p = tmp_path / "mutable.bin"
        p.write_bytes(b"version 1")
        before = content_hash(p)

        p.write_bytes(b"version 2")
        after = content_hash(p)

        assert before != after

    def test_single_bit_flip_changes_digest(self, tmp_path: Path) -> None:
        """Even a 1-byte change should produce a different digest."""
        p = tmp_path / "flip.bin"
        p.write_bytes(b"A")
        before = content_hash(p)

        p.write_bytes(b"B")
        after = content_hash(p)

        assert before != after

    def test_append_changes_digest(self, tmp_path: Path) -> None:
        p = tmp_path / "append.bin"
        p.write_bytes(b"original")
        before = content_hash(p)

        with p.open("ab") as f:
            f.write(b" plus more")
        after = content_hash(p)

        assert before != after

    def test_truncation_changes_digest(self, tmp_path: Path) -> None:
        p = tmp_path / "truncate.bin"
        p.write_bytes(b"long content here")
        before = content_hash(p)

        p.write_bytes(b"long")
        after = content_hash(p)

        assert before != after


# ---------------------------------------------------------------------------
# Streaming — 10 MB file must not require 10 MB of memory
# ---------------------------------------------------------------------------


class TestStreaming:
    """Verify large files are processed in chunks, not loaded whole into memory."""

    @staticmethod
    def _make_big_file(path: Path, total_size: int, chunk: bytes) -> None:
        """Write ``total_size`` bytes by repeating ``chunk``.

        Done at write-time so the test process never holds the full payload
        in memory itself.
        """
        assert len(chunk) > 0
        remaining = total_size
        with path.open("wb") as f:
            while remaining > 0:
                n = min(len(chunk), remaining)
                f.write(chunk[:n])
                remaining -= n

    def test_large_file_hash_matches_bytes_hash(self, tmp_path: Path) -> None:
        """The streamed file digest must equal the in-memory digest of identical content."""
        size = 10 * 1024 * 1024  # 10 MB
        chunk = (b"haute-streaming-test-" * 4)[:64]  # 64-byte repeating pattern
        assert size % len(chunk) == 0, "pick a size that's a multiple of chunk length"

        p = tmp_path / "big.bin"
        self._make_big_file(p, size, chunk)

        # Compute the expected digest over the full payload (we DO hold it
        # all in memory here — this is the reference for comparison, not the
        # function under test).
        expected = content_hash_bytes(chunk * (size // len(chunk)))
        actual = content_hash(p)

        assert actual == expected

    @pytest.mark.skipif(
        resource is None,
        reason="resource.getrusage is POSIX-only; RSS tracking unavailable on Windows",
    )
    def test_large_file_does_not_load_entire_file_into_memory(self, tmp_path: Path) -> None:
        """Hashing a 10 MB file must not balloon RSS by ~10 MB.

        Measured with :func:`resource.getrusage`. We allow generous slack
        (5 MB on top of whatever's already resident) because Python heap
        fragmentation, test framework allocations, and xxhash's own
        internal state are not accounted for here. The point of the test
        is to catch a naive ``path.read_bytes()`` implementation, which
        would increase RSS by the full 10 MB.
        """
        assert resource is not None  # narrow type for mypy; skipif guarantees this
        size = 10 * 1024 * 1024  # 10 MB
        chunk = b"x" * 4096
        p = tmp_path / "big_rss.bin"
        self._make_big_file(p, size, chunk)

        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        _ = content_hash(p)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        # On Linux, ru_maxrss is in KB; on macOS it's in bytes. Convert to
        # bytes uniformly by assuming KB if the reported value is "small".
        # This matches the convention used elsewhere in the Python ecosystem.
        if sys.platform == "darwin":
            before_bytes = before
            after_bytes = after
        else:
            before_bytes = before * 1024
            after_bytes = after * 1024

        growth = after_bytes - before_bytes
        # If the implementation loaded the whole file, growth would be >= 10MB.
        # We allow 5MB of slack for unrelated allocations.
        assert growth < 5 * 1024 * 1024, (
            f"RSS grew by {growth} bytes while hashing a 10MB file — "
            "implementation likely loaded the file fully into memory"
        )


# ---------------------------------------------------------------------------
# Error propagation — no silent fallbacks
# ---------------------------------------------------------------------------


class TestMissingFile:
    def test_nonexistent_path_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.bin"
        assert not missing.exists()
        with pytest.raises(FileNotFoundError):
            content_hash(missing)

    def test_nonexistent_absolute_path_raises_file_not_found(self) -> None:
        # Platform-appropriate "definitely not there" path.
        if sys.platform == "win32":
            missing = Path("C:\\this\\path\\does\\not\\exist\\haute_test.bin")
        else:
            missing = Path("/does/not/exist/haute_test.bin")
        with pytest.raises(FileNotFoundError):
            content_hash(missing)

    def test_missing_file_does_not_create_it(self, tmp_path: Path) -> None:
        """Failing to hash must not have the side-effect of creating the file."""
        missing = tmp_path / "ghost.bin"
        with pytest.raises(FileNotFoundError):
            content_hash(missing)
        assert not missing.exists()


class TestDirectoryInput:
    def test_directory_raises(self, tmp_path: Path) -> None:
        """Passing a directory should raise a clear OS-level error, not silently succeed.

        Implementations that use ``open(path, 'rb')`` naturally raise
        :class:`IsADirectoryError` on POSIX. On Windows, ``open`` on a directory
        raises :class:`PermissionError`. Either is acceptable — what's NOT
        acceptable is a successful hash of a directory path.
        """
        # tmp_path itself is a directory.
        assert tmp_path.is_dir()
        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            content_hash(tmp_path)

    def test_empty_subdirectory_raises(self, tmp_path: Path) -> None:
        sub = tmp_path / "empty_subdir"
        sub.mkdir()
        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            content_hash(sub)


# ---------------------------------------------------------------------------
# Unicode paths
# ---------------------------------------------------------------------------


class TestUnicodeFilename:
    def test_unicode_filename_hashes_successfully(self, tmp_path: Path) -> None:
        """File whose *name* is non-ASCII must still hash its *content* correctly."""
        payload = b"unicode filename, ascii content"
        p = tmp_path / "\u6d4b\u8bd5\u6587\u4ef6.bin"  # 测试文件 (test file)
        p.write_bytes(payload)
        assert p.exists()

        assert content_hash(p) == content_hash_bytes(payload)

    def test_emoji_filename_hashes_successfully(self, tmp_path: Path) -> None:
        payload = b"hello from an emoji-named file"
        p = tmp_path / "\U0001f600_greet.bin"  # grinning face emoji
        p.write_bytes(payload)
        assert p.exists()

        assert content_hash(p) == content_hash_bytes(payload)

    def test_accented_latin_filename_hashes_successfully(self, tmp_path: Path) -> None:
        payload = b"cafe content"
        p = tmp_path / "caf\u00e9_na\u00efve_r\u00e9sum\u00e9.bin"  # café_naïve_résumé
        p.write_bytes(payload)
        assert p.exists()

        assert content_hash(p) == content_hash_bytes(payload)

    def test_unicode_content_with_ascii_filename(self, tmp_path: Path) -> None:
        """Content bytes containing UTF-8 multibyte sequences must round-trip."""
        text = "caf\u00e9 na\u00efve r\u00e9sum\u00e9 \U0001f600 \u6d4b\u8bd5"
        payload = text.encode("utf-8")
        p = tmp_path / "utf8_content.txt"
        p.write_bytes(payload)

        assert content_hash(p) == content_hash_bytes(payload)


# ---------------------------------------------------------------------------
# Path-like inputs
# ---------------------------------------------------------------------------


class TestPathInput:
    """content_hash is typed as Path, but it should also accept str paths
    (implementation choice — confirm with dev). At minimum, Path must work
    consistently regardless of how the Path was constructed.
    """

    def test_absolute_path(self, tmp_path: Path) -> None:
        p = tmp_path / "abs.bin"
        p.write_bytes(b"abs-content")
        assert p.is_absolute()
        assert content_hash(p) == content_hash_bytes(b"abs-content")

    def test_path_resolve_same_digest(self, tmp_path: Path) -> None:
        """An unresolved and a resolved path to the same file must agree."""
        p = tmp_path / "res.bin"
        p.write_bytes(b"res-content")
        unresolved = Path(os.fspath(p))
        resolved = p.resolve()
        assert content_hash(unresolved) == content_hash(resolved)
