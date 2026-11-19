"""The model resolution: which model a Model Scoring node's MLflow reference means.

A run or registered source is answered here, once, with one :class:`ResolvedModel`:
the store, the run, the artifact (a model file or an MLflow pyfunc model folder),
its model family, the feature contract logged beside a native model when there is
one, and what identifies the bytes of each as the store gives them. Everything
that needs the scoring model (the loader, the key of the local copy, the run
picker's listing, the deploy bundle and the image it builds, and the identity a
stored preview result is kept under) takes that answer and searches nothing of its
own. This is the only module that lists a run's artifacts for a scoring model;
``tests/test_model_resolution.py`` fails on another.

``resolve_mlflow_source`` (``_mlflow_utils``) keeps answering the callers that want
only a run, a version or a client; it never says which model scores.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from haute._model_flavors import (
    ModelFamily,
    family_for_artifact,
    family_for_suffix,
    supported_model_files_description,
)
from haute._model_source import RegisteredModelSource, RunModelSource
from haute.errors import ModelResolutionError

if TYPE_CHECKING:
    from mlflow.tracking import MlflowClient

    from haute._mlflow_utils import ResolvedBackend

#: The file that makes a folder an MLflow pyfunc model.
MLMODEL_FILE = "MLmodel"

#: ``(started, ended)`` of one artifact listing, for a caller that measures them.
ListingObserver = Callable[[float, float], None]

_ResolutionKey = tuple[str, RunModelSource | RegisteredModelSource]
_RESOLUTIONS: ContextVar[dict[_ResolutionKey, ResolvedModel] | None] = ContextVar(
    "haute_model_resolutions", default=None
)


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """One file or folder of a run as the store describes it.

    ``identity`` is what identifies its bytes as the store gives them:
    ``content:<algorithm>:<digest>`` for a file in a local ``file:`` artifact
    location, whose bytes are on disk; ``size:<bytes>`` for a file elsewhere,
    the size its listing reports, all MLflow's artifact stores give without a
    download; ``listing:<digest>`` for a pyfunc folder, the hash of its own
    listing (each entry's name, kind and size).
    """

    path: str
    identity: str

    @property
    def token(self) -> str:
        """The filesystem-safe name a local copy of these bytes is kept under."""
        return hashlib.sha256(self.identity.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """The one model a Model Scoring node's MLflow reference means."""

    backend: ResolvedBackend
    run_id: str
    artifact: StoredArtifact
    family: ModelFamily
    #: The ``<stem>.feature_contract.json`` beside a native model; ``None`` when the
    #: run holds none there, and always for a pyfunc folder.
    contract: StoredArtifact | None
    #: The concrete registered version; ``""`` for a run source.
    version: str
    #: How messages name what was asked for.
    reference: str

    @property
    def loads_from_file(self) -> bool:
        return self.family.load_file is not None

    def identity_record(self) -> dict[str, object]:
        """The secret-free identity a stored preview result is kept under."""
        return {
            "backend": self.backend.identity,
            "run_id": self.run_id,
            "artifact": {"path": self.artifact.path, "identity": self.artifact.identity},
            "contract": (
                None
                if self.contract is None
                else {"path": self.contract.path, "identity": self.contract.identity}
            ),
        }


def validate_artifact_path(artifact_path: str) -> None:
    """Refuse a run-relative artifact path that is empty or leaves the run."""
    if not artifact_path or "\\" in artifact_path or "\x00" in artifact_path:
        raise ValueError(f"Invalid artifact_path: {artifact_path!r}")
    raw_parts = artifact_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"Invalid artifact_path: {artifact_path!r}")
    parsed = PurePosixPath(artifact_path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError(f"Invalid artifact_path: {artifact_path!r}")


def contract_beside(artifact_path: str) -> str:
    """The run-relative path of the feature contract logged beside a model file."""
    from haute.modelling._training_job import model_contract_filename

    model = PurePosixPath(artifact_path)
    return str(model.with_name(model_contract_filename(model.stem)))


@contextmanager
def model_resolution_scope() -> Iterator[None]:
    """Answer each reference once for the operation this scope spans.

    Inside the scope :func:`resolve_model` asks the store once per backend and
    source and every later call shares that answer, so the identity a result is
    stored under and the model it was computed from are the same model. A scope
    opened inside another shares the outer one. A failed resolution is not kept.
    """
    if _RESOLUTIONS.get() is not None:
        yield
        return
    token = _RESOLUTIONS.set({})
    try:
        yield
    finally:
        _RESOLUTIONS.reset(token)


def resolve_model(
    source: RunModelSource | RegisteredModelSource, *, backend: ResolvedBackend
) -> ResolvedModel:
    """The one model *source* means at *backend*.

    Raises:
        ModelResolutionError: the reference does not resolve to one model.
        ConfigError: the artifact's suffix is one no family registers.
        ValueError: a registered model has no such alias or no versions.
        ImportError: ``mlflow`` is not installed.
        MlflowException: the store refused or could not be reached; a missing
            run is the store's own error.
    """
    memo = _RESOLUTIONS.get()
    key = (backend.identity, source)
    if memo is not None:
        known = memo.get(key)
        if known is not None:
            return known
    from haute._mlflow_utils import pinned_client

    client = pinned_client(backend)
    if isinstance(source, RunModelSource):
        resolved = _resolve_in_run(
            _RunArtifacts(client, source.run_id),
            backend,
            source.artifact_path,
            version="",
            reference=f"MLflow run {source.run_id!r}",
        )
    else:
        resolved = _resolve_version(client, backend, source)
    if memo is not None:
        memo[key] = resolved
    return resolved


def run_model_candidates(
    client: MlflowClient, run_id: str, *, on_listing: ListingObserver | None = None
) -> tuple[str, ...]:
    """The models a run holds by the one discovery rule, in listing order.

    The files a registered family loads at the run's top level; when there are
    none, those one folder down in folders that are not pyfunc model folders;
    when there are none, the top-level pyfunc model folders (a folder holding an
    ``MLmodel`` file). The first level that holds one answers.
    """
    return _candidates(_RunArtifacts(client, run_id, on_listing=on_listing))


class _RunArtifacts:
    """One resolution's view of a run's artifacts: each folder listed at most once."""

    def __init__(
        self, client: MlflowClient, run_id: str, *, on_listing: ListingObserver | None = None
    ) -> None:
        self.client = client
        self.run_id = run_id
        self._on_listing = on_listing
        self._listings: dict[str, tuple[Any, ...]] = {}
        self._location: tuple[str] | None = None

    def listing(self, folder: str) -> tuple[Any, ...]:
        """The entries of *folder* (``""`` for the top level), paths run-relative."""
        known = self._listings.get(folder)
        if known is not None:
            return known
        started = perf_counter()
        try:
            entries = tuple(
                self.client.list_artifacts(self.run_id, folder)
                if folder
                else self.client.list_artifacts(self.run_id)
            )
        finally:
            if self._on_listing is not None:
                self._on_listing(started, perf_counter())
        self._listings[folder] = entries
        return entries

    def entry(self, path: str) -> Any | None:
        parent = str(PurePosixPath(path).parent)
        return next(
            (item for item in self.listing("" if parent == "." else parent) if item.path == path),
            None,
        )

    def location(self) -> str:
        """The run's artifact location, read once."""
        if self._location is None:
            self._location = (str(self.client.get_run(self.run_id).info.artifact_uri),)
        return self._location[0]


def _is_model_file(item: Any) -> bool:
    suffix = PurePosixPath(item.path).suffix.lower()
    return not item.is_dir and family_for_suffix(suffix) is not None


def _is_pyfunc_folder(entries: Sequence[Any]) -> bool:
    return any(
        not item.is_dir and PurePosixPath(item.path).name == MLMODEL_FILE for item in entries
    )


def _candidates(artifacts: _RunArtifacts) -> tuple[str, ...]:
    top = artifacts.listing("")
    files = tuple(str(item.path) for item in top if _is_model_file(item))
    if files:
        return files
    nested: list[str] = []
    pyfuncs: list[str] = []
    for folder in (item for item in top if item.is_dir):
        inside = artifacts.listing(str(folder.path))
        if _is_pyfunc_folder(inside):
            pyfuncs.append(str(folder.path))
        else:
            nested.extend(str(item.path) for item in inside if _is_model_file(item))
    return tuple(nested) if nested else tuple(pyfuncs)


def _refused(message: str, reference: str, candidates: Sequence[str]) -> ModelResolutionError:
    return ModelResolutionError(message, reference=reference, candidates=list(candidates))


def _resolve_version(
    client: MlflowClient, backend: ResolvedBackend, source: RegisteredModelSource
) -> ResolvedModel:
    """A registered version, through its source."""
    from haute._mlflow_utils import resolve_version

    version = resolve_version(client, source.registered_model, source.version, source.alias)
    reference = f"version {version} of registered model {source.registered_model!r}"
    model_version = client.get_model_version(source.registered_model, version)
    run_id = str(model_version.run_id or "")
    if not run_id:
        raise _refused(
            f"{reference} has no run. Haute loads a registered model from the run it was "
            "registered from; register it from a file logged to a run.",
            reference,
            (),
        )
    # The source names the file: in a run by ``runs:/<run>/<path>``, or under the run's
    # artifact location. A version registered from a logged model (``models:/...``, as
    # MLflow 3 makes of ``runs:/<run>/model``) names no file of its run, so its run is
    # searched; a source naming the run itself is the run with no path.
    source_text = str(model_version.source or "")
    if source_text.startswith("models:/"):
        artifacts, path = _RunArtifacts(client, run_id), ""
    elif source_text.startswith("runs:/"):
        named_run, _, rest = source_text[len("runs:/") :].partition("/")
        artifacts, path = _RunArtifacts(client, named_run or run_id), rest.strip("/")
    else:
        artifacts = _RunArtifacts(client, run_id)
        path = _path_under(source_text, artifacts.location(), reference)
    return _resolve_in_run(artifacts, backend, path, version=version, reference=reference)


def _path_under(source: str, location: str, reference: str) -> str:
    """The run-relative path *source* names under the run's artifact *location*.

    The source text is never echoed: it can name a storage location.
    """
    location = location.rstrip("/")
    stripped = source.rstrip("/")
    if stripped == location:
        return ""
    if stripped.startswith(location + "/"):
        return stripped[len(location) + 1 :]
    raise _refused(
        f"{reference} was registered from a location outside its run's artifacts. Haute "
        "loads a registered model from its run; register it from a file logged to the run.",
        reference,
        (),
    )


def _resolve_in_run(
    artifacts: _RunArtifacts,
    backend: ResolvedBackend,
    path: str,
    *,
    version: str,
    reference: str,
) -> ResolvedModel:
    if not path:
        candidates = _candidates(artifacts)
        if not candidates:
            raise _refused(
                f"{reference} holds no model Haute can score. Expected "
                f"{supported_model_files_description()}, at the run's top level or one "
                "folder down.",
                reference,
                (),
            )
        if len(candidates) > 1:
            remedy = (
                "Register the model from its file."
                if version
                else "Set the artifact path to the one to score."
            )
            raise _refused(
                f"{reference} holds several models: {', '.join(candidates)}. {remedy}",
                reference,
                candidates,
            )
        path = candidates[0]
    validate_artifact_path(path)
    entry = artifacts.entry(path)
    if entry is None:
        held = _candidates(artifacts)
        holds = f" It holds {', '.join(held)}." if held else " It holds no model Haute can score."
        raise _refused(f"{reference} holds no artifact {path!r}.{holds}", reference, held)
    family = family_for_artifact(path)
    if entry.is_dir:
        inside = artifacts.listing(path)
        if family.load_file is not None or not _is_pyfunc_folder(inside):
            raise _refused(
                f"{path!r} in {reference} is a folder without an {MLMODEL_FILE} file, not a "
                "model Haute can score.",
                reference,
                _candidates(artifacts),
            )
        stored = StoredArtifact(path, _listing_identity(inside))
        contract = None
    else:
        if family.load_file is None:
            raise _refused(
                f"{path!r} in {reference} is not a model Haute can score. Expected "
                f"{supported_model_files_description()}.",
                reference,
                _candidates(artifacts),
            )
        local = _local_location(artifacts, backend)
        stored = StoredArtifact(path, _file_identity(entry, local))
        contract_entry = artifacts.entry(contract_beside(path))
        contract = (
            StoredArtifact(str(contract_entry.path), _file_identity(contract_entry, local))
            if contract_entry is not None and not contract_entry.is_dir
            else None
        )
    return ResolvedModel(
        backend=backend,
        run_id=artifacts.run_id,
        artifact=stored,
        family=family,
        contract=contract,
        version=version,
        reference=reference,
    )


def _local_location(artifacts: _RunArtifacts, backend: ResolvedBackend) -> Path | None:
    """The folder a local store keeps the run's artifacts in; ``None`` elsewhere."""
    if backend.mode != "local":
        return None
    location = artifacts.location()
    parsed = urlparse(location)
    if parsed.scheme not in ("", "file"):
        return None
    from mlflow.utils.file_utils import local_file_uri_to_path

    return Path(local_file_uri_to_path(location))


def _file_identity(item: Any, local: Path | None) -> str:
    """What identifies a stored file's bytes as the store gives them."""
    if local is not None:
        return _content_identity(local / str(item.path))
    size = item.file_size
    if size is None:
        raise ModelResolutionError(
            f"The MLflow artifact store reports no size for {item.path!r}, so Haute cannot "
            "tell when it changes.",
            artifact_path=str(item.path),
        )
    return _size_identity(size)


def _content_identity(path: Path) -> str:
    from haute._hashing import HASH_ALGO
    from haute._json_shred._source_proof import file_signature

    return f"content:{HASH_ALGO}:{file_signature(path).digest}"


def _size_identity(size: int) -> str:
    return f"size:{size}"


def copy_identity(stored: StoredArtifact, copy: Path) -> str:
    """The identity a local *copy* of *stored* has, in the terms the store gave *stored*'s.

    Equal to ``stored.identity`` exactly when the copy is the bytes the store
    described: the same size for a ``size:`` identity, the same content for a
    ``content:`` one.
    """
    kind = stored.identity.partition(":")[0]
    if kind == "size":
        return _size_identity(copy.stat().st_size)
    if kind == "content":
        return _content_identity(copy)
    raise ValueError(f"{stored.path!r} is not a file; its identity is {stored.identity!r}.")


def _listing_identity(entries: Sequence[Any]) -> str:
    listed = sorted(
        (PurePosixPath(item.path).name, bool(item.is_dir), item.file_size) for item in entries
    )
    return "listing:" + hashlib.sha256(repr(listed).encode("utf-8")).hexdigest()
