"""The haute a Databricks-served model installs is the one that validated its deploy.

Model Serving installs the model's environment from the package index. A deploy on a
released haute pins it; any other haute (here: one installed from Git, as a project
depending on an unreleased haute installs it in CI) ships as a wheel repacked from its
installed files, because the index's release of the same version number is other code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

import pytest

from haute.deploy._served_haute import ServedHaute, repack_installed_wheel, served_haute
from haute.errors import DeployError

_INFO = "haute-0.6.0.dist-info"
_GIT = {
    "url": "https://github.com/PricingFrontier/haute",
    "vcs_info": {"vcs": "git", "commit_id": "c4e66747d"},
}


def _record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def _install(site: Path, *, direct_url: dict | None, missing: str | None = None) -> Path:
    """An installed haute in *site*, laid out and recorded as an installer leaves it."""
    files = {
        "haute/__init__.py": b"__version__ = '0.6.0'\n",
        "haute/deploy/_model_code.py": b"MODEL = True\n",
        "haute/static/index.html": b"<html></html>\n",
        "haute/__pycache__/__init__.cpython-313.pyc": b"\x00compiled",
        f"{_INFO}/METADATA": b"Metadata-Version: 2.4\nName: haute\nVersion: 0.6.0\n",
        f"{_INFO}/WHEEL": b"Wheel-Version: 1.0\nGenerator: hatchling\nTag: py3-none-any\n",
        f"{_INFO}/entry_points.txt": b"[console_scripts]\nhaute = haute.cli:cli\n",
        f"{_INFO}/INSTALLER": b"uv\n",
        f"{_INFO}/REQUESTED": b"",
        f"{_INFO}/uv_cache.json": b"{}",
    }
    if direct_url is not None:
        files[f"{_INFO}/direct_url.json"] = json.dumps(direct_url).encode()
    for relative, data in files.items():
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    record = [_record_line(rel, data) for rel, data in files.items()]
    record.append(_record_line("../../bin/haute", b"#!python\n"))
    record.append(f"{_INFO}/RECORD,,")
    (site / _INFO / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    if missing is not None:
        (site / missing).unlink()
    return site / _INFO


def _distribution(site: Path, **kwargs: object) -> metadata.Distribution:
    return metadata.PathDistribution(_install(site, **kwargs))  # type: ignore[arg-type]


def test_a_released_haute_is_pinned(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path / "site", direct_url=None)

    with patch("haute.deploy._served_haute.metadata.distribution", return_value=distribution):
        served = served_haute(tmp_path / "wheel")

    assert served == ServedHaute("haute==0.6.0")
    assert not (tmp_path / "wheel").exists()


def test_an_editable_haute_is_refused(tmp_path: Path) -> None:
    editable = {"url": "file:///C:/src/haute", "dir_info": {"editable": True}}
    distribution = _distribution(tmp_path / "site", direct_url=editable)

    with (
        patch("haute.deploy._served_haute.metadata.distribution", return_value=distribution),
        pytest.raises(DeployError, match="editable install of /C:/src/haute"),
    ):
        served_haute(tmp_path / "wheel")


def test_a_haute_installed_from_git_ships_as_its_own_wheel(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path / "site", direct_url=_GIT)

    with patch("haute.deploy._served_haute.metadata.distribution", return_value=distribution):
        served = served_haute(tmp_path / "wheel")

    assert served.wheel == tmp_path / "wheel" / "haute-0.6.0-py3-none-any.whl"
    assert served.requirement == "code/haute-0.6.0-py3-none-any.whl"
    with zipfile.ZipFile(served.wheel) as wheel:
        names = wheel.namelist()
        # The package and its metadata, without the installer's records, the
        # installed script, or compiled files.
        assert names == [
            "haute-0.6.0.dist-info/METADATA",
            "haute-0.6.0.dist-info/WHEEL",
            "haute-0.6.0.dist-info/entry_points.txt",
            "haute/__init__.py",
            "haute/deploy/_model_code.py",
            "haute/static/index.html",
            "haute-0.6.0.dist-info/RECORD",
        ]
        assert wheel.read("haute/static/index.html") == b"<html></html>\n"
        record = wheel.read("haute-0.6.0.dist-info/RECORD").decode().splitlines()
        for line in record[:-1]:
            path, digest, size = line.split(",")
            data = wheel.read(path)
            assert line == _record_line(path, data)
            assert int(size) == len(data)
        assert record[-1] == "haute-0.6.0.dist-info/RECORD,,"


def test_a_repack_is_byte_identical_every_time(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path / "site", direct_url=_GIT)

    first = repack_installed_wheel(distribution, tmp_path / "a").read_bytes()
    second = repack_installed_wheel(distribution, tmp_path / "b").read_bytes()

    assert first == second


def test_a_recorded_file_missing_from_the_install_is_refused(tmp_path: Path) -> None:
    distribution = _distribution(
        tmp_path / "site", direct_url=_GIT, missing="haute/deploy/_model_code.py"
    )

    with pytest.raises(DeployError, match="haute/deploy/_model_code.py is missing"):
        repack_installed_wheel(distribution, tmp_path / "wheel")


def test_the_served_environment_installs_that_haute_and_mlflow() -> None:
    import mlflow
    import polars

    from haute.deploy._mlflow import _conda_env

    env = _conda_env("3.13", "code/haute-0.6.0-py3-none-any.whl")

    assert env["dependencies"][:2] == ["python=3.13", "pip"]
    # Model Serving builds the container only with mlflow itself in the environment.
    assert env["dependencies"][2] == {
        "pip": [
            "code/haute-0.6.0-py3-none-any.whl",
            f"mlflow=={mlflow.__version__}",
            f"polars=={polars.__version__}",
        ]
    }
