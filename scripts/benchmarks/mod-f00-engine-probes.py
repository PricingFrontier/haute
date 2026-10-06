"""MOD-F00 engine probes for XGBoost, LightGBM and InterpretML EBM.

Fits tiny real models and records the native behaviour the model-family plan
depends on: offsets, categorical handling, stopping and round conventions,
contribution sums, persistence, and EBM's ``bags`` leakage semantics.

Run from the repository root in an environment holding Haute's dependencies
plus the probed engines (see ``mod-f00-engine-probes.md``)::

    PYTHONPATH=src python scripts/benchmarks/mod-f00-engine-probes.py OUTPUT.json
"""

from __future__ import annotations

import inspect
import json
import platform
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RESULTS: dict[str, Any] = {}


def record(section: str, key: str, value: Any) -> None:
    if isinstance(value, np.generic):
        value = value.item()
    RESULTS.setdefault(section, {})[key] = value


def max_rel(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return float(np.max(np.abs(a - b) / np.maximum(1.0, np.abs(b))))


def fixture(n: int = 2000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice(["north", "south", "east", "west"], size=n)
    age = rng.uniform(18, 80, size=n)
    exposure = rng.uniform(0.2, 1.0, size=n)
    weight = rng.uniform(0.5, 2.0, size=n)
    rate = np.exp(-2.0 + 0.01 * (age - 40) + np.where(region == "north", 0.3, 0.0))
    claims = rng.poisson(rate * exposure)
    severity = rng.gamma(shape=2.0, scale=500 * np.exp(0.005 * age), size=n)
    flag = (rng.uniform(size=n) < 1 / (1 + np.exp(-(age - 50) / 10))).astype(int)
    frame = pd.DataFrame(
        {
            "region": pd.Categorical(region, categories=["east", "north", "south", "west"]),
            "age": age,
            "exposure": exposure,
            "weight": weight,
            "claims": claims.astype(float),
            "severity": severity,
            "flag": flag,
        }
    )
    frame["partition"] = np.where(np.arange(n) % 5 == 0, "validation", "train")
    return frame


FEATURES = ["region", "age"]


# ---------------------------------------------------------------- XGBoost
def probe_xgboost(frame: pd.DataFrame, workdir: Path) -> None:
    import xgboost as xgb

    record("versions", "xgboost", xgb.__version__)
    train = frame[frame.partition == "train"]
    valid = frame[frame.partition == "validation"]

    def dmatrix(part: pd.DataFrame, label: str, offset: bool = True) -> Any:
        return xgb.DMatrix(
            part[FEATURES],
            label=part[label],
            weight=part["weight"],
            base_margin=np.log(part["exposure"]) if offset else None,
            enable_categorical=True,
            nthread=1,
        )

    params = {
        "objective": "count:poisson",
        "tree_method": "hist",
        "nthread": 1,
        "seed": 0,
        "max_depth": 3,
        "eta": 0.3,
    }
    dtrain, dvalid = dmatrix(train, "claims"), dmatrix(valid, "claims")
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=400,
        evals=[(dvalid, "valid")],
        early_stopping_rounds=5,
        verbose_eval=False,
    )
    best = booster.best_iteration
    record("xgboost", "best_iteration", best)
    record("xgboost", "num_boosted_rounds_after_early_stop", booster.num_boosted_rounds())
    record(
        "xgboost",
        "best_iteration_is_zero_based",
        "best_iteration is the 0-based index; iteration_range=(0, best_iteration + 1)",
    )

    # Offset: margin prediction includes base_margin from the DMatrix.
    rng_range = (0, best + 1)
    margin_with = booster.predict(dvalid, output_margin=True, iteration_range=rng_range)
    margin_without = booster.predict(
        dmatrix(valid, "claims", offset=False), output_margin=True, iteration_range=rng_range
    )
    difference = margin_with - margin_without - np.log(valid["exposure"].to_numpy())
    config = json.loads(booster.save_config())
    base_score = float(config["learner"]["learner_model_param"]["base_score"].strip("[]"))
    record("xgboost", "base_score_in_config", base_score)
    record(
        "xgboost",
        "margin_with_minus_without_minus_offset_is_constant",
        float(np.ptp(difference)) < 1e-5,
    )
    record("xgboost", "margin_with_minus_without_minus_offset", float(np.mean(difference)))
    record(
        "xgboost",
        "that_constant_equals_minus_log_base_score",
        abs(float(np.mean(difference)) + np.log(base_score)) < 1e-5,
    )
    response = booster.predict(dvalid, iteration_range=rng_range)
    record("xgboost", "response_is_exp_margin", max_rel(response, np.exp(margin_with)) < 1e-6)

    # Contributions: sum equals the margin including base_margin?
    contribs = booster.predict(dvalid, pred_contribs=True, iteration_range=rng_range)
    record("xgboost", "contrib_shape", list(contribs.shape))
    record(
        "xgboost", "contrib_sum_minus_margin_max_rel", max_rel(contribs.sum(axis=1), margin_with)
    )
    record(
        "xgboost",
        "contrib_sum_vs_margin_without_offset_max_rel",
        max_rel(contribs.sum(axis=1), margin_without),
    )

    # Persistence of a model trimmed to the selected range.
    trimmed = booster[: best + 1]
    path = workdir / "model.ubj"
    trimmed.save_model(path)
    reloaded = xgb.Booster()
    reloaded.load_model(path)
    reload_pred = reloaded.predict(dvalid)
    record("xgboost", "trimmed_reload_rounds", reloaded.num_boosted_rounds())
    record("xgboost", "trimmed_reload_bit_identical", bool(np.array_equal(reload_pred, response)))
    cfg = json.loads(reloaded.save_config())
    record("xgboost", "objective_in_saved_config", cfg["learner"]["objective"]["name"])
    record("xgboost", "feature_names_after_reload", reloaded.feature_names)
    record("xgboost", "feature_types_after_reload", reloaded.feature_types)

    # Category order: scoring frame with categories declared in another order.
    reordered = valid[FEATURES].copy()
    reordered["region"] = reordered["region"].cat.reorder_categories(
        ["west", "south", "north", "east"]
    )
    try:
        pred_reordered = reloaded.predict(
            xgb.DMatrix(reordered, base_margin=np.log(valid["exposure"]), enable_categorical=True)
        )
        record(
            "xgboost",
            "reordered_categories_same_predictions",
            bool(np.allclose(pred_reordered, reload_pred, rtol=1e-6)),
        )
    except Exception as exc:  # noqa: BLE001 - recorded evidence
        record("xgboost", "reordered_categories_error", repr(exc)[:300])

    # Unseen category at predict time.
    unseen = valid[FEATURES].head(5).copy()
    unseen["region"] = pd.Categorical(["mars"] * 5)
    try:
        out = reloaded.predict(xgb.DMatrix(unseen, enable_categorical=True))
        record("xgboost", "unseen_category", f"no error; predictions {out[:2].tolist()}")
    except Exception as exc:  # noqa: BLE001
        record("xgboost", "unseen_category", f"error: {repr(exc)[:300]}")

    # No-validation fit uses exactly the configured rounds?
    plain = xgb.train(params, dtrain, num_boost_round=17)
    record("xgboost", "no_validation_rounds_for_17", plain.num_boosted_rounds())

    # Objectives exist for the translated Haute losses.
    accepted = {}
    for objective, label, extra in (
        ("reg:squarederror", "severity", {}),
        ("reg:absoluteerror", "severity", {}),
        ("reg:gamma", "severity", {}),
        ("reg:tweedie", "claims", {"tweedie_variance_power": 1.5}),
        ("binary:logistic", "flag", {}),
    ):
        try:
            xgb.train(
                {**params, "objective": objective, **extra},
                dmatrix(train, label, offset=False),
                num_boost_round=3,
            )
            accepted[objective] = True
        except Exception as exc:  # noqa: BLE001
            accepted[objective] = repr(exc)[:200]
    record("xgboost", "objectives_accepted", accepted)

    # Unknown parameters: warning, error, or silence?
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        xgb.train({**params, "not_a_param": 1}, dtrain, num_boost_round=1)
    record("xgboost", "unknown_param_python_warnings", [str(w.message)[:200] for w in caught])


# ---------------------------------------------------------------- LightGBM
def probe_lightgbm(frame: pd.DataFrame, workdir: Path) -> None:
    import lightgbm as lgb

    record("versions", "lightgbm", lgb.__version__)
    train = frame[frame.partition == "train"]
    valid = frame[frame.partition == "validation"]
    params = {
        "objective": "poisson",
        "num_threads": 1,
        "seed": 0,
        "verbosity": -1,
        "num_leaves": 8,
        "learning_rate": 0.3,
    }

    dtrain = lgb.Dataset(
        train[FEATURES],
        label=train["claims"],
        weight=train["weight"],
        init_score=np.log(train["exposure"]),
        categorical_feature=["region"],
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        valid[FEATURES],
        label=valid["claims"],
        weight=valid["weight"],
        init_score=np.log(valid["exposure"]),
        reference=dtrain,
    )
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=400,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(5, verbose=False)],
    )
    best = booster.best_iteration
    record("lightgbm", "best_iteration", best)
    record("lightgbm", "current_iteration_after_early_stop", booster.current_iteration())
    record(
        "lightgbm",
        "best_iteration_is_count",
        "best_iteration is a 1-based count; predict(num_iteration=best_iteration)",
    )

    raw = booster.predict(valid[FEATURES], num_iteration=best, raw_score=True)
    response = booster.predict(valid[FEATURES], num_iteration=best)
    log_exposure = np.log(valid["exposure"].to_numpy())
    record("lightgbm", "predict_ignores_init_score", max_rel(response, np.exp(raw)) < 1e-9)
    served = np.exp(raw + log_exposure)
    record("lightgbm", "served_equals_exp_raw_plus_offset", True)

    contribs = booster.predict(valid[FEATURES], num_iteration=best, pred_contrib=True)
    record("lightgbm", "contrib_shape", list(np.asarray(contribs).shape))
    record(
        "lightgbm", "contrib_sum_minus_raw_max_rel", max_rel(np.asarray(contribs).sum(axis=1), raw)
    )

    path = workdir / "model.lgbm"
    booster.save_model(path, num_iteration=best)
    reloaded = lgb.Booster(model_file=str(path))
    reload_raw = reloaded.predict(valid[FEATURES], raw_score=True)
    record("lightgbm", "saved_best_reload_trees", reloaded.num_trees())
    record("lightgbm", "saved_best_reload_bit_identical", bool(np.array_equal(reload_raw, raw)))
    text = path.read_text(encoding="utf-8")
    record(
        "lightgbm",
        "objective_line_in_model_text",
        next(line for line in text.splitlines() if line.startswith("objective=")),
    )
    record("lightgbm", "pandas_categorical_stored", "pandas_categorical:" in text)
    record("lightgbm", "served_prediction_example", float(served[0]))

    reordered = valid[FEATURES].copy()
    reordered["region"] = reordered["region"].cat.reorder_categories(
        ["west", "south", "north", "east"]
    )
    try:
        pred_reordered = reloaded.predict(reordered, raw_score=True)
        record(
            "lightgbm",
            "reordered_categories_same_predictions",
            bool(np.allclose(pred_reordered, reload_raw, rtol=1e-9)),
        )
    except Exception as exc:  # noqa: BLE001
        record("lightgbm", "reordered_categories_error", repr(exc)[:300])

    unseen = valid[FEATURES].head(5).copy()
    unseen["region"] = pd.Categorical(["mars"] * 5, categories=["east", "mars"])
    try:
        out = reloaded.predict(unseen, raw_score=True)
        record("lightgbm", "unseen_category", f"no error; raw {out[:2].tolist()}")
    except Exception as exc:  # noqa: BLE001
        record("lightgbm", "unseen_category", f"error: {repr(exc)[:300]}")

    # Native exhaustion without validation: constant features cannot split.
    constant = pd.DataFrame({"c": np.ones(200)})
    exhausted = lgb.train(
        {**params, "objective": "regression"},
        lgb.Dataset(constant, label=np.arange(200, dtype=float)),
        num_boost_round=17,
    )
    record("lightgbm", "constant_feature_configured_rounds", 17)
    record("lightgbm", "constant_feature_current_iteration", exhausted.current_iteration())
    record("lightgbm", "constant_feature_num_trees", exhausted.num_trees())
    plain = lgb.train(params, dtrain, num_boost_round=17)
    record("lightgbm", "no_validation_rounds_for_17", plain.current_iteration())

    # Conflicting aliases.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alias = lgb.train(
            {**params, "num_leaves": 8, "num_leaf": 4},
            lgb.Dataset(train[FEATURES], label=train["claims"]),
            num_boost_round=1,
        )
    record("lightgbm", "alias_conflict_warnings", [str(w.message)[:200] for w in caught])
    record("lightgbm", "alias_conflict_effective_num_leaves", alias.params.get("num_leaves"))

    accepted = {}
    for objective, label, extra in (
        ("regression", "severity", {}),
        ("regression_l1", "severity", {}),
        ("gamma", "severity", {}),
        ("tweedie", "claims", {"tweedie_variance_power": 1.5}),
        ("binary", "flag", {}),
    ):
        try:
            lgb.train(
                {**params, "objective": objective, **extra},
                lgb.Dataset(train[FEATURES], label=train[label]),
                num_boost_round=3,
            )
            accepted[objective] = True
        except Exception as exc:  # noqa: BLE001
            accepted[objective] = repr(exc)[:200]
    record("lightgbm", "objectives_accepted", accepted)


