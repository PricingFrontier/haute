"""File I/O utilities: data source reading and external object loading."""

from __future__ import annotations

import csv
import functools
import re as _re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import polars as pl

from haute._execution_context import ExecutionProfile
from haute._hashing import content_hash
from haute._logging import get_logger
from haute._polars_utils import (
    is_bounded_execution_profile,
    normalise_execution_profile,
)
from haute.errors import BoundedMemoryUnsupportedError, SchemaMismatchError

logger = get_logger(component="io")


class SourceFormat(StrEnum):
    """Supported flat-file source formats."""

    CSV = "csv"
    JSON = "json"
    NDJSON = "jsonl"
    PARQUET = "parquet"


_SUPPORTED_SOURCE_SUFFIXES = (".csv", ".json", ".jsonl", ".ndjson", ".parquet")
_COMPRESSION_SUFFIXES = frozenset({".bz2", ".gz", ".xz", ".zst"})


class UnsupportedSourceFormatError(ValueError):
    """A safe unsupported-source diagnostic containing no filesystem path."""

    supported_suffixes = _SUPPORTED_SOURCE_SUFFIXES

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        observed = suffix or "(no extension)"
        super().__init__(
            f"Unsupported file type: {observed}. Supported file types: "
            + ", ".join(self.supported_suffixes)
        )


def _observed_source_suffix(path: str) -> str:
    suffixes = [suffix.lower() for suffix in Path(path).suffixes]
    if len(suffixes) >= 2 and suffixes[-1] in _COMPRESSION_SUFFIXES:
        return "".join(suffixes[-2:])
    return suffixes[-1] if suffixes else ""


_POLARS_DTYPE_ALIASES: Mapping[str, Any] = {
    "bool": pl.Boolean,
    "boolean": pl.Boolean,
    "date": pl.Date,
    "datetime": pl.Datetime,
    "float32": pl.Float32,
    "float64": pl.Float64,
    "int8": pl.Int8,
    "int16": pl.Int16,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "string": pl.String,
    "str": pl.String,
    "uint8": pl.UInt8,
    "uint16": pl.UInt16,
    "uint32": pl.UInt32,
    "uint64": pl.UInt64,
    "utf8": pl.String,
}


def _normalise_columns(columns: Iterable[str] | None) -> tuple[str, ...] | None:
    if columns is None:
        return None
    if isinstance(columns, str | bytes):
        raise ValueError("source projection columns must be an iterable of column names")

    ordered: list[str] = []
    seen: set[str] = set()
    for column in columns:
        if not isinstance(column, str) or not column:
            raise ValueError("source projection columns must contain non-empty string names")
        if column not in seen:
            ordered.append(column)
            seen.add(column)
    return tuple(ordered)


def _normalise_dtype(value: Any, *, column: str) -> Any:
    if not isinstance(value, str):
        return value

    dtype_name = value.strip()
    key = dtype_name.lower()
    if key in _POLARS_DTYPE_ALIASES:
        return _POLARS_DTYPE_ALIASES[key]

    dtype = getattr(pl, dtype_name, None)
    if dtype is not None:
        return dtype

    raise SchemaMismatchError(
        "Unsupported declared source dtype.",
        column=column,
        dtype=value,
    )


