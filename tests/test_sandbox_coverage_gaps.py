"""Coverage-gap tests for the restricted unpickler ACCEPT arms (_sandbox.py).

The existing ``test_sandbox.py`` suite exercises the *blocked* paths of
``_RestrictedUnpickler.find_class`` and the joblib ``find_class`` shim
exhaustively, but the *accept* arms are only lightly hit.  This file pins
down the RCE-prevention boundary from the allow side:

- the single-element prefix match accept (``module.startswith(prefix)``)
- the two-element exact ``module == / name ==`` match accept
- the joblib-variant accept (delegating to the original ``find_class``)
- the ``ImportError`` fallback to ``safe_unpickle`` when joblib is absent

Each test asserts allowlisted classes round-trip while disallowed ones
still raise — the accept arm must not weaken the deny arm.
"""

from __future__ import annotations

import io
import pickle
import sys
from pathlib import Path

import pytest

from haute._sandbox import (
    UnsafeCodeError,
    _resolve_allowed_global,
    _RestrictedUnpickler,
    safe_joblib_load,
    safe_unpickle,
    set_project_root,
    validate_project_path,
    validate_user_code,
)


class TestRestrictedUnpicklerAcceptArms:
    """Directly exercise the accept branches of find_class (lines 390, 392)."""

    def test_single_element_prefix_match_accepts(self):
        """A module matching a 1-element prefix (numpy) is resolved.

        Hits the ``len(prefix) == 1 and module.startswith(...)`` accept arm.
        """
        import numpy as np

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        # numpy.ndarray's module starts with "numpy" → accepted.
        resolved = unpickler.find_class("numpy", "dtype")
        assert resolved is np.dtype

    def test_single_element_prefix_match_submodule_accepts(self):
        """A submodule (numpy.core.multiarray) still matches the prefix."""
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        # The module string starts with "numpy" → prefix accept arm.
        resolved = unpickler.find_class("numpy.core.multiarray", "_reconstruct")
        assert callable(resolved)

    def test_two_element_exact_match_accepts(self):
        """builtins.dict matches the exact 2-tuple (builtins, dict) entry.

        Hits the ``len(prefix) == 2 and module == ... and name == ...`` arm.
        """
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        resolved = unpickler.find_class("builtins", "dict")
        assert resolved is dict

    def test_two_element_exact_match_accepts_set(self):
        """builtins.set matches its exact 2-tuple allowlist entry."""
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        resolved = unpickler.find_class("builtins", "set")
        assert resolved is set

    def test_builtins_with_unlisted_name_still_blocked(self):
        """builtins.eval is NOT in the 2-tuple allowlist — still rejected.

        Confirms the accept arm does not leak: only the exact (module, name)
        pairs pass, not the whole builtins module.
        """
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("builtins", "eval")


class TestSafeUnpickleRoundTrip:
    """End-to-end safe_unpickle: allowlisted payloads load, others raise."""

    def test_numpy_array_round_trips(self, tmp_path: Path):
        """A numpy array (module 'numpy*') round-trips via the prefix arm."""
        import numpy as np

        set_project_root(tmp_path)
        f = tmp_path / "arr.pkl"
        arr = np.array([1.0, 2.0, 3.0])
        f.write_bytes(pickle.dumps(arr))
        result = safe_unpickle(str(f))
        np.testing.assert_array_equal(result, arr)

    def test_builtin_collections_round_trip(self, tmp_path: Path):
        """Nested builtins (dict/list/tuple/set) load via the 2-tuple arm."""
        set_project_root(tmp_path)
        f = tmp_path / "coll.pkl"
        obj = {"nums": [1, 2, 3], "pair": (4, 5), "uniq": {6, 7}}
        f.write_bytes(pickle.dumps(obj))
        result = safe_unpickle(str(f))
        assert result == obj

    def test_disallowed_class_still_raises(self, tmp_path: Path):
        """An os.system payload is rejected even though accept arms exist."""
        set_project_root(tmp_path)
        f = tmp_path / "evil.pkl"
        payload = (
            b"\x80\x04\x95%\x00\x00\x00\x00\x00\x00\x00"
            b"\x8c\x05posix\x94\x8c\x06system\x94\x93\x94"
            b"\x8c\necho pwned\x94\x85\x94R\x94."
        )
        f.write_bytes(payload)
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_unpickle(str(f))


