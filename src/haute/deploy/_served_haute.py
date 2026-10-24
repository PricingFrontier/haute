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
import hashlib
import re
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path, PurePosixPath

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


def _escape(component: str) -> str:
    """A wheel filename component (PEP 427): runs of ``-_.`` become ``_``."""
    return re.sub(r"[-_.]+", "_", component)


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


def repack_installed_wheel(distribution: metadata.Distribution, directory: Path) -> Path:
    """Write *distribution*'s installed files into a wheel in *directory*; return its path.

    The installed RECORD lists every file: those under the install root become the
    wheel, with the ``.dist-info`` metadata but not the installer's records, a new
    RECORD written last; scripts installed outside it (``../bin/haute``) are left out,
    since an install regenerates them from ``entry_points.txt``; and compiled
    ``__pycache__`` files are left out. A missing record or a listed file that is not on
    disk raises ``DeployError``.
    """
    files = distribution.files
    if not files:
        raise DeployError(
            "haute's installation has no RECORD of its files, so it cannot be repacked as "
            "a wheel for the served model. Reinstall haute and deploy again."
        )
    name = distribution.metadata["Name"]
    info_dir = f"{_escape(name)}-{distribution.version}.dist-info"
    entries: list[tuple[str, Path]] = []
    for path in files:
        posix = PurePosixPath(path.as_posix())
        if ".." in posix.parts or "__pycache__" in posix.parts or posix.suffix == ".pyc":
            continue
        if posix.parts[0] == info_dir and posix.name in _INSTALLER_FILES:
            continue
        source = Path(str(distribution.locate_file(path)))
        if not source.is_file():
            raise DeployError(
                f"haute's installed file {posix} is missing from {source.parent}, so haute "
                "cannot be repacked as a wheel for the served model. Reinstall haute."
            )
        entries.append((posix.as_posix(), source))
    if not any(arcname == f"{info_dir}/METADATA" for arcname, _ in entries):
        raise DeployError(
            f"haute's installation record lists no {info_dir}/METADATA, so it cannot be "
            "repacked as a wheel for the served model. Reinstall haute."
        )

    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"{_escape(name)}-{distribution.version}-{_wheel_tag(distribution)}.whl"
    record: list[str] = []
    with zipfile.ZipFile(wheel, "w") as archive:
        for arcname, source in sorted(entries):
            data = source.read_bytes()
            archive.writestr(_entry(arcname), data)
            record.append(f"{arcname},{_record_digest(data)},{len(data)}")
        record.append(f"{info_dir}/RECORD,,")
        archive.writestr(_entry(f"{info_dir}/RECORD"), "\n".join(record) + "\n")
    return wheel
