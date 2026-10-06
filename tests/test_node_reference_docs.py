"""The published node reference documents the node types that exist (BUILD-R01).

The internal specification corpus has its own accuracy checks; these check the
user-facing site. Every node type a user can add has exactly one page in the
node reference, the navigation lists exactly those pages, and a page's
"In the pipeline file" table names only keys the config validator accepts for
that node type.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from haute._config_validation import VALID_KEYS
from haute._types import NodeType

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NODE_REFERENCE = DOCS / "building-models" / "nodes"
MKDOCS_CONFIG = ROOT / "mkdocs.yml"

# Every node type a user can add, and its one reference page.
NODE_REFERENCE_PAGES: dict[NodeType, str] = {
    NodeType.API_INPUT: "quote-input.md",
    NodeType.DATA_INPUT: "data-input.md",
    NodeType.DATA_OUTPUT: "data-output.md",
    NodeType.CONSTANT: "constant.md",
    NodeType.POLARS: "transform.md",
    NodeType.EDGE_JOIN: "edge-join.md",
    NodeType.BANDING: "banding.md",
    NodeType.RATING_STEP: "rating-step.md",
    NodeType.SCENARIO_EXPANDER: "scenario-expander.md",
    NodeType.LIVE_SWITCH: "source-switch.md",
    NodeType.MODELLING: "model-training.md",
    NodeType.MODEL_SCORE: "model-score.md",
    NodeType.EXTERNAL_FILE: "external-file.md",
    NodeType.OPTIMISER: "optimiser.md",
    NodeType.OPTIMISER_APPLY: "optimiser-apply.md",
    NodeType.OUTPUT: "output.md",
    NodeType.EXPLORE: "explore.md",
    NodeType.SUBMODEL: "submodel.md",
}
# A submodel port is created with its submodel and documented on that page.
NOT_AUTHORED_DIRECTLY = frozenset({NodeType.SUBMODEL_PORT})
# Reference pages that document a feature rather than a node type.
FEATURE_PAGES = frozenset({"index.md", "instances.md"})
# Node types and config values the canonical-only code has removed.
RETIRED_VOCABULARY = ("Data Source", "Data Sink", "dataSource", "dataSink", "flat_file")


class _MkdocsConfigLoader(yaml.SafeLoader):
    """Safe loading that tolerates the ``!!python/name:`` tags of theme extensions."""


_MkdocsConfigLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/", lambda _loader, _suffix, _node: None
)


def _nav_reference_pages(config_text: str) -> list[str]:
    """The node-reference pages the parsed MkDocs ``nav`` links to, in order."""
    pages: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            if item.startswith("building-models/nodes/"):
                pages.append(item.removeprefix("building-models/nodes/"))
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)

    walk(yaml.load(config_text, Loader=_MkdocsConfigLoader)["nav"])  # noqa: S506
    return pages


def _published_pages() -> list[Path]:
    config = MKDOCS_CONFIG.read_text(encoding="utf-8")
    excluded = set(re.search(r"exclude_docs: \|\n((?:  .*\n)+)", config).group(1).split())  # type: ignore[union-attr]
    return [path for path in DOCS.rglob("*.md") if path.name not in excluded]


_PIPELINE_FILE_SECTION = re.compile(
    r'^\?\?\? note "In the pipeline file"\n((?:(?:    .*)?\n)+)', re.M
)
_SETTINGS_TABLE = re.compile(
    r"^\| *Setting in the editor *\| *Stored as *\|\n\|[-| :]+\|\n((?:\|.*\n)+)", re.M
)


def _pipeline_file_keys(text: str) -> list[str]:
    """The top-level config keys a page's "In the pipeline file" table stores settings as.

    The table sits in a closed admonition. A "Stored as" cell that starts with a
    backticked key names a config key, written with its parent path when nested
    (``tables[].emit``), so its first segment is the node config's own key. A cell
    that starts with prose describes where else a setting lives, such as a
    connection's ``target_port``.
    """
    section = _PIPELINE_FILE_SECTION.search(text)
    if section is None:
        return []
    body = "\n".join(line.removeprefix("    ") for line in section.group(1).splitlines()) + "\n"
    keys: list[str] = []
    for table in _SETTINGS_TABLE.findall(body):
        for row in table.splitlines():
            stored = re.split(r"(?<!\\)\|", row.strip().strip("|"))[-1].strip()
            match = re.match(r"`([^`]+)`", stored)
            if match:
                keys.append(re.split(r"[\[.]", match.group(1), maxsplit=1)[0])
    return keys


def test_every_node_type_is_documented_or_deliberately_not() -> None:
    pages = list(NODE_REFERENCE_PAGES.values())

    assert set(NODE_REFERENCE_PAGES) | NOT_AUTHORED_DIRECTLY == set(NodeType)
    assert not set(NODE_REFERENCE_PAGES) & NOT_AUTHORED_DIRECTLY
    # One page per type: no two types share a page, and no type borrows a feature page.
    assert len(pages) == len(set(pages))
    assert not set(pages) & FEATURE_PAGES


def test_navigation_lists_one_page_per_node_type_and_nothing_else() -> None:
    nav = _nav_reference_pages(MKDOCS_CONFIG.read_text(encoding="utf-8"))
    duplicates = sorted({page for page in nav if nav.count(page) > 1})

    assert duplicates == []
    assert set(nav) == set(NODE_REFERENCE_PAGES.values()) | FEATURE_PAGES
    assert {path.name for path in NODE_REFERENCE.glob("*.md")} == set(nav)


def test_a_commented_out_navigation_entry_is_not_listed() -> None:
    config = MKDOCS_CONFIG.read_text(encoding="utf-8")
    entry = "        - Explore: building-models/nodes/explore.md\n"
    assert entry in config

    commented = config.replace(entry, "        # - Explore: building-models/nodes/explore.md\n")

    assert "explore.md" in _nav_reference_pages(config)
    assert "explore.md" not in _nav_reference_pages(commented)


@pytest.mark.parametrize(
    ("node_type", "page"), sorted(NODE_REFERENCE_PAGES.items()), ids=lambda value: str(value)
)
def test_the_pipeline_file_table_names_only_accepted_config_keys(
    node_type: NodeType, page: str
) -> None:
    documented = _pipeline_file_keys((NODE_REFERENCE / page).read_text(encoding="utf-8"))

    assert documented, f"{page} has no In the pipeline file table"
    assert sorted(set(documented) - VALID_KEYS[node_type]) == []


def test_the_table_parser_reads_nested_keys_and_escaped_pipes_and_skips_prose() -> None:
    page = (
        "# Example\n\n"
        '??? note "In the pipeline file"\n'
        "    Settings live in a sidecar.\n\n"
        "    | Setting in the editor | Stored as |\n"
        "    |---|---|\n"
        "    | **emit** | `tables[].emit` (`true` or `false`) |\n"
        "    | **SUFFIX** | `suffix` |\n"
        "    | **INPUT** | the `target_port` of each connection |\n"
        "    | **SEPARATOR** | `separator`: `left\\|right` |\n\n"
        "**See also:**\n"
    )

    assert _pipeline_file_keys(page) == ["tables", "suffix", "separator"]
    assert _pipeline_file_keys("| Config | Description |\n|---|---|\n| `path` | x |\n") == []


def test_published_pages_use_no_retired_node_vocabulary() -> None:
    found = [
        f"{path.relative_to(ROOT).as_posix()}: {term}"
        for path in _published_pages()
        for term in RETIRED_VOCABULARY
        if term in path.read_text(encoding="utf-8")
    ]

    assert found == []
