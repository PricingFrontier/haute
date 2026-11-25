"""Haute's own folder under the system temporary folder: one per user, private.

Every file Haute keeps in the temporary folder under a fixed name (training and
optimiser artifacts, the project, form folder, output and writer locks, a command's
incident record outside a project) is under :func:`haute_temp_root`, never directly
under the shared folder, where another local user could make the name first and then
read, or replace, what Haute puts there. ``tempfile.gettempdir`` is called nowhere
else in ``src/haute`` (``tests/test_temp_folder.py`` scans for it).

Design: specs/sandbox-security/high-level.md (Trust boundary, Haute's temporary folder).
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

from haute.errors import HauteError

#: The permission bits of a root open to anyone but its owner.
_OPEN_TO_OTHERS = 0o077


class TemporaryFolderError(HauteError):
    """Haute's temporary folder is not a private folder of the user running Haute."""


def haute_temp_root() -> Path:
    """Haute's folder in the system temporary folder, made if missing and checked.

    The temporary folder is read at each call, as ``tempfile`` reads it. On POSIX the
    folder is ``haute-<uid>``, made private (``0700``), and answered only while it is a
    real folder, not a link, that the user running Haute owns and no one else may open;
    otherwise :class:`TemporaryFolderError` names it and what to do. On Windows, whose
    temporary folder is the user's own, it is ``haute``.
    """
    temporary = Path(tempfile.gettempdir())
    if sys.platform == "win32":
        root = temporary / "haute"
        root.mkdir(exist_ok=True)
        return root
    uid = os.getuid()
    root = temporary / f"haute-{uid}"
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    else:
        # A umask can only take bits away, but the folder must be exactly the owner's.
        Path(root).chmod(0o700)
    status = os.lstat(root)
    if not stat.S_ISDIR(status.st_mode):
        raise TemporaryFolderError(
            f"Haute keeps its temporary files in {root}, which is not a folder: remove it, "
            "then start Haute again."
        )
    if status.st_uid != uid:
        raise TemporaryFolderError(
            f"Haute keeps its temporary files in {root}, which belongs to user "
            f"{status.st_uid}, not you: remove it, or set TMPDIR to a folder of your own, "
            "then start Haute again."
        )
    if status.st_mode & _OPEN_TO_OTHERS:
        raise TemporaryFolderError(
            f"Haute keeps its temporary files in {root}, which other users can open "
            f"(mode {stat.S_IMODE(status.st_mode):o}): run `chmod 700 {root}`, then start "
            "Haute again."
        )
    return root
