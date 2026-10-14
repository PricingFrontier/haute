"""The project's folder layout: the versioned pipeline folder and where model files land."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._project_layout import (
    default_model_folder,
    pipeline_files_folder,
    pipeline_folder,
)
from haute.errors import ConfigError


def _project(root: Path, toml: str | None) -> Path:
    if toml is not None:
        (root / "haute.toml").write_text(toml, encoding="utf-8")
    return root


def test_a_nested_pipeline_has_its_folder_versioned_with_files_inside(tmp_path: Path) -> None:
    root = _project(tmp_path, '[project]\npipeline = "rating/main.py"\n')

    assert pipeline_folder(root) == "rating"
    assert pipeline_files_folder(root) == "rating/files"
    assert default_model_folder(root) == "rating/files/models"


def test_a_deeper_pipeline_folder_is_named_with_forward_slashes(tmp_path: Path) -> None:
    # Answered in POSIX form on every platform, as Git and the panels name paths.
    root = _project(tmp_path, '[project]\npipeline = "src/pricing/main.py"\n')

    assert pipeline_folder(root) == "src/pricing"
    assert default_model_folder(root) == "src/pricing/files/models"


@pytest.mark.parametrize(
    "toml",
    [
        None,
        "[project]\n",
        '[project]\npipeline = "main.py"\n',
        '[project]\npipeline = "../elsewhere/main.py"\n',
    ],
    ids=["no-haute-toml", "no-pipeline", "pipeline-at-root", "pipeline-outside-root"],
)
def test_without_a_folder_below_the_root_nothing_is_versioned_as_a_whole(
    tmp_path: Path, toml: str | None
) -> None:
    root = _project(tmp_path / "project", None)
    root.mkdir()
    _project(root, toml)

    assert pipeline_folder(root) is None
    assert pipeline_files_folder(root) is None
    assert default_model_folder(root) == "models"


def test_a_malformed_haute_toml_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path, "[project\npipeline = ")

    with pytest.raises(ConfigError):
        pipeline_folder(root)
