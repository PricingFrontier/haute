"""The form folder's files as models (specs/workbench): strict, each key in its order, the
keys that say what a thing is required and every other setting defaulted as ``FormSpec``
defaults it, so a file holds only what is set. References are names: a column is
``table.column``, or its bare name where its table is said. The folder module
(``haute._workbench_folder``) reads files into these and writes them from a form.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Strict,
    Tag,
    TypeAdapter,
    create_model,
    model_validator,
)

from haute._workbench_form import (
    IDENTITY_IDS,
    PARAMETER_NAME,
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_MANAGED,
    Cell,
    ChartWidget,
    CollectionWidget,
    ColumnLook,
    ColumnName,
    ConditionTest,
    CountTest,
    CsvExport,
    CsvImport,
    ExcelExport,
    ExcelImport,
    ExportWidget,
    HeadingWidget,
    ImportWidget,
    Match,
    SchemaColumn,
    SqlExport,
    SqlImport,
    TableInputWidget,
    TableName,
    Theme,
    TotalKind,
    _condition_kind,
)

#: A table's or a column's name: an ASCII identifier.
NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
#: Where a component sits, which its sheet's layout file holds, not its sheet's file.
PLACEMENT = frozenset({"x", "y", "w", "h"})
#: The settings an output column cannot have: the rules of what is typed into it.
INPUT_ONLY = (
    "required",
    "min",
    "max",
    "options",
    "options_source",
    "row_per_value",
    "default_value",
)
#: The settings ``quote_details`` takes on any column, and on those the underwriter types.
DETAILS_ANY = (
    "label",
    "group",
    "help",
    "placeholder",
    "number_format",
    "decimal_places",
    "use_grouping",
)
DETAILS_TYPED = ("required", "min", "max", "options", "options_source", "default_value")
DETAILS_NAMES = tuple(name for _, name, _ in QUOTE_DETAILS_COLUMNS)
DETAILS_IDS = {name: column_id for column_id, name, _ in QUOTE_DETAILS_COLUMNS}


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")


Name = Annotated[str, Field(pattern=rf"^{NAME_PATTERN}$")]
#: A column as ``table.column``.
Dotted = Annotated[str, Field(pattern=rf"^{NAME_PATTERN}\.{NAME_PATTERN}$")]
_Text = Annotated[str, Strict()]


def _derived(
    name: str,
    source: type[BaseModel],
    *,
    drop: frozenset[str] = frozenset(),
    replace: Mapping[str, Any] | None = None,
    required: frozenset[str] = frozenset(),
) -> type[BaseModel]:
    """A file model holding *source*'s fields in its order but *drop*: a field in *replace*
    takes the annotation and field given, one in *required* loses its default, and every
    other keeps its annotation, default and constraints."""
    fields: dict[str, Any] = {}
    for field_name, info in source.model_fields.items():
        if field_name in drop:
            continue
        if replace is not None and field_name in replace:
            fields[field_name] = replace[field_name]
        elif field_name in required:
            fields[field_name] = (info.annotation, ...)
        else:
            fields[field_name] = (info.annotation, info)
    model: type[BaseModel] = create_model(name, __base__=_File, **fields)
    return model


class ConditionFile(_File):
    """A condition on a one-row table's column, the column as ``table.column``."""

    column: Dotted
    op: ConditionTest
    values: list[_Text] = Field(default_factory=list)


class RowTestFile(_File):
    """A test a row must pass, its column by its bare name in the condition's table."""

    column: Name
    op: ConditionTest
    values: list[_Text] = Field(default_factory=list)


class RowsConditionFile(_File):
    table: Name
    rows: Literal["any", "every"]
    where: list[RowTestFile] = Field(min_length=1)


class RowCountFile(_File):
    table: Name
    where: list[RowTestFile]
    op: CountTest
    count: Annotated[int, Strict(), Field(ge=0)]


class RowTotalFile(_File):
    table: Name
    total: TotalKind
    column: Name
    where: list[RowTestFile]
    op: CountTest
    value: _Text


