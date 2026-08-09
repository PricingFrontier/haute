"""Format registry for the dataInput / dataOutput node types.

One frozen dataclass + one tuple: everything else derives. A fully-configured
``dataInput`` node is equivalent to exactly one invocation of a polars input
callable (``read_*``/``scan_*``/``from_*``); ``dataOutput`` covers the
``write_*``/``sink_*`` surface for single tables. The argument surface is not
hand-typed — it derives from the committed interface schema intersected with
the installed polars (:mod:`haute._polars_io_schema`), so any polars inside
the specifier gets an argument surface that version actually accepts, and a
signature change is caught by the drift contract test rather than by users.

Design rules carried over from the io-nodes review (IO12):

- **Chunkability is opt-in** — nothing here registers with the chunking
  machinery; new formats are not chunkable by omission.
- **Bounded memory is enforced before parse** — eager-only formats are
  refused in bounded profiles up front, mirroring the plain-JSON rule in
  ``read_source``.
- **Security posture is format-independent** — every path-kind source/target
  goes through the same URL/`..` guard as the existing nodes; remote object
  stores stay a separate policy decision (their argument surface —
  ``storage_options``, ``credential_provider``, … — is excluded from configs).
- **This pass adds no runtime dependencies** — formats whose polars callable
  needs an engine package (Excel, ODS, database, Delta, Iceberg) are fully
  configurable, marked with their requirements, and fail loudly with an
  actionable message when the engine is absent.

Struct capability: nothing in this module inspects or restricts column
dtypes. Struct/List-valued columns flow through untouched; schema-declaration
arguments decode via the struct-capable codec in ``_polars_dtypes``.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

import polars as pl

from haute._execution_context import ExecutionProfile
from haute._polars_dtypes import parse_schema_mapping
from haute._polars_io_schema import retired_argument_names, supported_argument_names
from haute._polars_utils import is_bounded_execution_profile
from haute.errors import BoundedMemoryUnsupportedError

SourceKind = Literal["path", "database", "inline"]
InputMode = Literal["read", "scan"]
OutputMode = Literal["write", "sink"]
IoGroup = Literal["file", "database", "lakehouse", "inline"]
_IO_UNIVERSAL_KEYS = {
    "instanceOf",
    "inputMapping",
    "selected_columns",
    "column_renames",
    "categorical_levels",
    "contract",
}


def format_group(fmt: IoFormat) -> IoGroup:
    """Return the editor group that owns a registered format."""
    if fmt.name in {"delta", "iceberg"}:
        return "lakehouse"
    if fmt.source_kind == "database":
        return "database"
    if fmt.source_kind == "inline":
        return "inline"
    return "file"


@dataclass(frozen=True, slots=True)
class IoFormat:
    """One format's capabilities across the read/scan/write/sink surface."""

    name: str
    label: str
    source_kind: SourceKind
    # polars callable names; None = that operation does not exist for the format.
    reader: str | None = None  # eager module function (polars.<reader>)
    scanner: str | None = None  # lazy module function (polars.<scanner>)
    writer: str | None = None  # DataFrame method
    sinker: str | None = None  # LazyFrame method
    # Advisory extensions for pickers/editors. Dispatch is always the explicit
    # ``format`` config key — never extension sniffing.
    extensions: tuple[str, ...] = ()
    # False → reading this format requires eager parsing, which bounded
    # profiles refuse BEFORE the parse begins.
    bounded_read: bool = False
    # True → bounded reads additionally require a full declared ``schema``
    # argument (the generic form of the CSV declared-dtypes rule).
    needs_schema_when_bounded: bool = False
    unstable: bool = False
    # Engine packages (import names): reading/writing needs at least one
    # importable. Empty = polars-native, always available.
    read_engines: tuple[str, ...] = ()
    write_engines: tuple[str, ...] = ()
    # Argument names owned by the node's source/target fields rather than the
    # ``arguments`` dict (the first positional(s) of the polars callables).
    source_owned_args: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for extension in self.extensions:
            if (
                len(extension) < 2
                or not extension.startswith(".")
                or extension != extension.casefold()
                or "/" in extension
                or "\\" in extension
            ):
                raise ValueError(
                    f"{self.name} extension {extension!r} must be a lower-case leading-dot suffix"
                )


