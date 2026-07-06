"""Tests for the security sandbox (_sandbox.py)."""

from __future__ import annotations

import pickle
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

from haute._sandbox import (
    UnsafeCodeError,
    safe_globals,
    safe_joblib_load,
    safe_unpickle,
    set_project_root,
    validate_project_path,
    validate_user_code,
)


class TestSafeGlobals:
    """Verify restricted builtins block dangerous operations."""

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

    def test_import_blocked(self):
        """__import__ should be blocked."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("__import__('os')", ns, {})

    def test_open_blocked(self):
        """open() should be blocked."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("open('/etc/passwd')", ns, {})

    def test_eval_blocked(self):
        """eval() should be blocked."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("eval('1+1')", ns, {})

    def test_exec_blocked(self):
        """Nested exec() should be blocked."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("exec('x=1')", ns, {})

    def test_compile_blocked(self):
        """compile() should be blocked."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("compile('x=1', '<str>', 'exec')", ns, {})

    def test_breakpoint_blocked(self):
        """breakpoint() should be blocked."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("breakpoint()", ns, {})

    def test_all_dangerous_builtins_blocked(self):
        """All dangerous builtins must be absent from safe namespace."""
        ns = safe_globals()
        blocked = {
            "__import__",
            "breakpoint",
            "compile",
            "eval",
            "exec",
            "globals",
            "locals",
            "open",
            "input",
            "memoryview",
        }
        builtins_ns = ns.get("__builtins__", ns)
        if isinstance(builtins_ns, dict):
            present = blocked & set(builtins_ns.keys())
        else:
            present = {b for b in blocked if hasattr(builtins_ns, b)}
        assert present == set(), f"Dangerous builtins present: {present}"


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