_ONE_CONDITION_FILE = (
    Annotated[ConditionFile, Tag("one")]
    | Annotated[RowsConditionFile, Tag("rows")]
    | Annotated[RowCountFile, Tag("count")]
    | Annotated[RowTotalFile, Tag("total")]
)
SingleConditionFile = Annotated[_ONE_CONDITION_FILE, Discriminator(_condition_kind)]


class ConditionGroupFile(_File):
    match: Match
    conditions: list[SingleConditionFile] = Field(min_length=1)


ShowWhenEntryFile = Annotated[
    _ONE_CONDITION_FILE | Annotated[ConditionGroupFile, Tag("group")],
    Discriminator(_condition_kind),
]


class ShowWhenFile(_File):
    match: Match = "all"
    conditions: list[ShowWhenEntryFile] = Field(default_factory=list)


_SHOW_WHEN = (ShowWhenFile, Field(default_factory=ShowWhenFile))


class QueryParameterFile(_File):
    name: Annotated[str, Field(pattern=PARAMETER_NAME)]
    column: Dotted


if TYPE_CHECKING:
    # These models are made from the form's own by ``_derived`` as the module loads, which
    # mypy cannot follow: to it each is ``Any``, while pydantic and every reader at run time
    # see the model itself.
    ColumnLookFile = Any
    TableComponentFile = Any
    CollectionComponentFile = Any
    ChartComponentFile = Any
    HeadingComponentFile = Any
    SqlImportFile = Any
    ColumnNameFile = Any
    ImportComponentFile = Any
    TableNameFile = Any
    SqlExportFile = Any
    ExportComponentFile = Any
    ComponentFile = Any
    ColumnFile = Any
    QuoteDetailsColumnFile = Any
else:
    ColumnLookFile = _derived(
        "ColumnLookFile",
        ColumnLook,
        drop=frozenset({"table"}),
        replace={"column": (Dotted, ...), "show_when": _SHOW_WHEN},
    )
    _COMPONENT_LOOKS = {
        "fields": (list[Dotted], Field(default_factory=list)),
        "show_when": _SHOW_WHEN,
        "column_looks": (list[ColumnLookFile], Field(default_factory=list)),
    }
    TableComponentFile = _derived(
        "TableComponentFile",
        TableInputWidget,
        drop=PLACEMENT,
        replace={"type": (Literal["table"], ...), **_COMPONENT_LOOKS},
    )
    CollectionComponentFile = _derived(
        "CollectionComponentFile", CollectionWidget, drop=PLACEMENT, replace=_COMPONENT_LOOKS
    )
    ChartComponentFile = _derived(
        "ChartComponentFile",
        ChartWidget,
        drop=PLACEMENT,
        replace={
            "fields": (list[Dotted], Field(default_factory=list)),
            "show_when": _SHOW_WHEN,
            "category": (Dotted | None, None),
        },
    )
    HeadingComponentFile = _derived(
        "HeadingComponentFile", HeadingWidget, drop=PLACEMENT, replace={"show_when": _SHOW_WHEN}
    )
    SqlImportFile = _derived(
        "SqlImportFile", SqlImport, replace={"parameters": (list[QueryParameterFile], ...)}
    )
    ColumnNameFile = _derived("ColumnNameFile", ColumnName, replace={"column": (Name, ...)})
    ImportComponentFile = _derived(
        "ImportComponentFile",
        ImportWidget,
        drop=PLACEMENT,
        replace={
            "show_when": _SHOW_WHEN,
            "source": (
                Annotated[CsvImport | ExcelImport | SqlImportFile, Field(discriminator="format")],
                ...,
            ),
            "table": (Name | None, ...),
            "column_names": (list[ColumnNameFile], ...),
        },
    )
    TableNameFile = _derived("TableNameFile", TableName, replace={"table": (Name, ...)})
    SqlExportFile = _derived(
        "SqlExportFile", SqlExport, replace={"table_names": (list[TableNameFile], ...)}
    )
    ExportComponentFile = _derived(
        "ExportComponentFile",
        ExportWidget,
        drop=PLACEMENT,
        replace={
            "show_when": _SHOW_WHEN,
            "target": (
                Annotated[CsvExport | ExcelExport | SqlExportFile, Field(discriminator="format")],
                ...,
            ),
            "tables": (list[Name], ...),
        },
    )
    ComponentFile = Annotated[
        TableComponentFile
        | CollectionComponentFile
        | ChartComponentFile
        | HeadingComponentFile
        | ImportComponentFile
        | ExportComponentFile,
        Field(discriminator="type"),
    ]
    # An identifier, as the writer holds it to: `table.column` splits at its one dot.
    ColumnFile = _derived(
        "ColumnFile", SchemaColumn, replace={"name": (Name, ...)}, required=frozenset({"type"})
    )
    QuoteDetailsColumnFile = _derived(
        "QuoteDetailsColumnFile",
        SchemaColumn,
        drop=frozenset({"id", "type", "key", "row_per_value", "index"}),
        replace={"name": (Literal[DETAILS_NAMES], ...)},
    )


