"""The workbench's form folder: the form as files in the pipeline folder (specs/workbench).

The form lives in the pipeline folder's ``workbench/``:

- ``form.json``, the index: the folder format's ``version``, the form's ``name``, the
  colours set in ``theme``, and the order of the tables (``schema``) and the sheets
  (``sheets``);
- ``schema/<table>.json`` per table, named by the table's name in lower case: its id, role,
  rows and columns. ``schema/quote_details.json`` holds only what the project sets on
  Haute's own table, and the quote's identity columns are written in no table;
- ``sheets/<sheet>.json`` per sheet, named by its title made a name: its sections and their
  components, what they show and how they look; ``sheets/<sheet>.layout.json`` beside it,
  where they sit;
- ``sample/<table>.json`` per input table the sample has rows in.

Files name things by name: a column is ``table.column``, or its bare name where its table is
said. A table and a column keep the id written beside their name for life, which the form
and the quote store key values by. Only what is set is written, beside the keys that say what
a thing is. A name the schema does not hold reads as an unresolved id, ``?`` and the name,
which binds to nothing and is written back as it was.

A read takes every file's bytes once under the form folder's lock, which a save holds while it
writes, in any process, and reads again when a writer that takes no lock changed the folder
meanwhile; the revision is the hash of the bytes it took. ``folder_files`` renders every file
of a form; the save route writes those that changed and deletes those the form no longer has.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    TypeAdapter,
    ValidationError,
)

from haute._artifact_paths import safe_path
from haute._cache import canonical_json
from haute._config_io import is_windows_reserved_filename
from haute._file_lock import FileLock
from haute._hashing import content_hash_bytes
from haute._workbench_config import WorkbenchConfig
from haute._workbench_folder_files import (
    DETAILS_ANY,
    DETAILS_TYPED,
    INPUT_ONLY,
    NAME_PATTERN,
    PLACEMENT,
    SAMPLE,
    IndexFile,
    LayoutFile,
    QuoteDetailsFile,
    RectFile,
    SheetFile,
    ShowWhenFile,
    TableFile,
)
from haute._workbench_form import (
    IDENTITY_IDS,
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_ID,
    UNRESOLVED_MARK,
    ExportWidget,
    FormDocument,
    FormSpec,
    ImportWidget,
    SchemaColumn,
    SchemaTable,
    SqlExport,
    SqlImport,
    WorkbenchFormError,
    _component_named,
    _condition_kind,
    _grown_to_hold,
    quote_details_table,
    with_quote_details,
)

#: The folder's files.
INDEX_FILE = "form.json"
SCHEMA_DIR = "schema"
SHEETS_DIR = "sheets"
SAMPLE_DIR = "sample"
LAYOUT_SUFFIX = ".layout.json"
#: The folder format: what each file holds and each setting's default.
FOLDER_VERSION = 1
#: The longest name a sheet's title makes.
SHEET_NAME_MAX = 64
#: The longest full path a file of the folder may have.
PATH_MAX = 240

#: How many times a read starts again while the folder changes under it.
_READ_TRIES = 3
#: A Windows reparse point: a junction or a symlink.
_REPARSE_POINT = 0x400


class FormFolderNameError(WorkbenchFormError):
    """A form the folder cannot hold: names that cannot name its files or tell its
    references apart, each in ``problems``."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        # The problems alone: the editor's toast already says what could not be saved.
        super().__init__("; ".join(problems))


# -- Names -------------------------------------------------------------------------------


def sheet_name(title: str) -> str:
    """The name a sheet's title makes, its file's name: lower case, each run of other
    characters than ASCII letters and digits one ``_``, none at either end, 64 at most."""
    name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return name[:SHEET_NAME_MAX].rstrip("_")


def _table_file(name: str) -> str:
    return f"{SCHEMA_DIR}/{name.lower()}.json"


def _sample_file(name: str) -> str:
    return f"{SAMPLE_DIR}/{name.lower()}.json"


def _sheet_files(name: str) -> tuple[str, str]:
    return f"{SHEETS_DIR}/{name}.json", f"{SHEETS_DIR}/{name}{LAYOUT_SUFFIX}"


def _unresolved(name: str) -> str:
    return UNRESOLVED_MARK + name


def is_unresolved(identifier: str) -> bool:
    """Whether *identifier* is a reference's unresolved id: a name the schema does not hold."""
    return identifier.startswith(UNRESOLVED_MARK)


