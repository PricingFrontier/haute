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
from typing import TYPE_CHECKING, Any

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
from haute._json_files import ANY, JsonPath, at, render_json_bytes
from haute._workbench_config import WorkbenchConfig

if TYPE_CHECKING:
    from haute._workbench_params import ParamValues
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
    IDENTITY_COLUMNS,
    QUOTE_DETAILS_NAME,
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
    holds_identity,
    quote_details_table,
    sheet_name,
    with_quote_details,
)
from haute._workbench_tables import store_name_problems

#: The folder's files.
INDEX_FILE = "form.json"
SCHEMA_DIR = "schema"
SHEETS_DIR = "sheets"
SAMPLE_DIR = "sample"
LAYOUT_SUFFIX = ".layout.json"
#: The folder format: what each file holds and each setting's default.
FOLDER_VERSION = 1
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


def _table_file(name: str) -> str:
    return f"{SCHEMA_DIR}/{name.lower()}.json"


def _sample_file(name: str) -> str:
    return f"{SAMPLE_DIR}/{name.lower()}.json"


def _sheet_files(name: str) -> tuple[str, str]:
    return f"{SHEETS_DIR}/{name}.json", f"{SHEETS_DIR}/{name}{LAYOUT_SUFFIX}"


def _ref(dotted: str) -> dict[str, str]:
    """A column written as ``table.column`` as the form holds it: by its table's name and its
    own (READ-23). A name the schema does not hold is kept as written."""
    table, column = dotted.split(".")
    return {"table": table, "column": column}


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
        # Each table is a table of its name in the quote store too, which also refuses two
        # columns whose names differ only in case: a column is known by its name (READ-23).
        problems.extend(
            store_name_problems(table.name, table.rows, [column.name for column in table.columns])
        )
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
        # A section is known by its title on its sheet (READ-24), which keys its layout.
        titles = [section.title for section in page.sections]
        twice = next((title for title in titles if titles.count(title) > 1), None)
        if twice is not None:
            problems.append(
                f"the sheet {page.title!r} holds two sections titled {twice!r}: a section is "
                "known by its title, so give each its own"
            )
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


def _a_condition(path: JsonPath) -> bool:
    """A show-when condition, wherever its rule sits: on a sheet, a component or a field."""
    return len(path) >= 2 and path[-2] == "conditions" and isinstance(path[-1], int)


#: A schema file's columns, one a line.
_SCHEMA_ONE_LINE = at(("columns", ANY))


def _never(_path: JsonPath) -> bool:
    return False


