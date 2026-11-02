"""A deployment's dependency lock and the check a scoring image runs on it (DEP-R09).

The resolver runs ``uv pip compile`` as a subprocess; here that is ``tests/_fake_uv.py``
(the suite-wide fixture in ``tests/conftest.py``), so nothing reaches an index. What the
real index resolves is ``tests/test_deploy_wheels.py``'s, marked ``index``.
"""

from __future__ import annotations

import json
import shutil
import sys
from importlib import metadata
from pathlib import Path

import pytest

from haute.deploy._container import _SCORING_RUNTIME_DEPENDENCIES, HAUTE_SCORING_OMISSIONS
from haute.deploy._lock import TARGET_PLATFORM, lock_requirements
from haute.deploy._lock import uv_command as real_uv_command
from haute.deploy._runtime_check import main as check_main
from haute.deploy._runtime_check import runtime_problems, selected_extras
from haute.errors import DeployError

# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def test_a_lock_is_resolved_for_the_target_from_wheels_whatever_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows or macOS host locks what Linux installs, wheels only, every file hashed."""
    recorded = tmp_path / "args.json"
    monkeypatch.setenv("HAUTE_FAKE_UV_ARGS", str(recorded))
    monkeypatch.setattr(sys, "platform", "win32")

    lock = lock_requirements(
        ["uvicorn[standard]==0.40.0", "polars==2.0.0"],
        python_version="3.11",
        constraints=["websockets>=16,<17"],
    )

    args = json.loads(recorded.read_text())
    assert args[args.index("--python-platform") + 1] == TARGET_PLATFORM == "x86_64-manylinux_2_28"
    assert args[args.index("--python-version") + 1] == "3.11"
    assert args[args.index("--only-binary") + 1] == ":all:"
    assert "--generate-hashes" in args
    assert "--constraint" in args
    assert lock.pins == {"uvicorn": "0.40.0", "polars": "2.0.0"}
    assert lock.roots == ("uvicorn[standard]==0.40.0", "polars==2.0.0")
    assert lock.resolver == "uv 0.0.0 (fake)"


def test_an_index_credential_never_reaches_the_lock_or_its_record() -> None:
    lock = lock_requirements(["polars==2.0.0"], python_version="3.12")

    assert "s3cret" not in lock.requirements
    assert "deploy-bot" not in json.dumps(lock.provenance())
    assert lock.indexes == ("https://index.example/simple",)
    assert "# resolved from --index-url https://index.example/simple" in lock.requirements
    assert lock.provenance() == {
        "python_version": "3.12",
        "platform": TARGET_PLATFORM,
        "resolver": "uv 0.0.0 (fake)",
        "only_binary": True,
        "roots": ["polars==2.0.0"],
        "indexes": ["https://index.example/simple"],
        "pins": {"polars": "2.0.0"},
    }


def test_a_closure_that_cannot_be_locked_from_wheels_fails_naming_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_FAKE_UV_FAIL", "Because legacy-pkg has no wheels ...")

    with pytest.raises(DeployError, match=r"CPython 3\.11 on x86_64-manylinux_2_28") as failed:
        lock_requirements(["legacy-pkg==1.0"], python_version="3.11")
    assert "Because legacy-pkg has no wheels" in str(failed.value)


def test_a_host_without_uv_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(DeployError, match="needs uv, which is not on PATH"):
        real_uv_command()


# ---------------------------------------------------------------------------
# What a scoring image leaves out of haute's requirements
# ---------------------------------------------------------------------------


def test_haute_scoring_omissions_are_reviewed_against_haute_s_requirements() -> None:
    """Every haute requirement is the image's runtime, arrives through it, or is a
    reviewed omission: a requirement haute adds fails here until it is placed."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    core = {
        str(canonicalize_name(requirement.name))
        for requirement in map(Requirement, metadata.distribution("haute").requires or ())
        if requirement.marker is None or "extra" not in str(requirement.marker)
    }
    runtime = {name for name, _ in _SCORING_RUNTIME_DEPENDENCIES}

    assert HAUTE_SCORING_OMISSIONS <= core, "an omission names a requirement haute lacks"
    assert not HAUTE_SCORING_OMISSIONS & runtime
    # uvicorn[standard] brings these into every image; test_deploy_wheels proves it.
    assert core - runtime - HAUTE_SCORING_OMISSIONS == {
        "click",
        "python-dotenv",
        "watchfiles",
        "websockets",
    }


# ---------------------------------------------------------------------------
# The runtime check
# ---------------------------------------------------------------------------


def _install(site: Path, name: str, version: str, *requires: str) -> None:
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines += [f"Requires-Dist: {requirement}" for requirement in requires]
    (info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _image(tmp_path: Path) -> Path:
    """An image's site-packages: the lock below installed, haute beside it, and pip."""
    site = tmp_path / "site"
    _install(site, "pip", "24.0")
    _install(site, "fastapi", "0.139.2", "starlette>=0.40,<0.50", "pydantic>=2")
    _install(site, "starlette", "0.48.0", "anyio>=3.6")
    _install(site, "anyio", "4.16.0")
    _install(site, "pydantic", "2.12.5")
    _install(site, "uvicorn", "0.40.0", "click>=7", 'uvloop>=0.15; extra == "standard"')
    _install(site, "click", "8.4.2")
    _install(site, "uvloop", "0.22.1")
    _install(
        site,
        "haute",
        "0.7.1",
        "fastapi>=0.139",
        "optuna>=4.9",
        'pywin32; sys_platform == "win32"',
        'databricks-sdk; extra == "databricks"',
    )
    return site


