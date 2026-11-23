"""Real canonical jobs reopen owned stage evidence without calling the booster again."""

from pathlib import Path

import numpy as np
import pytest
from t_boost import PruningPathSettings, _Booster

from haute.modelling._stage_objects import StageArchive, read_archive_object
from haute.modelling._staged_config import StagedConfig
from haute.modelling._staged_runtime import open_stage_bundle, run_stage_job
from haute.modelling._tboost import TBoostModel
from haute.modelling._training_job import TrainingJob
from tests._project import use_project
from tests.test_staged_tboost import PARAMS
from tests.test_tboost_family import EVALUATION, frame


def build(tmp_path, validation=None):
    use_project(tmp_path)
    data = frame(1500)
    result = TrainingJob(
        name="stages",
        data=data,
        target="claims",
        algorithm="tboost",
        feature_columns=["region", "age"],
        weight="weight",
        offset="exposure",
        params=PARAMS,
        loss_function="Poisson",
        evaluation={
            **EVALUATION,
            "test": {"size": 0.2},
            "validation": validation or EVALUATION["validation"],
        },
        stages={"path": {"min_rows": 20}},
        purpose="exploration",
        chart_left_out=True,
        output_dir=str(tmp_path / "full"),
    ).run()
    return data, result


def draft(bundle, stage):
    """The draft operation the bundle's own stage settings describe."""
    return StagedConfig.from_plain_data(bundle.metadata["job_kwargs"]["stages"]).operation(stage)


def test_full_prune_band_smooth_resume_through_canonical_jobs(tmp_path, monkeypatch):
    data, full = build(tmp_path)
    checkpoint = full.stage_checkpoint_path
    assert checkpoint is not None
    with open_stage_bundle(checkpoint) as bundle:
        settings = bundle.metadata["job_kwargs"]["stages"]
        expected_session = bundle.session("validation-0")
        for stage in ("pruned", "banded", "smoothed"):
            expected = expected_session.apply(draft(bundle, stage)).model
    with open(checkpoint, "rb") as source:
        original = source.read()
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("resumed job fitted"))
    previous = full
    for stage in ("pruned", "banded", "smoothed"):
        _, result = run_stage_job(
            previous.stage_checkpoint_path,
            operation=stage,
            stages=settings,
            output_dir=str(tmp_path / stage),
        )
        assert result.staged["evaluation_identity"] == full.staged["evaluation_identity"]
        assert result.staged["selected_stage"] == stage
        assert result.staged["stages"]["full"] == full.staged["stages"]["full"]
        assert result.final_test_metrics == {}
        assert result.diagnostics_set == "validation"
        previous = result
    assert set(previous.staged["stages"]) == {"full", "pruned", "banded", "smoothed"}
    final = TBoostModel.load(previous.model_path)
    np.testing.assert_array_equal(
        final.estimator.predict(final.prepared(data)), expected.predict(final.prepared(data))
    )
    with open(checkpoint, "rb") as source:
        assert source.read() == original


def test_resume_refuses_wrong_fit_identity_and_missing_parent(tmp_path, monkeypatch):
    _, full = build(tmp_path)
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("resumed job fitted"))
    with pytest.raises(ValueError, match="configuration"):
        run_stage_job(
            full.stage_checkpoint_path,
            operation="pruned",
            stages={},
            output_dir=str(tmp_path / "wrong"),
            expected_fit_config="0" * 64,
        )
    with pytest.raises(ValueError, match="parent"):
        run_stage_job(
            full.stage_checkpoint_path,
            operation="smoothed",
            stages={},
            output_dir=str(tmp_path / "missing"),
        )


def test_bundle_reuses_validated_parent_without_sharing_stage_mutations(tmp_path, monkeypatch):
    from t_boost import TBoostTrainingSession

    _, full = build(tmp_path)
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        first = bundle.session("validation-0")
        first.apply(draft(bundle, "pruned"))
        monkeypatch.setattr(
            TBoostTrainingSession, "load", lambda *a, **kw: pytest.fail("parent reloaded")
        )
        second = bundle.session("validation-0")
        assert set(second.stages) == {"full"}
        np.testing.assert_array_equal(
            second.stage("full").model._model.predict_raw(
                second._state.x, cat_x=second._state.categories
            ),
            first.stage("full").model._model.predict_raw(
                first._state.x, cat_x=first._state.categories
            ),
        )


@pytest.mark.parametrize("cache_budget", [0, 512 * 1024**2])
def test_session_cache_revalidates_replaced_checkpoints_and_respects_budget(
    tmp_path, monkeypatch, cache_budget
):
    from t_boost import TBoostTrainingSession

    from haute.modelling import _staged_runtime

    _, full = build(tmp_path)
    original = TBoostTrainingSession.load
    calls = []

    def load(*args, **kwargs):
        calls.append(kwargs["chain"])
        return original(*args, **kwargs)

    monkeypatch.setattr(TBoostTrainingSession, "load", load)
    monkeypatch.setattr(_staged_runtime, "_MAX_SESSION_CACHE_BYTES", cache_budget)
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        bundle.session("validation-0")
        bundle.session("validation-0")
        assert len(calls) == (2 if cache_budget == 0 else 1)
        # A job's new chain for the fit supersedes the cached parent.
        chain = "fit/validation-0/chain"
        replacement = bundle.scratch / "replacement.member"
        replacement.write_bytes(bundle.member(chain).read_bytes())
        bundle.adopt_member(chain, replacement)
        before = len(calls)
        bundle.session("validation-0")
        assert len(calls) == before + 1
        assert bundle._session_bytes <= cache_budget
    assert bundle._session_bytes == 0
    assert not bundle._sessions


def test_path_defers_optional_shap_and_pdp_but_keeps_canonical_decision_diagnostics(
    tmp_path, monkeypatch
):
    from haute.modelling import _metrics
    from haute.modelling._tboost import TBoostAlgorithm

    _, full = build(tmp_path)
    calls = []
    original_pdp = _metrics.compute_pdp
    original_shap = TBoostAlgorithm.shap_values

    def pdp(*args, **kwargs):
        calls.append("pdp")
        return original_pdp(*args, **kwargs)

    def shap(*args, **kwargs):
        calls.append("shap")
        return original_shap(*args, **kwargs)

    monkeypatch.setattr(_metrics, "compute_pdp", pdp)
    monkeypatch.setattr(TBoostAlgorithm, "shap_values", shap)
    _, result = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages=full.staged["configuration"],
        prepare_path=True,
        output_dir=str(tmp_path / "path"),
    )
    assert calls == []
    assert result.staged["diagnostic_scope"] == "path"
    assert result.metrics and result.ave_per_feature and result.tboost_tables
    assert result.diagnostics_errors == []


