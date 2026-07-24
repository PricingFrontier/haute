from __future__ import annotations

import ast
import re
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
# Scope is src/haute/ only: tests/, scripts/, and hatch_build.py may
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
# two blessed sanitizer pairs, each frontend/backend twinned:
#
#   identifier pair:        src/haute/_graph_utils.py::_sanitize_func_name
#                           <-> frontend/src/utils/sanitizeName.ts
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
    "frontend/src/utils/sanitizeName.ts",  # identifier pair twin
    "frontend/src/utils/apiInputPorts.ts",  # sanitiseLabelForFilesystem twin
}

# Every non-blessed module allowed to contain a mint shape, with why.  A new
# entry needs the same justification review as a new subprocess chokepoint.
_BACKEND_MINT_ALLOWLIST = {
    # Git branch-name slug from a username; collisions are cosmetic and
    # _validate_ref_name guards injection.
    "src/haute/_git.py",
    # One-shot scaffold: project dir name -> package name at `haute init`;
    # single value, no collision space.
    "src/haute/cli/_init_cmd.py",
    # NOTE (not an entry): _scaffold.py's clean_columns mint lives inside the
    # starter-pipeline TEMPLATE STRING that `haute init` writes into the
    # user's project — string constants are invisible to the AST walk, and
    # scaffolded user code is outside this scan's contract anyway.
    # Keys rows from an external library's column headers, not user input.
    "src/haute/modelling/_rustystats.py",
    # label_slug feeds only the default `version` STRING inside the artifact
    # payload (timestamp-salted); the on-disk path comes from the
    # user-supplied output_path, so no name it mints reaches persistence.
    "src/haute/routes/optimiser.py",
    # Secret-key comparison normalisation only: folds hyphens to underscores
    # before checking credential substrings. It neither mints nor persists a
    # filesystem or identifier name.
    "src/haute/_source_cache.py",
}
_FRONTEND_MINT_ALLOWLIST = {
    # Deliberate third sanitizer with distinct semantics (run-collapse
    # salting of dotted leaves; collisions handled actively by dedupName /
    # ambiguousNames).  Confined to ONE local helper, collapseToNameChars.
    "frontend/src/panels/editors/apiInputInherit.ts",
    # safeTestId mints data-testid attributes only — never persisted.
    "frontend/src/panels/explore/SchemaTableCard.tsx",
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
    return sorted(
        rel
        for rel in _tracked_files()
        if rel.startswith(_FRONTEND_SRC_PREFIX)
        and rel.endswith((".ts", ".tsx"))
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
        "the derivation through sanitizeName or sanitiseLabelForFilesystem, "
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
_FRONTEND_BLESSED_IMPORT_RE = re.compile(r'from\s+"[^"\n]*utils/(?:sanitizeName|apiInputPorts)"')

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
        "sanitizeName or sanitiseLabelForFilesystem (the optimiser-preview bug "
        "class); if every interpolated part is machine-derived, add a "
        f"reason-commented allowlist entry. Offenders: {offenders}"
    )
