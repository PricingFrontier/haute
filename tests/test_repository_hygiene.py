from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path


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
    )

    assert offenders == []


def test_example_pipeline_config_lives_only_under_rating() -> None:
    tracked = _tracked_files()
    root_config = sorted(path for path in tracked if path == "config" or path.startswith("config/"))

    assert root_config == []
    assert any(path.startswith("rating/config/") for path in tracked)


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
# Scope is src/haute/ only: tests/, scripts/, review/, and hatch_build.py may
# shell out freely (that is their job; the build hook cannot even import the
# package's helpers because it runs before the package is installed).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_HAUTE = _REPO_ROOT / "src" / "haute"
_SCAN_SKIP_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

# The only modules in src/haute/ allowed to import subprocess, and why.
_SUBPROCESS_IMPORT_ALLOWLIST = {
    "src/haute/_git.py",  # git chokepoint (caller)
    "src/haute/_ram_estimate.py",  # nvidia-smi chokepoint (caller; function-local import)
    "src/haute/cli/_serve.py",  # npm chokepoint (caller, via _helpers._npm)
    "src/haute/deploy/_container.py",  # docker chokepoint (caller)
    # import-only: deliberate F401-suppressed patch-target — tests patch the
    # module attribute and assert this module never shells out.
    "src/haute/cli/_helpers.py",
    # import-only: sandbox denylist membership (_DANGEROUS_MODULE_OBJECTS);
    # never launches anything.
    "src/haute/executor.py",
}

# The chokepoints that actually launch subprocesses (the two import-only
# entries above never make calls, so they carry no text-mode call sites).
_CALLER_CHOKEPOINTS = (
    "src/haute/_git.py",
    "src/haute/_ram_estimate.py",
    "src/haute/cli/_serve.py",
    "src/haute/deploy/_container.py",
)


def _iter_src_haute_sources() -> list[Path]:
    return sorted(
        path
        for path in _SRC_HAUTE.rglob("*.py")
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
            text_mode = "encoding" in kwargs or any(
                key in kwargs
                and not (
                    isinstance(kwargs[key], ast.Constant)
                    and kwargs[key].value in (False, None)  # type: ignore[union-attr]
                )
                for key in ("text", "universal_newlines")
            )
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