def rewrite_archive(path, destination, change):
    """Rewrite a stage archive after *change*(members, revision) edits its decoded parts.

    Members are bytes by name. Object digests and sizes are recomputed, so only the
    reader's own validation can refuse the result. The revision is written as given.
    """
    import hashlib
    import json
    import zipfile

    with zipfile.ZipFile(path) as archive:
        header = archive.read("archive.json")
        revision = json.loads(archive.read("revision.json"))
        members = {
            name: archive.read(f"objects/{ref['sha256']}")
            for name, ref in revision["objects"].items()
        }
    change(members, revision)
    revision["objects"] = {
        name: {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "kind": revision["objects"].get(name, {}).get("kind", "json"),
        }
        for name, data in members.items()
    }
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("archive.json", header)
        archive.writestr("revision.json", json.dumps(revision))
        for data in {hashlib.sha256(d).hexdigest(): d for d in members.values()}.items():
            archive.writestr(f"objects/{data[0]}", data[1])
    return destination


@pytest.mark.parametrize("corruption", ["checksum", "unknown", "size", "missing-source"])
def test_bundle_load_refuses_corrupt_or_unknown_payloads(tmp_path, corruption):
    import json
    import zipfile
    from pathlib import Path

    from haute.errors import HauteValidationError

    _, full = build(tmp_path)
    path = Path(full.stage_checkpoint_path)
    if corruption == "missing-source":
        tampered = rewrite_archive(
            path, tmp_path / "tampered.hstage", lambda members, _: members.pop("source.parquet")
        )
        with pytest.raises(HauteValidationError, match="missing"):
            with open_stage_bundle(tampered):
                pytest.fail("bundle without its source loaded")
        return
    if corruption == "size":
        with pytest.raises(HauteValidationError, match="size"):
            with open_stage_bundle(path, max_bytes=1):
                pytest.fail("oversized bundle loaded")
        return
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
        source = json.loads(members["revision.json"])["objects"]["source.parquet"]["sha256"]
    if corruption == "checksum":
        name = f"objects/{source}"
        members[name] = members[name][:-4] + b"xxxx"
    else:
        members["../../outside"] = b"escape"
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    with pytest.raises(HauteValidationError, match="checksum|Unknown"):
        with open_stage_bundle(path):
            pytest.fail("malformed bundle loaded")
    assert not (tmp_path / "outside").exists()


def published_store(tmp_path, full):
    """A store whose current revision is *full*'s bundle: a worker's real parent."""
    from haute.modelling._stage_store import StageStore

    store = StageStore(tmp_path / "project")
    key = store.key("p", "n", "m")
    store.begin(key, job_id="fit", operation="full", expected_revision=None)
    prepared = store.prepare(
        key,
        Path(full.stage_checkpoint_path),
        result={"response": {"job_id": "fit"}, "graph_digest": "c" * 64},
        config_digest="a" * 64,
    )
    revision = store.publish(key, job_id="fit", prepared=prepared)["current"]["revision"]
    return store, key, revision


def _store_state(root):
    """Every path under *root* with its content digest: names and bytes.

    Lock files (process tokens, store locks) are recorded by size and modification time:
    Windows forbids reading a byte-range-locked file, even from the process holding it.
    """
    import hashlib

    def state(path):
        if not path.is_file():
            return None
        if path.suffix == ".lock":
            info = path.stat()
            return ("lock", info.st_size, info.st_mtime_ns)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return {str(path.relative_to(root)): state(path) for path in sorted(root.rglob("*"))}


def _spawned_stage_job(arguments):
    """The stage worker's job function, run in a freshly spawned process."""
    from haute._worker_protocol import WorkerRequest, WorkerRuntime
    from haute.routes._training_worker import _run_training_process_job
    from tests.test_training_worker_protocol import _ForwardingQueue

    root, payload = arguments
    result = _run_training_process_job(
        WorkerRuntime(_ForwardingQueue(), root), WorkerRequest("stage-job", "training", payload)
    )
    return result  # pickled back to the test process


def test_supervised_worker_reads_its_parent_in_place_and_returns_a_delta(tmp_path, monkeypatch):
    from haute._execution_context import ExecutionProfile
    from haute._worker_protocol import WorkerResultManifest
    from haute.routes._training_artifacts import (
        _read_training_response,
        _validate_training_artifacts,
    )
    from haute.schemas import TrainResponse

    _, full = build(tmp_path)
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        kwargs = bundle.metadata["job_kwargs"]
        digest = bundle.metadata["fit_config_digest"]
    store, key, parent = published_store(tmp_path, full)
    # As in the service: the running-job record roots the parent before the worker starts.
    store.begin(key, job_id="prune", operation="pruned", expected_revision=parent)
    before = _store_state(store.root)
    root = tmp_path / "worker"
    payload = {
        "job_kwargs": kwargs,
        "stage_resume": {
            "parent": store.parent_reference(key, parent),
            "operation": "pruned",
            "expected_fit_config": digest,
        },
        "profile": ExecutionProfile.TRAINING_PREP.value,
        "memory_limit_bytes": None,
    }
    import multiprocessing

    # A genuinely separate process: its own token registry, and the spawn start method
    # that Windows uses.
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        result = pool.apply(_spawned_stage_job, ((str(root), payload),))
    assert isinstance(result, WorkerResultManifest), result
    # The worker wrote nothing under the store: no new file, and no existing one changed.
    assert _store_state(store.root) == before
    response = TrainResponse.model_validate(_read_training_response(result, artifact_root=root))
    assert response.staged.selected_stage == "pruned"
    assert response.staged.stages["full"]["fit_evidence"]["term_update_steps"] is None
    assert response.staged.stages["pruned"]["fit_evidence"]["term_update_steps"] is None
    assert response.staged.evaluation_identity == full.staged["evaluation_identity"]
    assert not response.final_test_metrics
    with pytest.raises(Exception, match="unknown parent"):
        _validate_training_artifacts(
            result,
            artifact_root=root,
            expected_model_name="stages",
            expected_evaluation=response.evaluation,
            expected_staged=response.staged,
        )
    paths = _validate_training_artifacts(
        result,
        artifact_root=root,
        expected_model_name="stages",
        expected_evaluation=response.evaluation,
        expected_staged=response.staged,
        stage_parent=lambda: store.lease(key, parent),
    )
    # The delta carries only what the job wrote; rows and fit evidence are inherited.
    archive = StageArchive.read(paths["stage_state"])
    assert {"source.parquet", "fit/validation-0/evidence"} <= archive.inherited
    assert "fit/validation-0/chain" not in archive.inherited
    prepared = store.prepare(
        key,
        paths["stage_state"],
        result={"response": {"job_id": "prune"}, "graph_digest": "c" * 64},
        config_digest="a" * 64,
        parent_revision=parent,
    )
    preview = store.publish(key, job_id="prune", prepared=prepared, preview=True)["preview"]
    with store.lease(key, preview["revision"]) as stored, open_stage_bundle(stored) as bundle:
        from haute.modelling._stage_diagnostics import without_nulls

        assert without_nulls(bundle.metadata["staged"]) == without_nulls(
            response.staged.model_dump(mode="json")
        )


