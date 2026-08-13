"""Durable, project-local cache generations for Explore datasets."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from haute._cache import canonical_json
from haute._dataframe_execution_cache import (
    DataFrameExecutionCacheEntry,
    DataFrameExecutionCacheRequest,
)
from haute._file_ops import atomic_write_text
from haute._hashing import content_hash_bytes
from haute._polars_utils import read_parquet_metadata
from haute.schemas import ExploreCacheReport

EXPLORE_PERSISTENT_CACHE_SCHEMA_VERSION = 1


class ExplorePersistentCacheError(RuntimeError):
    """Base class for durable Explore cache failures."""


class ExplorePersistentCacheCorruptError(ExplorePersistentCacheError):
    """Raised when a selected durable Explore generation is inconsistent."""


@dataclass(frozen=True, slots=True)
class ExplorePersistentCacheSnapshot:
    """A strictly validated selected generation for one Explore family."""

    state: Literal["current", "stale"]
    report_cache_key: str
    report: ExploreCacheReport | None
    data_path: Path
    artifact_metadata: dict[str, Any]


_COORDINATION_GUARD = threading.Lock()
_FAMILY_LOCKS: dict[tuple[Path, str], threading.RLock] = {}


def _positive_or_zero_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _generation_id(value: object) -> str:
    text = _non_empty_string(value, field="generation_id")
    parsed = uuid.UUID(text)
    if str(parsed) != text:
        raise ValueError("generation_id must be a canonical UUID")
    return text


class ExplorePersistentCacheStore:
    """Own the latest immutable Explore generation below a project root."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.cache_root = self.project_root / ".haute_cache" / "explore"

    @staticmethod
    def family_digest(family_key: tuple[str, str, str, str]) -> str:
        payload = {
            "kind": family_key[0],
            "source_file": family_key[1],
            "node_id": family_key[2],
            "source": family_key[3],
        }
        return content_hash_bytes(canonical_json(payload).encode())

    def _family_dir(self, family_key: tuple[str, str, str, str]) -> Path:
        return self.cache_root / self.family_digest(family_key)

    def _family_lock(self, family_key: tuple[str, str, str, str]) -> threading.RLock:
        digest = self.family_digest(family_key)
        coordination_key = (self.cache_root.resolve(), digest)
        with _COORDINATION_GUARD:
            return _FAMILY_LOCKS.setdefault(coordination_key, threading.RLock())

    def _read_generation(
        self,
        family_key: tuple[str, str, str, str],
        *,
        expected_report_cache_key: str,
    ) -> tuple[str, ExploreCacheReport | None, Path, dict[str, Any]] | None:
        family_dir = self._family_dir(family_key)
        pointer_path = family_dir / "current.json"
        if not pointer_path.exists():
            return None

        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            if not isinstance(pointer, dict):
                raise ValueError("current pointer must be an object")
            expected_digest = self.family_digest(family_key)
            if pointer.get("family_digest") != expected_digest:
                raise ValueError("current pointer family does not match")
            generation_id = _generation_id(pointer.get("generation_id"))

            generation_dir = family_dir / "generations" / generation_id
            metadata_path = generation_dir / "meta.json"
            data_path = generation_dir / "data.parquet"
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("generation metadata must be an object")
            if raw.get("schema_version") != EXPLORE_PERSISTENT_CACHE_SCHEMA_VERSION:
                raise ValueError("unsupported durable Explore cache schema")
            if raw.get("family_digest") != expected_digest:
                raise ValueError("generation family digest does not match")
            if raw.get("family") != list(family_key):
                raise ValueError("generation family does not match")
            if raw.get("generation_id") != generation_id:
                raise ValueError("generation id does not match")

            report_cache_key = _non_empty_string(
                raw.get("report_cache_key"),
                field="report_cache_key",
            )
            dataframe_cache_key = _non_empty_string(
                raw.get("dataframe_cache_key"),
                field="dataframe_cache_key",
            )
            report: ExploreCacheReport | None = None
            if report_cache_key == expected_report_cache_key:
                report = ExploreCacheReport.model_validate(raw.get("report"))
                if (
                    report.dataframe_cache_key != dataframe_cache_key
                    or report.node_id != family_key[2]
                    or report.source != family_key[3]
                ):
                    raise ValueError("generation report identity does not match")

            artifact = raw.get("artifact")
            if not isinstance(artifact, dict):
                raise ValueError("artifact metadata must be an object")
            stored_columns = artifact.get("columns")
            if not isinstance(stored_columns, dict) or not all(
                isinstance(name, str) and isinstance(dtype, str)
                for name, dtype in stored_columns.items()
            ):
                raise ValueError("artifact columns must be a string mapping")
            expected_artifact = {
                "row_count": _positive_or_zero_int(
                    artifact.get("row_count"), field="artifact.row_count"
                ),
                "column_count": _positive_or_zero_int(
                    artifact.get("column_count"), field="artifact.column_count"
                ),
                "columns": dict(stored_columns),
                "size_bytes": _positive_or_zero_int(
                    artifact.get("size_bytes"), field="artifact.size_bytes"
                ),
                "uncompressed_size_bytes": _positive_or_zero_int(
                    artifact.get("uncompressed_size_bytes"),
                    field="artifact.uncompressed_size_bytes",
                ),
            }
            observed = read_parquet_metadata(data_path)
            self._validate_artifact(observed, expected_artifact)
            if report is not None and (
                report.row_count != expected_artifact["row_count"]
                or report.column_count != expected_artifact["column_count"]
            ):
                raise ValueError("report shape does not match durable dataframe")
        except FileNotFoundError as exc:
            raise ExplorePersistentCacheCorruptError(
                "Selected Explore cache generation is incomplete"
            ) from exc
        except OSError:
            raise
        except Exception as exc:
            if isinstance(exc, ExplorePersistentCacheCorruptError):
                raise
            raise ExplorePersistentCacheCorruptError(
                "Selected Explore cache generation is corrupt"
            ) from exc

        return report_cache_key, report, data_path, expected_artifact

    @staticmethod
    def _validate_artifact(observed: dict[str, Any], expected: dict[str, Any]) -> None:
        for field in (
            "row_count",
            "column_count",
            "columns",
            "size_bytes",
            "uncompressed_size_bytes",
        ):
            if observed.get(field) != expected[field]:
                raise ValueError(f"durable Explore artifact {field} does not match metadata")

    def lookup(
        self,
        family_key: tuple[str, str, str, str],
        *,
        report_cache_key: str,
    ) -> ExplorePersistentCacheSnapshot | None:
        """Return the selected generation and classify it against the exact key."""

        with self._family_lock(family_key):
            selected = self._read_generation(
                family_key,
                expected_report_cache_key=report_cache_key,
            )
            if selected is None:
                return None
            stored_key, report, data_path, artifact = selected
            return ExplorePersistentCacheSnapshot(
                state="current" if stored_key == report_cache_key else "stale",
                report_cache_key=stored_key,
                report=report,
                data_path=data_path,
                artifact_metadata=artifact,
            )

    def restore(
        self,
        snapshot: ExplorePersistentCacheSnapshot,
        request: DataFrameExecutionCacheRequest,
        *,
        node_id: str,
    ) -> DataFrameExecutionCacheEntry:
        """Copy a current durable generation into the process-owned dataframe cache."""

        if snapshot.state != "current" or snapshot.report is None:
            raise ValueError("only a current Explore generation can be restored")
        key = request.keys_by_node[node_id]
        if key.cache_key != snapshot.report.dataframe_cache_key:
            raise ExplorePersistentCacheCorruptError(
                "Durable Explore report does not match the requested dataframe cache key"
            )

        cache = request.cache
        with cache.materialization_lock(key):
            existing = cache.get(key)
            if existing is not None:
                return existing
            target = cache.path_for_key(key)
            try:
                shutil.copy2(snapshot.data_path, target)
                observed = read_parquet_metadata(target)
                self._validate_artifact(observed, snapshot.artifact_metadata)
                return cache.store_artifact(key, target, observed)
            except BaseException:
                target.unlink(missing_ok=True)
                raise

    def publish(
        self,
        family_key: tuple[str, str, str, str],
        *,
        report_cache_key: str,
        report: ExploreCacheReport,
        entry: DataFrameExecutionCacheEntry,
    ) -> None:
        """Atomically select a fully written replacement generation."""

        _non_empty_string(report_cache_key, field="report_cache_key")
        if (
            family_key[0] != "explore"
            or report.node_id != family_key[2]
            or report.source != family_key[3]
            or report.dataframe_cache_key != entry.key.cache_key
            or report.row_count != entry.row_count
            or report.column_count != entry.column_count
        ):
            raise ValueError("Explore report, family, and dataframe cache entry do not match")

        lock = self._family_lock(family_key)
        with lock:
            family_dir = self._family_dir(family_key)
            generations_dir = family_dir / "generations"
            generations_dir.mkdir(parents=True, exist_ok=True)
            generation_id = str(uuid.uuid4())
            staging = family_dir / f".staging-{generation_id}"
            final_dir = generations_dir / generation_id
            selected = False
            try:
                staging.mkdir()
                staged_data = staging / "data.parquet"
                shutil.copy2(entry.path, staged_data)
                observed = read_parquet_metadata(staged_data)
                expected = {
                    "row_count": entry.row_count,
                    "column_count": entry.column_count,
                    "columns": dict(entry.columns),
                    "size_bytes": entry.size_bytes,
                    "uncompressed_size_bytes": entry.uncompressed_size_bytes,
                }
                self._validate_artifact(observed, expected)
                metadata = {
                    "schema_version": EXPLORE_PERSISTENT_CACHE_SCHEMA_VERSION,
                    "family_digest": self.family_digest(family_key),
                    "family": list(family_key),
                    "generation_id": generation_id,
                    "report_cache_key": report_cache_key,
                    "dataframe_cache_key": entry.key.cache_key,
                    "report": report.model_dump(mode="json"),
                    "artifact": expected,
                }
                atomic_write_text(staging / "meta.json", canonical_json(metadata))
                staging.replace(final_dir)
                atomic_write_text(
                    family_dir / "current.json",
                    canonical_json(
                        {
                            "family_digest": self.family_digest(family_key),
                            "generation_id": generation_id,
                        }
                    ),
                )
                selected = True
                for candidate in generations_dir.iterdir():
                    if candidate.is_dir() and candidate.name != generation_id:
                        shutil.rmtree(candidate)
            except BaseException:
                if staging.exists():
                    shutil.rmtree(staging)
                if final_dir.exists() and not selected:
                    shutil.rmtree(final_dir)
                raise
