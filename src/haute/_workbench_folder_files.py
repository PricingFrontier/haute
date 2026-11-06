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
    Days,
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

#: A table's, a column's or a component's name: an ASCII identifier.
NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
#: Where a component sits, which its sheet's file holds, not its own.
PLACEMENT = frozenset({"x", "y", "w", "h"})
#: What a component's file never holds: its id, the editor's; its name, its file's; and
#: where it sits, its sheet's.
NOT_IN_COMPONENT_FILE = PLACEMENT | {"id", "name"}
#: The settings an output column cannot have: the rules of what is typed into it. Required
#: is not among them: marking a quote final needs a value in an output column as in an input.
INPUT_ONLY = (
    "min",
    "max",
    "options",
    "options_source",
    "default_value",
)
#: What of ``INPUT_ONLY`` an output takes while it is a table of outputs alone's index of
#: values: the allowed values that give its rows.
INDEX_VALUES_SETTINGS = ("options", "options_source")
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
DETAILS_NAMES = tuple(name for name, _ in QUOTE_DETAILS_COLUMNS)


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
    """A condition on a one-row table's column, the column, and the field it is tested
    against, as ``table.column``."""

    column: Dotted
    op: ConditionTest
    values: list[_Text] = Field(default_factory=list)
    field: Dotted | None = None
    days: Days = 0


class RowTestFile(_File):
    """A test a row must pass, its column, and the column of the same row it is tested
    against, by their bare names in the condition's table."""

    column: Name
    op: ConditionTest
    values: list[_Text] = Field(default_factory=list)
    field: Name | None = None
    days: Days = 0


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


#: How many of a rule's conditions must hold, as a file writes it (READ-31): ``match`` says
#: all or any, and ``at_least`` a number of them in its place.
_AtLeast = Annotated[int, Strict(), Field(ge=1)]


def _one_match(match: object, at_least: object, where: str) -> None:
    if at_least is not None and match is not None:
        raise ValueError(f"{where} gives both match and at_least: give one of them")


class ConditionGroupFile(_File):
    match: Literal["all", "any"] | None = None
    at_least: _AtLeast | None = None
    conditions: list[SingleConditionFile] = Field(min_length=1)

    @model_validator(mode="after")
    def _match_or_at_least(self) -> ConditionGroupFile:
        _one_match(self.match, self.at_least, "a group")
        if self.match is None and self.at_least is None:
            raise ValueError("a group needs match (all or any) or at_least")
        return self

    @property
    def counted(self) -> Match:
        return self.at_least if self.at_least is not None else self.match or "all"


ShowWhenEntryFile = Annotated[
    _ONE_CONDITION_FILE | Annotated[ConditionGroupFile, Tag("group")],
    Discriminator(_condition_kind),
]


class ShowWhenFile(_File):
    match: Literal["all", "any"] = "all"
    at_least: _AtLeast | None = None
    conditions: list[ShowWhenEntryFile] = Field(default_factory=list)

    @model_validator(mode="after")
    def _match_or_at_least(self) -> ShowWhenFile:
        # A match the file leaves out is all; one it writes, all included, is given.
        given = self.match if "match" in self.model_fields_set else None
        _one_match(given, self.at_least, "a show_when")
        return self

    @property
    def counted(self) -> Match:
        return self.at_least if self.at_least is not None else self.match


_SHOW_WHEN = (ShowWhenFile, Field(default_factory=ShowWhenFile))


class QueryParameterFile(_File):
    name: Annotated[str, Field(pattern=PARAMETER_NAME)]
    column: Dotted


if TYPE_CHECKING:
    # These models are made from the form's own by ``_derived`` as the module loads, which
    # mypy cannot follow: to it each is ``Any``, while pydantic and every reader at run time
    # see the model itself.
    ColumnLookFile = Any
    FieldLookFile = Any
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
    FieldLookFile = create_model(
        "FieldLookFile",
        __base__=_File,
        field=(Dotted, ...),
        **{
            name: (info.annotation, info)
            for name, info in ColumnLook.model_fields.items()
            if name not in ("table", "column", "show_when")
        },
        show_when=_SHOW_WHEN,
    )
    TableComponentFile = _derived(
        "TableComponentFile",
        TableInputWidget,
        drop=NOT_IN_COMPONENT_FILE | {"column_looks"},
        replace={
            "type": (Literal["table"], ...),
            "fields": (list[Dotted | FieldLookFile], Field(default_factory=list)),
            "show_when": _SHOW_WHEN,
        },
    )
    CollectionComponentFile = _derived(
        "CollectionComponentFile",
        CollectionWidget,
        drop=NOT_IN_COMPONENT_FILE | {"column_looks", "spacers"},
        replace={
            "fields": (list[Dotted | FieldLookFile | None], Field(default_factory=list)),
            "show_when": _SHOW_WHEN,
        },
    )
    ChartComponentFile = _derived(
        "ChartComponentFile",
        ChartWidget,
        drop=NOT_IN_COMPONENT_FILE,
        replace={
            "fields": (list[Dotted], Field(default_factory=list)),
            "show_when": _SHOW_WHEN,
            "category": (Dotted | None, None),
        },
    )
    HeadingComponentFile = _derived(
        "HeadingComponentFile",
        HeadingWidget,
        drop=NOT_IN_COMPONENT_FILE,
        replace={"show_when": _SHOW_WHEN},
    )
    SqlImportFile = _derived(
        "SqlImportFile", SqlImport, replace={"parameters": (list[QueryParameterFile], ...)}
    )
    ColumnNameFile = _derived("ColumnNameFile", ColumnName, replace={"column": (Name, ...)})
    ImportComponentFile = _derived(
        "ImportComponentFile",
        ImportWidget,
        drop=NOT_IN_COMPONENT_FILE,
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
        drop=NOT_IN_COMPONENT_FILE,
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
        drop=frozenset({"id", "type", "index"}),
        replace={"name": (Literal[DETAILS_NAMES], ...)},
    )