def test_a_delta_is_refused_once_its_parent_is_gone(tmp_path):
    from haute.errors import ConflictError

    _, full = build(tmp_path)
    store, key, parent = published_store(tmp_path, full)
    with store.lease(key, parent) as stored:
        _, pruning = run_stage_job(
            stored,
            operation="pruned",
            stages=full.staged["configuration"],
            prepare_path=True,
            output_dir=str(tmp_path / "prune"),
        )
    # A rebuild replaces the parent and collection removes it.
    store.begin(key, job_id="rebuild", operation="full", expected_revision=None)
    rebuilt = store.prepare(
        key, Path(full.stage_checkpoint_path), result={}, config_digest="a" * 64
    )
    store.publish(key, job_id="rebuild", prepared=rebuilt)
    store.cleanup(key)
    pointer = (store._directory(key) / "manifest.json").read_bytes()
    with pytest.raises(ConflictError, match="parent changed"):
        store.prepare(
            key,
            Path(pruning.stage_checkpoint_path),
            result={},
            config_digest="a" * 64,
            parent_revision=parent,
        )
    assert (store._directory(key) / "manifest.json").read_bytes() == pointer
    assert not list(store._directory(key).glob("staging/*"))


def test_an_orphaned_delta_fails_validation_once_its_parent_is_collected(tmp_path):
    """A worker's finished delta is refused at validation when its parent has gone."""
    from haute._execution_context import ExecutionProfile
    from haute._worker_protocol import WorkerRequest, WorkerResultManifest, WorkerRuntime
    from haute.errors import ConflictError
    from haute.routes._training_artifacts import (
        _read_training_response,
        _validate_training_artifacts,
    )
    from haute.routes._training_worker import _run_training_process_job
    from haute.schemas import TrainResponse
    from tests.test_training_worker_protocol import _ForwardingQueue

    _, full = build(tmp_path)
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        kwargs = bundle.metadata["job_kwargs"]
        digest = bundle.metadata["fit_config_digest"]
    store, key, parent = published_store(tmp_path, full)
    root = tmp_path / "worker"
    result = _run_training_process_job(
        WorkerRuntime(_ForwardingQueue(), str(root)),
        WorkerRequest(
            "stage-job",
            "training",
            {
                "job_kwargs": kwargs,
                "stage_resume": {
                    "parent": store.parent_reference(key, parent),
                    "operation": "pruned",
                    "expected_fit_config": digest,
                },
                "profile": ExecutionProfile.TRAINING_PREP.value,
                "memory_limit_bytes": None,
            },
        ),
    )
    assert isinstance(result, WorkerResultManifest), result
    response = TrainResponse.model_validate(_read_training_response(result, artifact_root=root))
    store.remove(key)  # the parent is unrooted and collected after the worker finished
    with pytest.raises(ConflictError, match="no longer available"):
        _validate_training_artifacts(
            result,
            artifact_root=root,
            expected_model_name="stages",
            expected_evaluation=response.evaluation,
            expected_staged=response.staged,
            stage_parent=lambda: store.lease(key, parent),
        )


def test_an_orphaned_worker_fails_validation_when_its_parent_is_collected(tmp_path):
    from haute.errors import HauteValidationError

    _, full = build(tmp_path)
    store, key, parent = published_store(tmp_path, full)
    reference = store.parent_reference(key, parent)
    store.remove(key)  # the parent is unrooted and collected under the worker
    with pytest.raises(HauteValidationError, match="no longer available|missing"):
        run_stage_job(
            store.open_parent(reference),
            operation="pruned",
            stages=full.staged["configuration"],
            prepare_path=True,
            output_dir=str(tmp_path / "prune"),
        )


@pytest.mark.parametrize("strategy", ["random", "group", "temporal"])
def test_cross_validation_stages_use_each_folds_predictions_and_never_test_rows(
    tmp_path, monkeypatch, strategy
):
    from dataclasses import replace

    import polars as pl

    from haute.modelling._metrics import compute_ave_per_feature, compute_metrics
    from haute.modelling._split import PARTITION_VALIDATION

    use_project(tmp_path)
    data = frame(1800).with_columns(pl.Series("age", np.arange(1800, dtype=float)))
    from datetime import date, timedelta

    from haute.modelling._tboost import TBoostAlgorithm

    data = data.with_columns(
        pl.Series("group_id", np.arange(1800) // 10),
        pl.Series("day", [date(2020, 1, 1) + timedelta(days=i) for i in range(1800)]),
    )
    evaluation = {
        **EVALUATION,
        "strategy": strategy,
        "validation": {"method": "cross_validation", "fold_count": 3},
        "test": {"size": 0.2},
    }
    if strategy == "group":
        evaluation["group_column"] = "group_id"
    elif strategy == "temporal":
        evaluation.pop("seed")
        evaluation["date_column"] = "day"
        evaluation["validation"]["window"] = "expanding"
        evaluation["test"] = {"start": (date(2020, 1, 1) + timedelta(days=1440)).isoformat()}
    fitted, scored_rows = set(), set()
    fit, predict = TBoostAlgorithm.fit, TBoostAlgorithm.predict

    def observed_fit(self, train_df, *args, **kwargs):
        fitted.update(train_df["age"].to_list())
        return fit(self, train_df, *args, **kwargs)

    def observed_predict(self, model, df, *args, **kwargs):
        scored_rows.update(df["age"].to_list())
        return predict(self, model, df, *args, **kwargs)

    monkeypatch.setattr(TBoostAlgorithm, "fit", observed_fit)
    monkeypatch.setattr(TBoostAlgorithm, "predict", observed_predict)
    result = TrainingJob(
        name="cv",
        data=data,
        target="claims",
        algorithm="tboost",
        feature_columns=["region", "age"],
        weight="weight",
        offset="exposure",
        params=PARAMS,
        loss_function="Poisson",
        evaluation=evaluation,
        stages={"path": {"min_rows": 20}},
        purpose="exploration",
        chart_left_out=True,
        output_dir=str(tmp_path / "full"),
    ).run()
    assert result.staged["diagnostics_basis"] == "out_of_fold"
    assert result.staged["reference_fit"] == "development"
    assert result.diagnostics_set == "validation"
    assert result.final_test_metrics == {}
    assert result.evaluation["fit_count"] == 4
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("stage refitted CV booster"))
    for stage in ("full", "pruned"):
        if stage != "full":
            _, result = run_stage_job(
                result.stage_checkpoint_path,
                operation=stage,
                stages={"path": {"min_rows": 20}},
                output_dir=str(tmp_path / stage),
            )
        reference = TBoostModel.load(result.model_path)
        with open_stage_bundle(result.stage_checkpoint_path) as bundle:
            assert set(bundle.metadata["fits"]) == {
                "validation-0",
                "validation-1",
                "validation-2",
                "development",
            }
            source = pl.read_parquet(bundle.source)
            predictions = np.full(len(source), np.nan)
            for fold in range(3):
                session = bundle.session(f"validation-{fold}")
                model = replace(reference, estimator=session.stage(stage).model)
                rows = np.flatnonzero(bundle.plan.selection_mask(fold) == PARTITION_VALIDATION)
                assert np.isnan(predictions[rows]).all()
                predictions[rows] = model.predict_response(source[rows])
            scored = np.flatnonzero(np.isfinite(predictions))
            assert len(scored) == result.validation_rows
            assert len(scored) <= bundle.plan.development_rows
            reserved = set(bundle.plan.test_positions)
            assert reserved and not reserved.intersection(fitted)
            assert scored_rows and not reserved.intersection(scored_rows)
            subset = source[scored]
            expected = compute_metrics(
                subset["claims"].to_numpy(),
                predictions[scored],
                subset["weight"].to_numpy(),
                list(result.metrics),
            )
            assert result.metrics == pytest.approx(expected)
            ave = compute_ave_per_feature(
                subset,
                ["region", "age"],
                ["region"],
                subset["claims"].to_numpy(),
                predictions[scored],
                subset["weight"].to_numpy(),
                exposure=subset["exposure"].to_numpy(),
            )
            assert {item["feature"]: item for item in result.ave_per_feature} == {
                item["feature"]: item for item in ave
            }