class TestSafeJoblibLoad:
    """Verify joblib loading goes through the restricted unpickler."""

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
    """Verify AST-level code validation blocks sandbox escape vectors."""

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

    def test_safe_dunder_passes(self):
        """Dunders not in the block list are allowed (e.g. __name__)."""
        # __name__ is not in _BLOCKED_ATTRS — it's harmless
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

    def test_assignment_with_dangerous_pattern_blocked(self):
        """Assignment that contains a dangerous pattern should still be blocked."""
        with pytest.raises(UnsafeCodeError, match="__class__"):
            validate_user_code("df = df.filter(x.__class__)")

    def test_empty_code_passes(self):
        """Empty string should pass."""
        validate_user_code("")

    # ------- Dunder access blocked -------

    def test_subclasses_blocked(self):
        """__subclasses__() is the classic sandbox escape."""
        with pytest.raises(UnsafeCodeError, match="__subclasses__"):
            validate_user_code("().__class__.__bases__[0].__subclasses__()")

    def test_class_blocked(self):
        """__class__ access is blocked."""
        with pytest.raises(UnsafeCodeError, match="__class__"):
            validate_user_code('"".__class__')

    def test_bases_blocked(self):
        """__bases__ access is blocked."""
        with pytest.raises(UnsafeCodeError, match="__bases__"):
            validate_user_code("object.__bases__")

    def test_mro_blocked(self):
        """__mro__ access is blocked."""
        with pytest.raises(UnsafeCodeError, match="__mro__"):
            validate_user_code("object.__mro__")

    def test_globals_attr_blocked(self):
        """__globals__ access on function objects is blocked."""
        with pytest.raises(UnsafeCodeError, match="__globals__"):
            validate_user_code("func.__globals__")

    def test_code_attr_blocked(self):
        """__code__ access on function objects is blocked."""
        with pytest.raises(UnsafeCodeError, match="__code__"):
            validate_user_code("func.__code__")

    def test_dict_blocked(self):
        """__dict__ access is blocked."""
        with pytest.raises(UnsafeCodeError, match="__dict__"):
            validate_user_code("obj.__dict__")

    def test_builtins_attr_blocked(self):
        """__builtins__ access is blocked."""
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code("x.__builtins__")

    def test_import_attr_blocked(self):
        """__import__ attribute access is blocked."""
        with pytest.raises(UnsafeCodeError, match="__import__"):
            validate_user_code("x.__import__")

    def test_reduce_blocked(self):
        """__reduce__ access is blocked (pickle exploit vector)."""
        with pytest.raises(UnsafeCodeError, match="__reduce__"):
            validate_user_code("obj.__reduce__()")

    # ------- Reflection calls blocked -------

    def test_getattr_blocked(self):
        """getattr() is blocked."""
        with pytest.raises(UnsafeCodeError, match="getattr"):
            validate_user_code('getattr(obj, "__class__")')

    def test_setattr_blocked(self):
        """setattr() is blocked."""
        with pytest.raises(UnsafeCodeError, match="setattr"):
            validate_user_code('setattr(obj, "x", 1)')

    def test_delattr_blocked(self):
        """delattr() is blocked."""
        with pytest.raises(UnsafeCodeError, match="delattr"):
            validate_user_code('delattr(obj, "x")')

    def test_type_blocked(self):
        """type() is blocked (can create classes dynamically)."""
        with pytest.raises(UnsafeCodeError, match="type"):
            validate_user_code('type("Evil", (object,), {})')

    def test_vars_blocked(self):
        """vars() is blocked."""
        with pytest.raises(UnsafeCodeError, match="vars"):
            validate_user_code("vars(obj)")

    def test_dir_blocked(self):
        """dir() is blocked."""
        with pytest.raises(UnsafeCodeError, match="dir"):
            validate_user_code("dir(obj)")

    def test_hasattr_blocked(self):
        """hasattr() is blocked."""
        with pytest.raises(UnsafeCodeError, match="hasattr"):
            validate_user_code('hasattr(obj, "__class__")')

    def test_eval_call_blocked(self):
        """eval() call is blocked at AST level."""
        with pytest.raises(UnsafeCodeError, match="eval"):
            validate_user_code('eval("1+1")')

    def test_exec_call_blocked(self):
        """exec() call is blocked at AST level."""
        with pytest.raises(UnsafeCodeError, match="exec"):
            validate_user_code('exec("x=1")')

    def test_open_call_blocked(self):
        """open() call is blocked at AST level."""
        with pytest.raises(UnsafeCodeError, match="open"):
            validate_user_code('open("/etc/passwd")')

    def test_compile_call_blocked(self):
        """compile() call is blocked at AST level."""
        with pytest.raises(UnsafeCodeError, match="compile"):
            validate_user_code('compile("x=1", "<>", "exec")')

    def test_super_blocked(self):
        """super() is blocked."""
        with pytest.raises(UnsafeCodeError, match="super"):
            validate_user_code("super().__init__()")

    # ------- Imports blocked -------

    def test_import_blocked(self):
        """import statements are blocked."""
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code("import os")

    def test_from_import_blocked(self):
        """from...import statements are blocked."""
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code("from os import system")

    # ------- Class / async / scope escaping blocked -------

    def test_class_def_blocked(self):
        """class definitions are blocked."""
        with pytest.raises(UnsafeCodeError, match="class"):
            validate_user_code("class Evil:\n    pass")

    def test_async_def_blocked(self):
        """async function definitions are blocked."""
        with pytest.raises(UnsafeCodeError, match="async"):
            validate_user_code("async def exploit():\n    pass")

    def test_global_blocked(self):
        """global statements are blocked."""
        with pytest.raises(UnsafeCodeError, match="global"):
            validate_user_code("global x")

    def test_nonlocal_blocked(self):
        """nonlocal statements are blocked."""
        with pytest.raises(UnsafeCodeError, match="nonlocal"):
            validate_user_code("def f():\n    nonlocal x")

    # ------- Known sandbox escape patterns -------

    def test_classic_subclasses_escape(self):
        """The classic CPython sandbox escape via __subclasses__ is blocked."""
        code = (
            "[c for c in ().__class__.__bases__[0].__subclasses__() "
            "if c.__name__ == 'catch_warnings'][0]()._module.__builtins__"
        )
        with pytest.raises(UnsafeCodeError):
            validate_user_code(code)

    def test_getattr_based_escape(self):
        """getattr-based escape route is blocked."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code('getattr(getattr("", "__class__"), "__bases__")')

    def test_type_metaclass_escape(self):
        """type() dynamic class creation is blocked."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code('type("X", (object,), {"__init__": lambda s: None})')


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
        """FIX: The joblib find_class now properly checks 2-element tuple
        constraints, so builtins.eval is blocked (same as the pickle path).
        """
        set_project_root(tmp_path)
        from haute._sandbox import _pickle_global_is_allowed

        # The joblib path now correctly blocks builtins.eval
        assert _pickle_global_is_allowed("builtins", "eval") is False, (
            "builtins.eval should NOT be allowed by joblib find_class — "
            "the 2-element tuple constraint should reject it"
        )

    def test_builtins_exec_blocked_by_both_pickle_and_joblib(self, tmp_path: Path):
        """FIX: Both pickle and joblib paths now block builtins.exec."""
        import io

        set_project_root(tmp_path)
        from haute._sandbox import _pickle_global_is_allowed, _RestrictedUnpickler

        # Pickle path blocks it
        buf = io.BytesIO(b"")
        unpickler = _RestrictedUnpickler(buf)
        with pytest.raises(pickle.UnpicklingError, match="not in.*allowlist"):
            unpickler.find_class("builtins", "exec")

        # Joblib path now also blocks it (properly checks 2-element tuples)
        assert _pickle_global_is_allowed("builtins", "exec") is False, (
            "builtins.exec should NOT be allowed by joblib find_class"
        )


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

    def test_allowlist_allows_legitimate_submodule(self):
        """``numpy.core`` remains allowed as a real submodule of ``numpy``."""
        from haute._sandbox import _pickle_global_is_allowed

        assert _pickle_global_is_allowed("numpy.core", "ndarray")


class TestJoblibMonkeyPatchThreadSafety:
    """Gap 2: safe_joblib_load replaces NumpyUnpickler.find_class at the
    class level.  Two concurrent calls can race.

    Production failure: Thread A starts safe_joblib_load, patches find_class.
    Thread B starts safe_joblib_load, patches find_class again.  Thread A
    finishes and restores the *wrong* original (Thread B's patched version).
    Thread B finishes and restores the true original, but Thread A's restore
    was already corrupted.  Or worse — during the race window, one thread
    runs with no restriction at all.
    """

    def test_concurrent_safe_joblib_load_no_crash(self, tmp_path: Path):
        """Two threads loading safe joblib files concurrently should not
        corrupt find_class or crash."""
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
        """After concurrent safe_joblib_load calls, the original find_class
        must be fully restored on NumpyUnpickler.

        F208 fix: the genuine ``find_class`` is captured *inside* the joblib
        lock, so a concurrent loader can never have its restricted shim
        mistaken for the original and leaked as the permanent restore target.
        """
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

        # After all threads finish, find_class must be the original
        assert NumpyUnpickler.find_class is original_find_class, (
            "find_class was not properly restored after concurrent loads — "
            "the monkey-patching is not thread-safe"
        )


