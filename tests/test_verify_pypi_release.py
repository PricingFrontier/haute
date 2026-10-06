from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import verify_pypi_release as verify

_WHEEL = "haute-0.4.0-py3-none-any.whl"
_SDIST = "haute-0.4.0.tar.gz"


def _built(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / _WHEEL).write_bytes(b"wheel bytes")
    (dist / _SDIST).write_bytes(b"sdist bytes")
    return dist


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _published(tmp_path: Path, files: dict[str, bytes]) -> Path:
    release_json = tmp_path / "release.json"
    release_json.write_text(
        json.dumps(
            {
                "info": {"version": "0.4.0"},
                "urls": [
                    {"filename": name, "digests": {"sha256": _sha(content), "md5": "unused"}}
                    for name, content in files.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    return release_json


def _run(dist: Path, release_json: Path) -> int:
    return verify.main(["--dist", str(dist), "--pypi-json", str(release_json)])


def test_a_complete_release_matches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = _built(tmp_path)
    release_json = _published(tmp_path, {_WHEEL: b"wheel bytes", _SDIST: b"sdist bytes"})

    assert _run(dist, release_json) == 0
    assert capsys.readouterr().out == "PyPI serves exactly the 2 built files.\n"


def test_a_partial_upload_names_the_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The wheel made it and the sdist did not: the release is not complete, so
    # it is not tagged until a re-run of the publish uploads the sdist.
    dist = _built(tmp_path)
    release_json = _published(tmp_path, {_WHEEL: b"wheel bytes"})

    assert _run(dist, release_json) == 1
    assert capsys.readouterr().out == f"{_SDIST} is not on PyPI\n"


def test_a_file_with_other_content_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = _built(tmp_path)
    release_json = _published(tmp_path, {_WHEEL: b"another build", _SDIST: b"sdist bytes"})

    assert _run(dist, release_json) == 1
    assert capsys.readouterr().out == (
        f"{_WHEEL} on PyPI has SHA-256 {_sha(b'another build')}, "
        f"not the built {_sha(b'wheel bytes')}\n"
    )


def test_a_file_this_run_did_not_build_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = _built(tmp_path)
    extra = "haute-0.4.0-cp312-cp312-win_amd64.whl"
    release_json = _published(
        tmp_path, {_WHEEL: b"wheel bytes", _SDIST: b"sdist bytes", extra: b"other"}
    )

    assert _run(dist, release_json) == 1
    assert capsys.readouterr().out == f"{extra} is on PyPI but was not built by this run\n"


def test_an_empty_build_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    release_json = _published(tmp_path, {})

    assert _run(dist, release_json) == 1
    assert capsys.readouterr().out == f"No built files in {dist}.\n"
