"""The params files a schema column's allowed values can come from (specs/workbench): the
CSVs in the pipeline folder's ``files/params/``, as the Pipeline files panel lists them.

A file's header names the columns the schema editor offers; a column's distinct values,
trimmed, blanks left out, in the order the file first gives them, are the allowed values a
schema column copies in. The file is read as written, every value as text, so ``01`` keeps
its zero, as UTF-8 or else Windows-1252, which Excel writes on Windows. A column giving none,
or more than ``PARAM_VALUES_LIMIT``, is refused. Building the underwriter app reads every
input column's values again from its file (``with_param_options``), so a release allows what
its commit's params files hold.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from haute._artifact_paths import ordinary_path_problem
from haute._project_layout import pipeline_files_folder
from haute._workbench_config import WorkbenchError
from haute._workbench_form import FormSpec

#: The folder of the pipeline's files a column's allowed values come from.
PARAMS_FOLDER = "params"

#: The most distinct values a column gives: more is no list to choose from, and each is
#: copied into the form, and into the form kept with every revision of a quote.
PARAM_VALUES_LIMIT = 5000


class ParamFileError(WorkbenchError):
    """A params file, or a column of one, cannot give allowed values: the message says why."""


@dataclass(frozen=True, slots=True)
class ParamFile:
    """A params file's header, and the distinct values of the column asked for, if any."""

    columns: list[str]
    values: list[str]


def _params_path(project_root: Path, file: str) -> tuple[Path, str]:
    """The params file *file* names, and its path from the project root, which messages
    name it by. Only a CSV directly in the folder, by an ordinary name, is one."""
    files = pipeline_files_folder(project_root)
    if files is None:
        raise ParamFileError(
            "This project's pipeline sits at the project root, so it has no params folder."
        )
    if not file.casefold().endswith(".csv") or "/" in file or "\\" in file or file.startswith("."):
        raise ParamFileError(f"{file!r} isn't the name of a CSV file in the params folder.")
    name = f"{files}/{PARAMS_FOLDER}/{file}"
    path, problem = ordinary_path_problem(project_root, name)
    # A hard link reads as the file it is; the Pipeline files panel lists one too.
    if problem is not None and problem != "multiply_linked":
        raise ParamFileError(f"{name} isn't an ordinary file in the project.", path=name)
    if not path.is_file():
        raise ParamFileError(f"{name} doesn't exist.", path=name)
    return path, name


def _read(
    path: Path, name: str, *, columns: list[str] | None = None, n_rows: int | None = None
) -> pl.DataFrame:
    """The CSV at *path* read as text, UTF-8 when its bytes are, else Windows-1252, as Excel
    writes a "CSV (Comma delimited)" file on Windows: the encoding chosen from the bytes,
    never from what a CSV reader makes of them, since one replaces a byte it cannot read. A
    column name holding a character no encoding could give (U+FFFD, left by an earlier save
    that lost it) is refused; so is a file neither encoding reads."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ParamFileError(
            f"{name} could not be read: {exc.strerror or exc}", path=name
        ) from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError:
            raise ParamFileError(
                f"{name} isn't UTF-8 or Windows-1252 text: save it as CSV UTF-8.", path=name
            ) from None
    try:
        frame = pl.read_csv(
            io.BytesIO(text.encode("utf-8")), infer_schema=False, columns=columns, n_rows=n_rows
        )
    except pl.exceptions.NoDataError:
        raise ParamFileError(f"{name} is empty.", path=name) from None
    except pl.exceptions.PolarsError as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise ParamFileError(f"{name} could not be read as a CSV: {reason}", path=name) from None
    lost = next((column for column in frame.columns if "\ufffd" in column), None)
    if lost is not None:
        raise ParamFileError(
            f"{name} has a column named {lost!r}, a character of which was lost when the file "
            "was saved: correct the name and save it as CSV UTF-8.",
            path=name,
        )
    return frame


def read_param_file(project_root: Path, file: str, column: str | None = None) -> ParamFile:
    """The header of the params file *file* names, with the distinct values of *column*, its
    allowed values, when one is named; none otherwise.

    Raises :class:`ParamFileError` when the file is not a CSV in the params folder or cannot
    be read, or *column* is not in its header, holds no values or holds too many.
    """
    path, name = _params_path(project_root, file)
    columns = _read(path, name, n_rows=0).columns
    if column is None:
        return ParamFile(columns=columns, values=[])
    if column not in columns:
        raise ParamFileError(f"{name} has no column {column}.", path=name)
    values = (
        _read(path, name, columns=[column])
        .lazy()
        .select(pl.col(column).str.strip_chars())
        .filter(pl.col(column).is_not_null() & (pl.col(column) != ""))
        .unique(maintain_order=True)
        .head(PARAM_VALUES_LIMIT + 1)
        .collect(engine="streaming")
        .to_series()
        .to_list()
    )
    if not values:
        raise ParamFileError(f"Column {column} of {name} holds no values.", path=name)
    if len(values) > PARAM_VALUES_LIMIT:
        raise ParamFileError(
            f"Column {column} of {name} holds more than {PARAM_VALUES_LIMIT:,} distinct "
            "values: too many to choose from.",
            path=name,
        )
    return ParamFile(columns=columns, values=values)


def with_param_options(spec: FormSpec, project_root: Path) -> FormSpec:
    """*spec* with each input column whose allowed values come from a params file given them
    as the file holds them now.

    Raises :class:`ParamFileError`, naming the column, when a file or column cannot give them.
    """
    tables = []
    for table in spec.data_schema.tables:
        columns = []
        for column in table.columns:
            source = column.options_source
            if table.role == "input" and source is not None:
                try:
                    values = read_param_file(project_root, source.file, source.column).values
                except ParamFileError as exc:
                    raise ParamFileError(
                        f"The allowed values of {table.name}.{column.name} could not be read "
                        f"again: {exc.message}",
                        **exc.context,
                    ) from exc
                column = column.model_copy(update={"options": values})
            columns.append(column)
        tables.append(table.model_copy(update={"columns": columns}))
    return spec.model_copy(
        update={"data_schema": spec.data_schema.model_copy(update={"tables": tables})}
    )