class TestLambdaAllowedInSandbox:
    """Gap 3: The AST validator has no visit_Lambda, so lambda definitions
    pass through.

    Production failure: A user writes ``fn = lambda: __import__('os')``
    which passes AST validation.  The __import__ call in the lambda body
    IS blocked by visit_Call, but the lambda itself is unrestricted —
    meaning users can create arbitrary callable objects in the sandbox.
    """

    def test_lambda_passes_ast_validation(self):
        """Lambda expressions are not blocked by the AST validator."""
        # This should NOT raise — documenting that lambdas are allowed
        validate_user_code("fn = lambda x: x * 2")

    def test_lambda_executes_in_sandbox(self):
        """Lambda can be defined and called inside safe_globals."""
        ns = safe_globals()
        local = {}
        exec("fn = lambda x, y: x + y", ns, local)
        assert local["fn"](3, 4) == 7

    def test_lambda_with_blocked_body_still_caught(self):
        """A lambda containing a blocked call is still caught by visit_Call."""
        with pytest.raises(UnsafeCodeError, match="eval"):
            validate_user_code("fn = lambda: eval('1+1')")

    def test_nested_lambda_passes(self):
        """Nested lambdas pass AST validation — no visit_Lambda exists."""
        validate_user_code("fn = lambda f: lambda x: f(x)")


class TestAllowImportsPrivilegeEscalation:
    """Gap 4: allow_imports=True restores __import__ in the namespace,
    letting preamble code import os, subprocess, etc.

    Production failure: A malicious preamble uses ``import os; os.system(...)``
    and the allow_imports=True path permits it.  The AST validator with
    allow_imports=True skips the import check entirely, and safe_globals
    restores __import__ to the real builtins.__import__.
    """

    def test_allow_imports_permits_import_os_in_validation(self):
        """allow_imports=True skips import blocking in the AST validator."""
        # This should pass — that's the intended behavior, but it's risky
        validate_user_code("import os", allow_imports=True)

    def test_allow_imports_permits_import_subprocess(self):
        """allow_imports=True also allows subprocess — no module filtering."""
        validate_user_code("import subprocess", allow_imports=True)

    def test_allow_imports_restores_real_import(self):
        """safe_globals(allow_imports=True) restores the real __import__."""
        import builtins as _builtins

        ns = safe_globals(allow_imports=True)
        builtins_ns = ns.get("__builtins__", {})
        assert builtins_ns.get("__import__") is _builtins.__import__, (
            "allow_imports=True should restore the real __import__"
        )

    def test_os_system_callable_with_allow_imports(self):
        """With allow_imports=True, import os succeeds and os is accessible.

        This documents that preamble code has full import privileges, which
        is a privilege escalation vector if preamble content is attacker-
        controlled.
        """
        ns = safe_globals(allow_imports=True)
        local = {}
        exec("import os; os_name = os.name", ns, local)
        assert local["os_name"] in ("posix", "nt", "java")

    def test_default_path_blocks_imports(self):
        """Confirm the default (allow_imports=False) blocks imports."""
        ns = safe_globals()
        with pytest.raises(NameError):
            exec("__import__('os')", ns, {})


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
        assert ("evicted_probe = 1", False) not in cache
        # Re-validating still succeeds (it is genuinely safe) and re-caches.
        validate_user_code("evicted_probe = 1")
        assert ("evicted_probe = 1", False) in cache


class TestNonBlockedDunders:
    """Gap 8: Several potentially dangerous dunders are NOT in _BLOCKED_ATTRS:
    __init__, __closure__, __qualname__, __annotations__.

    Production failure: An attacker accesses ``func.__closure__`` to leak
    cell variables from closures, or uses ``__annotations__`` to probe
    type hints and discover internal APIs.
    """

    def test_init_not_blocked(self):
        """__init__ is not in _BLOCKED_ATTRS — accessible in sandboxed code."""
        # Should NOT raise — __init__ is not blocked
        validate_user_code("obj.__init__()")

    def test_init_callable_in_sandbox(self):
        """__init__ can be called on objects inside the sandbox."""
        ns = safe_globals()
        local = {}
        exec("x = [1, 2, 3]; x.__init__([4, 5])", ns, local)
        # list.__init__ reinitializes the list
        assert local["x"] == [4, 5]

    def test_closure_is_blocked(self):
        """__closure__ is in _BLOCKED_ATTRS — prevents leaking closure vars."""
        with pytest.raises(UnsafeCodeError, match="__closure__"):
            validate_user_code("fn.__closure__")

    def test_qualname_not_blocked(self):
        """__qualname__ is not in _BLOCKED_ATTRS."""
        validate_user_code("fn.__qualname__")

    def test_annotations_not_blocked(self):
        """__annotations__ is not in _BLOCKED_ATTRS."""
        validate_user_code("fn.__annotations__")

    def test_closure_leaks_values_in_sandbox(self):
        """__closure__ can be used to extract values from closure cells."""
        ns = safe_globals()
        local = {}
        code = (
            "def make():\n"
            "    secret = 42\n"
            "    def inner(): return secret\n"
            "    return inner\n"
            "fn = make()\n"
            "leaked = fn.__closure__[0].cell_contents"
        )
        exec(code, ns, local)
        assert local["leaked"] == 42, "__closure__ allows extracting values from closure cells"

    def test_doc_not_blocked(self):
        """__doc__ is not in _BLOCKED_ATTRS — generally harmless but
        documents the allowlist approach."""
        validate_user_code('x = "".__doc__')

    def test_name_not_blocked(self):
        """__name__ is not in _BLOCKED_ATTRS."""
        validate_user_code("fn.__name__")


