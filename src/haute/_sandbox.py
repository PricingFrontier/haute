"""Accident guard for project code, and restricted deserialization.

Project code (node text, the preamble, pivot formulas, expression steps) is
trusted: it runs with the privileges of the haute process, and nothing here
contains it. Its ``exec()``/``eval()`` call sites use two helpers:

1. ``validate_user_code`` parses the code and rejects only a direct call that
   would hang or stop the server it runs in (``input``, ``exit``, ``quit``,
   ``breakpoint``). Everything else is ordinary Python.
2. ``safe_globals`` builds the execution namespace: the ordinary builtins
   without those four calls, plus caller bindings such as ``pl``.

Also provides:
- ``safe_unpickle(path)`` — a ``RestrictedUnpickler`` that narrows pickle
  globals to expected ML/data libraries (numpy, sklearn, catboost, etc.).
- ``contained_path(root, path)`` — the one path-containment check, and
  ``validate_project_path(path)``, the same check against the project root.
"""

from __future__ import annotations

import ast
import builtins
import itertools
import pickle
import sys
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import CodeType
from typing import Any

from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute.errors import HauteError, InvalidPathError, PathOutsideProjectError

logger = get_logger(component="sandbox")

# ---------------------------------------------------------------------------
# Project-root path validation
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path | None = None


def _get_project_root() -> Path:
    """Return the cached project root (cwd at import time)."""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = Path.cwd().resolve()
    return _PROJECT_ROOT


def set_project_root(root: Path) -> None:
    """Override the project root (used by tests and CLI)."""
    global _PROJECT_ROOT
    _PROJECT_ROOT = root.resolve()


_OUTSIDE_PROJECT = "Cannot access paths outside the project root"


def contained_path(root: Path, path: str | Path) -> Path:
    """Return *path* resolved against *root*, refusing one that leaves *root*.

    The one containment check: every caller that must keep a path inside a
    directory calls this. *path* may be relative (joined to *root*) or
    absolute. The policy:

    - An absolute *path* that is not lexically inside the resolved *root* is
      refused before anything resolves it, so a request can never make the
      process touch an outside location (a network share, a device) just to
      check it. Such a path is refused even if it would resolve back inside.
    - Otherwise the joined path is resolved: ``..`` segments collapse and
      symbolic links and Windows junctions are followed, so a link inside the
      root that points outside is refused and one that points inside is
      accepted. Callers that must refuse links altogether check that
      themselves.
    - The resolved path must lie under the resolved *root*, compared
      component-wise: case-insensitively on Windows, case-sensitively
      elsewhere. On a case-insensitive macOS volume a differently-cased
      spelling of an inside path can therefore be refused; it can never let an
      outside path through.

    Raises:
        InvalidPathError: *path* holds a NUL byte.
        PathOutsideProjectError: *path* leaves *root*.
    """
    if "\x00" in str(path):
        raise InvalidPathError("Invalid path")
    base = root.resolve()
    raw = Path(path)
    if raw.is_absolute() and not raw.is_relative_to(base):
        raise PathOutsideProjectError(_OUTSIDE_PROJECT, path=str(path))
    target = (base / raw).resolve()
    if not target.is_relative_to(base):
        raise PathOutsideProjectError(_OUTSIDE_PROJECT, path=str(path))
    return target


def validate_project_path(path: str | Path) -> Path:
    """Resolve *path* (relative to the working directory) inside the project root.

    Used before a project file is deserialised. The path comes from project
    configuration, not from a request, so it is resolved before
    :func:`contained_path` compares it with :func:`_get_project_root`.
    """
    return contained_path(_get_project_root(), Path(path).resolve())


# ---------------------------------------------------------------------------
# Accident guard for project code run inside the server
# ---------------------------------------------------------------------------

# Builtins whose direct call hangs or stops the server process that runs
# project code. Everything else, reflection, classes and imports included, is
# ordinary Python: project code is trusted, and this is not a sandbox.
_SERVER_STOPPING_CALLS: dict[str, str] = {
    "input": "waits for console input the server never receives",
    "exit": "stops the server process",
    "quit": "stops the server process",
    "breakpoint": "waits for a debugger on the server's console",
}

# The prefix of the module name project code runs under, so a class it
# defines records where it came from instead of claiming to be a builtin. It is
# deliberately not ``__main__``: a script's ``if __name__ == "__main__":`` block
# must not run inside the server. Each namespace gets its own numbered name, so
# concurrent executions never resolve each other's names.
PROJECT_CODE_MODULE = "haute_project_code"
_project_code_numbers = itertools.count(1)