# ---------------------------------------------------------------- EBM
def _ebm(objective: str, classification: bool, **kwargs: Any) -> Any:
    from interpret.glassbox import ExplainableBoostingClassifier, ExplainableBoostingRegressor

    cls = ExplainableBoostingClassifier if classification else ExplainableBoostingRegressor
    return cls(
        objective=objective,
        outer_bags=1,
        n_jobs=1,
        random_state=0,
        feature_types=["nominal", "continuous"],
        **kwargs,
    )


def probe_ebm(frame: pd.DataFrame, workdir: Path) -> None:
    import interpret
    from interpret.glassbox import ExplainableBoostingRegressor

    record("versions", "interpret_core", interpret.__version__)
    fit_sig = inspect.signature(ExplainableBoostingRegressor.fit)
    predict_sig = inspect.signature(ExplainableBoostingRegressor.predict)
    init_sig = inspect.signature(ExplainableBoostingRegressor.__init__)
    record("ebm", "fit_signature", str(fit_sig))
    record("ebm", "predict_signature", str(predict_sig))
    record("ebm", "has_monotone_constraints", "monotone_constraints" in init_sig.parameters)
    record("ebm", "has_to_json", hasattr(ExplainableBoostingRegressor, "to_json"))
    record(
        "ebm",
        "has_from_json",
        hasattr(ExplainableBoostingRegressor, "from_json")
        or hasattr(interpret.glassbox, "from_json"),
    )

    x = frame[["region", "age"]].copy()
    x["region"] = x["region"].astype(str)
    is_valid = (frame.partition == "validation").to_numpy()
    bags = np.where(is_valid, -1, 1).astype(np.int8).reshape(-1, 1)

    cases = {
        "rmse": ("rmse", "severity", False, None),
        "poisson_deviance": ("poisson_deviance", "claims", False, "log"),
        "gamma_deviance": ("gamma_deviance", "severity", False, "log"),
        "tweedie_deviance": ("tweedie_deviance:variance_power=1.5", "claims", False, "log"),
        "log_loss": ("log_loss", "flag", True, None),
    }
    leakage: dict[str, Any] = {}
    for name, (objective, label, classification, offset) in cases.items():
        y = frame[label].to_numpy().astype(float if not classification else int)
        init = np.log(frame["exposure"].to_numpy()) if offset else None
        perturbed = y.copy()
        rng = np.random.default_rng(1)
        if classification:
            perturbed[is_valid] = 1 - perturbed[is_valid]
        else:
            perturbed[is_valid] = perturbed[is_valid] * rng.uniform(0.2, 5.0, is_valid.sum())
        fits = []
        for target in (y, perturbed):
            model = _ebm(
                objective, classification, interactions=2, max_rounds=60, early_stopping_rounds=0
            )
            model.fit(
                x, target, sample_weight=frame["weight"].to_numpy(), bags=bags, init_score=init
            )
            fits.append(model)
        a, b = fits
        same_terms = a.term_features_ == b.term_features_
        same_scores = same_terms and all(
            np.array_equal(sa, sb) for sa, sb in zip(a.term_scores_, b.term_scores_)
        )
        pa = a.predict(x, init_score=init) if not classification else a.predict_proba(x)[:, 1]
        pb = b.predict(x, init_score=init) if not classification else b.predict_proba(x)[:, 1]
        leakage[name] = {
            "interaction_terms_equal": bool(same_terms),
            "term_scores_equal": bool(same_scores),
            "intercept_equal": bool(np.array_equal(a.intercept_, b.intercept_)),
            "predictions_equal": bool(np.array_equal(pa, pb)),
        }
    record("ebm", "bags_leakage_fixed_budget", leakage)

    # Early stopping driven by the -1 bag rows.
    y = frame["claims"].to_numpy().astype(float)
    init = np.log(frame["exposure"].to_numpy())
    stopping = _ebm(
        "poisson_deviance",
        False,
        interactions=0,
        max_rounds=5000,
        early_stopping_rounds=20,
        validation_size=0.0,
    )
    try:
        stopping.fit(x, y, sample_weight=frame["weight"].to_numpy(), bags=bags, init_score=init)
        record(
            "ebm",
            "bags_early_stopping_best_iteration",
            np.asarray(stopping.best_iteration_).tolist(),
        )
        record("ebm", "bags_early_stopping_ok", True)
    except Exception as exc:  # noqa: BLE001
        record("ebm", "bags_early_stopping_error", repr(exc)[:300])

    # Offset at predict time.
    fitted = _ebm("poisson_deviance", False, interactions=0, max_rounds=50, early_stopping_rounds=0)
    fitted.fit(x, y, init_score=init, bags=np.ones((len(y), 1), dtype=np.int8))
    with_offset = fitted.predict(x, init_score=init)
    without_offset = fitted.predict(x)
    record(
        "ebm",
        "predict_init_score_multiplies_log_link",
        max_rel(with_offset, without_offset * frame["exposure"].to_numpy()) < 1e-9,
    )
    local = fitted.eval_terms(x) if hasattr(fitted, "eval_terms") else None
    if local is not None:
        margin = np.asarray(local).sum(axis=1) + fitted.intercept_
        record(
            "ebm",
            "eval_terms_sum_plus_intercept_vs_log_predict_max_rel",
            max_rel(margin, np.log(without_offset)),
        )

    # Persistence: joblib payload globals, for the restricted-loader allowlist.
    import joblib

    path = workdir / "model.ebm"
    joblib.dump(fitted, path)
    # Record every global the payload resolves, for the restricted-loader allowlist.
    seen: list[tuple[str, str]] = []

    from joblib import numpy_pickle

    original_find_class = numpy_pickle.NumpyUnpickler.find_class

    def recording_find_class(self: Any, module: str, name: str) -> Any:
        seen.append((module, name))
        return original_find_class(self, module, name)

    numpy_pickle.NumpyUnpickler.find_class = recording_find_class  # type: ignore[method-assign]
    try:
        joblib.load(path)
    finally:
        numpy_pickle.NumpyUnpickler.find_class = original_find_class  # type: ignore[method-assign]
    record("ebm", "joblib_globals", sorted(set(f"{m}:{n}" for m, n in seen)))
    loaded = joblib.load(path)
    record(
        "ebm",
        "joblib_reload_bit_identical",
        bool(np.array_equal(loaded.predict(x, init_score=init), with_offset)),
    )
    # Haute's restricted loader: unchanged, then with only the EBM classes added.

    from haute import _sandbox

    _sandbox.set_project_root(workdir)
    try:
        _sandbox.safe_joblib_load(path)
        record("ebm", "haute_safe_joblib_load_unchanged", "loaded")
    except Exception as exc:  # noqa: BLE001
        record("ebm", "haute_safe_joblib_load_unchanged", f"blocked: {str(exc)[:160]}")
    ebm_classes = {
        ("interpret.glassbox._ebm._ebm", "ExplainableBoostingRegressor"),
        ("interpret.glassbox._ebm._ebm", "ExplainableBoostingClassifier"),
    }
    original_classes = _sandbox._ALLOWED_PICKLE_CLASSES
    _sandbox._ALLOWED_PICKLE_CLASSES = original_classes | ebm_classes
    try:
        restricted = _sandbox.safe_joblib_load(path)
        record(
            "ebm",
            "haute_safe_joblib_load_with_ebm_classes_bit_identical",
            bool(np.array_equal(restricted.predict(x, init_score=init), with_offset)),
        )
        classifier = _ebm("log_loss", True, interactions=0, max_rounds=30, early_stopping_rounds=0)
        classifier.fit(x, frame["flag"].to_numpy(), bags=np.ones((len(x), 1), dtype=np.int8))
        class_path = workdir / "classifier.ebm"
        joblib.dump(classifier, class_path)
        seen.clear()
        numpy_pickle.NumpyUnpickler.find_class = recording_find_class  # type: ignore[method-assign]
        try:
            joblib.load(class_path)
        finally:
            numpy_pickle.NumpyUnpickler.find_class = original_find_class  # type: ignore[method-assign]
        record("ebm", "classifier_joblib_globals", sorted(set(f"{m}:{n}" for m, n in seen)))
        reloaded_classifier = _sandbox.safe_joblib_load(class_path)
        record(
            "ebm",
            "classifier_restricted_reload_bit_identical",
            bool(np.array_equal(reloaded_classifier.predict_proba(x), classifier.predict_proba(x))),
        )
        record("ebm", "classifier_classes", np.asarray(classifier.classes_).tolist())

        class Gadget:
            def __reduce__(self) -> Any:
                import os

                return (os.system, ("echo pwned",))

        gadget_path = workdir / "gadget.ebm"
        joblib.dump(Gadget(), gadget_path)
        try:
            _sandbox.safe_joblib_load(gadget_path)
            record("ebm", "crafted_payload", "LOADED (unsafe)")
        except Exception as exc:  # noqa: BLE001
            record("ebm", "crafted_payload", f"blocked: {str(exc)[:120]}")
    finally:
        _sandbox._ALLOWED_PICKLE_CLASSES = original_classes
    record(
        "ebm",
        "state_hooks",
        {
            hook: next(
                (
                    cls.__module__ + "." + cls.__qualname__
                    for cls in ExplainableBoostingRegressor.__mro__
                    if hook in vars(cls)
                ),
                None,
            )
            for hook in ("__reduce__", "__reduce_ex__", "__setstate__", "__getstate__")
        },
    )
    record(
        "ebm",
        "fitted_attribute_types",
        sorted(
            {
                type(value).__module__ + "." + type(value).__qualname__
                for value in vars(fitted).values()
            }
        ),
    )


