"""The haute a Databricks-served model installs: the one that validated its deploy.

Model Serving builds a model's environment from the package index when its endpoint
is created or updated, and the model runs the haute it installs there. A deploy that
runs on a released haute (one installed from an index) pins that release. Any other
haute, installed from a Git repository, an archive or a local directory, is not the
code the index holds under its version number, so the deploy ships it inside the model
as a wheel repacked from its installed files, and the environment installs that wheel
(the private-library route Model Serving documents: a ``code_paths`` wheel required as
``code/<wheel>``). An editable checkout is refused: its files are a working tree, and
deploys run in CI, which installs haute as a package.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path, PurePosixPath, PureWindowsPath

from haute._install_source import install_source
from haute.errors import DeployError

_DISTRIBUTION = "haute"

# Files an installer writes into ``.dist-info``, which a wheel never carries: pip and
# uv's own records, and the RECORD the repacked wheel replaces.
_INSTALLER_FILES = frozenset(
    {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json", "uv_build.json"}
)

# Fixed entry timestamps (the zip format's earliest), so one install repacks to the
# same bytes on every deploy.
_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ServedHaute:
    """How the served environment installs haute: a requirement, and a wheel it names."""

    requirement: str
    wheel: Path | None = None


def served_haute(directory: Path) -> ServedHaute:
    """The haute requirement for a served model, repacking a wheel into *directory* if needed.

    A released haute is ``haute==<version>``; any other install is the wheel this writes
    into *directory*, required as ``code/<wheel>`` because MLflow logs a ``code_paths``
    file under the model's ``code/`` folder. An editable install raises ``DeployError``.
    """
    distribution = metadata.distribution(_DISTRIBUTION)
    kind, location = install_source(distribution)
    if kind == "wheel":
        return ServedHaute(f"{_DISTRIBUTION}=={distribution.version}")
    if kind == "editable":
        raise DeployError(
            f"haute is an editable install of {location}: a served model must install a "
            "packaged haute, and an editable checkout's files are its working tree. Deploy "
            "from CI, which installs haute as a package, or reinstall it here without -e."
        )
    wheel = repack_installed_wheel(distribution, directory)
    return ServedHaute(f"code/{wheel.name}", wheel)


def _wheel_tag(distribution: metadata.Distribution) -> str:
    """The wheel tag the installed ``WHEEL`` file records, compressed as a filename takes it."""
    tags = [
        line.split(":", 1)[1].strip()
        for line in (distribution.read_text("WHEEL") or "").splitlines()
        if line.startswith("Tag:")
    ]
    if not tags:
        raise DeployError(
            "haute's installed WHEEL file names no tag, so it cannot be repacked as a wheel "
            "for the served model. Reinstall haute and deploy again."
        )
    parts = [tag.split("-") for tag in tags]
    return "-".join(".".join(dict.fromkeys(tag[index] for tag in parts)) for index in range(3))


def _entry(arcname: str) -> zipfile.ZipInfo:
    """A compressed, world-readable archive entry with the fixed timestamp."""
    info = zipfile.ZipInfo(arcname, _ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={digest.decode('ascii')}"


def _recorded_paths(distribution: metadata.Distribution) -> list[PurePosixPath]:
    """Every path the installed RECORD lists, read from the file itself.

    The distribution's own file list leaves out a recorded file missing from disk (from
    Python 3.12), and a repack must refuse an incomplete install, not ship it.
    """
    record = distribution.read_text("RECORD")
    if not record:
        raise DeployError(
            "haute's installation has no RECORD of its files, so it cannot be repacked as "
            "a wheel for the served model. Reinstall haute and deploy again."
        )
    return [PurePosixPath(row[0]) for row in csv.reader(record.splitlines()) if row and row[0]]


def _outside_install_root(path: PurePosixPath) -> bool:
    """Whether the install put *path* outside its root: a console script, recorded
    relative to it (``../../bin/haute``), or absolute when on another drive."""
    return ".." in path.parts or bool(PureWindowsPath(path.as_posix()).anchor)


def _dist_info(paths: list[PurePosixPath]) -> str:
    """The ``.dist-info`` folder the record lists the install's METADATA in."""
    for path in paths:
        if path.name == "METADATA" and len(path.parts) == 2 and path.parent.suffix == ".dist-info":
            return path.parts[0]
    raise DeployError(
        "haute's installation record lists no .dist-info/METADATA, so it cannot be repacked "
        "as a wheel for the served model. Reinstall haute."
    )


def repack_installed_wheel(distribution: metadata.Distribution, directory: Path) -> Path:
    """Write *distribution*'s installed files into a wheel in *directory*; return its path.

    The installed RECORD lists every file: those under the install root become the
    wheel, with the ``.dist-info`` metadata but not the installer's records, a new
    RECORD written last; scripts installed outside it (``../bin/haute``, or an absolute
    path) are left out, since an install regenerates them from ``entry_points.txt``; and
    compiled
    ``__pycache__`` files are left out. The wheel is named after the ``.dist-info``
    folder, as the wheel haute was installed from was, so its name and its metadata
    agree. A missing record, a record without METADATA, or a listed file that is not on
    disk raises ``DeployError``.
    """
    paths = _recorded_paths(distribution)
    info_dir = _dist_info(paths)
    entries: list[tuple[str, Path]] = []
    for path in paths:
        if _outside_install_root(path) or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.parent == PurePosixPath(info_dir) and path.name in _INSTALLER_FILES:
            continue
        source = Path(str(distribution.locate_file(path)))
        if not source.is_file():
            raise DeployError(
                f"haute's installed file {path} is missing from {source.parent}, so haute "
                "cannot be repacked as a wheel for the served model. Reinstall haute."
            )
        entries.append((path.as_posix(), source))

    directory.mkdir(parents=True, exist_ok=True)
    stem = info_dir.removesuffix(".dist-info")
    wheel = directory / f"{stem}-{_wheel_tag(distribution)}.whl"
    record = io.StringIO()
    rows = csv.writer(record, lineterminator="\n")
    with zipfile.ZipFile(wheel, "w") as archive:
        for arcname, source in sorted(entries):
            data = source.read_bytes()
            archive.writestr(_entry(arcname), data)
            rows.writerow([arcname, _record_digest(data), len(data)])
        rows.writerow([f"{info_dir}/RECORD", "", ""])
        archive.writestr(_entry(f"{info_dir}/RECORD"), record.getvalue())
    return wheel