FORMATS: tuple[IoFormat, ...] = (
    IoFormat(
        name="csv",
        label="CSV",
        source_kind="path",
        reader="read_csv",
        scanner="scan_csv",
        writer="write_csv",
        sinker="sink_csv",
        extensions=(".csv",),
        bounded_read=True,
        needs_schema_when_bounded=True,
        source_owned_args=frozenset({"source", "file", "path"}),
    ),
    IoFormat(
        name="json",
        label="JSON",
        source_kind="path",
        reader="read_json",
        writer="write_json",
        extensions=(".json",),
        bounded_read=False,
        source_owned_args=frozenset({"source", "file"}),
    ),
    IoFormat(
        name="ndjson",
        label="NDJSON",
        source_kind="path",
        reader="read_ndjson",
        scanner="scan_ndjson",
        writer="write_ndjson",
        sinker="sink_ndjson",
        extensions=(".jsonl", ".ndjson"),
        bounded_read=True,
        source_owned_args=frozenset({"source", "file", "path"}),
    ),
    IoFormat(
        name="parquet",
        label="Parquet",
        source_kind="path",
        reader="read_parquet",
        scanner="scan_parquet",
        writer="write_parquet",
        sinker="sink_parquet",
        extensions=(".parquet",),
        bounded_read=True,
        source_owned_args=frozenset({"source", "file", "path"}),
    ),
    IoFormat(
        name="ipc",
        label="Arrow IPC / Feather",
        source_kind="path",
        reader="read_ipc",
        scanner="scan_ipc",
        writer="write_ipc",
        sinker="sink_ipc",
        extensions=(".arrow", ".feather", ".ipc"),
        bounded_read=True,
        source_owned_args=frozenset({"source", "file", "path"}),
    ),
    IoFormat(
        name="ipc_stream",
        label="Arrow IPC stream",
        source_kind="path",
        reader="read_ipc_stream",
        writer="write_ipc_stream",
        extensions=(".arrows",),
        bounded_read=False,
        source_owned_args=frozenset({"source", "file"}),
    ),
    IoFormat(
        name="avro",
        label="Avro",
        source_kind="path",
        reader="read_avro",
        writer="write_avro",
        extensions=(".avro",),
        bounded_read=False,
        source_owned_args=frozenset({"source", "file"}),
    ),
    IoFormat(
        name="excel",
        label="Excel",
        source_kind="path",
        reader="read_excel",
        writer="write_excel",
        extensions=(".xlsx", ".xlsm", ".xlsb", ".xls"),
        bounded_read=False,
        read_engines=("fastexcel", "openpyxl", "xlsx2csv"),
        write_engines=("xlsxwriter",),
        source_owned_args=frozenset({"source", "workbook"}),
    ),
    IoFormat(
        name="ods",
        label="OpenDocument spreadsheet",
        source_kind="path",
        reader="read_ods",
        extensions=(".ods",),
        bounded_read=False,
        read_engines=("fastexcel",),
        source_owned_args=frozenset({"source"}),
    ),
    IoFormat(
        name="lines",
        label="Text lines",
        source_kind="path",
        reader="read_lines",
        scanner="scan_lines",
        extensions=(".txt", ".log"),
        bounded_read=True,
        unstable=True,
        source_owned_args=frozenset({"source"}),
    ),
    IoFormat(
        name="database",
        label="Database (URI)",
        source_kind="database",
        reader="read_database_uri",
        writer="write_database",
        bounded_read=False,
        read_engines=("connectorx", "adbc_driver_manager"),
        write_engines=("sqlalchemy", "adbc_driver_manager"),
        source_owned_args=frozenset({"query", "uri", "connection", "table_name"}),
    ),
    IoFormat(
        name="delta",
        label="Delta Lake",
        source_kind="path",
        reader="read_delta",
        scanner="scan_delta",
        writer="write_delta",
        sinker="sink_delta",
        bounded_read=True,
        read_engines=("deltalake",),
        write_engines=("deltalake",),
        source_owned_args=frozenset({"source", "target"}),
    ),
    IoFormat(
        name="iceberg",
        label="Iceberg",
        source_kind="path",
        scanner="scan_iceberg",
        writer="write_iceberg",
        sinker="sink_iceberg",
        bounded_read=True,
        unstable=True,
        read_engines=("pyiceberg",),
        write_engines=("pyiceberg",),
        source_owned_args=frozenset({"source", "target"}),
    ),
    IoFormat(
        name="records",
        label="Inline records",
        source_kind="inline",
        reader="from_dicts",
        # The records live in the node config itself, so "eager" here does not
        # pull unbounded data from disk — bounded profiles may build them.
        bounded_read=True,
        source_owned_args=frozenset({"data"}),
    ),
)