@pytest.mark.parametrize(
    "validation", [EVALUATION["validation"], {"method": "cross_validation", "fold_count": 3}]
)
def test_staged_tuning_retains_only_winner_and_resumes_without_trials(
    tmp_path, monkeypatch, validation
):
    use_project(tmp_path)
    tuning = {
        "schema_version": 1,
        "trial_count": 5,
        "seed": 7,
        "metric": "rmse",
        "search_space": {"learning_rate": [0.05, 0.3]},
    }
    result = TrainingJob(
        name="tuned",
        data=frame(1500),
        target="severity",
        algorithm="tboost",
        feature_columns=["region", "age"],
        params={**PARAMS, "learning_rate": 0.1},
        loss_function="RMSE",
        evaluation={**EVALUATION, "validation": validation, "test": {"size": 0.2}},
        stages={"path": {"min_rows": 20}},
        tuning=tuning,
        purpose="exploration",
        output_dir=str(tmp_path / "full"),
    ).run()
    assert result.diagnostics_set == "validation"
    assert not result.final_test_metrics
    folds = 3 if validation["method"] == "cross_validation" else 1
    assert result.evaluation["fit_count"] == 5 * folds + 1
    with open_stage_bundle(result.stage_checkpoint_path) as bundle:
        assert len(bundle.metadata["fits"]) == folds + 1
        winner = result.tuning["final_params"]
        for key in bundle.metadata["fits"]:
            assert bundle.session(key).stage("full").model.learning_rate == winner["learning_rate"]
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("postfit tuning fitted"))
    _, pruned = run_stage_job(
        result.stage_checkpoint_path,
        operation="pruned",
        stages={"path": {"min_rows": 20}},
        output_dir=str(tmp_path / "pruned"),
    )
    assert pruned.staged["evaluation_identity"] == result.staged["evaluation_identity"]
    assert pruned.staged["stages"]["full"] == result.staged["stages"]["full"]
    assert pruned.tuning is None
    assert not pruned.final_test_metrics


def test_finalisation_replays_applied_recipe_and_retains_native_export(tmp_path):
    from pathlib import Path

    from haute.modelling._training_job import model_contract_filename

    _, full = build(tmp_path)
    _, pruned = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages={"path": {"min_rows": 20}},
        output_dir=str(tmp_path / "pruned"),
    )
    _, final = run_stage_job(
        pruned.stage_checkpoint_path,
        operation="finalise",
        stages={},
        output_dir=str(tmp_path / "final"),
    )
    assert final.diagnostics_set == "final_test"
    assert final.final_test_metrics
    assert final.evaluation.get("purpose") != "exploration"
    native = TBoostModel.load(final.model_path)
    assert native.selected_stage == "pruned"
    with open_stage_bundle(final.stage_checkpoint_path) as bundle:
        assert bundle.metadata["finalised"] is True
        paths = bundle.export_artifacts("reopened")
        assert paths["model"].read_bytes() == Path(final.model_path).read_bytes()
        assert (
            paths["feature_contract"].read_bytes()
            == (Path(final.model_path).parent / model_contract_filename("stages")).read_bytes()
        )
        assert (
            bundle.metadata["staged"]["configuration"]["applied_recipe"]
            == pruned.staged["configuration"]["applied_recipe"]
        )


def test_draft_path_edits_do_not_change_applied_recipe_replay(tmp_path):
    from copy import deepcopy

    _, full = build(tmp_path)
    _, pruned = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages={"path": {"min_rows": 20}},
        output_dir=str(tmp_path / "pruned"),
    )
    with open_stage_bundle(pruned.stage_checkpoint_path) as bundle:
        config = deepcopy(pruned.staged["configuration"])
        assert config["applied_recipe"]["operations"][0]["path"]["min_rows"] == 20
        kwargs = {**bundle.metadata["job_kwargs"], "data": str(bundle.source), "purpose": "final"}
        baseline = TrainingJob(
            **{**kwargs, "stages": config, "output_dir": str(tmp_path / "baseline")}
        ).run()
        config["path"]["min_rows"] = 999999
        pending = TrainingJob(
            **{**kwargs, "stages": config, "output_dir": str(tmp_path / "pending")}
        ).run()
        import polars as pl

        source = pl.read_parquet(bundle.source)
        np.testing.assert_array_equal(
            TBoostModel.load(baseline.model_path).predict_response(source),
            TBoostModel.load(pending.model_path).predict_response(source),
        )


def test_overlay_identity_includes_target_weights_and_offset(tmp_path):
    _, full = build(tmp_path)
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        for field, value in [("target", "severity"), ("weight", None), ("offset", None)]:
            kwargs = {
                **bundle.metadata["job_kwargs"],
                field: value,
                "data": str(bundle.source),
                "output_dir": str(tmp_path / field),
            }
            other = TrainingJob(**kwargs).run()
            assert other.staged["evaluation_identity"] != full.staged["evaluation_identity"]


@pytest.mark.parametrize("pruning,banding", [(False, False), (False, True), (True, False)])
def test_skipped_phases_reuse_the_nearest_enabled_fit(tmp_path, monkeypatch, pruning, banding):
    data, full = build(tmp_path)
    settings = {
        "path": {"min_rows": 20},
        "enabled": {"pruned": pruning, "banded": banding, "smoothed": True},
    }
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        expected = bundle.session("validation-0")
    monkeypatch.setattr(
        _Booster, "fit", lambda *a, **kw: pytest.fail("skipped-phase resume fitted booster")
    )
    previous = full
    parent = "full"
    for stage, enabled in (("pruned", pruning), ("banded", banding), ("smoothed", True)):
        if not enabled:
            continue
        operation = StagedConfig.from_plain_data(settings).operation(stage)
        assert operation.parent == parent
        estimator = expected.apply(operation).model
        _, result = run_stage_job(
            previous.stage_checkpoint_path,
            operation=stage,
            stages=settings,
            output_dir=str(tmp_path / stage),
        )
        native = TBoostModel.load(result.model_path)
        np.testing.assert_array_equal(
            native.estimator.predict(native.prepared(data)),
            estimator.predict(native.prepared(data)),
        )
        assert result.staged["stages"][stage]["parent"] == parent
        previous, parent = result, stage
    applied = previous.staged["configuration"]["applied_recipe"]["operations"]
    assert [op["stage"] for op in applied] == [
        stage
        for stage, enabled in (("pruned", pruning), ("banded", banding), ("smoothed", True))
        if enabled
    ]


