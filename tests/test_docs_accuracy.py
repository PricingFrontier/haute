"""Pin user-facing doc claims to the code they describe.

Docs stating machine-checkable facts (node-type counts, CI secret names,
scaffold paths) have drifted from the code before — a by-the-book setup
following the deployment guides failed its first deploy because the guides
named the wrong secrets. These tests make that class of drift fail CI.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._edge_join import _ALLOWED_HOW
from haute._scaffold import TARGETS, haute_toml
from haute._types import NodeType

ROOT = Path(__file__).resolve().parents[1]
MKDOCS_CONFIG = ROOT / "mkdocs.yml"
EXECUTION_STRATEGY_DOC = ROOT / "docs" / "building-models" / "execution-strategy.md"
EDGE_JOIN_GUIDE = ROOT / "docs" / "building-models" / "nodes" / "edge-join.md"
EDGE_JOIN_RUNTIME_SPEC = ROOT / "specs" / "json-shredding" / "low-level.md"
EDGE_JOIN_EDITOR_SPEC = ROOT / "specs" / "frontend-node-editors" / "low-level.md"
SPECS_README = ROOT / "specs" / "README.md"
PIPELINE_CONFIG_SPEC = ROOT / "specs" / "pipeline-config" / "low-level.md"
DEPLOYMENT_DOCS = sorted((ROOT / "docs" / "deployment").rglob("*.md"))
LOW_LEVEL_SPECS = tuple(sorted((ROOT / "specs").rglob("low-level.md")))
BACKEND_SOURCE_ROOT = ROOT / "src" / "haute"
FRONTEND_SOURCE_ROOT = ROOT / "frontend" / "src"
SPECS_ROOT = ROOT / "specs"
SPECS_OWNERSHIP = SPECS_ROOT / "ownership.toml"
ROADMAP_ROOT = ROOT / "specs" / "roadmap"
ROADMAP_INDEX = ROADMAP_ROOT / "README.md"

_MARKDOWN_CODE_SPAN = re.compile(r"(?<!`)`([^`\r\n]+)`(?!`)")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_MARKDOWN_FENCE = re.compile(r"```.*?```", flags=re.DOTALL)
_MODULE_MAP_HEADING = re.compile(r"^## Module map\s*$", flags=re.MULTILINE)
_LEVEL_TWO_HEADING = re.compile(r"^##(?!#)\s+\S.*$", flags=re.MULTILINE)
_MODULE_MAP_ROW = re.compile(r"^\s*\|\s*(.*?)\s*\|", flags=re.MULTILINE)
_FRONTEND_SOURCE_SUFFIXES = frozenset({".css", ".ts", ".tsx"})
_FRONTEND_TEST_ONLY_DIRS = frozenset({"__tests__", "test-utils", "testSupport"})
_BACKEND_BEHAVIOUR_ASSETS = frozenset(
    {
        "_polars_io_arguments.json",
        "assistant/assets/authoring_guide.md",
        "assistant/assets/examples/config/data_input/quotes.json",
        "assistant/assets/examples/config/data_input/regions.json",
        "assistant/assets/examples/config/quote_input/quote.json",
        "assistant/assets/examples/config/quote_response/joined_priced.json",
        "assistant/assets/examples/config/quote_response/linear_priced.json",
        "assistant/assets/examples/config/quote_response/response.json",
    }
)
# These files are deliberately outside behavioral component coverage: ``py.typed``
# is a distribution marker owned by the build/distribution spec, while ``static``
# and bytecode caches are generated outputs rather than source-of-truth modules.
_BACKEND_COVERAGE_EXCLUDED_FILES = frozenset({"py.typed"})
_BACKEND_COVERAGE_EXCLUDED_DIRS = frozenset({"__pycache__", "static"})
_GENERATED_REFERENCE_PREFIXES = ("src/haute/static",)

_FRONTEND_OPERATIONAL_FILES = (
    ".npmrc",
    "README.md",
    "bun.lock",
    "eslint.config.js",
    "index.html",
    "package-lock.json",
    "package.json",
    "playwright.config.ts",
    "tsconfig.app.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "vitest.config.ts",
)
_REPOSITORY_PATH_PREFIXES = (
    ".github/",
    "docs/",
    "frontend/",
    "mutation/",
    "rating/",
    "scripts/",
    "specs/",
    "src/",
    "tests/",
    "security/",
    "repro/",
)
_REQUIRED_HIGH_LEVEL_HEADINGS = (
    "## Purpose",
    "## Scope",
    "## Behaviour",
    "## Design rationale",
    "## Interactions",
    "## Failure model",
)
_REQUIRED_LOW_LEVEL_HEADINGS = (
    "## Module map",
    "## Key types and data structures",
    "## Control flow",
    "## Edge cases and invariants",
    "## Error handling",
    "## Testing",
)
_REQUIRED_COMPONENT_ROADMAP_HEADINGS = (
    "## Scope",
    "## Priorities",
    "## Planned improvements",
)
_EXPECTED_COMPONENT_ROADMAPS = (
    "assistant",
    "background-jobs-api",
    "caching",
    "deploy-platform",
    "engineering-quality",
    "execution-engine",
    "explore-eda",
    "frontend-canvas",
    "git-integration",
    "io-layer",
    "modelling",
    "optimiser",
    "pipeline-authoring",
    "rating",
    "security-supply-chain",
    "specs-corpus",
    "tracing-explainability",
)
_COMPONENT_PACKAGE_HEADING = re.compile(
    r"^###\s+([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\b",
    flags=re.MULTILINE,
)
_REQUIRED_PACKAGE_FIELDS = (
    "**Why:**",
    "**Plan:**",
    "**Acceptance:**",
    "**Dependencies:**",
    "**Evidence:**",
)
_DOCS_ACCURACY_BASELINE = ROOT / "tests" / "docs_accuracy_baseline.txt"
_TEMPORARY_CONTRACT_PREFIXES = (
    "Approved change contract",
    "Approved reproducible execution-evidence contract",
    "Polars backend contracts",
)
_LEGACY_CONTRACT_PREFIX = "Polars backend contracts"
_FUTURE_ACTION = re.compile(
    r"\b(?:add|create|introduce|move|moves|moved)\b",
    flags=re.IGNORECASE,
)
_TARGET_LABEL = re.compile(r"\bTarget behavio(?:u)?r\b", flags=re.IGNORECASE)
_ACCEPTANCE_LABEL = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?Acceptance(?: evidence)?\b",
    flags=re.IGNORECASE | re.MULTILINE,
)
_TEST_PATH = re.compile(r"(?:^|/)(?:test_[^/]+[.]py|[^/]+[.](?:test|spec)[.](?:ts|tsx))$")
_TEST_COUNT_CLAIM = re.compile(
    r"`(?P<path>(?:tests/)?test_[^`\s]+[.]py)`\s+\((?P<count>\d+)\s+tests?\)"
)
_DELIVERED_ROADMAP_STATE = re.compile(
    r"^(?:Complete(?:d)?|Implemented|Verified|Audited|Delivered)\b",
    flags=re.IGNORECASE,
)
_DEFERRED_ROADMAP_STATE = re.compile(r"^Deferred\b", flags=re.IGNORECASE)
_TRACKED_WORK_POINTER = re.compile(
    r"\b(?:remaining|completed)\b.{0,240}\b(?:work|improvements?|packages?)\b",
    flags=re.IGNORECASE | re.DOTALL,
)

DATABRICKS_SECRET_DOCS = [
    ROOT / "docs" / "deployment" / "targets" / "databricks.md",
    ROOT / "docs" / "deployment" / "ci" / "github-actions.md",
    ROOT / "docs" / "deployment" / "ci" / "gitlab.md",
    ROOT / "docs" / "deployment" / "ci" / "azure-devops.md",
]


def test_execution_strategy_guide_is_in_public_navigation_and_states_key_contracts() -> None:
    nav = MKDOCS_CONFIG.read_text(encoding="utf-8")
    guide = EXECUTION_STRATEGY_DOC.read_text(encoding="utf-8")

    assert "Execution Strategy: building-models/execution-strategy.md" in nav
    for claim in (
        "Schema all-except",
        "Streaming boundary",
        "Materialisation boundary",
        "Haute never generically chunks a group-by.",
        "`preview_eager` or `deploy_live`",
        "unavailable or `null`",
    ):
        assert claim in guide


def test_edge_join_guide_matches_runtime_and_canvas_contract() -> None:
    guide = EDGE_JOIN_GUIDE.read_text(encoding="utf-8")
    normalised_guide = " ".join(guide.replace("**", "").split())

    supported_modes = re.search(r"Supported join types are (.+?)\.", normalised_guide)
    assert supported_modes is not None
    documented_modes = set(_MARKDOWN_CODE_SPAN.findall(supported_modes.group(1)))
    assert documented_modes == set(_ALLOWED_HOW)

    for claim in (
        "dragging a connection onto an existing edge",
        "connecting the output of one node to the output of another node",
        "base input on the left",
        "join input above or below",
        "output on the right",
        "both the top and bottom join-handle candidates are available",
        "Cross joins do not use keys",
        "`on`, `leftOn`, and `rightOn` must all be absent",
        '"on": ["quote_id"]',
        '"leftOn": ["quote_id"]',
        '"rightOn": ["id"]',
    ):
        assert claim in normalised_guide
    assert "palette" not in guide.casefold()

    for spec_path in (EDGE_JOIN_RUNTIME_SPEC, EDGE_JOIN_EDITOR_SPEC):
        spec = spec_path.read_text(encoding="utf-8")
        for mode in _ALLOWED_HOW:
            assert f"`{mode}`" in spec


def test_internal_engineering_docs_are_excluded_from_public_mkdocs_site() -> None:
    config = MKDOCS_CONFIG.read_text(encoding="utf-8")
    exclude_block = config.split("exclude_docs: |", maxsplit=1)[1].split("\ndev_addr:", maxsplit=1)[
        0
    ]

    assert "  trip/\n" in exclude_block
    for internal_file in (
        "CI_MIRROR.md",
        "COMMIT_STANDARDS.md",
        "PERFORMANCE_CHECKS.md",
    ):
        assert f"  {internal_file}\n" in exclude_block
    assert "\n  - Roadmap:" not in config


def test_component_roadmaps_are_flat_complete_and_self_contained() -> None:
    expected_markdown = {"README.md"} | {
        f"{component}.md" for component in _EXPECTED_COMPONENT_ROADMAPS
    }
    roadmap_markdown = {
        path.relative_to(ROADMAP_ROOT).as_posix() for path in ROADMAP_ROOT.rglob("*.md")
    }
    assert roadmap_markdown == expected_markdown

    component_files = {
        path.stem: path for path in ROADMAP_ROOT.glob("*.md") if path.name != ROADMAP_INDEX.name
    }
    assert tuple(sorted(component_files)) == _EXPECTED_COMPONENT_ROADMAPS

    index = ROADMAP_INDEX.read_text(encoding="utf-8")
    start_with: dict[str, str] = {}
    for line in index.splitlines():
        match = re.match(
            r"^\|\s*\[[^\]]+\]\(([^)#]+)[^)]*\)\s*\|.*\|\s*([^|]+?)\s*\|$",
            line,
        )
        if match:
            start_with[Path(match.group(1)).stem] = match.group(2).strip().strip("`")

    package_owners: dict[str, str] = {}
    for component, path in component_files.items():
        text = path.read_text(encoding="utf-8")
        headings = _h2_sections(text)
        for heading in _REQUIRED_COMPONENT_ROADMAP_HEADINGS:
            assert heading[3:] in headings, f"{path.relative_to(ROOT)} is missing {heading}"
        assert f"({component}.md)" in index

        package_ids = _COMPONENT_PACKAGE_HEADING.findall(text)
        priorities = headings["Priorities"]
        priority_rows = (
            _markdown_table_records(
                priorities,
                required_columns=("package", "state", "priority", "outcome"),
                context=path.relative_to(ROOT).as_posix(),
            )
            if _module_map_rows(priorities)
            else []
        )
        delivered_rows = [
            row for row in priority_rows if _DELIVERED_ROADMAP_STATE.match(row["state"])
        ]
        assert not delivered_rows, (
            f"{path.relative_to(ROOT)} retains delivered package priorities: {delivered_rows}"
        )
        all_packages_deferred = (
            bool(package_ids)
            and bool(priority_rows)
            and all(_DEFERRED_ROADMAP_STATE.match(row["state"]) for row in priority_rows)
        )
        if not package_ids:
            assert "There are no active" in text, (
                f"{path.relative_to(ROOT)} has no work packages without declaring that state"
            )
        if not package_ids or all_packages_deferred:
            assert start_with.get(component) == "—", (
                f"{component} has no non-deferred package, so its Start with cell must be —"
            )
        else:
            assert start_with.get(component) in package_ids, (
                f"{component} Start with must name a non-deferred package; "
                f"got {start_with.get(component)!r}, expected one of {package_ids}"
            )
            start_row = next(
                (
                    row
                    for row in priority_rows
                    if _roadmap_package_cell_contains(row["package"], start_with[component])
                ),
                None,
            )
            assert start_row is not None, (
                f"{component} Start with package {start_with[component]!r} is absent "
                "from the Priorities table"
            )
            assert not _DEFERRED_ROADMAP_STATE.match(start_row["state"]), (
                f"{component} Start with package {start_with[component]!r} is Deferred"
            )
        for package_id in package_ids:
            assert package_id not in package_owners, (
                f"{package_id} is owned by both {package_owners[package_id]} and {component}"
            )
            package_owners[package_id] = component
            package_text = text.split(f"### {package_id}", maxsplit=1)[1].split(
                "\n### ", maxsplit=1
            )[0]
            for field in _REQUIRED_PACKAGE_FIELDS:
                assert field in package_text, (
                    f"{path.relative_to(ROOT)} package {package_id} is missing {field}"
                )

        for target in _MARKDOWN_LINK.findall(text):
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            local_target = target.split("#", maxsplit=1)[0]
            assert (path.parent / local_target).resolve().exists(), (
                f"{path.relative_to(ROOT)} links to missing {local_target}"
            )

    for retired_root in (
        ROOT / "docs" / "fable-Review",
        ROOT / "docs" / "review",
        ROOT / "specs" / "roadmap" / "components",
        ROOT / "docs" / "trip" / "code-review",
        ROOT / "docs" / "trip" / "plans",
    ):
        assert not retired_root.exists(), (
            f"retired planning/review directory still exists: {retired_root.relative_to(ROOT)}"
        )

    stray_planning_markdown: list[str] = []
    forbidden_directory_names = {
        "code-review",
        "fable-review",
        "plan",
        "plans",
        "review",
        "reviews",
        "roadmap",
        "roadmaps",
    }
    for path in _tracked_repo_files():
        if not path.exists() or path.suffix.casefold() != ".md":
            continue
        relative = path.relative_to(ROOT)
        relative_posix = relative.as_posix()
        if relative_posix.startswith("specs/roadmap/"):
            continue
        name = path.name.casefold()
        if (
            any(part.casefold() in forbidden_directory_names for part in relative.parts)
            or name.endswith(".plan.md")
            or "roadmap" in name
            or "remediation" in name
        ):
            stray_planning_markdown.append(relative_posix)
    assert not stray_planning_markdown, (
        "review/roadmap/remediation-plan Markdown must live only in specs/roadmap: "
        f"{sorted(stray_planning_markdown)}"
    )

    forbidden_references = (
        "fable-Review",
        "docs/review",
        "roadmap/components",
        "trip/plans",
        "trip/code-review",
        "REMEDIATION-",
    )
    combined_roadmap = "\n".join(
        path.read_text(encoding="utf-8") for path in component_files.values()
    )
    for forbidden in forbidden_references:
        assert forbidden not in combined_roadmap


def _normalise_doc_reference(value: str) -> str:
    """Return the file portion of an inline-code reference in repo path form."""
    reference = value.strip().replace("\\", "/")
    if reference.startswith("./"):
        reference = reference[2:]
    # A symbol-qualified path still explicitly identifies its source file. Keep
    # the delimiter strict so prose such as ``foo.py handles ...`` cannot match.
    return reference.split("::", maxsplit=1)[0]


def _module_map_text(spec: Path) -> str:
    text = spec.read_text(encoding="utf-8")
    module_map = _MODULE_MAP_HEADING.search(text)
    assert module_map is not None, f"{spec.relative_to(ROOT)} has no ## Module map section"
    next_heading = _LEVEL_TWO_HEADING.search(text, module_map.end())
    end = next_heading.start() if next_heading is not None else len(text)
    return text[module_map.end() : end]


def _low_level_spec_references() -> set[str]:
    references: set[str] = set()
    for spec in LOW_LEVEL_SPECS:
        references.update(_module_map_file_references(_module_map_text(spec)))
    return references


def _component_module_map_file_references() -> dict[str, set[str]]:
    """Return exact, tracked files by their low-level spec's component name."""
    tracked_references = {
        path.relative_to(ROOT).as_posix() for path in _tracked_repo_files() if path.is_file()
    }
    return {
        spec.parent.name: _module_map_file_references(_module_map_text(spec)) & tracked_references
        for spec in LOW_LEVEL_SPECS
    }


