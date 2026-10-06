"""XGBoost GPU training (MOD-F06): device checks, config, setup command, real CUDA fits.

The real-device tests run only where the full ``xgboost`` build and a CUDA GPU
are present (``haute gpu-setup``); everything else runs on the default
``xgboost-cpu`` install.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from click.testing import CliRunner

from haute._mlflow_io import load_local_model
from haute.errors import HauteValidationError
from haute.modelling import _gpu
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 3,
    "validation": {"method": "single", "size": 0.2},
}
GPU = _gpu.xgboost_gpu_status()
# The real-device tests need the full xgboost build and an NVIDIA GPU
# (`haute gpu-setup`); Haute's default xgboost-cpu install has no CUDA.
needs_gpu = pytest.mark.skipif(
    not GPU.available, reason="needs XGBoost's CUDA build and an NVIDIA GPU (haute gpu-setup)"
)


def frame(n: int = 4000, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice(["east", "north", "south"], n)
    age = rng.uniform(18, 80, n)
    exposure = rng.uniform(0.2, 1.0, n)
    rate = np.exp(-2 + 0.01 * (age - 40) + np.where(region == "north", 0.4, 0.0))
    return pl.DataFrame(
        {
            "region": region.tolist(),
            "age": age,
            "exposure": exposure,
            "claims": rng.poisson(rate * exposure).astype(float),
            "severity": rng.gamma(2.0, 500 * np.exp(0.005 * age)),
            "flag": np.where(rng.uniform(size=n) < 1 / (1 + np.exp(-(age - 50) / 8)), "yes", "no"),
        }
    )


def config(**extra: object) -> dict:
    return {
        "target": "claims",
        "algorithm": "xgboost",
        "loss_function": "Poisson",
        "offset": "exposure",
        "params": {"num_boost_round": 60, "eta": 0.2, "max_depth": 4},
        "feature_columns": ["region", "age"],
        "evaluation": EVALUATION,
        **extra,
    }


def train(tmp_path: Path, device: str) -> object:
    return TrainingJob(
        **build_training_job_kwargs(
            {**config(device=device), "output_dir": str(tmp_path)}, data=frame()
        )
    ).run()


# ---------------------------------------------------------------------------
# Configuration and device checks (any install)
# ---------------------------------------------------------------------------


def test_only_the_xgboost_family_takes_the_gpu_device() -> None:
    assert build_training_job_kwargs(config(device="gpu"), data="d.parquet")["device"] == "gpu"
    assert build_training_job_kwargs(config(), data="d.parquet")["device"] == "cpu"
    with pytest.raises(TrainingConfigError, match='device must be "cpu" or "gpu"'):
        build_training_job_kwargs(config(device="cuda"), data="d.parquet")
    lightgbm = {
        **config(device="gpu"),
        "algorithm": "lightgbm",
        "params": {"num_iterations": 10},
    }
    with pytest.raises(TrainingConfigError, match="LightGBM trains on CPU only"):
        build_training_job_kwargs(lightgbm, data="d.parquet")
    with pytest.raises(HauteValidationError, match="EBM trains on CPU only"):
        TrainingJob(
            name="m",
            data=frame(),
            target="claims",
            algorithm="ebm",
            device="gpu",
            params={"max_rounds": 5},
        )


def test_xgboost_refuses_monotone_constraints_under_mae() -> None:
    # reg:absoluteerror refits leaves after each tree and breaks the
    # constraint on CPU and GPU alike, so Haute refuses the combination.
    with pytest.raises(TrainingConfigError, match="cannot apply monotonicity constraints"):
        build_training_job_kwargs(
            {**config(), "loss_function": "MAE", "monotone_constraints": {"age": 1}},
            data="d.parquet",
        )


def test_the_glm_refuses_a_gpu_or_unknown_device() -> None:
    glm = {
        "target": "claims",
        "algorithm": "glm",
        "family": "poisson",
        "terms": {"age": {"type": "linear"}},
        "evaluation": EVALUATION,
    }
    assert build_training_job_kwargs(glm, data="d.parquet")["device"] == "cpu"
    with pytest.raises(TrainingConfigError, match="GLM trains on CPU only"):
        build_training_job_kwargs({**glm, "device": "gpu"}, data="d.parquet")
    with pytest.raises(TrainingConfigError, match='device must be "cpu" or "gpu"'):
        build_training_job_kwargs({**glm, "device": "tpu"}, data="d.parquet")


def test_the_device_is_part_of_the_training_identity() -> None:
    cpu = TrainingJob(**build_training_job_kwargs(config(), data=frame()))
    gpu = TrainingJob(**build_training_job_kwargs(config(device="gpu"), data=frame()))
    assert cpu.training_identity_sha256 != gpu.training_identity_sha256


def test_the_exported_script_keeps_the_gpu_device() -> None:
    from haute.modelling._export import generate_training_script

    assert "device='gpu'" in generate_training_script(config(device="gpu"), "d.parquet")
    assert "device=" not in generate_training_script(config(), "d.parquet")


def test_a_cpu_only_build_reports_why_gpu_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gpu, "xgboost_cuda_build", lambda: False)
    _gpu.xgboost_gpu_status.cache_clear()
    try:
        status = _gpu.xgboost_gpu_status()
    finally:
        _gpu.xgboost_gpu_status.cache_clear()
    assert not status.available
    assert "haute gpu-setup" in status.detail


def test_a_gpu_request_without_a_usable_gpu_fails_before_fitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _gpu, "xgboost_gpu_status", lambda: _gpu.GpuStatus(False, "No CUDA GPU is visible.")
    )
    with pytest.raises(HauteValidationError, match="GPU training is unavailable: No CUDA GPU"):
        train(tmp_path, "gpu")
    assert not list(tmp_path.glob("*.ubj"))


def test_a_booster_that_fell_back_to_the_cpu_is_refused() -> None:
    import xgboost as xgb

    data = np.arange(40.0).reshape(20, 2)
    booster = xgb.train({"device": "cpu"}, xgb.DMatrix(data, label=data[:, 0]), 1)
    with pytest.raises(HauteValidationError, match="trained on cpu, not the requested GPU"):
        _gpu.verify_trained_on_gpu(booster)


def test_the_vram_estimate_covers_the_measured_peak() -> None:
    from haute._ram_estimate import estimate_xgboost_gpu_vram_bytes

    # MOD-F06 probes measured 88-132 MiB on the device for 250,000 x 21.
    assert estimate_xgboost_gpu_vram_bytes(250_000, 21) > 132 * 1024**2
    assert estimate_xgboost_gpu_vram_bytes(10_000_000, 100) > estimate_xgboost_gpu_vram_bytes(
        1_000_000, 100
    )


def test_an_xgboost_gpu_job_is_refused_when_it_cannot_fit_in_vram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import _training_preparation

    monkeypatch.setattr("haute._host_memory.available_vram_bytes", lambda: 64 * 1024**2)
    check = _training_preparation._check_gpu_vram(1_000_000, 50, {}, algorithm="xgboost")
    assert check.insufficient
    assert "GPU training needs" in (check.warning or "")


def test_the_gpu_status_route_reports_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from haute.server import app

    monkeypatch.setattr(
        _gpu,
        "xgboost_gpu_status",
        lambda: _gpu.GpuStatus(True, "XGBoost trains on cuda:0.", "cuda:0"),
    )
    response = TestClient(app).get("/api/modelling/gpu")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "xgboost": {"available": True, "detail": "XGBoost trains on cuda:0.", "device": "cuda:0"}
    }


# ---------------------------------------------------------------------------
# haute gpu-setup
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_env(monkeypatch: pytest.MonkeyPatch):
    from haute.cli import _gpu_setup

    state = {"installed": ("xgboost-cpu", "3.2.0"), "gpu": "RTX Test", "cuda": True, "ran": []}
    monkeypatch.setattr(_gpu_setup, "installed_xgboost", lambda: state["installed"])
    monkeypatch.setattr(_gpu_setup, "nvidia_gpu_name", lambda: state["gpu"])
    monkeypatch.setattr(_gpu_setup, "fresh_cuda_build", lambda: state["cuda"])
    monkeypatch.setattr(_gpu_setup.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(_gpu_setup.sys, "platform", "win32")

    def runner(command):
        state["ran"].append(list(command))
        return state.get("exit", 0)

    state["runner"] = runner
    return state


def run_setup(state: dict, *flags: str):
    from haute.cli._gpu_setup import GpuSetupConfig, handle_gpu_setup

    runner = CliRunner()

    @__import__("click").command()
    def command() -> None:
        handle_gpu_setup(
            GpuSetupConfig(check_only="--check" in flags, to_cpu="--cpu" in flags, assume_yes=True),
            runner=state["runner"],
        )

    return runner.invoke(command, [])


def test_setup_swaps_the_cpu_build_for_the_cuda_build_at_the_same_version(setup_env) -> None:
    result = run_setup(setup_env)
    assert result.exit_code == 0, result.output
    assert [command[1:3] + command[-1:] for command in setup_env["ran"]] == [
        ["pip", "uninstall", "xgboost-cpu"],
        ["pip", "install", "xgboost==3.2.0"],
    ]
    assert sys.executable in setup_env["ran"][0]
    assert "Restart `haute serve`" in result.output
    assert "`uv sync`) restores xgboost-cpu" in result.output


def test_setup_reverts_to_the_cpu_build(setup_env) -> None:
    setup_env["installed"] = ("xgboost", "3.2.0")
    setup_env["cuda"] = False
    result = run_setup(setup_env, "--cpu")
    assert result.exit_code == 0, result.output
    assert [command[-1] for command in setup_env["ran"]] == ["xgboost", "xgboost-cpu==3.2.0"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"gpu": None}, "no NVIDIA GPU was found"),
        ({"installed": None}, "expected exactly one of xgboost-cpu or xgboost"),
        ({"cuda": False}, "lacks CUDA support"),
        ({"exit": 1}, "Restore the previous build with"),
    ],
)
def test_setup_refuses_or_reports_what_went_wrong(setup_env, change: dict, message: str) -> None:
    setup_env.update(change)
    result = run_setup(setup_env)
    assert result.exit_code == 1
    assert message in result.output


@pytest.mark.parametrize("flags", [(), ("--cpu",)])
def test_setup_refuses_macos(setup_env, monkeypatch: pytest.MonkeyPatch, flags) -> None:
    from haute.cli import _gpu_setup

    # macOS installs the standard xgboost package (no CPU-only wheel exists).
    setup_env["installed"] = ("xgboost", "3.2.0")
    monkeypatch.setattr(_gpu_setup.sys, "platform", "darwin")
    result = run_setup(setup_env, *flags)
    assert result.exit_code == 1
    assert "no CUDA build for macOS" in result.output
    assert setup_env["ran"] == []


def test_setup_is_a_no_op_when_the_build_is_already_installed(setup_env) -> None:
    setup_env["installed"] = ("xgboost", "3.2.0")
    result = run_setup(setup_env)
    assert result.exit_code == 0
    assert "already installed" in result.output
    assert setup_env["ran"] == []


def test_the_cli_registers_gpu_setup() -> None:
    from haute.cli import cli

    result = CliRunner().invoke(cli, ["gpu-setup", "--help"])
    assert result.exit_code == 0
    assert "--check" in result.output and "--cpu" in result.output


# ---------------------------------------------------------------------------
# Real CUDA fits (full xgboost build and an NVIDIA GPU)
# ---------------------------------------------------------------------------


@needs_gpu
def test_a_gpu_fit_trains_on_the_device_and_scores_identically_on_the_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.modelling._xgboost import XGBoostModel

    data = frame(seed=5)
    captured: dict[str, np.ndarray] = {}
    original_save = XGBoostModel.save

    def save_and_capture(self: XGBoostModel, path: Path) -> None:
        # References taken from the fitted in-memory model before serialization:
        # its own prediction, and the native booster predicting on the GPU.
        captured["before_save"] = self.predict_response(data)
        on_device = self.booster.copy()
        on_device.set_param({"device": "cuda"})
        captured["on_gpu"] = np.asarray(on_device.predict(self.matrix(data)), dtype=np.float64)
        original_save(self, path)

    monkeypatch.setattr(XGBoostModel, "save", save_and_capture)
    result = train(tmp_path, "gpu")
    assert result.fit_evidence["device"].startswith("cuda")

    model = load_local_model(result.model_path).raw_model
    config_device = json.loads(model.booster.save_config())["learner"]["generic_param"]["device"]
    assert config_device == "cpu"
    served = model.predict_response(data)
    assert np.isfinite(served).all()
    np.testing.assert_array_equal(served, captured["before_save"])
    # Device and host inference sum float32 leaf values in different orders.
    np.testing.assert_allclose(served, captured["on_gpu"], rtol=1e-5)

    # A CPU-only deployment: score the saved artifact through Haute in an
    # interpreter that has only xgboost-cpu (MOD-F06 probe evidence records a run).
    cpu_python = os.environ.get("HAUTE_XGBOOST_CPU_PYTHON")
    if cpu_python:
        data.write_parquet(tmp_path / "score.parquet")
        script = (
            "import sys, numpy, polars, xgboost;"
            "assert not xgboost.build_info().get('USE_CUDA');"
            "from haute._mlflow_io import load_local_model;"
            "m = load_local_model(sys.argv[1]).raw_model;"
            "numpy.save(sys.argv[3], m.predict_response(polars.read_parquet(sys.argv[2])))"
        )
        subprocess.run(
            [
                cpu_python,
                "-c",
                script,
                result.model_path,
                str(tmp_path / "score.parquet"),
                str(tmp_path / "cpu.npy"),
            ],
            check=True,
        )
        np.testing.assert_array_equal(np.load(tmp_path / "cpu.npy"), captured["before_save"])


LOSSES = {
    "RMSE": {"target": "severity"},
    "Poisson": {"target": "claims", "offset": "exposure"},
    "Gamma": {"target": "severity"},
    "Tweedie": {"target": "claims", "variance_power": 1.5},
    "Logloss": {
        "target": "flag",
        "task": "classification",
        "positive_class": "yes",
        "metrics": ["auc"],
    },
}


@needs_gpu
@pytest.mark.parametrize("loss", sorted(LOSSES))
def test_every_xgboost_loss_trains_on_the_gpu_under_a_monotone_constraint(
    tmp_path: Path, loss: str
) -> None:
    extra = dict(LOSSES[loss])
    base = {key: value for key, value in config().items() if key != "offset"}
    job = TrainingJob(
        **build_training_job_kwargs(
            {
                **base,
                **extra,
                "loss_function": loss,
                "device": "gpu",
                "monotone_constraints": {"age": 1},
                "output_dir": str(tmp_path),
            },
            data=frame(),
        )
    )
    result = job.run()
    assert result.fit_evidence["device"].startswith("cuda")
    task = extra.get("task", "regression")
    model = load_local_model(result.model_path, task=task).raw_model
    grid = pl.DataFrame(
        {"region": ["north"] * 60, "age": np.linspace(18, 80, 60), "exposure": [1.0] * 60}
    )
    scored = model.predict_response(grid)
    assert np.isfinite(scored).all()
    assert (np.diff(scored) >= -1e-9).all(), f"{loss} broke the monotone constraint on the GPU"


@needs_gpu
def test_a_hidden_gpu_fails_the_fit_instead_of_training_on_the_cpu(tmp_path: Path) -> None:
    script = (
        "import sys; sys.path.insert(0, 'tests');"
        "from pathlib import Path;"
        "import test_xgboost_gpu as t;"
        f"t.train(Path({str(tmp_path)!r}), 'gpu')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "-1"},
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    assert completed.returncode != 0
    assert "No CUDA GPU is visible to XGBoost" in completed.stderr
    assert not list(tmp_path.glob("*.ubj"))
