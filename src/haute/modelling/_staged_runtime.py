"""Owned checkpoint bundles and post-fit replay through the canonical TrainingJob evaluator."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import numpy as np
import polars as pl

from haute.errors import HauteValidationError
from haute.modelling._candidate_run import training_identity_sha256
from haute.modelling._evaluation import EvaluationPlan
from haute.modelling._stage_diagnostics import closure, hydrate, split
from haute.modelling._stage_objects import (
    DeltaRevision,
    ObjectRef,
    Opener,
    RevisionManifest,
    StageArchive,
    StoredRevision,
    extract_archive,
    object_kind,
    strict_json_loads,
    write_bundle,
)
from haute.modelling._stage_views import table_summary
from haute.modelling._staged_config import StagedConfig

if TYPE_CHECKING:
    from haute.modelling._training_job import _SplitResult

# A revision read in place through verified handles.
InPlaceRevision = StoredRevision | DeltaRevision
_FIT_KEY = re.compile(r"(?:development|validation-[0-9])\Z")
_JOB_FIELDS = (
    "name",
    "target",
    "weight",
    "exclude",
    "feature_columns",
    "fold_column",
    "id_columns",
    "algorithm",
    "task",
    "params",
    "metrics",
    "loss_function",
    "variance_power",
    "offset",
    "monotone_constraints",
    "feature_weights",
    "refit_on_development",
    "positive_class",
    "device",
    "fit_mode",
    "chart_left_out",
)
_DIAGNOSTICS = (
    "metrics",
    "feature_importance",
    "features",
    "cat_features",
    "diagnostics_set",
    "train_rows",
    "validation_rows",
    "development_rows",
    "final_test_rows",
    "ave_per_feature",
    "ave_left_out",
    "tboost_tables",
    "loss_history",
    "fit_evidence",
    "diagnostics_errors",
)
_LIMIT = 4 * 1024**3
_MAX_PATH_SPLIT_BYTES = 256 * 1024**2
_MAX_SESSION_CACHE_BYTES = 512 * 1024**2
_EXPORT_MEMBERS = {
    "model": "export/model.native",
    "feature_contract": "export/feature-contract.json",
    "evaluation_plan": "plan.json",  # a final revision's plan is the final-fit plan
    "evaluation_results": "export/evaluation-results.json",
    "evaluation_report": "export/evaluation-report.json",
}


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _kwargs(job: Any) -> dict[str, Any]:
    return {
        **{name: copy.deepcopy(getattr(job, name)) for name in _JOB_FIELDS},
        "evaluation": job.evaluation.to_plain_data(),
        "stages": job.stages.to_plain_data(),
        "purpose": "exploration",
        "tuning": job.tuning.to_plain_data() if job.tuning is not None else None,
    }


def fit_config_digest(kwargs: Mapping[str, Any]) -> str:
    """Stage drafts, output stage and model name never change the retained GBM fit."""
    values = {key: value for key, value in kwargs.items() if key != "stages"}
    for name in ("exclude", "feature_columns", "id_columns"):
        values[name] = values.get(name) or None
    if values.get("purpose") == "exploration" and isinstance(values.get("evaluation"), Mapping):
        values["refit_on_development"] = (
            values["evaluation"]["validation"]["method"] != "single"
            or values.get("tuning") is not None
        )
    return training_identity_sha256(values)


def _fit_key(job: Any) -> str:
    return (
        "development"
        if job.evaluation_fit_index is None
        else f"validation-{job.evaluation_fit_index}"
    )


def checkpoint_members(key: str) -> tuple[str, str]:
    """A retained fit's t-boost checkpoint parts: immutable evidence and the stage chain."""
    return f"fit/{key}/evidence", f"fit/{key}/chain"


def _pack(
    path: Path,
    files: Mapping[str, Path | bytes | Opener | ObjectRef],
    metadata: Mapping[str, Any],
) -> None:
    """Write a portable stage archive; `metadata.staged` diagnostics become shared objects."""
    members, stored = pack_members(files, metadata)
    write_bundle(path, members, revision=uuid.uuid4().hex, result={}, metadata=stored)


