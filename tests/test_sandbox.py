"""Tests for the security sandbox (_sandbox.py)."""

from __future__ import annotations

import builtins
import pickle
import sys
import tomllib
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest
from packaging.requirements import Requirement

from haute._sandbox import (
    _ALLOWED_PICKLE_CLASSES,
    _ALLOWED_PICKLE_GLOBALS,
    ArtifactVersionMismatchError,
    UnsafeCodeError,
    _resolve_allowed_global,
    safe_globals,
    safe_joblib_load,
    safe_unpickle,
    set_project_root,
    validate_project_path,
    validate_user_code,
)


class TestSafeGlobals:
    """The execution namespace: the ordinary builtins without the server-stopping calls."""

    def test_polars_operations_work(self):
        """Normal Polars code should execute fine."""
        import polars as pl

        ns = safe_globals(pl=pl)
        local = {}
        exec("result = [1, 2, 3]", ns, local)
        assert local["result"] == [1, 2, 3]

    def test_builtins_available(self):
        """Common builtins like len, range, sorted, etc. should work."""
        ns = safe_globals()
        local = {}
        exec("result = len(sorted(range(5)))", ns, local)
        assert local["result"] == 5

    def test_list_comprehension_works(self):
        """List comprehensions need __builtins__ to resolve names."""
        ns = safe_globals()
        local = {}
        exec("result = [x * 2 for x in range(3)]", ns, local)
        assert local["result"] == [0, 2, 4]

    @pytest.mark.parametrize("builtin_name", ["input", "exit", "quit", "breakpoint"])
    def test_a_server_stopping_builtin_cannot_be_called_through_an_alias(self, builtin_name: str):
        """The namespace omits the calls the guard rejects, so an alias cannot reach them."""
        ns = safe_globals()
        with pytest.raises(NameError, match=builtin_name):
            exec(f"alias = {builtin_name}\nalias()", ns, {})


class TestAccidentGuard:
    """SBX-R02: project code is trusted, so the guard rejects only a direct call
    that would hang or stop the server, and ordinary Python passes."""

    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            ("value = input()", "waits for console input the server never receives"),
            ("exit()", "stops the server process"),
            ("quit(1)", "stops the server process"),
            ("breakpoint()", "waits for a debugger on the server's console"),
            ("rows = [input() for _ in range(2)]", "waits for console input"),
        ],
    )
    def test_a_server_stopping_call_is_rejected(self, code: str, reason: str) -> None:
        with pytest.raises(UnsafeCodeError, match=reason):
            validate_user_code(code)

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param("class Band:\n    low = 1", id="class"),
            pytest.param("total = 0\ndef add(x):\n    global total\n    total += x", id="global"),
            pytest.param(
                "def outer():\n    n = 0\n    def inner():\n        nonlocal n\n        n += 1\n"
                "    inner()\n    return n",
                id="nonlocal",
            ),
            pytest.param("column = getattr(pl, 'col')", id="getattr"),
            pytest.param("kind = type(1)", id="type"),
            pytest.param("names = vars()", id="vars"),
            pytest.param("import math", id="import"),
            pytest.param("name = (1).__class__.__name__", id="dunder"),
            pytest.param("text = open", id="open"),
            pytest.param("def input():\n    return 1\nvalue = input()", id="own-input"),
        ],
    )
    def test_ordinary_python_is_accepted(self, code: str) -> None:
        validate_user_code(code)

    def test_the_namespace_runs_classes_reflection_and_imports(self) -> None:
        ns = safe_globals()
        exec(
            "import math\n"
            "class Band:\n"
            "    def __init__(self, low):\n"
            "        self.low = low\n"
            "band = Band(2)\n"
            "low = getattr(band, 'low')\n"
            "kind = type(band).__name__\n"
            "fields = sorted(vars(band))\n"
            "root = math.sqrt(16)\n",
            ns,
        )
        assert (ns["low"], ns["kind"], ns["fields"], ns["root"]) == (2, "Band", ["low"], 4.0)


class TestValidateProjectPath:
    """Verify path validation catches directory traversal."""

    def test_path_inside_root(self, tmp_path: Path):
        set_project_root(tmp_path)
        f = tmp_path / "data.parquet"
        f.touch()
        assert validate_project_path(str(f)) == f

    def test_path_outside_root_raises(self, tmp_path: Path):
        set_project_root(tmp_path / "subdir")
        with pytest.raises(ValueError, match="outside.*project root"):
            validate_project_path("/etc/passwd")

    def test_traversal_attack_blocked(self, tmp_path: Path):
        set_project_root(tmp_path)
        with pytest.raises(ValueError, match="outside.*project root"):
            validate_project_path(str(tmp_path / ".." / ".." / "etc" / "passwd"))