class _Names:
    """The schema's tables and columns, by id and by name, for references both ways."""

    def __init__(self, tables: list[tuple[str, str, list[tuple[str, str]]]]) -> None:
        self.table_names = {table_id: name for table_id, name, _ in tables}
        self.table_ids = {name: table_id for table_id, name, _ in tables}
        self.column_names = {table_id: dict(columns) for table_id, _, columns in tables}
        self.column_ids = {
            table_id: {name: column_id for column_id, name in columns}
            for table_id, _, columns in tables
        }
        self.missing: list[str] = []

    @classmethod
    def of(cls, spec: FormSpec) -> _Names:
        return cls(
            [
                (table.id, table.name, [(column.id, column.name) for column in table.columns])
                for table in spec.data_schema.tables
            ]
        )

    # Ids to names, for writing: an id the schema does not hold is a problem.

    def table(self, table_id: str) -> str:
        if is_unresolved(table_id):
            return table_id[1:]
        name = self.table_names.get(table_id)
        if name is None:
            self.missing.append(
                f"a reference names the table id {table_id!r}, which the schema does not hold"
            )
            return table_id
        return name

    def column(self, table_id: str, column_id: str) -> str:
        if is_unresolved(column_id):
            return column_id[1:]
        name = self.column_names.get(table_id, {}).get(column_id)
        if name is None:
            self.missing.append(
                f"a reference names the column id {column_id!r} of {self.table(table_id)}, "
                "which the schema does not hold"
            )
            return column_id
        return name

    def dotted(self, table_id: str, column_id: str) -> str:
        return f"{self.table(table_id)}.{self.column(table_id, column_id)}"

    # Names to ids, for reading: a name the schema does not hold is unresolved.

    def table_id(self, name: str) -> str:
        return self.table_ids.get(name, _unresolved(name))

    def column_id(self, table_id: str, name: str) -> str:
        if is_unresolved(table_id):
            return _unresolved(name)
        return self.column_ids.get(table_id, {}).get(name, _unresolved(name))

    def ref(self, dotted: str) -> dict[str, str]:
        table_name, column_name = dotted.split(".")
        table_id = self.table_id(table_name)
        return {"table": table_id, "column": self.column_id(table_id, column_name)}


