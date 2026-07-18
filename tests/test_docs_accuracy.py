"""Pin user-facing doc claims to the code they describe.

Docs stating machine-checkable facts (node-type counts, CI secret names,
scaffold paths) have drifted from the code before — a by-the-book setup
following the deployment guides failed its first deploy because the guides
named the wrong secrets. These tests make that class of drift fail CI.
"""

from __future__ import annotations

import re
from pathlib import Path

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._scaffold import TARGETS, haute_toml
from haute._types import NodeType

ROOT = Path(__file__).resolve().parents[1]
SPECS_README = ROOT / "docs" / "specs" / "README.md"
PIPELINE_CONFIG_SPEC = ROOT / "docs" / "specs" / "pipeline-config" / "low-level.md"
DEPLOYMENT_DOCS = sorted((ROOT / "docs" / "deployment").rglob("*.md"))

DATABRICKS_SECRET_DOCS = [
    ROOT / "docs" / "deployment" / "targets" / "databricks.md",
    ROOT / "docs" / "deployment" / "ci" / "github-actions.md",
    ROOT / "docs" / "deployment" / "ci" / "gitlab.md",
    ROOT / "docs" / "deployment" / "ci" / "azure-devops.md",
]


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
