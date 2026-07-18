"""Adversarial repro: validate-time `score_test_quotes` / `score_graph`
(no artifact_paths) loads a DIFFERENT model than the deployed container
serves AND skips the bundled feature-contract check.

Claim under test (deploy-validate-vs-serve-model-artifact-divergence):
  * Test-before-live gate (`validate_deploy` -> `score_test_quotes`,
    and `resolve_config` -> `infer_output_schema`) calls
    `score_graph(... )` with NO `artifact_paths`.
  * With `artifact_paths=None`, `score_graph_lazy` sets `remap = {}`
    (src/haute/deploy/_scorer.py:457).  The modelScore intercept
    (_scorer.py:605) computes `bundled_contract_path = None` and
    `remapped_path = None`, so BOTH intercept branches are skipped and
    the node falls through to the base builder `_build_model_score`
    (src/haute/_builders.py:1152), which loads the model LIVE from MLflow
    and (because `feature_contract_path` was never attached) runs NO
    `_assert_runtime_contract_matches`.
  * The deployed container / pyfunc pass `artifact_paths` (the PINNED,
    bundled artifacts incl. the feature contract); the intercept fires
    and `_assert_runtime_contract_matches` enforces train-vs-score drift.

We prove the divergence by scoring the SAME modelScore graph two ways on
a contract-VIOLATING input:

  A. validate path  -> score_graph(graph, df)                    [no artifact_paths]
  B. serve   path   -> score_graph(graph, df, artifact_paths={...bundled...})

Expected (if the claim holds):
  * A succeeds and returns a numeric prediction  (contract check skipped)
  * B raises FeatureMismatchError                (contract check enforced)

If the gate were sound, A would ALSO raise — i.e. the test-before-live
validation would catch the same drift the container would block.

Isolation: real CatBoost model trained in-memory; MLflow file-store under
a tempdir; cwd switched to the tempdir so the `.cache/models/` download
cache stays inside it.  No real project / src / tests files are touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    import catboost
    import mlflow
    import polars as pl

    from haute._execution_admission import _clear_in_flight_reservations_for_tests
    from haute._mlflow_io import _model_cache
    from haute.deploy._scorer import _clear_deploy_artifact_caches, score_graph
    from haute.errors import FeatureMismatchError
    from haute.modelling._feature_contract import (
        CONTRACT_FILENAME,
        build_contract,
        save_contract,
    )
    from haute.modelling._mlflow_log import configure_mlflow_tracking

    tmp = Path(tempfile.mkdtemp(prefix="deploy_divergence_"))
    # Keep the MLflow download cache (.cache/models) inside the tempdir.
    prev_cwd = Path.cwd()
    # chdir BEFORE configuring tracking so haute's resolve_tracking_backend()
    # (which uses Path.cwd()/mlruns) and our logging share the SAME file
    # store URI — otherwise the validate path resolves a different store and
    # cannot find the run.  Also neutralise any ambient Databricks creds.
    os.environ.pop("DATABRICKS_HOST", None)
    os.environ.pop("DATABRICKS_TOKEN", None)
    os.chdir(tmp)

    def _reset_runtime_state() -> None:
        # Drop deploy artifact caches, model cache, and any leaked in-flight
        # admission reservations so each scoring path starts clean.
        _clear_deploy_artifact_caches()
        _model_cache.clear()
        _clear_in_flight_reservations_for_tests()

    try:
        # Use the exact tracking URI haute will resolve at score time.
        configure_mlflow_tracking()
        # ── Train a tiny real CatBoost regressor on 2 features ──────────
        # Features: age (Int64), weight (Float64).  No categoricals — the
        # raw model will happily score the live frame; only the *contract*
        # disagrees, so the divergence is about the CHECK, not scoring.
        train = pl.DataFrame(
            {
                "age": [20, 30, 40, 50, 60, 25, 35, 45],
                "weight": [60.0, 70.0, 80.0, 90.0, 100.0, 65.0, 75.0, 85.0],
                "y": [1.0, 2.0, 3.0, 4.0, 5.0, 1.5, 2.5, 3.5],
            }
        )
        model = catboost.CatBoostRegressor(
            iterations=5, depth=2, verbose=False, allow_writing_files=False
        )
        model.fit(train.select(["age", "weight"]).to_pandas(), train["y"].to_pandas())

        cbm_path = tmp / "model.cbm"
        model.save_model(str(cbm_path))

        # ── Log the SAME .cbm to a local MLflow run so the validate path
        #    (sourceType='run') can resolve `runs:/<run_id>/model.cbm`.
        #    Tracking URI was set by configure_mlflow_tracking() above so
        #    logging and resolution share one store. ─────────────────────
        mlflow.set_experiment("divergence")
        with mlflow.start_run() as run:
            mlflow.log_artifact(str(cbm_path))  # logged as artifact "model.cbm"
            run_id = run.info.run_id

        # ── Build the bundled feature contract the container would ship.
        #    DELIBERATE drift vs the live frame: the contract declares
        #    `weight` as Int64, but the live frame supplies Float64.
        #    `_canonical_dtype(Float64) == "Float64" != "Int64"` →
        #    assert_contracts_match raises FeatureMismatchError on the
        #    serve path.  The raw CatBoost model still scores Float fine. ─
        contract = build_contract(
            features=["age", "weight"],
            feature_types={"age": "Int64", "weight": "Int64"},  # weight: WRONG on purpose
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        contract_path = tmp / CONTRACT_FILENAME
        save_contract(contract, contract_path)

        # ── The deployed graph: apiInput -> modelScore(run) -> output.
        #    NOTE: config has NO `feature_contract_path` — exactly as a
        #    user's pruned pipeline graph looks.  The contract exists only
        #    as a bundled artifact (discovered by collect_artifacts), which
        #    the validate path never receives. ─────────────────────────
        graph = pl_graph(run_id)

        # Live, contract-VIOLATING input (weight is Float64 not Int64).
        # 1 row → DEPLOY_LIVE profile (keeps admission trivial).
        live_df = pl.DataFrame({"age": [33], "weight": [72.5]})

        # ── A. VALIDATE path — mirrors score_test_quotes / infer_output_schema:
        #       score_graph WITHOUT artifact_paths. ──────────────────────
        _reset_runtime_state()
        validate_error: Exception | None = None
        validate_result = None
        try:
            validate_result = score_graph(
                graph=graph,
                input_df=live_df,
                input_node_ids=["api_in"],
                output_node_id="output",
                # artifact_paths intentionally omitted (== None) — this is
                # the EXACT call shape at _validators.py:375-380 and
                # _schema.py:134-139.
            )
        except Exception as exc:  # noqa: BLE001 — we want to observe whether it raises
            validate_error = exc

        # ── B. SERVE path — mirrors HauteModel.predict / container:
        #       score_graph WITH the pinned bundled artifacts (model + contract).
        _reset_runtime_state()
        serve_error: Exception | None = None
        serve_result = None
        try:
            serve_result = score_graph(
                graph=graph,
                input_df=live_df,
                input_node_ids=["api_in"],
                output_node_id="output",
                artifact_paths={
                    f"ms__{Path('model.cbm').name}": str(cbm_path),
                    f"ms__{CONTRACT_FILENAME}": str(contract_path),
                },
            )
        except Exception as exc:  # noqa: BLE001
            serve_error = exc

        # ── Report ──────────────────────────────────────────────────────
        print("=== VALIDATE path (no artifact_paths — the test-before-live gate) ===")
        if validate_error is not None:
            print(f"  raised: {type(validate_error).__name__}: {validate_error}")
        else:
            assert validate_result is not None
            print(f"  PASSED — returned {validate_result.shape} rows, columns={validate_result.columns}")
            # Show it produced an actual prediction value.
            pred_cols = [c for c in validate_result.columns if c not in ("age", "weight")]
            print(f"  prediction columns: {pred_cols}; first row: {validate_result.head(1).to_dicts()}")

        print("=== SERVE path (artifact_paths = pinned bundle incl. contract) ===")
        if serve_error is not None:
            print(f"  raised: {type(serve_error).__name__}: {serve_error}")
        else:
            assert serve_result is not None
            print(f"  PASSED — returned {serve_result.shape} rows")

        # ── Assertions: the divergence must hold ────────────────────────
        # 1. Serve path MUST block the drift.
        assert isinstance(serve_error, FeatureMismatchError), (
            "EXPECTED the serve path to raise FeatureMismatchError on contract "
            f"drift, but got: {serve_error!r} (result={serve_result!r}). If this "
            "fails the contract-check premise is wrong."
        )
        # 2. Validate path MUST NOT block it — proving the gate skips the
        #    contract check the container enforces.
        if isinstance(validate_error, FeatureMismatchError):
            print(
                "\nNOT REPRODUCED: validate path ALSO raised FeatureMismatchError "
                "— the gate is NOT divergent (claim refuted)."
            )
            return 1
        assert validate_error is None, (
            "Validate path raised an unrelated error (not a clean pass): "
            f"{validate_error!r}. Cannot cleanly attribute to the divergence."
        )
        assert validate_result is not None and validate_result.height == 1

        print(
            "\nREPRODUCED: the test-before-live gate (score_graph WITHOUT "
            "artifact_paths) accepted a quote whose schema DRIFTS from the "
            "bundled feature contract and produced a prediction, while the "
            "deployed serve path (score_graph WITH the pinned bundle) raised "
            "FeatureMismatchError on the very same input.  Validation never "
            "exercises the pinned artifact + contract path that ships."
        )
        return 0
    finally:
        os.chdir(prev_cwd)


def pl_graph(run_id: str):
    from haute._types import PipelineGraph

    return PipelineGraph.model_validate(
        {
            "nodes": [
                {"id": "api_in", "data": {"label": "api_in", "nodeType": "apiInput", "config": {}}},
                {
                    "id": "ms",
                    "data": {
                        "label": "ms",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "run",
                            "run_id": run_id,
                            "artifact_path": "model.cbm",
                            "task": "regression",
                            "output_column": "prediction",
                            # NO feature_contract_path — as in a user's graph.
                        },
                    },
                },
                {"id": "output", "data": {"label": "output", "nodeType": "output", "config": {}}},
            ],
            "edges": [
                {"id": "e1", "source": "api_in", "target": "ms"},
                {"id": "e2", "source": "ms", "target": "output"},
            ],
        }
    )


if __name__ == "__main__":
    sys.exit(main())