def folder_name_problems(spec: FormSpec, folder: Path) -> list[str]:
    """What stops *spec*'s files being named and its references told apart in *folder*."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for table in spec.data_schema.tables:
        if not re.fullmatch(NAME_PATTERN, table.name):
            problems.append(f"the table name {table.name!r} is not an identifier")
        elif is_windows_reserved_filename(table.name):
            problems.append(f"the table name {table.name!r} is one Windows keeps for itself")
        folded = table.name.casefold()
        if folded in seen:
            problems.append(f"the tables {seen[folded]!r} and {table.name!r} share a name")
        seen[folded] = table.name
        names: set[str] = set()
        for column in table.columns:
            if not re.fullmatch(NAME_PATTERN, column.name):
                problems.append(f"{table.name}'s column name {column.name!r} is not an identifier")
            if column.name in names:
                problems.append(f"{table.name} has two columns named {column.name!r}")
            names.add(column.name)
    sheets: dict[str, str] = {}
    for page in spec.pages:
        name = sheet_name(page.title)
        if not name:
            problems.append(
                f"the sheet {page.title!r} needs a title with a letter or a digit for its "
                "file's name"
            )
        elif is_windows_reserved_filename(name):
            problems.append(f"the sheet {page.title!r} makes {name!r}, a name Windows keeps")
        elif name in sheets:
            problems.append(
                f"the sheets {sheets[name]!r} and {page.title!r} would both be saved as "
                f"{SHEETS_DIR}/{name}.json: give one another title"
            )
        else:
            sheets[name] = page.title
    longest = max(
        [_table_file(table.name) for table in spec.data_schema.tables]
        + [_sheet_files(name)[1] for name in sheets],
        key=len,
    )
    if len(str(folder / longest)) > PATH_MAX:
        problems.append(
            f"{longest} would pass {PATH_MAX} characters in {folder}: give it a shorter name "
            "or move the project to a shorter path"
        )
    return problems


# -- Writing -----------------------------------------------------------------------------


def _dumps(value: Any, indent: int = 0) -> str:
    """*value* as the folder writes JSON: two-space indent, a list of numbers or a mapping
    of numbers (a layout entry) on one line, other lists and mappings one item a line."""
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        if all(_is_number(item) for item in value.values()):
            inner = ", ".join(
                f"{json.dumps(key)}: {json.dumps(item)}" for key, item in value.items()
            )
            return "{" + inner + "}"
        items = ",\n".join(
            f"{pad}{json.dumps(key, ensure_ascii=False)}: {_dumps(item, indent + 1)}"
            for key, item in value.items()
        )
        return "{\n" + items + "\n" + "  " * indent + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(_is_number(item) for item in value):
            return "[" + ", ".join(json.dumps(item) for item in value) + "]"
        items = ",\n".join(f"{pad}{_dumps(item, indent + 1)}" for item in value)
        return "[\n" + items + "\n" + "  " * indent + "]"
    return json.dumps(value, ensure_ascii=False)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def render_folder_file(value: BaseModel | list[Any]) -> bytes:
    """A file's bytes: a model with each setting at its default left out, or a list."""
    data = (
        value.model_dump(mode="json", exclude_defaults=True, by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    return (_dumps(data) + "\n").encode("utf-8")


def _show_when_file(show_when: Any, names: _Names) -> dict[str, Any]:
    return {
        "match": show_when.match,
        "conditions": [_entry_file(entry, names) for entry in show_when.conditions],
    }


def _entry_file(entry: Any, names: _Names) -> dict[str, Any]:
    kind = _condition_kind(entry)
    if kind == "group":
        return {
            "match": entry.match,
            "conditions": [_entry_file(item, names) for item in entry.conditions],
        }
    if kind == "one":
        return {
            "column": names.dotted(entry.table, entry.column),
            "op": entry.op,
            "values": entry.values,
        }
    where = [
        {"column": names.column(entry.table, test.column), "op": test.op, "values": test.values}
        for test in entry.where
    ]
    table = names.table(entry.table)
    if kind == "rows":
        return {"table": table, "rows": entry.rows, "where": where}
    if kind == "count":
        return {"table": table, "where": where, "op": entry.op, "count": entry.count}
    return {
        "table": table,
        "total": entry.total,
        "column": names.column(entry.table, entry.column),
        "where": where,
        "op": entry.op,
        "value": entry.value,
    }


def _component_file(widget: Any, names: _Names) -> dict[str, Any]:
    data: dict[str, Any] = widget.model_dump(mode="json", exclude=PLACEMENT)
    data["show_when"] = _show_when_file(widget.show_when, names)
    if widget.type == "tableInput":
        data["type"] = "table"
    if widget.type in ("tableInput", "collection", "chart"):
        data["fields"] = [names.dotted(field.table, field.column) for field in widget.fields]
    if widget.type in ("tableInput", "collection"):
        data["column_looks"] = [
            {
                **look.model_dump(mode="json", exclude={"table", "column", "show_when"}),
                "column": names.dotted(look.table, look.column),
                "show_when": _show_when_file(look.show_when, names),
            }
            for look in widget.column_looks
        ]
    if widget.type == "chart" and widget.category is not None:
        data["category"] = names.dotted(widget.category.table, widget.category.column)
    if widget.type == "import":
        # An Import that fills no table names no columns (``ImportWidget`` holds it so).
        if widget.table is not None:
            data["table"] = names.table(widget.table)
            data["column_names"] = [
                {"column": names.column(widget.table, named.column), "name": named.name}
                for named in widget.column_names
            ]
        if widget.source.format == "sql":
            data["source"]["parameters"] = [
                {"name": given.name, "column": names.dotted(given.table, given.column)}
                for given in widget.source.parameters
            ]
    if widget.type == "export":
        data["tables"] = [names.table(table_id) for table_id in widget.tables]
        if widget.target.format == "sql":
            data["target"]["table_names"] = [
                {"table": names.table(named.table), "name": named.name}
                for named in widget.target.table_names
            ]
    return data


def _sheet_and_layout(page: Any, names: _Names) -> tuple[SheetFile, LayoutFile]:
    sections = [
        {
            "id": section.id,
            "title": section.title,
            "show_when": _show_when_file(section.show_when, names),
            "components": [_component_file(widget, names) for widget in section.widgets],
        }
        for section in page.sections
    ]
    sheet = SheetFile.model_validate(
        {
            "id": page.id,
            "title": page.title,
            "show_when": _show_when_file(page.show_when, names),
            "sections": sections,
        }
    )
    layout = LayoutFile.model_validate(
        {
            "width": page.width,
            "sections": {
                section.id: {
                    "height": section.height,
                    "components": {
                        widget.id: {"x": widget.x, "y": widget.y, "w": widget.w, "h": widget.h}
                        for widget in section.widgets
                    },
                }
                for section in page.sections
            },
        }
    )
    return sheet, layout


def _table_file_model(table: SchemaTable, problems: list[str]) -> TableFile:
    """*table*'s file but its name. An output's columns are written without the rules only
    an input column takes, which they hold only at their defaults; one set is a problem."""
    columns: list[dict[str, Any]] = []
    for column in table.columns:
        if column.id in IDENTITY_IDS:
            continue
        data = column.model_dump(mode="json")
        if table.role == "output":
            for key in INPUT_ONLY:
                if data.pop(key) != SchemaColumn.model_fields[key].get_default(
                    call_default_factory=True
                ):
                    problems.append(
                        f"output column {table.name}.{column.name} holds {key}, which only an "
                        "input column takes"
                    )
        columns.append(data)
    return TableFile.model_validate(
        {"id": table.id, "role": table.role, "rows": table.rows, "columns": columns}
    )


def _quote_details_file(table: SchemaTable) -> QuoteDetailsFile:
    columns: list[dict[str, Any]] = []
    for column in table.columns:
        given = {
            key: value
            for key, value in column.model_dump(mode="json").items()
            if key in (*DETAILS_ANY, *DETAILS_TYPED)
            and value != SchemaColumn.model_fields[key].get_default(call_default_factory=True)
        }
        if given:
            columns.append({"name": column.name, **given})
    return QuoteDetailsFile.model_validate({"columns": columns})


def _sample_rows(
    table: SchemaTable, rows: list[dict[str, Any]], names: _Names
) -> list[dict[str, Any]]:
    """*rows* by column name in the table's column order, unresolved names after them."""
    order = [column.id for column in table.columns]
    written: list[dict[str, Any]] = []
    for row in rows:
        record = {
            names.column(table.id, column_id): row[column_id]
            for column_id in order
            if column_id in row
        }
        for column_id, value in row.items():
            if column_id not in order:
                record[names.column(table.id, column_id)] = value
        written.append(record)
    return written


def folder_files(spec: FormSpec, folder: Path) -> dict[str, bytes]:
    """Every file of *spec*'s folder at *folder*, by its path from the folder.

    Raises :class:`FormFolderNameError` before anything is rendered when the folder cannot
    hold the form's names, or when a reference or a sample value names an id the schema does
    not hold that is not an unresolved one.
    """
    spec = with_quote_details(spec)
    problems = folder_name_problems(spec, folder)
    if problems:
        raise FormFolderNameError(problems)
    names = _Names.of(spec)
    tables = spec.data_schema.tables
    files: dict[str, bytes] = {
        INDEX_FILE: render_folder_file(
            IndexFile.model_validate(
                {
                    "version": FOLDER_VERSION,
                    "name": spec.name,
                    "theme": spec.theme.model_dump(mode="json"),
                    "schema": [table.name for table in tables],
                    "sheets": [sheet_name(page.title) for page in spec.pages],
                }
            )
        )
    }
    for table in tables:
        model: BaseModel = (
            _quote_details_file(table)
            if table.id == QUOTE_DETAILS_ID
            else _table_file_model(table, names.missing)
        )
        files[_table_file(table.name)] = render_folder_file(model)
    for page in spec.pages:
        sheet, layout = _sheet_and_layout(page, names)
        sheet_path, layout_path = _sheet_files(sheet_name(page.title))
        files[sheet_path] = render_folder_file(sheet)
        files[layout_path] = render_folder_file(layout)
    inputs = {table.id: table for table in tables if table.role == "input"}
    for table_id, rows in spec.sample.items():
        if not rows:
            continue
        sampled = inputs.get(table_id)
        if sampled is None:
            names.missing.append(
                f"the sample holds rows for {names.table(table_id)!r}, which is no input table"
            )
            continue
        files[_sample_file(sampled.name)] = render_folder_file(_sample_rows(sampled, rows, names))
    if names.missing:
        raise FormFolderNameError(list(dict.fromkeys(names.missing)))
    return files


# -- Reading -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FolderSnapshot:
    """The folder as one read took it: each file's bytes by its path from the folder, and
    the revision those bytes make."""

    files: Mapping[str, bytes]
    revision: str


def folder_revision(files: Mapping[str, bytes]) -> str:
    """The revision of a folder holding *files*: every file's path and its bytes' hash."""
    manifest = [[path, content_hash_bytes(files[path])] for path in sorted(files)]
    return content_hash_bytes(canonical_json(manifest).encode("utf-8"))


#: One lock per project in this process, so a thread that holds it re-enters it rather than
#: waiting on a second handle to the same file.
_FOLDER_LOCKS: dict[str, FileLock] = {}
_FOLDER_LOCKS_GUARD = threading.Lock()


def form_folder_lock(project_root: Path) -> FileLock:
    """The form folder's lock: a lock of the project's own, held by every save while it
    writes and by every read, in every process."""
    identity = hashlib.sha256(os.path.normcase(str(project_root.resolve())).encode()).hexdigest()
    with _FOLDER_LOCKS_GUARD:
        lock = _FOLDER_LOCKS.get(identity)
        if lock is None:
            lock = FileLock(
                safe_path(Path(tempfile.gettempdir()), f"haute-form-locks/{identity}.lock")
            )
            _FOLDER_LOCKS[identity] = lock
        return lock


def _missing(config: WorkbenchConfig) -> WorkbenchFormError:
    return WorkbenchFormError(
        f"{config.form} does not exist: restore it from version history (a project starts its "
        "workbench with haute init --workbench)",
        path=str(config.form_folder),
    )


def _listing(config: WorkbenchConfig) -> dict[str, tuple[int, int]]:
    """Every file in the folder, by its path from the folder as the disk spells it, with its
    size and modification time; a link or a junction anywhere in it refused."""
    root = config.form_folder
    if not root.is_dir():
        raise _missing(config)
    found: dict[str, tuple[int, int]] = {}

    def walk(folder: Path, prefix: str) -> None:
        with os.scandir(folder) as entries:
            for entry in entries:
                relative = f"{prefix}{entry.name}"
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
                    raise WorkbenchFormError(
                        f"{config.form}/{relative} is a link: the form folder holds its own files",
                        path=str(folder / entry.name),
                    )
                if stat.S_ISDIR(info.st_mode):
                    walk(folder / entry.name, f"{relative}/")
                else:
                    found[relative] = (info.st_size, info.st_mtime_ns)

    walk(root, "")
    return found


def read_folder_unlocked(config: WorkbenchConfig) -> FolderSnapshot:
    """The folder's files, each read once, read again while a writer that takes no lock
    changes them; the caller holds :func:`form_folder_lock`."""
    for _ in range(_READ_TRIES):
        before = _listing(config)
        files: dict[str, bytes] = {}
        for relative in before:
            try:
                files[relative] = (config.form_folder / relative).read_bytes()
            except FileNotFoundError:
                break
            except OSError as exc:
                raise WorkbenchFormError(
                    f"{config.form}/{relative} could not be read: {exc}",
                    path=str(config.form_folder / relative),
                ) from exc
        else:
            if _listing(config) == before:
                return FolderSnapshot(files, folder_revision(files))
    raise WorkbenchFormError(
        f"{config.form} changed while it was being read: try again", path=str(config.form_folder)
    )


def read_folder(config: WorkbenchConfig) -> FolderSnapshot:
    """The folder's files as one read takes them, under the form folder's lock."""
    with form_folder_lock(config.project_root):
        return read_folder_unlocked(config)


def _no_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"the key {key!r} is given twice")
        seen[key] = value
    return seen


def _parse(config: WorkbenchConfig, files: Mapping[str, bytes], path: str, model: Any) -> Any:
    """*path*'s file read as *model*, or a :class:`WorkbenchFormError` naming the file and
    the place."""
    where = f"{config.form}/{path}"
    try:
        text = files[path].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkbenchFormError(f"{where} is not UTF-8 text: {exc}", path=where) from exc
    try:
        parsed = json.loads(text, object_pairs_hook=_no_repeated_keys)
    except ValueError as exc:
        raise WorkbenchFormError(f"{where} is not JSON: {exc}", path=where) from exc
    try:
        if isinstance(model, TypeAdapter):
            return model.validate_python(parsed)
        return model.model_validate(parsed)
    except ValidationError as exc:
        first = exc.errors()[0]
        place = ".".join(str(part) for part in first["loc"]) or "the file"
        raise WorkbenchFormError(f"{where}: {place}: {first['msg']}", path=where) from exc


def _entry(entry: Any, names: _Names) -> dict[str, Any]:
    kind = _condition_kind(entry)
    if kind == "group":
        return {
            "match": entry.match,
            "conditions": [_entry(item, names) for item in entry.conditions],
        }
    if kind == "one":
        return {**names.ref(entry.column), "op": entry.op, "values": entry.values}
    table_id = names.table_id(entry.table)
    where = [
        {"column": names.column_id(table_id, test.column), "op": test.op, "values": test.values}
        for test in entry.where
    ]
    if kind == "rows":
        return {"table": table_id, "rows": entry.rows, "where": where}
    if kind == "count":
        return {"table": table_id, "where": where, "op": entry.op, "count": entry.count}
    return {
        "table": table_id,
        "total": entry.total,
        "column": names.column_id(table_id, entry.column),
        "where": where,
        "op": entry.op,
        "value": entry.value,
    }


def _show_when(show_when: ShowWhenFile, names: _Names) -> dict[str, Any]:
    return {
        "match": show_when.match,
        "conditions": [_entry(item, names) for item in show_when.conditions],
    }


def _component(component: Any, rect: RectFile, names: _Names) -> dict[str, Any]:
    data: dict[str, Any] = component.model_dump(mode="json")
    data.update(x=rect.x, y=rect.y, w=rect.w, h=rect.h)
    data["show_when"] = _show_when(component.show_when, names)
    kind = component.type
    if kind == "table":
        data["type"] = "tableInput"
    if kind in ("table", "collection", "chart"):
        data["fields"] = [names.ref(field) for field in component.fields]
    if kind in ("table", "collection"):
        data["column_looks"] = [
            {
                **look.model_dump(mode="json", exclude={"column", "show_when"}),
                **names.ref(look.column),
                "show_when": _show_when(look.show_when, names),
            }
            for look in component.column_looks
        ]
    if kind == "chart" and component.category is not None:
        data["category"] = names.ref(component.category)
    if kind == "import":
        # An Import that fills no table names no columns, which ``ImportWidget`` holds it to.
        if component.table is not None:
            table_id = names.table_id(component.table)
            data["table"] = table_id
            data["column_names"] = [
                {"column": names.column_id(table_id, named.column), "name": named.name}
                for named in component.column_names
            ]
        if component.source.format == "sql":
            data["source"]["parameters"] = [
                {"name": given.name, **names.ref(given.column)}
                for given in component.source.parameters
            ]
    if kind == "export":
        data["tables"] = [names.table_id(name) for name in component.tables]
        if component.target.format == "sql":
            data["target"]["table_names"] = [
                {"table": names.table_id(named.table), "name": named.name}
                for named in component.target.table_names
            ]
    return data


def _page(
    config: WorkbenchConfig, name: str, sheet: SheetFile, layout: LayoutFile, names: _Names
) -> dict[str, Any]:
    """A sheet's file and its layout file as one sheet of the form, each section and
    component placed."""
    where = f"{config.form}/{_sheet_files(name)[1]}"
    sections: list[dict[str, Any]] = []
    for section in sheet.sections:
        placed = layout.sections.get(section.id)
        if placed is None:
            raise WorkbenchFormError(
                f"{where} leaves out the section {section.title!r} ({section.id})", path=where
            )
        extras = sorted(set(placed.components) - {component.id for component in section.components})
        if extras:
            raise WorkbenchFormError(
                f"{where} places {extras[0]}, which the section {section.title!r} does not hold",
                path=where,
            )
        components: list[dict[str, Any]] = []
        for component in section.components:
            rect = placed.components.get(component.id)
            if rect is None:
                kind = "tableInput" if component.type == "table" else component.type
                named = _component_named({**component.model_dump(), "type": kind})
                raise WorkbenchFormError(f"{where} leaves out {named} ({component.id})", path=where)
            components.append(_component(component, rect, names))
        sections.append(
            {
                "id": section.id,
                "title": section.title,
                "height": placed.height,
                "show_when": _show_when(section.show_when, names),
                "widgets": components,
            }
        )
    extra_sections = sorted(set(layout.sections) - {section.id for section in sheet.sections})
    if extra_sections:
        raise WorkbenchFormError(
            f"{where} places a section {extra_sections[0]} the sheet does not hold", path=where
        )
    return {
        "id": sheet.id,
        "title": sheet.title,
        "width": layout.width,
        "show_when": _show_when(sheet.show_when, names),
        "sections": sections,
    }


def _check_inventory(config: WorkbenchConfig, files: Mapping[str, bytes], index: IndexFile) -> None:
    """Refuse the folder unless it holds exactly the files its index names, each named in
    lower case, and samples only of tables it names."""
    for things, listed in (
        ("table", [name.casefold() for name in index.tables]),
        ("sheet", index.sheets),
    ):
        seen: set[str] = set()
        for name in listed:
            if name in seen:
                raise WorkbenchFormError(
                    f"{config.form}/{INDEX_FILE} names the {things} {name} twice"
                )
            seen.add(name)
    if not index.tables or index.tables[0] != QUOTE_DETAILS_ID:
        raise WorkbenchFormError(
            f"{config.form}/{INDEX_FILE}'s schema must start with {QUOTE_DETAILS_ID}, "
            "Haute's own table"
        )
    expected = {INDEX_FILE, *(_table_file(name) for name in index.tables)}
    for name in index.sheets:
        expected.update(_sheet_files(name))
    samples = {_sample_file(name) for name in index.tables}
    for path in sorted(files):
        if path in expected or path in samples:
            continue
        lowered = path.lower()
        if lowered != path and (lowered in expected or lowered in samples):
            raise WorkbenchFormError(
                f"{config.form}/{path} must be named in lower case: rename it "
                f"{lowered.rsplit('/', 1)[-1]}"
            )
        if path.startswith(f"{SAMPLE_DIR}/"):
            raise WorkbenchFormError(
                f"{config.form}/{path} is the sample of no input table: rename it after its "
                "table or remove it"
            )
        raise WorkbenchFormError(
            f"{config.form}/{path} is not part of the form: list it in {INDEX_FILE} or remove it"
        )
    missing = sorted(expected - set(files))
    if missing:
        raise WorkbenchFormError(
            f"{config.form}/{INDEX_FILE} names {missing[0]}, but the folder does not hold it"
        )


def form_from_files(
    config: WorkbenchConfig, files: Mapping[str, bytes]
) -> tuple[FormSpec, tuple[str, ...]]:
    """The form *files* hold, with what reading it grew; a :class:`WorkbenchFormError`
    naming the file and the place for anything the folder does not hold as it should."""
    if INDEX_FILE not in files:
        raise WorkbenchFormError(f"{config.form} has no {INDEX_FILE}: the folder is not a form")
    index: IndexFile = _parse(config, files, INDEX_FILE, IndexFile)
    _check_inventory(config, files, index)

    details: QuoteDetailsFile = _parse(
        config, files, _table_file(QUOTE_DETAILS_ID), QuoteDetailsFile
    )
    quote_details = quote_details_table().model_dump(mode="json")
    given = {
        column.name: column.model_dump(mode="json", exclude_unset=True)
        for column in details.columns
    }
    for column in quote_details["columns"]:
        column.update(given.get(column["name"], {}))
    tables: list[dict[str, Any]] = [quote_details]
    parsed_tables: list[tuple[str, TableFile]] = []
    for name in index.tables[1:]:
        table: TableFile = _parse(config, files, _table_file(name), TableFile)
        parsed_tables.append((name, table))
        tables.append(
            {
                "id": table.id,
                "name": name,
                "role": table.role,
                "rows": table.rows,
                "columns": [column.model_dump(mode="json") for column in table.columns],
            }
        )

    identity = [(column_id, column_id) for column_id in sorted(IDENTITY_IDS)]
    names = _Names(
        [
            (
                QUOTE_DETAILS_ID,
                QUOTE_DETAILS_ID,
                [(column_id, name) for column_id, name, _ in QUOTE_DETAILS_COLUMNS],
            )
        ]
        + [
            (table.id, name, [*identity, *((column.id, column.name) for column in table.columns)])
            for name, table in parsed_tables
        ]
    )

    pages: list[dict[str, Any]] = []
    for name in index.sheets:
        sheet_path, layout_path = _sheet_files(name)
        sheet: SheetFile = _parse(config, files, sheet_path, SheetFile)
        layout: LayoutFile = _parse(config, files, layout_path, LayoutFile)
        pages.append(_page(config, name, sheet, layout, names))

    roles = {name: table.role for name, table in parsed_tables}
    sample: dict[str, list[dict[str, Any]]] = {}
    for name in index.tables:
        path = _sample_file(name)
        if path not in files:
            continue
        if name == QUOTE_DETAILS_ID or roles.get(name) == "input":
            table_id = names.table_id(name)
            rows = _parse(config, files, path, SAMPLE)
            sample[table_id] = [
                {names.column_id(table_id, column): value for column, value in row.items()}
                for row in rows
            ]
        else:
            raise WorkbenchFormError(
                f"{config.form}/{path} is the sample of no input table: rename it after its "
                "table or remove it"
            )

    document: dict[str, Any] = {
        "version": 1,
        "name": index.name,
        "theme": index.theme.model_dump(mode="json"),
        "schema": {"tables": tables},
        "pages": pages,
        "sample": sample,
    }
    repairs = _grown_to_hold(document)
    try:
        spec = FormSpec.model_validate(document)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise WorkbenchFormError(
            f"{config.form} is not a workbench form: {_place(first['loc'], index)}: {first['msg']}",
            path=str(config.form_folder),
        ) from exc
    return spec, tuple(repairs)


def _place(loc: tuple[Any, ...], index: IndexFile) -> str:
    """A ``FormSpec`` error's place as the folder spells it: the file, then the place in it."""
    parts = list(loc)
    if (
        parts[:1] == ["pages"]
        and len(parts) > 1
        and isinstance(parts[1], int)
        and parts[1] < len(index.sheets)
    ):
        rest = ["components" if part == "widgets" else str(part) for part in parts[2:]]
        return f"{_sheet_files(index.sheets[parts[1]])[0]}: " + ".".join(rest)
    if parts[:2] == ["schema", "tables"] and len(parts) > 2 and isinstance(parts[2], int):
        position = parts[2]
        if position < len(index.tables):
            return f"{_table_file(index.tables[position])}: " + ".".join(
                str(part) for part in parts[3:]
            )
    return ".".join(str(part) for part in parts) or "the form"


def read_form_document(config: WorkbenchConfig) -> FormDocument:
    """The form the project's folder holds now, with the folder's revision and what reading
    it grew; a :class:`WorkbenchFormError` for a folder that cannot be read as a form."""
    snapshot = read_folder(config)
    spec, repairs = form_from_files(config, snapshot.files)
    return FormDocument(spec, snapshot.revision, repairs)


# -- What deploy and the release refuse --------------------------------------------------


#: A reference: a table's id, with a column's id of it or None for the table alone.
_Ref = tuple[str, str | None]


def _entry_refs(entry: Any) -> Iterator[_Ref]:
    kind = _condition_kind(entry)
    if kind == "group":
        for item in entry.conditions:
            yield from _entry_refs(item)
        return
    if kind == "one":
        yield entry.table, entry.column
        return
    yield entry.table, None
    for test in entry.where:
        yield entry.table, test.column
    if kind == "total":
        yield entry.table, entry.column


def _show_when_refs(show_when: Any) -> list[_Ref]:
    return [ref for entry in show_when.conditions for ref in _entry_refs(entry)]


def unresolved_references(spec: FormSpec) -> list[str]:
    """Each place *spec*'s sheets name a table or column its schema does not hold, by sheet
    file and component, as ``haute app build`` and deploy refuse them: a table by its name,
    a column as ``table.column``, as the files spell them."""
    tables = {table.id: table.name for table in spec.data_schema.tables}
    found: list[str] = []

    def spelled(table_id: str, column_id: str | None) -> str | None:
        """The reference as the files spell it, when it names what the schema does not hold."""
        table = table_id[1:] if is_unresolved(table_id) else tables.get(table_id, table_id)
        if column_id is None:
            return table if is_unresolved(table_id) else None
        if not (is_unresolved(table_id) or is_unresolved(column_id)):
            return None
        column = column_id[1:] if is_unresolved(column_id) else column_id
        return f"{table}.{column}"

    def report(where: str, refs: list[_Ref]) -> None:
        missing = sorted({name for ref in refs if (name := spelled(*ref)) is not None})
        found.extend(f"{where} names {name}, which the schema does not hold" for name in missing)

    for page in spec.pages:
        sheet = f"{SHEETS_DIR}/{sheet_name(page.title)}.json"
        report(f"{sheet} (its Show when)", _show_when_refs(page.show_when))
        for section in page.sections:
            report(f"{sheet}, section {section.title!r}", _show_when_refs(section.show_when))
            for widget in section.widgets:
                refs = _show_when_refs(widget.show_when)
                refs += [(field.table, field.column) for field in getattr(widget, "fields", [])]
                for look in getattr(widget, "column_looks", []):
                    refs += [(look.table, look.column), *_show_when_refs(look.show_when)]
                category = getattr(widget, "category", None)
                if category is not None:
                    refs.append((category.table, category.column))
                if isinstance(widget, ImportWidget) and widget.table is not None:
                    refs.append((widget.table, None))
                    refs += [(widget.table, named.column) for named in widget.column_names]
                if isinstance(widget, ImportWidget) and isinstance(widget.source, SqlImport):
                    refs += [(given.table, given.column) for given in widget.source.parameters]
                if isinstance(widget, ExportWidget):
                    refs += [(table_id, None) for table_id in widget.tables]
                    if isinstance(widget.target, SqlExport):
                        refs += [(named.table, None) for named in widget.target.table_names]
                report(f"{sheet}, {_component_named(widget.model_dump())}", refs)
    return found
