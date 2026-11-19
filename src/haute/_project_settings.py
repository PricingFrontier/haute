"""The one parse of a project's ``haute.toml``.

Every reader of the file takes its table from :func:`read_project_settings` and
opens the file nowhere else (``tests/test_project_settings_lint.py``). The parse
is made afresh for each call, so an edit to the file reaches the next call of a
running server; nothing holds the settings for the life of a process.

The rules it states, which every command and the editor follow:

* **Encoding.** The file is UTF-8. A leading UTF-8 byte-order mark is read
  through, as Haute reads a JSON input's; any other byte that is not UTF-8 is
  refused, naming the byte's position.
* **Tables and keys.** Every table and key Haute knows is checked across the
  whole file, and an unknown one is refused, named where it is together with the
  keys or tables that place takes. A table's own reader checks its values.
* **Paths.** A path the file names is read by the rule a node's path is read by:
  a backslash is a separator on every system. A path that starts or ends with a
  space is refused, and the configured pipeline must lie inside the project.
"""

from __future__ import annotations

import copy
import functools
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haute.errors import ConfigError, InvalidPathError, PathOutsideProjectError

__all__ = [
    "SETTINGS_FILE",
    "ProjectSettings",
    "ProjectSettingsError",
    "has_settings",
    "read_project_settings",
    "settings_path",
    "write_settings_table",
    "write_settings_text",
]

#: The project's settings file, at its root.
SETTINGS_FILE = "haute.toml"

_BOM = b"\xef\xbb\xbf"

#: The keys holding a path, by table: read by the rule a node's path is read by.
PATH_SETTINGS: frozenset[tuple[str, str]] = frozenset(
    {("project", "pipeline"), ("test_quotes", "dir"), ("mlflow", "folder")}
)


class ProjectSettingsError(ConfigError):
    """``haute.toml`` cannot be read as settings: its message names the file and what is wrong.

    It carries the file, the bytes the parse read and their text (a byte that is not UTF-8
    shown replaced), and the line and column TOML named, so the editor shows the file with
    the fault marked, at the revision of those bytes, without opening it again.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path,
        text: str = "",
        line: int | None = None,
        column: int | None = None,
        raw: bytes = b"",
    ) -> None:
        super().__init__(message)
        self.path = path
        self.text = text
        self.line = line
        self.column = column
        self.raw = raw

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            _restore_settings_error,
            (type(self), self.message, self.path, self.text, self.line, self.column, self.raw),
        )


def _restore_settings_error(
    error_type: type[ProjectSettingsError],
    message: str,
    path: Path,
    text: str,
    line: int | None,
    column: int | None,
    raw: bytes,
) -> ProjectSettingsError:
    """A :class:`ProjectSettingsError` as a worker sent it."""
    return error_type(message, path=path, text=text, line=line, column=column, raw=raw)


def settings_path(folder: Path) -> Path:
    """Where *folder*'s ``haute.toml`` is, whether or not it exists."""
    return folder / SETTINGS_FILE


def has_settings(folder: Path) -> bool:
    """Whether *folder* holds a ``haute.toml``: what the project finder looks for."""
    return settings_path(folder).is_file()


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    """One reading of a project's ``haute.toml``, checked whole.

    Each table's reader takes its table with :meth:`table`, a copy, so no reader
    changes what another reads.
    """

    #: The file read.
    path: Path
    #: Its text, a leading byte-order mark taken off.
    text: str
    _document: Mapping[str, Any]

    @property
    def folder(self) -> Path:
        """The folder holding the file: the project's root."""
        return self.path.parent

    def table(self, name: str) -> dict[str, Any] | None:
        """The top-level table *name*, or ``None`` when the file has none."""
        value = self._document.get(name)
        return None if value is None else copy.deepcopy(value)

    def document(self) -> dict[str, Any]:
        """The whole file, parsed: for a reader of every table, as ``haute init``'s re-run is."""
        return copy.deepcopy(dict(self._document))

    def path_setting(self, table: str, key: str) -> str | None:
        """The path *table*'s *key* names, by the node-path rule; ``None`` when the table does
        not set it. An empty string is kept: what it means is its reader's to say."""
        if (table, key) not in PATH_SETTINGS:
            raise KeyError(f"[{table}] {key} is not a path setting of haute.toml.")
        section = self._document.get(table)
        raw = None if section is None else section.get(key)
        return None if raw is None else _path_text(raw)

    @property
    def configured_pipeline(self) -> Path | None:
        """``[project].pipeline`` as a path under the root, unresolved; ``None`` when unset.

        The parse has checked that it lies inside the project. Whether the file is there and
        holds a pipeline is the resolver's question (``haute._project``).
        """
        pipeline = self.path_setting("project", "pipeline")
        # An empty path configures no pipeline, as an absent one does.
        return self.folder / pipeline if pipeline else None


