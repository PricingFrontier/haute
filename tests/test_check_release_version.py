from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_release_version as release


def _project(tmp_path: Path, version: str, released: list[str] | None) -> list[str]:
    """Write a pyproject and PyPI JSON response; return the script's arguments."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "haute"\nversion = "{version}"\n', encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("earlier=1\n", encoding="utf-8")
    args = ["--pyproject", str(pyproject), "--github-output", str(output)]
    if released is not None:
        pypi_json = tmp_path / "pypi.json"
        pypi_json.write_text(
            json.dumps({"info": {"name": "haute"}, "releases": {v: [] for v in released}}),
            encoding="utf-8",
        )
        args += ["--pypi-json", str(pypi_json)]
    return args


@pytest.mark.parametrize(
    ("version", "released"),
    [
        ("0.4.0", ["0.2.4", "0.3.0", "0.3.3"]),
        ("0.3.4", ["0.3.3", "0.3.10a1"]),
        ("1.0.0", ["0.9.12"]),
        ("0.10.0", ["0.9.0"]),
        ("0.1.0", None),
    ],
)
def test_a_new_version_is_released_and_written_for_later_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], version: str, released: list[str] | None
) -> None:
    args = _project(tmp_path, version, released)

    assert release.main(args) == 0

    output = (tmp_path / "github_output").read_text(encoding="utf-8")
    assert output == f"earlier=1\nversion={version}\n"
    assert capsys.readouterr().out == f"Releasing haute {version}.\n"


@pytest.mark.parametrize(
    ("version", "released", "message"),
    [
        ("0.3.3", ["0.3.2", "0.3.3"], "haute 0.3.3 is already on PyPI."),
        ("0.3.2", ["0.3.2", "0.3.3"], "haute 0.3.2 is already on PyPI."),
        ("0.3.1", ["0.3.0", "0.3.3"], "haute 0.3.1 is not newer than 0.3.3, the latest release"),
        ("0.9.0", ["0.10.0"], "haute 0.9.0 is not newer than 0.10.0, the latest release"),
    ],
)
def test_an_unbumped_version_is_refused_with_how_to_bump(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    version: str,
    released: list[str],
    message: str,
) -> None:
    args = _project(tmp_path, version, released)

    assert release.main(args) == 1

    out = capsys.readouterr().out
    assert out.startswith(f"::error::{message}")
    assert "uv version --bump patch" in out
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == "earlier=1\n"


@pytest.mark.parametrize("version", ["0.4", "0.4.0rc1", "v0.4.0", "0.04.0", "0.4.0 "])
def test_a_version_that_is_not_x_y_z_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], version: str
) -> None:
    args = _project(tmp_path, version, ["0.3.3"])

    assert release.main(args) == 1

    assert capsys.readouterr().out == (
        f"::error::pyproject.toml declares version {version!r}; a release must be X.Y.Z.\n"
    )
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == "earlier=1\n"


def test_the_repository_declares_a_releasable_version_shape() -> None:
    version = release.declared_version(Path("pyproject.toml"))

    assert release.check_release_version(version, []) == version