# ===================================================================
# Adversarial sandbox-escape regression tests
# ===================================================================
#
# Each test attempts a known CPython sandbox-escape technique and
# verifies the sandbox BLOCKS it.  If any test fails, we have a
# security regression.


class TestTypeBypass:
    """Exploit #1: Dynamic class creation via type().

    type('X', (object,), {'__init__': lambda self: None}) creates a new
    class at runtime, which could be used to build objects with custom
    __reduce__, __getattr__, etc.  The sandbox must block type() calls.
    """

    def test_type_three_arg_blocked_ast(self):
        """type() with 3 args (metaclass use) is blocked at AST level."""
        with pytest.raises(UnsafeCodeError, match="type"):
            validate_user_code("Evil = type('Evil', (object,), {'__init__': lambda self: None})")

    def test_type_one_arg_blocked_ast(self):
        """type(x) -- even the 1-arg introspection form is blocked."""
        with pytest.raises(UnsafeCodeError, match="type"):
            validate_user_code("t = type(42)")

    def test_type_not_in_safe_builtins(self):
        """FIX: type is now in _BLOCKED_BUILTINS, so it is removed from
        the runtime builtins (defence in depth alongside AST blocking)."""
        ns = safe_globals()
        builtins_ns = ns.get("__builtins__", ns)
        if isinstance(builtins_ns, dict):
            has_type = "type" in builtins_ns
        else:
            has_type = "type" in dir(builtins_ns)
        assert not has_type, (
            "type should NOT be in runtime builtins -- it is now in _BLOCKED_BUILTINS"
        )

    def test_type_via_alias_blocked_at_runtime(self):
        """FIX: type is now in _BLOCKED_BUILTINS, so aliasing it at runtime
        raises NameError even though 't = type' passes AST validation.
        """
        code = "t = type\nEvil = t('X', (object,), {})"
        validate_user_code(code)  # Still passes AST (bare name, not a call)

        # At runtime, type is now blocked via _BLOCKED_BUILTINS
        ns = safe_globals()
        local: dict[str, object] = {}
        with pytest.raises((NameError, RuntimeError)):
            exec(code, ns, local)


class TestSubclassWalking:
    """Exploit #2: Classic CPython escape via subclass walking.

    ().__class__.__bases__[0].__subclasses__() traverses the type
    hierarchy to find dangerous classes like os._wrap_close.  Each
    dunder access in the chain should be blocked.
    """

    def test_full_chain_blocked(self):
        """The complete subclass-walking chain is blocked."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code("().__class__.__bases__[0].__subclasses__()")

    def test_class_step_blocked(self):
        """First step: ().__class__ is blocked."""
        with pytest.raises(UnsafeCodeError, match="__class__"):
            validate_user_code("x = ().__class__")

    def test_bases_step_blocked(self):
        """Second step: .__bases__ is blocked."""
        with pytest.raises(UnsafeCodeError, match="__bases__"):
            validate_user_code("x = object.__bases__")

    def test_subclasses_step_blocked(self):
        """Third step: .__subclasses__() is blocked."""
        with pytest.raises(UnsafeCodeError, match="__subclasses__"):
            validate_user_code("x = object.__subclasses__()")

    def test_mro_step_blocked(self):
        """Alternative chain via __mro__ is also blocked."""
        with pytest.raises(UnsafeCodeError, match="__mro__"):
            validate_user_code("x = int.__mro__")

    def test_string_subclass_walk_blocked(self):
        """Using a string literal as the starting point."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code('"".__class__.__bases__[0].__subclasses__()')

    def test_int_subclass_walk_blocked(self):
        """Using an int literal as the starting point."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code("(1).__class__.__bases__[0].__subclasses__()")


class TestFormatStringExploitation:
    """Exploit #3: f-strings accessing dunder attributes.

    f"{obj.__class__}" uses attribute access inside the format
    expression.  The AST validator must inspect f-string contents.
    """

    def test_fstring_class_access_blocked(self):
        """f-string accessing __class__ is blocked."""
        with pytest.raises(UnsafeCodeError, match="__class__"):
            validate_user_code('x = f"{obj.__class__}"')

    def test_fstring_bases_access_blocked(self):
        """f-string accessing __bases__ is blocked."""
        with pytest.raises(UnsafeCodeError, match="__bases__"):
            validate_user_code('x = f"{obj.__bases__}"')

    def test_fstring_globals_access_blocked(self):
        """f-string accessing __globals__ is blocked."""
        with pytest.raises(UnsafeCodeError, match="__globals__"):
            validate_user_code('x = f"{fn.__globals__}"')

    def test_fstring_nested_dunder_blocked(self):
        """Nested dunder access inside f-string is blocked."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code('x = f"{().__class__.__bases__}"')

    def test_fstring_with_getattr_blocked(self):
        """f-string containing getattr() call is blocked."""
        with pytest.raises(UnsafeCodeError, match="getattr"):
            validate_user_code("x = f\"{getattr(obj, 'secret')}\"")

    def test_safe_fstring_passes(self):
        """Normal f-strings without dunders should pass."""
        validate_user_code('x = f"hello {name}"')