def _shared_file_components(
    component_references: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    """Keep the component multiplicity needed to assign a primary owner."""
    owners: dict[str, set[str]] = {}
    for component, references in component_references.items():
        for reference in references:
            owners.setdefault(reference, set()).add(component)
    return {path: components for path, components in owners.items() if len(components) > 1}


def _module_map_file_references(module_map: str) -> set[str]:
    """Read inline-code references only from each module-map table's file cell."""
    references: set[str] = set()
    for row in _MODULE_MAP_ROW.finditer(module_map):
        file_cell = row.group(1)
        references.update(
            _normalise_doc_reference(match.group(1))
            for match in _MARKDOWN_CODE_SPAN.finditer(file_cell)
        )
    return references


@cache
def _tracked_repo_files() -> frozenset[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return frozenset(ROOT / relative for relative in result.stdout.split("\0") if relative)


@dataclass(frozen=True, order=True)
class DocViolation:
    """A stable, reviewable documentation-contract failure."""

    document: str
    rule: str
    detail: str

    def tsv(self) -> str:
        return "\t".join((self.document, self.rule, self.detail))


@dataclass(frozen=True)
class RepoInventory:
    """Tracked repository paths indexed once for repeated documentation lookups."""

    root: Path
    files: frozenset[Path]
    file_names: frozenset[str]
    paths_by_name: Mapping[str, Path]
    files_by_suffix: Mapping[str, tuple[Path, ...]]
    directories_by_suffix: Mapping[str, tuple[Path, ...]]

    @classmethod
    def build(cls, root: Path, files: set[Path] | frozenset[Path]) -> RepoInventory:
        tracked = frozenset(files)
        file_paths = {path.relative_to(root).as_posix(): path for path in tracked}
        directory_paths: dict[str, Path] = {}
        for path in tracked:
            for parent in path.parents:
                if parent == root:
                    break
                directory_paths[parent.relative_to(root).as_posix()] = parent

        def suffix_index(paths: Mapping[str, Path]) -> dict[str, tuple[Path, ...]]:
            indexed: dict[str, list[Path]] = {}
            for relative, path in paths.items():
                parts = relative.split("/")
                for start in range(len(parts)):
                    indexed.setdefault("/".join(parts[start:]), []).append(path)
            return {suffix: tuple(sorted(matches)) for suffix, matches in indexed.items()}

        return cls(
            root=root,
            files=tracked,
            file_names=frozenset(file_paths),
            paths_by_name={**directory_paths, **file_paths},
            files_by_suffix=suffix_index(file_paths),
            directories_by_suffix=suffix_index(directory_paths),
        )


def _without_fences(text: str) -> str:
    return _MARKDOWN_FENCE.sub("", text)


def _h2_sections(text: str) -> dict[str, str]:
    """Extract exact level-two headings (not substring matches)."""
    headings = list(re.finditer(r"^##(?!#)\s+(.+?)\s*$", text, flags=re.MULTILINE))
    return {
        match.group(1): text[
            match.end() : (headings[index + 1].start() if index + 1 < len(headings) else len(text))
        ]
        for index, match in enumerate(headings)
    }


def _repo_files(root: Path) -> set[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {path for item in result.stdout.split("\0") if item and (path := root / item).is_file()}


def _strip_path_suffix(reference: str) -> tuple[str, str | None]:
    value = reference.strip().replace("\\", "/")
    if value.startswith("./"):
        value = value[2:]
    path, separator, symbol = value.partition("::")
    path = re.sub(r":\d+(?:-\d+)?$", "", path)
    path = path.split("#", maxsplit=1)[0]
    return path.rstrip("/"), symbol if separator else None


def _is_placeholder(reference: str) -> bool:
    return any(marker in reference for marker in ("<", ">", "{", "}"))


def _looks_like_repo_reference(
    value: str,
    root_files: set[str] | frozenset[str],
    file_suffixes: set[str] | frozenset[str] | None = None,
) -> bool:
    path, symbol = _strip_path_suffix(value)
    if not path or path.startswith("/") or _is_placeholder(path) or re.search(r"\s", path):
        return False
    if any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in _GENERATED_REFERENCE_PREFIXES
    ):
        return False
    return (
        path in root_files
        or path.startswith(_REPOSITORY_PATH_PREFIXES)
        or (
            path in file_suffixes
            if file_suffixes is not None
            else any(item.endswith(f"/{path}") for item in root_files)
        )
        or (
            symbol is not None
            and bool(re.search(r"[.](?:css|js|json|md|mjs|py|toml|ts|tsx|yml|yaml)$", path))
        )
    )


def _repo_reference_candidates(
    reference: str,
    root: Path,
    files: set[Path] | frozenset[Path],
    *,
    inventory: RepoInventory | None = None,
) -> tuple[tuple[Path, ...], str | None]:
    path, symbol = _strip_path_suffix(reference)
    if not path or path.startswith("/") or _is_placeholder(path):
        return (), symbol
    inventory = inventory or RepoInventory.build(root, files)
    if any(character in path for character in "*?["):
        candidates = {
            item
            for relative, item in inventory.paths_by_name.items()
            if fnmatch.fnmatch(relative, path)
            or ("/" not in path and fnmatch.fnmatch(item.name, path))
        }
    else:
        exact = inventory.paths_by_name.get(path)
        if exact is not None:
            candidates = {exact}
        else:
            candidates = set(inventory.files_by_suffix.get(path, ()))
            if not candidates:
                candidates = set(inventory.directories_by_suffix.get(path, ()))
    return tuple(sorted(candidates)), symbol


@cache
def _python_symbol_names(path: Path) -> frozenset[str]:
    if path.suffix == ".py":
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return frozenset()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Import):
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(
                    alias.asname or alias.name for alias in node.names if alias.name != "*"
                )
            elif isinstance(node, ast.arg):
                names.add(node.arg)

            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
        return frozenset(names)
    return frozenset()


def _file_has_symbol(path: Path, symbol: str) -> bool:
    if not symbol or not path.is_file():
        return bool(not symbol)
    normalised = symbol.replace("::", ".").removesuffix("()")
    name = normalised.split(".")[-1]
    if path.suffix == ".py":
        names = _python_symbol_names(path)
        if normalised in names or name in names:
            return True
        if "." not in normalised:
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        return bool(
            re.search(
                rf"(?<![\w$]){re.escape(normalised)}(?![\w$])",
                text,
            )
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text))


