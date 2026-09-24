from __future__ import annotations

import ast
import os
import re
import subprocess
import textwrap
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._source_files import SourceTreeGuard, source_files

pytest_plugins = ["pytester"]


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def test_generated_and_local_agent_artifacts_are_not_tracked() -> None:
    tracked = _tracked_files()
    offenders = sorted(
        path
        for path in tracked
        if path == ".omc"
        or path.startswith(".omc/")
        or path == "graphify-out"
        or "/graphify-out/" in f"/{path}/"
        or (Path(path).name.startswith("PR23_") and path.endswith(".md"))
        # Installed packages and their tool caches (a Vitest results cache
        # under a stray root-level node_modules/ was once committed).
        or path.startswith("node_modules/")
        or "/node_modules/" in f"/{path}"
    )

    assert offenders == []


def test_example_pipeline_config_lives_only_under_the_reference_example() -> None:
    tracked = _tracked_files()
    root_config = sorted(path for path in tracked if path == "config" or path.startswith("config/"))

    assert root_config == []
    assert any(path.startswith("examples/reference/config/") for path in tracked)


def test_no_local_mlflow_store_is_tracked() -> None:
    tracked = _tracked_files()

    assert (
        sorted(
            path
            for path in tracked
            if path.endswith(".db") or path.startswith("mlruns/") or "/mlruns/" in path
        )
        == []
    )


def test_source_walks_prune_bytecode_caches_before_entering_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another worker may create or remove a __pycache__ mid-walk, so the walk
    # must never list one: enumerating a cache directory fails this test.
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "module.cpython-311.pyc").write_bytes(b"")
    (tmp_path / "pkg" / "module.py").write_text("", encoding="utf-8")
    real_scandir = os.scandir

    def scandir(path: str) -> object:
        if Path(path).name == "__pycache__":
            raise AssertionError(f"the walk entered {path}")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    assert source_files(tmp_path) == [tmp_path / "pkg" / "module.py"]


# Trees that hold no Python, so no bytecode cache can appear in them mid-walk.
_TREES_WITHOUT_PYTHON = frozenset({"docs", "specs", "frontend"})


def _name_bindings(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """Every expression a module assigns to each name, anywhere in the module.

    Names imported from the test package count as bound to the repository
    (``REPO_ROOT`` and friends are anchored at ``__file__``).
    """
    bindings: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tests"):
            for alias in node.names:
                bindings.setdefault(alias.asname or alias.name, []).append(ast.Name(id="__file__"))
            continue
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, []).append(value)
    return bindings


def _walk_origin(
    expression: ast.expr, bindings: dict[str, list[ast.expr]], seen: frozenset[str]
) -> tuple[bool, set[str]]:
    """Whether *expression* derives from a ``__file__``, and the path segments it names."""
    anchored = False
    segments: set[str] = set()
    for node in ast.walk(expression):
        if (isinstance(node, ast.Name) and node.id == "__file__") or (
            isinstance(node, ast.Attribute) and node.attr == "__file__"
        ):
            anchored = True
        elif isinstance(node, ast.Name) and node.id in bindings and node.id not in seen:
            for value in bindings[node.id]:
                value_anchored, value_segments = _walk_origin(value, bindings, seen | {node.id})
                anchored |= value_anchored
                segments |= value_segments
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            segments.add(node.value)
    return anchored, segments


def _walk_receiver(call: ast.Call) -> ast.expr | None:
    """The directory a recursive walk call descends from, if *call* is one."""
    if not isinstance(call.func, ast.Attribute):
        return None
    method, owner = call.func.attr, call.func.value
    if method == "walk" and isinstance(owner, ast.Name) and owner.id == "os":
        return call.args[0] if call.args else None
    if method in {"rglob", "walk"} and not (isinstance(owner, ast.Name) and owner.id == "ast"):
        return owner
    if (
        method == "glob"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and "**" in str(call.args[0].value)
    ):
        return owner
    return None