FORMATS_BY_NAME: dict[str, IoFormat] = {fmt.name: fmt for fmt in FORMATS}

# Argument-name classes excluded from node configs: remote-IO arguments ride
# the retained local-path posture; execution-owned sink arguments belong to
# haute's execution discipline, not per-node config.
REMOTE_IO_ARGUMENTS: frozenset[str] = frozenset(
    {"storage_options", "credential_provider", "retries", "file_cache_ttl"}
)
_OBJECT_VALUED_ARGUMENTS: frozenset[str] = frozenset(
    {"with_column_names", "delta_merge_options", "pyarrow_options", "credentials"}
)
_SINK_EXECUTION_OWNED: frozenset[str] = frozenset({"lazy", "engine", "optimizations"})

_DTYPE_MAPPING_ARGUMENTS: frozenset[str] = frozenset(
    {"schema", "schema_overrides", "hive_schema", "dtypes"}
)


class PolarsIoConfigError(ValueError):
    """A dataInput/dataOutput config does not describe a valid invocation."""


def _require_nonempty_string(config: Mapping[str, Any], field: str, *, subject: str) -> None:
    value = config.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PolarsIoConfigError(f"{subject} requires a non-empty {field!r}.")


def _validate_raw_uri(uri: str) -> None:
    from haute._credential_security import (
        CredentialMaterialError,
        validate_credential_free_uri,
    )

    try:
        validate_credential_free_uri(uri)
    except CredentialMaterialError as exc:
        raise PolarsIoConfigError("Raw database 'uri' must not contain credentials.") from exc


def _validate_exactly_one_locator(config: Mapping[str, Any], *, subject: str) -> None:
    connection = config.get("connection")
    uri = config.get("uri")
    has_connection = isinstance(connection, str) and bool(connection.strip())
    has_uri = isinstance(uri, str) and bool(uri.strip())
    if has_connection == has_uri:
        raise PolarsIoConfigError(
            f"{subject} requires exactly one non-empty 'connection' or 'uri'."
        )
    if has_uri:
        _validate_raw_uri(cast(str, uri))


def _reject_inactive_fields(
    config: Mapping[str, Any], *, allowed: set[str], discriminant: str, value: str
) -> None:
    for field in config:
        if field not in allowed:
            raise PolarsIoConfigError(f"Field {field!r} is not valid for {discriminant} {value!r}.")


def data_input_is_direct(config: Mapping[str, Any]) -> bool:
    """THE derivation of a Data Input's execution mode — never stored in config.

    A file-backed Parquet scan already has the lazy, schema-bearing execution
    shape a snapshot would duplicate, so it is read directly from its
    configured source. Every other canonical input executes from a published
    snapshot generation. An absent or blank ``mode`` means the format's
    default — the same unset rule as :func:`resolve_input_mode` — which for
    Parquet is ``scan``.
    """
    return (
        config.get("inputType") == "file"
        and config.get("format") == "parquet"
        and config.get("mode") in (None, "", "scan")
    )