#: A component's file: one of the kinds, told apart by ``type``.
COMPONENT: TypeAdapter[Any] = TypeAdapter(ComponentFile)
#: The models a component's file is read as, one a kind.
COMPONENT_FILES: tuple[type[BaseModel], ...] = (
    TableComponentFile,
    CollectionComponentFile,
    ChartComponentFile,
    HeadingComponentFile,
    ImportComponentFile,
    ExportComponentFile,
)

_Whole = Annotated[int, Strict()]


class PlacementFile(_File):
    """A component placed in a section: by its name, which names its file, and where it
    sits, in pixels from the section's top-left corner."""

    component: Name
    x: Annotated[_Whole, Field(ge=0)]
    y: Annotated[_Whole, Field(ge=0)]
    w: Annotated[_Whole, Field(gt=0)]
    h: Annotated[_Whole, Field(gt=0)]


class SectionFile(_File):
    """A section, known by its title on its sheet (READ-24), and its components in the
    order they are drawn in where two overlap."""

    title: str
    height: Annotated[_Whole, Field(gt=0)]
    show_when: ShowWhenFile = Field(default_factory=ShowWhenFile)
    components: list[PlacementFile]


class SheetFile(_File):
    """A sheet, known by its file's name (READ-24)."""

    title: str
    width: Annotated[_Whole, Field(gt=0)]
    show_when: ShowWhenFile = Field(default_factory=ShowWhenFile)
    sections: list[SectionFile] = Field(min_length=1)


class TableFile(_File):
    """A table but its name, which is its index entry: its inputs and its outputs, each
    column named once whatever its case across both, and an output holding no rule but
    Required and no index. The quote's identity, which Haute gives every table, is written in
    none; a column of either name is the project's own, a clash the schema's problems name
    (``haute._workbench_form.identity_clashes``)."""

    rows: Literal["one", "many"]
    inputs: list[ColumnFile] = Field(default_factory=list)
    outputs: list[ColumnFile] = Field(default_factory=list)

    @model_validator(mode="after")
    def _holds_its_own_columns(self) -> TableFile:
        # A column is known by its name (READ-23): two of one name whatever their case, as a
        # merge can leave them, would give the sheets one of them and not the other, and a
        # database that ignores case one column for both, whichever blocks hold them.
        names: dict[str, str] = {}
        for column in [*self.inputs, *self.outputs]:
            folded = column.name.casefold()
            if folded in names:
                raise ValueError(
                    f"two columns are named {names[folded]} and {column.name}: each column of "
                    "a table needs a name of its own, whatever its case"
                )
            names[folded] = column.name
        for column in self.outputs:
            held = INDEX_VALUES_SETTINGS if column.index == "values" else ()
            rules = [
                key for key in INPUT_ONLY if key in column.model_fields_set and key not in held
            ]
            if rules:
                raise ValueError(
                    f"{column.name} is an output and holds {', '.join(rules)}, which only an "
                    "input takes"
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
            if column.name in QUOTE_DETAILS_MANAGED:
                given = [key for key in DETAILS_TYPED if key in column.model_fields_set]
                if given:
                    raise ValueError(
                        f"{column.name} is set by the underwriter app and takes no "
                        f"{', '.join(given)}"
                    )
        return self


class IndexFile(_File):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    version: Literal[3]
    name: str
    theme: Theme = Field(default_factory=Theme)
    #: ``schema`` in the file; ``BaseModel`` already has a ``schema`` attribute.
    tables: list[Name] = Field(alias="schema")
    sheets: list[Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$", max_length=64)]]


SAMPLE: TypeAdapter[list[dict[str, Cell]]] = TypeAdapter(
    Annotated[list[dict[str, Cell]], Field(min_length=1)]
)