def test_a_stage_built_on_full_publishes_evidence_for_the_path_it_keeps(tmp_path):
    """Only pruning adopts a pending path edit: a banding job on full keeps the retained
    path settings, and the evidence it shows is for those settings."""
    _, full = build(tmp_path)
    _, banded = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages={"path": {"min_rows": 99999}, "enabled": {"pruned": False}},
        output_dir=str(tmp_path / "banded"),
    )
    staged = banded.staged
    assert staged["configuration"]["path"]["min_rows"] == 20
    assert staged["pruning_path"] is not None
    assert staged["path_unavailable_reason"] is None
    assert staged["pruning_path"] == full.staged["pruning_path"]


def test_phase_change_requires_rebuilding_an_old_parent_chain(tmp_path, monkeypatch):
    _, full = build(tmp_path)
    previous = full
    for stage in ("pruned", "banded"):
        _, previous = run_stage_job(
            previous.stage_checkpoint_path,
            operation=stage,
            stages={"path": {"min_rows": 20}},
            output_dir=str(tmp_path / stage),
        )
    monkeypatch.setattr(
        _Booster, "fit", lambda *a, **kw: pytest.fail("phase change fitted booster")
    )
    with pytest.raises(ValueError, match="Rebuild banded"):
        run_stage_job(
            previous.stage_checkpoint_path,
            operation="smoothed",
            stages={"enabled": {"pruned": False}},
            output_dir=str(tmp_path / "stale"),
        )


@pytest.mark.parametrize("cache_partitions", [True, False])
def test_banding_path_retains_parent_once_and_applies_exact_candidate(
    tmp_path, monkeypatch, cache_partitions
):
    from haute.modelling._stage_paths import materialize_path_point, read_path_result

    _, full = build(tmp_path)
    settings = full.staged["configuration"]
    settings["enabled"] = {"pruned": False, "banded": True, "smoothed": True}
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("path fitted booster"))
    import haute._chunked_writes as writes
    import haute.modelling._staged_runtime as runtime_module

    if not cache_partitions:
        monkeypatch.setattr(runtime_module, "_MAX_PATH_SPLIT_BYTES", 0)
    original_write = writes.write_file_with_column
    split_paths = []

    def record_split(path, *args, **kwargs):
        split_paths.append(str(path))
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(writes, "write_file_with_column", record_split)
    _, result = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages=settings,
        prepare_path=True,
        output_dir=str(tmp_path / "path"),
    )
    points = result.staged["paths"]["banded"]["points"]
    unique_outcomes = sum("equivalent_to" not in p["diagnostics"] for p in points)
    assert unique_outcomes > 1
    assert len(split_paths) == (1 if cache_partitions else unique_outcomes)
    from pathlib import Path

    assert all(not Path(path).exists() for path in split_paths)
    _, independent = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages={**settings, "banding": {"tolerance": 0.75, "deviance_cap": 0.001}},
        output_dir=str(tmp_path / "independent"),
    )
    summary = result.staged["paths"]["banded"]
    assert len(summary["points"]) == 7
    assert summary["evidence"] == "validation"
    assert all(p["loss"] is not None for p in summary["points"])
    assert summary["points"][0]["loss_delta"] == 0
    with open_stage_bundle(result.stage_checkpoint_path) as bundle:
        assert {name for name in bundle.manifest.objects if name.startswith("fit/")} == {
            "fit/validation-0/evidence",
            "fit/validation-0/chain",
        }
        last = read_path_result(bundle, summary["id"], "point-6")
        assert last.metrics == independent.metrics
        assert last.ave_per_feature == independent.ave_per_feature
        assert last.ave_left_out == independent.ave_left_out
        assert not any("split" in name for name in bundle.manifest.objects)
        chosen = summary["points"][2]["id"]
        candidate_result = read_path_result(bundle, summary["id"], chosen)
        selected = bundle.session("validation-0", point_id=chosen).stage("banded").model
        from t_boost import TBoostTrainingSession

        with monkeypatch.context() as patch:
            patch.setattr(
                TBoostTrainingSession,
                "load",
                lambda *a, **kw: pytest.fail("Apply reloaded a prepared checkpoint"),
            )
            patch.setattr(
                TBoostTrainingSession,
                "save",
                lambda *a, **kw: pytest.fail("Apply recompressed a prepared checkpoint"),
            )
            artifact, applied = materialize_path_point(
                bundle, summary["id"], chosen, tmp_path / "apply"
            )
        assert applied.metrics == candidate_result.metrics
        with open_stage_bundle(artifact) as restored:
            actual = restored.session("validation-0")
            np.testing.assert_array_equal(
                actual.stage("banded").model._model.predict_raw(
                    actual._state.x, cat_x=actual._state.categories
                ),
                selected._model.predict_raw(actual._state.x, cat_x=actual._state.categories),
            )
            assert (
                actual.stage("banded").recipe.operations[-1].settings()
                == summary["points"][2]["settings"]
            )


def test_smoothing_path_scores_each_retained_fold_and_preserves_earlier_paths(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    import polars as pl

    from haute.modelling._metrics import compute_metrics
    from haute.modelling._split import PARTITION_VALIDATION
    from haute.modelling._stage_paths import materialize_path_point, read_path_result

    _, full = build(tmp_path, {"method": "cross_validation", "fold_count": 3})
    settings = full.staged["configuration"]
    settings["enabled"] = {"pruned": False, "banded": True, "smoothed": True}
    monkeypatch.setattr(_Booster, "fit", lambda *a, **kw: pytest.fail("path refitted booster"))
    _, banding = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages=settings,
        prepare_path=True,
        output_dir=str(tmp_path / "band-path"),
    )
    band_path = banding.staged["paths"]["banded"]
    with open_stage_bundle(banding.stage_checkpoint_path) as bundle:
        applied, _ = materialize_path_point(
            bundle, band_path["id"], "point-2", tmp_path / "band-applied"
        )
    _, smoothing = run_stage_job(
        applied,
        operation="smoothed",
        stages=settings,
        prepare_path=True,
        output_dir=str(tmp_path / "smooth-path"),
    )
    smooth_path = smoothing.staged["paths"]["smoothed"]
    assert smooth_path["evidence"] == "out_of_fold"
    assert band_path["id"] == smoothing.staged["paths"]["banded"]["id"]
    reference = TBoostModel.load(full.model_path)
    with open_stage_bundle(smoothing.stage_checkpoint_path) as bundle:
        earlier = read_path_result(bundle, band_path["id"], "point-2")
        assert earlier.staged["selected_stage"] == "banded"
        source = pl.read_parquet(bundle.source)
        predictions = np.full(len(source), np.nan)
        for fold in range(3):
            session = bundle.session(
                f"validation-{fold}", path_id=smooth_path["id"], point_id="point-2"
            )
            model = replace(reference, estimator=session.stage("smoothed").model)
            rows = np.flatnonzero(bundle.plan.selection_mask(fold) == PARTITION_VALIDATION)
            predictions[rows] = model.predict_response(source[rows])
        scored = np.flatnonzero(np.isfinite(predictions))
        assert not set(scored).intersection(bundle.plan.test_positions)
        expected = compute_metrics(
            source[scored]["claims"].to_numpy(),
            predictions[scored],
            source[scored]["weight"].to_numpy(),
            ["poisson_deviance"],
        )
        assert smooth_path["points"][2]["loss"] == pytest.approx(expected["poisson_deviance"])
        baseline = read_path_result(bundle, smooth_path["id"], "unchanged")
        assert baseline.staged["selected_stage"] == "banded"
        assert all(
            not read_path_result(bundle, smooth_path["id"], p["id"]).final_test_metrics
            for p in smooth_path["points"]
        )