def validate_data_input_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate one persisted canonical ``dataInput`` config.

    The removed ``cacheMode`` field has no compatibility path: direct-versus-
    snapshot execution is derived by :func:`data_input_is_direct`, so a config
    still carrying the field is rejected as an inactive field.
    """
    result = dict(config)
    input_type = result.get("inputType")
    if input_type not in {"file", "database", "lakehouse", "databricks", "inline"}:
        raise PolarsIoConfigError(f"Unknown inputType {input_type!r}.")

    common = {
        "inputType",
        "format",
        "arguments",
        "code",
    } | _IO_UNIVERSAL_KEYS
    polars_common = common | {"mode"}
    if input_type == "databricks":
        _reject_inactive_fields(
            result,
            allowed={
                "inputType",
                "http_path",
                "table",
                "query",
                "arguments",
                "code",
            }
            | _IO_UNIVERSAL_KEYS,
            discriminant="inputType",
            value=input_type,
        )
        _require_nonempty_string(result, "http_path", subject="Databricks input")
        _require_nonempty_string(result, "table", subject="Databricks input")
        query = result.get("query")
        if query is not None and (not isinstance(query, str) or not query.strip()):
            raise PolarsIoConfigError(
                "Databricks input 'query' must be a non-empty string when set."
            )
        arguments = result.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise PolarsIoConfigError("Databricks input 'arguments' must be an object.")
        unknown = set(arguments) - {"batch_size"}
        if unknown:
            raise PolarsIoConfigError(
                f"Databricks input does not support arguments {sorted(unknown)}."
            )
        if "batch_size" in arguments and (
            isinstance(arguments["batch_size"], bool)
            or not isinstance(arguments["batch_size"], int)
            or arguments["batch_size"] <= 0
        ):
            raise PolarsIoConfigError("Databricks input 'batch_size' must be a positive integer.")
        return result

    fmt = format_for_config(result)
    group = format_group(fmt)
    if input_type != group:
        raise PolarsIoConfigError(
            f"Format {fmt.name!r} belongs to group {group!r}, not inputType {input_type!r}."
        )
    mode = resolve_input_mode(fmt, result) if input_type != "database" else None
    if input_type == "file":
        _reject_inactive_fields(
            result, allowed=polars_common | {"path"}, discriminant="inputType", value=input_type
        )
        _require_nonempty_string(result, "path", subject=f"Format {fmt.name!r}")
    elif input_type == "lakehouse":
        _reject_inactive_fields(
            result, allowed=polars_common | {"path"}, discriminant="inputType", value=input_type
        )
        _require_nonempty_string(result, "path", subject=f"Format {fmt.name!r}")
    elif input_type == "database":
        _reject_inactive_fields(
            result,
            allowed=common | {"connection", "uri", "query"},
            discriminant="inputType",
            value=input_type,
        )
        _validate_exactly_one_locator(result, subject="Database input")
        _require_nonempty_string(result, "query", subject="Database input")
        arguments = result.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise PolarsIoConfigError("Database input 'arguments' must be an object.")
        unknown = set(arguments) - {"batch_size"}
        if unknown:
            raise PolarsIoConfigError(
                f"Database input does not support arguments {sorted(unknown)}."
            )
        if "batch_size" in arguments and (
            isinstance(arguments["batch_size"], bool)
            or not isinstance(arguments["batch_size"], int)
            or arguments["batch_size"] <= 0
        ):
            raise PolarsIoConfigError("Database input 'batch_size' must be a positive integer.")
    else:
        _reject_inactive_fields(
            result,
            allowed=polars_common | {"records"},
            discriminant="inputType",
            value=input_type,
        )
        if not isinstance(result.get("records"), list):
            raise PolarsIoConfigError("Inline input requires 'records' as a list.")
        if not all(isinstance(record, Mapping) for record in result["records"]):
            raise PolarsIoConfigError("Inline input 'records' must contain objects.")

    if input_type != "database":
        assert mode is not None
        owner, callable_name = input_callable_key(fmt, mode)
        validate_arguments(fmt, owner, callable_name, result.get("arguments") or {})
    return result


def validate_data_output_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate one persisted canonical ``dataOutput`` config."""
    result = dict(config)
    output_type = result.get("outputType")
    if output_type not in {"file", "database", "lakehouse"}:
        raise PolarsIoConfigError(f"Unknown outputType {output_type!r}.")
    fmt = format_for_config(result)
    group = format_group(fmt)
    if output_type != group:
        raise PolarsIoConfigError(
            f"Format {fmt.name!r} belongs to group {group!r}, not outputType {output_type!r}."
        )
    if fmt.writer is None and fmt.sinker is None:
        raise PolarsIoConfigError(f"Format {fmt.name!r} has no output capability.")
    common = {"outputType", "format", "mode", "arguments"} | _IO_UNIVERSAL_KEYS
    if output_type == "database":
        _reject_inactive_fields(
            result,
            allowed=common | {"connection", "uri", "table"},
            discriminant="outputType",
            value=output_type,
        )
        _validate_exactly_one_locator(result, subject="Database output")
        _require_nonempty_string(result, "table", subject="Database output")
    else:
        _reject_inactive_fields(
            result, allowed=common | {"path"}, discriminant="outputType", value=output_type
        )
        _require_nonempty_string(result, "path", subject=f"Format {fmt.name!r} output")
    mode = resolve_output_mode(fmt, result)
    owner, callable_name = output_callable_key(fmt, mode)
    validate_arguments(fmt, owner, callable_name, result.get("arguments") or {})
    return result


def format_for_config(config: Mapping[str, Any]) -> IoFormat:
    """Resolve the ``format`` key of a node config to a registry entry."""
    name = config.get("format")
    if not isinstance(name, str) or not name:
        raise PolarsIoConfigError(
            "Config requires a non-empty 'format'. Supported formats: "
            + ", ".join(sorted(FORMATS_BY_NAME))
        )
    fmt = FORMATS_BY_NAME.get(name)
    if fmt is None:
        raise PolarsIoConfigError(
            f"Unsupported format: {name!r}. Supported formats: "
            + ", ".join(sorted(FORMATS_BY_NAME))
        )
    return fmt