class SectionFile(_File):
    id: str
    title: str
    show_when: ShowWhenFile = Field(default_factory=ShowWhenFile)
    components: list[ComponentFile]


class SheetFile(_File):
    id: str
    title: str
    show_when: ShowWhenFile = Field(default_factory=ShowWhenFile)
    sections: list[SectionFile] = Field(min_length=1)


_Whole = Annotated[int, Strict()]


class RectFile(_File):
    """Where a component sits in its section, in pixels from its top-left corner."""

    x: Annotated[_Whole, Field(ge=0)]
    y: Annotated[_Whole, Field(ge=0)]
    w: Annotated[_Whole, Field(gt=0)]
    h: Annotated[_Whole, Field(gt=0)]


class LayoutSectionFile(_File):
    height: Annotated[_Whole, Field(gt=0)]
    components: dict[str, RectFile]


class LayoutFile(_File):
    width: Annotated[_Whole, Field(gt=0)]
    sections: dict[str, LayoutSectionFile]


class TableFile(_File):
    """A table but its name, which is its index entry: an output's columns hold no rule,
    and no table holds the quote's identity, which Haute gives every table."""

    id: str
    role: Literal["input", "output"]
    rows: Literal["one", "many"]
    columns: list[ColumnFile]

    @model_validator(mode="after")
    def _holds_its_own_columns(self) -> TableFile:
        for column in self.columns:
            if column.id in IDENTITY_IDS:
                raise ValueError(
                    f"{column.name} is the quote's identity, which Haute holds in every "
                    "table: remove it"
                )
            if self.role == "output":
                rules = [key for key in INPUT_ONLY if key in column.model_fields_set]
                if rules:
                    raise ValueError(
                        f"output column {column.name} holds {', '.join(rules)}, which only "
                        "an input column takes"
                    )
        return self


class QuoteDetailsFile(_File):
    """What the project sets on Haute's ``quote_details``: each column it gives something,
    in Haute's order, with what it gives it."""

    columns: list[QuoteDetailsColumnFile]

    @model_validator(mode="after")
    def _sets_only_its_own(self) -> QuoteDetailsFile:
        names = [column.name for column in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("a column is given twice")
        if names != [name for name in DETAILS_NAMES if name in names]:
            raise ValueError("the columns must be in Haute's order")
        for column in self.columns:
            if DETAILS_IDS[column.name] in QUOTE_DETAILS_MANAGED:
                given = [key for key in DETAILS_TYPED if key in column.model_fields_set]
                if given:
                    raise ValueError(
                        f"{column.name} is set by the underwriter app and takes no "
                        f"{', '.join(given)}"
                    )
        return self


class IndexFile(_File):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    version: Literal[1]
    name: str
    theme: Theme = Field(default_factory=Theme)
    #: ``schema`` in the file; ``BaseModel`` already has a ``schema`` attribute.
    tables: list[Name] = Field(alias="schema")
    sheets: list[Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$", max_length=64)]]


SAMPLE: TypeAdapter[list[dict[str, Cell]]] = TypeAdapter(
    Annotated[list[dict[str, Cell]], Field(min_length=1)]
)
