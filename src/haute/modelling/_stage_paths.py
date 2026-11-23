"""Bounded path preparation and exact replay through retained canonical evaluation fits."""

from __future__ import annotations

import copy
import hashlib
import math
import re
import shutil
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from haute.errors import HauteValidationError
from haute.modelling._stage_diagnostics import hydrate, split
from haute.modelling._stage_objects import RevisionSource, StageArchive, strict_json_loads
from haute.modelling._staged_config import StagedConfig
from haute.modelling._staged_runtime import (
    StageBundle,
    StageRuntime,
    _json,
    _pack,
    revision_files,
)

_POINT = re.compile(r"(?:unchanged|point-(?:[1-9]|1[0-6]))\Z")
_SECTIONS = {"pruned": "pruning", "banded": "banding", "smoothed": "smoothing"}


def path_metric(task: str, loss: str | None) -> str:
    from haute.modelling._train_config import default_metrics

    return default_metrics(task, loss_function=loss)[-1]


def require_point(path: Mapping[str, Any], point_id: object) -> dict[str, Any]:
    if not isinstance(point_id, str) or not _POINT.fullmatch(point_id):
        raise HauteValidationError("Invalid path point identity")
    for point in path["points"]:
        if point["id"] == point_id:
            return dict(point)
    raise HauteValidationError("This point is not part of the prepared path")


def candidate_member(path_id: str, point_id: str, key: str) -> str:
    """A path point's retained t-boost candidate for one fit."""
    return f"path/{path_id}/{point_id}/{key}.candidate"


def result_member(path_id: str, point_id: str) -> str:
    """A path point's stored result envelope (diagnostics are shared objects)."""
    return f"path/{path_id}/{point_id}.result"


def path_members(path: Any, fits: Mapping[str, Any]) -> set[str]:
    if (
        not isinstance(path, dict)
        or path.get("stage") not in _SECTIONS
        or path.get("parent") not in ("full", "pruned", "banded")
        or not isinstance(path.get("points"), list)
        or not 1 <= len(path["points"]) <= 17
    ):
        raise HauteValidationError("Invalid stage path envelope")
    ids: list[Any] = [point.get("id") for point in path["points"] if isinstance(point, dict)]
    if len(ids) != len(path["points"]) or len(ids) != len(set(ids)):
        raise HauteValidationError("Invalid stage path point identities")
    for point_id in ids:
        require_point(path, point_id)
    require_point(path, path.get("selected_point"))
    if not isinstance(path.get("id"), str) or not re.fullmatch(r"[a-f0-9]{64}", path["id"]):
        raise HauteValidationError("Invalid stage path identity")
    return {
        *(result_member(path["id"], point_id) for point_id in ids),
        *(candidate_member(path["id"], point_id, key) for point_id in ids for key in fits),
    }


def resolve_path(bundle: StageBundle, path_id: str) -> dict[str, Any]:
    for path in bundle.metadata.get("paths", {}).values():
        if path["id"] == path_id:
            return dict(path)
    raise HauteValidationError("The prepared path has changed")


def _valid_paths(paths: Mapping[str, Any], staged: Mapping[str, Any]) -> dict[str, Any]:
    return {
        stage: copy.deepcopy(path)
        for stage, path in paths.items()
        if staged["stages"].get(path["parent"], {}).get("revision")
        == path["parent_revisions"][path["reference_fit"]]
    }


def _point_config(config: StagedConfig, stage: str, point: Mapping[str, Any]) -> StagedConfig:
    value = config.to_plain_data()
    if point["identity"]:
        value["enabled"] = {
            **dict.fromkeys(_SECTIONS, True),
            **value.get("enabled", {}),
            stage: False,
        }
    else:
        section = _SECTIONS[stage]
        value[section] = {
            **({"parent": value[section]["parent"]} if "parent" in value[section] else {}),
            **point["settings"],
        }
    return StagedConfig.from_plain_data(value)