def _reference_violation(
    document: str,
    reference: str,
    root: Path,
    files: set[Path] | frozenset[Path],
    *,
    inventory: RepoInventory | None = None,
) -> DocViolation | None:
    candidates, symbol = _repo_reference_candidates(
        reference,
        root,
        files,
        inventory=inventory,
    )
    has_glob = any(character in _strip_path_suffix(reference)[0] for character in "*?[")
    if not candidates:
        return DocViolation(document, "missing-repo-reference", reference)
    if len(candidates) > 1 and not has_glob:
        matches = ", ".join(path.relative_to(root).as_posix() for path in candidates)
        return DocViolation(document, "ambiguous-repo-reference", f"{reference} -> {matches}")
    if symbol and (len(candidates) != 1 or not _file_has_symbol(candidates[0], symbol)):
        return DocViolation(document, "missing-symbol", reference)
    return None


def _slug(heading: str) -> str:
    plain = re.sub(r"[`*_~]", "", heading).strip().casefold()
    plain = re.sub(r"[^\w\s-]", "", plain)
    return re.sub(r"\s", "-", plain)


def _is_bare_symbol(value: str) -> bool:
    candidate = value.removesuffix("()")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", candidate):
        return False
    if re.search(r"[.](?:css|js|json|md|mjs|py|toml|ts|tsx|yml|yaml)$", candidate):
        return False
    return "_" in candidate or "." in candidate or candidate[:1].isupper() or value.endswith("()")


