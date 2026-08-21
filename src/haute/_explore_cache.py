"""Durable, project-local cache generations for Explore datasets."""

from __future__ import annotations

import json
import shutil
import stat as stat_module
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from haute._cache import canonical_json
from haute._dataframe_execution_cache import (
    DataFrameExecutionCacheEntry,
    DataFrameExecutionCacheRequest,
)
from haute._file_ops import atomic_write_text
from haute._hashing import content_hash, content_hash_bytes
from haute._logging import get_logger
from haute._polars_utils import read_parquet_metadata
from haute.schemas import ExploreCacheReport

EXPLORE_PERSISTENT_CACHE_SCHEMA_VERSION = 1
logger = get_logger(component="explore.persistent_cache")

ExploreFamilyKey = tuple[str, str, str, str]


class ExplorePersistentCacheError(RuntimeError):
    """Base class for durable Explore cache failures."""


class ExplorePersistentCacheCorruptError(ExplorePersistentCacheError):
    """Raised when a selected durable Explore generation is inconsistent."""


@dataclass(frozen=True, slots=True)
class ExplorePersistentCacheSnapshot:
    """A strictly validated selected generation for one Explore family."""

    state: Literal["current", "stale"]
    generation_id: str
    report_cache_key: str
    report: ExploreCacheReport | None
    data_path: Path
    artifact_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExplorePersistentCachePublication:
    """One fully staged generation that is not selected until commit."""

    family_key: ExploreFamilyKey
    generation_id: str
    staging_path: Path
    final_path: Path


@dataclass(slots=True)
class _FamilyCoordination:
    lock: threading.RLock = field(default_factory=threading.RLock)
    leases_by_generation: dict[str, int] = field(default_factory=dict)