def _recommend(
    points: list[dict[str, Any]], stage: str, selection: Mapping[str, float], evidence: str
) -> str | None:
    if evidence == "development":
        return None
    feasible = [p for p in points if p["status"] != "rejected" and p["loss"] is not None]
    parent = points[0]["loss"]
    best = min(p["loss"] for p in feasible)
    tolerance = max(
        selection["absolute_tolerance"],
        selection["relative_tolerance"] * max(abs(parent), abs(best)),
    )
    feasible = [p for p in feasible if p["loss"] <= min(parent, best) + tolerance]
    if not feasible:
        return "unchanged"

    def complexity(point: dict[str, Any]) -> tuple[float, float, int, str]:
        if stage == "smoothed":
            amount = (
                0.0
                if point["status"] in ("identity", "unchanged")
                else point["diagnostics"]["strength"]
            )
            return -float(amount), point["loss"], 0 if point["identity"] else 1, point["id"]
        diagnostic = point["diagnostics"]
        count = diagnostic["tables"] if stage == "pruned" else diagnostic["interaction_cells"]
        # A factored parent has not yet acquired a product grid. Do not describe its
        # absence of dense cells as a simpler zero-cell model.
        if stage == "banded" and diagnostic["factored_interactions"]:
            count = math.inf
        return float(count), point["loss"], 0 if point["identity"] else 1, point["id"]

    return str(min(feasible, key=complexity)["id"])