class TestExceptionTracebackExploit:
    """Exploit #4: Accessing globals via exception traceback.

    try: 1/0
    except Exception as e: e.__traceback__.tb_frame.f_globals

    This requires accessing dunder attributes on the exception/traceback.
    """

    def test_traceback_via_dunder_blocked(self):
        """FIX: __traceback__ is now in _BLOCKED_FRAME_ATTRS, so accessing
        it is blocked at the AST level."""
        code = "try:\n    1/0\nexcept Exception as e:\n    tb = e.__traceback__"
        with pytest.raises(UnsafeCodeError, match="__traceback__"):
            validate_user_code(code)

    def test_traceback_frame_globals_chain_blocked(self):
        """FIX: The full traceback -> frame -> globals chain is now blocked
        at AST level because __traceback__, tb_frame, and f_globals are
        all in _BLOCKED_FRAME_ATTRS."""
        code = (
            "try:\n"
            "    1/0\n"
            "except Exception as e:\n"
            "    tb = e.__traceback__\n"
            "    frame = tb.tb_frame\n"
            "    leaked = frame.f_globals\n"
        )
        with pytest.raises(UnsafeCodeError):
            validate_user_code(code)


class TestGeneratorFrameAccess:
    """Exploit #5: Accessing builtins via generator frame.

    (x for x in []).gi_frame.f_builtins

    gi_frame, gi_code are not dunder attributes, so the AST validator's
    dunder check does not apply.  However, the runtime sandbox should
    limit what f_builtins contains.
    """

    def test_generator_gi_frame_blocked_ast(self):
        """FIX: gi_frame is now in _BLOCKED_FRAME_ATTRS, so AST blocks it."""
        with pytest.raises(UnsafeCodeError, match="gi_frame"):
            validate_user_code("g = (x for x in [1])\nf = g.gi_frame")

    def test_generator_frame_builtins_blocked_ast(self):
        """FIX: gi_frame and f_builtins are both in _BLOCKED_FRAME_ATTRS,
        so the full chain is blocked at AST level."""
        code = "g = (x for x in [1])\nbuiltins_dict = g.gi_frame.f_builtins\n"
        with pytest.raises(UnsafeCodeError):
            validate_user_code(code)

    def test_generator_gi_code_blocked_ast(self):
        """FIX: gi_code is now in _BLOCKED_FRAME_ATTRS, so AST blocks it."""
        with pytest.raises(UnsafeCodeError, match="gi_code"):
            validate_user_code("g = (x for x in [1])\nc = g.gi_code")


class TestDecoratorFrameCapture:
    """Exploit #6: Using a decorator to capture the execution frame.

    A decorator function could use sys._getframe() or inspect to capture
    the frame.  But import is blocked (no sys/inspect), and the AST
    blocks class defs.  Test that function defs with decorators work
    but cannot import the tools needed to exploit frames.
    """

    def test_decorator_syntax_allowed(self):
        """Function decorators are allowed (they're normal function defs)."""
        code = "def decorator(fn):\n    return fn\n\n@decorator\ndef my_func():\n    return 42\n"
        validate_user_code(code)

    def test_decorator_cannot_import_sys(self):
        """A decorator trying to import sys is blocked."""
        code = "import sys\ndef decorator(fn):\n    frame = sys._getframe()\n    return fn\n"
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code(code)

    def test_decorator_cannot_call_globals(self):
        """A decorator calling globals() is blocked."""
        code = "def decorator(fn):\n    g = globals()\n    return fn\n"
        with pytest.raises(UnsafeCodeError, match="globals"):
            validate_user_code(code)

    def test_decorator_runtime_no_globals(self):
        """At runtime, globals() is not available in the sandbox namespace."""
        ns = safe_globals()
        local: dict[str, object] = {}
        code = (
            "def decorator(fn):\n"
            "    return fn\n"
            "\n"
            "@decorator\n"
            "def my_func():\n"
            "    return 42\n"
            "result = my_func()\n"
        )
        exec(code, ns, local)
        assert local["result"] == 42


