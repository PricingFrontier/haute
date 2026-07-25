"""Pin user-facing doc claims to the code they describe.

Docs stating machine-checkable facts (node-type counts, CI secret names,
scaffold paths) have drifted from the code before — a by-the-book setup
following the deployment guides failed its first deploy because the guides
named the wrong secrets. These tests make that class of drift fail CI.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._scaffold import TARGETS, haute_toml
from haute._types import NodeType

ROOT = Path(__file__).resolve().parents[1]
MKDOCS_CONFIG = ROOT / "mkdocs.yml"
EXECUTION_STRATEGY_DOC = ROOT / "docs" / "building-models" / "execution-strategy.md"
SPECS_README = ROOT / "docs" / "specs" / "README.md"
PIPELINE_CONFIG_SPEC = ROOT / "docs" / "specs" / "pipeline-config" / "low-level.md"
DEPLOYMENT_DOCS = sorted((ROOT / "docs" / "deployment").rglob("*.md"))
LOW_LEVEL_SPECS = tuple(sorted((ROOT / "docs" / "specs").rglob("low-level.md")))
BACKEND_SOURCE_ROOT = ROOT / "src" / "haute"
FRONTEND_SOURCE_ROOT = ROOT / "frontend" / "src"
SPECS_ROOT = ROOT / "docs" / "specs"
SPECS_OWNERSHIP = SPECS_ROOT / "ownership.toml"
ROADMAP_ROOT = ROOT / "docs" / "roadmap"
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
    {"_polars_io_arguments.json", "assistant/assets/authoring_guide.md"}
)
# These files are deliberately outside behavioral component coverage: ``py.typed``
# is a distribution marker owned by the build/distribution spec, while ``static``
# and bytecode caches are generated outputs rather than source-of-truth modules.
_BACKEND_COVERAGE_EXCLUDED_FILES = frozenset({"py.typed"})
_BACKEND_COVERAGE_EXCLUDED_DIRS = frozenset({"__pycache__", "static"})

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
    "src/",
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
    "edge-join",
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


def test_internal_engineering_docs_are_excluded_from_public_mkdocs_site() -> None:
    config = MKDOCS_CONFIG.read_text(encoding="utf-8")
    exclude_block = config.split("exclude_docs: |", maxsplit=1)[1].split("\ndev_addr:", maxsplit=1)[
        0
    ]

    for internal_dir in ("specs/", "roadmap/", "trip/"):
        assert f"  {internal_dir}\n" in exclude_block
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
    package_owners: dict[str, str] = {}
    for component, path in component_files.items():
        text = path.read_text(encoding="utf-8")
        for heading in _REQUIRED_COMPONENT_ROADMAP_HEADINGS:
            assert heading in text, f"{path.relative_to(ROOT)} is missing {heading}"
        assert f"({component}.md)" in index

        package_ids = _COMPONENT_PACKAGE_HEADING.findall(text)
        assert package_ids, f"{path.relative_to(ROOT)} has no work packages"
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

    retired_or_folded_packages = {
        "AUD-C02",
        "AUD-C03",
        "AUD-C04",
        "AUD-C11",
        "AUD-C18",
        "AUD-SEC-02",
        "AUD-TRACE-01",
    }
    expected_packages = (
        ({f"AUD-C{number:02d}" for number in range(1, 21)} - retired_or_folded_packages)
        | {f"EDA-E{number:02d}" for number in range(1, 14)}
        | {f"GIT-G{number:02d}" for number in range(1, 17)}
        | {f"IO-IO{number:02d}" for number in range(1, 13)}
        | {f"MOD-M{number:02d}" for number in range(1, 10)}
        | {f"OPT-P{number:02d}" for number in range(1, 11)}
        | {f"ROAD-WORKER-{number:02d}" for number in range(1, 6)}
        | {f"ROAD-EXEC-{number:02d}" for number in range(1, 6)}
        | {f"ROAD-TEST-{number:02d}" for number in range(1, 6)}
        | {f"ROAD-UI-{number:02d}" for number in range(1, 6)}
        | {f"ROAD-EDGE-{number:02d}" for number in range(1, 4)}
        | {
            "ASSIST-01",
            "ASSIST-02",
            "AUD-CACHE-01",
            "AUD-DEPLOY-01",
            "AUD-DEPLOY-02",
            "AUD-PIPE-01",
            "AUD-QUALITY-01",
            "AUD-QUALITY-02",
            "AUD-QUALITY-03",
            "AUD-RATING-01",
            "AUD-SEC-01",
            "CACHE-PERF-01",
            "RATING-PERF-01",
        }
    )
    assert set(package_owners) == expected_packages, (
        "component roadmaps and the expected consolidated package set differ: "
        f"missing={sorted(expected_packages - set(package_owners))}, "
        f"unexpected={sorted(set(package_owners) - expected_packages)}"
    )
    assert retired_or_folded_packages.isdisjoint(package_owners), (
        "implemented or folded packages must not remain as separate roadmap work: "
        f"{sorted(retired_or_folded_packages & set(package_owners))}"
    )

    for retired_root in (
        ROOT / "docs" / "fable-Review",
        ROOT / "docs" / "review",
        ROOT / "docs" / "roadmap" / "components",
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
        if relative_posix.startswith("docs/roadmap/"):
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
        "review/roadmap/remediation-plan Markdown must live only in docs/roadmap: "
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
    assert counts, "docs/specs/README.md no longer states the node-type count"
    for count in counts:
        assert int(count) == len(NodeType)


def test_specs_readme_node_type_table_lists_every_enum_value() -> None:
    text = SPECS_README.read_text(encoding="utf-8")
    for node_type in NodeType:
        assert f"`{node_type.value}`" in text, (
            f"docs/specs/README.md node-type table is missing `{node_type.value}`"
        )


def test_low_level_specs_reference_every_backend_source_file() -> None:
    sources = _backend_production_sources()
    uncovered = _unreferenced_sources(sources)
    assert not uncovered, (
        "Every behavioral backend source must be explicitly named in a docs/specs "
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
        "Every production frontend source must be explicitly named in a docs/specs "
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
        "artifact must be explicitly named by its exact repo path in a docs/specs low-level.md "
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
    assert set(records_by_path) == set(shared_files), (
        "ownership.toml must declare every and only tracked file shared by multiple Module maps. "
        f"Missing: {sorted(set(shared_files) - set(records_by_path))}; "
        f"stale: {sorted(set(records_by_path) - set(shared_files))}"
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


def test_every_spec_component_has_required_documents_and_readme_entry() -> None:
    readme = SPECS_README.read_text(encoding="utf-8")
    components = sorted(
        path for path in SPECS_ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")
    )
    assert components, "docs/specs contains no component directories"

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
        "docs/specs/README.md is missing component-index entries:\n- "
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
        path for path in SPECS_ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")
    ):
        for name, headings in (
            ("high-level.md", _REQUIRED_HIGH_LEVEL_HEADINGS),
            ("low-level.md", _REQUIRED_LOW_LEVEL_HEADINGS),
        ):
            document = component / name
            text = document.read_text(encoding="utf-8")
            missing = [heading for heading in headings if heading not in text]
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

    assert not broken, "Broken relative links in docs/specs:\n- " + "\n- ".join(broken)


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
