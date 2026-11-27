"""The package install smoke is one script, and it installs as pip installs.

CI's ``package-smoke`` job and the Release workflow's ``build`` job each wrote the
install out by hand. The bytecode compilation the smoke check needs reached CI's copy
only, and the 0.8.0 release failed its smoke. Both now run
``scripts/package_install_smoke.py``.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import package_install_smoke as install_smoke
from tests._source_files import REPO_ROOT

_WHEEL = "haute-1.0.0-py3-none-any.whl"
_SDIST = "haute-1.0.0.tar.gz"


def _recording_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    answer: Callable[[list[str]], None] = lambda command: None,
) -> list[list[str]]:
    """Stand in for every process the script starts, recording each command in turn."""
    calls: list[list[str]] = []

    def run(command: Sequence[object], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert kwargs.get("check") is True, command
        calls.append([str(part) for part in command])
        answer(calls[-1])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        install_smoke, "subprocess", SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL)
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    for name in (_WHEEL, _SDIST):
        (tmp_path / name).write_bytes(b"")
    return calls


def test_each_distribution_is_installed_compiled_and_checked_in_its_own_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recording_runs(monkeypatch, tmp_path)

    install_smoke.main(["--dist", str(tmp_path), "--python", "3.12"])

    environments = [Path(call[2]) for call in calls if call[:2] == ["uv", "venv"]]
    assert len(set(environments)) == 2
    expected: list[list[str]] = []
    for environment, name in zip(environments, (_WHEEL, _SDIST), strict=True):
        python, haute = install_smoke._executables(environment)
        expected += [
            ["uv", "venv", str(environment), "--python", "3.12"],
            ["uv", "pip", "install", "--compile-bytecode", "--python", str(python)]
            + [str(tmp_path / name)],
            [str(python), str(install_smoke._CHECK)],
            [str(haute), "--help"],
        ]
    assert calls == expected


def test_a_failed_check_fails_the_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_the_check(command: list[str]) -> None:
        if command[-1] == str(install_smoke._CHECK):
            raise subprocess.CalledProcessError(1, command)

    calls = _recording_runs(monkeypatch, tmp_path, fail_the_check)

    with pytest.raises(subprocess.CalledProcessError):
        install_smoke.main(["--dist", str(tmp_path), "--python", "3.12"])
    assert calls[-1][-1] == str(install_smoke._CHECK)


def test_a_distribution_is_installed_with_every_module_compiled(tmp_path: Path) -> None:
    command = install_smoke.install_command(tmp_path / "python", tmp_path / _WHEEL)

    assert command[:3] == ["uv", "pip", "install"]
    assert "--compile-bytecode" in command
    assert command[-1] == str(tmp_path / _WHEEL)


def test_the_folder_s_one_wheel_and_one_sdist_are_found(tmp_path: Path) -> None:
    for name in (_WHEEL, _SDIST):
        (tmp_path / name).write_bytes(b"")

    assert install_smoke.distributions(tmp_path) == {
        "wheel": tmp_path / _WHEEL,
        "sdist": tmp_path / _SDIST,
    }


@pytest.mark.parametrize(
    ("names", "refused"),
    [
        ([_SDIST], "wheel"),
        ([_WHEEL], "sdist"),
        ([_WHEEL, "haute-0.9.0-py3-none-any.whl", _SDIST], "wheel"),
    ],
)
def test_a_folder_without_exactly_one_wheel_and_one_sdist_is_refused(
    tmp_path: Path, names: list[str], refused: str
) -> None:
    for name in names:
        (tmp_path / name).write_bytes(b"")

    with pytest.raises(SystemExit, match=f"exactly one {refused}"):
        install_smoke.distributions(tmp_path)


@pytest.mark.meta
def test_every_workflow_smokes_a_built_distribution_through_the_one_script() -> None:
    by_hand: list[str] = []
    callers: set[str] = set()
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        if "scripts/package_install_smoke.py" in text:
            callers.add(workflow.name)
        by_hand += [
            f"{workflow.name}:{number}: {line.strip()}"
            for number, line in enumerate(text.splitlines(), start=1)
            if "package_smoke_check.py" in line
        ]

    assert by_hand == [], (
        "Run the package smoke through scripts/package_install_smoke.py, which installs "
        f"as pip does, not by hand: {by_hand}"
    )
    assert {"ci.yml", "release.yml"} <= callers