def _normalise_schema_overrides(
    schema_overrides: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not schema_overrides:
        return None

    normalised: dict[str, Any] = {}
    for column, dtype in schema_overrides.items():
        if not isinstance(column, str) or not column:
            raise SchemaMismatchError("Source schema columns must be non-empty strings.")
        normalised[column] = _normalise_dtype(dtype, column=column)
    return normalised


def _schema_overrides_from_config(config: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = (
        config.get("schema_overrides")
        or config.get("dtypes")
        or config.get("column_dtypes")
        or config.get("schema")
    )
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SchemaMismatchError("Source schema declaration must be a mapping.")
    return _normalise_schema_overrides(raw)


def _validate_source_path(path: str | Path) -> str:
    path_string = str(path)
    if _re.match(r"[a-zA-Z][a-zA-Z0-9+\-.]*://", path_string):
        raise ValueError(f"Path '{path_string}' looks like a URL and is not allowed")
    if ".." in Path(path_string).parts:
        raise ValueError(f"Path '{path_string}' contains '..' and is not allowed")
    return path_string


def _source_format(path: str) -> SourceFormat:
    lower = path.lower()
    if lower.endswith(".csv"):
        return SourceFormat.CSV
    if lower.endswith(".json"):
        return SourceFormat.JSON
    if lower.endswith((".jsonl", ".ndjson")):
        return SourceFormat.NDJSON
    if lower.endswith(".parquet"):
        return SourceFormat.PARQUET

    suffix = _observed_source_suffix(path)
    logger.error("unsupported_file_type", path=path, suffix=suffix)
    raise UnsupportedSourceFormatError(suffix)


def _select_columns(
    lf: pl.LazyFrame,
    columns: tuple[str, ...] | None,
    *,
    validate_columns: tuple[str, ...] | None = None,
) -> pl.LazyFrame:
    schema_columns = lf.collect_schema().names()
    requested = set(columns or ())
    validation_requested = set(validate_columns or ())
    validation_missing = validation_requested - set(schema_columns)
    if validation_missing:
        raise SchemaMismatchError(
            "Source selected_columns references columns missing from the source schema.",
            missing=sorted(validation_missing),
            available=schema_columns,
        )
    if columns is None:
        return lf
    missing = requested - set(schema_columns)
    if missing:
        raise SchemaMismatchError(
            "Source projection references columns missing from the source schema.",
            missing=sorted(missing),
            available=schema_columns,
        )
    return lf.select([column for column in schema_columns if column in requested])


def _csv_header_columns(path: str) -> list[str]:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        try:
            header = next(csv.reader(f))
        except StopIteration as exc:
            raise SchemaMismatchError(
                "CSV source is empty and has no header row.",
                path=path,
            ) from exc

    if not header:
        raise SchemaMismatchError("CSV source has no header columns.", path=path)

    seen: set[str] = set()
    duplicates: set[str] = set()
    for column in header:
        if column in seen:
            duplicates.add(column)
        seen.add(column)
    if duplicates:
        raise SchemaMismatchError(
            "CSV source header contains duplicate columns.",
            path=path,
            duplicates=sorted(duplicates),
        )

    return header


def _is_bounded_csv_profile(profile: ExecutionProfile | None) -> bool:
    return is_bounded_execution_profile(profile)


def _validate_csv_declared_schema_for_profile(
    *,
    path: str,
    profile: ExecutionProfile | None,
    schema_overrides: Mapping[str, Any] | None,
    columns: tuple[str, ...] | None,
    validate_columns: tuple[str, ...] | None,
) -> list[str] | None:
    if not schema_overrides and not _is_bounded_csv_profile(profile):
        return None

    header = _csv_header_columns(path)
    header_set = set(header)

    if schema_overrides:
        missing_declared = sorted(set(schema_overrides) - header_set)
        if missing_declared:
            raise SchemaMismatchError(
                "Declared source schema mismatch.",
                path=path,
                missing=missing_declared,
                mismatches=[],
            )

    if columns:
        missing_projection = sorted(set(columns) - header_set)
        if missing_projection:
            raise SchemaMismatchError(
                "Source projection references columns missing from the source schema.",
                missing=missing_projection,
                available=header,
            )
    if validate_columns:
        missing_validation = sorted(set(validate_columns) - header_set)
        if missing_validation:
            raise SchemaMismatchError(
                "Source selected_columns references columns missing from the source schema.",
                missing=missing_validation,
                available=header,
            )

    if _is_bounded_csv_profile(profile):
        required = list(columns) if columns is not None else header
        missing_required = sorted(
            set(required) - set(schema_overrides or {}),
        )
        if missing_required:
            raise BoundedMemoryUnsupportedError(
                "CSV sources require declared dtypes for bounded-memory execution "
                "profiles. Add schema_overrides, dtypes, column_dtypes, or schema "
                "for every CSV column read by this execution.",
                path=path,
                profile=profile.value if profile is not None else None,
                missing_schema_columns=missing_required,
            )

    return header


def _validate_declared_schema(
    lf: pl.LazyFrame,
    schema_overrides: Mapping[str, Any] | None,
    *,
    path: str,
) -> None:
    if not schema_overrides:
        return

    schema = lf.collect_schema()
    schema_columns = set(schema.names())
    missing = sorted(set(schema_overrides) - schema_columns)
    mismatches: list[dict[str, str]] = []
    for column, declared_dtype in schema_overrides.items():
        if column not in schema_columns:
            continue
        actual_dtype = schema[column]
        if actual_dtype != declared_dtype:
            mismatches.append(
                {
                    "column": column,
                    "declared": str(declared_dtype),
                    "actual": str(actual_dtype),
                }
            )

    if missing or mismatches:
        raise SchemaMismatchError(
            "Declared source schema mismatch.",
            path=path,
            missing=missing,
            mismatches=mismatches,
        )


def _validate_declared_columns_exist(
    lf: pl.LazyFrame,
    schema_overrides: Mapping[str, Any] | None,
    *,
    path: str,
) -> None:
    if not schema_overrides:
        return

    schema_columns = set(lf.collect_schema().names())
    missing = sorted(set(schema_overrides) - schema_columns)
    if missing:
        raise SchemaMismatchError(
            "Declared source schema mismatch.",
            path=path,
            missing=missing,
            mismatches=[],
        )


@dataclass(frozen=True, slots=True)
class DataSourceAdapter:
    """Normalised reader for a configured data-source boundary."""

    source_type: str
    location: str
    _reader: Callable[
        [ExecutionProfile | str | None, tuple[str, ...] | None, tuple[str, ...] | None],
        pl.LazyFrame,
    ]

    def read(
        self,
        *,
        profile: ExecutionProfile | str | None = None,
        columns: Iterable[str] | None = None,
        validate_columns: Iterable[str] | None = None,
    ) -> pl.LazyFrame:
        """Read the configured source as a Polars LazyFrame."""
        return self._reader(
            profile,
            _normalise_columns(columns),
            _normalise_columns(validate_columns),
        )


def _required_config_string(
    config: Mapping[str, Any],
    key: str,
    *,
    source_type: str,
) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"data source type {source_type!r} requires non-empty {key!r}")
    return value


def build_data_source_adapter(config: Mapping[str, Any]) -> DataSourceAdapter:
    """Build a strict data-source adaptor from a node config mapping.

    This is the shared source I/O boundary for runtime callers that need to
    turn a data-source config into a LazyFrame reader without duplicating
    ``sourceType`` branching. It validates source-specific required fields
    up front and rejects unknown source types.
    """
    raw_source_type = config.get("sourceType", "flat_file")
    if not isinstance(raw_source_type, str) or not raw_source_type.strip():
        raise ValueError("data source config requires non-empty 'sourceType'")
    source_type = raw_source_type

    if source_type == "flat_file":
        path = _required_config_string(config, "path", source_type=source_type)
        schema_overrides = _schema_overrides_from_config(config)

        def _read_flat_file(
            _profile: ExecutionProfile | str | None,
            _columns: tuple[str, ...] | None,
            _validate_columns: tuple[str, ...] | None,
            _path: str = path,
            _schema_overrides: Mapping[str, Any] | None = schema_overrides,
        ) -> pl.LazyFrame:
            return read_source(
                _path,
                profile=_profile,
                columns=_columns,
                validate_columns=_validate_columns,
                schema_overrides=_schema_overrides,
            )

        return DataSourceAdapter(source_type=source_type, location=path, _reader=_read_flat_file)

    raise ValueError(f"Unsupported API Input source type: {source_type!r}. Supported: flat_file")


def read_data_source(
    config: Mapping[str, Any],
    *,
    profile: ExecutionProfile | str | None = None,
    columns: Iterable[str] | None = None,
    validate_columns: Iterable[str] | None = None,
) -> pl.LazyFrame:
    """Read a configured data source through the shared adaptor boundary."""
    return build_data_source_adapter(config).read(
        profile=profile,
        columns=columns,
        validate_columns=validate_columns,
    )


def read_user_text(path: str | Path) -> str:
    """Read a user-supplied text file, tolerating non-UTF-8 bytes.

    User files may contain Windows-1252 or other non-UTF-8 bytes. Invalid
    bytes are replaced instead of raising ``UnicodeDecodeError``.
    """
    return Path(path).read_text(encoding="utf-8", errors="replace")


_OBJECT_CACHE_MAX_SIZE = 32


def read_source(
    path: str | Path,
    *,
    profile: ExecutionProfile | str | None = None,
    columns: Iterable[str] | None = None,
    validate_columns: Iterable[str] | None = None,
    schema_overrides: Mapping[str, Any] | None = None,
) -> pl.LazyFrame:
    """Read a data file into a LazyFrame, dispatching on file extension.

    Dispatch table and laziness guarantees:

    * ``.csv`` maps to ``pl.scan_csv``.
    * ``.jsonl`` maps to ``pl.scan_ndjson``.
    * ``.parquet`` maps to ``pl.scan_parquet``.
    * ``.json`` maps to eager ``pl.read_json(...).lazy()``.

    Plain JSON has no Polars lazy scan path. Bounded-memory execution profiles
    therefore reject it before eager parsing begins. Prefer NDJSON or Parquet
    for large files where projection and row-limit pushdown matter.

    Raises:
        ValueError: If the file extension is not supported.
        BoundedMemoryUnsupportedError: If plain JSON is used in a bounded profile.
    """
    path_string = _validate_source_path(path)
    normalised_profile = normalise_execution_profile(profile)
    projection_columns = _normalise_columns(columns)
    validation_columns = _normalise_columns(validate_columns)
    source_schema_overrides = _normalise_schema_overrides(schema_overrides)
    fmt = _source_format(path_string)

    if fmt == SourceFormat.CSV:
        _validate_csv_declared_schema_for_profile(
            path=path_string,
            profile=normalised_profile,
            schema_overrides=source_schema_overrides,
            columns=projection_columns,
            validate_columns=validation_columns,
        )
        if source_schema_overrides is None:
            lf = pl.scan_csv(path_string)
        else:
            scan_kwargs: dict[str, Any] = {"schema_overrides": source_schema_overrides}
            if _is_bounded_csv_profile(normalised_profile):
                scan_kwargs["infer_schema"] = False
            lf = pl.scan_csv(path_string, **scan_kwargs)
            _validate_declared_schema(lf, source_schema_overrides, path=path_string)
        return _select_columns(lf, projection_columns, validate_columns=validation_columns)

    if fmt == SourceFormat.JSON:
        if normalised_profile is not None and is_bounded_execution_profile(normalised_profile):
            raise BoundedMemoryUnsupportedError(
                "Plain JSON sources require eager parsing and are not supported "
                "for bounded-memory execution profiles. Cache the JSON as parquet "
                "or use NDJSON.",
                path=path_string,
                profile=normalised_profile.value,
            )
        lf = pl.read_json(path_string).lazy()
        _validate_declared_schema(lf, source_schema_overrides, path=path_string)
        return _select_columns(lf, projection_columns, validate_columns=validation_columns)

    if fmt == SourceFormat.NDJSON:
        if source_schema_overrides is None:
            lf = pl.scan_ndjson(path_string)
        else:
            _validate_declared_columns_exist(
                pl.scan_ndjson(path_string),
                source_schema_overrides,
                path=path_string,
            )
            lf = pl.scan_ndjson(path_string, schema_overrides=source_schema_overrides)
            _validate_declared_schema(lf, source_schema_overrides, path=path_string)
        return _select_columns(lf, projection_columns, validate_columns=validation_columns)

    lf = pl.scan_parquet(path_string)
    _validate_declared_schema(lf, source_schema_overrides, path=path_string)
    return _select_columns(lf, projection_columns, validate_columns=validation_columns)


@functools.lru_cache(maxsize=_OBJECT_CACHE_MAX_SIZE)
def _load_cached(
    path: str,
    digest: str,  # noqa: ARG001 - part of cache key, not used in body.
    file_type: str,
    model_class: str,
) -> object:
    """Memoised loader keyed on ``(path, digest, file_type, model_class)``."""
    return _load_external_object_uncached(path, file_type, model_class)


def load_external_object(path: str, file_type: str, model_class: str = "classifier") -> object:
    """Load an external file (model, JSON, pickle, joblib) and return the object.

    Results are cached by ``(path, content_hash, file_type, model_class)`` so
    repeated calls skip disk parse/deserialisation cost. Pickle files are
    deserialized with a restricted unpickler.
    """
    from haute._sandbox import validate_project_path

    validate_project_path(path)

    digest = content_hash(Path(path))
    return _load_cached(path, digest, file_type, model_class)


def _load_external_object_uncached(
    path: str,
    file_type: str,
    model_class: str,
) -> object:
    """Deserialize an external file from disk without caching."""
    if file_type == "json":
        import json as _json

        with open(path, encoding="utf-8", errors="replace") as f:
            return _json.load(f)
    if file_type == "joblib":
        from haute._sandbox import safe_joblib_load

        return safe_joblib_load(path)
    if file_type == "catboost":
        from haute._mlflow_io import _load_catboost_model

        class_to_task = {"regressor": "regression", "classifier": "classification"}
        task = class_to_task.get(model_class, "regression")
        return _load_catboost_model(path, task)
    if file_type == "pickle":
        from haute._sandbox import safe_unpickle

        return safe_unpickle(path)
    raise ValueError(f"Unsupported file_type: {file_type!r}")