class TestSafeUnpickle:
    """Verify restricted unpickler blocks dangerous payloads."""

    def test_safe_object_loads(self, tmp_path: Path):
        """A plain dict should unpickle fine."""
        set_project_root(tmp_path)
        f = tmp_path / "safe.pkl"
        f.write_bytes(pickle.dumps({"key": "value", "nums": [1, 2, 3]}))
        result = safe_unpickle(str(f))
        assert result == {"key": "value", "nums": [1, 2, 3]}

    def test_os_system_blocked(self, tmp_path: Path):
        """A pickle payload calling os.system should be blocked."""
        set_project_root(tmp_path)
        f = tmp_path / "evil.pkl"
        # Properly crafted payload via __reduce__ → os.system("echo pwned")
        payload = (
            b"\x80\x04\x95%\x00\x00\x00\x00\x00\x00\x00"
            b"\x8c\x05posix\x94\x8c\x06system\x94\x93\x94"
            b"\x8c\necho pwned\x94\x85\x94R\x94."
        )
        f.write_bytes(payload)
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_unpickle(str(f))

    def test_path_outside_root_blocked(self, tmp_path: Path):
        """Pickle loading should fail if path is outside root."""
        set_project_root(tmp_path / "safe_dir")
        f = tmp_path / "outside.pkl"
        f.write_bytes(pickle.dumps(42))
        with pytest.raises(ValueError, match="outside.*project root"):
            safe_unpickle(str(f))

    def test_sklearn_version_mismatch_is_an_error_not_a_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An estimator pickled under another scikit-learn must refuse to load.

        ``BaseEstimator.__setstate__`` compares the pickled ``_sklearn_version``
        to the installed ``sklearn.__version__`` and only warns on mismatch, so
        the load path is real scikit-learn, not a mock.  ``__getstate__`` stamps
        the *current* version at dump time, so the stale pickle is produced by
        dumping under a patched module version rather than by setting the
        attribute by hand.
        """
        import sklearn
        import sklearn.base
        from sklearn.linear_model import LinearRegression

        set_project_root(tmp_path)
        model = LinearRegression().fit([[0.0], [1.0]], [1.0, 3.0])
        f = tmp_path / "stale.pkl"
        with monkeypatch.context() as patched:
            patched.setattr(sklearn.base, "__version__", "0.0.1")
            f.write_bytes(pickle.dumps(model))

        with pytest.raises(ArtifactVersionMismatchError) as excinfo:
            safe_unpickle(str(f))

        message = str(excinfo.value)
        assert "0.0.1" in message
        assert sklearn.__version__ in message

    def test_version_mismatch_filter_does_not_leak_into_process_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The promotion is scoped to the load; the process-wide filters are untouched."""
        import warnings

        set_project_root(tmp_path)
        f = tmp_path / "plain.pkl"
        f.write_bytes(pickle.dumps({"a": 1}))

        # Warm the load path before snapshotting: the allowlist machinery
        # imports scipy on first use, and scipy installs warnings filters of
        # its own at import time. Those are not the promotion under test, and
        # whether they are already installed depends on what ran earlier in
        # this process — so let the imports settle first and compare only what
        # a second, fully warmed load leaves behind.
        assert safe_unpickle(str(f)) == {"a": 1}
        before = list(warnings.filters)

        assert safe_unpickle(str(f)) == {"a": 1}

        assert list(warnings.filters) == before

    @staticmethod
    def _import_refusing_sklearn(missing_name: str):
        """A ``__import__`` that reports scikit-learn absent under *missing_name*.

        A genuinely absent package surfaces as ``ModuleNotFoundError`` whose
        ``.name`` is the top-level package (``sklearn``); a failure deeper in the
        tree carries the deeper name. Both shapes are what the loader
        discriminates on, so both are simulated at the import boundary rather
        than by editing ``sys.modules``, which cannot produce the top-level name
        once the package is already imported.
        """
        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "sklearn" or name.startswith("sklearn."):
                raise ModuleNotFoundError(f"No module named {name!r}", name=missing_name)
            return real_import(name, *args, **kwargs)

        return fake_import

    def test_absent_scikit_learn_means_no_promotion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Without scikit-learn there is no version marker to check; loads proceed."""
        set_project_root(tmp_path)
        f = tmp_path / "plain.pkl"
        f.write_bytes(pickle.dumps({"a": 1}))
        monkeypatch.setattr(builtins, "__import__", self._import_refusing_sklearn("sklearn"))

        assert safe_unpickle(str(f)) == {"a": 1}

    def test_broken_scikit_learn_install_is_not_treated_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Only a missing ``sklearn`` package counts as absent.

        A ``ModuleNotFoundError`` naming a module deeper in the tree is a broken
        install, and it surfaces instead of silently disabling the guard.
        """
        set_project_root(tmp_path)
        f = tmp_path / "plain.pkl"
        f.write_bytes(pickle.dumps({"a": 1}))
        monkeypatch.setattr(
            builtins, "__import__", self._import_refusing_sklearn("sklearn.exceptions")
        )

        with pytest.raises(ModuleNotFoundError, match="sklearn"):
            safe_unpickle(str(f))


