"""The ``[workbench]`` table of ``haute.toml``: whether the project's workbench is enabled
(specs/workbench).

A project's workbench is a fact about the project, so it is recorded with the project's
other facts, versioned and reviewed with them::

    [workbench]
    enabled = true

    [workbench.app]
    host = "azure-container-apps"   # optional; where the underwriter app runs
    edit_role = "Quotes.Edit"        # optional; who may change quotes in it

Without the table the project has no workbench. With it, ``enabled`` is required; ``app``,
when given, holds ``host``, one of ``APP_HOSTS``, and ``edit_role``, the role the host signs
in with which an underwriter may change quotes, every other one reading them. The form is
the pipeline folder's ``workbench/`` folder, the pipeline folder being the folder of the
project's pipeline file, and is never configured. The table is read from disk whenever a
workbench route needs it, so an edit reaches the next request.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from haute._project import resolve_pipeline_file
from haute._sandbox import contained_path
from haute.errors import ConfigError, HauteError, PathOutsideProjectError

#: The keys the table may hold. ``haute deploy``'s whole-file check of ``haute.toml``
#: (``haute.deploy._config``) accepts exactly these under ``[workbench]``.
WORKBENCH_TOML_KEYS: frozenset[str] = frozenset({"enabled", "app"})
#: Where the underwriter app can run: the host whose sign-in fronts it.
APP_HOSTS: frozenset[str] = frozenset({"azure-container-apps"})
#: The form folder's name in the pipeline folder.
FORM_FOLDER = "workbench"

_TABLE = "workbench"

#: Where an earlier Haute named the form's one file; refused saying what replaced it.
_REMOVED_FORM_KEY = (
    "[workbench].form was removed: the form is the pipeline folder's workbench/ folder; "
    "delete the key"
)


class WorkbenchError(HauteError):
    """A workbench problem the analyst fixes in the project: its table or its form.

    A workbench route answers one as 409 with its message (``haute.routes._error_handlers``),
    so the editor shows what to fix and the pipeline editor goes on working.
    """


class WorkbenchConfigError(WorkbenchError, ConfigError):
    """The ``[workbench]`` table, or the ``haute.toml`` holding it, cannot be read as settings."""


@dataclass(frozen=True, slots=True)
class WorkbenchConfig:
    """The table as read: whether the workbench is enabled, and where its form folder is."""

    enabled: bool
    project_root: Path
    #: The form folder's path from the project root, posix (``rating/workbench`` in a new
    #: project), as the status and every message name it; empty while the workbench is
    #: disabled, when nothing reads a form.
    form: str = ""
    #: Where the underwriter app runs (``[workbench.app] host``); None when unsaid.
    app_host: str | None = None
    #: The role that may change quotes in the app (``[workbench.app] edit_role``); None lets
    #: everyone signed in change them.
    app_edit_role: str | None = None

    @property
    def form_folder(self) -> Path:
        """The form folder; a disabled workbench has none."""
        if not self.enabled:
            raise WorkbenchConfigError("This project's workbench is not enabled.")
        return self.project_root / self.form


def read_workbench_config(project_root: Path) -> WorkbenchConfig:
    """The ``[workbench]`` table of *project_root*'s ``haute.toml``; disabled without one.

    Raises :class:`WorkbenchConfigError` when the file cannot be read or parsed, the table is
    not as the module docstring describes, or, while enabled, the project has no pipeline
    file or its form folder resolves outside the project.
    """
    toml_path = project_root / "haute.toml"
    disabled = WorkbenchConfig(enabled=False, project_root=project_root)
    if not toml_path.is_file():
        return disabled
    try:
        with toml_path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise WorkbenchConfigError(
            f"haute.toml could not be read: {exc}", path=str(toml_path)
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise WorkbenchConfigError(
            f"haute.toml is malformed and could not be parsed: {exc}", path=str(toml_path)
        ) from exc
    except UnicodeDecodeError as exc:
        raise WorkbenchConfigError(
            f"haute.toml is not UTF-8 text: {exc}", path=str(toml_path)
        ) from exc

    table = data.get(_TABLE)
    if table is None:
        return disabled
    if not isinstance(table, dict):
        raise WorkbenchConfigError("[workbench] must be a TOML table", path=str(toml_path))
    if "form" in table:
        raise WorkbenchConfigError(_REMOVED_FORM_KEY, path=str(toml_path))
    unknown = sorted(set(table) - WORKBENCH_TOML_KEYS)
    if unknown:
        raise WorkbenchConfigError(
            f"[workbench] has unknown key(s): {', '.join(unknown)}; the keys are enabled and app",
            path=str(toml_path),
        )
    enabled = table.get("enabled")
    if not isinstance(enabled, bool):
        raise WorkbenchConfigError("[workbench].enabled must be true or false", path=str(toml_path))
    app_host, app_edit_role = _app_settings(table.get("app"), toml_path)
    if not enabled:
        return WorkbenchConfig(
            enabled=False,
            project_root=project_root,
            app_host=app_host,
            app_edit_role=app_edit_role,
        )
    return WorkbenchConfig(
        enabled=True,
        project_root=project_root,
        form=_form_folder(project_root, toml_path),
        app_host=app_host,
        app_edit_role=app_edit_role,
    )


def _app_settings(raw: object, toml_path: Path) -> tuple[str | None, str | None]:
    """``[workbench.app]``'s ``host``, one of ``APP_HOSTS``, and ``edit_role``, a role's
    name; each None when unsaid, both without the table."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise WorkbenchConfigError("[workbench.app] must be a TOML table", path=str(toml_path))
    unknown = sorted(set(raw) - {"host", "edit_role"})
    if unknown:
        raise WorkbenchConfigError(
            f"[workbench.app] has unknown key(s): {', '.join(unknown)}; the keys are host and "
            "edit_role",
            path=str(toml_path),
        )
    host = raw.get("host")
    if not isinstance(host, str) or host not in APP_HOSTS:
        raise WorkbenchConfigError(
            f"[workbench.app].host must be one of {', '.join(sorted(APP_HOSTS))}; got {host!r}",
            path=str(toml_path),
        )
    edit_role = raw.get("edit_role")
    if edit_role is not None and (not isinstance(edit_role, str) or not edit_role.strip()):
        raise WorkbenchConfigError(
            "[workbench.app].edit_role must be the name of a role the host signs underwriters "
            f"in with; got {edit_role!r}",
            path=str(toml_path),
        )
    return str(host), None if edit_role is None else edit_role.strip()


def _form_folder(project_root: Path, toml_path: Path) -> str:
    """The form folder's path from *project_root*: the pipeline folder's ``workbench/``."""
    try:
        pipeline_file = resolve_pipeline_file(project_root)
    except FileNotFoundError as exc:
        raise WorkbenchConfigError(
            f"The workbench needs the project's pipeline file to find its form: {exc}",
            path=str(toml_path),
        ) from exc
    root = project_root.resolve()
    folder = pipeline_file.resolve().parent / FORM_FOLDER
    try:
        relative = folder.relative_to(root).as_posix()
    except ValueError as exc:
        raise WorkbenchConfigError(
            "The pipeline folder is outside the project, so the workbench's form folder "
            f"would be too: {folder}",
            path=str(toml_path),
        ) from exc
    # A `workbench` folder that is a symlink or junction to somewhere outside the project is
    # refused: the workbench never reads a file outside it.
    try:
        contained_path(project_root, relative)
    except PathOutsideProjectError as exc:
        raise WorkbenchConfigError(
            f"{relative} must stay inside the project", path=str(toml_path)
        ) from exc
    return relative
