"""File I/O utilities — data source reading and external object loading."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from haute._hashing import content_hash
from haute._logging import get_logger
from haute._lru_cache import LRUCache

logger = get_logger(component="io")


def read_user_text(path: str | Path) -> str:
    """Read a user-supplied text file, tolerating non-UTF-8 bytes.

    User files may contain Windows-1252 or other legacy-encoded bytes
    (e.g. en-dash ``0x96`` pasted from Excel).  ``errors="replace"``
    substitutes invalid bytes with U+FFFD rather than raising
    ``UnicodeDecodeError``.
    """
    return Path(path).read_text(encoding="utf-8", errors="replace")


_OBJECT_CACHE_MAX_SIZE = 32


def read_source(path: str) -> pl.LazyFrame:
    """Read a data file into a LazyFrame, dispatching on file extension.

    Centralises the csv/json/parquet dispatch that was previously duplicated
    across the executor, scorer, schema inference, and server modules.

    Dispatch table and laziness guarantees
    --------------------------------------
    * ``.csv``      → ``pl.scan_csv``      (lazy; ``.head(n)`` push-down)
    * ``.jsonl``    → ``pl.scan_ndjson``   (lazy; ``.head(n)`` push-down)
    * ``.parquet``  → ``pl.scan_parquet``  (lazy; ``.head(n)`` push-down)
    * ``.json``     → ``pl.read_json``     (**eager** — see below)

    Eager-read limitation for plain ``.json``
    -----------------------------------------
    Polars exposes no ``scan_json`` for plain (object-per-file) JSON, so
    ``.json`` files are read **eagerly** via ``pl.read_json(path).lazy()``:
    the entire file is parsed into memory before the wrapped ``LazyFrame``
    is returned.  A downstream ``.head(n)`` **cannot** reduce I/O cost on
    this path — the full file has already been materialised.  For large
    JSON blobs this is an O(file-size) memory spike.

    Escape hatch for large JSON: :func:`haute._json_flatten.read_json_flat`.
    That function flattens the JSON once, caches the result as parquet, and
    returns ``pl.scan_parquet(cache_path)`` — a truly lazy ``LazyFrame``
    with row-limit push-down.  Prefer ``read_json_flat`` over ``read_source``
    whenever the JSON file is too large to comfortably fit in memory, or
    whenever ``.head(n)`` / filter push-down matters.

    NDJSON (``.jsonl``) is the "safe" JSON format for this module:
    ``scan_ndjson`` is lazy, so ``.head(n)`` actually reduces read cost.

    Raises:
        ValueError: If the file extension is not supported.
    """
    import re as _re
    from pathlib import Path as _Path

    if _re.match(r"[a-zA-Z][a-zA-Z0-9+\-.]*://", path):
        raise ValueError(f"Path '{path}' looks like a URL and is not allowed")

    if ".." in _Path(path).parts:
        raise ValueError(f"Path '{path}' contains '..' and is not allowed")

    lower = path.lower()
    if lower.endswith(".csv"):
        return pl.scan_csv(path)
    if lower.endswith(".json"):
        # No scan_json in Polars — read eagerly.  Callers should prefer
        # the JSON flatten/cache path (read_json_flat) for large files.
        return pl.read_json(path).lazy()
    if lower.endswith(".jsonl"):
        return pl.scan_ndjson(path)
    if lower.endswith(".parquet"):
        return pl.scan_parquet(path)
    suffix = path.rsplit(".", 1)[-1] if "." in path else ""
    logger.error("unsupported_file_type", path=path, suffix=suffix)
    raise ValueError(f"Unsupported file type: .{suffix}")


_object_cache: LRUCache[tuple[str, str, str, str], object] = LRUCache(
    max_size=_OBJECT_CACHE_MAX_SIZE,
)


def load_external_object(path: str, file_type: str, model_class: str = "classifier") -> object:
    """Load an external file (model, JSON, pickle, joblib) and return the object.

    Shared by the development executor and the deploy scoring engine.

    Results are cached by ``(path, content_hash, file_type, model_class)``
    so repeated calls (preview clicks, API scoring requests) skip disk
    parse/deserialisation cost.  Keying on the xxh64 content hash closes
    the TOCTOU hole where a same-second overwrite keeps ``mtime``
    unchanged and the cache would otherwise serve stale content.
    Bounded to ``_OBJECT_CACHE_MAX_SIZE`` entries (LRU eviction).

    All paths are validated to be within the project root before loading.
    Pickle files are deserialized with a restricted unpickler that only
    allows known-safe classes.
    """
    from haute._sandbox import validate_project_path

    validate_project_path(path)

    digest = content_hash(Path(path))
    key = (path, digest, file_type, model_class)

    cached = _object_cache.get(key)
    if cached is not None:
        return cached

    obj = _load_external_object_uncached(path, file_type, model_class)
    _object_cache.put(key, obj)
    return obj


def _load_external_object_uncached(
    path: str,
    file_type: str,
    model_class: str,
) -> object:
    """Deserialize an external file from disk (no caching)."""
    if file_type == "json":
        import json as _json

        with open(path, encoding="utf-8", errors="replace") as f:
            return _json.load(f)
    elif file_type == "joblib":
        from haute._sandbox import safe_joblib_load

        return safe_joblib_load(path)
    elif file_type == "catboost":
        from haute._mlflow_io import _load_catboost_model

        class_to_task = {"regressor": "regression", "classifier": "classification"}
        task = class_to_task.get(model_class, "regression")
        return _load_catboost_model(path, task)
    elif file_type == "pickle":
        from haute._sandbox import safe_unpickle

        return safe_unpickle(path)
    else:
        raise ValueError(f"Unsupported file_type: {file_type!r}")