def pack_members(
    files: Mapping[str, Any], metadata: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A revision's members and stored metadata: `metadata.staged` split into objects."""
    objects: dict[str, bytes] = {}
    stored = dict(metadata)
    if isinstance(metadata.get("staged"), Mapping):
        stored["staged"] = split({"staged": metadata["staged"]}, objects.__setitem__)["staged"]
    return {**files, **objects}, stored


class StageBundle:
    """One stage revision opened for a job.

    The revision's members are read only through `open_member`. For a store revision that
    is the object store's verified handle; for an extracted portable bundle, it is the
    bundle's private, already verified copy. A job writes new members (checkpoint chains,
    path candidates and results, diagnostics) into its own scratch directory, and those are
    preferred to the revision's.
    """

    def __init__(
        self,
        root: Path,
        manifest: RevisionManifest,
        opener: Callable[[str], AbstractContextManager[IO[bytes]]],
        files: dict[str, Path] | None,
        metadata: dict[str, Any],
        plan: EvaluationPlan,
    ) -> None:
        self.root, self.metadata, self.plan = root, metadata, plan
        self.manifest = manifest
        self._opener = opener
        # Extracted copies (a portable bundle only); a store revision has no paths.
        self._files = files
        self._source: Path | None = None
        self.scratch = root / "scratch"
        self.scratch.mkdir(exist_ok=True)
        self._written: dict[str, Path] = {}
        self.path_splits: dict[str, tuple[_SplitResult, Any]] = {}
        self.path_split_bytes = 0
        self._sessions: OrderedDict[str, tuple[tuple[object, ...], int, Any]] = OrderedDict()
        self._session_bytes = 0

    @property
    def source(self) -> Path:
        """The training rows as a Parquet path.

        An extracted bundle has its private copy. A revision read in place copies its rows
        once, through their verified handle, into this job's scratch: training reads
        Parquet by path, and no path to a store object is ever handed out.
        """
        if self._files is not None or "source.parquet" in self._written:
            return self.member("source.parquet")
        if self._source is None:
            copy = self.scratch / "source.parquet"
            with self.open_member("source.parquet") as rows, copy.open("xb") as target:
                shutil.copyfileobj(rows, target, 1024 * 1024)
            self._source = copy
        return self._source

    def packed(self, name: str) -> Path | ObjectRef | Opener:
        """How a new revision of this bundle carries member *name*.

        A member this job wrote is carried. An untouched member of a revision read in place
        is inherited by its reference, so the new archive is a delta on this revision. An
        extracted bundle's members are carried from their verified copies.
        """
        if name in self._written:
            return self._written[name]
        if self._files is None:
            ref = self.manifest.objects.get(name)
            if ref is None:
                raise HauteValidationError(f"Stage revision has no member {name!r}")
            return ref
        return self.opener(name)

    def has_member(self, name: str) -> bool:
        return name in self._written or name in self.manifest.objects

    def member(self, name: str) -> Path:
        """A member's file path: a job-written member or an extracted bundle's copy."""
        found = self._written.get(name) or (self._files or {}).get(name)
        if found is None:
            if name in self.manifest.objects:
                raise HauteValidationError(
                    f"Stage member {name!r} is read in place; open it through its handle"
                )
            raise HauteValidationError(f"Stage revision has no member {name!r}")
        return found

    @contextmanager
    def open_member(self, name: str) -> Iterator[IO[bytes]]:
        """A member's read handle; a revision member's bytes are verified as read."""
        written = self._written.get(name)
        if written is not None:
            with written.open("rb") as handle:
                yield handle
            return
        if name not in self.manifest.objects:
            raise HauteValidationError(f"Stage revision has no member {name!r}")
        with self._opener(name) as handle:
            yield handle

    def opener(self, name: str) -> Callable[[], AbstractContextManager[IO[bytes]]]:
        """A deferred `open_member` for *name*, for packing a new revision."""
        if not self.has_member(name):
            raise HauteValidationError(f"Stage revision has no member {name!r}")
        return lambda: self.open_member(name)

    def read_bytes(self, name: str) -> bytes:
        with self.open_member(name) as handle:
            return handle.read()

    def _identity(self, name: str) -> tuple[object, ...]:
        """A member's content identity: its digest, or a job-written file's stat."""
        written = self._written.get(name)
        if written is None:
            return ("object", self.manifest.objects[name].sha256)
        info = written.stat()
        return ("file", info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def write_member(self, name: str, data: bytes) -> Path:
        """Record a new member this job wrote; content-addressed names are written once."""
        object_kind(name)  # validates the name
        if name in self._written and name.split("/", 1)[0] in {"snapshot", "table", "history"}:
            return self._written[name]
        path = self.scratch / f"{uuid.uuid4().hex}.member"
        path.write_bytes(data)
        self._written[name] = path
        return path

    def adopt_member(self, name: str, path: Path) -> None:
        """Record a scratch file written by another writer (a checkpoint chain) as *name*."""
        object_kind(name)
        if path.parent != self.scratch:
            raise HauteValidationError("Stage members must be written to the job's scratch")
        self._written[name] = path

    def read_json(self, name: str) -> Any:
        data = self.read_bytes(name)  # verification errors keep their own message
        try:
            return strict_json_loads(data)
        except (ValueError, UnicodeError) as exc:
            raise HauteValidationError(f"Invalid stage member {name!r}") from exc

    def export_artifacts(self, name: str, destination: Path | None = None) -> dict[str, Path]:
        """Rehydrate only ordinary inference/evaluation artifacts, never checkpoint data."""
        from haute.modelling._training_job import (
            evaluation_artifact_filenames,
            model_contract_filename,
        )

        if not self.metadata.get("finalised"):
            raise HauteValidationError(
                "Finalise the applied model before exporting its final artifacts"
            )
        if Path(name).name != name or not name:
            raise HauteValidationError("Invalid export model name")
        output = destination if destination is not None else self.scratch / "export"
        output.mkdir(parents=True, exist_ok=True)
        evaluation = evaluation_artifact_filenames(name)
        names = {
            "model": f"{name}.tboost",
            "feature_contract": model_contract_filename(name),
            **{f"evaluation_{key}": value for key, value in evaluation.items()},
        }
        result = {}
        for kind, member in _EXPORT_MEMBERS.items():
            path = output / names[kind]
            with self.open_member(member) as source, path.open("wb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            result[kind] = path
        return result

    def _load_session(self, key: str) -> tuple[int, Any]:
        """A retained fit's session from its checkpoint parts, read through verified handles."""
        from t_boost import TBoostTrainingSession

        evidence, chain = checkpoint_members(key)
        with self.open_member(evidence) as evidence_part, self.open_member(chain) as chain_part:
            size = _session_size(evidence_part, chain_part)
            session = TBoostTrainingSession.load(
                evidence=evidence_part,
                chain=chain_part,
                expected_data_digest=self.metadata["fits"][key]["data_digest"],
            )
        return size, session

    def session(
        self,
        key: str,
        *,
        point_id: str | None = None,
        path_id: str | None = None,
        raw: bool = False,
    ) -> Any:
        if key not in self.metadata["fits"] or not _FIT_KEY.fullmatch(key):
            raise HauteValidationError(f"Stage checkpoint has no retained fit {key!r}")
        identity = tuple(self._identity(name) for name in checkpoint_members(key))
        cached = self._sessions.pop(key, None)
        if cached is not None:
            self._session_bytes -= cached[1]
        if cached is None or cached[0] != identity:
            size, parent = self._load_session(key)
            cached = (identity, size, parent)
        self._retain(key, cached)
        session = cached[2].fork()
        from haute.modelling._stage_paths import resolve_path

        path = resolve_path(self, path_id) if path_id is not None else self.metadata.get("path")
        if path is not None and not raw:
            from t_boost import TBoostPathFamily

            from haute.modelling._stage_paths import candidate_member, require_point

            point = require_point(path, point_id or path["selected_point"])
            family = TBoostPathFamily.from_candidate_bytes(
                session, self.read_bytes(candidate_member(path["id"], point["id"], key))
            )
            family.apply(session, point["id"])
        return session

    def replace_checkpoint(self, key: str, session: Any) -> None:
        """Write a retained fit's new stage chain from ``session``; its evidence is unchanged.

        The written state becomes the cached parent for the new chain, so a job never
        reloads and re-validates a checkpoint it has just written.
        """
        if key not in self.metadata["fits"] or not _FIT_KEY.fullmatch(key):
            raise HauteValidationError(f"Stage checkpoint has no retained fit {key!r}")
        if session.data_digest != self.metadata["fits"][key]["data_digest"]:
            raise HauteValidationError("Replacement checkpoint belongs to different training data")
        evidence_name, chain_name = checkpoint_members(key)
        chain = self.scratch / f"{uuid.uuid4().hex}.member"
        session.save_chain(chain)
        self.adopt_member(chain_name, chain)
        cached = self._sessions.pop(key, None)
        if cached is not None:
            self._session_bytes -= cached[1]
        with self.open_member(evidence_name) as evidence, chain.open("rb") as written:
            size = _session_size(evidence, written)
        identity = (self._identity(evidence_name), self._identity(chain_name))
        self._retain(key, (identity, size, session.fork()))

    def _retain(self, key: str, cached: tuple[tuple[object, ...], int, Any]) -> None:
        """Keep ``cached`` as the most recent entry when it fits the cache budget."""
        if cached[1] <= _MAX_SESSION_CACHE_BYTES:
            while self._sessions and self._session_bytes + cached[1] > _MAX_SESSION_CACHE_BYTES:
                _, evicted = self._sessions.popitem(last=False)
                self._session_bytes -= evicted[1]
            self._sessions[key] = cached
            self._session_bytes += cached[1]


def _session_size(*parts: IO[bytes]) -> int:
    # Allow for decoded strings, arrays, native banks and binary templates.
    total = 0
    for part in parts:
        with zipfile.ZipFile(part) as archive:
            total += sum(member.file_size for member in archive.infolist())
        part.seek(0)
    return 4 * total


def bundle_members(metadata: Mapping[str, Any], read: Any) -> set[str]:
    """Every member name a revision with *metadata* holds; *read* decodes a member's JSON."""
    from haute.modelling._stage_paths import path_members

    fits = metadata["fits"]
    names = {"source.parquet", "plan.json"}
    if metadata.get("finalised") is True:
        names |= set(_EXPORT_MEMBERS.values())  # a final revision holds no checkpoint parts
    else:
        names |= {name for key in fits for name in checkpoint_members(key)}
    for summary in metadata.get("paths", {}).values():
        members = path_members(summary, fits)
        names |= members
        for name in members:
            if name.endswith(".result"):
                names |= closure(read(name), read)
    return names


def revision_closure(metadata: Mapping[str, Any], result: Mapping[str, Any], read: Any) -> set[str]:
    """Every member name a revision holds.

    That is its bundle members and staged diagnostics, plus, for a stored revision, its
    result envelope's diagnostics and compact `view`. A portable bundle's result is empty.
    The store builds revisions with exactly this closure, and `open_stage_bundle` requires
    it.
    """
    staged = metadata.get("staged")
    if not isinstance(staged, dict):
        raise HauteValidationError("Invalid stage bundle metadata")
    names = bundle_members(metadata, read) | closure({"staged": staged}, read)
    # A stored revision adds its result's diagnostics and its persisted compact view.
    return names | closure(result, read) | {"view"} if result else names


def revision_files(
    bundle: StageBundle, metadata: Mapping[str, Any]
) -> dict[str, Path | ObjectRef | Opener]:
    """The members a new revision of *bundle* with *metadata* holds (see `packed`).

    `metadata.staged` objects are excluded: `_pack` splits them from the metadata itself.
    """
    return {name: bundle.packed(name) for name in bundle_members(metadata, bundle.read_json)}


@contextmanager
def open_stage_bundle(
    source: str | Path | InPlaceRevision,
    *,
    scratch_dir: str | None = None,
    max_bytes: int = _LIMIT,
) -> Iterator[StageBundle]:
    """Open a revision: a portable `.hstage` (extracted, verified) or one read in place.

    In place means a store revision (`StoredRevision`) or a delta archive over its parent
    (`DeltaRevision`), read through verified handles. Their holder keeps the store revision
    rooted (a pin, or the running-job record) while the bundle is open.
    """
    if scratch_dir is not None:
        Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="haute_stage_", dir=scratch_dir) as temporary:
        root = Path(temporary)
        if isinstance(source, (str, Path)):
            read_archive = StageArchive.read(Path(source))
            archive: StageArchive | None = read_archive
            in_place: InPlaceRevision | None = None
            manifest = read_archive.manifest
        else:
            archive, in_place, manifest = None, source, source.manifest
        if sum(ref.size for ref in manifest.objects.values()) > max_bytes:
            raise HauteValidationError("Invalid stage bundle members or expanded size")
        metadata = dict(manifest.metadata)
        fits = metadata.get("fits")
        if (
            not isinstance(fits, dict)
            or not fits
            or len(fits) > 11
            or any(not _FIT_KEY.fullmatch(key) for key in fits)
        ):
            raise HauteValidationError("Invalid stage bundle fit identities")
        paths = metadata.get("paths", {})
        if not isinstance(paths, dict) or set(paths) - {"pruned", "banded", "smoothed"}:
            raise HauteValidationError("Invalid stage path families")
        for stage, path_summary in paths.items():
            if not isinstance(path_summary, dict) or path_summary.get("stage") != stage:
                raise HauteValidationError("Stage path identity mismatch")
        if metadata.get("path") is not None:
            path_summary = metadata["path"]
            if paths.get(path_summary.get("stage")) != path_summary:
                raise HauteValidationError("Selected path is not a retained family")
        files: dict[str, Path] | None = None
        opener: Callable[[str], AbstractContextManager[IO[bytes]]]
        if archive is not None:
            files = extract_archive(archive, root / "members")
            extracted = files

            def opener(name: str) -> AbstractContextManager[IO[bytes]]:
                return extracted[name].open("rb")

        else:
            assert in_place is not None
            opener = in_place.open

        def read(name: str) -> Any:
            if name not in manifest.objects:
                raise HauteValidationError(f"Stage bundle lacks member {name!r}")
            with opener(name) as handle:
                data = handle.read()
            try:
                return strict_json_loads(data)
            except (ValueError, UnicodeError) as exc:
                raise HauteValidationError(f"Invalid stage member {name!r}") from exc

        if set(manifest.objects) != revision_closure(metadata, manifest.result, read):
            raise HauteValidationError("Unknown or missing stage bundle member")
        metadata["staged"] = hydrate({"staged": metadata["staged"]}, read)["staged"]
        if files is not None:
            # Readers dispatch on the file extension: name the extracted rows as Parquet.
            # Only a private extracted copy is linked, never a store object.
            rows = root / "source.parquet"
            os.link(files["source.parquet"], rows)
            files["source.parquet"] = rows
        plan = EvaluationPlan.from_plain_data(read("plan.json"))
        if plan.source_sha256 != manifest.objects["source.parquet"].sha256:
            raise HauteValidationError("Stage bundle source disagrees with its evaluation plan")
        kwargs = metadata.get("job_kwargs")
        if (
            not isinstance(kwargs, dict)
            or fit_config_digest(kwargs) != metadata.get("fit_config_digest")
            or kwargs.get("purpose") != "exploration"
        ):
            raise HauteValidationError("Stage bundle fit configuration mismatch")
        bundle = StageBundle(root, manifest, opener, files, metadata, plan)
        try:
            yield bundle
        finally:
            bundle._sessions.clear()
            bundle._session_bytes = 0


class StageRuntime:
    """Per-worker state: intermediate files are owned here until the bundle is complete."""

    def __init__(
        self,
        *,
        bundle: StageBundle | None = None,
        operation: str = "full",
        scratch_dir: str | None = None,
        candidate_id: str | None = None,
    ) -> None:
        self.candidate_id = candidate_id
        self.metadata: dict[str, Any] = {}
        self.path_loss: float | None = None
        if operation not in ("full", "pruned", "banded", "smoothed"):
            raise HauteValidationError("Unknown stage operation")
        if bundle is None and operation != "full":
            raise HauteValidationError("This stage requires a saved parent build")
        self.bundle, self.operation = bundle, operation
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        if bundle is None:
            if scratch_dir is not None:
                Path(scratch_dir).mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix="haute_stages_", dir=scratch_dir)
            self.root = Path(self._temporary.name)
        else:
            self.root = bundle.scratch
        self.fits: dict[str, dict[str, Any]] = {}
        self.summary: dict[str, Any] = {}
        self.selected_params = (
            copy.deepcopy(bundle.metadata.get("selected_params")) if bundle is not None else None
        )
        self.oof_rows = 0
        self._oof_files: dict[int, Path] = {}
        self._oof_names: tuple[str, str] | None = None

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()

    def _split_key(self, job: Any, prepared: Any) -> str | None:
        if (
            self.candidate_id is None
            or self.bundle is None
            or prepared.owns_tmp
            or Path(prepared.data_path) != self.bundle.source
            or job.evaluation_plan is None
        ):
            return None
        plan = job.evaluation_plan
        return hashlib.sha256(
            _json(
                [
                    plan.to_plain_data(),
                    job.evaluation_fit_index,
                    job.purpose,
                    job.refit_on_development,
                    job.task,
                    job.target,
                    job.positive_class,
                    prepared.total_rows,
                    prepared.target_null_count,
                ]
            )
        ).hexdigest()

    def evaluation_plan(self, prepared: Any) -> EvaluationPlan | None:
        """The bundle's canonical plan, when the job evaluates the bundle's own source.

        The bundle verified that plan against its source when it opened, and the job's
        evaluation settings are the bundle's (`finalize` enforces the fit configuration), so
        rebuilding it would reproduce it exactly.
        """
        if (
            self.bundle is None
            or prepared.owns_tmp
            or Path(prepared.data_path) != self.bundle.source
        ):
            return None
        return self.bundle.plan

    def cached_split(self, job: Any, prepared: Any) -> _SplitResult | None:
        key = self._split_key(job, prepared)
        if key is None or self.bundle is None or key not in self.bundle.path_splits:
            return None
        split, labels = self.bundle.path_splits[key]
        job._class_labels = labels
        return copy.copy(split)

    def retain_split(self, job: Any, prepared: Any, split: _SplitResult) -> _SplitResult:
        key = self._split_key(job, prepared)
        if key is None or self.bundle is None:
            return split
        size = Path(split.split_path).stat().st_size
        if self.bundle.path_split_bytes + size > _MAX_PATH_SPLIT_BYTES:
            return split
        destination = self.bundle.scratch / f"path-split-{key}.parquet"
        try:
            shutil.move(split.split_path, destination)
        finally:
            Path(split.split_path).unlink(missing_ok=True)
        shared = copy.copy(split)
        shared.split_path, shared.owns_tmp = str(destination), False
        self.bundle.path_splits[key] = (shared, job._class_labels)
        self.bundle.path_split_bytes += size
        return copy.copy(shared)

    def adapter_kwargs(self, job: Any) -> dict[str, Any]:
        if self.bundle is None:
            return {}
        session = self.bundle.session(_fit_key(job), point_id=self.candidate_id)
        if self.candidate_id is not None:
            from haute.modelling._stage_paths import require_point

            point = require_point(self.bundle.metadata["path"], self.candidate_id)
            selected = (
                self.bundle.metadata["path"]["parent"] if point["identity"] else self.operation
            )
            return {"stage_session": session, "prepared_stage": selected}
        return {"stage_session": session, "stage_operation": self.operation}

    def retain_trial(self, trial: StageRuntime, params: Mapping[str, Any]) -> None:
        """Replace the retained best trial; never accumulate every candidate's banks."""
        retained = ("*.evidence", "*.chain", "*.oof.parquet")
        for pattern in retained:
            for path in self.root.glob(pattern):
                path.unlink()
        for path in (path for pattern in retained for path in trial.root.glob(pattern)):
            shutil.move(str(path), self.root / path.name)
        self.fits = copy.deepcopy(trial.fits)
        self.summary = copy.deepcopy(trial.summary)
        self._oof_names = trial._oof_names
        self._oof_files = {index: self.root / path.name for index, path in trial._oof_files.items()}
        self.selected_params = copy.deepcopy(dict(params))

    def record_validation(self, job: Any, frame: pl.DataFrame, predictions: Any) -> None:
        """Own matching held-out rows, without keeping every fold frame resident."""
        from haute.modelling._split import PARTITION_VALIDATION

        if job.evaluation.validation["method"] == "none":
            return
        index = job.evaluation_fit_index
        if index is None or index in self._oof_files:
            raise HauteValidationError("Duplicate or missing OOF fit identity")
        rows = np.flatnonzero(job.evaluation_plan.selection_mask(index) == PARTITION_VALIDATION)
        predictions = np.asarray(predictions)
        if predictions.ndim != 1 or len(predictions) != len(rows) or len(frame) != len(rows):
            raise HauteValidationError("OOF predictions do not match their validation rows")
        if not np.isfinite(predictions).all():
            raise HauteValidationError("OOF predictions must be finite")
        if self._oof_names is None:
            names = []
            for base in ("_haute_oof_row", "_haute_oof_prediction"):
                while base in frame.columns:
                    base += "_"
                names.append(base)
            self._oof_names = (names[0], names[1])
        row_name, prediction_name = self._oof_names
        path = self.root / f"validation-{index}.oof.parquet"
        frame.with_columns(
            pl.Series(row_name, rows), pl.Series(prediction_name, predictions)
        ).write_parquet(path)
        self._oof_files[index] = path

    def oof_diagnostics(self, job: Any, execution_context: Any) -> tuple[pl.DataFrame, np.ndarray]:
        """Pool the canonical validation windows once, before any chart binning."""
        from haute.modelling._split import PARTITION_VALIDATION
        from haute.modelling._training_job import _training_streaming_collect

        plan = job.evaluation_plan
        if set(self._oof_files) != set(range(len(plan.validation_fits))) or self._oof_names is None:
            raise HauteValidationError("OOF diagnostics need every validation fit")
        row_name, prediction_name = self._oof_names
        frame = _training_streaming_collect(
            pl.scan_parquet([self._oof_files[index] for index in sorted(self._oof_files)]).sort(
                row_name
            ),
            stage_name="stage_oof_diagnostics_materialise",
            execution_context=execution_context,
        )
        expected = np.sort(
            np.concatenate(
                [
                    np.flatnonzero(plan.selection_mask(index) == PARTITION_VALIDATION)
                    for index in sorted(self._oof_files)
                ]
            )
        )
        if len(np.unique(expected)) != len(expected) or not np.array_equal(
            frame[row_name].to_numpy(), expected
        ):
            raise HauteValidationError("OOF diagnostics have duplicate, missing or wrong-fold rows")
        self.oof_rows = len(frame)
        return frame.drop(row_name, prediction_name), frame[prediction_name].to_numpy()

    def remember(self, job: Any, model: Any) -> None:
        session, key = model.training_session, _fit_key(job)
        if session is None:
            raise HauteValidationError("Stage job did not retain its training session")
        if self.candidate_id is None:
            if self.bundle is None:
                session.save_evidence(self.root / f"{key}.evidence")
                session.save_chain(self.root / f"{key}.chain")
            else:
                self.bundle.replace_checkpoint(key, session)
        self.remember_session(key, session, model.selected_stage)

    def remember_session(self, key: str, session: Any, selected: str) -> None:
        """Record stage identity separately from row evaluation (also used by exact aliases)."""
        self.fits[key] = {"data_digest": session.data_digest}
        self.summary = {
            "selected_stage": selected,
            "stages": session.stages,
            "recipes": {name: session.recipe(name) for name in session.stages},
            "pruning_path": session.pruning_path,
            "path_unavailable_reason": session.path_unavailable_reason,
            "recommendation": session.pruning_selection()
            if session.pruning_path is not None
            else None,
        }

    def record_diagnostics(self, job: Any, frame: pl.DataFrame, predictions: Any) -> None:
        if self.candidate_id is None:
            return
        from haute.modelling._metrics import compute_metrics
        from haute.modelling._stage_paths import path_metric

        metric = path_metric(job.task, job.loss_function)
        self.path_loss = compute_metrics(
            frame[job.target].to_numpy(),
            predictions,
            frame[job.weight].to_numpy() if job.weight else None,
            [metric],
            variance_power=job.variance_power,
        )[metric]

    def finalize(self, job: Any, result: Any, source: str, plan: EvaluationPlan) -> Path | None:
        if not self.fits or not self.summary:
            raise HauteValidationError("Stage job did not capture a fitted model")
        selected = self.summary["selected_stage"]
        config = job.stages.to_plain_data()
        if self.bundle is not None and self.operation != "pruned":
            config["path"] = copy.deepcopy(self.bundle.metadata["staged"]["configuration"]["path"])
        config["applied_recipe"] = self.summary["recipes"][selected]
        uses_pruning = any(op["stage"] == "pruned" for op in config["applied_recipe"])
        if uses_pruning and self.operation == "pruned":
            config["applied_path"] = copy.deepcopy(config["path"])
        elif uses_pruning and self.bundle is not None:
            previous = self.bundle.metadata["staged"]["configuration"]
            config["applied_path"] = copy.deepcopy(
                self.bundle.metadata["staged"]["stages"]["pruned"].get("path_settings")
                or previous.get("applied_path")
                or previous["path"]
            )
        else:
            config["applied_path"] = None
        job.stages = StagedConfig.from_plain_data(config)
        kwargs = _kwargs(job)
        digest = fit_config_digest(kwargs)
        if self.bundle is not None and digest != self.bundle.metadata["fit_config_digest"]:
            raise HauteValidationError("Stage fit configuration changed; build the GBM again")
        identity = hashlib.sha256(
            _json(
                {
                    "source": plan.source_sha256,
                    "plan": result.evaluation["plan_sha256"],
                    "partition": result.diagnostics_set,
                    "semantics": {
                        name: getattr(job, name)
                        for name in (
                            "target",
                            "weight",
                            "offset",
                            "task",
                            "positive_class",
                            "loss_function",
                            "variance_power",
                        )
                    },
                }
            )
        ).hexdigest()
        previous = self.bundle.metadata["staged"]["stages"] if self.bundle is not None else {}
        snapshots = {
            name: value
            for name, value in previous.items()
            if name in self.summary["stages"]
            and value["revision"] == self.summary["stages"][name]["revision"]
        }
        snapshots[selected] = {
            **self.summary["stages"][selected],
            "recipe": self.summary["recipes"][selected],
            **(
                {"path_settings": copy.deepcopy(config["applied_path"])}
                if selected == "pruned"
                else {}
            ),
            # Shared, not copied: after finalisation a result's diagnostics are only read
            # (serialised, split or returned), so the snapshot and the result cannot diverge.
            **{name: getattr(result, name) for name in _DIAGNOSTICS},
            **(
                {"table_summary": table_summary(result.tboost_tables)}
                if result.tboost_tables
                else {}
            ),
        }
        result.staged = {
            "schema_version": 1,
            **({"diagnostic_scope": "path"} if self.candidate_id is not None else {}),
            "selected_stage": selected,
            "configuration": config,
            "diagnostics_basis": "out_of_fold" if self.oof_rows else "partition",
            "reference_fit": "development" if job.refit_on_development else "validation-0",
            "evaluation_identity": identity,
            "stages": snapshots,
            "pruning_path": self.summary["pruning_path"],
            "path_unavailable_reason": self.summary["path_unavailable_reason"],
            "recommendation": self.summary["recommendation"],
            "fit_config_digest": digest,
        }
        metadata = {
            "job_kwargs": kwargs,
            "fit_config_digest": digest,
            "fits": self.fits,
            "selected_params": self.selected_params,
            "staged": result.staged,
        }
        self.metadata = metadata
        if self.candidate_id is not None:
            return None
        files: dict[str, Path | bytes | Opener | ObjectRef] = {
            # A resumed stage inherits its parent's rows rather than its scratch copy.
            "source.parquet": self.bundle.packed("source.parquet")
            if self.bundle is not None
            else Path(source),
            "plan.json": Path(result.evaluation["plan_path"]),
        }
        for key in self.fits:
            for name in checkpoint_members(key):
                files[name] = (
                    self.bundle.packed(name)
                    if self.bundle is not None
                    else self.root / f"{key}.{name.rsplit('/', 1)[1]}"
                )
        destination = Path(job.output_dir) / f"{job.name}.hstage"
        _pack(destination, files, metadata)
        result.stage_checkpoint_path = str(destination)
        return destination


def run_stage_job(
    checkpoint: str | Path | InPlaceRevision,
    *,
    operation: str,
    stages: Mapping[str, Any],
    output_dir: str,
    expected_fit_config: str | None = None,
    prepare_path: bool = False,
    name: str | None = None,
    scratch_dir: str | None = None,
    **run_kwargs: Any,
) -> tuple[Any, Any]:
    """Resume a stage inside the same evaluator used by ordinary and scripted training."""
    from haute.modelling._training_job import TrainingJob

    with open_stage_bundle(checkpoint, scratch_dir=scratch_dir) as bundle:
        if (
            expected_fit_config is not None
            and expected_fit_config != bundle.metadata["fit_config_digest"]
        ):
            raise HauteValidationError("Stage fit configuration changed; build the GBM again")
        config = StagedConfig.from_plain_data(stages)
        if prepare_path and operation != "finalise":
            from haute.modelling._stage_paths import run_path_job

            return run_path_job(
                bundle,
                operation=operation,
                config=config,
                output_dir=output_dir,
                name=name,
                scratch_dir=scratch_dir,
                **run_kwargs,
            )
        kwargs = {
            **bundle.metadata["job_kwargs"],
            "data": str(bundle.source),
            "stages": config.to_plain_data(),
            "output_dir": output_dir,
            "scratch_dir": scratch_dir,
        }
        if name is not None:
            kwargs["name"] = name
        if operation == "finalise":
            # Finalisation freezes the applied recipe, independently of pending controls.
            kwargs["stages"] = copy.deepcopy(bundle.metadata["staged"]["configuration"])
            kwargs["params"] = bundle.metadata.get("selected_params") or kwargs["params"]
            kwargs["tuning"] = None
            kwargs["purpose"] = "final"
            kwargs["refit_on_development"] = True
            job = TrainingJob(**kwargs)
            result = job.run(**run_kwargs)
            result.staged = copy.deepcopy(bundle.metadata["staged"])
            metadata = {
                **{
                    key: value
                    for key, value in bundle.metadata.items()
                    if key not in {"path", "paths"}
                },
                "finalised": True,
                "final_job_kwargs": {
                    **kwargs,
                    "data": None,
                    "scratch_dir": None,
                    "output_dir": None,
                },
            }
            from haute.modelling._training_job import model_contract_filename

            # The final revision's closure: the source, the final plan, the exports and the
            # diagnostics. It keeps no checkpoint parts; nothing resumes from a final model.
            files: dict[str, Path | bytes | Opener | ObjectRef] = {
                "source.parquet": bundle.packed("source.parquet")
            }
            assert result.evaluation is not None
            files.update(
                {
                    "export/model.native": Path(result.model_path),
                    "export/feature-contract.json": Path(result.model_path).parent
                    / model_contract_filename(job.name),
                    "plan.json": Path(result.evaluation["plan_path"]),
                    "export/evaluation-results.json": Path(result.evaluation["results_path"]),
                    "export/evaluation-report.json": Path(result.evaluation["report_path"]),
                }
            )
            destination = Path(output_dir) / f"{job.name}.hstage"
            _pack(destination, files, metadata)
            result.stage_checkpoint_path = str(destination)
            return job, result
        job = TrainingJob(**kwargs)
        runtime = StageRuntime(bundle=bundle, operation=operation)
        job._stage_runtime = runtime
        try:
            result = job.run(**run_kwargs)
            return job, result
        finally:
            runtime.close()