def _module_map_rows(section: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.lstrip().startswith("|") and "---" not in line
    ]


def _markdown_table_records(
    section: str,
    *,
    required_columns: tuple[str, ...],
    context: str,
) -> list[dict[str, str]]:
    rows = _module_map_rows(section)
    assert rows, f"{context} has no Markdown table"
    headers = [re.sub(r"\s+", " ", cell).strip().casefold() for cell in rows[0]]
    assert len(headers) == len(set(headers)), f"{context} has duplicate table columns: {headers}"
    assert set(headers) == set(required_columns), (
        f"{context} table columns must be {list(required_columns)}, got {headers}"
    )

    records: list[dict[str, str]] = []
    for row in rows[1:]:
        assert len(row) == len(headers), (
            f"{context} table row has {len(row)} cells for {len(headers)} columns: {row}"
        )
        records.append(dict(zip(headers, row, strict=True)))
    return records


def _roadmap_package_cell_contains(cell: str, package_id: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Z0-9]){re.escape(package_id)}(?![A-Z0-9])",
            cell.replace("`", ""),
        )
        is not None
    )


def test_markdown_table_records_address_columns_by_header() -> None:
    records = _markdown_table_records(
        """
| State | Outcome | Package | Priority |
|---|---|---|---|
| Deferred | Wait for persistence | `ROAD-WORKER-04` | P1 |
""",
        required_columns=("package", "state", "priority", "outcome"),
        context="reordered table",
    )

    assert records == [
        {
            "state": "Deferred",
            "outcome": "Wait for persistence",
            "package": "`ROAD-WORKER-04`",
            "priority": "P1",
        }
    ]


def _is_test_reference(value: str) -> bool:
    path, _ = _strip_path_suffix(value)
    if re.search(r"\s", path):
        return False
    return bool(_TEST_PATH.search(path)) or "/__tests__/" in f"/{path}" or path.startswith("tests/")


def _is_frontend_test_reference(reference: str, candidates: tuple[Path, ...], root: Path) -> bool:
    path, _ = _strip_path_suffix(reference)
    return (
        path.startswith("frontend/")
        or bool(re.search(r"[.](?:test|spec)[.](?:ts|tsx)$", path))
        or any(candidate.is_relative_to(root / "frontend") for candidate in candidates)
    )


def _is_temporary_contract_heading(heading: str) -> bool:
    return heading.startswith(_TEMPORARY_CONTRACT_PREFIXES)


def _contract_sections(sections: Mapping[str, str]) -> list[tuple[str, str]]:
    return [
        (heading, body)
        for heading, body in sections.items()
        if _is_temporary_contract_heading(heading)
    ]


def _symbol_delivery_evidence(
    block: str,
    *,
    document: str,
    root: Path,
    files: set[Path],
    file_names: set[str],
    file_suffixes: set[str],
    inventory: RepoInventory,
    tests_only: bool = False,
) -> tuple[str, ...]:
    references = [
        item
        for item in _MARKDOWN_CODE_SPAN.findall(block)
        if _looks_like_repo_reference(item, file_names, file_suffixes)
        and _strip_path_suffix(item)[1]
        and (not tests_only or _is_test_reference(item))
    ]
    if not references or any(
        _reference_violation(
            document,
            item,
            root,
            files,
            inventory=inventory,
        )
        is not None
        for item in references
    ):
        return ()
    return tuple(dict.fromkeys(references))


def _roadmap_evidence_blocks(text: str) -> list[str]:
    return re.findall(
        r"(?ms)^\*\*Evidence:\*\*\s*(.*?)(?=^\s*$|^###\s|\Z)",
        text,
    )


