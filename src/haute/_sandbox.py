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
- ``safe_unpickle(path)`` — a ``RestrictedUnpickler`` that narrows pickle
  globals to expected ML/data libraries (numpy, sklearn, catboost, etc.).
- ``validate_project_path(path)`` — ensures a path resolves inside the
  project root directory, preventing directory-traversal attacks.
"""

from __future__ import annotations

import ast
import builtins
import os
import pickle
import string
from pathlib import Path
from typing import Any

from haute._logging import get_logger
from haute._lru_cache import LRUCache
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

    Containment is checked with ``os.path.commonpath`` over
    ``os.path.normcase``-folded, fully-resolved paths rather than a raw
    ``Path.is_relative_to`` string prefix.  ``is_relative_to`` is
    case-sensitive, so on case-insensitive filesystems (macOS/APFS,
    Windows/NTFS) a case-variant path such as ``PROJECT/../SECRET`` could
    slip past a case-sensitive prefix check while still resolving to the
    same real file.  Folding both sides through ``normcase`` closes that
    bypass; on case-sensitive POSIX filesystems ``normcase`` is the
    identity so behaviour there is unchanged.

    Raises:
        ValueError: If the path escapes the project directory.
    """
    resolved = Path(path).resolve()
    root = _get_project_root()
    root_norm = os.path.normcase(str(root))
    resolved_norm = os.path.normcase(str(resolved))
    try:
        common = os.path.commonpath([root_norm, resolved_norm])
    except ValueError:
        # Different drives / mixed absolute-relative — cannot share a root.
        common = None
    if common != root_norm:
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
        "exit",
        "quit",
        "help",
        "super",
    }
)

# Base mapping of safe builtins *without* the ``__builtins__`` self-reference.
# Each ``safe_globals`` call layers a fresh ``__builtins__`` dict on top of a
# copy of this, so no returned namespace ever aliases module-global mutable
# state (mutating one exec namespace's builtins must not leak into the next).
_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in dir(builtins)
    if not name.startswith("_") and name not in _BLOCKED_BUILTINS
}


def safe_globals(*, allow_imports: bool = False, **extra: Any) -> dict[str, Any]:
    """Build a restricted global namespace for ``exec()``.

    Includes safe builtins + any extra bindings (e.g. ``pl=polars``).
    Blocks ``__import__``, ``open``, ``eval``, ``exec``, ``compile``,
    ``breakpoint``, ``globals``, ``locals``, and ``input``.

    *allow_imports* restores ``__import__`` — used for preamble code
    that legitimately imports from project utilities.

    A fresh ``__builtins__`` dict is built per call so mutations to one
    namespace's builtins cannot leak into subsequent exec namespaces.
    """
    inner: dict[str, Any] = dict(_SAFE_BUILTINS)
    if allow_imports:
        inner["__import__"] = builtins.__import__
    # Keep __builtins__ pointing at the restricted set so nested lookups
    # (e.g. list comprehensions) resolve names correctly.
    inner["__builtins__"] = inner
    ns: dict[str, Any] = dict(inner)
    if allow_imports:
        ns["__import__"] = builtins.__import__
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
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__import__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "__closure__",
        # Type-system traversal siblings of the entries above — each is a
        # reachable escape route if left off the list (e.g. ``__base__``
        # reaches a parent type just like ``__bases__``; ``__class_getitem__``
        # and ``__subclasshook__`` expose the type machinery; the pickle
        # state hooks let crafted objects drive ``__setstate__`` logic).
        "__base__",
        "__subclasshook__",
        "__class_getitem__",
        "__getstate__",
        "__setstate__",
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


# Parse ``str.format`` replacement fields, including fields nested inside
# format specs. Bare dunder-named fields remain harmless; traversal through an
# attribute or item into one is rejected.
_FORMATTER = string.Formatter()


def _format_template_has_dunder_traversal(template: str) -> bool:
    """Return whether *template* traverses into a dunder format field.

    ``Formatter.parse`` understands nested replacement fields in format specs,
    unlike the former regular expression. Format specs are parsed recursively
    because they may contain their own field traversal.
    """
    pending = [template]
    while pending:
        current = pending.pop()
        for _literal, field_name, format_spec, _conversion in _FORMATTER.parse(current):
            if field_name is not None:
                traverses = "." in field_name or "[" in field_name
                if traverses and "__" in field_name:
                    return True
            if format_spec:
                pending.append(format_spec)
    return False


# ``str`` methods that consume a *template string* and parse replacement fields
# (``{0.__globals__}``) out of it at runtime.  These are guarded at the call
# layer (``_ASTValidator.visit_Call``): the template must be a single string
# literal we can statically vet, because ``ast.parse`` does not constant-fold
# ``+``, so a template assembled at runtime (``'{0.' + '__globals__}'`` or a
# name-bound string) would otherwise smuggle dunder traversal past a
# literal-only scan.
_FORMAT_METHOD_NAMES = frozenset({"format", "format_map", "vformat", "get_field", "format_field"})

# Name bound to the polars module inside the sandbox namespace
# (``safe_globals(pl=pl)``).  ``pl.format("{}", expr)`` is the polars string
# builder — its receiver is the module, not a template string, and polars only
# understands positional ``{}`` placeholders (no attribute traversal), so it is
# carved out of the template guard below.
_POLARS_MODULE_ALIAS = "pl"


class UnsafeCodeError(HauteError):
    """Raised when AST validation detects a dangerous pattern."""