def read_project_settings(folder: Path) -> ProjectSettings | None:
    """The settings of *folder*'s ``haute.toml``, read now; ``None`` when it has none.

    Raises :class:`ProjectSettingsError` naming the file and the fault for a byte that is not
    UTF-8, malformed TOML, an unknown table or key, a table that is not one, a value of the
    wrong type for a path, or a configured pipeline outside the project. A file the system
    will not let Haute read raises its ``OSError``.
    """
    path = settings_path(folder)
    if not path.is_file():
        return None
    raw = path.read_bytes()
    return _parse(path, raw)


def write_settings_text(folder: Path, text: str) -> Path:
    """Write *text* as *folder*'s ``haute.toml``, LF line endings, and return the file.

    For a writer of the whole file (``haute init``, a repair preview's stand-in project); the
    written text must read back as settings.
    """
    from haute._scaffold_files import write_lf

    path = settings_path(folder)
    _parse(path, text.encode("utf-8"))
    write_lf(path, text)
    return path


def write_settings_table(
    folder: Path, name: str, table: Mapping[str, Any], *, write: Callable[[Path, str], None]
) -> None:
    """Replace the top-level table *name* of *folder*'s ``haute.toml``, keeping the rest as written.

    The file is read by the one parse first, so a file it refuses is not rewritten, and the
    result must read back as settings. *write* writes the text atomically to the file (the
    caller's containment check decides where).
    """
    import tomlkit
    from tomlkit.exceptions import TOMLKitError

    current = read_project_settings(folder)
    path = settings_path(folder)
    try:
        document = tomlkit.parse(current.text) if current is not None else tomlkit.document()
    except TOMLKitError as exc:  # the parse read it; tomlkit must too
        raise ProjectSettingsError(
            f"{SETTINGS_FILE} ({path}) is malformed and could not be parsed: {exc}",
            path=path,
            text=current.text if current is not None else "",
        ) from exc
    replacement = tomlkit.table()
    for key, value in table.items():
        replacement[key] = value
    document[name] = replacement
    text = tomlkit.dumps(document)
    _parse(path, text.encode("utf-8"))
    write(path, text)


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


_TOML_POSITION = re.compile(r"\(at line (\d+), column (\d+)\)")


def _parse(path: Path, raw: bytes) -> ProjectSettings:
    try:
        return _parse_bytes(path, raw)
    except ProjectSettingsError as exc:
        # The bytes refused, so the editor's document of them carries their revision.
        exc.raw = raw
        raise


def _parse_bytes(path: Path, raw: bytes) -> ProjectSettings:
    body = raw.removeprefix(_BOM)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        position = exc.start + len(raw) - len(body)
        shown = body.decode("utf-8", errors="replace")
        line = body.count(b"\n", 0, exc.start) + 1
        raise ProjectSettingsError(
            f"{SETTINGS_FILE} ({path}) is not UTF-8 text: the byte at position {position} is "
            "not UTF-8. Save the file as UTF-8.",
            path=path,
            text=shown,
            line=line,
        ) from None
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        at = _TOML_POSITION.search(str(exc))
        raise ProjectSettingsError(
            f"{SETTINGS_FILE} ({path}) is malformed and could not be parsed: {exc}",
            path=path,
            text=text,
            line=int(at.group(1)) if at else None,
            column=int(at.group(2)) if at else None,
        ) from None
    problems = _schema_problems(document)
    if problems:
        raise ProjectSettingsError(
            f"{SETTINGS_FILE} ({path}) cannot be read as settings: " + "; ".join(problems) + ".",
            path=path,
            text=text,
        )
    _check_paths(path, text, document)
    return ProjectSettings(path=path, text=text, _document=document)


#: A table's schema: its own keys, its sub-tables' schemas, and keys an earlier Haute read that
#: are refused in their own words.
@dataclass(frozen=True, slots=True)
class _Table:
    keys: frozenset[str] = frozenset()
    tables: Mapping[str, _Table] | None = None
    retired: Mapping[str, str] | None = None