def _docs_violations(
    root: Path = ROOT,
    specs_root: Path | None = None,
    *,
    repo_files: set[Path] | None = None,
) -> list[DocViolation]:
    specs_root = specs_root or root / "specs"
    files = _repo_files(root) if repo_files is None else set(repo_files)
    inventory = RepoInventory.build(root, files)
    file_names = set(inventory.file_names)
    file_suffixes = set(inventory.files_by_suffix)
    violations: set[DocViolation] = set()
    referenced_tests: set[str] = set()
    roadmap_root = root / "specs" / "roadmap"

    for document in sorted(specs_root.rglob("*.md")):
        if roadmap_root in document.parents:
            continue
        relative = document.relative_to(root).as_posix()
        text = _without_fences(document.read_text(encoding="utf-8"))
        sections = _h2_sections(text)
        expected = (
            _REQUIRED_LOW_LEVEL_HEADINGS
            if document.name == "low-level.md"
            else _REQUIRED_HIGH_LEVEL_HEADINGS
            if document.name == "high-level.md"
            else ()
        )
        for heading in expected:
            if heading[3:] not in sections:
                violations.add(DocViolation(relative, "missing-required-heading", heading))

        for reference in _MARKDOWN_CODE_SPAN.findall(text):
            if not _looks_like_repo_reference(reference, file_names, file_suffixes):
                continue
            violation = _reference_violation(
                relative,
                reference,
                root,
                files,
                inventory=inventory,
            )
            if violation:
                violations.add(violation)

        for target in _MARKDOWN_LINK.findall(text):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            target_path, hash_mark, anchor = target.partition("#")
            linked = document if not target_path else (document.parent / target_path).resolve()
            if not linked.exists():
                violations.add(DocViolation(relative, "broken-link", target))
            elif hash_mark and linked.is_file():
                slugs = {
                    _slug(match.group(1))
                    for match in re.finditer(
                        r"^#{1,6}\s+(.+?)\s*$",
                        linked.read_text(encoding="utf-8"),
                        re.MULTILINE,
                    )
                }
                if anchor not in slugs:
                    violations.add(DocViolation(relative, "broken-link-anchor", target))

        for paragraph in re.split(r"\n\s*\n", text):
            if not _TRACKED_WORK_POINTER.search(paragraph):
                continue
            for target in _MARKDOWN_LINK.findall(paragraph):
                target_path = target.partition("#")[0]
                if not target_path:
                    continue
                linked = (document.parent / target_path).resolve()
                if not linked.is_file() or linked.parent != roadmap_root:
                    continue
                linked_text = linked.read_text(encoding="utf-8")
                if "There are no active" in linked_text and not _COMPONENT_PACKAGE_HEADING.search(
                    linked_text
                ):
                    violations.add(DocViolation(relative, "empty-roadmap-pointer", target))

        for heading, contract in _contract_sections(sections):
            if heading.startswith(_LEGACY_CONTRACT_PREFIX):
                violations.add(DocViolation(relative, "legacy-contract-section", heading))
            for sentence in re.split(r"(?<=[.!?])\s+|\n(?=\s*[-*])", contract):
                refs = [
                    item
                    for item in _MARKDOWN_CODE_SPAN.findall(sentence)
                    if _looks_like_repo_reference(item, file_names, file_suffixes)
                ]
                delivery_refs = _symbol_delivery_evidence(
                    sentence,
                    document=relative,
                    root=root,
                    files=files,
                    file_names=file_names,
                    file_suffixes=file_suffixes,
                    inventory=inventory,
                )
                if refs and len(delivery_refs) == len(refs) and _FUTURE_ACTION.search(sentence):
                    violations.add(
                        DocViolation(
                            relative,
                            "shipped-contract-action",
                            f"{heading}: {', '.join(delivery_refs)}",
                        )
                    )

            contract_blocks = re.split(
                r"\n\s*\n|(?=^\s*[-*]\s)",
                contract,
                flags=re.MULTILINE,
            )
            acceptance_evidence = tuple(
                dict.fromkeys(
                    reference
                    for block in contract_blocks
                    if _ACCEPTANCE_LABEL.search(block)
                    for reference in _symbol_delivery_evidence(
                        block,
                        document=relative,
                        root=root,
                        files=files,
                        file_names=file_names,
                        file_suffixes=file_suffixes,
                        inventory=inventory,
                        tests_only=True,
                    )
                )
            )
            for target_block in contract_blocks:
                if not _TARGET_LABEL.search(target_block):
                    continue
                refs = [
                    item
                    for item in _MARKDOWN_CODE_SPAN.findall(target_block)
                    if _looks_like_repo_reference(item, file_names, file_suffixes)
                ]
                target_evidence = _symbol_delivery_evidence(
                    target_block,
                    document=relative,
                    root=root,
                    files=files,
                    file_names=file_names,
                    file_suffixes=file_suffixes,
                    inventory=inventory,
                )
                evidence = (
                    target_evidence
                    if refs and len(target_evidence) == len(refs)
                    else acceptance_evidence
                )
                if evidence:
                    violations.add(
                        DocViolation(
                            relative,
                            "contract-target-present",
                            f"{heading}: {', '.join(evidence)}",
                        )
                    )

        if document.name == "low-level.md":
            module_map = sections.get("Module map", "")
            for row in _module_map_rows(module_map):
                if len(row) < 2 or row[0].casefold() == "file":
                    continue
                path_references = _MARKDOWN_CODE_SPAN.findall(row[0])
                paths = [
                    candidates[0]
                    for reference in path_references
                    if len(
                        candidates := _repo_reference_candidates(
                            reference,
                            root,
                            files,
                            inventory=inventory,
                        )[0]
                    )
                    == 1
                ]
                for cell in row[1:]:
                    for symbol in _MARKDOWN_CODE_SPAN.findall(cell):
                        if (
                            _is_bare_symbol(symbol)
                            and paths
                            and not any(_file_has_symbol(path, symbol) for path in paths)
                        ):
                            violations.add(
                                DocViolation(relative, "missing-module-map-symbol", symbol)
                            )

            testing = sections.get("Testing", "")
            for reference in _MARKDOWN_CODE_SPAN.findall(testing):
                if not _is_test_reference(reference):
                    continue
                path, _ = _strip_path_suffix(reference)
                candidates, _ = _repo_reference_candidates(
                    reference,
                    root,
                    files,
                    inventory=inventory,
                )
                if _is_frontend_test_reference(reference, candidates, root) and not path.startswith(
                    "frontend/"
                ):
                    violations.add(
                        DocViolation(
                            relative,
                            "non-root-relative-test-reference",
                            reference,
                        )
                    )
                violation = _reference_violation(
                    relative,
                    reference,
                    root,
                    files,
                    inventory=inventory,
                )
                referenced_tests.update(
                    candidate.relative_to(root).as_posix()
                    for candidate in candidates
                    if candidate.is_file()
                )
                if violation:
                    rule = (
                        "missing-test-symbol"
                        if violation.rule == "missing-symbol"
                        else "missing-test-reference"
                    )
                    violations.add(DocViolation(relative, rule, violation.detail))

    for test in files:
        rel = test.relative_to(root).as_posix()
        if re.fullmatch(r"tests(?:/.+)?/test_[^/]+\.py", rel) and rel not in referenced_tests:
            violations.add(
                DocViolation(
                    rel,
                    "unreferenced-test",
                    "not named in any low-level Testing section",
                )
            )

    for document in sorted(roadmap_root.glob("*.md")):
        relative = document.relative_to(root).as_posix()
        text = _without_fences(document.read_text(encoding="utf-8"))
        for evidence in _roadmap_evidence_blocks(text):
            for reference in _MARKDOWN_CODE_SPAN.findall(evidence):
                if not _looks_like_repo_reference(reference, file_names, file_suffixes):
                    continue
                violation = _reference_violation(
                    relative,
                    reference,
                    root,
                    files,
                    inventory=inventory,
                )
                if violation:
                    violations.add(
                        DocViolation(
                            relative,
                            f"roadmap-evidence-{violation.rule}",
                            violation.detail,
                        )
                    )
    return sorted(violations)


def _read_baseline(path: Path) -> set[DocViolation]:
    entries: list[DocViolation] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        assert len(fields) == 3, f"{path}:{line_number} must contain exactly three TSV fields"
        entries.append(DocViolation(*fields))
    assert entries == sorted(set(entries)), (
        f"{path.relative_to(ROOT)} entries must be sorted and unique for deterministic review"
    )
    return set(entries)


def _explicit_documented_repo_paths() -> set[str]:
    references = _low_level_spec_references()
    root_files = {
        path.relative_to(ROOT).as_posix() for path in _tracked_repo_files() if path.parent == ROOT
    }
    return {
        reference.rstrip("/")
        for reference in references
        if (reference in root_files or reference.startswith(_REPOSITORY_PATH_PREFIXES))
        and not any(marker in reference for marker in ("*", "<", ">", "{"))
        and not re.search(r"\s", reference)
    }


def _unreferenced_sources(paths: list[Path]) -> list[str]:
    references = _low_level_spec_references()
    return [
        path.relative_to(ROOT).as_posix()
        for path in paths
        if path.relative_to(ROOT).as_posix() not in references
    ]


def _backend_production_sources() -> list[Path]:
    tracked = _tracked_repo_files()
    sources: list[Path] = []
    for path in BACKEND_SOURCE_ROOT.rglob("*"):
        if not path.is_file() or path not in tracked:
            continue
        relative = path.relative_to(BACKEND_SOURCE_ROOT)
        if any(part in _BACKEND_COVERAGE_EXCLUDED_DIRS for part in relative.parts):
            continue
        relative_name = relative.as_posix()
        if relative_name in _BACKEND_COVERAGE_EXCLUDED_FILES:
            continue
        if path.suffix == ".py" or relative_name in _BACKEND_BEHAVIOUR_ASSETS:
            sources.append(path)
            continue
        raise AssertionError(
            f"Unclassified packaged backend file: {path.relative_to(ROOT).as_posix()}"
        )
    return sorted(sources)


def _is_frontend_production_source(path: Path) -> bool:
    relative = path.relative_to(FRONTEND_SOURCE_ROOT)
    if path.suffix not in _FRONTEND_SOURCE_SUFFIXES:
        return False
    if any(part in _FRONTEND_TEST_ONLY_DIRS for part in relative.parts):
        return False
    if path.name == "setupTests.ts":
        return False
    return re.search(r"[.](?:test|spec)[.](?:css|ts|tsx)$", path.name) is None