def test_path_candidate_configuration_resets_implicit_auto_strength():
    from haute.modelling._stage_paths import _point_config
    from haute.modelling._staged_config import StagedConfig

    config = StagedConfig.from_plain_data({"smoothing": {"strength": 4.0}})
    point = {"identity": False, "settings": {"alpha": None, "high_order_alpha": 0.0}}
    selected = _point_config(config, "smoothed", point).to_plain_data()
    assert selected["smoothing"]["strength"] == 1.0
    assert selected["smoothing"]["parent"] == "banded"


def test_identical_path_outcomes_share_evaluation_but_keep_exact_policies(tmp_path, monkeypatch):
    from haute.modelling._stage_paths import materialize_path_point, read_path_result

    monkeypatch.setitem(PARAMS, "max_interaction_order", 1)
    data, full = build(tmp_path)
    calls = []
    original = TrainingJob.run

    def counted(job, **kwargs):
        if (
            job._stage_runtime is not None
            and job._stage_runtime.candidate_id is not None
            and job.evaluation_plan is None
        ):
            calls.append(job._stage_runtime.candidate_id)
        return original(job, **kwargs)

    monkeypatch.setattr(TrainingJob, "run", counted)
    _, prepared = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages={"enabled": {"pruned": False, "banded": True, "smoothed": True}},
        prepare_path=True,
        output_dir=str(tmp_path / "path"),
    )
    path = prepared.staged["paths"]["banded"]
    assert calls == ["unchanged"]
    assert len(path["points"]) == 7
    assert all(p["diagnostics"]["equivalent_to"] == "unchanged" for p in path["points"][1:])
    with open_stage_bundle(prepared.stage_checkpoint_path) as bundle:
        alias = read_path_result(bundle, path["id"], "point-6")
        assert alias.staged["selected_stage"] == "banded"
        assert (
            alias.staged["configuration"]["applied_recipe"]["operations"][-1]["settings"]
            == path["points"][6]["settings"]
        )
        assert alias.metrics == read_path_result(bundle, path["id"], "unchanged").metrics
        artifact, applied = materialize_path_point(
            bundle, path["id"], "point-6", tmp_path / "apply"
        )
        with open_stage_bundle(artifact) as restored:
            session = restored.session("validation-0")
            model = TBoostModel.load(full.model_path)
            np.testing.assert_array_equal(
                session.stage("banded").model.predict(model.prepared(data)),
                model.estimator.predict(model.prepared(data)),
            )
            assert (
                applied.staged["configuration"]["applied_recipe"]
                == session.stage("banded").recipe.to_plain_data()
            )


def test_pruning_path_uses_budgets_feasible_for_every_retained_fit(tmp_path):
    import polars as pl

    use_project(tmp_path)
    rng = np.random.default_rng(0)
    x = rng.normal(size=(180, 8)).astype("f")
    y = (x[:, 0] + rng.normal(size=180) * 2).astype("f")
    data = pl.DataFrame({**{f"x{i}": x[:, i] for i in range(8)}, "y": y})
    full = TrainingJob(
        name="folds",
        data=data,
        target="y",
        algorithm="tboost",
        params={"n_trees": 2, "n_bags": 3},
        loss_function="RMSE",
        evaluation={
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "validation": {"method": "cross_validation", "fold_count": 3},
            "test": None,
        },
        stages={"path": {"min_rows": 10}},
        purpose="exploration",
        output_dir=str(tmp_path / "full"),
    ).run()
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        floors = {
            key: bundle.session(key).pruning_path(PruningPathSettings(min_rows=10))["table_counts"][
                0
            ]
            for key in bundle.metadata["fits"]
        }
        assert max(floors.values()) > floors["development"]
    _, result = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages={"path": {"min_rows": 10}},
        prepare_path=True,
        output_dir=str(tmp_path / "path"),
    )
    path = result.staged["paths"]["pruned"]
    assert path["evidence"] == "out_of_fold"
    for point in path["points"][1:]:
        assert point["settings"]["max_tables"] >= max(floors.values())
        assert len(point["fold_status"]) == 4
        assert np.isfinite(point["loss"])


def test_path_split_reuse_preserves_class_encoding(tmp_path, monkeypatch):
    import polars as pl

    import haute._chunked_writes as writes
    from haute.modelling._stage_paths import read_path_result

    use_project(tmp_path)
    data = frame(1200).with_columns(
        pl.when(pl.col("claims") > 0)
        .then(pl.lit("claim"))
        .otherwise(pl.lit("clear"))
        .alias("label")
    )
    full = TrainingJob(
        name="classes",
        data=data,
        target="label",
        algorithm="tboost",
        task="classification",
        feature_columns=["age", "region"],
        params={**PARAMS, "interaction_gain_hurdle": 0},
        positive_class="claim",
        loss_function="Logloss",
        evaluation=EVALUATION,
        stages={},
        purpose="exploration",
        output_dir=str(tmp_path / "full"),
    ).run()
    saved_write = writes.write_file_with_column
    writes_seen = []

    def observed(*args, **kwargs):
        writes_seen.append(args[0])
        return saved_write(*args, **kwargs)

    monkeypatch.setattr(writes, "write_file_with_column", observed)
    settings = {"enabled": {"pruned": False, "banded": True, "smoothed": True}}
    _, path = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages=settings,
        prepare_path=True,
        output_dir=str(tmp_path / "path"),
    )
    assert len(writes_seen) == 1
    _, independent = run_stage_job(
        full.stage_checkpoint_path,
        operation="banded",
        stages=settings,
        output_dir=str(tmp_path / "independent"),
    )
    with open_stage_bundle(path.stage_checkpoint_path) as bundle:
        point = read_path_result(bundle, path.staged["paths"]["banded"]["id"], "point-6")
        assert point.metrics == independent.metrics
        assert point.ave_per_feature == independent.ave_per_feature