_COORDINATION_GUARD = threading.Lock()
_FAMILY_COORDINATION: dict[tuple[Path, str], _FamilyCoordination] = {}


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
    def family_digest(family_key: ExploreFamilyKey) -> str:
        payload = {
            "kind": family_key[0],
            "source_file": family_key[1],
            "node_id": family_key[2],
            "source": family_key[3],
        }
        return content_hash_bytes(canonical_json(payload).encode())

    def _family_dir(self, family_key: ExploreFamilyKey) -> Path:
        return self.cache_root / self.family_digest(family_key)

    def _family_coordination(self, family_key: ExploreFamilyKey) -> _FamilyCoordination:
        digest = self.family_digest(family_key)
        coordination_key = (self.cache_root.resolve(), digest)
        with _COORDINATION_GUARD:
            return _FAMILY_COORDINATION.setdefault(coordination_key, _FamilyCoordination())

    def _family_lock(self, family_key: ExploreFamilyKey) -> threading.RLock:
        return self._family_coordination(family_key).lock

    def _read_generation(
        self,
        family_key: ExploreFamilyKey,
        *,
        expected_report_cache_key: str,
    ) -> tuple[str, str, ExploreCacheReport | None, Path, dict[str, Any]] | None:
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
            self._validate_generation_files(generation_dir, metadata_path, data_path)
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
                "digest": _non_empty_string(artifact.get("digest"), field="artifact.digest"),
            }
            observed = read_parquet_metadata(data_path)
            self._validate_artifact(observed, expected_artifact)
            if content_hash(data_path) != expected_artifact["digest"]:
                raise ValueError("durable Explore artifact digest does not match metadata")
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

        return generation_id, report_cache_key, report, data_path, expected_artifact

    @staticmethod
    def _validate_artifact(observed: dict[str, Any], expected: dict[str, Any]) -> None:
        for artifact_field in (
            "row_count",
            "column_count",
            "columns",
            "size_bytes",
            "uncompressed_size_bytes",
        ):
            if observed.get(artifact_field) != expected[artifact_field]:
                raise ValueError(
                    f"durable Explore artifact {artifact_field} does not match metadata"
                )

    @staticmethod
    def _validate_generation_files(
        generation_dir: Path, metadata_path: Path, data_path: Path
    ) -> None:
        """Reject links and non-regular artifacts before trusting a generation."""
        generation_stat = generation_dir.lstat()
        if (
            not stat_module.S_ISDIR(generation_stat.st_mode)
            or stat_module.S_ISLNK(generation_stat.st_mode)
            or bool(getattr(generation_stat, "st_file_attributes", 0) & 0x400)
        ):
            raise ValueError("durable Explore generation contains a non-regular artifact")
        for path in (metadata_path, data_path):
            artifact_stat = path.lstat()
            if (
                not stat_module.S_ISREG(artifact_stat.st_mode)
                or stat_module.S_ISLNK(artifact_stat.st_mode)
                or bool(getattr(artifact_stat, "st_file_attributes", 0) & 0x400)
            ):
                raise ValueError("durable Explore generation contains a non-regular artifact")
            if artifact_stat.st_nlink != 1:
                raise ValueError("durable Explore artifact must not be hard-linked")
        resolved_generation = generation_dir.resolve(strict=True)
        if resolved_generation.parent != generation_dir.parent.resolve(strict=True):
            raise ValueError("durable Explore generation escapes its cache family")
        for path in (metadata_path, data_path):
            resolved = path.resolve(strict=True)
            if resolved.parent != resolved_generation or not resolved.is_file():
                raise ValueError("durable Explore artifact escapes its generation")

    @contextmanager
    def lease(
        self,
        family_key: ExploreFamilyKey,
        *,
        report_cache_key: str,
    ) -> Iterator[ExplorePersistentCacheSnapshot | None]:
        """Pin and yield the selected generation for the full reader operation."""

        coordination = self._family_coordination(family_key)
        snapshot: ExplorePersistentCacheSnapshot | None = None
        with coordination.lock:
            selected = self._read_generation(
                family_key,
                expected_report_cache_key=report_cache_key,
            )
            if selected is not None:
                generation_id, stored_key, report, data_path, artifact = selected
                snapshot = ExplorePersistentCacheSnapshot(
                    state="current" if stored_key == report_cache_key else "stale",
                    generation_id=generation_id,
                    report_cache_key=stored_key,
                    report=report,
                    data_path=data_path,
                    artifact_metadata=artifact,
                )
                coordination.leases_by_generation[generation_id] = (
                    coordination.leases_by_generation.get(generation_id, 0) + 1
                )

        try:
            yield snapshot
        finally:
            if snapshot is not None:
                with coordination.lock:
                    generation_id = snapshot.generation_id
                    lease_count = coordination.leases_by_generation[generation_id]
                    if lease_count == 1:
                        del coordination.leases_by_generation[generation_id]
                        self._retire_generation_if_unselected_locked(family_key, generation_id)
                    else:
                        coordination.leases_by_generation[generation_id] = lease_count - 1

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
                if content_hash(target) != snapshot.artifact_metadata.get("digest"):
                    raise ExplorePersistentCacheCorruptError(
                        "Restored Explore artifact digest does not match its generation"
                    )
                return cache.store_artifact(key, target, observed)
            except BaseException:
                target.unlink(missing_ok=True)
                raise

    def prepare_publication(
        self,
        family_key: ExploreFamilyKey,
        *,
        report_cache_key: str,
        report: ExploreCacheReport,
        entry: DataFrameExecutionCacheEntry,
        generation_id: str,
    ) -> ExplorePersistentCachePublication:
        """Copy and validate a replacement without making it visible to readers."""

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

        family_dir = self._family_dir(family_key)
        generations_dir = family_dir / "generations"
        generations_dir.mkdir(parents=True, exist_ok=True)
        publication = self.new_publication(family_key, generation_id=generation_id)
        staging = publication.staging_path
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
                "digest": content_hash(staged_data),
            }
            self._validate_artifact(observed, expected)
            metadata = {
                "schema_version": EXPLORE_PERSISTENT_CACHE_SCHEMA_VERSION,
                "family_digest": self.family_digest(family_key),
                "family": list(family_key),
                "generation_id": publication.generation_id,
                "report_cache_key": report_cache_key,
                "dataframe_cache_key": entry.key.cache_key,
                "report": report.model_dump(mode="json"),
                "artifact": expected,
            }
            atomic_write_text(staging / "meta.json", canonical_json(metadata))
        except BaseException:
            self._retire_directory_best_effort(staging)
            raise
        return publication

    def new_publication(
        self,
        family_key: ExploreFamilyKey,
        *,
        generation_id: str | None = None,
    ) -> ExplorePersistentCachePublication:
        """Allocate the exact parent-owned paths for one private generation."""
        if family_key[0] != "explore" or not all(
            isinstance(item, str) and item for item in family_key
        ):
            raise ValueError("invalid Explore cache family")
        canonical_id = (
            _generation_id(generation_id) if generation_id is not None else str(uuid.uuid4())
        )
        family_dir = self._family_dir(family_key)
        return ExplorePersistentCachePublication(
            family_key=family_key,
            generation_id=canonical_id,
            staging_path=family_dir / f".staging-{canonical_id}",
            final_path=family_dir / "generations" / canonical_id,
        )

    def validate_publication(
        self,
        publication: ExplorePersistentCachePublication,
        *,
        report_cache_key: str,
        report: ExploreCacheReport,
    ) -> dict[str, Any]:
        """Validate a staged child result before it can move the current pointer."""
        self._validate_publication_paths(publication)
        return self._validate_generation_at(
            publication.family_key,
            publication.generation_id,
            publication.staging_path,
            report_cache_key=report_cache_key,
            report=report,
        )

    def restore_publication(
        self,
        publication: ExplorePersistentCachePublication,
        request: DataFrameExecutionCacheRequest,
        *,
        node_id: str,
        report_cache_key: str,
        report: ExploreCacheReport,
    ) -> DataFrameExecutionCacheEntry:
        """Restore one validated, still-unselected generation into the parent cache."""

        artifact = self.validate_publication(
            publication,
            report_cache_key=report_cache_key,
            report=report,
        )
        return self.restore(
            ExplorePersistentCacheSnapshot(
                state="current",
                generation_id=publication.generation_id,
                report_cache_key=report_cache_key,
                report=report,
                data_path=publication.staging_path / "data.parquet",
                artifact_metadata=artifact,
            ),
            request,
            node_id=node_id,
        )

    def commit_publication(self, publication: ExplorePersistentCachePublication) -> None:
        """Atomically select one prepared generation; previous readers stay leased."""

        self._validate_publication_paths(publication)
        family_dir = self._family_dir(publication.family_key)
        with self._family_lock(publication.family_key):
            selected = False
            try:
                publication.staging_path.replace(publication.final_path)
                atomic_write_text(
                    family_dir / "current.json",
                    canonical_json(
                        {
                            "family_digest": self.family_digest(publication.family_key),
                            "generation_id": publication.generation_id,
                        }
                    ),
                )
                selected = True
            except BaseException:
                self._retire_directory_best_effort(publication.staging_path)
                if not selected:
                    self._retire_directory_best_effort(publication.final_path)
                raise

    def discard_publication(self, publication: ExplorePersistentCachePublication) -> None:
        """Best-effort removal for a prepared generation that lost latest-wins."""

        self._validate_publication_paths(publication)
        with self._family_lock(publication.family_key):
            try:
                if publication.staging_path.exists():
                    shutil.rmtree(publication.staging_path)
            except OSError as exc:
                logger.warning(
                    "explore_cache_staging_retirement_failed",
                    generation_id=publication.generation_id,
                    error=str(exc),
                )

    def retire_unleased_generations(self, family_key: ExploreFamilyKey) -> None:
        """Best-effort retirement of non-current generations with no active reader."""

        coordination = self._family_coordination(family_key)
        with coordination.lock:
            family_dir = self._family_dir(family_key)
            try:
                pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
                if not isinstance(pointer, dict):
                    raise ValueError("current pointer must be an object")
                current_generation_id = _generation_id(pointer.get("generation_id"))
            except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "explore_cache_retirement_pointer_unreadable",
                    error=str(exc),
                )
                return
            generations_dir = family_dir / "generations"
            try:
                candidates = tuple(generations_dir.iterdir())
            except FileNotFoundError:
                return
            except OSError as exc:
                logger.warning(
                    "explore_cache_generations_scan_failed",
                    error=str(exc),
                )
                return
            for candidate in candidates:
                try:
                    eligible = (
                        candidate.is_dir()
                        and candidate.name != current_generation_id
                        and not coordination.leases_by_generation.get(candidate.name)
                    )
                except OSError as exc:
                    logger.warning(
                        "explore_cache_generation_inspection_failed",
                        generation_id=candidate.name,
                        error=str(exc),
                    )
                    continue
                if eligible:
                    self._retire_directory_best_effort(candidate)

    def _retire_generation_if_unselected_locked(
        self,
        family_key: ExploreFamilyKey,
        generation_id: str,
    ) -> None:
        family_dir = self._family_dir(family_key)
        try:
            pointer = json.loads((family_dir / "current.json").read_text(encoding="utf-8"))
            if not isinstance(pointer, dict):
                raise ValueError("current pointer must be an object")
            if _generation_id(pointer.get("generation_id")) == generation_id:
                return
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning(
                "explore_cache_lease_retirement_pointer_unreadable",
                generation_id=generation_id,
                error=str(exc),
            )
            return
        self._retire_directory_best_effort(family_dir / "generations" / generation_id)

    @staticmethod
    def _retire_directory_best_effort(candidate: Path) -> None:
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "explore_cache_generation_retirement_failed",
                generation_id=candidate.name,
                error=str(exc),
            )

    def _validate_publication_paths(
        self,
        publication: ExplorePersistentCachePublication,
    ) -> None:
        family_dir = self._family_dir(publication.family_key)
        generation_id = _generation_id(publication.generation_id)
        expected_staging = family_dir / f".staging-{generation_id}"
        expected_final = family_dir / "generations" / generation_id
        if publication.staging_path != expected_staging or publication.final_path != expected_final:
            raise ValueError("Explore publication paths do not match its family and generation")

    def _validate_generation_at(
        self,
        family_key: ExploreFamilyKey,
        generation_id: str,
        generation_dir: Path,
        *,
        report_cache_key: str,
        report: ExploreCacheReport,
    ) -> dict[str, Any]:
        metadata_path = generation_dir / "meta.json"
        data_path = generation_dir / "data.parquet"
        self._validate_generation_files(generation_dir, metadata_path, data_path)
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != EXPLORE_PERSISTENT_CACHE_SCHEMA_VERSION
        ):
            raise ValueError("invalid durable Explore generation metadata")
        if (
            raw.get("family_digest") != self.family_digest(family_key)
            or raw.get("family") != list(family_key)
            or raw.get("generation_id") != generation_id
            or raw.get("report_cache_key") != report_cache_key
            or raw.get("dataframe_cache_key") != report.dataframe_cache_key
            or ExploreCacheReport.model_validate(raw.get("report")) != report
        ):
            raise ValueError("durable Explore generation identity does not match worker result")
        artifact = raw.get("artifact")
        if not isinstance(artifact, dict):
            raise ValueError("invalid durable Explore artifact metadata")
        raw_columns = artifact.get("columns")
        if not isinstance(raw_columns, dict):
            raise ValueError("artifact columns must be a string mapping")
        columns: dict[str, str] = {}
        for name, dtype in raw_columns.items():
            if not isinstance(name, str) or not isinstance(dtype, str):
                raise ValueError("artifact columns must be a string mapping")
            columns[name] = dtype
        expected: dict[str, Any] = {
            "row_count": report.row_count,
            "column_count": report.column_count,
            "columns": columns,
            "size_bytes": _positive_or_zero_int(
                artifact.get("size_bytes"), field="artifact.size_bytes"
            ),
            "uncompressed_size_bytes": _positive_or_zero_int(
                artifact.get("uncompressed_size_bytes"), field="artifact.uncompressed_size_bytes"
            ),
            "digest": _non_empty_string(artifact.get("digest"), field="artifact.digest"),
        }
        self._validate_artifact(read_parquet_metadata(data_path), expected)
        if content_hash(data_path) != expected["digest"]:
            raise ValueError("durable Explore artifact digest does not match metadata")
        return expected
