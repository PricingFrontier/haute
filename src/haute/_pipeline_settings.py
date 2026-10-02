"""The pipeline settings: one per-clone file behind the Pipeline settings pane.

``<project root>/.haute/pipeline-settings.json`` holds what a person sets in
the pane or by hand. ``.haute/`` is the clone's untracked state directory, so
the settings belong to this clone on this machine and survive a restart, and
nothing else stores them. Every key is optional: an absent key or ``null``
means automatic. The contract is ``specs/execution-engine/low-level.md``
("Pipeline settings").

Reads are cheap and current. The parsed file is kept per path with its stat
signature, so a read costs one ``stat`` and a hand edit reaches the next read.
An invalid file raises :class:`PipelineSettingsError` on every read: nothing
falls back to automatic values in its place.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TypeGuard, TypeVar

from haute._file_ops import atomic_write_text

SETTINGS_PATH: Final = ".haute/pipeline-settings.json"
"""The file, relative to the project root, as messages and the pane name it."""

DEFAULT_STREAMING_CHUNK_SIZE: Final = 500_000
MAX_STREAMING_CHUNK_SIZE: Final = 10_000_000
GIB: Final = 1024**3
# A pebibyte keeps every byte figure inside the native size fields the caps use.
MAX_SIZE_GB: Final = 1024 * 1024
MAX_TIME_LIMIT_MINUTES: Final = 10_080
AUTOMATIC_CACHE_SIZE_CEILING_BYTES: Final = 20 * GIB
AUTOMATIC_PIPELINE_TIME_LIMIT_MINUTES: Final = 30
AUTOMATIC_MODELLING_TIME_LIMIT_MINUTES: Final = 60

_Signature = tuple[int, int, int]


class PipelineSettingsError(ValueError):
    """The settings file cannot be read as valid pipeline settings."""


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    """Each key's value as the file sets it, or ``None`` where it is automatic."""

    chunk_rows: int | None = None
    caching: bool | None = None
    cache_size_gb: float | None = None
    preview_memory_gb: float | None = None
    kept_free_gb: float | None = None
    pipeline_time_limit_minutes: float | None = None
    modelling_time_limit_minutes: float | None = None
    optimisation_time_limit_minutes: float | None = None

    @property
    def caching_enabled(self) -> bool:
        """Whether previews and runs read and write node-output snapshots."""
        return self.caching is not False

    @property
    def streaming_chunk_size(self) -> int:
        return DEFAULT_STREAMING_CHUNK_SIZE if self.chunk_rows is None else self.chunk_rows

    @property
    def cache_size_bytes(self) -> int | None:
        return _whole_bytes(self.cache_size_gb, minimum=1)

    @property
    def preview_memory_bytes(self) -> int | None:
        return _whole_bytes(self.preview_memory_gb, minimum=1)

    @property
    def kept_free_bytes(self) -> int | None:
        return _whole_bytes(self.kept_free_gb, minimum=0)

    @property
    def pipeline_time_limit_seconds(self) -> float:
        minutes = self.pipeline_time_limit_minutes
        return (AUTOMATIC_PIPELINE_TIME_LIMIT_MINUTES if minutes is None else minutes) * 60

    @property
    def modelling_time_limit_seconds(self) -> float:
        minutes = self.modelling_time_limit_minutes
        return (AUTOMATIC_MODELLING_TIME_LIMIT_MINUTES if minutes is None else minutes) * 60

    @property
    def optimisation_time_limit_seconds(self) -> float | None:
        """``None`` is no limit, the automatic optimisation time limit."""
        minutes = self.optimisation_time_limit_minutes
        return None if minutes is None else minutes * 60

    def values(self) -> dict[str, object]:
        """Every key with its value, ``None`` where it is automatic."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AutomaticPipelineSettings:
    """Each key's automatic figure now; the optimisation limit's is no limit."""

    chunk_rows: int
    caching: bool
    cache_size_gb: float
    preview_memory_gb: float
    kept_free_gb: float
    pipeline_time_limit_minutes: float
    modelling_time_limit_minutes: float
    optimisation_time_limit_minutes: float | None


def _whole_bytes(gigabytes: float | None, *, minimum: int) -> int | None:
    # A positive size below half a byte still sizes something rather than nothing.
    return None if gigabytes is None else max(minimum, round(gigabytes * GIB))


def _refuse(key: str, requirement: str) -> PipelineSettingsError:
    return PipelineSettingsError(f"{SETTINGS_PATH}: {key} must be {requirement}")


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _number(key: str, value: object, requirement: str) -> float:
    if not _is_number(value):
        raise _refuse(key, requirement)
    return float(value)


def _chunk_rows(key: str, value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_STREAMING_CHUNK_SIZE
    ):
        raise _refuse(key, f"a whole number from 1 to {MAX_STREAMING_CHUNK_SIZE:,}")
    return value


def _switch(key: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise _refuse(key, "true or false")
    return value


def _positive_size(key: str, value: object) -> float:
    requirement = f"a number of GB greater than 0 and at most {MAX_SIZE_GB:,}"
    gigabytes = _number(key, value, requirement)
    if not 0 < gigabytes <= MAX_SIZE_GB:
        raise _refuse(key, requirement)
    return gigabytes


def _size_from_zero(key: str, value: object) -> float:
    requirement = f"a number of GB from 0 to {MAX_SIZE_GB:,}"
    gigabytes = _number(key, value, requirement)
    if not 0 <= gigabytes <= MAX_SIZE_GB:
        raise _refuse(key, requirement)
    return gigabytes


def _minutes(key: str, value: object) -> float:
    requirement = f"a number of minutes greater than 0 and at most {MAX_TIME_LIMIT_MINUTES:,}"
    minutes = _number(key, value, requirement)
    if not 0 < minutes <= MAX_TIME_LIMIT_MINUTES:
        raise _refuse(key, requirement)
    return minutes


# The file's keys in the order it is written, each with its validator.
_VALIDATORS: Final[dict[str, Callable[[str, object], object]]] = {
    "chunk_rows": _chunk_rows,
    "caching": _switch,
    "cache_size_gb": _positive_size,
    "preview_memory_gb": _positive_size,
    "kept_free_gb": _size_from_zero,
    "pipeline_time_limit_minutes": _minutes,
    "modelling_time_limit_minutes": _minutes,
    "optimisation_time_limit_minutes": _minutes,
}
SETTING_KEYS: Final = tuple(_VALIDATORS)

_T = TypeVar("_T")


def _checked(
    data: Mapping[str, object],
    key: str,
    validate: Callable[[str, object], _T],
) -> _T | None:
    value = data.get(key)
    return None if value is None else validate(key, value)


def validated_pipeline_settings(data: Mapping[str, object]) -> PipelineSettings:
    """Validate a mapping of keys to values (``None`` for automatic) as the file is."""
    unknown = sorted(key for key in data if key not in _VALIDATORS)
    if unknown:
        raise PipelineSettingsError(
            f"{SETTINGS_PATH}: {unknown[0]!r} is not a pipeline setting; the settings are "
            + ", ".join(SETTING_KEYS)
        )
    return PipelineSettings(
        chunk_rows=_checked(data, "chunk_rows", _chunk_rows),
        caching=_checked(data, "caching", _switch),
        cache_size_gb=_checked(data, "cache_size_gb", _positive_size),
        preview_memory_gb=_checked(data, "preview_memory_gb", _positive_size),
        kept_free_gb=_checked(data, "kept_free_gb", _size_from_zero),
        pipeline_time_limit_minutes=_checked(data, "pipeline_time_limit_minutes", _minutes),
        modelling_time_limit_minutes=_checked(data, "modelling_time_limit_minutes", _minutes),
        optimisation_time_limit_minutes=_checked(data, "optimisation_time_limit_minutes", _minutes),
    )


def settings_file(project_root: Path | str) -> Path:
    """The settings file of the project at *project_root*."""
    return Path(project_root) / SETTINGS_PATH


def _key(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _signature(path: Path) -> _Signature | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PipelineSettingsError(
            f"{SETTINGS_PATH} could not be read: {exc.strerror or exc}"
        ) from exc
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _refuse_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PipelineSettingsError(f"{SETTINGS_PATH}: {key!r} is set twice")
        result[key] = value
    return result


def _refuse_constant(name: str) -> object:
    raise PipelineSettingsError(f"{SETTINGS_PATH}: {name} is not a number")


def _parse(path: Path) -> PipelineSettings:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PipelineSettingsError(f"{SETTINGS_PATH} is not UTF-8 text") from exc
    except OSError as exc:
        raise PipelineSettingsError(
            f"{SETTINGS_PATH} could not be read: {exc.strerror or exc}"
        ) from exc
    try:
        data = json.loads(
            text,
            object_pairs_hook=_refuse_duplicates,
            parse_constant=_refuse_constant,
        )
    except json.JSONDecodeError as exc:
        raise PipelineSettingsError(
            f"{SETTINGS_PATH} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise PipelineSettingsError(f"{SETTINGS_PATH} must hold one JSON object")
    return validated_pipeline_settings(data)


_READ_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_READS: dict[str, tuple[_Signature | None, PipelineSettings]] = {}
_followed_root: str | None = None


def read_pipeline_settings(project_root: Path | str) -> PipelineSettings:
    """The settings of the project at *project_root*, as its file holds them now.

    A missing file is all automatic. The file is parsed again only when its
    stat signature changes. In the server process (see :func:`follow_chunk_rows`)
    a read that parses a changed file applies its chunk rows to the process.
    """
    path = settings_file(project_root)
    cache_key = _key(path)
    signature = _signature(path)
    with _READ_LOCK:
        cached = _READS.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    settings = PipelineSettings() if signature is None else _parse(path)
    with _READ_LOCK:
        _READS[cache_key] = (signature, settings)
    if _followed_root is not None and _key(Path(project_root)) == _followed_root:
        apply_chunk_rows(settings)
    return settings


def project_pipeline_settings() -> PipelineSettings:
    """The settings of the project the current execution runs in."""
    from haute._path_resolution import current_runtime_project_root

    return read_pipeline_settings(current_runtime_project_root())


def _rendered(settings: PipelineSettings) -> str:
    document: dict[str, object] = {}
    for key, value in settings.values().items():
        if value is None:
            continue
        # A whole float is written as an integer, as a person would write it.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        document[key] = value
    return json.dumps(document, indent=2) + "\n"


def update_pipeline_settings(
    project_root: Path | str,
    changes: Mapping[str, object],
) -> PipelineSettings:
    """Change the keys in *changes* (``None`` restores automatic) and write the file.

    The current file is read first, so an invalid file refuses the update and
    is left as it is. The whole result is validated before the file is
    replaced atomically.
    """
    path = settings_file(project_root)
    with _WRITE_LOCK:
        current = read_pipeline_settings(project_root)
        updated = validated_pipeline_settings({**current.values(), **changes})
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, _rendered(updated))
    return read_pipeline_settings(project_root)


def follow_chunk_rows(project_root: Path | str) -> None:
    """Make this process (the server) follow the project's chunk rows.

    Applies them now, before any worker spawns, and again whenever a read of
    this project's settings parses a changed file. Workers never call this:
    they take the server's value from their spawn environment or their task.
    """
    global _followed_root
    _followed_root = _key(Path(project_root))
    apply_chunk_rows(read_pipeline_settings(project_root))


def stop_following_chunk_rows() -> None:
    global _followed_root
    _followed_root = None


def apply_chunk_rows(settings: PipelineSettings) -> None:
    """Set this process's streaming chunk size to *settings*' chunk rows."""
    from haute._polars_utils import current_streaming_chunk_size, set_streaming_chunk_size

    if current_streaming_chunk_size() != settings.streaming_chunk_size:
        set_streaming_chunk_size(settings.streaming_chunk_size)


def automatic_cache_size_bytes(directory: Path) -> int:
    """The automatic captures' budget: 20 GiB, or a tenth of the free disk under *directory*."""
    return min(AUTOMATIC_CACHE_SIZE_CEILING_BYTES, shutil.disk_usage(directory).free // 10)


def automatic_pipeline_settings(project_root: Path | str) -> AutomaticPipelineSettings:
    """Each key's automatic figure now, computed the way its consumer computes it."""
    from haute._execution_admission import automatic_preview_memory_and_kept_free_bytes

    root = Path(project_root)
    settings = read_pipeline_settings(root)
    inputs_root = root / ".haute_cache" / "inputs"
    preview_bytes, kept_free_bytes = automatic_preview_memory_and_kept_free_bytes(settings)
    return AutomaticPipelineSettings(
        chunk_rows=DEFAULT_STREAMING_CHUNK_SIZE,
        caching=True,
        cache_size_gb=automatic_cache_size_bytes(inputs_root if inputs_root.is_dir() else root)
        / GIB,
        preview_memory_gb=preview_bytes / GIB,
        kept_free_gb=kept_free_bytes / GIB,
        pipeline_time_limit_minutes=AUTOMATIC_PIPELINE_TIME_LIMIT_MINUTES,
        modelling_time_limit_minutes=AUTOMATIC_MODELLING_TIME_LIMIT_MINUTES,
        optimisation_time_limit_minutes=None,
    )