# The ordinary builtins without the server-stopping calls. Each
# ``safe_globals`` call copies this, so no returned namespace aliases
# module-global mutable state.
_EXEC_BUILTINS: dict[str, Any] = {
    name: value for name, value in vars(builtins).items() if name not in _SERVER_STOPPING_CALLS
}


def safe_globals(**extra: Any) -> dict[str, Any]:
    """Build the global namespace for ``exec()``/``eval()`` of project code.

    The ordinary builtins without the calls that would hang or stop the
    server, plus any extra bindings (e.g. ``pl=polars``). A fresh
    ``__builtins__`` dict is built per call so mutations to one namespace's
    builtins cannot leak into subsequent exec namespaces.
    """
    inner: dict[str, Any] = dict(_EXEC_BUILTINS)
    ns: dict[str, Any] = {name: value for name, value in inner.items() if not name.startswith("_")}
    # Nested scopes (comprehensions, helpers) resolve builtins through this.
    ns["__builtins__"] = inner
    ns["__name__"] = f"{PROJECT_CODE_MODULE}_{next(_project_code_numbers)}"
    ns.update(extra)
    return ns


class _ProjectCodeModule:
    """Stands in for a module in ``sys.modules``; its ``__dict__`` is the namespace."""

    def __init__(self, namespace: dict[str, Any]) -> None:
        self.__dict__ = namespace


@contextmanager
def project_code_module(namespace: dict[str, Any]) -> Iterator[None]:
    """Register *namespace* as its module while project code runs in it.

    ``dataclasses`` and ``typing.get_type_hints`` resolve a class's string
    annotations through ``sys.modules[cls.__module__]``, so a quoted annotation
    or a ``from __future__ import annotations`` in project code needs the
    namespace to be findable there. The entry is removed afterwards, so a
    long-lived server does not accumulate one module per execution.
    """
    name = namespace["__name__"]
    sys.modules[name] = _ProjectCodeModule(namespace)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.modules.pop(name, None)


def compile_project_code(code: str) -> CodeType:
    """Compile project code for ``exec()`` as an ordinary module would be.

    ``dont_inherit`` keeps haute's own ``from __future__`` imports (postponed
    annotations) out of the project's code, so a dataclass or a
    ``get_type_hints`` call in node code sees the annotations Python gives a
    module that does not ask for postponement.
    """
    return compile(code, "<string>", "exec", dont_inherit=True)


class UnsafeCodeError(HauteError):
    """Raised when project code calls a server-stopping builtin or cannot be parsed."""


class _AccidentGuard(ast.NodeVisitor):
    """Reject a direct call to a server-stopping builtin the code does not rebind."""

    def __init__(self, bound_names: set[str]) -> None:
        super().__init__()
        self.bound_names = bound_names

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id in _SERVER_STOPPING_CALLS
            and func.id not in self.bound_names
        ):
            raise UnsafeCodeError(
                f"'{func.id}()' {_SERVER_STOPPING_CALLS[func.id]}, so pipeline code cannot call it."
            )
        self.generic_visit(node)


# Bounded cache of validated code strings.  A long-lived server previews and
# traces many distinct code fragments; a plain unbounded dict would retain one
# entry per fragment forever.  Reuse the codebase's bounded ``LRUCache`` (the
# same primitive backing ``_feature_validation_cache``) so the cache self-caps.
_VALIDATION_CACHE_MAX_SIZE = 1024
_validation_cache: LRUCache[str, bool] = LRUCache(max_size=_VALIDATION_CACHE_MAX_SIZE)


