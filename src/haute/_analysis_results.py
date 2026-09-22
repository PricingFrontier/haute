"""Analysis results keyed by the exact data they were computed from.

Every result belongs to one data point and one data version: a refreshed,
widened, or rewritten point has a new data version, so an analysis of the
previous data is never returned for it. Durable results (the data profile)
live as one atomic, schema-validated JSON document per
``(point identity digest, data version, analysis kind, analysis version)``
under ``.haute_cache/analyses``; short synchronous analyses (banding
statistics, rating levels, pivot members) are memoised in-process by
``(point identity digest, data version, request digest)``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from haute._cache import canonical_json
from haute._file_ops import atomic_write_text
from haute._logging import get_logger
from haute._lru_cache import LRUCache

logger = get_logger(component="analysis_results")

ANALYSIS_DOCUMENT_SCHEMA_VERSION = 1
SYNCHRONOUS_ANALYSIS_CACHE_MAX_ENTRIES = 64
POINT_DIR_NAME_LENGTH = 16
DATA_VERSION_NAME_LENGTH = 12

ModelT = TypeVar("ModelT", bound=BaseModel)


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


@dataclass(frozen=True, slots=True)
class AnalysisKey:
    point_digest: str
    data_version: str
    kind: str
    version: int

    def __post_init__(self) -> None:
        if not _is_digest(self.point_digest):
            raise ValueError("analysis point digest must be a SHA-256 hex digest")
        if not isinstance(self.data_version, str) or not self.data_version:
            raise ValueError("analysis data version must be a non-empty string")
        if not self.kind.isidentifier():
            raise ValueError("analysis kind must be an identifier")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("analysis version must be a positive integer")


class AnalysisResultStore:
    """Durable analysis documents under ``<project>/.haute_cache/analyses``."""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve() / ".haute_cache" / "analyses"

    def _point_dir(self, point_digest: str) -> Path:
        if not _is_digest(point_digest):
            raise ValueError("analysis point digest must be a SHA-256 hex digest")
        # Directory and file names stay short so a deeply nested project still
        # fits inside the Windows path limit; every document carries the full
        # digest and is validated against its key when it is read.
        return self.root / point_digest[:POINT_DIR_NAME_LENGTH]

    @staticmethod
    def _prefix(key: AnalysisKey) -> str:
        return f"{key.kind}-v{key.version}-"

    def _path(self, key: AnalysisKey) -> Path:
        version_digest = hashlib.sha256(key.data_version.encode("utf-8")).hexdigest()[
            :DATA_VERSION_NAME_LENGTH
        ]
        return self._point_dir(key.point_digest) / f"{self._prefix(key)}{version_digest}.json"

    def read(self, key: AnalysisKey, model: type[ModelT]) -> ModelT | None:
        """Return the validated result for exactly this data version, or ``None``.

        A document for any other data version of the same analysis is removed:
        the point has moved on, so it can never be served again. A document that
        fails validation is discarded and reported as absent, so the analysis
        is recomputed rather than trusted.
        """
        target = self._path(key)
        point_dir = target.parent
        if point_dir.is_dir():
            for other in point_dir.glob(f"{self._prefix(key)}*.json"):
                if other != target:
                    other.unlink(missing_ok=True)
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            return None
        try:
            document = json.loads(raw)
            if (
                not isinstance(document, dict)
                or document.get("schema_version") != ANALYSIS_DOCUMENT_SCHEMA_VERSION
                or document.get("point_digest") != key.point_digest
                or document.get("data_version") != key.data_version
                or document.get("kind") != key.kind
                or document.get("version") != key.version
                or "result" not in document
            ):
                raise ValueError("analysis document does not match its key")
            return model.model_validate(document["result"])
        except (ValueError, ValidationError) as exc:
            logger.warning(
                "analysis_result_discarded",
                kind=key.kind,
                point_digest=key.point_digest,
                error=str(exc),
            )
            target.unlink(missing_ok=True)
            return None

    def write(self, key: AnalysisKey, result: BaseModel) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            canonical_json(
                {
                    "schema_version": ANALYSIS_DOCUMENT_SCHEMA_VERSION,
                    "point_digest": key.point_digest,
                    "data_version": key.data_version,
                    "kind": key.kind,
                    "version": key.version,
                    "result": result.model_dump(mode="json"),
                }
            ),
        )

    def clear_point(self, point_digest: str) -> None:
        """Remove every analysis document of one data point.

        Only an absent directory is tolerated: a deletion that fails for any other
        reason must not be reported as a successful clear, because the analyses
        would still be there to serve.
        """
        try:
            shutil.rmtree(self._point_dir(point_digest))
        except FileNotFoundError:
            return


class SynchronousAnalysisCache:
    """In-process memo of short analyses keyed by point, data version, and request."""

    def __init__(self, max_entries: int = SYNCHRONOUS_ANALYSIS_CACHE_MAX_ENTRIES) -> None:
        self._cache: LRUCache[tuple[str, str, str], object] = LRUCache(max_size=max_entries)

    @staticmethod
    def request_digest(request: object) -> str:
        return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()

    def get(self, point_digest: str, data_version: str, request_digest: str) -> object | None:
        return self._cache.get((point_digest, data_version, request_digest))

    def put(
        self, point_digest: str, data_version: str, request_digest: str, result: object
    ) -> None:
        self._cache.put((point_digest, data_version, request_digest), result)

    def clear(self) -> None:
        self._cache.clear()