def test_path_split_cache_is_removed_when_cancelled(tmp_path, monkeypatch):
    from pathlib import Path

    from haute.modelling._staged_runtime import StageRuntime

    _, full = build(tmp_path)
    cached = []
    retain = StageRuntime.retain_split

    def remember(runtime, *args):
        result = retain(runtime, *args)
        cached.append(Path(result.split_path))
        return result

    monkeypatch.setattr(StageRuntime, "retain_split", remember)

    def cancel():
        if cached:
            raise KeyboardInterrupt("cancelled path")

    with pytest.raises(KeyboardInterrupt, match="cancelled path"):
        run_stage_job(
            full.stage_checkpoint_path,
            operation="banded",
            stages={"enabled": {"pruned": False}},
            prepare_path=True,
            output_dir=str(tmp_path / "path"),
            check_cancelled=cancel,
        )
    assert cached and all(not path.exists() for path in cached)


@pytest.mark.parametrize("validation", [None, {"method": "cross_validation", "fold_count": 3}])
def test_path_jobs_write_parents_once_and_store_families_by_reference(
    tmp_path, monkeypatch, validation
):
    """Each fit's checkpoint is written at most once; point results reference summaries."""
    import json

    from t_boost import TBoostTrainingSession

    from haute.modelling._stage_paths import materialize_path_point, read_path_result

    _, full = build(tmp_path, validation)
    settings = full.staged["configuration"]
    calls = {"load": 0, "save": 0, "evidence": 0}
    original_load, original_save = TBoostTrainingSession.load, TBoostTrainingSession.save_chain
    original_evidence = TBoostTrainingSession.save_evidence

    def load(*args, **kwargs):
        calls["load"] += 1
        return original_load(*args, **kwargs)

    def save(*args, **kwargs):
        calls["save"] += 1
        return original_save(*args, **kwargs)

    def evidence(*args, **kwargs):
        calls["evidence"] += 1
        return original_evidence(*args, **kwargs)

    monkeypatch.setattr(TBoostTrainingSession, "load", load)
    monkeypatch.setattr(TBoostTrainingSession, "save_chain", save)
    monkeypatch.setattr(TBoostTrainingSession, "save_evidence", evidence)

    def prepare(checkpoint, operation, output):
        calls.update(load=0, save=0, evidence=0)
        _, result = run_stage_job(
            checkpoint, operation=operation, stages=settings, prepare_path=True, output_dir=output
        )
        return result, dict(calls)

    pruning, counted = prepare(full.stage_checkpoint_path, "pruned", str(tmp_path / "prune"))
    with open_stage_bundle(pruning.stage_checkpoint_path) as bundle:
        fits = len(bundle.metadata["fits"])
        assert counted["save"] == fits
        assert counted["evidence"] == 0  # path jobs never rewrite fit evidence
        assert counted["load"] == fits
        summary = bundle.metadata["paths"]["pruned"]
        for point in summary["points"]:
            result = read_path_result(bundle, summary["id"], point["id"])
            assert result.staged["prepared_path"] == summary["id"]
            assert result.staged["paths"] == {"pruned": {**summary, "selected_point": point["id"]}}
            assert "path_stages" not in result.staged and "path_refs" not in result.staged
        chosen = next(p["id"] for p in summary["points"] if p["status"] == "changed")
        applied, _ = materialize_path_point(bundle, summary["id"], chosen, tmp_path / "applied")

    banding, counted = prepare(applied, "banded", str(tmp_path / "band"))
    assert counted["save"] == fits  # flattening the applied pruning candidate, once per fit
    assert counted["load"] == fits
    archive = StageArchive.read(Path(banding.stage_checkpoint_path))
    manifest = archive.manifest.metadata
    band, prune = manifest["paths"]["banded"], manifest["paths"]["pruned"]
    stored = json.loads(
        read_archive_object(
            archive.path, archive.manifest.objects[f"path/{band['id']}/point-1.result"]
        )
    )
    assert not {"paths", "path_stages", "path_refs"} & set(stored["staged"])
    with open_stage_bundle(banding.stage_checkpoint_path) as bundle:
        result = read_path_result(bundle, band["id"], "point-1")
        assert result.staged["paths"] == {
            "pruned": prune,
            "banded": {**band, "selected_point": "point-1"},
        }
        assert result.staged["paths"] == banding.staged["paths"] | {
            "banded": {**band, "selected_point": "point-1"}
        }


@pytest.mark.parametrize(
    "payload", ["NaN", "1e999", "foreign-path", "legacy", "manifest-NaN", "null", "list"]
)
def test_path_point_reads_refuse_invalid_members_even_with_matching_checksums(tmp_path, payload):
    import json

    from haute.errors import HauteValidationError
    from haute.modelling._stage_paths import read_path_artifact

    _, full = build(tmp_path)
    _, pruning = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages=full.staged["configuration"],
        prepare_path=True,
        output_dir=str(tmp_path / "prune"),
    )
    path_id = pruning.staged["paths"]["pruned"]["id"]
    member = f"path/{path_id}/point-1.result"

    def change(members, revision):
        stored = json.loads(members[member])
        if payload == "foreign-path":
            stored["staged"]["prepared_path"] = "0" * 64
            text = json.dumps(stored)
        elif payload == "legacy":
            stored["staged"]["paths"] = revision["metadata"]["paths"]
            text = json.dumps(stored)
        elif payload in ("null", "list"):
            text = "null" if payload == "null" else "[]"
        elif payload == "manifest-NaN":
            text = json.dumps(stored)
            revision["metadata"]["paths"]["pruned"]["points"][1]["loss"] = float("nan")
        else:
            stored["metrics"] = {"poisson_deviance": "__NUMBER__"}
            text = json.dumps(stored).replace('"__NUMBER__"', payload)
        members[member] = text.encode()

    tampered = rewrite_archive(pruning.stage_checkpoint_path, tmp_path / "tampered.hstage", change)
    with pytest.raises(HauteValidationError):
        read_path_artifact(tampered, path_id, "point-1")
    if payload in ("null", "list"):
        # The bundle reader refuses it while validating the revision's closure.
        with pytest.raises(HauteValidationError, match="envelope"):
            with open_stage_bundle(tampered):
                pytest.fail("bundle with a malformed point result opened")
    if payload == "manifest-NaN":
        with pytest.raises(HauteValidationError, match="manifest"):
            with open_stage_bundle(tampered):
                pass