def _callable_owner(fmt_callable: str, *, owner: str) -> tuple[str, str]:
    return owner, fmt_callable


def input_callable_key(fmt: IoFormat, mode: InputMode) -> tuple[str, str]:
    name = fmt.scanner if mode == "scan" else fmt.reader
    if name is None:
        raise PolarsIoConfigError(
            f"Format {fmt.name!r} has no {'lazy scan' if mode == 'scan' else 'eager read'} "
            f"support in polars."
        )
    return ("polars", name)


def output_callable_key(fmt: IoFormat, mode: OutputMode) -> tuple[str, str]:
    if mode == "sink":
        if fmt.sinker is None:
            raise PolarsIoConfigError(f"Format {fmt.name!r} has no streaming sink in polars.")
        return ("LazyFrame", fmt.sinker)
    if fmt.writer is None:
        raise PolarsIoConfigError(f"Format {fmt.name!r} has no write support in polars.")
    return ("DataFrame", fmt.writer)


def resolve_input_mode(fmt: IoFormat, config: Mapping[str, Any]) -> InputMode:
    mode = config.get("mode")
    if mode in (None, ""):
        default: InputMode = "scan" if fmt.scanner is not None else "read"
        input_callable_key(fmt, default)  # a format with no read surface fails loudly
        return default
    if mode not in ("read", "scan"):
        raise PolarsIoConfigError(
            f"Input mode must be 'read' or 'scan', got {mode!r} (format {fmt.name!r})."
        )
    # Validate availability eagerly so a bad mode fails at config time.
    input_callable_key(fmt, mode)
    return cast(InputMode, mode)


def resolve_output_mode(fmt: IoFormat, config: Mapping[str, Any]) -> OutputMode:
    mode = config.get("mode")
    if mode in (None, ""):
        default: OutputMode = "sink" if fmt.sinker is not None else "write"
        output_callable_key(fmt, default)  # a read-only format fails loudly
        return default
    if mode not in ("write", "sink"):
        raise PolarsIoConfigError(
            f"Output mode must be 'write' or 'sink', got {mode!r} (format {fmt.name!r})."
        )
    output_callable_key(fmt, mode)
    return cast(OutputMode, mode)


def allowed_arguments(fmt: IoFormat, owner: str, callable_name: str) -> frozenset[str]:
    """Config-expressible argument names for one polars callable.

    Derived from the committed interface schema intersected with the installed
    polars' signature, minus the excluded classes: underscore-private
    plumbing, remote-IO arguments, object-valued arguments, execution-owned
    sink arguments, and the source/target arguments owned by the node's own
    fields.

    The intersection is what lets one committed schema serve the whole
    ``polars`` specifier. The exclusions below are subtractive, so an argument
    a newer polars introduces would otherwise be config-expressible without
    ever having been classified; and an argument a newer polars has dropped
    from the signature would otherwise stay on offer, resting on whatever
    deprecation shim happens to still accept it.
    """
    names = set(supported_argument_names(owner, callable_name))
    names -= {n for n in names if n.startswith("_")}
    names -= REMOTE_IO_ARGUMENTS
    names -= _OBJECT_VALUED_ARGUMENTS
    names -= fmt.source_owned_args
    if callable_name.startswith("sink_"):
        names -= _SINK_EXECUTION_OWNED
    return frozenset(names)


