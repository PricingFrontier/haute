"""Bounded database snapshot acquisition for Data Input.

The initial connector surface is deliberately small and auditable: SQLite is
implemented with the standard-library DB-API cursor and ``fetchmany``. Other
schemes fail before a connection is opened instead of falling back to Polars'
eager ``read_database_uri`` path.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import pyarrow as pa

if TYPE_CHECKING:
    from haute._source_cache import SourceCacheBuildContext


class DatabaseConfigError(ValueError):
    """A database source cannot be resolved safely."""


_ENV_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_READ_ONLY_PREFIX_RE = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(?:ALTER|ATTACH|CREATE|DELETE|DETACH|DROP|EXEC|EXECUTE|GRANT|INSERT|"
    r"PRAGMA|REPLACE|REVOKE|TRUNCATE|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)


def validate_read_query(query: str) -> str:
    """Return a stripped, read-only single statement or raise."""
    stripped = query.strip()
    if not stripped or not _READ_ONLY_PREFIX_RE.match(stripped):
        raise DatabaseConfigError("Database query must start with SELECT or WITH.")
    if ";" in stripped:
        raise DatabaseConfigError("Database query must not contain semicolons.")
    if "--" in stripped or "/*" in stripped:
        raise DatabaseConfigError("Database query must not contain SQL comments.")
    forbidden = _FORBIDDEN_SQL_RE.search(stripped)
    if forbidden:
        raise DatabaseConfigError(
            f"Database query contains forbidden keyword {forbidden.group()!r}."
        )
    return stripped


def resolve_connection_uri(config: Mapping[str, Any]) -> str:
    """Resolve a credential URI from a named environment reference or safe raw URI.

    The resolved value is returned only to the connector and must never be
    placed back into config, cache identity, metadata, logs, or API responses.
    """
    connection_ref = config.get("connection")
    raw_uri = config.get("uri")
    if isinstance(connection_ref, str) and connection_ref.strip():
        reference = connection_ref.strip()
        if not _ENV_REF_RE.fullmatch(reference):
            raise DatabaseConfigError("Database connection must be an environment-variable name.")
        value = os.getenv(reference)
        if not value:
            raise DatabaseConfigError(
                f"Database connection environment reference {reference!r} is not set."
            )
        return value
    if isinstance(raw_uri, str) and raw_uri.strip():
        return raw_uri.strip()
    raise DatabaseConfigError(
        "Database source requires exactly one of 'connection' or credential-free 'uri'."
    )


def resolve_sqlite_path(
    uri: str,
    *,
    base_dir: str | Path | None = None,
) -> str:
    """Resolve a SQLite URI using SQLAlchemy's relative-path convention."""
    parsed = urlsplit(uri)
    if parsed.scheme != "sqlite":
        raise DatabaseConfigError(
            f"Database snapshot scheme {parsed.scheme or '<missing>'!r} is unsupported; "
            "this build supports bounded SQLite snapshots only."
        )
    if parsed.username is not None or parsed.password is not None:
        raise DatabaseConfigError("Database URI must not contain credentials.")
    if parsed.netloc not in ("", "localhost"):
        raise DatabaseConfigError("SQLite URI must not name a remote host.")

    raw_path = unquote(parsed.path)
    if raw_path in ("/:memory:", ":memory:"):
        return ":memory:"
    if re.match(r"^/[A-Za-z]:/", raw_path):
        raw_path = raw_path[1:]
    if not raw_path:
        raise DatabaseConfigError("SQLite URI requires a database path.")
    # Three slashes denote a relative database path (``sqlite:///data.db``);
    # four slashes, a drive-qualified path, or a UNC path remain absolute.
    if raw_path.startswith("/") and not raw_path.startswith("//"):
        raw_path = raw_path[1:]
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = Path(base_dir).resolve() / candidate if base_dir is not None else candidate
    return str(candidate.resolve())


def validate_sqlite_project_path(
    uri: str,
    *,
    base_dir: str | Path,
    project_root: str | Path,
) -> Path | None:
    """Validate a raw local SQLite locator without constraining named connections."""
    if urlsplit(uri).scheme != "sqlite":
        return None
    database_path = resolve_sqlite_path(uri, base_dir=base_dir)
    if database_path == ":memory:":
        return None

    resolved = Path(database_path).resolve()
    root = Path(project_root).resolve()
    resolved_norm = os.path.normcase(str(resolved))
    root_norm = os.path.normcase(str(root))
    try:
        common = os.path.commonpath([root_norm, resolved_norm])
    except ValueError:
        common = None
    if common != root_norm:
        raise ValueError("SQLite database path resolves outside the project root")
    return resolved


def canonical_database_locator(
    uri: str,
    *,
    base_dir: str | Path | None = None,
) -> str:
    """Return a stable, credential-free locator for cache identity."""
    if urlsplit(uri).scheme != "sqlite":
        return uri
    return f"sqlite-path:{resolve_sqlite_path(uri, base_dir=base_dir)}"


class DatabaseSnapshotBuilder:
    """Stream one consistent SQLite read transaction as Arrow record batches."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> None:
        self._config = dict(config)
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self._query = validate_read_query(str(config.get("query") or ""))
        # Resolve and classify before the cache route creates a background job.
        # This opens no connector and ensures unsupported drivers fail as an
        # admission/capability error instead of an opaque asynchronous failure.
        resolve_sqlite_path(
            resolve_connection_uri(self._config),
            base_dir=self._base_dir,
        )
        raw_batch_size = (config.get("arguments") or {}).get("batch_size", 10_000)
        if isinstance(raw_batch_size, bool) or not isinstance(raw_batch_size, int):
            raise DatabaseConfigError("Database batch_size must be a positive integer.")
        if raw_batch_size <= 0:
            raise DatabaseConfigError("Database batch_size must be a positive integer.")
        self._batch_size = raw_batch_size

    def build(self, context: SourceCacheBuildContext) -> Iterator[pa.RecordBatch]:
        uri = resolve_connection_uri(self._config)
        database_path = resolve_sqlite_path(uri, base_dir=self._base_dir)

        def batches() -> Iterator[pa.RecordBatch]:
            context.checkpoint()
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA query_only = ON")
                connection.execute("BEGIN")
                with closing(connection.execute(self._query)) as cursor:
                    description = cursor.description
                    if description is None:
                        raise DatabaseConfigError("Database query did not return a table.")
                    columns = [str(item[0]) for item in description]
                    yielded = False
                    while True:
                        context.checkpoint()
                        rows = cursor.fetchmany(self._batch_size)
                        if not rows:
                            if not yielded:
                                yield pa.RecordBatch.from_arrays(
                                    [pa.array([], type=pa.null()) for _ in columns],
                                    names=columns,
                                )
                            break
                        yielded = True
                        yield pa.RecordBatch.from_pylist(
                            [dict(zip(columns, row, strict=True)) for row in rows]
                        )

        return batches()
