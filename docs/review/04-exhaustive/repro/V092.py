"""Isolated reproduction for V092.

Claim: ``log_experiment`` registers the model with source URI
``runs:/<id>/{Path(model_path).name}`` (e.g. ``runs:/<id>/model.cbm``), but the
CatBoost branch of ``_log_model_with_signature`` logs the model via
``mlflow.catboost.log_model(artifact_path="model")`` -- which logs an MLflow
*model* at artifact path ``model`` and NEVER logs a top-level ``model.cbm``
artifact.  Therefore the registration source ``runs:/<id>/model.cbm`` does NOT
resolve and ``mlflow.register_model`` raises; the correct source is
``runs:/<id>/model``.

Environment: this is verified against the pinned ``mlflow==3.10.0`` /
``catboost==1.2.10``.  In MLflow 3.x logged models live in a "logged models"
store (``models:/m-...``) rather than the legacy run-artifact root, so
``client.list_artifacts(run_id)`` is empty -- but the run-relative URI
``runs:/<run_id>/<artifact_path>`` is exactly how ``register_model`` resolves a
model, so we assert on that resolution directly (and exercise ``register_model``
too).

Fully isolated: MLflow tracking + registry live in a tempdir (local file
store); a tiny real CatBoost model is trained in memory; no rating/, src/,
tests/, or real project files are read or written.

Demonstrates:
  (a) ``runs:/<id>/model``       resolves to the logged model dir (has MLmodel);
  (b) ``runs:/<id>/model.cbm``   FAILS to resolve (the production registration URI);
  (c) ``register_model(runs:/<id>/model.cbm)`` raises; ``register_model(runs:/<id>/model)`` succeeds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def _resolves(mlflow: object, uri: str, dst: Path) -> tuple[bool, str, bool]:
    """Return (ok, err, has_MLmodel) for downloading *uri* to *dst*."""
    try:
        local = mlflow.artifacts.download_artifacts(uri, dst_path=str(dst))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - asserted on by caller
        return False, f"{type(exc).__name__}: {exc}", False
    p = Path(local)
    has_mlmodel = p.is_dir() and any(p.rglob("MLmodel"))
    return True, "", has_mlmodel


def main() -> None:
    import mlflow
    from catboost import CatBoostRegressor

    tmp = Path(tempfile.mkdtemp(prefix="v092_"))
    tracking_uri = (tmp / "mlruns").as_uri()
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)  # local file store backs the registry too
    mlflow.set_experiment("v092-exp")

    # --- Train a tiny real CatBoost model (in memory) --------------------
    X = [[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]]
    y = [0.0, 1.0, 0.5, 0.3]
    model = CatBoostRegressor(iterations=2, depth=1, verbose=False)
    model.fit(X, y)

    # The production ``model_path`` basename -- this is what log_experiment uses
    # for the registration URI (src/haute/modelling/_mlflow_log.py:379).
    model_path = tmp / "model.cbm"
    model.save_model(str(model_path))
    registered_basename = Path(model_path).name  # "model.cbm" -> claimed-wrong source
    assert registered_basename == "model.cbm"

    # --- Log EXACTLY as _log_model_with_signature does for .cbm ----------
    # Mirror src/haute/modelling/_mlflow_log.py:457-462 (artifact_path="model";
    # no separate log_artifact of the raw .cbm in the CatBoost branch).
    with mlflow.start_run() as run:
        run_id = run.info.run_id
        mlflow.catboost.log_model(cb_model=model, artifact_path="model")

    wrong_uri = f"runs:/{run_id}/{registered_basename}"  # runs:/<id>/model.cbm  (production)
    correct_uri = f"runs:/{run_id}/model"                # runs:/<id>/model      (fix)
    print(f"run_id={run_id}")
    print(f"wrong_uri={wrong_uri}")
    print(f"correct_uri={correct_uri}")

    # (a) The CORRECT run-relative URI resolves to the logged model dir.
    ok_correct, err_correct, has_mlmodel = _resolves(mlflow, correct_uri, tmp / "dl_model")
    print(f"correct_uri_resolves={ok_correct} has_MLmodel={has_mlmodel} err={err_correct}")

    # (b) The WRONG run-relative URI (production basename) does NOT resolve.
    ok_wrong, err_wrong, _ = _resolves(mlflow, wrong_uri, tmp / "dl_model_cbm")
    print(f"wrong_uri_resolves={ok_wrong} err={err_wrong}")

    # (c) Exercise register_model itself end-to-end against the same store.
    wrong_reg_failed = False
    wrong_reg_err = ""
    try:
        mlflow.register_model(wrong_uri, "v092-model-wrong")
    except Exception as exc:  # noqa: BLE001 - asserted below
        wrong_reg_failed = True
        wrong_reg_err = f"{type(exc).__name__}: {exc}"
    print(f"register(wrong_uri)_failed={wrong_reg_failed} err={wrong_reg_err}")

    correct_reg_ok = False
    correct_reg_err = ""
    try:
        mlflow.register_model(correct_uri, "v092-model-correct")
        correct_reg_ok = True
    except Exception as exc:  # noqa: BLE001
        correct_reg_err = f"{type(exc).__name__}: {exc}"
    print(f"register(correct_uri)_ok={correct_reg_ok} err={correct_reg_err}")

    # --- Assertions on the SPECIFIC wrong VALUE/behaviour ----------------
    assert ok_correct and has_mlmodel, (
        "expected runs:/<id>/model to resolve to a logged-model dir (MLmodel present); "
        f"resolves={ok_correct} has_MLmodel={has_mlmodel} err={err_correct}"
    )
    assert not ok_wrong, (
        "expected the production registration source runs:/<id>/model.cbm to FAIL to "
        "resolve (no such artifact path), but it resolved."
    )
    assert wrong_reg_failed, (
        "expected mlflow.register_model(runs:/<id>/model.cbm) to RAISE, but it succeeded."
    )
    assert correct_reg_ok, (
        "expected mlflow.register_model(runs:/<id>/model) to SUCCEED, but it failed: "
        f"{correct_reg_err}"
    )

    print(
        "REPRODUCED: log_experiment passes runs:/<id>/model.cbm to register_model, which "
        "FAILS to resolve (CatBoost model is logged at artifact_path 'model'); the correct "
        "source is runs:/<id>/model."
    )


if __name__ == "__main__":
    main()