def _bound_names(tree: ast.AST) -> set[str]:
    """Return names bound anywhere in *tree*.

    A call to a name the code binds itself (``def input(): ...``) is the
    code's own function, not the builtin, so the guard leaves it alone.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def validate_user_code(code: str) -> None:
    """Parse *code* and reject a direct call that would hang or stop the server.

    Raises ``UnsafeCodeError`` for a call to ``input``, ``exit``, ``quit`` or
    ``breakpoint`` by that bare name (unless the code binds the name itself),
    and for code that cannot be parsed, chaining the ``SyntaxError``.

    Called before project code runs inside the server. Results for accepted
    code are cached by code string so repeated executions of the same node
    (preview, trace) skip the parse.
    """
    if _validation_cache.get(code) is not None:
        return
    # _try_parse_code raises UnsafeCodeError (wrapping the SyntaxError)
    # when the code cannot be parsed as standalone Python.
    tree = _try_parse_code(code)
    _AccidentGuard(_bound_names(tree)).visit(tree)
    _validation_cache.put(code, True)


def _try_parse_code(code: str) -> ast.Module:
    """Try to parse *code* as Python; return the AST or raise.

    Raises ``UnsafeCodeError`` (with the original ``SyntaxError`` as
    ``__cause__``) when the code cannot be parsed.
    """
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        raise UnsafeCodeError(
            f"Cannot validate code with syntax errors (line {exc.lineno}): {exc.msg}"
        ) from exc


# ---------------------------------------------------------------------------
# Restricted unpickler
# ---------------------------------------------------------------------------
#
# Threat model.  ``pickle`` invokes ``find_class(module, name)`` and then, on a
# ``REDUCE`` opcode, *calls the returned object*.  If ``find_class`` hands back
# a plain module-level function, an attacker-crafted payload can invoke it with
# attacker-chosen arguments — i.e. arbitrary code execution.  The scalar
# scaffolding functions pickle legitimately needs (``numpy._core.multiarray.
# _reconstruct``, ``copyreg._reconstructor``, …) are *also* module-level
# functions, so the two cannot be told apart by structure alone.
#
# A whole-package prefix allowlist (the previous design) is therefore unsafe:
# large libraries inevitably ship code-execution gadget functions somewhere in
# their tree (``numpy.testing._private.utils.runstring``,
# ``numpy.ctypeslib.load_library``, ``pandas.core.computation.eval.eval``, …),
# and a ``module.startswith("numpy")`` rule admits every one of them.
#
# The allowlist below is split into two exact, resolution-checked tiers:
#
#   * ``_ALLOWED_PICKLE_GLOBALS`` — an *exact* ``(module, qualname)`` set of the
#     vetted scaffolding functions and builtin scalar/container constructors
#     that legitimate model/data pickles reference.  Only these named callables
#     may be returned as-is.
#
#   * ``_ALLOWED_PICKLE_CLASSES`` - an exact ``(module, qualname)`` set of
#     model/data classes that may be reconstructed after resolving to a class.
#     Whole package trees are not trusted.
#
# Pickle remains a code-*bearing* format: an attacker who controls a model file
# can still forge the *state* of an allowlisted class (e.g. a fake ``coef_``).
# That is inherent to loading an untrusted model and is out of scope here — what
# this closes is arbitrary *code execution* via non-class callable gadgets.

# Exact ``(module, qualname)`` pairs that may be returned verbatim.  These are
# the pickle scaffolding callables (functions) plus the builtin scalar/container
# type constructors.  ``builtins`` is intentionally *not* a trusted class prefix
# because it also holds ``eval``/``exec``/``getattr`` (functions) and ``type``
# (a class) — so only these named entries are admitted.
_ALLOWED_PICKLE_GLOBALS: frozenset[tuple[str, str]] = frozenset(
    {
        # NumPy 2 array/scalar reconstruction.
        ("numpy._core.multiarray", "_reconstruct"),
        ("numpy._core.multiarray", "scalar"),
        ("numpy._core.numeric", "_frombuffer"),
        # generic object reconstruction helpers used by ``__reduce_ex__``.
        ("copyreg", "_reconstructor"),
        ("copyreg", "__newobj__"),
        ("copyreg", "__newobj_ex__"),
        # bytes reconstruction (``_codecs.encode(text, 'latin1')``).
        ("_codecs", "encode"),
        # pandas block/manager reconstruction helpers (functions, not classes).
        ("pandas.core.internals.blocks", "new_block"),
        ("pandas.core.indexes.base", "_new_Index"),
        ("pandas._libs.internals", "_unpickle_block"),
        # builtin scalar / container constructors (safe to call on data args).
        ("builtins", "frozenset"),
        ("builtins", "set"),
        ("builtins", "dict"),
        ("builtins", "list"),
        ("builtins", "tuple"),
        ("builtins", "range"),
        ("builtins", "slice"),
        ("builtins", "bytes"),
        ("builtins", "bytearray"),
        ("builtins", "complex"),
        ("builtins", "float"),
        ("builtins", "int"),
        ("builtins", "bool"),
        ("builtins", "str"),
        # NOTE: ``True``/``False``/``None`` are pickle opcodes, never routed
        # through ``find_class`` — so they are deliberately omitted.
    }
)

# Exact ``(module, qualname)`` pairs for classes that legitimate persisted
# artifacts currently need. Whole package trees are deliberately not trusted:
# adding support for a new estimator/container class is an explicit allowlist
# review, not an incidental side effect of living under ``sklearn``, ``pandas``,
# ``joblib``, etc.
_ALLOWED_PICKLE_CLASSES: frozenset[tuple[str, str]] = frozenset(
    {
        ("joblib.numpy_pickle", "NumpyArrayWrapper"),
        ("numpy", "dtype"),
        ("numpy", "ndarray"),
        ("pandas.core.frame", "DataFrame"),
        ("pandas.core.indexes.base", "Index"),
        ("pandas.core.indexes.range", "RangeIndex"),
        ("pandas.core.internals.managers", "BlockManager"),
        ("pandas.core.internals.managers", "SingleBlockManager"),
        ("pandas.core.series", "Series"),
        ("polars.dataframe.frame", "DataFrame"),
        ("polars.series.series", "Series"),
        ("sklearn.ensemble._forest", "RandomForestRegressor"),
        ("sklearn.linear_model._base", "LinearRegression"),
        ("sklearn.tree._classes", "DecisionTreeRegressor"),
        ("sklearn.tree._tree", "Tree"),
        ("catboost.core", "CatBoost"),
        ("catboost.core", "CatBoostClassifier"),
        ("catboost.core", "CatBoostRegressor"),
        ("lightgbm.sklearn", "LGBMClassifier"),
        ("lightgbm.sklearn", "LGBMModel"),
        ("lightgbm.sklearn", "LGBMRegressor"),
        ("xgboost.sklearn", "XGBClassifier"),
        ("xgboost.sklearn", "XGBModel"),
        ("xgboost.sklearn", "XGBRegressor"),
        # Exactly the two EBM estimators; no other InterpretML symbol is trusted.
        ("interpret.glassbox._ebm._ebm", "ExplainableBoostingClassifier"),
        ("interpret.glassbox._ebm._ebm", "ExplainableBoostingRegressor"),
    }
)


def _blocked_pickle_error(module: str, name: str, reason: str) -> pickle.UnpicklingError:
    """Build the uniform ``UnpicklingError`` raised when a global is rejected."""
    return pickle.UnpicklingError(
        f"Blocked unpickling of {module}.{name} — {reason}. If this is a "
        f"legitimate model/data class, add its exact (module, qualname) to "
        f"_ALLOWED_PICKLE_CLASSES in src/haute/_sandbox.py; add only vetted "
        f"scaffolding functions to _ALLOWED_PICKLE_GLOBALS"
    )


def _resolve_allowed_global(
    resolver: Any,
    module: str,
    name: str,
) -> Any:
    """Resolve ``module.name`` through *resolver* iff it clears both tiers.

    *resolver* is the underlying ``pickle.Unpickler.find_class`` (bound or the
    joblib ``NumpyUnpickler`` original).  An exact scaffolding entry is returned
    verbatim; an exact class entry is returned only when it resolves to a
    class. Everything else raises ``UnpicklingError``.
    """
    if (module, name) in _ALLOWED_PICKLE_GLOBALS:
        return _resolve_installed(resolver, module, name)
    if (module, name) in _ALLOWED_PICKLE_CLASSES:
        obj = _resolve_installed(resolver, module, name)
        if isinstance(obj, type):
            return obj
        raise _blocked_pickle_error(
            module,
            name,
            f"expected an allowlisted class, but {module}.{name} resolved to a non-class callable",
        )
    raise _blocked_pickle_error(module, name, "not in the allowlist")


def _resolve_installed(resolver: Any, module: str, name: str) -> Any:
    """Resolve an allowlisted ``module.name``, naming a package that is absent.

    The allowlist names other projects' module paths, some of which
    (LightGBM, XGBoost) are not installed in every environment.  Only a
    ``ModuleNotFoundError`` for the entry's own top-level package becomes the
    uniform blocked-pickle error; a missing module deeper in the tree is a
    broken install and propagates unchanged.
    """
    package = module.partition(".")[0]
    try:
        return resolver(module, name)
    except ModuleNotFoundError as exc:
        if exc.name != package:
            raise
        raise _blocked_pickle_error(
            module,
            name,
            f"allowlisted, but `{package}` is not installed in this environment",
        ) from exc


class ArtifactVersionMismatchError(HauteError):
    """A persisted estimator was written by a different library version than the loader's."""


