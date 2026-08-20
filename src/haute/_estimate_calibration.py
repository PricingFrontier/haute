"""Process-local, upward-only calibration for materialisation estimates."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from haute._execution_context import ExecutionProfile

CALIBRATION_BASE_BASIS_POINTS: Final = 10_000
CALIBRATION_SAFETY_MARGIN_BASIS_POINTS: Final = 12_500
CALIBRATION_MAX_BASIS_POINTS: Final = 80_000

_calibration_lock = threading.RLock()
_calibration_by_profile: dict[ExecutionProfile, int] = {}


@dataclass(frozen=True, slots=True)
class CalibratedMaterialisationBytes:
    """One deterministic application of the current profile calibration."""

    raw_bytes: int
    calibrated_bytes: int
    factor_basis_points: int


def _checked_profile(profile: ExecutionProfile) -> ExecutionProfile:
    if not isinstance(profile, ExecutionProfile):
        raise TypeError("materialisation calibration requires an ExecutionProfile")
    return profile


def _checked_bytes(value: int, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def materialisation_calibration_factor(profile: ExecutionProfile) -> int:
    """Return the current basis-point multiplier without mutating the registry."""

    checked_profile = _checked_profile(profile)
    with _calibration_lock:
        return _calibration_by_profile.get(
            checked_profile,
            CALIBRATION_BASE_BASIS_POINTS,
        )


def calibrate_materialisation_bytes(
    profile: ExecutionProfile,
    raw_bytes: int,
) -> CalibratedMaterialisationBytes:
    """Apply the current upward-only factor, rounding conservatively upward."""

    checked_raw = _checked_bytes(raw_bytes, name="raw_bytes")
    factor = materialisation_calibration_factor(profile)
    calibrated = (
        checked_raw * factor + CALIBRATION_BASE_BASIS_POINTS - 1
    ) // CALIBRATION_BASE_BASIS_POINTS
    return CalibratedMaterialisationBytes(
        raw_bytes=checked_raw,
        calibrated_bytes=calibrated,
        factor_basis_points=factor,
    )


def observe_materialisation_estimate(
    profile: ExecutionProfile,
    *,
    estimated_bytes: int,
    observed_growth_bytes: int,
) -> int:
    """Ratchet a profile factor upward when observation exceeds its estimate.

    Zero is valid but carries no ratio evidence. A fixed 25% margin is applied
    only when a new observed/estimated ratio exceeds the current factor.
    """

    checked_profile = _checked_profile(profile)
    checked_estimate = _checked_bytes(estimated_bytes, name="estimated_bytes")
    checked_observed = _checked_bytes(
        observed_growth_bytes,
        name="observed_growth_bytes",
    )
    with _calibration_lock:
        current = _calibration_by_profile.get(
            checked_profile,
            CALIBRATION_BASE_BASIS_POINTS,
        )
        if checked_estimate == 0 or checked_observed == 0:
            return current
        observed_ratio = (
            checked_observed * CALIBRATION_BASE_BASIS_POINTS + checked_estimate - 1
        ) // checked_estimate
        if observed_ratio <= current:
            return current
        with_margin = (
            observed_ratio * CALIBRATION_SAFETY_MARGIN_BASIS_POINTS
            + CALIBRATION_BASE_BASIS_POINTS
            - 1
        ) // CALIBRATION_BASE_BASIS_POINTS
        updated = min(CALIBRATION_MAX_BASIS_POINTS, with_margin)
        _calibration_by_profile[checked_profile] = updated
        return updated


def materialisation_calibration_snapshot() -> MappingProxyType[str, int]:
    """Return immutable, sorted test/diagnostic state for elevated profiles."""

    with _calibration_lock:
        return MappingProxyType(
            {
                profile.value: _calibration_by_profile[profile]
                for profile in sorted(_calibration_by_profile, key=lambda item: item.value)
            }
        )


def _reset_materialisation_calibration_for_tests() -> None:
    with _calibration_lock:
        _calibration_by_profile.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_materialisation_calibration_for_tests)


__all__ = [
    "CALIBRATION_BASE_BASIS_POINTS",
    "CALIBRATION_MAX_BASIS_POINTS",
    "CALIBRATION_SAFETY_MARGIN_BASIS_POINTS",
    "CalibratedMaterialisationBytes",
    "calibrate_materialisation_bytes",
    "materialisation_calibration_factor",
    "materialisation_calibration_snapshot",
    "observe_materialisation_estimate",
]