_RECORD = {
    "roots": ["fastapi==0.139.2", "uvicorn[standard]==0.40.0"],
    "pins": {
        "fastapi": "0.139.2",
        "starlette": "0.48.0",
        "anyio": "4.16.0",
        "pydantic": "2.12.5",
        "uvicorn": "0.40.0",
        "click": "8.4.2",
        "uvloop": "0.22.1",
    },
    "haute_omitted": ["optuna"],
}


def _problems(site: Path, record: dict | None = None) -> list[str]:
    return runtime_problems(
        record or _RECORD, distributions=lambda: metadata.distributions(path=[str(site)])
    )


def test_an_image_that_installed_its_lock_passes(tmp_path: Path) -> None:
    # haute's omitted optuna, a Windows-only and an extra's requirement are not required.
    assert _problems(_image(tmp_path)) == []


def test_a_missing_transitive_dependency_fails(tmp_path: Path) -> None:
    site = _image(tmp_path)
    shutil.rmtree(next(site.glob("anyio-*.dist-info")))

    assert _problems(site) == [
        "anyio==4.16.0 is locked but not installed.",
        "starlette requires anyio>=3.6, which is not installed.",
    ]


def test_an_incompatible_version_fails(tmp_path: Path) -> None:
    site = _image(tmp_path)
    shutil.rmtree(next(site.glob("starlette-*.dist-info")))
    _install(site, "starlette", "0.52.0", "anyio>=3.6")

    assert _problems(site) == [
        "starlette is installed at 0.52.0, but locked at 0.48.0.",
        "fastapi requires starlette<0.50,>=0.40, but starlette 0.52.0 is installed.",
    ]


def test_a_distribution_the_lock_does_not_hold_fails(tmp_path: Path) -> None:
    site = _image(tmp_path)
    _install(site, "requests", "2.32.0")

    assert _problems(site) == ["requests is installed but the lock does not hold it."]


def test_haute_may_lack_only_what_the_record_omits(tmp_path: Path) -> None:
    """Never all of haute's missing requirements: only the reviewed omissions."""
    site = _image(tmp_path)

    assert _problems(site, {**_RECORD, "haute_omitted": []}) == [
        "haute requires optuna>=4.9, which is not installed."
    ]


def test_a_root_s_extras_are_required_of_it(tmp_path: Path) -> None:
    """uvicorn[standard] needs uvloop; uvicorn alone does not."""
    site = _image(tmp_path)
    shutil.rmtree(next(site.glob("uvloop-*.dist-info")))
    pins = {name: version for name, version in _RECORD["pins"].items() if name != "uvloop"}

    assert _problems(site, {**_RECORD, "pins": pins}) == [
        'uvicorn requires uvloop>=0.15; extra == "standard", which is not installed.'
    ]
    plain = {**_RECORD, "pins": pins, "roots": ["fastapi==0.139.2", "uvicorn==0.40.0"]}
    assert _problems(site, plain) == []
    assert selected_extras(["uvicorn[standard,other]==1", "fastapi==1"]) == {
        "uvicorn": frozenset({"standard", "other"})
    }


@pytest.mark.parametrize("installed", [None, "0.20"], ids=["missing", "incompatible"])
def test_an_extra_a_dependency_requests_is_required_of_its_target(
    tmp_path: Path, installed: str | None
) -> None:
    """starlette asks for anyio[trio]: anyio's trio requirement holds, though no root
    selects that extra."""
    site = _image(tmp_path)
    shutil.rmtree(next(site.glob("starlette-*.dist-info")))
    _install(site, "starlette", "0.48.0", "anyio[trio]>=3.6")
    shutil.rmtree(next(site.glob("anyio-*.dist-info")))
    _install(site, "anyio", "4.16.0", 'trio>=0.31; extra == "trio"')
    pins = dict(_RECORD["pins"])
    if installed is not None:
        _install(site, "trio", installed)
        pins["trio"] = installed

    problems = _problems(site, {**_RECORD, "pins": pins})

    requirement = 'trio>=0.31; extra == "trio"'
    assert problems == [
        f"anyio requires {requirement}, which is not installed."
        if installed is None
        else f"anyio requires {requirement}, but trio {installed} is installed."
    ]


def test_the_command_line_exits_non_zero_naming_each_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    site = _image(tmp_path)
    record = tmp_path / "dependency_lock.json"
    record.write_text(json.dumps(_RECORD))
    monkeypatch.setattr(
        metadata, "distributions", lambda: metadata.Distribution.discover(path=[str(site)])
    )

    assert check_main([str(record)]) == 0
    assert "matches the lock" in capsys.readouterr().out

    shutil.rmtree(next(site.glob("click-*.dist-info")))
    assert check_main([str(record)]) == 1
    assert "runtime check: click==8.4.2 is locked but not installed." in capsys.readouterr().err
