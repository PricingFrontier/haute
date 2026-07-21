"""Layer 2 of the test write-sandbox: a static AST gate over ``tests/``.

Filesystem-write APIs in test code must target paths derived from the
per-test scratch fixtures (``haute_scratch`` / ``tmp_path`` /
``tmp_path_factory`` — one sandbox substrate; ``haute_scratch`` is the
convention name). This scan turns that rule into a CI gate, in the same
idiom as the subprocess chokepoint scan (``test_repository_hygiene.py``)
and the skip/xfail debt ratchet (``test_test_debt.py``): explicit
allowlist, asserted stale in both directions.

What it flags, per lexical scope, unless the target is *taint-derived*
from a scratch fixture:

* ``tempfile.mkdtemp`` / ``tempfile.mkstemp`` — system-temp, leak-prone
  (no ``dir=`` derived from the sandbox). The self-cleaning context
  managers (``TemporaryDirectory`` etc.) stay legal: the runtime guard
  redirects them into the sandbox.
* ``open(..., "w"/"a"/"x"/"+")`` and ``Path.open`` in write modes.
* ``Path.write_text`` / ``Path.write_bytes``.
* ``os.makedirs``.
* Destination-writing ``shutil`` calls.
* Absolute-path and parent-traversal (``..``) string literals inside the
  *targets of the write APIs above*. Bare ``Path("/abs")`` constructions
  and read-mode opens are deliberately not flagged: adversarial-input
  tests (path traversal, security) use absolute/parent literals as data,
  and the sandbox rule is about writes, not reads.

A line ending in ``# write-sandbox: deliberate`` is exempt — reserved for
the guard's own self-tests (which must attempt real escapes) and the
census spool plumbing in ``conftest.py``.

The taint rule is deliberately conservative-static: fixture parameters
seed it, assignments/`with` targets/f-strings propagate it, *any*
function parameter counts as derived (call-side responsibility), and
targets the scan cannot resolve (call results, non-literal modes) pass —
the runtime guard (layer 3, ``tests/_write_sandbox.py``) owns what
static analysis cannot see.

The allowlist below carries the not-yet-converted files with their
current violation counts. Shrinking it is the conversion ratchet; a new
entry, or a count that grows, is a deliberate review decision made in
this file, where review sees it.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._write_sandbox import ENV_ROOT, STRICT_FILES

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

_TAINT_SEEDS = frozenset({"haute_scratch", "tmp_path", "tmp_path_factory"})

_TEMPFILE_SELF_CLEANING = frozenset(
    {"TemporaryDirectory", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
)
_TEMPFILE_BANNED = frozenset({"mkdtemp", "mkstemp"})

_PATH_WRITE_ATTRS = frozenset({"write_text", "write_bytes"})

# shutil callables that write at a destination: name -> (positional index of
# the destination, keyword name). Read-only shutil (disk_usage, which) is fine.
_SHUTIL_DEST = {
    "copy": (1, "dst"),
    "copy2": (1, "dst"),
    "copyfile": (1, "dst"),
    "copymode": (1, "dst"),
    "copystat": (1, "dst"),
    "copytree": (1, "dst"),
    "move": (1, "dst"),
    "rmtree": (0, "path"),
    "make_archive": (0, "base_name"),
    "unpack_archive": (1, "extract_dir"),
}

_PATH_CONSTRUCTORS = frozenset(
    {"Path", "PurePath", "PurePosixPath", "PureWindowsPath", "PosixPath", "WindowsPath"}
)

_ABS_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[/\\]|\\\\)")
_PARENT_RE = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)")

_WRITE_MODE_CHARS = frozenset("wax+")


@dataclass(frozen=True)
class LintViolation:
    file: str  # repo-relative posix path
    line: int
    kind: str
    detail: str


def _iter_test_sources() -> list[Path]:
    return sorted(
        path
        for path in _TESTS_DIR.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in path.parts)
    )


def _module_aliases(tree: ast.Module, module: str) -> tuple[set[str], dict[str, str]]:
    """Names bound to *module* and to its members, file-wide.

    Returns ``(module_aliases, member_alias_to_member)`` — e.g. for
    ``import tempfile as _tempfile`` and ``from tempfile import mkdtemp as mk``:
    ``({"_tempfile"}, {"mk": "mkdtemp"})``.
    """
    aliases: set[str] = set()
    members: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                members[alias.asname or alias.name] = alias.name
    return aliases, members


_PRAGMA = "# write-sandbox: deliberate"


class _FileScanner:
    """Scans one test file, scope by scope."""

    def __init__(self, rel: str, tree: ast.Module, source_lines: list[str]) -> None:
        self.rel = rel
        self.source_lines = source_lines
        self.violations: list[LintViolation] = []
        self.tempfile_aliases, self.tempfile_members = _module_aliases(tree, "tempfile")
        self.shutil_aliases, self.shutil_members = _module_aliases(tree, "shutil")
        self.os_aliases, self.os_members = _module_aliases(tree, "os")
        self.io_aliases, _ = _module_aliases(tree, "io")

    # -- taint ------------------------------------------------------------

    def _call_member(
        self, call: ast.Call, aliases: set[str], members: dict[str, str]
    ) -> str | None:
        """The member name a call resolves to within a module, or None."""
        func = call.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in aliases:
                return func.attr
        elif isinstance(func, ast.Name) and func.id in members:
            return members[func.id]
        return None

    def _is_sandbox_env_lookup(self, node: ast.expr) -> bool:
        """``os.environ[ENV_ROOT]`` / ``os.environ.get(ENV_ROOT)`` / ``os.getenv(ENV_ROOT)``."""
        if isinstance(node, ast.Subscript):
            value, key = node.value, node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            if node.func.attr == "get":
                value, key = node.func.value, node.args[0]
            elif node.func.attr == "getenv" and isinstance(node.func.value, ast.Name):
                if node.func.value.id in self.os_aliases:
                    key = node.args[0]
                    return isinstance(key, ast.Constant) and key.value == ENV_ROOT
                return False
            else:
                return False
        else:
            return False
        return (
            isinstance(value, ast.Attribute)
            and value.attr == "environ"
            and isinstance(value.value, ast.Name)
            and value.value.id in self.os_aliases
            and isinstance(key, ast.Constant)
            and key.value == ENV_ROOT
        )

    def _tainted(self, node: ast.expr, tainted: set[str]) -> bool:
        """True if *node* plausibly derives from a scratch fixture."""
        if isinstance(node, ast.Name):
            return node.id in tainted
        if isinstance(node, ast.Starred):
            return self._tainted(node.value, tainted)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._tainted(node.left, tainted) or self._tainted(node.right, tainted)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # str concatenation building on a tainted prefix
            return self._tainted(node.left, tainted) or self._tainted(node.right, tainted)
        if isinstance(node, ast.Attribute):
            # os.devnull is a legitimate sink everywhere
            if (
                node.attr == "devnull"
                and isinstance(node.value, ast.Name)
                and node.value.id in self.os_aliases
            ):
                return True
            return self._tainted(node.value, tainted)
        if isinstance(node, ast.Subscript):
            if self._is_sandbox_env_lookup(node):
                return True
            return self._tainted(node.value, tainted)
        if isinstance(node, ast.JoinedStr):
            return any(
                self._tainted(part.value, tainted)
                for part in node.values
                if isinstance(part, ast.FormattedValue)
            )
        if isinstance(node, ast.IfExp):
            return self._tainted(node.body, tainted) or self._tainted(node.orelse, tainted)
        if isinstance(node, ast.Call):
            if self._is_sandbox_env_lookup(node):
                return True
            func = node.func
            if isinstance(func, ast.Name) and (
                func.id in _PATH_CONSTRUCTORS or func.id in {"str", "repr"}
            ):
                return any(self._tainted(arg, tainted) for arg in node.args)
            if isinstance(func, ast.Attribute):
                if func.attr == "fspath":
                    return any(self._tainted(arg, tainted) for arg in node.args)
                # method call on a tainted object: p.with_suffix(...), p.joinpath(...),
                # tmp_path_factory.mktemp(...), f"{p}".format? (attr chains handled above)
                return self._tainted(func.value, tainted)
        return False

    def _bind_targets(self, target: ast.expr, tainted: set[str]) -> None:
        if isinstance(target, ast.Name):
            tainted.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_targets(element, tainted)
        elif isinstance(target, ast.Starred):
            self._bind_targets(target.value, tainted)

    def _propagate(self, nodes: list[ast.stmt], tainted: set[str]) -> None:
        """Grow the taint set from assignment-like statements (to fixpoint)."""
        while True:
            before = len(tainted)
            for stmt in nodes:
                for node in self._walk_scope(stmt):
                    if isinstance(node, ast.Assign):
                        if self._assign_value_tainted(node.value, tainted):
                            for target in node.targets:
                                self._bind_targets(target, tainted)
                    elif isinstance(node, ast.AnnAssign) and node.value is not None:
                        if self._assign_value_tainted(node.value, tainted):
                            self._bind_targets(node.target, tainted)
                    elif isinstance(node, ast.NamedExpr):
                        if self._assign_value_tainted(node.value, tainted):
                            self._bind_targets(node.target, tainted)
                    elif isinstance(node, (ast.With, ast.AsyncWith)):
                        for item in node.items:
                            if item.optional_vars is None:
                                continue
                            if self._with_context_tainted(item.context_expr, tainted):
                                self._bind_targets(item.optional_vars, tainted)
                    elif isinstance(node, (ast.For, ast.AsyncFor)):
                        if self._tainted(node.iter, tainted):
                            self._bind_targets(node.target, tainted)
            if len(tainted) == before:
                return

    def _assign_value_tainted(self, value: ast.expr, tainted: set[str]) -> bool:
        if self._tainted(value, tainted):
            return True
        # fd, path = tempfile.mkstemp(dir=<tainted>) — sandbox-located result
        if isinstance(value, ast.Call):
            member = self._call_member(value, self.tempfile_aliases, self.tempfile_members)
            if member in _TEMPFILE_BANNED and self._dir_kwarg_tainted(value, tainted):
                return True
        return False

    def _with_context_tainted(self, context: ast.expr, tainted: set[str]) -> bool:
        if self._tainted(context, tainted):
            return True
        if isinstance(context, ast.Call):
            member = self._call_member(context, self.tempfile_aliases, self.tempfile_members)
            if member in _TEMPFILE_SELF_CLEANING:
                return True  # self-cleaning; runtime TMPDIR redirect sandboxes it
        return False

    def _dir_kwarg_tainted(self, call: ast.Call, tainted: set[str]) -> bool:
        for keyword in call.keywords:
            if keyword.arg == "dir":
                return self._tainted(keyword.value, tainted)
        return False

    # -- scope plumbing ----------------------------------------------------

    def _walk_scope(self, root: ast.AST) -> list[ast.AST]:
        """All nodes under *root* without descending into nested functions."""
        collected: list[ast.AST] = []
        stack: list[ast.AST] = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested scope — scanned separately with its own taint
            collected.append(node)
            stack.extend(ast.iter_child_nodes(node))
        return collected

    def scan(self, tree: ast.Module) -> list[LintViolation]:
        # Module/class-level statements form one scope with no parameter seeds;
        # every function/method is its own scope, seeded with the fixtures, its
        # parameters, and — closures — everything tainted in its enclosing scope.
        module_scope: list[ast.stmt] = []
        functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

        def split(body: list[ast.stmt]) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(stmt)
                elif isinstance(stmt, ast.ClassDef):
                    split(stmt.body)
                else:
                    module_scope.append(stmt)

        split(tree.body)
        module_tainted: set[str] = set()
        self._propagate(module_scope, module_tainted)
        self._check_scope(module_scope, module_tainted)
        for function in functions:
            self._scan_function(function, module_tainted)
        return self.violations

    def _scan_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, enclosing: set[str]
    ) -> None:
        tainted = set(enclosing) | _TAINT_SEEDS
        args = node.args
        for arg in (
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            *((args.vararg,) if args.vararg else ()),
            *((args.kwarg,) if args.kwarg else ()),
        ):
            tainted.add(arg.arg)
        self._propagate(node.body, tainted)
        self._check_scope(node.body, tainted)
        for nested in self._nested_functions(node.body):
            self._scan_function(nested, tainted)

    def _nested_functions(
        self, body: list[ast.stmt]
    ) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        stack: list[ast.AST] = list(body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(node)
            elif not isinstance(node, ast.Lambda):
                stack.extend(ast.iter_child_nodes(node))
        return found

    # -- checks -------------------------------------------------------------

    def _flag(self, node: ast.AST, kind: str, detail: str) -> None:
        line = getattr(node, "lineno", 0)
        if 0 < line <= len(self.source_lines) and self.source_lines[line - 1].rstrip().endswith(
            _PRAGMA
        ):
            return
        self.violations.append(LintViolation(file=self.rel, line=line, kind=kind, detail=detail))

    def _check_write_target(
        self, call: ast.Call, target: ast.expr, tainted: set[str], kind: str
    ) -> None:
        """One violation max per write call: untainted target, else literal escape."""
        if not self._tainted(target, tainted):
            self._flag(call, kind, ast.dump(target)[:60])
            return
        for node in ast.walk(target):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Interior f-string segments legitimately start with a
                # separator (f"{root}/name"); only a *leading* segment can
                # re-root the path, and JoinedStr walks handle that below.
                if _ABS_PATH_RE.match(node.value) and not self._is_interior_fstring_segment(
                    target, node
                ):
                    self._flag(call, "absolute-path-literal", node.value)
                    return
                if node.value == ".." or _PARENT_RE.search(node.value):
                    self._flag(call, "parent-traversal-literal", node.value)
                    return

    @staticmethod
    def _is_interior_fstring_segment(target: ast.expr, constant: ast.Constant) -> bool:
        for node in ast.walk(target):
            if isinstance(node, ast.JoinedStr) and constant in node.values[1:]:
                return True
        return False

    def _open_mode(self, call: ast.Call, position: int) -> str | None:
        """The literal mode argument of an open-like call, if statically known."""
        if len(call.args) > position and isinstance(call.args[position], ast.Constant):
            value = call.args[position].value
            return value if isinstance(value, str) else None
        for keyword in call.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                return value if isinstance(value, str) else None
        return None

    def _check_scope(self, nodes: list[ast.stmt], tainted: set[str]) -> None:
        for stmt in nodes:
            for node in self._walk_scope(stmt):
                if isinstance(node, ast.Call):
                    self._check_call(node, tainted)

    def _check_call(self, call: ast.Call, tainted: set[str]) -> None:
        func = call.func

        # tempfile.mkdtemp / mkstemp
        member = self._call_member(call, self.tempfile_aliases, self.tempfile_members)
        if member in _TEMPFILE_BANNED:
            dir_kwargs = [kw.value for kw in call.keywords if kw.arg == "dir"]
            if not dir_kwargs:
                self._flag(call, f"tempfile.{member}", "system-temp target; use haute_scratch")
            else:
                self._check_write_target(call, dir_kwargs[0], tainted, f"tempfile.{member}")
            return

        # builtin open / io.open
        is_open = (isinstance(func, ast.Name) and func.id == "open") or (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id in self.io_aliases
        )
        if is_open and call.args:
            mode = self._open_mode(call, 1)
            if mode is not None and _WRITE_MODE_CHARS & set(mode):
                self._check_write_target(call, call.args[0], tainted, "open-write")
            return

        # Path-object writes: .write_text/.write_bytes/.open("w")
        if isinstance(func, ast.Attribute):
            if func.attr in _PATH_WRITE_ATTRS:
                self._check_write_target(call, func.value, tainted, f"Path.{func.attr}")
                return
            if func.attr == "open":
                mode = self._open_mode(call, 0)
                if mode is not None and _WRITE_MODE_CHARS & set(mode):
                    self._check_write_target(call, func.value, tainted, "Path.open-write")
                return

        # os.makedirs
        os_member = self._call_member(call, self.os_aliases, self.os_members)
        if os_member == "makedirs" and call.args:
            self._check_write_target(call, call.args[0], tainted, "os.makedirs")
            return

        # shutil destination writers
        shutil_member = self._call_member(call, self.shutil_aliases, self.shutil_members)
        if shutil_member in _SHUTIL_DEST:
            position, kw_name = _SHUTIL_DEST[shutil_member]
            dest: ast.expr | None = None
            if len(call.args) > position:
                dest = call.args[position]
            else:
                for keyword in call.keywords:
                    if keyword.arg == kw_name:
                        dest = keyword.value
            if dest is not None:
                self._check_write_target(call, dest, tainted, f"shutil.{shutil_member}")


def scan_source(rel: str, source: str) -> list[LintViolation]:
    tree = ast.parse(source)
    return _FileScanner(rel, tree, source.splitlines()).scan(tree)


def scan_tests() -> dict[str, list[LintViolation]]:
    results: dict[str, list[LintViolation]] = {}
    for path in _iter_test_sources():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        violations = scan_source(rel, path.read_text(encoding="utf-8"))
        if violations:
            results[rel] = violations
    return results


# ---------------------------------------------------------------------------
# The allowlist: not-yet-converted files -> current violation count.
# Shrink by converting a file to derive its writes from haute_scratch and
# deleting its row. Counts are exact on purpose: growth in an allowlisted
# file fails the gate the same as a new file would.
# ---------------------------------------------------------------------------

EXPECTED_VIOLATIONS: dict[str, int] = {
    # Mostly writes through cache-dir/model-dir helper call results the static
    # taint cannot resolve, plus a handful of genuine system-temp users. The
    # runtime census (layer 3 observe mode) is the ground truth for which of
    # these actually escape the sandbox at run time.
    "tests/test_algorithms_coverage.py": 3,
    "tests/test_cache_perf_fixes.py": 2,
    "tests/test_databricks_io.py": 3,
    "tests/test_dataframe_execution_cache.py": 1,
    "tests/test_deploy.py": 1,
    "tests/test_deploy_contract_integrity.py": 1,
    "tests/test_deploy_scorer_artifact_cache.py": 1,
    "tests/test_executor.py": 2,
    "tests/test_file_ops.py": 18,
    "tests/test_git_graph.py": 5,
    "tests/test_graph_fingerprint_cached.py": 1,
    # These writes resolve through isolated project/cache helper paths. The
    # static scanner intentionally cannot infer taint through those helpers;
    # the runtime sandbox census verifies that they remain under tmp_path.
    "tests/test_apiinput_multi_port_runtime.py": 1,
    "tests/test_json_cache_coverage_uplift.py": 9,
    "tests/test_json_cache_integrity.py": 6,
    "tests/test_json_shred_mut_stragglers.py": 1,
    "tests/test_load_v2_api_source.py": 10,
    # 2 pre-existing + 2 cache-key-contract tests writing through
    # _artifact_cache_path(tmp_path / ...) results (tmp_path-rooted, but the
    # static taint cannot see through the helper call).
    "tests/test_mlflow_io.py": 4,
    "tests/test_mlflow_io_concurrency.py": 12,
    "tests/test_model_score_executor.py": 1,
    "tests/test_optimiser_routes.py": 2,
    "tests/test_optimiser_service_coverage.py": 2,
    "tests/test_polars_utils.py": 1,
    "tests/test_runtime_input_cache_invalidation.py": 1,
    "tests/test_server.py": 1,
    "tests/test_submodel_routes.py": 3,
}


@pytest.mark.timeout(180)
def test_write_apis_derive_from_scratch_fixture() -> None:
    # The full-corpus AST scan is slow and load-sensitive under `-n 4` +
    # coverage, so scan once and give the test headroom beyond the default
    # pytest-timeout.
    scanned = scan_tests()
    observed = {rel: len(violations) for rel, violations in scanned.items()}
    details = {
        rel: [f"  {v.file}:{v.line} {v.kind} ({v.detail})" for v in violations]
        for rel, violations in scanned.items()
    }

    unexpected = {
        rel: count for rel, count in observed.items() if count > EXPECTED_VIOLATIONS.get(rel, 0)
    }
    stale = {
        rel: expected
        for rel, expected in EXPECTED_VIOLATIONS.items()
        if observed.get(rel, 0) < expected
    }

    unexpected_lines = "\n".join(
        line
        for rel in sorted(unexpected)
        for line in [f"{rel}: {observed[rel]} (allowlisted: {EXPECTED_VIOLATIONS.get(rel, 0)})"]
        + details.get(rel, [])
    )
    assert unexpected == {}, (
        "New test-write violations outside the sandbox convention. Derive the "
        "write target from the haute_scratch fixture (or tmp_path); tempfile."
        "mkdtemp/mkstemp, absolute paths, and '..' are banned in tests/. If the "
        "file genuinely cannot convert yet, add/update its allowlist row here, "
        f"where review sees it.\n{unexpected_lines}"
    )
    stale_report = {rel: (EXPECTED_VIOLATIONS[rel], observed.get(rel, 0)) for rel in sorted(stale)}
    assert stale == {}, (
        "Allowlist is stale — these files now have fewer violations than "
        "recorded. Ratchet down their counts (or delete the rows) so the "
        f"gate keeps its teeth: {stale_report}"
    )


def test_strict_files_are_lint_clean() -> None:
    """Layer coupling: a strict (converted) module may not be allowlisted."""
    overlap = sorted(rel for rel in EXPECTED_VIOLATIONS if Path(rel).name in STRICT_FILES)
    assert overlap == []


# ---------------------------------------------------------------------------
# Unit tests for the scanner itself (textwrap probes, test_test_debt idiom)
# ---------------------------------------------------------------------------


def _probe(source: str) -> list[str]:
    violations = scan_source("tests/probe.py", textwrap.dedent(source))
    return [v.kind for v in violations]


def test_scanner_flags_bare_mkstemp_and_mkdtemp() -> None:
    kinds = _probe(
        """
        import tempfile

        def test_x():
            fd, path = tempfile.mkstemp(suffix=".json")
            d = tempfile.mkdtemp()
        """
    )
    assert kinds == ["tempfile.mkstemp", "tempfile.mkdtemp"]


def test_scanner_allows_mkstemp_into_scratch() -> None:
    kinds = _probe(
        """
        import tempfile

        def test_x(haute_scratch):
            fd, path = tempfile.mkstemp(dir=haute_scratch)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("ok")
        """
    )
    assert kinds == []


def test_scanner_flags_untainted_open_write_but_not_read() -> None:
    kinds = _probe(
        """
        def test_x():
            data = open("config.json", encoding="utf-8").read()
            with open(computed(), "w", encoding="utf-8") as fh:
                fh.write("x")
        """
    )
    assert kinds == ["open-write"]


def test_scanner_allows_scratch_derived_writes_through_assignments() -> None:
    kinds = _probe(
        """
        import shutil

        def test_x(tmp_path, haute_scratch):
            sub = tmp_path / "nested" / "dir"
            target = sub.with_suffix(".json")
            target.write_text("{}", encoding="utf-8")
            (haute_scratch / "b.bin").write_bytes(b"x")
            shutil.copytree("fixtures", haute_scratch / "copy")
            shutil.rmtree(sub)
            with open(f"{tmp_path}/f.txt", "w", encoding="utf-8") as fh:
                fh.write("ok")
        """
    )
    assert kinds == []


def test_scanner_flags_module_level_literal_writes() -> None:
    kinds = _probe(
        """
        from pathlib import Path

        Path("out.json").write_text("{}", encoding="utf-8")
        """
    )
    assert kinds == ["Path.write_text"]


def test_scanner_flags_literal_escapes_from_tainted_roots() -> None:
    kinds = _probe(
        """
        def test_x(tmp_path, haute_scratch):
            (tmp_path / ".." / "escape.txt").write_text("x", encoding="utf-8")
            with open(f"{haute_scratch}/../gone.txt", "w", encoding="utf-8") as fh:
                fh.write("x")
            (tmp_path / "/etc/absolute").write_bytes(b"x")
        """
    )
    assert sorted(kinds) == [
        "absolute-path-literal",
        "parent-traversal-literal",
        "parent-traversal-literal",
    ]


def test_scanner_flags_untainted_write_targets_once_each() -> None:
    kinds = _probe(
        """
        from pathlib import Path

        def test_x():
            Path("/etc/haute.cfg").write_text("x", encoding="utf-8")
            with open("/var/log/haute.log", "a", encoding="utf-8") as fh:
                fh.write("x")
        """
    )
    assert kinds == ["Path.write_text", "open-write"]


def test_scanner_spares_adversarial_path_data_and_reads() -> None:
    kinds = _probe(
        """
        from pathlib import Path

        def test_x(client):
            evil = Path("/etc/evil.py")
            probe = Path("../../../etc/passwd")
            body = open("/etc/hosts", encoding="utf-8").read()
            response = client.post("/api/save", json={"path": str(evil)})
        """
    )
    assert kinds == []


def test_scanner_honours_deliberate_pragma_and_devnull() -> None:
    kinds = _probe(
        """
        import os

        def test_x():
            with open("outside.txt", "w", encoding="utf-8") as fh:  # write-sandbox: deliberate
                fh.write("probe")
            with open(os.devnull, "w", encoding="utf-8") as sink:
                sink.write("gone")
        """
    )
    assert kinds == []


def test_scanner_does_not_flag_route_literals_or_data_strings() -> None:
    kinds = _probe(
        """
        def test_x(client):
            response = client.get("/api/files")
            assert "../traversal" in response.text
        """
    )
    assert kinds == []


def test_scanner_treats_env_root_lookup_as_scratch_derived() -> None:
    kinds = _probe(
        f"""
        import os
        from pathlib import Path

        def helper():
            root = Path(os.environ["{ENV_ROOT}"])
            (root / "artifact.json").write_text("{{}}", encoding="utf-8")
        """
    )
    assert kinds == []


def test_scanner_trusts_function_parameters_call_side() -> None:
    kinds = _probe(
        """
        def write_config(directory, payload):
            (directory / "cfg.json").write_text(payload, encoding="utf-8")
        """
    )
    assert kinds == []


def test_scanner_taints_self_cleaning_tempfile_context_names() -> None:
    kinds = _probe(
        """
        import tempfile
        from pathlib import Path

        def test_x():
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / "f.txt").write_text("ok", encoding="utf-8")
        """
    )
    assert kinds == []


def test_scanner_scopes_closures_and_nested_defs() -> None:
    kinds = _probe(
        """
        def test_x(tmp_path):
            target = tmp_path / "f.py"

            def writer(content):
                target.write_text(content, encoding="utf-8")

            def helper(path):
                path.write_text("x", encoding="utf-8")

            def stray():
                open("outside.txt", "w", encoding="utf-8").write("bad")

            writer("data")
        """
    )
    assert kinds == ["open-write"]


def test_scanner_flags_untainted_makedirs_and_shutil_dest() -> None:
    kinds = _probe(
        """
        import os
        import shutil

        def test_x(tmp_path):
            os.makedirs(computed_dir())
            shutil.copy(tmp_path / "src.txt", computed_dir())
            shutil.move(computed(), tmp_path / "kept.txt")
        """
    )
    assert kinds == ["os.makedirs", "shutil.copy"]
