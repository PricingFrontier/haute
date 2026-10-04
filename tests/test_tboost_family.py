"""t-boost family acceptance: fit, persist, score, explain, tables, tune, serve."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from t_boost import TBoostClassifier, TBoostRegressor

from haute._mlflow_io import load_local_model
from haute._model_explainability import explain_native_prediction
from haute._model_scorer import score_frame
from haute.errors import ConfigError, HauteValidationError
from haute.modelling._descriptors import TBOOST
from haute.modelling._feature_contract import load_contract
from haute.modelling._tboost import TBoostAlgorithm, TBoostModel
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob, model_contract_filename

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 3,
    "validation": {"method": "single", "size": 0.2},
}
LEVELS = ["east", "north", "south"]
# One bag and no pruning CV keep each fit to a fraction of a second.
FAST = {"n_trees": 300, "learning_rate": 0.2, "n_bags": 1, "prune": False}


def frame(n: int = 600, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice([*LEVELS, None], n)
    age = rng.uniform(18, 80, n)
    exposure = rng.uniform(0.2, 1.0, n)
    weight = rng.uniform(0.5, 2.0, n)
    young_north = (age < 30) & (region == "north")
    rate = np.exp(
        -2 + 0.01 * (age - 40) + np.where(region == "north", 0.4, 0.0) + 0.5 * young_north
    )
    return pl.DataFrame(
        {
            "region": region.tolist(),
            "age": age,
            "exposure": exposure,
            "weight": weight,
            "policy": [f"P{i // 3}" for i in range(n)],
            "claims": rng.poisson(rate * exposure).astype(float),
            "severity": rng.gamma(2.0, 500 * np.exp(0.005 * age)),
            "flag": np.where(rng.uniform(size=n) < 1 / (1 + np.exp(-(age - 50) / 8)), "yes", "no"),
        }
    )


def train(tmp_path: Path, *, target: str, loss: str, **kwargs: Any) -> tuple[TrainingJob, Any]:
    job = TrainingJob(
        name="tb",
        data=kwargs.pop("data", frame()),
        target=target,
        algorithm="tboost",
        loss_function=loss,
        params=kwargs.pop("params", FAST),
        metrics=kwargs.pop("metrics", ["rmse"]),
        output_dir=str(tmp_path),
        evaluation=kwargs.pop("evaluation", EVALUATION),
        feature_columns=kwargs.pop("feature_columns", ["region", "age"]),
        **kwargs,
    )
    return job, job.run()


def fit_adapter(data: pl.DataFrame, **kwargs: Any) -> Any:
    return TBoostAlgorithm().fit(
        data,
        kwargs.pop("features", ["region", "age"]),
        kwargs.pop("cat_features", ["region"]),
        kwargs.pop("target", "claims"),
        kwargs.pop("weight", None),
        kwargs.pop("params", FAST),
        kwargs.pop("task", "regression"),
        loss=kwargs.pop("loss", "Poisson"),
        threads=kwargs.pop("threads", 1),
        seed=0,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("loss", "target", "extra", "objective", "link"),
    [
        ("RMSE", "severity", {}, "squared_error", "identity"),
        ("Poisson", "claims", {"offset": "exposure"}, "poisson", "log"),
        ("Gamma", "severity", {}, "gamma", "log"),
        ("Tweedie", "claims", {"variance_power": 1.4, "offset": "exposure"}, "tweedie", "log"),
    ],
)
def test_each_loss_trains_weighted_and_reloads_with_its_objective(
    tmp_path: Path, loss: str, target: str, extra: dict, objective: str, link: str
) -> None:
    _job, result = train(tmp_path, target=target, loss=loss, weight="weight", **extra)
    assert result.diagnostics_errors == []
    assert result.shap_summary and result.pdp_data
    assert result.shap_link == link
    assert result.tboost_tables["link"] == link
    assert Path(result.model_path).suffix == ".tboost"
    model = TBoostModel.load(result.model_path)
    assert (model.objective(), model.link) == (objective, link)
    if loss == "Tweedie":
        assert model.estimator.tweedie_rho == 1.4
    identity = load_contract(Path(result.model_path).parent / model_contract_filename("tb")).model
    assert identity.algorithm == "tboost"
    assert identity.engine_name == "t_boost"


def test_fit_evidence_records_the_ceiling_the_fitted_trees_and_why_they_stopped(
    tmp_path: Path,
) -> None:
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    evidence = result.fit_evidence
    assert evidence["rounds_configured"] == FAST["n_trees"]
    assert 0 < evidence["rounds_fitted"] <= FAST["n_trees"]
    assert evidence["stopping_reason"] == "validation"
    # Without validation rows or t-boost's own holdout, every round is kept.
    capped = fit_adapter(frame(), params={**FAST, "n_trees": 3, "validation_fraction": None})
    assert (capped.rounds_fitted, capped.stopping_reason) == (3, "none")


def test_the_loss_history_is_the_bag_mean_deviance_per_round() -> None:
    data = frame(1500)
    result = fit_adapter(
        data, offset="exposure", params={**FAST, "n_bags": 2}, eval_df=frame(400, seed=9)
    )
    evals = result.model.estimator.evals_result_
    history = result.loss_history
    assert len(history) == max(len(bag) for bag in evals["eval"]["deviance"])
    first = history[0]
    assert first["iteration"] == 1.0
    assert first["eval_deviance"] == pytest.approx(
        np.mean([bag[0] for bag in evals["eval"]["deviance"]])
    )
    assert first["train_deviance"] == pytest.approx(
        np.mean([bag[0] for bag in evals["train"]["deviance"]])
    )


def test_a_classification_offset_and_feature_weights_fail_before_fitting() -> None:
    with pytest.raises(HauteValidationError, match="classification does not support an offset"):
        fit_adapter(
            frame(), loss="Logloss", task="classification", target="claims", offset="exposure"
        )
    with pytest.raises(HauteValidationError, match="feature weights"):
        fit_adapter(frame(), feature_weights={"age": 2.0})


def test_an_rmse_offset_is_added_verbatim_at_fit_and_scoring() -> None:
    data = frame().with_columns(base=pl.col("age") * 10.0)
    model = fit_adapter(data, loss="RMSE", target="severity", offset="base").model
    assert model.offset_link == "identity"
    native = TBoostRegressor(**FAST, objective="squared_error", seed=0, n_jobs=1).fit(
        data.select("region", "age"),
        data["severity"].to_numpy(),
        offset=data["base"].to_numpy(),
    )
    features = data.select("region", "age")
    expected = native.predict_raw(features) + data["base"].to_numpy()
    np.testing.assert_array_equal(model.predict_margin(data), expected)
    np.testing.assert_array_equal(model.predict(data), expected)
    contributions = model.contributions(data)
    np.testing.assert_allclose(
        contributions.bias + contributions.values.sum(axis=1), expected, rtol=1e-6, atol=1e-6
    )


def test_a_feature_name_with_a_colon_trains_and_explains() -> None:
    data = frame(3000, seed=4).rename({"age": "age:years"})
    model = fit_adapter(
        data, features=["region", "age:years"], params={**FAST, "learning_rate": 0.3}
    ).model
    contributions = model.contributions(data)
    assert ("age:years",) in contributions.terms
    assert model.shapley_values(data).shape == (len(data), 2)


def test_an_offset_scores_the_rate_times_exposure_and_adds_log_offset_to_the_margin(
    tmp_path: Path,
) -> None:
    data = frame()
    _job, result = train(tmp_path, target="claims", loss="Poisson", offset="exposure")
    scoring = load_local_model(result.model_path)
    model = scoring.raw_model
    scored = score_frame(
        model=model,
        lf=data.lazy(),
        features=scoring.feature_names,
        cat_feature_names=scoring.cat_feature_names,
        flavor="tboost",
        offset_column=scoring.offset_column,
    ).collect()["prediction"]
    # Plain t-boost loads the file (the haute record is only metadata) and predicts a rate.
    native = TBoostRegressor.from_json(Path(result.model_path).read_text(encoding="utf-8"))
    features = data.select("region", "age")
    exposure = data["exposure"].to_numpy()
    # Haute serves exp(raw + log(exposure)); t-boost's own rate is rounded in float32.
    np.testing.assert_allclose(scored.to_numpy(), native.predict(features) * exposure, rtol=1e-6)
    np.testing.assert_allclose(
        model.predict_margin(data), native.predict_raw(features) + np.log(exposure), rtol=1e-12
    )
    doubled = data.with_columns(pl.col("exposure") * 2)
    np.testing.assert_allclose(model.predict(doubled), 2 * model.predict(data), rtol=1e-12)
    with pytest.raises(HauteValidationError, match="offset column 'exposure' is missing"):
        model.predict(data.drop("exposure"))


def test_the_fit_matches_an_independent_native_fit_with_the_weight_and_exposure() -> None:
    data = frame()
    fitted = fit_adapter(data, weight="weight", offset="exposure").model
    native = TBoostRegressor(**FAST, objective="poisson", seed=0, n_jobs=1).fit(
        data.select("region", "age"),
        data["claims"].to_numpy(),
        sample_weight=data["weight"].to_numpy(),
        exposure=data["exposure"].to_numpy(),
    )
    features = data.select("region", "age")
    expected = native.predict_raw(features) + np.log(data["exposure"].to_numpy())
    np.testing.assert_array_equal(fitted.predict_margin(data), expected)
    unweighted = fit_adapter(data, offset="exposure").model
    assert not np.array_equal(unweighted.predict_margin(data), expected)


def test_numeric_cells_are_right_closed_like_t_boosts_own_scoring() -> None:
    data = frame(3000, seed=4)
    model = fit_adapter(data, params={**FAST, "learning_rate": 0.3}).model
    age = next(
        t for t in TBoostAlgorithm().tboost_tables(model)["tables"] if t["features"] == ["age"]
    )
    axis = age["axes"][0]
    cuts = axis["cuts"]
    assert axis["labels"][:2] == ["Missing", f"<= {cuts[0]:.6g}"]
    assert axis["labels"][-1] == f"> {cuts[-1]:.6g}"
    # A value equal to a cut scores in the cell that ends at it.
    on_cut = pl.DataFrame({"region": ["east"], "age": [float(np.float32(cuts[1]))]})
    contributions = model.contributions(on_cut)
    column = contributions.terms.index(("age",))
    assert contributions.values[0, column] == pytest.approx(age["scores"][2], abs=1e-9)
    assert axis["labels"][2] == f"> {cuts[0]:.6g} to <= {cuts[1]:.6g}"


def test_contributions_rebuild_the_margin_and_shapley_values_share_its_total() -> None:
    data = frame(1500)
    model = fit_adapter(data, offset="exposure", params={**FAST, "max_interaction_order": 2}).model
    contributions = model.contributions(data)
    margin = contributions.bias + contributions.values.sum(axis=1)
    np.testing.assert_allclose(margin, model.predict_margin(data), rtol=1e-6, atol=1e-6)
    assert all(term in {("region",), ("age",), ("region", "age")} for term in contributions.terms)
    shapley = TBoostAlgorithm().shap_values(model, data, ["region", "age"], ["region"])
    assert shapley.shape == (len(data), 2)
    np.testing.assert_allclose(
        shapley.sum(axis=1), contributions.values.sum(axis=1), rtol=1e-9, atol=1e-9
    )


def test_a_feature_named_like_the_contribution_frames_columns_still_explains() -> None:
    data = frame(1500).rename({"age": "row_index", "region": "term"})
    model = fit_adapter(
        data, features=["term", "row_index"], cat_features=["term"], offset="exposure"
    ).model
    contributions = model.contributions(data)
    np.testing.assert_allclose(
        contributions.bias + contributions.values.sum(axis=1),
        model.predict_margin(data),
        rtol=1e-6,
        atol=1e-6,
    )
    shapley = model.shapley_values(data)
    np.testing.assert_allclose(shapley.sum(axis=1), contributions.values.sum(axis=1), atol=1e-9)


def test_unseen_levels_fail_and_nulls_score_in_the_missing_level() -> None:
    data = frame()
    model = fit_adapter(data).model
    assert None in model.categorical_levels["region"]
    with pytest.raises(HauteValidationError, match="not trained on: 'mars'"):
        model.predict(data.with_columns(pl.lit("mars").alias("region")))
    nulls = data.with_columns(pl.lit(None, dtype=pl.String).alias("region"))
    assert np.isfinite(model.predict(nulls)).all()


def test_reordered_categories_score_identically() -> None:
    data = frame()
    model = fit_adapter(data).model
    reordered = data.with_columns(pl.col("region").cast(pl.Enum(["south", "north", "east"])))
    assert np.array_equal(model.predict(reordered), model.predict(data))


def test_the_selection_fit_early_stops_on_the_validation_rows_and_the_refit_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int | None]] = []
    original = TBoostRegressor.fit

    def spy(self: Any, X: Any, y: Any, **kwargs: Any) -> Any:  # noqa: N803 - sklearn's name
        eval_set = kwargs.get("eval_set")
        seen.append((len(X), None if eval_set is None else len(eval_set[0])))
        return original(self, X, y, **kwargs)

    monkeypatch.setattr(TBoostRegressor, "fit", spy)
    evaluation = {**EVALUATION, "test": {"size": 0.25}}
    _job, result = train(
        tmp_path, target="claims", loss="Poisson", offset="exposure", evaluation=evaluation
    )
    (selection, eval_rows), (final, final_eval) = seen
    fit = result.evaluation["selection_fits"][0]
    assert (selection, eval_rows) == (fit["train_rows"], fit["validation_rows"])
    assert (final, final_eval) == (result.development_rows, None)


def test_without_a_refit_the_early_stopped_fit_is_the_published_model(tmp_path: Path) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    original = TBoostRegressor.fit

    def spy(self: Any, X: Any, y: Any, **kwargs: Any) -> Any:  # noqa: N803 - sklearn's name
        calls.append((self, kwargs))
        return original(self, X, y, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(TBoostRegressor, "fit", spy)
        _job, result = train(
            tmp_path,
            target="claims",
            loss="Poisson",
            offset="exposure",
            refit_on_development=False,
        )
    assert len(calls) == 1
    fitted, kwargs = calls[0]
    assert kwargs["eval_set"] is not None
    assert result.fit_evidence["stopping_reason"] == "validation"
    published = TBoostModel.load(result.model_path).estimator
    assert published.n_trees_per_bag_ == fitted.n_trees_per_bag_


def test_a_fit_is_identical_whatever_the_thread_allotment() -> None:
    data = frame()
    one = fit_adapter(data, threads=1).model.predict(data)
    four = fit_adapter(data, threads=4).model.predict(data)
    assert np.array_equal(one, four)


def test_a_group_plan_passes_its_column_to_every_fit_as_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], Any]] = []
    original = TBoostRegressor.fit

    def spy(self: Any, X: Any, y: Any, **kwargs: Any) -> Any:  # noqa: N803 - sklearn's name
        calls.append((list(X.columns), kwargs.get("groups")))
        return original(self, X, y, **kwargs)

    monkeypatch.setattr(TBoostRegressor, "fit", spy)
    data = frame()
    # An evaluation key is never a feature (TrainingJob refuses it), so the group
    # column reaches the fit only through ``groups``.
    features = ["region", "age"]
    evaluation = {
        "schema_version": 1,
        "strategy": "group",
        "group_column": "policy",
        "seed": 3,
        "validation": {"method": "single", "size": 0.2},
    }
    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        data=data,
        evaluation=evaluation,
        feature_columns=features,
    )
    assert len(calls) == 2  # the selection fit and the final refit
    for columns, groups in calls:
        assert columns == features
        assert groups is not None and set(groups) <= set(data["policy"].to_list())
    assert len(calls[-1][1]) == result.development_rows


def _cell(axis: dict[str, Any], value: Any) -> int:
    """The displayed cell a feature value falls in, read from the report axis."""
    if axis["type"] == "nominal":
        label = "Missing" if value is None else str(value)
        return next(index for index, cell in enumerate(axis["labels"]) if label in cell.split(", "))
    if value is None:
        return 0
    cuts = axis["cuts"]
    return 1 + sum(value > cut for cut in cuts)


def test_the_table_report_reads_back_every_rows_contribution() -> None:
    data = frame(3000, seed=4)
    model = fit_adapter(
        data, offset="exposure", params={**FAST, "max_interaction_order": 2, "learning_rate": 0.3}
    ).model
    report = TBoostAlgorithm().tboost_tables(model)
    assert report["link"] == "log"
    assert [t["importance"] for t in report["tables"]] == sorted(
        (t["importance"] for t in report["tables"]), reverse=True
    )
    contributions = model.contributions(data)
    columns = {term: index for index, term in enumerate(contributions.terms)}
    assert {tuple(t["features"]) for t in report["tables"]} == set(columns)
    rows = data.to_dicts()
    for table in report["tables"]:
        scores = np.asarray(table["scores"])
        assert scores.shape == tuple(len(axis["labels"]) for axis in table["axes"])
        np.testing.assert_allclose(np.asarray(table["relativities"]), np.exp(scores))
        column = columns[tuple(table["features"])]
        for row_index, row in enumerate(rows):
            cell = tuple(_cell(axis, row[axis["feature"]]) for axis in table["axes"])
            assert scores[cell] == pytest.approx(
                contributions.values[row_index, column], abs=1e-9
            ), (table["term"], row)
    region = next(t for t in report["tables"] if t["features"] == ["region"])
    assert "Missing" in ", ".join(region["axes"][0]["labels"])


def test_a_cell_with_no_training_rows_reports_the_value_the_model_scores_there() -> None:
    rng = np.random.default_rng(7)
    region = rng.choice(["a", "b", "c"], 20_000)
    age = rng.uniform(18, 80, 20_000)
    keep = ~((region == "c") & (age < 30))  # no young driver in region c
    region, age = region[keep], age[keep]
    rate = np.exp(
        -2 + 0.6 * (age < 30) + 0.3 * (region == "b") + 0.5 * ((age < 30) & (region == "b"))
    )
    data = pl.DataFrame({"region": region, "age": age, "claims": rng.poisson(rate).astype(float)})
    params = {**FAST, "n_trees": 400, "max_interaction_order": 2}
    model = fit_adapter(data, params=params, threads=4).model
    table = next(t for t in TBoostAlgorithm().tboost_tables(model)["tables"] if t["order"] == 2)
    row = {"region": "c", "age": 20.0}
    cell = tuple(_cell(axis, row[axis["feature"]]) for axis in table["axes"])
    assert np.asarray(table["support"])[cell] == 0
    contributions = model.contributions(pl.DataFrame({k: [v] for k, v in row.items()}))
    column = contributions.terms.index(tuple(table["features"]))
    assert np.asarray(table["scores"])[cell] == pytest.approx(contributions.values[0, column])


@pytest.mark.parametrize(
    ("loss", "target", "weight", "offset"),
    [
        ("Poisson", "claims", None, None),
        ("Poisson", "claims", "weight", None),
        ("Poisson", "claims", None, "exposure"),
        ("Poisson", "claims", "weight", "exposure"),
        # An identity-link offset shifts the target; it never enters the mass.
        ("RMSE", "severity", "weight", "exposure"),
    ],
)
def test_support_is_the_training_weight_times_a_log_link_exposure(
    loss: str, target: str, weight: str | None, offset: str | None
) -> None:
    data = frame()
    model = fit_adapter(data, loss=loss, target=target, weight=weight, offset=offset).model
    expected = np.ones(len(data))
    if weight:
        expected = expected * data[weight].to_numpy()
    if offset and loss != "RMSE":
        expected = expected * data[offset].to_numpy()
    for table in TBoostAlgorithm().tboost_tables(model)["tables"]:
        assert np.asarray(table["support"]).sum() == pytest.approx(expected.sum(), rel=1e-5)


def test_bags_at_the_ceiling_pass_haute_progress_gate_once() -> None:
    from haute.modelling._training_job import _FitRounds, _LiveRounds

    checks: list[int] = []
    sent: list[int] = []
    rounds = _FitRounds(
        _LiveRounds(clock=lambda: 0.0).fit(),
        lambda iteration, total, metrics, row: sent.append(iteration),
        lambda message, fraction: None,
        span=(0.0, 1.0),
        check_cancelled=lambda: checks.append(1),
        execution_context=None,
    )
    params = {**FAST, "n_trees": 30, "n_bags": 4, "validation_fraction": None}
    fit_adapter(frame(), threads=1, params=params, on_iteration=rounds)
    rounds.finish()
    assert len(checks) == 4 * 30 + 1
    # With the clock held still only the first round and the final one pass.
    assert sent == [1, 30]


def test_every_bags_rounds_reach_the_cancellation_check() -> None:
    calls: list[int] = []

    def on_iteration(iteration: int, total: int, metrics: Any, row: Any) -> None:
        calls.append(iteration)

    params = {**FAST, "n_trees": 30, "n_bags": 2, "validation_fraction": None}
    result = fit_adapter(frame(), threads=1, params=params, on_iteration=on_iteration)
    rounds = [len(bag) for bag in result.model.estimator.evals_result_["train"]["deviance"]]
    # Every round of both bags, then the final round once after the fit.
    assert sum(rounds) == 60 and len(calls) == 61
    assert calls == sorted(calls)
    assert calls.count(30) == 1 and calls[-1] == 30

    class CancelledError(Exception):
        pass

    def cancel_in_second_bag(iteration: int, total: int, metrics: Any, row: Any) -> None:
        calls.append(iteration)
        if len(calls) > 31:
            raise CancelledError

    calls.clear()
    with pytest.raises(CancelledError):
        fit_adapter(frame(), threads=1, params=params, on_iteration=cancel_in_second_bag)
    assert len(calls) == 32


def test_a_logit_or_identity_report_has_no_relativities() -> None:
    data = frame()
    model = fit_adapter(data, loss="RMSE", target="severity").model
    report = TBoostAlgorithm().tboost_tables(model)
    assert report["link"] == "identity"
    assert all(table["relativities"] is None for table in report["tables"])


def test_binary_string_labels_score_original_labels_from_the_probability(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="flag",
        loss="Logloss",
        task="classification",
        positive_class="yes",
        metrics=["auc"],
    )
    scoring = load_local_model(result.model_path, task="classification")
    scored = score_frame(
        model=scoring.raw_model,
        lf=frame().lazy(),
        features=scoring.feature_names,
        cat_feature_names=scoring.cat_feature_names,
        flavor="tboost",
        task="classification",
    ).collect()
    expected = np.where(scored["prediction_proba"].to_numpy() > 0.5, "yes", "no")
    assert scored["prediction"].to_list() == expected.tolist()
    assert result.tboost_tables["link"] == "logit"


def test_a_regression_model_refuses_to_score_as_a_classifier(tmp_path: Path) -> None:
    _job, result = train(tmp_path, target="severity", loss="RMSE")
    with pytest.raises(ConfigError, match="This t-boost model was trained for regression"):
        load_local_model(result.model_path, task="classification")


def test_an_explanation_lists_one_term_per_table(tmp_path: Path) -> None:
    _job, result = train(
        tmp_path,
        target="claims",
        loss="Poisson",
        offset="exposure",
        data=frame(8000, seed=4),
        params={**FAST, "max_interaction_order": 2, "learning_rate": 0.3},
    )
    scoring = load_local_model(result.model_path)
    explanation = explain_native_prediction(
        scoring, {"region": "north", "age": 24.0, "exposure": 0.5}
    )
    assert explanation["status"] == "ok"
    assert explanation["type"] == "tboost_tables"
    terms = {entry["term"]: entry for entry in explanation["contributions"]}
    assert {"region", "age"} <= set(terms)
    assert terms["region"]["term_type"] == "main"
    interaction = [entry for entry in terms.values() if entry["term_type"] == "interaction"]
    for entry in interaction:
        assert entry["term_features"] == ["region", "age"]
        assert entry["feature_value"] == {"region": "north", "age": 24.0}


def test_an_intercept_only_model_has_no_terms_and_still_explains(tmp_path: Path) -> None:
    data = frame().with_columns(pl.lit(1.0).alias("severity"))
    model = fit_adapter(data, loss="RMSE", target="severity").model
    contributions = model.contributions(data)
    assert contributions.terms == [] and contributions.values.shape == (len(data), 0)
    np.testing.assert_allclose(contributions.bias, model.predict_margin(data), rtol=1e-6)
    assert TBoostAlgorithm().tboost_tables(model)["tables"] == []
    path = tmp_path / "flat.tboost"
    model.save(path)
    explanation = explain_native_prediction(
        load_local_model(str(path)), {"region": "east", "age": 30.0}
    )
    assert explanation["status"] == "ok" and explanation["contributions"] == []


def test_save_and_load_round_trip_and_record_the_haute_metadata_once(tmp_path: Path) -> None:
    data = frame()
    model = fit_adapter(data, offset="exposure").model
    path = tmp_path / "model.tboost"
    model.save(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document["metadata"]) == {"haute"}
    assert document["metadata"]["haute"]["offset_column"] == "exposure"
    assert document["metadata"]["haute"]["features"] == ["region", "age"]
    assert TBoostModel.load(path).estimator.n_trees_ == model.estimator.n_trees_
    assert np.array_equal(TBoostModel.load(path).predict(data), model.predict(data))


def _saved(tmp_path: Path, **kwargs: Any) -> tuple[Path, dict[str, Any]]:
    model = fit_adapter(frame(), **kwargs).model
    path = tmp_path / "model.tboost"
    model.save(path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def _rewrite(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("features", [], "no valid feature list"),
        ("features", ["region", "region"], "no valid feature list"),
        ("categorical_levels", {"town": ["a"]}, "unknown features"),
        ("categorical_levels", {"region": [1]}, "invalid levels"),
        ("task", "ranking", "unknown task"),
        ("link", "logit", "link 'logit' for a regression"),
        ("offset_link", "identity", "'identity' offset under the log link"),
        ("offset_column", None, "offset link without an offset column"),
        ("class_labels", ["no", "yes"], "class labels for a regression"),
    ],
)
def test_a_malformed_record_is_refused(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    path, document = _saved(tmp_path, offset="exposure")
    document["metadata"]["haute"][field] = value
    _rewrite(path, document)
    with pytest.raises(ConfigError, match=message):
        load_local_model(str(path))


def test_a_record_with_extra_keys_or_none_at_all_is_refused(tmp_path: Path) -> None:
    path, document = _saved(tmp_path)
    document["metadata"]["haute"]["extra"] = 1
    _rewrite(path, document)
    with pytest.raises(ConfigError, match="has keys"):
        TBoostModel.load(path)
    document["metadata"] = {}
    _rewrite(path, document)
    with pytest.raises(HauteValidationError, match="not a t-boost model Haute trained"):
        TBoostModel.load(path)


def test_a_record_that_disagrees_with_the_native_model_is_refused(tmp_path: Path) -> None:
    path, document = _saved(tmp_path, loss="RMSE", target="severity")
    for field, value, message in [
        ("link", "log", "trained with squared_error"),
        ("features", ["age", "region"], "features do not match"),
        ("categorical_levels", {}, "categorical features do not match"),
        ("task", "classification", "link 'identity' for a classification"),
    ]:
        changed = json.loads(json.dumps(document))
        changed["metadata"]["haute"][field] = value
        _rewrite(path, changed)
        with pytest.raises(ConfigError, match=message):
            TBoostModel.load(path)


def test_a_classifier_record_on_a_regressor_and_a_multiclass_model_are_refused(
    tmp_path: Path,
) -> None:
    path, document = _saved(tmp_path, loss="RMSE", target="severity")
    document["metadata"]["haute"].update(
        task="classification", link="logit", class_labels=["no", "yes"]
    )
    _rewrite(path, document)
    with pytest.raises(ConfigError, match="holds a TBoostRegressor"):
        TBoostModel.load(path)

    data = frame()
    multiclass = TBoostClassifier(n_trees=50, n_bags=1, prune=False).fit(
        data.select("region", "age"), np.arange(len(data)) % 3
    )
    native = json.loads(multiclass.to_json())
    native["metadata"] = {
        "haute": {
            "features": ["region", "age"],
            "categorical_levels": {"region": ["east", "north", "south", None]},
            "task": "classification",
            "link": "logit",
            "offset_column": None,
            "offset_link": None,
            "class_labels": [0, 1],
        }
    }
    _rewrite(path, native)
    with pytest.raises(ConfigError, match="single-output t-boost regressors and binary"):
        TBoostModel.load(path)


def test_an_mlflow_run_artifact_loads_and_serves_through_the_shared_pyfunc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow

    from haute._mlflow_io import load_mlflow_model
    from haute._mlflow_utils import mlflow_fluent_operation, set_tracking_uri_preserving_env
    from haute._sandbox import set_project_root
    from haute.modelling._mlflow_log import resolve_tracking_backend
    from haute.modelling._native_pyfunc import package_native_model

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    data = frame()
    _job, result = train(tmp_path / "train", target="claims", loss="Poisson", offset="exposure")
    model_path = Path(result.model_path)
    expected = load_local_model(str(model_path)).raw_model.predict(data)
    package = package_native_model(
        model_path, model_path.parent / model_contract_filename("tb"), tmp_path / "package"
    )
    broken = tmp_path / "broken" / "model.tboost"
    broken.parent.mkdir()
    document = json.loads(model_path.read_text(encoding="utf-8"))
    document["metadata"]["haute"]["link"] = "identity"
    _rewrite(broken, document)
    with mlflow_fluent_operation():
        set_tracking_uri_preserving_env(mlflow, resolve_tracking_backend("")[0])
        with mlflow.start_run() as run:
            mlflow.log_artifact(str(model_path))
            mlflow.pyfunc.log_model(
                name="model",
                loader_module="haute.modelling._native_pyfunc",
                data_path=str(package),
            )
        with mlflow.start_run() as broken_run:
            mlflow.log_artifact(str(broken))
        served = mlflow.pyfunc.load_model(f"runs:/{run.info.run_id}/model").predict(
            data.to_pandas()
        )
    scoring = load_mlflow_model(source_type="run", run_id=run.info.run_id, task="regression")
    assert scoring.flavor == "tboost"
    assert np.array_equal(scoring.raw_model.predict(data), expected)
    np.testing.assert_allclose(served, expected, rtol=1e-12)
    with pytest.raises(ConfigError, match="'log' offset under the identity link"):
        load_mlflow_model(source_type="run", run_id=broken_run.info.run_id, task="regression")


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"learning_rate": 0.1}, "explicit n_trees"),
        ({"n_trees": 0}, "positive integer"),
        ({"n_trees": 100, "seed": 3}, "Haute owns it"),
        ({"n_trees": 100, "tweedie_rho": 1.5}, "Haute owns it"),
        ({"n_trees": 100, "dart_drop_rate": 0.1}, "is not supported"),
    ],
)
def test_config_rejects_invalid_tboost_settings(params: dict, message: str) -> None:
    config = {
        "target": "claims",
        "algorithm": "tboost",
        "loss_function": "Poisson",
        "params": params,
        "evaluation": EVALUATION,
        "feature_columns": ["region", "age"],
    }
    with pytest.raises(TrainingConfigError, match=message):
        build_training_job_kwargs(config, data="d.parquet")


def test_config_refuses_mae_crossentropy_and_feature_weights_and_accepts_a_valid_one() -> None:
    config = {
        "target": "claims",
        "algorithm": "tboost",
        "loss_function": "Poisson",
        "params": {"n_trees": 100},
        "evaluation": EVALUATION,
        "feature_columns": ["region", "age"],
        "monotone_constraints": {"age": 1},
    }
    assert build_training_job_kwargs(config, data="d.parquet")["algorithm"] == "tboost"
    with pytest.raises(TrainingConfigError, match="does not support the MAE loss"):
        build_training_job_kwargs({**config, "loss_function": "MAE"}, data="d.parquet")
    with pytest.raises(TrainingConfigError, match="does not support feature weights"):
        build_training_job_kwargs({**config, "feature_weights": {"age": 2.0}}, data="d.parquet")
    with pytest.raises(HauteValidationError, match="t-boost does not support the CrossEntropy"):
        TBOOST.native_loss("classification", "CrossEntropy")


def test_a_study_searches_parameters_and_refits_with_the_winning_ones(tmp_path: Path) -> None:
    from haute.schemas import TuningReportPayload

    _job, result = train(
        tmp_path,
        target="severity",
        loss="RMSE",
        tuning={
            "schema_version": 1,
            "trial_count": 5,
            "seed": 7,
            "metric": "rmse",
            "search_space": {"learning_rate": [0.1, 0.3], "max_interaction_order": [1, 2]},
        },
    )
    tuning = result.tuning
    winner = tuning["trials"][tuning["winner_trial_index"]]
    assert tuning["final_params"] == winner["resolved_params"]
    assert tuning["final_tree_count"] is None
    payload = TuningReportPayload.model_validate(
        {key: value for key, value in tuning.items() if value is not None}
    )
    assert payload.final_tree_count is None