def _repository_operational_sources() -> list[Path]:
    """Return maintained non-runtime artifacts that need explicit spec ownership."""
    tracked = _tracked_repo_files()
    paths = [path for path in tracked if path.parent == ROOT]
    paths.extend(ROOT / "frontend" / relative for relative in _FRONTEND_OPERATIONAL_FILES)
    paths.append(ROOT / "src" / "haute" / "py.typed")

    for pattern in (
        ".github/workflows/*.yml",
        "scripts/*",
        "frontend/e2e/**/*.ts",
        "frontend/public/*",
        "frontend/scripts/*.mjs",
        "mutation/*",
    ):
        paths.extend(path for path in ROOT.glob(pattern) if path.is_file() and path in tracked)

    paths.extend(
        path
        for path in (ROOT / "rating").rglob("*")
        if path.is_file()
        and path in tracked
        and path.suffix in {".json", ".py", ".rsglm"}
        and not {"__pycache__", "output", "outputs"}.intersection(
            path.relative_to(ROOT / "rating").parts
        )
    )

    missing = [path.relative_to(ROOT).as_posix() for path in paths if not path.is_file()]
    assert not missing, "Operational coverage inventory names missing files:\n- " + "\n- ".join(
        missing
    )
    untracked = [path.relative_to(ROOT).as_posix() for path in paths if path not in tracked]
    assert not untracked, (
        "Operational coverage inventory must contain tracked files only:\n- "
        + "\n- ".join(untracked)
    )
    return sorted(set(paths))


def test_module_map_reference_parser_reads_only_file_cells() -> None:
    module_map = """
Prose must not count: `src/haute/prose_only.py`.

| File | Responsibility |
|---|---|
| `src/haute/owned.py`, `frontend/src/Owned.tsx` | Uses `src/haute/dependency.py`. |
"""
    assert _module_map_file_references(module_map) == {
        "frontend/src/Owned.tsx",
        "src/haute/owned.py",
    }


def test_shared_file_component_parser_preserves_component_multiplicity() -> None:
    assert _shared_file_components(
        {
            "pipeline-config": {"src/haute/shared.py", "src/haute/only_config.py"},
            "server-api": {"src/haute/shared.py", "src/haute/only_api.py"},
        }
    ) == {"src/haute/shared.py": {"pipeline-config", "server-api"}}


def test_specs_readme_node_type_count_matches_enum() -> None:
    text = SPECS_README.read_text(encoding="utf-8")
    counts = re.findall(r"covers all\s+(\d+) node types", text)
    assert counts, "specs/README.md no longer states the node-type count"
    for count in counts:
        assert int(count) == len(NodeType)


def test_specs_readme_node_type_table_lists_every_enum_value() -> None:
    text = SPECS_README.read_text(encoding="utf-8")
    section = _h2_sections(text).get("Where is each node type specced?", "")
    assert section, "specs/README.md lacks the node-type table section"
    values = {
        match.group(1)
        for row in _module_map_rows(section)
        if row
        for match in _MARKDOWN_CODE_SPAN.finditer(row[0])
    }
    assert values == {node_type.value for node_type in NodeType}


def test_low_level_specs_reference_every_backend_source_file() -> None:
    sources = _backend_production_sources()
    uncovered = _unreferenced_sources(sources)
    assert not uncovered, (
        "Every behavioral backend source must be explicitly named in a specs "
        "low-level.md Module map inline-code entry. Uncovered sources:\n- " + "\n- ".join(uncovered)
    )


def test_low_level_specs_reference_every_frontend_source_file() -> None:
    tracked = _tracked_repo_files()
    sources = sorted(
        path
        for path in FRONTEND_SOURCE_ROOT.rglob("*")
        if path.is_file() and path in tracked and _is_frontend_production_source(path)
    )
    uncovered = _unreferenced_sources(sources)
    assert not uncovered, (
        "Every production frontend source must be explicitly named in a specs "
        "low-level.md Module map inline-code entry. Uncovered sources:\n- " + "\n- ".join(uncovered)
    )


def test_low_level_specs_reference_every_repository_operational_source() -> None:
    references = _low_level_spec_references()
    uncovered = [
        path.relative_to(ROOT).as_posix()
        for path in _repository_operational_sources()
        if path.relative_to(ROOT).as_posix() not in references
    ]
    assert not uncovered, (
        "Every maintained build, CI, tooling, browser-E2E, mutation, and reference-pipeline "
        "artifact must be explicitly named by its exact repo path in a specs low-level.md "
        "Module map entry. Uncovered sources:\n- " + "\n- ".join(uncovered)
    )


def test_repository_operational_inventory_includes_every_tracked_root_file() -> None:
    tracked_root_files = {path for path in _tracked_repo_files() if path.parent == ROOT}
    operational_sources = set(_repository_operational_sources())
    assert tracked_root_files <= operational_sources


def test_shared_module_map_files_have_one_primary_owner_and_complete_ledger() -> None:
    component_references = _component_module_map_file_references()
    shared_files = _shared_file_components(component_references)
    with SPECS_OWNERSHIP.open("rb") as ownership_file:
        document = tomllib.load(ownership_file)

    assert set(document) == {"version", "shared_file"}, (
        "ownership.toml must contain only version and [[shared_file]] records"
    )
    assert type(document["version"]) is int and document["version"] == 1, (
        "ownership.toml must use integer schema version 1"
    )
    records = document.get("shared_file", [])
    assert isinstance(records, list), "ownership.toml must use [[shared_file]] records"
    records_by_path: dict[str, dict[str, object]] = {}
    duplicate_records: list[str] = []
    for record in records:
        assert isinstance(record, dict), "Each ownership record must be a TOML table"
        path = record.get("path")
        primary = record.get("primary")
        consumers = record.get("consumers")
        reason = record.get("reason")
        assert set(record) == {"path", "primary", "consumers", "reason"}, (
            f"{path or '<unknown path>'} has missing or unknown ownership fields"
        )
        assert isinstance(path, str) and path, "Each ownership record needs a path"
        assert isinstance(primary, str) and primary, f"{path} needs a primary component"
        assert isinstance(consumers, list) and all(isinstance(item, str) for item in consumers), (
            f"{path} needs a string consumers list"
        )
        assert (ROOT / path).is_file(), f"{path} must name an existing repository file"
        assert (SPECS_ROOT / primary).is_dir(), f"{path} primary {primary!r} is not a component"
        assert all((SPECS_ROOT / consumer).is_dir() for consumer in consumers), (
            f"{path} names an unknown consumer component"
        )
        assert consumers == sorted(set(consumers)), (
            f"{path} consumers must be unique and sorted for deterministic review"
        )
        assert isinstance(reason, str) and reason.strip(), f"{path} needs a concise reason"
        if path in records_by_path:
            duplicate_records.append(path)
        records_by_path[path] = record

    assert not duplicate_records, "Duplicate ownership records:\n- " + "\n- ".join(
        sorted(duplicate_records)
    )
    assert list(records_by_path) == sorted(records_by_path), (
        "ownership.toml records must be sorted by path for deterministic review"
    )
    assert set(shared_files) <= set(records_by_path), (
        "ownership.toml must declare every tracked file shared by multiple Module maps. "
        f"Missing: {sorted(set(shared_files) - set(records_by_path))}"
    )
    for path, components in shared_files.items():
        record = records_by_path[path]
        primary = record["primary"]
        consumers = record["consumers"]
        assert primary in components, f"{path} primary {primary!r} is not a mapped component"
        assert set(consumers) == components - {primary}, (
            f"{path} consumers must be exactly the other mapped components; "
            f"expected {sorted(components - {primary})}, got {sorted(consumers)}"
        )
    # An explicitly ledgered extra shared file is valid only when it is mapped by
    # its primary and all consumers name that exact path somewhere in their specs.
    for path, record in records_by_path.items():
        if path in shared_files:
            continue
        primary = record["primary"]
        consumers = record["consumers"]
        assert path in component_references.get(primary, set()), (
            f"{path} is not module-mapped by {primary}"
        )
        for consumer in consumers:
            consumer_spec = SPECS_ROOT / consumer
            mentioned = any(
                path == _normalise_doc_reference(item)
                for doc in consumer_spec.glob("*.md")
                for section_heading, section_body in _h2_sections(
                    _without_fences(doc.read_text(encoding="utf-8"))
                ).items()
                if not _is_temporary_contract_heading(section_heading)
                for item in _MARKDOWN_CODE_SPAN.findall(section_body)
            )
            assert mentioned, f"{path} consumer {consumer} does not reference the exact path"

    for document in sorted(SPECS_ROOT.glob("*/*.md")):
        component = document.parent.name
        sections = _h2_sections(_without_fences(document.read_text(encoding="utf-8")))
        prose = "\n\n".join(
            body
            for heading, body in sections.items()
            if heading != "Module map" and not _is_temporary_contract_heading(heading)
        )
        paragraphs = re.split(
            r"\n\s*\n",
            prose,
        )
        for paragraph in paragraphs:
            if "primary owner" not in paragraph.casefold():
                continue
            for reference in _MARKDOWN_CODE_SPAN.findall(paragraph):
                path = _normalise_doc_reference(reference)
                if not (ROOT / path).is_file():
                    continue
                record = records_by_path.get(path)
                assert record is not None, (
                    f"{document.relative_to(ROOT).as_posix()} claims primary ownership of "
                    f"{path} outside its Module map without an ownership.toml record"
                )
                claimants = {record["primary"], *record["consumers"]}
                assert component in claimants, (
                    f"{document.relative_to(ROOT).as_posix()} claims ownership of {path} "
                    "but is absent from its ownership.toml record"
                )