def _raw_repository_walks(source: str) -> list[int]:
    """Lines that walk a repository tree which can hold bytecode caches."""
    tree = ast.parse(source)
    bindings = _name_bindings(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or (receiver := _walk_receiver(node)) is None:
            continue
        anchored, segments = _walk_origin(receiver, bindings, frozenset())
        if anchored and not segments & _TREES_WITHOUT_PYTHON:
            lines.append(node.lineno)
    return sorted(lines)


def test_raw_repository_walk_detection_follows_names_to_the_repository() -> None:
    sample = textwrap.dedent(
        """
        import os
        from pathlib import Path

        from tests._source_files import REPO_ROOT

        ROOT = Path(__file__).resolve().parents[1]
        SOURCE = ROOT / "src"
        SPECS = ROOT / "specs"


        def walks(tmp_path, folder):
            this_file = Path(__file__).resolve()
            root = this_file.parents[1]
            list(SOURCE.rglob("*"))
            list((root / folder).rglob("*"))
            list(REPO_ROOT.glob("**/*.py"))
            list(os.walk(ROOT / "tests"))
            list(SPECS.rglob("*.md"))
            list(tmp_path.rglob("*"))
            list(ROOT.glob("*.md"))
        """
    )

    assert _raw_repository_walks(sample) == [15, 16, 17, 18]


def test_tests_walk_repository_trees_through_the_shared_helper() -> None:
    offenders = [
        f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}"
        for path in source_files(_REPO_ROOT / "tests")
        if path.name != "_source_files.py"
        for line in _raw_repository_walks(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def _guarded_run(
    pytester: pytest.Pytester, watched: Path, test_source: str, *args: str
) -> pytest.RunResult:
    (watched / "pkg").mkdir(parents=True, exist_ok=True)
    (watched / "pkg" / "module.py").write_text("", encoding="utf-8")
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    pytester.makepyfile(test_guarded=test_source)
    return pytester.runpytest(*args, plugins=[SourceTreeGuard(watched, label="src")])


def _writes(target: Path) -> str:
    return textwrap.dedent(
        f"""
        from pathlib import Path


        def test_writes():
            target = Path({str(target)!r})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
        """
    )


def test_a_file_left_under_the_source_tree_fails_the_session_and_is_named(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    watched = tmp_path / "src"
    result = _guarded_run(
        pytester, watched, _writes(watched / "pkg" / "mlruns" / "0" / "meta.yaml")
    )

    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*files left under src/*", "src/pkg/mlruns/0/meta.yaml"])


def test_a_file_left_by_a_test_on_an_xdist_worker_fails_the_controller_session(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    watched = tmp_path / "src"
    result = _guarded_run(
        pytester, watched, _writes(watched / "pkg" / "left_behind.json"), "-n", "1"
    )

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["*files left under src/*", "src/pkg/left_behind.json", "*1 passed*"]
    )


@pytest.mark.parametrize("left_behind", [None, "pkg/__pycache__/module.cpython-311.pyc"])
def test_clean_and_bytecode_only_sessions_pass(
    pytester: pytest.Pytester, tmp_path: Path, left_behind: str | None
) -> None:
    watched = tmp_path / "src"
    source = (
        "def test_nothing():\n    pass\n" if left_behind is None else _writes(watched / left_behind)
    )
    result = _guarded_run(pytester, watched, source)

    assert result.ret == pytest.ExitCode.OK
    result.stdout.no_fnmatch_line("*files left under*")


def test_the_guard_does_nothing_on_an_xdist_worker(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    guard = SourceTreeGuard(tmp_path, label="src")
    session = SimpleNamespace(config=SimpleNamespace(workerinput={}), exitstatus=pytest.ExitCode.OK)

    guard.pytest_sessionstart(session)  # type: ignore[arg-type]
    (tmp_path / "pkg" / "left_behind.json").write_text("{}", encoding="utf-8")
    guard.pytest_sessionfinish(session)  # type: ignore[arg-type]

    assert guard.added == []
    assert session.exitstatus == pytest.ExitCode.OK


def test_the_roadmap_holds_reports_not_probes_or_benchmark_output() -> None:
    """Executable probes and raw results live in scripts/benchmarks/; the roadmap
    keeps only Markdown."""
    tracked = _tracked_files()
    roadmap = sorted(path for path in tracked if path.startswith("specs/roadmap/"))

    assert roadmap
    assert [path for path in roadmap if not path.endswith(".md")] == []


def test_graphify_is_not_a_runtime_dependency() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    lockfile = Path("uv.lock").read_text(encoding="utf-8")

    assert not any(dep.lower().startswith("graphifyy") for dep in dependencies)
    assert 'name = "graphifyy"' not in lockfile


# ---------------------------------------------------------------------------
# Subprocess chokepoint scan.
#
# ``subprocess`` is how haute shells out to external tools (git, npm, docker,
# nvidia-smi).  Each tool has exactly one module owning those calls, so that
# platform quirks — Windows executable resolution, output decoding — live in
# one audited place per tool.  The tests below turn that convention into a CI
# gate: a new ``import subprocess`` outside the allowlist means either a new
# external tool (add a chokepoint module and an allowlist entry, in this file,
# where review sees it) or a call that belongs in an existing chokepoint.
#
# Scope is src/haute/ only: tests/, scripts/, and hatch_build.py may
# shell out freely (that is their job; the build hook cannot even import the
# package's helpers because it runs before the package is installed).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_HAUTE = _REPO_ROOT / "src" / "haute"
_SCAN_SKIP_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

# The only modules in src/haute/ allowed to import subprocess, and why.
_SUBPROCESS_IMPORT_ALLOWLIST = {
    "src/haute/_git_core.py",  # Git subprocess chokepoint
    "src/haute/_host_memory.py",  # nvidia-smi chokepoint (caller; function-local import)
    "src/haute/cli/_serve.py",  # npm chokepoint (caller, via _helpers._npm)
    "src/haute/deploy/_container.py",  # docker chokepoint (caller)
    "src/haute/cli/_gpu_setup.py",  # uv/pip installer + fresh-interpreter chokepoint (caller)
    # import-only: deliberate F401-suppressed patch-target — tests patch the
    # module attribute and assert this module never shells out.
    "src/haute/cli/_helpers.py",
}

# The chokepoints that actually launch subprocesses (the import-only entry
# above never makes calls, so it carries no text-mode call sites).
_CALLER_CHOKEPOINTS = (
    "src/haute/_git_core.py",
    "src/haute/_host_memory.py",
    "src/haute/cli/_serve.py",
    "src/haute/deploy/_container.py",
    "src/haute/cli/_gpu_setup.py",
)


def _iter_src_haute_sources() -> list[Path]:
    return sorted(
        path
        for path in source_files(_SRC_HAUTE)
        if not any(part in _SCAN_SKIP_DIRS for part in path.parts)
    )


def _rel_posix(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _imports_module(tree: ast.AST, module: str) -> bool:
    """True if *tree* imports *module* in any form, at any nesting depth.

    Catches ``import m``, ``import m as x``, ``import m.sub``, and
    ``from m import ...`` — including function-local imports (``ast.walk``
    visits every node).  Comments, docstrings, and string literals cannot
    trip this: only genuine import statements produce Import/ImportFrom
    nodes.
    """
    prefix = module + "."
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == module or a.name.startswith(prefix) for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module
            if mod is not None and (mod == module or mod.startswith(prefix)):
                return True
    return False


def test_subprocess_imported_only_in_chokepoint_modules() -> None:
    importing = {
        _rel_posix(path)
        for path in _iter_src_haute_sources()
        if _imports_module(ast.parse(path.read_text(encoding="utf-8")), "subprocess")
    }

    unexpected = sorted(importing - _SUBPROCESS_IMPORT_ALLOWLIST)
    missing = sorted(_SUBPROCESS_IMPORT_ALLOWLIST - importing)
    assert unexpected == [], (
        "New subprocess import outside the chokepoint allowlist. Every external "
        "tool gets exactly one module owning its subprocess calls (so Windows "
        "resolution and output-decoding quirks stay in one audited place). Route "
        "the call through the tool's existing chokepoint, or — for a genuinely "
        "new external tool — add a chokepoint module and an allowlist entry "
        f"with a reason comment in this file. Offenders: {unexpected}"
    )
    assert missing == [], (
        "Allowlist is stale: these modules no longer import subprocess. Remove "
        f"their entries so the allowlist stays meaningful: {missing}"
    )


def test_libcst_is_imported_only_by_the_structured_syntax_boundary() -> None:
    """``haute._python_syntax`` is the one module that uses LibCST (ENGQ-R03).

    The codegen structured-syntax boundary specification says that module hides
    LibCST behind small typed results; a second importer would make the
    specification, and the expression-parsing account of how codegen edits
    source, untrue.
    """
    importing = {
        _rel_posix(path)
        for path in _iter_src_haute_sources()
        if _imports_module(ast.parse(path.read_text(encoding="utf-8")), "libcst")
    }

    assert importing == {"src/haute/_python_syntax.py"}


def test_only_the_git_command_core_starts_git() -> None:
    """Every git subprocess goes through ``_git_core.py`` (DEP-R04).

    Only allowlisted modules may import ``subprocess``, so a git process can be
    launched elsewhere only by one of them building a git argument list. None
    of the other chokepoints (docker, npm, nvidia-smi, the installer) has a
    reason to; a consumer that needs git calls the core's helpers instead.
    """
    offenders: list[str] = []
    for rel in sorted(_SUBPROCESS_IMPORT_ALLOWLIST - {"src/haute/_git_core.py"}):
        tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.List, ast.Tuple))
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "git"
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], (
        "A git command is built outside the git command core; call the core's "
        f"_run_git / _run_git_ok / _run_git_rc instead. Offenders: {offenders}"
    )


def test_no_subprocess_backdoors_in_package() -> None:
    """``os.system`` / ``os.popen`` / ``pty`` are banned outright in src/haute/.

    They are the back door someone reaches for when the subprocess rule blocks
    a call.  Zero current uses; no allowlist.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in _iter_src_haute_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = _rel_posix(path)
        if _imports_module(tree, "pty"):
            offenders.append((rel, 0, "pty import"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in {"system", "popen"}
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ):
                offenders.append((rel, node.lineno, f"os.{node.attr}"))
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "os"
                and any(a.name in {"system", "popen"} for a in node.names)
            ):
                offenders.append((rel, node.lineno, "from os import system/popen"))

    assert offenders == [], (
        "os.system / os.popen / pty found in src/haute/. These bypass the "
        "subprocess chokepoint convention entirely (no arg-list safety, no "
        "encoding control) and are banned with no allowlist — use subprocess "
        f"via the tool's chokepoint module instead. Offenders: {offenders}"
    )


def _subprocess_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the subprocess module / its members in *tree*.

    Returns ``(module_aliases, member_names)``: local names referring to the
    module itself (``import subprocess [as sp]``) and local names referring
    to members (``from subprocess import run [as r]``).
    """
    module_aliases: set[str] = set()
    member_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                member_names.add(alias.asname or alias.name)
    return module_aliases, member_names


def test_text_mode_subprocess_calls_pin_utf8_in_caller_chokepoints() -> None:
    """Every text-mode subprocess call in the caller chokepoints pins utf-8.

    ``text=True`` (or ``universal_newlines=True``, or an ``encoding=`` kwarg)
    without ``encoding="utf-8"`` decodes tool output with the locale codepage
    — cp1252 on Windows — silently corrupting non-ASCII branch names, paths,
    and error messages.  Ruff cannot catch this (PLW1514 covers the ``open()``
    family only), so it is pinned here.  A stricter _git.py-local variant of
    this check lives in test_git_engine.py and stays there.
    """
    offenders: list[tuple[str, int, str]] = []
    for rel in _CALLER_CHOKEPOINTS:
        tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"))
        module_aliases, member_names = _subprocess_bindings(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_subprocess_call = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in module_aliases
            ) or (isinstance(func, ast.Name) and func.id in member_names)
            if not is_subprocess_call:
                continue

            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
            # Text mode = any of text= / universal_newlines= not statically
            # False/None, or any encoding= kwarg (encoding implies text mode).
            # Non-constant values are treated as text-mode, conservatively.
            text_mode = "encoding" in kwargs
            for key in ("text", "universal_newlines"):
                value = kwargs.get(key)
                if value is None:
                    continue
                if isinstance(value, ast.Constant) and value.value in (False, None):
                    continue
                text_mode = True
            if not text_mode:
                continue

            encoding = kwargs.get("encoding")
            if not (isinstance(encoding, ast.Constant) and encoding.value == "utf-8"):
                offenders.append((rel, node.lineno, ast.unparse(func)))

    assert offenders == [], (
        'Text-mode subprocess calls must pin encoding="utf-8". Without it the '
        "output decodes with the locale codepage (cp1252 on Windows), silently "
        f"corrupting non-ASCII tool output. Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Sanitizer-proliferation scan.
#
# Deriving a persisted name/key/filename from a user string by LOCAL string-
# mashing — instead of routing through a blessed sanitizer — creates its own
# tiny identity relation, usually coarser than the blessed one: a latent
# collision site (two labels converge on one artefact) and a latent drift
# site (its rules diverge as the blessed rules evolve).  There are exactly
# three sanctioned naming operations with deliberately different contracts;
# only the filesystem-label operation is cross-runtime:
#
#   executable identity:    src/haute/_graph_utils.py::_sanitize_func_name
#                           (backend only)
#   browser persistence:    frontend/src/utils/portableKey.ts
#   filesystem-label pair:  src/haute/_api_input_schema.py::
#                           sanitise_label_for_filesystem
#                           <-> frontend/src/utils/apiInputPorts.ts
#
# Two scans hold the line:
#
#   BIRTH-SCAN — name-mint shapes (replace-to-underscore, fold-then-replace,
#   character-class substitution) are allowed only in the blessed modules
#   plus an explicit reason-commented allowlist.  Catches a new ad-hoc
#   sanitizer the moment it is written, whatever it feeds.
#
#   SINK-SCAN — frontend files that build an interpolated persistence path
#   (a template literal ending .json/.parquet) must import a blessed
#   sanitizer.  This is the shape of the optimiser-preview specimen (a file
#   composing `output/<derived>.json` from a locally-mashed label).
#
# A site that VALIDATES-and-rejects invalid names rather than transforming
# them (e.g. routes/utility.py's _VALID_NAME) is fine and is not flagged:
# rejection cannot silently merge two labels.  Display-only formatting and
# search case-folds never match these shapes either.
# ---------------------------------------------------------------------------

_FRONTEND_SRC_PREFIX = "frontend/src/"

# The blessed sanitizer modules — the only places mint shapes live by right.
_BACKEND_BLESSED_SANITIZERS = {
    "src/haute/_graph_utils.py",  # _sanitize_func_name (identifier pair)
    "src/haute/_api_input_schema.py",  # sanitise_label_for_filesystem
}
_FRONTEND_BLESSED_SANITIZERS = {
    "frontend/src/utils/portableKey.ts",  # browser-owned persistence keys
    "frontend/src/utils/apiInputPorts.ts",  # sanitiseLabelForFilesystem twin
}

# Every non-blessed module allowed to contain a mint shape, with why.  A new
# entry needs the same justification review as a new subprocess chokepoint.
_BACKEND_MINT_ALLOWLIST = {
    # Git branch-name slug from a username; collisions are cosmetic and
    # _validate_ref_name guards injection.
    "src/haute/_git_core.py",
    # One-shot scaffold: project dir name -> package name at `haute init`;
    # single value, no collision space.
    "src/haute/cli/_init_cmd.py",
    # NOTE (not an entry): _scaffold.py's clean_columns mint lives inside the
    # starter-pipeline TEMPLATE STRING that `haute init` writes into the
    # user's project — string constants are invisible to the AST walk, and
    # scaffolded user code is outside this scan's contract anyway.
    # label_slug feeds only the default `version` STRING inside the artifact
    # payload (timestamp-salted); the on-disk path comes from the
    # user-supplied output_path, so no name it mints reaches persistence.
    "src/haute/routes/optimiser.py",
    # Secret-key comparison normalisation only: folds hyphens to underscores
    # before checking credential substrings. It neither mints nor persists a
    # filesystem or identifier name.
    "src/haute/_source_cache.py",
    # Local replace-to-underscore canonicalizes bounded metric display spellings
    # only; it does not mint or persist filesystem or identifier names.
    "src/haute/modelling/_tuning.py",
}
_FRONTEND_MINT_ALLOWLIST = {
    # Deliberate third sanitizer with distinct semantics (run-collapse
    # salting of dotted leaves; collisions handled actively by dedupName /
    # ambiguousNames).  Confined to ONE local helper, collapseToNameChars.
    "frontend/src/panels/editors/apiInputInherit.ts",
    # safeTestId mints data-testid attributes only — never persisted.
    "frontend/src/panels/explore/SchemaTableCard.tsx",
    # chartExportFileName mints a one-off browser-download filename only;
    # it never names a project artefact, and collisions are therefore cosmetic.
    "frontend/src/panels/explore/chartData.ts",
}


def _module_has_mint_shape(tree: ast.AST) -> bool:
    """True if the module contains a name-mint shape.

    Shapes (AST, so strings/comments cannot trip it):

    * ``<expr>.replace(<x>, "_")`` — replace-to-underscore, the fold-family
      mint (also catches ``.lower().replace(" ", "_")`` chains).
    * ``re.sub(pat, "_"|"-", s)`` / ``<compiled>.sub("_"|"-", s)`` —
      substitution collapsing a character class to a separator.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr == "replace" and len(node.args) >= 2:
            repl = node.args[1]
            if isinstance(repl, ast.Constant) and repl.value == "_":
                return True
        elif func.attr == "sub" and node.args:
            is_re_module = isinstance(func.value, ast.Name) and func.value.id == "re"
            repl = node.args[1] if is_re_module and len(node.args) >= 2 else node.args[0]
            if isinstance(repl, ast.Constant) and repl.value in ("_", "-"):
                return True
    return False


def _tracked_frontend_sources() -> list[str]:
    # Include sanctioned files explicitly so a newly-added implementation is
    # covered before its first commit; `git ls-files` does not report untracked
    # files in a developer worktree.
    candidates = set(_tracked_files()) | _FRONTEND_BLESSED_SANITIZERS | _FRONTEND_MINT_ALLOWLIST
    return sorted(
        rel
        for rel in candidates
        if rel.startswith(_FRONTEND_SRC_PREFIX)
        and rel.endswith((".ts", ".tsx"))
        and (_REPO_ROOT / rel).is_file()
        and not rel.endswith(".d.ts")
        and "__tests__" not in rel
        and "/testSupport/" not in rel  # vitest scaffolding, not product code
    )


# Text shapes for the frontend half (no TS AST is available under pytest;
# the shapes are narrow enough that a string-literal false positive would be
# an acceptable prompt to restructure).  House style is double quotes, which
# the replace-to-underscore pattern assumes.
_FRONTEND_MINT_RES = (
    re.compile(r"\.replace\(\s*/\[\^"),  # character-class substitution regex
    re.compile(r"toLowerCase\(\)\s*\.\s*replace\("),  # fold-then-mint
    re.compile(r'\.replace\([^)\n]*,\s*"_"\s*\)'),  # replace-to-underscore
    re.compile(r'\.push\(\s*"_"\s*\)'),  # character-wise separator mint
)


def test_backend_name_mints_confined_to_blessed_sanitizers_and_allowlist() -> None:
    minting = {
        _rel_posix(path)
        for path in _iter_src_haute_sources()
        if _module_has_mint_shape(ast.parse(path.read_text(encoding="utf-8")))
    }
    expected = _BACKEND_BLESSED_SANITIZERS | _BACKEND_MINT_ALLOWLIST

    unexpected = sorted(minting - expected)
    missing = sorted(expected - minting)
    assert unexpected == [], (
        "New name-mint shape (replace-to-underscore / sub-to-separator) outside "
        "the blessed sanitizers. Route the derivation through _sanitize_func_name "
        "or sanitise_label_for_filesystem, make the site validate-and-reject "
        "instead of transforming, or — if the local mint is genuinely deliberate "
        f"— add a reason-commented allowlist entry here. Offenders: {unexpected}"
    )
    assert missing == [], (
        "Allowlist/blessed set is stale: these modules no longer contain a mint "
        f"shape. Remove their entries so the scan stays meaningful: {missing}"
    )


def test_frontend_name_mints_confined_to_blessed_sanitizers_and_allowlist() -> None:
    minting = {
        rel
        for rel in _tracked_frontend_sources()
        if any(
            pattern.search((_REPO_ROOT / rel).read_text(encoding="utf-8"))
            for pattern in _FRONTEND_MINT_RES
        )
    }
    expected = _FRONTEND_BLESSED_SANITIZERS | _FRONTEND_MINT_ALLOWLIST

    unexpected = sorted(minting - expected)
    missing = sorted(expected - minting)
    assert unexpected == [], (
        "New frontend name-mint shape (char-class substitution / fold-then-"
        "replace / replace-to-underscore) outside the blessed sanitizers. Route "
        "the derivation through server-owned identity, portableKey, or the "
        "filesystem-label helper; "
        "validate-and-reject instead of transforming, or add a reason-commented "
        f"allowlist entry here. Offenders: {unexpected}"
    )
    assert missing == [], (
        "Allowlist/blessed set is stale: these files no longer contain a mint "
        f"shape. Remove their entries so the scan stays meaningful: {missing}"
    )


# Interpolated template literal ending in a persisted-artifact extension —
# the sink where a derived name reaches disk.
_FRONTEND_PERSIST_SINK_RE = re.compile(r"`[^`\n]*\$\{[^`\n]*\.(?:json|parquet)`")
_FRONTEND_BLESSED_IMPORT_RE = re.compile(r'from\s+"[^"\n]*utils/(?:portableKey|apiInputPorts)"')

# Frontend files allowed to build a persistence path WITHOUT importing a
# blessed sanitizer (e.g. every interpolated part is machine-derived, never
# a user label).  A new entry needs a reason comment.
_FRONTEND_PERSIST_SINK_ALLOWLIST: set[str] = {
    # `${filename}.json` names a user-initiated BROWSER DOWNLOAD of the
    # previewed document (downloadTextFile); nothing is persisted into the
    # project tree, so no project artefact can collide.
    "frontend/src/panels/editors/JsonPreview.tsx",
}


def test_frontend_persistence_path_builders_import_a_blessed_sanitizer() -> None:
    sinks = {
        rel
        for rel in _tracked_frontend_sources()
        if _FRONTEND_PERSIST_SINK_RE.search((_REPO_ROOT / rel).read_text(encoding="utf-8"))
    }
    # Non-vacuity pin: the optimiser artifact-path builder (the fixed
    # specimen of this class) must stay visible to the sink pattern.  If it
    # moves or the pattern rots, this fails rather than the scan silently
    # covering nothing.
    assert "frontend/src/panels/optimiser/optimiserHelpers.ts" in sinks, (
        "Sink pattern no longer matches the known persistence-path builder — "
        "the scan has gone vacuous; update _FRONTEND_PERSIST_SINK_RE (or this "
        "pin) to track the code."
    )

    offenders = sorted(
        rel
        for rel in sinks - _FRONTEND_PERSIST_SINK_ALLOWLIST
        if not _FRONTEND_BLESSED_IMPORT_RE.search((_REPO_ROOT / rel).read_text(encoding="utf-8"))
    )
    assert offenders == [], (
        "Frontend file builds an interpolated persistence path (template "
        "literal ending .json/.parquet) without importing a blessed sanitizer. "
        "Any user-derived part of a persisted filename must pass through "
        "portableKey or sanitiseLabelForFilesystem (the optimiser-preview bug "
        "class); if every interpolated part is machine-derived, add a "
        f"reason-commented allowlist entry. Offenders: {offenders}"
    )
