"""Adversarial reproduction for claim `pyfunc-loader-module-missing-symbol`.

Claim: For non-CatBoost models, `_log_model_with_signature` calls
`mlflow.pyfunc.log_model(loader_module="haute._mlflow_io", ...)`. MLflow's
loader-module contract requires the named module to expose a module-level
`_load_pyfunc(path)` (or `load_model`). `haute._mlflow_io` defines only
`_load_pyfunc_model(mlflow_module, run_id, artifact_path)` — a different name
AND signature. Therefore `mlflow.pyfunc.load_model("runs:/<run>/model")` on the
advertised flavor fails with a loader-contract error (AttributeError on
`_load_pyfunc`). The attached ModelSignature (the documented deploy drift
surface) is non-functional for standard MLflow tooling.

This script proves the bug by:
  (A) STATIC assertion: `haute._mlflow_io` exposes no module-level
      `_load_pyfunc` or `load_model`, but DOES expose `_load_pyfunc_model`.
  (B) DYNAMIC assertion: logging a fake `.rsglm` through the real
      `_log_model_with_signature` and then calling
      `mlflow.pyfunc.load_model(...)` raises AttributeError naming
      `_load_pyfunc` — i.e. fails BECAUSE of the predicted missing symbol,
      not an unrelated setup error.

ISOLATION: all disk I/O via tempfile; project root pinned to the tempdir via
haute._sandbox.set_project_root; no real project files are read or written.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


def main() -> None:
    # ----- isolation: pin project root + MLflow tracking to a tempdir -----
    tmp = Path(tempfile.mkdtemp(prefix="pyfunc_loader_repro_"))

    import haute._sandbox as sandbox

    sandbox.set_project_root(tmp)

    import mlflow

    tracking_dir = tmp / "mlruns"
    tracking_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_dir.resolve().as_uri())

    import haute._mlflow_io as mio
    from haute.modelling._mlflow_log import _log_model_with_signature

    # ----------------------------------------------------------------------
    # (A) STATIC: the loader module must (per MLflow contract) expose a
    #     module-level `_load_pyfunc` OR `load_model`. It does not.
    # ----------------------------------------------------------------------
    has_load_pyfunc = hasattr(mio, "_load_pyfunc")
    has_load_model = hasattr(mio, "load_model")
    has_internal = hasattr(mio, "_load_pyfunc_model")

    print(f"[static] haute._mlflow_io._load_pyfunc present : {has_load_pyfunc}")
    print(f"[static] haute._mlflow_io.load_model  present  : {has_load_model}")
    print(f"[static] haute._mlflow_io._load_pyfunc_model    : {has_internal}")

    assert has_internal, (
        "Sanity: expected the module to define _load_pyfunc_model "
        "(the real, differently-named/-signatured helper)."
    )
    assert not has_load_pyfunc and not has_load_model, (
        "REFUTED: the loader module DOES expose a contract entry point; "
        "the claimed missing-symbol bug does not hold."
    )
    print("[static] CONFIRMED: no _load_pyfunc / load_model entry point exists.\n")

    # ----------------------------------------------------------------------
    # (B) DYNAMIC: log a fake .rsglm through the real code path, then try to
    #     load the advertised pyfunc flavor the way external MLflow tooling
    #     would. We expect an AttributeError that names `_load_pyfunc`.
    # ----------------------------------------------------------------------
    fake_model = tmp / "model.rsglm"
    fake_model.write_bytes(b"not-a-real-rustystats-model")

    mlflow.set_experiment("repro-pyfunc-loader")
    with mlflow.start_run() as run:
        run_id = run.info.run_id
        _log_model_with_signature(
            mlflow,
            model_path=str(fake_model),
            algorithm="rustystats_glm",
            task="regression",
            features=["x0", "x1"],
            feature_types={"x0": "Float64", "x1": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
        )

    model_uri = f"runs:/{run_id}/model"

    # Confirm the flavor was actually advertised with our loader_module, so
    # the failure below is about loading the *advertised* flavor.
    info = mlflow.models.get_model_info(model_uri)
    pyfunc_flavor = info.flavors.get("python_function", {})
    advertised_loader = pyfunc_flavor.get("loader_module")
    print(f"[dynamic] advertised loader_module = {advertised_loader!r}")
    assert advertised_loader == "haute._mlflow_io", (
        "Setup mismatch: expected the pyfunc flavor to advertise "
        f"loader_module='haute._mlflow_io', got {advertised_loader!r}."
    )
    # The signature (documented deploy drift surface) IS attached...
    print(f"[dynamic] signature attached       = {info.signature is not None}")

    # ...but loading the advertised flavor fails on the missing symbol.
    raised: BaseException | None = None
    try:
        mlflow.pyfunc.load_model(model_uri)
    except BaseException as exc:  # noqa: BLE001 - we inspect the exception
        raised = exc

    assert raised is not None, (
        "REFUTED: mlflow.pyfunc.load_model SUCCEEDED on a loader_module that "
        "exposes no _load_pyfunc — the claimed loader-contract failure does "
        "not occur."
    )

    msg = f"{type(raised).__name__}: {raised}"
    full = "".join(traceback.format_exception(type(raised), raised, raised.__traceback__))
    print(f"[dynamic] load_model raised        = {msg}")

    # The failure must be BECAUSE the loader module lacks `_load_pyfunc`
    # (predicted bug), not an unrelated import/setup error.
    is_attribute_error = isinstance(raised, AttributeError)
    names_load_pyfunc = "_load_pyfunc" in full
    # Guard against false-positive: it must NOT be a "module not found" /
    # import error (which would mean haute itself isn't importable in the
    # loader env — an unrelated cause).
    looks_like_import_failure = (
        "ModuleNotFoundError" in full or "No module named 'haute'" in full
    )

    print(f"[dynamic] isinstance AttributeError : {is_attribute_error}")
    print(f"[dynamic] traceback names _load_pyfunc: {names_load_pyfunc}")
    print(f"[dynamic] (not) import failure       : {not looks_like_import_failure}")

    assert names_load_pyfunc and not looks_like_import_failure, (
        "Failure was NOT due to the missing _load_pyfunc symbol "
        "(unrelated error) — does not count as reproduced.\n"
        "--- full traceback ---\n" + full
    )
    # MLflow surfaces this as AttributeError: module 'haute._mlflow_io' has no
    # attribute '_load_pyfunc' (it calls `module._load_pyfunc(data_path)`).
    assert is_attribute_error, (
        "Expected AttributeError from the loader-module contract lookup; "
        f"got {type(raised).__name__}.\n--- full traceback ---\n" + full
    )

    print(
        "\nREPRODUCED: the advertised pyfunc flavor "
        f"({model_uri}) cannot be loaded by standard MLflow tooling because "
        "haute._mlflow_io exposes no module-level `_load_pyfunc`. The attached "
        "ModelSignature is therefore unreachable via mlflow.pyfunc.load_model."
    )


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("\n*** ASSERTION FAILED (claim NOT cleanly reproduced) ***")
        print(exc)
        sys.exit(1)
    print("\n[exit] repro completed: claim REAL.")