class _ASTValidator(ast.NodeVisitor):
    """Walk an AST and raise ``UnsafeCodeError`` on dangerous patterns.

    Blocks:
    - Dunder attribute access (``obj.__class__``, ``obj.__subclasses__()``)
    - Calls to reflection helpers (``getattr``, ``type``, ``vars``, etc.)
    - Import statements (unless ``allow_imports=True``)
    - ``class`` and ``async`` definitions
    - ``global`` / ``nonlocal`` scope-escaping statements
    - ``__builtins__[...]`` subscript access
    - ``str.format`` / ``.format_map`` / ``.vformat`` calls whose template is
      not a single statically-vettable string literal (``"{0.__globals__}"
      .format(obj)`` reads secrets via a format field the attribute-visitor
      never sees; a runtime-assembled template such as ``('{0.' +
      '__globals__}').format(obj)`` would slip past a literal-only scan).
    """

    def __init__(
        self,
        *,
        allow_imports: bool = False,
        polars_alias_shadowed: bool = False,
    ) -> None:
        super().__init__()
        self.allow_imports = allow_imports
        self.polars_alias_shadowed = polars_alias_shadowed

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
        # Guard ``str.format`` / ``.format_map`` / ``.vformat`` at the call
        # layer.  A literal-only scan is bypassable because ``ast.parse`` does
        # not constant-fold ``+`` — ``('{0.' + '__globals__}').format(g)`` and
        # ``tmpl = '{0.__globals__}'; tmpl.format(g)`` both leave a template the
        # scan never reconstructs.  Requiring the template to be a single vetted
        # string literal closes that side channel.
        if isinstance(node.func, ast.Attribute) and node.func.attr in _FORMAT_METHOD_NAMES:
            self._check_format_call(node.func)
        self.generic_visit(node)

    def _check_format_call(self, func: ast.Attribute) -> None:
        """Reject a ``.format``-family call whose template cannot be vetted.

        The template of ``str.format``/``.format_map``/``.vformat`` is the
        *receiver* (``func.value``).  polars' ``pl.format(...)`` is a distinct
        module-level builder — its receiver is the polars module, not a
        template string, and it only parses positional ``{}`` placeholders — so
        it is carved out.  Every other receiver shape is rejected: a string
        literal is admitted only when it contains no dunder-traversing field;
        anything non-literal (a ``BinOp`` concatenation, a name-bound template,
        a call result) cannot be statically vetted and is blocked.
        """
        receiver = func.value
        # polars ``pl.format("{}", expr)`` — receiver is the module, not a str.
        if (
            isinstance(receiver, ast.Name)
            and receiver.id == _POLARS_MODULE_ALIAS
            and not self.polars_alias_shadowed
        ):
            return
        if isinstance(receiver, ast.Constant) and isinstance(receiver.value, str):
            try:
                has_dunder_traversal = _format_template_has_dunder_traversal(receiver.value)
            except ValueError as exc:
                raise UnsafeCodeError(
                    f"Format-string template could not be statically parsed: {exc}"
                ) from exc
            if has_dunder_traversal:
                raise UnsafeCodeError(
                    "Format-string templates that traverse dunder attributes "
                    f"(e.g. '{{0.__globals__}}') are blocked in pipeline code — "
                    f"'.{func.attr}()' template rejected"
                )
            return
        raise UnsafeCodeError(
            f"'.{func.attr}()' requires a single string-literal template that "
            "can be statically vetted; a runtime-assembled or name-bound format "
            "template is blocked in pipeline code (it can hide dunder traversal "
            "such as '{0.__globals__}')"
        )

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


# Bounded cache of validated code strings.  A long-lived server previews and
# traces many distinct code fragments; a plain unbounded dict would retain one
# entry per fragment forever.  Reuse the codebase's bounded ``LRUCache`` (the
# same primitive backing ``_feature_validation_cache``) so the cache self-caps.
_VALIDATION_CACHE_MAX_SIZE = 1024
_validation_cache: LRUCache[tuple[str, bool], bool] = LRUCache(max_size=_VALIDATION_CACHE_MAX_SIZE)


def _bound_names(tree: ast.AST) -> set[str]:
    """Return names bound anywhere in *tree*.

    Used conservatively for the sandbox's special ``pl.format`` carve-out:
    if user code binds ``pl`` in any scope, ``pl.format`` is no longer assumed
    to be the trusted polars module function.
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
                bound = alias.asname or alias.name.split(".", 1)[0]
                if alias.name == "polars" and bound == _POLARS_MODULE_ALIAS:
                    continue
                names.add(bound)
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

    Uses a bounded ``LRUCache`` keyed by ``(code, allow_imports)``.
    Safe-code results (``True``) are cached; unsafe code always raises
    before caching.  The cache is thread-safe internally, so no external
    lock is needed.
    """
    cache_key = (code, allow_imports)
    if _validation_cache.get(cache_key) is not None:
        return

    # _try_parse_code raises UnsafeCodeError (wrapping the SyntaxError)
    # when the code cannot be parsed as standalone Python.
    tree = _try_parse_code(code)

    v = _ASTValidator(
        allow_imports=allow_imports,
        polars_alias_shadowed=_POLARS_MODULE_ALIAS in _bound_names(tree),
    )
    v.visit(tree)
    _validation_cache.put(cache_key, True)


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
        return resolver(module, name)
    if (module, name) in _ALLOWED_PICKLE_CLASSES:
        obj = resolver(module, name)
        if isinstance(obj, type):
            return obj
        raise _blocked_pickle_error(
            module,
            name,
            f"expected an allowlisted class, but {module}.{name} resolved to a non-class callable",
        )
    raise _blocked_pickle_error(module, name, "not in the allowlist")


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
    with open(validated, "rb") as f:
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
    validated = validate_project_path(path)

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
            return unpickler.load()
