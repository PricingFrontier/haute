"""Per-model feature-contract artifact tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from haute.modelling._feature_contract import load_contract
from haute.modelling._training_job import TrainingJob, _TrainModelResult

_FAST_PARAMS = {"iterations": 2, "depth": 1, "verbose": 0}


def _stub_train_result() -> _TrainModelResult:
    return _TrainModelResult(
        model=MagicMock(),
        algo=MagicMock(),
        fit_result=MagicMock(),
        fit_params={},
    )


def _save_job(name: str, feature: str, output_dir: Path) -> TrainingJob:
    job = TrainingJob(
        name=name,
        data=pl.DataFrame({feature: [1.0], "y": [1.0]}),
        target="y",
        algorithm="catboost",
        output_dir=str(output_dir),
    )
    job._contract_feature_dtypes = {feature: "Float64"}
    job._contract_target_dtype = "Float64"
    job._save_artifacts(_stub_train_result(), features=[feature], cat_features=[])
    return job


class TestPerModelContractFiles:
    def test_two_models_in_one_output_dir_keep_distinct_contracts(self, tmp_path: Path) -> None:
        """The overwrite bug, scheme-agnostic: after two models save into
        one dir, a contract describing EACH model must still exist."""
        out = tmp_path / "outputs"
        _save_job("model_a", "a1", out)
        _save_job("model_b", "b1", out)

        feature_sets = {
            tuple(load_contract(p).features)
            for p in out.iterdir()
            if p.name.endswith("feature_contract.json")
        }
        assert ("a1",) in feature_sets, (
            "model_a's contract was overwritten by model_b — a scorer pointed at "
            f"it would validate against the wrong schema; surviving: {feature_sets}"
        )
        assert ("b1",) in feature_sets

    def test_contract_path_derives_from_model_name(self, tmp_path: Path) -> None:
        """The naming scheme is public API for everything that wires
        ``feature_contract_path`` configs: ``{name}.feature_contract.json``
        sitting next to the model file."""
        from haute.modelling._training_job import model_contract_filename

        assert model_contract_filename("claims_freq") == "claims_freq.feature_contract.json"

        out = tmp_path / "outputs"
        _save_job("claims_freq", "a1", out)

        contract_path = out / model_contract_filename("claims_freq")
        assert contract_path.is_file()
        assert load_contract(contract_path).features == ["a1"]

    def test_end_to_end_two_runs_one_dir_serve_correct_contracts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full ``run()`` twice into one ``output_dir`` (the UI default
        layout): each model's contract describes that model's features."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute.modelling._training_job import model_contract_filename

        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])

        rng = np.random.RandomState(42)
        n = 80
        out = tmp_path / "outputs"

        def _run(name: str, feature: str) -> list[str]:
            df = pl.DataFrame(
                {
                    feature: rng.randn(n),
                    "region": rng.choice(["north", "south"], n),
                    "y": rng.poisson(0.2, n).astype(np.float64),
                }
            )
            job = TrainingJob(
                name=name,
                data=df,
                target="y",
                params=dict(_FAST_PARAMS),
                metrics=["rmse"],
                output_dir=str(out),
            )
            return job.run().features

        features_a = _run("model_a", "alpha")
        features_b = _run("model_b", "beta")

        contract_a = load_contract(out / model_contract_filename("model_a"))
        contract_b = load_contract(out / model_contract_filename("model_b"))
        assert set(contract_a.features) == set(features_a)
        assert set(contract_b.features) == set(features_b)
        assert "alpha" in contract_a.features and "alpha" not in contract_b.features
        assert "beta" in contract_b.features and "beta" not in contract_a.features
        # Both model files coexist with their contracts; no shared file.
        assert (out / "model_a.cbm").is_file()
        assert (out / "model_b.cbm").is_file()
