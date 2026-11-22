"""Haute's staged adapter shares data, offset and export semantics with ordinary training."""

import json

import numpy as np
import pytest
from t_boost import TBoostRegressor, _Booster

from haute.errors import HauteValidationError
from haute.modelling._staged_config import StagedConfig
from haute.modelling._tboost import TBoostModel
from tests.test_tboost_family import fit_adapter, frame

PARAMS = {"n_trees": 25, "n_bags": 3}


def test_adapter_full_then_recipe_matches_library_and_preserves_native_metadata(tmp_path):
    data = frame(1500)
    config = StagedConfig.from_plain_data({"path": {"min_rows": 20}})
    fitted = fit_adapter(
        data, params=PARAMS, stages=config.to_plain_data(), offset="exposure", weight="weight"
    )
    model = fitted.model
    session = model.training_session
    assert session is not None
    np.testing.assert_array_equal(
        model.estimator.predict(model.prepared(data)),
        session.full_model.predict(model.prepared(data)),
    )
    assert model.estimator.prune is False
    assert model.estimator.graduate is False
    assert fitted.loss_history
    session.prune()
    session.band()
    expected = session.smooth()
    payload = config.to_plain_data()
    payload["applied_recipe"] = session.recipe("smoothed")
    staged = fit_adapter(data, params=PARAMS, stages=payload, offset="exposure", weight="weight")
    np.testing.assert_array_equal(
        staged.model.estimator.predict(model.prepared(data)), expected.predict(model.prepared(data))
    )
    path = tmp_path / "staged.tboost"
    staged.model.estimator.metadata = {"custom": "keep me"}
    staged.model.save(path)
    document = json.loads(path.read_text())
    assert document["metadata"]["custom"] == "keep me"
    assert document["metadata"]["haute_stage"]["recipe"] == payload["applied_recipe"]
    exported = TBoostRegressor.from_json(path.read_text())
    loaded = TBoostModel.load(path)
    np.testing.assert_array_equal(
        exported.predict(model.prepared(data)), expected.predict(model.prepared(data))
    )
    np.testing.assert_array_equal(
        loaded.predict_response(data), staged.model.predict_response(data)
    )


def test_misplaced_postfit_settings_fail_before_fit(monkeypatch):
    monkeypatch.setattr(
        _Booster, "fit", lambda *a, **kw: pytest.fail("invalid settings reached fit")
    )
    with pytest.raises(HauteValidationError, match="prune"):
        fit_adapter(frame(), params={**PARAMS, "prune": True}, stages={})


def test_canonical_job_and_generated_script_replay_the_same_recipe(tmp_path):
    from haute.modelling._export import generate_training_script
    from haute.modelling._train_config import build_training_job_kwargs
    from haute.modelling._training_job import TrainingJob
    from tests._project import use_project
    from tests.test_tboost_family import EVALUATION

    data = frame(1500)
    source = tmp_path / "rows.parquet"
    data.write_parquet(source)
    stages = StagedConfig.from_plain_data({"path": {"min_rows": 20}})
    payload = stages.to_plain_data()
    payload["applied_recipe"] = [stages.operation("pruned"), stages.operation("banded")]
    config = {
        "name": "staged",
        "algorithm": "tboost",
        "task": "regression",
        "target": "claims",
        "weight": "weight",
        "offset": "exposure",
        "feature_columns": ["region", "age"],
        "params": PARAMS,
        "stages": payload,
        "loss_function": "Poisson",
        "metrics": ["poisson_deviance"],
        "evaluation": EVALUATION,
        "output_dir": str(tmp_path / "direct"),
    }
    kwargs = build_training_job_kwargs(config, data=str(source))
    use_project(tmp_path)
    direct = TrainingJob(**kwargs)
    result = direct.run()
    config["output_dir"] = str(tmp_path / "script")
    namespace = {"__name__": "staged_export_test"}
    exec(compile(generate_training_script(config, str(source)), "generated.py", "exec"), namespace)
    scripted = namespace["job"]
    assert scripted.training_identity_sha256 == direct.training_identity_sha256
    script_result = scripted.run()
    first, second = TBoostModel.load(result.model_path), TBoostModel.load(script_result.model_path)
    np.testing.assert_array_equal(first.predict_response(data), second.predict_response(data))
    assert first.estimator.metadata["haute_stage"]["recipe"] == payload["applied_recipe"]
    assert first.estimator.band_tolerance == 0.75
    assert first.estimator.graduate is False


@pytest.mark.parametrize("validation", [{"method": "single", "size": 0.2}, {"method": "none"}])
def test_exploration_never_refits_or_scores_reserved_test_rows(tmp_path, monkeypatch, validation):
    from haute.modelling._evaluation import EvaluationPlan
    from haute.modelling._tboost import TBoostAlgorithm
    from haute.modelling._training_job import TrainingJob
    from tests._project import use_project

    data = frame(1500).with_row_index("row_id")
    fitted_rows, scored_rows = [], []
    fit, predict = TBoostAlgorithm.fit, TrainingJob._predict_diagnostic_batches
    row_of_age = dict(zip(data["age"].to_list(), data["row_id"].to_list()))

    def observed_fit(self, train_df, *args, **kwargs):
        fitted_rows.append({row_of_age[age] for age in train_df["age"].to_list()})
        return fit(self, train_df, *args, **kwargs)

    def observed_predict(self, algo, model, df, *args, **kwargs):
        scored_rows.extend(row_of_age[age] for age in df["age"].to_list())
        return predict(self, algo, model, df, *args, **kwargs)

    monkeypatch.setattr(TBoostAlgorithm, "fit", observed_fit)
    monkeypatch.setattr(TrainingJob, "_predict_diagnostic_batches", observed_predict)
    use_project(tmp_path)
    job = TrainingJob(
        name="exploration",
        data=data,
        target="claims",
        algorithm="tboost",
        feature_columns=["region", "age"],
        id_columns=["row_id"],
        loss_function="Poisson",
        params=PARAMS,
        stages={},
        purpose="exploration",
        evaluation={
            "schema_version": 1,
            "strategy": "random",
            "seed": 3,
            "validation": validation,
            "test": {"size": 0.2},
        },
        output_dir=str(tmp_path / "result"),
    )
    result = job.run()
    from pathlib import Path

    plan = EvaluationPlan.from_plain_data(
        json.loads(Path(result.evaluation["plan_path"]).read_text(encoding="utf-8"))
    )
    reserved = set(plan.test_positions)
    assert reserved
    assert len(fitted_rows) == 1
    assert not reserved.intersection(fitted_rows[0])
    assert scored_rows and not reserved.intersection(scored_rows)
    if validation["method"] == "single":
        assert len(fitted_rows[0]) < plan.development_rows
        assert result.diagnostics_set == "validation"
        assert not fitted_rows[0].intersection(scored_rows)
    else:
        assert len(fitted_rows[0]) == plan.development_rows
        assert result.diagnostics_set == "development"
    assert result.final_test_rows == len(reserved)
    assert result.final_test_metrics == {}
    assert result.holdout_metrics == {}
    assert result.evaluation["purpose"] == "exploration"

    from haute.routes._training_worker import _training_response_payload

    response = _training_response_payload(
        result,
        job_id="exploration",
        model_path=result.model_path,
        evaluation=result.evaluation,
        tuning=None,
    )
    assert response["evaluation"]["purpose"] == "exploration"
    assert response["final_test_metrics"] == {}