def test_later_family_reads_the_rebuilt_earlier_family_it_remains_valid_for(tmp_path):
    """Rebuilding pruning replaces its summary; a still-valid banding family stays readable."""
    from haute.modelling._stage_paths import materialize_path_point, read_path_result

    _, full = build(tmp_path)
    settings = full.staged["configuration"]

    def prepare(checkpoint, operation, stages, output):
        return run_stage_job(
            checkpoint, operation=operation, stages=stages, prepare_path=True, output_dir=output
        )[1]

    first = prepare(full.stage_checkpoint_path, "pruned", settings, str(tmp_path / "p1"))
    p1 = first.staged["paths"]["pruned"]
    with open_stage_bundle(first.stage_checkpoint_path) as bundle:
        applied, _ = materialize_path_point(bundle, p1["id"], p1["selected_point"], tmp_path / "a")
    banding = prepare(applied, "banded", settings, str(tmp_path / "band"))
    band = banding.staged["paths"]["banded"]
    selection = {**settings["selection"]}
    selection["relative_tolerance"] *= 1.01  # a new family identity, the same suggestion
    rebuilt = prepare(
        banding.stage_checkpoint_path,
        "pruned",
        {**settings, "selection": selection},
        str(tmp_path / "p2"),
    )
    p2 = rebuilt.staged["paths"]["pruned"]
    assert p2["id"] != p1["id"] and p2["selected_point"] == p1["selected_point"]
    assert rebuilt.staged["paths"]["banded"]["id"] == band["id"]
    with open_stage_bundle(rebuilt.stage_checkpoint_path) as bundle:
        for point in band["points"]:
            result = read_path_result(bundle, band["id"], point["id"])
            assert result.staged["paths"]["pruned"] == bundle.metadata["paths"]["pruned"]
            assert result.staged["paths"]["pruned"]["id"] == p2["id"]
        # Applying `unchanged` invalidates the banding family; every pruning point stays readable.
        unchanged, _ = materialize_path_point(bundle, p2["id"], "unchanged", tmp_path / "u")
    with open_stage_bundle(unchanged) as bundle:
        assert set(bundle.metadata["paths"]) == {"pruned"}
        for point in p2["points"]:
            result = read_path_result(bundle, p2["id"], point["id"])
            assert set(result.staged["paths"]) == {"pruned"}
            assert result.staged["paths"]["pruned"]["selected_point"] == point["id"]


@pytest.mark.parametrize("replacement", ["rewrite", "symlink"])
def test_store_revisions_are_read_only_through_verified_handles(tmp_path, replacement):
    """A store revision's objects are never handed out as paths to reopen unverified."""
    from haute.errors import HauteValidationError
    from haute.modelling._stage_store import StageStore

    _, full = build(tmp_path)
    store = StageStore(tmp_path / "project")
    key = store.key("p", "n", "m")
    store.begin(key, job_id="fit", operation="full", expected_revision=None)
    prepared = store.prepare(
        key,
        Path(full.stage_checkpoint_path),
        result={"response": {"job_id": "fit"}, "graph_digest": "c" * 64},
        config_digest="a" * 64,
    )
    revision = store.publish(key, job_id="fit", prepared=prepared)["current"]["revision"]
    with store.lease(key, revision) as stored, open_stage_bundle(stored) as bundle:
        assert bundle.session("validation-0").stages  # loaded through verified handles
        plan = bundle.read_json("plan.json")
        with pytest.raises(HauteValidationError, match="in place"):
            bundle.member("plan.json")
        path = stored.objects.object_path(stored.manifest.objects["plan.json"].sha256)
        if replacement == "rewrite":
            path.write_bytes(path.read_bytes().replace(b"{", b"[", 1))
        else:
            outside = tmp_path / "outside.json"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        with pytest.raises(HauteValidationError, match="checksum|regular"):
            bundle.read_json("plan.json")
    assert plan["schema_version"]


def test_point_results_are_stored_byte_for_byte_as_the_strict_round_trip_would(
    tmp_path, monkeypatch
):
    """Encoding once is exactly the old encode, decode and split: same objects, same bytes.

    The live results entering `_write_point_result` from a real path job are compared, so
    the guarantee covers today's producers (string keys, list score rows).
    """
    from dataclasses import fields

    from haute.modelling import _stage_paths
    from haute.modelling._stage_diagnostics import split
    from haute.modelling._stage_objects import strict_json_loads
    from haute.modelling._staged_runtime import _json

    original = _stage_paths._write_point_result
    compared = []

    def compare(bundle, result, path_id, point_id):
        written: dict[str, bytes] = {}
        record = bundle.write_member

        def capture(name, data):
            written[name] = data
            return record(name, data)

        monkeypatch.setattr(bundle, "write_member", capture)
        try:
            original(bundle, result, path_id, point_id)
        finally:
            monkeypatch.setattr(bundle, "write_member", record)
        envelope = written.pop(_stage_paths.result_member(path_id, point_id))
        # The old form, from the same live result: a strict JSON round trip, then split.
        staged = {k: v for k, v in result.staged.items() if k != "paths"}
        staged["prepared_path"] = path_id
        live = {
            f.name: staged if f.name == "staged" else getattr(result, f.name)
            for f in fields(result)
        }
        old: dict[str, bytes] = {}
        assert _json(split(strict_json_loads(_json(live)), old.__setitem__)) == envelope
        # Objects written are the old split's; any it skips the bundle already holds,
        # byte for byte (the parent's reused snapshots and their dense diagnostics).
        assert all(old[name] == data for name, data in written.items())
        skipped = set(old) - set(written)
        assert all(bundle.read_bytes(name) == old[name] for name in skipped)
        reused.update(skipped)
        compared.append(point_id)

    reused: set[str] = set()
    monkeypatch.setattr(_stage_paths, "_write_point_result", compare)
    _, full = build(tmp_path)
    _, pruning = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages=full.staged["configuration"],
        prepare_path=True,
        output_dir=str(tmp_path / "prune"),
    )
    assert compared == [p["id"] for p in pruning.staged["paths"]["pruned"]["points"]]
    assert any(name.startswith("snapshot/") for name in reused)  # the full snapshot


def test_path_points_reuse_the_bundles_evaluation_plan(tmp_path, monkeypatch):
    """Each candidate is evaluated on the bundle's own canonical plan, not a rebuilt one."""
    _, full = build(tmp_path)
    with open_stage_bundle(full.stage_checkpoint_path) as bundle:
        expected = bundle.plan.to_plain_data()
    monkeypatch.setattr(
        TrainingJob, "_build_evaluation_plan", lambda *a, **k: pytest.fail("plan rebuilt")
    )
    _, pruning = run_stage_job(
        full.stage_checkpoint_path,
        operation="pruned",
        stages=full.staged["configuration"],
        prepare_path=True,
        output_dir=str(tmp_path / "prune"),
    )
    from types import SimpleNamespace

    from haute.modelling._stage_paths import read_path_result
    from haute.modelling._staged_runtime import StageRuntime

    path = pruning.staged["paths"]["pruned"]
    with open_stage_bundle(pruning.stage_checkpoint_path) as bundle:
        assert bundle.plan.to_plain_data() == expected
        digest = full.evaluation["plan_sha256"]
        for point in path["points"]:  # every candidate's own evaluation used that plan
            assert (
                read_path_result(bundle, path["id"], point["id"]).evaluation["plan_sha256"]
                == digest
            )
        # The bundle's plan serves only the bundle's own source, never another input.
        runtime = StageRuntime(bundle=bundle, operation="pruned")
        own = SimpleNamespace(owns_tmp=False, data_path=str(bundle.source))
        assert runtime.evaluation_plan(own) is bundle.plan
        assert (
            runtime.evaluation_plan(SimpleNamespace(owns_tmp=True, data_path=str(bundle.source)))
            is None
        )
        assert (
            runtime.evaluation_plan(SimpleNamespace(owns_tmp=False, data_path="other.parquet"))
            is None
        )
        runtime.close()