def render_folder_file(value: BaseModel | list[Any]) -> bytes:
    """A file's bytes: a model with each setting at its default left out, or a list, written
    by the one printer (:mod:`haute._json_files`): a schema file's columns and a sheet's
    show-when conditions, and a layout box or any other run of numbers alone, on one line
    each, whoever writes the file."""
    data = (
        value.model_dump(mode="json", exclude_defaults=True, by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    one_line = (
        _a_condition
        if isinstance(value, SheetFile)
        else _SCHEMA_ONE_LINE
        if isinstance(value, TableFile | QuoteDetailsFile)
        else _never
    )
    return render_json_bytes(data, one_line=one_line)


def _counted_file(match: Any) -> dict[str, Any]:
    """How many of a rule's conditions must hold, as a file writes it (READ-31): ``match``
    for all or any, ``at_least`` for a number of them."""
    if isinstance(match, int) and not isinstance(match, bool):
        return {"at_least": match}
    return {"match": match}


def _show_when_file(show_when: Any) -> dict[str, Any]:
    return {
        **_counted_file(show_when.match),
        "conditions": [_entry_file(entry) for entry in show_when.conditions],
    }


def _entry_file(entry: Any) -> dict[str, Any]:
    kind = _condition_kind(entry)
    if kind == "group":
        return {
            **_counted_file(entry.match),
            "conditions": [_entry_file(item) for item in entry.conditions],
        }
    if kind == "one":
        return {
            "column": f"{entry.table}.{entry.column}",
            "op": entry.op,
            "values": entry.values,
            "field": None if entry.field is None else f"{entry.field.table}.{entry.field.column}",
            "days": entry.days,
        }
    where = [
        {
            "column": test.column,
            "op": test.op,
            "values": test.values,
            "field": test.field,
            "days": test.days,
        }
        for test in entry.where
    ]
    table = entry.table
    if kind == "rows":
        return {"table": table, "rows": entry.rows, "where": where}
    if kind == "count":
        return {"table": table, "where": where, "op": entry.op, "count": entry.count}
    return {
        "table": table,
        "total": entry.total,
        "column": entry.column,
        "where": where,
        "op": entry.op,
        "value": entry.value,
    }


def _component_file(widget: Any) -> dict[str, Any]:
    data: dict[str, Any] = widget.model_dump(mode="json", exclude=PLACEMENT | {"id"})
    data["show_when"] = _show_when_file(widget.show_when)
    if widget.type == "tableInput":
        data["type"] = "table"
    if widget.type == "chart":
        data["fields"] = [f"{field.table}.{field.column}" for field in widget.fields]
    if widget.type in ("tableInput", "collection"):
        data["fields"] = _fields_file(widget)
        data.pop("column_looks", None)
        data.pop("spacers", None)
    if widget.type == "chart" and widget.category is not None:
        data["category"] = f"{widget.category.table}.{widget.category.column}"
    if widget.type == "import":
        # An Import that fills no table names no columns (``ImportWidget`` holds it so).
        if widget.table is not None:
            data["table"] = widget.table
            data["column_names"] = [
                {"column": named.column, "name": named.name} for named in widget.column_names
            ]
        if widget.source.format == "sql":
            data["source"]["parameters"] = [
                {"name": given.name, "column": f"{given.table}.{given.column}"}
                for given in widget.source.parameters
            ]
    if widget.type == "export":
        data["tables"] = list(widget.tables)
        if widget.target.format == "sql":
            data["target"]["table_names"] = [
                {"table": named.table, "name": named.name} for named in widget.target.table_names
            ]
    return data


def _fields_file(widget: Any) -> list[Any]:
    """A Table's or a Collection's fields as its file writes them (READ-31): each field as
    ``table.column``, a field with a look of its own as an object holding the field and its
    settings, in its place, and each of a Collection's gaps as ``null`` where it sits."""
    looks = {(look.table, look.column): look for look in widget.column_looks}
    entries: list[Any] = []
    for field in widget.fields:
        dotted = f"{field.table}.{field.column}"
        look = looks.get((field.table, field.column))
        entries.append(
            dotted
            if look is None
            else {
                "field": dotted,
                **look.model_dump(mode="json", exclude={"table", "column", "show_when"}),
                "show_when": _show_when_file(look.show_when),
            }
        )
    gaps = list(getattr(widget, "spacers", []))
    placed: list[Any] = []
    for index, entry in enumerate(entries):
        while gaps and gaps[0] == index:
            placed.append(None)
            gaps.pop(0)
        placed.append(entry)
    placed.extend(None for _ in gaps)
    return placed


def _numbers(section: Any) -> list[int]:
    """Each of *section*'s components' numbers (READ-24): its own, or, for one sent without,
    one more than the highest in the section, in the section's order."""
    highest = max((widget.number for widget in section.widgets if widget.number), default=0)
    numbers: list[int] = []
    for widget in section.widgets:
        if widget.number is None:
            highest += 1
            numbers.append(highest)
        else:
            numbers.append(widget.number)
    return numbers


def _sheet_and_layout(page: Any) -> tuple[SheetFile, LayoutFile]:
    """A sheet's file and its layout file, neither holding an id (READ-24): a section is known
    by its title and a component by its number in its section, which key their boxes."""
    numbered = [(section, _numbers(section)) for section in page.sections]
    sections = [
        {
            "title": section.title,
            "show_when": _show_when_file(section.show_when),
            "components": [
                {**_component_file(widget), "number": number}
                for widget, number in zip(section.widgets, numbers, strict=True)
            ],
        }
        for section, numbers in numbered
    ]
    sheet = SheetFile.model_validate(
        {
            "title": page.title,
            "show_when": _show_when_file(page.show_when),
            "sections": sections,
        }
    )
    layout = LayoutFile.model_validate(
        {
            "width": page.width,
            "sections": {
                section.title: {
                    "height": section.height,
                    "components": {
                        str(number): {"x": widget.x, "y": widget.y, "w": widget.w, "h": widget.h}
                        for widget, number in zip(section.widgets, numbers, strict=True)
                    },
                }
                for section, numbers in numbered
            },
        }
    )
    return sheet, layout


def _table_file_model(table: SchemaTable, problems: list[str]) -> TableFile:
    """*table*'s file but its name. The quote's identity at its head is Haute's and not
    written; a column of either name elsewhere is the project's own and is. An output's
    columns are written without the rules only an input column takes, which they hold only
    at their defaults; one set is a problem."""
    columns: list[dict[str, Any]] = []
    held = len(IDENTITY_COLUMNS) if holds_identity(table) else 0
    for column in table.columns[held:]:
        data = column.model_dump(mode="json")
        if table.role == "input" and column.options_source is not None:
            # Its values are its params file's, read with the form (READ-25): only the
            # reference is written.
            data["options"] = []
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
    return TableFile.model_validate({"role": table.role, "rows": table.rows, "columns": columns})


def _quote_details_file(table: SchemaTable) -> QuoteDetailsFile:
    columns: list[dict[str, Any]] = []
    for column in table.columns:
        given = {
            key: value
            for key, value in column.model_dump(mode="json").items()
            if key in (*DETAILS_ANY, *DETAILS_TYPED)
            and value != SchemaColumn.model_fields[key].get_default(call_default_factory=True)
            # Sourced values are its params file's, read with the form (READ-25).
            and not (key == "options" and column.options_source is not None)
        }
        if given:
            columns.append({"name": column.name, **given})
    return QuoteDetailsFile.model_validate({"columns": columns})


def _sample_rows(table: SchemaTable, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* by column name in the table's column order, any name the table does not hold
    after them, kept as written."""
    order = [column.name for column in table.columns]
    return [
        {
            **{name: row[name] for name in order if name in row},
            **{name: value for name, value in row.items() if name not in order},
        }
        for row in rows
    ]


def folder_files(spec: FormSpec, folder: Path) -> dict[str, bytes]:
    """Every file of *spec*'s folder at *folder*, by its path from the folder.

    Raises :class:`FormFolderNameError` before anything is rendered when the folder cannot
    hold the form's names, and when an output column holds an input's rule or the sample holds
    rows of no input table.
    """
    spec = with_quote_details(spec)
    problems = folder_name_problems(spec, folder)
    if problems:
        raise FormFolderNameError(problems)
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
            if table.name == QUOTE_DETAILS_NAME
            else _table_file_model(table, problems)
        )
        files[_table_file(table.name)] = render_folder_file(model)
    for page in spec.pages:
        sheet, layout = _sheet_and_layout(page)
        sheet_path, layout_path = _sheet_files(sheet_name(page.title))
        files[sheet_path] = render_folder_file(sheet)
        files[layout_path] = render_folder_file(layout)
    inputs = {table.name: table for table in tables if table.role == "input"}
    for table_name, rows in spec.sample.items():
        if not rows:
            continue
        sampled = inputs.get(table_name)
        if sampled is None:
            problems.append(f"the sample holds rows for {table_name!r}, which is no input table")
            continue
        files[_sample_file(sampled.name)] = render_folder_file(_sample_rows(sampled, rows))
    if problems:
        raise FormFolderNameError(list(dict.fromkeys(problems)))
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


def _entry(entry: Any) -> dict[str, Any]:
    kind = _condition_kind(entry)
    if kind == "group":
        return {
            "match": entry.counted,
            "conditions": [_entry(item) for item in entry.conditions],
        }
    if kind == "one":
        return {
            **_ref(entry.column),
            "op": entry.op,
            "values": entry.values,
            "field": None if entry.field is None else _ref(entry.field),
            "days": entry.days,
        }
    table = entry.table
    where = [
        {
            "column": test.column,
            "op": test.op,
            "values": test.values,
            "field": test.field,
            "days": test.days,
        }
        for test in entry.where
    ]
    if kind == "rows":
        return {"table": table, "rows": entry.rows, "where": where}
    if kind == "count":
        return {"table": table, "where": where, "op": entry.op, "count": entry.count}
    return {
        "table": table,
        "total": entry.total,
        "column": entry.column,
        "where": where,
        "op": entry.op,
        "value": entry.value,
    }


def _show_when(show_when: ShowWhenFile) -> dict[str, Any]:
    return {
        "match": show_when.counted,
        "conditions": [_entry(item) for item in show_when.conditions],
    }


def _component(component: Any, rect: RectFile) -> dict[str, Any]:
    data: dict[str, Any] = component.model_dump(mode="json")
    data.update(x=rect.x, y=rect.y, w=rect.w, h=rect.h)
    data["show_when"] = _show_when(component.show_when)
    kind = component.type
    if kind == "table":
        data["type"] = "tableInput"
    if kind == "chart":
        data["fields"] = [_ref(field) for field in component.fields]
    if kind in ("table", "collection"):
        fields: list[dict[str, Any]] = []
        looks: list[dict[str, Any]] = []
        gaps: list[int] = []
        for entry in component.fields:
            if entry is None:
                gaps.append(len(fields))
                continue
            if isinstance(entry, str):
                fields.append(_ref(entry))
                continue
            ref = _ref(entry.field)
            fields.append(ref)
            looks.append(
                {
                    **entry.model_dump(mode="json", exclude={"field", "show_when"}),
                    **ref,
                    "show_when": _show_when(entry.show_when),
                }
            )
        data["fields"] = fields
        data["column_looks"] = looks
        if kind == "collection":
            data["spacers"] = gaps
    if kind == "chart" and component.category is not None:
        data["category"] = _ref(component.category)
    if kind == "import":
        # An Import that fills no table names no columns, which ``ImportWidget`` holds it to.
        if component.table is not None:
            data["table"] = component.table
            data["column_names"] = [
                {"column": named.column, "name": named.name} for named in component.column_names
            ]
        if component.source.format == "sql":
            data["source"]["parameters"] = [
                {"name": given.name, **_ref(given.column)} for given in component.source.parameters
            ]
    if kind == "export":
        data["tables"] = list(component.tables)
        if component.target.format == "sql":
            data["target"]["table_names"] = [
                {"table": named.table, "name": named.name} for named in component.target.table_names
            ]
    return data


def _page(
    config: WorkbenchConfig, name: str, sheet: SheetFile, layout: LayoutFile
) -> dict[str, Any]:
    """A sheet's file and its layout file as one sheet of the form, each section and
    component placed."""
    where = f"{config.form}/{_sheet_files(name)[1]}"
    sections: list[dict[str, Any]] = []
    titles = [section.title for section in sheet.sections]
    twice = next((title for title in titles if titles.count(title) > 1), None)
    if twice is not None:
        raise WorkbenchFormError(
            f"{config.form}/{_sheet_files(name)[0]} holds two sections titled {twice!r}: a "
            "section is known by its title, so give each its own",
            path=where,
        )
    for section in sheet.sections:
        placed = layout.sections.get(section.title)
        if placed is None:
            raise WorkbenchFormError(
                f"{where} leaves out the section {section.title!r}", path=where
            )
        numbers = [component.number for component in section.components]
        doubled = next((number for number in numbers if numbers.count(number) > 1), None)
        if doubled is not None:
            raise WorkbenchFormError(
                f"{config.form}/{_sheet_files(name)[0]}: the section {section.title!r} holds two "
                f"components numbered {doubled}; give one of them the next free number",
                path=where,
            )
        extras = sorted(set(placed.components) - {str(number) for number in numbers})
        if extras:
            raise WorkbenchFormError(
                f"{where} places component {extras[0]}, which the section {section.title!r} "
                "does not hold",
                path=where,
            )
        components: list[dict[str, Any]] = []
        for component in section.components:
            rect = placed.components.get(str(component.number))
            if rect is None:
                kind = "tableInput" if component.type == "table" else component.type
                named = _component_named({**component.model_dump(), "type": kind})
                raise WorkbenchFormError(
                    f"{where} leaves out {named} (number {component.number})", path=where
                )
            components.append(_component(component, rect))
        sections.append(
            {
                "title": section.title,
                "height": placed.height,
                "show_when": _show_when(section.show_when),
                "widgets": components,
            }
        )
    extra_sections = sorted(set(layout.sections) - set(titles))
    if extra_sections:
        raise WorkbenchFormError(
            f"{where} places a section {extra_sections[0]} the sheet does not hold", path=where
        )
    return {
        "title": sheet.title,
        "width": layout.width,
        "show_when": _show_when(sheet.show_when),
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
    if not index.tables or index.tables[0] != QUOTE_DETAILS_NAME:
        raise WorkbenchFormError(
            f"{config.form}/{INDEX_FILE}'s schema must start with {QUOTE_DETAILS_NAME}, "
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


def folder_index(config: WorkbenchConfig, files: Mapping[str, bytes]) -> IndexFile:
    """The index of the folder *files* make, refusing a folder that is not exactly the files
    the index names: every read takes it so, and a save before it deletes any of them."""
    if INDEX_FILE not in files:
        raise WorkbenchFormError(f"{config.form} has no {INDEX_FILE}: the folder is not a form")
    index: IndexFile = _parse(config, files, INDEX_FILE, IndexFile)
    _check_inventory(config, files, index)
    return index


def form_from_files(
    config: WorkbenchConfig,
    files: Mapping[str, bytes],
    read_params: ParamValues | None = None,
) -> tuple[FormSpec, tuple[str, ...]]:
    """The form *files* hold, with what reading it grew; a :class:`WorkbenchFormError`
    naming the file and the place for anything the folder does not hold as it should. Each
    column whose allowed values come from a params file is given them by *read_params*, the
    files at the form's own revision, the working tree's unless it says otherwise (READ-25)."""
    from haute._workbench_params import fill_sourced_options, working_tree_param_values

    index = folder_index(config, files)

    details: QuoteDetailsFile = _parse(
        config, files, _table_file(QUOTE_DETAILS_NAME), QuoteDetailsFile
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
                "name": name,
                "role": table.role,
                "rows": table.rows,
                "columns": [column.model_dump(mode="json") for column in table.columns],
            }
        )

    fill_sourced_options(tables, read_params or working_tree_param_values(config.project_root))

    pages: list[dict[str, Any]] = []
    for name in index.sheets:
        sheet_path, layout_path = _sheet_files(name)
        sheet: SheetFile = _parse(config, files, sheet_path, SheetFile)
        layout: LayoutFile = _parse(config, files, layout_path, LayoutFile)
        pages.append(_page(config, name, sheet, layout))

    roles = {name: table.role for name, table in parsed_tables}
    sample: dict[str, list[dict[str, Any]]] = {}
    for name in index.tables:
        path = _sample_file(name)
        if path not in files:
            continue
        if name == QUOTE_DETAILS_NAME or roles.get(name) == "input":
            sample[name] = [dict(row) for row in _parse(config, files, path, SAMPLE)]
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


def form_at_ref(config: WorkbenchConfig, ref: str) -> FormSpec | None:
    """The form the project's folder holds at the git *ref*, read from git with nothing
    checked out, or None when *ref* holds no form folder; a folder there that cannot be read
    as a form is a :class:`WorkbenchFormError`, as it is on disk."""
    from haute._git_core import _run_git_ok, _run_git_process

    root = config.project_root
    folder = f"{config.form}/"
    # Paths are named from the working directory, the project root, as ``./`` reads them.
    ok, listing = _run_git_ok("ls-tree", "-r", "-z", "--name-only", ref, "--", folder, cwd=root)
    names = [name for name in listing.split("\0") if name] if ok else []
    if not names:
        return None
    files: dict[str, bytes] = {}
    for name in names:
        shown = _run_git_process("show", f"{ref}:./{name}", binary=True, cwd=root)
        if shown.returncode != 0:
            raise WorkbenchFormError(f"{name} could not be read at {ref}", path=name)
        files[name.removeprefix(folder)] = shown.stdout
    from haute._workbench_params import param_values_at

    # A past revision's form reads its params files at that revision, never the working tree.
    try:
        spec, _ = form_from_files(config, files, param_values_at(root, ref))
    except WorkbenchFormError as exc:
        # A revision saved before a format change holds files this Haute refuses: say which
        # conversion brings it across rather than only what the parser refused (READ-20).
        path = str(exc.context.get("path") or "")
        raise WorkbenchFormError(
            f"The form on {ref} cannot be read with this Haute ({exc.message}). If that "
            "revision was saved before a change to the form folder's files, convert it there "
            f"with {_conversion_for(path)}, commit and push, then compare again.",
            path=path or None,
        ) from exc
    return spec


def _conversion_for(path: str) -> str:
    """The one-off conversions that bring a form folder file at *path* across (READ-20)."""
    if "sheets/" in path:
        return "scripts/one_off/read_24_sheet_names.py"
    if "schema/" in path:
        return (
            "scripts/one_off/read_23_schema_names.py and "
            "scripts/one_off/read_25_options_by_reference.py"
        )
    return "the one-off conversion scripts in scripts/one_off/"


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
        if entry.field is not None:
            yield entry.field.table, entry.field.column
        return
    yield entry.table, None
    for test in entry.where:
        yield entry.table, test.column
        if test.field is not None:
            yield entry.table, test.field
    if kind == "total":
        yield entry.table, entry.column


def _show_when_refs(show_when: Any) -> list[_Ref]:
    return [ref for entry in show_when.conditions for ref in _entry_refs(entry)]


def unresolved_references(spec: FormSpec) -> list[str]:
    """Each place *spec*'s sheets name a table or column its schema does not hold, by sheet
    file and component, as ``haute app build`` and deploy refuse them: a table by its name,
    a column as ``table.column``, as the files spell them."""
    held = {
        table.name: {column.name for column in table.columns} for table in spec.data_schema.tables
    }
    found: list[str] = []

    def spelled(table: str, column: str | None) -> str | None:
        """The reference as the files spell it, when it names what the schema does not hold."""
        if column is None:
            return None if table in held else table
        return None if column in held.get(table, ()) else f"{table}.{column}"

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