EBM_OBJECTIVES = ("rmse", "poisson_deviance", "gamma_deviance", "tweedie_deviance", "log_loss")
EBM_ALLOWED_GLOBALS = {
    "interpret.glassbox._ebm._ebm:ExplainableBoostingRegressor",
    "interpret.glassbox._ebm._ebm:ExplainableBoostingClassifier",
    "joblib.numpy_pickle:NumpyArrayWrapper",
    "numpy._core.multiarray:scalar",
    "numpy:dtype",
    "numpy:ndarray",
}


def _is_true(value: Any) -> bool:
    return value is True or value == "True"


def expectations() -> list[tuple[str, str, Any]]:
    """Every outcome the report relies on, as (section, key, predicate or value).

    The EBM ``bags`` leakage rows expect the intercept and predictions to change:
    that failure is the recorded finding, so a release where it stops failing is
    also an unexpected result that must be re-examined.
    """
    checks: list[tuple[str, str, Any]] = [
        ("xgboost", "margin_with_minus_without_minus_offset_is_constant", True),
        ("xgboost", "that_constant_equals_minus_log_base_score", True),
        ("xgboost", "response_is_exp_margin", True),
        ("xgboost", "trimmed_reload_bit_identical", True),
        ("xgboost", "objective_in_saved_config", "count:poisson"),
        ("xgboost", "reordered_categories_same_predictions", False),
        ("xgboost", "no_validation_rounds_for_17", 17),
        ("xgboost", "contrib_sum_minus_margin_max_rel", lambda v: v < 1e-5),
        ("xgboost", "unseen_category", lambda v: str(v).startswith("no error")),
        ("xgboost", "unknown_param_python_warnings", lambda v: bool(v)),
        ("xgboost", "objectives_accepted", lambda v: all(_is_true(x) for x in v.values())),
        ("lightgbm", "predict_ignores_init_score", True),
        ("lightgbm", "saved_best_reload_bit_identical", True),
        ("lightgbm", "pandas_categorical_stored", True),
        ("lightgbm", "reordered_categories_same_predictions", True),
        ("lightgbm", "objective_line_in_model_text", "objective=poisson"),
        ("lightgbm", "contrib_sum_minus_raw_max_rel", lambda v: v < 1e-9),
        ("lightgbm", "constant_feature_current_iteration", lambda v: v < 17),
        ("lightgbm", "no_validation_rounds_for_17", 17),
        ("lightgbm", "alias_conflict_warnings", []),
        ("lightgbm", "unseen_category", lambda v: str(v).startswith("no error")),
        ("lightgbm", "objectives_accepted", lambda v: all(_is_true(x) for x in v.values())),
        ("ebm", "bags_early_stopping_ok", True),
        ("ebm", "predict_init_score_multiplies_log_link", True),
        ("ebm", "eval_terms_sum_plus_intercept_vs_log_predict_max_rel", lambda v: v < 1e-9),
        ("ebm", "joblib_reload_bit_identical", True),
        ("ebm", "joblib_globals", lambda v: set(v) <= EBM_ALLOWED_GLOBALS),
        ("ebm", "classifier_joblib_globals", lambda v: set(v) <= EBM_ALLOWED_GLOBALS),
        ("ebm", "haute_safe_joblib_load_unchanged", lambda v: str(v).startswith("blocked")),
        ("ebm", "haute_safe_joblib_load_with_ebm_classes_bit_identical", True),
        ("ebm", "classifier_restricted_reload_bit_identical", True),
        ("ebm", "crafted_payload", lambda v: str(v).startswith("blocked")),
        (
            "ebm",
            "bags_leakage_fixed_budget",
            lambda v: (
                set(v) == set(EBM_OBJECTIVES)
                and all(
                    row["interaction_terms_equal"]
                    and row["term_scores_equal"]
                    and not row["intercept_equal"]
                    and not row["predictions_equal"]
                    for row in v.values()
                )
            ),
        ),
    ]
    return checks