def test_every_spec_component_has_required_documents_and_readme_entry() -> None:
    readme = SPECS_README.read_text(encoding="utf-8")
    components = sorted(
        path
        for path in SPECS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "roadmap"
    )
    assert components, "specs contains no component directories"

    missing_documents: list[str] = []
    missing_readme_entries: list[str] = []
    for component in components:
        for name in ("high-level.md", "low-level.md"):
            document = component / name
            if not document.is_file():
                missing_documents.append(document.relative_to(ROOT).as_posix())
        if f"[{component.name}]({component.name}/high-level.md)" not in readme:
            missing_readme_entries.append(component.name)

    assert not missing_documents, "Spec components missing required documents:\n- " + "\n- ".join(
        missing_documents
    )
    assert not missing_readme_entries, (
        "specs/README.md is missing component-index entries:\n- "
        + "\n- ".join(missing_readme_entries)
    )


def test_every_explicit_module_map_repo_path_exists() -> None:
    missing = sorted(
        reference
        for reference in _explicit_documented_repo_paths()
        if not (ROOT / reference).exists()
    )
    assert not missing, "Module maps contain stale repository paths:\n- " + "\n- ".join(missing)


def test_every_spec_document_follows_the_required_structure() -> None:
    failures: list[str] = []
    for component in sorted(
        path
        for path in SPECS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "roadmap"
    ):
        for name, headings in (
            ("high-level.md", _REQUIRED_HIGH_LEVEL_HEADINGS),
            ("low-level.md", _REQUIRED_LOW_LEVEL_HEADINGS),
        ):
            document = component / name
            present = _h2_sections(document.read_text(encoding="utf-8"))
            missing = [heading for heading in headings if heading[3:] not in present]
            if missing:
                failures.append(f"{document.relative_to(ROOT).as_posix()}: {', '.join(missing)}")

    assert not failures, "Spec documents missing required sections:\n- " + "\n- ".join(failures)


