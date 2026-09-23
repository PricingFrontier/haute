"""Destination rules for the Export pane's "Save model to file"."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._path_resolution import MalformedRuntimePathError, RuntimePathOutsideProjectError
from haute.modelling._model_export import MODEL_FILE_SUFFIXES, resolve_model_export_destination


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def test_bare_filename_lands_in_models_with_the_model_extension(project: Path) -> None:
    destination = resolve_model_export_destination(
        "frequency", model_suffix=".cbm", project_root=project
    )

    assert destination.display_path == "models/frequency.cbm"
    assert destination.path == (project / "models" / "frequency.cbm").resolve()
    assert destination.suffix_mismatch is False


def test_paths_with_a_folder_are_project_root_relative(project: Path) -> None:
    destination = resolve_model_export_destination(
        "rating\\models\\severity", model_suffix=".rsglm", project_root=project
    )

    assert destination.display_path == "rating/models/severity.rsglm"
    assert destination.path == (project / "rating" / "models" / "severity.rsglm").resolve()


def test_explicit_extension_is_kept_and_compared_ignoring_case(project: Path) -> None:
    matching = resolve_model_export_destination(
        "frequency.CBM", model_suffix=".cbm", project_root=project
    )
    mismatched = resolve_model_export_destination(
        "frequency.parquet", model_suffix=".cbm", project_root=project
    )

    assert matching.display_path == "models/frequency.CBM"
    assert matching.suffix_mismatch is False
    assert mismatched.display_path == "models/frequency.parquet"
    assert mismatched.suffix_mismatch is True


def test_absolute_path_inside_the_project_is_accepted(project: Path) -> None:
    absolute = project / "exports" / "frequency.cbm"

    destination = resolve_model_export_destination(
        str(absolute), model_suffix=".cbm", project_root=project
    )

    assert destination.path == absolute.resolve()


@pytest.mark.parametrize("raw", ["../frequency.cbm", "models/../../frequency.cbm"])
def test_relative_escape_is_rejected(project: Path, raw: str) -> None:
    with pytest.raises(RuntimePathOutsideProjectError):
        resolve_model_export_destination(raw, model_suffix=".cbm", project_root=project)


def test_absolute_path_outside_the_project_is_rejected(project: Path, tmp_path: Path) -> None:
    with pytest.raises(RuntimePathOutsideProjectError):
        resolve_model_export_destination(
            str(tmp_path / "elsewhere.cbm"), model_suffix=".cbm", project_root=project
        )


def test_reserved_device_names_and_nul_bytes_are_malformed(project: Path) -> None:
    with pytest.raises(MalformedRuntimePathError):
        resolve_model_export_destination(
            "models/NUL.cbm", model_suffix=".cbm", project_root=project
        )
    with pytest.raises(MalformedRuntimePathError):
        resolve_model_export_destination("bad\x00name", model_suffix=".cbm", project_root=project)


def test_every_algorithm_has_a_model_file_suffix() -> None:
    assert dict(MODEL_FILE_SUFFIXES) == {
        "catboost": ".cbm",
        "glm": ".rsglm",
        "xgboost": ".ubj",
        "lightgbm": ".lgbm",
    }