def unexpected_results() -> list[str]:
    problems = [
        f"{section}.probe_error: {values['probe_error']}"
        for section, values in RESULTS.items()
        if "probe_error" in values
    ]
    xgb_results = RESULTS.get("xgboost", {})
    if "best_iteration" in xgb_results and xgb_results.get("trimmed_reload_rounds") != (
        xgb_results["best_iteration"] + 1
    ):
        problems.append("xgboost.trimmed_reload_rounds != best_iteration + 1")
    lgb_results = RESULTS.get("lightgbm", {})
    if (
        "best_iteration" in lgb_results
        and lgb_results.get("saved_best_reload_trees") != (lgb_results["best_iteration"])
    ):
        problems.append("lightgbm.saved_best_reload_trees != best_iteration")
    for section, key, expected in expectations():
        if key not in RESULTS.get(section, {}):
            problems.append(f"{section}.{key}: missing")
            continue
        value = RESULTS[section][key]
        if callable(expected):
            ok = bool(expected(value))
        elif isinstance(expected, bool):
            ok = _is_true(value) if expected else value is False or value == "False"
        else:
            ok = value == expected
        if not ok:
            problems.append(f"{section}.{key}: unexpected {value!r}")
    return problems


def main() -> None:
    out = Path(sys.argv[1])
    record("platform", "python", sys.version.split()[0])
    record("platform", "system", f"{platform.system()} {platform.machine()}")
    record("versions", "numpy", np.__version__)
    record("versions", "pandas", pd.__version__)
    frame = fixture()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for name, probe in (
            ("xgboost", probe_xgboost),
            ("lightgbm", probe_lightgbm),
            ("ebm", probe_ebm),
        ):
            try:
                probe(frame, workdir)
            except Exception as exc:  # noqa: BLE001 - one failing engine must not hide others
                record(name, "probe_error", repr(exc)[:500])
    out.write_text(
        json.dumps(RESULTS, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(RESULTS, indent=2, sort_keys=True, default=str))
    problems = unexpected_results()
    for problem in problems:
        print(f"UNEXPECTED: {problem}", file=sys.stderr)
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