@functools.cache
def _schema() -> Mapping[str, _Table]:
    """Every table and key Haute reads from ``haute.toml``.

    The assistant's and the workbench's key sets are owned by their readers and imported
    here when first needed, so neither can drift from this check.
    """
    from haute._workbench_config import WORKBENCH_RETIRED_KEYS, WORKBENCH_TOML_KEYS
    from haute.assistant._config import ASSISTANT_EGRESS_TOML_KEYS, ASSISTANT_TOML_KEYS

    def table(*keys: str, **tables: _Table) -> _Table:
        return _Table(frozenset(keys), tables or None)

    return {
        "project": table("name", "pipeline"),
        "deploy": table(
            "target",
            "model_name",
            "endpoint_name",
            "output_fields",
            databricks=table(
                "experiment_name",
                "catalog",
                "schema",
                "serving_workload_size",
                "serving_scale_to_zero",
            ),
            container=table("registry", "port", "base_image"),
            **{
                "azure-container-apps": table(
                    "resource_group", "container_app_name", "environment_name"
                ),
                "aws-ecs": table("region", "cluster", "service"),
                "gcp-run": table("project", "region", "service"),
            },
        ),
        "test_quotes": table("dir"),
        # The MLflow destination inventory, written by the MLflow settings endpoint.
        "mlflow": table("tracking_uri", "folder"),
        "safety": table("impact_dataset", approval=table("min_approvers")),
        "ci": table(
            "provider",
            staging=table("endpoint_suffix", "endpoint_url"),
            production=table("endpoint_url", "environment"),
        ),
        "server": table("host"),
        "assistant": table(
            *(ASSISTANT_TOML_KEYS - {"egress"}), egress=table(*ASSISTANT_EGRESS_TOML_KEYS)
        ),
        # The workbench's sub-tables name its environments, which its reader checks.
        "workbench": _Table(WORKBENCH_TOML_KEYS, None, WORKBENCH_RETIRED_KEYS),
    }


def _listed(names: frozenset[str] | set[str]) -> str:
    ordered = sorted(names)
    if len(ordered) <= 1:
        return "".join(ordered)
    return ", ".join(ordered[:-1]) + " and " + ordered[-1]


def _schema_problems(document: Mapping[str, Any]) -> list[str]:
    schema = _schema()
    problems: list[str] = []
    for name in document:
        if name not in schema:
            problems.append(f"unknown table [{name}] (the tables are {_listed(set(schema))})")
    for name, spec in schema.items():
        if name in document:
            _check_table(name, document[name], spec, problems)
    return problems


def _check_table(where: str, value: object, spec: _Table, problems: list[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"[{where}] must be a table, not {value!r}")
        return
    sub_tables = spec.tables or {}
    retired = spec.retired or {}
    allowed = spec.keys | set(sub_tables)
    for key in sorted(value):
        if key in retired:
            problems.append(f"[{where}] {key} {retired[key]}")
        elif key not in allowed:
            problems.append(f"[{where}] unknown key '{key}' (the keys are {_listed(allowed)})")
    for key, sub_spec in sub_tables.items():
        if key in value:
            _check_table(f"{where}.{key}", value[key], sub_spec, problems)


def _path_text(raw: str) -> str:
    """A path setting as the node-path rule reads it: a backslash is a separator."""
    from haute._path_resolution import _normalise_path_text

    return _normalise_path_text(raw)


def _check_paths(path: Path, text: str, document: Mapping[str, Any]) -> None:
    for table, key in sorted(PATH_SETTINGS):
        section = document.get(table)
        if section is None or key not in section:
            continue
        value = section[key]
        where = f"[{table}] {key}"
        if not isinstance(value, str):
            raise ProjectSettingsError(
                f"{SETTINGS_FILE} ({path}) {where} must be a path written as a string, not "
                f"{value!r}.",
                path=path,
                text=text,
            )
        if value != value.strip():
            raise ProjectSettingsError(
                f"{SETTINGS_FILE} ({path}) {where} {value!r} starts or ends with a space: "
                "take the space out of the path.",
                path=path,
                text=text,
            )
    pipeline = (document.get("project") or {}).get("pipeline")
    if pipeline:
        from haute._sandbox import contained_path

        try:
            contained_path(path.parent, _path_text(pipeline))
        except (InvalidPathError, PathOutsideProjectError) as exc:
            raise ProjectSettingsError(
                f"{SETTINGS_FILE} ({path}) [project] pipeline {pipeline!r} must name a file "
                "inside the project: write its path from the folder holding haute.toml.",
                path=path,
                text=text,
            ) from exc