def _reuse_evaluation(
    bundle: StageBundle, job: Any, runtime: StageRuntime, point: Mapping[str, Any], prior: Any
) -> Any:
    """Reuse canonical diagnostics only after every retained inference bank matches."""
    from haute.modelling._tboost import TBoostModel

    result = copy.deepcopy(prior)
    reference = bundle.metadata["staged"]["reference_fit"]
    session = bundle.session(reference, raw=True)
    acquired = session.apply(bundle.candidate(reference, session, point_id=point["id"]))
    selected = acquired.info.stage
    runtime.fits = copy.deepcopy(bundle.metadata["fits"])
    runtime.remember_session(reference, session, selected, job.stages)
    runtime.oof_rows = int(prior.staged["diagnostics_basis"] == "out_of_fold")
    runtime.finalize(job, result, str(bundle.source), bundle.plan)
    # Alias exports carry the alias's own policy and stage revision, even though
    # inference is identical. Evaluation files and feature contracts are unchanged.
    output = Path(job.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for source in Path(prior.model_path).parent.iterdir():
        if source.is_file():
            shutil.copyfile(source, output / source.name)
    model = TBoostModel.load(prior.model_path)
    model.estimator = acquired.model
    model.training_session, model.selected_stage = session, selected
    result.model_path = str(output / Path(prior.model_path).name)
    model.save(Path(result.model_path))
    for field in ("plan_path", "results_path", "report_path"):
        result.evaluation[field] = str(output / Path(result.evaluation[field]).name)
    if result.tuning is not None:
        for field in ("plan_path", "trials_path", "report_path"):
            result.tuning[field] = str(output / Path(result.tuning[field]).name)
    return result


def _pruning_budgets(
    bundle: StageBundle, config: StagedConfig, fit_keys: list[str], cancel: Any
) -> list[int]:
    """The reference fit's schedule, raised to every retained fit's mandatory-table floor.

    Each fit's configured parent is written here once; the family loop reuses it.
    """
    selection = config.operation("pruned")
    schedule: list[int] = []
    minimum = 0
    for index, key in enumerate(fit_keys):
        cancel()
        # Each fit is read before its own replacement, so the previous envelope still
        # applies here; it is dropped once every parent is flattened. The replacement
        # chain also stores this fit's evidence for the selection's path settings.
        session = bundle.session(key)
        evidence = session.pruning_path(selection.path)
        if evidence is None:
            raise HauteValidationError(session.pruning_unavailable_reason(selection.path))
        minimum = max(minimum, evidence["table_counts"][0])
        if index == 0:
            schedule = session.pruning_budgets(selection)
        bundle.replace_checkpoint(key, session)
        del session
    bundle.metadata.pop("path", None)
    return sorted({max(minimum, count) for count in schedule})


def _write_point_result(bundle: StageBundle, result: Any, path_id: str, point_id: str) -> None:
    """Store a point result: its own diagnostics, without any path family summary.

    ``staged.paths`` is derived on every read from the families the artifact retains (see
    `_resolve_point_paths`), so each summary is stored once per artifact and a point can
    never refer to a family that was rebuilt or invalidated after it was written. The
    diagnostics become shared bundle objects (`_stage_diagnostics.split`).
    """
    if result.staged is None:
        raise HauteValidationError("Path candidate produced no staged result")
    staged = {key: value for key, value in result.staged.items() if key != "paths"}
    staged["prepared_path"] = path_id
    stored = {
        field.name: staged if field.name == "staged" else getattr(result, field.name)
        for field in fields(result)
    }
    # Each object is encoded once, by the same strict canonical JSON that reads decode.
    # This equals a JSON round trip because producers use string keys and list rows
    # (tuples encode as lists; non-finite numbers are refused).
    envelope = split(stored, bundle.write_member, names=bundle.names)
    bundle.write_member(result_member(path_id, point_id), _json(envelope))


def _resolve_point_paths(
    stored: Any, retained: Mapping[str, Any], path_id: str, point_id: str
) -> Any:
    """A hydrated stored point result; its families are those retained and valid for it."""
    from haute.modelling._training_job import TrainResult

    staged = stored.get("staged") if isinstance(stored, dict) else None
    if not isinstance(staged, dict) or "paths" in staged:
        raise HauteValidationError(
            "This path was prepared by an older version of Haute; prepare it again"
        )
    if staged.get("prepared_path") != path_id:
        raise HauteValidationError("Path point result belongs to a different path")
    own = next((path for path in retained.values() if path["id"] == path_id), None)
    if own is None:
        raise HauteValidationError("The prepared path has changed")
    # Another family belongs to this point exactly when its parent is this point's stage.
    staged["paths"] = {
        **_valid_paths(retained, staged),
        own["stage"]: {**copy.deepcopy(own), "selected_point": point_id},
    }
    return TrainResult(**stored)


def _path_request(operation: Any, budgets: list[int] | None) -> Any:
    """The t-boost path request for a draft operation (budgets: the cross-fit schedule)."""
    from t_boost import Band, BandPath, Prune, PrunePath, SmoothPath

    if isinstance(operation, Prune):
        return PrunePath(operation, None if budgets is None else tuple(budgets))
    if isinstance(operation, Band):
        return BandPath(operation)
    return SmoothPath(operation)


def run_path_job(
    bundle: StageBundle,
    *,
    operation: str,
    config: StagedConfig,
    output_dir: str,
    name: str | None,
    scratch_dir: str | None,
    **run_kwargs: Any,
) -> tuple[Any, Any]:
    from haute.modelling._training_job import TrainingJob

    op = config.operation(operation)
    parent = op.parent
    original = copy.deepcopy(bundle.metadata)
    reference = original["staged"]["reference_fit"]
    fit_keys = [reference, *(key for key in original["fits"] if key != reference)]
    points: list[dict[str, Any]] = []
    fold_status: dict[str, list[dict[str, str]]] = {}
    outcomes: dict[str, list[str]] = {}
    family_ids = {}
    parent_revisions = {}
    # Candidates wait in scratch until the family identity is known.
    pending: dict[tuple[str, str], Path] = {}
    cancel = run_kwargs.get("check_cancelled", lambda: None)
    pruning_budgets = (
        _pruning_budgets(bundle, config, fit_keys, cancel) if operation == "pruned" else None
    )
    # A previous path's selected candidate is still an envelope over its parent checkpoint.
    flatten = bundle.metadata.get("path") is not None
    for key in fit_keys:
        cancel()
        session = bundle.session(key)
        config.require_parent(parent, session.stage(parent).recipe)
        family = session.prepare_path(_path_request(op, pruning_budgets))
        metadata = [candidate.to_plain_data() for candidate in family.candidates]
        if key == reference:
            points = metadata
        if [p["id"] for p in metadata] != [p["id"] for p in points]:
            raise HauteValidationError("Retained fits produced different logical path points")
        family_ids[key] = family.path_id
        parent_revisions[key] = session.stages[parent].revision
        # Flatten a previous path envelope once; all new candidates share this parent.
        if flatten:
            bundle.replace_checkpoint(key, session)
        for candidate in family.candidates:
            point_id = candidate.point_id
            outcomes.setdefault(point_id, []).append(candidate.outcome_id)
            destination = bundle.scratch / f"candidate-{point_id}-{key}.member"
            destination.write_bytes(candidate.to_bytes())
            pending[(point_id, key)] = destination
            fold_status.setdefault(point_id, []).append(dict(fit=key, status=candidate.status))
        del family, session
    path_id = hashlib.sha256(
        _json(
            [
                family_ids,
                config.to_plain_data()["selection"],
                original["staged"]["evaluation_identity"],
            ]
        )
    ).hexdigest()
    for (point_id, key), source in pending.items():
        bundle.adopt_member(candidate_member(path_id, point_id, key), source)
    evidence = (
        "development"
        if original["job_kwargs"]["evaluation"]["validation"]["method"] == "none"
        else "out_of_fold"
        if original["staged"]["diagnostics_basis"] == "out_of_fold"
        else "validation"
    )
    path = dict(
        id=path_id,
        parent_revisions=parent_revisions,
        family_ids=family_ids,
        stage=operation,
        parent=parent,
        points=points,
        selected_point="unchanged",
        suggested_point=None,
        evidence=evidence,
        metric=path_metric(original["job_kwargs"]["task"], original["job_kwargs"]["loss_function"]),
        evaluation_identity=original["staged"]["evaluation_identity"],
        controls={
            name: value
            for name, value in config.to_plain_data()[_SECTIONS[operation]].items()
            if name != "parent"
        },
        selection=config.to_plain_data()["selection"],
        reference_fit=reference,
    )
    bundle.metadata["path"] = path
    bundle.metadata["paths"] = {**original.get("paths", {}), operation: path}
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    baseline = None
    evaluated: dict[tuple[str, ...], dict[str, Any]] = {}
    for point in points:
        cancel()
        point_id = point["id"]
        kwargs = {
            **original["job_kwargs"],
            "data": str(bundle.source),
            "stages": _point_config(config, operation, point).to_plain_data(),
            "output_dir": str(root / point_id),
            "scratch_dir": scratch_dir,
        }
        if name is not None:
            kwargs["name"] = name
        job = TrainingJob(**kwargs)
        runtime = StageRuntime(bundle=bundle, operation=operation, candidate_id=point_id)
        job._stage_runtime = runtime
        outcome = tuple(outcomes[point_id])
        equivalent = evaluated.get(outcome)
        if equivalent is None:
            result = job.run(**run_kwargs)
            evaluated[outcome] = point
        else:
            point["diagnostics"]["equivalent_to"] = equivalent["id"]
            result = _reuse_evaluation(
                bundle, job, runtime, point, read_path_result(bundle, path_id, equivalent["id"])
            )
            runtime.path_loss = equivalent["loss"]
        if runtime.path_loss is None or not math.isfinite(runtime.path_loss):
            raise HauteValidationError("Path candidate has no finite canonical diagnostic loss")
        if baseline is None:
            baseline = runtime.path_loss
        point["loss"] = runtime.path_loss
        point["loss_delta"] = runtime.path_loss - baseline
        point["relative_loss_delta"] = (
            (runtime.path_loss - baseline) / abs(baseline) if abs(baseline) > 1e-12 else None
        )
        point["fold_status"] = fold_status[point_id]
        point["reference_status"] = point["status"]
        if any(item["status"] == "rejected" for item in fold_status[point_id]):
            point["status"] = "rejected"
        elif any(item["status"] == "changed" for item in fold_status[point_id]):
            point["status"] = "changed"
        _write_point_result(bundle, result, path_id, point_id)
        job._stage_runtime = None
        runtime.close()
        del result, runtime, job
    suggested = _recommend(points, operation, path["selection"], evidence)
    # Development-only evidence can be inspected, but never earns a validation recommendation.
    selected = suggested or next(
        (
            p["id"]
            for p in points
            if p["status"] == "changed"
            and p["diagnostics"].get("strength", p["diagnostics"].get("budget_fraction")) == 1
        ),
        "unchanged",
    )
    path.update(suggested_point=suggested, selected_point=selected)
    selected_result = read_path_result(bundle, path_id, selected)
    selected_metadata = {
        **original,
        "path": path,
        "staged": selected_result.staged,
        "paths": selected_result.staged["paths"],
        "job_kwargs": {**original["job_kwargs"], "stages": selected_result.staged["configuration"]},
    }
    selected_kwargs = {
        **selected_metadata["job_kwargs"],
        "data": str(bundle.source),
        "output_dir": str(root / selected),
        "scratch_dir": scratch_dir,
    }
    if name is not None:
        selected_kwargs["name"] = name
        selected_metadata["job_kwargs"]["name"] = name
    selected_job = TrainingJob(**selected_kwargs)
    # The supervised worker publishes one flat artifact set. Other candidate exports
    # are temporary; diagnostic JSON and compact deltas are already in the envelope.
    selected_output = root / selected
    for source in selected_output.iterdir():
        if source.is_file():
            shutil.copyfile(source, root / source.name)
    selected_result.model_path = str(root / Path(selected_result.model_path).name)
    for field in ("plan_path", "results_path", "report_path"):
        selected_result.evaluation[field] = str(root / Path(selected_result.evaluation[field]).name)
    if selected_result.tuning is not None:
        for field in ("plan_path", "trials_path", "report_path"):
            selected_result.tuning[field] = str(root / Path(selected_result.tuning[field]).name)
    selected_job.output_dir = str(root)
    destination = root / f"{selected_job.name}.hstage"
    _pack(destination, revision_files(bundle, selected_metadata), selected_metadata)
    selected_result.stage_checkpoint_path = str(destination)
    return selected_job, selected_result


def read_path_result(bundle: StageBundle, path_id: str, point_id: str) -> Any:

    path = resolve_path(bundle, path_id)
    require_point(path, point_id)
    stored = hydrate(bundle.read_json(result_member(path_id, point_id)), bundle.read_json)
    return _resolve_point_paths(
        stored,
        bundle.metadata.get("paths", {}),
        path_id,
        point_id,
    )


def read_path_artifact(artifact: Path | RevisionSource, path_id: str, point_id: str) -> Any:
    """Read one point's result from a revision: a portable bundle or a pinned store revision.

    Inspection reads only that result and its diagnostic objects, never parent rows,
    boosters or other candidate banks.
    """

    revision = StageArchive.read(artifact) if isinstance(artifact, Path) else artifact
    objects = revision.manifest.objects
    paths = revision.manifest.metadata.get("paths", {})
    summary = next((p for p in paths.values() if p["id"] == path_id), None)
    if summary is None:
        raise HauteValidationError("The prepared path has changed")
    require_point(summary, point_id)

    def read(name: str) -> Any:
        ref = objects.get(name)
        if ref is None:
            raise HauteValidationError(f"Path artifact lacks member {name!r}")
        try:
            return strict_json_loads(revision.read_bytes(ref))
        except (ValueError, UnicodeError) as exc:
            raise HauteValidationError("Invalid path point result") from exc

    stored = hydrate(read(result_member(path_id, point_id)), read)
    return _resolve_point_paths(stored, paths, path_id, point_id)


def materialize_path_point(
    bundle: StageBundle,
    path_id: str,
    point_id: str,
    destination: Path,
    *,
    configuration: Mapping[str, Any] | None = None,
) -> tuple[Path, Any]:
    """Apply a path point as a portable bundle (scripted use; the store applies in place)."""
    members, metadata, result = path_point_revision(
        bundle, path_id, point_id, configuration=configuration
    )
    destination.mkdir(parents=True, exist_ok=True)
    artifact = destination / "applied.hstage"
    _pack(artifact, members, metadata)
    result.stage_checkpoint_path = str(artifact)
    return artifact, result


def path_point_revision(
    bundle: StageBundle,
    path_id: str,
    point_id: str,
    *,
    configuration: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """The applied revision of a path point: its members, metadata and point result.

    Nothing is rewritten. The members are the parent's, so a revision read in place
    inherits every one of them by reference.
    """
    result = read_path_result(bundle, path_id, point_id)
    result.staged.pop("prepared_path", None)
    if configuration is not None:
        result.staged["configuration"] = StagedConfig.from_plain_data(configuration).to_plain_data()
    path = resolve_path(bundle, path_id)
    point = require_point(path, point_id)
    if point["status"] == "rejected":
        raise HauteValidationError("This candidate was rejected by a retained fit's training guard")
    metadata = {
        key: copy.deepcopy(value) for key, value in bundle.metadata.items() if key != "path"
    }
    metadata["staged"] = result.staged
    metadata["paths"] = _valid_paths(bundle.metadata.get("paths", {}), result.staged)
    # Keep the validated parent plus the exact prepared delta. The normal session
    # reader restores this selection; Apply never re-encodes the training arrays.
    metadata["path"] = {**copy.deepcopy(path), "selected_point": point_id}
    metadata["paths"][path["stage"]] = metadata["path"]
    metadata["job_kwargs"]["stages"] = result.staged["configuration"]
    return revision_files(bundle, metadata), metadata, result
