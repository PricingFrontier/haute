"""GPU capability for the XGBoost family: build detection and device checks.

Haute installs ``xgboost-cpu`` by default; GPU training needs the full
``xgboost`` build (``haute gpu-setup`` swaps it in). XGBoost itself never
refuses a CUDA request: with no visible GPU it trains on the CPU and only logs
a warning (MOD-F06 probes). Haute therefore checks the device before a fit and
verifies afterwards that the booster really trained on it, failing loudly
instead of reporting CPU work as GPU work.
"""

from __future__ import annotations

import functools
import json
import warnings
from dataclasses import dataclass
from typing import Any

from haute.errors import HauteValidationError

GPU_SETUP_HINT = (
    "Run `haute gpu-setup` to install XGBoost's CUDA build, then restart `haute serve`."
)


@dataclass(frozen=True)
class GpuStatus:
    """Whether XGBoost can train on a GPU in this process, and why not if not."""

    available: bool
    detail: str
    device: str | None = None

    def to_plain_data(self) -> dict[str, Any]:
        return {"available": self.available, "detail": self.detail, "device": self.device}


def xgboost_cuda_build() -> bool:
    """True when the installed XGBoost was built with CUDA (the full wheel)."""
    import xgboost

    return bool(xgboost.build_info().get("USE_CUDA"))


def booster_device(booster: Any) -> str:
    """The device a trained booster actually used (``cpu`` or ``cuda:N``)."""
    config = json.loads(booster.save_config())
    return str(config["learner"]["generic_param"].get("device", "cpu"))


@functools.lru_cache(maxsize=1)
def xgboost_gpu_status() -> GpuStatus:
    """Probe once per process: the build, then a tiny fit on the device.

    A fit is the only reliable check: a CUDA build on a machine without a
    visible GPU silently trains on the CPU.
    """
    import numpy as np
    import xgboost as xgb

    if not xgboost_cuda_build():
        return GpuStatus(
            False,
            "The installed XGBoost (xgboost-cpu) has no CUDA support. " + GPU_SETUP_HINT,
        )
    data = np.arange(64, dtype=np.float64).reshape(32, 2)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            booster = xgb.train(
                {"device": "cuda", "tree_method": "hist", "verbosity": 0},
                xgb.DMatrix(data, label=data[:, 0]),
                num_boost_round=1,
            )
    except xgb.core.XGBoostError as exc:
        return GpuStatus(False, f"XGBoost could not use a CUDA GPU: {exc}".splitlines()[0])
    device = booster_device(booster)
    if not device.startswith("cuda"):
        return GpuStatus(
            False,
            "No CUDA GPU is visible to XGBoost (check the NVIDIA driver and CUDA_VISIBLE_DEVICES).",
        )
    return GpuStatus(True, f"XGBoost trains on {device}.", device)


def require_xgboost_gpu() -> GpuStatus:
    """The GPU status, or an actionable error when GPU training cannot run."""
    status = xgboost_gpu_status()
    if not status.available:
        raise HauteValidationError(f"XGBoost GPU training is unavailable: {status.detail}")
    return status


def verify_trained_on_gpu(booster: Any) -> str:
    """Refuse a booster that did not train on the GPU it was asked for."""
    device = booster_device(booster)
    if not device.startswith("cuda"):
        raise HauteValidationError(
            f"XGBoost trained on {device}, not the requested GPU; nothing was saved. "
            "Train on CPU, or fix GPU access and retry."
        )
    return device