class TestListComprehensionScopeLeaking:
    """Exploit #7: List comprehension with dunder access.

    [x for x in ().__class__.__bases__] -- the dunder access inside
    the comprehension must still be caught by the AST validator.
    """

    def test_comprehension_with_class_blocked(self):
        """__class__ inside a list comprehension is blocked."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code("[x for x in ().__class__.__bases__]")

    def test_comprehension_with_subclasses_blocked(self):
        """__subclasses__() inside a list comprehension is blocked."""
        with pytest.raises(UnsafeCodeError, match="__subclasses__"):
            validate_user_code("[c for c in object.__subclasses__()]")

    def test_nested_comprehension_with_dunder_blocked(self):
        """Nested comprehensions with dunders are also blocked."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code("[[a for a in b.__subclasses__()] for b in ().__class__.__bases__]")

    def test_generator_expr_with_dunder_blocked(self):
        """Generator expressions with dunders are also caught."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code("list(x for x in ().__class__.__bases__)")

    def test_dict_comprehension_with_dunder_blocked(self):
        """Dict comprehensions with dunders are also caught."""
        with pytest.raises(UnsafeCodeError):
            validate_user_code("{k: v for k, v in ().__class__.__dict__.items()}")

    def test_safe_comprehension_passes(self):
        """Normal list comprehension without dunders passes."""
        validate_user_code("[x * 2 for x in range(10)]")


class TestLambdaGetattr:
    """Exploit #8: Lambda combined with getattr to bypass name checks.

    (lambda: getattr).__name__ -- the lambda returns getattr as a value
    object.  The AST validator blocks getattr() CALLS but does it block
    getattr as a bare name reference?
    """

    def test_getattr_call_in_lambda_blocked(self):
        """Calling getattr() inside a lambda is blocked."""
        with pytest.raises(UnsafeCodeError, match="getattr"):
            validate_user_code("fn = lambda obj: getattr(obj, '__class__')")

    def test_getattr_as_bare_name_passes_ast(self):
        """Referencing getattr without calling it passes the AST check.

        The AST validator only checks calls (visit_Call), not bare name
        references.  So 'fn = getattr' passes validation.
        """
        # This is a known limitation: the AST only blocks CALLS to
        # getattr, not references.  However, at runtime, getattr IS
        # available in safe_globals (it's not in _BLOCKED_BUILTINS).
        validate_user_code("fn = getattr")

    def test_getattr_blocked_at_runtime(self):
        """FIX: getattr is now in _BLOCKED_BUILTINS, so it is NOT available
        at runtime. Both AST and runtime layers block it."""
        ns = safe_globals()
        builtins_ns = ns.get("__builtins__", ns)
        if isinstance(builtins_ns, dict):
            has_getattr = "getattr" in builtins_ns
        else:
            has_getattr = "getattr" in dir(builtins_ns)
        assert not has_getattr, (
            "getattr should NOT be present in runtime builtins -- it is now in _BLOCKED_BUILTINS"
        )

    def test_lambda_returning_getattr_ref_blocked(self):
        """FIX: A lambda that tries to alias getattr fails at runtime
        because getattr is now in _BLOCKED_BUILTINS."""
        ns = safe_globals()
        local: dict[str, object] = {}
        code = (
            "ga = getattr\n"  # bare reference -- passes AST
            "result = ga([], '__len__')()\n"  # indirect call at runtime
        )
        validate_user_code(code)  # still passes AST
        with pytest.raises(NameError):
            exec(code, ns, local)


class TestPickleWithinExec:
    """Exploit #9: Constructing and unpickling a malicious pickle payload
    inside exec'd code.

    Can user code import pickle and deserialize an arbitrary payload?
    """

    def test_import_pickle_blocked_ast(self):
        """Importing pickle inside sandboxed code is blocked."""
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code("import pickle")

    def test_from_pickle_import_blocked_ast(self):
        """from pickle import ... is also blocked."""
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code("from pickle import loads")

    def test_import_blocked_at_runtime(self):
        """__import__ is not available at runtime in the sandbox."""
        ns = safe_globals()
        with pytest.raises((NameError, TypeError, ImportError)):
            exec("import pickle", ns, {})

    def test_pickle_loads_not_directly_available(self):
        """pickle.loads is not in the sandbox namespace by default."""
        ns = safe_globals()
        assert "pickle" not in ns


class TestImportViaBuiltinsDict:
    """Exploit #10: Accessing __import__ via the __builtins__ dict.

    __builtins__["__import__"]("os") -- if __builtins__ is accessible
    as a namespace key and contains __import__, this bypasses the
    blocked builtins.
    """

    def test_builtins_attr_blocked_ast(self):
        """Accessing __builtins__ as an attribute is blocked at AST level."""
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code('x = obj.__builtins__["__import__"]')

    def test_builtins_as_name_in_namespace(self):
        """__builtins__ IS in the safe namespace (needed for comprehensions),
        but it points to the restricted dict without __import__."""
        ns = safe_globals()
        builtins_ns = ns.get("__builtins__")
        assert builtins_ns is not None, "__builtins__ must be in namespace"
        if isinstance(builtins_ns, dict):
            assert "__import__" not in builtins_ns, "__builtins__ dict must not contain __import__"
            assert "eval" not in builtins_ns
            assert "exec" not in builtins_ns
            assert "open" not in builtins_ns
            assert "compile" not in builtins_ns

    def test_builtins_subscript_import_runtime(self):
        """At runtime, __builtins__['__import__'] should raise KeyError."""
        ns = safe_globals()
        local: dict[str, object] = {}
        # Direct name lookup of __builtins__ works (it's in the namespace),
        # but subscripting for __import__ should fail.
        with pytest.raises(KeyError):
            exec('x = __builtins__["__import__"]', ns, local)

    def test_builtins_get_import_returns_none(self):
        """__builtins__.get('__import__') should return None."""
        ns = safe_globals()
        local: dict[str, object] = {}
        exec('x = __builtins__.get("__import__")', ns, local)
        assert local["x"] is None

    def test_allow_imports_does_expose_import(self):
        """With allow_imports=True, __builtins__ DOES contain __import__."""
        ns = safe_globals(allow_imports=True)
        builtins_ns = ns.get("__builtins__", {})
        if isinstance(builtins_ns, dict):
            assert "__import__" in builtins_ns, "allow_imports=True should restore __import__"


class TestIndirectReflectionEvasion:
    """Additional exploit vectors: indirect ways to call blocked functions
    that evade the AST name check.
    """

    def test_getattr_via_dict_lookup_blocked(self):
        """FIX: __builtins__[...] subscript access is now blocked at AST level
        by the new visit_Subscript check."""
        code = 'ga = __builtins__["getattr"]'
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code(code)

    def test_eval_via_builtins_subscript_blocked(self):
        """FIX: __builtins__['eval'] is now blocked at AST level too."""
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code('e = __builtins__["eval"]')

    def test_exec_via_builtins_subscript_blocked(self):
        """FIX: __builtins__['exec'] is now blocked at AST level too."""
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code('e = __builtins__["exec"]')

    def test_type_via_builtins_subscript_blocked(self):
        """FIX: type is now in _BLOCKED_BUILTINS so it's not in the runtime
        namespace, AND __builtins__[...] subscript is blocked at AST level."""
        # AST blocks __builtins__[...] subscript
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code('t = __builtins__["type"]')

        # Runtime also blocks type — it's not in safe builtins
        ns = safe_globals()
        builtins_ns = ns.get("__builtins__", {})
        if isinstance(builtins_ns, dict):
            assert "type" not in builtins_ns, "type should not be in runtime builtins"


class TestStringManipulationEvasion:
    """Attempting to construct dangerous attribute names via string
    concatenation to evade static AST checks.
    """

    def test_getattr_with_constructed_string_ast(self):
        """getattr(obj, '__' + 'class' + '__') -- getattr call is blocked."""
        with pytest.raises(UnsafeCodeError, match="getattr"):
            validate_user_code("getattr(obj, '__' + 'class' + '__')")

    def test_constructed_string_cannot_access_dunder_at_runtime(self):
        """FIX: __builtins__[...] subscript is now blocked at AST level,
        so the exploit chain cannot even pass validation."""
        code = 'ga = __builtins__["getattr"]\nattr = "__" + "class" + "__"\nresult = ga((), attr)\n'
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code(code)


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

    def test_type_blocked_in_builtins(self):
        ns = safe_globals()
        builtins_ns = ns.get("__builtins__", ns)
        assert "type" not in builtins_ns

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

    def test_allow_imports_restores_import(self):
        import builtins as _builtins

        ns = safe_globals(allow_imports=True)
        assert ns["__import__"] is _builtins.__import__

    def test_allow_imports_false_has_no_import(self):
        ns = safe_globals(allow_imports=False)
        assert "__import__" not in ns

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


class TestASTValidatorEdgeCases:
    def test_class_definition_blocked(self):
        with pytest.raises(UnsafeCodeError, match="class"):
            validate_user_code("class Foo:\n    x = 1")

    def test_async_function_definition_blocked(self):
        with pytest.raises(UnsafeCodeError, match="async"):
            validate_user_code("async def fetch():\n    await something()")

    def test_global_statement_blocked(self):
        with pytest.raises(UnsafeCodeError, match="global"):
            validate_user_code("def f():\n    global x\n    x = 1")

    def test_nonlocal_statement_blocked(self):
        with pytest.raises(UnsafeCodeError, match="nonlocal"):
            validate_user_code("def outer():\n    x = 1\n    def inner():\n        nonlocal x")

    def test_import_statement_blocked(self):
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code("import sys")

    def test_from_import_statement_blocked(self):
        with pytest.raises(UnsafeCodeError, match="import"):
            validate_user_code("from pathlib import Path")

    def test_allow_imports_permits_import(self):
        validate_user_code("import os", allow_imports=True)

    def test_allow_imports_permits_from_import(self):
        validate_user_code("from os.path import join", allow_imports=True)

    def test_builtins_subscript_blocked(self):
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code('__builtins__["open"]')

    def test_builtins_subscript_variable_key_blocked(self):
        with pytest.raises(UnsafeCodeError, match="__builtins__"):
            validate_user_code("__builtins__[key]")

    def test_chained_dunder_access_blocked(self):
        with pytest.raises(UnsafeCodeError):
            validate_user_code("obj.__class__.__subclasses__()")

    def test_chained_dunder_class_bases(self):
        with pytest.raises(UnsafeCodeError):
            validate_user_code("x.__class__.__bases__")

    def test_single_blocked_dunder_in_chain(self):
        with pytest.raises(UnsafeCodeError, match="__globals__"):
            validate_user_code("f.__globals__['os']")


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

    def test_assignment_with_dangerous_pattern_blocked(self):
        with pytest.raises(UnsafeCodeError, match="__class__"):
            validate_user_code("df = df.select(x.__class__)")

    def test_caching_returns_consistent_results(self):
        import haute._sandbox

        code = "cached_test_unique_12345 = 1"
        cache = haute._sandbox._validation_cache
        key = (code, False)
        cache.evict_where(lambda k: k == key)
        validate_user_code(code)
        assert key in cache

    def test_caching_second_call_uses_cache(self):
        import time

        import haute._sandbox

        code = "cached_perf_test_unique_67890 = 1"
        cache = haute._sandbox._validation_cache
        key = (code, False)
        cache.evict_where(lambda k: k == key)

        validate_user_code(code)
        assert key in cache

        start = time.perf_counter()
        for _ in range(1000):
            validate_user_code(code)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, "1000 cached validations should be near-instant"

    def test_unsafe_code_not_cached(self):
        import haute._sandbox

        code = "import os"
        key = (code, False)
        with pytest.raises(UnsafeCodeError):
            validate_user_code(code)
        assert key not in haute._sandbox._validation_cache


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

        # Insert ``bound + 5`` distinct preambles with force_refresh=False
        # so each is a fresh miss that populates the cache rather than a
        # cache_clear() on every call.
        for i in range(bound + 5):
            preamble = f"PREAMBLE_EVICT_TEST_{i} = {i}\n"
            _compile_preamble(preamble, force_refresh=False)

        info_after = _compile_preamble.cache_info()  # type: ignore[attr-defined]
        assert info_after.currsize <= bound

        # Clean up so we don't pollute other tests.
        _compile_preamble.cache_clear()  # type: ignore[attr-defined]


class TestValidationCacheDoesNotCacheUnsafe:
    def test_syntax_error_then_fixed_code_passes(self):
        import haute._sandbox

        bad_code = "def _cache_edge_test(:"
        good_code = "def _cache_edge_test(): pass"

        haute._sandbox._validation_cache.evict_where(lambda k: k == (bad_code, False))
        haute._sandbox._validation_cache.evict_where(lambda k: k == (good_code, False))

        with pytest.raises(UnsafeCodeError):
            validate_user_code(bad_code)

        assert (bad_code, False) not in haute._sandbox._validation_cache

        validate_user_code(good_code)
        assert (good_code, False) in haute._sandbox._validation_cache


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
        with pytest.raises(pickle.UnpicklingError, match="non-class callable"):
            unpickler.find_class("numpy", "load")

    def test_ctypeslib_load_library_gadget_rejected(self):
        """The concrete RCE gadget cited by the finding is rejected."""
        import io

        from haute._sandbox import _RestrictedUnpickler

        unpickler = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError, match="non-class callable"):
            unpickler.find_class("numpy.ctypeslib", "load_library")

    def test_class_under_trusted_package_allowed(self):
        """A class (numpy.ndarray) resolved from a trusted tree is allowed."""
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
        with pytest.raises(pickle.UnpicklingError, match="non-class callable"):
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
        with pytest.raises(pickle.UnpicklingError, match="non-class callable"):
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


class TestFormatStringDunderTraversal:
    """F735: str.format template attribute traversal (``{0.__globals__}``) hides
    dunder access inside a plain string literal the attribute visitor misses."""

    def test_format_globals_traversal_blocked(self):
        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code('leaked = "{0.__globals__}".format(fn)')

    def test_format_class_traversal_blocked(self):
        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code("x = '{0.__class__}'.format(obj)")

    def test_format_item_dunder_traversal_blocked(self):
        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code('x = "{0[__class__]}".format(d)')

    def test_format_map_traversal_blocked(self):
        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code('x = "{a.__globals__}".format_map(m)')

    def test_bare_template_literal_blocked_even_without_call(self):
        """The template literal is rejected wherever it appears — even assigned
        to a name first (defeats the ``tmpl = ...; tmpl.format(obj)`` bypass)."""
        with pytest.raises(UnsafeCodeError, match="[Ff]ormat"):
            validate_user_code('tmpl = "{0.__globals__}"')

    def test_pl_format_not_blocked(self):
        """polars' ``pl.format(...)`` is a legitimate API and must still pass."""
        validate_user_code('df = df.with_columns(pl.format("{}-{}", pl.col("a"), pl.col("b")))')

    def test_ordinary_str_format_not_blocked(self):
        """Plain formatting without dunder traversal is unaffected."""
        validate_user_code('label = "{}-{}".format(a, b)')

    def test_named_field_not_blocked(self):
        """A bare named field (no ``.``/``[`` traversal) is harmless."""
        validate_user_code('msg = "{name}".format(name=x)')


class TestNewlyBlockedDunders:
    """F736: dunders that previously slipped past the denylist and reached the
    type machinery (e.g. ``__base__`` reaches a parent type like ``__bases__``)."""

    @pytest.mark.parametrize(
        "code",
        [
            "x = obj.__getattribute__",
            "x = int.__base__",
            "x = obj.__subclasshook__",
            "x = list.__class_getitem__",
            "x = obj.__getstate__",
            "x = obj.__setstate__",
        ],
    )
    def test_dangerous_dunder_blocked(self, code: str):
        with pytest.raises(UnsafeCodeError):
            validate_user_code(code)

    def test_base_escape_chain_blocked(self):
        """``().__class__.__base__`` (the ``__base__`` variant of the classic
        ``__bases__`` walk) is blocked."""
        with pytest.raises(UnsafeCodeError, match="__base__"):
            validate_user_code("().__class__.__base__")


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
        assert "_injected_marker" not in sandbox._SAFE_BUILTINS

    def test_allow_imports_branch_also_isolated(self):
        ns1 = safe_globals(allow_imports=True)
        ns2 = safe_globals(allow_imports=True)
        assert ns1["__builtins__"] is not ns2["__builtins__"]
        assert ns1["__builtins__"].get("__import__") is not None


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