class TestSandboxBoundaryCoverage:
    """Exercise security-boundary branches pinned by the critical gate."""

    def test_project_path_commonpath_value_error_is_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        set_project_root(tmp_path)
        f = tmp_path / "data.pkl"
        f.write_bytes(pickle.dumps({"ok": True}))

        def _raise_value_error(_paths):
            raise ValueError("mixed roots")

        monkeypatch.setattr("haute._sandbox.os.path.commonpath", _raise_value_error)

        with pytest.raises(ValueError, match="outside.*project root"):
            validate_project_path(str(f))

    def test_match_star_bound_polars_alias_format_is_not_trusted(self):
        code = "match [1, 2, 3]:\n    case [*pl]:\n        leaked = pl.format(fn)\n"

        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code(code)

    def test_match_mapping_rest_bound_polars_alias_format_is_not_trusted(self):
        code = 'match {"x": 1}:\n    case {"x": x, **pl}:\n        leaked = pl.format(fn)\n'

        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code(code)

    def test_allowlisted_class_resolving_to_callable_is_blocked(self):
        def _resolver(_module: str, _name: str):
            return lambda: None

        with pytest.raises(pickle.UnpicklingError, match="non-class callable"):
            _resolve_allowed_global(_resolver, "numpy", "dtype")


class TestJoblibAcceptArm:
    """Exercise the joblib find_class accept arm (line 441) end-to-end."""

    def test_joblib_builtins_exact_match_round_trips(self, tmp_path: Path):
        """A bytearray via joblib hits the joblib 2-tuple exact accept arm.

        Unlike a plain dict (which pickles as literal opcodes), a bytearray
        is reconstructed via a ``find_class("builtins", "bytearray")`` call,
        so it drives the ``len(prefix) == 2`` joblib accept branch.
        """
        import joblib

        set_project_root(tmp_path)
        f = tmp_path / "ba.joblib"
        obj = {"blob": bytearray(b"abc"), "n": range(3), "z": complex(1, 2)}
        joblib.dump(obj, str(f))
        loaded = safe_joblib_load(str(f))
        assert loaded["blob"] == bytearray(b"abc")
        assert list(loaded["n"]) == [0, 1, 2]
        assert loaded["z"] == complex(1, 2)

    def test_joblib_numpy_array_round_trips(self, tmp_path: Path):
        """A numpy array via joblib hits the joblib prefix accept arm."""
        import joblib
        import numpy as np

        set_project_root(tmp_path)
        f = tmp_path / "arr.joblib"
        arr = np.arange(10)
        joblib.dump(arr, str(f))
        np.testing.assert_array_equal(safe_joblib_load(str(f)), arr)

    def test_joblib_disallowed_class_still_raises(self, tmp_path: Path):
        """A malicious joblib payload is still blocked after accept arms run."""
        import joblib

        set_project_root(tmp_path)
        f = tmp_path / "evil.joblib"

        class _Evil:
            def __reduce__(self):
                import os

                return (os.system, ("echo pwned",))

        joblib.dump(_Evil(), str(f))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_joblib_load(str(f))


class TestJoblibMissingImportFallback:
    """Exercise the ImportError -> safe_unpickle fallback (lines 428-431)."""

    def test_falls_back_to_safe_unpickle_when_joblib_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If joblib.numpy_pickle is unimportable, load via safe_unpickle.

        We simulate joblib being unavailable by blocking the import of
        ``joblib.numpy_pickle`` so the ``from ... import NumpyUnpickler``
        raises ImportError, driving the warning + safe_unpickle fallback.
        The file itself is a plain pickle so safe_unpickle can read it.
        """
        set_project_root(tmp_path)
        f = tmp_path / "plain.pkl"
        obj = {"fallback": [1, 2, 3]}
        f.write_bytes(pickle.dumps(obj))

        # Force the `from joblib.numpy_pickle import NumpyUnpickler` to fail.
        monkeypatch.setitem(sys.modules, "joblib.numpy_pickle", None)

        result = safe_joblib_load(str(f))
        assert result == obj

    def test_fallback_still_blocks_disallowed_class(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The safe_unpickle fallback still rejects non-allowlisted classes."""
        set_project_root(tmp_path)
        f = tmp_path / "evil.pkl"
        payload = (
            b"\x80\x04\x95%\x00\x00\x00\x00\x00\x00\x00"
            b"\x8c\x05posix\x94\x8c\x06system\x94\x93\x94"
            b"\x8c\necho pwned\x94\x85\x94R\x94."
        )
        f.write_bytes(payload)

        monkeypatch.setitem(sys.modules, "joblib.numpy_pickle", None)

        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_joblib_load(str(f))
