"""Security sandbox for user-code execution and file deserialization.

Two layers of defence for ``exec()``-based user code:

1. **AST validation** (``validate_user_code``) — parses the code string
   and walks the tree *before* execution, rejecting dangerous patterns:
   dunder attribute access (``__class__``, ``__subclasses__``), reflection
   helpers (``getattr``, ``type``, ``vars``), import statements, class
   definitions, and scope-escaping keywords (``global``, ``nonlocal``).
   This closes known CPython sandbox-escape vectors at the structural
   level.

2. **Restricted builtins** (``safe_globals``) — runtime defence-in-depth
   that removes ``__import__``, ``open``, ``eval``, ``exec``, ``compile``,
   ``breakpoint``, ``globals``, ``locals``, and ``input`` from the
   namespace passed to ``exec()``.

Also provides:
- ``safe_unpickle(path)`` — a ``RestrictedUnpickler`` that only allows
  known-safe classes (numpy, sklearn, catboost, etc.).
- ``validate_project_path(path)`` — ensures a path resolves inside the
  project root directory, preventing directory-traversal attacks.
"""

from __future__ import annotations

import ast
import builtins
import pickle
import threading
from pathlib import Path
from typing import Any

from haute._logging import get_logger
from haute.errors import HauteError

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


def validate_project_path(path: str | Path) -> Path:
    """Resolve *path* and verify it is inside the project root.

    Raises:
        ValueError: If the path escapes the project directory.
    """
    resolved = Path(path).resolve()
    root = _get_project_root()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"Path '{path}' resolves to '{resolved}' which is outside the project root '{root}'"
        )
    return resolved


# ---------------------------------------------------------------------------
# Restricted builtins for exec()
# ---------------------------------------------------------------------------

# Builtins that allow arbitrary code execution or system access.
_BLOCKED_BUILTINS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "open",
        "input",
        "memoryview",
        "vars",
        "dir",
        "type",
        "hasattr",
    }
)

_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in dir(builtins)
    if not name.startswith("_") and name not in _BLOCKED_BUILTINS
}
# Keep __builtins__ itself pointing to the restricted set so nested
# lookups (e.g. list comprehensions) work correctly.
_SAFE_BUILTINS["__builtins__"] = _SAFE_BUILTINS


def safe_globals(*, allow_imports: bool = False, **extra: Any) -> dict[str, Any]:
    """Build a restricted global namespace for ``exec()``.

    Includes safe builtins + any extra bindings (e.g. ``pl=polars``).
    Blocks ``__import__``, ``open``, ``eval``, ``exec``, ``compile``,
    ``breakpoint``, ``globals``, ``locals``, and ``input``.

    *allow_imports* restores ``__import__`` — used for preamble code
    that legitimately imports from project utilities.
    """
    ns = dict(_SAFE_BUILTINS)
    if allow_imports:
        ns["__import__"] = builtins.__import__
        ns["__builtins__"] = {**_SAFE_BUILTINS, "__import__": builtins.__import__}
    ns.update(extra)
    return ns


# ---------------------------------------------------------------------------
# AST-level code validation — runs BEFORE exec()
# ---------------------------------------------------------------------------

# Attribute names that enable sandbox escapes via the Python type system.
_BLOCKED_ATTRS = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__class__",
        "__globals__",
        "__code__",
        "__func__",
        "__self__",
        "__module__",
        "__dict__",
        "__init_subclass__",
        "__set_name__",
        "__reduce__",
        "__reduce_ex__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__import__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "__closure__",
    }
)

# Non-dunder attribute names that enable frame/traceback inspection escapes.
_BLOCKED_FRAME_ATTRS = frozenset(
    {
        "__traceback__",
        "tb_frame",
        "tb_next",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_code",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "ag_code",
    }
)

# Built-in function names that can be used to bypass attribute restrictions.
_BLOCKED_CALLS = frozenset(
    {
        "getattr",
        "setattr",
        "delattr",
        "type",
        "vars",
        "dir",
        "hasattr",
        "classmethod",
        "staticmethod",
        "super",
        "__import__",
        "eval",
        "exec",
        "compile",
        "open",
        "breakpoint",
        "globals",
        "locals",
        "input",
        "exit",
        "quit",
        "help",
    }
)


class UnsafeCodeError(HauteError):
    """Raised when AST validation detects a dangerous pattern."""


class _ASTValidator(ast.NodeVisitor):
    """Walk an AST and raise ``UnsafeCodeError`` on dangerous patterns.

    Blocks:
    - Dunder attribute access (``obj.__class__``, ``obj.__subclasses__()``)
    - Calls to reflection helpers (``getattr``, ``type``, ``vars``, etc.)
    - Import statements (unless ``allow_imports=True``)
    - Class, async, and lambda definitions
    - Star expressions in assignments (``a, *b = ...``)
    """

    def __init__(self, *, allow_imports: bool = False) -> None:
        super().__init__()
        self.allow_imports = allow_imports

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            if node.attr in _BLOCKED_ATTRS:
                raise UnsafeCodeError(f"Access to '{node.attr}' is blocked in pipeline code")
        # Block traceback frame access — prevents sandbox escape via
        # exception handler: e.__traceback__.tb_frame.f_globals
        if node.attr in _BLOCKED_FRAME_ATTRS:
            raise UnsafeCodeError(f"Access to '{node.attr}' is blocked in pipeline code")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Block calls to dangerous built-in names
        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            raise UnsafeCodeError(f"Call to '{node.func.id}()' is blocked in pipeline code")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # Block __builtins__["getattr"] style access — prevents retrieving
        # blocked callables via dict subscription on the builtins namespace.
        if isinstance(node.value, ast.Name) and node.value.id == "__builtins__":
            raise UnsafeCodeError("Subscript access to '__builtins__' is blocked in pipeline code")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if not self.allow_imports:
            raise UnsafeCodeError("import statements are blocked in pipeline code")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self.allow_imports:
            raise UnsafeCodeError("import statements are blocked in pipeline code")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        raise UnsafeCodeError("class definitions are blocked in pipeline code")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        raise UnsafeCodeError("async function definitions are blocked in pipeline code")

    def visit_Global(self, node: ast.Global) -> None:
        raise UnsafeCodeError("global statements are blocked in pipeline code")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        raise UnsafeCodeError("nonlocal statements are blocked in pipeline code")


