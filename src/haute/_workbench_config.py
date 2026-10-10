"""The ``[workbench]`` table of ``haute.toml``: whether the project's workbench is enabled
and where its form is (specs/workbench).

A project's workbench is a fact about the project, so it is recorded with the project's
other facts, versioned and reviewed with them::

    [workbench]
    enabled = true
    form = "forms/form.json"   # optional; this is the default

Without the table the project has no workbench. With it, ``enabled`` is required and
``form`` is a path relative to the project root that stays inside it. The table is read
from disk whenever a workbench route needs it, so an edit reaches the next request.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from haute._sandbox import contained_path
from haute.errors import ConfigError, HauteError, PathOutsideProjectError

#: The keys the table may hold. ``haute deploy``'s whole-file check of ``haute.toml``
#: (``haute.deploy._config``) accepts exactly these under ``[workbench]``.
WORKBENCH_TOML_KEYS: frozenset[str] = frozenset({"enabled", "form"})
DEFAULT_FORM_PATH = "forms/form.json"

_TABLE = "workbench"


class WorkbenchError(HauteError):
    """A workbench problem the analyst fixes in the project: its table or its form file.

    A workbench route answers one as 409 with its message (``haute.routes._error_handlers``),
    so the editor shows what to fix and the pipeline editor goes on working.
    """


class WorkbenchConfigError(WorkbenchError, ConfigError):
    """The ``[workbench]`` table, or the ``haute.toml`` holding it, cannot be read as settings."""


@dataclass(frozen=True, slots=True)
class WorkbenchConfig:
    """The table as read: whether the workbench is enabled and where its form is."""

    enabled: bool
    #: The form's path as ``haute.toml`` names it, or the default, relative to the project root.
    form: str
    project_root: Path

    @property
    def form_path(self) -> Path:
        return self.project_root / self.form


def read_workbench_config(project_root: Path) -> WorkbenchConfig:
    """The ``[workbench]`` table of *project_root*'s ``haute.toml``; disabled without one.

    Raises :class:`WorkbenchConfigError` when the file cannot be read or parsed, or the
    table is not as the module docstring describes.
    """
    toml_path = project_root / "haute.toml"
    disabled = WorkbenchConfig(enabled=False, form=DEFAULT_FORM_PATH, project_root=project_root)
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
    unknown = sorted(set(table) - WORKBENCH_TOML_KEYS)
    if unknown:
        raise WorkbenchConfigError(
            f"[workbench] has unknown key(s): {', '.join(unknown)}; the keys are enabled and form",
            path=str(toml_path),
        )
    enabled = table.get("enabled")
    if not isinstance(enabled, bool):
        raise WorkbenchConfigError("[workbench].enabled must be true or false", path=str(toml_path))
    form = _form(table.get("form"), project_root, toml_path)
    return WorkbenchConfig(enabled=enabled, form=form, project_root=project_root)


def _form(raw: object, project_root: Path, toml_path: Path) -> str:
    """``form`` as written, a relative path inside the project; the default without one."""
    if raw is None:
        form = DEFAULT_FORM_PATH
    elif not isinstance(raw, str) or not raw.strip():
        raise WorkbenchConfigError(
            "[workbench].form must be a path, relative to the project root, such as "
            f"{DEFAULT_FORM_PATH!r}",
            path=str(toml_path),
        )
    elif Path(raw).is_absolute():
        raise WorkbenchConfigError(
            "[workbench].form must be relative to the project root, not absolute",
            path=str(toml_path),
            form=raw,
        )
    else:
        form = raw
    # The default path is checked like a configured one, through the one containment check:
    # a `forms` folder that is a symlink or junction to somewhere outside the project is
    # refused too, since the workbench must never read a file outside it.
    try:
        contained_path(project_root, form)
    except PathOutsideProjectError as exc:
        raise WorkbenchConfigError(
            "[workbench].form must stay inside the project", path=str(toml_path), form=form
        ) from exc
    return form