class TestSafeJoblibLoad:
    """Verify joblib loading goes through the restricted unpickler."""

    def test_supported_joblib_floor_is_a_direct_dependency(self) -> None:
        """The private restricted-loader contract must be installable from Haute alone."""
        pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
        requirements = {
            requirement.name: requirement
            for raw_requirement in project["dependencies"]
            for requirement in (Requirement(raw_requirement),)
        }

        assert "joblib" in requirements
        supported = requirements["joblib"].specifier
        assert supported.contains("1.5")
        assert not supported.contains("1.4.2")
        assert not supported.contains("2")

    def test_safe_object_loads(self, tmp_path: Path):
        """A plain numpy array saved with joblib should load fine."""
        import joblib
        import numpy as np

        set_project_root(tmp_path)
        f = tmp_path / "safe.joblib"
        data = {"weights": np.array([1.0, 2.0, 3.0]), "bias": 0.5}
        joblib.dump(data, str(f))
        result = safe_joblib_load(str(f))
        assert result["bias"] == 0.5
        np.testing.assert_array_equal(result["weights"], [1.0, 2.0, 3.0])

    def test_safe_sklearn_model_loads(self, tmp_path: Path):
        """A sklearn model saved with joblib should load fine."""
        import joblib
        from sklearn.linear_model import LinearRegression

        set_project_root(tmp_path)
        f = tmp_path / "model.joblib"
        model = LinearRegression()
        joblib.dump(model, str(f))
        result = safe_joblib_load(str(f))
        assert isinstance(result, LinearRegression)
        assert result.get_params() == model.get_params()

    def test_fitted_linear_regression_round_trips(self, tmp_path: Path):
        """A *fitted* LinearRegression must load through the allowlist and
        predict identically.

        The unfitted-model test never exercises the numpy scaffolding path
        (``numpy._core.multiarray._reconstruct``, ``numpy.dtype``/``ndarray``)
        because an unfitted estimator has no ndarray state.  Fitting populates
        ``coef_``/``intercept_`` as numpy arrays, so this is the regression that
        proves the exact-symbol allowlist did not silently break model loading.
        """
        import joblib
        import numpy as np
        from sklearn.linear_model import LinearRegression

        set_project_root(tmp_path)
        x = np.array([[0.0], [1.0], [2.0], [3.0]])
        y = np.array([1.0, 3.0, 5.0, 7.0])  # y = 2x + 1
        model = LinearRegression().fit(x, y)
        expected = model.predict(x)

        f = tmp_path / "fitted.joblib"
        joblib.dump(model, str(f))
        result = safe_joblib_load(str(f))

        assert isinstance(result, LinearRegression)
        np.testing.assert_array_equal(result.coef_, model.coef_)
        assert result.intercept_ == model.intercept_
        np.testing.assert_array_equal(result.predict(x), expected)

    def test_sklearn_version_mismatch_is_an_error_via_joblib(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The joblib loader applies the same version-mismatch promotion as pickle."""
        import joblib
        import sklearn
        import sklearn.base
        from sklearn.linear_model import LinearRegression

        set_project_root(tmp_path)
        model = LinearRegression().fit([[0.0], [1.0]], [1.0, 3.0])
        f = tmp_path / "stale.joblib"
        with monkeypatch.context() as patched:
            patched.setattr(sklearn.base, "__version__", "0.0.1")
            joblib.dump(model, str(f))

        with pytest.raises(ArtifactVersionMismatchError) as excinfo:
            safe_joblib_load(str(f))

        message = str(excinfo.value)
        assert "0.0.1" in message
        assert sklearn.__version__ in message

    def test_fitted_random_forest_round_trips(self, tmp_path: Path):
        """A fitted tree ensemble (RandomForest) round-trips if available.

        Tree models reference a wider set of numpy/sklearn reconstruction
        symbols than a linear model, so this widens coverage of the allowlist's
        scaffolding path.  Skipped only if sklearn's ensemble is unavailable.
        """
        import joblib
        import numpy as np

        pytest.importorskip(
            "sklearn.ensemble",
            reason="sklearn is an optional extra; the tree-ensemble round-trip "
            "only runs when the ensemble module is importable.",
        )
        from sklearn.ensemble import RandomForestRegressor

        set_project_root(tmp_path)
        x = np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]])
        y = np.array([0.0, 1.0, 4.0, 9.0, 16.0, 25.0])
        model = RandomForestRegressor(n_estimators=5, random_state=0).fit(x, y)
        expected = model.predict(x)

        f = tmp_path / "forest.joblib"
        joblib.dump(model, str(f))
        result = safe_joblib_load(str(f))

        assert isinstance(result, RandomForestRegressor)
        np.testing.assert_array_equal(result.predict(x), expected)

    def test_malicious_joblib_blocked(self, tmp_path: Path):
        """A joblib file containing os.system should be blocked."""
        import joblib

        set_project_root(tmp_path)
        f = tmp_path / "evil.joblib"

        # Create a malicious object that would exec on unpickle
        class _Evil:
            def __reduce__(self):
                import os

                return (os.system, ("echo pwned",))

        joblib.dump(_Evil(), str(f))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_joblib_load(str(f))

    def test_subprocess_payload_blocked(self, tmp_path: Path):
        """A joblib file trying to use subprocess should be blocked."""
        import joblib

        set_project_root(tmp_path)
        f = tmp_path / "evil2.joblib"

        class _Evil:
            def __reduce__(self):
                import subprocess

                return (subprocess.call, (["echo", "pwned"],))

        joblib.dump(_Evil(), str(f))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_joblib_load(str(f))

    def test_path_outside_root_blocked(self, tmp_path: Path):
        """Joblib loading should fail if path is outside root."""
        import joblib

        set_project_root(tmp_path / "safe_dir")
        f = tmp_path / "outside.joblib"
        joblib.dump(42, str(f))
        with pytest.raises(ValueError, match="outside.*project root"):
            safe_joblib_load(str(f))

    def test_safe_load_does_not_break_subsequent_loads(self, tmp_path: Path):
        """After safe_joblib_load, normal joblib.load of safe objects works."""
        import joblib

        set_project_root(tmp_path)
        f = tmp_path / "test.joblib"
        joblib.dump([1, 2, 3], str(f))
        safe_joblib_load(str(f))
        # A subsequent normal joblib.load should still work
        assert joblib.load(str(f)) == [1, 2, 3]

    def test_safe_load_restored_after_error(self, tmp_path: Path):
        """After a failed safe_joblib_load, normal joblib.load still works."""
        import joblib

        set_project_root(tmp_path)
        safe_f = tmp_path / "safe.joblib"
        joblib.dump({"a": 1}, str(safe_f))

        evil_f = tmp_path / "evil.joblib"

        class _Evil:
            def __reduce__(self):
                import os

                return (os.system, ("echo pwned",))

        joblib.dump(_Evil(), str(evil_f))
        with pytest.raises(pickle.UnpicklingError):
            safe_joblib_load(str(evil_f))
        # Normal joblib.load should still work after the error
        assert joblib.load(str(safe_f)) == {"a": 1}


class TestValidateUserCode:
    """Ordinary code passes the accident guard; unparseable code is reported."""

    # ------- Legitimate Polars code should pass -------

    def test_polars_assignment_passes(self):
        """Standard Polars assignment is allowed."""
        validate_user_code('df = df.filter(pl.col("age") > 25).select("name", "age")')

    def test_polars_with_columns_passes(self):
        """with_columns expression is allowed."""
        validate_user_code('df.with_columns(\n    premium=pl.col("base") * pl.col("factor")\n)')

    def test_polars_join_passes(self):
        """join expression is allowed."""
        validate_user_code('claims.join(exposure, on="IDpol", how="left")')

    def test_assignment_passes(self):
        """Variable assignment is allowed."""
        validate_user_code('df = claims.filter(pl.col("amount") > 0)')

    def test_list_comprehension_passes(self):
        """List comprehensions are allowed."""
        validate_user_code('cols = [c for c in df.columns if c != "id"]')

    def test_f_string_passes(self):
        """f-strings are allowed."""
        validate_user_code('label = f"col_{i}"')

    def test_function_def_passes(self):
        """Regular (non-async) function definitions are allowed."""
        validate_user_code("def helper(x):\n    return x * 2")

    def test_lambda_passes(self):
        """Lambda expressions are allowed."""
        validate_user_code("fn = lambda x: x * 2")

    def test_dunder_access_passes(self):
        """Dunder access is ordinary Python."""
        validate_user_code('x = "hello".__len__()')

    def test_syntax_error_raises_unsafe_code_error(self):
        """SyntaxError raises UnsafeCodeError (can't verify safety)."""
        with pytest.raises(UnsafeCodeError, match="syntax errors"):
            validate_user_code("df = (((")

    def test_syntax_error_preserves_cause(self):
        """UnsafeCodeError for syntax errors should chain the original SyntaxError as __cause__."""
        with pytest.raises(UnsafeCodeError) as exc_info:
            validate_user_code("def f(\n")
        assert isinstance(exc_info.value.__cause__, SyntaxError)

    def test_syntax_error_not_cached_as_safe(self):
        """Code with syntax errors must not be cached as 'safe' on subsequent calls."""
        # First call should raise
        with pytest.raises(UnsafeCodeError):
            validate_user_code("really broken ((( code ===")
        # Second call should also raise (not return from cache)
        with pytest.raises(UnsafeCodeError):
            validate_user_code("really broken ((( code ===")

    def test_explicit_assignment_passes_validation(self):
        """Explicit df assignment is valid transform code and should pass."""
        validate_user_code('df = df.filter(pl.col("x") > 0)')

    def test_empty_code_passes(self):
        """Empty string should pass."""
        validate_user_code("")


# ===================================================================
# Gap analysis tests — catching real production failure modes
# ===================================================================


class TestJoblibFindClassWeakerThanPickle:
    """Gap 1: joblib find_class only checks module prefix, ignoring the
    2-element tuple constraint.

    Production failure: An attacker crafts a joblib file containing
    ``builtins.eval`` or ``builtins.exec``.  The pickle unpickler correctly
    rejects it (``builtins.eval`` is not in the allowlist), but the joblib
    path silently allows it because it only checks
    ``module.startswith("builtins")`` without verifying the name.
    """

    def test_builtins_eval_blocked_by_pickle(self, tmp_path: Path):
        """The pickle RestrictedUnpickler correctly rejects builtins.eval."""
        import io

        set_project_root(tmp_path)
        # Manually verify that the RestrictedUnpickler blocks builtins.eval
        from haute._sandbox import _RestrictedUnpickler

        buf = io.BytesIO(b"")
        unpickler = _RestrictedUnpickler(buf)
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("builtins", "eval")

    def test_builtins_eval_blocked_by_joblib_find_class(self, tmp_path: Path):
        """FIX: The real gate (``_resolve_allowed_global`` via find_class) —
        shared by the pickle and joblib paths — rejects builtins.eval because
        it is a function, not an exact allowlist entry or a trusted class.
        """
        import io

        set_project_root(tmp_path)
        from haute._sandbox import _RestrictedUnpickler

        # The genuine gate (find_class) blocks builtins.eval on the real path
        # the joblib shim also delegates through.
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("builtins", "eval")

    def test_builtins_exec_blocked_by_both_pickle_and_joblib(self, tmp_path: Path):
        """FIX: The shared gate blocks builtins.exec on both the pickle and
        joblib paths — both route through ``_resolve_allowed_global``."""
        import io

        set_project_root(tmp_path)
        from haute._sandbox import _resolve_allowed_global, _RestrictedUnpickler

        # Pickle path blocks it via find_class.
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("builtins", "exec")

        # The joblib shim delegates to the same ``_resolve_allowed_global``
        # gate; invoking it directly (as the shim does) also rejects exec.
        base_find_class = super(_RestrictedUnpickler, unpickler).find_class
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            _resolve_allowed_global(base_find_class, "builtins", "exec")


class TestPickleAllowlistDotAnchoring:
    """One-segment allowlist entries must match only the package or its submodules."""

    def test_restricted_unpickler_blocks_sibling_module(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``numpy`` in the allowlist must not allow an importable ``numpy_evil`` module."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        module = ModuleType("numpy_evil")
        module.Marker = type("Marker", (), {})
        monkeypatch.setitem(sys.modules, "numpy_evil", module)

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("numpy_evil", "Marker")

    def test_safe_joblib_load_blocks_sibling_module(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``sklearn`` in the allowlist must not allow a ``sklearn_evil`` joblib payload."""
        import joblib

        set_project_root(tmp_path)
        (tmp_path / "sklearn_evil.py").write_text(
            "class Marker:\n    def __init__(self):\n        self.value = 42\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("sklearn_evil", None)
        marker = import_module("sklearn_evil").Marker()

        f = tmp_path / "sibling.joblib"
        joblib.dump(marker, str(f))

        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_joblib_load(str(f))


class TestJoblibMonkeyPatchThreadSafety:
    """Concurrent restricted loads are isolated from process-wide joblib state."""

    def test_concurrent_safe_joblib_load_no_crash(self, tmp_path: Path):
        """Two threads can load safe files without shared-class mutation."""
        import threading

        import joblib
        import numpy as np

        set_project_root(tmp_path)

        # Create two safe joblib files
        for i in range(2):
            f = tmp_path / f"data_{i}.joblib"
            joblib.dump({"arr": np.arange(100), "idx": i}, str(f))

        errors: list[Exception] = []
        results: list[dict] = [None, None]  # type: ignore[list-item]

        def load_file(idx: int) -> None:
            try:
                results[idx] = safe_joblib_load(str(tmp_path / f"data_{idx}.joblib"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=load_file, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent safe_joblib_load raised: {errors}"
        assert results[0]["idx"] == 0
        assert results[1]["idx"] == 1

    def test_find_class_restored_after_concurrent_loads(self, tmp_path: Path):
        """Concurrent calls never change ``NumpyUnpickler.find_class``."""
        import threading

        import joblib
        import numpy as np
        from joblib.numpy_pickle import NumpyUnpickler

        set_project_root(tmp_path)
        original_find_class = NumpyUnpickler.find_class

        for i in range(4):
            f = tmp_path / f"data_{i}.joblib"
            joblib.dump(np.zeros(10), str(f))

        barrier = threading.Barrier(4)

        def load_with_barrier(idx: int) -> None:
            barrier.wait()
            safe_joblib_load(str(tmp_path / f"data_{idx}.joblib"))

        threads = [threading.Thread(target=load_with_barrier, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert NumpyUnpickler.find_class is original_find_class, (
            "safe_joblib_load mutated process-wide NumpyUnpickler.find_class"
        )

    def test_safe_load_never_mutates_process_wide_find_class(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Unrelated joblib callers must never observe a temporary security shim."""
        import joblib
        import numpy as np
        from joblib.numpy_pickle import NumpyUnpickler

        set_project_root(tmp_path)
        original_find_class = NumpyUnpickler.find_class
        observed: list[object] = []

        class WatchingUnpickler(NumpyUnpickler):
            def load(self):
                observed.append(WatchingUnpickler.find_class)
                return super().load()

        monkeypatch.setattr("joblib.numpy_pickle.NumpyUnpickler", WatchingUnpickler)
        artifact = tmp_path / "data.joblib"
        joblib.dump(np.arange(5), artifact)

        np.testing.assert_array_equal(safe_joblib_load(artifact), np.arange(5))
        assert observed == [original_find_class]

    def test_restricted_subclass_delegates_to_current_base_find_class(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The private subclass composes with the installed base implementation."""
        import joblib
        import numpy as np
        from joblib.numpy_pickle import NumpyUnpickler

        set_project_root(tmp_path)
        real_find_class = NumpyUnpickler.find_class

        def _sentinel_find_class(self: object, module: str, name: str) -> object:
            return real_find_class(self, module, name)

        monkeypatch.setattr(NumpyUnpickler, "find_class", _sentinel_find_class)

        f = tmp_path / "data.joblib"
        joblib.dump(np.arange(5), str(f))

        for _ in range(2):
            assert NumpyUnpickler.find_class is _sentinel_find_class
            result = safe_joblib_load(str(f))
            np.testing.assert_array_equal(result, np.arange(5))
            assert NumpyUnpickler.find_class is _sentinel_find_class, (
                "safe_joblib_load mutated the installed base find_class"
            )

    def test_incompatible_joblib_private_api_fails_loudly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """An installed but incompatible joblib must not masquerade as absent."""
        import joblib

        set_project_root(tmp_path)
        artifact = tmp_path / "data.joblib"
        joblib.dump({"value": 1}, artifact)
        monkeypatch.delattr(joblib.numpy_pickle, "_validate_fileobject_and_memmap")

        with pytest.raises(RuntimeError, match="joblib is incompatible"):
            safe_joblib_load(artifact)

    def test_incompatible_joblib_constructor_fails_loudly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Private constructor signature drift must retain Haute's typed boundary."""
        import joblib

        set_project_root(tmp_path)
        artifact = tmp_path / "data.joblib"
        joblib.dump({"value": 1}, artifact)

        class IncompatibleNumpyUnpickler:
            def __init__(
                self,
                filename: str,
                file_handle: object,
                mmap_mode: str | None = None,
            ) -> None:
                del filename, file_handle, mmap_mode

        monkeypatch.setattr(
            joblib.numpy_pickle,
            "NumpyUnpickler",
            IncompatibleNumpyUnpickler,
        )

        with pytest.raises(RuntimeError, match="joblib is incompatible"):
            safe_joblib_load(artifact)


class TestBoundedValidationCache:
    """F060 fix: ``_validation_cache`` is a bounded ``LRUCache``.

    A long-lived server previews/traces many distinct code fragments.  The
    cache must self-cap at ``max_size`` and evict the least-recently-used
    entries instead of retaining one entry per distinct fragment forever.
    """

    def test_cache_is_bounded_lru_cache(self):
        """The validation cache is an LRUCache with a finite max_size."""
        import haute._sandbox
        from haute._lru_cache import LRUCache

        cache = haute._sandbox._validation_cache
        assert isinstance(cache, LRUCache), (
            f"Expected a bounded LRUCache, got {type(cache).__name__} — "
            "an unbounded dict leaks memory in long-lived servers."
        )
        assert cache._max_size == haute._sandbox._VALIDATION_CACHE_MAX_SIZE

    def test_cache_caps_at_max_size_under_distinct_load(self):
        """Feeding many more distinct fragments than max_size must not grow
        the cache past its bound — the LRU evicts old entries."""
        import haute._sandbox

        cache = haute._sandbox._validation_cache
        cache.clear()
        max_size = haute._sandbox._VALIDATION_CACHE_MAX_SIZE

        # Feed 2x the cap of distinct safe fragments.
        for i in range(max_size * 2):
            validate_user_code(f"bounded_cache_probe_{i} = {i}")

        assert len(cache) <= max_size, (
            f"Cache grew to {len(cache)} entries, exceeding the {max_size} "
            "cap — the eviction policy is not bounding growth."
        )

    def test_evicted_entry_is_revalidated_not_silently_trusted(self):
        """An entry pushed out of the cache is re-validated on next call, so
        eviction never turns previously-unsafe code into a silent pass."""
        import haute._sandbox

        cache = haute._sandbox._validation_cache
        cache.clear()
        # Prime one safe fragment, then evict it by flooding past the cap.
        validate_user_code("evicted_probe = 1")
        max_size = haute._sandbox._VALIDATION_CACHE_MAX_SIZE
        for i in range(max_size * 2):
            validate_user_code(f"flood_{i} = {i}")
        assert "evicted_probe = 1" not in cache
        # Re-validating still succeeds (it is genuinely safe) and re-caches.
        validate_user_code("evicted_probe = 1")
        assert "evicted_probe = 1" in cache


# ===================================================================
# Edge-case tests for sandbox module
# ===================================================================


class TestValidateProjectPathEdgeCases:
    def test_relative_path_resolved_to_absolute(self, tmp_path: Path):
        set_project_root(tmp_path)
        subdir = tmp_path / "data"
        subdir.mkdir()
        f = subdir / "file.csv"
        f.touch()
        result = validate_project_path(str(subdir / ".." / "data" / "file.csv"))
        assert result.is_absolute()
        assert result == f

    def test_empty_string_resolves_to_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        set_project_root(tmp_path)
        result = validate_project_path("")
        assert result == tmp_path

    def test_empty_string_outside_root_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.chdir(other)
        set_project_root(tmp_path / "restricted")
        with pytest.raises(ValueError, match="outside.*project root"):
            validate_project_path("")

    def test_nested_subdirectory_inside_root(self, tmp_path: Path):
        set_project_root(tmp_path)
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        f = nested / "deep.txt"
        f.touch()
        assert validate_project_path(str(f)) == f

    def test_symlink_traversal_blocked(self, tmp_path: Path):
        set_project_root(tmp_path / "project")
        (tmp_path / "project").mkdir()
        outside = tmp_path / "secret.txt"
        outside.touch()
        link = tmp_path / "project" / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks not supported")
        with pytest.raises(ValueError, match="outside.*project root"):
            validate_project_path(str(link))


class TestSafeGlobalsBuiltinCoverage:
    @pytest.mark.parametrize(
        "name",
        [
            "len",
            "range",
            "min",
            "max",
            "sum",
            "int",
            "float",
            "str",
            "bool",
            "list",
            "dict",
            "tuple",
            "set",
            "sorted",
            "reversed",
            "enumerate",
            "zip",
            "map",
            "filter",
            "any",
            "all",
            "abs",
            "round",
            "isinstance",
            "issubclass",
            "print",
            "repr",
        ],
    )
    def test_common_builtin_available(self, name: str):
        ns = safe_globals()
        builtins_ns = ns.get("__builtins__", ns)
        assert name in builtins_ns, f"{name} should be available in safe builtins"

    def test_extra_kwargs_injected(self):
        import polars as pl

        ns = safe_globals(pl=pl, my_value=42)
        assert ns["pl"] is pl
        assert ns["my_value"] == 42

    def test_extra_kwargs_override_nothing_in_builtins(self):
        ns = safe_globals(custom_fn=lambda x: x + 1)
        local = {}
        exec("result = custom_fn(10)", ns, local)
        assert local["result"] == 11

    def test_polars_operations_in_namespace(self):
        import polars as pl

        ns = safe_globals(pl=pl)
        local = {}
        exec(
            'df = pl.DataFrame({"a": [1, 2, 3]})\nresult = df.select(pl.col("a") * 2)\n',
            ns,
            local,
        )
        assert local["result"]["a"].to_list() == [2, 4, 6]


class TestValidateUserCodeEdgeCases:
    def test_valid_polars_code_passes(self):
        validate_user_code('df.filter(pl.col("age") > 25)')

    def test_lambda_expression_passes(self):
        validate_user_code("fn = lambda x, y: x + y")

    def test_empty_code_passes(self):
        validate_user_code("")

    def test_whitespace_only_code_passes(self):
        validate_user_code("   \n\n  ")

    def test_syntax_error_raises_unsafe_code_error(self):
        with pytest.raises(UnsafeCodeError, match="syntax errors"):
            validate_user_code("def f(:")

    def test_explicit_assignment_passes(self):
        validate_user_code('df = df.select("name", "age")')

    def test_caching_returns_consistent_results(self):
        import haute._sandbox

        code = "cached_test_unique_12345 = 1"
        cache = haute._sandbox._validation_cache
        cache.evict_where(lambda k: k == code)
        validate_user_code(code)
        assert code in cache

    def test_caching_second_call_uses_cache(self):
        import time

        import haute._sandbox

        code = "cached_perf_test_unique_67890 = 1"
        cache = haute._sandbox._validation_cache
        cache.evict_where(lambda k: k == code)

        validate_user_code(code)
        assert code in cache

        start = time.perf_counter()
        for _ in range(1000):
            validate_user_code(code)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, "1000 cached validations should be near-instant"

    def test_unsafe_code_not_cached(self):
        import haute._sandbox

        code = "value = input()"
        with pytest.raises(UnsafeCodeError):
            validate_user_code(code)
        assert code not in haute._sandbox._validation_cache


class TestSafeUnpickleEdgeCases:
    def test_safe_dict_unpickles(self, tmp_path: Path):
        set_project_root(tmp_path)
        f = tmp_path / "data.pkl"
        f.write_bytes(pickle.dumps({"a": 1, "b": [2, 3]}))
        assert safe_unpickle(str(f)) == {"a": 1, "b": [2, 3]}

    def test_safe_nested_structures(self, tmp_path: Path):
        set_project_root(tmp_path)
        data = {"list": [1, 2.0, "three"], "tuple": (4, 5), "set": frozenset({6})}
        f = tmp_path / "nested.pkl"
        f.write_bytes(pickle.dumps(data))
        result = safe_unpickle(str(f))
        assert result["list"] == [1, 2.0, "three"]
        assert result["tuple"] == (4, 5)
        assert result["set"] == frozenset({6})

    def test_os_system_payload_blocked(self, tmp_path: Path):
        set_project_root(tmp_path)
        f = tmp_path / "evil.pkl"

        class _Evil:
            def __reduce__(self):
                import os

                return (os.system, ("echo pwned",))

        f.write_bytes(pickle.dumps(_Evil()))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_unpickle(str(f))

    def test_path_outside_project_root_blocked(self, tmp_path: Path):
        set_project_root(tmp_path / "project")
        f = tmp_path / "outside.pkl"
        f.write_bytes(pickle.dumps(42))
        with pytest.raises(ValueError, match="outside.*project root"):
            safe_unpickle(str(f))

    def test_subprocess_payload_blocked(self, tmp_path: Path):
        set_project_root(tmp_path)
        f = tmp_path / "evil2.pkl"

        class _Evil:
            def __reduce__(self):
                import subprocess

                return (subprocess.check_output, (["echo", "pwned"],))

        f.write_bytes(pickle.dumps(_Evil()))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_unpickle(str(f))


# ===================================================================
# Critical edge-case gap-closing tests
# ===================================================================


class TestPickleBombDeeplyNested:
    def test_deeply_nested_pickle_no_stack_overflow(self, tmp_path: Path):
        import sys

        obj: object = "leaf"
        for _ in range(1500):
            obj = [obj]
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(5000)
        try:
            payload = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        finally:
            sys.setrecursionlimit(old_limit)
        set_project_root(tmp_path)
        f = tmp_path / "nested_bomb.pkl"
        f.write_bytes(payload)
        try:
            result = safe_unpickle(str(f))
            depth = 0
            cur = result
            while isinstance(cur, list) and len(cur) == 1:
                depth += 1
                cur = cur[0]
            assert depth == 1500
            assert cur == "leaf"
        except (RecursionError, pickle.UnpicklingError):
            pass


class TestPickleReduceExploit:
    def test_reduce_os_system_blocked(self, tmp_path: Path):
        import os

        class Exploit:
            def __reduce__(self):
                return (os.system, ("echo pwned",))

        payload = pickle.dumps(Exploit())
        set_project_root(tmp_path)
        f = tmp_path / "reduce_exploit.pkl"
        f.write_bytes(payload)
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_unpickle(str(f))


class TestJoblibConcurrentLoadSafety:
    def test_ten_threads_same_file_no_corruption(self, tmp_path: Path):
        import threading

        import joblib
        import numpy as np

        set_project_root(tmp_path)
        f = tmp_path / "shared.joblib"
        data = {"arr": np.array([1.0, 2.0, 3.0]), "label": "test"}
        joblib.dump(data, str(f))

        results: list[dict | None] = [None] * 10
        errors: list[Exception] = []

        def load(idx: int) -> None:
            try:
                results[idx] = safe_joblib_load(str(f))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=load, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent loads raised: {errors}"
        for i, r in enumerate(results):
            assert r is not None, f"Thread {i} returned None"
            assert r["label"] == "test"
            np.testing.assert_array_equal(r["arr"], [1.0, 2.0, 3.0])


class TestDeeplyNestedASTValidation:
    def test_deeply_nested_parens_no_crash(self):
        depth = 200
        code = "x = " + "(" * depth + "1" + ")" * depth
        try:
            validate_user_code(code)
        except (RecursionError, MemoryError, UnsafeCodeError):
            # Python 3.13+ raises MemoryError for deep nesting;
            # older versions may raise RecursionError. Either is acceptable.
            pass

    def test_100_nested_parens_valid(self):
        depth = 100
        code = "x = " + "(" * depth + "42" + ")" * depth
        validate_user_code(code)


class TestPreambleCacheEviction:
    def test_cache_does_not_exceed_max(self):
        """Regression guard for the ``functools.lru_cache`` preamble cache.

        Post-refactor the cache is bounded by the stdlib's
        ``maxsize`` parameter (128 by default).  Inserting more than that
        must not cause the cache to grow past the bound.
        """
        from haute.executor import _compile_preamble

        # Snapshot the cache bound from the cache_info surface — the test
        # shouldn't hard-code 128 in case the default is tuned later.
        info_before = _compile_preamble.cache_info()  # type: ignore[attr-defined]
        bound = info_before.maxsize

        # Insert ``bound + 5`` distinct preambles with execution_fingerprint
        # so each is a fresh miss that populates the cache rather than a
        # cache_clear() on every call.
        for i in range(bound + 5):
            preamble = f"PREAMBLE_EVICT_TEST_{i} = {i}\n"
            _compile_preamble(preamble, execution_fingerprint=f"pin-{i}")

        info_after = _compile_preamble.cache_info()  # type: ignore[attr-defined]
        assert info_after.currsize <= bound

        # Clean up so we don't pollute other tests.
        _compile_preamble.cache_clear()  # type: ignore[attr-defined]


class TestValidationCacheDoesNotCacheUnsafe:
    def test_syntax_error_then_fixed_code_passes(self):
        import haute._sandbox

        bad_code = "def _cache_edge_test(:"
        good_code = "def _cache_edge_test(): pass"

        haute._sandbox._validation_cache.evict_where(lambda k: k == bad_code)
        haute._sandbox._validation_cache.evict_where(lambda k: k == good_code)

        with pytest.raises(UnsafeCodeError):
            validate_user_code(bad_code)

        assert bad_code not in haute._sandbox._validation_cache

        validate_user_code(good_code)
        assert good_code in haute._sandbox._validation_cache


# ===================================================================
# F737 / F059 / F290 — exact fully-qualified-symbol unpickle allowlist
# ===================================================================


class TestPickleRCEGadgetGate:
    """The restricted unpickler must reject code-execution gadget *functions*
    that live under an otherwise-trusted package tree, while still allowing the
    model *classes* and vetted scaffolding functions legitimate pickles need.

    Before the fix, a whole-package ``module.startswith("numpy")`` prefix
    admitted every callable under numpy — including RCE gadgets such as
    ``numpy.ctypeslib.load_library`` / ``numpy.testing.*.runstring``.  Because
    pickle *calls* whatever ``find_class`` returns on a REDUCE opcode, returning
    any such function is arbitrary code execution.
    """

    def test_function_under_trusted_package_rejected(self):
        """A bare function resolved from numpy (np.load) is rejected."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("numpy", "load")

    def test_ctypeslib_load_library_gadget_rejected(self):
        """The concrete RCE gadget cited by the finding is rejected."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("numpy.ctypeslib", "load_library")

    def test_class_under_trusted_package_allowed(self):
        """An exact allowlisted class (numpy.ndarray) is allowed."""
        import io

        import numpy as np

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        assert unpickler.find_class("numpy", "ndarray") is np.ndarray

    def test_sklearn_estimator_class_allowed(self):
        """A real sklearn estimator class still resolves (model loading path)."""
        import io

        from sklearn.linear_model._base import LinearRegression

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        resolved = unpickler.find_class("sklearn.linear_model._base", "LinearRegression")
        assert resolved is LinearRegression

    @pytest.mark.parametrize(
        ("module", "name"),
        [
            ("joblib.memory", "Memory"),
            ("sklearn.pipeline", "Pipeline"),
            ("pandas.io.formats.style", "Styler"),
        ],
    )
    def test_unreviewed_classes_under_common_packages_rejected(self, module: str, name: str):
        """Classes are not admitted just because their package is familiar."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class(module, name)

    def test_scaffolding_function_allowed_via_exact_entry(self):
        """The numpy reconstruction helper is admitted by its exact entry."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        resolved = unpickler.find_class("numpy._core.multiarray", "_reconstruct")
        assert callable(resolved)

    def test_end_to_end_trusted_tree_function_gadget_blocked(self, tmp_path: Path):
        """A pickle whose REDUCE callable is a trusted-tree *function* is blocked
        end-to-end, even though the module is under the numpy prefix."""
        import numpy

        set_project_root(tmp_path)

        class _Gadget:
            def __reduce__(self):
                # numpy.load is a function reachable under the numpy tree; the
                # old prefix allowlist would have returned (and pickle called) it.
                return (numpy.load, ("/nonexistent/path",))

        f = tmp_path / "gadget.pkl"
        f.write_bytes(pickle.dumps(_Gadget()))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_unpickle(str(f))

    def test_joblib_trusted_tree_function_gadget_blocked(self, tmp_path: Path):
        """The joblib path applies the same class-vs-function gate."""
        import joblib
        import numpy

        set_project_root(tmp_path)

        class _Gadget:
            def __reduce__(self):
                return (numpy.load, ("/nonexistent/path",))

        f = tmp_path / "gadget.joblib"
        joblib.dump(_Gadget(), str(f))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            safe_joblib_load(str(f))

    def test_true_false_none_omitted_from_exact_allowlist(self):
        """F290: ('builtins','True'/'False'/'None') were dead rows (pickle uses
        opcodes for them) and must not be present in the exact allowlist."""
        from haute._sandbox import _ALLOWED_PICKLE_GLOBALS

        for dead in ("True", "False", "None"):
            assert ("builtins", dead) not in _ALLOWED_PICKLE_GLOBALS

    def test_builtins_scalar_constructors_still_allowed(self):
        """The live builtin scalar/container constructors remain admitted."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        assert unpickler.find_class("builtins", "frozenset") is frozenset
        assert unpickler.find_class("builtins", "int") is int

    def test_builtins_eval_still_blocked(self):
        """builtins.eval is a function and not in the exact allowlist."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("builtins", "eval")


class TestSafeGlobalsIsolation:
    """F289: each safe_globals call returns an isolated builtins dict; mutating
    one exec namespace must not leak into the next or into module state."""

    def test_builtins_not_shared_across_calls(self):
        ns1 = safe_globals()
        ns2 = safe_globals()
        b1 = ns1["__builtins__"]
        b2 = ns2["__builtins__"]
        assert isinstance(b1, dict) and isinstance(b2, dict)
        assert b1 is not b2

    def test_builtins_mutation_does_not_leak(self):
        import haute._sandbox as sandbox

        ns1 = safe_globals()
        ns1["__builtins__"]["_injected_marker"] = 123
        ns2 = safe_globals()
        assert "_injected_marker" not in ns2["__builtins__"]
        # The shared module-global base must remain pristine.
        assert "_injected_marker" not in sandbox._EXEC_BUILTINS


class TestCaseInsensitiveContainment:
    """F740: containment must fold case so a case-variant path on a
    case-insensitive filesystem cannot slip past ``is_relative_to``."""

    def test_case_variant_root_contained_when_fs_case_insensitive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Simulate a case-insensitive filesystem by folding case in normcase;
        a path whose root segment differs only in case must be accepted."""
        import os as _os

        monkeypatch.setattr(_os.path, "normcase", lambda s: s.lower())

        root = tmp_path / "Project"
        root.mkdir()
        set_project_root(root)
        inside = root / "data.csv"
        inside.touch()

        # Same file, but the project segment is upper-cased. With the old
        # case-sensitive is_relative_to this raised ValueError (over-restrictive
        # / bypass surface); with normcase folding it resolves as contained.
        variant = str(inside).replace("Project", "PROJECT")
        result = validate_project_path(variant)
        assert _os.path.normcase(str(result)) == _os.path.normcase(str(inside))

    def test_sibling_prefix_still_rejected(self, tmp_path: Path):
        """A sibling directory sharing a name *prefix* is not contained —
        commonpath honours component boundaries where startswith would not."""
        root = tmp_path / "proj"
        root.mkdir()
        sibling = tmp_path / "proj_evil"
        sibling.mkdir()
        set_project_root(root)
        target = sibling / "secret.csv"
        target.touch()
        with pytest.raises(ValueError, match="outside.*project root"):
            validate_project_path(str(target))


# ---------------------------------------------------------------------------
# Pickle allowlist entries resolve against the real installed packages
# ---------------------------------------------------------------------------

# Top-level module -> distribution name, for every third-party prefix the
# allowlist names. A new prefix must be mapped here or the test fails loudly.
_PICKLE_ALLOWLIST_DISTRIBUTIONS = {
    "catboost": "catboost",
    "joblib": "joblib",
    "interpret": "interpret-core",
    "lightgbm": "lightgbm",
    "numpy": "numpy",
    "pandas": "pandas",
    "polars": "polars",
    "sklearn": "scikit-learn",
    "xgboost": "xgboost",
}
_PICKLE_ALLOWLIST_STDLIB = frozenset({"builtins", "copyreg", "_codecs"})


def _allowlist_entry(module: str, name: str) -> object:
    """Import an allowlist entry, or skip when its distribution is absent.

    The allowlist names other projects' private module paths; a rename there
    is invisible until a model fails to load. Consulting the installed package
    is the only check that means anything, so an entry whose distribution is
    not installed here is reported as unverified rather than passed.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    package = module.partition(".")[0]
    if package not in _PICKLE_ALLOWLIST_STDLIB:
        assert package in _PICKLE_ALLOWLIST_DISTRIBUTIONS, (
            f"map the allowlist prefix {package!r} to its distribution in this test"
        )
        distribution_name = _PICKLE_ALLOWLIST_DISTRIBUTIONS[package]
        try:
            distribution(distribution_name)
        except PackageNotFoundError:
            # The parametrised test id names the entry; the distribution is absent
            # from this environment, so the entry is unverified rather than passed.
            pytest.skip(
                "allowlisted distribution is not installed in this environment, "
                "so the entry is unverified rather than passed"
            )
    return getattr(import_module(module), name)


class TestPickleAllowlistResolves:
    """Every allowlist entry is asked of the real installed package."""

    @pytest.mark.parametrize(("module", "name"), sorted(_ALLOWED_PICKLE_CLASSES))
    def test_class_entry_resolves_to_a_class(self, module: str, name: str) -> None:
        resolved = _allowlist_entry(module, name)
        assert isinstance(resolved, type), (
            f"{module}.{name} is allowlisted as a class but resolves to {resolved!r}; "
            "the restricted unpickler will now block models that reference it"
        )

    @pytest.mark.parametrize(("module", "name"), sorted(_ALLOWED_PICKLE_GLOBALS))
    def test_global_entry_resolves_to_a_callable(self, module: str, name: str) -> None:
        resolved = _allowlist_entry(module, name)
        assert callable(resolved), f"{module}.{name} is allowlisted but is not callable"

    def test_missing_allowlisted_package_is_a_blocked_pickle_error(self) -> None:
        """An allowlisted entry whose package is absent is reported as such."""

        def resolver(module: str, name: str) -> object:
            raise ModuleNotFoundError(f"No module named {module!r}", name="xgboost")

        with pytest.raises(pickle.UnpicklingError, match="`xgboost` is not installed"):
            _resolve_allowed_global(resolver, "xgboost.sklearn", "XGBModel")

    def test_broken_install_deeper_in_the_tree_propagates(self) -> None:
        """Only the entry's own top-level package counts as absent."""

        def resolver(module: str, name: str) -> object:
            raise ModuleNotFoundError("No module named 'scipy.sparse'", name="scipy.sparse")

        with pytest.raises(ModuleNotFoundError, match="scipy.sparse"):
            _resolve_allowed_global(resolver, "xgboost.sklearn", "XGBModel")

    def test_real_unpickler_reports_absent_xgboost(self) -> None:
        """The real ``find_class`` path in an environment without xgboost."""
        import importlib.util
        import io

        from haute._sandbox import _RestrictedUnpickler

        if importlib.util.find_spec("xgboost") is not None:
            pytest.skip("xgboost is installed here; the absent-package path is not reachable")
        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="`xgboost` is not installed"):
            unpickler.find_class("xgboost.sklearn", "XGBModel")