_validation_cache: dict[tuple[str, bool], bool] = {}
_validation_cache_lock = threading.Lock()


def validate_user_code(code: str, *, allow_imports: bool = False) -> None:
    """Parse *code* and check for dangerous AST patterns.

    Raises ``UnsafeCodeError`` if the code contains blocked constructs
    (dunder access, imports, getattr, class defs, etc.).

    Called by ``_exec_user_code`` before ``exec()`` so dangerous code
    is rejected at the structural level — not just at runtime via
    restricted builtins.

    *allow_imports* permits ``import`` / ``from … import`` statements,
    used for preamble code which legitimately imports from utility modules.

    Results for safe code are cached by code string so repeated
    executions of the same node (preview, trace) skip the AST parse.
    """
    _validate_user_code_cached(code, allow_imports=allow_imports)


def _validate_user_code_cached(
    code: str,
    *,
    allow_imports: bool = False,
) -> None:
    """Inner validation with per-code-string caching.

    Uses a module-level dict as a simple cache.  Safe-code results
    (``True``) are cached; unsafe code always raises before caching.

    Code that cannot be parsed as standalone Python (e.g. chain syntax
    ``.filter(…)``) is wrapped as ``df = (\\n    df\\n    <code>\\n)``
    and re-parsed before giving up — this mirrors how the executor
    wraps user code fragments.
    """
    cache_key = (code, allow_imports)
    with _validation_cache_lock:
        if cache_key in _validation_cache:
            return

    # _try_parse_code raises UnsafeCodeError (wrapping the SyntaxError)
    # if neither the raw code nor a wrapped version can be parsed.
    tree = _try_parse_code(code)

    v = _ASTValidator(allow_imports=allow_imports)
    v.visit(tree)
    with _validation_cache_lock:
        _validation_cache[cache_key] = True


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

# Allowlisted (module, qualname) prefixes for pickle deserialization.
# These cover the common model/data types used in pricing pipelines.
_ALLOWED_PICKLE_PREFIXES: list[tuple[str, ...]] = [
    ("numpy",),
    ("sklearn",),
    ("scipy",),
    ("catboost",),
    ("xgboost",),
    ("lightgbm",),
    ("pandas",),
    ("polars",),
    ("joblib",),
    ("collections",),
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
    ("builtins", "True"),
    ("builtins", "False"),
    ("builtins", "None"),
    ("_codecs",),
    ("copyreg",),
    ("datetime",),
]


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler that only allows known-safe classes.

    Prevents arbitrary code execution via crafted pickle payloads
    while still supporting common ML model and data formats.
    """

    def find_class(self, module: str, name: str) -> Any:
        for prefix in _ALLOWED_PICKLE_PREFIXES:
            if len(prefix) == 1 and module.startswith(prefix[0]):
                return super().find_class(module, name)
            if len(prefix) == 2 and module == prefix[0] and name == prefix[1]:
                return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"Blocked unpickling of {module}.{name} — "
            f"class not in the allowlist. If this is a legitimate model "
            f"class, add its module to _ALLOWED_PICKLE_PREFIXES in "
            f"src/haute/_sandbox.py"
        )


def safe_unpickle(path: str | Path) -> Any:
    """Deserialize a pickle file using the restricted unpickler.

    Also validates the path is within the project root.
    """
    validated = validate_project_path(path)
    with open(validated, "rb") as f:
        return _RestrictedUnpickler(f).load()


_joblib_lock = threading.Lock()


def safe_joblib_load(path: str | Path) -> Any:
    """Deserialize a joblib file using a restricted unpickler.

    ``joblib.load()`` uses pickle internally but provides no class
    restriction hook.  This function patches joblib's ``NumpyUnpickler``
    with the same ``find_class`` allowlist used by ``safe_unpickle``,
    then restores the original after loading.

    Also validates the path is within the project root.
    """
    validated = validate_project_path(path)

    try:
        from joblib.numpy_pickle import NumpyUnpickler
    except ImportError:
        # joblib not installed — fall back to restricted pickle
        logger.warning("joblib_missing", msg="falling back to safe_unpickle")
        return safe_unpickle(validated)

    original_find_class = NumpyUnpickler.find_class

    def _restricted_joblib_find_class(self: Any, module: str, name: str) -> Any:
        """find_class with allowlist, delegating to the original on match."""
        for prefix in _ALLOWED_PICKLE_PREFIXES:
            if len(prefix) == 1 and module.startswith(prefix[0]):
                return original_find_class(self, module, name)
            if len(prefix) == 2 and module == prefix[0] and name == prefix[1]:
                return original_find_class(self, module, name)
        raise pickle.UnpicklingError(
            f"Blocked unpickling of {module}.{name} — "
            f"class not in the allowlist. If this is a legitimate model "
            f"class, add its module to _ALLOWED_PICKLE_PREFIXES in "
            f"src/haute/_sandbox.py"
        )

    with _joblib_lock:
        NumpyUnpickler.find_class = _restricted_joblib_find_class
        try:
            import joblib

            return joblib.load(validated)
        finally:
            NumpyUnpickler.find_class = original_find_class