def test_every_relative_spec_link_resolves() -> None:
    broken: list[str] = []
    for document in sorted(SPECS_ROOT.rglob("*.md")):
        text = _MARKDOWN_FENCE.sub("", document.read_text(encoding="utf-8"))
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = raw_target.split("#", maxsplit=1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (document.parent / target).resolve().exists():
                broken.append(f"{document.relative_to(ROOT).as_posix()} -> {raw_target}")

    assert not broken, "Broken relative links in specs:\n- " + "\n- ".join(broken)


def test_pipeline_config_spec_sidecar_count_matches_mapping() -> None:
    text = PIPELINE_CONFIG_SPEC.read_text(encoding="utf-8")
    match = re.search(r"(\d+) of the (\d+)\s*\n?\s*node types store external config", text)
    assert match, "pipeline-config low-level spec no longer states the sidecar-config count"
    assert int(match.group(1)) == len(NODE_TYPE_TO_FOLDER)
    assert int(match.group(2)) == len(NodeType)


def test_pipeline_config_spec_folder_table_matches_mapping() -> None:
    text = PIPELINE_CONFIG_SPEC.read_text(encoding="utf-8")
    for folder in NODE_TYPE_TO_FOLDER.values():
        assert f"`config/{folder}/`" in text, (
            f"pipeline-config low-level spec config-folder table is missing `config/{folder}/`"
        )


def test_deployment_docs_name_the_real_databricks_secrets() -> None:
    secrets = TARGETS["databricks"]["secrets"]
    assert isinstance(secrets, list)
    for doc in DATABRICKS_SECRET_DOCS:
        text = doc.read_text(encoding="utf-8")
        for secret in secrets:
            assert secret in text, f"{doc.name} does not mention CI secret {secret}"
        # The deploy path reads only the RATING-prefixed pair; a secret table
        # row naming the bare pair sends the reader through a failing setup.
        for stale in ("`DATABRICKS_HOST` |", "`DATABRICKS_TOKEN` |"):
            assert stale not in text, (
                f"{doc.name} lists {stale.strip('` |')} as a CI secret; the deploy "
                "reads the DATABRICKS_RATING_* names (see haute._scaffold.TARGETS)"
            )


def test_deployment_docs_use_scaffolded_pipeline_path() -> None:
    scaffolded = haute_toml("motor-pricing", "databricks", "github")
    match = re.search(r'^pipeline = "(.*)"$', scaffolded, flags=re.MULTILINE)
    assert match is not None
    real_path = match.group(1)
    for doc in DEPLOYMENT_DOCS:
        for shown in re.findall(
            r'^pipeline = "(.*)"$', doc.read_text(encoding="utf-8"), flags=re.MULTILINE
        ):
            assert shown == real_path, (
                f'{doc.name} shows pipeline = "{shown}" but haute init '
                f'scaffolds pipeline = "{real_path}"'
            )


def test_docs_accuracy_ratchet() -> None:
    baseline = _read_baseline(_DOCS_ACCURACY_BASELINE)
    actual = set(_docs_violations())
    added, resolved = sorted(actual - baseline), sorted(baseline - actual)
    assert not added and not resolved, (
        "Documentation accuracy ratchet changed.\n"
        + (
            "Added violations:\n- " + "\n- ".join(item.tsv() for item in added) + "\n"
            if added
            else ""
        )
        + (
            "Resolved baseline entries (delete these one-line TSV entries):\n- "
            + "\n- ".join(item.tsv() for item in resolved)
            if resolved
            else ""
        )
    )


def test_documented_python_test_counts_match_source() -> None:
    """Any surviving exact count claim must match source-defined test functions."""
    mismatches: list[str] = []
    for document in sorted(SPECS_ROOT.rglob("*.md")):
        text = _without_fences(document.read_text(encoding="utf-8"))
        for claim in _TEST_COUNT_CLAIM.finditer(text):
            reference = claim.group("path")
            test_path = (
                ROOT / reference if reference.startswith("tests/") else ROOT / "tests" / reference
            )
            if not test_path.is_file():
                mismatches.append(
                    f"{document.relative_to(ROOT).as_posix()}: {reference} does not exist"
                )
                continue
            tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
            actual = sum(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
                for node in ast.walk(tree)
            )
            expected = int(claim.group("count"))
            if actual != expected:
                mismatches.append(
                    f"{document.relative_to(ROOT).as_posix()}: {reference} claims "
                    f"{expected}, source defines {actual}"
                )

    assert not mismatches, "Stale exact test-count claims:\n- " + "\n- ".join(mismatches)


def _seed_spec(root: Path, low_level: str) -> Path:
    spec = root / "specs/example"
    spec.mkdir(parents=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_ok.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (spec / "low-level.md").write_text(low_level, encoding="utf-8")
    return root / "specs"


def _fixture_repo_files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file()}


def _seed_high_level_contract(root: Path, heading: str, *contract_lines: str) -> Path:
    component = root / "specs" / "example"
    component.mkdir(parents=True)
    (component / "high-level.md").write_text(
        "\n".join(
            [
                "## Purpose",
                "Example.",
                "## Scope",
                "Example.",
                "## Behaviour",
                "Example.",
                "## Design rationale",
                "Example.",
                "## Interactions",
                "Example.",
                "## Failure model",
                "Example.",
                f"## {heading}",
                *contract_lines,
            ]
        ),
        encoding="utf-8",
    )
    return root / "specs"


def test_repo_files_fails_loudly_when_git_inventory_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_git(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(128, ["git", "ls-files", "-z"])

    monkeypatch.setattr(subprocess, "run", fail_git)

    with pytest.raises(subprocess.CalledProcessError):
        _repo_files(tmp_path)


def test_repo_reference_candidates_respect_explicit_inventory(tmp_path: Path) -> None:
    untracked = tmp_path / "src" / "untracked.py"
    untracked.parent.mkdir()
    untracked.write_text("value = 1\n", encoding="utf-8")
    tracked = tmp_path / "src" / "tracked.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    files = {tracked}
    inventory = RepoInventory.build(tmp_path, files)

    assert _repo_reference_candidates("src/untracked.py", tmp_path, set()) == ((), None)
    assert _repo_reference_candidates(
        "src/tracked.py::value",
        tmp_path,
        files,
        inventory=inventory,
    ) == ((tracked,), "value")
    assert _repo_reference_candidates(
        "tracked.py",
        tmp_path,
        files,
        inventory=inventory,
    ) == ((tracked,), None)
    assert _repo_reference_candidates(
        "src/*.py",
        tmp_path,
        files,
        inventory=inventory,
    ) == ((tracked,), None)
    assert _repo_reference_candidates(
        "src",
        tmp_path,
        files,
        inventory=inventory,
    ) == ((tracked.parent,), None)


def test_docs_guard_seeded_s3_regressions(tmp_path: Path) -> None:
    # The fixture seeds each claim class from the review's S3 table.
    specs = _seed_spec(
        tmp_path,
        """## Module map
| File | Responsibility |
| --- | --- |
| `src/missing.py` | `missing_symbol` |
| `src/existing.py` | `also_missing_symbol` |
## Key types and data structures
None.
## Control flow
Prose `src/prose_missing.py`.
## Edge cases and invariants
None.
## Error handling notes
This substring must not satisfy the required heading.
## Testing
`tests/test_ok.py::test_missing`
## Approved change contract
Add `src/existing.py`.
Add `src/existing.py::x`.
""",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/existing.py").write_text("x = 1\n", encoding="utf-8")
    document = tmp_path / "specs/example/low-level.md"
    text = document.read_text(encoding="utf-8") + "\n[bad](#missing-anchor)\n"
    document.write_text(text, encoding="utf-8")
    violations = set(_docs_violations(tmp_path, specs, repo_files=_fixture_repo_files(tmp_path)))
    relative = "specs/example/low-level.md"
    expected = {
        DocViolation(relative, "missing-repo-reference", "src/missing.py"),
        DocViolation(relative, "missing-module-map-symbol", "also_missing_symbol"),
        DocViolation(relative, "missing-test-symbol", "tests/test_ok.py::test_missing"),
        DocViolation(relative, "missing-repo-reference", "src/prose_missing.py"),
        DocViolation(relative, "broken-link-anchor", "#missing-anchor"),
        DocViolation(relative, "missing-required-heading", "## Error handling"),
        DocViolation(
            relative,
            "shipped-contract-action",
            "Approved change contract: src/existing.py::x",
        ),
    }
    assert expected <= violations
    assert (
        DocViolation(
            relative,
            "shipped-contract-action",
            "Approved change contract: src/existing.py",
        )
        not in violations
    )


def test_docs_guard_does_not_treat_existing_target_file_as_delivery(tmp_path: Path) -> None:
    source = tmp_path / "src" / "existing.py"
    source.parent.mkdir()
    source.write_text("def existing() -> None:\n    pass\n", encoding="utf-8")
    specs = _seed_high_level_contract(
        tmp_path,
        "Approved change contract — pending edit",
        "- **Target behaviour.** Change `src/existing.py`.",
    )

    assert DocViolation(
        "specs/example/high-level.md",
        "contract-target-present",
        "Approved change contract — pending edit: src/existing.py",
    ) not in _docs_violations(
        tmp_path,
        specs,
        repo_files=_fixture_repo_files(tmp_path),
    )


def test_docs_guard_retires_contract_when_named_target_is_present(tmp_path: Path) -> None:
    source = tmp_path / "src" / "ready.py"
    source.parent.mkdir()
    source.write_text("def ready() -> None:\n    pass\n", encoding="utf-8")
    specs = _seed_high_level_contract(
        tmp_path,
        "Approved change contract — ready target",
        "- **Target behaviour.** Call `src/ready.py::ready`.",
    )

    assert DocViolation(
        "specs/example/high-level.md",
        "contract-target-present",
        "Approved change contract — ready target: src/ready.py::ready",
    ) in _docs_violations(
        tmp_path,
        specs,
        repo_files=_fixture_repo_files(tmp_path),
    )


def test_docs_guard_retires_contract_when_acceptance_test_symbol_is_present(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "existing.py"
    acceptance = tmp_path / "tests" / "test_ready.py"
    source.parent.mkdir()
    acceptance.parent.mkdir()
    source.write_text("def existing() -> None:\n    pass\n", encoding="utf-8")
    acceptance.write_text("def test_ready() -> None:\n    pass\n", encoding="utf-8")
    specs = _seed_high_level_contract(
        tmp_path,
        "Approved change contract — tested target",
        "- **Target behaviour.** Change `src/existing.py`.",
        "- **Acceptance.** `tests/test_ready.py::test_ready` proves delivery.",
    )

    assert DocViolation(
        "specs/example/high-level.md",
        "contract-target-present",
        "Approved change contract — tested target: tests/test_ready.py::test_ready",
    ) in _docs_violations(
        tmp_path,
        specs,
        repo_files=_fixture_repo_files(tmp_path),
    )


def test_docs_guard_rejects_remaining_work_pointer_to_empty_roadmap(
    tmp_path: Path,
) -> None:
    component = tmp_path / "specs" / "example"
    roadmap = tmp_path / "specs" / "roadmap"
    component.mkdir(parents=True)
    roadmap.mkdir(parents=True)
    (component / "high-level.md").write_text(
        "\n".join(
            [
                "## Purpose",
                "Example.",
                "## Scope",
                "Example.",
                "## Behaviour",
                "Example.",
                "## Design rationale",
                "Example.",
                "## Interactions",
                "Example.",
                "## Failure model",
                "Remaining improvement work is tracked in the",
                "[example roadmap](../roadmap/example.md).",
            ]
        ),
        encoding="utf-8",
    )
    (roadmap / "example.md").write_text(
        "\n".join(
            [
                "## Scope",
                "Example.",
                "## Priorities",
                "There are no active example improvement packages.",
                "## Planned improvements",
                "There are no active example improvement packages.",
            ]
        ),
        encoding="utf-8",
    )

    assert DocViolation(
        "specs/example/high-level.md",
        "empty-roadmap-pointer",
        "../roadmap/example.md",
    ) in _docs_violations(
        tmp_path,
        tmp_path / "specs",
        repo_files=_fixture_repo_files(tmp_path),
    )