def _sklearn_inconsistent_version_warning() -> type[Warning] | None:
    """scikit-learn's version-mismatch warning class, or ``None`` if scikit-learn is absent.

    Only a missing ``sklearn`` package counts as absent -- the same
    discrimination ``safe_joblib_load`` applies to joblib -- so a scikit-learn
    install that is present but broken surfaces instead of being ignored.
    """
    try:
        from sklearn.exceptions import InconsistentVersionWarning
    except ModuleNotFoundError as exc:
        if exc.name != "sklearn":
            raise
        return None
    warning_cls: type[Warning] = InconsistentVersionWarning
    return warning_cls


@contextmanager
def _estimator_version_mismatch_is_an_error() -> Iterator[None]:
    """Promote scikit-learn's ``InconsistentVersionWarning`` to a haute error.

    ``BaseEstimator.__setstate__`` compares the pickled ``_sklearn_version`` to
    the installed ``sklearn.__version__`` and only *warns* on a mismatch, after
    which the estimator behaves however it behaves.  A model trained on one
    machine and deployed from another must fail here, not score.  The filter
    is scoped to the load: ``catch_warnings`` restores the process-wide filter
    state on exit.
    """
    warning_cls = _sklearn_inconsistent_version_warning()
    if warning_cls is None:
        yield
        return
    with warnings.catch_warnings():
        warnings.simplefilter("error", warning_cls)
        try:
            yield
        except warning_cls as exc:
            # scikit-learn's own message names both versions; keep it whole.
            raise ArtifactVersionMismatchError(
                f"Refusing to load an estimator persisted under a different "
                f"scikit-learn version. {exc}"
            ) from exc


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler with a narrow allowlist for expected project artifacts.

    Pickle remains a code-bearing format.  This reduces the allowed import
    surface for persisted ML models/data, but it is not a general guarantee
    that arbitrary untrusted pickle payloads are safe.
    """

    def find_class(self, module: str, name: str) -> Any:
        return _resolve_allowed_global(super().find_class, module, name)


def safe_unpickle(path: str | Path) -> Any:
    """Deserialize a pickle file using the restricted unpickler.

    The restricted unpickler narrows the import surface for expected project
    artifacts, but pickle payloads should still be treated as trusted inputs.
    Also validates the path is within the project root.
    """
    validated = validate_project_path(path)
    with open(validated, "rb") as f, _estimator_version_mismatch_is_an_error():
        return _RestrictedUnpickler(f).load()


def safe_joblib_load(path: str | Path) -> Any:
    """Deserialize a joblib file using a restricted unpickler.

    ``joblib.load()`` uses pickle internally but provides no public class
    restriction hook. This function instantiates a private restricted subclass
    of joblib's ``NumpyUnpickler`` so no process-wide class is ever patched.

    The allowlist narrows the import surface for expected project artifacts;
    joblib payloads should still be treated as trusted inputs.  Also validates
    the path is within the project root.
    """
    return restricted_joblib_load(validate_project_path(path))


def restricted_joblib_load(path: str | Path) -> Any:
    """Deserialize a joblib file through the class allowlist, wherever it lives.

    :func:`safe_joblib_load` without the project-root check, for model files
    Haute's own loaders locate (training outputs, the MLflow artifact cache, an
    MLflow pyfunc package), which need not sit under the working directory.
    """
    validated = Path(path).resolve()

    try:
        import joblib
    except ModuleNotFoundError as exc:
        if exc.name != "joblib":
            raise
        # joblib not installed — fall back to restricted pickle.
        logger.warning("joblib_missing", msg="falling back to safe_unpickle")
        return safe_unpickle(validated)

    try:
        if not hasattr(joblib.numpy_pickle, "NumpyUnpickler"):
            raise AttributeError("NumpyUnpickler")
        validate_fileobject_and_memmap = joblib.numpy_pickle._validate_fileobject_and_memmap
    except AttributeError as exc:
        raise RuntimeError(
            "Installed joblib is incompatible with Haute's restricted loader: "
            "required numpy_pickle APIs are unavailable"
        ) from exc

    class RestrictedNumpyUnpickler(joblib.numpy_pickle.NumpyUnpickler):
        def find_class(self, module: str, name: str) -> Any:
            return _resolve_allowed_global(
                lambda m, n: super(RestrictedNumpyUnpickler, self).find_class(m, n),
                module,
                name,
            )

    filename = str(validated)
    with open(validated, "rb") as raw_file:
        with validate_fileobject_and_memmap(raw_file, filename, None) as (
            file_object,
            mmap_mode,
        ):
            if isinstance(file_object, str):
                raise ValueError("legacy joblib persistence formats are not supported")
            try:
                unpickler = RestrictedNumpyUnpickler(
                    filename,
                    file_object,
                    ensure_native_byte_order=True,
                    mmap_mode=mmap_mode,
                )
            except TypeError as exc:
                raise RuntimeError(
                    "Installed joblib is incompatible with Haute's restricted loader: "
                    "the numpy_pickle unpickler constructor is unsupported"
                ) from exc
            with _estimator_version_mismatch_is_an_error():
                return unpickler.load()