def validate_arguments(
    fmt: IoFormat,
    owner: str,
    callable_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate + decode a config ``arguments`` mapping for one callable.

    Unknown names fail loudly with the polars callable named; dtype-mapping
    arguments are decoded through the struct-capable codec.
    """
    if not isinstance(arguments, Mapping):
        raise PolarsIoConfigError("Config 'arguments' must be an object.")
    allowed = allowed_arguments(fmt, owner, callable_name)
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        # Name the version boundary when that is what removed the argument;
        # "unknown argument" alone sends the reader hunting for a typo.
        retired = sorted(retired_argument_names(owner, callable_name).intersection(unknown))
        because = (
            f" Argument(s) {retired} are absent from the installed polars {pl.__version__}."
            if retired
            else ""
        )
        raise PolarsIoConfigError(
            f"Unknown or unsupported argument(s) {unknown} for polars.{callable_name} "
            f"(format {fmt.name!r}). Config-expressible arguments: {sorted(allowed)}.{because}"
        )
    decoded: dict[str, Any] = {}
    for name, value in arguments.items():
        if name in _DTYPE_MAPPING_ARGUMENTS and isinstance(value, Mapping):
            decoded[name] = parse_schema_mapping(value, argument=name)
        else:
            decoded[name] = value
    return decoded


def missing_engines(engines: tuple[str, ...]) -> list[str]:
    """Return the engine list when none of *engines* is importable (else [])."""
    if not engines:
        return []
    for module in engines:
        if importlib.util.find_spec(module) is not None:
            return []
    return list(engines)


def _require_engines(fmt: IoFormat, engines: tuple[str, ...], *, operation: str) -> None:
    absent = missing_engines(engines)
    if absent:
        raise PolarsIoConfigError(
            f"Format {fmt.name!r} needs an engine package to {operation}: install one of "
            f"{absent}. This haute install has none of them."
        )


def _is_bounded_profile(profile: ExecutionProfile | str | None) -> bool:
    return is_bounded_execution_profile(profile)


def _resolve_input_source(fmt: IoFormat, config: Mapping[str, Any]) -> tuple[Any, ...]:
    """Leading positional argument(s) for the input callable, from node fields."""
    from haute._io import _validate_source_path

    if fmt.source_kind == "path":
        path = config.get("path")
        if not isinstance(path, str) or not path.strip():
            raise PolarsIoConfigError(f"Format {fmt.name!r} requires a non-empty 'path'.")
        return (_validate_source_path(path),)
    if fmt.source_kind == "database":
        query = config.get("query")
        uri = config.get("uri")
        if not isinstance(query, str) or not query.strip():
            raise PolarsIoConfigError("Database input requires a non-empty 'query'.")
        if not isinstance(uri, str) or not uri.strip():
            raise PolarsIoConfigError("Database input requires a non-empty 'uri'.")
        return (query, uri)
    # inline records
    records = config.get("records")
    if not isinstance(records, list):
        raise PolarsIoConfigError("Inline-records input requires 'records' as a list of objects.")
    return (records,)


def read_polars_input(
    config: Mapping[str, Any],
    *,
    profile: ExecutionProfile | str | None = None,
) -> pl.LazyFrame:
    """Execute the polars input invocation a dataInput config describes.

    Returns a LazyFrame (eager reads are wrapped ``.lazy()``). Struct/array
    values flow through untouched — no dtype guards, by ruling.
    """
    fmt = format_for_config(config)
    mode = resolve_input_mode(fmt, config)
    owner, callable_name = input_callable_key(fmt, mode)
    arguments = validate_arguments(fmt, owner, callable_name, config.get("arguments") or {})

    if _is_bounded_profile(profile):
        if mode == "read" and not (fmt.source_kind == "inline"):
            if fmt.scanner is not None:
                raise BoundedMemoryUnsupportedError(
                    f"Format {fmt.name!r} was configured with eager mode 'read', which "
                    "bounded-memory execution profiles refuse. Use mode 'scan'.",
                    format=fmt.name,
                    profile=str(profile),
                )
            if not fmt.bounded_read:
                raise BoundedMemoryUnsupportedError(
                    f"Format {fmt.name!r} requires eager parsing and is not supported "
                    "for bounded-memory execution profiles. Cache it to parquet first.",
                    format=fmt.name,
                    profile=str(profile),
                )
        if fmt.needs_schema_when_bounded and "schema" not in arguments:
            raise BoundedMemoryUnsupportedError(
                f"Format {fmt.name!r} requires a full declared 'schema' argument for "
                "bounded-memory execution profiles (schema inference reads the data).",
                format=fmt.name,
                profile=str(profile),
            )

    _require_engines(fmt, fmt.read_engines, operation="read")

    source_args = _resolve_input_source(fmt, config)
    fn = getattr(pl, callable_name)
    try:
        result = fn(*source_args, **arguments)
    except (ImportError, ModuleNotFoundError) as exc:
        raise PolarsIoConfigError(
            f"Reading format {fmt.name!r} needs an engine package this haute install "
            f"lacks ({exc}). Candidates: {list(fmt.read_engines)}."
        ) from exc

    if isinstance(result, pl.LazyFrame):
        return result
    if isinstance(result, pl.DataFrame):
        return result.lazy()
    raise PolarsIoConfigError(
        f"polars.{callable_name} returned {type(result).__name__}, not a table; "
        f"format {fmt.name!r} config does not describe a single-table input."
    )


def _resolve_output_target(fmt: IoFormat, config: Mapping[str, Any]) -> dict[str, Any]:
    """Target fields for an output invocation (validated, not resolved to disk)."""
    if fmt.source_kind == "database":
        from haute._database_io import resolve_connection_uri

        table = config.get("table")
        if not isinstance(table, str) or not table.strip():
            raise PolarsIoConfigError("Database output requires a non-empty 'table'.")
        return {"table": table, "uri": resolve_connection_uri(config)}
    path = config.get("path")
    if not isinstance(path, str) or not path.strip():
        raise PolarsIoConfigError(f"Format {fmt.name!r} requires a non-empty output 'path'.")
    return {"path": path}


def write_polars_output(
    lf: pl.LazyFrame,
    config: Mapping[str, Any],
    *,
    resolved_path: Any = None,
    profile: ExecutionProfile | str = ExecutionProfile.LAZY_SINK,
) -> int | None:
    """Execute the polars output invocation a dataOutput config describes.

    *resolved_path* is the filesystem target the caller resolved (sink-path
    discipline lives with the executor); database outputs ignore it. Returns
    a row count when the write path reports one (eager writes, database),
    else ``None`` (streaming sinks — the caller may re-scan).

    Eager write formats materialise through the profiled streaming-collect
    contract (typed bounded-memory error, never a silent broad collect).
    """
    from haute._polars_utils import streaming_collect

    fmt = format_for_config(config)
    mode = resolve_output_mode(fmt, config)
    owner, callable_name = output_callable_key(fmt, mode)
    arguments = validate_arguments(fmt, owner, callable_name, config.get("arguments") or {})
    target = _resolve_output_target(fmt, config)

    _require_engines(fmt, fmt.write_engines, operation="write")

    try:
        if fmt.source_kind == "database":
            df = streaming_collect(lf)
            rows = df.write_database(target["table"], connection=target["uri"], **arguments)
            return int(rows) if isinstance(rows, int) else df.height

        if resolved_path is None:
            raise PolarsIoConfigError(
                f"Format {fmt.name!r} output requires a resolved filesystem path."
            )
        if mode == "sink":
            getattr(lf, callable_name)(resolved_path, **arguments)
            return None
        df = streaming_collect(lf)
        getattr(df, callable_name)(resolved_path, **arguments)
        return df.height
    except (ImportError, ModuleNotFoundError) as exc:
        raise PolarsIoConfigError(
            f"Writing format {fmt.name!r} needs an engine package this haute install "
            f"lacks ({exc}). Candidates: {list(fmt.write_engines)}."
        ) from exc


def anchor_config_source_path(config: Mapping[str, Any], base_dir: Any = None) -> dict[str, Any]:
    """Return *config* with a relative path-kind source anchored to *base_dir*.

    Mirrors the pipeline-dir anchoring the other source node types apply: a
    relative ``path`` in a saved config resolves against the pipeline dir,
    never the process cwd. Non-path source kinds pass through unchanged.
    """
    from pathlib import Path

    result = dict(config)
    raw = result.get("path")
    if base_dir is not None and isinstance(raw, str) and raw.strip():
        candidate = Path(raw)
        if not candidate.is_absolute():
            result["path"] = str(Path(base_dir) / candidate)
    return result


def default_output_extension(fmt: IoFormat) -> str | None:
    """Advisory extension for a path-kind output target (None for table targets)."""
    if fmt.name in ("delta", "iceberg"):
        return None  # directory/table targets, not single files
    return fmt.extensions[0] if fmt.extensions else None


def _snapshot_build(fmt: IoFormat) -> Literal["bounded", "admitted_eager", "unsupported"]:
    if fmt.source_kind == "inline":
        return "bounded"
    if fmt.source_kind == "database":
        return "bounded"
    return "bounded" if fmt.bounded_read else "admitted_eager"


def registry_capabilities() -> dict[str, Any]:
    """Canonical, ordered I/O editor capabilities derived from the registry."""
    groups: dict[str, dict[str, Any]] = {
        "file": {
            "name": "file",
            "label": "File",
            "input_available": True,
            "output_available": True,
            "cache_modes": ["direct", "snapshot"],
            "input_fields": [{"name": "path", "label": "Path", "kind": "path", "required": True}],
            "output_fields": [{"name": "path", "label": "Path", "kind": "path", "required": True}],
            "formats": [],
        },
        "database": {
            "name": "database",
            "label": "Database",
            "input_available": True,
            "output_available": True,
            "cache_modes": ["snapshot"],
            "input_fields": [
                {
                    "name": "connection",
                    "label": "Connection environment reference",
                    "kind": "connection",
                    "required": False,
                },
                {"name": "uri", "label": "Credential-free URI", "kind": "text", "required": False},
                {"name": "query", "label": "Query", "kind": "query", "required": True},
            ],
            "output_fields": [
                {
                    "name": "connection",
                    "label": "Connection environment reference",
                    "kind": "connection",
                    "required": False,
                },
                {"name": "uri", "label": "Credential-free URI", "kind": "text", "required": False},
                {"name": "table", "label": "Table", "kind": "text", "required": True},
            ],
            "formats": [],
        },
        "lakehouse": {
            "name": "lakehouse",
            "label": "Lakehouse",
            "input_available": True,
            "output_available": True,
            "cache_modes": ["snapshot"],
            "input_fields": [
                {"name": "path", "label": "Table locator", "kind": "path", "required": True}
            ],
            "output_fields": [
                {"name": "path", "label": "Table locator", "kind": "path", "required": True}
            ],
            "formats": [],
        },
        "databricks": {
            "name": "databricks",
            "label": "Databricks",
            "input_available": True,
            "output_available": False,
            "cache_modes": ["snapshot"],
            "input_fields": [
                {
                    "name": "http_path",
                    "label": "SQL warehouse HTTP path",
                    "kind": "text",
                    "required": True,
                },
                {"name": "table", "label": "Table", "kind": "table", "required": True},
                {"name": "query", "label": "SELECT clause", "kind": "query", "required": False},
            ],
            "output_fields": [],
            "formats": [],
        },
        "inline": {
            "name": "inline",
            "label": "Inline",
            "input_available": True,
            "output_available": False,
            "cache_modes": ["snapshot"],
            "input_fields": [
                {"name": "records", "label": "Records", "kind": "records", "required": True}
            ],
            "output_fields": [],
            "formats": [],
        },
    }
    for fmt in FORMATS:
        input_modes = ["scan"] if fmt.scanner else (["read"] if fmt.reader else [])
        output_modes = [
            mode for mode, available in (("sink", fmt.sinker), ("write", fmt.writer)) if available
        ]
        if fmt.source_kind == "database":
            input_modes = []
            output_modes = ["write"] if fmt.writer is not None else []
        input_arguments = {
            mode: sorted(allowed_arguments(fmt, *input_callable_key(fmt, cast(InputMode, mode))))
            for mode in input_modes
        }
        if fmt.source_kind == "database":
            input_arguments = {"snapshot": ["batch_size"]}
        output_arguments = {
            mode: sorted(allowed_arguments(fmt, *output_callable_key(fmt, cast(OutputMode, mode))))
            for mode in output_modes
        }
        input = {
            "modes": input_modes,
            "cache_mode": "direct" if fmt.name == "parquet" else "snapshot",
            "arguments": input_arguments,
            # Database Data Input is acquired by the bounded provider adapter
            # rather than Polars' eager read_database_uri callable. Its engine
            # requirements therefore belong to that adapter, not the Polars
            # format metadata.
            "engines_missing": (
                []
                if fmt.source_kind == "database"
                else (missing_engines(fmt.read_engines) if (fmt.reader or fmt.scanner) else [])
            ),
            "direct_bounded": fmt.bounded_read,
            "needs_schema_when_bounded": fmt.needs_schema_when_bounded,
            "snapshot_build": _snapshot_build(fmt),
            "cached_read": _snapshot_build(fmt) != "unsupported",
        }
        output = None
        if output_modes:
            publication = "transactional" if format_group(fmt) == "lakehouse" else "atomic_file"
            if fmt.source_kind == "database":
                publication = "transactional"
            output = {
                "modes": output_modes,
                "arguments": output_arguments,
                "engines_missing": missing_engines(fmt.write_engines),
                "native_sink": fmt.sinker is not None,
                "eager_writer": fmt.writer is not None,
                "publication": publication,
            }
        groups[format_group(fmt)]["formats"].append(
            {
                "name": fmt.name,
                "label": fmt.label,
                "group": format_group(fmt),
                "extensions": list(fmt.extensions),
                "unstable": fmt.unstable,
                "input": input,
                "output": output,
            }
        )
    return {"schema_version": 1, "groups": list(groups.values())}
