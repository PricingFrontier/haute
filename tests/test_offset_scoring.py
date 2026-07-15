"""Offset threading: fit → predict → metrics → contract → serve.

A model trained with an offset column must carry that offset through every
prediction surface, not just the fit:

* ``BaseAlgorithm.predict`` re-applies the offset (GLM: the rustystats model
  extracts its fit-time offset column from the scoring frame; CatBoost: the
  baseline is re-supplied via a ``Pool``).
* Training metrics/diagnostics are computed on offset-inclusive predictions.
* The offset column is declared in the MLflow signature and the feature
  contract, so a scoring payload without it fails loud — predictions are
  never silently produced on an offset-0/absent basis.
* Both scorers (canvas ``_run_score_pipeline`` and the deploy container
  path) apply the offset at score time.

Unit-basis semantics: an offset column that is constant 1 reproduces the
no-offset fit (for the log-link GLM exposure workflow this is exact — the
column is exposure on the response scale and ``log(1) == 0``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from haute.errors import FeatureMismatchError
from haute.modelling._feature_contract import (
    build_contract,
    load_contract,
    save_contract,
)
from haute.modelling._signature import build_signature

# ---------------------------------------------------------------------------
# Shared synthetic data: Poisson frequency with a real exposure effect
# ---------------------------------------------------------------------------


def _freq_frame(n: int = 1500, seed: int = 42) -> pl.DataFrame:
    """Poisson counts whose rate is proportional to an exposure column.

    Carries a categorical ``region`` column so CatBoost fits keep real
    feature names in the .cbm (the numeric-only pool path saves positional
    names, which breaks name-based scoring — a pre-existing gap outside
    this suite's scope).
    """
    rng = np.random.default_rng(seed)
    age = rng.uniform(20.0, 60.0, n)
    region = rng.choice(["north", "south"], n)
    exposure = rng.uniform(0.25, 2.0, n)
    lam = exposure * np.exp(-2.0 + 0.03 * age + np.where(region == "north", 0.2, 0.0))
    return pl.DataFrame(
        {
            "age": age,
            "region": region,
            "exposure": exposure,
            "claim_count": rng.poisson(lam).astype(np.float64),
        }
    )


def _fit_glm(df: pl.DataFrame, *, offset: str | None):
    """Fit a tiny Poisson log-link GLM via the haute algorithm wrapper."""
    from haute.modelling._rustystats import GLMAlgorithm

    algo = GLMAlgorithm()
    params: dict = {
        "terms": {"age": {"type": "linear"}},
        "family": "poisson",
        "link": "log",
    }
    fit = algo.fit(
        df,
        ["age"],
        [],
        "claim_count",
        None,
        params,
        "regression",
        offset=offset,
    )
    return algo, fit.model


def _fit_catboost(df: pl.DataFrame, *, offset: str | None, loss: str = "Poisson"):
    from haute.modelling._algorithms import CatBoostAlgorithm

    algo = CatBoostAlgorithm()
    fit = algo.fit(
        df,
        ["age", "region"],
        ["region"],
        "claim_count",
        None,
        {"iterations": 30, "depth": 3, "verbose": 0, "loss_function": loss},
        "regression",
        offset=offset,
    )
    return algo, fit.model


# ---------------------------------------------------------------------------
# 1. Algorithm-level predict re-applies the offset
# ---------------------------------------------------------------------------


class TestGLMPredictOffset:
    def test_predict_scales_linearly_with_exposure(self) -> None:
        """Response-scale GLM predictions must carry the exposure effect:
        doubling the offset column doubles the prediction (log link)."""
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        df = _freq_frame()
        algo, model = _fit_glm(df, offset="exposure")

        preds = algo.predict(model, df, ["age"], offset="exposure")
        doubled = algo.predict(
            model,
            df.with_columns(pl.col("exposure") * 2),
            ["age"],
            offset="exposure",
        )
        np.testing.assert_allclose(doubled, preds * 2.0, rtol=1e-10)

    def test_predict_missing_offset_column_fails_loud(self) -> None:
        """A scoring frame without the offset column must raise, never
        silently score on an offset-absent basis."""
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        df = _freq_frame()
        algo, model = _fit_glm(df, offset="exposure")

        with pytest.raises((ValueError, FeatureMismatchError), match="exposure"):
            algo.predict(model, df.drop("exposure"), ["age"], offset="exposure")

    def test_constant_one_offset_reproduces_no_offset_fit(self) -> None:
        """offset ≡ 1 is the unit basis: identical predictions to a model
        trained without any offset (log(1) == 0, exactly)."""
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        df = _freq_frame().with_columns(pl.lit(1.0).alias("unit"))
        algo_u, model_u = _fit_glm(df, offset="unit")
        algo_n, model_n = _fit_glm(df, offset=None)

        preds_unit = algo_u.predict(model_u, df, ["age"], offset="unit")
        preds_none = algo_n.predict(model_n, df, ["age"])
        np.testing.assert_allclose(preds_unit, preds_none, rtol=1e-6)


class TestCatBoostPredictOffset:
    def test_predict_reapplies_baseline(self) -> None:
        """CatBoost predictions with the offset kwarg must include the
        baseline: for a Poisson loss the raw-score baseline multiplies the
        response prediction by exp(baseline)."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        df = _freq_frame()
        algo, model = _fit_catboost(df, offset="exposure")

        with_offset = algo.predict(model, df, ["age", "region"], offset="exposure")
        without = algo.predict(model, df, ["age", "region"])
        expected_ratio = np.exp(df["exposure"].to_numpy())
        np.testing.assert_allclose(with_offset / without, expected_ratio, rtol=1e-5)

    def test_predict_missing_offset_column_fails_loud(self) -> None:
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        df = _freq_frame()
        algo, model = _fit_catboost(df, offset="exposure")

        with pytest.raises((ValueError, FeatureMismatchError), match="exposure"):
            algo.predict(model, df.drop("exposure"), ["age", "region"], offset="exposure")

    def test_saved_model_self_describes_offset(self, haute_scratch: Path) -> None:
        """The .cbm artifact records the offset column so scorers loading
        the bare model file know the offset must be re-supplied."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model

        df = _freq_frame()
        algo, model = _fit_catboost(df, offset="exposure")
        model_path = haute_scratch / "freq.cbm"
        algo.save(model, model_path)

        scoring_model = load_local_model(str(model_path), "regression")
        assert scoring_model.offset_column == "exposure"

    def test_saved_model_without_offset_has_none(self, haute_scratch: Path) -> None:
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model

        df = _freq_frame()
        algo, model = _fit_catboost(df, offset=None)
        model_path = haute_scratch / "freq_no_offset.cbm"
        algo.save(model, model_path)

        scoring_model = load_local_model(str(model_path), "regression")
        assert scoring_model.offset_column is None


# ---------------------------------------------------------------------------
# 2. Training metrics are computed on offset-inclusive predictions
# ---------------------------------------------------------------------------


class TestTrainingMetricsIncludeOffset:
    def test_glm_training_with_offset_runs_and_scales(self, haute_scratch: Path) -> None:
        """End-to-end TrainingJob with a GLM offset: the run completes (the
        metrics step must predict WITH the offset — an offset-less predict
        raises inside rustystats) and the saved model still carries the
        exposure effect."""
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        df = _freq_frame()
        job = TrainingJob(
            name="glm_offset",
            data=df,
            target="claim_count",
            algorithm="glm",
            params={
                "terms": {"age": {"type": "linear"}},
                "family": "poisson",
                "link": "log",
            },
            offset="exposure",
            metrics=["rmse"],
            output_dir=str(haute_scratch),
        )
        result = job.run()
        assert "exposure" not in result.features
        assert np.isfinite(result.metrics["rmse"])

    def test_catboost_metrics_use_offset_inclusive_predictions(
        self, haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RMSE-loss CatBoost with a large additive offset: if the metrics
        step dropped the baseline, every prediction would be off by ~10 and
        RMSE would blow up.  Offset-inclusive predictions keep it small."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])

        rng = np.random.default_rng(7)
        n = 600
        x = rng.uniform(0.0, 1.0, n)
        base = np.full(n, 10.0)
        df = pl.DataFrame(
            {
                "x": x,
                "base": base,
                "y": x + base + rng.normal(0.0, 0.05, n),
            }
        )
        job = TrainingJob(
            name="cb_offset_metrics",
            data=df,
            target="y",
            offset="base",
            params={"iterations": 60, "depth": 3, "verbose": 0, "loss_function": "RMSE"},
            metrics=["rmse"],
            output_dir=str(haute_scratch),
        )
        result = job.run()
        assert result.metrics["rmse"] < 2.0, (
            "validation RMSE must be computed on offset-inclusive predictions; "
            f"an offset-dropping metrics path scores ~10 off (got {result.metrics['rmse']})"
        )


# ---------------------------------------------------------------------------
# 3. Signature + feature contract declare the offset column
# ---------------------------------------------------------------------------


class TestOffsetInSignatureAndContract:
    def test_build_signature_declares_offset_input(self) -> None:
        sig = build_signature(
            features=["age"],
            feature_types={"age": "Float64"},
            categorical_features=[],
            target_name="claim_count",
            target_type="Float64",
            task="regression",
            offset_name="exposure",
            offset_type="Float64",
        )
        assert "exposure" in sig.inputs.input_names()

    def test_build_signature_rejects_offset_shadowing_feature(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            build_signature(
                features=["age"],
                feature_types={"age": "Float64"},
                categorical_features=[],
                target_name="claim_count",
                target_type="Float64",
                task="regression",
                offset_name="age",
                offset_type="Float64",
            )

    def test_contract_roundtrips_offset_column(self, haute_scratch: Path) -> None:
        contract = build_contract(
            features=["age"],
            feature_types={"age": "Float64"},
            categorical_features=[],
            target_name="claim_count",
            target_type="Float64",
            task="regression",
            offset_column="exposure",
        )
        assert contract.offset_column == "exposure"
        path = haute_scratch / "contract.json"
        save_contract(contract, path)
        loaded = load_contract(path)
        assert loaded.offset_column == "exposure"
        assert loaded.contract_hash == contract.contract_hash

    def test_contract_offset_mismatch_is_loud(self) -> None:
        from haute.modelling._feature_contract import assert_contracts_match

        with_offset = build_contract(
            features=["age"],
            feature_types={"age": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
            offset_column="exposure",
        )
        without_offset = build_contract(
            features=["age"],
            feature_types={"age": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        with pytest.raises(FeatureMismatchError, match="offset_column"):
            assert_contracts_match(with_offset, without_offset)

    def test_offsetless_contract_hash_is_stable(self) -> None:
        """Adding the offset field must not change the hash of contracts
        that have no offset — existing deployed artifacts stay valid."""
        contract = build_contract(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="ClaimCount",
            target_type="Int64",
            task="regression",
        )
        # Hash pinned from the pre-offset contract format.
        payload = {
            "features": ["age", "region"],
            "feature_types": {"age": "Int64", "region": "String"},
            "categorical_features": ["region"],
            "target_name": "ClaimCount",
            "target_type": "Int64",
            "task": "regression",
        }
        import hashlib
        import json

        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert contract.contract_hash == expected

    def test_training_writes_offset_into_contract(self, haute_scratch: Path) -> None:
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute.modelling._training_job import TrainingJob, model_contract_filename

        df = _freq_frame()
        job = TrainingJob(
            name="glm_offset_contract",
            data=df,
            target="claim_count",
            algorithm="glm",
            params={
                "terms": {"age": {"type": "linear"}},
                "family": "poisson",
                "link": "log",
            },
            offset="exposure",
            metrics=["rmse"],
            output_dir=str(haute_scratch),
        )
        job.run()
        contract = load_contract(haute_scratch / model_contract_filename("glm_offset_contract"))
        assert contract.offset_column == "exposure"
        assert "exposure" not in contract.features


# ---------------------------------------------------------------------------
# 4. Scorers: canvas path and deploy container path
# ---------------------------------------------------------------------------


def _glm_scoring_model(haute_scratch: Path):
    from haute._mlflow_io import load_local_model

    df = _freq_frame()
    algo, model = _fit_glm(df, offset="exposure")
    model_path = haute_scratch / "glm_offset.rsglm"
    algo.save(model, model_path)
    return df, load_local_model(str(model_path), "regression")


class TestCanvasScorerOffset:
    def test_glm_scoring_missing_offset_column_fails_loud(self, haute_scratch: Path) -> None:
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute._model_scorer import _run_score_pipeline

        df, scoring_model = _glm_scoring_model(haute_scratch)
        with pytest.raises(FeatureMismatchError):
            _run_score_pipeline(
                scoring_model,
                df.drop("exposure", "claim_count").lazy(),
                task="regression",
                output_col="prediction",
            ).collect()

    def test_glm_scoring_applies_offset(self, haute_scratch: Path) -> None:
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute._model_scorer import _run_score_pipeline

        df, scoring_model = _glm_scoring_model(haute_scratch)
        base = (
            _run_score_pipeline(
                scoring_model,
                df.drop("claim_count").lazy(),
                task="regression",
                output_col="prediction",
            )
            .collect()["prediction"]
            .to_numpy()
        )
        doubled = (
            _run_score_pipeline(
                scoring_model,
                df.drop("claim_count").with_columns(pl.col("exposure") * 2).lazy(),
                task="regression",
                output_col="prediction",
            )
            .collect()["prediction"]
            .to_numpy()
        )
        np.testing.assert_allclose(doubled, base * 2.0, rtol=1e-8)

    @pytest.mark.parametrize("source", ["live", "batch"])
    def test_catboost_scoring_applies_offset(self, haute_scratch: Path, source: str) -> None:
        """Both the eager and the batched scorer must re-supply the CatBoost
        baseline recorded on the model."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model
        from haute._model_scorer import _run_score_pipeline

        df = _freq_frame()
        algo, model = _fit_catboost(df, offset="exposure")
        model_path = haute_scratch / "cb_offset.cbm"
        algo.save(model, model_path)
        scoring_model = load_local_model(str(model_path), "regression")

        scored = (
            _run_score_pipeline(
                scoring_model,
                df.drop("claim_count").lazy(),
                task="regression",
                output_col="prediction",
                source=source,
            )
            .collect()["prediction"]
            .to_numpy()
        )
        expected = algo.predict(model, df, ["age", "region"], offset="exposure")
        np.testing.assert_allclose(scored, expected, rtol=1e-6)

    def test_catboost_scoring_missing_offset_column_fails_loud(self, haute_scratch: Path) -> None:
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model
        from haute._model_scorer import _run_score_pipeline

        df = _freq_frame()
        algo, model = _fit_catboost(df, offset="exposure")
        model_path = haute_scratch / "cb_offset_missing.cbm"
        algo.save(model, model_path)
        scoring_model = load_local_model(str(model_path), "regression")

        with pytest.raises(FeatureMismatchError, match="exposure"):
            _run_score_pipeline(
                scoring_model,
                df.drop("exposure", "claim_count").lazy(),
                task="regression",
                output_col="prediction",
            ).collect()


class TestDeployScorerOffset:
    """Container path: bundled model + contract, scored via score_graph."""

    def _graph(self, contract_path: Path):
        from haute.graph_utils import PipelineGraph
        from tests.conftest import make_output_config

        return PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "api_in",
                        "data": {"label": "api_in", "nodeType": "apiInput", "config": {}},
                    },
                    {
                        "id": "ms",
                        "data": {
                            "label": "ms",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_offset",
                                "artifact_path": "glm_offset.rsglm",
                                "feature_contract_path": str(contract_path),
                                "output_column": "prediction",
                            },
                        },
                    },
                    {
                        "id": "output",
                        "data": {
                            "label": "output",
                            "nodeType": "output",
                            "config": make_output_config(["prediction"]),
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "api_in", "target": "ms"},
                    {"id": "e2", "source": "ms", "target": "output"},
                ],
            }
        )

    def _bundle(self, haute_scratch: Path):
        from haute.modelling._feature_contract import CONTRACT_FILENAME

        df = _freq_frame()
        algo, model = _fit_glm(df, offset="exposure")
        model_path = haute_scratch / "glm_offset.rsglm"
        algo.save(model, model_path)
        contract = build_contract(
            features=["age"],
            feature_types={"age": "Float64"},
            categorical_features=[],
            target_name="claim_count",
            target_type="Float64",
            task="regression",
            offset_column="exposure",
        )
        contract_path = haute_scratch / CONTRACT_FILENAME
        save_contract(contract, contract_path)
        artifact_paths = {
            "ms__glm_offset.rsglm": str(model_path),
            f"ms__{CONTRACT_FILENAME}": str(contract_path),
        }
        return df, algo, model, contract_path, artifact_paths

    def test_deploy_payload_missing_offset_fails_loud(self, haute_scratch: Path) -> None:
        """A payload without the offset column must be rejected loud.

        The column-contract planner (which reads the bundled feature
        contract's ``offset_column``) rejects the graph before scoring even
        starts; the runtime contract check and feature validation back it
        up at score time.  Any of those layers raising is a pass — what is
        forbidden is a silent offset-absent score.
        """
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute.deploy._scorer import score_graph
        from haute.errors import ContractMismatchError

        df, _algo, _model, contract_path, artifact_paths = self._bundle(haute_scratch)
        with pytest.raises((FeatureMismatchError, ContractMismatchError)):
            score_graph(
                graph=self._graph(contract_path),
                input_df=df.drop("exposure", "claim_count"),
                input_node_ids=["api_in"],
                output_node_id="output",
                artifact_paths=artifact_paths,
            )

    def test_deploy_served_score_includes_offset(self, haute_scratch: Path) -> None:
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute.deploy._scorer import score_graph

        df, algo, model, contract_path, artifact_paths = self._bundle(haute_scratch)
        served = score_graph(
            graph=self._graph(contract_path),
            input_df=df.drop("claim_count"),
            input_node_ids=["api_in"],
            output_node_id="output",
            artifact_paths=artifact_paths,
        )
        expected = algo.predict(model, df, ["age"], offset="exposure")
        np.testing.assert_allclose(
            served["prediction"].to_numpy(),
            expected,
            rtol=1e-8,
        )
